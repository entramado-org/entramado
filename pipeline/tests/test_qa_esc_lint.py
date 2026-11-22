"""Tests de los lints L22-L25 (spec 063, AC14): cada comprobación salta sobre el defecto
plantado y calla sobre el gemelo limpio de al lado. Sin grafo, sin modelos, sin red.

Cada caso monta su propio repo sintético (snapshot + overlays) porque L22-L25 dependen de
qué YAML existe: `familiares_revisados.yaml` ausente es, justamente, uno de los casos.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from datetime import date

import yaml

from pipeline.src.qa_lint import CHECKS, exceeds, procedural_terms, run

AS_OF = date(2026, 9, 21)
ESC_CHECKS = ('L22', 'L23', 'L24', 'L25')

# Registro de los lints heredados: la 063 solo AÑADE checks. Si este literal deja de
# coincidir con CHECKS, es que se tocó la 061 (y eso sale del alcance de la 063).
CHECKS_061 = [
    ('L1', 'error'), ('L2', 'error'), ('L3', 'warn'), ('L4', 'warn'), ('L5', 'warn'),
    ('L6', 'warn'), ('L7', 'warn'), ('L8', 'info'), ('L9', 'info'), ('L10', 'info'),
    ('L11', 'warn'), ('L12', 'error'), ('L13', 'error'), ('L14', 'warn'), ('L15', 'error'),
    ('L16', 'warn'), ('L17', 'warn'), ('L18', 'warn'), ('L19', 'warn'), ('L20', 'warn'),
    ('L21', 'warn'),
]

# Overlays mínimos: sin ellos `Overlays` los lee como listas vacías igualmente, pero
# escribirlos deja explícito qué ve cada caso.
BASE_FILES = {
    'pipeline/data/accusations/accusations.yaml': {'accusations': []},
    'pipeline/data/context/figures.yaml': {'entities': []},
    'pipeline/data/ultima_hora/feed.yaml': {'items': []},
}


def person(slug, **props):
    props.setdefault('name', slug.replace('-', ' ').title())
    return {'ref': slug, 'labels': ['Person'], 'props': {'entity_id': f'Person:{slug}', **props}}


def other(ref, label, name):
    return {'ref': ref, 'labels': [label], 'props': {'entity_id': f'{label}:{ref}', 'name': name}}


def edge(ref, start, end, type_, **props):
    return {'ref': ref, 'start': start, 'end': end, 'type': type_, 'props': props}


def estado_row(entity_id, status):
    return {'person': entity_id, 'entity_id': entity_id, 'doc': 'auto.pdf',
            'estado': {'status': status, 'fecha': '2026-01-02',
                       'fuente': {'doc': 'auto.pdf', 'tipo': 'auto'}, 'revisado': '2026-01-02'}}


def lint(nodes, rels=(), files=None, checks=ESC_CHECKS):
    """Corre el lint sobre un repo sintético y devuelve {check: [filas]}."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        snapshot = repo / 'neo4j' / 'sintetico'
        snapshot.mkdir(parents=True)
        counts = {}
        for name, rows in (('nodes', nodes), ('relationships', rels)):
            path = snapshot / f'{name}.jsonl'
            path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
            counts[name] = {'count': len(rows),
                            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        (snapshot / 'snapshot.json').write_text(json.dumps(counts))
        for rel, value in {**BASE_FILES, **(files or {})}.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(value, allow_unicode=True))
        out = repo / 'lint'
        run(snapshot, out, AS_OF, only=set(checks), repo=repo)
        return {c: [json.loads(line) for line
                    in (out / f'L{int(c[1:]):02d}.jsonl').read_text().splitlines()]
                for c in checks}


class LadderTest(unittest.TestCase):
    """`procedural_terms` y `exceeds`: las dos funciones puras que L22/L23 comparten."""

    def test_procedural_terms_reads_the_stage_not_the_noun(self):
        self.assertEqual(procedural_terms('Está investigado por cohecho.'), {'investigado'})
        self.assertEqual(procedural_terms('Fue procesado y luego acusado.'),
                         {'procesado', 'acusado'})
        # Una condena recurrida es un peldaño por debajo de la firme.
        self.assertEqual(procedural_terms('Condenado en sentencia no firme, recurrible.'),
                         {'condenado_no_firme'})
        self.assertEqual(procedural_terms('Condenado por la sentencia firme de 2024.'),
                         {'condenado'})
        # Sustantivos de parte o de trámite: no atribuyen fase a nadie.
        self.assertEqual(procedural_terms('Ejerce la acusación particular en la causa.'), set())
        self.assertEqual(procedural_terms('La investigación sigue abierta.'), set())
        self.assertEqual(procedural_terms('Nada que declarar.'), set())

    def test_exceeds_ladder_and_closed_states(self):
        self.assertTrue(exceeds('acusado', 'investigado'))
        self.assertFalse(exceeds('investigado', 'acusado'))
        self.assertFalse(exceeds('imputado', 'imputado'))
        self.assertTrue(exceeds('condenado', 'condenado_no_firme'))
        # Sin registro, el techo es la fase más leve.
        self.assertTrue(exceeds('imputado', None))
        self.assertFalse(exceeds('investigado', None))
        # Cerrados: cualquier fase abierta sobra; decir «absuelto» nunca agrava.
        self.assertTrue(exceeds('investigado', 'absuelto'))
        self.assertTrue(exceeds('condenado', 'archivado'))
        self.assertFalse(exceeds('absuelto', 'archivado'))
        self.assertFalse(exceeds('absuelto', 'investigado'))


