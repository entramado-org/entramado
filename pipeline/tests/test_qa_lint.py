"""Adversarial tests for the spec-061 lint: every check fires on a planted defect and
stays silent on the clean twin sitting right next to it. No graph, no model, no network.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from datetime import date

import yaml

from pipeline.src.qa_lint import CHECKS, URL_INVALIDA, contains_quote, norm, run

AS_OF = date(2026, 9, 9)
REAL_REPO = Path(__file__).resolve().parents[2]

NODES = [
    # Clean twin: conviction declared firme, fresh sources, every name it drops is linked.
    {'ref': 'ana', 'labels': ['Person'], 'props': {
        'entity_id': 'Person:ana-buena', 'name': 'Ana Buena', 'mentions': 3,
        'summary': 'Ana Buena fue condenada en 2020 y la sentencia es firme.',
        'summary_sources': ['https://buena.example/ana'], 'summary_updated': '2026-09-01',
        'role': 'Alcaldesa de Villa Baja'}},
    # Defective twin: conviction without firmeza, meta-text, no sources, stale, stray name.
    {'ref': 'beto', 'labels': ['Person'], 'props': {
        'entity_id': 'Person:beto-malo', 'name': 'Beto Malo', 'mentions': 5,
        'summary': ('Beto Malo fue condenado por cohecho. El nodo agrega referencias distintas '
                    'que deben separarse. Cenó con Carla Ajena en marzo.'),
        'summary_updated': '2026-01-01'}},
    {'ref': 'carla', 'labels': ['Person'], 'props': {
        'entity_id': 'Person:carla-ajena', 'name': 'Carla Ajena', 'mentions': 1,
        # L21 (061-C): a lone first name, an OCR line break, a bare honorific and a
        # generic description are the owner's alias_deny rules; the surname alone, a real
        # OCR variant and a full variant are kept.
        'aliases': ['Carla', 'Ajena', 'Carla\nAjena', 'Sra.', 'la asesora de Beto',
                    'Karla Ajena', 'Carla Ajena Pérez']}},
    {'ref': 'huerfana', 'labels': ['Person'], 'props': {
        'entity_id': 'Person:huerfana-sola', 'name': 'Huérfana Sola', 'mentions': 1}},
    # Pseudonyms: `david` keeps the roster name (clean), `felix` acquired a civil surname.
    {'ref': 'david', 'labels': ['Person'], 'props': {
        'entity_id': 'Person:david', 'name': 'David', 'aliases': ['don David'], 'mentions': 2,
        'is_pseudonym': True, 'summary': 'David declaró ante el instructor.',
        'summary_sources': ['https://buena.example/david'], 'summary_updated': '2026-09-01',
        'role': 'Investigado en la causa'}},
    {'ref': 'felix', 'labels': ['Person'], 'props': {
        'entity_id': 'Person:felix', 'name': 'Felix Apellido Civil', 'mentions': 2,
        'is_pseudonym': True}},
    {'ref': 'dni', 'labels': ['Person'], 'props': {
        'entity_id': 'Person:con-dni', 'name': 'Persona 12345678Z', 'mentions': 1}},
    {'ref': 'cohecho', 'labels': ['Crime'], 'props': {'entity_id': 'Crime:cohecho', 'name': 'cohecho'}},
    {'ref': 'org', 'labels': ['Organization'], 'props': {
        'entity_id': 'Organization:ayuntamiento', 'name': 'Ayuntamiento de Villa Baja'}},
    {'ref': 'doc_firme', 'labels': ['Document'], 'props': {
        'doc_id': 'sentencia-firme.pdf', 'doc_date': '2026-01-15', 'page_count': 40}},
    {'ref': 'doc_mudo', 'labels': ['Document'], 'props': {
        'doc_id': 'auto-sin-firmeza.pdf', 'doc_date': '2026-02-20', 'page_count': 12}},
    {'ref': 'chunk', 'labels': ['Chunk'], 'props': {
        'chunk_id': 'c1', 'doc_id': 'sentencia-firme.pdf', 'page_start': '4', 'page_end': '5',
        'embedding': [0.1] * 8, 'text': 'La Sala declara probado que Ana Buena recibió el sobre.'}},
]
RELS = [
    # L1/L2 clean: conviction from a document whose firmeza is declared, coherent status.
    {'ref': 'r_conv_ok', 'start': 'ana', 'end': 'cohecho', 'type': 'CONVICTED_OF', 'props': {
        'source_kind': 'judicial', 'source_doc': 'sentencia-firme.pdf', 'source_chunk_id': 'c1',
        'source_page': 4, 'source_quote': 'Ana Buena recibió el sobre', 'firme': True,
        'status': 'condenado'}},
    # L1: firmeza undeclared. L2: firme=true with a non-final status.
    {'ref': 'r_conv_bad', 'start': 'beto', 'end': 'cohecho', 'type': 'CONVICTED_OF', 'props': {
        'source_kind': 'judicial', 'source_doc': 'auto-sin-firmeza.pdf', 'firme': True,
        'status': 'condenado_no_firme', 'source_quote': 'condenado'}},
    # L2 clean twin: the registry (spec 061) propagated its conviction to an ACCUSED_OF
    # together with its firmeza — coherent, so it is not a defect.
    {'ref': 'r_acc_prop_ok', 'start': 'ana', 'end': 'cohecho', 'type': 'ACCUSED_OF', 'props': {
        'source_kind': 'judicial', 'source_doc': 'sentencia-firme.pdf', 'source_chunk_id': 'c1',
        'source_page': 4, 'source_quote': 'Ana Buena recibió el sobre',
        'status': 'condenado_no_firme', 'firme': False, 'status_fuente_tipo': 'sentencia'}},
    # L2: the same propagated status WITHOUT a coherent firmeza stays a defect.
    {'ref': 'r_acc_prop_bad', 'start': 'ana', 'end': 'cohecho', 'type': 'ACCUSED_OF', 'props': {
        'source_kind': 'judicial', 'source_doc': 'sentencia-firme.pdf', 'source_chunk_id': 'c1',
        'source_page': 4, 'source_quote': 'Ana Buena recibió el sobre',
        'status': 'condenado', 'firme': False, 'status_fuente_tipo': 'sentencia'}},
    # L3: judicial accusation with no procedural stage.
    {'ref': 'r_acc_null', 'start': 'beto', 'end': 'cohecho', 'type': 'ACCUSED_OF', 'props': {
        'source_kind': 'judicial', 'source_doc': 'sentencia-firme.pdf', 'source_chunk_id': 'c1',
        'source_page': 4, 'source_quote': 'Ana Buena recibió el sobre'}},
    # L4: the same person is absuelto and investigado at once, with no registry row.
    {'ref': 'r_acc_abs', 'start': 'carla', 'end': 'cohecho', 'type': 'ACCUSED_OF', 'props': {
        'source_kind': 'judicial', 'status': 'absuelto', 'source_doc': 'sentencia-firme.pdf',
        'source_chunk_id': 'c1', 'source_page': 4, 'source_quote': 'Ana Buena recibió el sobre'}},
    {'ref': 'r_acc_inv', 'start': 'carla', 'end': 'cohecho', 'type': 'ACCUSED_OF', 'props': {
        'source_kind': 'judicial', 'status': 'investigado', 'source_doc': 'sentencia-firme.pdf',
        'source_chunk_id': 'c1', 'source_page': 4, 'source_quote': 'Ana Buena recibió el sobre'}},
    # L11: expired press accusation vs a fresh one.
    {'ref': 'r_uh_old', 'start': 'beto', 'end': 'cohecho', 'type': 'ACCUSED_OF', 'props': {
        'source_kind': 'ultima_hora', 'status': 'investigado', 'date': '2025-01-05',
        'url': 'https://prensa.example/viejo', 'source_quote': 'según el diario'}},
    {'ref': 'r_uh_new', 'start': 'beto', 'end': 'cohecho', 'type': 'ACCUSED_OF', 'props': {
        'source_kind': 'ultima_hora', 'status': 'investigado', 'date': '2026-09-01',
        'url': 'https://prensa.example/nuevo', 'source_quote': 'según el diario'}},
    # L17: context edge without source_url vs one with it.
    {'ref': 'r_ctx_bad', 'start': 'ana', 'end': 'org', 'type': 'WORKS_FOR', 'props': {
        'source_kind': 'contexto', 'note': 'alcaldesa'}},
    {'ref': 'r_ctx_ok', 'start': 'carla', 'end': 'org', 'type': 'WORKS_FOR', 'props': {
        'source_kind': 'contexto', 'source_url': 'https://villabaja.example/corporacion'}},
    # L14: quote absent from its chunk; page outside the chunk's range.
    {'ref': 'r_quote_bad', 'start': 'ana', 'end': 'org', 'type': 'MET_WITH', 'props': {
        'source_kind': 'judicial', 'source_doc': 'sentencia-firme.pdf', 'source_chunk_id': 'c1',
        'source_page': 30, 'source_quote': 'una frase que el chunk jamás contuvo'}},
    # L15/L16: unknown layer, amount without currency, fact dated after its own document.
    {'ref': 'r_kind_bad', 'start': 'ana', 'end': 'org', 'type': 'PAID', 'props': {
        'source_kind': 'administrativo', 'amount': 1000, 'date': '2026-06-01',
        'source_doc': 'sentencia-firme.pdf'}},
    {'ref': 'r_pay_ok', 'start': 'carla', 'end': 'org', 'type': 'PAID', 'props': {
        'source_kind': 'judicial', 'amount': 500, 'currency': 'EUR', 'date': '2026-01-02',
        'source_doc': 'sentencia-firme.pdf'}},
    # Technical edges: they never count as domain degree.
    {'ref': 'r_mention', 'start': 'huerfana', 'end': 'chunk', 'type': 'MENTIONED_IN', 'props': {}},
    {'ref': 'r_part', 'start': 'chunk', 'end': 'doc_firme', 'type': 'PART_OF', 'props': {}},
    {'ref': 'r_david', 'start': 'david', 'end': 'org', 'type': 'WORKS_FOR', 'props': {
        'source_kind': 'judicial', 'source_doc': 'sentencia-firme.pdf', 'source_chunk_id': 'c1',
        'source_page': 4, 'source_quote': 'Ana Buena recibió el sobre'}},
    {'ref': 'r_felix', 'start': 'felix', 'end': 'org', 'type': 'WORKS_FOR', 'props': {
        'source_kind': 'judicial', 'source_doc': 'sentencia-firme.pdf', 'source_chunk_id': 'c1',
        'source_page': 4, 'source_quote': 'Ana Buena recibió el sobre'}},
    {'ref': 'r_dni', 'start': 'dni', 'end': 'org', 'type': 'WORKS_FOR', 'props': {
        'source_kind': 'contexto', 'source_url': 'https://villabaja.example/plantilla'}},
]


class LintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls.tmp.name)
        cls.snapshot = cls.repo / 'neo4j' / 'synthetic'
        cls.snapshot.mkdir(parents=True)
        counts = {}
        for name, rows in (('nodes', NODES), ('relationships', RELS)):
            path = cls.snapshot / f'{name}.jsonl'
            path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
            counts[name] = {'count': len(rows),
                            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        (cls.snapshot / 'snapshot.json').write_text(json.dumps(counts))
        cls.write_overlays()
        # L26 (spec 070) pregunta a git qué documentos publica el árbol: sin repo git no hay respuesta (fail-closed).
        for args in (['init', '-q'], ['add', '-A']):
            subprocess.run(['git', '-C', str(cls.repo), *args], check=True, capture_output=True)
        cls.out = cls.repo / 'lint'
        cls.summary = run(cls.snapshot, cls.out, AS_OF, repo=cls.repo)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def write_overlays(cls):
        def dump(rel, value):
            path = cls.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(value, allow_unicode=True))

        dump('pipeline/data/documents/documents.yaml', {'documents': [
            {'doc_id': 'sentencia-firme.pdf', 'doc_type': 'auto_judicial', 'sentencia_firme': True},
            {'doc_id': 'auto-sin-firmeza.pdf', 'doc_type': 'auto_judicial'}]})
        dump('pipeline/data/accusations/accusations.yaml', {'accusations': [
            {'person': 'Ana Buena', 'entity_id': 'Person:ana-buena', 'doc': 'sentencia-firme.pdf',
             'status': 'condenado', 'crimes': [{'name': 'cohecho'}]}]})
        dump('pipeline/data/context/summaries.yaml', {'summaries': [   # complete (spec 070, AC192): with its text
            {'entity_id': 'Person:ana-buena', 'resumen': 'Alcaldesa de Villa Baja.',
             'sources': ['https://buena.example/ana']}]})
        dump('pipeline/data/context/pseudonyms.yaml', {'pseudonyms': [
            {'id': 'Person:david', 'note': 'seudónimo'}, {'id': 'Person:felix', 'note': 'seudónimo'}]})
        dump('pipeline/data/context/merges.yaml', {'merges': []})
        dump('pipeline/data/ultima_hora/ultima_hora.yaml', {'relations': []})
        dump('pipeline/data/ultima_hora/feed.yaml', {'items': [
            {'iso': '2026-09-01', 'headline': 'Villa Baja aprueba el presupuesto',
             'dek': 'El pleno lo aprobó sin votos en contra.',
             'entities': ['Person:ana-buena', 'Person:no-existe']}]})
        dump('pipeline/data/patrones/patrones.yaml', {'patrones': [
            {'slug': 'racimo', 'titulo': 'Racimo', 'tesis': 'Una sociedad encadena menores al tope.',
             'entities': ['Organization:ayuntamiento'], 'hitos': []}]})
        dump('api/app/data/cases.yaml', {'cases': [
            {'slug': 'caso-villa', 'titulo': 'Caso Villa', 'dek': 'Resumen neutro del caso.',
             'entidades': [{'id': 'Person:ana-buena', 'papel': 'Alcaldesa'}],
             'documentos': ['sentencia-firme.pdf']}]})
        briefing = cls.repo / 'sm-vault' / '_Briefings' / 'villa.md'
        briefing.parent.mkdir(parents=True, exist_ok=True)
        briefing.write_text("---\nslug: villa\nanchors: ['Person:ana-buena']\n---\n\n"
                            '# Villa\n\nEl pleno aprobó el presupuesto sin incidencias.\n')
        # L13 needs a scannable layer file and the real scanner next to the synthetic repo.
        dump('pipeline/data/prensa/filtracion.yaml', {'nota': 'contacto 12345678Z'})
        scripts = cls.repo / 'pipeline' / 'scripts'
        scripts.mkdir(parents=True, exist_ok=True)
        shutil.copy(REAL_REPO / 'pipeline' / 'scripts' / 'pii_check.py', scripts / 'pii_check.py')

    def rows(self, check):
        path = self.out / f'L{int(check[1:]):02d}.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()]

    def ids(self, check, field='entity_id'):
        return sorted({r[field] for r in self.rows(check)})

    def test_every_check_ran(self):
        self.assertEqual(sorted(self.summary, key=lambda c: int(c[1:])),
                         [f'L{i}' for i in range(1, 27)])
        self.assertEqual(len(CHECKS), 26)  # L1-L21 (061) + L22-L25 (063) + L26 (070)
        for check, entry in self.summary.items():
            self.assertEqual(entry['count'], len(self.rows(check)), check)
            self.assertIn(entry['severity'], {'error', 'warn', 'info'})
        self.assertTrue((self.out / 'summary.md').read_text().startswith('# qa-lint 061'))
        self.assertEqual(json.loads((self.out / 'manifest.json').read_text())['nodes'], len(NODES))

    def test_l1_firmeza_undeclared(self):
        self.assertEqual([r['ref'] for r in self.rows('L1')], ['r_conv_bad'])

    def test_l2_status_firme_incoherent(self):
        self.assertEqual([r['ref'] for r in self.rows('L2')], ['r_conv_bad', 'r_acc_prop_bad'])
        self.assertIn('firme=true', self.rows('L2')[0]['detail'])

    def test_l2_accepts_the_registry_propagated_conviction(self):
        # T0d: the registry propagates its status + firmeza to the person's ACCUSED_OF;
        # only an INCOHERENT pair (or one without a sentencia behind it) is a defect.
        refs = [r['ref'] for r in self.rows('L2')]
        self.assertNotIn('r_acc_prop_ok', refs)
        self.assertIn('status de condena «condenado»',
                      next(r for r in self.rows('L2') if r['ref'] == 'r_acc_prop_bad')['detail'])

    def test_l3_accusation_without_status(self):
        self.assertEqual([r['ref'] for r in self.rows('L3')], ['r_acc_null'])

    def test_l4_open_and_closed_at_once(self):
        # Ana holds a conviction too, but her registry row arbitrates it: only Carla is flagged.
        self.assertEqual(self.ids('L4'), ['Person:carla-ajena'])

    def test_l5_conviction_without_firmeza(self):
        self.assertEqual(self.ids('L5'), ['Person:beto-malo'])

    def test_l6_prose_on_a_pseudonym(self):
        self.assertEqual(sorted((r['entity_id'], r['field']) for r in self.rows('L6')),
                         [('Person:david', 'role'), ('Person:david', 'summary')])

    def test_l7_meta_text(self):
        self.assertEqual(self.ids('L7'), ['Person:beto-malo'])
        self.assertIn('El nodo', self.rows('L7')[0]['detail'])

    def test_l8_unlinked_person_named_in_prose(self):
        # Beto's summary names Carla with no edge; the feed item names nobody undeclared.
        self.assertEqual([(r['entity_id'], 'Person:carla-ajena' in r['detail'])
                          for r in self.rows('L8')], [('Person:beto-malo', True)])

    def test_l9_missing_and_unknown_sources(self):
        self.assertEqual(self.ids('L9'), ['Person:beto-malo'])
        self.assertEqual(self.summary['L9']['domains'], {'buena.example': 2})
        self.assertIsNone(self.summary['L9']['dead_urls'])

    def test_l9_a_source_the_parser_cannot_read(self):
        """Spec 070, AC193 (enmienda 39): a summary source the URL parser cannot read — a bracketed host that is not
        an IPv6, an authority NFKC changes — no longer crashes L9 nor the overlay allowlist (`urlparse` raises with the
        URL in its message): L9 reports it with AC187's fixed reason, never the value."""
        malas = ['https://[qz55centinela]/x', 'https://qz56\uff0fcentinela.example/x']
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            snapshot = repo / 'snap'
            snapshot.mkdir()
            nodo = {'ref': 'n', 'labels': ['Person'], 'props': {
                'entity_id': 'Person:yy-persona-test', 'name': 'Yy Persona Test', 'summary': 'Resumen neutro.',
                'summary_sources': [*malas, 'https://buena.example/a'], 'summary_updated': '2026-09-01'}}
            (snapshot / 'nodes.jsonl').write_text(json.dumps(nodo, ensure_ascii=False) + '\n', encoding='utf-8')
            (snapshot / 'relationships.jsonl').write_text('', encoding='utf-8')
            (snapshot / 'snapshot.json').write_text(json.dumps({'nodes': {'count': 1}}), encoding='utf-8')
            summaries = repo / 'pipeline' / 'data' / 'context' / 'summaries.yaml'
            summaries.parent.mkdir(parents=True)
            summaries.write_text(yaml.safe_dump({'summaries': [{'entity_id': 'Person:yy-persona-test',
                                                                'resumen': 'Resumen neutro.',
                                                                'sources': [*malas, 'https://buena.example/a']}]},
                                                allow_unicode=True), encoding='utf-8')
            summary = run(snapshot, repo / 'lint', AS_OF, only={'L9'}, repo=repo)
            filas = [json.loads(line) for line in (repo / 'lint' / 'L09.jsonl').read_text().splitlines()]
        self.assertEqual([f['detail'] for f in filas], [URL_INVALIDA, URL_INVALIDA])
        self.assertEqual(summary['L9']['domains'], {'buena.example': 1})
        self.assertNotIn('qz', json.dumps(filas).lower())

    def test_l10_stale_procedural_summary(self):
        self.assertEqual(self.ids('L10'), ['Person:beto-malo'])

    def test_l11_expired_press_accusation(self):
        self.assertEqual([r['ref'] for r in self.rows('L11')], ['r_uh_old'])
        self.assertIn('sin fila de registro', self.rows('L11')[0]['detail'])

    def test_l12_pseudonym_with_a_civil_name(self):
        self.assertEqual(self.ids('L12'), ['Person:felix'])

    def test_l13_pii_in_layers_and_node_names(self):
        self.assertEqual(sorted(r['object_kind'] for r in self.rows('L13')),
                         ['node', 'overlay_row'])
        self.assertTrue(all('12345678Z' in r['detail'] for r in self.rows('L13')))

    def test_l14_quote_and_page(self):
        self.assertEqual(sorted((r['ref'], r['field']) for r in self.rows('L14')),
                         [('r_quote_bad', 'source_page'), ('r_quote_bad', 'source_quote')])

    def test_l15_unknown_source_kind(self):
        self.assertEqual([r['ref'] for r in self.rows('L15')], ['r_kind_bad'])

    def test_l16_amount_and_date(self):
        self.assertEqual(sorted((r['ref'], r['field']) for r in self.rows('L16')),
                         [('r_kind_bad', 'currency'), ('r_kind_bad', 'date')])

    def test_l17_context_without_source(self):
        self.assertEqual([r['ref'] for r in self.rows('L17')], ['r_ctx_bad'])

    def test_l18_person_without_domain_edges(self):
        self.assertEqual(self.ids('L18'), ['Person:huerfana-sola'])

    def test_l19_accused_without_registry_row(self):
        self.assertEqual(self.ids('L19'), ['Person:beto-malo', 'Person:carla-ajena'])

    def test_l20_unresolved_editorial_ids(self):
        self.assertEqual(self.ids('L20'), ['Person:no-existe'])

    def test_l21_alias_rules_of_the_owner(self):
        rows = self.rows('L21')
        # `david` («don David» on a pseudonym) and the alias-less nodes never fire.
        self.assertEqual([r['entity_id'] for r in rows], ['Person:carla-ajena'])
        hits = {h['alias']: h['motivo'] for h in json.loads(rows[0]['detail'])}
        self.assertEqual(sorted(hits), ['Carla', 'Carla\nAjena', 'Sra.', 'la asesora de Beto'])
        self.assertEqual(hits['Carla'], 'nombre de pila suelto')
        self.assertIn('artefacto', hits['Carla\nAjena'])
        self.assertIn('honorífico', hits['Sra.'])
        self.assertIn('artículo', hits['la asesora de Beto'])
        self.assertEqual(rows[0]['field'], 'aliases')

    def test_l21_unit_rules(self):
        from pipeline.src.qa_lint import alias_findings
        found = dict(alias_findings('Miguel Asesor De Fran', ['Miguel', 'Miguel Asesor', 'Asesor de', 'M1', 'ab']))
        self.assertEqual(sorted(found), sorted(['Miguel', 'Asesor de', 'M1', 'ab']))
        self.assertEqual(found['Miguel'], 'nombre de pila suelto')
        self.assertIn('conector', found['Asesor de'])
        # A pseudonym node keeps its roster first name; a one-token civil name has no "first name".
        self.assertEqual(list(alias_findings('Plácido', ['don Plácido', 'Plácido'], pseudonym=True)), [])
        self.assertEqual(list(alias_findings('Rajoy', ['Rajoy'])), [])
        self.assertEqual(list(alias_findings('Mariano Rajoy', ['Rajoy', 'señor Rajoy', 'Mariano Rajoy Brey'])), [])
        self.assertEqual(list(alias_findings('Ignacio de Foronda Vaquero', ['De Foronda', 'de Foronda Vaquero'])), [])
        self.assertEqual(list(alias_findings('Aena', ['Aena S.A.', 'AENA, S.M.E, S.A.', 'Aerovip GSA C.A.'])), [])
        self.assertEqual([a for a, _ in alias_findings('Obras Públicas SA', ['Obras Públicas y'])], ['Obras Públicas y'])
        self.assertEqual(list(alias_findings('Ion Fernando Antolín', ['lon Fernando ANTOLÍN LLORENTE'])), [])

    def test_subset_and_fail_on_are_independent(self):
        out = self.repo / 'subset'
        summary = run(self.snapshot, out, AS_OF, only={'L3'}, repo=self.repo)
        self.assertEqual(list(summary), ['L3'])
        self.assertFalse((out / 'L01.jsonl').exists())

    def test_quote_normalisation(self):
        haystack = norm('La Sala declara probado que  «Ana  Buena» recibió el sobre.')
        self.assertTrue(contains_quote(haystack, 'ana buena" recibio'))
        self.assertTrue(contains_quote(haystack, 'La Sala declara ... recibió el sobre'))
        self.assertFalse(contains_quote(haystack, 'Ana Buena devolvió el sobre'))


if __name__ == '__main__':
    unittest.main()
