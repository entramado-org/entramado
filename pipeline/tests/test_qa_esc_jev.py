"""Deterministic tests for Jev as classifier and router (spec 063, T7). No model, no network.

`entity_facts` (T5) and the SDK client are stubbed: this suite proves the rules, the states
that leave the Mac, the gold and the adoption gate — never that the API answers anything.
"""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pipeline.src import qa_esc_jev as jev
from pipeline.src.qa_dual_jev import ConfigError
from pipeline.src.qa_integral import connect
from pipeline.tests.test_qa_dual import DualBase

FACTS = {'name': 'Alejo Pérez', 'type': 'Person', 'aliases': ['Alejo Pérez', 'A. Pérez'],
         'is_pseudonym': False, 'pseudonym_note': '', 'anonymized': False, 'degree': 12,
         'context_role': 'Exministro del ramo.', 'is_boe': False, 'family_of': [],
         # `entity_facts` hands over the WHOLE accusations.yaml row, quotes included.
         'estado_row': {'entity_id': 'Person:alejo', 'status': 'investigado',
                        'estado': {'status': 'investigado', 'fecha': '2026-05-18',
                                   'fuente': {'doc': 'auto-zapatero.pdf', 'tipo': 'auto'}},
                        'crimes': [{'name': 'cohecho', 'quote': 'literal del auto judicial'}]},
         'feed_items': [{'headline': 'Titular reciente', 'iso': '2026-09-01'}],
         'accused_status_null': 1, 'edge_counts': {'ACCUSED_OF': 1, 'PAID': 1},
         'only_associative': False, 'a5_familiar': False}


def facts(**over):
    merged = {**FACTS, **over}
    return lambda conn, repo, entity_id: merged


def unit(kind='summary', tier='T0', value='Alejo pagó 5.700 euros.', unit_id='a' * 64, field='/summary'):
    return {'unit_id': unit_id, 'kind': kind, 'tier': tier, 'field': field,
            'value': json.dumps(value, ensure_ascii=False)}


def edge_unit(rel_type='ACCUSED_OF', tier='T1', unit_id='b' * 64, **props):
    value = {'type': rel_type, 'start': 'Person:alejo', 'end': 'Crime:cohecho',
             'props': {'source_kind': 'judicial', 'status': 'investigado',
                       'source_quote': 'literal del informe UCO', **props},
             'public_label': 'Acusado de'}
    return {'unit_id': unit_id, 'kind': 'edge', 'tier': tier, 'field': '',
            'value': json.dumps(value, ensure_ascii=False)}


def decision(unit_id='a' * 64, verdict='verificado', label='ok', confidence='alta',
             evidence='informe p.12 sostiene la frase', **over):
    base = {'entity_id': 'Person:alejo', 'notes': '',
            'units': [{'unit_id': unit_id, 'verdict': verdict, 'label': label,
                       'confidence': confidence, 'evidence': evidence, 'requested_sources': []}],
            'summary': {'action': 'none'}, 'role': {'action': 'none'}, 'alias_deny': [],
            'estado': {'action': 'none'}, 'edges': [], 'editorial': []}
    base.update(over)
    return base


def judgments(**over):
    base = {'clase': 'figura_publica', 'riesgo': 'medio', 'menor_o_victima': False,
            'afirma_conducta': False, 'mas_alla_del_registro': False, 'dato_biografico': False,
            'valoracion_o_etiqueta': False, 'nombra_tercero': False, 'apoyo_en_prensa': False,
            'riesgo_unidad': 'medio'}
    base.update(over)
    return base


ENTITY = {'id': 'Person:alejo', 'flags': {'seudonimo': False, 'anonimizado': False,
                                          'familiar_sin_acusacion': False}}


