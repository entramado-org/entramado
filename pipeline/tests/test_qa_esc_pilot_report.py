"""Spec 063 T14 — informe del piloto y sus siete puertas. Sin modelos, sin red, sin grafo.

El censo es el falso de `tests/test_qa_esc_packs.EscBase`; las filas `esc_packs`/`esc_readings`,
los recibos y las decisiones finales se escriben a mano. La herramienta vive en el vault
(`specs/` es un symlink), así que se importa por ruta con `importlib`.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

from src.qa_esc import REPO, SPEC_DIR
from src.qa_integral import canonical, digest, now
from tests.test_qa_esc_packs import ALEJO, BENITO, EMPRESA, EscBase

TOOL = Path(REPO) / SPEC_DIR / 'tools-063' / 'pilot_report.py'
GOLD_RES = 'humano-2026-09-17e'


def load_tool():
    spec = importlib.util.spec_from_file_location('pilot_report_063', TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = load_tool()


def usage(fresh, cached=0):
    """Reparte `fresh` entre los cuatro campos que cuentan como tokens frescos."""
    return {'input': fresh - 3, 'output': 1, 'cache_write': 1, 'reasoning': 1, 'cached': cached}


def receipt(fresh, *, cached=0, cost=0.5, turns=3, searches=2, seconds=100.0, effort='xhigh',
            model='claude-opus-5', error=None, denials=(), tool_calls=()):
    return {'attempt_id': f'a{fresh}', 'model': model, 'effort': effort, 'usage': usage(fresh, cached),
            'cost_usd': cost, 'num_turns': turns, 'searches': [f'ev.py {i}' for i in range(searches)],
            'denials': list(denials), 'tool_calls': list(tool_calls), 'elapsed_seconds': seconds,
            'error': error}


def decision(unit_ids, verdicts=None, labels=None):
    verdicts, labels = verdicts or {}, labels or {}
    return {'entity_id': 'Person:x',
            'units': [{'unit_id': uid, 'verdict': verdicts.get(uid, 'verificado'),
                       'label': labels.get(uid, 'ok'), 'evidence': f'original.pdf p.1 ({uid[:6]})',
                       'confidence': 'alta', 'requested_sources': []} for uid in unit_ids],
            'summary': {'action': 'none'}, 'role': {'action': 'none'}, 'alias_deny': [],
            'estado': {'action': 'none'}, 'edges': [], 'editorial': [], 'notes': ''}


class ReportBase(EscBase):
    def setUp(self):
        super().setUp()
        from src.qa_esc_packs import ensure_esc_schema
        ensure_esc_schema(self.conn)
        self.entities_path = self.base / 'pilot-entities.json'
        self.write_entities()
        self.out = self.base / 'out' / 'PILOTO.md'

    # ── fixture ───────────────────────────────────────────────────────────────

    def write_entities(self, gold=(ALEJO, BENITO), pending=(EMPRESA,)):
        payload = {'gold_t0e': [{'entity': e, 'why': 'gold', 'overlay_ref': '1675317^'} for e in gold],
                   'pending_alto': [{'entity': e, 'why': 'pendiente'} for e in pending]}
        self.entities_path.write_text(json.dumps(payload, ensure_ascii=False))

    def unit_ids(self, entity, n=None, kind=None):
        clause = ' AND u.kind=?' if kind else ''
        params = (entity, kind) if kind else (entity,)
        rows = self.conn.execute('SELECT u.unit_id FROM units u JOIN objects o USING(object_id) '
                                 f'WHERE o.entity=?{clause} ORDER BY u.unit_id', params).fetchall()
        ids = [r['unit_id'] for r in rows]
        return ids[:n] if n else ids

    def add_units(self, entity, n, kind='summary'):
        """Unidades sintéticas colgadas del nodo de la entidad: el censo falso trae 9 y aquí
        hacen falta decenas para medir un porcentaje con dos decimales."""
        oid = self.conn.execute("SELECT object_id FROM objects WHERE object_kind='node' AND entity=?",
                                (entity,)).fetchone()[0]
        ids = []
        with self.conn:
            for i in range(n):
                uid = digest(['sintetica', entity, kind, i])
                self.conn.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,'
                                  'risk,prior) VALUES (?,?,?,?,?,0,100,"[]")',
                                  (uid, oid, kind, f'/summary@{i}:{i + 5}', f'Frase sintética {i}.'))
                ids.append(uid)
        return ids

    def add_pack(self, pack_id, entity, unit_ids, *, riesgo='medio', state='packed', route=None,
                 idx=1, total=1):
        with self.conn:
            self.conn.execute(
                'INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,pack_path,pack_sha256,'
                'overlay_ref,riesgo,clase,route,state,batch,created_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (pack_id, entity, 'entity', idx, total, json.dumps(unit_ids), f'/tmp/{pack_id}.json',
                 'sha', '1675317^', riesgo, 'figura_publica', route, state, 'b1', now()))
        return pack_id

    def add_reading(self, pack_id, side, result, rec):
        with self.conn:
            self.conn.execute('INSERT INTO esc_readings(attempt_id,pack_id,side,result,receipt,at) '
                              'VALUES (?,?,?,?,?,?)',
                              (f'{pack_id}:{side}', pack_id, side,
                               None if result is None else canonical(result), canonical(rec), now()))

    def add_event(self, unit_id, event, payload):
        with self.conn:
            seq = self.conn.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events').fetchone()[0]
            self.conn.execute('INSERT INTO events(seq,at,unit_id,event,payload) VALUES (?,?,?,?,?)',
                              (seq, now(), unit_id, event, canonical(payload)))

    def mark_gold(self, unit_id, corregido, etiqueta):
        """Una unidad del gold T0e: resolución del dueño + la etiqueta que dejó en la evidencia."""
        resolution = GOLD_RES + ('-corregido' if corregido else '')
        with self.conn:
            self.conn.execute("UPDATE units SET state='verified',resolution=? WHERE unit_id=?",
                              (resolution, unit_id))
        self.add_event(unit_id, 'corregido-humano' if corregido else 'verificado-humano',
                       {'evidence': f'[{etiqueta}] el dueño lo decidió el 17-sep'})

    def mark_unit(self, unit_id, state, resolution):
        with self.conn:
            self.conn.execute('UPDATE units SET state=?,resolution=? WHERE unit_id=?',
                              (state, resolution, unit_id))

    def write_decision(self, pack_id, resolution, sides, unit_ids):
        path = self.root / 'esc' / 'decisions' / f'{pack_id}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = decision(unit_ids)
        payload['_meta'] = {'pack_id': pack_id, 'entity': 'Person:x', 'kind': 'entity', 'index': 1,
                            'total': 1, 'resolution': resolution, 'route': 'cerrar',
                            'decided_at': now(), 'sides': list(sides), 'unit_ids': unit_ids,
                            'overlay_ref': '1675317^'}
        path.write_text(json.dumps(payload, ensure_ascii=False))

    # ── ejecución ─────────────────────────────────────────────────────────────

    def build(self):
        return R.build(self.root, self.base, str(self.entities_path), '2026-09-21')

    def run_cli(self, extra=()):
        return R.main(['--root', str(self.root), '--repo', str(self.base),
                       '--entities', str(self.entities_path), '--out', str(self.out),
                       '--fecha', '2026-09-21', *extra])

    def gate(self, data, gate_id):
        return next(g for g in data['puertas'] if g['id'] == gate_id)


# ── 1. coste ──────────────────────────────────────────────────────────────────

class CostTest(ReportBase):
    def test_subpacks_of_one_entity_are_added_up(self):
        units = self.unit_ids(ALEJO)
        self.add_pack('E-a1', ALEJO, units[:2], idx=1, total=2)
        self.add_pack('E-a2', ALEJO, units[2:4], idx=2, total=2)
        self.add_reading('E-a1', 'opus-qc', decision(units[:2]),
                         receipt(40_000, cached=1_000, cost=0.4, turns=3, searches=2, seconds=60.0))
        self.add_reading('E-a2', 'opus-qc', decision(units[2:4]),
                         receipt(60_000, cached=2_000, cost=0.6, turns=5, searches=3, seconds=40.0))
        data = self.build()
        row = next(e for e in data['coste']['entidades'] if e['entidad'] == ALEJO)
        self.assertEqual((row['lado'], row['packs'], row['frescos'], row['cache']),
                         ('opus-qc', 2, 100_000, 3_000))
        self.assertEqual((row['num_turns'], row['busquedas'], row['elapsed_seconds']), (8, 5, 100.0))
        self.assertAlmostEqual(row['cost_usd'], 1.0)
        self.assertEqual(data['coste']['lector']['por_entidad'][ALEJO]['frescos'], 100_000)

    def test_median_p90_and_projection(self):
        for i, (entity, fresh) in enumerate(((ALEJO, 100_000), (BENITO, 200_000), (EMPRESA, 300_000))):
            units = self.unit_ids(entity, 1)
            self.add_pack(f'E-{i}', entity, units)
            self.add_reading(f'E-{i}', 'opus', decision(units), receipt(fresh, cached=fresh))
        lector = self.build()['coste']['lector']
        self.assertEqual((lector['n'], lector['mediana'], lector['media'], lector['max']),
                         (3, 200_000, 200_000, 300_000))
        # p90 por rango más cercano sobre 3 valores: ceil(0,9 × 3) = 3 → el mayor.
        self.assertEqual(lector['p90'], 300_000)
        proy = R.projection(lector)
        self.assertEqual(proy['frescos']['por_mediana'], 200_000 * 830)
        self.assertEqual(proy['frescos']['por_media'], 200_000 * 830)
        self.assertAlmostEqual(proy['frescos']['ventanas_mediana'], 200_000 * 830 / 8_000_000)
        # La segunda proyección suma la caché leída: aquí el doble.
        self.assertEqual(proy['con_cache']['por_mediana'], 400_000 * 830)

    def test_totals_are_split_by_side(self):
        for i, (entity, side, fresh) in enumerate(((ALEJO, 'opus', 10_000), (ALEJO, 'fable-esc', 5_000),
                                                   (ALEJO, 'astra-esc', 7_000))):
            units = self.unit_ids(entity, 1)
            self.add_pack(f'E-{i}', entity, units)
            self.add_reading(f'E-{i}', side, decision(units), receipt(fresh))
        por_lado = self.build()['coste']['por_lado']
        self.assertEqual(por_lado['opus']['frescos_total'], 10_000)
        self.assertEqual(por_lado['fable-esc']['frescos_total'], 5_000)
        self.assertEqual(por_lado['astra-esc']['frescos_total'], 7_000)
        self.assertEqual(por_lado['opus-qc']['entidades'], 0)


# ── 2. validez ────────────────────────────────────────────────────────────────

class ValidityTest(ReportBase):
    def test_valid_at_first_rejections_and_searches(self):
        units = self.unit_ids(ALEJO, 3)
        for i, uid in enumerate(units):
            self.add_pack(f'E-{i}', ALEJO, [uid])
        self.add_reading('E-0', 'opus', decision([units[0]]), receipt(10, searches=4))
        self.add_reading('E-1', 'opus', decision([units[1]]), receipt(10, searches=10))
        self.add_reading('E-2', 'opus', None, receipt(10, searches=13, error='tool-budget-exceeded'))
        self.add_event(None, 'reading-rejected',
                       {'pack_id': 'E-2', 'side': 'opus', 'why': 'tool-budget-exceeded'})
        # Un rechazo previo sobre un par que acabó válido: cuenta como intento, no «a la primera».
        self.add_event(None, 'reading-rejected', {'pack_id': 'E-1', 'side': 'opus', 'why': 'quota'})
        # Ruido del ledger 061: un rechazo de otro pack no entra en el informe.
        self.add_event(None, 'reading-rejected', {'pack_id': 'AJENO', 'side': 'opus', 'why': 'otro'})
        valid = self.build()['validez']
        self.assertEqual((valid['intentos'], valid['validas'], valid['validas_a_la_primera']), (4, 2, 1))
        self.assertEqual(valid['tasa_a_la_primera'], 0.25)
        self.assertEqual(valid['anuladas'], {'tool-budget-exceeded': 1, 'quota': 1})
        self.assertEqual(valid['busquedas_lector'], {'n': 3, 'mediana': 10, 'max': 13})

    def test_denials_and_foreign_tools_are_counted(self):
        units = self.unit_ids(ALEJO, 1)
        self.add_pack('E-0', ALEJO, units)
        self.add_reading('E-0', 'opus', None,
                         receipt(10, error='unexpected-tool-use', denials=[{'id': 'x'}],
                                 tool_calls=['WebFetch', 'Bash: curl http://x']))
        valid = self.build()['validez']
        self.assertEqual(valid['con_denegacion'], 1)
        self.assertEqual(valid['herramientas_ajenas_n'], 2)
        self.assertEqual(valid['herramientas_ajenas'][0]['herramienta'], 'WebFetch')

    def test_jev_rows_are_not_readings(self):
        units = self.unit_ids(ALEJO, 1)
        self.add_pack('E-0', ALEJO, units)
        self.add_reading('E-0', 'jev-class', {'clase': 'figura_publica'}, {'backend': 'reglas'})
        data = self.build()
        self.assertEqual(data['validez']['intentos'], 0)
        self.assertIsNone(self.gate(data, 'G3')['pasa'])


# ── 3. falso-ok frente al gold ────────────────────────────────────────────────

class FalseOkTest(ReportBase):
    def gold_pack(self, entity, pack_id, gold, opus, riesgo='medio'):
        """`gold` = [(corregido, etiqueta)]; `opus` = [(verdict, label)], unidad a unidad."""
        units = self.unit_ids(entity, len(gold))
        self.add_pack(pack_id, entity, units, riesgo=riesgo)
        for uid, (corregido, etiqueta) in zip(units, gold):
            self.mark_gold(uid, corregido, etiqueta)
        verdicts = {uid: v for uid, (v, _) in zip(units, opus)}
        labels = {uid: label for uid, (_, label) in zip(units, opus)}
        self.add_reading(pack_id, 'opus-qc', decision(units, verdicts, labels), receipt(10))
        return units

    def test_strict_and_background_false_ok(self):
        # 4 correcciones del dueño (3 de fondo: reescrita, retirada, estado; 1 de atribución),
        # Opus dice «verificado» en 2 de ellas (1 de fondo) y acierta el resto.
        self.gold_pack(ALEJO, 'E-a',
                       gold=[(True, 'reescrita'), (True, 'retirada'), (True, 'estado'),
                             (True, 'atrib'), (False, 'ok')],
                       opus=[('verificado', 'ok'), ('corregido', 'retirada'), ('corregido', 'estado'),
                             ('verificado', 'ok'), ('verificado', 'ok')])
        falso = self.build()['falso_ok']
        self.assertEqual((falso['unidades_gold'], falso['con_lectura']), (5, 5))
        self.assertEqual(falso['gold_corregido'], 4)
        self.assertEqual(falso['falso_ok_estricto_n'], 2)
        self.assertAlmostEqual(falso['falso_ok_estricto'], 2 / 4)
        self.assertEqual(falso['gold_fondo'], 3)
        self.assertEqual(falso['falso_ok_fondo_n'], 1)
        self.assertAlmostEqual(falso['falso_ok_fondo'], 1 / 3)
        self.assertAlmostEqual(falso['acuerdo_veredicto'], 3 / 5)
        self.assertEqual({d['unit_id'] for d in falso['discrepancias']},
                         {u['unit_id'] for u in falso['filas']
                          if u['gold_verdict'] != u['opus_verdict']})
        self.assertEqual(len(falso['discrepancias']), 2)
        self.assertTrue(all(len(d['opus_evidencia']) <= 200 for d in falso['discrepancias']))

    def test_label_class_agreement_only_when_both_correct(self):
        # Ambos corrigen 3 veces; «reescrita» y «retirada» son la MISMA clase (fondo), «alias» no.
        self.gold_pack(ALEJO, 'E-a',
                       gold=[(True, 'reescrita'), (True, 'reescrita'), (True, 'reescrita'),
                             (True, 'reescrita')],
                       opus=[('corregido', 'reescrita'), ('corregido', 'retirada'),
                             ('corregido', 'alias'), ('verificado', 'ok')])
        falso = self.build()['falso_ok']
        self.assertEqual(falso['ambos_corrigen'], 3)
        self.assertAlmostEqual(falso['acuerdo_clase_etiqueta'], 2 / 3)

    def test_tables_by_risk_and_kind_and_only_gold_entities(self):
        self.gold_pack(ALEJO, 'E-a', gold=[(True, 'reescrita')], opus=[('verificado', 'ok')],
                       riesgo='alto')
        self.gold_pack(BENITO, 'E-b', gold=[(True, 'reescrita')], opus=[('corregido', 'reescrita')],
                       riesgo='bajo')
        # La entidad pendiente no es gold: su lectura no entra en el falso-ok.
        pend = self.unit_ids(EMPRESA, 1)
        self.add_pack('E-p', EMPRESA, pend)
        self.add_reading('E-p', 'opus', decision(pend), receipt(10))
        falso = self.build()['falso_ok']
        self.assertEqual(falso['unidades_gold'], 2)
        self.assertAlmostEqual(falso['por_riesgo']['alto']['falso_ok_fondo'], 1.0)
        self.assertAlmostEqual(falso['por_riesgo']['bajo']['falso_ok_fondo'], 0.0)
        self.assertEqual(sorted(falso['por_kind']), sorted({f['kind'] for f in falso['filas']}))
        self.assertAlmostEqual(falso['medio_bajo']['falso_ok_fondo'], 0.0)


# ── 4. rutas y cierres ────────────────────────────────────────────────────────

class RoutesTest(ReportBase):
    def test_route_table_and_closure_rates(self):
        units = self.unit_ids(ALEJO)[:5]
        self.assertEqual(len(units), 5)
        self.add_pack('E-1', ALEJO, units[:2], state='closed', route='cerrar')
        self.add_pack('E-2', ALEJO, units[2:4], state='closed', route='escalar')
        self.add_pack('E-3', ALEJO, units[4:5], state='tanda', route='dueno')
        self.mark_unit(units[0], 'verified', 'opus-cerrada')
        self.mark_unit(units[1], 'verified', 'opus-cerrada-corregido')
        self.mark_unit(units[2], 'verified', 'triple-cerrada')
        self.mark_unit(units[3], 'verified', 'triple-cerrada-corregido')
        self.mark_unit(units[4], 'needs-human', 'tanda-2026-09-21-pendiente')
        self.write_decision('E-1', 'opus-cerrada', ['opus'], units[:2])
        self.write_decision('E-2', 'triple-cerrada', ['opus', 'fable-esc', 'astra-esc'], units[2:4])
        ruta = self.build()['rutas']
        self.assertEqual(ruta['packs_por_route'], {'cerrar': 1, 'escalar': 1, 'dueno': 1})
        self.assertEqual(ruta['packs_por_state'], {'closed': 2, 'tanda': 1})
        self.assertEqual((ruta['unidades_universo'], ruta['opus_cerrada'], ruta['triple_cerrada'],
                          ruta['tanda']), (5, 2, 2, 1))
        self.assertAlmostEqual(ruta['pct_cerradas'], 4 / 5)
        self.assertAlmostEqual(ruta['pct_tanda'], 1 / 5)
        self.assertEqual(ruta['decisiones']['por_resolucion'],
                         {'opus-cerrada': 1, 'triple-cerrada': 1})

    def test_owner_decisions_of_the_pilot_count_and_older_rounds_do_not(self):
        units = self.unit_ids(ALEJO)[:5]
        self.add_pack('E-1', ALEJO, units, state='closed', route='arbitraje')
        self.mark_unit(units[0], 'verified', 'arbitraje-2026-09-23')
        self.mark_unit(units[1], 'verified', 'arbitraje-2026-09-23-prudente-corregido')
        self.mark_unit(units[2], 'verified', 'humano-2026-09-24-corregido')
        self.mark_unit(units[3], 'verified', 'humano-2026-09-17e')
        self.mark_unit(units[4], 'pending', None)
        ruta = self.build()['rutas']
        # La ronda 061 (09-17e) queda fuera; la tanda del piloto cuenta como del dueño, no como cierre.
        self.assertEqual((ruta['unidades_universo'], ruta['triple_cerrada'], ruta['prudentes'],
                          ruta['dueno'], ruta['tanda']), (4, 2, 1, 1, 0))
        self.assertAlmostEqual(ruta['pct_cerradas'], 2 / 4)

    def test_dual_agreement_and_astra_cost(self):
        units = self.unit_ids(ALEJO, 2)
        self.add_pack('E-1', ALEJO, units[:1], state='closed', route='escalar')
        self.add_pack('E-2', ALEJO, units[1:], state='tanda', route='escalar')
        for pack, uid in (('E-1', units[0]), ('E-2', units[1])):
            self.add_reading(pack, 'opus', decision([uid]), receipt(10))
            self.add_reading(pack, 'fable-esc', decision([uid]), receipt(10, model='claude-fable-5-1'))
        self.add_reading('E-1', 'astra-esc', decision(units[:1]),
                         receipt(300_000, searches=0, model='gpt-astra'))
        self.add_reading('E-2', 'astra-esc', decision(units[1:]),
                         receipt(200_000, searches=5, model='gpt-astra'))
        self.add_event(units[0], 'esc-cerrada', {'pack_id': 'E-1', 'mode': 'triple'})
        self.add_event(units[1], 'esc-tanda', {'pack_id': 'E-2', 'reasons': ['veredicto distinto']})
        ruta = self.build()['rutas']
        self.assertEqual(ruta['escalados'], 2)
        self.assertEqual(ruta['acuerdo_dual'], {'triple': 1, 'tanda': 1})
        self.assertEqual((ruta['astra_lecturas'], ruta['astra_con_herramienta']), (2, 1))
        self.assertAlmostEqual(ruta['astra_pct_herramienta'], 0.5)
        self.assertEqual(ruta['astra_por_entidad'][ALEJO]['frescos'], 500_000)


# ── 5-6. puertas ──────────────────────────────────────────────────────────────

class GatesTest(ReportBase):
    def reader(self, entity, pack, fresh, *, searches=2, riesgo='medio', units=None):
        units = units or self.unit_ids(entity, 1)
        self.add_pack(pack, entity, units, riesgo=riesgo)
        self.add_reading(pack, 'opus', decision(units), receipt(fresh, searches=searches))
        return units

    def test_g1_thresholds_are_exact(self):
        """G1 por pack (decisión 2026-09-24): mediana ≤ 80 k, p90 ≤ 120 k y proyección por unidad ≤ 70 M."""
        entities = (EMPRESA, ALEJO, BENITO)
        ten = {entity: self.add_units(entity, 10) for entity in entities}
        for i, entity in enumerate(entities):
            self.reader(entity, f'E-{i}', 80_000, units=ten[entity])
        g1 = self.gate(self.build(), 'G1')
        self.assertEqual(g1['metricas']['mediana_pack'], 80_000)
        self.assertEqual(g1['metricas']['proyeccion'], 8_000 * 2459)
        self.assertTrue(g1['pasa'])

        def rebuild(fresh, n_units):
            pick = {e: (ten[e] if n_units == 10 else self.unit_ids(e, 1)) for e in entities}
            with self.conn:
                self.conn.execute('DELETE FROM esc_readings')
                self.conn.execute('DELETE FROM esc_packs')
            for i, entity in enumerate(entities):
                self.reader(entity, f'F{fresh}-{i}', fresh, units=pick[entity])
            return self.gate(self.build(), 'G1')
        # Un token más por pack que la mediana permitida.
        self.assertFalse(rebuild(80_001, 10)['pasa'])
        # Packs baratos pero de una sola unidad: la proyección (30 k × 2459) se pasa de 70 M.
        self.assertFalse(rebuild(30_000, 1)['pasa'])

    def test_g2_threshold_is_exact_on_medium_and_low_risk(self):
        # 40 unidades de fondo en riesgo medio, 1 falso-ok = 2,5 % → PASA.
        units = self.add_units(ALEJO, 40)
        self.add_pack('E-a', ALEJO, units, riesgo='medio')
        for uid in units:
            self.mark_gold(uid, True, 'reescrita')
        verdicts = {uid: 'corregido' for uid in units}
        verdicts[units[0]] = 'verificado'
        self.add_reading('E-a', 'opus-qc',
                         decision(units, verdicts, {uid: 'reescrita' for uid in units}), receipt(10))
        data = self.build()
        g2 = self.gate(data, 'G2')
        self.assertAlmostEqual(g2['metricas']['falso_ok_fondo'], 1 / 40)
        self.assertTrue(g2['pasa'])
        # Dos falsos-ok = 5 % > 3,5 % → NO PASA.
        verdicts[units[1]] = 'verificado'
        with self.conn:
            self.conn.execute('DELETE FROM esc_readings')
        self.add_reading('E-a', 'opus-qc',
                         decision(units, verdicts, {uid: 'reescrita' for uid in units}), receipt(10))
        self.assertFalse(self.gate(self.build(), 'G2')['pasa'])

    def test_g3_thresholds(self):
        # Mediana 8 y máximo 12: los dos topes justos → PASA.
        self.reader(ALEJO, 'E-0', 10, searches=8)
        self.reader(BENITO, 'E-1', 10, searches=8)
        self.reader(EMPRESA, 'E-2', 10, searches=12)
        data = self.build()
        self.assertEqual(data['validez']['busquedas_lector'], {'n': 3, 'mediana': 8, 'max': 12})
        self.assertTrue(self.gate(data, 'G3')['pasa'])
        # Una búsqueda más que el tope y la puerta cae por el máximo.
        with self.conn:
            self.conn.execute("DELETE FROM esc_readings WHERE pack_id='E-2'")
        self.add_reading('E-2', 'opus', decision(self.unit_ids(EMPRESA, 1)), receipt(10, searches=13))
        self.assertFalse(self.gate(self.build(), 'G3')['pasa'])
        # Y también por la mediana, con todos por debajo del máximo.
        with self.conn:
            self.conn.execute('DELETE FROM esc_readings')
        for pack, entity in (('E-0', ALEJO), ('E-1', BENITO), ('E-2', EMPRESA)):
            self.add_reading(pack, 'opus', decision(self.unit_ids(entity, 1)), receipt(10, searches=9))
        self.assertFalse(self.gate(self.build(), 'G3')['pasa'])

    def test_g4_threshold(self):
        units = self.unit_ids(ALEJO, 5)
        self.add_pack('E-1', ALEJO, units, state='closed', route='cerrar')
        for uid in units[:3]:
            self.mark_unit(uid, 'verified', 'opus-cerrada')
        for uid in units[3:]:
            self.mark_unit(uid, 'needs-human', 'tanda-2026-09-21-pendiente')
        self.assertAlmostEqual(self.gate(self.build(), 'G4')['metricas']['pct_cerradas'], 0.6)
        self.assertTrue(self.gate(self.build(), 'G4')['pasa'])
        self.mark_unit(units[2], 'needs-human', 'tanda-2026-09-21-pendiente')
        self.assertFalse(self.gate(self.build(), 'G4')['pasa'])

    def test_g5_reads_the_calibration_report(self):
        self.assertFalse(self.gate(self.build(), 'G5')['pasa'])
        summary = {'gold': 925, 'clase': {'exactitud': 0.95, 'recall': {}}, 'riesgo': {},
                   'conducta': {}, 'ruta': {'n': 0}}
        for relative in ('esc/jev/calibration.json', 'esc/jev/run2/calibration.json'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(summary))
        data = self.build()
        self.assertTrue(self.gate(data, 'G5')['pasa'])
        self.assertTrue(data['jev']['fichero'].endswith('run2/calibration.json'))
        self.assertIs(data['jev']['veredicto']['jev_route'], False)

    def test_g6_thresholds(self):
        # Cinco entidades escaladas, una sola con herramienta: 20 % justo, y los dos topes justos.
        pending = (EMPRESA, 'Person:e1', 'Person:e2', 'Person:e3', 'Person:e4')
        self.write_entities(pending=pending)
        for i, entity in enumerate(pending):
            self.add_pack(f'E-{i}', entity, [], route='escalar')
            self.add_reading(f'E-{i}', 'astra-esc', {'units': []},
                             receipt(400_000 if i == 0 else 60_000, searches=3 if i == 0 else 0))
        data = self.build()
        self.assertAlmostEqual(data['rutas']['astra_pct_herramienta'], 0.2)
        self.assertEqual(self.gate(data, 'G6')['metricas'],
                         {'max_con_herramienta': 400_000, 'max_ciega': 60_000, 'pct_herramienta': 0.2})
        self.assertTrue(self.gate(data, 'G6')['pasa'])
        with self.conn:
            self.conn.execute("DELETE FROM esc_readings WHERE pack_id='E-0'")
        self.add_reading('E-0', 'astra-esc', {'units': []}, receipt(400_001, searches=3))
        self.assertFalse(self.gate(self.build(), 'G6')['pasa'])

    def test_g7_comes_from_qa_esc(self):
        from src.qa_esc import g7
        units = self.unit_ids(ALEJO, 1)
        self.add_pack('E-1', ALEJO, units, riesgo='alto', state='closed', route='cerrar')
        self.mark_unit(units[0], 'verified', 'opus-cerrada')
        data = self.build()
        self.assertEqual(data['g7'], g7(self.conn))
        self.assertEqual(data['g7']['cierres_indebidos'], 1)
        self.assertFalse(self.gate(data, 'G7')['pasa'])
        self.assertEqual(data['veredicto']['estado'], 'NO-GO')
        self.assertIn('G7', [a['puerta'] for a in data['veredicto']['ajustes']])


# ── 7. informe, tolerancia y efectos secundarios ──────────────────────────────

class ReportTest(ReportBase):
    def test_empty_ledger_renders_without_data_instead_of_breaking(self):
        self.assertEqual(self.run_cli(), 0)
        text = self.out.read_text()
        for heading in ('## 1. Coste por entidad y modelo', '## 2. Validez de las lecturas',
                        '## 3. Falso-ok de Opus solo frente al gold T0e', '## 4. Rutas y cierres',
                        '## 5. G7', '## 6. Calibración de Jev'):
            self.assertIn(heading, text)
        self.assertIn('_Sin lecturas todavía._', text)
        self.assertIn('SIN DATOS', text)
        data = self.build()
        self.assertEqual([g['id'] for g in data['puertas'] if g['pasa'] is None],
                         ['G1', 'G2', 'G3', 'G4', 'G6'])
        self.assertEqual(data['veredicto']['estado'], 'NO-GO')  # G5 sin calibración

    def test_markdown_and_json_carry_the_numbers(self):
        units = self.unit_ids(ALEJO, 2)
        self.add_pack('E-1', ALEJO, units)
        self.add_reading('E-1', 'opus-qc', decision(units), receipt(123_456))
        json_out = self.base / 'out' / 'pilot.json'
        self.assertEqual(self.run_cli(['--json', str(json_out)]), 0)
        text = self.out.read_text()
        self.assertIn('123 456', text)
        self.assertIn('| G1 |', text)
        data = json.loads(json_out.read_text())
        self.assertEqual(data['coste']['lector']['por_entidad'][ALEJO]['frescos'], 123_456)
        self.assertEqual(data['proyeccion']['entidades'], 830)
        self.assertEqual(len(data['puertas']), 7)

    def test_the_tool_never_writes_inside_the_root(self):
        units = self.unit_ids(ALEJO, 1)
        self.add_pack('E-1', ALEJO, units)
        self.add_reading('E-1', 'opus-qc', decision(units), receipt(1_000))
        self.write_decision('E-1', 'opus-cerrada', ['opus-qc'], units)
        self.conn.commit()

        def fingerprint():
            out = {}
            for path in sorted(Path(self.root).rglob('*')):
                if path.is_file():
                    out[str(path.relative_to(self.root))] = hashlib.sha256(path.read_bytes()).hexdigest()
            return out

        before = fingerprint()
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(fingerprint(), before)
        self.assertTrue(self.out.exists())


if __name__ == '__main__':
    unittest.main()
