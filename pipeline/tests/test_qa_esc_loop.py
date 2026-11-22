"""Spec 063 T8 — orquestador, latido y consulta G7. Sin modelos, sin red, sin grafo.

El censo es el falso de `tests/test_qa_esc_packs.EscBase` (que hereda el de la 061) y todo lo
que llamaría a un modelo —`qa_esc_opus.run_claude`, `qa_esc_jev.classify` y el módulo de T9
`qa_esc_escalate`— viaja como stub. Ningún test escribe en el ledger real.
"""
from contextlib import contextmanager
import fcntl
import io
import json
import tempfile
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from src import qa_esc as E
from src import qa_esc_jev, qa_esc_opus, qa_esc_packs
from src.qa_integral import canonical, connect, now
from tests.test_qa_esc_packs import ALEJO, BENITO, EMPRESA, EscBase

# El paquete bajo el que `qa_esc` importa a sus hermanos (`src` o `pipeline.src`).
ESC_PKG = E.__name__.rsplit('.', 1)[0]
BRIEF = 'BRIEF de prueba: lee la entidad entera y devuelve el JSON del esquema.\n'
USAGE = {'input': 10, 'output': 5, 'cache_write': 2, 'reasoning': 3, 'cached': 100}
FRESH = USAGE['input'] + USAGE['output'] + USAGE['cache_write'] + USAGE['reasoning']


def decision_for(manifest, verdict='verificado', label='ok', **over):
    """Una decisión válida que cubre cada unidad del pack exactamente una vez."""
    out = {'entity_id': manifest['entity']['id'],
           'units': [{'unit_id': unit['unit_id'], 'verdict': verdict, 'label': label,
                      'evidence': 'original.pdf p.1', 'confidence': 'alta', 'requested_sources': []}
                     for unit in manifest['units']],
           'summary': {'action': 'none', 'resumen': '', 'sources': [], 'reason': ''},
           'role': {'action': 'none', 'role': '', 'reason': ''}, 'alias_deny': [],
           'estado': {'action': 'none', 'status': '', 'fecha': '', 'doc': '', 'url': '', 'tipo': '',
                      'nota': ''},
           'edges': [], 'editorial': [], 'notes': ''}
    out.update(over)
    return out


class FakeReader:
    """`qa_esc_opus.run_claude` sin proceso: devuelve lo que el test le dé, con recibo."""

    def __init__(self, decide=None, error=None, effort=None, usage=None, searches=('ev.py "alejo"',)):
        self.decide, self.error, self.effort = decide, error, effort
        self.usage = USAGE if usage is None else usage
        self.searches = list(searches)
        self.calls = []

    def __call__(self, attempt_dir, side, model, effort, system, schema, input_text, **kwargs):
        stamp = kwargs.get('stamp') or {}
        self.calls.append({'side': side, 'model': model, 'effort': effort, 'system': system,
                           'stamp': stamp, 'tools_prefix': kwargs.get('tools_prefix'),
                           'extra_env': kwargs.get('extra_env'), 'input': input_text, 'schema': schema})
        receipt = {'attempt_id': Path(attempt_dir).name, 'side': side, 'model': model,
                   'effort': self.effort or effort, 'error': self.error, 'usage': self.usage,
                   'searches': self.searches, 'denials': [], 'cost_usd': 0.5,
                   'pack_sha256': stamp.get('pack_sha256'), 'brief_sha256': stamp.get('brief_sha256')}
        if self.error:
            return None, receipt
        decision = self.decide(stamp.get('pack_id')) if callable(self.decide) else self.decide
        return decision, receipt


def jev_answers(clase='figura_publica', riesgo='medio', **nouls):
    levels = {'bajo': 'bajo: x', 'medio': 'medio: x', 'alto': 'alto: x', 'critico': 'critico: x'}
    probabilities = {text: (0.91 if key == riesgo else 0.03) for key, text in levels.items()}
    answers = {'clase_persona': {'type': 'choice', 'choice': clase},
               'menor_o_victima': {'type': 'noul', 'noul': 0.02},
               'riesgo_entidad': {'type': 'score', 'score': 1, 'probabilities': probabilities},
               'riesgo_unidad': {'type': 'score', 'score': 1, 'probabilities': probabilities}}
    answers.update({name: {'type': 'noul', 'noul': value} for name, value in nouls.items()})
    return answers


class FakeJev:
    """`qa_esc_jev.classify`: una respuesta por ítem, sin SDK ni red."""

    def __init__(self, answers=None, route=None, fail=None):
        self.answers, self.route, self.fail = answers or jev_answers(), route, fail
        self.groups = []

    def __call__(self, client, items, questions, attempt_dir):
        group = 'ruta' if 'ruta' in questions else ('entidad' if 'clase_persona' in questions else 'unidad')
        self.groups.append((group, [item['id'] for item in items]))
        if self.fail is not None:
            raise self.fail
        results = []
        for item in items:
            if group == 'ruta':
                answers = {'ruta': {'type': 'choice', 'choice': self.route}}
            else:
                answers = self.answers
            results.append({'id': item['id'], 'backend': 'jev', 'model': 'jev-1.13.0',
                            'answers': answers, 'usage': {}, 'latency_ms': 1.0, 'meta': item['meta']})
        return results, {'attempt_id': Path(attempt_dir).name, 'side': 'jev', 'items': len(items),
                         'model_resolved': 'jev-1.13.0', 'error': None}


@contextmanager
def escalate_module(run_dual=None, dual=None, export=None, merge=None):
    """El módulo de T9 que el orquestador importa en perezoso, como stub.

    Se sustituye en `sys.modules` Y como atributo del paquete: si el módulo real ya está
    importado, `from . import qa_esc_escalate` lo resuelve por el atributo del paquete.
    """
    module = types.ModuleType(f'{ESC_PKG}.qa_esc_escalate')
    module.run_dual = run_dual or (lambda *a, **k: {})
    module.dual_agreement = dual or (lambda pack, decisions, **_: {'mode': 'arbitraje', 'final': None,
                                                              'per_unit': {}, 'reasons': [],
                                                              'closed_units': [], 'tanda_units':
                                                              [u['unit_id'] for u in pack['units']]})
    module.export_tanda = export or (lambda root, conn, packs, date: (None, None))
    module.merge_tanda_answers = merge or (lambda root, conn, answers, date: [])
    name = f'{ESC_PKG}.qa_esc_escalate'
    with patch.dict(sys.modules, {name: module}), \
            patch.object(sys.modules[ESC_PKG], 'qa_esc_escalate', module, create=True):
        yield module


class LoopBase(EscBase):
    def setUp(self):
        super().setUp()
        brief = self.base / E.SPEC_DIR / E.BRIEF
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text(BRIEF)
        E.save_state(self.root, E.initial_state())

    # ── helpers ───────────────────────────────────────────────────────────────

    def rows(self, state=None):
        where, params = ('WHERE state=?', (state,)) if state else ('', ())
        return [dict(r) for r in self.conn.execute(f'SELECT * FROM esc_packs {where} ORDER BY pack_id',
                                                   params)]

    def row(self, pack_id):
        return dict(self.conn.execute('SELECT * FROM esc_packs WHERE pack_id=?', (pack_id,)).fetchone())

    def unit(self, unit_id):
        row = self.conn.execute('SELECT state,resolution FROM units WHERE unit_id=?', (unit_id,)).fetchone()
        return row['state'], row['resolution']

    def events(self, event):
        return [json.loads(r['payload']) for r in
                self.conn.execute('SELECT payload FROM events WHERE event=? ORDER BY seq', (event,))]

    def events_by_unit(self, event):
        return {r['unit_id']: json.loads(r['payload']) for r in
                self.conn.execute('SELECT unit_id,payload FROM events WHERE event=? ORDER BY seq',
                                  (event,))}

    def readings(self, pack_id):
        return {r['side']: dict(r) for r in
                self.conn.execute('SELECT * FROM esc_readings WHERE pack_id=?', (pack_id,))}

    def set_state(self, **over):
        state = E.load_state(self.root)
        state.update(over)
        return E.save_state(self.root, state)

    def make_pack(self, entity, state='packed', riesgo=None, clase='no_aplica', mutate=None,
                  kinds=None):
        """Un pack congelado de la entidad, ya clasificado, en el estado que pida el test."""
        entry = self.entry(entity)
        unit_ids = entry['unit_ids']
        if kinds:
            keep = {r['unit_id'] for r in self.conn.execute(
                'SELECT unit_id,kind FROM units') if r['kind'] in kinds}
            unit_ids = [uid for uid in unit_ids if uid in keep]
        riesgo = riesgo or entry['riesgo']
        classification = {'clase': clase, 'riesgo': riesgo, 'source': 'reglas'}
        manifests = qa_esc_packs.build_packs(self.conn, self.base, entity, unit_ids,
                                             kind=entry['kind'], classification=classification,
                                             fetch=False)
        ids = []
        for manifest in manifests:
            if mutate:
                mutate(manifest)
            qa_esc_packs.write_pack(self.root, manifest,
                                    qa_esc_packs.render_pack(manifest, tools=True,
                                                             ev_path=str(E.EV_PATH)))
            ids.append(manifest['pack_id'])
        reading = {'source': 'reglas', 'entidad': {'answers': {}, 'classification': classification},
                   'unidades': {}}
        with self.conn:
            for pack_id in ids:
                self.conn.execute('UPDATE esc_packs SET state=?,riesgo=?,clase=? WHERE pack_id=?',
                                  (state, riesgo, clase, pack_id))
                E._put_reading(self.conn, pack_id, 'jev-class', reading,
                               {'attempt_id': f'cls-{pack_id}', 'side': 'jev-class', 'backend': 'reglas'})
        return ids[0]

    def read_pack(self, pack_id, **over):
        """Deja el pack en `opus-read` con una decisión válida, como si Opus lo hubiera leído."""
        manifest, _ = qa_esc_packs.load_pack(self.root, pack_id)
        decision = decision_for(manifest, **over)
        with self.conn:
            E._put_reading(self.conn, pack_id, 'opus', decision,
                           {'attempt_id': f'opus-{pack_id}', 'side': 'opus', 'effort': 'high',
                            'error': None, 'usage': USAGE, 'searches': ['ev.py "x"'], 'denials': []})
            self.conn.execute("UPDATE esc_packs SET state='opus-read' WHERE pack_id=?", (pack_id,))
        return manifest, decision


