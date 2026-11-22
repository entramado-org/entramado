"""Registro público de rectificaciones: forma (`--tree`, CI) y veracidad contra un export (`--snapshot`).

Censo (`units` + `esc_packs`), decisiones, export y registro sintéticos y mínimos: la misma forma que los
reales, con entidades inventadas. Sin grafo ni red.
"""
from collections import Counter
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import types
import unicodedata
import unittest
from unittest import mock

import yaml

from pipeline.scripts import _reposicion_071 as RP
from pipeline.scripts import registro_check as RC
from src.alcance import MOTIVO_AJENA  # noqa: E402 (registro_check pone pipeline/ en sys.path)
try:
    from . import rangos_yaml as RY
except ImportError:  # `unittest discover -s pipeline/tests`: los tests no son un paquete
    import rangos_yaml as RY

FULANA, MENGANO, ZUTANO = 'Person:fulana-de-tal', 'Person:mengano-ficticio', 'Person:zutano-inventado'
EMPRESA, DELITO = 'Company:empresa-ficticia', 'Crime:delito-ficticio'
CITA = 'Fulana de Tal pagó a Mengano Ficticio la comisión acordada'
FALLO = 'condena a Fulana de Tal como autora de un delito ficticio'
SOCIEDAD_A, SOCIEDAD_B = 'Company:sociedad-a-ficticia', 'Company:sociedad-b-ficticia'
PARTICIPA = 'la sociedad A participa al 100% en la sociedad B'
ADMINISTRADOR, CONTROL = 'A y B comparten administrador', 'la sociedad A controla la sociedad B'
(U_VIVA, U_RETIRADA, U_GEMELA, U_CONSERVADA, U_RESUMEN, U_JUDICIAL, U_CURADA, U_NO_COPIA, U_SIN_RESOLVER,
 U_ZUTANO, U_DEBIL, U_PRIMERA, U_SIM_RETIRADA, U_SIM_CONSERVADA, U_INV_RETIRADA) = (c * 64 for c in 'abcde1234567890')
U_INV_CONSERVADA = '89' * 32
# Dos afirmaciones de Fulana hacia un Claim (`units` no guarda su claim_id: `end` es None, y lo recupera el objeto de
# su relación, AC104), y tres gemelas de Mengano de las que dos comparten el prefijo que nombra la decisión.
U_CLAIM_RETIRADA, U_CLAIM_CONSERVADA, U_AMB_RETIRADA = '12' * 32, '34' * 32, 'ab' * 32
U_AMB_B, U_AMB_C = 'deadbeef' + '1' * 56, 'deadbeef' + '2' * 56
# Otra arista de Fulana a Mengano cuyo trozo de cita está en la de U_VIVA (su regla `fix` la alcanza), otra igual desde
# otro documento (no la alcanza), y una retirada y su gemela de Zutano que comparten los 8 primeros hex.
U_TRANSFORMA, U_OTRO_DOC = '56' * 32, '78' * 32
U_COLISION_RETIRADA, U_COLISION_GEMELA = 'c0ffee00' + '1' * 56, 'c0ffee00' + '2' * 56
# 6ª pasada: dos OWNS judiciales de la sociedad A a la C, mismo documento y cita, otra página (cada una en el pack de una
# sociedad); y dos aristas de la A a la B cuyas citas solo comparten los primeros 60 caracteres.
SOCIEDAD_C, PARTICIPA_C = 'Company:sociedad-c-ficticia', 'la sociedad A controla el 60% de la sociedad C'
U_RT_RETIRADA, U_RT_RETIPADA, U_PREFIJO_A, U_PREFIJO_B = '9a' * 32, '9b' * 32, '9c' * 32, '9d' * 32
SENTINEL = 'CITA_RETIRADA_SINTETICA'
CLAIM = 'claim-ficticio'
# Los mensajes de `registro_check` que miran estos tests, por su literal: contra un commit anterior de la spec 071, un
# test falla por lo que hace, no por un nombre que entonces no existía.
FORMA_OPACA = 'regla opaca con forma inválida: solo {unidad, declaraciones?, kinds?, reason}'
HUELLA = 'huella de una fila anonimizada fuera de su regla opaca'


def arista(start, end, rtype='PAID', kind='judicial', doc='auto-ficticio.pdf', quote=CITA):
    props = {'source_kind': kind, 'source_quote': quote}
    if doc:
        props['source_doc'] = doc
    return {'start': start, 'end': end, 'type': rtype, 'props': props}


def viva(value):
    """La arista de una unidad tal como la deja ver el export."""
    return value['start'], value['end'], value['type'], dict(value['props'])


EDGES = {U_VIVA: arista(FULANA, MENGANO),
         U_RETIRADA: arista(FULANA, EMPRESA, rtype='OWNS', quote='Fulana de Tal es dueña de la empresa'),
         U_GEMELA: arista(FULANA, EMPRESA, rtype='WORKS_FOR', quote='Fulana trabajó en la empresa'),
         U_CONSERVADA: arista(FULANA, EMPRESA, rtype='WORKS_FOR', quote='Fulana trabajó en la empresa'),
         U_JUDICIAL: arista(FULANA, DELITO, rtype='CONVICTED_OF', doc='sentencia-ficticia.pdf', quote=FALLO),
         U_CURADA: arista(FULANA, DELITO, rtype='CONVICTED_OF', kind='acusacion', doc='sentencia-ficticia.pdf',
                          quote=FALLO),
         U_NO_COPIA: arista(MENGANO, FULANA, rtype='ASSOCIATED_WITH', kind='prensa', doc=None,
                            quote='Mengano y Fulana coincidieron en el consejo'),
         U_SIN_RESOLVER: arista(FULANA, MENGANO, rtype='MET_WITH', kind='prensa', doc=None,
                                quote='Se reunieron en una cafetería inventada'),
         U_ZUTANO: arista(ZUTANO, MENGANO, rtype='MET_WITH', kind='prensa', doc=None,
                          quote='Se reunieron en una cafetería inventada'),
         # R-2026-10-03-0054: la retirada cita «100%» y la conservada, otra página del mismo informe, lo contiene.
         U_DEBIL: arista(SOCIEDAD_A, SOCIEDAD_B, rtype='OWNS', doc='informe-ficticio.pdf', quote='100%'),
         U_PRIMERA: arista(SOCIEDAD_A, SOCIEDAD_B, rtype='OWNS', doc='informe-ficticio.pdf', quote=PARTICIPA),
         # Gemelas simétricas que el censo guarda en sentidos opuestos, y dos OWNS idénticas salvo la dirección.
         U_SIM_RETIRADA: arista(SOCIEDAD_A, SOCIEDAD_B, rtype='ASSOCIATED_WITH', doc='informe-ficticio.pdf',
                                quote=ADMINISTRADOR),
         U_SIM_CONSERVADA: arista(SOCIEDAD_B, SOCIEDAD_A, rtype='ASSOCIATED_WITH', doc='informe-ficticio.pdf',
                                  quote=ADMINISTRADOR),
         U_INV_RETIRADA: arista(SOCIEDAD_A, SOCIEDAD_B, rtype='OWNS', doc='informe-ficticio.pdf', quote=CONTROL),
         U_INV_CONSERVADA: arista(SOCIEDAD_B, SOCIEDAD_A, rtype='OWNS', doc='informe-ficticio.pdf', quote=CONTROL),
         U_CLAIM_RETIRADA: arista(FULANA, None, rtype='ASSERTS', quote='Fulana sostuvo que el pago era un préstamo'),
         U_CLAIM_CONSERVADA: arista(FULANA, None, rtype='ASSERTS', quote='Fulana declaró que el pago fue un préstamo'),
         **{uid: arista(MENGANO, EMPRESA, rtype='WORKS_FOR', quote='Mengano trabajó en la empresa')
            for uid in (U_AMB_RETIRADA, U_AMB_B, U_AMB_C)},
         U_TRANSFORMA: arista(FULANA, MENGANO, quote='Fulana de Tal pagó a Mengano'),
         U_OTRO_DOC: arista(FULANA, MENGANO, doc='otro-auto.pdf', quote='Fulana de Tal pagó a Mengano'),
         **{uid: arista(ZUTANO, EMPRESA, rtype='WORKS_FOR', quote='Zutano trabajó en la empresa')
            for uid in (U_COLISION_RETIRADA, U_COLISION_GEMELA)},
         U_RT_RETIRADA: arista(SOCIEDAD_A, SOCIEDAD_C, rtype='OWNS', doc='informe-ficticio.pdf', quote=PARTICIPA_C),
         U_RT_RETIPADA: arista(SOCIEDAD_A, SOCIEDAD_C, rtype='OWNS', doc='informe-ficticio.pdf', quote=PARTICIPA_C),
         U_PREFIJO_A: arista(SOCIEDAD_A, SOCIEDAD_B, rtype='CONTROLS', doc='informe-ficticio.pdf', quote='x' * 60 + ' A'),
         U_PREFIJO_B: arista(SOCIEDAD_A, SOCIEDAD_B, rtype='CONTROLS', doc='informe-ficticio.pdf', quote='x' * 60 + ' B')}
PACKS = {'E-fulana': (FULANA, [U_VIVA, U_RETIRADA, U_GEMELA, U_CONSERVADA, U_RESUMEN, U_JUDICIAL, U_CURADA,
                               U_SIN_RESOLVER, U_CLAIM_RETIRADA, U_CLAIM_CONSERVADA, U_TRANSFORMA, U_OTRO_DOC]),
         'E-mengano': (MENGANO, [U_NO_COPIA, U_AMB_RETIRADA, U_AMB_B, U_AMB_C]),
         'E-zutano': (ZUTANO, [U_ZUTANO, U_COLISION_RETIRADA, U_COLISION_GEMELA]),
         'E-sociedad': (SOCIEDAD_A, [U_DEBIL, U_PRIMERA, U_SIM_RETIRADA, U_SIM_CONSERVADA, U_INV_RETIRADA,
                                     U_INV_CONSERVADA, U_RT_RETIRADA, U_PREFIJO_A]),
         'E-sociedad-c': (SOCIEDAD_C, [U_RT_RETIPADA, U_PREFIJO_B])}


def fila(rid, unidad, decision='suprimido'):
    return {'id': rid, 'fecha': '2026-09-24', 'origen': 'auditoria-063', 'entidad': FULANA, 'unidad': unidad,
            'dato': f'({FULANA}) -[PAID]-> (Person:[tercero]): arista retirada', 'fuente': 'auto-ficticio.pdf p.3',
            'decision': decision, 'fecha_resolucion': '2026-09-24', 'seed_version': '20260924-qa063a'}


def nombrada(rid, unidad, decision='suprimido'):
    """La fila de una unidad cuyo par publica el export: nombra sus dos extremos, sin marcador (D1; con el overlay, que
    `--snapshot` exige desde AC179, el anonimato de una marcada se comprueba con el censo)."""
    arista = EDGES[unidad]
    fin = arista['end'] or f'Claim:{CLAIM}'
    return dict(fila(rid, unidad, decision), dato=f'({arista["start"]}) -[{arista["type"]}]-> ({fin}): arista '
                f'{"retirada" if decision == "suprimido" else "corregida"}')


def con_pipeline():
    """Si el entorno es el del pipeline, con el loader que prepara las reglas de un overlay con reglas: el del CI (el de
    la API) solo corre --tree."""
    try:
        from pipeline.src import qa_esc_apply  # noqa: F401
    except ImportError:
        return False
    return True


class RegistroCheckBase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='registro-check-'))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root, self.snap = self.base / 'audit', self.base / 'snap'
        (self.root / 'esc/decisions').mkdir(parents=True)
        self.snap.mkdir()
        self.registro = self.base / 'registro.yaml'
        # El --snapshot exige el overlay (spec 071 AC179): el de un test que no pone reglas, sin ningún fichero.
        self.sin_reglas = self.base / 'sin-reglas'
        self.sin_reglas.mkdir()
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('CREATE TABLE units (unit_id TEXT PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL, '
                   'object_id TEXT)')
        db.execute('CREATE TABLE objects (object_id TEXT PRIMARY KEY, object_kind TEXT, ref TEXT, payload TEXT)')
        db.execute('CREATE TABLE esc_packs (pack_id TEXT PRIMARY KEY, entity TEXT NOT NULL, unit_ids TEXT NOT NULL)')
        db.executemany('INSERT INTO units(unit_id, kind, value) VALUES (?,?,?)',
                       [(uid, 'edge', json.dumps(value, ensure_ascii=False)) for uid, value in EDGES.items()]
                       + [(U_RESUMEN, 'summary', json.dumps('Fulana de Tal pagó la comisión.'))])
        db.executemany('INSERT INTO esc_packs VALUES (?,?,?)',
                       [(pack, entity, json.dumps(uids)) for pack, (entity, uids) in PACKS.items()])
        db.commit()
        db.close()
        for uid in (U_CLAIM_RETIRADA, U_CLAIM_CONSERVADA):
            self.afirmacion(uid, CLAIM)
        self.decision('D-1', U_VIVA, 'la cita no sostiene el pago')
        self.decision('D-2', U_RETIRADA, 'la cita habla de otra sociedad')
        self.decision('D-3', U_GEMELA, f'Idéntica a {U_CONSERVADA[:8]}…; se conserva aquella.')
        self.decision('D-4', U_CURADA, f'Duplicado con metadatos peores; se conserva la arista judicial {U_JUDICIAL[:8]}.')
        self.decision('D-5', U_NO_COPIA, 'No es una copia: la cita habla de otra persona.', entity=MENGANO)
        self.decision('D-6', U_SIN_RESOLVER, 'Duplicado de 0123abcd; se conserva aquella.')
        self.decision('D-7', U_DEBIL, f'Duplica la arista {U_PRIMERA[:8]}… y su cita es demasiado débil.',
                      entity=SOCIEDAD_A)
        # Vivas: la de U_VIVA (en el otro sentido, con espacios y mayúsculas de más), otra suya de otra capa y
        # otra con la cita recortada, que no cuentan; una de las gemelas; la judicial y la curada; las de
        # U_NO_COPIA y U_SIN_RESOLVER; la conservada de U_DEBIL.
        self.export([(MENGANO, FULANA, 'PAID', {'source_kind': 'judicial', 'source_doc': 'auto-ficticio.pdf',
                                                'source_quote': '  ' + CITA.upper().replace(' ', '  ')}),
                     (MENGANO, FULANA, 'PAID', {'source_kind': 'acusacion', 'source_doc': 'auto-ficticio.pdf',
                                                'source_quote': CITA}),
                     (MENGANO, FULANA, 'PAID', {'source_kind': 'judicial', 'source_doc': 'auto-ficticio.pdf',
                                                'source_quote': CITA[:30]}),
                     viva(EDGES[U_CONSERVADA]), viva(EDGES[U_JUDICIAL]), viva(EDGES[U_CURADA]),
                     viva(EDGES[U_NO_COPIA]), viva(EDGES[U_SIN_RESOLVER]), viva(EDGES[U_PRIMERA])])

    def decision(self, name, uid, reason, entity=FULANA):
        d = {'entity_id': entity,
             'units': [{'unit_id': uid, 'verdict': 'corregido', 'label': 'arista', 'evidence': 'auto p.3'}],
             'edges': [{'unit_id': uid, 'action': 'deny', 'evidence': 'auto p.3', 'reason': reason}],
             '_meta': {'pack_id': f'E-{entity.split(":")[1].split("-")[0]}', 'entity': entity,
                       'resolution': 'arbitraje-2026-10-03', 'decided_at': '2026-10-03T10:00:00+00:00'}}
        (self.root / 'esc/decisions' / f'{name}.json').write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')

    def afirmacion(self, uid, claim_id):
        """La postura `uid` del censo apunta, como en el real, al nodo Claim de su afirmación por el objeto de su
        relación (`units` no guarda el claim_id: spec 071 AC104)."""
        db = sqlite3.connect(self.root / 'census.sqlite')
        with db:
            db.execute('INSERT OR IGNORE INTO objects VALUES (?,?,?,?)',
                       (f'n-{claim_id}', 'node', f'n:{claim_id}',
                        json.dumps({'labels': ['Claim'], 'props': {'claim_id': claim_id, 'text': 't'}})))
            relacion = json.dumps({'start': 'n:x', 'end': f'n:{claim_id}'})
            db.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?,?)',
                       (f'r-{uid[:8]}', 'relationship', f'r:{uid[:8]}', relacion))
            db.execute('UPDATE units SET object_id=? WHERE unit_id=?', (f'r-{uid[:8]}', uid))
        db.close()

    def otra_decision(self, action, unidad=U_PRIMERA, entidad=SOCIEDAD_A, **campos):
        """Otra decisión sobre una conservada: por defecto U_PRIMERA, la que conserva la decisión de la 0054."""
        d = {'entity_id': entidad, '_meta': {'entity': entidad, 'resolution': 'arbitraje-2026-10-05',
                                             'decided_at': '2026-10-05T10:00:00+00:00'},
             'units': [{'unit_id': unidad, 'verdict': 'corregido', 'label': 'arista', 'evidence': 'p.3'}],
             'edges': [{'unit_id': unidad, 'action': action, 'evidence': 'p.3', 'reason': 'otra decisión',
                        **campos}]}
        (self.root / 'esc/decisions/D-8.json').write_text(json.dumps(d), encoding='utf-8')

    def export(self, edges, nodos=()):
        """Un extremo None es el Claim de las afirmaciones del censo (CLAIM) y uno `Claim:<id>`, otro: el export los
        lleva sin entity_id, con su claim_id."""
        ids = sorted({x for start, end, _, _ in edges for x in (start, end) if x} | set(nodos))
        refs = {eid: f'4:x:{n}' for n, eid in enumerate(ids)}
        refs[None] = '4:x:claim'
        with (self.snap / 'nodes.jsonl').open('w', encoding='utf-8') as f:
            for eid, ref in refs.items():
                labels, props = (['Claim'], {'claim_id': CLAIM}) if eid is None else \
                    (['Claim'], {'claim_id': eid.split(':', 1)[1]}) if eid.startswith('Claim:') else \
                    ([eid.split(':')[0]], {'entity_id': eid})
                f.write(json.dumps({'labels': labels, 'props': props, 'ref': ref}) + '\n')
            f.write(json.dumps({'labels': ['Chunk'], 'props': {'chunk_id': 'c1', 'embedding': [0.1]},
                                'ref': '4:x:99'}) + '\n')
        with (self.snap / 'relationships.jsonl').open('w', encoding='utf-8') as f:
            for n, (start, end, rtype, props) in enumerate(edges):
                f.write(json.dumps({'start': refs[start], 'end': refs[end], 'type': rtype, 'props': props,
                                    'ref': f'5:x:{n}'}, ensure_ascii=False) + '\n')

    def con_overlay(self, args):
        """Los argumentos del CLI, con el overlay sin reglas si piden un --snapshot sin el suyo (AC179)."""
        args = list(args)
        return args + ['--overlay', str(self.sin_reglas)] if '--snapshot' in args and '--overlay' not in args \
            else args

    def run_check(self, rows, *args):
        self.registro.write_text(yaml.safe_dump({'rectificaciones': rows}, allow_unicode=True, sort_keys=False),
                                 encoding='utf-8')
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = RC.main(['--registro', str(self.registro), *self.con_overlay(args)])
        return code, out.getvalue() + err.getvalue()

    def snapshot(self, rows):
        return self.run_check(rows, '--snapshot', str(self.snap), '--root', str(self.root))

    def no_vale(self, rows, *args):
        """El --snapshot ante una decisión que no vale: exit 2, nada por stdout y lo que dice por stderr."""
        self.registro.write_text(yaml.safe_dump({'rectificaciones': rows}, allow_unicode=True, sort_keys=False),
                                 encoding='utf-8')
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as salida:
            RC.main(['--registro', str(self.registro),
                     *self.con_overlay(['--snapshot', str(self.snap), '--root', str(self.root), *args])])
        self.assertEqual((salida.exception.code, out.getvalue()), (2, ''))
        return err.getvalue().strip()

    def cli(self, *args):
        """El CLI de verdad, en otro proceso: lo que imprima un traceback llega a su salida."""
        proc = subprocess.run([sys.executable, RC.__file__, '--registro', str(self.registro), *self.con_overlay(args)],
                              capture_output=True, encoding='utf-8', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                              timeout=120)
        return proc.returncode, proc.stdout + proc.stderr


class SnapshotTest(RegistroCheckBase):
    def test_a_suppressed_edge_still_alive_is_reported_without_its_endpoints(self):
        code, out = self.snapshot([fila('R-2026-09-24-0001', U_VIVA)])
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-09-24-0001: arista «suprimida» viva (1 copia; tolerancia 0)', out)
        for secreto in (FULANA, MENGANO, CITA[:20]):
            self.assertNotIn(secreto, out)

    def test_a_withdrawn_edge_passes(self):
        self.assertEqual(self.snapshot([nombrada('R-2026-09-24-0002', U_RETIRADA)])[0], 0)

    def test_a_curated_copy_kept_alive_beside_the_judicial_one_is_not_a_tolerated_duplicate(self):
        # Codex, 1ª pasada (a): la decisión retiró la copia curada y conservó la judicial; la curada sigue viva
        # (su capa la recrea). Contada con la capa, el documento y la cita de la curada solo se ve ella, y la
        # palabra «duplicado» del motivo la toleraba.
        code, out = self.snapshot([fila('R-2026-09-24-0003', U_CURADA)])
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-09-24-0003: arista «suprimida» viva (1 copia; tolerancia 0)', out)
        self.assertNotIn(FALLO[:20], out)

    def test_an_identical_twin_named_by_the_decision_may_stay_alive_once(self):
        # (b) Gemelas byte a byte: en el export no se distingue cuál sobrevive; la decisión nombra la conservada.
        code, out = self.snapshot([nombrada('R-2026-09-24-0004', U_GEMELA)])
        self.assertEqual(code, 0, out)

    def test_a_twin_another_decision_retyped_is_not_the_surviving_copy(self):
        # Codex, 3ª pasada: la gemela que la decisión conserva se retipó, así que la copia viva con el tipo original
        # solo puede ser la retirada. La tolerancia salía de los props del censo de la gemela, y su retype la eximía
        # de seguir publicada: el check no veía nada. Con la misma cita, la regla de ese retype alcanza también a la
        # retirada (6ª pasada): las dos copias pueden ser ella y una, con el tipo nuevo, puede ser la gemela.
        self.otra_decision('retype', unidad=U_CONSERVADA, entidad=FULANA, new_type='MEMBER_OF')
        retipada = (FULANA, EMPRESA, 'MEMBER_OF', dict(EDGES[U_CONSERVADA]['props']))
        for edges, mensaje in (([viva(EDGES[U_GEMELA]), retipada], '2 copias; tolerancia 1'),
                               ([viva(EDGES[U_GEMELA])], '1 copia; tolerancia 0')):
            self.export(edges)
            code, out = self.snapshot([fila('R-2026-09-24-0004', U_GEMELA)])
            self.assertEqual(code, 1)
            self.assertIn(f'REGISTRO R-2026-09-24-0004: arista «suprimida» viva ({mensaje})', out)
            self.assertNotIn('gemela conservada ausente', out)

    def test_a_twin_another_decision_inverted_or_withdrew_gives_no_tolerance(self):
        for action in ('invert', 'deny'):
            with self.subTest(action=action):
                self.otra_decision(action, unidad=U_CONSERVADA, entidad=FULANA)
                self.export([viva(EDGES[U_GEMELA])])
                code, out = self.snapshot([fila('R-2026-09-24-0004', U_GEMELA)])
                self.assertEqual(code, 1)
                self.assertIn('REGISTRO R-2026-09-24-0004: arista «suprimida» viva (1 copia; tolerancia 0)', out)

    def test_a_twin_whose_props_another_decision_changed_still_gives_tolerance(self):
        # `keep` y `set_prop` no la quitan de donde estaba: la copia viva puede ser ella (2ª pasada).
        for action, campos in (('keep', {}), ('set_prop', {'prop': 'note', 'value': 'nota ficticia'})):
            with self.subTest(action=action):
                self.otra_decision(action, unidad=U_CONSERVADA, entidad=FULANA, **campos)
                code, out = self.snapshot([nombrada('R-2026-09-24-0004', U_GEMELA)])
                self.assertEqual(code, 0, out)

    def test_a_twin_whose_layer_document_or_quote_another_decision_changed_gives_no_tolerance(self):
        # Codex, 4ª pasada: con otra capa, la gemela es distinguible de la retirada; su firma del censo seguía dando
        # tolerancia a una copia viva de la retirada. Con la misma cita, la regla de ese `set_prop` alcanza también a la
        # retirada (5ª pasada): las dos vivas pueden ser ella y sobra una; sola, la copia con la firma del censo no la
        # excusa la gemela, que ya no la lleva. El documento y la cita no los cambia ningún set_prop: el loader descarta
        # el bloque, y la decisión que lo pide no vale (spec 071 AC77 y AC78): exit 2, sin el valor.
        self.otra_decision('set_prop', unidad=U_CONSERVADA, entidad=FULANA, prop='source_kind', value='prensa')
        start, end, rtype, props = viva(EDGES[U_CONSERVADA])
        for edges, mensaje in (([viva(EDGES[U_GEMELA]), (start, end, rtype, {**props, 'source_kind': 'prensa'})],
                                '2 copias; tolerancia 1'),
                               ([viva(EDGES[U_GEMELA])], '1 copia; tolerancia 0')):
            self.export(edges)
            code, out = self.snapshot([fila('R-2026-09-24-0004', U_GEMELA)])
            self.assertEqual(code, 1)
            self.assertIn(f'REGISTRO R-2026-09-24-0004: arista «suprimida» viva ({mensaje})', out)
        for prop, valor in (('source_doc', 'otro-auto.pdf'),
                            ('source_quote', 'Fulana figuró en la plantilla de la empresa')):
            with self.subTest(prop=prop):
                self.otra_decision('set_prop', unidad=U_CONSERVADA, entidad=FULANA, prop=prop, value=valor)
                err = self.no_vale([fila('R-2026-09-24-0004', U_GEMELA)])
                self.assertEqual(err, f'operación con carga inválida (D-8.json, unidad {U_CONSERVADA[:8]}: {prop}: '
                                      'fuera de la lista cerrada del loader)')

    def test_a_kept_edge_to_a_claim_is_required_by_its_quote(self):
        # Codex, 4ª pasada: el Claim no tiene entity_id y su extremo None se tomaba por un nodo podado. La persona y el
        # Claim siguen en el export (unidos por una DISPUTES), pero ninguna ASSERTS: la conservada no está. Desde AC104
        # la afirmación se identifica por su claim_id, recuperado del censo: con otra cita sobre la misma afirmación es
        # la conservada (`claims_loader` fusiona por titular, afirmación, postura y capa), y sobre otra, no.
        self.decision('D-11', U_CLAIM_RETIRADA, f'Duplica la afirmación {U_CLAIM_CONSERVADA[:8]}; se conserva aquella.')
        disputa = (FULANA, None, 'DISPUTES', dict(EDGES[U_CLAIM_CONSERVADA]['props'], source_quote='Fulana lo niega'))
        conservada = viva(EDGES[U_CLAIM_CONSERVADA])
        otra_cita = conservada[:3] + ({**conservada[3], 'source_quote': 'Fulana afirmó otra cosa'},)
        otra = (FULANA, 'Claim:otra-afirmacion-ficticia', *conservada[2:])
        for nombre, edges, codigo in (('solo la DISPUTES', [disputa], 1), ('otra afirmación', [otra], 1),
                                      ('la misma afirmación con otra cita', [otra_cita], 0),
                                      ('la conservada viva', [conservada], 0)):
            with self.subTest(export=nombre):
                self.export(edges)
                code, out = self.snapshot([nombrada('R-2026-10-03-0093', U_CLAIM_RETIRADA)])
                self.assertEqual(code, codigo, out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-10-03-0093: gemela conservada ausente', out)

    def test_the_kept_edge_is_looked_up_as_the_apply_leaves_it(self):
        # Remate de la 4ª pasada: la conservada se busca con el documento y la cita que deja el apply. Ningún `set_prop`
        # los cambia (el loader descarta un `set` sobre la evidencia): la decisión que pide cambiar el documento de la
        # conservada, o la cita si su otro extremo es un Claim, no vale (spec 071 AC77 y AC78), ni con la copia
        # publicada con el valor pedido ni con la del censo: exit 2, sin el valor.
        self.decision('D-11', U_CLAIM_RETIRADA, f'Duplica la afirmación {U_CLAIM_CONSERVADA[:8]}; se conserva aquella.')
        casos = (('documento', U_PRIMERA, SOCIEDAD_A, 'source_doc', 'informe-corregido.pdf',
                  fila('R-2026-10-03-0054', U_DEBIL)),
                 ('cita hacia un Claim', U_CLAIM_CONSERVADA, FULANA, 'source_quote',
                  'Fulana declaró ante el juez que el pago fue un préstamo', fila('R-2026-10-03-0093', U_CLAIM_RETIRADA)))
        for caso, unidad, entidad, prop, valor, row in casos:
            with self.subTest(caso=caso):
                self.otra_decision('set_prop', unidad=unidad, entidad=entidad, prop=prop, value=valor)
                start, end, rtype, props = viva(EDGES[unidad])
                for edges in ([(start, end, rtype, {**props, prop: valor})], [(start, end, rtype, props)]):
                    self.export(edges)
                    err = self.no_vale([row])
                    self.assertEqual(err, f'operación con carga inválida (D-8.json, unidad {unidad[:8]}: {prop}: '
                                          'fuera de la lista cerrada del loader)')

    def test_an_ambiguous_prefix_names_no_kept_edge(self):
        # Codex, 4ª pasada: «deadbeef» señala dos aristas del pack; una está retirada y la otra es gemela viva. La
        # referencia no prueba cuál se conservó: no hay tolerancia, y la fila lo dice sin ids ni prefijos.
        self.decision('D-12', U_AMB_RETIRADA, 'Duplicado; se conserva la arista deadbeef.', entity=MENGANO)
        self.otra_decision('deny', unidad=U_AMB_B, entidad=MENGANO)
        self.export([viva(EDGES[U_AMB_C])])
        code, out = self.snapshot([fila('R-2026-10-03-0095', U_AMB_RETIRADA)])
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-10-03-0095: referencia ambigua a la conservada', out)
        self.assertIn('REGISTRO R-2026-10-03-0095: arista «suprimida» viva (1 copia; tolerancia 0)', out)
        self.assertNotIn('deadbeef', out)
        # Un identificador más largo señala una sola: vuelve la tolerancia.
        self.decision('D-12', U_AMB_RETIRADA, f'Duplicado; se conserva la arista {U_AMB_C[:12]}.', entity=MENGANO)
        code, out = self.snapshot([fila('R-2026-10-03-0095', U_AMB_RETIRADA)])
        self.assertEqual(code, 0, out)

    def test_a_layer_change_that_also_reaches_the_withdrawn_twin_keeps_it_counted(self):
        # Codex, 5ª pasada: el `set_prop` de la gemela (a prensa) es una regla `fix` por par, documento y cita que casa
        # también la retirada, y el loader la aplica antes que el deny, que ya no la encuentra: las dos siguen vivas en
        # prensa (con su página cada una). Contadas solo con la firma judicial del censo, no se veía ninguna.
        self.otra_decision('set_prop', unidad=U_CONSERVADA, entidad=FULANA, prop='source_kind', value='prensa')

        def en_prensa(uid, pagina):
            start, end, rtype, props = viva(EDGES[uid])
            return start, end, rtype, {**props, 'source_kind': 'prensa', 'source_page': pagina}

        self.export([en_prensa(U_GEMELA, 3), en_prensa(U_CONSERVADA, 4)])
        code, out = self.snapshot([nombrada('R-2026-09-24-0004', U_GEMELA)])
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-09-24-0004: arista «suprimida» viva (2 copias; tolerancia 1)', out)
        self.export([en_prensa(U_CONSERVADA, 4)])
        code, out = self.snapshot([nombrada('R-2026-09-24-0004', U_GEMELA)])
        self.assertEqual(code, 0, out)

    def test_a_layer_change_of_another_edge_reaches_the_withdrawn_one_only_from_its_document(self):
        # La regla del `set_prop` de otra arista del mismo par y documento, cuya cita contiene la retirada, también la
        # pasa a prensa antes del deny; sin gemela nombrada, esa copia no tiene excusa. Desde otro documento, no. Las
        # dos quedan en prensa, una capa que su cargador recrea por par, tipo y capa (AC110): en el export no se
        # distinguen, y la otra arista, que sigue con derecho, excusa una de las dos.
        start, end, rtype, props = viva(EDGES[U_VIVA])
        for unidad, codigo in ((U_TRANSFORMA, 1), (U_OTRO_DOC, 0)):
            with self.subTest(unidad=unidad[:8]):
                otra = viva(EDGES[unidad])
                self.export([(start, end, rtype, {**props, 'source_kind': 'prensa'}),
                             (*otra[:3], {**otra[3], 'source_kind': 'prensa'})])
                self.otra_decision('set_prop', unidad=unidad, entidad=FULANA, prop='source_kind', value='prensa')
                code, out = self.snapshot([nombrada('R-2026-09-24-0001', U_VIVA)])
                self.assertEqual(code, codigo, out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-09-24-0001: arista «suprimida» viva (2 copias; tolerancia 1)', out)

    def test_a_retype_of_another_edge_that_reaches_the_withdrawn_one_keeps_it_counted(self):
        # Codex, 6ª pasada: la retirada (deny, pack de la sociedad A) y otra OWNS idéntica salvo la página, retipada a
        # SUBSIDIARY_OF desde el pack de la C. Su regla `fix` casa las dos y el loader la aplica antes que el deny OWNS,
        # que ya no encuentra nada: las dos siguen vivas como SUBSIDIARY_OF. Solo se buscaba el tipo original.
        self.decision('D-14', U_RT_RETIRADA, 'la cita no sostiene la propiedad', entity=SOCIEDAD_A)
        self.otra_decision('retype', unidad=U_RT_RETIPADA, entidad=SOCIEDAD_C, new_type='SUBSIDIARY_OF')

        def filial(uid, pagina):
            start, end, _, props = viva(EDGES[uid])
            return start, end, 'SUBSIDIARY_OF', {**props, 'source_page': pagina}

        self.export([filial(U_RT_RETIRADA, 3), filial(U_RT_RETIPADA, 4)])
        code, out = self.snapshot([fila('R-2026-10-03-0097', U_RT_RETIRADA)])
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-10-03-0097: arista «suprimida» viva (2 copias; tolerancia 1)', out)
        self.export([filial(U_RT_RETIPADA, 4)])
        code, out = self.snapshot([fila('R-2026-10-03-0097', U_RT_RETIRADA)])
        self.assertEqual(code, 0, out)

    def test_a_rule_reaches_by_the_prefix_and_with_the_value_the_apply_writes(self):
        # Codex, 6ª pasada: el apply escribe `quote_contains` con los 60 primeros caracteres de la cita y el valor del
        # `set_prop` sin espacios; el check comparaba citas de 300 y el valor tal cual, y no veía la retirada en prensa.
        # Las dos acaban en prensa, que su cargador recrea por par, tipo y capa (AC110): la otra excusa una de las dos.
        casos = (('citas que comparten 60 caracteres', U_PREFIJO_A, U_PREFIJO_B, SOCIEDAD_C, 'prensa',
                  '2 copias; tolerancia 1'),
                 ('valor con espacios', U_GEMELA, U_CONSERVADA, FULANA, ' prensa ', '2 copias; tolerancia 1'))
        for caso, retirada, otra, entidad, valor, mensaje in casos:
            with self.subTest(caso=caso):
                self.decision('D-15', retirada, 'la cita no sostiene la relación',
                              entity=SOCIEDAD_A if retirada == U_PREFIJO_A else FULANA)
                self.otra_decision('set_prop', unidad=otra, entidad=entidad, prop='source_kind', value=valor)
                prensa = [viva(EDGES[uid])[:3] + ({**EDGES[uid]['props'], 'source_kind': 'prensa'},)
                          for uid in (retirada, otra)]
                self.export(prensa)
                code, out = self.snapshot([fila('R-2026-10-03-0098', retirada)])
                self.assertEqual(code, 1)
                self.assertIn(f'REGISTRO R-2026-10-03-0098: arista «suprimida» viva ({mensaje})', out)

    def test_a_prefix_that_also_names_the_withdrawn_edge_is_ambiguous(self):
        # Codex, 5ª pasada: la retirada y su gemela comparten «c0ffee00»; quitando antes la retirada, el prefijo parecía
        # señalar solo la gemela y le daba tolerancia.
        self.decision('D-13', U_COLISION_RETIRADA, 'Duplicado; se conserva la arista c0ffee00.', entity=ZUTANO)
        self.export([viva(EDGES[U_COLISION_GEMELA])])
        code, out = self.snapshot([fila('R-2026-10-03-0096', U_COLISION_RETIRADA)])
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-10-03-0096: referencia ambigua a la conservada', out)
        self.assertIn('REGISTRO R-2026-10-03-0096: arista «suprimida» viva (1 copia; tolerancia 0)', out)
        self.assertNotIn('c0ffee00', out)

    def test_an_export_that_cannot_be_read_as_the_graph_stops_with_exit_2(self):
        # Codex, 5ª pasada: con los dos ficheros vacíos, ninguna retirada aparecía viva y las conservadas se daban por
        # podadas: «0 problemas», exit 0. Vacío, ilegible o con aristas hacia nodos que no están: el export no vale.
        nodos = (self.snap / 'nodes.jsonl').read_text(encoding='utf-8')
        aristas = (self.snap / 'relationships.jsonl').read_text(encoding='utf-8')
        roto = '{"labels": ["Person"], "props": {"entity_id": "CENTINELA_EXPORT"'
        colgada = json.dumps({'start': '4:x:9999', 'end': '4:x:0', 'type': 'PAID', 'ref': '5:x:9999',
                              'props': {'source_quote': 'CENTINELA_EXPORT'}})
        sin_tipo = json.dumps({'start': '4:x:0', 'end': '4:x:1', 'ref': '5:x:9998',
                               'props': {'source_quote': 'CENTINELA_EXPORT'}})
        empresa = json.dumps({'labels': ['Company'], 'props': {'name': 'CENTINELA_EXPORT'}, 'ref': '4:x:9997'})

        def mixto(*etiquetas):
            return json.dumps({'labels': list(etiquetas), 'props': {'name': 'CENTINELA_EXPORT'}, 'ref': '4:x:9996'})
        for caso, (n, r), motivo in (('los dos vacíos', ('', ''), 'nodes.jsonl vacío'),
                                     ('nodes vacío con aristas', ('', aristas), 'nodes.jsonl vacío'),
                                     ('aristas vacío', (nodos, ''), 'relationships.jsonl vacío'),
                                     ('línea cortada', (nodos + roto + '\n', aristas), 'línea'),
                                     ('arista hacia un nodo ausente', (nodos, aristas + colgada + '\n'),
                                      'apunta a un nodo que no está'),
                                     # Codex, 6ª pasada: objetos sin las claves del grafo
                                     ('objetos vacíos', ('{}\n{}\n', '{}\n'), 'sin ref'),
                                     ('arista sin tipo', (nodos, aristas + sin_tipo + '\n'), 'sin tipo'),
                                     ('empresa sin entity_id', (nodos + empresa + '\n', aristas), 'sin entity_id'),
                                     # una etiqueta documental de más no exime a un nodo de entidad sin entity_id
                                     ('persona con Chunk sin entity_id', (nodos + mixto('Person', 'Chunk') + '\n', aristas),
                                      'sin entity_id'),
                                     ('empresa con Document sin entity_id',
                                      (nodos + mixto('Document', 'Company') + '\n', aristas), 'sin entity_id')):
            with self.subTest(caso=caso):
                (self.snap / 'nodes.jsonl').write_text(n, encoding='utf-8')
                (self.snap / 'relationships.jsonl').write_text(r, encoding='utf-8')
                code, out = self.snapshot([fila('R-2026-09-24-0002', U_RETIRADA)])
                self.assertEqual(code, 2, out)
                self.assertIn('registro-check: el export no vale', out)
                self.assertIn(motivo, out)
                self.assertNotIn('CENTINELA_EXPORT', out)

    def test_two_twins_alive_exceed_the_tolerance(self):
        # (c) Las mismas gemelas con las dos copias vivas.
        self.export([viva(EDGES[U_GEMELA]), viva(EDGES[U_CONSERVADA])])
        code, out = self.snapshot([fila('R-2026-09-24-0004', U_GEMELA)])
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-09-24-0004: arista «suprimida» viva (2 copias; tolerancia 1)', out)

    def test_the_word_copia_in_the_reason_is_not_a_tolerance(self):
        # (d) «No es una copia», sin unidad nombrada: la retirada tiene que haber desaparecido.
        code, out = self.snapshot([fila('R-2026-09-24-0005', U_NO_COPIA)])
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-09-24-0005: arista «suprimida» viva (1 copia; tolerancia 0)', out)

    def test_a_name_that_does_not_resolve_to_a_twin_of_the_entity_gives_no_tolerance(self):
        # (e) Un prefijo que no está en el censo, el de la propia retirada, o el de una gemela de otra entidad.
        for reason in ('Duplicado de 0123abcd; se conserva aquella.',
                       f'[{U_SIN_RESOLVER[:8]}] duplicado; se conserva la primera.',
                       f'Duplicado de {U_ZUTANO[:8]}; se conserva aquella.'):
            with self.subTest(reason=reason):
                self.decision('D-6', U_SIN_RESOLVER, reason)
                code, out = self.snapshot([fila('R-2026-09-24-0006', U_SIN_RESOLVER)])
                self.assertEqual(code, 1)
                self.assertIn('REGISTRO R-2026-09-24-0006: arista «suprimida» viva (1 copia; tolerancia 0)', out)

    def test_a_quote_that_only_contains_the_withdrawn_one_is_another_copy(self):
        # R-2026-10-03-0054: la cita retirada («100%») está dentro de la de la conservada, que vuelve a estar viva
        # tras la reposición; su decisión no nombra la conservada, así que no hay tolerancia que la cubra.
        self.decision('D-7', U_DEBIL, 'Duplica la arista de la otra página y su cita es demasiado débil.',
                      entity=SOCIEDAD_A)
        code, out = self.snapshot([fila('R-2026-10-03-0054', U_DEBIL)])
        self.assertEqual(code, 0, out)

    def test_the_kept_twin_must_still_be_published(self):
        # Spec 071, enmienda 2 (AC17): la regla de la copia retirada borró también la que el arbitraje conservaba.
        self.export([viva(EDGES[U_VIVA])], nodos=(SOCIEDAD_A, SOCIEDAD_B))
        code, out = self.snapshot([fila('R-2026-10-03-0054', U_DEBIL)])
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-10-03-0054: gemela conservada ausente', out)
        for secreto in (SOCIEDAD_A, SOCIEDAD_B, PARTICIPA[:20]):
            self.assertNotIn(secreto, out)

    def test_the_same_edge_alive_from_the_same_document_counts_as_kept(self):
        # Opción B: otra copia de la misma arista desde el mismo documento (otra cita, otra capa) la mantiene publicada.
        otra = dict(EDGES[U_PRIMERA]['props'], source_quote='B es filial de A', source_kind='acusacion')
        self.export([(SOCIEDAD_A, SOCIEDAD_B, 'OWNS', otra)])
        self.assertEqual(self.snapshot([fila('R-2026-10-03-0054', U_DEBIL)])[0], 0)
        otro_doc = dict(otra, source_doc='otro-informe.pdf')
        self.export([(SOCIEDAD_A, SOCIEDAD_B, 'OWNS', otro_doc)])
        self.assertEqual(self.snapshot([fila('R-2026-10-03-0054', U_DEBIL)])[0], 1)

    def test_the_inverse_of_a_directed_kept_edge_is_another_fact(self):
        # Codex, 2ª pasada: una OWNS invertida es otra propiedad; con la clave sin dirección certificaba la conservada.
        otra = dict(EDGES[U_PRIMERA]['props'], source_quote='A es filial de B', source_kind='acusacion')
        self.export([(SOCIEDAD_B, SOCIEDAD_A, 'OWNS', otra)])
        code, out = self.snapshot([fila('R-2026-10-03-0054', U_DEBIL)])
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-10-03-0054: gemela conservada ausente', out)
        self.assertNotIn('arista «suprimida» viva', out)

    def test_a_symmetric_kept_twin_alive_in_the_other_direction_is_kept(self):
        # Las simétricas viven en la dirección canónica y el censo puede guardar la otra: ni la gemela ni su
        # presencia dependen del sentido.
        self.decision('D-9', U_SIM_RETIRADA, f'Idéntica a {U_SIM_CONSERVADA[:8]}; se conserva aquella.',
                      entity=SOCIEDAD_A)
        self.export([viva(EDGES[U_SIM_RETIRADA])])
        code, out = self.snapshot([fila('R-2026-10-03-0091', U_SIM_RETIRADA)])
        self.assertEqual(code, 0, out)

    def test_a_twin_in_the_other_direction_gives_no_tolerance(self):
        # La misma capa, documento y cita en sentido contrario no es una gemela: la regla de la retirada (sin
        # dirección) también la borraría, así que una copia viva no se tolera.
        self.decision('D-10', U_INV_RETIRADA, f'Idéntica a {U_INV_CONSERVADA[:8]}; se conserva aquella.',
                      entity=SOCIEDAD_A)
        self.export([viva(EDGES[U_INV_CONSERVADA])])
        code, out = self.snapshot([fila('R-2026-10-03-0092', U_INV_RETIRADA)])
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-10-03-0092: arista «suprimida» viva (1 copia; tolerancia 0)', out)
        self.assertNotIn('gemela conservada ausente', out)

    def test_a_kept_unit_withdrawn_retyped_inverted_or_without_its_nodes_is_not_required(self):
        # La conservada que otra decisión retiró, retipó o invirtió, o cuyo extremo se podó, ya no tiene que estar.
        self.export([viva(EDGES[U_VIVA])], nodos=(SOCIEDAD_A,))
        self.assertEqual(self.snapshot([fila('R-2026-10-03-0054', U_DEBIL)])[0], 0, 'extremo podado')
        self.export([viva(EDGES[U_VIVA])], nodos=(SOCIEDAD_A, SOCIEDAD_B))
        for action in ('deny', 'retype', 'invert'):
            with self.subTest(action=action):
                self.otra_decision(action)
                self.assertEqual(self.snapshot([fila('R-2026-10-03-0054', U_DEBIL)])[0], 0)

    def test_a_kept_unit_whose_props_a_decision_changed_must_still_be_published(self):
        # Codex, 2ª pasada (R-2026-09-24-0035): un `set_prop` (o `set_status`) cambia props, no quita la arista.
        for action, campos in (('set_prop', {'prop': 'note', 'value': 'nota ficticia'}),
                               ('set_status', {'status': 'investigado'})):
            with self.subTest(action=action):
                self.otra_decision(action, **campos)
                self.export([viva(EDGES[U_VIVA])], nodos=(SOCIEDAD_A, SOCIEDAD_B))
                code, out = self.snapshot([fila('R-2026-10-03-0054', U_DEBIL)])
                self.assertEqual(code, 1)
                self.assertIn('REGISTRO R-2026-10-03-0054: gemela conservada ausente', out)
                self.export([viva(EDGES[U_PRIMERA])])
                code, out = self.snapshot([fila('R-2026-10-03-0054', U_DEBIL)])
                self.assertEqual(code, 0, out)

    def test_rows_without_unit_or_whose_unit_is_not_an_edge_are_ignored(self):
        # Spec 071 AC149: la forma del anonimato también corre con --snapshot; una «rectificado» nombra los dos
        # extremos. Una fila cuya unidad no es una arista no se contrasta como arista; una «suprimido» sin unidad ni
        # marcador, por la forma de su dato (AC183): la de un seudónimo, con su política (spec 070).
        rectificada = dict(fila('R-2026-09-24-0008', U_VIVA, decision='rectificado'),
                           dato=f'({FULANA}) -[PAID]-> ({MENGANO}): arista corregida')
        seudonimo = dict(fila('R-2026-10-03-0001', ''),
                         dato='(Person:fulana-de-tal): relación institucional retirada (seudónimo judicial)')
        resumen = dict(fila('R-2026-09-24-0007', U_RESUMEN), dato='frase del resumen retirada')
        code, out = self.snapshot([seudonimo, resumen, rectificada])
        self.assertEqual(code, 0, out)
        self.assertIn('0 aristas «suprimidas»', out)

    def test_a_withdrawal_without_unit_is_checked_by_the_form_of_its_dato(self):
        # Spec 071 AC183 (21a-1): una «suprimido» sin unidad ni marcador ya no se salta. Una arista «(A) -[T]-> (B): …»
        # necesita una regla deny explícita del overlay que la retire (sin ella, «retirada sin regla pública»: aquí, un
        # overlay sin reglas); la fórmula cerrada de un seudónimo la comprueba su política; cualquier otra forma es un
        # problema ya en el árbol (nada la comprobaría), que no deja correr la puerta. Sin valores en la salida.
        arista = dict(fila('R-2026-10-08-0001', ''), dato=f'({FULANA}) -[PAID]-> ({MENGANO}): arista retirada')
        code, out = self.snapshot([arista])
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-08-0001: retirada sin regla pública', out)
        libre = dict(fila('R-2026-10-08-0002', ''), dato='retirada la relación del centinela con su sociedad')
        for modo in (('--tree',), ('--snapshot', str(self.snap), '--root', str(self.root))):
            with self.subTest(modo=modo[0]):
                code, out = self.run_check([libre], *modo)
                self.assertEqual(code, 1, out)
                self.assertIn('REGISTRO R-2026-10-08-0002: retirada sin unidad con un dato que no se puede comprobar',
                              out)
                self.assertNotIn('centinela', out)

    def test_a_unit_missing_from_the_census_is_a_problem(self):
        code, out = self.snapshot([fila('R-2026-09-24-0009', 'f' * 64)])
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-09-24-0009: la unidad no está en el censo', out)

    def test_an_internal_error_names_only_its_class(self):
        # Codex, 3ª pasada: ningún traceback llega al log; el texto de la excepción puede llevar un valor.
        with mock.patch.object(RC, 'export_vivo', side_effect=ValueError(SENTINEL)):
            code, out = self.snapshot([fila('R-2026-09-24-0001', U_VIVA)])
        self.assertEqual(code, 2)
        self.assertNotIn(SENTINEL, out)
        self.assertIn('registro-check: error interno (ValueError)', out)

    def test_without_the_census_the_gate_stops_with_exit_2(self):
        (self.root / 'census.sqlite').unlink()
        code, out = self.snapshot([fila('R-2026-09-24-0001', U_VIVA)])
        self.assertEqual(code, 2)
        self.assertIn('falta el censo', out)

    def test_a_malformed_registry_is_not_checked_against_the_export(self):
        code, out = self.snapshot([fila('R-2026-09-24-0001', U_VIVA), fila('R-2026-09-24-0001', U_RETIRADA)])
        self.assertEqual(code, 1)
        self.assertIn('id repetido', out)
        self.assertIn('snapshot sin comprobar', out)


    # --- 8ª pasada de Codex (spec 071, enmienda 10) -------------------------------------------------------------

    def unidades(self, valores, entidad=SOCIEDAD_A, pack='E-sociedad-octava'):
        """Aristas que se añaden al censo de prueba, en un pack de `entidad`."""
        db = sqlite3.connect(self.root / 'census.sqlite')
        with db:
            db.executemany('INSERT INTO units(unit_id, kind, value) VALUES (?,?,?)',
                           [(u, 'edge', json.dumps(v, ensure_ascii=False)) for u, v in valores.items()])
            db.execute('INSERT INTO esc_packs VALUES (?,?,?)', (pack, entidad, json.dumps(list(valores))))
        db.close()

    def resolucion(self, nombre, uid, accion, entidad, resolution='humano-2026-10-07',
                   cuando='2026-10-07T10:00:00+00:00', **campos):
        """Otra decisión sobre `uid` con su resolución e instante."""
        d = {'entity_id': entidad, '_meta': {'entity': entidad, 'resolution': resolution, 'decided_at': cuando},
             'units': [{'unit_id': uid, 'verdict': 'verificado' if accion == 'keep' else 'corregido',
                        'label': 'ok' if accion == 'keep' else 'arista', 'evidence': 'p.3'}],
             'edges': [{'unit_id': uid, 'action': accion, 'evidence': 'p.3', 'reason': 'otra resolución', **campos}]}
        (self.root / 'esc/decisions' / f'{nombre}.json').write_text(json.dumps(d), encoding='utf-8')

    def test_a_manual_retype_that_reaches_the_withdrawn_edge_keeps_it_counted(self):
        # Codex, 8ª pasada (AC51): dos trozos distintos de una misma cita no se contienen. La regla manual del overlay
        # que retipa con «second fragment» corre antes que el deny de «first fragment» y deja viva la retirada bajo el
        # tipo nuevo. Sin las reglas del overlay (`--overlay`), no se ve.
        cita, u = 'first fragment and second fragment', 'f1' * 32
        self.unidades({u: arista(SOCIEDAD_A, SOCIEDAD_C, rtype='OWNS', doc='informe-ficticio.pdf', quote=cita)})
        self.decision('D-20', u, 'la cita no sostiene la propiedad', entity=SOCIEDAD_A)
        manual = {'source_id': SOCIEDAD_A, 'target_id': SOCIEDAD_C, 'type': 'OWNS', 'corrected_type': 'SUBSIDIARY_OF',
                  'swap': False, 'set': None, 'doc': 'informe-ficticio.pdf', 'quote_contains': 'second fragment',
                  'kinds': None}
        start, end, _, props = viva(arista(SOCIEDAD_A, SOCIEDAD_C, rtype='OWNS', doc='informe-ficticio.pdf', quote=cita))
        self.export([(start, end, 'SUBSIDIARY_OF', props)])
        filas = [fila('R-2026-10-07-0101', u)]
        with mock.patch.object(RC, '_reglas_overlay', return_value=([], [manual])):
            code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                       '--overlay', str(self.base))
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0101: arista «suprimida» viva (1 copia; tolerancia 0)', out)
        self.assertEqual(self.snapshot(filas)[0], 0)

    def test_a_reversed_twin_with_a_note_gives_no_tolerance(self):
        # Codex, 8ª pasada (AC52): una OWNS b→a con un set_prop de nota no es la OWNS a→b retirada: el loader casa su
        # regla por dirección, así que no la alcanza ni puede ser la copia que queda. En un tipo simétrico, sí.
        for rtype, n, tolerancia in (('OWNS', '1', 0), ('ASSOCIATED_WITH', '2', 1)):
            with self.subTest(tipo=rtype):
                ua, ub = ('a' + n) * 32, ('b' + n) * 32
                retirada = arista(SOCIEDAD_A, SOCIEDAD_C, rtype=rtype, doc='informe-ficticio.pdf', quote=PARTICIPA_C)
                inversa = arista(SOCIEDAD_C, SOCIEDAD_A, rtype=rtype, doc='informe-ficticio.pdf', quote=PARTICIPA_C)
                self.unidades({ua: retirada, ub: inversa}, pack=f'E-sociedad-octava-{n}')
                self.decision(f'D-2{n}', ua, 'la cita no sostiene la propiedad', entity=SOCIEDAD_A)
                self.resolucion(f'D-3{n}', ub, 'set_prop', SOCIEDAD_A, resolution='arbitraje-2026-10-03',
                                cuando='2026-10-03T10:00:00+00:00', prop='note', value='nota ficticia')
                self.export([viva(retirada)])
                code, out = self.snapshot([fila('R-2026-10-07-0102', ua)])
                self.assertEqual(code, 0 if tolerancia else 1, out)
                if not tolerancia:
                    self.assertIn('REGISTRO R-2026-10-07-0102: arista «suprimida» viva (1 copia; tolerancia 0)', out)

    def test_only_the_operation_in_force_of_a_kept_twin_counts(self):
        # Codex, 8ª pasada (AC54): la gemela que la retirada dice conservar tuvo un deny y luego una resolución humana
        # que la conserva. Lo que hizo la decisión sustituida no cuenta: tiene que seguir publicada y da tolerancia.
        ua, ub = 'c1' * 32, 'c2' * 32
        gemela = arista(SOCIEDAD_A, SOCIEDAD_C, rtype='OWNS', doc='informe-ficticio.pdf', quote=PARTICIPA_C)
        self.unidades({ua: gemela, ub: gemela})
        self.decision('D-40', ua, f'Idéntica a {ub[:8]}…; se conserva aquella.', entity=SOCIEDAD_A)
        self.decision('D-41', ub, 'retirada en una primera vuelta', entity=SOCIEDAD_A)
        self.resolucion('D-42', ub, 'keep', SOCIEDAD_A)
        filas = [fila('R-2026-10-07-0103', ua)]
        self.export([viva(EDGES[U_VIVA])], nodos=(SOCIEDAD_A, SOCIEDAD_C))
        code, out = self.snapshot(filas)
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0103: gemela conservada ausente', out)
        self.export([viva(gemela)])
        code, out = self.snapshot(filas)
        self.assertEqual(code, 0, out)

    def test_a_decision_that_cannot_be_ordered_stops(self):
        # Spec 071 AC55: un decided_at sin zona no se puede comparar en UTC. Para con exit 2 y el fichero, sin valor.
        self.resolucion('D-50', U_PRIMERA, 'keep', SOCIEDAD_A, cuando='2026-10-07T10:00:00')
        self.registro.write_text(yaml.safe_dump({'rectificaciones': [fila('R-2026-09-24-0001', U_RETIRADA)]},
                                                allow_unicode=True, sort_keys=False), encoding='utf-8')
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as salida:
            RC.main(['--registro', str(self.registro), '--snapshot', str(self.snap), '--root', str(self.root),
                     '--overlay', str(self.sin_reglas)])
        self.assertEqual(salida.exception.code, 2)
        self.assertIn('decisión con decided_at inválido (D-50.json)', err.getvalue())

    # --- 9ª pasada de Codex (spec 071, enmienda 11) -------------------------------------------------------------

    def test_a_retype_followed_by_another_rule_that_changes_its_layer_keeps_the_withdrawn_edge_counted(self):
        # Codex, 9ª pasada (AC57): el loader compone las fases. Un retype OWNS→CONTROLS del overlay y el `set` de otra
        # regla sobre CONTROLS que la pasa a prensa dejan viva la retirada como CONTROLS/prensa, y el deny OWNS no la
        # encuentra. Cada regla evaluada contra el valor del censo no daba esa firma; con el retype solo, ya fallaba.
        u = 'e1' * 32
        retirada = arista(SOCIEDAD_A, SOCIEDAD_C, rtype='OWNS', doc='informe-ficticio.pdf', quote=PARTICIPA_C)
        self.unidades({u: retirada}, pack='E-sociedad-novena')
        self.decision('D-60', u, 'la cita no sostiene la propiedad', entity=SOCIEDAD_A)
        base = {'source_id': SOCIEDAD_A, 'target_id': SOCIEDAD_C, 'doc': 'informe-ficticio.pdf',
                'quote_contains': PARTICIPA_C[:30], 'kinds': None, 'swap': False}
        retipo = {**base, 'type': 'OWNS', 'corrected_type': 'CONTROLS', 'set': None}
        a_prensa = {**base, 'type': 'CONTROLS', 'corrected_type': None, 'set': {'source_kind': 'prensa'}}
        start, end, _, props = viva(retirada)
        filas = [fila('R-2026-10-07-0111', u)]
        for caso, reglas, capa in (('retype y set', [retipo, a_prensa], 'prensa'), ('solo el retype', [retipo], 'judicial')):
            with self.subTest(caso=caso):
                self.export([(start, end, 'CONTROLS', {**props, 'source_kind': capa})])
                with mock.patch.object(RC, '_reglas_overlay', return_value=([], reglas)):
                    code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                               '--overlay', str(self.base))
                self.assertEqual(code, 1, out)
                self.assertIn('REGISTRO R-2026-10-07-0111: arista «suprimida» viva (1 copia; tolerancia 0)', out)

    def test_an_accusation_or_a_claim_posture_alive_with_another_quote_is_the_withdrawn_edge(self):
        # Codex, 9ª pasada (AC59): `accusations_loader` y `claims_loader` recrean su arista con un MERGE por identidad que
        # actualiza la cita. La acusación retirada sigue publicada con otra cita y otra página: es ella; y desde otro
        # documento también, que el MERGE es por persona, delito y tipo (10ª pasada, AC65). La postura retirada, con
        # otra cita: es ella.
        u = 'e2' * 32
        acusacion = arista(FULANA, DELITO, rtype='ACCUSED_OF', kind='acusacion', doc='auto-ficticio.pdf', quote=FALLO)
        self.unidades({u: acusacion}, entidad=FULANA, pack='E-fulana-novena')
        self.decision('D-61', u, 'el auto no sostiene la acusación')
        start, end, rtype, props = viva(acusacion)
        for caso, otra, codigo in (('otra cita y página', {'source_quote': 'Fulana, investigada por un delito ficticio',
                                                           'source_page': 9}, 1),
                                   ('otro documento', {'source_doc': 'otro-auto.pdf'}, 1)):
            with self.subTest(caso=caso):
                self.export([(start, end, rtype, {**props, **otra})])
                code, out = self.snapshot([fila('R-2026-10-07-0112', u)])
                self.assertEqual(code, codigo, out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-10-07-0112: arista «suprimida» viva (1 copia; tolerancia 0)', out)
        self.decision('D-62', U_CLAIM_RETIRADA, 'la afirmación no tiene fuente')
        start, end, rtype, props = viva(EDGES[U_CLAIM_RETIRADA])
        self.export([(start, end, rtype, {**props, 'source_quote': 'Fulana sostuvo otra versión del pago'})])
        code, out = self.snapshot([fila('R-2026-10-07-0113', U_CLAIM_RETIRADA)])
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0113: arista «suprimida» viva (1 copia; tolerancia 0)', out)

    def test_a_quote_in_another_unicode_form_is_still_the_withdrawn_edge(self):
        # Codex, 16ª pasada (16a-M1, AC107): `_cita` no normalizaba Unicode. La retirada viva con su cita en NFD (las
        # tildes descompuestas) se daba por hecha. NFKC antes de comparar: viva, como con la cita en NFC.
        u = 'cf' * 32
        retirada = arista(SOCIEDAD_A, SOCIEDAD_B, rtype='OWNS', doc='informe-ficticio.pdf',
                          quote='la sociedad A participa en la sociedad B según el informe')
        self.unidades({u: retirada}, pack='E-veinte-unicode')
        self.decision('D-70', u, 'la cita no sostiene la propiedad', entity=SOCIEDAD_A)
        start, end, rtype, props = viva(retirada)
        for forma in ('NFD', 'NFC'):
            with self.subTest(forma=forma):
                cita = unicodedata.normalize(forma, props['source_quote'])
                self.export([(start, end, rtype, {**props, 'source_quote': cita})])
                code, out = self.snapshot([fila('R-2026-10-08-0270', u)])
                self.assertEqual(code, 1, out)
                self.assertIn('REGISTRO R-2026-10-08-0270: arista «suprimida» viva (1 copia; tolerancia 0)', out)

    def test_a_posture_whose_claim_is_not_recovered_is_undetermined(self):
        # Codex, 16ª pasada (16a-H3, AC104): sin el claim_id de su afirmación, una postura casaba con cualquier Claim de
        # su titular. Si el censo no lo recupera, la fila no se puede comprobar: «postura indeterminada», nunca
        # acreditada por cualquier Claim, ni viva por cualquiera.
        u = 'df' * 32
        self.unidades({u: arista(FULANA, None, rtype='DISPUTES', quote='Fulana negó una versión ficticia')},
                      entidad=FULANA, pack='E-veinte-postura')
        self.decision('D-71', u, 'la postura no tiene fuente')
        for export in ([], [(FULANA, None, 'DISPUTES', {'source_kind': 'judicial', 'source_quote': 'otra'})]):
            with self.subTest(postura_en_el_export=bool(export)):
                self.export([viva(EDGES[U_PRIMERA]), *export])
                code, out = self.snapshot([fila('R-2026-10-08-0271', u)])
                self.assertEqual(code, 1, out)
                self.assertIn(f'REGISTRO R-2026-10-08-0271: {RC.INDETERMINADA}', out)
                self.assertNotIn('viva', out)
        self.afirmacion(u, CLAIM)
        self.export([viva(EDGES[U_PRIMERA])])
        code, out = self.snapshot([fila('R-2026-10-08-0271', u)])
        self.assertEqual(code, 0, out)

    # --- enmienda 12 (spec 071 AC61) ----------------------------------------------------------------------------

    ANTES, NUEVA = 'Company:sociedad-de-antes-ficticia', 'Company:sociedad-nueva-ficticia'

    def reidentificada(self, u, nombre, rtype='CONTROLS', quote=CONTROL):
        """Una retirada de la sociedad A a una sociedad cuyo id ya no está en el export (re-identificada después del
        censo), con su decisión deny."""
        valor = arista(SOCIEDAD_A, self.ANTES, rtype=rtype, doc='informe-ficticio.pdf', quote=quote)
        self.unidades({u: valor}, pack=f'E-sociedad-{nombre}')
        self.decision(nombre, u, 'la cita no sostiene la relación', entity=SOCIEDAD_A)
        return valor

    def test_a_withdrawn_edge_alive_on_a_reidentified_pair_is_found_by_its_signature(self):
        # Spec 071 AC61: buscada solo en su par, que ya no existe, la retirada no se veía aunque el hecho siguiera vivo
        # entre el nodo nuevo y el otro extremo.
        u = 'f7' * 32
        valor = self.reidentificada(u, 'D-70')
        filas = [fila('R-2026-10-07-0121', u)]
        self.export([(SOCIEDAD_A, self.NUEVA, 'CONTROLS', dict(valor['props']))])
        code, out = self.snapshot(filas)
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0121: arista «suprimida» viva (par re-identificado; 1 copia; tolerancia 0)',
                      out)
        self.export([viva(EDGES[U_VIVA])], nodos=(SOCIEDAD_A, self.NUEVA))
        self.assertEqual(self.snapshot(filas)[0], 0)

    def test_the_entity_of_a_row_missing_from_the_export_is_not_a_problem_by_itself(self):
        # Spec 071 AC61: la `entidad` de la fila es el id de la época; con los dos extremos de la arista en el export se
        # busca en su par, como siempre.
        row = dict(nombrada('R-2026-10-07-0122', U_RETIRADA), entidad='Person:entidad-de-la-epoca-ficticia')
        code, out = self.snapshot([row])
        self.assertEqual(code, 0, out)

    def test_a_symmetric_withdrawn_edge_on_a_reidentified_pair_counts_in_any_direction(self):
        u = 'f8' * 32
        valor = self.reidentificada(u, 'D-71', rtype='ASSOCIATED_WITH', quote=ADMINISTRADOR)
        self.export([(self.NUEVA, SOCIEDAD_A, 'ASSOCIATED_WITH', dict(valor['props']))])
        code, out = self.snapshot([fila('R-2026-10-07-0123', u)])
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0123: arista «suprimida» viva (par re-identificado; 1 copia; tolerancia 0)',
                      out)

    def test_a_retype_composed_by_phases_on_a_reidentified_pair_still_counts(self):
        # Spec 071 AC61 con AC57: una regla retipó la retirada OWNS→CONTROLS antes de la re-identificación; su copia
        # viva lleva el tipo compuesto. Sin la regla, el tipo del censo no tiene copia.
        u = 'f9' * 32
        valor = self.reidentificada(u, 'D-72', rtype='OWNS', quote=PARTICIPA)
        retipo = {'source_id': SOCIEDAD_A, 'target_id': self.ANTES, 'type': 'OWNS', 'corrected_type': 'CONTROLS',
                  'set': None, 'swap': False, 'doc': 'informe-ficticio.pdf', 'quote_contains': PARTICIPA[:30],
                  'kinds': None}
        self.export([(SOCIEDAD_A, self.NUEVA, 'CONTROLS', dict(valor['props']))])
        filas = [fila('R-2026-10-07-0124', u)]
        with mock.patch.object(RC, '_reglas_overlay', return_value=([], [retipo])):
            code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                       '--overlay', str(self.base))
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0124: arista «suprimida» viva (par re-identificado; 1 copia; tolerancia 0)',
                      out)
        self.assertEqual(self.snapshot(filas)[0], 0)

    def test_a_named_twin_reidentified_with_the_withdrawn_edge_still_gives_its_tolerance(self):
        # Spec 071 AC61: la gemela que la decisión conserva se re-identificó con la retirada. La clave de tolerancia va
        # sin extremos: su copia la excusa una vez, y una segunda copia es la retirada.
        u, k = 'fa' * 32, 'fb' * 32
        valor = arista(SOCIEDAD_A, self.ANTES, rtype='CONTROLS', doc='informe-ficticio.pdf', quote=CONTROL)
        self.unidades({u: valor, k: dict(valor)}, pack='E-sociedad-gemelas')
        self.decision('D-73', u, f'Idéntica a {k[:8]}…; se conserva aquella.', entity=SOCIEDAD_A)
        copia = (SOCIEDAD_A, self.NUEVA, 'CONTROLS', dict(valor['props']))
        filas = [fila('R-2026-10-07-0125', u)]
        self.export([copia])
        self.assertEqual(self.snapshot(filas)[0], 0)
        self.export([copia, copia])
        code, out = self.snapshot(filas)
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0125: arista «suprimida» viva (par re-identificado; 2 copias; tolerancia 1)',
                      out)


    # --- enmienda 13 (spec 071 AC63, AC64 y AC67) -------------------------------------------------------------

    def test_a_retype_on_the_current_pair_of_a_reidentified_edge_keeps_it_counted(self):
        # Codex, 10ª pasada (AC63): las fases solo se componían en el par del censo; con el extremo re-identificado, el
        # retype OWNS→CONTROLS del par actual no entraba y la copia CONTROLS no se veía.
        u = 'fc' * 32
        valor = self.reidentificada(u, 'D-74', rtype='OWNS', quote=PARTICIPA)
        par = {'source_id': SOCIEDAD_A, 'target_id': self.NUEVA, 'type': 'OWNS', 'doc': 'informe-ficticio.pdf',
               'quote_contains': PARTICIPA[:30], 'kinds': None, 'swap': False, 'set': None}
        deny = {**par, 'corrected_type': None, 'reason': f'prueba [{u[:8]}]: retirada sobre el par vigente'}
        retipo = {**par, 'corrected_type': 'CONTROLS', 'reason': 'prueba: retipo posterior'}
        self.export([(SOCIEDAD_A, self.NUEVA, 'CONTROLS', dict(valor['props']))])
        filas = [fila('R-2026-10-07-0131', u)]
        with mock.patch.object(RC, '_reglas_overlay', return_value=([deny], [retipo])):
            code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                       '--overlay', str(self.base))
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0131: arista «suprimida» viva (par re-identificado; 1 copia; tolerancia 0)',
                      out)

    def test_a_rectified_retype_with_its_old_copy_still_alive_is_reported(self):
        # Codex, 10ª pasada (AC64): una fila «rectificado» de un retype vigente afirma que ya no hay copia sin corregir;
        # el check no miraba esas filas. Con solo la copia retipada, el retype completo pasa.
        retipo = dict(fila('R-2026-10-07-0132', U_PRIMERA, decision='rectificado'),
                      dato=f'({SOCIEDAD_A}) -[OWNS]-> ({SOCIEDAD_B}): arista corregida')
        self.otra_decision('retype', new_type='CONTROLS')
        start, end, _, props = viva(EDGES[U_PRIMERA])
        self.export([viva(EDGES[U_PRIMERA]), (start, end, 'CONTROLS', props)])
        code, out = self.snapshot([retipo])
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0132: arista «rectificada» viva sin corregir (1 copia; tolerancia 0)', out)
        self.export([(start, end, 'CONTROLS', props)])
        code, out = self.snapshot([retipo])
        self.assertEqual(code, 0, out)

    def test_the_latest_row_of_a_unit_is_the_one_checked(self):
        # Spec 071 AC67: el registro no borra historia. Una «suprimido» completada por una «rectificado» posterior de la
        # misma unidad ya no se comprueba; la «rectificado» afirma que la arista sigue publicada desde el documento que
        # cita, y necesita una operación vigente que no la retire.
        u = 'fd' * 32
        acusacion = arista(FULANA, DELITO, rtype='ACCUSED_OF', kind='acusacion', doc='auto-ficticio.pdf', quote=FALLO)
        self.unidades({u: acusacion}, entidad=FULANA, pack='E-fulana-decimotercera')
        self.decision('D-76', u, 'el auto no sostiene la acusación')
        # La «suprimido» de una unidad rectificada después nombra los dos extremos (AC113; AC149 lo mira también aquí).
        suprimida = dict(fila('R-2026-09-24-0141', u), fecha='2026-09-24',
                         dato=f'({FULANA}) -[ACCUSED_OF]-> ({DELITO}): arista retirada')
        rectificada = dict(fila('R-2026-10-07-0142', u, decision='rectificado'), fecha='2026-10-07',
                           fuente='auto-nuevo-ficticio.pdf p.4',
                           dato=f'({FULANA}) -[ACCUSED_OF]-> ({DELITO}): arista corregida')
        start, end, rtype, props = viva(acusacion)
        nueva = (start, end, rtype, {**props, 'source_doc': 'auto-nuevo-ficticio.pdf', 'source_quote': 'otra cita'})
        self.export([nueva])
        code, out = self.snapshot([suprimida, rectificada])
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0142: fila «rectificado» sin una operación vigente que la respalde', out)
        self.assertNotIn('R-2026-09-24-0141', out)
        self.resolucion('D-77', u, 'keep', FULANA, resolution='humano-2026-09-30', cuando='2026-09-30T10:00:00+00:00')
        code, out = self.snapshot([suprimida, rectificada])
        self.assertEqual(code, 0, out)
        self.export([viva(EDGES[U_VIVA])], nodos=(FULANA, DELITO))
        code, out = self.snapshot([suprimida, rectificada])
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0142: arista «rectificada» ausente del export', out)

    # --- enmienda 14 (spec 071 AC68–AC71, 11ª pasada de Codex) ------------------------------------------------------

    def suprimida_y_rectificada(self, unidad, n):
        """La historia de una unidad: una «suprimido» del 24-sep completada por una «rectificado» del 07-oct que cita
        el informe ficticio."""
        return [dict(fila(f'R-2026-09-24-0{n}1', unidad), fecha='2026-09-24',
                     dato=f'({SOCIEDAD_A}) -[OWNS]-> ({SOCIEDAD_B}): arista retirada'),
                dict(fila(f'R-2026-10-07-0{n}2', unidad, decision='rectificado'), fecha='2026-10-07',
                     fuente='informe-ficticio.pdf p.4',
                     dato=f'({SOCIEDAD_A}) -[OWNS]-> ({SOCIEDAD_B}): arista corregida')]

    def test_a_reidentified_accusation_is_found_by_its_loader_identity_on_the_current_pair(self):
        # Codex, 11ª pasada (AC68): con el extremo antiguo fuera del export y un deny etiquetado sobre el par actual, la
        # misma acusación publicada con otro documento y otra cita no tenía copias por la firma documental del censo y
        # la retirada se daba por hecha; el MERGE de `accusations_loader` la sigue publicando (persona, delito y tipo).
        u, antes = 'fe' * 32, 'Person:fulana-de-antes-ficticia'
        acusacion = arista(antes, DELITO, rtype='ACCUSED_OF', kind='acusacion', doc='auto-ficticio.pdf', quote=FALLO)
        self.unidades({u: acusacion}, entidad=antes, pack='E-fulana-decimocuarta')
        self.decision('D-80', u, 'el auto no sostiene la acusación', entity=antes)
        deny = {'source_id': FULANA, 'target_id': DELITO, 'type': 'ACCUSED_OF', 'doc': 'auto-ficticio.pdf',
                'quote_contains': FALLO[:30], 'kinds': ['acusacion'],
                'reason': f'prueba [{u[:8]}]: sobre el par vigente'}
        otra = {'source_kind': 'acusacion', 'source_doc': 'otro-auto.pdf', 'source_quote': 'otra cita del otro auto'}
        filas = [fila('R-2026-10-07-0151', u)]
        for caso, export, codigo in (('en el par actual', [(FULANA, DELITO, 'ACCUSED_OF', otra)], 1),
                                     ('de otra persona', [(ZUTANO, DELITO, 'ACCUSED_OF', otra)], 0)):
            with self.subTest(caso=caso):
                self.export(export, nodos=(FULANA,))
                with mock.patch.object(RC, '_reglas_overlay', return_value=([deny], [])):
                    code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                               '--overlay', str(self.base))
                self.assertEqual(code, codigo, out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-10-07-0151: arista «suprimida» viva (par re-identificado; 1 copia; '
                                  'tolerancia 0)', out)
        # Sin una regla que diga el par actual no hay par en que buscar por identidad: la firma del censo, como antes.
        self.export([(FULANA, DELITO, 'ACCUSED_OF', otra)])
        self.assertEqual(self.snapshot(filas)[0], 0)

    def test_a_rectified_retype_completing_a_withdrawal_needs_its_resulting_copy(self):
        # Codex, 11ª pasada (AC69): «suprimido» antigua, «rectificado» vigente, retype OWNS→CONTROLS vigente y ninguna
        # copia de los dos tipos: el check no miraba la presencia cuando la fila corrige. Con la CONTROLS en su sentido,
        # desde el documento que cita la fila, la corrección está publicada.
        self.otra_decision('retype', new_type='CONTROLS')
        filas = self.suprimida_y_rectificada(U_PRIMERA, 15)
        self.export([viva(EDGES[U_VIVA])], nodos=(SOCIEDAD_A, SOCIEDAD_B))
        code, out = self.snapshot(filas)
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0152: arista «rectificada» ausente del export', out)
        start, end, _, props = viva(EDGES[U_PRIMERA])
        self.export([(start, end, 'CONTROLS', props)])
        code, out = self.snapshot(filas)
        self.assertEqual(code, 0, out)

    def test_a_kept_fact_is_published_only_in_its_direction(self):
        # Codex, 11ª pasada (AC70): tras una «suprimido» completada por una «rectificado» y una decisión humana que
        # conserva la OWNS A→B, un export con solo la B→A pasaba: se comparaban capa y documento, no la dirección. En un
        # tipo simétrico la dirección no cuenta.
        for unidad, codigo in ((U_PRIMERA, 1), (U_SIM_RETIRADA, 0)):
            with self.subTest(tipo=EDGES[unidad]['type']):
                self.resolucion('D-81', unidad, 'keep', SOCIEDAD_A, resolution='humano-2026-09-30',
                                cuando='2026-09-30T10:00:00+00:00')
                filas = self.suprimida_y_rectificada(unidad, 16)
                start, end, rtype, props = viva(EDGES[unidad])
                self.export([(end, start, rtype, props)])
                code, out = self.snapshot(filas)
                self.assertEqual(code, codigo, out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-10-07-0162: arista «rectificada» ausente del export', out)
                self.export([(start, end, rtype, props)])
                self.assertEqual(self.snapshot(filas)[0], 0)

    def test_a_rectified_retype_whose_rule_also_inverts_is_alive(self):
        # Codex, 11ª pasada (AC71): vigente retype OWNS→CONTROLS y una regla etiquetada que además invierte. El loader
        # publica CONTROLS al revés, y el check descartaba toda copia del tipo decidido. El retype sin swap, con la
        # CONTROLS en su sentido, sigue sin problema.
        self.otra_decision('retype', new_type='CONTROLS')
        regla = {'source_id': SOCIEDAD_A, 'target_id': SOCIEDAD_B, 'type': 'OWNS', 'corrected_type': 'CONTROLS',
                 'swap': True, 'set': None, 'doc': 'informe-ficticio.pdf', 'quote_contains': PARTICIPA[:30],
                 'kinds': None, 'reason': f'prueba [{U_PRIMERA[:8]}]: retipo'}
        filas = [dict(fila('R-2026-10-07-0171', U_PRIMERA, decision='rectificado'),
                      dato=f'({SOCIEDAD_A}) -[OWNS]-> ({SOCIEDAD_B}): arista corregida')]
        start, end, _, props = viva(EDGES[U_PRIMERA])
        for caso, swap, export, codigo in (('con swap, copia al revés', True, (end, start), 1),
                                           ('sin swap, copia en su sentido', False, (start, end), 0)):
            with self.subTest(caso=caso):
                self.export([(*export, 'CONTROLS', props)])
                with mock.patch.object(RC, '_reglas_overlay', return_value=([], [dict(regla, swap=swap)])):
                    code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                               '--overlay', str(self.base))
                self.assertEqual(code, codigo, out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-10-07-0171: arista «rectificada» viva sin corregir (1 copia; '
                                  'tolerancia 0)', out)

    # --- enmienda 15 (spec 071 AC73 y AC74, pistas de la 12ª pasada de Codex) -----------------------------------

    def test_a_reidentified_retype_needs_its_resulting_copy_in_its_direction_and_with_its_set(self):
        # AC73: con el extremo antiguo fuera del export, la copia resultante de un retype se aceptaba en cualquier par y
        # dirección y sin los props del `set` de la operación. En el par actual tiene que ir en el sentido decidido
        # (salvo en un tipo simétrico) y con esos props; si no, es una copia sin corregir.
        u, nota = 'f3' * 32, {'note': 'NOTA_DECIDIDA_SINTETICA'}
        valor = self.reidentificada(u, 'D-90', rtype='OWNS', quote=PARTICIPA)
        filas = self.suprimida_y_rectificada(u, 18)
        for caso, nuevo, decidida, copia, codigo in (
                ('al revés', 'CONTROLS', {}, (self.NUEVA, SOCIEDAD_A, {}), 1),
                ('sin la nota decidida', 'CONTROLS', nota, (SOCIEDAD_A, self.NUEVA, {}), 1),
                ('simétrica al revés', 'ASSOCIATED_WITH', {}, (self.NUEVA, SOCIEDAD_A, {}), 0),
                ('completa', 'CONTROLS', nota, (SOCIEDAD_A, self.NUEVA, nota), 0)):
            with self.subTest(caso=caso):
                self.resolucion('D-91', u, 'retype', SOCIEDAD_A, new_type=nuevo,
                                **({'set': decidida} if decidida else {}))
                regla = {'source_id': SOCIEDAD_A, 'target_id': self.NUEVA, 'type': 'OWNS', 'corrected_type': nuevo,
                         'swap': False, 'set': decidida or None, 'doc': 'informe-ficticio.pdf',
                         'quote_contains': PARTICIPA[:30], 'kinds': None,
                         'reason': f'prueba [{u[:8]}]: retipo sobre el par vigente'}
                inicio, fin, extra = copia
                self.export([(inicio, fin, nuevo, {**valor['props'], **extra})])
                with mock.patch.object(RC, '_reglas_overlay', return_value=([], [regla])):
                    code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                               '--overlay', str(self.base))
                self.assertEqual(code, codigo, out)
                self.assertNotIn('NOTA_DECIDIDA_SINTETICA', out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-10-07-0182: arista «rectificada» ausente del export', out)
                    self.assertIn('REGISTRO R-2026-10-07-0182: arista «rectificada» viva sin corregir (par '
                                  're-identificado; 1 copia; tolerancia 0)', out)

    def test_a_rectified_retype_with_a_set_needs_its_props_on_the_resulting_copy(self):
        # AC74: la «rectificado» que completa una «suprimido» de un retype con mapa `set` afirma la arista corregida con
        # esos props; la copia del tipo nuevo sin ellos no la materializa.
        self.otra_decision('retype', new_type='CONTROLS', set={'note': 'NOTA_DECIDIDA_SINTETICA'})
        filas = self.suprimida_y_rectificada(U_PRIMERA, 19)
        start, end, _, props = viva(EDGES[U_PRIMERA])
        self.export([(start, end, 'CONTROLS', props)])
        code, out = self.snapshot(filas)
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0192: arista «rectificada» ausente del export', out)
        self.assertNotIn('NOTA_DECIDIDA_SINTETICA', out)
        self.export([(start, end, 'CONTROLS', {**props, 'note': 'NOTA_DECIDIDA_SINTETICA'})])
        code, out = self.snapshot(filas)
        self.assertEqual(code, 0, out)

    # --- enmienda 16 (spec 071 AC76–AC78, 12ª pasada de Codex) ------------------------------------------------------

    NOTA, OTRA = {'note': 'NOTA_DECIDIDA_SINTETICA'}, {'note': 'NOTA_EQUIVOCADA_SINTETICA'}

    def rectificada(self, rid, unidad):
        return [dict(fila(rid, unidad, decision='rectificado'), fecha='2026-10-08',
                     dato=f'({SOCIEDAD_A}) -[OWNS]-> ({SOCIEDAD_B}): arista corregida')]

    def regla_de_la_primera(self, accion, **fases):
        """La regla etiquetada que el apply escribe para la operación vigente de U_PRIMERA, ya preparada."""
        return {'source_id': SOCIEDAD_A, 'target_id': SOCIEDAD_B, 'type': 'OWNS', 'corrected_type': None,
                'swap': False, 'set': None, 'doc': 'informe-ficticio.pdf', 'quote_contains': PARTICIPA[:30],
                'kinds': None, 'reason': f'prueba [{U_PRIMERA[:8]}]: {accion}', **fases}

    def con_y_sin_overlay(self, filas, regla):
        """(exit, salida) del --snapshot sin las reglas del overlay y con ellas (la regla etiquetada, preparada)."""
        salidas = [self.snapshot(filas)]
        with mock.patch.object(RC, '_reglas_overlay', return_value=([], [regla])):
            salidas.append(self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                          '--overlay', str(self.base)))
        return salidas

    def test_a_correct_copy_does_not_excuse_another_in_the_decided_state_without_the_load(self):
        # Codex, 12ª pasada (AC76): retype OWNS→CONTROLS con su nota en el mapa `set`, regla preparada y, en el export,
        # DOS copias CONTROLS A→B con la misma firma: una con la nota decidida y otra con otra. La correcta acreditaba y
        # la otra no se miraba (`incompatibles` excluía todo el tipo nuevo en su sentido). Igual con un invert. Con la
        # correcta sola, pasa.
        filas = self.rectificada('R-2026-10-08-0201', U_PRIMERA)
        start, end, _, props = viva(EDGES[U_PRIMERA])
        for accion, campos, fases, (inicio, fin, tipo) in (
                ('retype', {'new_type': 'CONTROLS'}, {'corrected_type': 'CONTROLS'}, (start, end, 'CONTROLS')),
                ('invert', {}, {'swap': True}, (end, start, 'OWNS'))):
            self.otra_decision(accion, set=dict(self.NOTA), **campos)
            regla = self.regla_de_la_primera(accion, set=dict(self.NOTA), **fases)
            for caso, notas, codigo in (('una con la nota y otra con otra', (self.NOTA, self.OTRA), 1),
                                        ('solo la de la nota', (self.NOTA,), 0)):
                with self.subTest(accion=accion, caso=caso):
                    self.export([(inicio, fin, tipo, {**props, **nota}) for nota in notas])
                    for code, out in self.con_y_sin_overlay(filas, regla):
                        self.assertEqual(code, codigo, out)
                        self.assertNotIn('SINTETICA', out)
                        if codigo:
                            self.assertIn('REGISTRO R-2026-10-08-0201: arista «rectificada» viva sin corregir '
                                          '(1 copia; tolerancia 0)', out)

    def test_a_reidentified_invert_counts_its_copies_without_the_load(self):
        # AC76 en la rama re-identificada: un invert con mapa `set`; en el par actual, la copia invertida sin la nota
        # está sin corregir, haya o no otra con ella. Con la completa sola, pasa.
        u = 'f6' * 32
        valor = self.reidentificada(u, 'D-92', rtype='OWNS', quote=PARTICIPA)
        filas = self.rectificada('R-2026-10-08-0202', u)
        self.resolucion('D-93', u, 'invert', SOCIEDAD_A, set=dict(self.NOTA))
        regla = {'source_id': SOCIEDAD_A, 'target_id': self.NUEVA, 'type': 'OWNS', 'corrected_type': None, 'swap': True,
                 'set': dict(self.NOTA), 'doc': 'informe-ficticio.pdf', 'quote_contains': PARTICIPA[:30], 'kinds': None,
                 'reason': f'prueba [{u[:8]}]: invert sobre el par vigente'}
        for caso, notas, codigo in (('una con la nota y otra con otra', (self.NOTA, self.OTRA), 1),
                                    ('solo la de la nota', (self.NOTA,), 0)):
            with self.subTest(caso=caso):
                self.export([(self.NUEVA, SOCIEDAD_A, 'OWNS', {**valor['props'], **nota}) for nota in notas])
                with mock.patch.object(RC, '_reglas_overlay', return_value=([], [regla])):
                    code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                               '--overlay', str(self.base))
                self.assertEqual(code, codigo, out)
                self.assertNotIn('SINTETICA', out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-10-08-0202: arista «rectificada» viva sin corregir '
                                  '(par re-identificado; 1 copia; tolerancia 0)', out)

    def test_the_named_kept_twin_is_still_tolerated_but_not_a_copy_without_the_load(self):
        # Guarda de AC76: la tolerancia es la de hoy. La gemela conservada que nombra la decisión (el mismo hecho, con
        # su tipo del censo) sigue excusando una copia; la del tipo nuevo sin la nota no la excusa nadie.
        self.resolucion('D-94', U_GEMELA, 'retype', FULANA, new_type='MEMBER_OF', set=dict(self.NOTA),
                        reason=f'Idéntica a {U_CONSERVADA[:8]}…; se conserva aquella y esta se retipa.')
        filas = [dict(fila('R-2026-10-08-0203', U_GEMELA, decision='rectificado'),
                      dato=f'({FULANA}) -[WORKS_FOR]-> (Company:empresa-ficticia): arista corregida')]
        start, end, rtype, props = viva(EDGES[U_GEMELA])
        conservada, retipada = viva(EDGES[U_CONSERVADA]), (start, end, 'MEMBER_OF', {**props, **self.NOTA})
        self.export([conservada, retipada])
        code, out = self.snapshot(filas)
        self.assertEqual(code, 0, out)
        self.export([conservada, retipada, (start, end, 'MEMBER_OF', {**props, **self.OTRA})])
        code, out = self.snapshot(filas)
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-08-0203: arista «rectificada» viva sin corregir (2 copias; tolerancia 1)',
                      out)
        self.assertNotIn('SINTETICA', out)

    def test_a_symmetric_retype_with_its_load_passes_in_either_direction(self):
        # Guarda de AC76: el tipo nuevo simétrico vale en cualquier sentido (el colapso puede llevarlo a la canónica);
        # sin la nota, en cualquier sentido está sin corregir.
        self.otra_decision('retype', new_type='ASSOCIATED_WITH', set=dict(self.NOTA))
        filas = self.rectificada('R-2026-10-08-0204', U_PRIMERA)
        start, end, _, props = viva(EDGES[U_PRIMERA])
        for caso, nota, codigo in (('con la nota', self.NOTA, 0), ('sin la nota', {}, 1)):
            with self.subTest(caso=caso):
                self.export([(end, start, 'ASSOCIATED_WITH', {**props, **nota})])
                code, out = self.snapshot(filas)
                self.assertEqual(code, codigo, out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-10-08-0204: arista «rectificada» viva sin corregir (1 copia; '
                                  'tolerancia 0)', out)

    def test_the_load_the_export_must_show_is_the_loaders_with_its_derived_props(self):
        # Codex, 12ª pasada (AC77): la carga de la decisión se leía con `cast_value`, no con la preparación del loader,
        # que a partir de `date` escribe `date_norm` y `date_year` (lo que la API publica antes que `date`). Una copia
        # con la fecha decidida y las derivadas viejas, o sin ellas, acreditaba el retype.
        self.otra_decision('retype', new_type='CONTROLS', set={'date': '2026-01-01'})
        filas = self.rectificada('R-2026-10-08-0205', U_PRIMERA)
        start, end, _, props = viva(EDGES[U_PRIMERA])
        for caso, fecha, codigo in (
                ('derivadas viejas', {'date': '2026-01-01', 'date_norm': '1999-01-01', 'date_year': 1999}, 1),
                ('sin derivadas', {'date': '2026-01-01'}, 1),
                ('derivadas correctas', {'date': '2026-01-01', 'date_norm': '2026-01-01', 'date_year': 2026}, 0)):
            with self.subTest(caso=caso):
                self.export([(start, end, 'CONTROLS', {**props, **fecha})])
                code, out = self.snapshot(filas)
                self.assertEqual(code, codigo, out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-10-08-0205: arista «rectificada» viva sin corregir (1 copia; '
                                  'tolerancia 0)', out)

    def test_a_load_the_loader_rejects_is_a_decision_that_does_not_count(self):
        # AC78: una carga que el loader descarta no es «sin set». La decisión no vale: exit 2 con el fichero, la unidad,
        # el prop y el motivo, nunca el valor (que lleva un centinela).
        casos = (({'new_type': 'CONTROLS', 'set': {'source_page': '0', 'note': SENTINEL}},
                  'source_page: tiene que ser un entero ≥ 1'),
                 ({'new_type': 'CONTROLS', 'set': {'source_quote': SENTINEL}},
                  'source_quote: fuera de la lista cerrada del loader'),
                 ({'new_type': 'CONTROLS', 'set': {'source_kind': 'judicial', 'note': SENTINEL}},
                  'source_kind: solo puede degradar la capa de una arista judicial'),
                 ({'new_type': 'CONTROLS', 'set': {'status': SENTINEL}},
                  'status: fuera de los estados procesales de la ontología'),
                 ({'new_type': 'CONTROLS', 'set': {}}, 'el bloque `set` está vacío o no es un mapa'),
                 ({'new_type': 'CONTROLS', 'set': {'amount': SENTINEL}}, 'amount: no es un número'))
        for campos, motivo in casos:
            with self.subTest(motivo=motivo):
                self.otra_decision('retype', **campos)
                err = self.no_vale([fila('R-2026-10-03-0054', U_DEBIL)])
                self.assertEqual(err, f'operación con carga inválida (D-8.json, unidad {U_PRIMERA[:8]}: {motivo})')
                self.assertNotIn(SENTINEL, err)

    def test_a_date_that_is_not_iso_without_dateparser_stops_with_its_reason(self):
        # Condición del módulo compartido (enmienda 16): el entorno de la API, el del CI, no tiene dateparser. Una fecha
        # que no es ISO ni un año no se interpreta de otra manera: la decisión no vale, con el motivo explícito y nunca
        # como «error interno».
        self.otra_decision('retype', new_type='CONTROLS', set={'date': f'3 de marzo de 2021 {SENTINEL}'})
        with mock.patch.dict(sys.modules, {'dateparser': None}):
            err = self.no_vale([fila('R-2026-10-03-0054', U_DEBIL)])
        self.assertEqual(err, f'operación con carga inválida (D-8.json, unidad {U_PRIMERA[:8]}: date: una fecha que '
                              'no es ISO ni un año solo la interpreta dateparser, que este entorno no tiene)')
        self.assertNotIn(SENTINEL, err)

    # --- enmienda 17 (spec 071 AC80–AC83, 13ª pasada de Codex) ------------------------------------------------------

    PRENSA = {'source_kind': 'prensa'}

    def test_a_half_transformed_copy_does_not_hide_behind_the_decided_signature(self):
        # Codex, 13ª pasada (AC80): retype OWNS→CONTROLS con `set={source_kind: prensa}`. `_decididas` buscaba las
        # copias del estado decidido con la firma de después de la carga: la CONTROLS que conserva la capa judicial
        # quedaba fuera y nunca llegaba a `_lleva`, sola o junto a otra bien degradada, y seguía presentando respaldo
        # judicial. Igual con un invert. Con la degradada sola, pasa.
        filas = self.rectificada('R-2026-10-08-0211', U_PRIMERA)
        start, end, _, props = viva(EDGES[U_PRIMERA])
        for accion, campos, fases, (inicio, fin, tipo) in (
                ('retype', {'new_type': 'CONTROLS'}, {'corrected_type': 'CONTROLS'}, (start, end, 'CONTROLS')),
                ('invert', {}, {'swap': True}, (end, start, 'OWNS'))):
            self.otra_decision(accion, set=dict(self.PRENSA), **campos)
            regla = self.regla_de_la_primera(accion, set=dict(self.PRENSA), **fases)
            for caso, capas, codigo in (('solo la que conserva la capa', ('judicial',), 1),
                                        ('la que conserva la capa junto a la degradada', ('judicial', 'prensa'), 1),
                                        ('solo la degradada', ('prensa',), 0)):
                with self.subTest(accion=accion, caso=caso):
                    self.export([(inicio, fin, tipo, {**props, 'source_kind': capa}) for capa in capas])
                    for code, out in self.con_y_sin_overlay(filas, regla):
                        self.assertEqual(code, codigo, out)
                        if codigo:
                            self.assertIn('REGISTRO R-2026-10-08-0211: arista «rectificada» viva sin corregir '
                                          '(1 copia; tolerancia 0)', out)

    def test_a_half_transformed_copy_on_a_reidentified_pair_is_alive(self):
        # Guarda de AC80 en la rama re-identificada: la copia del tipo nuevo en el par actual se busca por documento y
        # cita, sin la capa; la que conserva la judicial no lleva la carga.
        u = 'e3' * 32
        valor = self.reidentificada(u, 'D-95', rtype='OWNS', quote=PARTICIPA)
        self.resolucion('D-96', u, 'retype', SOCIEDAD_A, new_type='CONTROLS', set=dict(self.PRENSA))
        regla = {'source_id': SOCIEDAD_A, 'target_id': self.NUEVA, 'type': 'OWNS', 'corrected_type': 'CONTROLS',
                 'swap': False, 'set': dict(self.PRENSA), 'doc': 'informe-ficticio.pdf',
                 'quote_contains': PARTICIPA[:30], 'kinds': None,
                 'reason': f'prueba [{u[:8]}]: retipo sobre el par vigente'}
        filas = self.rectificada('R-2026-10-08-0212', u)
        for caso, capas, codigo in (('junto a la degradada', ('judicial', 'prensa'), 1),
                                    ('solo la degradada', ('prensa',), 0)):
            with self.subTest(caso=caso):
                self.export([(SOCIEDAD_A, self.NUEVA, 'CONTROLS', {**valor['props'], 'source_kind': capa})
                             for capa in capas])
                with mock.patch.object(RC, '_reglas_overlay', return_value=([], [regla])):
                    code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                               '--overlay', str(self.base))
                self.assertEqual(code, codigo, out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-10-08-0212: arista «rectificada» viva sin corregir '
                                  '(par re-identificado; 1 copia; tolerancia 0)', out)

    def test_a_rectified_props_correction_is_checked_on_every_copy(self):
        # AC83: el check del registro no evaluaba las filas «rectificado» de un set_prop (con la regla y la fila de
        # f012bc19, `materializacion` contaba la fecha sin corregir y este check no la mencionaba). Cada copia de la
        # arista lleva la carga: la que no, viva sin corregir, haya otra que sí o no. Y un set de capa sin retype
        # (AC80): la copia que conserva la judicial, también. Sin ninguna copia, la fila afirma una corrección que no se
        # publica (AC100).
        filas = self.rectificada('R-2026-10-08-0213', U_PRIMERA)
        start, end, rtype, props = viva(EDGES[U_PRIMERA])
        for campos, carga, sin in (({'prop': 'note', 'value': self.NOTA['note']}, self.NOTA, self.OTRA),
                                   ({'set': dict(self.PRENSA)}, self.PRENSA, {'source_kind': 'judicial'})):
            self.otra_decision('set_prop', **campos)
            for caso, copias, codigo in (('sin la carga', (sin,), 1), ('con la carga', (carga,), 0),
                                         ('una con la carga y otra sin ella', (carga, sin), 1), ('sin copias', (), 1)):
                with self.subTest(carga=sorted(carga), caso=caso):
                    self.export([viva(EDGES[U_VIVA]), *[(start, end, rtype, {**props, **c}) for c in copias]],
                                nodos=(SOCIEDAD_A, SOCIEDAD_B))
                    code, out = self.snapshot(filas)
                    self.assertEqual(code, codigo, out)
                    self.assertNotIn('SINTETICA', out)
                    if codigo:
                        self.assertIn('REGISTRO R-2026-10-08-0213: rectificación decidida sin arista corregida'
                                      if not copias else
                                      'REGISTRO R-2026-10-08-0213: arista «rectificada» viva sin corregir (1 copia; '
                                      'tolerancia 0)', out)

    def test_an_accusation_correction_is_checked_on_its_copy_by_its_loader_identity(self):
        # Codex, 13ª pasada (AC81, con AC83): una acusación de la capa `acusacion` con un set_prop de nota. Su
        # cargador sobrescribe la cita y el documento sobre la misma arista: con otra cita u otro documento sigue siendo
        # ella; sin la nota, viva sin corregir; con ella, nada. La judicial del mismo par y tipo, desde otro documento,
        # no es ella.
        u = 'e4' * 32
        acusacion = arista(FULANA, DELITO, rtype='ACCUSED_OF', kind='acusacion', doc='auto-ficticio.pdf', quote=FALLO)
        self.unidades({u: acusacion}, entidad=FULANA, pack='E-fulana-decimoseptima')
        self.resolucion('D-97', u, 'set_prop', FULANA, prop='note', value=self.NOTA['note'])
        filas = self.rectificada('R-2026-10-08-0214', u)
        start, end, rtype, props = viva(acusacion)
        judicial = (start, end, rtype, {'source_kind': 'judicial', 'source_doc': 'sentencia-ficticia.pdf',
                                        'source_quote': 'Fulana, acusada en la sentencia ficticia'})
        for caso, otra, nota, codigo in (('otra cita, sin la nota', {'source_quote': 'Fulana, acusada'}, {}, 1),
                                         ('otro documento, sin la nota', {'source_doc': 'otro-auto.pdf'}, {}, 1),
                                         ('otra cita, con la nota', {'source_quote': 'Fulana, acusada'}, self.NOTA, 0)):
            with self.subTest(caso=caso):
                self.export([(start, end, rtype, {**props, **otra, **nota}), judicial])
                code, out = self.snapshot(filas)
                self.assertEqual(code, codigo, out)
                self.assertNotIn('SINTETICA', out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-10-08-0214: arista «rectificada» viva sin corregir (1 copia; '
                                  'tolerancia 0)', out)

    def test_a_reidentified_props_correction_is_checked_on_its_current_pair(self):
        # AC83 con AC81: re-identificada, la copia se busca en el par actual que dice la regla del overlay.
        u = 'e5' * 32
        valor = self.reidentificada(u, 'D-98', quote=CONTROL)
        self.resolucion('D-99', u, 'set_prop', SOCIEDAD_A, prop='note', value=self.NOTA['note'])
        regla = {'source_id': SOCIEDAD_A, 'target_id': self.NUEVA, 'type': 'CONTROLS', 'corrected_type': None,
                 'swap': False, 'set': dict(self.NOTA), 'doc': 'informe-ficticio.pdf', 'quote_contains': CONTROL[:30],
                 'kinds': None, 'reason': f'prueba [{u[:8]}]: nota sobre el par vigente'}
        filas = self.rectificada('R-2026-10-08-0215', u)
        for caso, nota, codigo in (('sin la nota', {}, 1), ('con la nota', self.NOTA, 0)):
            with self.subTest(caso=caso):
                self.export([(SOCIEDAD_A, self.NUEVA, 'CONTROLS', {**valor['props'], **nota})])
                with mock.patch.object(RC, '_reglas_overlay', return_value=([], [regla])):
                    code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                               '--overlay', str(self.base))
                self.assertEqual(code, codigo, out)
                if codigo:
                    self.assertIn('REGISTRO R-2026-10-08-0215: arista «rectificada» viva sin corregir '
                                  '(par re-identificado; 1 copia; tolerancia 0)', out)

    def test_an_unknown_prop_is_named_by_its_position_never_by_its_name(self):
        # Codex, 13ª pasada (AC82): `_NOMBRE_PROP` aceptaba cualquier identificador, así que una clave con forma de
        # nombre salía entera en stderr. Solo se nombran las del vocabulario cerrado; cualquier otra, por su posición en
        # el bloque. Receta de AC66/AC72: dentro de otra suite, ni su informe completo ni stdout ni stderr llevan el
        # centinela.
        self.registro.write_text(yaml.safe_dump({'rectificaciones': [fila('R-2026-10-03-0054', U_DEBIL)]},
                                                allow_unicode=True, sort_keys=False), encoding='utf-8')
        argv = ['--registro', str(self.registro), '--snapshot', str(self.snap), '--root', str(self.root),
                '--overlay', str(self.sin_reglas)]
        for campos, nombre in (({'prop': SENTINEL, 'value': 'x'}, 'prop desconocido (posición 1)'),
                               ({'set': {'note': 'x', SENTINEL: 'y'}}, 'prop desconocido (posición 2)')):
            with self.subTest(nombre=nombre):
                self.otra_decision('set_prop', **campos)

                class Snapshot(unittest.TestCase):
                    def test_snapshot(self):
                        RC.main(argv)

                informe, salida, errores = io.StringIO(), io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(errores):
                    resultado = unittest.TextTestRunner(stream=informe, verbosity=2).run(Snapshot('test_snapshot'))
                self.assertEqual(len(resultado.errors), 1)  # el SystemExit(2) de la decisión que no vale
                todo = salida.getvalue() + errores.getvalue() + informe.getvalue()
                self.assertNotIn(SENTINEL, todo)
                self.assertIn(f'operación con carga inválida (D-8.json, unidad {U_PRIMERA[:8]}: {nombre}: fuera de la '
                              'lista cerrada del loader)', todo)
    # --- enmienda 18 (spec 071 AC84–AC88, 14ª pasada de Codex) ------------------------------------------------------

    def test_the_old_type_or_direction_with_the_new_layer_is_alive(self):
        # Codex, 14ª pasada (AC84): retype OWNS→CONTROLS con `set={source_kind: prensa}`. `incompatibles` buscaba bajo
        # el tipo antiguo solo las firmas que no cambia la carga: la OWNS que ya lleva la capa nueva pasaba, sola o
        # junto a la CONTROLS correcta, y la arista seguía publicada con su tipo antiguo. Igual con un invert y la copia
        # sin invertir que ya lleva la capa nueva. La correcta sola, pasa.
        filas = self.rectificada('R-2026-10-08-0221', U_PRIMERA)
        start, end, rtype, props = viva(EDGES[U_PRIMERA])
        prensa = {**props, **self.PRENSA}
        for accion, campos, fases, correcta in (
                ('retype', {'new_type': 'CONTROLS'}, {'corrected_type': 'CONTROLS'}, (start, end, 'CONTROLS', prensa)),
                ('invert', {}, {'swap': True}, (end, start, rtype, prensa))):
            self.otra_decision(accion, set=dict(self.PRENSA), **campos)
            regla = self.regla_de_la_primera(accion, set=dict(self.PRENSA), **fases)
            antigua = (start, end, rtype, prensa)
            for caso, export, codigo in (('la antigua con la capa nueva', [antigua], 1),
                                         ('la antigua junto a la correcta', [antigua, correcta], 1),
                                         ('la correcta sola', [correcta], 0)):
                with self.subTest(accion=accion, caso=caso):
                    self.export(export)
                    for code, out in self.con_y_sin_overlay(filas, regla):
                        self.assertEqual(code, codigo, out)
                        if codigo:
                            self.assertIn('REGISTRO R-2026-10-08-0221: arista «rectificada» viva sin corregir '
                                          '(1 copia; tolerancia 0)', out)

    def test_a_reidentified_accusation_with_another_quote_and_a_wrong_load_is_alive(self):
        # Codex, 14ª pasada (AC85): una acusación de la capa `acusacion` retipada CONVICTED_OF→ACCUSED_OF con su nota y
        # su persona re-identificada; en el par actual, la copia con la cita del censo y la nota, y otra con la cita
        # cambiada (la misma arista para su cargador) y otra nota. `sin_su_carga` las filtraba por documento y cita: la
        # segunda no se miraba y el check daba 0, cuando sin re-identificar el mismo caso da 1. Una sola identificación:
        # la del cargador. Con la cita cambiada y la nota decidida, pasa.
        antes = 'Person:fulana-de-antes-ficticia'
        cambiada = {'source_quote': 'Fulana, condenada en otra pieza ficticia'}
        for caso, inicio, u, problema in (('re-identificada', antes, 'e6' * 32, 'par re-identificado; 1 copia'),
                                          ('sin re-identificar', FULANA, 'e8' * 32, '1 copia')):
            acusacion = arista(inicio, DELITO, rtype='CONVICTED_OF', kind='acusacion', doc='auto-ficticio.pdf',
                               quote=FALLO)
            self.unidades({u: acusacion}, entidad=inicio, pack=f'E-decimoctava-{u[:4]}')
            self.resolucion(f'D-{u[:4]}', u, 'retype', inicio, new_type='ACCUSED_OF', set=dict(self.NOTA))
            regla = {'source_id': FULANA, 'target_id': DELITO, 'type': 'CONVICTED_OF', 'corrected_type': 'ACCUSED_OF',
                     'swap': False, 'set': dict(self.NOTA), 'doc': 'auto-ficticio.pdf', 'quote_contains': FALLO[:30],
                     'kinds': ['acusacion'], 'reason': f'prueba [{u[:8]}]: retipo sobre el par vigente'}
            filas = self.rectificada('R-2026-10-08-0222', u)
            _, _, _, props = viva(acusacion)
            correcta = (FULANA, DELITO, 'ACCUSED_OF', {**props, **self.NOTA})
            for export, codigo in (([correcta, (FULANA, DELITO, 'ACCUSED_OF', {**props, **cambiada, **self.OTRA})], 1),
                                   ([(FULANA, DELITO, 'ACCUSED_OF', {**props, **cambiada, **self.NOTA})], 0)):
                with self.subTest(caso=caso, copias=len(export)):
                    self.export(export)
                    with mock.patch.object(RC, '_reglas_overlay', return_value=([], [regla])):
                        code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                                   '--overlay', str(self.base))
                    self.assertEqual(code, codigo, out)
                    self.assertNotIn('SINTETICA', out)
                    if codigo:
                        self.assertIn(f'REGISTRO R-2026-10-08-0222: arista «rectificada» viva sin corregir '
                                      f'({problema}; tolerancia 0)', out)

    def test_a_props_correction_whose_rule_keeps_the_old_pair_is_found_by_its_signature(self):
        # Codex, 14ª pasada (AC86): set_prop de nota sobre una arista re-identificada cuya regla sigue apuntando al par
        # del censo, o sin regla. La copia se buscaba solo en los pares que deduce el overlay: la del par actual, sin la
        # nota, no se veía y la corrección pasaba por «sin copias». Se busca también por documento y cita en todo el
        # export: viva sin corregir en un par distinto del de la regla; con la regla alineada, la misma copia; con la
        # nota, nada. La sociedad B está en el export: sin ella, las dos OWNS del censo entre A y B con esa cita
        # tendrían un extremo fuera y la copia que comparte A sería ambigua (AC91).
        u = 'e7' * 32
        valor = self.reidentificada(u, 'D-e7e7', quote=CONTROL)
        self.resolucion('D-e7e8', u, 'set_prop', SOCIEDAD_A, prop='note', value=self.NOTA['note'])
        filas = self.rectificada('R-2026-10-08-0223', u)
        regla = {'source_id': SOCIEDAD_A, 'type': 'CONTROLS', 'corrected_type': None, 'swap': False,
                 'set': dict(self.NOTA), 'doc': 'informe-ficticio.pdf', 'quote_contains': CONTROL[:30], 'kinds': None,
                 'reason': f'prueba [{u[:8]}]: nota'}
        fuera = 'par re-identificado; par distinto del de la regla; 1 copia'
        vieja, alineada = dict(regla, target_id=self.ANTES), dict(regla, target_id=self.NUEVA)
        for caso, reglas, nota, problema in (('regla en el par del censo', [vieja], {}, fuera),
                                             ('sin regla', [], {}, fuera),
                                             ('regla alineada', [alineada], {}, 'par re-identificado; 1 copia'),
                                             ('regla alineada, con la nota', [alineada], self.NOTA, None)):
            with self.subTest(caso=caso):
                self.export([(SOCIEDAD_A, self.NUEVA, 'CONTROLS', {**valor['props'], **nota})], nodos=(SOCIEDAD_B,))
                with mock.patch.object(RC, '_reglas_overlay', return_value=([], reglas)):
                    code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                               '--overlay', str(self.base))
                self.assertEqual(code, 1 if problema else 0, out)
                self.assertNotIn('SINTETICA', out)
                if problema:
                    self.assertIn(f'REGISTRO R-2026-10-08-0223: arista «rectificada» viva sin corregir ({problema}; '
                                  'tolerancia 0)', out)

    def test_a_layer_correction_completing_a_withdrawal_is_published_in_its_resulting_layer(self):
        # Codex, 14ª pasada (AC88): «suprimido» completada por una «rectificado» y un set_prop vigente que degrada la
        # capa a prensa, publicada así en el export: `_sigue_publicada` comparaba la capa del censo y daba «ausente»,
        # cuando `materializacion` daba 0. Se compara con el estado resultante: la degradada está publicada; la que aún
        # es judicial también lo está, sin corregir. Una conservación (keep), como hoy: con la capa del censo.
        self.otra_decision('set_prop', set=dict(self.PRENSA))
        filas = self.suprimida_y_rectificada(U_PRIMERA, 22)
        start, end, rtype, props = viva(EDGES[U_PRIMERA])
        for caso, capa, problema in (('degradada', 'prensa', None),
                                     ('aún judicial', 'judicial', 'arista «rectificada» viva sin corregir (1 copia; '
                                                                  'tolerancia 0)')):
            with self.subTest(caso=caso):
                self.export([(start, end, rtype, {**props, 'source_kind': capa})])
                code, out = self.snapshot(filas)
                self.assertEqual(code, 1 if problema else 0, out)
                self.assertNotIn('ausente del export', out)
                if problema:
                    self.assertIn(f'REGISTRO R-2026-10-07-0222: {problema}', out)
        self.otra_decision('keep')
        self.export([(start, end, rtype, {**props, 'source_kind': 'prensa'})])
        code, out = self.snapshot(filas)
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-10-07-0222: arista «rectificada» ausente del export', out)

    # --- ampliación de la enmienda 18 (spec 071 AC89) -----------------------------------------------------------------

    D, E, F, G = ('Company:sociedad-d-ficticia', 'Company:sociedad-e-ficticia', 'Company:sociedad-f-ficticia',
                  'Company:sociedad-g-ficticia')
    PASAJE = 'la sociedad D participa en la E según un pasaje ficticio'

    def pasaje(self, start, end, rtype='OWNS'):
        """Una arista del mismo pasaje ficticio: mismo documento y misma cita."""
        return arista(start, end, rtype=rtype, doc='informe-ficticio.pdf', quote=self.PASAJE)

    def test_a_retype_or_an_invert_is_also_judged_outside_the_pairs_of_its_rule(self):
        # AC89: en su par del censo no queda ninguna copia y la regla apunta a ese par (o no hay regla). La búsqueda
        # libre la encuentra en otro par que comparte exactamente un extremo, con el sentido que da su posición: con el
        # tipo antiguo (o sin invertir) o en el estado decidido sin su carga, viva sin corregir en un par distinto del
        # de la regla; la correcta se acepta; la que no comparte ningún extremo es otro hecho del pasaje y no cuenta
        # (junto a la correcta: sin ella, falta la arista corregida, AC92).
        u = 'e9' * 32
        self.unidades({u: self.pasaje(self.D, self.E)}, pack='E-decimoctava-libre')
        filas = self.rectificada('R-2026-10-08-0231', u)
        props = viva(self.pasaje(self.D, self.E))[3]
        con, sin = {**props, **self.NOTA}, {**props, **self.OTRA}
        fuera = 'arista «rectificada» viva sin corregir (par distinto del de la regla; 1 copia; tolerancia 0)'
        for accion, campos, fases, decidida in (
                ('retype', {'new_type': 'CONTROLS'}, {'corrected_type': 'CONTROLS'},
                 lambda carga: (self.D, self.F, 'CONTROLS', carga)),
                ('invert', {}, {'swap': True}, lambda carga: (self.F, self.D, 'OWNS', carga))):
            self.resolucion(f'D-{u[:4]}', u, accion, SOCIEDAD_A, set=dict(self.NOTA), **campos)
            regla = {'source_id': self.D, 'target_id': self.E, 'type': 'OWNS', 'corrected_type': None, 'swap': False,
                     'set': dict(self.NOTA), 'doc': 'informe-ficticio.pdf', 'quote_contains': self.PASAJE[:30],
                     'kinds': None, 'reason': f'prueba [{u[:8]}]: {accion}', **fases}
            for caso, copias, problema in (('la antigua en otro par', [(self.D, self.F, 'OWNS', props)], fuera),
                                           ('la decidida sin su carga en otro par', [decidida(sin)], fuera),
                                           ('la correcta en otro par', [decidida(con)], None),
                                           ('sin extremo compartido', [(self.G, self.F, 'OWNS', props), decidida(con)],
                                            None)):
                with self.subTest(accion=accion, caso=caso):
                    self.export(copias, nodos=(self.D, self.E))
                    for code, out in self.con_y_sin_overlay(filas, regla):
                        self.assertEqual(code, 1 if problema else 0, out)
                        self.assertNotIn('SINTETICA', out)
                        if problema:
                            self.assertIn(f'REGISTRO R-2026-10-08-0231: {problema}', out)

    def test_a_copy_that_is_another_edge_of_the_census_is_not_attributed(self):
        # AC89 (corrección): la firma documental no es identidad. Con el extremo del censo re-identificado y sin regla,
        # la búsqueda por firma atribuía a la arista cualquier copia del pasaje en cualquier par. Ahora no cuenta la que
        # no comparte ningún extremo con ella, ni la que es otra arista del censo de su firma: en su forma censal, ni en
        # la que le deja su operación vigente (un retype ya hecho por la otra unidad). La del tipo antiguo que sí es
        # suya en otro par sigue viva. Con la suya correcta en el export (sin ella, falta la arista corregida, AC92) y
        # los extremos de las otras (sin ellos, cualquier copia que comparta el que les queda sería ambigua, AC91).
        u, otra, retipada = 'ea' * 32, 'eb' * 32, 'ec' * 32
        valor = self.reidentificada(u, 'D-eaea', rtype='OWNS', quote=self.PASAJE)
        self.unidades({otra: self.pasaje(SOCIEDAD_A, self.F), retipada: self.pasaje(SOCIEDAD_A, self.G, rtype='PAID')},
                      pack='E-decimoctava-ajenas')
        self.resolucion('D-eaeb', retipada, 'retype', SOCIEDAD_A, new_type='OWNS')
        props = dict(valor['props'])
        for accion, campos, suya in (('retype', {'new_type': 'CONTROLS'}, (SOCIEDAD_A, self.NUEVA, 'CONTROLS', props)),
                                     ('invert', {}, (self.NUEVA, SOCIEDAD_A, 'OWNS', props))):
            self.resolucion('D-eaec', u, accion, SOCIEDAD_A, **campos)
            filas = self.rectificada('R-2026-10-08-0232', u)
            for caso, copias, problema in (
                    ('otra arista, en su forma censal', [(SOCIEDAD_A, self.F, 'OWNS', props)], None),
                    ('otra arista, en la forma que le deja su retype', [(SOCIEDAD_A, self.G, 'OWNS', props)], None),
                    ('sin extremo compartido', [(self.G, self.F, 'OWNS', props)], None),
                    ('la suya, sin retipar ni invertir', [(SOCIEDAD_A, self.NUEVA, 'OWNS', props)],
                     'par re-identificado; par distinto del de la regla; 1 copia')):
                with self.subTest(accion=accion, caso=caso):
                    self.export([suya, *copias], nodos=(SOCIEDAD_A, self.F, self.G))
                    code, out = self.snapshot(filas)
                    self.assertEqual(code, 1 if problema else 0, out)
                    if problema:
                        self.assertIn(f'REGISTRO R-2026-10-08-0232: arista «rectificada» viva sin corregir '
                                      f'({problema}; tolerancia 0)', out)

    # --- segunda ampliación de la enmienda 18 (spec 071 AC91–AC93) ---------------------------------------------------

    def test_a_copy_that_can_also_be_another_reidentified_edge_is_ambiguous(self):
        # AC91: en su par del censo no queda ninguna copia y la búsqueda libre halla una correcta en otro par; otra
        # arista del censo del mismo pasaje tiene un extremo fuera del export y comparte con la copia el que le queda.
        # Se le atribuía y el check pasaba; ahora no se atribuye a ninguna: «copia ambigua entre unidades», en un retype
        # y en un set_prop, sin sumarle la falta de la arista corregida (AC92), siempre que la otra pueda producirla
        # (AC98: en el retype, con el suyo al mismo tipo). Controles: con los dos extremos de la otra en el export, rige
        # el criterio (ii) de AC89 y la copia es suya; si la otra no puede tener su tipo, también.
        u, otra = 'f2' * 32, 'f3' * 32
        self.unidades({u: self.pasaje(self.D, self.E), otra: self.pasaje(self.G, self.E)}, pack='E-ampliacion-ambigua')
        filas = self.rectificada('R-2026-10-08-0241', u)
        props = viva(self.pasaje(self.D, self.E))[3]
        for accion, campos, copia, la_otra in (
                ('retype', {'new_type': 'CONTROLS', 'set': dict(self.NOTA)},
                 (self.F, self.E, 'CONTROLS', {**props, **self.NOTA}), {'new_type': 'CONTROLS'}),
                ('retype', {'new_type': 'CONTROLS', 'set': dict(self.NOTA)},
                 (self.F, self.E, 'CONTROLS', {**props, **self.NOTA}), None),
                ('set_prop', {'prop': 'note', 'value': self.NOTA['note']},
                 (self.F, self.E, 'OWNS', {**props, **self.NOTA}), None)):
            self.resolucion(f'D-{u[:4]}', u, accion, SOCIEDAD_A, **campos)
            (self.root / f'esc/decisions/D-{otra[:4]}.json').unlink(missing_ok=True)
            if la_otra:
                self.resolucion(f'D-{otra[:4]}', otra, 'retype', SOCIEDAD_A, **la_otra)
            puede = accion == 'set_prop' or la_otra is not None
            for caso, nodos, codigo in (('la otra, con un extremo fuera del export', (self.D, self.E), int(puede)),
                                        ('la otra, con los dos extremos en el export', (self.D, self.E, self.G), 0)):
                with self.subTest(accion=accion, la_otra_puede_ser_la_copia=puede, caso=caso):
                    self.export([copia], nodos=nodos)
                    code, out = self.snapshot(filas)
                    self.assertEqual(code, codigo, out)
                    self.assertNotIn('SINTETICA', out)
                    self.assertEqual(out.count('REGISTRO'), codigo, out)
                    if codigo:
                        self.assertIn(f'REGISTRO R-2026-10-08-0241: copia ambigua entre unidades ({u[:8]}, {otra[:8]})',
                                      out)

    # --- enmienda 19 (spec 071 AC94 y AC101, 15ª pasada de Codex) ---------------------------------------------------

    def test_a_correct_copy_does_not_hide_a_wrong_one_in_a_pair_its_split_declares(self):
        # Codex, 15ª pasada (H1, AC94): con la copia correcta en su par, una del tipo antiguo en otro par que comparte
        # un extremo es suya solo si el overlay declara la re-identificación (aquí, una escisión que liga E con F,
        # simulada como la prepara `qa_esc_apply._reidentificaciones`): viva sin corregir. Sin declarar, otro hecho del
        # pasaje: un aviso, y el check pasa. Sin la correcta, la del otro par es suya (AC89).
        u = 'b5' * 32
        self.unidades({u: self.pasaje(self.D, self.E)}, pack='E-diecinueve-escision')
        self.resolucion(f'D-{u[:4]}', u, 'retype', SOCIEDAD_A, new_type='CONTROLS')
        filas = self.rectificada('R-2026-10-08-0251', u)
        props = viva(self.pasaje(self.D, self.E))[3]
        buena, mala = (self.D, self.E, 'CONTROLS', props), (self.D, self.F, 'OWNS', props)
        viva_fuera = 'REGISTRO R-2026-10-08-0251: arista «rectificada» viva sin corregir (par distinto del de la ' \
                     'regla; 1 copia; tolerancia 0)'
        aviso = (f'registro-check: aviso R-2026-10-08-0251: copia del mismo pasaje en otro par, hecho no atribuido '
                 f'({u[:8]})')
        for caso, enlaces, export, codigo, linea in (
                ('escisión declarada', {frozenset((self.E, self.F))}, [buena, mala], 1, viva_fuera),
                ('sin declarar', set(), [buena, mala], 0, aviso),
                ('sin la correcta', set(), [mala], 1, viva_fuera)):
            with self.subTest(caso=caso):
                self.export(export, nodos=(self.D, self.E))
                with mock.patch.object(RC, '_reglas_overlay', return_value=([], [])), \
                        mock.patch.object(RC, '_enlaces_overlay', return_value=frozenset(enlaces)):
                    code, out = self.run_check(filas, '--snapshot', str(self.snap), '--root', str(self.root),
                                               '--overlay', str(self.base))
                self.assertEqual(code, codigo, out)
                self.assertIn(linea, out)

    def test_a_feed_layer_edge_is_found_by_its_loader_key_with_a_rewritten_quote(self):
        # AC101: los cargadores de prensa, última hora y contexto recrean la arista por par, tipo y capa y sobrescriben
        # la cita: una retirada viva con la cita reescrita en el feed se daba por hecha. Por la clave de su cargador,
        # sigue viva. Codex, 16ª pasada (16b-H2, AC110): también con documento, que el cargador fusiona por esa clave
        # sobre la que ya está y puede recrearla tras el deny; este test exigía ese falso verde.
        for n, (capa, documento) in enumerate((('prensa', None), ('ultima_hora', None), ('contexto', None),
                                                ('prensa', 'informe-ficticio.pdf'),
                                                ('ultima_hora', 'informe-ficticio.pdf'))):
            u, empresa = ('b6', 'b7', 'b8', 'b9', 'bf')[n] * 32, f'Company:feed-ficticia-{n}'
            rid = f'R-2026-10-08-026{n}'
            self.unidades({u: arista(FULANA, empresa, rtype='ASSOCIATED_WITH', kind=capa, doc=documento,
                                     quote='cita original del feed ficticio')}, pack=f'E-diecinueve-feed-{n}')
            self.decision(f'D-feed-{n}', u, 'retirada de la capa')
            reescrita = {'source_kind': capa, 'source_quote': 'cita reescrita del feed ficticio',
                         **({'source_doc': documento} if documento else {})}
            with self.subTest(capa=capa, documento=bool(documento)):
                self.export([viva(EDGES[U_VIVA]), (FULANA, empresa, 'ASSOCIATED_WITH', reescrita)])
                code, out = self.snapshot([fila(rid, u)])
                self.assertEqual(code, 1, out)
                self.assertIn(f'REGISTRO {rid}: arista «suprimida» viva (1 copia; tolerancia 0)', out)

    def test_a_rectified_retype_or_invert_without_its_corrected_edge_is_a_problem(self):
        # AC92: la «rectificado» de un retype o un invert afirma la arista corregida; sin ninguna copia en el estado
        # resultante (ni en su par, ni en otro) el registro no decía nada. Ahora: «rectificación decidida sin arista
        # corregida». Con la copia aún en el estado anterior manda la viva sin corregir; con la correcta, nada; la que
        # completa una «suprimido» conserva su motivo (AC69).
        start, end, rtype, props = viva(EDGES[U_PRIMERA])
        rectificada = self.rectificada('R-2026-10-08-0242', U_PRIMERA)
        for accion, campos, correcta in (('retype', {'new_type': 'CONTROLS'}, (start, end, 'CONTROLS', props)),
                                         ('invert', {}, (end, start, rtype, props))):
            self.otra_decision(accion, **campos)
            for caso, filas, export, problema in (
                    ('sin ninguna copia', rectificada, [],
                     'REGISTRO R-2026-10-08-0242: rectificación decidida sin arista corregida'),
                    ('con la copia en el estado anterior', rectificada, [viva(EDGES[U_PRIMERA])],
                     'REGISTRO R-2026-10-08-0242: arista «rectificada» viva sin corregir (1 copia; tolerancia 0)'),
                    ('con la correcta', rectificada, [correcta], None),
                    ('completa una «suprimido»', self.suprimida_y_rectificada(U_PRIMERA, 24), [],
                     'REGISTRO R-2026-10-07-0242: arista «rectificada» ausente del export')):
                with self.subTest(accion=accion, caso=caso):
                    self.export([viva(EDGES[U_VIVA]), *export], nodos=(SOCIEDAD_A, SOCIEDAD_B))
                    code, out = self.snapshot(filas)
                    self.assertEqual(code, 1 if problema else 0, out)
                    if problema:
                        self.assertIn(problema, out)
                    self.assertEqual('sin arista corregida' in out, caso == 'sin ninguna copia', out)

    def test_a_withdrawn_row_on_an_edge_its_decision_keeps_contradicts_it(self):
        # AC93: una «suprimido» vigente sobre una arista cuya operación vigente la conserva (un retype, un invert, un
        # set_prop o un keep) contradice la decisión, la retire lo que la retire; sin copia viva, el check la daba por
        # retirada. Un set_status o un deny con su «suprimido», no.
        filas = [fila('R-2026-10-08-0243', U_PRIMERA)]
        self.export([viva(EDGES[U_VIVA])], nodos=(SOCIEDAD_A, SOCIEDAD_B))
        for accion, campos, contradice in (('retype', {'new_type': 'CONTROLS'}, True), ('invert', {}, True),
                                           ('set_prop', {'prop': 'note', 'value': 'nota ficticia'}, True),
                                           ('keep', {}, True), ('set_status', {'status': 'investigado'}, False),
                                           ('deny', {}, False)):
            with self.subTest(accion=accion):
                self.otra_decision(accion, **campos)
                code, out = self.snapshot(filas)
                self.assertEqual(code, 1 if contradice else 0, out)
                self.assertEqual(f'REGISTRO R-2026-10-08-0243: fila contradice la decisión (suprimido; decisión: '
                                 f'{accion})' in out, contradice, out)
                self.assertEqual(out.count('REGISTRO'), int(contradice), out)


def huella_de_firma(cita):
    """La huella de AC284, calculada aquí: sha256 de la cita de la firma (normalizada y recortada a 300)."""
    return hashlib.sha256(RC._cita(cita).encode('utf-8')).hexdigest()


class MigracionDeCitaTest(RegistroCheckBase):
    """Spec 071 AC284–AC289 (enmienda 44): una migración del Path A reescribe la cita de la arista de una unidad con un
    set_prop «rectificado» (el caso 7b5fac52 de la fusión con el 070). Su copia corregida, con la cita nueva, ya no tiene
    la firma del censo: sin declaración, «sin arista corregida» para siempre. La declaración privada de su unidad
    (`esc/migraciones_citas.yaml`, solo huellas) la identifica a ella y a nadie más; una que no vale no cuenta y es un
    problema, sin valores."""

    PERSONA, EMPRESA = 'Person:persona-migrada-ficticia', 'Company:empresa-migrada-ficticia'
    OTRA, OTRA_PERSONA = 'Company:otra-empresa-ficticia', 'Person:otra-persona-ficticia'
    DOC = 'auto-migrado-ficticio.pdf'
    GUARDADA = 'La persona migrada ficticia recibió de la empresa ficticia el pago'
    NUEVA = GUARDADA + ' acordado en la reunión ficticia del consejo de administración'
    OTRA_CITA = 'Otra cita ficticia del mismo auto migrado'
    U, GEMELA, VECINA, AJENA, ACUSACION, RETIRADA = (c * 32 for c in ('7e', '7f', '6e', '6f', '6d', '6c'))
    NOTA = {'note': 'nota ficticia de la corrección'}
    FICHERO = 'esc/migraciones_citas.yaml'
    LINEA = 'REGISTRO esc/migraciones_citas.yaml: migración de cita declarada no válida: '

    def setUp(self):
        super().setUp()
        valor = arista(self.PERSONA, self.EMPRESA, rtype='RECEIVED', doc=self.DOC, quote=self.GUARDADA)
        valores = {
            self.U: valor,
            # Las gemelas de su firma: la misma arista desde otra página (mismo par y tipo) y una de otro par que comparte
            # un extremo; ninguna declara nada.
            self.GEMELA: dict(valor, props={**valor['props'], 'source_page': 7}),
            self.VECINA: arista(self.PERSONA, self.OTRA, rtype='RECEIVED', doc=self.DOC, quote=self.GUARDADA),
            self.AJENA: arista(self.OTRA_PERSONA, self.OTRA, rtype='PAID', doc=self.DOC, quote=self.OTRA_CITA),
            self.ACUSACION: arista(self.OTRA_PERSONA, DELITO, rtype='ACCUSED_OF', kind='acusacion', doc=self.DOC,
                                   quote='acusación ficticia del auto migrado'),
            self.RETIRADA: arista(self.OTRA_PERSONA, self.EMPRESA, rtype='PAID', doc=self.DOC,
                                  quote='pago ficticio retirado del auto migrado')}
        db = sqlite3.connect(self.root / 'census.sqlite')
        with db:
            db.executemany('INSERT INTO units(unit_id, kind, value) VALUES (?,?,?)',
                           [(u, 'edge', json.dumps(v, ensure_ascii=False)) for u, v in valores.items()])
            db.execute('INSERT INTO esc_packs VALUES (?,?,?)', ('E-migrada', self.PERSONA, json.dumps(list(valores))))
        db.close()
        for n, uid in enumerate((self.U, self.GEMELA, self.VECINA)):
            d = {'entity_id': self.PERSONA,
                 '_meta': {'entity': self.PERSONA, 'resolution': 'humano-2026-10-01',
                           'decided_at': '2026-10-01T10:00:00+00:00'},
                 'units': [{'unit_id': uid, 'verdict': 'corregido', 'label': 'arista', 'evidence': 'p.6'}],
                 'edges': [{'unit_id': uid, 'action': 'set_prop', 'prop': 'note', 'value': self.NOTA['note'],
                            'evidence': 'p.6', 'reason': 'nota de la corrección'}]}
            (self.root / f'esc/decisions/D-migrada-{n}.json').write_text(json.dumps(d), encoding='utf-8')
        self.decision('D-migrada-retirada', self.RETIRADA, 'la cita no sostiene el pago', entity=self.PERSONA)

    def fila(self, rid, uid, fin=None):
        return dict(fila(rid, uid, decision='rectificado'), fecha='2026-10-08',
                    dato=f'({self.PERSONA}) -[RECEIVED]-> ({fin or self.EMPRESA}): arista corregida')

    def declarar(self, entradas=None, texto=None):
        """El fichero privado: `texto` tal cual o las `entradas` (por defecto, la de U: la cita guardada y la nueva)."""
        if texto is None:
            texto = yaml.safe_dump(self.declaracion() if entradas is None else entradas, sort_keys=False)
        (self.root / self.FICHERO).write_text(texto, encoding='utf-8')

    def declaracion(self, uid=None, **cambios):
        return {uid or self.U: {'documento': self.DOC, 'antes': huella_de_firma(self.GUARDADA),
                                'despues': huella_de_firma(self.NUEVA), **cambios}}

    def copia(self, cita, nota=True, fin=None):
        return (self.PERSONA, fin or self.EMPRESA, 'RECEIVED',
                {'source_kind': 'judicial', 'source_doc': self.DOC, 'source_quote': cita, **(self.NOTA if nota else {})})

    def sin_valores(self, out):
        for valor in (self.DOC, self.GUARDADA[:25], self.NUEVA[-25:], huella_de_firma(self.GUARDADA),
                      huella_de_firma(self.NUEVA), huella_de_firma(self.NUEVA)[:16], 'persona-migrada', 'empresa-migrada',
                      self.NOTA['note']):
            self.assertNotIn(valor, out)

    def correr(self, filas, *copias):
        """El --snapshot con estas copias (y la de U_VIVA: un export sin aristas no vale) y los nodos de las unidades."""
        self.export([viva(EDGES[U_VIVA]), *copias], nodos=(self.PERSONA, self.EMPRESA, self.OTRA, self.OTRA_PERSONA))
        code, out = self.snapshot(filas)
        self.sin_valores(out)
        return code, out

    def test_the_migrated_copy_of_the_declared_unit_is_its_corrected_edge(self):
        # AC285: con la cita nueva y su nota, la copia es la arista corregida de U solo si su unidad lo declara; la de
        # la cita guardada sigue siendo suya (la declaración suma, no sustituye); la de la cita nueva sin la nota es suya
        # y está sin corregir; sin ninguna, nada la prueba. AC284: la huella es la de la firma (300 caracteres).
        filas = [self.fila('R-2026-10-01-0901', self.U)]
        sin_corregida = 'REGISTRO R-2026-10-01-0901: rectificación decidida sin arista corregida'
        larga = self.NUEVA + ' ' + 'y' * 300
        for caso, declaracion, copias, linea in (
                ('sin fichero', None, [self.copia(self.NUEVA)], sin_corregida),
                ('declarada', self.declaracion(), [self.copia(self.NUEVA)], None),
                ('declarada, con la cita guardada', self.declaracion(), [self.copia(self.GUARDADA)], None),
                ('declarada, sin la nota', self.declaracion(), [self.copia(self.NUEVA, nota=False)],
                 'REGISTRO R-2026-10-01-0901: arista «rectificada» viva sin corregir (1 copia; tolerancia 0)'),
                ('declarada, sin copias', self.declaracion(), [], sin_corregida),
                ('otra cita', self.declaracion(), [self.copia(self.NUEVA + ' con otra cola')], sin_corregida),
                ('otro documento', self.declaracion(),
                 [(*self.copia(self.NUEVA)[:3], {**self.copia(self.NUEVA)[3], 'source_doc': 'otro-auto-ficticio.pdf'})],
                 sin_corregida),
                # La búsqueda libre (AC89): en otro par que comparte un extremo, por la cita de después, y sin gemela allí.
                ('declarada, en otro par', self.declaracion(),
                 [self.copia(self.NUEVA, fin='Company:tercera-empresa-ficticia')], None),
                ('sin fichero, en otro par', None, [self.copia(self.NUEVA, fin='Company:tercera-empresa-ficticia')],
                 sin_corregida),
                ('igual hasta el carácter 300', self.declaracion(despues=huella_de_firma(larga + ' cola uno')),
                 [self.copia(larga + ' cola dos')], None),
                ('distinta antes del 300', self.declaracion(despues=huella_de_firma(larga + ' cola uno')),
                 [self.copia('Otra ' + larga + ' cola uno')], sin_corregida)):
            with self.subTest(caso=caso):
                fichero = self.root / self.FICHERO
                if declaracion is None:
                    fichero.unlink(missing_ok=True)
                else:
                    self.declarar(declaracion)
                code, out = self.correr(filas, *copias)
                self.assertEqual(code, 1 if linea else 0, out)
                if linea:
                    self.assertIn(linea, out)
                self.assertEqual(out.count('REGISTRO '), int(bool(linea)), out)

    def test_the_fingerprint_is_that_of_the_signature_cut_at_quote_chars(self):
        # AC284 (superviviente M04 de la enmienda 44): la huella de `antes` es la de la cita de la FIRMA, recortada a 300.
        # Con una cita guardada más larga, la declaración con la huella recortada vale y la de la cita entera no casa.
        u, fin = '6b' * 32, 'Company:empresa-larga-ficticia'
        larga = self.GUARDADA + ' ' + 'z' * 300
        nueva = 'Cita nueva ficticia: ' + larga
        db = sqlite3.connect(self.root / 'census.sqlite')
        with db:
            db.execute('INSERT INTO units(unit_id, kind, value) VALUES (?,?,?)',
                       (u, 'edge', json.dumps(arista(self.PERSONA, fin, rtype='RECEIVED', doc=self.DOC, quote=larga))))
            db.execute('INSERT INTO esc_packs VALUES (?,?,?)', ('E-larga', self.PERSONA, json.dumps([u])))
        db.close()
        d = {'entity_id': self.PERSONA, '_meta': {'entity': self.PERSONA, 'resolution': 'humano-2026-10-01',
                                                  'decided_at': '2026-10-01T10:00:00+00:00'},
             'units': [{'unit_id': u, 'verdict': 'corregido', 'label': 'arista', 'evidence': 'p.6'}],
             'edges': [{'unit_id': u, 'action': 'set_prop', 'prop': 'note', 'value': self.NOTA['note'],
                        'evidence': 'p.6', 'reason': 'nota de la corrección'}]}
        (self.root / 'esc/decisions/D-larga.json').write_text(json.dumps(d), encoding='utf-8')
        entera = hashlib.sha256(' '.join(RC.cita_normalizada(larga).split()).encode('utf-8')).hexdigest()
        filas = [self.fila('R-2026-10-01-0931', u, fin=fin)]
        for caso, antes, codigo in (('recortada', huella_de_firma(larga), 0), ('entera', entera, 1)):
            with self.subTest(caso=caso):
                self.declarar({u: {'documento': self.DOC, 'antes': antes, 'despues': huella_de_firma(nueva)}})
                code, out = self.correr(filas, self.copia(nueva, fin=fin))
                self.assertEqual(code, codigo, out)
                self.assertEqual(self.LINEA + 'unidad 6b6b6b6b: la huella de antes no es la de la cita de su arista en '
                                 'el censo\n' in out, bool(codigo), out)

    def test_a_valid_declaration_leaves_what_the_free_search_knows_unchanged(self):
        # AC297 / AC287: la firma de después nunca forma grupo ni empareja aristas (AC286 la prohíbe como firma del
        # censo o de otra entrada): `formas_ajenas` es el mismo con y sin la declaración válida de U.
        self.declarar()
        self.correr([self.fila('R-2026-10-01-0941', self.U)], self.copia(self.NUEVA))
        conn = sqlite3.connect(self.root / 'census.sqlite')
        try:
            migradas, motivos = RC.migraciones_citas(self.root, conn)
            self.assertEqual((sorted(migradas), motivos), ([self.U], []))
            _, acciones, sets, retipos = RC.decisiones(self.root, set())
            trans = RC.transformadoras(conn, acciones, sets, retipos)
            _, nodos = RC.export_vivo(self.snap)
            sin, con = (RC.formas_ajenas(RC.aristas_censales(conn, m), trans, acciones, sets, retipos, (), nodos)
                        for m in (None, migradas))
        finally:
            conn.close()
        self.assertEqual(sin, con)
        self.assertIn(self.GEMELA, con)   # las gemelas de su firma siguen siéndolo

    def test_the_twins_of_its_signature_do_not_get_the_migrated_copy(self):
        # AC285: la declaración es de la unidad, nunca de la firma. Una gemela del mismo par, tipo, documento y cita
        # (otra página) y una vecina de otro par que comparte un extremo, con su propio set_prop «rectificado», no se
        # atribuyen la copia migrada de U: siguen sin su arista corregida; una regla por firma las daría por corregidas.
        self.declarar()
        filas = [self.fila('R-2026-10-01-0911', self.U), self.fila('R-2026-10-01-0912', self.GEMELA),
                 self.fila('R-2026-10-01-0913', self.VECINA, fin=self.OTRA)]
        code, out = self.correr(filas, self.copia(self.NUEVA))
        self.assertEqual(code, 1, out)
        for rid in ('0912', '0913'):
            self.assertIn(f'REGISTRO R-2026-10-01-{rid}: rectificación decidida sin arista corregida', out)
        self.assertNotIn('R-2026-10-01-0911', out)
        self.assertEqual(out.count('REGISTRO '), 2, out)
        # Con la cita guardada, la copia es de las dos del par, como antes de la migración; la vecina sigue sin la suya.
        code, out = self.correr(filas, self.copia(self.GUARDADA))
        self.assertEqual((code, out.count('REGISTRO ')), (1, 1), out)
        self.assertIn('REGISTRO R-2026-10-01-0913: rectificación decidida sin arista corregida', out)

    def test_a_declaration_that_does_not_hold_counts_for_nothing_and_is_a_problem(self):
        # AC286 y AC288: con cualquiera de estos, ninguna declaración vale (U vuelve a «sin arista corregida») y el
        # motivo nombra la posición o el hash8 de la unidad, nunca el documento, una huella ni un texto.
        buena = self.declaracion()[self.U]
        u, gemela = 'unidad 7e7e7e7e', 'unidad 7f7f7f7f'
        forma = 'sin la forma {documento, antes, despues} con huellas de 64 hex'
        for caso, declarar, motivo in (
                ('no es YAML', {'texto': 'a: [\n'}, 'no se lee como YAML (o con una clave repetida)'),
                ('clave repetida', {'texto': f'{self.U}: {{}}\n{self.U}: {{}}\n'},
                 'no se lee como YAML (o con una clave repetida)'),
                ('no es un mapa', {'entradas': [buena]}, 'no es un mapa de unidades'),
                ('le falta un campo', {'entradas': {self.U: {'documento': self.DOC, 'antes': buena['antes']}}},
                 f'{u}: {forma}'),
                ('le sobra un campo', {'entradas': {self.U: {**buena, 'cita': 'x'}}}, f'{u}: {forma}'),
                ('huella corta', {'entradas': self.declaracion(despues=buena['despues'][:16])}, f'{u}: {forma}'),
                ('huella larga', {'entradas': self.declaracion(despues=buena['despues'] + '0')}, f'{u}: {forma}'),
                ('unidad larga', {'entradas': {self.U + '0': buena}}, f'entrada 1: {forma}'),
                ('huella en mayúsculas', {'entradas': self.declaracion(despues=buena['despues'].upper())},
                 f'{u}: {forma}'),
                ('documento vacío', {'entradas': self.declaracion(documento=' ')}, f'{u}: {forma}'),
                ('clave que no es una unidad', {'entradas': {'no-es-una-unidad': buena}}, f'entrada 1: {forma}'),
                ('fuera del censo', {'entradas': {'5d' * 32: buena}}, 'unidad 5d5d5d5d: no es una arista del censo'),
                ('documento distinto', {'entradas': self.declaracion(documento='otro-auto-ficticio.pdf')},
                 f'{u}: documento distinto del de su arista en el censo'),
                ('antes que no casa', {'entradas': self.declaracion(antes=huella_de_firma(self.OTRA_CITA))},
                 f'{u}: la huella de antes no es la de la cita de su arista en el censo'),
                ('después = antes', {'entradas': self.declaracion(despues=buena['antes'])},
                 f'{u}: la huella de después es la firma de una arista del censo'),
                ('después = la cita vacía', {'entradas': self.declaracion(despues=huella_de_firma(''))},
                 f'{u}: la huella de después es la de una cita vacía'),
                ('después = la firma de otra arista', {'entradas': self.declaracion(
                    despues=huella_de_firma(self.OTRA_CITA))},
                 f'{u}: la huella de después es la firma de una arista del censo'),
                ('dos dueños', {'entradas': {**self.declaracion(), **self.declaracion(self.GEMELA)}},
                 f'{gemela}: la huella de después ya la declara otra unidad'),
                ('por su identidad', {'entradas': {**self.declaracion(), self.ACUSACION: {
                    'documento': self.DOC, 'antes': huella_de_firma('acusación ficticia del auto migrado'),
                    'despues': huella_de_firma('otra acusación')}}},
                 'unidad 6d6d6d6d: su cargador la identifica por su identidad, no por su cita'),
                ('retirada', {'entradas': {**self.declaracion(), self.RETIRADA: {
                    'documento': self.DOC, 'antes': huella_de_firma('pago ficticio retirado del auto migrado'),
                    'despues': huella_de_firma('otro pago')}}},
                 'unidad 6c6c6c6c: su operación vigente la retira (deny)')):
            with self.subTest(caso=caso):
                self.declarar(**declarar)
                code, out = self.correr([self.fila('R-2026-10-01-0921', self.U)], self.copia(self.NUEVA))
                self.assertEqual(code, 1, out)
                self.assertIn(self.LINEA + motivo + '\n', out)
                self.assertIn('REGISTRO R-2026-10-01-0921: rectificación decidida sin arista corregida', out)
                self.assertEqual(out.count('REGISTRO '), 2, out)
        # AC284 (e44 bis): solo el fichero ausente exime; presente y vacío, nulo, solo con comentarios o sin ninguna
        # entrada es un problema (un paso del A2 que lo dejara truncado no pasa en silencio).
        for caso, texto, motivo in (('vacío', '', 'no es un mapa de unidades'),
                                    ('nulo', 'null\n', 'no es un mapa de unidades'),
                                    ('solo comentarios', '# nada\n', 'no es un mapa de unidades'),
                                    ('mapa sin entradas', '{}\n', 'no declara ninguna unidad')):
            with self.subTest(caso=caso):
                self.declarar(texto=texto)
                code, out = self.correr([self.fila('R-2026-10-01-0921', self.U)], self.copia(self.NUEVA))
                self.assertEqual((code, out.count('REGISTRO ')), (1, 2), out)
                self.assertIn(self.LINEA + motivo + '\n', out)
        (self.root / self.FICHERO).unlink()
        code, out = self.correr([self.fila('R-2026-10-01-0921', self.U)], self.copia(self.NUEVA))
        self.assertEqual((code, out.count('REGISTRO ')), (1, 1), out)
        self.assertNotIn(self.LINEA, out)


class CargaTest(unittest.TestCase):
    """Spec 071 AC77/AC78 (enmienda 16): una sola preparación del `set:` de una regla, la del loader, en `alcance`
    (ligero: el CI la importa con el entorno de la API). Cada motivo de rechazo es `CargaInvalida` con el prop y un
    motivo neutro, nunca el valor; la fecha arrastra sus derivadas."""

    def setUp(self):
        from src import alcance  # el mismo módulo que importa registro_check (sus excepciones son las mismas)
        self.alcance = alcance

    def rechazo(self, props):
        with self.assertRaises(self.alcance.CargaInvalida) as fallo:
            self.alcance.preparar_set(props)
        self.assertNotIn(SENTINEL, str(fallo.exception))
        return fallo.exception

    def test_every_reason_the_loader_discards_a_block(self):
        casos = ((None, None, 'fix_skip_set_shape'), ({}, None, 'fix_skip_set_shape'),
                 (f'note={SENTINEL}', None, 'fix_skip_set_shape'),
                 ({'source_quote': SENTINEL}, 'source_quote', 'fix_skip_set_props'),
                 ({'note': 'x', 'source_doc': SENTINEL}, 'source_doc', 'fix_skip_set_props'),
                 ({f'{SENTINEL} con espacios': 'x'}, 'prop desconocido (posición 1)', 'fix_skip_set_props'),
                 ({'source_kind': 'judicial'}, 'source_kind', 'fix_skip_set_kind'),
                 ({'source_kind': SENTINEL}, 'source_kind', 'fix_skip_set_kind'),
                 ({'status': SENTINEL}, 'status', 'fix_skip_set_status'),
                 ({'source_page': 0, 'note': SENTINEL}, 'source_page', 'fix_skip_set_page'),
                 ({'source_page': '220'}, 'source_page', 'fix_skip_set_page'),
                 ({'source_page': True}, 'source_page', 'fix_skip_set_page'),
                 # Spec 070, enmienda 89: un nulo tampoco (borraría el [doc, p.N] de la cita), como en main.
                 ({'source_page': None}, 'source_page', 'fix_skip_set_page'))
        for n, (props, prop, evento) in enumerate(casos):
            with self.subTest(caso=n):
                fallo = self.rechazo(props)
                self.assertEqual((fallo.prop, fallo.evento), (prop, evento))

    def test_the_date_carries_its_derived_props_and_the_status_its_canonical_form(self):
        preparar = self.alcance.preparar_set
        self.assertEqual(preparar({'date': '2026-01-01'}), {'date': '2026-01-01', 'date_norm': '2026-01-01',
                                                             'date_year': 2026})
        self.assertEqual(preparar({'date': '2021'}), {'date': '2021', 'date_norm': '2021', 'date_year': 2021})
        for vacia in ('', None):
            self.assertEqual(preparar({'date': vacia}), {'date': vacia, 'date_norm': None, 'date_year': None})
        self.assertEqual(preparar({'status': 'Imputado'}), {'status': 'imputado'})
        self.assertEqual(preparar({'source_kind': 'prensa', 'source_page': 3, 'amount': 5.0, 'note': 'x'}),
                         {'source_kind': 'prensa', 'source_page': 3, 'amount': 5.0, 'note': 'x'})

    def test_an_unknown_key_is_named_by_its_position_never_by_its_name(self):
        # AC82: un diagnóstico nombra una clave solo si es del vocabulario cerrado (las props de la lista del loader,
        # las de la firma documental y los campos de una operación); una con forma de identificador puede ser un nombre.
        fallo = self.rechazo({'note': 'x', SENTINEL: 'y', 'source_doc': 'z'})
        self.assertEqual((fallo.prop, fallo.evento),
                         ('prop desconocido (posición 2), source_doc', 'fix_skip_set_props'))
        self.assertEqual(self.rechazo({'new_type': 'x'}).prop, 'new_type')
        for op, prop in (({'action': 'set_prop', 'prop': SENTINEL, 'value': 'x'}, 'prop desconocido (posición 1)'),
                         ({'action': 'retype', 'new_type': 'CONTROLS', 'set': {'note': 'x', SENTINEL: 'y'}},
                          'prop desconocido (posición 2)')):
            with self.subTest(prop=prop):
                with self.assertRaises(self.alcance.CargaInvalida) as fallo:
                    self.alcance.carga(op)
                self.assertEqual(str(fallo.exception), f'{prop}: fuera de la lista cerrada del loader')
                self.assertNotIn(SENTINEL, ''.join(traceback.format_exception(fallo.exception)))

    def test_a_date_that_is_not_iso_without_dateparser_is_rejected_by_its_reason(self):
        with mock.patch.dict(sys.modules, {'dateparser': None}):
            fallo = self.rechazo({'date': f'3 de marzo de 2021 {SENTINEL}'})
        self.assertEqual((fallo.prop, fallo.evento), ('date', 'fix_skip_set_date'))
        self.assertIn('dateparser', fallo.motivo)

    def test_the_load_of_an_operation(self):
        # `carga`: la de un set_prop (un prop o un mapa, con los valores del apply), un set_status o el mapa de un
        # retype o un invert; None si no cambia props; CargaInvalida si el loader no la escribiría.
        carga, invalida = self.alcance.carga, self.alcance.CargaInvalida
        self.assertIsNone(carga({'action': 'deny'}))
        self.assertIsNone(carga({'action': 'retype', 'new_type': 'CONTROLS'}))
        self.assertEqual(carga({'action': 'set_prop', 'prop': 'source_page', 'value': ' 7 '}), {'source_page': 7})
        self.assertEqual(carga({'action': 'set_prop', 'set': {'amount': '5.700,00', 'date': '2019-04-02'}}),
                         {'amount': 5700.0, 'date': '2019-04-02', 'date_norm': '2019-04-02', 'date_year': 2019})
        self.assertEqual(carga({'action': 'set_status', 'status': ' Acusado '}), {'status': 'acusado'})
        self.assertEqual(carga({'action': 'invert', 'set': {'note': 'x'}}), {'note': 'x'})
        for op, prop in (({'action': 'set_prop', 'prop': 'amount', 'value': SENTINEL}, 'amount'),
                         ({'action': 'set_prop', 'prop': 'source_page', 'value': SENTINEL}, 'source_page'),
                         ({'action': 'set_prop', 'prop': '', 'value': SENTINEL}, None),
                         ({'action': 'retype', 'new_type': 'CONTROLS', 'set': {}}, None),
                         ({'action': 'set_status', 'status': SENTINEL}, 'status')):
            with self.subTest(op=op['action'], prop=prop):
                with self.assertRaises(invalida) as fallo:
                    carga(op)
                self.assertEqual(fallo.exception.prop, prop)
                # Tampoco en su traceback: el ValueError de la conversión, que lleva el valor, no se encadena.
                self.assertNotIn(SENTINEL, ''.join(traceback.format_exception(fallo.exception)))


class ImportsTest(RegistroCheckBase):
    """Condición del módulo compartido (enmienda 16): `--tree`, lo que corre el CI, y `--snapshot` sin `--overlay`
    preparan la carga con `alcance`, sin importar el loader ni dateparser (el entorno de la API no los tiene)."""

    def importados(self, *args):
        proc = subprocess.run([sys.executable, '-X', 'importtime', RC.__file__, '--registro', str(self.registro),
                               *args], capture_output=True, encoding='utf-8',
                              env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, timeout=120)
        return proc.returncode, {line.rsplit('|', 1)[-1].strip() for line in proc.stderr.splitlines()
                                 if line.startswith('import time:')}

    def test_neither_the_tree_nor_the_snapshot_import_the_loader(self):
        self.registro.write_text(yaml.safe_dump({'rectificaciones': [nombrada('R-2026-09-24-0002', U_RETIRADA)]},
                                                allow_unicode=True, sort_keys=False), encoding='utf-8')
        self.otra_decision('retype', new_type='CONTROLS', set={'date': '2026-01-01', 'note': 'nota ficticia'})
        # Con el overlay, la forma de sus deny se comprueba con la función del loader, que vive en `alcance` (AC176):
        # una con un extremo por su nombre la resuelve como él (con `entity_ids`), sin importarlo ni el resolutor.
        reglas = self.base / 'repo/pipeline/data/relations/relations.yaml'
        reglas.parent.mkdir(parents=True)
        reglas.write_text(yaml.safe_dump({'deny': [{'source': 'Nombre Ficticio', 'target_id': MENGANO, 'type': 'PAID',
                                                    'reason': 'x'}]}), encoding='utf-8')
        for modo in (['--tree'], ['--tree', '--overlay', str(self.base / 'repo')],
                     ['--snapshot', str(self.snap), '--root', str(self.root), '--overlay', str(self.sin_reglas)]):
            with self.subTest(modo=' '.join(modo[:2])):
                code, modulos = self.importados(*modo)
                self.assertEqual(code, 0)
                self.assertIn('src.alcance', modulos)
                for pesado in ('src.relations_loader', 'src.entity_resolution', 'src.cleanup', 'src.qa_esc_apply',
                               'dateparser', 'rapidfuzz'):
                    self.assertNotIn(pesado, modulos)


class OverlayObligatorioTest(RegistroCheckBase):
    """Spec 071 AC179 (residual de la 21-sexies): sin el overlay, la puerta no cargaría sus reglas deny ni contrastaría
    el fichero privado con ellas, y certificaría algo distinto de lo que aplica el loader."""

    FILAS = [fila('R-2026-09-24-0001', U_RETIRADA)]

    def argv(self):
        self.registro.write_text(yaml.safe_dump({'rectificaciones': self.FILAS}, allow_unicode=True, sort_keys=False),
                                 encoding='utf-8')
        return ['--registro', str(self.registro), '--snapshot', str(self.snap), '--root', str(self.root)]

    def test_a_snapshot_without_its_overlay_stops(self):
        # --snapshot sin --overlay para con exit 2 y el motivo, sin ningún valor, también por la línea de órdenes; con
        # un overlay sin reglas y sin fichero privado, la puerta corre.
        argv = self.argv()
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as salida:
            RC.main(argv)
        self.assertEqual(salida.exception.code, 2)
        self.assertIn('--snapshot exige --overlay', err.getvalue())
        proc = subprocess.run([sys.executable, RC.__file__, *argv], capture_output=True, encoding='utf-8',
                              env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, timeout=120)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('--snapshot exige --overlay', proc.stderr)
        self.assertNotIn(U_RETIRADA[:8], proc.stdout + proc.stderr)
        self.assertIn(self.snapshot(self.FILAS)[0], (0, 1))

    def test_an_overlay_without_rules_still_checks_the_private_file(self):
        # Un overlay sin reglas no se salta el fichero privado: se contrasta igual (AC171), y una declaración que
        # ninguna regla opaca resuelve para; vacío, la puerta corre. Los predicados privados se preparan como en el
        # loader: sin el entorno del pipeline (el del CI, que solo corre --tree), se salta.
        try:
            from pipeline.src import qa_esc_apply  # noqa: F401  el loader, que prepara los predicados privados
        except ImportError:
            self.skipTest('sin el entorno del pipeline')
        argv = self.argv()
        clave = 'c3' * 32
        (self.root / 'esc/reglas_opacas.yaml').write_text(yaml.safe_dump({clave: [
            {'source_id': FULANA, 'target_id': MENGANO, 'type': 'PAID'}]}), encoding='utf-8')
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as salida:
            RC.main([*argv, '--overlay', str(self.sin_reglas)])
        self.assertEqual(salida.exception.code, 2)
        self.assertIn('declaraciones privadas de una clave que ninguna regla opaca resuelve', err.getvalue())
        self.assertNotIn(clave[:8], err.getvalue())
        (self.root / 'esc/reglas_opacas.yaml').write_text('{}\n', encoding='utf-8')
        self.assertIn(self.snapshot(self.FILAS)[0], (0, 1))


class PrecedenciaTest(unittest.TestCase):
    """Spec 071 AC54/AC55: una sola precedencia (`alcance.vigentes`), la que usan el apply, el registro y la puerta.
    Manda la familia (humano > arbitraje > el resto) y, dentro de ella, el instante más reciente en UTC."""

    UNIDAD = 'c7' * 32

    def decision(self, nombre, accion, resolution, cuando, unidad=UNIDAD, **op):
        meta = {'resolution': resolution}
        if cuando is not None:
            meta['decided_at'] = cuando
        return {'_file': f'{nombre}.json', '_meta': meta, 'units': [{'unit_id': unidad}],
                'edges': [{'unit_id': unidad, 'action': accion, **op}]}

    def vigente(self, *decisiones):
        return RC.vigentes(list(decisiones))[self.UNIDAD][1]

    def para(self, *decisiones):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as salida:
            RC.vigentes(list(decisiones))
        return salida.exception.code, err.getvalue().strip()

    def test_instants_are_compared_in_utc(self):
        # Como texto, «…T10:30+02:00» quedaba por delante de «…T09:00+00:00», que es media hora posterior.
        keep = self.decision('k', 'keep', 'arbitraje-2026-10-03', '2026-10-03T10:30:00+02:00')
        deny = self.decision('d', 'deny', 'arbitraje-2026-10-03', '2026-10-03T09:00:00+00:00')
        self.assertEqual(self.vigente(keep, deny)['action'], 'deny')
        self.assertEqual(self.vigente(deny, keep)['action'], 'deny')

    def test_the_family_comes_before_the_instant(self):
        humano = self.decision('h', 'keep', 'humano-2026-10-01', '2026-10-01T09:00:00+00:00')
        arbitraje = self.decision('a', 'deny', 'arbitraje-2026-10-05', '2026-10-05T09:00:00+00:00')
        self.assertEqual(self.vigente(arbitraje, humano)['action'], 'keep')

    def test_an_incompatible_tie_stops_and_a_compatible_one_does_not(self):
        cuando = '2026-10-03T09:00:00+00:00'
        uno = self.decision('uno', 'retype', 'arbitraje-2026-10-03', cuando, new_type='MEMBER_OF')
        otro = self.decision('otro', 'retype', 'arbitraje-2026-10-03', cuando, new_type='WORKS_FOR')
        self.assertEqual(self.para(uno, otro), (2, 'precedencia irresoluble (unidad c7c7c7c7)'))
        self.assertEqual(self.vigente(uno, dict(uno, _file='copia.json'))['new_type'], 'MEMBER_OF')

    def test_a_unit_id_without_the_unit_form_is_named_by_its_position(self):
        # Codex, 15ª pasada (AC95): `validar_cargas` y la precedencia irresoluble citaban los 8 primeros caracteres de
        # cualquier unit_id. Uno sin la forma de una unidad (un nombre escrito por error) se nombra por el fichero y la
        # posición de su operación, y el centinela no sale.
        cuando, centinela = '2026-10-03T09:00:00+00:00', 'XCENTINX' + 'f' * 56
        uno = self.decision('uno', 'retype', 'arbitraje-2026-10-03', cuando, unidad=centinela, new_type='MEMBER_OF')
        otro = self.decision('otro', 'retype', 'arbitraje-2026-10-03', cuando, unidad=centinela, new_type='WORKS_FOR')
        self.assertEqual(self.para(uno, otro), (2, 'precedencia irresoluble (uno.json, operación 1)'))
        mala = self.decision('mala', 'set_prop', 'arbitraje-2026-10-03', cuando, unidad=centinela,
                             set={'amount': 'no es un número'})
        codigo, err = self.para(mala)
        self.assertEqual(codigo, 2)
        self.assertIn('operación con carga inválida (mala.json, operación 1: ', err)
        self.assertNotIn('XCENTINX', err)

    def test_a_tie_between_two_maps_of_props_compares_the_whole_operation(self):
        # Spec 071, enmienda 11: un set_prop con mapa es una sola operación; dos mapas distintos a la vez no se ordenan, y
        # un set_status con otro estado tampoco.
        cuando = '2026-10-03T09:00:00+00:00'
        uno = self.decision('uno', 'set_prop', 'humano-2026-10-03', cuando, set={'note': 'nota', 'source_page': '3'})
        otro = self.decision('otro', 'set_prop', 'humano-2026-10-03', cuando, set={'note': 'nota', 'source_page': '4'})
        self.assertEqual(self.para(uno, otro), (2, 'precedencia irresoluble (unidad c7c7c7c7)'))
        self.assertEqual(self.vigente(uno, dict(uno, _file='copia.json'))['set'], {'note': 'nota', 'source_page': '3'})
        imputado = self.decision('i', 'set_status', 'humano-2026-10-03', cuando, status='imputado')
        acusado = self.decision('a', 'set_status', 'humano-2026-10-03', cuando, status='acusado')
        self.assertEqual(self.para(imputado, acusado), (2, 'precedencia irresoluble (unidad c7c7c7c7)'))

    def test_a_missing_or_naive_decided_at_stops(self):
        for cuando in (None, '2026-10-03T09:00:00', 'ayer'):
            with self.subTest(cuando=cuando):
                self.assertEqual(self.para(self.decision('sin', 'keep', 'arbitraje-2026-10-03', cuando)),
                                 (2, 'decisión con decided_at inválido (sin.json)'))


class TreeTest(RegistroCheckBase):
    def test_a_well_formed_registry_passes(self):
        rows = [fila('R-2026-09-24-0001', U_VIVA), fila('R-2026-10-03-0001', '', decision='rectificado')]
        rows[1]['seed_version'] = 'pendiente-path-a'
        self.assertEqual(self.run_check(rows, '--tree')[0], 0)

    def test_a_repeated_id_fails_naming_the_row(self):
        code, out = self.run_check([fila('R-2026-09-24-0001', U_VIVA), fila('R-2026-09-24-0001', U_RETIRADA)],
                                   '--tree')
        self.assertEqual(code, 1)
        self.assertIn('REGISTRO R-2026-09-24-0001: id repetido', out)

    def test_shape_vocabulary_and_seed(self):
        cases = [({'id': 'R-24-09-2026-1'}, 'REGISTRO fila 1: id con formato inválido'),
                 ({'origen': 'correo'}, '`origen` fuera del vocabulario'),
                 ({'decision': 'retirado'}, '`decision` fuera del vocabulario'),
                 ({'seed_version': 'qa063a'}, '`seed_version` no es'),
                 ({'fecha': '24-09-2026'}, '`fecha` fuera de la forma'),
                 ({'fuente': ''}, 'campos sin texto: fuente'),
                 ({'unidad': None}, '`unidad` no es'),
                 ({'unidad': 'abc'}, '`unidad` no es')]
        for over, fragment in cases:
            with self.subTest(over=over):
                row = {**fila('R-2026-09-24-0001', U_VIVA), **over}
                code, out = self.run_check([row], '--tree')
                self.assertEqual(code, 1)
                self.assertIn(fragment, out)
        row = fila('R-2026-09-24-0001', U_VIVA)
        del row['dato']
        code, out = self.run_check([row], '--tree')
        self.assertIn('faltan campos: dato', out)

    def test_no_value_read_from_the_registry_reaches_the_report(self):
        # Codex, 1ª pasada: el log de Actions es público; un id, un origen o una fecha mal puestos pueden
        # llevar dentro lo que se retiró. El informe nombra la fila por su posición y el campo, nunca el valor.
        rows = []
        for campo in ('id', 'decision', 'seed_version', 'origen', 'fecha', 'fecha_resolucion', 'unidad'):
            row = fila(f'R-2026-09-24-{len(rows) + 1:04d}', U_VIVA)
            row[campo] = SENTINEL
            rows.append(row)
        rows.append({**fila(SENTINEL, U_VIVA)})  # el mismo id inválido dos veces: «repetido», sin el valor
        code, out = self.run_check(rows, '--tree')
        self.assertEqual(code, 1)
        self.assertNotIn(SENTINEL, out)
        self.assertIn('REGISTRO fila 1: id con formato inválido', out)
        self.assertIn('REGISTRO fila 8: id repetido', out)
        self.assertIn('REGISTRO R-2026-09-24-0002: `decision` fuera del vocabulario', out)
        self.assertIn('REGISTRO R-2026-09-24-0003: `seed_version` no es', out)

    def test_a_registry_that_does_not_load_reports_only_the_file_and_the_position(self):
        # El log de Actions del repo es público. El texto de un error de PyYAML cita la línea rota, y `!!int "…"`
        # ni siquiera es un YAMLError: el ValueError, con el valor, salía en el traceback (Codex, 3ª pasada). Un
        # registro que no carga no se puede comprobar: exit 2, como uno con una clave repetida (spec 071 AC108).
        casos = {'!!int': ('rectificaciones:\n- id: !!int "CENTINELA"\n', 'posición desconocida'),
                 '!!float': ('rectificaciones:\n- id: !!float "CENTINELA"\n', 'posición desconocida'),
                 'tabulador': ('rectificaciones:\n\t- id: CENTINELA\n', 'línea 2, columna 1'),
                 'comilla sin cerrar': ('rectificaciones:\n- id: R-2026-09-24-0001\n'
                                        '  dato: "CENTINELA que no se cierra\n  fuente: x\n', 'línea 5, columna 1')}
        for caso, (texto, donde) in casos.items():
            self.registro.write_text(texto, encoding='utf-8')
            for modo in (['--tree'], ['--snapshot', str(self.snap), '--root', str(self.root)]):
                with self.subTest(caso=caso, modo=modo[0]):
                    code, out = self.cli(*modo)
                    self.assertEqual(code, 2)
                    self.assertNotIn('centinela', out.lower())
                    self.assertIn(f'registro.yaml: YAML inválido ({donde})', out)

    def test_a_repeated_key_is_rejected_with_only_its_position(self):
        # Codex, 16ª pasada (16a-M2 y 16c-M, AC108): PyYAML se queda con la última de dos claves iguales. Un segundo
        # bloque `rectificaciones:` vacío escondía a `--tree` la fila inválida del primero, y una `decision` repetida
        # dejaba la última. El lector las rechaza en cualquier nivel: exit 2, el fichero y la posición, sin la clave ni
        # el valor; también en `--snapshot`.
        fila = ("- {id: R-2026-10-08-0001, fecha: '2026-10-08', origen: auditoria-063, entidad: x, unidad: '', "
                "dato: x, fuente: x, decision: %s, fecha_resolucion: '2026-10-08', seed_version: pendiente-path-a%s}\n")
        casos = {'dos bloques': ('rectificaciones:\n' + fila % ('CENTINELA', '') + 'rectificaciones: []\n', 'línea 3'),
                 'decision repetida': ('rectificaciones:\n' + fila % ('suprimido', ', decision: CENTINELA'), 'línea 2')}
        for caso, (texto, donde) in casos.items():
            self.registro.write_text(texto, encoding='utf-8')
            for modo in (['--tree'], ['--snapshot', str(self.snap), '--root', str(self.root)]):
                with self.subTest(caso=caso, modo=modo[0]):
                    code, out = self.cli(*modo)
                    self.assertEqual(code, 2, out)
                    self.assertIn(f'registro.yaml: YAML inválido ({donde}, columna ', out)
                    self.assertNotIn('centinela', out.lower())
                    self.assertNotIn('decision', out)

    def test_the_shipped_registry_is_well_formed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(RC.main(['--tree']), 0, out.getvalue())



class AnonimatoTreeTest(RegistroCheckBase):
    """Spec 071 AC113, AC115 y AC123 (enmienda 21): lo que `--tree` (el CI, sin censo) comprueba del anonimato del
    registro frente al overlay público."""

    def overlay(self, deny=(), fix=()):
        """Un overlay de juguete con estas reglas; el `--tree` lo lee con `--overlay`."""
        reglas = self.base / 'repo/pipeline/data/relations/relations.yaml'
        reglas.parent.mkdir(parents=True, exist_ok=True)
        reglas.write_text(yaml.safe_dump({'deny': list(deny), 'fix': list(fix)}, allow_unicode=True, sort_keys=False),
                          encoding='utf-8')
        return str(self.base / 'repo')

    def test_a_marked_row_whose_edge_stays_published_names_both_endpoints(self):
        # AC113 y AC123: el marcador solo va en la fila de una unidad cuya fila vigente la retira. La «rectificado»
        # vigente que lo conserva espera una decisión del dueño; la anterior de una unidad retirada después y la que no
        # tiene unidad lo conservan con derecho.
        rectificada = {**fila('R-2026-09-24-0001', U_VIVA, decision='rectificado'),
                       'dato': f'({FULANA}) -[PAID]-> (Person:[tercero]): arista corregida'}
        anterior = {**fila('R-2026-09-24-0002', U_RETIRADA, decision='rectificado'),
                    'dato': f'({FULANA}) -[OWNS]-> (Company:[sociedad]): arista corregida'}
        retirada = {**fila('R-2026-10-01-0001', U_RETIRADA), 'fecha': '2026-10-01',
                    'dato': f'({FULANA}) -[OWNS]-> (Company:[sociedad]): arista retirada'}
        sin_unidad = fila('R-2026-10-03-0001', '', decision='rectificado')
        code, out = self.run_check([rectificada, anterior, retirada, sin_unidad], '--tree')
        self.assertEqual(code, 1, out)
        self.assertIn(f'REGISTRO R-2026-09-24-0001: {RC.RECTIFICADO_MARCADO}', out)
        for rid in ('R-2026-09-24-0002', 'R-2026-10-01-0001', 'R-2026-10-03-0001'):
            self.assertNotIn(f'REGISTRO {rid}:', out)
        self.assertIn('1 problema de forma', out)

    def test_the_fingerprint_of_an_anonymized_row_only_goes_in_opaque_rules(self):
        # AC115: la huella de una fila con marcador, con corchetes o sin ellos, solo puede ir en una regla deny opaca
        # (`{unidad, kinds?, reason}`), que respalda una fila con marcador de su clave. La de una fila que ya nombra sus
        # dos extremos (una «rectificado» re-renderizada, AC113) puede citarse en cualquier motivo: los cuatro motivos
        # de deny que la citan sin corchetes dejan de ser un problema.
        marcada = fila('R-2026-09-24-0001', U_VIVA)
        nombrada = {**fila('R-2026-09-24-0002', U_RETIRADA, decision='rectificado'),
                    'dato': f'({FULANA}) -[OWNS]-> ({EMPRESA}): arista corregida'}
        explicita = {'source_id': FULANA, 'target_id': MENGANO, 'type': 'PAID'}
        opaca = {'unidad': U_VIVA, 'reason': f'063 auditoría escalonada 2026-09-24 [{U_VIVA[:8]}]: {MOTIVO_AJENA}'}
        casos = (('etiqueta en una explícita', {'deny': [{**explicita, 'reason': f'063 [{U_VIVA[:8]}]: x'}]},
                  'huella de una fila anonimizada fuera de su regla opaca (relations/relations.yaml:deny[0])'),
                 ('huella sin corchetes', {'deny': [{**explicita, 'reason': f'gemela de {U_VIVA[:8]}'}]},
                  'huella de una fila anonimizada fuera de su regla opaca (relations/relations.yaml:deny[0])'),
                 ('opaca con un campo de más', {'deny': [{**opaca, 'source_id': FULANA}]},
                  'regla opaca con forma inválida'),
                 ('opaca en fix', {'deny': [opaca], 'fix': [opaca]},
                  'regla opaca fuera de una sección deny del overlay de relaciones'),
                 ('opaca sin fila que la respalde', {'deny': [opaca, {'unidad': U_GEMELA, 'reason': 'x'}]},
                  'regla opaca sin fila anonimizada que la respalde'),
                 ('la opaca de la fila', {'deny': [opaca]}, None),
                 ('huella de una fila que nombra sus extremos', {'deny': [
                     opaca, {**explicita, 'reason': f'gemela de {U_RETIRADA[:8]}, conservada'}]}, None))
        for caso, reglas, problema in casos:
            with self.subTest(caso=caso):
                code, out = self.run_check([marcada, nombrada], '--tree', '--overlay', self.overlay(**reglas))
                self.assertEqual(code, 1 if problema else 0, out)
                if problema:
                    self.assertIn(problema, out)
                for valor in (FULANA, MENGANO, U_VIVA, U_GEMELA):
                    self.assertNotIn(valor, out)

    def publico(self, rel, contenido):
        """Un fichero público más del repo de juguete, en `pipeline/data/<rel>`."""
        path = self.base / 'repo/pipeline/data' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contenido, encoding='utf-8')

    def test_a_fingerprint_of_any_length_in_any_public_text_names_its_rows_by_position(self):
        # Spec 071 AC131, AC133 y AC135 (17ª pasada de Codex): la huella de una fila con marcador es cualquier tramo de
        # 8 a 64 caracteres hexadecimales que es prefijo de su unidad, en cualquier fichero de texto público: un YAML,
        # un JSON (cada elemento de una lista, también la de una clave) y un Markdown (cada línea). Un tramo que es
        # prefijo de dos unidades, las dos; uno de 7, o que no es prefijo, ninguna. La posición nombra el fichero y una
        # sección de vocabulario cerrado, o su orden, nunca una clave libre.
        marcada, gemela = fila('R-2026-09-24-0001', U_VIVA), fila('R-2026-09-24-0002', U_VIVA[:8] + 'f' * 56)
        casos = (('8 en un JSON de lista', 'boe/ficticio.json', json.dumps([{'nota': f'unidad {U_VIVA[:8]}'}]),
                  ('R-2026-09-24-0001', 'R-2026-09-24-0002'), 'boe/ficticio.json:[0]'),
                 ('9 en la lista de un JSON de mapa', 'boe/ficticio.json',
                  json.dumps({'generado': 'x', 'lista': [U_VIVA[:9]]}), ('R-2026-09-24-0001',),
                  'boe/ficticio.json:clave 2[0]'),
                 ('64 en un Markdown', 'press/_LEEME.md', f'# Notas\n\nla unidad {U_VIVA} se retiró\n',
                  ('R-2026-09-24-0001',), 'press/_LEEME.md:línea 3'),
                 ('10 en una sección libre de un YAML', 'context/libre.yaml',
                  yaml.safe_dump({'Seccion CENTINELA': [{'nota': f'ver {U_VIVA[:10]}'}]}, allow_unicode=True),
                  ('R-2026-09-24-0001',), 'context/libre.yaml:sección 1[0]'),
                 ('7 no son huella', 'boe/ficticio.json', json.dumps([U_VIVA[:7]]), (), None),
                 ('un tramo que no es prefijo', 'boe/ficticio.json', json.dumps(['0123456789abcdef']), (), None))
        for caso, rel, contenido, filas, posicion in casos:
            with self.subTest(caso=caso):
                repo = self.overlay()
                for viejo in (self.base / 'repo/pipeline/data').rglob('*'):
                    if viejo.is_file() and viejo.name != 'relations.yaml':
                        viejo.unlink()
                self.publico(rel, contenido)
                code, out = self.run_check([marcada, gemela], '--tree', '--overlay', repo)
                self.assertEqual(code, 1 if filas else 0, out)
                for rid in ('R-2026-09-24-0001', 'R-2026-09-24-0002'):
                    self.assertEqual(f'REGISTRO {rid}: {HUELLA} ({posicion})' in out, rid in filas, out)
                self.assertNotIn('CENTINELA', out)

    def test_only_what_git_publishes_is_public(self):
        # Spec 071 AC133: en un checkout, solo es público lo que git versiona o no ignora; la huella en una caché
        # ignorada no es un problema, y en un fichero sin versionar, sí.
        repo = self.overlay()
        subprocess.run(['git', 'init', '-q', repo], check=True)
        (Path(repo) / '.gitignore').write_text('pipeline/data/cache/\n', encoding='utf-8')
        marcada = fila('R-2026-09-24-0001', U_VIVA)
        self.publico('cache/ficticio.json', json.dumps([U_VIVA[:8]]))
        code, out = self.run_check([marcada], '--tree', '--overlay', repo)
        self.assertEqual(code, 0, out)
        self.publico('boe/ficticio.json', json.dumps([U_VIVA[:8]]))
        code, out = self.run_check([marcada], '--tree', '--overlay', repo)
        self.assertEqual(code, 1, out)
        self.assertIn(f'{HUELLA} (boe/ficticio.json:[0])', out)

    def test_an_opaque_rule_says_only_its_closed_form(self):
        # Spec 071 AC127 y AC130: una regla opaca lleva su clave, cuántas declaraciones privadas resuelve (un entero
        # ≥ 0) y un motivo con su fórmula cerrada (la etiqueta de su lote si tiene forma de lote, la de su clave y el
        # motivo neutro). Otro motivo puede nombrar lo que su fila oculta: la fila es reconstruible; un recuento que no
        # es un entero ≥ 0 es una forma inválida.
        marcada = fila('R-2026-09-24-0001', U_VIVA)
        formula = f'063 auditoría escalonada 2026-09-24 [{U_VIVA[:8]}]: {MOTIVO_AJENA}'
        reconstruible = f'{RC.RECONSTRUIBLE} (relations/relations.yaml:deny[0])'
        casos = (('fórmula', {'reason': formula}, None),
                 ('fórmula sin lote', {'reason': f'[{U_VIVA[:8]}]: {MOTIVO_AJENA}'}, None),
                 ('con sus declaraciones', {'reason': formula, 'declaraciones': 2}, None),
                 ('motivo con más texto', {'reason': f'{formula}; {CITA}'}, reconstruible),
                 ('lote libre', {'reason': f'063 tramo 3: texto libre [{U_VIVA[:8]}]: {MOTIVO_AJENA}'},
                  reconstruible),
                 ('declaraciones negativas', {'reason': formula, 'declaraciones': -1}, FORMA_OPACA),
                 ('declaraciones como texto', {'reason': formula, 'declaraciones': '2'}, FORMA_OPACA),
                 ('declaraciones booleanas', {'reason': formula, 'declaraciones': True}, FORMA_OPACA))
        for caso, campos, problema in casos:
            with self.subTest(caso=caso):
                code, out = self.run_check([marcada], '--tree', '--overlay',
                                           self.overlay(deny=[{'unidad': U_VIVA, **campos}]))
                self.assertEqual(code, 1 if problema else 0, out)
                if problema:
                    self.assertIn(problema, out)
                self.assertNotIn(CITA, out)
                self.assertNotIn('texto libre', out)

    def test_a_merge_key_in_the_registry_stops_with_its_position(self):
        # Spec 071 AC128: una clave de fusión `<<` mezcla en una fila lo que dice otra sin que se vea: el registro no
        # vale (exit 2), con el fichero y la posición, nunca el valor.
        self.registro.write_text('base: &b {fuente: FUENTE_CENTINELA}\nrectificaciones:\n  - <<: *b\n'
                                 '    id: R-2026-09-24-0001\n', encoding='utf-8')
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                self.assertRaises(SystemExit) as salida:
            RC.main(['--registro', str(self.registro), '--tree'])
        self.assertEqual(salida.exception.code, 2)
        self.assertIn('registro.yaml: YAML inválido (línea 3', out.getvalue())
        self.assertNotIn('CENTINELA', out.getvalue())

    # --- enmienda 21-ter (spec 071 AC143–AC149 y AC153; 18ª pasada de Codex) -----------------------------------

    def en_el_repo(self):
        """El repo de juguete con el registro dentro, donde el anonimato lo barre como un fichero público más."""
        repo = self.overlay()
        self.registro = Path(repo) / 'pipeline/data/rectificaciones/registro.yaml'
        self.registro.parent.mkdir(parents=True, exist_ok=True)
        return repo

    def test_an_anonymized_row_says_nothing_of_its_hidden_endpoint_outside_its_dato(self):
        # Spec 071 AC143 (18b-1): la propia fila marcada ya no se excluye en bloque del barrido; solo el valor de su
        # `unidad`. Sin el censo no se sabe cuál es el extremo oculto: otro extremo que los que nombran su `dato` y su
        # `entidad`, en cualquier otro campo o tras la arista, puede serlo; y la huella de su unidad en otro campo,
        # también. Lo de la fila lo dice igual --snapshot (AC149).
        repo = self.en_el_repo()
        oculto = 'Person:oculto-centinela'
        # Los mensajes, por su literal: contra el commit anterior el test falla por lo que hace, no por un nombre.
        fuera = ('fila anonimizada que nombra un extremo fuera de su dato '
                 '(rectificaciones/registro.yaml:rectificaciones[0])')
        casos = (('un extremo de más en la fuente', {'fuente': f'auto-ficticio.pdf p.3; extremo retirado: {oculto}'},
                  fuera),
                 ('un extremo de más tras la arista',
                  {'dato': f'({FULANA}) -[PAID]-> (Person:[tercero]): arista retirada; era {oculto}'}, fuera),
                 ('la huella en la fuente', {'fuente': f'auto-ficticio.pdf p.3 ({U_VIVA[:12]})'},
                  f'{HUELLA} (rectificaciones/registro.yaml:rectificaciones[0])'),
                 ('su entidad en la fuente', {'fuente': f'auto-ficticio.pdf p.3 ({FULANA})'}, None),
                 ('solo su unidad', {}, None))
        for caso, campos, problema in casos:
            with self.subTest(caso=caso):
                marcada = {**fila('R-2026-09-24-0001', U_VIVA), **campos}
                code, out = self.run_check([marcada], '--tree', '--overlay', repo)
                self.assertEqual(code, 1 if problema else 0, out)
                if problema:
                    self.assertIn(f'REGISTRO R-2026-09-24-0001: {problema}', out)
                self.assertNotIn('centinela', out)
                if not con_pipeline():
                    continue
                # Con el mismo overlay, que lleva el registro: la fila sale de su propio fichero (AC196).
                _, out = self.run_check([marcada], '--snapshot', str(self.snap), '--root', str(self.root),
                                        '--overlay', repo)
                self.assertEqual(fuera in out, problema == fuera, out)
                self.assertNotIn('centinela', out)

    def test_only_the_structural_fields_are_left_out_and_by_their_position(self):
        # Spec 071 AC144 (18b-2): lo estructural se deja fuera por su posición, nunca sustituyendo la cadena en todo el
        # texto: de una fila, el valor de su `unidad`; de una opaca, el de su `unidad`, el de sus `declaraciones` y la
        # etiqueta de su clave dentro de su motivo validado. La unidad entera en otro campo de la fila, o la etiqueta o
        # la unidad en un comentario junto a la opaca, es su huella; y cada comentario de un YAML público se barre
        # además aparte, como «fichero:línea n».
        repo = self.en_el_repo()
        reglas = Path(repo) / 'pipeline/data/relations/relations.yaml'
        formula = f'063 auditoría escalonada 2026-09-24 [{U_VIVA[:8]}]: {MOTIVO_AJENA}'
        opaca = f"  - {{unidad: {U_VIVA}, reason: '{formula}'}}"
        otra = f'  - {{source_id: {FULANA}, target_id: {EMPRESA}, type: OWNS, reason: higiene}}'
        casos = (('la opaca sola', {}, f'deny:\n{opaca}\nfix: []\n', None),
                 ('un comentario de cola con la etiqueta', {},
                  f'deny:\n{opaca}  # Person:oculto-centinela [{U_VIVA[:8]}]\nfix: []\n',
                  'relations/relations.yaml:línea 2'),
                 ('un comentario con la unidad dentro de la sección', {},
                  f'deny:\n{opaca}\n  # nota {U_VIVA}\n{otra}\nfix: []\n', 'relations/relations.yaml:línea 3'),
                 ('la unidad entera en la fuente de la fila', {'fuente': f'auto-ficticio.pdf p.3 {U_VIVA}'},
                  f'deny:\n{opaca}\nfix: []\n', 'rectificaciones/registro.yaml:rectificaciones[0]'))
        for caso, campos, texto, posicion in casos:
            with self.subTest(caso=caso):
                reglas.write_text(texto, encoding='utf-8')
                code, out = self.run_check([{**fila('R-2026-09-24-0001', U_VIVA), **campos}], '--tree', '--overlay',
                                           repo)
                self.assertEqual(code, 1 if posicion else 0, out)
                if posicion:
                    self.assertIn(f'REGISTRO R-2026-09-24-0001: {HUELLA} ({posicion})', out)
                self.assertNotIn('centinela', out)

    def test_a_json_key_is_swept_with_every_element_of_its_list(self):
        # Spec 071 AC145 (18b-3): de una clave de un JSON cuyo valor es una lista, cada elemento con su clave
        # (`{clave: elemento}`), y la lista vacía como una entrada `{clave: []}`: la clave siempre se barre.
        repo = self.en_el_repo()
        casos = (('una lista', {U_VIVA[:10]: ['uno', 'dos']},
                  ('boe/ficticio.json:clave 1[0]', 'boe/ficticio.json:clave 1[1]')),
                 ('una lista vacía', {'generado': 'x', U_VIVA[:10]: []}, ('boe/ficticio.json:clave 2',)),
                 ('sin huella', {'nombre': ['uno'], 'otro': []}, ()))
        for caso, contenido, posiciones in casos:
            with self.subTest(caso=caso):
                self.publico('boe/ficticio.json', json.dumps(contenido))
                code, out = self.run_check([fila('R-2026-09-24-0001', U_VIVA)], '--tree', '--overlay', repo)
                self.assertEqual(code, 1 if posiciones else 0, out)
                for posicion in posiciones:
                    self.assertIn(f'REGISTRO R-2026-09-24-0001: {HUELLA} ({posicion})', out)

    def test_a_fingerprint_in_capitals_is_still_a_fingerprint(self):
        # Spec 071 AC146 (18b-4): la huella de una unidad no distingue mayúsculas: en mayúsculas, en caja mixta o
        # entera, en cualquier fichero público.
        repo = self.en_el_repo()
        for caso, texto in (('en mayúsculas', U_VIVA[:12].upper()), ('en caja mixta', 'AaAaAaAaAa'),
                            ('entera', U_VIVA.upper())):
            with self.subTest(caso=caso):
                self.publico('press/_LEEME.md', f'nota sobre {texto}\n')
                code, out = self.run_check([fila('R-2026-09-24-0001', U_VIVA)], '--tree', '--overlay', repo)
                self.assertEqual(code, 1, out)
                self.assertIn(f'REGISTRO R-2026-09-24-0001: {HUELLA} (press/_LEEME.md:línea 1)', out)

    def test_the_shape_of_the_anonymity_says_the_same_with_snapshot(self):
        # Spec 071 AC149 (18b-7): la forma del anonimato corre también con --snapshot: lo que dice --tree de una fila,
        # lo dice --snapshot.
        rectificada = {**fila('R-2026-09-24-0001', U_VIVA, decision='rectificado'),
                       'dato': f'({FULANA}) -[PAID]-> (Person:[tercero]): arista corregida'}
        for modo in (('--tree',), ('--snapshot', str(self.snap), '--root', str(self.root))):
            with self.subTest(modo=modo[0]):
                code, out = self.run_check([rectificada], *modo)
                self.assertEqual(code, 1, out)
                self.assertIn(f'REGISTRO R-2026-09-24-0001: {RC.RECTIFICADO_MARCADO}', out)

    def test_an_edge_row_names_each_endpoint_once_in_its_own_orientation(self):
        # Spec 071 AC153 (18c-1): una fila de arista que nombra el mismo extremo en los dos lados no dice qué arista se
        # retiró: es un problema de forma. Y el marcador toma el extremo de la arista que no es el visible, aunque la
        # arista lleve el par al revés que el dato: cada extremo una vez, en la orientación del dato.
        dos_veces = {**fila('R-2026-09-24-0001', U_VIVA), 'dato': f'({FULANA}) -[PAID]-> ({FULANA}): arista retirada'}
        code, out = self.run_check([dos_veces], '--tree')
        self.assertEqual(code, 1, out)
        self.assertIn('REGISTRO R-2026-09-24-0001: fila con el mismo extremo dos veces', out)
        nombrada = {**dos_veces, 'dato': f'({FULANA}) -[PAID]-> ({MENGANO}): arista retirada'}
        self.assertEqual(self.run_check([nombrada], '--tree')[0], 0)
        marcado = f'({FULANA}) -[PAID]-> (Person:[tercero]): arista retirada'
        for caso, inicio, fin in (('en su orden', FULANA, MENGANO), ('al revés', MENGANO, FULANA)):
            with self.subTest(caso=caso):
                self.assertEqual(RC.extremos_de_fila(marcado, {'type': 'PAID', 'start': inicio, 'end': fin}, FULANA),
                                 ([FULANA, MENGANO], {MENGANO}, ': arista retirada'))
        self.assertIsNone(RC.extremos_de_fila(dos_veces['dato'], {'type': 'PAID', 'start': MENGANO, 'end': FULANA},
                                              FULANA))

    def test_invisible_format_characters_hide_no_quote(self):
        # Spec 071 AC148 (18b-6): los caracteres de formato (categoría Cf: los de anchura cero, el guion blando…) se
        # quitan antes de partir en palabras, igual en los campos públicos que en las citas privadas: una cita con uno
        # dentro de una palabra sigue siendo su trozo.
        cita = 'Fulana de Tal pagó a Mengano Ficticio la comisión acordada junto al césped'
        casos = (('anchura cero en la fila', cita.replace('césped', 'cé\u200bsped'), cita),
                 ('guion blando en la fila', cita.replace('comisión', 'comi\u00adsión'), cita),
                 ('unión de palabras en la cita', cita, cita.replace('acordada', 'acor\u2060dada')),
                 ('marca de orden al principio', '\ufeff' + cita, cita))
        for caso, publica, privada in casos:
            with self.subTest(caso=caso):
                self.assertEqual(RC._llano(publica), RC._llano(privada))
                self.assertTrue(RC.trozo_de_cita({'fuente': f'auto-ficticio.pdf p.3: {publica}'}, {RC._llano(privada)}))

    # --- enmienda 21-quater (spec 071 AC158–AC164; 19ª pasada de Codex) ----------------------------------------
    # Los mensajes van por su literal: contra el commit anterior, cada test falla por lo que hace, no por un nombre.

    FUERA = ('REGISTRO R-2026-09-24-0001: fila anonimizada que nombra un extremo fuera de su dato '
             '(rectificaciones/registro.yaml:rectificaciones[0])')

    def arbol_de(self, texto, *args):
        """(exit, salida) de --tree sobre el registro escrito tal cual, con sus comentarios."""
        self.registro.write_text(texto, encoding='utf-8')
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = RC.main(['--registro', str(self.registro), '--tree', *args])
        return code, out.getvalue()

    def test_a_free_dato_names_no_endpoint_but_its_entity(self):
        # Spec 071 AC161 (19b-1): sin la forma de la fila de una arista, el dato no nombra ningún extremo; sus ids se
        # barren como los de cualquier otro campo y solo la `entidad` es visible. «Retirada de [tercero] respecto a
        # <visible>; anteriormente <oculto>» revela al oculto; con solo la entidad, nada. Igual con --snapshot (AC149).
        oculto = 'Person:oculto-centinela'
        repo = self.en_el_repo()
        for caso, dato, problema in (
                ('revela al oculto', f'Retirada de [tercero] respecto a {FULANA}; anteriormente {oculto}', True),
                ('solo su entidad', f'Retirada de [tercero] respecto a {FULANA}', False)):
            marcada = {**fila('R-2026-09-24-0001', U_VIVA), 'dato': dato}
            for modo in (('--tree',), ('--snapshot', str(self.snap), '--root', str(self.root), '--overlay', repo)):
                if modo[0] == '--snapshot' and not con_pipeline():
                    continue  # el overlay con reglas necesita el loader
                with self.subTest(caso=caso, modo=modo[0]):
                    code, out = self.run_check([marcada], *modo)
                    self.assertEqual(self.FUERA in out, problema, out)
                    self.assertNotIn('centinela', out)

    def test_the_whole_own_row_is_swept_without_the_census(self):
        # Spec 071 AC164 (19b-4): sin el censo, la fila marcada se barre entera desde su entrada del fichero (sus
        # valores, sus claves y sus comentarios), salvo el valor de su `unidad`. Un id en un comentario de la fila o
        # como una clave suya es otro extremo; uno de los que nombran las posiciones de su dato o su entidad, no.
        oculto = 'Person:oculto-centinela'
        marcada = fila('R-2026-09-24-0001', U_VIVA)
        texto = yaml.safe_dump({'rectificaciones': [marcada]}, allow_unicode=True, sort_keys=False)
        fuente = '  fuente: auto-ficticio.pdf p.3\n'
        self.assertIn(fuente, texto)
        casos = (('el oculto en un comentario', texto.replace(fuente, f'{fuente[:-1]}  # {oculto}\n'), True),
                 ('el oculto como clave', yaml.safe_dump({'rectificaciones': [{**marcada, oculto: 'x'}]},
                                                        allow_unicode=True, sort_keys=False), True),
                 ('su entidad en un comentario', texto.replace(fuente, f'{fuente[:-1]}  # {FULANA}\n'), False))
        for caso, registro, problema in casos:
            with self.subTest(caso=caso):
                code, out = self.arbol_de(registro)
                self.assertEqual(code, 1 if problema else 0, out)
                self.assertEqual(self.FUERA in out, problema, out)
                self.assertNotIn('centinela', out)

    def test_an_escaped_fingerprint_is_still_a_fingerprint(self):
        # Spec 071 AC163 (19b-3): además del texto literal se barren las claves y los escalares tal como los lee el
        # cargador: un escape (`a` es `a`) no esconde la huella de una unidad, ni en la propia fila ni en otro YAML,
        # en un valor o en una clave. Un JSON ya se barría leído.
        repo = self.en_el_repo()
        marcada = fila('R-2026-09-24-0001', U_VIVA)
        texto = yaml.safe_dump({'rectificaciones': [marcada]}, allow_unicode=True, sort_keys=False)
        fuente = '  fuente: auto-ficticio.pdf p.3\n'
        escapada = '\\u0061aaaaaaa'
        huella = 'REGISTRO R-2026-09-24-0001: huella de una fila anonimizada fuera de su regla opaca'
        casos = (('en la fuente de la fila', texto.replace(fuente, f'  fuente: "auto {escapada}"\n'), None, None,
                  'rectificaciones/registro.yaml:rectificaciones[0]'),
                 ('en una clave de otro YAML', texto, 'boe/escapada.yaml', f'"{escapada}aa":\n- nota\n',
                  'boe/escapada.yaml:sección 1[0]'),
                 ('en un valor de otro YAML', texto, 'boe/escapada.yaml', f'nota: "{escapada}"\n',
                  'boe/escapada.yaml:sección 1'),
                 ('en un JSON', texto, 'boe/escapada.json', f'{{"nota": "{escapada}"}}', 'boe/escapada.json:clave 1'))
        for caso, registro, rel, contenido, posicion in casos:
            with self.subTest(caso=caso):
                for viejo in (Path(repo) / 'pipeline/data/boe').glob('escapada.*'):
                    viejo.unlink()
                if rel:
                    self.publico(rel, contenido)
                code, out = self.arbol_de(registro, '--overlay', repo)
                self.assertEqual(code, 1, out)
                self.assertIn(f'{huella} ({posicion})', out)

    def test_a_deny_entry_with_neither_form_is_a_problem_of_form(self):
        # Spec 071 AC159 (19a-3), en --tree, lo que corre el CI: una entrada deny que no es ni una opaca exacta ni una
        # explícita (una variante de `unidad` en mayúsculas o con blancos, otra clave) es un problema de forma.
        marcada = fila('R-2026-09-24-0001', U_VIVA)
        motivo = f'063 auditoría escalonada 2026-09-24 [{U_VIVA[:8]}]: {MOTIVO_AJENA}'
        forma = ('REGISTRO relations/relations.yaml:deny[0]: regla deny con forma inválida: una clave que no es de una '
                 'regla deny (AC159)')
        for caso, regla in (('Unidad', {'Unidad': U_VIVA, 'reason': motivo}),
                            ('unidad con un blanco', {' unidad': U_VIVA, 'reason': motivo}),
                            ('una clave de más', {'source_id': FULANA, 'target_id': MENGANO, 'type': 'PAID',
                                                  'nota': 'x', 'reason': 'x'})):
            with self.subTest(caso=caso):
                code, out = self.run_check([marcada], '--tree', '--overlay', self.overlay(deny=[regla]))
                self.assertEqual(code, 1, out)
                self.assertIn(forma, out)
        code, out = self.run_check([marcada], '--tree', '--overlay', self.overlay(deny=[{'unidad': U_VIVA,
                                                                                          'reason': motivo}]))
        self.assertEqual(code, 0, out)


    def test_an_explicit_deny_the_loader_rejects_is_a_problem_of_form(self):
        # Spec 071 AC176 (20b-4): la forma de una deny explícita, en --tree, con la misma función que el loader
        # (`alcance.forma_deny`), no solo sus claves: sin un tipo válido o con un extremo sin resolver es un problema,
        # con el motivo del loader y sin ningún valor. Una explícita válida y una opaca válida, nada.
        marcada = fila('R-2026-09-24-0001', U_VIVA)
        motivo = f'063 auditoría escalonada 2026-09-24 [{U_VIVA[:8]}]: {MOTIVO_AJENA}'
        opaca = {'unidad': U_VIVA, 'reason': motivo}
        for caso, regla, razon in (('vacía', {}, 'sin un tipo de relación válido'),
                                   ('solo un motivo', {'reason': 'x'}, 'sin un tipo de relación válido'),
                                   ('solo un tipo', {'type': 'PAID'}, 'un extremo sin resolver')):
            with self.subTest(caso=caso):
                code, out = self.run_check([marcada], '--tree', '--overlay', self.overlay(deny=[opaca, regla]))
                self.assertEqual(code, 1, out)
                self.assertIn(f'REGISTRO relations/relations.yaml:deny[1]: regla deny con forma inválida: {razon} '
                              '(AC159)', out)
        valida = {'source_id': FULANA, 'target_id': MENGANO, 'type': 'PAID', 'reason': 'x'}
        code, out = self.run_check([marcada], '--tree', '--overlay', self.overlay(deny=[opaca, valida]))
        self.assertEqual(code, 0, out)


class ComponerTest(unittest.TestCase):
    """Spec 071 AC170: el superconjunto de lo que las reglas `fix` pueden hacer con una arista en varias pasadas del
    Path A (`componer`) invierte también una simétrica, en su sentido, como `_SWAP_Q`."""

    @staticmethod
    def regla(inicio, fin, tipo, **campos):
        return {'source_id': inicio, 'target_id': fin, 'type': tipo, 'corrected_type': None, 'swap': False, 'set': None,
                'doc': None, 'quote_contains': None, 'kinds': None, 'reason': None, **campos}

    def test_a_swap_inverts_a_symmetric_edge_too(self):
        value = {'type': 'ASSOCIATED_WITH', 'start': FULANA, 'end': MENGANO,
                 'props': {'source_kind': 'judicial', 'source_doc': 'auto-ficticio.pdf', 'source_quote': CITA}}
        firma = RC._estado(value)[3]
        swap = self.regla(FULANA, MENGANO, 'ASSOCIATED_WITH', swap=True)
        retipo = self.regla(MENGANO, FULANA, 'ASSOCIATED_WITH', corrected_type='OWNS')
        estados = RC.componer(value, [swap, retipo])
        # La inversión, y lo que un retipo posterior desde el otro sentido hace de ella: un tipo dirigido al revés.
        self.assertIn(('ASSOCIATED_WITH', MENGANO, FULANA, firma), estados)
        self.assertIn(('OWNS', MENGANO, FULANA, firma), estados)
        # En su sentido, como `_SWAP_Q`: un swap escrito al revés no casa la arista y no la invierte.
        al_reves = RC.componer(value, [self.regla(MENGANO, FULANA, 'ASSOCIATED_WITH', swap=True)])
        self.assertNotIn(('ASSOCIATED_WITH', MENGANO, FULANA, firma), al_reves)


class TrasElOverlayTest(unittest.TestCase):
    """Spec 071 AC158 (19a-2): lo que publica el grafo tras el overlay, con las fases del loader tal como las ejecutan
    `_RETYPE_Q`, `_SET_Q` y `_SWAP_Q`: cada una contra el estado en que la deja la anterior y una sola transformación
    por arista. Contrastado una vez contra el loader real en un Neo4j efímero (la entrega de la enmienda 21-quater)."""

    @staticmethod
    def regla(inicio, fin, tipo, **campos):
        return {'source_id': inicio, 'target_id': fin, 'type': tipo, 'corrected_type': None, 'swap': False, 'set': None,
                'doc': None, 'quote_contains': None, 'kinds': None, 'reason': None, **campos}

    def test_each_phase_runs_once_from_its_initial_state(self):
        props = {'source_kind': 'judicial', 'source_doc': 'auto-ficticio.pdf', 'source_quote': CITA}
        index = {}
        for inicio, fin, tipo in ((FULANA, MENGANO, 'MET_WITH'), (FULANA, EMPRESA, 'OWNS'), (ZUTANO, EMPRESA, 'OWNS'),
                                  (FULANA, ZUTANO, 'ASSOCIATED_WITH')):
            index.setdefault(RC._par(tipo, inicio, fin), []).append((inicio, fin, dict(props)))
        fix = [self.regla(FULANA, MENGANO, 'MET_WITH', corrected_type='PAID', reason='r1'),
               self.regla(FULANA, MENGANO, 'PAID', corrected_type='ASSOCIATED_WITH', reason='r2'),
               self.regla(FULANA, EMPRESA, 'OWNS', set={'note': 'nota primera'}, reason='s1'),
               self.regla(FULANA, EMPRESA, 'OWNS', set={'note': 'nota segunda', 'currency': 'EUR'}, reason='s2'),
               self.regla(ZUTANO, EMPRESA, 'OWNS', swap=True, reason='w1'),
               self.regla(EMPRESA, ZUTANO, 'OWNS', swap=True, reason='w2'),
               self.regla(FULANA, ZUTANO, 'ASSOCIATED_WITH', swap=True, reason='z1')]
        deny = [self.regla(FULANA, MENGANO, 'ASSOCIATED_WITH', reason='d1')]
        vivas = {(clave[0], inicio, fin): copia for clave, copias in RC.tras_el_overlay(index, deny, fix).items()
                 for inicio, fin, copia in copias}
        # (i) el retipo de la primera regla, sin encadenar: la MET_WITH queda PAID y la deny ASSOCIATED_WITH no la
        # retira.
        self.assertIn(('PAID', FULANA, MENGANO), vivas)
        # (ii) la carga y el motivo de la primera regla que casa, no la suma de las dos.
        self.assertEqual(vivas[('OWNS', FULANA, EMPRESA)], {**props, 'note': 'nota primera', 'qa_set_run': 's1'})
        # (iii) una sola inversión aunque la case una regla en cada sentido; también la de una simétrica.
        self.assertIn(('OWNS', EMPRESA, ZUTANO), vivas)
        self.assertIn(('ASSOCIATED_WITH', ZUTANO, FULANA), vivas)
        self.assertEqual(len(vivas), 4)


class PropioTest(unittest.TestCase):
    """Spec 071 AC215: el texto propio de un elemento (`Entrada.propio`) es el suyo sin los valores de sus campos de fuente
    (`quote_contains`, `source_quote`, `quote`, `cita`), en blanco por su posición y en cualquier estilo de escalar, con
    las mismas líneas: lo que esa cita contiene, dicho en otro campo o en un comentario, sigue ahí."""

    @staticmethod
    def elemento(texto):
        entradas = [e for e in RC._entradas_yaml(Path('regla.yaml'), Path('boe/regla.yaml'), texto)
                    if isinstance(e[4], dict)]
        assert len(entradas) == 1, entradas
        return entradas[0]

    def test_the_value_of_a_source_field_is_blank_in_every_scalar_style(self):
        for caso, campo in (('plano', 'quote_contains: Terciano'),
                            ('con comillas dobles y un escape', 'quote_contains: "Tercian\\u006f"'),
                            ('con comillas simples', "quote_contains: 'Terciano'"),
                            ('de bloque', 'quote_contains: |\n      Terciano'),
                            ('plegado', 'quote_contains: >-\n      Terciano'),
                            ('con un comentario de cola', 'quote_contains: Terciano  # Terciano, nuestro')):
            with self.subTest(caso=caso):
                texto = f'items:\n- reason: Terciano lo negoció\n  {campo}\n  note: cierre\n'
                _, literal, _, _, valor, _, (propio, _) = self.elemento(texto)
                self.assertEqual(valor['quote_contains'].strip(), 'Terciano')
                # El valor del campo se va de las dos copias (la literal y la leída); lo demás se queda: el motivo en las
                # dos y el comentario de cola, que es nuestro.
                self.assertEqual(propio.count('Terciano'), 2 + (1 if '# Terciano' in campo else 0))
                self.assertGreater(literal.count('Terciano'), propio.count('Terciano'))
                self.assertIn('reason: Terciano lo negoció', propio)
                # Las líneas del texto literal siguen siendo las mismas: el campo siguiente está en su sitio.
                self.assertEqual(literal.splitlines().index('  note: cierre'),
                                 propio.splitlines().index('  note: cierre'))

    def test_only_text_fields_count_and_at_any_depth(self):
        texto = ('items:\n- reason: Terciano\n  quote: 12345678\n  anidado:\n    source_quote: Terciano\n'
                 '    otra:\n    - cita: Terciano\n    - note: Terciano\n')
        _, _, _, _, _, _, (propio, _) = self.elemento(texto)
        # `quote: 12345678` es un entero, no un texto; `source_quote` y `cita`, anidados, sí; `note`, nuestro.
        self.assertIn('12345678', propio)
        self.assertEqual(propio.count('Terciano'), 2 * 2)

    def test_a_text_shared_with_another_field_through_an_anchor_stays_ours(self):
        # Un ancla que un campo de fuente comparte con otro campo: el texto es también nuestro, y no se borra de los dos.
        for caso, texto in (('el ancla en el otro campo', 'items:\n- nota: &n Terciano\n  quote_contains: *n\n'),
                            ('el ancla en el campo de fuente', 'items:\n- quote_contains: &q Terciano\n  nota: *q\n')):
            with self.subTest(caso=caso):
                *_, (propio, _) = self.elemento(texto)
                self.assertIn('Terciano', propio)

    def test_a_section_that_is_not_a_list_has_its_own_text_too(self):
        entradas = [e for e in RC._entradas_yaml(Path('regla.yaml'), Path('boe/regla.yaml'),
                                                 'mapa:\n  a:\n    quote: Terciano\n    nota: Terciano\n')
                    if e[6] is not None]
        (_, literal, *_, (propio, _)), = entradas
        # Se va el valor del campo de fuente en el texto literal y en el leído; `nota`, nuestra, se queda en los dos.
        self.assertEqual(literal.count('Terciano') - propio.count('Terciano'), 2)
        self.assertEqual(propio.count('Terciano'), 2)

    def test_a_json_entry_loses_the_values_of_its_source_fields(self):
        entradas = RC._entradas_json(Path('boe/regla.json'),
                                     '{"items": [{"reason": "Terciano", "quote_contains": "Terciano", "quote": [1]}]}')
        (_, texto, _, _, _, _, (propio, _)), = entradas
        self.assertEqual(texto.count('Terciano'), 2)
        self.assertEqual(propio.count('Terciano'), 1)
        self.assertIn('"quote": [1]', propio)

    def test_the_scalars_of_an_element_are_read_apart_without_the_quotes_of_the_syntax(self):
        # Spec 071 AC222: `escalares` son las claves y los escalares leídos del elemento, uno a uno, sin los valores de
        # sus campos de fuente y sin las comillas de la sintaxis; en YAML, además, los comentarios de su bloque.
        texto = ('items:\n- reason: "Terciano \\u00e9"  # nuestro\n  nota: \'otra\'\n  quote_contains: Terciano\n'
                 '  # aparte\n')
        *_, (_, escalares) = self.elemento(texto)
        self.assertEqual(escalares, ('items', 'reason', 'Terciano é', 'nota', 'otra', 'quote_contains',
                                     '# nuestro', '# aparte'))
        (_, _, _, _, _, _, (_, escalares)), = RC._entradas_json(
            Path('boe/regla.json'), '{"items": [{"reason": "Terciano \\u00e9", "n": 1, "quote_contains": "Terciano"}]}')
        self.assertEqual(escalares, ('items', 'reason', 'Terciano é', 'n', 'quote_contains', ''))
        # Una línea literal de un JSON con una clave repetida: sus cadenas leídas, sin comillas.
        lineas = [e for e in RC._entradas_json(Path('boe/regla.json'), '{"a": "x \\"y\\"", "a": 2}')
                  if e[5] == '{"a": "x \\"y\\"", "a": 2}']
        (*_, (_, escalares)), = lineas
        self.assertEqual(escalares, ('a', 'x "y"', 'a'))

    def test_a_quote_is_exempt_by_its_span_not_by_what_it_contains(self):
        # `tramos_citados`: los tramos entre comillas que su documento dice, por su posición, con sus comillas.
        texto = 'Terciano llegó. Luego «Terciano … salió sin declarar» y Terciano se fue.'
        documentos = {'a.pdf': RC._llano('Terciano, tras la reunión, salió sin declarar nada.')}
        (inicio, fin), = RC.tramos_citados(texto, {'a.pdf'}, documentos)
        self.assertEqual(texto[inicio:fin], '«Terciano … salió sin declarar»')
        self.assertEqual(RC.tramos_citados(texto, {'otro.pdf'}, documentos), [])
        self.assertEqual(RC.tramos_citados(texto.replace('declarar', 'huir'), {'a.pdf'}, documentos), [])

    def test_only_a_pure_chunk_is_document_text(self):
        # Un nodo que además de Chunk lleva una etiqueta de entidad (Person+Chunk) es una ficha: su `text` no es el
        # documento fuente, así que no prueba una cita (`textos_de_documentos`, AC197); `_fragmento` es el mismo criterio.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'nodes.jsonl').write_text(''.join(json.dumps(n) + '\n' for n in (
                {'labels': ['Chunk'], 'ref': '1', 'props': {'doc_id': 'a.pdf', 'text': 'tramo uno', 'chunk_index': 0}},
                {'labels': ['Person', 'Chunk'], 'ref': '2',
                 'props': {'entity_id': 'Person:x', 'doc_id': 'a.pdf', 'text': 'tramo falso', 'chunk_index': 1}},
                {'labels': ['Chunk', 'Company'], 'ref': '3',
                 'props': {'entity_id': 'Company:x', 'doc_id': 'b.pdf', 'text': 'otro falso'}})))
            self.assertEqual(RC.textos_de_documentos(tmp), {'a.pdf': RC._llano('tramo uno')})
        self.assertTrue(RC._fragmento(['Chunk']) and RC._fragmento(['Document', 'Chunk']))
        self.assertFalse(any(RC._fragmento(e) for e in (None, [], ['Document'], ['Person', 'Chunk'], ['Chunk', 'Company'])))


class TiposDeNombreTest(unittest.TestCase):
    """Spec 070 AC338 (4ª pasada de Codex): `export_vivo` falla cerrado si `name`, `canonical_name`, `name_original` o una denominación de
    un nodo (o de una arista) no es una cadena, o si `aliases` no es una lista de cadenas: la web pinta una lista de fragmentos como su
    texto y ni el homónimo ni los caracteres la leen entera. Un Chunk o un Document puros no tienen nombre y quedan fuera."""

    NODO = {'labels': ['Court'], 'ref': '1', 'props': {'entity_id': 'Court:sala', 'name': 'Sala'}}
    OTRO = {'labels': ['Person'], 'ref': '2', 'props': {'entity_id': 'Person:fulano', 'name': 'Fulano'}}
    ARISTA = {'ref': 'r0', 'type': 'ASSOCIATED_WITH', 'start': '2', 'end': '2', 'props': {}}   # un export sin aristas no vale

    def leer(self, nodos, aristas=()):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'nodes.jsonl').write_text(''.join(json.dumps(n) + '\n' for n in nodos), encoding='utf-8')
            (Path(tmp) / 'relationships.jsonl').write_text(''.join(json.dumps(a) + '\n' for a in aristas or [self.ARISTA]),
                                                                  encoding='utf-8')
            return RC.export_vivo(tmp)

    def con(self, etiquetas=('Court',), **props):
        return {'labels': list(etiquetas), 'ref': '1', 'props': {'entity_id': 'Court:sala', 'name': 'Sala', **props}}

    def test_export_vivo_solo_lee_la_estructura_y_la_guarda_de_homonimos_es_seudonimos_check(self):
        """Spec 070 AC338 (6ª pasada de Codex): un civil con el nombre de un seudónimo con una puntuación entre cada letra, sean cuantas sean
        sus piezas (10, 11, 12, 500), es un export de la forma del grafo: `export_vivo` lo lee y no lo rechaza (su trabajo es la estructura y
        los tipos del nombre). Quien lo rechaza es `seudonimos_check` (`verificar_snapshot`, `test_seudonimos.ColisionSinTopesTest`)."""
        for piezas in (10, 11, 12, 500):
            valor = 'D.' * max(piezas - 11, 0) + '.'.join('mariaisabel'[:piezas - 1]) + ('.' + 'mariaisabel'[piezas - 1:] if piezas <= 11 else '')
            with self.subTest(piezas=piezas):
                index, ids = self.leer([self.con(name=valor), self.OTRO])
                self.assertEqual(ids, {'Court:sala', 'Person:fulano'})

    def test_un_nombre_que_no_es_una_cadena_para_con_exit_2(self):
        for etiquetas in (('Court',), ('Person',), ('Person', 'Document'), ('Company', 'Chunk')):
            for clave in ('name', 'canonical_name', 'name_original', 'denominacion', 'amp_denominacion'):
                for valor in (['\u041escar'], ['O', 'scar'], [['Oscar']], 5, 5.5, True, {'a': 'Oscar'}, None, []):
                    with self.subTest(etiquetas=etiquetas, clave=clave, valor=repr(valor)):
                        with self.assertRaises(RC.ExportInvalido) as e:
                            self.leer([self.con(etiquetas, **{clave: valor}), self.OTRO])
                        self.assertIn(f'`{clave}` no es una cadena', str(e.exception))
                        self.assertNotIn('scar', str(e.exception))

    def test_unos_alias_que_no_son_una_lista_de_cadenas_paran(self):
        for valor in ('Sala Civil', '', 5, True, None, {'a': 'Sala'}, [['Sala']], [5], [None], ['Sala', 7], [{'a': 'Sala'}]):
            with self.subTest(valor=repr(valor)):
                with self.assertRaises(RC.ExportInvalido) as e:
                    self.leer([self.con(aliases=valor), self.OTRO])
                self.assertIn('`aliases`', str(e.exception))

    def test_la_denominacion_de_una_arista_tambien(self):
        arista = {'ref': 'r', 'type': 'RECEIVED_GRANT', 'start': '1', 'end': '2', 'props': {'denominacion_bdns': ['Sociedad']}}
        with self.assertRaises(RC.ExportInvalido) as e:
            self.leer([self.NODO, self.OTRO], [arista])
        self.assertIn('relationships.jsonl', str(e.exception))
        arista['props'] = {'denominacion_bdns': 'Sociedad'}
        self.leer([self.NODO, self.OTRO], [arista])

    def test_lo_valido_se_lee_y_un_documento_puro_queda_fuera(self):
        index, ids = self.leer([self.OTRO, self.con(name='', canonical_name='Sala', name_original='S', denominacion='SL', aliases=[]),
                                self.con(aliases=['La Sala']) | {'ref': '3', 'props': {'entity_id': 'Court:otra', 'aliases': ['La Sala']}},
                                {'labels': ['Document'], 'ref': '4', 'props': {'doc_id': 'a.pdf', 'name': ['x'], 'aliases': 5}},
                                {'labels': ['Chunk'], 'ref': '5', 'props': {'chunk_id': 'c', 'name': 5}}])
        self.assertEqual(ids, {'Court:sala', 'Court:otra', 'Person:fulano'})


class NormalizadoTest(unittest.TestCase):
    """Spec 071 AC226: un identificador (un entity_id, una huella) se compara con un texto público normalizado igual los
    dos: sin caracteres de formato (Cf), NFKC, y mayúsculas solo para comparar; los límites de token de `_nombra` se
    conservan."""

    VIEJO = 'Person:jose-ficticio'

    def test_an_id_is_named_through_invisible_characters_width_and_case(self):
        ancho = ''.join(chr(ord(c) + 0xFEE0) if '!' <= c <= '~' else c for c in self.VIEJO)
        for caso, escrito in (('literal', self.VIEJO), ('ZWSP', 'Person:jo\u200bse-ficticio'),
                              ('ZWJ', 'Person:jo\u200dse-ficticio'), ('guion blando', 'Person:jo\u00adse-ficticio'),
                              ('BOM', '\ufeffPerson:jose-ficticio'), ('ancho completo', ancho),
                              ('unificador de grafemas', 'Person:jo\u034fse-ficticio'),
                              ('selector de variación', 'Person:jos\ufe0fe-ficticio'),
                              ('relleno de hangul', 'Person:jo\u3164se-ficticio'),
                              ('mayúsculas', self.VIEJO.upper()),
                              ('todo junto', ancho[:9] + '\u200b' + ancho[9:].upper())):
            with self.subTest(caso=caso):
                self.assertTrue(RC._nombra(self.VIEJO, f'ver {escrito} y más'))
                self.assertTrue(RC._nombra(self.VIEJO, escrito))
        # Un carácter invisible entre el id y lo que lo rodea no los pega: sigue siendo un token propio.
        for escrito in (self.VIEJO, ancho, self.VIEJO.upper()):
            with self.subTest(separado=escrito):
                self.assertTrue(RC._nombra(self.VIEJO, f'{escrito}\u200bmás'))
                self.assertTrue(RC._nombra(self.VIEJO, f'más\u200b{escrito}'))

    def test_the_token_limits_are_kept_after_normalising(self):
        for escrito in ('Person:jose-ficticio-2', 'Person:jose-ficticiox', 'xPerson:jose-ficticio',
                        'Person:jose-ficticio.pdf', 'Person:otro-ficticio', 'jose-ficticio', 'Person:jo se-ficticio',
                        'Ｐｅｒｓｏｎ：ｊｏｓｅ-ｆｉｃｔｉｃｉｏ-２'):
            with self.subTest(escrito=escrito):
                self.assertFalse(RC._nombra(self.VIEJO, f'ver {escrito} y más'))
        self.assertFalse(RC._nombra('', 'cualquier texto'))
        self.assertTrue(RC._nombra('informe-uco.pdf', 'ver informe\u00ad-uco.pdf y más'))

    def test_a_text_with_nothing_to_remove_is_not_copied_and_llano_is_unchanged(self):
        # Medido (spec 071 AC226): `--snapshot` pasó de 30 a 165 s cuando `_formas` copiaba cada texto en cada fila; sin
        # nada que quitar devuelve el propio texto. Y `_llano`, con su atajo para el ASCII, es el de siempre.
        for texto in ('Person:jose-ficticio y más', 'sólo ascii', 'ñandú “con comillas” — y raya'):
            with self.subTest(texto=texto):
                self.assertIs(RC._formas(texto)[0], texto)
        for texto in ('Jos\u00e9 Ficticio', 'Jose\u0301 Ficticio', 'Jo\u200bse  FICTICIO', '\uff2a\uff4f\uff53\uff45',
                      '\u00f1\u0483x', 'abc   DEF'):
            with self.subTest(llano=texto):
                visible = ''.join(c for c in texto if unicodedata.category(c) != 'Cf')
                esperado = ''.join(c for c in unicodedata.normalize('NFKD', RC.cita_normalizada(visible))
                                   if not unicodedata.combining(c))
                self.assertEqual(RC._llano(texto), esperado)

    def test_the_ids_of_a_text_are_read_normalised(self):
        ancho = 'Ｐｅｒｓｏｎ：ｊｏｓｅ-ｆｉｃｔｉｃｉｏ'
        for escrito in ('Person:jose-ficticio', 'Person:jo\u200bse-ficticio', 'Person:jo\u00adse-ficticio', ancho,
                        'PERSON:JOSE-FICTICIO', 'person:jose-ficticio'):
            with self.subTest(escrito=escrito):
                self.assertIn(self.VIEJO, RC.ids_en_texto(f'ver {escrito} y más'))
        self.assertEqual(RC.ids_en_texto(f'ver {self.VIEJO} y más'), [self.VIEJO])
        self.assertEqual(RC.ids_en_texto('ver PERSON:Jose-Ficticio y Company:ficcionbar'),
                         [self.VIEJO, 'Company:ficcionbar'])
        self.assertEqual(RC.ids_en_texto('nada de esto'), [])

    def test_a_unit_reference_is_read_through_invisible_characters_and_width(self):
        unidad = 'f86b4e9c' + '0' * 56
        for escrito in ('f86b4e9c', 'f86b4e\u200b9c', 'f86b\u00ad4e9c', 'ｆ８６ｂ４ｅ９ｃ', 'F86B4E9C'):
            with self.subTest(escrito=escrito):
                self.assertEqual(RC.referencias_hex(f'se conserva la arista {escrito}', {unidad}), {unidad})
        self.assertEqual(RC.referencias_hex('se conserva la arista f86b4e9d', {unidad}), set())

    # --- enmienda 28 (spec 071 AC230 y AC231; 26ª pasada de Codex, 12-10-2026) --------------------------------------

    # Las familias de caracteres que no se ven, de la categoría Cf y de las que no lo son (AC230).
    INVISIBLES = {'unificador de grafemas': '\u034f', 'selector de variación 1': '\ufe00',
                  'selector de variación 16': '\ufe0f', 'selector de variación 17': '\U000e0100',
                  'selector de variación 256': '\U000e01ef', 'relleno de hangul (choseong)': '\u115f',
                  'relleno de hangul (jungseong)': '\u1160', 'relleno de hangul (compatibilidad)': '\u3164',
                  'relleno de hangul de media anchura': '\uffa0', 'vocal inherente de jemer 1': '\u17b4',
                  'vocal inherente de jemer 2': '\u17b5', 'selector mongol 1': '\u180b',
                  'selector mongol 2': '\u180c', 'selector mongol 3': '\u180d', 'selector mongol 4': '\u180f',
                  'separador de vocal mongol': '\u180e', 'anchura cero': '\u200b',
                  'unión de palabras': '\u2060', 'guion blando': '\u00ad', 'BOM': '\ufeff'}

    def test_names_and_ids_share_one_definition_of_what_is_not_seen(self):
        # Spec 071 AC230 (26a-1, la reproducción de Codex): `_llano` solo quitaba la categoría Cf y `_formas`, además,
        # el unificador de grafemas, los rellenos de hangul y los selectores de variación: «José<U+034F> Ficticio» no
        # era «jose ficticio». Una sola función (`_sin_invisibles`) para los dos y para todo filtro previo.
        for familia, c in self.INVISIBLES.items():
            with self.subTest(familia=familia):
                self.assertEqual(RC._llano(f'Jos\u00e9{c} Ficticio'), 'jose ficticio')
                self.assertEqual(RC._llano(f'Jos{c}\u00e9 Ficticio'), 'jose ficticio')
                self.assertEqual(RC._visible(f'Jos\u00e9{c} Ficticio'), 'José Ficticio')
                self.assertEqual(RC._nombre_corto(f'Terci{c}ano'), 'terciano')
                self.assertTrue(RC._es_nombre(f'Jos\u00e9{c} Ficticio Apellido', 'Person'))
                self.assertTrue(RC._nombra('Person:jose-ficticio', f'ver Person:jo{c}se-ficticio y más'))
                self.assertEqual(RC.referencias_hex(f'la arista f86b{c}4e9c', {'f86b4e9c' + '0' * 56}),
                                 {'f86b4e9c' + '0' * 56})
                self.assertTrue(RC.trozo_de_cita({'fuente': f'auto-ficticio.pdf p.3: Fulana{c} de Tal pagó a Mengano'
                                                            ' Ficticio la comisión acordada'}, {RC._llano(CITA)}))
        self.assertEqual(RC._llano('José Ficticio'), 'jose ficticio')

    def test_what_llano_does_not_see_is_what_formas_does_not_see_over_all_of_unicode(self):
        # La propiedad: para todo carácter de Unicode, `_llano` y `_visible` coinciden en si lo quitan. Quedan fuera los
        # que `_llano` quita por otra razón (las marcas de combinación, que son las tildes que también quita).
        distintos = []
        for punto in range(0x110000):
            c = chr(punto)
            if 0xD800 <= punto <= 0xDFFF or all(unicodedata.combining(x) for x in unicodedata.normalize('NFKD', c)):
                continue
            if (RC._llano(f'a{c}b') == 'ab') != (RC._visible(f'a{c}b') == 'ab'):
                distintos.append(f'U+{punto:04X}')
        RC._formas.cache_clear()
        RC._plegadas.cache_clear()
        self.assertEqual(distintos, [])

    def test_the_ids_of_a_text_fold_like_nombra(self):
        # AC231 (26a-2): `_nombra('Person:otto-ss', 'Person:otto-ß')` es True; `ids_en_texto` leía `Person:otto` y
        # `Person:otto-ss`: una fila cuyo extremo visible es `Person:otto` dejaba pasar el primero y bloqueaba el
        # segundo. Los ids salen de las formas ya plegadas, con `casefold`.
        for escrito, esperado in (('Person:otto-\u00df', 'Person:otto-ss'), ('Person:otto-ss', 'Person:otto-ss'),
                                  ('Person:otto-\u1e9e', 'Person:otto-ss'), ('Person:otto-\ufb06', 'Person:otto-st'),
                                  ('Person:OTTO-\u00df', 'Person:otto-ss'),
                                  ('PERSON:otto-\ufb03', 'Person:otto-ffi')):
            with self.subTest(escrito=escrito):
                self.assertEqual(RC.ids_en_texto(f'ver {escrito} y más'), [esperado])
                self.assertTrue(RC._nombra(esperado, escrito))
        # Con un invisible dentro, también la forma con un blanco en su lugar (AC226): el id de siempre y, además, el
        # que parte el invisible.
        self.assertIn('Person:otto-ss', RC.ids_en_texto('ver Person:ot\u200bto-\u00df y más'))
        self.assertEqual(RC.ids_en_texto('ver Person:otto-\u00df y Person:otto'), ['Person:otto-ss', 'Person:otto'])

    def test_no_text_in_the_position_of_the_type_makes_the_id_reader_fail(self):
        # AC231: `re.IGNORECASE` aceptaba `Crıme` (i sin punto) y `Crİme` (İ con punto), `tipo.lower()` no estaba en el
        # diccionario de tipos y el CLI terminaba con un `KeyError`. Todos los caracteres de Unicode en una posición
        # del tipo, y los de las categorías Lu, Ll, Lt y Lm en todas: nunca una excepción, y solo el id de verdad.
        for texto in ('Cr\u0131me:x', 'Cr\u0130me:x', 'Cr\u01c5me:x', 'Cr\ufb03me:x', '\u212aime:x'):
            with self.subTest(texto=texto):
                self.assertEqual(RC.ids_en_texto(texto), [])
        vistos = 0
        for tipo in ('Person', 'Company', 'Crime'):
            for posicion in range(len(tipo)):
                todo_unicode = posicion == 2 and tipo == 'Crime'
                for punto in range(0x110000):
                    c = chr(punto)
                    if 0xD800 <= punto <= 0xDFFF or not (
                            todo_unicode or unicodedata.category(c) in ('Lu', 'Ll', 'Lt', 'Lm')):
                        continue
                    vistos += 1
                    salida = RC.ids_en_texto(tipo[:posicion] + c + tipo[posicion + 1:] + ':x')
                    self.assertIn(salida, ([], [f'{tipo}:x']), f'U+{punto:04X} en {tipo}[{posicion}]')
        RC._formas.cache_clear()
        RC._plegadas.cache_clear()
        self.assertGreater(vistos, 1_000_000)


class PreparacionDeReglasSinRapidfuzzTest(unittest.TestCase):
    """AC278: el job de CI de la API corre `registro_check` y estas pruebas con `uv run --project api`, que no tiene
    rapidfuzz (solo `pipeline/pyproject.toml`). Preparar las reglas (`relations_loader`) no puede arrastrar el
    resolvedor de entidades: sin ello, tres pruebas de `CuerpoDeEscalarTest` caen con ModuleNotFoundError en el CI."""

    def test_relations_loader_imports_without_the_resolver_stack(self):
        codigo = ("import sys; sys.modules['rapidfuzz'] = None; sys.path.insert(0, 'pipeline');"
                  "from src import relations_loader; print(relations_loader.entity_id('Person', 'Ana Ficticia'))")
        res = subprocess.run([sys.executable, '-c', codigo], cwd=Path(__file__).resolve().parents[2],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr[-400:])
        self.assertEqual(res.stdout.strip(), 'Person:ana-ficticia')


class CuerpoDeEscalarTest(unittest.TestCase):
    """Spec 071 AC227: lo que un nodo YAML tiene en su tramo literal y no es ni sintaxis ni su valor leído (los
    comentarios de cabecera de un escalar de bloque o con ancla, el nombre del ancla, la etiqueta) es texto propio de la
    entrada. Una propiedad sobre entradas generadas, no solo casos."""

    @staticmethod
    def elemento(texto):
        entradas = [e for e in RC._entradas_yaml(Path('regla.yaml'), Path('boe/regla.yaml'), texto)
                    if isinstance(e[4], dict)]
        assert len(entradas) == 1, entradas
        return entradas[0]

    def test_the_header_comment_of_a_block_or_anchored_scalar_is_in_the_scalars(self):
        for caso, texto in (('plegado', 'reason: >- # comentario1 uno\n    cuerpo1\n'),
                            ('literal', 'reason: | # comentario1 uno\n    cuerpo1\n'),
                            ('indicadores', 'reason: >+2 # comentario1 uno\n    cuerpo1\n'),
                            ('ancla', 'reason: &q # comentario1 uno\n    cuerpo1\n'),
                            ('ancla y etiqueta', 'reason: &q !!str # comentario1 uno\n    cuerpo1\n'),
                            ('su línea', 'reason: &q !!str\n    # comentario1 uno\n    cuerpo1\n')):
            with self.subTest(caso=caso):
                entrada = self.elemento(f'items:\n- {texto}  n: 1\n')
                self.assertIn('comentario1 uno', ' '.join(entrada[6].escalares))
                self.assertNotIn('comentario1', str(entrada[4]))

    def test_the_cuerpo_of_a_scalar_is_its_value_without_header_anchor_or_tag(self):
        texto = 'items:\n- reason: &q !!str >- # c1\n    cuerpo1\n    cuerpo2\n  n: 1\n  m: |\n    otro\n'
        cuerpos = RC._tokens_de_texto(texto)[0]
        trozos = [texto[a:b] for a, b in cuerpos]
        self.assertIn('    cuerpo1\n    cuerpo2\n', trozos)
        self.assertIn('    otro\n', trozos)
        self.assertFalse([t for t in trozos if '>-' in t or '# c1' in t or '&q' in t or '!!str' in t])
        self.assertEqual(RC._comentarios(texto, cuerpos), {1: '# c1'})

    def test_a_quote_field_keeps_its_header_comment_in_the_own_text(self):
        # Un campo de fuente en bloque con un comentario de cabecera: se va el valor, no el comentario (misma clase).
        texto = 'items:\n- reason: neutro\n  quote_contains: >- # Terciano, nuestro\n    Terciano\n  n: 1\n'
        _, literal, _, _, _, _, (propio, escalares) = self.elemento(texto)
        self.assertIn('# Terciano, nuestro', propio)
        self.assertEqual(propio.count('Terciano'), 1)
        self.assertEqual(literal.count('Terciano'), 3)  # el comentario, el cuerpo y el valor leído
        self.assertIn('# Terciano, nuestro', escalares)

    def test_every_word_of_the_literal_is_in_the_scalars_or_the_value_of_a_source_field(self):
        # La propiedad: para entradas YAML generadas (escalares planos, entrecomillados, plegados, literales, con ancla
        # y etiqueta, con cabecera de bloque con indicadores y comentarios en todas las posiciones válidas), toda
        # palabra del texto literal aparece en `propio.escalares` (ni una se pierde), y las del comentario no están en
        # ningún valor leído. Los valores van sin escapes: así «ni una palabra del literal» es medible.
        generadas = 0
        for texto, comentado in self.entradas():
            with self.subTest(texto=texto):
                try:
                    cargado = yaml.safe_load(f'items:\n- {texto}')
                except yaml.YAMLError:
                    continue
                generadas += 1
                valor = cargado['items'][0]
                self.assertIn('cuerpo1', str(valor['reason']))
                _, literal, _, _, _, _, (propio, escalares) = self.elemento(f'items:\n- {texto}')
                dicho = ' '.join(escalares)
                for palabra in re.findall(r'[A-Za-z][A-Za-z0-9_]*', literal):
                    self.assertIn(palabra, dicho, f'la palabra {palabra!r} del literal se pierde')
                if comentado:
                    self.assertNotIn('comentario1', str(valor))
                    self.assertIn('comentario1', dicho)
        self.assertGreater(generadas, 150)

    def test_the_comments_are_numbered_like_the_marks_of_the_nodes(self):
        # Un salto de línea de Unicode (U+0085, U+2028, U+2029) o un CR solo cuenta como línea para PyYAML: el
        # comentario del segundo elemento no puede caer en el primero por numerarlas distinto.
        for salto in ('\u2028', '\u2029', '\x85', '\r'):
            with self.subTest(salto=salto.encode('unicode_escape')):
                texto = f'items:\n- reason: a{salto}    b\n  n: 1\n- reason: x # comentario1 Terciano\n  n: 2\n'
                primero, segundo = [e for e in RC._entradas_yaml(Path('regla.yaml'), Path('boe/regla.yaml'), texto)
                                    if isinstance(e[4], dict)]
                self.assertNotIn('comentario1', ' '.join(primero[6].escalares))
                self.assertIn('# comentario1 Terciano', segundo[6].escalares)

    def test_a_section_that_is_not_a_list_keeps_its_header_comments_too(self):
        for caso, texto in (('plegado', 'mapa:\n  a:\n    reason: >- # comentario1 uno\n      cuerpo1\n'),
                            ('ancla', 'mapa:\n  a:\n    reason: &ancla1 # comentario1 uno\n      cuerpo1\n')):
            with self.subTest(caso=caso):
                entradas = [e for e in RC._entradas_yaml(Path('regla.yaml'), Path('boe/regla.yaml'), texto)
                            if e[6] is not None]
                (entrada,) = entradas
                dicho = ' '.join(entrada[6].escalares)
                self.assertIn('comentario1 uno', dicho)
                self.assertEqual('ancla1' in texto, 'ancla1' in dicho)

    @staticmethod
    def entradas():
        """(entrada YAML, lleva un comentario que no es un valor): el campo `reason` en todas las formas de la
        propiedad."""
        cuerpo = '    cuerpo1\n    cuerpo2\n'
        for prop in ('', '&ancla1 ', '!!str ', '&ancla1 !!str '):
            for valor in ('cuerpo1 cuerpo2', "'cuerpo1 cuerpo2'", '"cuerpo1 cuerpo2"'):
                yield f'reason: {prop}{valor}\n  n: 1\n', False
                yield f'reason: {prop}{valor} # comentario1 comentario2\n  n: 1\n', True
                yield f'reason: {prop}{valor}\n  # comentario1 comentario2\n  n: 1\n', True
                yield f'reason: {prop}# comentario1 comentario2\n    {valor}\n  n: 1\n', True
                if prop:
                    yield f'reason: {prop.rstrip()}\n    # comentario1 comentario2\n    {valor}\n  n: 1\n', True
            yield f'reason: {prop}cuerpo1\n    cuerpo2\n  n: 1\n', False
            for indicador in ('>-', '>', '|', '|-', '>+2', '|2', '>2-', '|+'):
                yield f'reason: {prop}{indicador}\n{cuerpo}  n: 1\n', False
                yield f'reason: {prop}{indicador} # comentario1 comentario2\n{cuerpo}  n: 1\n', True
                yield f'reason: {prop}{indicador}\n{cuerpo}  # comentario1 comentario2\n  n: 1\n', True
                yield f'reason: {prop}{indicador}\n{cuerpo}# comentario1 comentario2\n  n: 1\n', True
                yield f'reason: {prop}{indicador}\n    cuerpo1\n    # sigue\n    cuerpo2\n  n: 1\n', False
        # En línea (flow): con ancla, etiqueta y comentarios tras la coma o entre campos.
        yield '{reason: cuerpo1 cuerpo2, n: 1}\n', False
        yield '{reason: &ancla1 cuerpo1, n: 1}\n', False
        yield "{reason: &ancla1 !!str 'cuerpo1 cuerpo2', # comentario1 comentario2\n    n: 1}\n", True
        yield '{reason: !!str "cuerpo1 cuerpo2", n: 1} # comentario1\n', True
        yield '{reason: [cuerpo1, &ancla1 cuerpo2], # comentario1\n    n: 1}\n', True
        yield 'reason: "cuerpo1 # sigue cuerpo2"\n  n: 1\n', False
        yield "reason: 'cuerpo1 # sigue cuerpo2'\n  n: 1\n", False
        yield 'reason: cuerpo1#sigue cuerpo2\n  n: 1\n', False

    # --- enmienda 28 (spec 071 AC232 y AC233; 26ª pasada de Codex, 12-10-2026) --------------------------------------

    # Los saltos de línea que admite PyYAML: CR, LF, CRLF (una unidad), NEL, LS y PS.
    SALTOS = ('\n', '\r', '\r\n', '\x85', '\u2028', '\u2029')

    def test_the_header_of_a_block_scalar_ends_at_the_first_line_break_pyyaml_accepts(self):
        # Spec 071 AC232 (26a-3, la reproducción de Codex): `_tokens_de_texto` buscaba solo `\n`; con CR, NEL, LS o
        # PS el cuerpo de `quote: |` cuyo contenido es `# Terciano` salía como (22, 22) en vez de (9, 22) y
        # `_comentarios` lo incorporaba a `propio.escalares`: el mismo texto de fuente pasaba a ser afirmación propia.
        # Con todos los separadores del encabezado y del cuerpo, y mezclados.
        for cabecera_sep in self.SALTOS:
            for cuerpo_sep in self.SALTOS:
                for indicador in ('|', '>', '|-', '>+', '|2', '>-2'):
                    for cola in ('', ' # cabecera1'):
                        with self.subTest(cabecera=repr(cabecera_sep), cuerpo=repr(cuerpo_sep), indicador=indicador,
                                          cola=cola):
                            encabezado = f'quote: {indicador}{cola}{cabecera_sep}'
                            cuerpo = f'  # cuerpo1 Terciano{cuerpo_sep}  # cuerpo2{cuerpo_sep}'
                            texto = f'{encabezado}{cuerpo}k: 1{cuerpo_sep}'
                            cuerpos = RC._tokens_de_texto(texto)[0]
                            self.assertIn((len(encabezado), len(encabezado) + len(cuerpo)), cuerpos)
                            self.assertEqual(RC._comentarios(texto, cuerpos), {0: cola.strip()} if cola else {})
                            self.assertIn('# cuerpo1', yaml.safe_load(texto)['quote'])

    def test_a_line_is_what_pyyaml_counts_as_a_line_for_every_text_it_reads(self):
        # AC232: `str.splitlines` parte además en VT, FF, FS, GS y RS; PyYAML solo en los suyos (y esos otros no son
        # imprimibles para él: no compone). Para todo texto que compone, las líneas del módulo son las que numera, con
        # cada separador y con mezclas de ellos; y uno que no compone no se da por limpio.
        trozos = ['a1: x', 'b2: |', '  c3', 'd4: [e5,', ' f6]', '# g7', 'h8: y']
        for n, _ in enumerate(self.SALTOS):
            seps = [self.SALTOS[(n + k) % len(self.SALTOS)] for k in range(len(trozos))]
            texto = ''.join(t + s for t, s in zip(trozos, seps))
            with self.subTest(seps=repr(seps)):
                lineas = RC._lineas_de(texto)
                tokens = list(yaml.scan(texto, Loader=yaml.SafeLoader))
                self.assertEqual(tokens[-1].end_mark.line, len(lineas))
                for token in tokens:
                    if isinstance(token, yaml.ScalarToken) and token.style is None:
                        self.assertIn(token.value, lineas[token.start_mark.line])
        for c in '\x0b\x0c\x1c\x1d\x1e':
            with self.subTest(c=repr(c)):
                texto = f'items:\n- a: 1{c}b: 2\n'
                self.assertNotEqual(len(texto.splitlines()), len(RC._lineas_de(texto)))
                entradas = RC._entradas_yaml(Path('x.yaml'), Path('boe/x.yaml'), texto)
                self.assertTrue([e for e in entradas if e[3] == RC.FICHERO_NO_VERIFICABLE])

    def test_blanking_a_multiline_unit_keeps_the_lines_aligned(self):
        # AC232 (la numeración de línea): poner en blanco el valor de la `unidad` no puede quitar saltos de línea; con
        # un valor entrecomillado en dos líneas, el barrido de las demás filas se corría una línea.
        texto = ('rectificaciones:\n- id: R-1\n  unidad: "aaaa\n    bbbb"\n  dato: x\n- id: R-2 # ZZMARCA\n'
                 '  dato: y\n')
        entradas = RC._entradas_yaml(Path('registro.yaml'), Path('rectificaciones/registro.yaml'), texto)
        segunda = [e for e in entradas if e[0].endswith('rectificaciones[1]')][0]
        self.assertIn('# ZZMARCA', segunda[5])
        self.assertIn('# ZZMARCA', segunda[1])

    # El comentario junto a un alias va con el valor expandido de ESA entrada (AC233): (caso, YAML, {elemento:
    # comentarios que lleva}).
    ALIAS = (
        ('un alias', 'base: &b {x: 1}\nitems:\n- *b # comentario1\n', {0: {'comentario1'}}),
        ('varios alias del mismo ancla', 'base: &b {x: 1}\nitems:\n- *b # comentario1\n- *b # comentario2\n- *b\n',
         {0: {'comentario1'}, 1: {'comentario2'}, 2: set()}),
        ('el ancla en el mismo elemento', 'items:\n- &b {x: 1}\n- *b # comentario1\n- y: 2 # comentario2\n',
         {0: set(), 1: {'comentario1'}, 2: {'comentario2'}}),
        ('el comentario de la línea siguiente', 'base: &b {x: 1}\nitems:\n- *b\n  # comentario1\n- *b # comentario2\n',
         {0: {'comentario1'}, 1: {'comentario2'}}),
        ('una clave de fusión', 'base: &b {x: 1}\nitems:\n- <<: *b # comentario1\n  y: 2\n', {0: {'comentario1'}}),
        ('un alias en una secuencia de flujo', 'base: &b {x: 1}\nitems: [*b, *b] # comentario1\n',
         {0: {'comentario1'}, 1: {'comentario1'}}),
        ('un alias en un mapa de flujo', 'base: &b {x: 1}\nitems:\n- {<<: *b, y: 2} # comentario1\n',
         {0: {'comentario1'}}),
        ('el alias en su propia línea', 'base: &b {x: 1}\nitems:\n-\n  *b # comentario1\n', {0: {'comentario1'}}),
        ('un bloque con comentario de cabecera tras un alias',
         'base: &b {x: 1}\nitems:\n- *b\n- reason: >- # comentario1\n    cuerpo\n  n: 1\n',
         {0: set(), 1: {'comentario1'}}),
        ('un alias tras un elemento de varias líneas', 'base: &b {x: 1}\nitems:\n- y: 2\n  z: 3 # comentario1\n'
         '- *b # comentario2\n', {0: {'comentario1'}, 1: {'comentario2'}}),
        ('una sección que es un alias', 'base: &s\n- {x: 1}\n- {x: 2}\nitems: *s # comentario1\n',
         {0: {'comentario1'}, 1: {'comentario1'}}),
        ('un alias sin comentario', 'base: &b {x: 1}\nitems:\n- *b\n', {0: set()}))

    def comentarios_por_elemento(self, texto):
        out = {}
        for e in RC._entradas_yaml(Path('x.yaml'), Path('boe/x.yaml'), texto):
            m = re.fullmatch(r'boe/x\.yaml:items\[(\d+)\]', e[0])
            if m and e[6] is not None:
                out[int(m[1])] = {w for w in ('comentario1', 'comentario2') if w in ' '.join(e[6].escalares)}
        return out

    def test_the_comment_next_to_an_alias_goes_with_the_value_expanded_in_that_entry(self):
        # Spec 071 AC233 (26b-1): PyYAML devuelve para `*a` el MISMO nodo que el ancla, con las marcas del ancla, y el
        # filtro por líneas dejaba el comentario junto al alias fuera de la entrada que expande el valor. La posición
        # de cada aparición del alias; sus comentarios (los de su línea y los que le siguen hasta el siguiente
        # elemento) van con el valor expandido de esa entrada y con ninguna otra.
        for caso, texto, esperado in self.ALIAS:
            with self.subTest(caso=caso):
                self.assertEqual(self.comentarios_por_elemento(texto), esperado)

    def test_an_alias_that_does_not_compose_is_not_clean(self):
        # AC233: PyYAML no compone un alias de un ancla definida después ni de otro documento (los anclas son de cada
        # documento): el fichero no se da por limpio.
        for caso, texto in (('el ancla después', 'items:\n- *b # comentario1\nbase: &b {x: 1}\n'),
                            ('el ancla en otro documento', 'base: &b {x: 1}\n---\nitems:\n- *b # comentario1\n')):
            with self.subTest(caso=caso):
                entradas = RC._entradas_yaml(Path('x.yaml'), Path('boe/x.yaml'), texto)
                self.assertTrue([e for e in entradas if e[3] == RC.FICHERO_NO_VERIFICABLE])

    # --- enmienda 29 (spec 071 AC235; 27ª pasada de Codex, 16-10-2026) -----------------------------------------------

    B = 'base: &b {source_id: Person:fuldo-fictez, reason: neutro}\n'
    C = '# Terciano se reunió con él'
    VISIBLE = {'source_id': 'Person:fuldo-fictez', 'reason': 'neutro'}

    def juntos(self, texto):
        """Si algún elemento o sección (no el ancla) lleva a la vez el comentario `C` y el id visible: lo que el cruce
        de `reconstrucciones` necesita para ver el nombre corto junto al extremo visible (AC235)."""
        entradas = RC._entradas_yaml(Path('x.yaml'), Path('boe/x.yaml'), texto)
        if '<<' not in texto:  # una clave de fusión no se construye: el fichero no se da por limpio, de todos modos
            self.assertFalse([e for e in entradas if e[3] == RC.FICHERO_NO_VERIFICABLE], texto)
        return any('Terciano' in e[1] and 'Person:fuldo-fictez' in e[1] and not e[0].endswith(':base')
                   for e in entradas)

    def ficha(self, sangria='  ', primera='- '):
        """La ficha del extremo visible escrita en el sitio, como elemento de lista de bloque."""
        return f'{primera}source_id: Person:fuldo-fictez\n{sangria}reason: neutro\n'

    def rangos_de_ac235(self):
        """(caso, YAML): el comentario `C` y el extremo visible en el mismo elemento o sección, con el comentario donde
        la cabecera, la cola o la clave de la sección lo ponen (AC235)."""
        B, C, f = self.B, self.C, self.ficha
        return (
            ('cabecera del alias', f'{B}items:\n- {C}\n  *b\n'),
            ('cabecera del alias, no el primero', f'{B}items:\n- n: 1\n- {C}\n  *b\n- n: 2\n'),
            ('bajo el último alias, con sangría', f'{B}items:\n- *b\n  {C}\n'),
            ('bajo el último alias, sin sangría', f'{B}items:\n- *b\n{C}\n'),
            ('bajo un alias intermedio', f'{B}items:\n- *b\n  {C}\n- n: 2\n'),
            ('bajo un alias intermedio, sin sangría', f'{B}items:\n- *b\n{C}\n- n: 2\n'),
            ('cabecera de un elemento sin alias', f'items:\n- {C}\n{f(primera="  ")}'),
            ('cabecera de un elemento sin alias, no el primero',
             f'items:\n- n: 1\n- {C}\n{f(primera="  ")}- n: 2\n'),
            ('bajo el último elemento sin alias', f'items:\n{f()}  {C}\n'),
            ('bajo el último elemento sin alias, sin sangría', f'items:\n{f()}{C}\n'),
            ('entre la clave y el primer guion', f'items:\n{C}\n{f()}'),
            ('entre la clave y el primer guion, con alias', f'{B}items:\n{C}\n- *b\n'),
            ('junto a la clave', f'items: {C}\n{f()}'),
            ('sección que no es lista, alias, la última', f'{B}valor: *b\n{C}\n'),
            ('sección que no es lista, alias, no la última', f'{B}valor: *b\n{C}\nlista: []\n'),
            ('sección que no es lista, mapa, la última', f'valor:\n  source_id: Person:fuldo-fictez\n{C}\n'),
            ('sección que no es lista, escalar', f'valor: Person:fuldo-fictez\n{C}\n'),
            ('segundo documento, alias terminal', f'x: 1\n---\n{B}items:\n- *b\n{C}\n'),
            ('segundo documento, sección no lista', f'x: 1\n---\n{B}valor: *b\n{C}\n'),
            ('primer documento, cola antes del siguiente', f'{B}items:\n- *b\n{C}\n---\nx: 1\n'),
            ('segundo documento, con directiva', f'x: 1\n...\n%YAML 1.1\n---\n{B}items:\n- *b\n{C}\n'),
            ('lista en flujo, la última sección', f'{B}items: [*b]\n{C}\n'),
            ('lista en flujo de varias líneas', f'{B}items: [\n  {C}\n  *b,\n  n\n]\n'),
            ('lista en flujo, comentario bajo un elemento', f'{B}items: [\n  *b,\n  {C}\n  n\n]\n'),
            ('lista en flujo, no la última sección', f'{B}items: [*b]\n{C}\nlista: []\n'),
            ('lista en flujo, comentario junto a la clave y visible en el último', f'{B}items: [ {C}\n  n,\n  *b\n]\n'),
            ('lista en flujo, visible en el primero y comentario en el último', f'{B}items: [\n  *b,\n  n {C}\n]\n'),
            ('clave de fusión, terminal', f'{B}items:\n- <<: *b\n  n: 1\n  {C}\n'),
            ('clave de fusión, cabecera', f'{B}items:\n- {C}\n  <<: *b\n  n: 1\n'),
            ('mapa de flujo, terminal', f'{B}items:\n- {{<<: *b, n: 1}}\n  {C}\n'),
            ('sección que es un alias, comentario bajo ella', f'base: &s\n- {{source_id: Person:fuldo-fictez}}\n'
                                                              f'items: *s\n{C}\n'),
            ('raíz escalar', f'# cabecera\nPerson:fuldo-fictez\n{C}\n'),
            ('raíz escalar, segundo documento', f'x: 1\n---\nPerson:fuldo-fictez\n{C}\n'),
            ('raíz secuencia, cabecera', f'- {C}\n  source_id: Person:fuldo-fictez\n'),
            ('raíz secuencia, cola', f'- source_id: Person:fuldo-fictez\n  {C}\n'),
            ('raíz secuencia, alias', f'- &b {{source_id: Person:fuldo-fictez}}\n- {C}\n  *b\n'),
            ('cola de la última sección, tras líneas en blanco', f'{B}items:\n- *b\n\n\n{C}\n'),
            ('final sin salto de línea', f'{B}items:\n- *b\n{C}'))

    def test_the_range_of_an_element_goes_from_its_indicator_to_the_next_with_all_its_comments(self):
        # Spec 071 AC235 (27a y 27b, la tabla de Codex): `aparece` daba solo las líneas del alias y el último elemento
        # terminaba en `ultima + 1`: el comentario de cabecera (`- # …`), el de cola, el de entre la clave y el primer
        # guion y el de una sección que no es lista quedaban como entradas sueltas, sin el extremo visible. El rango de
        # un elemento va de la línea de su indicador a la del siguiente (o al cierre de su sección, del documento o del
        # fichero), con todos sus comentarios.
        for caso, texto in self.rangos_de_ac235():
            with self.subTest(caso=caso):
                self.assertTrue(self.juntos(texto), texto)

    def test_an_element_keeps_its_own_comments_and_not_those_of_its_neighbours(self):
        # AC235: los límites son los de la spec, no un rango que se desborda: el comentario de la cola de un elemento
        # intermedio no va con el siguiente ni el de la cabecera del siguiente con él; sin el extremo visible en el
        # mismo elemento, el comentario sigue sin ir con él.
        B, C = self.B, self.C
        cabecera_y_cola = f'{B}items:\n- *b\n  {C}\n- n: 2\n'
        entradas = RC._entradas_yaml(Path('x.yaml'), Path('boe/x.yaml'), cabecera_y_cola)
        con = {e[0] for e in entradas if 'Terciano' in e[1]}
        self.assertIn('boe/x.yaml:items[0]', con)
        self.assertNotIn('boe/x.yaml:items[1]', con)
        entradas = RC._entradas_yaml(Path('x.yaml'), Path('boe/x.yaml'), f'{B}items:\n- n: 1\n- {C}\n  *b\n- n: 2\n')
        con = {e[0] for e in entradas if 'Terciano' in e[1]}
        self.assertIn('boe/x.yaml:items[1]', con)
        self.assertEqual(con & {'boe/x.yaml:items[0]', 'boe/x.yaml:items[2]'}, set())
        # El comentario entre dos secciones es la cola de la anterior, no de la cabecera de la siguiente.
        for caso, texto in (('una sección que no es lista', f'valor:\n  x: 1\n{C}\nsiguiente: Person:fuldo-fictez\n'),
                            ('una lista de bloque', f'items:\n- n: 1\n{C}\nsiguiente: Person:fuldo-fictez\n'),
                            ('una lista de flujo', f'items: [n, 1]\n{C}\nsiguiente: Person:fuldo-fictez\n')):
            with self.subTest(caso=caso):
                self.assertFalse(self.juntos(texto), texto)

    def test_the_alias_terminal_the_alias_in_the_middle_and_the_expanded_value_give_the_same_result(self):
        # AC235: con el alias al final, en medio o expandido en el sitio, y con el comentario en la cabecera, en su
        # línea o en la cola, el resultado es el mismo.
        C = self.C
        # AC237: también con el alias anidado (dentro de un mapa o de una lista de flujo del elemento).
        formas = {'alias': ['- *b'], 'expandido': ['- source_id: Person:fuldo-fictez', '  reason: neutro'],
                  'flujo': ['- {source_id: Person:fuldo-fictez, reason: neutro}'],
                  'anidado': ['- {y: *b}'], 'doble': ['- [*b, *b]']}
        sitios = {'cabecera': lambda f: [f'- {C}', '  ' + f[0][2:], *f[1:]],
                  'línea': lambda f: [*f[:-1], f'{f[-1]} {C}'],
                  'cola': lambda f: [*f, f'  {C}']}
        for nombre, forma in formas.items():
            for sitio, poner in sitios.items():
                for lugar, antes, despues in (('último', ['- n: 0'], []), ('intermedio', ['- n: 0'], ['- n: 2']),
                                              ('primero', [], ['- n: 2'])):
                    with self.subTest(forma=nombre, sitio=sitio, lugar=lugar):
                        lineas = [*antes, *poner(forma), *despues]
                        texto = self.B + 'items:\n' + '\n'.join(lineas) + '\n'
                        self.assertTrue(self.juntos(texto), texto)

    # --- enmienda 30 (spec 071 AC237; 28ª pasada de Codex, 17-10-2026) -----------------------------------------------

    def rangos_de_ac237(self):
        """(caso, YAML, si el comentario `C` y el extremo visible comparten alguna entrada): la tabla de Codex de la 28ª
        pasada, sus controles y los casos de la clase (AC237)."""
        C, V = self.C, 'Person:fuldo-fictez'
        return (
            ('la sección vecina no aporta su comentario al valor expandido',
             f'base: &b\n  entity_id: {V}\nother: {C}\n  x: neutro\nvalue: *b\n', False),
            ('la sección vecina, tras la que usa el alias',
             f'base: &b\n  entity_id: {V}\nvalue: *b\nother: {C}\n  x: neutro\n', False),
            ('la cabecera del ancla va con la expansión', f'base: &b {C}\n  entity_id: {V}\nvalue: *b\n', True),
            ('la cola del ancla va con la expansión',
             f'base: &b\n  entity_id: {V}\n  {C}\nvalue: *b\nother: x\n', True),
            ('la cola de un ancla de lista antes de la clave que la usa', f'base: &b\n- neutro\n{C}\n{V}: *b\n', True),
            ('la cola de un ancla de flujo antes de la clave que la usa', f'base: &b [neutro]\n{C}\n{V}: *b\n', True),
            ('la cola de un ancla anidada en otro alias',
             f'base: &b\n- neutro\n{C}\nmid: &m\n  y: *b\n{V}: *m\n', True),
            ('la apertura de una secuencia de flujo raíz', f'[ {C}\n {{source_id: {V}}}\n]\n', True),
            ('una sección con lista vacía', f'{V}: []\n{C}\n', True),
            ('una sección con mapa vacío', f'{V}: {{}}\n{C}\n', True),
            ('la clave explícita', f'? {C}\n  items\n:\n- source_id: {V}\n', True),
            ('un alias en una lista de flujo dentro de una clave de fusión',
             f'a: &a {{x: {V}}}\nb: &b\n- neutro\n{C}\nitems:\n- <<: [*a, *b]\n', True),
            ('el alias de una sección de lista lleva su ancla entera',
             f'base: &s\n- {{source_id: {V}}}\n- neutro\n{C}\nitems: *s\n', True),
            # AC239 (29ª pasada): el alias como clave, la apertura de la raíz y el cierre de un escalar de bloque raíz.
            ('el alias como clave, valor escalar', f'base: &k neutral {C}\n*k : {V}\n', True),
            ('el alias como clave, valor lista de bloque', f'base: &k neutral {C}\n*k :\n- {V}\n', True),
            ('el alias como clave, valor mapa', f'base: &k clave {C}\n*k :\n  source_id: {V}\n', True),
            ('el alias como clave, heredada por el segundo elemento',
             f'base: &k clave {C}\n*k :\n- n\n- {V}\n', True),
            ('la apertura de un mapa de flujo raíz', f'{{ {C}\nentity_id: {V}\n}}\n', True),
            ('la apertura de una secuencia raíz con ancla', f'&a {C}\n- entity_id: {V}\n', True),
            ('la apertura de una secuencia raíz con etiqueta', f'!!seq {C}\n- entity_id: {V}\n', True),
            ('la apertura de un mapa de bloque raíz con ancla', f'&a {C}\nentity_id: {V}\n', True),
            ('AC241: la directiva con el visible y una secuencia raíz vacía', f'%TAG !x! {V} {C}\n---\n[]\n', True),
            ('AC241: la directiva con el visible y un mapa raíz vacío', f'%TAG !x! {V} {C}\n---\n{{}}\n', True),
            ('AC241: la directiva con el visible y una lista no vacía', f'%TAG !x! {V} {C}\n---\n- a\n', True),
            ('AC241: la directiva de un segundo documento con la raíz vacía',
             f'x: neutral\n---\n%TAG !x! {V} {C}\n---\n[]\n', True),
            ('AC241: un ancla con el comentario sobre una raíz vacía y el visible debajo',
             f'&a {C}\n[]\n# {V}\n', True),
            ('AC241: el visible en un documento y el comentario en la raíz vacía del siguiente',
             f'x: {V}\n---\n{C}\n[]\n', False),
            ('el --- del primer documento', f'--- {C}\nentity_id: {V}\n', True),
            ('el --- de un segundo documento', f'x: neutral\n--- {C}\nentity_id: {V}\n', True),
            ('las directivas de un segundo documento', f'x: neutral\n...\n%YAML 1.1 {C}\n---\nentity_id: {V}\n', True),
            ('el --- de un segundo documento, el visible en el primero', f'x: {V}\n--- {C}\ny: neutral\n', False),
            ('un escalar de bloque raíz seguido de otro documento: el comentario es del siguiente',
             f'|\n  {V}\n--- {C}\nx: neutral\n', False),
            ('un escalar de bloque raíz plegado seguido de otro documento', f'>\n  {V}\n--- {C}\nx: neutral\n', False),
            ('un escalar de bloque raíz, el comentario del propio documento', f'|\n  {V}\n  {C}\n--- \nx: neutral\n',
             True),
            # Controles: JUNTOS.
            ('control: el comentario en la línea del alias', f'base: &b {{source_id: {V}}}\nvalue: *b {C}\n', True),
            ('control: el comentario en la línea del valor', f'value: {V} {C}\n', True),
            ('control: la clave explícita con el comentario en su línea', f'? items {C}\n:\n- source_id: {V}\n', True),
            ('control: el mapa expandido', f'value:\n  source_id: {V}\n  {C}\n', True),
            ('control: la lista expandida', f'items:\n- source_id: {V}\n  {C}\n', True),
            # Contrarios: SEPARADOS.
            ('contrario: el comentario de la sección vecina', f'a: x {C}\nb: {V}\n', False),
            ('contrario: el comentario de un elemento vecino', f'items:\n- n: 1 {C}\n- source_id: {V}\n', False),
            ('contrario: el comentario de la sección anterior a una lista vacía', f'a: x\n{C}\n{V}: []\n', False),
            ('contrario: el ancla de otra sección', f'base: &b {{x: neutro}}\nvalue: *b\nother: {C}\n{V}: x\n', False))

    def test_the_range_of_each_construction_goes_from_its_indicator_to_the_next_even_through_an_alias(self):
        # Spec 071 AC237 (28a y 28b, la tabla de Codex): `end_mark.line + 1` de un ancla incluía la clave vecina (un
        # par fabricado con el comentario de `other`), la expansión omitía la cabecera y la cola del ancla, la apertura
        # de una secuencia de flujo raíz y la clave explícita empezaban en el primer valor, y una sección con lista
        # vacía no generaba entrada. Cada rango empieza en el indicador de su construcción y la expansión de un alias usa el
        # rango lógico completo de su ancla.
        for caso, texto, esperado in self.rangos_de_ac237():
            with self.subTest(caso=caso):
                self.assertEqual(self.juntos(texto), esperado, texto)

    # Cada línea de la implementación de los rangos (AC237 y AC239), cambiada por otra que falla: (nombre, texto viejo,
    # texto nuevo). El oráculo tiene que detectar cada una.
    MUTACIONES = (
        ('AC241: sin entrada para una raíz vacía',
         'if isinstance(nodo, (yaml.MappingNode, yaml.SequenceNode)) and not nodo.value:', 'if False:'),
        ('AC241: la raíz vacía sin su apertura', 'rangos = [(min(abre, posiciones.ini[raiz]), max(cierra, ',
         'rangos = [(posiciones.ini[raiz], max(cierra, '),
        ('AC241: la raíz vacía sin su cierre', 'max(cierra, posiciones.ini[raiz] + 1))]', 'posiciones.ini[raiz] + 1)]'),
        ('AC242: las directivas no son texto propio',
         '(yaml.AnchorToken, yaml.AliasToken, yaml.TagToken, yaml.DirectiveToken)',
         '(yaml.AnchorToken, yaml.AliasToken, yaml.TagToken)', 'cobertura'),
        ('AC244: el cierre de una cita se reutiliza como apertura',
         '            out.append((abierta, i + 1, texto[abierta + 1:i]))\n            abierta = -1',
         '            out.append((abierta, i + 1, texto[abierta + 1:i]))\n'
         '            abierta = i if texto[i] in _CIERRE_DE else -1', 'citas'),
        ('AC244: el interior de un par', 'texto[abierta + 1:i]))', 'texto[abierta:i]))', 'citas'),
        ('AC250: un cierre sin apertura conserva los pares anteriores',
         '            if texto[i] not in _CIERRE_DE:\n                return []\n            abierta = i',
         '            if texto[i] not in _CIERRE_DE:\n                break\n            abierta = i', 'citas'),
        ('AC250: una apertura seguida de otra comilla conserva los pares anteriores',
         '        else:\n            return []\n    return out if abierta < 0 else []',
         '        else:\n            abierta = -1\n            break\n    return out if abierta < 0 else []', 'citas'),
        ('AC250: una apertura sin cierre al final conserva los pares anteriores',
         'return out if abierta < 0 else []', 'return out', 'citas'),

        ('ninguna expansión de alias', 'out.append(self.rango(self.definicion[id(nodo)], self.ancla[id(nodo)]))',
         'pass'),
        ('el rango de una aparición acaba en su línea', 'return ini, max(self.fin(aparicion), ini + 1)',
         'return ini, ini + 1'),
        ('el último hermano acaba en su línea',
         '            aparicion = self.definicion[aparicion[0]]\n        return self.cierres',
         '            return self.ini[aparicion] + 1\n        return self.cierres'),
        ('la expansión no baja a los alias anidados', 'pendientes.extend(self.hijos.get(id(nodo), ()))', 'pass'),
        ('la expansión con las marcas del nodo',
         'out.append(self.rango(self.definicion[id(nodo)], self.ancla[id(nodo)]))',
         'out.append((nodo.start_mark.line, nodo.end_mark.line + 1))'),
        ('sin el alias de la clave', 'suma_clave = posiciones.expansion(de_clave) if clave is not None else []',
         'suma_clave = []'),
        ('la clave la hereda solo el primer elemento', '*posiciones.expansion(de_item), *suma_clave]',
         '*posiciones.expansion(de_item), *(suma_clave if i == 0 else [])]'),
        ('la lista de una sola construcción sin la clave', '*suma, *suma_clave, *apertura]\n                    desde',
         '*suma, *apertura]\n                    desde'),
        ('la sección sin el alias de la clave', '*suma, *suma_clave, *apertura]\n                cubrir(rangos)',
         '*suma, *apertura]\n                cubrir(rangos)'),
        ('la sección sin la apertura de la raíz', '*suma, *suma_clave, *apertura]\n                cubrir(rangos)',
         '*suma, *suma_clave]\n                cubrir(rangos)'),
        ('la lista de una sola construcción sin la apertura',
         '*suma, *suma_clave, *apertura]\n                    desde', '*suma, *suma_clave]\n                    desde'),
        ('sin la apertura de la raíz', 'apertura = [(desde, primera)] if desde < primera else []', 'apertura = []'),
        ('la apertura la llevan todos los elementos', 'if i == 0:\n                            rangos.extend(apertura)',
         'if True:\n                            rangos.extend(apertura)'),
        ('la apertura de una secuencia raíz acaba en su clave',
         'primera = posiciones.ini[(id(valor), 0, False)] if clave is None and valor.value else clave_en',
         'primera = clave_en'),
        ('sin el --- ni las directivas en la apertura',
         'desde = min(posiciones.ini[raiz], aperturas[d - 1] if aperturas[d - 1] is not None else cierra)',
         'desde = posiciones.ini[raiz]'),
        ('el escalar raíz rebasa el cierre del documento', 'fin = max(cierra, ini + 1)',
         'fin = max(cierra, nodo.end_mark.line + 1, ini + 1)'),
        ('una clave explícita empieza en su primer valor', 'linea = self.llave', 'linea = evento.start_mark.line'),
        ('un elemento empieza en su primer valor', 'linea = self.guion', 'linea = evento.start_mark.line'),
    )

    def test_every_token_and_comment_reaches_some_entry_and_its_own_text(self):
        # AC241 y AC242, el invariante de cobertura (la 30ª pasada abrió dos regresiones de la e31: una construcción
        # nueva que cubría un rango sin entrada y otra que traía las directivas al texto sin llevarlas al propio): en
        # cada documento del corpus y en cada variante con un comentario en un hueco, todo token de `yaml.scan` con
        # texto (directiva, etiqueta, ancla, alias, escalar) y todo comentario está en alguna entrada y en su texto
        # propio.
        entradas = lambda texto: RC._entradas_yaml(Path('x.yaml'), Path('boe/x.yaml'), texto)  # noqa: E731
        for nombre, dsl in RY.CORPUS.items():
            for sep, salto in RY.SEPARADORES.items():
                with self.subTest(documento=nombre, separador=sep):
                    self.assertEqual(RY.comprobar_cobertura(dsl, entradas, salto, primero=True)[:2], [], nombre)

    def test_the_quoted_text_is_exactly_the_union_of_the_complete_pairs(self):
        # AC242 (30b-1): con `"([^"\\n]+)"` el primer `"` de una cita que cruza un salto de línea no casaba y la
        # búsqueda seguía en su comilla de CIERRE: el texto ENTRE dos citas se tomaba por cita y quedaba exento. Las
        # comillas se emparejan de izquierda a derecha por pares completos, con 0..4 pares de cada clase, saltos de
        # línea, pares vacíos y adyacentes, y una comilla impar al final; lo anidado se conserva como propio.
        self.assertEqual(RY.comprobar_citas(RC), [])
        texto = '"Una declaración con\nsalto de línea" Terciano salió sin declarar "Otra declaración"'
        documentos = {'d': RC._llano('Una declaración con salto de línea Terciano salió sin declarar Otra declaración')}
        tramos = RC.tramos_citados(texto, {'d'}, documentos)
        self.assertEqual([texto[a:b] for a, b in tramos],
                         ['"Una declaración con\nsalto de línea"', '"Otra declaración"'])
        self.assertNotIn(' Terciano salió sin declarar ', [texto[a + 1:b - 1] for a, b in tramos])

    def test_the_quotes_are_paired_in_one_pass_and_a_loose_quote_voids_the_whole_scalar(self):
        # AC244 (31ª pasada) y AC250 (33ª): se trabaja sobre la secuencia de comillas del escalar. Un par es válido si
        # sus dos comillas son contiguas en esa secuencia y la segunda cierra la primera; toda otra comilla es suelta,
        # y con UNA sola suelta, delante o detrás de un par, no se exime nada del escalar (todo o nada). Propiedad
        # exhaustiva (todas las cadenas de hasta 7 símbolos de «, », “, ”, " y un relleno, más aleatorias de hasta 40
        # con saltos de línea) contra un modelo de referencia independiente: lo eximido es exactamente lo que el
        # modelo exime. (La prueba de seguridad no es esta: es `comprobar_eximido`, sobre lo efectivamente eximido.)
        self.assertEqual(RY.comprobar_pares(RC), [])
        declaro = 'Terciano salió sin declarar'
        mezclada = f'«Una declaración con "inciso» termina" {declaro} "Otra declaración"'
        for texto, esperado in ((f'«uno «dos» «{declaro}» final»', []), (f'“uno “dos” “{declaro}” final”', []),
                                (mezclada, []),
                                (f'dijo "{declaro}" fin', [f'"{declaro}"']),
                                (f'dijo «uno» y luego «{declaro}» fin', ['«uno»', f'«{declaro}»']),
                                ('«uno» y luego «dos «tres»', []), ('«uno» y «dos» luego "tres', []),
                                ('«uno» y «dos»» luego «tres»', []), ('uno» y luego «dos»', []),
                                ('x " «uno» y "dos"', []), ('x "uno" «dos» y "tres" «cuatro»',
                                                            ['"uno"', '«dos»', '"tres"', '«cuatro»'])):
            with self.subTest(texto=texto):
                self.assertEqual([texto[a:b] for a, b, _ in RC._pares_de_comillas(texto)], esperado)

    def reconstruye(self, crudas, documento):
        """(problemas, avisos) de `reconstrucciones` (AC244 y AC246, de punta a punta) para las entradas crudas de
        `_entradas_yaml` o `_entradas_json` y una fila anonimizada cuyo extremo oculto se llama «terciano» (nombre
        corto) y cuyo visible es `Person:fuldo-fictez`; `documento` es el texto de a.pdf, que las entradas citan."""
        oculto, visible, clave, rid = 'Person:terciano-ficticio', 'Person:fuldo-fictez', 'c1' * 32, 'R-2026-10-01-0001'
        fila = {'id': rid, 'unidad': clave, 'decision': 'suprimido', 'entidad': visible,
                'dato': f'({visible}) -[MET_WITH]-> (Person:[tercero]): arista retirada'}
        privadas = {clave: [{'source_id': oculto, 'target_id': visible, 'type': 'MET_WITH'}]}
        propia = RC.Entrada('registro.yaml:fila[0]', '', 'rectificaciones/registro.yaml', 'filas', fila, frozenset(),
                            frozenset(), frozenset(), '', None, '', None)
        entradas = [RC.Entrada(posicion, texto, fichero, seccion, valor, frozenset(), frozenset(), frozenset(),
                               RC._llano(texto), None, barrido, propio_)
                    for posicion, texto, fichero, seccion, valor, barrido, propio_ in crudas]
        return RC.reconstrucciones([fila], {}, privadas, entradas, {}, {}, propias={0: propia},
                                   cortos={oculto: {'terciano'}}, por_documento={'a.pdf': RC._llano(documento)})

    def reconstruye_nota(self, nota, documento):
        """Lo mismo para una nota (con sus comillas) en un elemento de YAML y en uno de JSON: {formato: (problemas,
        avisos)}."""
        campos = {'source_doc': 'a.pdf', 'source_id': 'Person:fuldo-fictez', 'nota': nota}
        yaml_texto = '- ' + '  '.join(f'{k}: {json.dumps(v, ensure_ascii=False)}\n' for k, v in campos.items())
        json_texto = json.dumps({'items': [campos]}, ensure_ascii=False)
        return {formato: self.reconstruye(crudas, documento)
                for formato, crudas in (('yaml', RC._entradas_yaml(Path('x.yaml'), Path('boe/x.yaml'), yaml_texto)),
                                        ('json', RC._entradas_json(Path('boe/x.json'), json_texto)))}

    def test_a_nested_or_mixed_quote_never_exempts_the_text_between_other_quotes_end_to_end(self):
        # AC244, de punta a punta (YAML y JSON, por `reconstrucciones`): una cita exterior con dos interiores del mismo
        # tipo, y la mezcla de un cierre angular tras una apertura recta, daban por cita verificada un tramo que es
        # nuestro («Terciano», el nombre corto del oculto, junto al visible): cero problemas y un aviso. Ahora es un
        # problema y ningún aviso.
        documento = 'Terciano salió sin declarar nada de lo que le pidieron'
        anidada = '«uno «dos» «Terciano salió sin declarar» final»'
        mezclada = '«Una declaración con "inciso» termina" Terciano salió sin declarar "Otra declaración"'
        limpia = 'dijo «Terciano salió sin declarar» y se fue'
        for caso, nota, esperado in (('anidada', anidada, (1, 0)), ('mezclada', mezclada, (1, 0)),
                                     ('control: una cita limpia', limpia, (0, 1)),
                                     ('control: dos citas limpias seguidas',
                                      '«Otra declaración» y «Terciano salió sin declarar»', (0, 1)),
                                     ('control: sin comillas', 'dijo Terciano salió sin declarar y se fue', (1, 0))):
            for formato, (problemas, avisos) in self.reconstruye_nota(nota, documento).items():
                with self.subTest(caso=caso, formato=formato):
                    self.assertEqual((len(problemas), len(avisos)), esperado, (problemas, avisos))

    @staticmethod
    def pares_e33(texto):
        """El emparejador de la e33 (AC244), con rectas impares: la suelta era la ÚLTIMA. Es el control negativo de la
        monotonía: 32a (31ª pasada) lo ha visto exponer un nombre cuando se pierde una comilla."""
        cierre = {'«': '»', '“': '”', '"': '"'}
        out, abierta = [], -1
        for m in re.finditer('[«»“”"]', texto):
            i = m.start()
            if abierta < 0:
                if texto[i] not in cierre:
                    break
                abierta = i
            elif cierre[texto[abierta]] == texto[i]:
                out.append((abierta, i + 1, texto[abierta + 1:i]))
                abierta = -1
            else:
                break
        return out

    def test_a_lost_or_extra_quote_never_widens_what_is_exempt(self):
        # AC246 (32a): con rectas impares el emparejamiento de izquierda a derecha deja suelta la última, y una recta
        # perdida (`Una declaración anterior" Terciano … "Otra declaración"`, sin su primera comilla) emparejaba las dos
        # del medio y eximía el nombre. Desde AC250 una suelta anula la exención de todo el escalar. La prueba de
        # seguridad no compara con el modelo (los dos compartían la lectura): es la MONOTONÍA ante una comilla perdida
        # o sobrante, exhaustiva sobre los textos bien formados, con el emparejador de la e33 como control negativo
        # (tiene que dar rojo). Sobre los PARES sintácticos; sobre lo eximido de verdad, `comprobar_eximido` (AC250).
        self.assertEqual(RY.comprobar_monotonia(RC._pares_de_comillas), [])
        fallos = RY.comprobar_monotonia(self.pares_e33)
        self.assertGreater(len(fallos), 100, fallos[:3])
        completa = '"Una declaración anterior" Terciano salió sin declarar "Otra declaración"'
        sin_la_primera = completa[1:]
        self.assertEqual([completa[a:b] for a, b, _ in RC._pares_de_comillas(completa)],
                         ['"Una declaración anterior"', '"Otra declaración"'])
        self.assertEqual(RC._pares_de_comillas(sin_la_primera), [])
        self.assertEqual([x[:2] for x in self.pares_e33(sin_la_primera)], [(24, 55)])
        for texto, esperado in (('«uno» y «dos» luego "tres', []), ('«uno» "dos" y "tres', []), ('“uno” y "dos', []),
                                ('"uno" y «dos» luego "tres" y "cuatro"', ['"uno"', '«dos»', '"tres"', '"cuatro"'])):
            with self.subTest(texto=texto):
                self.assertEqual([texto[a:b] for a, b, _ in RC._pares_de_comillas(texto)], esperado)

    def test_a_lost_straight_quote_never_exempts_the_name_between_two_quotes_end_to_end(self):
        # AC246, de punta a punta (YAML y JSON, por `reconstrucciones`): con las cuatro rectas el nombre entre las dos
        # citas es nuestro (un problema); sin la primera (tres rectas) lo era también y quedaba eximido (cero
        # problemas y un aviso); y con las dos citas completas, cada una exime su propio tramo.
        documento = ('Una declaración anterior. Terciano salió sin declarar nada de lo que le pidieron. '
                     'Otra declaración posterior.')
        declaro = 'Terciano salió sin declarar'
        for caso, nota, esperado in (
                ('cuatro rectas', f'"Una declaración anterior" {declaro} "Otra declaración posterior"', (1, 0)),
                ('tres rectas, sin la primera', f'Una declaración anterior" {declaro} "Otra declaración posterior"',
                 (1, 0)),
                ('tres rectas, sin la última', f'"Una declaración anterior" {declaro} "Otra declaración posterior',
                 (1, 0)),
                ('control: el nombre dentro de la primera cita', f'"{declaro}" y "Otra declaración posterior"',
                 (0, 1)),
                ('control: el nombre dentro de la segunda cita', f'"Una declaración anterior" y "{declaro}"', (0, 1))):
            for formato, (problemas, avisos) in self.reconstruye_nota(nota, documento).items():
                with self.subTest(caso=caso, formato=formato):
                    self.assertEqual((len(problemas), len(avisos)), esperado, (problemas, avisos))

    @staticmethod
    def pares_e34(texto):
        """El emparejador de la e34 (`e61918d`, AC246): lo que sigue a la primera suelta es texto propio y con rectas
        impares ninguna recta exime, pero una suelta POSTERIOR a un par no le quita la exención. Es el control negativo
        de AC250: una comilla de más tras un par amplía lo que `tramos_citados` exime de verdad."""
        cierre = {'«': '»', '“': '”', '"': '"'}
        impares = texto.count('"') % 2
        out, abierta = [], -1
        for m in re.finditer('[«»“”"]', texto):
            i = m.start()
            if impares and texto[i] == '"':
                break
            if abierta < 0:
                if texto[i] not in cierre:
                    break
                abierta = i
            elif cierre[texto[abierta]] == texto[i]:
                out.append((abierta, i + 1, texto[abierta + 1:i]))
                abierta = -1
            else:
                break
        return out

    def test_one_extra_or_lost_quote_never_widens_what_tramos_citados_exempts(self):
        # AC250 (33a): la monotonía de AC246 comparaba PARES SINTÁCTICOS, no lo que `tramos_citados` exime: una sola
        # `»` insertada tras «declarar» en «Terciano salió sin declarar y esto me lo invento» dejaba el par
        # `«Terciano salió sin declarar»` (0,29) dentro del (0,50) y pasaba, y a la vez eximía «Terciano salió sin
        # declarar», que era texto propio. Ahora, para todo escalar BALANCEADO y cada edición de UNA comilla (borrarla
        # o insertar una suelta de cualquier clase en cualquier hueco), lo que el `tramos_citados` REAL exime del
        # editado está contenido en lo que exime el original (rellenos que forman citas fieles y una que no). Control
        # negativo: con el emparejador de la e34 (parcheado en el módulo, para que lo use `tramos_citados`) da rojo.
        self.assertEqual(RY.comprobar_eximido(RC), [])
        with mock.patch.object(RC, '_pares_de_comillas', self.pares_e34):
            fallos = RY.comprobar_eximido(RC, largo_maximo=5, aleatorios=100)
        self.assertGreater(len(fallos), 50, fallos[:3])
        declaro = 'Terciano salió sin declarar'
        entera, con_mas = f'«{declaro} y esto me lo invento»', f'«{declaro}» y esto me lo invento»'
        fuente = {'d': RC._llano('Terciano salió sin declarar nada de lo que le pidieron')}
        self.assertEqual(RC.tramos_citados(entera, {'d'}, fuente), [])
        self.assertEqual(RC.tramos_citados(con_mas, {'d'}, fuente), [])
        with mock.patch.object(RC, '_pares_de_comillas', self.pares_e34):
            self.assertEqual(RC.tramos_citados(con_mas, {'d'}, fuente), [(0, 29)])
        # Una suelta DETRÁS de una cita completa y fiel también la anula; reparar el entrecomillado (quitarla) la
        # recupera: es corregirlo, no ampliarlo (límite declarado).
        self.assertEqual(RC.tramos_citados(f'«{declaro}» y «otra', {'d'}, fuente), [])
        self.assertEqual(RC.tramos_citados(f'«{declaro}» y otra', {'d'}, fuente), [(0, 29)])
        # Dos citas completas y fieles de un escalar balanceado: `tramos_citados` da los dos tramos.
        fuente2 = {'d': RC._llano(f'{declaro} nada de lo que le pidieron. Otra declaración posterior.')}
        dos = f'«{declaro}» y «Otra declaración posterior»'
        self.assertEqual([dos[a:b] for a, b in RC.tramos_citados(dos, {'d'}, fuente2)],
                         [f'«{declaro}»', '«Otra declaración posterior»'])

    def test_one_extra_quote_never_exempts_the_name_end_to_end(self):
        # AC250, de punta a punta (YAML y JSON, por `reconstrucciones`): la nota con UNA `»` de más (y las variantes con
        # `“ ”` y con rectas) es un problema, y una comilla suelta detrás de una cita completa y fiel también anula su
        # exención; las citas completas y fieles de un escalar balanceado siguen eximiendo.
        documento = 'Terciano salió sin declarar nada de lo que le pidieron'
        declaro = 'Terciano salió sin declarar'
        for caso, nota, esperado in (
                ('entera entre comillas', f'«{declaro} y esto me lo invento»', (1, 0)),
                ('con UNA » de más tras declarar', f'«{declaro}» y esto me lo invento»', (1, 0)),
                ('lo mismo con “ ”', f'“{declaro}” y esto me lo invento”', (1, 0)),
                ('lo mismo con rectas (tres)', f'"{declaro}" y esto me lo invento"', (1, 0)),
                ('una apertura suelta detrás de la cita', f'dijo «{declaro}» y se «fue', (1, 0)),
                ('una apertura suelta delante de la cita', f'dijo «x y «{declaro}» y se fue', (1, 0)),
                ('control: la cita completa y fiel', f'dijo «{declaro}» y se fue', (0, 1)),
                ('control con “ ”', f'dijo “{declaro}” y se fue', (0, 1)),
                ('control con rectas', f'dijo "{declaro}" y se fue', (0, 1)),
                ('control: dos citas, una fiel y otra no', f'«Otra cosa» y «{declaro}» y se fue', (0, 1))):
            for formato, (problemas, avisos) in self.reconstruye_nota(nota, documento).items():
                with self.subTest(caso=caso, formato=formato):
                    self.assertEqual((len(problemas), len(avisos)), esperado, (problemas, avisos))
        # Control del punto (4) de AC250: dos citas completas y fieles, cada una exime su propio tramo.
        documento2 = f'{declaro} nada de lo que le pidieron. Otra declaración posterior.'
        for caso, nota, esperado in (
                ('control: dos citas completas y fieles', f'«{declaro}» y «Otra declaración posterior»', (0, 1)),
                ('control con “ ”', f'“{declaro}” y “Otra declaración posterior”', (0, 1)),
                ('control con rectas', f'"{declaro}" y "Otra declaración posterior"', (0, 1)),
                ('las mismas con una » de más al final', f'«{declaro}» y «Otra declaración posterior»»', (1, 0))):
            for formato, (problemas, avisos) in self.reconstruye_nota(nota, documento2).items():
                with self.subTest(caso=caso, formato=formato):
                    self.assertEqual((len(problemas), len(avisos)), esperado, (problemas, avisos))

    def test_the_pairing_of_quotes_is_linear_in_the_scalar(self):
        # AC244 (31a-3): `str.find` del cierre sobre todo el sufijo en cada apertura sin cierre era cuadrático
        # (1.280.000 «: 12,3 s). Cota absoluta, no razón entre tiempos: 1.280.000 comillas sin cierre y 640.000
        # aperturas anidadas, en menos de 1 s en total.
        inicio = time.perf_counter()
        self.assertEqual(RC._pares_de_comillas('«' * 1_280_000), [])
        self.assertEqual(RC._pares_de_comillas('«' * 640_000 + '»' * 640_000), [])
        self.assertEqual(RC._pares_de_comillas('""' + '«' * 640_000), [])
        self.assertLess(time.perf_counter() - inicio, 1)
        # Y los pares válidos, de uno en uno: lineal en su número.
        inicio = time.perf_counter()
        self.assertEqual(len(RC._pares_de_comillas('«uno» ' * 100_000)), 100_000)
        self.assertLess(time.perf_counter() - inicio, 2)

    def test_a_directive_is_own_text_of_the_entry_that_opens_with_it(self):
        # AC242 (30b-2): `_tokens_de_texto` omitía las `DirectiveToken`: el nombre de un `%TAG` con el visible en la
        # misma entrada no llegaba a `Propio.escalares`.
        texto = '%TAG !Terciano! tag:yaml.org,2002:\n---\nitems: [{source_id: Person:fuldo-fictez}]\n'
        entradas = RC._entradas_yaml(Path('x.yaml'), Path('boe/x.yaml'), texto)
        con_visible = [e for e in entradas if e[6] is not None and 'Person:fuldo-fictez' in e[1]]
        self.assertTrue(con_visible)
        for e in con_visible:
            self.assertTrue(any('%TAG !Terciano! tag:yaml.org,2002:' in x for x in e[6].escalares), e[6].escalares)

    def test_the_oracle_detects_each_mutation_of_the_range_model(self):
        # AC239: cada línea nueva de la implementación tiene una mutación que el oráculo detecta (sobre el corpus con
        # el separador LF): si no, la propiedad no la cubre.
        ruta = Path(RC.__file__)
        fuente = ruta.read_text(encoding='utf-8')
        for nombre, viejo, nuevo, *detector in self.MUTACIONES:
            with self.subTest(mutacion=nombre):
                self.assertEqual(fuente.count(viejo), 1, viejo)
                modulo = types.ModuleType('registro_check_mutado')
                modulo.__file__ = str(ruta)
                exec(compile(fuente.replace(viejo, nuevo), str(ruta), 'exec'), modulo.__dict__)  # noqa: S102
                entradas = lambda texto: modulo._entradas_yaml(Path('x.yaml'), Path('boe/x.yaml'), texto)  # noqa: E731
                if detector == ['citas']:
                    detectada = bool(RY.comprobar_citas(modulo) or RY.comprobar_pares(modulo)
                                     or RY.comprobar_monotonia(modulo._pares_de_comillas)
                                     or RY.comprobar_eximido(modulo, largo_maximo=5, aleatorios=100))
                elif detector == ['cobertura']:
                    detectada = any(RY.comprobar_cobertura(dsl, entradas, primero=True) for dsl in RY.CORPUS.values())
                else:
                    detectada = any(RY.comprobar(dsl, entradas, primero=True) for dsl in RY.CORPUS.values())
                self.assertTrue(detectada, nombre)

    def test_an_alias_takes_the_logical_range_of_its_anchor_and_not_what_comes_after_it(self):
        # AC237 (28a): el nodo de un ancla acaba en el primer token que le sigue (`end_mark`), que puede ser el
        # indicador de su hermano; la expansión de `*b` lleva hasta el indicador siguiente, sin él.
        C, V = self.C, 'Person:fuldo-fictez'
        for caso, texto, con_comentario in (
                ('el hermano siguiente no entra', f'base:\n  a: &b\n    x: {V}\n  c: {C}\nitems:\n- *b\n', False),
                ('la cola del ancla sí', f'base:\n  a: &b\n    x: {V}\n    {C}\n  c: neutro\nitems:\n- *b\n', True),
                ('la cola del ancla, tras líneas en blanco',
                 f'base:\n  a: &b\n    x: {V}\n\n  {C}\n  c: neutro\nitems:\n- *b\n', True),
                ('un ancla en un elemento y su hermano',
                 f'items:\n- a: &b\n    x: {V}\n  c: {C}\nuso:\n- *b\n', False)):
            with self.subTest(caso=caso):
                # El elemento 0 de la última sección de lista es el que usa el alias.
                entrada = [e for e in RC._entradas_yaml(Path('x.yaml'), Path('boe/x.yaml'), texto)
                           if e[0].endswith('[0]')][-1]
                self.assertIn(V, entrada[1])
                self.assertEqual('Terciano' in entrada[1], con_comentario, entrada[1])

    def test_a_comment_at_any_gap_between_tokens_pairs_with_exactly_the_visible_ends_of_its_units(self):
        # AC237, la propiedad que cierra la clase (AC233 y AC235 la dejaron abierta): en cada hueco entre dos tokens
        # consecutivos del escaneo de PyYAML de cada documento del corpus (`rangos_yaml.CORPUS`: la tabla, los casos
        # de AC233 y AC235 y sus variantes con anclas, alias anidados, claves de fusión, listas y mapas de flujo,
        # claves explícitas, secciones vacías, varios documentos y los seis separadores de línea) se inserta un
        # comentario con el extremo oculto, a continuación del token o en una línea propia, y el comentario comparte
        # entrada con un extremo visible si y solo si algún elemento (o sección, si no es una lista de bloque) los
        # lleva a la vez; lo que decide son las marcas del documento, no un recorrido de tokens.
        entradas = lambda texto: RC._entradas_yaml(Path('x.yaml'), Path('boe/x.yaml'), texto)
        descartes, fallos = Counter(), []
        for nombre, dsl in RY.CORPUS.items():
            for sep, salto in RY.SEPARADORES.items():
                with self.subTest(documento=nombre, separador=sep):
                    sueltos = RY.comprobar(dsl, entradas, salto, descartes)
                    self.assertEqual(sueltos[:2], [], nombre)
        # El barrido no está hueco: los huecos descartados son solo los que PyYAML no admite (un comentario entre una
        # clave implícita y su `:`) y quedan miles de válidos.
        self.assertGreater(descartes.pop('valen'), 6000)
        self.assertEqual(set(descartes), {'ScalarToken→ValueToken', 'AliasToken→ValueToken'}, descartes)


class ValoresDelFicheroTest(unittest.TestCase):
    """Spec 071 AC214: los valores decodificados de un fichero de datos, con sus escapes resueltos; si no se decodifica,
    no se da por limpio."""

    def test_a_json_is_read_with_all_its_pairs_and_its_escapes_resolved(self):
        valores, decodificado = RC._valores_del_fichero('.json', '{"a": "Jos\\u00e9", "a": ["x", {"Mar\\u00eda": 1}]}')
        self.assertTrue(decodificado)
        self.assertEqual(sorted(valores), sorted(['a', 'José', 'a', 'x', 'María']))

    def test_a_yaml_is_read_in_all_its_documents_with_escapes_binaries_and_integers(self):
        texto = 'a: "Jos\\xE9"\n---\nb: !!binary Sm9zw6k=\n---\nc: 12_345\n'
        valores, decodificado = RC._valores_del_fichero('.yaml', texto)
        self.assertTrue(decodificado)
        for esperado in ('José', '12345'):
            self.assertIn(esperado, valores)
        self.assertEqual(valores.count('José'), 2)

    def test_what_is_not_decoded_is_reported(self):
        self.assertEqual(RC._valores_del_fichero('.json', '{"a": '), ([], False))
        self.assertEqual(RC._valores_del_fichero('.yaml', 'a: [1,\n'), ([], False))
        self.assertEqual(RC._valores_del_fichero('.yaml', 'a: !!binary 33333\n')[1], False)
        self.assertEqual(RC._valores_del_fichero('.md', 'Jos\\u00e9'), ([], True))
        # Un anidamiento que ni el lector aguanta tampoco se da por limpio.
        self.assertEqual(RC._valores_del_fichero('.json', '[' * 100000 + ']' * 100000), ([], False))
        self.assertEqual(RC._valores_del_fichero('.yaml', '[' * 100000 + ']' * 100000), ([], False))


def _real(variable, defecto=None):
    ruta = os.environ.get(variable) or defecto
    return Path(ruta) if ruta else None


CENSO = _real('REGISTRO_ROOT', RP.ROOT)
EXPORT = _real('REGISTRO_SNAPSHOT')


@unittest.skipUnless(CENSO and (CENSO / 'census.sqlite').is_file() and EXPORT and (EXPORT / 'nodes.jsonl').is_file(),
                     'sin el censo privado (REGISTRO_ROOT) y un export del grafo (REGISTRO_SNAPSHOT)')
class RegistroRealTest(unittest.TestCase):
    """AC20 sobre el registro del repo: el export que este pide no deja nada que señalar. Es el de qa067b
    (`REGISTRO_SNAPSHOT`) con lo que hará el Path A pendiente: sin las copias que quitan las reglas deny del overlay
    que retiran cada fila «suprimido» `pendiente-path-a` (casadas como el loader, `alcance.casa` exacta) o que llevan
    la etiqueta de la unidad de una fila «suprimido» (las de un par re-identificado después del censo, AC61), con el tipo
    nuevo en las copias que retipan las reglas de cada fila «rectificado» `pendiente-path-a` (AC64), sin las
    posturas que claims.yaml ya no declara (como `claims_loader.prune`), con cada pareja de gemelas idénticas de
    `_reposicion_071.DEDUPLICAR` en una sola copia del tipo que deja su retype, y con las dos aristas repuestas. Una
    fila que nada materializa sigue viva y lo hace fallar."""

    def test_the_export_the_registry_asks_for_reports_nothing(self):
        from pipeline.src.alcance import casa  # el pipeline completo: solo corre con el censo, nunca en el CI
        base = Path(tempfile.mkdtemp(prefix='registro-real-'))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        conn = sqlite3.connect(f'file:{CENSO / "census.sqlite"}?mode=ro', uri=True)
        self.addCleanup(conn.close)

        def valor(uid):
            fila = conn.execute('SELECT kind, value FROM units WHERE unit_id=?', (uid,)).fetchone()
            return json.loads(fila[1]) if fila and fila[0] == 'edge' else None

        from pipeline.src.qa_esc_apply import _TAG, CLAIM_STANCES, _posturas_por_identidad, _reglas_del_overlay
        # El lector neutro del apply (spec 071 AC72): un overlay que el loader rechaza sale con exit 2, su fichero y su
        # posición, nunca con el valor ni el extremo en el informe de la suite, y sin los logs del loader.
        deny, fix = _reglas_del_overlay(RP.REPO, CENSO)  # las opacas, contra su raíz privada (AC127)
        reglas, fixes = [regla for _, regla in deny], [regla for _, regla in fix]
        suprimidas = [r for r in RC.cargar(RC.REGISTRO) if r.get('decision') == 'suprimido' and r.get('unidad')]
        pendientes = [v for v in (valor(r['unidad']) for r in suprimidas if r.get('seed_version') == RC.SEED_PENDIENTE)
                      if v]
        etiquetas = {r['unidad'][:8] for r in suprimidas}
        retiran = [regla for regla in reglas if any(casa(regla, v, exacta=True) for v in pendientes)
                   or set(_TAG.findall(regla.get('reason') or '')) & etiquetas]
        corregidas = [v for v in (valor(r['unidad']) for r in RC.cargar(RC.REGISTRO)
                                  if r.get('decision') == 'rectificado' and r.get('unidad')
                                  and r.get('seed_version') == RC.SEED_PENDIENTE) if v]
        retipan = [regla for regla in fixes if regla.get('corrected_type')
                   and any(casa(regla, v, exacta=True, dirigida=True) for v in corregidas)]
        posturas = _posturas_por_identidad(RP.REPO / 'pipeline/data/claims/claims.yaml')
        dedup = [(valor(retirada), resultante) for retirada, _, resultante in RP.DEDUPLICAR]
        refs, ids = {}, {}
        with (EXPORT / 'nodes.jsonl').open(encoding='utf-8') as handle:
            for line in handle:
                if '"entity_id"' in line:
                    node = json.loads(line)
                    refs[node['props']['entity_id']], ids[node['ref']] = node['ref'], node['props']['entity_id']
        (base / 'nodes.jsonl').symlink_to(EXPORT / 'nodes.jsonl')
        quitadas, supervivientes = 0, [0] * len(dedup)
        with (EXPORT / 'relationships.jsonl').open(encoding='utf-8') as src, \
                (base / 'relationships.jsonl').open('w', encoding='utf-8') as dst:
            for line in src:
                rel = json.loads(line)
                arista = {'type': rel['type'], 'start': ids.get(rel['start']), 'end': ids.get(rel['end']),
                          'props': rel.get('props') or {}}
                if any(casa(regla, arista, exacta=True) for regla in retiran):
                    quitadas += 1
                    continue
                retipo = next((regla['corrected_type'] for regla in retipan
                               if casa(regla, arista, exacta=True, dirigida=True)), None)
                if retipo:
                    rel, arista = dict(rel, type=retipo), dict(arista, type=retipo)
                if rel['type'] in CLAIM_STANCES and arista['end'] is None and (
                        arista['start'], rel['type'], str(arista['props'].get('source_kind') or 'judicial').lower()) \
                        not in posturas:
                    continue  # `claims_loader.prune`: claims.yaml ya no la declara
                for n, (gemela, resultante) in enumerate(dedup):
                    # La migración de REPOSICION: extremos y dirección, el tipo original o el del retype, y sus props.
                    if ((arista['start'], arista['end']) == (gemela['start'], gemela['end'])
                            and arista['type'] in (gemela['type'], resultante)
                            and all(arista['props'].get(k) == x for k, x in gemela['props'].items())):
                        supervivientes[n] += 1
                        rel = dict(rel, type=resultante) if supervivientes[n] == 1 else None
                        break
                if rel is None:
                    continue
                dst.write(json.dumps(rel, ensure_ascii=False) + '\n')
            for n, uid in enumerate(RP.UNIDADES):
                gemela = valor(uid)
                # Sin un extremo en el export, el KeyError imprimiría su entity_id: el mensaje lleva solo la unidad.
                self.assertTrue(gemela['start'] in refs and gemela['end'] in refs,
                                f'{uid[:8]}: un extremo no está en el export')
                dst.write(json.dumps({'start': refs[gemela['start']], 'end': refs[gemela['end']], 'type': gemela['type'],
                                      'props': gemela['props'], 'ref': f'reposicion-{n}'}, ensure_ascii=False) + '\n')
        self.assertGreater(quitadas, 0)
        self.assertTrue(all(supervivientes), 'una pareja de DEDUPLICAR sin copias en el export')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = RC.main(['--snapshot', str(base), '--root', str(CENSO), '--overlay', str(RC.REPO)])
        self.assertEqual(code, 0, out.getvalue())


class LecturaNeutraRealTest(unittest.TestCase):
    """Codex, 11ª pasada (AC72): `RegistroRealTest` leía el overlay con `load_files` y `_prepare_*`, y un `kinds`
    desconocido (un SystemExit del loader con el valor y el extremo) o un `!!int` roto (un ValueError con el valor)
    llegaban enteros al informe de unittest; y 12ª (AC75), lo mismo con `claims.yaml`. Aquí corre contra un overlay o un
    claims.yaml que no valen y un censo vacío en lugar del real, y nada de stdout, stderr ni del informe lleva un
    centinela. Sin el entorno del pipeline (el CI), se salta."""

    def setUp(self):
        try:
            from pipeline.src import qa_esc_apply  # noqa: F401  el pipeline completo, como RegistroRealTest
        except ImportError:
            self.skipTest('sin el entorno del pipeline')

    def informe(self, ficheros):
        """(resultado, todo lo que sale) de `RegistroRealTest` sobre un repo con `ficheros` ({ruta: texto}) y un censo
        vacío."""
        base = Path(tempfile.mkdtemp(prefix='registro-real-lectura-'))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        for ruta, texto in ficheros.items():
            (base / ruta).parent.mkdir(parents=True, exist_ok=True)
            (base / ruta).write_text(texto, encoding='utf-8')
        censo, export = base / 'censo', base / 'export'
        for carpeta in (censo, export):
            carpeta.mkdir()
        db = sqlite3.connect(censo / 'census.sqlite')
        db.execute('CREATE TABLE units (unit_id TEXT, kind TEXT, value TEXT)')
        db.commit()
        db.close()

        class Lectura(RegistroRealTest):
            __unittest_skip__ = False

        informe, salida, errores = io.StringIO(), io.StringIO(), io.StringIO()
        modulo = sys.modules[__name__]
        with mock.patch.object(RP, 'REPO', base), mock.patch.object(modulo, 'CENSO', censo), \
                mock.patch.object(modulo, 'EXPORT', export), contextlib.redirect_stdout(salida), \
                contextlib.redirect_stderr(errores):
            resultado = unittest.TextTestRunner(stream=informe, verbosity=2).run(
                Lectura('test_the_export_the_registry_asks_for_reports_nothing'))
        return resultado, salida.getvalue() + errores.getvalue() + informe.getvalue()

    def sin_valores(self, resultado, todo, neutro):
        self.assertEqual(len(resultado.errors), 1)
        for centinela in ('CENTINELA_VALOR', 'CENTINELA_EXTREMO'):
            self.assertNotIn(centinela, todo)
        self.assertIn(neutro, todo)

    def test_a_rejected_overlay_leaves_no_value_in_the_report(self):
        regla = '  - {source_id: "Person:CENTINELA_EXTREMO", target_id: "Person:otra-ficticia", type: OWNS, %s}\n'
        for caso, extra, neutro in (
                ('kinds desconocido', 'kinds: [CENTINELA_VALOR]',
                 'relations.yaml:deny[0]: fila rechazada por el loader'),
                ('YAML roto', 'quote_contains: !!int "CENTINELA_VALOR"', 'relations.yaml: YAML inválido (')):
            with self.subTest(caso=caso):
                self.sin_valores(*self.informe(
                    {'pipeline/data/relations/relations.yaml': 'deny:\n' + regla % extra + 'fix: []\n'}), neutro)

    def test_a_claims_file_that_does_not_load_leaves_no_value_in_the_report(self):
        # AC75: `_posturas_por_identidad` leía claims.yaml con `yaml.safe_load`: un error de sintaxis cita la línea
        # entera y un `!!int` roto lleva el valor.
        postura = 'claims:\n  - claim_id: c-ficticia\n    asserts:\n      - %s\n'
        for caso, linea in (
                ('YAML roto', '{entity_id: "Person:CENTINELA_EXTREMO", quote: "CENTINELA_VALOR}'),
                ('valor inválido', 'entity_id: !!int "CENTINELA_VALOR"')):
            with self.subTest(caso=caso):
                self.sin_valores(*self.informe({'pipeline/data/relations/relations.yaml': 'deny: []\nfix: []\n',
                                                'pipeline/data/claims/claims.yaml': postura % linea}),
                                 'claims.yaml: YAML inválido (')


class PuertaTest(unittest.TestCase):
    """Codex, 13ª pasada (AC82): las comprobaciones finales de `_qa063b_gate.sh` (registro y materialización) escribían
    su stdout y su stderr en la consola. La puerta corre aquí entera sobre un repo de juguete, con `container`, `make` y
    `uv` de mentira que escriben un centinela por orden en stdout o en stderr: ninguno sale a la consola y todos acaban
    en `$OUT`."""

    STUBS = {
        'container': ('for ultimo; do :; done\n'
                      'echo CENTINELA_VM; echo CENTINELA_VM >&2\n'
                      'case "$ultimo" in\n'
                      '  counts-edit) echo "2, 3" ;;\n'
                      '  alledges-edit) printf "a\\tb\\tPAID\\n" ;;\n'
                      '  accstatus-edit) printf "a\\timputado\\n" ;;\n'
                      'esac\n'),
        'make': 'echo CENTINELA_MAKE; echo CENTINELA_MAKE >&2\n',
        'uv': ('for ultimo; do :; done\n'
               'case "$*" in\n'
               # Spec 070: las aristas que la lista de seudónimos permite (un número) y los dos checks de seudónimos.
               '  *"-c "*) echo 3; echo CENTINELA_UV >&2 ;;\n'
               '  *_qa063b_export_snap.py*) mkdir -p "$ultimo"; printf "{}\\n{}\\n" > "$ultimo/nodes.jsonl"\n'
               '    printf "{}\\n{}\\n{}\\n" > "$ultimo/relationships.jsonl"; echo CENTINELA_EXPORT >&2 ;;\n'
               '  *registro_check.py*) echo "REGISTRO CENTINELA_REGISTRO"; echo CENTINELA_REGISTRO_ERR >&2; exit 1 ;;\n'
               '  *src.qa_esc_apply*) echo "MATERIALIZACION: CENTINELA_MATERIALIZACION"\n'
               '    echo CENTINELA_MATERIALIZACION_ERR >&2; exit 2 ;;\n'
               '  *) echo CENTINELA_UV; echo CENTINELA_UV >&2 ;;\n'
               'esac\n'),
    }
    CENTINELAS = ('CENTINELA_VM', 'CENTINELA_MAKE', 'CENTINELA_EXPORT', 'CENTINELA_REGISTRO', 'CENTINELA_REGISTRO_ERR',
                  'CENTINELA_MATERIALIZACION', 'CENTINELA_MATERIALIZACION_ERR', 'CENTINELA_UV', 'CENTINELA_CICLO')

    def puerta(self, censo=True):
        """(proceso, salida): la puerta entera sobre un repo de juguete con los stubs, con la raíz de la auditoría por
        defecto (un `census.sqlite` vacío basta para que arranque) o sin ella."""
        base = Path(tempfile.mkdtemp(prefix='puerta-'))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        scripts, stubs, out = base / 'repo/pipeline/scripts', base / 'bin', base / 'out'
        scripts.mkdir(parents=True)
        stubs.mkdir()
        if censo:
            raiz = base / 'repo/pipeline/data/audit/dual/20260909-20260909-qa061'
            raiz.mkdir(parents=True)
            (raiz / 'census.sqlite').write_bytes(b'')
        shutil.copy(RC.REPO / 'pipeline/scripts/_qa063b_gate.sh', scripts)
        (scripts / '_qa063b_cycle.sh').write_text('echo CENTINELA_CICLO; echo CENTINELA_CICLO >&2\n', encoding='utf-8')
        # Spec 070: el export canónico de cada ciclo (`snap-edit`, que la puerta corre sin la VM).
        (scripts / '_qa063b_dev.sh').write_text(
            '[ "$1" = snap-edit ] && mkdir -p "$2" && echo canonico > "$2/canonico.tsv"\n'
            'echo CENTINELA_VM; echo CENTINELA_VM >&2\n', encoding='utf-8')
        for nombre, cuerpo in self.STUBS.items():
            (stubs / nombre).write_text('#!/bin/sh\n' + cuerpo, encoding='utf-8')
            (stubs / nombre).chmod(0o755)
        env = {k: v for k, v in os.environ.items() if k not in ('REGISTRO_ROOT', 'QA_CENSO_ROOT')}
        proc = subprocess.run(['bash', str(scripts / '_qa063b_gate.sh'), str(out), '20261008-base-ficticia'],
                              capture_output=True, encoding='utf-8', timeout=120,
                              env={**env, 'PATH': f'{stubs}{os.pathsep}{os.environ["PATH"]}'})
        return proc, out

    def test_without_the_audit_root_the_gate_does_not_start(self):
        # Spec 071 AC116: las reglas deny opacas se resuelven contra el censo, que el loader recibe en QA_CENSO_ROOT;
        # sin él la puerta no arranca: ni la VM ni un ciclo de loaders.
        proc, out = self.puerta(censo=False)
        consola = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, consola)
        self.assertIn('CENSO_FALTA', consola)
        self.assertFalse((out / 'arranque.log').exists())
        for centinela in self.CENTINELAS:
            self.assertNotIn(centinela, consola)

    def test_nothing_the_checks_write_reaches_the_console(self):
        proc, out = self.puerta()
        consola = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, consola)
        for centinela in self.CENTINELAS:
            self.assertNotIn(centinela, consola)
        for marca in ('ALLEDGES_IDENTICOS', 'ACC_IDENTICOS', 'REGISTRO_FALLO', 'MATERIALIZACION_FALLO'):
            self.assertIn(marca, consola)
        escrito = ''.join(p.read_text(encoding='utf-8') for p in out.rglob('*') if p.is_file())
        for centinela in self.CENTINELAS:
            self.assertIn(centinela, escrito)


class ListaPublicadaTest(unittest.TestCase):
    """Fusión 070 ⟷ 071 (principio P con la lista, 9-nov): las filas de un seudónimo cuyo par conserva la lista de
    permitidas (david↔servinabar ASSOCIATED_WITH y david↔UCO INVESTIGATED_BY, vivas en el grafo también tras el Path A)
    nombran sus dos extremos, y `--snapshot` y la puerta no dan «par publicado» (D1); su gemelo con marcador sí."""

    DAVID, SERVINABAR, UCO = 'Person:david', 'Company:servinabar', 'Organization:unidad-central-operativa'
    FILAS = (('R-2026-09-24-0166', 'CONTROLS', SERVINABAR, 'arista retirada; la asociación sigue publicada bajo '
              'ASSOCIATED_WITH'),
             ('R-2026-09-24-0167', 'OWNS', SERVINABAR, 'arista retirada; la asociación sigue publicada bajo '
              'ASSOCIATED_WITH'),
             ('R-2026-09-24-0174', 'INVESTIGATED_BY', UCO, 'arista retirada'),
             ('R-2026-09-24-0176', 'INVESTIGATED_BY', UCO, 'arista retirada'))

    def test_the_rows_of_a_listed_pair_name_it_and_the_gate_does_not_object(self):
        vivas = {('ASSOCIATED_WITH', frozenset((self.DAVID, self.SERVINABAR))): [(self.DAVID, self.SERVINABAR, {})],
                 ('INVESTIGATED_BY', frozenset((self.DAVID, self.UCO))): [(self.DAVID, self.UCO, {})]}
        filas, marcadas, valores = [], [], {}
        for n, (rid, tipo, otro, resto) in enumerate(self.FILAS):
            unidad = f'{n:x}' * 64
            fila = {'id': rid, 'entidad': self.DAVID, 'unidad': unidad, 'decision': 'suprimido',
                    'dato': f'({self.DAVID}) -[{tipo}]-> ({otro}): {resto}'}
            filas.append(fila)
            marca = 'Company:[sociedad]' if otro.startswith('Company:') else 'Organization:[organismo]'
            marcadas.append({**fila, 'dato': fila['dato'].replace(otro, marca)})
            valores[unidad] = {'type': tipo, 'start': self.DAVID, 'end': otro, 'props': {}}
        problemas, _ = RC.reconstrucciones(filas, valores, {}, [], vivas, {})
        self.assertEqual(problemas, [])   # sin marcador, `hallazgos` no las mira: lo que se comprueba es su fórmula
        # La fórmula de D1 (AC142) es verdad con la ASSOCIATED_WITH viva y falsa sin ella; «arista retirada» no lleva.
        for fila, value in zip(filas, valores.values()):
            with self.subTest(fila['id']):
                self.assertIsNone(RC.motivo_publicada(fila, value, vivas))
                sin = {k: v for k, v in vivas.items() if k[0] != 'ASSOCIATED_WITH'}
                self.assertEqual(RC.motivo_publicada(fila, value, sin),
                                 RC.SIN_SIGUE if 'publicada bajo' in fila['dato'] else None)
        problemas, _ = RC.reconstrucciones(marcadas, valores, {}, [], vivas, {})
        self.assertEqual(sorted(rid for rid, motivo in problemas if motivo == RC.MARCADO_PUBLICADO),
                         [rid for rid, *_ in self.FILAS])


if __name__ == '__main__':
    unittest.main()