class FakeJev:
    """The SDK as `classify` uses it: `system_one` per item, typed answers back."""

    def __init__(self, model='jev-1.13.0'):
        self.model, self.calls = model, []

    def system_one(self, state, questions):
        self.calls.append({'state': state, 'questions': questions})
        levels = questions['riesgo_entidad']['criteria']
        return NS(model=self.model, usage=NS(input_tokens=1200, output_tokens=60),
                  choices={'clase_persona': NS(choice='particular_mencionado', confidence=0.7,
                                               probabilities={'particular_mencionado': 0.7, 'figura_publica': 0.3})},
                  nouls={'menor': NS(noul=0.1)},
                  scores={'riesgo_entidad': NS(score=2, confidence=0.8,
                                               probabilities={i: (0.6 if i == 2 else 0.4 / (len(levels) - 1))
                                                              for i in range(len(levels))})})


class QuestionsTest(unittest.TestCase):
    def test_three_groups_and_nothing_about_identity(self):
        groups = jev.load_questions()
        self.assertEqual({g: len(q) for g, q in groups.items()},
                         {'entidad': 4, 'unidad': 7, 'ruta': 1})
        names = [name for group in groups.values() for name in group]
        for name in names:
            self.assertNotRegex(name, r'identidad|alias|seud')
        self.assertEqual(jev.questions_for('unidad'), groups['unidad'])
        # Spec 069: no compound predicate survives (they became exclusive choices).
        for gone in ('afirma_conducta', 'menor_o_victima', 'valoracion_o_etiqueta', 'apoyo_en_prensa'):
            self.assertNotIn(gone, names)
        self.assertNotIn('Fable', json.dumps(groups, ensure_ascii=False))
        # Every question is one of the three `decide` primitives.
        self.assertLessEqual({q['type'] for group in groups.values() for q in group.values()},
                             {'noul', 'choice', 'score'})


class StateTest(DualBase):
    def setUp(self):
        super().setUp()
        self.db = connect(self.root)
        self.addCleanup(self.db.close)

    def test_entity_state_carries_flags_and_no_sources(self):
        with patch.object(jev, 'entity_facts', facts()):
            state = jev.entity_state(self.db, self.base, 'Person:alejo')
        self.assertEqual(state['entidad']['id'], 'Person:alejo')
        self.assertEqual(state['flags']['investigado_encausado'], True)
        self.assertEqual(state['flags']['figura_publica'], True)
        self.assertEqual(state['flags']['autoridad_judicial'], False)
        self.assertEqual(state['feed'], [{'titular': 'Titular reciente', 'fecha': '2026-09-01'}])
        self.assertNotIn('sources', json.dumps(state))
        self.assertLess(jev.state_tokens(state), jev.MAX_STATE_TOKENS)

    def test_states_never_carry_a_quote_from_an_investigation_report(self):
        with patch.object(jev, 'entity_facts', facts()):
            entity = jev.entity_state(self.db, self.base, 'Person:alejo')
        state = jev.unit_state(edge_unit(), entity, judgments(), self.db)
        rendered = json.dumps(state, ensure_ascii=False)
        self.assertNotIn('literal del informe UCO', rendered)
        for key in ('sources', 'chunks', 'source_quote'):
            self.assertNotIn(f'"{key}"', rendered)
        self.assertEqual(state['unidad']['arista']['type'], 'ACCUSED_OF')
        self.assertEqual(state['vigencia'], {'ultimo_acto': 'investigado', 'fecha': '2026-05-18'})
        # El registro viaja comprimido: estado, fecha y documento, nunca las citas de los delitos.
        self.assertNotIn('literal del auto judicial', rendered)
        self.assertEqual(state['registro']['fuente'], {'doc': 'auto-zapatero.pdf', 'tipo': 'auto'})

    def test_long_text_is_clipped_and_sources_are_only_the_ones_cited(self):
        long_unit = unit(value='x' * 5000 + ' https://example.com/fuente y original.pdf')
        with patch.object(jev, 'entity_facts', facts(summary='y' * 9000)):
            entity = jev.entity_state(self.db, self.base, 'Person:alejo')
        state = jev.unit_state(long_unit, entity, judgments(), self.db)
        self.assertEqual(len(entity['summary']), jev.MAX_TEXT_CHARS)
        self.assertEqual(len(state['unidad']['texto']), jev.MAX_TEXT_CHARS)
        cited = jev.unit_state(unit(value='ver https://example.com/a y original.pdf'), entity, None)
        self.assertEqual(cited['fuentes_citadas'], ['https://example.com/a', 'original.pdf'])

    def test_route_state_summarises_the_reader(self):
        state = jev.route_state(unit(), ENTITY, decision()['units'][0],
                                {'notes': False, 'searches': 4, 'summary': 'replace', 'role': 'none',
                                 'estado': 'none', 'edge_actions': set()},
                                judgments(), 'escalar')
        self.assertEqual(state['opus']['label'], 'ok')
        self.assertEqual(state['opus']['accion'], 'replace')
        self.assertEqual(state['opus']['evidencia']['tipo'], 'corpus')
        self.assertFalse(state['opus']['cero_apariciones'])
        self.assertEqual(state['regla']['ruta_baseline'], 'escalar')
        zero = jev.route_state(unit(), ENTITY, decision(evidence='0 apariciones en corpus')['units'][0],
                               {}, judgments(), 'dueno')
        self.assertTrue(zero['opus']['cero_apariciones'])

    def test_flags_read_the_rules_not_the_model(self):
        with patch.object(jev, 'entity_facts', facts(context_role='Magistrada instructora',
                                                     estado_row=None, edge_counts={'MET_WITH': 3},
                                                     only_associative=True, is_pseudonym=True)):
            out = jev.flags(self.db, self.base, 'Person:benito')
        self.assertTrue(out['autoridad_judicial'])
        self.assertTrue(out['seudonimo'])
        self.assertTrue(out['solo_mencionado'])
        self.assertFalse(out['investigado_encausado'])

    def test_items_are_decide_shaped(self):
        with patch.object(jev, 'entity_facts', facts()):
            state = jev.entity_state(self.db, self.base, 'Person:alejo')
        items = jev.items_for('entidad', [{'id': 'Person:alejo', 'state': state, 'meta': {'k': 1}}])
        self.assertEqual(sorted(items[0]), ['id', 'meta', 'state'])
        self.assertEqual(items[0]['meta']['grupo'], 'entidad')
        self.assertGreater(items[0]['meta']['state_tokens'], 0)
        with self.assertRaises(ValueError):
            jev.items_for('otro', [])