# ── censo ─────────────────────────────────────────────────────────────────────

class CensusTest(LoopBase):
    def test_census_queues_one_row_per_entity(self):
        out = E.census(self.root, self.base, 'pending')
        rows = self.rows()
        self.assertEqual(out['queued'], len(rows))
        self.assertEqual({r['state'] for r in rows}, {'queued'})
        self.assertTrue(all(r['pack_id'].startswith(E.PROVISIONAL) for r in rows))
        queue = {e['entity']: e for e in qa_esc_packs.entity_queue(self.conn, 'pending')}
        self.assertEqual({r['entity'] for r in rows}, set(queue))
        alejo = next(r for r in rows if r['entity'] == ALEJO)
        self.assertEqual(json.loads(alejo['unit_ids']), sorted(queue[ALEJO]['unit_ids']))
        self.assertEqual(alejo['riesgo'], 'alto')
        self.assertEqual(out['unidades'], sum(e['n_units'] for e in queue.values()))

    def test_census_is_idempotent_and_never_requeues_packed_units(self):
        E.census(self.root, self.base, 'pending')
        first = self.rows()
        E.census(self.root, self.base, 'pending')
        self.assertEqual([r['pack_id'] for r in self.rows()], [r['pack_id'] for r in first])
        empresa = next(r for r in first if r['entity'] == EMPRESA)
        with self.conn:
            self.conn.execute("UPDATE esc_packs SET state='packed' WHERE pack_id=?", (empresa['pack_id'],))
        out = E.census(self.root, self.base, 'pending')
        self.assertEqual(out['ya_empaquetadas'], 1)
        self.assertEqual(self.row(empresa['pack_id'])['state'], 'packed')

    def test_the_entities_filter_bounds_the_census_the_pack_and_the_reading(self):
        """El piloto trabaja con un subconjunto: lo que no entra en la cola no existe."""
        out = E.census(self.root, self.base, 'pending', E.entity_filter(f'{EMPRESA},{BENITO}'))
        self.assertEqual({r['entity'] for r in self.rows()}, {EMPRESA, BENITO})
        self.assertEqual(out['entidades_filtradas'], sorted([BENITO, EMPRESA]))
        self.assertTrue(out['fuera_del_filtro'])
        # Una lista en JSON vale igual que la lista con comas.
        path = Path(self.base) / 'piloto.json'
        path.write_text(json.dumps([ALEJO]))
        E.census(self.root, self.base, 'pending', E.entity_filter(str(path)))
        self.assertEqual({r['entity'] for r in self.rows()}, {EMPRESA, BENITO, ALEJO})
        with patch.object(qa_esc_jev, 'classify', FakeJev(fail=AssertionError('sin Jev'))):
            E.classify(self.root, self.base)
        packed = E.pack(self.root, self.base, n=10, fetch=False, entities={EMPRESA})
        self.assertEqual(packed['entidades'], 1)
        self.assertEqual({r['entity'] for r in self.rows('packed')}, {EMPRESA})
        reader = FakeReader(decide=lambda pid: decision_for(qa_esc_packs.load_pack(self.root, pid)[0]))
        with patch.object(qa_esc_opus, 'run_claude', reader):
            E.opus(self.root, self.base, n=10, entities={BENITO})
        self.assertEqual(reader.calls, [])
        with patch.object(qa_esc_opus, 'run_claude', reader):
            E.opus(self.root, self.base, n=10, entities={EMPRESA})
        self.assertEqual(len(reader.calls), 1)
        detail = E.status(self.root, packs=True, entities={EMPRESA})['packs_detalle']
        self.assertEqual([(d['entity'], d['state'], d['idx'], d['total'], d['riesgo'], d['route'])
                          for d in detail], [(EMPRESA, 'opus-read', 1, 1, 'bajo', None)])
        self.assertNotIn('packs_detalle', E.status(self.root))
        self.assertIsNone(E.entity_filter(None))
        self.assertIsNone(E.entity_filter(''))
        with self.assertRaises(ValueError):
            E.entity_filter(',')

    def test_census_records_the_scope_in_the_cursor(self):
        E.census(self.root, self.base, 'verified-T0')
        self.assertEqual(E.load_state(self.root)['cursor']['scope'], E.QC_SCOPE)


# ── clasificación ─────────────────────────────────────────────────────────────

class ClassifyTest(LoopBase):
    def test_rules_only_never_calls_jev(self):
        E.census(self.root, self.base, 'pending')
        with patch.object(qa_esc_jev, 'classify', FakeJev(fail=AssertionError('Jev no debe llamarse'))):
            out = E.classify(self.root, self.base)
        self.assertFalse(out['jev'])
        self.assertEqual(out['classified'], len(self.rows('classified')))
        benito = next(r for r in self.rows() if r['entity'] == BENITO)
        self.assertEqual((benito['clase'], benito['riesgo']), ('seudonimo', 'critico'))
        empresa = next(r for r in self.rows() if r['entity'] == EMPRESA)
        self.assertEqual((empresa['clase'], empresa['riesgo']), ('no_aplica', 'bajo'))
        alejo = next(r for r in self.rows() if r['entity'] == ALEJO)
        self.assertEqual(alejo['clase'], 'figura_publica')
        reading = self.readings(benito['pack_id'])['jev-class']
        self.assertEqual(json.loads(reading['receipt'])['backend'], 'reglas')
        self.assertEqual(json.loads(reading['result'])['source'], 'reglas')

    def test_jev_sets_the_class_and_the_worst_risk_wins(self):
        E.census(self.root, self.base, 'pending')
        self.set_state(jevClass=True)
        fake = FakeJev(jev_answers(clase='particular_mencionado', riesgo='bajo'))
        with patch.object(qa_esc_jev, 'classify', fake), \
                patch.object(qa_esc_jev, 'make_client', lambda *a, **k: object()):
            E.classify(self.root, self.base)
        empresa = next(r for r in self.rows() if r['entity'] == EMPRESA)
        self.assertEqual(empresa['clase'], 'particular_mencionado')
        # Jev dice «bajo»; la regla dice «alto» para Ábalos y «critico» para el seudónimo: manda la peor.
        alejo = next(r for r in self.rows() if r['entity'] == ALEJO)
        self.assertEqual(alejo['riesgo'], 'alto')
        benito = next(r for r in self.rows() if r['entity'] == BENITO)
        self.assertEqual(benito['riesgo'], 'critico')
        self.assertEqual({group for group, _ids in fake.groups}, {'entidad', 'unidad'})
        result = json.loads(self.readings(alejo['pack_id'])['jev-class']['result'])
        self.assertEqual(result['source'], 'jev')
        self.assertEqual(len(result['unidades']), len(json.loads(alejo['unit_ids'])))

    def test_config_error_halts_the_loop(self):
        E.census(self.root, self.base, 'pending')
        self.set_state(jevClass=True)
        fake = FakeJev(fail=qa_esc_jev.ConfigError('modelo resuelto jev-2 ≠ medido jev-1.13.0'))
        with patch.object(qa_esc_jev, 'classify', fake), \
                patch.object(qa_esc_jev, 'make_client', lambda *a, **k: object()):
            E.classify(self.root, self.base)
        state = E.load_state(self.root)
        self.assertTrue(state['halted'])
        self.assertIn('configuración', state['haltReason'])
        self.assertEqual(E.decide(self.root)['action'], 'HALT')


# ── packs ─────────────────────────────────────────────────────────────────────