class L22Test(unittest.TestCase):
    """Conducta ≤ registro."""

    def rows(self, summary=None, role=None, status='investigado', feed=None):
        eid = 'Person:reo'
        props = {'name': 'Reo Registrado'}
        if summary:
            props['summary'] = summary
        if role:
            props['role'] = role
        files = {'pipeline/data/accusations/accusations.yaml':
                 {'accusations': [estado_row(eid, status)] if status else []}}
        if feed is not None:
            files['pipeline/data/ultima_hora/feed.yaml'] = {'items': [
                {'iso': '2026-09-01', 'headline': 'Titular neutro del caso', 'dek': feed,
                 'outlet': 'Infobae', 'url': 'https://infobae.example/x', 'entities': [eid]}]}
        return lint([person('reo', **props), other('cohecho', 'Crime', 'cohecho')],
                    [edge('r', 'reo', 'cohecho', 'ACCUSED_OF', source_kind='judicial',
                          status='investigado')], files)['L22']

    def test_a_documented_conviction_raises_the_ceiling(self):
        # 063 (2026-09-29): fila `acusado` + CONVICTED_OF firme → «condenado» no es exceso.
        rows = lint([person('reo', name='Reo Registrado', role='Condenado por la sentencia del TS.'),
                     other('cohecho', 'Crime', 'cohecho')],
                    [edge('r', 'reo', 'cohecho', 'CONVICTED_OF', source_kind='judicial', status='condenado')],
                    {'pipeline/data/accusations/accusations.yaml':
                     {'accusations': [estado_row('Person:reo', 'acusado')]}})['L22']
        self.assertEqual(rows, [])

    def test_term_above_the_registry_fires(self):
        rows = self.rows(summary='Está procesado por cohecho desde marzo.')
        self.assertEqual([(r['entity_id'], r['field']) for r in rows],
                         [('Person:reo', 'summary')])
        self.assertIn('fase «procesado» por encima del registro «investigado»', rows[0]['detail'])

    def test_term_at_or_below_the_registry_stays_silent(self):
        self.assertEqual(self.rows(summary='Está investigado por cohecho.', status='acusado'), [])
        self.assertEqual(self.rows(summary='Está investigado por cohecho.'), [])

    def test_closed_registry_with_an_open_term_fires(self):
        rows = self.rows(summary='Sigue investigado en la pieza principal.', status='absuelto')
        self.assertEqual(len(rows), 1)
        self.assertIn('registro «absuelto»', rows[0]['detail'])

    def test_negated_term_is_not_an_assertion(self):
        self.assertEqual(self.rows(summary='No consta procesado en ninguna pieza.'), [])

    def test_conduct_without_modalisation_fires(self):
        rows = self.rows(role='Cobró 200.000 euros en mordidas del constructor.')
        self.assertEqual([(r['field'], 'conducta' in r['detail']) for r in rows], [('role', True)])
        self.assertIn('«Cobró»', rows[0]['detail'])

    def test_modalised_or_attributed_conduct_stays_silent(self):
        self.assertEqual(self.rows(role='Presuntamente cobró 200.000 euros del constructor.'), [])
        self.assertEqual(self.rows(role='Según Infobae (16-07-2026), cobró 200.000 euros.'), [])
        self.assertEqual(self.rows(role='El informe de la UCO dice que cobró 200.000 euros.'), [])
        # «el pago» es un sustantivo neutro: sin tilde no hay verbo que afirme nada.
        self.assertEqual(self.rows(role='Tramitó el pago de la obra en plazo.'), [])

    def test_one_finding_per_sentence(self):
        rows = self.rows(summary='Está procesado por cohecho. Fue acusado en julio. '
                                 'Nació en Toledo.')
        self.assertEqual(len(rows), 2)

    def test_feed_item_naming_the_person_fires(self):
        rows = self.rows(feed='El juez lo declaró procesado esta semana.')
        self.assertEqual([(r['object_kind'], r['field']) for r in rows],
                         [('feed_item', 'dek')])
        self.assertTrue(rows[0]['ref'].startswith('pipeline/data/ultima_hora/feed.yaml#'))
        self.assertEqual(rows[0]['entity_id'], 'Person:reo')

    def test_feed_item_within_the_registry_stays_silent(self):
        self.assertEqual(self.rows(feed='El juez lo mantiene investigado.'), [])

    def test_person_without_a_registry_row_defaults_to_investigado(self):
        rows = lint([person('suelto', name='Suelto Sin Fila',
                            summary='Fue acusado formalmente en junio.'),
                     other('cohecho', 'Crime', 'cohecho')],
                    [edge('r', 'suelto', 'cohecho', 'ACCUSED_OF', source_kind='judicial')])['L22']
        self.assertIn('registro «investigado»', rows[0]['detail'])

    def test_person_without_any_cause_is_not_a_subject(self):
        rows = lint([person('ajeno', name='Ajeno Total',
                            summary='Fue acusado formalmente en junio.')])['L22']
        self.assertEqual(rows, [])


