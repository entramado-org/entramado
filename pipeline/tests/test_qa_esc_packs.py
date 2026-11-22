"""Spec 063 — packs por entidad y ledger `esc_*`. Sin modelos, sin red, sin grafo."""
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import unittest
from unittest.mock import patch

from src import qa_esc_packs as P
from src.qa_integral import canonical, connect, digest
from tests.test_qa_dual import DualBase

ALEJO, BENITO, EMPRESA = 'Person:alejo', 'Person:benito', 'Company:empresa'
FAMILIAR = 'Person:familiar'
ACCUSATIONS = ('accusations:\n'
               '- person: Alejo Pérez\n  entity_id: Person:alejo\n  doc: original.pdf\n  status: acusado\n'
               '  estado:\n    status: acusado\n    fecha: "2026-06-20"\n'
               '    fuente: {url: "https://example.com/auto-apertura", tipo: auto_apertura_juicio_oral}\n'
               '  crimes: []\n')
FEED = ('items:\n'
        '- iso: "2026-07-16"\n  date: "2026-07-16"\n  headline: La Audiencia confirma la apertura\n'
        '  dek: Rechaza el recurso de la defensa.\n  outlet: Infobae\n'
        '  url: "https://example.com/audiencia"\n  entities: [Person:alejo]\n')
FIGURES = ('entities:\n'
           '  - {name: "Alejo Pérez", type: Person, role: "Exministro del ramo; PSOE"}\n'
           'relations: []\n')
PSEUDONYMS = ('pseudonyms:\n'
              '- id: Person:benito\n  note: Seudónimo de anonimización judicial (roster del CENDOJ).\n')


class EscBase(DualBase):
    def setUp(self):
        super().setUp()
        self.write_repo_yaml()
        self.conn = connect(self.root)
        self.addCleanup(self.conn.close)

    def write_repo_yaml(self, feed=FEED):
        for relative, text in (('pipeline/data/accusations/accusations.yaml', ACCUSATIONS),
                               ('pipeline/data/ultima_hora/feed.yaml', feed),
                               ('pipeline/data/context/figures.yaml', FIGURES),
                               ('pipeline/data/context/pseudonyms.yaml', PSEUDONYMS)):
            path = self.base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def entry(self, entity, conn=None, scope='pending'):
        queue = P.entity_queue(conn or self.conn, scope)
        return next(e for e in queue if e['entity'] == entity)

    def build(self, entry, conn=None, **kwargs):
        kwargs.setdefault('fetch', False)
        return P.build_packs(conn or self.conn, self.base, entry['entity'], entry['unit_ids'],
                             kind=entry['kind'], **kwargs)

    def extra_census(self, name, nodes=(), rels=(), **kwargs):
        """A second census over the same seed with extra nodes/edges (ids stay deterministic)."""
        from pipeline.src import qa_dual
        self.nodes.extend(nodes)
        self.rels.extend(rels)
        self.write_snapshot()
        root = self.base / 'audit' / name
        qa_dual.census(root, self.snapshot, self.seed, self.base, {'PAID': 'Pagó a'},
                       t4_sample=kwargs.pop('t4_sample', 100), **kwargs)
        conn = connect(root)
        self.addCleanup(conn.close)
        return root, conn