class PackTest(LoopBase):
    def test_pack_replaces_the_provisional_row_and_keeps_the_classification(self):
        E.census(self.root, self.base, 'pending')
        with patch.object(qa_esc_jev, 'classify', FakeJev(fail=AssertionError('sin Jev'))):
            E.classify(self.root, self.base)
        provisional = {r['pack_id']: r for r in self.rows()}
        out = E.pack(self.root, self.base, fetch=False)
        rows = self.rows()
        self.assertEqual(out['packs_nuevos'], len(rows))
        self.assertFalse([r for r in rows if r['pack_id'].startswith(E.PROVISIONAL)])
        self.assertTrue(all(r['pack_id'].startswith(('E-', 'D-')) for r in rows))
        self.assertEqual({r['state'] for r in rows}, {'packed'})
        alejo = next(r for r in rows if r['entity'] == ALEJO)
        old = next(r for r in provisional.values() if r['entity'] == ALEJO)
        self.assertEqual(alejo['riesgo'], old['riesgo'])
        self.assertEqual(alejo['clase'], old['clase'])
        self.assertEqual(alejo['batch'], out['batch'])
        self.assertIn('jev-class', self.readings(alejo['pack_id']))
        # Las unidades de la cola siguen todas cubiertas por los packs reales.
        packed = {uid for r in rows for uid in json.loads(r['unit_ids'])}
        self.assertEqual(packed, {uid for r in provisional.values() for uid in json.loads(r['unit_ids'])})
        manifest, markdown = qa_esc_packs.load_pack(self.root, alejo['pack_id'])
        self.assertEqual(manifest['entity']['classification']['riesgo'], alejo['riesgo'])
        self.assertIn(str(E.EV_PATH), markdown)


# ── lectura de Opus ───────────────────────────────────────────────────────────

class OpusTest(LoopBase):
    def test_valid_reading_moves_the_pack_and_counts_the_window(self):
        pack_id = self.make_pack(EMPRESA)
        reader = FakeReader(decide=lambda pid: decision_for(qa_esc_packs.load_pack(self.root, pid)[0]))
        with patch.object(qa_esc_opus, 'run_claude', reader):
            out = E.opus(self.root, self.base)
        self.assertEqual(out['leidos'], 1)
        self.assertEqual(self.row(pack_id)['state'], 'opus-read')
        call = reader.calls[0]
        self.assertEqual((call['side'], call['model']), ('opus', qa_esc_opus.MODELS['opus']))
        self.assertEqual(call['stamp']['pack_id'], pack_id)
        self.assertEqual(call['stamp']['pack_sha256'], self.row(pack_id)['pack_sha256'])
        self.assertEqual(call['tools_prefix'], f'python3 {E.EV_PATH}')
        self.assertEqual(call['extra_env'], {'QA_ESC_ROOT': str(Path(self.root).resolve())})
        self.assertEqual(call['system'], BRIEF)
        window = E.load_state(self.root)['window']
        self.assertEqual(window['usedClaude'], FRESH)
        self.assertEqual(window['cachedClaude'], USAGE['cached'])
        self.assertEqual([e['pack_id'] for e in self.events('reading-accepted')], [pack_id])

    def test_effort_follows_the_risk_and_a_lower_one_voids_the_reading(self):
        low = self.make_pack(EMPRESA, riesgo='bajo')
        reader = FakeReader(decide=lambda pid: decision_for(qa_esc_packs.load_pack(self.root, pid)[0]))
        with patch.object(qa_esc_opus, 'run_claude', reader):
            E.opus(self.root, self.base, n=1)
        self.assertEqual(reader.calls[0]['effort'], 'high')
        high = self.make_pack(ALEJO, riesgo='alto')
        reader = FakeReader(decide=lambda pid: decision_for(qa_esc_packs.load_pack(self.root, pid)[0]))
        with patch.object(qa_esc_opus, 'run_claude', reader):
            E.opus(self.root, self.base, n=1)
        self.assertEqual(reader.calls[0]['effort'], 'high')
        self.assertEqual(self.row(high)['state'], 'opus-read')
        # El esperado es lo pedido: si el estado sube el alto a xhigh, una lectura a high se anula.
        state = E.load_state(self.root)
        state['efforts']['alto'] = 'xhigh'
        E.save_state(self.root, state)
        raised = self.make_pack(BENITO, riesgo='alto')
        reader = FakeReader(decide=lambda pid: decision_for(qa_esc_packs.load_pack(self.root, pid)[0]))
        with patch.object(qa_esc_opus, 'run_claude', reader):
            E.opus(self.root, self.base, n=1)
        self.assertEqual(reader.calls[0]['effort'], 'xhigh')
        self.assertEqual(self.row(raised)['state'], 'opus-read')
        state['efforts']['alto'] = 'high'
        E.save_state(self.root, state)
        # Un esfuerzo por debajo del esperado anula la lectura aunque el JSON sea válido.
        other = self.make_pack(BENITO, riesgo='alto')
        weak = FakeReader(decide=lambda pid: decision_for(qa_esc_packs.load_pack(self.root, pid)[0]),
                          effort='medium')
        with patch.object(qa_esc_opus, 'run_claude', weak):
            E.opus(self.root, self.base, n=1)
        self.assertEqual(self.row(other)['state'], 'packed')
        self.assertEqual(self.events('reading-rejected')[0]['why'], 'unexpected-effort')
        self.assertEqual(low and self.row(low)['state'], 'opus-read')

    def test_invalid_decision_is_retried_once_and_then_goes_to_the_arbiter(self):
        pack_id = self.make_pack(EMPRESA)
        unit_ids = json.loads(self.row(pack_id)['unit_ids'])
        broken = FakeReader(decide={'entity_id': 'Person:nadie', 'units': []})
        with patch.object(qa_esc_opus, 'run_claude', broken):
            E.opus(self.root, self.base, n=1)
        self.assertEqual(self.row(pack_id)['state'], 'packed')
        # Piloto 2026-09-21: una decisión inválida es calidad del lector, no fallo de infraestructura.
        self.assertEqual(E.load_state(self.root)['consecutiveFailures'], 0)
        reading = self.readings(pack_id)['opus']
        self.assertIsNone(reading['result'])
        self.assertTrue(json.loads(reading['receipt'])['validation_errors'])
        with patch.object(qa_esc_opus, 'run_claude', broken):
            E.opus(self.root, self.base, n=1)
        # 063-B: sin lectura válida de Opus el pack va al árbitro, que lo lee desde cero.
        self.assertEqual(self.row(pack_id)['state'], 'arb-pending')
        self.assertEqual(self.unit(unit_ids[0]), ('pending', E.ARB_PENDING))
        arb = json.loads(E.arb_path(self.root, pack_id).read_text())
        self.assertEqual(arb['reasons'][unit_ids[0]], 'lado-sin-lectura-valida:opus:invalid-decision')

    def test_quota_pauses_without_counting_as_a_failure(self):
        pack_id = self.make_pack(EMPRESA)
        before = E.utcnow().timestamp()
        with patch.object(qa_esc_opus, 'run_claude', FakeReader(error='quota')):
            E.opus(self.root, self.base, n=1)
        state = E.load_state(self.root)
        self.assertEqual(state['consecutiveFailures'], 0)
        self.assertAlmostEqual(state['window']['pausedUntil'] - before, E.PAUSE_RETRY_SECONDS, delta=5)
        self.assertEqual(self.row(pack_id)['state'], 'packed')
        self.assertEqual(E.decide(self.root)['action'], 'WAIT')

    def test_a_pack_of_verified_units_reads_with_the_qc_side(self):
        """El lado sale del ledger, no del cursor: un pack sin nada `pending` es control de calidad."""
        pending = self.make_pack(EMPRESA)
        qc = self.make_pack(BENITO)
        with self.conn:
            for uid in json.loads(self.row(qc)['unit_ids']):
                self.conn.execute("UPDATE units SET state='verified',resolution='dual-jev-astra' "
                                  'WHERE unit_id=?', (uid,))
        reader = FakeReader(decide=lambda pid: decision_for(qa_esc_packs.load_pack(self.root, pid)[0]))
        with patch.object(qa_esc_opus, 'run_claude', reader):
            out = E.opus(self.root, self.base)
        self.assertEqual(out['sides'], {'opus': 1, 'opus-qc': 1})
        self.assertIn('opus-qc', self.readings(qc))
        self.assertIn('opus', self.readings(pending))


# ── enrutado ──────────────────────────────────────────────────────────────────