class L23Test(unittest.TestCase):
    """Particular con acusación de prensa."""

    FIGURES = {'entities': [{'name': 'Figura Publica', 'type': 'Person',
                             'role': 'Alcaldesa de Villa Baja'}]}

    def rows(self, nodes, rels, files=None):
        return lint(nodes + [other('cohecho', 'Crime', 'cohecho')], rels,
                    {'pipeline/data/context/figures.yaml': self.FIGURES, **(files or {})})['L23']

    def test_public_figure_with_a_press_accusation_stays_silent(self):
        rows = self.rows([person('figura', name='Figura Publica')],
                         [edge('r', 'figura', 'cohecho', 'ACCUSED_OF', source_kind='prensa')])
        self.assertEqual(rows, [])

    def test_boe_office_holder_stays_silent(self):
        rows = self.rows([person('cargo', name='Cargo Del Boe', is_boe=True)],
                         [edge('r', 'cargo', 'cohecho', 'ACCUSED_OF', source_kind='prensa')])
        self.assertEqual(rows, [])

    def test_private_person_with_a_press_accusation_fires(self):
        rows = self.rows([person('particular', name='Particular Cualquiera')],
                         [edge('r', 'particular', 'cohecho', 'ACCUSED_OF',
                               source_kind='ultima_hora')])
        self.assertEqual([r['entity_id'] for r in rows], ['Person:particular'])
        self.assertIn('ultima_hora', rows[0]['detail'])

    def test_private_person_with_procedural_prose_fires(self):
        rows = self.rows([person('particular', name='Particular Cualquiera',
                                 role='Imputado en la pieza de amaños.')], [])
        self.assertEqual([r['entity_id'] for r in rows], ['Person:particular'])
        self.assertIn('prosa con fase procesal: imputado', rows[0]['detail'])

    def test_negated_procedural_prose_stays_silent(self):
        # 063 (2026-09-29): «No consta investigada», «no fue acusado» no afirman la fase.
        rows = self.rows([person('funcionaria', name='Funcionaria Cualquiera',
                                 role='Vocal de la mesa. No consta investigada.',
                                 summary='Declaró como testigo; no fue acusado ni condenado.'),
                          person('otro', name='Otro Particular',
                                 role='No consta investigado. Imputado en la pieza de amaños.')], [])
        self.assertEqual([r['entity_id'] for r in rows], ['Person:otro'])
        self.assertIn('prosa con fase procesal: imputado', rows[0]['detail'])

    def test_private_person_with_a_registry_row_stays_silent(self):
        rows = self.rows([person('particular', name='Particular Cualquiera')],
                         [edge('r', 'particular', 'cohecho', 'ACCUSED_OF', source_kind='prensa')],
                         {'pipeline/data/accusations/accusations.yaml':
                          {'accusations': [estado_row('Person:particular', 'investigado')]}})
        self.assertEqual(rows, [])

    def test_pseudonym_and_anonymised_nodes_stay_silent(self):
        rows = self.rows([person('paulino', name='Paulino', is_pseudonym=True,
                                 role='Investigado en la causa especial.'),
                          person('menor', name='Familiar Anonimizado', anonymized=True,
                                 role='Investigado en la causa especial.')], [])
        self.assertEqual(rows, [])

    def test_judicial_accusation_alone_is_not_an_L23_cause(self):
        rows = self.rows([person('particular', name='Particular Cualquiera')],
                         [edge('r', 'particular', 'cohecho', 'ACCUSED_OF', source_kind='judicial')])
        self.assertEqual(rows, [])