class BaselineRouteTest(unittest.TestCase):
    """The fixed matrix B1-B3. Jev never appears here: these are rules, not judgments."""

    def route(self, unit_row=None, entity=None, dec=None, judged=None):
        return jev.baseline_route(unit_row or unit(), entity or ENTITY, dec or decision(),
                                  judged or judgments())

    def test_owner_cases(self):
        seudo = {'id': 'Person:benito', 'flags': {'seudonimo': True}}
        self.assertEqual(self.route(entity=seudo), 'dueno')
        self.assertEqual(self.route(entity={'id': 'x', 'flags': {'anonimizado': True}}), 'dueno')
        self.assertEqual(self.route(entity={'id': 'x', 'flags': {'familiar_sin_acusacion': True}}), 'dueno')
        self.assertEqual(self.route(unit_row=unit(kind='identity', unit_id='c' * 64),
                                    dec=decision(unit_id='c' * 64)), 'dueno')
        self.assertEqual(self.route(unit_row=unit(kind='identity', unit_id='c' * 64),
                                    dec=decision(unit_id='c' * 64, verdict='corregido', label='alias',
                                                 alias_deny=['Alejo'])), 'dueno')
        self.assertEqual(self.route(dec=decision(verdict='corregido', label='estado')), 'dueno')
        # Piloto 2026-09-21: las notas ya no mandan al dueño por sí solas (Opus las escribe siempre).
        self.assertNotEqual(self.route(dec=decision(notes='no tengo claro el parentesco')), 'dueno')
        self.assertEqual(self.route(judged=judgments(clase='familiar_no_investigado')), 'dueno')
        self.assertEqual(self.route(judged=judgments(riesgo='critico')), 'dueno')
        self.assertEqual(self.route(judged=judgments(menor_o_victima=True)), 'dueno')
        self.assertEqual(self.route(judged=judgments(clase='particular_mencionado',
                                                     valoracion_o_etiqueta=True)), 'dueno')

    def test_uncertain_accusatory_edge_goes_to_owner(self):
        # Spec 069: doubt on an accusatory edge is never settled by another model.
        unsure = decision(unit_id='b' * 64, verdict='incierto', label='ok', requested_sources=['x'])
        self.assertEqual(self.route(unit_row=edge_unit(), dec=unsure), 'dueno')
        self.assertEqual(self.route(unit_row=edge_unit(rel_type='FAMILY_OF', tier='T2'), dec=unsure), 'dueno')
        self.assertEqual(self.route(unit_row=edge_unit(rel_type='WORKS_FOR', tier='T2'), dec=unsure), 'escalar')

    def test_escalation_cases(self):
        self.assertEqual(self.route(judged=judgments(riesgo='alto')), 'escalar')
        self.assertEqual(self.route(unit_row=edge_unit(), dec=decision(unit_id='b' * 64)), 'escalar')
        self.assertEqual(self.route(dec=decision(verdict='corregido', label='retirada')), 'escalar')
        # A rewrite changes published facts: B2 escalates it whatever the risk (spec AC6/AC7).
        self.assertEqual(self.route(judged=judgments(riesgo='bajo'),
                                    dec=decision(verdict='corregido', label='reescrita')), 'escalar')
        self.assertEqual(self.route(dec=decision(verdict='incierto', label='ok',
                                                 requested_sources=['x'])), 'escalar')
        self.assertEqual(self.route(judged=judgments(clase='particular_mencionado',
                                                     afirma_conducta=True)), 'escalar')
        self.assertEqual(self.route(judged=judgments(nombra_tercero=True)), 'escalar')
        self.assertEqual(self.route(dec={**decision(), 'searches': 13}), 'escalar')
        denied = decision(unit_id='b' * 64, verdict='corregido', label='arista',
                          edges=[{'unit_id': 'b' * 64, 'action': 'deny', 'reason': 'sin apoyo'}])
        self.assertEqual(self.route(unit_row=edge_unit(rel_type='PAID', tier='T2'), dec=denied), 'escalar')
        self.assertEqual(self.route(dec=decision(verdict='corregido', label='reescrita',
                                                 summary={'action': 'remove'})), 'escalar')
        # Close conditions not all met → escalate, never close.
        self.assertEqual(self.route(dec=decision(confidence='media')), 'escalar')
        self.assertEqual(self.route(dec=decision(evidence='')), 'escalar')

    def test_closing_case(self):
        self.assertEqual(self.route(), 'cerrar')
        self.assertEqual(self.route(dec=decision(verdict='corregido', label='atrib')), 'cerrar')
        self.assertEqual(self.route(judged=judgments(riesgo='bajo'),
                                    dec=decision(verdict='corregido', label='cita')), 'cerrar')

    def test_harden_never_loosens(self):
        self.assertEqual(jev.harden('cerrar', 'dueno'), 'dueno')
        self.assertEqual(jev.harden('escalar', 'cerrar'), 'escalar')
        self.assertEqual(jev.harden('dueno', 'escalar'), 'dueno')
        self.assertEqual(jev.harden('cerrar', 'ok'), 'cerrar')
        self.assertEqual(jev.harden('cerrar', None), 'cerrar')

    def test_classification_thresholds_prefer_recall(self):
        answers = {'clase_persona': {'type': 'choice', 'choice': 'testigo', 'confidence': 0.6,
                                     'probabilities': {'testigo': 0.6}},
                   'victima': {'type': 'noul', 'noul': 0.51},
                   'conducta': {'type': 'choice', 'choice': 'conducta_modalizada',
                                'probabilities': {'sin_conducta': 0.1, 'conducta_modalizada': 0.4,
                                                  'conducta_afirmada': 0.5}},
                   'riesgo_entidad': {'type': 'score', 'score': 1, 'confidence': 0.5,
                                      'probabilities': {'bajo: x': 0.2, 'medio: y': 0.45,
                                                        'alto: z': 0.25, 'critico: w': 0.1}}}
        out = jev.classification_from(answers)
        self.assertEqual(out['clase'], 'testigo')
        self.assertTrue(out['menor_o_victima'])  # victima ≥ τ (spec 069: menor y víctima por separado)
        self.assertTrue(out['afirma_conducta'])  # P(conducta_afirmada) ≥ τ aunque gane otra opción
        self.assertEqual(out['riesgo'], 'alto')  # argmax medio, pero P(alto)+P(critico) ≥ 0,30


class ClassifyTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.attempt = Path(self.tmp.name) / 'attempt-1'
        self.questions = jev.load_questions()['entidad']
        self.items = [{'id': 'Person:alejo', 'state': {'entidad': {'id': 'Person:alejo'}}, 'meta': {}}]

    def test_records_the_resolved_model_and_journals_every_call(self):
        client = FakeJev()
        results, receipt = jev.classify(client, self.items, self.questions, self.attempt)
        self.assertEqual(receipt['model_resolved'], 'jev-1.13.0')
        self.assertIsNone(receipt['error'])
        self.assertEqual(results[0]['answers']['clase_persona']['choice'], 'particular_mencionado')
        self.assertEqual(results[0]['answers']['riesgo_entidad']['type'], 'score')
        # The rubric level's own text is the probability key, as `decide` writes it.
        self.assertTrue(any(k.startswith('alto:') for k in
                            results[0]['answers']['riesgo_entidad']['probabilities']))
        lines = (self.attempt / 'calls.jsonl').read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads((self.attempt / 'receipt.json').read_text())['items'], 1)

    def test_a_model_that_is_not_the_measured_one_halts(self):
        with self.assertRaises(ConfigError):
            jev.classify(FakeJev(model='jev-1.14.0'), self.items, self.questions, self.attempt)
        receipt = json.loads((self.attempt / 'receipt.json').read_text())
        self.assertEqual(receipt['model_resolved'], 'jev-1.14.0')
        self.assertTrue(receipt['error'].startswith('config:'))