class RouteTest(LoopBase):
    def calibration(self):
        path = Path(self.root) / 'esc' / 'jev' / 'calibration.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# calibración de prueba\n')
        return path

    def test_a_low_risk_pack_closes_with_its_decision_file(self):
        pack_id = self.make_pack(EMPRESA, riesgo='bajo')
        manifest, decision = self.read_pack(pack_id)
        out = E.route(self.root, self.base)
        self.assertEqual(out['cerrar'], 1)
        self.assertEqual(self.row(pack_id)['state'], 'closed')
        self.assertEqual(self.row(pack_id)['route'], 'cerrar')
        for unit in manifest['units']:
            self.assertEqual(self.unit(unit['unit_id']), ('verified', 'opus-cerrada'))
        path = Path(self.root) / 'esc' / 'decisions' / f'{pack_id}.json'
        saved = json.loads(path.read_text())
        self.assertEqual(saved['units'], decision['units'])
        meta = saved['_meta']
        self.assertEqual((meta['resolution'], meta['route'], meta['sides']),
                         ('opus-cerrada', 'cerrar', ['opus']))
        self.assertEqual(meta['unit_ids'], [u['unit_id'] for u in manifest['units']])
        trace = self.events('esc-routed')[0]
        self.assertEqual((trace['base'], trace['jev'], trace['final']), ('cerrar', None, 'cerrar'))
        self.assertEqual(self.events('esc-cerrada')[0]['resolution'], 'opus-cerrada')

    def test_a_rewrite_escalates_instead_of_closing(self):
        pack_id = self.make_pack(EMPRESA, riesgo='bajo')
        manifest, _ = qa_esc_packs.load_pack(self.root, pack_id)
        uid = manifest['units'][0]['unit_id']
        decision = decision_for(manifest)
        decision['units'][0].update(verdict='corregido', label='reescrita')
        decision['summary'] = {'action': 'replace', 'resumen': 'Texto nuevo (original.pdf p.1).',
                               'sources': [], 'reason': 'la frase no estaba sostenida'}
        with self.conn:
            E._put_reading(self.conn, pack_id, 'opus', decision,
                           {'attempt_id': 'opus-c', 'side': 'opus', 'error': None, 'searches': []})
            self.conn.execute("UPDATE esc_packs SET state='opus-read' WHERE pack_id=?", (pack_id,))
        E.route(self.root, self.base)
        # [reescrita] escala por B2: no se cierra sola.
        self.assertEqual(self.row(pack_id)['state'], 'dual-pending')
        self.assertEqual(self.unit(uid)[0], 'pending')

    def test_a_pseudonym_goes_to_the_owner(self):
        pack_id = self.make_pack(BENITO, riesgo='critico', clase='seudonimo')
        manifest, _ = self.read_pack(pack_id)
        out = E.route(self.root, self.base)
        self.assertEqual(out['dueno'], 1)
        self.assertEqual(self.row(pack_id)['state'], 'tanda')
        state, resolution = self.unit(manifest['units'][0]['unit_id'])
        self.assertEqual(state, 'needs-human')
        self.assertTrue(resolution.startswith('tanda-') and resolution.endswith('-pendiente'))
        self.assertFalse((Path(self.root) / 'esc' / 'decisions' / f'{pack_id}.json').exists())

    def test_an_accusatory_edge_escalates(self):
        pack_id = self.make_pack(ALEJO, riesgo='alto', clase='figura_publica', kinds={'edge'})
        manifest, _ = self.read_pack(pack_id)
        E.route(self.root, self.base)
        self.assertEqual(self.row(pack_id)['state'], 'dual-pending')
        self.assertEqual(self.row(pack_id)['route'], 'escalar')
        for unit in manifest['units']:
            self.assertEqual(self.unit(unit['unit_id'])[0], 'pending')

    def test_an_identity_unit_goes_to_the_owner_alone_and_the_rest_escalates(self):
        """(a) Dueño, 2026-10-01: lo del dueño se decide por unidad, no arrastra al pack."""
        pack_id = self.make_pack(ALEJO, riesgo='alto', clase='figura_publica')
        manifest, _ = self.read_pack(pack_id)
        out = E.route(self.root, self.base)
        self.assertEqual((out['escalar'], out['packs_divididos'], out['unidades_tanda']), (1, 1, 1))
        row = self.row(pack_id)
        self.assertEqual((row['state'], row['route']), ('dual-pending', 'escalar'))
        identity = next(u['unit_id'] for u in manifest['units'] if u['kind'] == 'identity')
        self.assertEqual(self.events_by_unit('esc-routed')[identity]['base'], 'dueno')
        state, resolution = self.unit(identity)
        self.assertEqual(state, 'needs-human')
        self.assertTrue(resolution.startswith('tanda-') and resolution.endswith('-pendiente'))
        rest = [u['unit_id'] for u in manifest['units'] if u['unit_id'] != identity]
        for uid in rest:
            self.assertEqual(self.unit(uid), ('pending', None))
        self.assertEqual(E.status(self.root)['dueno_en_vuelo'], {'packs': 1, 'unidades': 1})
        # El dual solo lee (y el árbitro solo recibe) lo que no es del dueño.
        seen = {}

        def run_dual(root, pid, mani, blind, tools, schema, brief, effort, astra_tools, opus_decision, **_):
            seen['units'] = [u['unit_id'] for u in mani['units']]
            seen['blind'] = blind
            return {'astra-esc': (decision_for(mani), {'attempt_id': 'a', 'side': 'astra-esc', 'error': None,
                                                       'usage': {}})}

        def dual(pack, decisions, full=None):
            seen['pack'], seen['full'] = [u['unit_id'] for u in pack['units']], full['pack_id']
            return {'mode': 'arbitraje', 'final': None, 'per_unit': {}, 'reasons': [], 'closed_units': [],
                    'tanda_units': [u['unit_id'] for u in pack['units']]}

        with escalate_module(run_dual=run_dual, dual=dual):
            E.escalate(self.root, self.base, n=1)
        self.assertEqual((seen['units'], seen['pack'], seen['full']), (rest, rest, pack_id))
        self.assertIn(f'unidades: {len(rest)}', seen['blind'])
        self.assertEqual(json.loads(E.arb_path(self.root, pack_id).read_text())['units'], rest)
        self.assertEqual(self.unit(identity), (state, resolution))
        self.assertEqual(self.row(pack_id)['state'], 'arb-pending')

    def test_an_owner_unit_in_a_prose_block_takes_its_block_mates_along(self):
        """(b) El resumen se decide entero, como en el árbitro; el rol (otro bloque) y las aristas no."""
        pack_id = self.make_pack(ALEJO, riesgo='alto', clase='figura_publica')
        manifest, _ = qa_esc_packs.load_pack(self.root, pack_id)
        summaries = [u['unit_id'] for u in manifest['units'] if u['kind'] == 'summary']
        decision = decision_for(manifest)
        # Un cambio de estado procesal en UNA frase del resumen es del dueño (B1).
        decision['units'][[u['unit_id'] for u in manifest['units']].index(summaries[0])].update(
            verdict='corregido', label='estado')
        with self.conn:
            E._put_reading(self.conn, pack_id, 'opus', decision,
                           {'attempt_id': 'opus-b', 'side': 'opus', 'error': None, 'searches': []})
            self.conn.execute("UPDATE esc_packs SET state='opus-read' WHERE pack_id=?", (pack_id,))
        E.route(self.root, self.base)
        routed = self.events_by_unit('esc-routed')
        self.assertEqual([routed[uid]['final'] for uid in summaries], ['dueno', 'escalar', 'escalar'])
        tanda = self.events_by_unit('esc-tanda')
        identity = next(u['unit_id'] for u in manifest['units'] if u['kind'] == 'identity')
        self.assertEqual(set(tanda), {*summaries, identity})
        self.assertEqual({uid: tanda[uid].get('motivo') for uid in summaries[1:]},
                         {uid: 'bloque de prosa con una unidad del dueño' for uid in summaries[1:]})
        for uid in summaries:
            self.assertEqual(self.unit(uid)[0], 'needs-human')
        others = [u['unit_id'] for u in manifest['units'] if u['kind'] in ('attribute', 'edge')]
        for uid in others:
            self.assertEqual(self.unit(uid), ('pending', None))
        self.assertEqual(self.row(pack_id)['state'], 'dual-pending')

    def test_a_pack_whose_units_are_all_the_owners_goes_whole_to_the_tanda(self):
        """(c) Sin resto, el pack va a la tanda como antes."""
        pack_id = self.make_pack(BENITO, riesgo='critico', clase='seudonimo')
        manifest, _ = self.read_pack(pack_id)
        out = E.route(self.root, self.base)
        self.assertEqual((out['dueno'], out.get('packs_divididos', 0)), (1, 0))
        self.assertEqual((self.row(pack_id)['state'], self.row(pack_id)['route']), ('tanda', 'dueno'))
        for unit in manifest['units']:
            self.assertEqual(self.unit(unit['unit_id'])[0], 'needs-human')
        self.assertEqual(E.owner_pack_ids(self.conn), [pack_id])

    def test_the_rest_closes_with_opus_and_the_owner_unit_waits_for_the_tanda(self):
        """(d) Resto `cerrar`: `opus-cerrada` acotada al resto; el pack espera al dueño y cierra con él."""
        pack_id = self.make_pack(ALEJO, riesgo='medio', clase='figura_publica',
                                 kinds={'identity', 'summary', 'attribute'})
        manifest, _ = self.read_pack(pack_id)
        out = E.route(self.root, self.base)
        self.assertEqual((out['cerrar'], out['unidades_cerradas'], out['unidades_tanda']),
                         (1, len(manifest['units']) - 1, 1))
        identity = next(u['unit_id'] for u in manifest['units'] if u['kind'] == 'identity')
        rest = [u['unit_id'] for u in manifest['units'] if u['unit_id'] != identity]
        for uid in rest:
            self.assertEqual(self.unit(uid), ('verified', 'opus-cerrada'))
        self.assertEqual(self.unit(identity)[0], 'needs-human')
        row = self.row(pack_id)
        self.assertEqual((row['state'], row['route']), ('tanda', 'cerrar'))
        saved = json.loads((Path(self.root) / 'esc' / 'decisions' / f'{pack_id}.json').read_text())
        self.assertEqual((saved['_meta']['unit_ids'], saved['_meta']['tanda_units']), (rest, [identity]))
        self.assertEqual([u['unit_id'] for u in saved['units']], rest)
        self.assertEqual(E.g7(self.conn)['cierres_indebidos'], 0)
        # El dueño responde la identidad: fichero propio, sin pisar el cierre de Opus, y el pack cierra.
        answers = Path(self.base) / 'respuestas.json'
        answers.write_text(json.dumps([{'pack_id': pack_id, 'unit_id': identity, 'choice_id': 'verificar'}]))
        self.assertEqual(E.tanda_apply(self.root, str(answers))['packs'], [pack_id])
        date = E._today()
        self.assertEqual(self.unit(identity), ('verified', f'humano-{date}'))
        self.assertEqual(self.row(pack_id)['state'], 'closed')
        human = json.loads((Path(self.root) / 'esc' / 'decisions'
                            / f'{pack_id}.humano-{date}.json').read_text())
        self.assertEqual(human['_meta']['unit_ids'], [identity])
        self.assertEqual(json.loads((Path(self.root) / 'esc' / 'decisions'
                                     / f'{pack_id}.json').read_text())['_meta']['resolution'], 'opus-cerrada')

    def test_jev_only_hardens_a_route_never_loosens_it(self):
        self.calibration()
        self.set_state(jevRouting=True)
        closing = self.make_pack(EMPRESA, riesgo='bajo')
        self.read_pack(closing)
        with patch.object(qa_esc_jev, 'classify', FakeJev(route='dueno')):
            E.route(self.root, self.base, client=object())
        self.assertEqual(self.row(closing)['state'], 'tanda')
        trace = self.events('esc-routed')[0]
        self.assertEqual((trace['base'], trace['jev'], trace['final']), ('cerrar', 'dueno', 'dueno'))
        self.assertIn('jev-route', self.readings(closing))
        # Y al revés: sobre un pack que la regla escala, «cerrar» de Jev no afloja nada.
        escalating = self.make_pack(ALEJO, riesgo='alto', kinds={'edge'})
        self.read_pack(escalating)
        with patch.object(qa_esc_jev, 'classify', FakeJev(route='cerrar')):
            E.route(self.root, self.base, client=object())
        self.assertEqual(self.row(escalating)['state'], 'dual-pending')
        traces = {t['pack_id']: t for t in self.events('esc-routed')}
        self.assertEqual(traces[escalating]['final'], 'escalar')

    def test_route_refuses_to_use_jev_without_a_calibration_report(self):
        self.set_state(jevRouting=True)
        pack_id = self.make_pack(EMPRESA, riesgo='bajo')
        self.read_pack(pack_id)
        out = E.route(self.root, self.base, client=object())
        self.assertEqual(out['action'], 'HALT')
        self.assertTrue(E.load_state(self.root)['halted'])
        self.assertEqual(self.row(pack_id)['state'], 'opus-read')

    def test_a_unit_that_is_not_pending_is_never_touched(self):
        pack_id = self.make_pack(EMPRESA, riesgo='bajo')
        manifest, _ = self.read_pack(pack_id)
        uid = manifest['units'][0]['unit_id']
        with self.conn:
            self.conn.execute("UPDATE units SET state='verified',resolution='dual-jev-astra' "
                              'WHERE unit_id=?', (uid,))
        E.route(self.root, self.base)
        self.assertEqual(self.unit(uid), ('verified', 'dual-jev-astra'))
        self.assertEqual(self.row(pack_id)['state'], 'closed')

    def test_the_quality_control_scope_closes_without_touching_units(self):
        pack_id = self.make_pack(EMPRESA, riesgo='bajo')
        manifest, _ = qa_esc_packs.load_pack(self.root, pack_id)
        with self.conn:
            E._put_reading(self.conn, pack_id, 'opus-qc', decision_for(manifest),
                           {'attempt_id': 'qc', 'side': 'opus-qc', 'error': None, 'searches': []})
            self.conn.execute("UPDATE esc_packs SET state='opus-read' WHERE pack_id=?", (pack_id,))
        E.route(self.root, self.base)
        self.assertEqual(self.row(pack_id)['state'], 'closed')
        for unit in manifest['units']:
            self.assertEqual(self.unit(unit['unit_id']), ('pending', None))
        self.assertEqual([e['pack_id'] for e in self.events('esc-qc-ok')],
                         [pack_id] * len(manifest['units']))
        self.assertFalse((Path(self.root) / 'esc' / 'decisions' / f'{pack_id}.json').exists())

    def test_the_searches_of_the_receipt_travel_as_a_number(self):
        pack_id = self.make_pack(EMPRESA, riesgo='bajo')
        manifest, _ = qa_esc_packs.load_pack(self.root, pack_id)
        with self.conn:
            E._put_reading(self.conn, pack_id, 'opus', decision_for(manifest),
                           {'attempt_id': 'opus-s', 'side': 'opus', 'error': None,
                            'searches': ['a', 'b', 'c']})
            self.conn.execute("UPDATE esc_packs SET state='opus-read' WHERE pack_id=?", (pack_id,))
        self.calibration()
        self.set_state(jevRouting=True)
        fake = FakeJev(route='cerrar')
        captured = []

        def spy(client, items, questions, attempt_dir):
            captured.extend(items)
            return fake(client, items, questions, attempt_dir)

        with patch.object(qa_esc_jev, 'classify', spy):
            E.route(self.root, self.base, client=object())
        self.assertEqual(captured[0]['state']['opus']['busquedas'], 3)