class PackTest(EscBase):
    def test_manifest_is_deterministic_and_hashed(self):
        entry = self.entry(ALEJO)
        first, second = self.build(entry), self.build(entry)
        self.assertEqual([m['pack_id'] for m in first], [m['pack_id'] for m in second])
        self.assertEqual(P.pack_sha256(first[0]), P.pack_sha256(second[0]))
        self.assertEqual(P.pack_sha256(first[0]), digest(first[0]))
        self.assertTrue(first[0]['pack_id'].startswith('E-'))
        # The overlay the pack was built against is part of its identity (T0e replay).
        self.assertNotEqual(self.build(entry, overlay_ref='0' * 40)[0]['pack_id'], first[0]['pack_id'])

    def test_unit_ids_are_closed_and_every_one_is_rendered(self):
        entry = self.entry(ALEJO)
        packs = self.build(entry)
        packed = [u['unit_id'] for m in packs for u in m['units']]
        self.assertEqual(sorted(packed), sorted(entry['unit_ids']))
        for manifest in packs:
            markdown = P.render_pack(manifest)
            headings = re.findall(r'^### ([0-9a-f]{64})$', markdown, re.M)
            self.assertEqual(headings, [u['unit_id'] for u in manifest['units']])
            self.assertIn('## Texto VIGENTE', markdown)
            self.assertIn('## Vigencia', markdown)
            self.assertIn('## Fuentes', markdown)
            self.assertIn('## Feed de última hora', markdown)

    def test_units_are_ordered_by_tier_and_edges_grouped_by_page(self):
        entry = self.entry(ALEJO)
        units = [u for m in self.build(entry) for u in m['units']]
        tiers = [u['tier'] for u in units]
        self.assertEqual(tiers, sorted(tiers, key=['T0', 'T1', 'T2', 'T4'].index))
        edge = next(u for u in units if u['kind'] == 'edge')
        self.assertEqual(edge['edge']['source_doc'], 'original.pdf')
        self.assertEqual((edge['edge']['source_page'], edge['edge']['source_kind']), (1, 'judicial'))
        self.assertIn(edge['edge']['type'], {'ACCUSED_OF', 'PAID', 'ASSOCIATED_WITH'})
        paid = next(u for u in units if u['kind'] == 'edge' and u['edge']['type'] == 'PAID')
        self.assertEqual((paid['edge']['amount'], paid['edge']['currency'], paid['edge']['public_label']),
                         (5700.0, 'EUR', 'Pagó a'))
        markdown = P.render_pack(self.build(entry)[0])
        self.assertIn('- cita guardada: «El original dice que Alejo pago»', markdown)
        self.assertIn('p.1 · procedencia: judicial', markdown)

    def test_a_hub_is_split_in_subpacks_that_share_the_entity_block(self):
        oid = self.conn.execute("SELECT object_id FROM objects WHERE object_kind='node' AND entity=?",
                                (ALEJO,)).fetchone()[0]
        with self.conn:
            for i in range(15):
                uid = digest(['hub', i])
                self.conn.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                                  ' VALUES (?,?,?,?,?,0,100,"[]")',
                                  (uid, oid, 'summary', f'/summary@{200 + i}:{205 + i}',
                                   canonical(f'Frase numero {i} del hub.')))
                self.conn.execute('INSERT INTO tiers VALUES (?,?,?)', (uid, 'T0', 1.0))
        entry = self.entry(ALEJO)
        packs = self.build(entry)
        self.assertGreater(len(packs), 1)
        self.assertTrue(all(len(m['units']) <= P.MAX_PACK_UNITS for m in packs))
        self.assertEqual([(m['index'], m['total']) for m in packs],
                         [(i, len(packs)) for i in range(1, len(packs) + 1)])
        self.assertEqual(packs[1]['pack_id'], packs[0]['pack_id'] + '-2')
        for manifest in packs:
            self.assertEqual((manifest['entity'], manifest['current'], manifest['vigencia']),
                             (packs[0]['entity'], packs[0]['current'], packs[0]['vigencia']))
            self.assertIn(f"SUB-PACK {manifest['index']} de {len(packs)}", manifest['warnings'])
        # Una entidad que cabe en un pack nunca lleva el aviso.
        self.assertNotIn('SUB-PACK', ' '.join(self.build(self.entry(BENITO))[0]['warnings']))
        self.assertIn('van vacíos', P.render_pack(packs[1]))

    def test_editorial_items_pack_in_sixes_by_principal_entity(self):
        items = ''.join(f'- iso: "2026-0{i}-01"\n  date: "2026-0{i}-01"\n  headline: Titular {i}\n'
                        f'  dek: Cuerpo del ítem {i}.\n  outlet: Infobae\n'
                        f'  url: "https://example.com/n{i}"\n  entities: [Person:alejo, Company:empresa]\n'
                        for i in range(1, 8))
        self.write_repo_yaml(feed='items:\n' + items)
        _, conn = self.extra_census('editorial', editorial=True)
        entry = self.entry(ALEJO, conn=conn)
        editorial = [e for e in P.entity_queue(conn, 'pending') if e['kind'] == 'editorial']
        self.assertEqual([(e['entity'], e['n_units']) for e in editorial], [(ALEJO, 7)])
        packs = self.build(editorial[0], conn=conn)
        self.assertEqual([len(m['units']) for m in packs], [6, 1])
        self.assertTrue(packs[0]['pack_id'].startswith('D-'))
        self.assertEqual({u['kind'] for m in packs for u in m['units']}, {'editorial'})
        # The entity pack of the same person is a different pack.
        self.assertNotEqual(self.build(entry, conn=conn)[0]['pack_id'], packs[0]['pack_id'])

    def test_the_warnings_carry_the_owner_rules(self):
        familiar = {'ref': 'n5', 'labels': ['Person'], 'props': {
            'entity_id': FAMILIAR, 'name': 'Familiar Sin Acusacion', 'degree': 1, 'anonymized': True,
            'summary': 'Es hermano de Alejo Pérez.'}}
        family_edge = {'ref': 'r11', 'start': 'n5', 'end': 'n1', 'type': 'FAMILY_OF', 'props': {}}
        _, conn = self.extra_census('familiar', nodes=[familiar], rels=[family_edge])
        manifest = self.build(self.entry(FAMILIAR, conn=conn), conn=conn)[0]
        self.assertEqual(manifest['warnings'], [P.W_A5, P.W_ANONYMIZED])
        self.assertTrue(manifest['entity']['a5_familiar'])
        self.assertEqual(manifest['entity']['family_of'], [ALEJO])
        pseudonym = self.build(self.entry(BENITO), )[0]
        self.assertIn(P.W_PSEUDONYM, pseudonym['warnings'])
        self.assertTrue(pseudonym['entity']['is_pseudonym'])
        self.assertIn('⚠️ **SEUDÓNIMO JUDICIAL (D5)**', P.render_pack(pseudonym))

    def test_vigencia_travels_only_with_a_register_row_or_a_feed_item(self):
        alejo = self.build(self.entry(ALEJO))[0]
        self.assertIn('«acusado» con fecha 2026-06-20', alejo['vigencia'])
        self.assertIn('auto_apertura_juicio_oral', alejo['vigencia'])
        self.assertIn('La Audiencia confirma la apertura', alejo['vigencia'])
        self.assertEqual(alejo['entity']['estado_row']['estado']['status'], 'acusado')
        benito = self.build(self.entry(BENITO))[0]
        self.assertEqual((benito['vigencia'], benito['entity']['estado_row'], benito['entity']['feed_items']),
                         ('', None, []))
        self.assertIn('sin fila de registro ni ítems', P.render_pack(benito))

    def test_the_current_text_is_the_overlay_when_there_is_one(self):
        node = self.build(self.entry(ALEJO))[0]['current']
        self.assertEqual(node['summary']['origin'], 'node')
        self.assertIn('Alejo pago 5.700 euros', node['summary']['text'])
        self.assertEqual(node['role'], {'text': 'Exministro del ramo.', 'origin': 'node'})
        overlay = self.base / 'pipeline/data/context'
        (overlay / 'summaries_qa061.yaml').write_text(
            'summaries:\n- entity_id: Person:alejo\n  resumen: Resumen corregido por overlay.\n'
            "  sources: ['https://example.com/fuente']\n  updated: '2026-09-19'\n"
            '- entity_id: Person:benito\n  remove: true\n')
        (overlay / 'roles_qa061.yaml').write_text(
            'entities:\n- {type: Person, name: Alejo Pérez, role: remove}\n')
        current = P.current_text(self.conn, self.base, ALEJO)
        self.assertEqual(current['summary'], {'text': 'Resumen corregido por overlay.',
                                              'sources': ['https://example.com/fuente'],
                                              'origin': 'overlay', 'updated': '2026-09-19'})
        self.assertEqual(current['role'], {'removed': True, 'origin': 'overlay'})
        self.assertEqual(P.current_text(self.conn, self.base, BENITO)['summary'],
                         {'removed': True, 'origin': 'overlay'})
        markdown = P.render_pack(self.build(self.entry(ALEJO))[0])
        self.assertIn('**summary (overlay summaries_qa061, updated 2026-09-19)**', markdown)
        self.assertIn('**role**: RETIRADO por overlay `roles_qa061`.', markdown)

    def test_overlay_ref_reads_the_blob_from_git(self):
        git = ['git', '-C', str(self.base)]
        subprocess.run(git + ['init', '-q'], check=True, capture_output=True)
        path = self.base / 'pipeline/data/context/summaries_qa061.yaml'
        path.write_text('summaries:\n- entity_id: Person:alejo\n  resumen: Resumen ANTIGUO, el del gold.\n'
                        "  updated: '2026-09-01'\n")
        subprocess.run(git + ['add', 'pipeline/data/context/summaries_qa061.yaml'], check=True, capture_output=True)
        subprocess.run(git + ['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'gold',
                              '--no-gpg-sign'], check=True, capture_output=True)
        sha = subprocess.run(git + ['rev-parse', 'HEAD'], check=True, capture_output=True,
                             text=True).stdout.strip()
        path.write_text('summaries:\n- entity_id: Person:alejo\n  resumen: Resumen de hoy, ya corregido.\n'
                        "  updated: '2026-09-20'\n")
        self.assertEqual(P.current_text(self.conn, self.base, ALEJO, sha)['summary']['text'],
                         'Resumen ANTIGUO, el del gold.')
        self.assertEqual(P.current_text(self.conn, self.base, ALEJO)['summary']['text'],
                         'Resumen de hoy, ya corregido.')
        manifest = self.build(self.entry(ALEJO), overlay_ref=sha)[0]
        self.assertEqual(manifest['overlay_ref'], sha)
        self.assertIn('Resumen ANTIGUO', P.render_pack(manifest))

    def test_sources_carry_the_corpus_pages_and_declare_what_is_missing(self):
        manifest = self.build(self.entry(ALEJO))[0]
        sources = manifest['sources']
        self.assertTrue(sources)
        self.assertEqual({'source_id', 'doc', 'url', 'page', 'status', 'complete', 'omitted_chars',
                          'note', 'text'}, set(sources[0]))
        self.assertIn('Alejo pago 5.700 euros', ' '.join(s['text'] for s in sources))
        markdown = P.render_pack(manifest)
        for source in sources:
            self.assertIn(f"[fuente {source['source_id']}]", markdown)
            if not source['complete']:
                self.assertIn(f"se han omitido {source['omitted_chars']} caracteres", markdown)
        self.assertIn('COMPLETA', markdown)
        # fetch=False leaves the cited URLs as declared, unfetched stubs — never the network.
        self.assertIn('not-fetched', {s['status'] for s in sources})

    def test_the_tool_line_says_whether_the_reader_can_search(self):
        manifest = self.build(self.entry(ALEJO))[0]
        self.assertIn('Lector ciego: sin herramienta', P.render_pack(manifest))
        with_tools = P.render_pack(manifest, tools=True, ev_path='/abs/qa_esc_ev.py')
        self.assertIn('Herramienta: python3 /abs/qa_esc_ev.py', with_tools)
        self.assertIn('Máximo 8 búsquedas', with_tools)
        with self.assertRaises(ValueError):
            P.render_pack(manifest, tools=True)