class GoldBase(DualBase):
    """A census with two units already resolved by the owner, one of them in his tandas."""

    def setUp(self):
        super().setUp()
        self.db = connect(self.root)
        self.addCleanup(self.db.close)
        context = self.base / 'pipeline' / 'data' / 'context'
        context.mkdir(parents=True)
        (context / 'pseudonyms.yaml').write_text('pseudonyms:\n- id: Person:benito\n  note: seudónimo\n')
        (context / 'figures.yaml').write_text(
            'entities:\n  - {name: "Alejo Pérez", type: Person, role: "Exministro"}\n')
        accusations = self.base / 'pipeline' / 'data' / 'accusations'
        accusations.mkdir(parents=True)
        (accusations / 'accusations.yaml').write_text(
            'accusations:\n- person: Alejo Pérez\n  entity_id: Person:alejo\n'
            '  estado: {status: investigado, fecha: "2026-05-18"}\n')
        self.summary_unit = self.unit_like('summary', '%5.700%')[1]
        self.benito = self.units(kind='summary')
        self.pseudonym_unit = next(u['unit_id'] for u in self.benito if 'seud' in u['value'])
        self.resolve(self.summary_unit, 'humano-2026-09-17e-corregido',
                     '[reescrita] «pagó 5.700 euros»: 0 apariciones en corpus → retirado')
        self.resolve(self.pseudonym_unit, 'humano-2026-09-17e', '[ok] la frase es correcta')
        self.revision = self.base / 'REVISION.md'
        self.revision.write_text(
            f'| `Person:alejo` | 1/0 | S ✎ | ({self.summary_unit[:5]}) [reescrita] tanda del dueño |\n')

    def resolve(self, unit_id, resolution, evidence):
        with self.db:
            self.db.execute('UPDATE units SET state=?,resolution=? WHERE unit_id=?',
                            ('verified', resolution, unit_id))
            seq = self.db.execute('SELECT coalesce(max(seq),0)+1 FROM events').fetchone()[0]
            self.db.execute('INSERT INTO events(seq,at,unit_id,event,payload) VALUES (?,?,?,?,?)',
                            (seq, '2026-09-17T00:00:00Z', unit_id,
                             'corregido-humano' if resolution.endswith('-corregido') else 'verificado-humano',
                             json.dumps({'evidence': 'Revisión humana T0e: ' + evidence}, ensure_ascii=False)))

    def gold(self):
        with patch.object(jev, 'A5_FINDING', self.base / 'no-such-finding.md'), \
                patch.object(jev, 'T0E_REVISION', self.revision):
            return {row['id']: row for row in jev.gold(self.db, self.base)}