# ── enrutado por unidad: la tanda y el re-enrutado de lo que se mandó entero ──

class OwnerUnitsTest(LoopBase):
    LEGACY = 'tanda-2026-09-30-pendiente'

    def legacy_tanda(self, pack_id):
        """Como lo dejaba `route()` antes del 2026-10-01: el pack entero al dueño, sin `from` en el evento."""
        manifest, _ = qa_esc_packs.load_pack(self.root, pack_id)
        with self.conn:
            for unit in manifest['units']:
                self.conn.execute("UPDATE units SET state='needs-human',resolution=? WHERE unit_id=?",
                                  (self.LEGACY, unit['unit_id']))
                E._event(self.conn, unit['unit_id'], 'esc-tanda', {'pack_id': pack_id, 'resolution': self.LEGACY})
            self.conn.execute("UPDATE esc_packs SET state='tanda',route='dueno' WHERE pack_id=?", (pack_id,))
        return manifest

    def ledger(self):
        return ([tuple(r) for r in self.conn.execute('SELECT unit_id,state,resolution FROM units ORDER BY unit_id')],
                [tuple(r) for r in self.conn.execute('SELECT pack_id,state,route FROM esc_packs ORDER BY pack_id')],
                self.conn.execute('SELECT count(*) FROM events').fetchone()[0])

    def test_the_tanda_lists_the_owner_units_of_a_pack_still_in_the_dual(self):
        """(f) La tanda sale de las unidades `needs-human`, no solo de los packs en `tanda`."""
        pack_id = self.make_pack(ALEJO, riesgo='alto', clase='figura_publica')
        manifest, _ = self.read_pack(pack_id)
        E.route(self.root, self.base)
        self.assertEqual(self.row(pack_id)['state'], 'dual-pending')
        identity = next(u['unit_id'] for u in manifest['units'] if u['kind'] == 'identity')
        out = E.tanda(self.root)
        items = json.loads(Path(out['json']).read_text())
        self.assertEqual([(i['pack_id'], i['unit_id']) for i in items], [(pack_id, identity)])
        # Exportar no toca lo que sigue en la máquina.
        for unit in manifest['units']:
            if unit['unit_id'] != identity:
                self.assertEqual(self.unit(unit['unit_id']), ('pending', None))
        self.assertEqual(self.row(pack_id)['state'], 'dual-pending')
        # La respuesta del dueño cierra solo su unidad, en su propio fichero; el dual sigue con el resto.
        answers = Path(self.base) / 'respuestas.json'
        answers.write_text(json.dumps([{'pack_id': pack_id, 'unit_id': identity, 'choice_id': 'verificar'}]))
        self.assertEqual(E.tanda_apply(self.root, str(answers))['packs'], [pack_id])
        date = E._today()
        self.assertEqual(self.unit(identity), ('verified', f'humano-{date}'))
        self.assertEqual(self.row(pack_id)['state'], 'dual-pending')
        self.assertFalse((Path(self.root) / 'esc' / 'decisions' / f'{pack_id}.json').exists())
        human = json.loads((Path(self.root) / 'esc' / 'decisions'
                            / f'{pack_id}.humano-{date}.json').read_text())
        self.assertEqual(human['_meta']['unit_ids'], [identity])

    def test_reroute_owner_splits_a_legacy_whole_pack_tanda(self):
        """(e) Lo del dueño se queda; el resto vuelve a `pending` y el pack escala. DRY no escribe."""
        pack_id = self.make_pack(ALEJO, riesgo='alto', clase='figura_publica')
        self.read_pack(pack_id)
        manifest = self.legacy_tanda(pack_id)
        # Un pack que releía unidades ya verificadas (el dueño las había cerrado antes): no se toca.
        reread = self.make_pack(EMPRESA, riesgo='bajo')
        reread_manifest, _ = self.read_pack(reread)
        with self.conn:
            for unit in reread_manifest['units']:
                self.conn.execute("UPDATE units SET state='verified',resolution='humano-2026-09-17e' "
                                  'WHERE unit_id=?', (unit['unit_id'],))
            self.conn.execute("UPDATE esc_packs SET state='tanda',route='dueno' WHERE pack_id=?", (reread,))
        # Y uno que es del dueño entero (seudónimo) tampoco se mueve.
        whole = self.make_pack(BENITO, riesgo='critico', clase='seudonimo')
        self.read_pack(whole)
        self.legacy_tanda(whole)
        identity = next(u['unit_id'] for u in manifest['units'] if u['kind'] == 'identity')
        rest = [u['unit_id'] for u in manifest['units'] if u['unit_id'] != identity]

        before = self.ledger()
        with patch('sys.stdout', new_callable=io.StringIO) as stdout:
            E.main(['--root', str(self.root), '--repo', str(self.base), 'reroute-owner', '--dry-run'])
        dry = json.loads(stdout.getvalue())
        self.assertEqual(self.ledger(), before)
        self.assertEqual(dry['packs'], [{'pack_id': pack_id, 'entity': ALEJO, 'dueno': 1, 'resto': len(rest),
                                         'ruta_resto': 'escalar'}])
        self.assertEqual((dry['relectura_de_verificadas'], dry['todo_del_dueno']), (1, 1))

        out = E.reroute_owner(self.root, self.base)
        self.assertEqual((out['reenrutados'], out['packs'][0]['estado']), (1, 'dual-pending'))
        self.assertEqual((self.row(pack_id)['state'], self.row(pack_id)['route']), ('dual-pending', 'escalar'))
        self.assertEqual(self.unit(identity), ('needs-human', self.LEGACY))
        for uid in rest:
            self.assertEqual(self.unit(uid), ('pending', None))
        moved = self.events_by_unit('esc-reenrutada')
        self.assertEqual(set(moved), set(rest))
        self.assertEqual({(m['from'], m['resolution']) for m in moved.values()}, {(self.LEGACY, None)})
        self.assertEqual([e['pack_id'] for e in self.events('esc-reenrutado')], [pack_id])
        self.assertEqual((self.row(reread)['state'], self.row(whole)['state']), ('tanda', 'tanda'))
        for unit in reread_manifest['units']:
            self.assertEqual(self.unit(unit['unit_id']), ('verified', 'humano-2026-09-17e'))
        # Una vez: lo re-enrutado ya no es una tanda entera.
        self.assertEqual(E.reroute_owner(self.root, self.base)['reenrutados'], 0)
        self.assertEqual(E.decide(self.root)['action'], 'ESCALATE')

    def test_reroute_owner_closes_a_rest_that_opus_alone_can_close(self):
        pack_id = self.make_pack(ALEJO, riesgo='medio', clase='figura_publica',
                                 kinds={'identity', 'summary', 'attribute'})
        self.read_pack(pack_id)
        manifest = self.legacy_tanda(pack_id)
        identity = next(u['unit_id'] for u in manifest['units'] if u['kind'] == 'identity')
        out = E.reroute_owner(self.root, self.base)
        self.assertEqual(out['packs'][0]['ruta_pack'], 'cerrar')
        for unit in manifest['units']:
            if unit['unit_id'] != identity:
                self.assertEqual(self.unit(unit['unit_id']), ('verified', 'opus-cerrada'))
        self.assertEqual(self.unit(identity), ('needs-human', self.LEGACY))
        self.assertEqual((self.row(pack_id)['state'], self.row(pack_id)['route']), ('tanda', 'cerrar'))

    def test_a_restored_unit_gets_back_the_resolution_it_had_while_pending(self):
        pack_id = self.make_pack(ALEJO, riesgo='alto', clase='figura_publica')
        manifest, _ = self.read_pack(pack_id)
        edge = next(u['unit_id'] for u in manifest['units'] if u['kind'] == 'edge')
        with self.conn:
            self.conn.execute("UPDATE units SET resolution='reabierta-061' WHERE unit_id=?", (edge,))
            # El `route()` de antes, pero con el `from` que ahora anota `_mark_units`.
            E._mark_units(self.conn, [u['unit_id'] for u in manifest['units']], 'needs-human', self.LEGACY,
                          'esc-tanda', {'pack_id': pack_id})
            self.conn.execute("UPDATE esc_packs SET state='tanda',route='dueno' WHERE pack_id=?", (pack_id,))
        E.reroute_owner(self.root, self.base)
        self.assertEqual(self.unit(edge), ('pending', 'reabierta-061'))