class QueueTest(EscBase):
    def test_the_queue_is_ordered_by_risk_then_size(self):
        queue = P.entity_queue(self.conn, 'pending')
        self.assertEqual(queue[0]['entity'], ALEJO)
        self.assertEqual(queue[0]['riesgo'], 'alto')
        ranks = [(P.RISK_ORDER[e['riesgo']], -e['n_units'], e['entity']) for e in queue]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual({e['kind'] for e in queue}, {'entity'})
        pending = {u['unit_id'] for u in self.units() if u['state'] == 'pending'}
        self.assertEqual({uid for e in queue for uid in e['unit_ids']}, pending)
        self.assertEqual(sum(e['n_units'] for e in queue), len(pending))

    def test_an_edge_belongs_to_its_person_endpoint(self):
        queue = {e['entity']: e for e in P.entity_queue(self.conn, 'pending')}
        alejo = set(queue[ALEJO]['unit_ids'])
        edges = {u['unit_id'] for u in self.units(kind='edge')}
        self.assertTrue(edges)
        # Las tres aristas salen de Person:alejo: ninguna cae en el pack de la empresa.
        self.assertTrue(edges <= alejo)
        self.assertFalse(edges & set(queue[EMPRESA]['unit_ids']))

    def test_the_qc_scope_only_sees_verified_T0_units(self):
        queue = P.entity_queue(self.conn, 'verified-T0')
        verified = [u for u in self.units(tier='T0') if u['state'] == 'verified' and not u['technical']]
        self.assertEqual({uid for e in queue for uid in e['unit_ids']}, {u['unit_id'] for u in verified})
        with self.assertRaises(ValueError):
            P.entity_queue(self.conn, 'inventado')