class GoldTest(GoldBase):
    """The gold the calibration is scored against, from resolutions and events of the 061."""

    def test_labels_classes_risks_and_routes(self):
        rows = self.gold()
        self.assertEqual(len(rows), 2)
        corrected = rows[self.summary_unit]
        self.assertEqual(corrected['gold']['etiqueta'], 'reescrita')
        self.assertEqual(corrected['gold']['causa'], 'sin_fuente')
        self.assertEqual(corrected['gold']['corregido'], 'no-ok')
        self.assertEqual(corrected['gold']['clase'], 'figura_publica')
        self.assertEqual(corrected['gold']['ruta'], 'dueno')  # la entidad va marcada ✎
        # Las clases deterministas no son excluyentes: Alejo está en figures.yaml Y tiene
        # ACCUSED_OF, así que ambas son respuestas válidas (`clase` es solo la de más prioridad).
        self.assertEqual(corrected['gold']['clases_validas'],
                         ['figura_publica', 'investigado_encausado'])
        verified = rows[self.pseudonym_unit]
        self.assertEqual(verified['gold']['etiqueta'], 'ok')
        self.assertEqual(verified['gold']['clase'], 'seudonimo')
        self.assertEqual(verified['gold']['clases_validas'], ['seudonimo'])
        self.assertEqual(verified['gold']['riesgo'], 'alto')  # seudónimo judicial
        self.assertEqual(verified['gold']['ruta'], 'cerrar')

    def test_a_unit_outside_the_owner_tandas_escalates(self):
        self.revision.write_text('| `Person:otro` | 1/0 | S ✎ | (00000) [ok] otra entidad |\n')
        rows = self.gold()
        self.assertEqual(rows[self.summary_unit]['gold']['ruta'], 'escalar')
        self.assertEqual(rows[self.summary_unit]['gold']['riesgo'], 'medio')

    def test_a5_names_come_from_the_finding(self):
        finding = self.base / 'a5.md'
        finding.write_text('| A5 | 2 familiares (Tatiana/Víctor Ábalos, S. Fernández Menéndez) | x |\n')
        groups = jev.a5_names(finding)
        self.assertTrue(jev._is_a5('Víctor Ábalos', groups))
        self.assertTrue(jev._is_a5('Tatiana Ábalos', groups))
        self.assertTrue(jev._is_a5('Susana Fernández Menéndez', groups))
        self.assertFalse(jev._is_a5('José Luis Ábalos', groups))


class CalibrateTest(GoldBase):
    def test_calibrate_exports_decide_files_without_calling_anything(self):
        out = self.base / 'jev-out'
        with patch.object(jev, 'entity_facts', facts()), \
                patch.object(jev, 'A5_FINDING', self.base / 'no-such-finding.md'), \
                patch.object(jev, 'T0E_REVISION', self.revision):
            summary = jev.calibrate(self.root, self.base, out, run_decide=False)
        self.assertEqual(summary['gold'], 2)
        self.assertEqual(summary['items']['ruta'], 0)  # sin decisiones de Opus todavía
        self.assertFalse((out / 'items.ruta.jsonl').exists())
        for name in ('gold.jsonl', 'items.entidad.jsonl', 'items.unidad.jsonl', 'calibration.md'):
            self.assertTrue((out / name).exists(), name)
        items = [json.loads(line) for line in (out / 'items.unidad.jsonl').read_text().splitlines()]
        self.assertEqual(sorted(items[0]), ['id', 'meta', 'state'])
        self.assertNotIn('source_quote', (out / 'items.unidad.jsonl').read_text())
        questions = json.loads((out / 'questions.entidad.json').read_text())
        self.assertEqual(sorted(questions), ['clase_persona', 'menor', 'riesgo_entidad', 'victima'])
        self.assertIn('Puerta de adopción', (out / 'calibration.md').read_text())