# ── escalado dual (T9 como stub) ──────────────────────────────────────────────

class EscalateTest(LoopBase):
    def dual(self, pack_id, error=None, over=None):
        manifest, _ = qa_esc_packs.load_pack(self.root, pack_id)

        def run_dual(root, pid, mani, blind, tools, schema, brief, effort, astra_tools, opus_decision, **_kwargs):
            self.seen = {'blind': blind, 'tools': tools, 'astra_tools': astra_tools, 'effort': effort}
            out = {}
            for side in ('astra-esc',):
                receipt = {'attempt_id': f'{side}-{pid}', 'side': side, 'error': error,
                           'usage': {'input': 100, 'output': 50}}
                out[side] = (None if error else decision_for(mani, **(over or {})), receipt)
            return out
        return run_dual, manifest

    def prepared(self, entity=EMPRESA, riesgo='bajo'):
        pack_id = self.make_pack(entity, riesgo=riesgo)
        self.read_pack(pack_id)
        with self.conn:
            self.conn.execute("UPDATE esc_packs SET state='dual-pending',route='escalar' "
                              'WHERE pack_id=?', (pack_id,))
        return pack_id

    def test_dual_agreement_closes_the_pack(self):
        pack_id = self.prepared()
        run_dual, manifest = self.dual(pack_id)
        claude_before = E.load_state(self.root)['window']['usedClaude']

        def dual(pack, decisions, **_):
            ids = [u['unit_id'] for u in pack['units']]
            return {'mode': 'dual', 'final': decisions['opus'], 'per_unit': {}, 'reasons': [],
                    'closed_units': ids, 'tanda_units': []}

        with escalate_module(run_dual=run_dual, dual=dual):
            out = E.escalate(self.root, self.base, n=1)
        self.assertEqual(out['cerrados'], 1)
        self.assertEqual(self.row(pack_id)['state'], 'closed')
        for unit in manifest['units']:
            self.assertEqual(self.unit(unit['unit_id']), ('verified', 'dual-cerrada'))
        meta = json.loads((Path(self.root) / 'esc' / 'decisions' / f'{pack_id}.json').read_text())['_meta']
        self.assertEqual(meta['resolution'], 'dual-cerrada')
        self.assertEqual(meta['sides'], ['opus', 'astra-esc'])
        self.assertEqual(set(self.readings(pack_id)), {'jev-class', 'opus', 'astra-esc'})
        window = E.load_state(self.root)['window']
        self.assertEqual((window['usedClaude'] - claude_before, window['usedAstra']), (0, 150))
        self.assertNotIn('Herramienta:', self.seen['blind'])
        self.assertIn('Herramienta:', self.seen['tools'])

    def test_a_corrected_unit_closes_with_the_corregido_suffix(self):
        pack_id = self.prepared()
        rewrite = {'summary': {'action': 'replace', 'resumen': 'Texto nuevo (original.pdf p.1).',
                               'sources': [], 'reason': 'la frase no estaba sostenida'}}
        run_dual, manifest = self.dual(pack_id, over={'verdict': 'corregido', 'label': 'reescrita',
                                                      **rewrite})

        def dual(pack, decisions, **_):
            return {'mode': 'dual', 'final': decisions['astra-esc'], 'per_unit': {},
                    'reasons': ['solo redacción'], 'tanda_units': [],
                    'closed_units': [u['unit_id'] for u in pack['units']]}

        with escalate_module(run_dual=run_dual, dual=dual):
            E.escalate(self.root, self.base, n=1)
        for unit in manifest['units']:
            self.assertEqual(self.unit(unit['unit_id']), ('verified', 'dual-cerrada-corregido'))

    def test_a_partial_agreement_closes_the_agreed_units_and_sends_the_rest_to_the_arbiter(self):
        pack_id = self.prepared(ALEJO)
        run_dual, manifest = self.dual(pack_id)
        ids = [u['unit_id'] for u in manifest['units']]
        self.assertGreaterEqual(len(ids), 2)
        first, rest = ids[:1], ids[1:]

        def dual(pack, decisions, **_):
            final = json.loads(json.dumps(decisions['opus']))
            final['units'] = [u for u in final['units'] if u['unit_id'] in first]
            return {'mode': 'parcial', 'final': final, 'closed_units': first, 'tanda_units': rest,
                    'per_unit': {uid: {'agree': False, 'reason': 'acción distinta'} for uid in rest},
                    'reasons': []}

        with escalate_module(run_dual=run_dual, dual=dual):
            out = E.escalate(self.root, self.base, n=1)
        self.assertEqual((out['unidades_cerradas'], out['unidades_arbitraje']), (1, len(rest)))
        row = self.row(pack_id)
        self.assertEqual((row['state'], row['route']), ('arb-pending', 'parcial'))
        self.assertEqual(self.unit(first[0]), ('verified', 'dual-cerrada'))
        # Siguen `pending` (lo estaban): solo cambia la resolución y el motivo queda persistido.
        self.assertEqual(self.unit(rest[0]), ('pending', E.ARB_PENDING))
        meta = json.loads((Path(self.root) / 'esc' / 'decisions' / f'{pack_id}.json').read_text())['_meta']
        self.assertEqual((meta['unit_ids'], meta['tanda_units']), (first, rest))
        arb = json.loads(E.arb_path(self.root, pack_id).read_text())
        self.assertEqual((arb['units'], arb['reasons'][rest[0]]), (rest, 'acción distinta'))
        self.assertEqual(self.events_by_unit('esc-arbitraje')[rest[0]]['reason'], 'acción distinta')
        self.assertEqual(self.events_by_unit('esc-cerrada')[first[0]]['closed_units'], first)

    def test_disagreement_sends_the_pack_to_the_arbiter_not_to_the_owner(self):
        pack_id = self.prepared()
        run_dual, manifest = self.dual(pack_id)
        with escalate_module(run_dual=run_dual):  # el `dual_agreement` por defecto no cierra nada
            out = E.escalate(self.root, self.base, n=1)
        self.assertEqual(out['arbitraje'], 1)
        self.assertEqual(self.row(pack_id)['state'], 'arb-pending')
        self.assertEqual(self.unit(manifest['units'][0]['unit_id']), ('pending', E.ARB_PENDING))
        self.assertEqual(E.decide(self.root)['action'], 'ARBITRATE')

    def test_a_null_side_is_retried_once_and_then_stranded(self):
        pack_id = self.prepared()
        run_dual, manifest = self.dual(pack_id, error='missing-structured-output')
        with escalate_module(run_dual=run_dual):
            E.escalate(self.root, self.base, n=1)
        self.assertEqual(self.row(pack_id)['state'], 'dual-pending')
        with escalate_module(run_dual=run_dual):
            E.escalate(self.root, self.base, n=1)
        self.assertEqual(self.row(pack_id)['state'], 'arb-pending')
        uid = manifest['units'][0]['unit_id']
        self.assertEqual(self.unit(uid), ('pending', E.ARB_PENDING))
        self.assertEqual(json.loads(E.arb_path(self.root, pack_id).read_text())['reasons'][uid],
                         'lado-sin-lectura-valida:astra-esc:missing-structured-output')