class FactsTest(EscBase):
    def test_entity_facts_reads_the_export_the_ledger_and_the_yaml_only(self):
        facts = P.entity_facts(self.conn, self.base, ALEJO)
        self.assertEqual(facts['name'], 'Alejo Pérez')
        self.assertEqual(facts['context_role'], 'Exministro del ramo; PSOE')
        # APPOINTED_TO nunca entró en el censo (no es de ningún tier) pero SÍ es una arista de
        # dominio del grafo: los hechos estructurales salen del export, no del ledger.
        self.assertEqual(facts['edge_counts'],
                         {'ACCUSED_OF': {'judicial': {'investigado': 1}},
                          'APPOINTED_TO': {'unspecified': {'': 1}},
                          'ASSOCIATED_WITH': {'judicial': {'': 1}},
                          'PAID': {'judicial': {'': 1}}})
        self.assertEqual(facts['accused_status_null'], 0)
        self.assertFalse(facts['only_associative'])
        self.assertFalse(facts['a5_familiar'])
        self.assertEqual(facts['feed_items'], [{'iso': '2026-07-16', 'outlet': 'Infobae',
                                                'headline': 'La Audiencia confirma la apertura',
                                                'url': 'https://example.com/audiencia'}])
        self.assertEqual(P.rule_flags(facts)['figura_publica'], True)
        self.assertEqual(P.rule_flags(facts)['investigado_encausado'], True)

    def test_a_familiar_without_accusation_and_an_accused_of_without_status(self):
        familiar = {'ref': 'n5', 'labels': ['Person'], 'props': {
            'entity_id': FAMILIAR, 'name': 'Familiar Sin Acusacion', 'degree': 1,
            'summary': 'Es hermano de Alejo Pérez.'}}
        rels = [{'ref': 'r11', 'start': 'n5', 'end': 'n1', 'type': 'FAMILY_OF', 'props': {}},
                {'ref': 'r12', 'start': 'n1', 'end': 'n4', 'type': 'ACCUSED_OF',
                 'props': {'source_doc': 'original.pdf', 'source_page': 2, 'source_kind': 'prensa'}}]
        _, conn = self.extra_census('facts', nodes=[familiar], rels=rels)
        facts = P.entity_facts(conn, self.base, FAMILIAR)
        self.assertTrue(facts['a5_familiar'])
        self.assertEqual(facts['family_of'], [ALEJO])
        self.assertFalse(facts['only_associative'])
        self.assertTrue(P.rule_flags(facts)['familiar_sin_acusacion'])
        self.assertFalse(P.rule_flags(facts)['investigado_encausado'])
        alejo = P.entity_facts(conn, self.base, ALEJO)
        self.assertEqual(alejo['accused_status_null'], 1)
        self.assertEqual(alejo['edge_counts']['ACCUSED_OF'],
                         {'judicial': {'investigado': 1}, 'prensa': {'': 1}})
        self.assertFalse(alejo['a5_familiar'])          # tiene ACCUSED_OF

    def test_only_associative_is_the_bare_mention_flag(self):
        mentioned = {'ref': 'n6', 'labels': ['Person'], 'props': {
            'entity_id': 'Person:mencionado', 'name': 'Persona Mencionada', 'degree': 1,
            'summary': 'Aparece junto a Alejo Pérez en una reunión.'}}
        rels = [{'ref': 'r13', 'start': 'n6', 'end': 'n1', 'type': 'MET_WITH',
                 'props': {'source_doc': 'original.pdf', 'source_page': 1, 'source_kind': 'judicial'}}]
        _, conn = self.extra_census('mencionado', nodes=[mentioned], rels=rels)
        facts = P.entity_facts(conn, self.base, 'Person:mencionado')
        self.assertTrue(facts['only_associative'])
        self.assertTrue(P.rule_flags(facts)['solo_mencionado'])
        self.assertFalse(P.rule_flags(facts)['figura_publica'])