class ScoreTest(unittest.TestCase):
    """Scoring the classifier: a hit is any class the rules accept for that entity."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.gold = [
            {'id': 'u1', 'entity': 'Person:alejo', 'kind': 'summary', 'tier': 'T0',
             'gold': {'clase': 'figura_publica',
                      'clases_validas': ['figura_publica', 'investigado_encausado'],
                      'riesgo': 'medio', 'ruta': None, 'etiqueta': 'ok', 'causa': '', 'corregido': 'ok'}},
            {'id': 'u2', 'entity': 'Person:benito', 'kind': 'summary', 'tier': 'T0',
             'gold': {'clase': 'seudonimo', 'clases_validas': ['seudonimo'], 'riesgo': 'alto',
                      'ruta': None, 'etiqueta': 'ok', 'causa': '', 'corregido': 'ok'}}]

    def items(self, **tipos):
        rows = [{'id': entity, 'state': {'entidad': {'tipo': tipo}, 'role': '', 'flags': {}}, 'meta': {}}
                for entity, tipo in tipos.items()]
        (self.out / 'items.entidad.jsonl').write_text(
            ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')

    def pred(self, **choices):
        rows = [{'id': entity, 'backend': 'jev', 'model': 'jev-1.13.0',
                 'answers': {'clase_persona': {'type': 'choice', 'choice': choice, 'confidence': 0.8,
                                               'probabilities': {choice: 0.8}}}}
                for entity, choice in choices.items()]
        (self.out / 'pred.entidad.jsonl').write_text(
            ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')

    def test_a_secondary_valid_class_is_a_hit(self):
        self.items(**{'Person:alejo': 'Person', 'Person:benito': 'Person'})
        self.pred(**{'Person:alejo': 'investigado_encausado', 'Person:benito': 'seudonimo'})
        summary = jev._score(self.out, self.gold)
        # Solo Alejo puntúa: Benito es seudónimo y `seudonimo` no es opción de `clase_persona`.
        self.assertEqual((summary['clase']['n'], summary['clase']['exactitud']), (1, 1.0))
        self.assertNotIn('seudonimo', summary['clase']['recall'])
        self.assertEqual(summary['clase']['seudonimos'], {'n': 1, 'respuestas': {'seudonimo': 1}})

    def test_a_class_outside_the_set_is_a_miss(self):
        self.items(**{'Person:alejo': 'Person', 'Person:benito': 'Person'})
        self.pred(**{'Person:alejo': 'testigo', 'Person:benito': 'no_aplica'})
        summary = jev._score(self.out, self.gold)
        self.assertEqual((summary['clase']['n'], summary['clase']['exactitud']), (1, 0.0))
        # Sin entidades de esa clase en el gold no hay métrica (y la puerta falla cerrada).
        self.assertIsNone(summary['clase']['recall']['menor_o_victima'])


    def test_a_company_is_out_of_the_accuracy(self):
        """Para una no-persona `no_aplica` es correcto por construcción: ni acierto ni fallo."""
        gold = self.gold + [{'id': 'u3', 'entity': 'Company:empresa', 'kind': 'summary', 'tier': 'T0',
                             'gold': {'clase': 'investigado_encausado',
                                      'clases_validas': ['investigado_encausado'], 'riesgo': 'medio',
                                      'ruta': None, 'etiqueta': 'ok', 'causa': '', 'corregido': 'ok'}}]
        self.items(**{'Person:alejo': 'Person', 'Person:benito': 'Person', 'Company:empresa': 'Company'})
        self.pred(**{'Person:alejo': 'figura_publica', 'Person:benito': 'no_aplica',
                     'Company:empresa': 'no_aplica'})
        summary = jev._score(self.out, gold)
        self.assertEqual((summary['clase']['n'], summary['clase']['exactitud']), (1, 1.0))
        self.assertEqual(summary['clase']['no_person']['n'], 1)
        self.assertEqual(summary['clase']['no_person']['no_aplica_total'], 2)
        # Una PERSONA con `no_aplica` sí es un fallo real y se nombra.
        self.assertEqual(summary['clase']['no_person']['personas_con_no_aplica'], ['Person:benito'])

    def test_the_route_without_a_pilot_is_not_a_missing_metric(self):
        gate = jev.adoption_gate({'clase': {}, 'riesgo': {}, 'conducta': {}, 'ruta': {'n': 0}})
        self.assertFalse(gate['jev_route'])
        self.assertIn('ruta: sin pilotar (no hay decisiones de Opus sobre las que enrutar)', gate['razones'])


class RescoreTest(GoldBase):
    """`--rescore`: el informe se recalcula con los pred en disco y los ítems no se tocan."""

    def test_rescore_reuses_the_preds_and_never_rewrites_the_items(self):
        out = self.base / 'jev-out'
        with patch.object(jev, 'entity_facts', facts()), \
                patch.object(jev, 'A5_FINDING', self.base / 'no-such-finding.md'), \
                patch.object(jev, 'T0E_REVISION', self.revision):
            jev.calibrate(self.root, self.base, out, run_decide=False)
            entity = json.loads((out / 'items.entidad.jsonl').read_text().splitlines()[0])['id']
            (out / 'pred.entidad.jsonl').write_text(json.dumps(
                {'id': entity, 'backend': 'jev', 'model': 'jev-1.13.0',
                 'answers': {'clase_persona': {'type': 'choice', 'choice': 'figura_publica',
                                               'confidence': 0.9, 'probabilities': {'figura_publica': 0.9}}}},
                ensure_ascii=False) + '\n', encoding='utf-8')
            before = (out / 'items.entidad.jsonl').read_text()
            (out / 'items.entidad.jsonl').write_text(before.replace('"role":', '"role":'))
            summary = jev.calibrate(self.root, self.base, out, run_decide=False, rescore=True)
        self.assertTrue(summary['rescore'])
        self.assertEqual(summary['items']['entidad'], len(before.strip().splitlines()))
        self.assertEqual((out / 'items.entidad.jsonl').read_text(), before)  # el envío no se reescribe
        self.assertIn('clase', summary)  # ahora sí hay métricas
        self.assertTrue((out / 'gold.entidad.jsonl').exists())
        entity_gold = [json.loads(line) for line in (out / 'gold.entidad.jsonl').read_text().splitlines()]
        self.assertTrue(all(row['id'] == row['entity'] for row in entity_gold))
        self.assertIn('re-puntuación', (out / 'calibration.md').read_text())


class AdoptionGateTest(unittest.TestCase):
    PASS = {'clase': {'exactitud': 0.93, 'recall': {'familiar_no_investigado': 1.0,
                                                    'particular_mencionado': 0.96,
                                                    'menor_o_victima': 1.0}},
            'riesgo': {'auc': 0.81, 'ece': 0.07},
            'conducta': {'recall': 0.94},
            'ruta': {'n': 120, 'falso_cierre': 0.02, 'falso_cierre_baseline': 0.03, 'recall_dueno': 1.0,
                     'cobertura': 0.55, 'cobertura_baseline': 0.40}}

    def test_a_passing_summary_adopts_both_roles(self):
        out = jev.adoption_gate(self.PASS)
        self.assertEqual((out['jev_class'], out['jev_route']), (True, True))
        self.assertEqual(out['razones'], [])

    def test_a_failing_summary_names_every_reason(self):
        failing = json.loads(json.dumps(self.PASS))
        failing['clase']['recall']['particular_mencionado'] = 0.80
        failing['riesgo']['ece'] = 0.22
        failing['ruta']['recall_dueno'] = 0.9
        failing['ruta']['cobertura'] = 0.42
        failing['conducta']['recall'] = None
        out = jev.adoption_gate(failing)
        self.assertEqual((out['jev_class'], out['jev_route']), (False, False))
        reasons = ' '.join(out['razones'])
        self.assertIn('recall de particular_mencionado', reasons)
        self.assertIn('ECE de riesgo', reasons)
        self.assertIn('recall de dueño', reasons)
        self.assertIn('cobertura', reasons)
        self.assertIn('sin métrica', reasons)
        # Sin métricas de ruta no se adopta nada: la puerta falla cerrada.
        self.assertEqual(jev.adoption_gate({})['jev_route'], False)


if __name__ == '__main__':
    unittest.main()