# ── latido ────────────────────────────────────────────────────────────────────

class StepTest(LoopBase):
    def spawn(self, pid=None):
        calls = []

        def fake(root, action, n):
            calls.append((action, n))
            return pid if pid is not None else 999999
        return fake, calls

    def test_step_is_idempotent_with_an_injected_spawn(self):
        E.census(self.root, self.base, 'pending')
        spawn, calls = self.spawn()
        with patch.object(E, '_alive', return_value=True):
            first = E.step(self.root, self.base, spawn=spawn)
            second = E.step(self.root, self.base, spawn=spawn)
        self.assertEqual(first['action'], 'CLASSIFY')
        self.assertEqual(second['action'], 'RUNNING')
        self.assertEqual(calls, [('CLASSIFY', E.PER_FIRE['classify'])])
        self.assertEqual(E.load_state(self.root)['firingCount'], 1)
        self.assertEqual(first['packs']['queued'], len(self.rows('queued')))
        self.assertIn('pending', first['units'])
        self.assertEqual(set(first['window']), {'usedClaude', 'usedAstra', 'pausedUntil'})

    def test_a_dead_batch_is_pruned_and_the_action_is_dispatched_again(self):
        E.census(self.root, self.base, 'pending')
        spawn, calls = self.spawn()
        with patch.object(E, '_alive', return_value=True):
            E.step(self.root, self.base, spawn=spawn)
        self.assertEqual(len(E.load_state(self.root)['batches']), 1)
        with patch.object(E, '_alive', return_value=False):
            again = E.step(self.root, self.base, spawn=spawn)
        self.assertEqual(again['action'], 'CLASSIFY')
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(E.load_state(self.root)['batches']), 1)

    def test_dry_run_decides_without_launching_anything(self):
        E.census(self.root, self.base, 'pending')
        spawn, calls = self.spawn()
        out = E.step(self.root, self.base, spawn=spawn, dry_run=True)
        self.assertEqual(out['action'], 'CLASSIFY')
        self.assertEqual(calls, [])
        self.assertIn('dry-run', out['note'])
        self.assertEqual(E.load_state(self.root)['batches'], [])

    def test_the_order_of_decisions(self):
        self.assertEqual(E.decide(self.root)['action'], 'DONE')
        E.census(self.root, self.base, 'pending')
        self.assertEqual(E.decide(self.root)['action'], 'CLASSIFY')
        with self.conn:
            self.conn.execute("UPDATE esc_packs SET state='classified' WHERE entity=?", (EMPRESA,))
        self.assertEqual(E.decide(self.root)['action'], 'PACK')
        with self.conn:
            self.conn.execute("UPDATE esc_packs SET state='packed' WHERE entity=?", (EMPRESA,))
        self.assertEqual(E.decide(self.root)['action'], 'OPUS')
        with self.conn:
            self.conn.execute("UPDATE esc_packs SET state='opus-read' WHERE entity=?", (EMPRESA,))
            E._put_reading(self.conn, self.rows('opus-read')[0]['pack_id'], 'opus', {'units': []},
                           {'attempt_id': 'x', 'side': 'opus'})
        self.assertEqual(E.decide(self.root)['action'], 'ROUTE')
        with self.conn:
            self.conn.execute("UPDATE esc_packs SET state='dual-pending' WHERE entity=?", (EMPRESA,))
        self.assertEqual(E.decide(self.root)['action'], 'ESCALATE')
        with self.conn:
            self.conn.execute("UPDATE esc_packs SET state='arb-pending' WHERE entity=?", (EMPRESA,))
        self.assertEqual(E.decide(self.root)['action'], 'ARBITRATE')
        state = E.load_state(self.root)
        state['window']['usedAstra'] = E.SOFT_CAP_ASTRA
        E.save_state(self.root, state)
        self.assertEqual(E.decide(self.root)['action'], 'WAIT')
        state['window']['usedAstra'] = 0
        E.save_state(self.root, state)
        with self.conn:
            self.conn.execute("UPDATE esc_packs SET state='closed'")
        self.assertEqual(E.decide(self.root)['action'], 'DONE')

    def test_wait_on_the_soft_cap_and_on_the_quota_pause(self):
        E.census(self.root, self.base, 'pending')
        state = E.load_state(self.root)
        state['window']['usedClaude'] = E.SOFT_CAP_CLAUDE
        E.save_state(self.root, state)
        decision = E.decide(self.root)
        self.assertEqual(decision['action'], 'WAIT')
        self.assertIn('ventana', decision['note'])
        self.assertTrue(decision['pausedUntil'])
        # Una ventana vencida se reinicia y el bucle sigue.
        state = E.load_state(self.root)
        state['window']['startedAt'] = '2020-01-01T00:00:00+00:00'
        E.save_state(self.root, state)
        spawn, _calls = self.spawn()
        self.assertEqual(E.step(self.root, self.base, spawn=spawn, dry_run=True)['action'], 'CLASSIFY')
        self.assertEqual(E.load_state(self.root)['window']['usedClaude'], 0)

    def test_wait_on_the_astra_cap_only_blocks_the_escalation(self):
        pack_id = self.make_pack(EMPRESA)
        self.read_pack(pack_id)
        with self.conn:
            self.conn.execute("UPDATE esc_packs SET state='dual-pending' WHERE pack_id=?", (pack_id,))
        state = E.load_state(self.root)
        state['window']['usedAstra'] = E.SOFT_CAP_ASTRA
        E.save_state(self.root, state)
        decision = E.decide(self.root)
        self.assertEqual(decision['action'], 'WAIT')
        self.assertIn('Astra', decision['note'])

    def test_a_read_pack_nobody_routes_halts_the_loop_and_resume_requeues_it(self):
        pack_id = self.make_pack(EMPRESA)
        self.read_pack(pack_id)
        with self.conn:
            self.conn.execute("UPDATE esc_readings SET at=? WHERE pack_id=? AND side='opus'",
                              ('2020-01-01T00:00:00+00:00', pack_id))
        decision = E.decide(self.root)
        self.assertEqual(decision['action'], 'HALT')
        self.assertIn('sin dueño', decision['note'])
        out = E.step(self.root, self.base, spawn=self.spawn()[0])
        self.assertEqual(out['action'], 'HALT')
        self.assertTrue(E.load_state(self.root)['halted'])
        self.assertEqual(E.status(self.root)['orphans'], [pack_id])
        resumed = E.resume(self.root)
        self.assertEqual(resumed['reencolados'], [pack_id])
        self.assertFalse(E.load_state(self.root)['halted'])
        # Reencolado: ya no es huérfano (antes volvía a parar el bucle en el acto) y ROUTE lo toma.
        self.assertEqual(E.status(self.root)['orphans'], [])
        self.assertNotEqual(E.decide(self.root)['action'], 'HALT')

    def test_the_breaker_halts_after_three_non_quota_failures(self):
        E.census(self.root, self.base, 'pending')
        for _ in range(2):
            E._fail(self.root, 'x', 'other')
        self.assertFalse(E.load_state(self.root)['halted'])
        E._fail(self.root, 'x', 'other')
        self.assertTrue(E.load_state(self.root)['halted'])
        self.assertEqual(E.step(self.root, self.base, spawn=self.spawn()[0])['action'], 'HALT')
        E.resume(self.root)
        self.assertEqual(E.load_state(self.root)['consecutiveFailures'], 0)
        self.assertEqual(E.decide(self.root)['action'], 'CLASSIFY')

    def test_a_config_failure_halts_at_once(self):
        E._fail(self.root, 'x', 'config: falta TYPESAFE_API_KEY')
        state = E.load_state(self.root)
        self.assertTrue(state['halted'])
        self.assertEqual(state['consecutiveFailures'], 0)
        self.assertIn('TYPESAFE', state['haltReason'])

    def test_halt_and_resume_are_explicit(self):
        E.halt(self.root, 'el dueño para el latido')
        self.assertEqual(E.decide(self.root)['action'], 'HALT')
        E.resume(self.root)
        self.assertFalse(E.load_state(self.root)['halted'])
        self.assertIsNone(E.load_state(self.root)['window']['pausedUntil'])

    def test_the_shared_runner_lock_stops_a_second_supervisor(self):
        E.census(self.root, self.base, 'pending')
        lock = Path(self.base) / 'runner.lock'
        lock.parent.mkdir(parents=True, exist_ok=True)
        spawn, calls = self.spawn()
        with lock.open('a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                out = E.step(self.root, self.base, spawn=spawn)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
        self.assertEqual(out['action'], 'RUNNING')
        self.assertEqual(calls, [])


# ── foto del bucle ────────────────────────────────────────────────────────────

class StatusTest(LoopBase):
    def test_g7_is_zero_after_a_legitimate_close(self):
        pack_id = self.make_pack(EMPRESA, riesgo='bajo')
        self.read_pack(pack_id)
        E.route(self.root, self.base)
        out = E.status(self.root)
        self.assertEqual(out['g7']['cierres_indebidos'], 0)
        self.assertEqual(out['g7']['cierres_solo_jev'], 0)
        self.assertEqual(out['packs']['closed'], 1)
        self.assertEqual(out['resoluciones']['opus-cerrada'], len(json.loads(self.row(pack_id)['unit_ids'])))
        self.assertFalse(out['orphans'])

    def test_g7_catches_a_high_risk_unit_closed_by_opus_alone(self):
        pack_id = self.make_pack(ALEJO, riesgo='alto')
        unit_ids = json.loads(self.row(pack_id)['unit_ids'])
        with self.conn:
            self.conn.execute("UPDATE units SET state='verified',resolution='opus-cerrada' "
                              'WHERE unit_id=?', (unit_ids[0],))
            self.conn.execute("UPDATE esc_packs SET state='closed' WHERE pack_id=?", (pack_id,))
            self.conn.execute("DELETE FROM esc_readings WHERE pack_id=?", (pack_id,))
        out = E.status(self.root)
        self.assertEqual(out['g7']['cierres_indebidos'], 1)
        self.assertEqual(out['g7']['cierres_solo_jev'], 1)
        self.assertEqual(out['g7']['packs_solo_jev'], [pack_id])

    def test_g7_does_not_count_a_pack_the_owner_closed_as_solo_jev(self):
        pack_id = self.make_pack(ALEJO, riesgo='critico')
        with self.conn:
            self.conn.execute("UPDATE esc_packs SET state='closed',route='humano' WHERE pack_id=?", (pack_id,))
            self.conn.execute("DELETE FROM esc_readings WHERE pack_id=?", (pack_id,))
        self.assertEqual(E.status(self.root)['g7']['cierres_solo_jev'], 0)

    def test_report_writes_the_snapshot_and_asks_t9_for_the_tanda(self):
        pack_id = self.make_pack(BENITO, riesgo='critico', clase='seudonimo')
        self.read_pack(pack_id)
        E.route(self.root, self.base)
        exported = []

        def export(root, conn, packs, date):
            exported.append(list(packs))
            return (Path(root) / 'esc' / 'report' / f'tanda-{date}.md',
                    Path(root) / 'esc' / 'report' / f'tanda-{date}.json')

        with escalate_module(export=export):
            out = E.report(self.root, self.base)
            tanda = E.tanda(self.root)
        self.assertEqual(exported, [[pack_id], [pack_id]])
        self.assertEqual(tanda['packs'], 1)
        text = Path(out['report']).read_text()
        self.assertIn('G7 (AC6)', text)
        self.assertIn('tanda-', text.split('## Resoluciones')[1])

    def test_tanda_apply_delegates_to_t9(self):
        answers = Path(self.base) / 'respuestas.json'
        answers.write_text('{}')
        with escalate_module(merge=lambda root, conn, path, date: ['E-000000000001']):
            out = E.tanda_apply(self.root, str(answers))
        self.assertEqual(out['packs'], ['E-000000000001'])


if __name__ == '__main__':
    unittest.main()


class SpawnCommandTest(unittest.TestCase):
    """El comando real de escalate lleva el modo de Astra del estado (ciega por defecto)."""

    def test_escalate_command_uses_state_or_default(self):
        import tempfile, json as _json
        from pathlib import Path as _P
        from src import qa_esc as _E
        root = _P(tempfile.mkdtemp())
        _E.save_state(root, _E.initial_state())
        captured = {}

        class _Proc:
            pid = 4242
            def poll(self): return None
        with unittest.mock.patch.object(_E.subprocess, 'Popen', side_effect=lambda cmd, **k: (captured.__setitem__('cmd', cmd), _Proc())[1]):
            _E._spawn(root, 'ESCALATE', 2)
        self.assertIn('never', captured['cmd'])
        st = _E.load_state(root); st['astraTools'] = 'auto'; _E.save_state(root, st)
        captured.clear()
        with unittest.mock.patch.object(_E.subprocess, 'Popen', side_effect=lambda cmd, **k: (captured.__setitem__('cmd', cmd), _Proc())[1]):
            _E._spawn(root, 'ESCALATE', 2)
        self.assertIn('auto', captured['cmd'])



class QuotaResetTest(unittest.TestCase):
    """Un límite semanal trae su hora: la pausa llega hasta ella, no a una hora a ciegas."""

    def setUp(self):
        from src import qa_esc as E
        self.E = E
        self.root = Path(tempfile.mkdtemp())
        E.save_state(self.root, E.initial_state('pending'))

    def test_pause_honours_reset_time_in_notice(self):
        from datetime import datetime
        folder = self.root / 'esc' / 'attempts' / 'a1'
        folder.mkdir(parents=True)
        (folder / 'events.jsonl').write_text(
            '{"type":"error","message":"You\u2019ve hit your usage limit. Visit x or try again at '
            'Sep 28th, 2099 1:47 PM."}\n')
        self.E._fail(self.root, 'P1', 'quota', {'attempt_id': 'a1'})
        expected = datetime(2099, 9, 28, 13, 47).astimezone().timestamp()
        self.assertEqual(self.E.load_state(self.root)['window']['pausedUntil'], expected)

    def test_pause_without_reset_time_waits_one_hour(self):
        before = self.E.utcnow().timestamp()
        self.E._fail(self.root, 'P1', 'quota', {'attempt_id': 'missing'})
        paused = self.E.load_state(self.root)['window']['pausedUntil']
        self.assertTrue(before + self.E.PAUSE_RETRY_SECONDS <= paused
                        < before + self.E.PAUSE_RETRY_SECONDS + 60)