class GraphIndexTest(EscBase):
    """Los hechos estructurales salen del export congelado: el censo 061 muestreó T4 al 10 %
    (6 de las 60 FAMILY_OF del grafo), así que leerlos del ledger deja ciega la regla A5."""

    def edges(self, index):
        db = sqlite3.connect(index)
        try:
            return [dict(zip(('type', 'start', 'end'), row)) for row in
                    db.execute('SELECT type,start_entity,end_entity FROM graph_edges ORDER BY ref')]
        finally:
            db.close()

    def test_the_index_holds_the_domain_edges_of_the_export_and_is_cached_by_hash(self):
        index = P.graph_index(self.root)
        self.assertEqual(index, self.root / 'esc' / 'graph-edges.sqlite')
        edges = self.edges(index)
        self.assertEqual(sorted(e['type'] for e in edges),
                         ['ACCUSED_OF', 'APPOINTED_TO', 'ASSOCIATED_WITH', 'PAID'])
        self.assertTrue(all(e['start'] == ALEJO for e in edges))      # MENTIONED_IN/PART_OF fuera
        with patch.object(P, 'build_graph_index', side_effect=AssertionError('no rebuild')) as build:
            self.assertEqual(P.graph_index(self.root), index)
            build.assert_not_called()
        # Un export que ya no cuadra con los hashes del manifest no se cree: manda el ledger.
        (self.snapshot / 'relationships.jsonl').write_text('')
        self.assertIsNone(P.graph_index(self.root, rebuild=True))

    def test_entity_facts_sees_the_family_that_the_sampled_ledger_lost(self):
        familiar = {'ref': 'n5', 'labels': ['Person'], 'props': {
            'entity_id': FAMILIAR, 'name': 'Familiar Sin Acusacion', 'degree': 1,
            'summary': 'Es hermano de Alejo Pérez.'}}
        family_edge = {'ref': 'r11', 'start': 'n5', 'end': 'n1', 'type': 'FAMILY_OF', 'props': {}}
        root, conn = self.extra_census('muestreada', nodes=[familiar], rels=[family_edge], t4_sample=0)
        ledger = conn.execute("SELECT count(*) FROM objects WHERE object_kind='relationship'"
                              " AND type='FAMILY_OF'").fetchone()[0]
        self.assertEqual(ledger, 0)                       # el 10 % de T4: el parentesco no está
        facts = P.entity_facts(conn, self.base, FAMILIAR)
        self.assertEqual(facts['family_of'], [ALEJO])
        self.assertTrue(facts['a5_familiar'])
        self.assertTrue(P.rule_flags(facts)['familiar_sin_acusacion'])
        self.assertIn(P.W_A5, self.build(self.entry(FAMILIAR, conn=conn), conn=conn)[0]['warnings'])
        # Sin export (snapshot movido) el ledger responde, y es un subconjunto muestreado.
        manifest = json.loads((root / 'manifest.json').read_text())
        manifest['snapshot_path'] = str(self.base / 'no-existe')
        (root / 'manifest.json').write_text(json.dumps(manifest))
        self.assertIsNone(P.graph_index(root))
        blind = P.entity_facts(conn, self.base, FAMILIAR)
        self.assertEqual((blind['family_of'], blind['a5_familiar']), ([], False))

    def test_a_public_office_is_never_an_a5_relative(self):
        presidente = {'ref': 'n7', 'labels': ['Person'], 'props': {
            'entity_id': 'Person:presidente', 'name': 'Presidenta Del Gobierno', 'degree': 40,
            'is_boe': True, 'summary': 'Preside el Gobierno y es cónyuge de Alejo Pérez.'}}
        rels = [{'ref': 'r14', 'start': 'n7', 'end': 'n1', 'type': 'FAMILY_OF', 'props': {}}]
        _, conn = self.extra_census('cargo', nodes=[presidente], rels=rels)
        facts = P.entity_facts(conn, self.base, 'Person:presidente')
        self.assertEqual(facts['family_of'], [ALEJO])
        self.assertFalse(facts['a5_familiar'])
        self.assertTrue(P.rule_flags(facts)['figura_publica'])