class L24Test(unittest.TestCase):
    """Familiar sin decisión del dueño."""

    NODES = [person('pariente', name='Pariente Sin Causa'),
             person('investigado', name='Investigado Central')]
    RELS = [edge('f', 'pariente', 'investigado', 'FAMILY_OF', source_kind='contexto')]

    def test_family_only_person_without_the_yaml_fires(self):
        rows = lint(self.NODES, self.RELS)['L24']
        self.assertEqual([r['entity_id'] for r in rows],
                         ['Person:investigado', 'Person:pariente'])
        self.assertIn('FAMILY_OF×1', rows[0]['detail'])
        self.assertEqual(rows[0]['field'], 'owner_reviewed')

    def test_reviewed_in_the_yaml_stays_silent(self):
        files = {'pipeline/data/context/familiares_revisados.yaml': {'revisados': [
            {'entity_id': 'Person:pariente', 'decision': 'conservar-con-papel',
             'fecha': '2026-09-21', 'reason': 'administradora receptora de pagos'},
            {'entity_id': 'Person:investigado', 'decision': 'podar',
             'fecha': '2026-09-21', 'reason': 'solo parentesco'}]}}
        self.assertEqual(lint(self.NODES, self.RELS, files)['L24'], [])

    def test_owner_reviewed_prop_stays_silent(self):
        nodes = [person('pariente', name='Pariente Sin Causa', owner_reviewed=True),
                 person('investigado', name='Investigado Central', owner_reviewed=True)]
        self.assertEqual(lint(nodes, self.RELS)['L24'], [])

    def test_family_member_with_a_cause_stays_silent(self):
        rels = self.RELS + [
            edge('a', 'pariente', 'cohecho', 'ACCUSED_OF', source_kind='judicial'),
            edge('p', 'investigado', 'causa', 'PARTY_TO', source_kind='judicial')]
        nodes = self.NODES + [other('cohecho', 'Crime', 'cohecho'),
                              other('causa', 'LegalCase', 'DP 77/2024')]
        self.assertEqual(lint(nodes, rels)['L24'], [])

    def test_registry_row_also_counts_as_a_decision(self):
        files = {'pipeline/data/accusations/accusations.yaml': {'accusations': [
            estado_row('Person:pariente', 'investigado'),
            estado_row('Person:investigado', 'imputado')]}}
        self.assertEqual(lint(self.NODES, self.RELS, files)['L24'], [])


class L25Test(unittest.TestCase):
    """Atribución vaga."""

    def test_vague_attribution_in_prose_fires(self):
        rows = lint([person('vago', name='Vago Atribuido',
                            summary='Según la prensa, negoció el rescate.'),
                     person('preciso', name='Preciso Atribuido',
                            summary='Según Infobae (16-07-2026), negoció el rescate.')])['L25']
        self.assertEqual([(r['entity_id'], r['field']) for r in rows],
                         [('Person:vago', 'summary')])
        self.assertIn('Según la prensa', rows[0]['detail'])

    def test_role_and_description_are_read_too(self):
        rows = lint([person('vago', name='Vago Atribuido',
                            role='Empresario; según los medios, intermedió en el rescate.',
                            description='Según las informaciones, cobró una comisión.')])['L25']
        self.assertEqual(sorted(r['field'] for r in rows), ['description', 'role'])

    def test_vague_attribution_in_the_feed_fires(self):
        files = {'pipeline/data/ultima_hora/feed.yaml': {'items': [
            {'iso': '2026-09-01', 'headline': 'Según los medios, el juez cita al exministro',
             'dek': 'La comparecencia se fijó para el martes, según El Mundo (01-09-2026).',
             'outlet': 'El Mundo', 'url': 'https://elmundo.example/x', 'entities': []}]}}
        rows = lint([], [], files)['L25']
        self.assertEqual([(r['object_kind'], r['field']) for r in rows],
                         [('feed_item', 'headline')])


class RegistryTest(unittest.TestCase):
    def test_the_061_checks_are_untouched(self):
        self.assertEqual([(cid, CHECKS[cid]['severity']) for cid, _ in CHECKS_061], CHECKS_061)

    def test_the_063_checks_are_registered_as_warn(self):
        self.assertEqual([(c, CHECKS[c]['severity']) for c in ESC_CHECKS],
                         [(c, 'warn') for c in ESC_CHECKS])
        self.assertEqual(sorted(CHECKS, key=lambda c: int(c[1:])),
                         [f'L{i}' for i in range(1, 27)])  # + L26 (070), bloqueante

    def test_run_accepts_a_list_of_new_check_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            snapshot = repo / 'neo4j' / 'vacio'
            snapshot.mkdir(parents=True)
            for name in ('nodes', 'relationships'):
                (snapshot / f'{name}.jsonl').write_text('')
            (snapshot / 'snapshot.json').write_text('{}')
            summary = run(snapshot, repo / 'lint', AS_OF, only=list(ESC_CHECKS), repo=repo)
        self.assertEqual(sorted(summary, key=lambda c: int(c[1:])), list(ESC_CHECKS))
        self.assertEqual({v['count'] for v in summary.values()}, {0})


if __name__ == '__main__':
    unittest.main()
