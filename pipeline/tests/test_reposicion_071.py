"""Spec 071, enmienda 2 (AC18): `_reposicion_071.py` emite un MERGE por gemela con sus props exactas del censo.

Censo sintético con los dos unit_id del script y valores inventados; el censo real, si está a mano
(`REGISTRO_ROOT` o la raíz por defecto del checkout principal), se contrasta además prop a prop. Sin grafo.
Un fallo contra el censo real nombra unidades (8 hex) y claves, nunca un valor: serían citas privadas en el
informe de la suite. Y `registro_check` toma los tipos simétricos de la misma ontología que el loader y el apply.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from pipeline.scripts import _reposicion_071 as RP
from pipeline.scripts import registro_check as RC
from pipeline.src import qa_audit, qa_esc_apply, relations_loader

U1, U2 = RP.UNIDADES
(D1, D2, RESULTANTE), = RP.DEDUPLICAR
A, B, C = 'Company:sociedad-a-ficticia', 'Company:sociedad-b-ficticia', 'Company:sociedad-c-ficticia'
P, D = 'Person:persona-ficticia', 'Company:sociedad-d-ficticia'
GEMELA = {'start': P, 'end': D, 'type': 'OWNS', 'public_label': 'Propietaria de',
          'props': {'source_kind': 'prensa', 'source_quote': 'La persona ficticia es dueña de la sociedad D',
                    'url': 'https://medio.example/noticia-ficticia', 'outlet': 'Medio Ficticio', 'date': '2025-01-01',
                    'date_year': 2025, 'note': 'nota ficticia'}}
VALORES = {U1: {'start': A, 'end': B, 'type': 'OWNS', 'public_label': 'Propietaria de',
                'props': {'source_kind': 'judicial', 'source_doc': 'informe-ficticio.pdf', 'source_page': 76,
                          'source_quote': "A es socia única de B, según el informe d'ejemplo", 'date_year': 2016}},
           U2: {'start': A, 'end': C, 'type': 'OWNS', 'public_label': 'Propietaria de',
                'props': {'source_kind': 'judicial', 'source_doc': 'informe-ficticio.pdf', 'source_page': 32,
                          'source_quote': 'A participa al 100% en C', 'source_chunk_id': 'c0ffee00c0ffee00'}},
           D1: GEMELA, D2: GEMELA}
_PAR = re.compile(r"(\w+): ('(?:[^'\\]|\\.)*'|-?\d+(?:\.\d+)?|true|false)")
_MARCA = re.compile(r'\[([0-9a-f]{8})\]')
CENTINELA = 'CITA_RETIRADA_SINTETICA_XYZ'
_FALTA = object()


def props_de(sentencia):
    """El mapa de props del MERGE, leído de vuelta del texto."""
    mapa = sentencia[sentencia.index('-[:'):sentencia.rindex(']->')]
    mapa = mapa[mapa.index('{') + 1:mapa.rindex('}')]
    out = {}
    for clave, literal in _PAR.findall(mapa):
        if literal.startswith("'"):
            out[clave] = re.sub(r'\\(.)', r'\1', literal[1:-1])
        elif literal in ('true', 'false'):
            out[clave] = literal == 'true'
        else:
            out[clave] = float(literal) if '.' in literal else int(literal)
    return out


def _comparar_props(uid, esperado, obtenido):
    """Falla si las props difieren nombrando solo la unidad (8 hex) y las claves distintas: con el censo real,
    el mensaje de `assertEqual` volcaría las citas."""
    claves = sorted(k for k in esperado.keys() | obtenido.keys() if esperado.get(k, _FALTA) != obtenido.get(k, _FALTA))
    if claves:
        raise AssertionError(f'{uid[:8]}: props distintas del censo en {", ".join(claves)}')


def _marcas(reglas):
    """Las unidades (8 hex) que nombra el motivo de cada regla: lo único que el informe puede decir de ellas."""
    return sorted({m for regla in reglas for m in _MARCA.findall(regla.get('reason') or '')})


def censo_real():
    root = Path(os.environ.get('REGISTRO_ROOT') or RP.ROOT)
    census = root / 'census.sqlite'
    return root if census.is_file() and census.stat().st_size else None


class ReposicionBase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='reposicion-071-'))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def censo(self, valores):
        db = sqlite3.connect(self.base / 'census.sqlite')
        db.execute('CREATE TABLE units (unit_id TEXT PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL)')
        db.executemany('INSERT INTO units VALUES (?,?,?)',
                       [(uid, 'edge', json.dumps(v, ensure_ascii=False)) for uid, v in valores.items()])
        db.commit()
        db.close()

    def run_script(self, *args, root=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = RP.main(['--root', str(root or self.base), *args])
        return code, out.getvalue(), err.getvalue()


class ReposicionTest(ReposicionBase):
    def test_two_merges_with_exactly_the_census_props(self):
        self.censo(VALORES)
        code, out, _ = self.run_script()
        self.assertEqual(code, 0)
        # Lo que ejecuta `make qa-migrate`: fuera los comentarios, partido por «;».
        sentencias = [s.strip() for s in qa_audit._strip_comments(out).split(';') if s.strip()]
        self.assertEqual(len(sentencias), 2 + 2 * len(RP.DEDUPLICAR))
        self.assertEqual(out.count('MERGE '), 2)
        for uid, sentencia in zip(RP.UNIDADES, sentencias):
            valor = VALORES[uid]
            self.assertTrue(sentencia.startswith(f"MATCH (a {{entity_id: '{valor['start']}'}}), "
                                                 f"(b {{entity_id: '{valor['end']}'}})"), sentencia)
            self.assertIn(f"MERGE (a)-[:{valor['type']} {{", sentencia)
            self.assertEqual(props_de(sentencia), valor['props'])
        self.assertIn("'A es socia única de B, según el informe d\\'ejemplo'", out)

    def test_identical_twins_are_deduplicated_to_exactly_one(self):
        # Spec 071: la retirada cuya gemela se retipa no lleva regla `deny` (mataría también la conservada). La
        # migración deja una copia de la firma, en el tipo original o en el del retype, y exige que quede una.
        self.censo(VALORES)
        code, out, _ = self.run_script()
        self.assertEqual(code, 0)
        borrar, comprobar = [s.strip() for s in qa_audit._strip_comments(out).split(';') if s.strip()][2:]
        patron = (f"MATCH (a {{entity_id: '{P}'}})-[r]->(b {{entity_id: '{D}'}})\n"
                  f"WHERE type(r) IN ['{RESULTANTE}', 'OWNS'] AND r.date = '2025-01-01' AND r.date_year = 2025 AND "
                  "r.note = 'nota ficticia' AND r.outlet = 'Medio Ficticio' AND r.source_kind = 'prensa' AND "
                  "r.source_quote = 'La persona ficticia es dueña de la sociedad D' AND "
                  "r.url = 'https://medio.example/noticia-ficticia'\n")
        self.assertEqual(borrar, patron + 'WITH collect(r) AS copias\nFOREACH (x IN tail(copias) | DELETE x)')
        self.assertEqual(comprobar, patron + "WITH count(r) AS n\nCALL apoc.util.validate(n <> 1, 'reposicion-071: la "
                                             "deduplicacion deja %d copias', [n])\nRETURN n")
        self.assertIn(f'// deduplicación: {D1[:8]} (gemela de {D2[:8]})', out)

    def test_twins_that_are_not_identical_emit_nothing(self):
        distinta = json.loads(json.dumps(VALORES))
        distinta[D2]['props']['note'] = 'otra nota'
        self.censo(distinta)
        code, out, err = self.run_script()
        self.assertEqual((code, out), (1, ''))
        self.assertIn('no son idénticas', err)

    def test_a_missing_unit_emits_nothing(self):
        self.censo({U1: VALORES[U1]})
        code, out, err = self.run_script()
        self.assertEqual((code, out), (1, ''))
        self.assertIn(U2[:8], err)

    def test_a_value_the_migration_runner_would_split_emits_nothing(self):
        roto = json.loads(json.dumps(VALORES))
        roto[U2]['props']['source_quote'] = 'A participa; C también'
        self.censo(roto)
        code, out, _ = self.run_script()
        self.assertEqual((code, out), (1, ''))

    def test_the_output_never_lands_inside_the_repo(self):
        self.censo(VALORES)
        dentro = RP.REPO / 'pipeline' / 'reposicion-071-prueba.cypher'
        code, out, _ = self.run_script('--out', str(dentro))
        self.assertEqual((code, out), (2, ''))
        self.assertFalse(dentro.exists())
        fuera = self.base / 'reposicion.cypher'
        self.assertEqual(self.run_script('--out', str(fuera))[0], 0)
        self.assertEqual(fuera.read_text(encoding='utf-8').count('MERGE '), 2)

    def test_without_the_census_it_stops(self):
        code, out, err = self.run_script()
        self.assertEqual((code, out), (2, ''))
        self.assertIn('falta el censo', err)


@unittest.skipUnless(censo_real(), 'sin el censo privado de la 063 (REGISTRO_ROOT)')
class CensoRealTest(ReposicionBase):
    def test_the_real_census_gives_two_merges_with_identical_props(self):
        root = censo_real()
        code, out, _ = self.run_script(root=root)
        self.assertEqual(code, 0)
        sentencias = [s.strip() for s in qa_audit._strip_comments(out).split(';') if s.strip()]
        self.assertEqual(len(sentencias), 2 + 2 * len(RP.DEDUPLICAR))
        # La deduplicación: un borrado y una comprobación por pareja (el texto lleva citas: el mensaje no lo vuelca).
        self.assertEqual(sum('FOREACH (x IN tail(copias) | DELETE x)' in s for s in sentencias), len(RP.DEDUPLICAR))
        self.assertEqual(sum('CALL apoc.util.validate(n <> 1' in s for s in sentencias), len(RP.DEDUPLICAR))
        conn = sqlite3.connect(f'file:{root / "census.sqlite"}?mode=ro', uri=True)
        try:
            for uid, sentencia in zip(RP.UNIDADES, sentencias):
                valor = json.loads(conn.execute('SELECT value FROM units WHERE unit_id=?', (uid,)).fetchone()[0])
                _comparar_props(uid, valor['props'], props_de(sentencia))
                self.assertTrue(f"MERGE (a)-[:{valor['type']} {{" in sentencia,
                                f'{uid[:8]}: el MERGE no lleva el tipo del censo')
        finally:
            conn.close()


def casa_deny(regla, value):
    """El WHERE de `relations_loader.apply_deny` en Python: par sin dirección, tipo, capa, documento y cita."""
    props = value.get('props') or {}
    kind = props.get('source_kind')
    return ({regla['source_id'], regla['target_id']} == {value['start'], value['end']} and regla['type'] == value['type']
            and (kind is None or kind == 'judicial' or kind in (regla.get('kinds') or []))
            and (regla.get('doc') is None or props.get('source_doc') == regla['doc'])
            and (regla.get('quote_contains') is None
                 or regla['quote_contains'].lower() in (props.get('source_quote') or '').lower()))


def casa_fix(regla, value):
    """`_RETYPE_Q`/`_SWAP_Q`/`_SET_Q`: lo mismo, pero con dirección (salvo un `set` sobre una simétrica)."""
    dirigida = (regla['source_id'], regla['target_id']) == (value['start'], value['end'])
    simetrica = value['type'] in relations_loader._SYMMETRIC_TYPES and regla.get('set') is not None
    tipos = {regla['type'], regla.get('corrected_type')}
    return ((dirigida or (simetrica and {regla['source_id'], regla['target_id']} == {value['start'], value['end']}))
            and value['type'] in tipos and casa_deny({**regla, 'type': value['type']}, value))


@unittest.skipUnless(censo_real(), 'sin el censo privado de la 063 (REGISTRO_ROOT)')
class ReglasTest(unittest.TestCase):
    """AC19: tras retirar las reglas culpables ninguna regla vuelve a borrar (ni retipar) las gemelas repuestas, y
    las que se mantienen siguen casando la copia que retiran."""

    def setUp(self):
        # El lector neutro del apply (spec 071 AC66): un overlay que el loader rechaza sale con exit 2 y su fichero y
        # posición, nunca con el valor ni el extremo en el informe de la suite, y los logs del loader no salen.
        deny, fix = qa_esc_apply._reglas_del_overlay(RP.REPO, censo_real())  # las opacas, con su raíz (AC127)
        self.deny, self.fix = [regla for _, regla in deny], [regla for _, regla in fix]
        self.conn = sqlite3.connect(f'file:{censo_real() / "census.sqlite"}?mode=ro', uri=True)
        self.addCleanup(self.conn.close)

    def unidad(self, prefijo):
        filas = self.conn.execute('SELECT value FROM units WHERE unit_id LIKE ?', (prefijo + '%',)).fetchall()
        self.assertEqual(len(filas), 1, prefijo)
        return json.loads(filas[0][0])

    def test_no_rule_matches_the_restored_twins(self):
        for uid in RP.UNIDADES:
            value = self.unidad(uid)
            for nombre, reglas, casa in (('deny', self.deny, casa_deny), ('fix', self.fix, casa_fix)):
                tocan = [r for r in reglas if casa(r, value)]
                self.assertEqual(len(tocan), 0, f'{uid[:8]}: reglas {nombre} que la casan {_marcas(tocan)}')

    def test_the_culprit_rules_are_gone_and_the_kept_ones_still_match(self):
        for prefijo in ('a4122b43', 'e5672bda', '1322429c'):
            self.assertFalse(any(f'[{prefijo}]' in (r.get('reason') or '') for r in self.deny), prefijo)
        # 0190 (la otra copia de la 0189), 0056 (prensa) y 0099 (la copia judicial de la 0103).
        for prefijo in ('2e2ffecb', 'dce711f8', '979e4cdb'):
            value = self.unidad(prefijo)
            self.assertTrue(any(f'[{prefijo}]' in (r.get('reason') or '') and casa_deny(r, value) for r in self.deny),
                            prefijo)


class LecturaNeutraTest(unittest.TestCase):
    """Codex, 10ª pasada (AC66): `ReglasTest` leía el overlay con el loader directamente, y un `kinds` desconocido
    terminaba en un SystemExit con el valor y el extremo de la regla en el informe de unittest. Aquí su `setUp` corre
    contra un overlay rechazado, sin el censo real, y nada de stdout, stderr ni del informe lleva un centinela."""

    def test_a_rejected_overlay_leaves_no_value_in_the_report(self):
        base = Path(tempfile.mkdtemp(prefix='reposicion-overlay-'))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        (base / 'pipeline/data/relations').mkdir(parents=True)
        (base / 'pipeline/data/relations/relations.yaml').write_text(
            'deny:\n  - {source_id: "Person:CENTINELA_EXTREMO", target_id: "Person:otra-ficticia", type: OWNS,\n'
            '     kinds: [CENTINELA_KINDS], reason: "regla de prueba"}\nfix: []\n', encoding='utf-8')

        class Lectura(ReglasTest):
            __unittest_skip__ = False

            def test_lee(self):
                pass

        informe, salida, errores = io.StringIO(), io.StringIO(), io.StringIO()
        with mock.patch.object(RP, 'REPO', base), contextlib.redirect_stdout(salida), \
                contextlib.redirect_stderr(errores):
            resultado = unittest.TextTestRunner(stream=informe, verbosity=2).run(Lectura('test_lee'))
        self.assertEqual(len(resultado.errors), 1)
        todo = salida.getvalue() + errores.getvalue() + informe.getvalue()
        for centinela in ('CENTINELA_KINDS', 'CENTINELA_EXTREMO'):
            self.assertNotIn(centinela, todo)
        self.assertIn('relations.yaml:deny[0]: fila rechazada por el loader', todo)


class FugaTest(ReposicionBase):
    """Codex, 2ª pasada: un fallo de las pruebas contra el censo real volcaba sus citas en el informe de la suite.
    Las de arriba corren aquí sobre un censo sintético cuya cita es un centinela, forzadas a fallar."""

    def setUp(self):
        super().setUp()
        valores = json.loads(json.dumps(VALORES))
        valores[U1]['props']['source_quote'] = CENTINELA
        self.censo(valores)
        self.enterContext(mock.patch.dict(os.environ, {'REGISTRO_ROOT': str(self.base)}))

    def test_a_props_mismatch_names_the_unit_and_the_key_but_no_value(self):
        # La reproducción de Codex: solo cambia `source_page`, y el mensaje traía la cita entera.
        original = props_de

        def alterada(sentencia):
            return {**original(sentencia), 'source_page': -1}

        caso = CensoRealTest('test_the_real_census_gives_two_merges_with_identical_props')
        with mock.patch.dict(globals(), {'props_de': alterada}), self.assertRaises(AssertionError) as fallo:
            caso.test_the_real_census_gives_two_merges_with_identical_props()
        self.assertNotIn(CENTINELA, str(fallo.exception))
        self.assertIn(U1[:8], str(fallo.exception))
        self.assertIn('source_page', str(fallo.exception))

    def test_a_rule_that_matches_a_restored_twin_is_named_by_its_unit_only(self):
        caso = ReglasTest('test_no_rule_matches_the_restored_twins')
        # Sus reglas son las de abajo: el overlay real (con reglas opacas que el censo sintético no resuelve) sobra.
        with mock.patch.object(qa_esc_apply, '_reglas_del_overlay', return_value=([], [])):
            caso.setUp()
        self.addCleanup(caso.doCleanups)
        caso.deny = [{'source_id': A, 'target_id': B, 'type': 'OWNS', 'doc': None, 'quote_contains': None,
                      'kinds': None, 'reason': f'063 ficticia [{U1[:8]}]: {CENTINELA}'}]
        caso.fix = []
        with self.assertRaises(AssertionError) as fallo:
            caso.test_no_rule_matches_the_restored_twins()
        self.assertNotIn(CENTINELA, str(fallo.exception))
        self.assertIn(f'{U1[:8]}: reglas deny', str(fallo.exception))


class SimetricasTest(unittest.TestCase):
    def test_the_registry_check_uses_the_symmetric_types_of_the_loader(self):
        # Dos derivaciones de `ontologia().simetricas` (el apply juzga el sentido con las del registro, spec 071 AC85):
        # guarda contra una copia a mano en cualquiera de ellas.
        self.assertEqual(set(RC.SIMETRICOS), set(relations_loader._SYMMETRIC_TYPES))


if __name__ == '__main__':
    unittest.main()