class LedgerTest(EscBase):
    def tables(self, conn):
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_the_esc_tables_are_additive_and_idempotent(self):
        before = self.tables(self.conn)
        self.assertFalse({'esc_packs', 'esc_readings'} & before)
        self.assertTrue(P.ensure_esc_schema(self.conn))
        self.assertTrue(P.ensure_esc_schema(self.conn))
        after = self.tables(self.conn)
        self.assertEqual(after - before, {'esc_packs', 'esc_readings'})
        self.assertTrue(before <= after)
        columns = {r['name'] for r in self.conn.execute('PRAGMA table_info(esc_packs)')}
        self.assertEqual(columns, {'pack_id', 'entity', 'kind', 'idx', 'total', 'unit_ids', 'pack_path',
                                   'pack_sha256', 'overlay_ref', 'riesgo', 'clase', 'route', 'state',
                                   'batch', 'created_at'})

    def test_write_pack_freezes_the_pack_and_is_idempotent(self):
        manifest = self.build(self.entry(ALEJO))[0]
        markdown = P.render_pack(manifest)
        first = P.write_pack(self.root, manifest, markdown)
        second = P.write_pack(self.root, manifest, markdown)
        self.assertEqual(first, second)
        self.assertEqual(first['pack_sha256'], P.pack_sha256(manifest))
        rows = list(self.conn.execute('SELECT * FROM esc_packs'))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['pack_id'], rows[0]['state'], rows[0]['kind'], rows[0]['idx'],
                          rows[0]['total'], rows[0]['overlay_ref'], rows[0]['riesgo']),
                         (manifest['pack_id'], 'packed', 'entity', 1, 1, P.WORKTREE, 'alto'))
        self.assertEqual(json.loads(rows[0]['unit_ids']), [u['unit_id'] for u in manifest['units']])
        again, stored_markdown = P.load_pack(self.root, manifest['pack_id'])
        self.assertEqual((again, stored_markdown), (manifest, markdown))
        self.assertTrue(Path(rows[0]['pack_path']).exists())
        with self.assertRaises(ValueError):
            P.write_pack(self.root, {**manifest, 'vigencia': 'otro texto'}, markdown)
        with self.assertRaises(ValueError):
            P.load_pack(self.root, 'E-inexistente')


if __name__ == '__main__':
    unittest.main()
