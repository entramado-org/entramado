"""Spec 063 T10 — apply de overlays, registro de rectificaciones y censo (sin modelos, red ni grafo).

El censo es falso y mínimo (esquema de `qa_dual.census` + las tablas `esc_*` de CONTRACTS.md)
y los overlays son copias de juguete con la MISMA estructura que los reales; un test aparte
comprueba el insertador de secciones contra el `relations.yaml` de verdad (solo lectura).
"""
import _vigilancia   # AC254 (enmienda 58): la PRIMERA importación; instala la guarda de relojes antes que nada
import _plazo_duro   # AC302 (enmienda 74): el gestor único del plazo duro
import base64
import codecs
import contextlib
import copy
import errno
import functools
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import random
import shutil
import signal
import sqlite3
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import unittest
from unittest import mock

import yaml

# Spec 076: el nivel lento del paso largo del CI. Con `SM_NIVEL=rapido` (solo lo fija `checks.yml`, y solo cuando el cambio
# es de datos) estos tests se omiten; sin esa palabra exacta el decorador devuelve la función tal cual.
def lento(prueba):
    if os.environ.get('SM_NIVEL', '') == 'rapido':
        omitir = unittest.skip('nivel lento (spec 076)')
        return omitir(prueba)
    return prueba

from scripts import registro_check as RC
from src import qa_esc_apply as A
from src import relations_loader, seudonimos
from src import alcance
from tests.test_seudonimos import (_HOMONIMOS, _LECTURAS_DEL_MISMO_NOMBRE, REJILLAS_DE_NOMBRE_LARGO, RELLENO,
                                   ASEGURA, controlando, disposicion, en_hijo,
                                   en_hijo_el_cuerpo, espiando, filas_de_relleno, piezas_distintas, quemar,
                                   rejilla_de_nombre_largo, lectura_desde_un_fichero_de_pruebas, escalada)

REPO = Path(__file__).resolve().parents[2]

U_SUM, U_ROLE, U_ID, U_EDGE, U_EDGE2, U_ED, U_OTRA = (f'{c * 64}' for c in 'abcdefg')
P1, P2, P3 = 'E-c0de00000001', 'E-c0de00000001-2', 'E-c0de00000001-3'
P4, P5, U_A, U_B = 'E-c0de00000001-4', 'E-c0de00000001-5', '1a' * 32, '2b' * 32
FULDO = 'Person:fuldo-fictez'
# Los mensajes de `registro_check` que miran estos tests, por su literal: contra un commit anterior de la spec 071, un
# test falla por lo que hace, no por un nombre que entonces no existía.
HUELLA = 'huella de una fila anonimizada fuera de su regla opaca'
MARCADO_PUBLICADO = 'fila anonimizada con el par publicado: nombra sus dos extremos (D1)'
NO_VERIFICABLE = 'anonimato no verificable (AC113): fila con marcador sin unidad ni declaración privada'
PAR_DESDE_GRAFO = 'par reconstruible desde el grafo'
RETIPO_NO_ALCANZABLE = 'fórmula con un retipo que ninguna regla alcanza'
SIGUE = 'la asociación sigue publicada'
SIN_SIGUE = 'fórmula de asociación publicada sin una arista viva del tipo que dice'
TROZO_CITA = 'fila anonimizada con un trozo de su cita retirada'
NO_VERIFICABLE_FICHERO = 'fichero público no verificable (AC174)'
EXTREMO_FUERA = 'fila anonimizada que nombra un extremo fuera de su dato'

CENSUS_SCHEMA = '''
CREATE TABLE objects (object_id TEXT PRIMARY KEY, object_kind TEXT NOT NULL, ref TEXT NOT NULL,
  entity TEXT, type TEXT NOT NULL, provenance TEXT NOT NULL, technical INTEGER NOT NULL,
  content_hash TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(object_kind, ref));
CREATE TABLE units (unit_id TEXT PRIMARY KEY, object_id TEXT NOT NULL REFERENCES objects,
  kind TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL, technical INTEGER NOT NULL,
  risk INTEGER NOT NULL, prior TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
  resolution TEXT, UNIQUE(object_id,kind,field));
CREATE TABLE tiers (unit_id TEXT PRIMARY KEY REFERENCES units, tier TEXT NOT NULL, rank REAL NOT NULL);
CREATE TABLE events (seq INTEGER PRIMARY KEY, at TEXT NOT NULL, unit_id TEXT, event TEXT NOT NULL,
  payload TEXT NOT NULL);
CREATE TABLE esc_packs (pack_id TEXT PRIMARY KEY, entity TEXT, kind TEXT, idx INTEGER, total INTEGER,
  unit_ids TEXT, pack_path TEXT, pack_sha256 TEXT, overlay_ref TEXT, riesgo TEXT, clase TEXT,
  route TEXT, state TEXT, batch TEXT, created_at TEXT);
CREATE TABLE esc_readings (attempt_id TEXT PRIMARY KEY, pack_id TEXT, side TEXT, result TEXT,
  receipt TEXT, at TEXT, UNIQUE(pack_id, side));
'''

SUMMARIES = """# Overlay de prueba (misma forma que summaries_qa061.yaml).

summaries:
- entity_id: Person:fuldo-fictez
  name: Fuldo Fíctez
  resumen: Fuldo Fíctez cobró 5.700 euros. Otra frase que nadie audita.
  updated: '2026-09-01'
  reason: fila previa
"""

ROLES = """# Overlay de prueba (misma forma que roles_qa061.yaml).
entities:
- type: Person
  name: Otro
  entity_id: Person:otro
  role: Rol previo.
- type: Person
  name: Fuldo Fíctez
  entity_id: Person:fuldo-fictez
  role: Asesor del ministro de Transportes.
relations: []
"""

RELATIONS = """# Overlay de prueba (misma forma que relations.yaml).
splits: []

deny:
  # --- fila previa ---
  - {source_id: "Person:pareja-ficticia", target_id: "Person:ministro-ficticio",
     type: FAMILY_OF, reason: "Mis-extracción: la cita dice 'pareja', no familiar."}

fix:
  - {source_id: "Event:convenio", target_id: "Organization:aragon", type: PARTY_TO,
     swap_direction: true, doc: "informe-uco.pdf", reason: "Dirección invertida."}

# ------------------------------------------------------------------------------
# alias_deny: — higiene de alias por-entidad (spec 025).
alias_deny:
  - id: "Company:aerolinea-ficticia"
    reason: "s86 — los alias de la filial viven en su nodo propio"
    drop:
      - "Aerolinea Ficticia Travel"
"""

ACCUSATIONS = "accusations:\n  - person: Fuldo Fíctez\n    entity_id: Person:fuldo-fictez\n"

# Spec 070: el apply lee los seudónimos declarados y la lista de permitidas del repo que escribe.
# El slug de SOC no puede ser el nombre de pila de SEUDO («zz-test»): AC338 (enmienda 91) lo daría por homónimo.
SEUDO, SOC, ORG = 'Person:zz-seudonimo-test', 'Company:zz-sociedad-test', 'Organization:zz-organismo-test'
PSEUDONYMS = f"pseudonyms:\n- id: {SEUDO}\n  note: seudónimo sintético de los tests\n"
PERMITIDOS = "aristas: []\nalias: []\n"
# AC80: una entrada sembrada de la lista lleva la forma y la plantilla cerradas del estado publicado.
SEMILLA = {'origen': 'estado-2026-10-07',
           'motivo': 'estado publicado del seed 20261007-qa067b, sin revisar (spec 070, Q1)'}
# Los documentos sintéticos que publica el árbol de prueba: solo de ellos puede citar una regla pública con extremo
# seudónimo (AC30). El árbol de prueba no es un repo git, así que se inyectan (`apply`/`gate`/`auditar(publicos=…)`).
DOCS_PUBLICOS = ('ATS_0000_2025.pdf', 'ATS_1111_2025.pdf', 'ATS_A.pdf', 'ATS_B.pdf', 'ATS_C.pdf')


def edge_value(end='Crime:cohecho', rtype='ACCUSED_OF', **props):
    base = {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf', 'source_page': 76,
            'source_quote': 'Fuldo Fíctez recibió 5.700 euros en efectivo de la trama'}
    base.update(props)
    return {'start': FULDO, 'end': end, 'type': rtype, 'props': base}


def identity_value(entity, name, aliases, **extra):
    """Una unidad `identity` tal como la guarda el censo (`qa_dual.py:313`): UNA por entidad (`field` ''), con el
    bloque entero de su identidad. `aliases=None`: el nodo no tiene y la clave falta."""
    identity = {'entity_id': entity, 'name': name, **extra}
    if aliases is not None:
        identity['aliases'] = list(aliases)
    return {'identity': identity, 'type': 'Person'}


def unit(uid, verdict='verificado', label='ok', **over):
    row = {'unit_id': uid, 'verdict': verdict, 'label': label, 'evidence': 'informe UCO p.76',
           'confidence': 'alta', 'requested_sources': []}
    row.update(over)
    return row


def decision(pack_id=P1, entity=FULDO, units=None, resolution='opus-cerrada', **over):
    d = {'entity_id': entity,
         'units': units if units is not None else [unit(U_SUM), unit(U_ROLE), unit(U_ID),
                                                   unit(U_EDGE), unit(U_ED)],
         'summary': {'action': 'none', 'resumen': '', 'sources': [], 'reason': ''},
         'role': {'action': 'none', 'role': '', 'reason': ''}, 'alias_deny': [],
         'estado': {'action': 'none', 'status': '', 'fecha': '', 'doc': '', 'url': '', 'tipo': '', 'nota': ''},
         'edges': [], 'editorial': [], 'notes': '',
         '_meta': {'pack_id': pack_id, 'entity': entity, 'kind': 'entity', 'index': 1, 'total': 1,
                   'resolution': resolution, 'route': 'cerrar', 'decided_at': '2026-09-21T10:00:00+00:00',
                   'sides': ['opus'], 'unit_ids': [], 'overlay_ref': 'worktree'}}
    d.update(over)
    return d


def partial(units, unit_ids, resolution='triple-cerrada', name=None, **over):
    """Una decisión que cubre solo PARTE del pack (cierre parcial de la 063: `_meta.unit_ids`)."""
    d = decision(units=units, resolution=resolution, **over)
    d['_meta']['unit_ids'] = list(unit_ids)
    d['_file'] = name or f'{d["_meta"]["pack_id"]}.json'
    return d


def edge_op(uid, action, **over):
    op = {'unit_id': uid, 'action': action, 'new_type': '', 'prop': '', 'value': '', 'status': '',
          'evidence': 'informe UCO p.76', 'reason': 'la cita no sostiene la relación'}
    op.update(over)
    return op


def set_unit(d, uid, **over):
    d = copy.deepcopy(d)
    for u in d['units']:
        if u['unit_id'] == uid:
            u.update(over)
    return d


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def nodo_del_censo(root, entity, tipo, nombre):
    """AC122 (spec 070, enmienda 31): el nodo de una entidad en el censo, con su nombre: el extremo civil de una arista
    conservada cuyos nombres se conocen (sin ellos, su cita no se da por que no lo nombra)."""
    db = sqlite3.connect(Path(root) / 'census.sqlite')
    db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
               (f'o-n-{entity}', 'node', f'n:{entity}', entity, tipo, 'unspecified', 0, 'h',
                json.dumps({'labels': [tipo], 'props': {'name': nombre}})))
    db.commit()
    db.close()


@contextlib.contextmanager
def presupuesto(test, segundos):
    """AC112 (spec 070, enmienda 29): el bloque termina en menos de `segundos`. Una alarma lo corta a las diez veces:
    el código anterior no acababa. AC335 (enmienda 90): `segundos` (la cota y su alarma) llevan el factor de
    `SM_FACTOR_DE_CPU`."""
    segundos = escalada(segundos)

    def cortar(*_):
        raise TimeoutError(f'el bloque no terminó en {10 * segundos:g} s')
    anterior = signal.signal(signal.SIGALRM, cortar)
    signal.setitimer(signal.ITIMER_REAL, 10 * segundos)
    inicio = time.perf_counter()
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, anterior)
    test.assertLess(time.perf_counter() - inicio, segundos)


@contextlib.contextmanager
def con_primera_llamada(segundos, ruta):
    """AC235 (enmienda 52, Codex 42c): el control de la primera llamada: `seudonimos.<ruta>` (`_fichas_de`: cada texto;
    `_candidato`: cada nombre) con `segundos` de CPU de más la PRIMERA vez que recibe una entrada larga, con su
    respuesta intacta y con memoria propia de las que ya vio (que `vaciar_caches()` no olvida: la peor forma, una
    entrada vista no vuelve a pagar). Una vez por proceso: cada hijo nace sin memoria y paga la primera entrada nueva
    (AC243: un control de `controlando`)."""
    original, vistos, pendiente = getattr(seudonimos, ruta), set(), [True]

    def con_sobrecoste(entrada, *args, **kwargs):
        if len(entrada) > 100 and entrada not in vistos:
            vistos.add(entrada)
            if pendiente[0]:
                pendiente[0] = False
                quemar(segundos)
        return original(entrada, *args, **kwargs)
    con_sobrecoste.cache_clear = original.cache_clear   # `vaciar_caches()` sigue vaciando la caché de verdad
    with mock.patch.object(seudonimos, ruta, con_sobrecoste):
        yield lambda: pendiente.__setitem__(0, True)


@contextlib.contextmanager
def con_coste_en_las_citas_que_empiezan_por(segundos, prefijo):
    """AC238 (enmienda 53), AC243 (enmienda 55): `segundos` de CPU de más en cada `_nombres_en` cuyo texto empieza por
    `prefijo`, sin memoria (un control de `controlando`)."""
    original = seudonimos._nombres_en

    def con_coste(texto, *args, **kwargs):
        if texto.startswith(prefijo):
            quemar(segundos)
        return original(texto, *args, **kwargs)
    with mock.patch.object(seudonimos, '_nombres_en', con_coste):
        yield


def medir_en_frio(camino, repo, snap, lista, rutas, segundos) -> float:
    """AC238 (enmienda 53), AC243 (enmienda 55): el tiempo de CPU de UNA ejecución de `camino` (`árbol`: `auditar`;
    otro: `verificar_snapshot`) sobre lo que la prueba dejó en disco; se ejecuta en un hijo, que arrancó en frío (las
    22 cachés vacías, comprobado). Mide `auditar` o `verificar_snapshot` enteros: el censo, el índice civil con su
    `derivado`, los candidatos y las fichas de la cita se construyen dentro. Afirma que corrieron las `rutas` y que lo
    hizo en menos de `segundos` (a factor 1: `presupuesto` les aplica `SM_FACTOR_DE_CPU`, AC335)."""
    publicos = frozenset(DOCS_PUBLICOS)
    with espiando(*rutas) as llamadas:
        inicio = time.process_time()
        with presupuesto(ASEGURA, segundos):
            if camino == 'árbol':
                seudonimos.auditar(repo, publicos)
            else:
                seudonimos.verificar_snapshot(snap, frozenset({SEUDO}), lista,
                                              personas=seudonimos.personas_contexto(repo), publicos=publicos, repo=repo)
        tiempo = time.process_time() - inicio
    sin_ejecutar = [ruta for ruta in rutas if not llamadas[ruta]]
    ASEGURA.assertEqual(sin_ejecutar, [], f'{camino} no ejecutó su ruta')
    return tiempo


# AC238 (enmienda 53): las entradas del árbol y el snapshot: (semilla de la cita, letra del nombre) de cada una, de la
# misma familia; la cota vale para CADA una (el mínimo es solo entre repeticiones de la misma), y el número de caso da
# ids nuevos a cada una (el censo guarda un nodo por id). La primera es la que la 43ª mostró en la rejilla (4, True,
# False): una cita que empieza por «a-b».
ENTRADAS_DE_NOMBRE_LARGO = ((3, 'a'), (4, 'c'))
CASOS_DE_NOMBRE_LARGO = itertools.count()


class EscApplyBase(unittest.TestCase):
    tag = '063 tanda de prueba'
    upd = '2026-09-21'

    def setUp(self):
        # Resuelta: `privatizar` rechaza un enlace en la ruta de su salida (spec 071 AC167), y en macOS `/var` lo es.
        self.base = Path(tempfile.mkdtemp(prefix='qa-esc-apply-')).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / 'audit'
        self.repo = self.base / 'repo'
        self.publicos = frozenset(DOCS_PUBLICOS)
        (self.root / 'esc/decisions').mkdir(parents=True)
        # El fichero privado de las reglas opacas, vacío: sin él, ninguna se resuelve (spec 071 AC127).
        (self.root / alcance.REGLAS_OPACAS).write_text('{}\n', encoding='utf-8')
        for rel, text in ((A.SUM_PATH, SUMMARIES), (A.ROL_PATH, ROLES), (A.REL_PATH, RELATIONS),
                          ('pipeline/data/accusations/accusations.yaml', ACCUSATIONS),
                          (seudonimos.PSEUDONYMS_REL, PSEUDONYMS), (seudonimos.PERMITIDOS_REL, PERMITIDOS)):
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        self._census()

    def _census(self):
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.executescript(CENSUS_SCHEMA)
        node = json.dumps({'labels': ['Person'], 'props': {
            'name': 'Fuldo Fíctez', 'canonical_name': 'Fuldo Fíctez', 'aliases': ['Fuldo'],
            'summary': 'Fuldo Fíctez cobró 5.700 euros. Otra frase que nadie audita.',
            'role': 'Asesor del ministro de Transportes.'}}, ensure_ascii=False)
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-node', 'node', 'n:1', FULDO, 'Person', 'unspecified', 0, 'h', node))
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-rel', 'relationship', 'r:1', FULDO, 'ACCUSED_OF', 'judicial', 0, 'h', '{}'))
        rows = [(U_SUM, 'o-node', 'summary', '/summary@0:31', 'Fuldo Fíctez cobró 5.700 euros.'),
                (U_ROLE, 'o-node', 'attribute', '/role', 'Asesor del ministro de Transportes.'),
                (U_ID, 'o-node', 'identity', '', identity_value(FULDO, 'Fuldo Fíctez', ['Fuldo'])),
                (U_ED, 'o-node', 'editorial', '/feed@0', 'Titular. Cuerpo del ítem.'),
                (U_OTRA, 'o-node', 'summary', '/summary@32:60', 'Otra frase que nadie audita.')]
        for uid, oid, kind, field, value in rows:
            db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                       ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, oid, kind, field, json.dumps(value)))
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")',
                   (U_EDGE, 'o-rel', 'edge', '/edge@1', json.dumps(edge_value(), ensure_ascii=False)))
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")',
                   (U_EDGE2, 'o-rel', 'edge', '/edge@2',
                    json.dumps(edge_value(end='Company:ficcionbar', rtype='PAID', amount=5700.0,
                                          source_quote='pago de 5.700 euros a Ficcionbar'),
                               ensure_ascii=False)))
        for uid in (U_SUM, U_ROLE, U_ID, U_EDGE, U_EDGE2, U_ED, U_OTRA):
            db.execute('INSERT INTO tiers VALUES (?,?,?)', (uid, 'T0', 1.0))
        packs = [(P1, 1, 1, [U_SUM, U_ROLE, U_ID, U_EDGE, U_ED]),
                 (P2, 2, 2, [U_EDGE2]),
                 (P3, 1, 1, [U_EDGE2])]
        for pack_id, idx, total, unit_ids in packs:
            db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) '
                       'VALUES (?,?,?,?,?,?,?)',
                       (pack_id, FULDO, 'entity', idx, total, json.dumps(unit_ids), 'routed'))
        db.commit()
        db.close()

    def accusation_layer(self, *entradas, rtype='ACCUSED_OF', end='Crime:cohecho'):
        """U_EDGE2 pasa a ser la arista curada de Fuldo (capa acusacion, `rtype`, hacia `end`) y accusations.yaml
        declara una entrada de Fuldo por cada lista de delitos de `entradas`; documents.yaml declara la firmeza
        del auto, que el cargador exige a una condena."""
        conn = A.connect(self.root)
        with conn:
            conn.execute('UPDATE units SET value=? WHERE unit_id=?',
                         (json.dumps(edge_value(end=end, rtype=rtype, source_kind='acusacion',
                                                source_doc='auto-fuldo.pdf'), ensure_ascii=False), U_EDGE2))
        conn.close()
        (self.repo / 'pipeline/data/accusations/accusations.yaml').write_text(yaml.safe_dump(
            {'accusations': [{'person': 'Fuldo Fíctez', 'entity_id': FULDO, 'doc': 'auto-fuldo.pdf', 'crimes': crimes}
                             for crimes in entradas]}, allow_unicode=True), encoding='utf-8')
        documents = self.repo / 'pipeline/data/documents/documents.yaml'
        documents.parent.mkdir(parents=True, exist_ok=True)
        documents.write_text('documents:\n  - {doc_id: auto-fuldo.pdf, doc_type: sentencia, sentencia_firme: false}\n',
                             encoding='utf-8')

    def write(self, name, d):
        (self.root / 'esc/decisions' / f'{name}.json').write_text(
            json.dumps(d, ensure_ascii=False), encoding='utf-8')
        return d

    def gate(self, *decisions):
        conn = A.connect(self.root)
        try:
            return A.gate(conn, list(decisions), self.root, publicos=self.publicos)
        finally:
            conn.close()

    def apply(self, *decisions, **kwargs):
        kwargs.setdefault('publicos', self.publicos)
        return A.apply(self.root, self.repo, list(decisions), self.tag, self.upd, **kwargs)

    def assertRejects(self, fragment, *decisions):
        errors, _ = self.gate(*decisions)
        self.assertTrue(any(fragment in e for e in errors), f'expected {fragment!r} in {errors}')

    def pack_state(self, pack_id):
        conn = A.connect(self.root)
        try:
            return conn.execute('SELECT state FROM esc_packs WHERE pack_id=?', (pack_id,)).fetchone()['state']
        finally:
            conn.close()

    def state(self, uid):
        conn = A.connect(self.root)
        try:
            row = conn.execute('SELECT state, resolution FROM units WHERE unit_id=?', (uid,)).fetchone()
            return row['state'], row['resolution']
        finally:
            conn.close()


class FixAntesDeDenyTest(EscApplyBase):
    """Codex, 5ª y 6ª pasadas: `relations_loader` aplica los `fix` antes que los `deny`, y sobre todas las aristas, sin
    packs. Un `set_prop` de capa o un `retype` escrito por par, documento y cita que alcance la arista que otra regla
    retira la saca de su deny y la deja publicada, venga del lote o esté ya en el overlay."""

    def setUp(self):
        super().setUp()
        # Spec 070: el apply lee el árbol público con git, y el repo de juguete no lo es: el de los documentos del
        # fixture, como las pruebas del CLI del 070.
        publicos = mock.patch.object(seudonimos, 'documentos_publicos', return_value=frozenset(DOCS_PUBLICOS))
        publicos.start()
        self.addCleanup(publicos.stop)

    def twin_packs(self, **props_b):
        """U_A (P4) y U_B (P5), gemelas de Fuldo en packs distintos (la misma cita y documento, otra página)."""
        db = A.connect(self.root)
        with db:
            for pack, uid, field, props in ((P4, U_A, '/edge@3', {'source_page': 76}),
                                            (P5, U_B, '/edge@4', {'source_page': 77, **props_b})):
                db.execute('INSERT OR REPLACE INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                           ' VALUES (?,?,?,?,?,0,100,"[]")',
                           (uid, 'o-rel', 'edge', field, json.dumps(edge_value(**props), ensure_ascii=False)))
                db.execute('INSERT OR REPLACE INTO tiers VALUES (?,?,?)', (uid, 'T0', 1.0))
                db.execute('INSERT OR REPLACE INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) '
                           'VALUES (?,?,?,?,?,?,?)', (pack, FULDO, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.close()

    def edge_decision(self, pack, uid, action, **over):
        return decision(pack_id=pack, units=[unit(uid, verdict='corregido', label='arista')],
                        edges=[edge_op(uid, action, **over)], _file=f'{pack}.json')

    def guarda(self, *decisions):
        self.assertEqual(self.gate(*decisions)[0], [])
        return A._guarda_fix_deny(A.materialize(list(decisions), self.tag, self.upd), self.repo)

    def test_a_layer_change_that_reaches_an_edge_another_pack_withdraws_is_rejected(self):
        casos = (('set_prop de capa', 'set_prop', {'prop': 'source_kind', 'value': 'prensa'}, {}, True),
                 ('retype', 'retype', {'new_type': 'CONVICTED_OF'}, {}, True),
                 ('set_prop de nota', 'set_prop', {'prop': 'note', 'value': 'nota ficticia'}, {}, False),
                 ('desde otro documento', 'set_prop', {'prop': 'source_kind', 'value': 'prensa'},
                  {'source_doc': 'otro-informe.pdf'}, False))
        for caso, accion, over, props_b, bloquea in casos:
            with self.subTest(caso=caso):
                self.twin_packs(**props_b)
                errores = self.guarda(self.edge_decision(P4, U_A, 'deny'), self.edge_decision(P5, U_B, accion, **over))
                self.assertEqual(len(errores), 1 if bloquea else 0, errores)
                for error in errores:
                    self.assertIn(f'(lote [{U_B[:8]}] → lote [{U_A[:8]}])', error)
                    for valor in ('Fuldo Fíctez recibió', 'informe-uco.pdf', 'prensa', FULDO):
                        self.assertNotIn(valor, error)

    def marcar_aplicado(self, pack):
        db = A.connect(self.root)
        with db:
            db.execute("UPDATE esc_packs SET state='applied' WHERE pack_id=?", (pack,))
        db.close()

    def test_a_fix_already_in_the_overlay_still_blocks_the_check(self):
        # El retype de U_B se aplicó (su regla está en relations.yaml) y su pack quedó `applied`: `skip_applied` lo saca
        # del lote, pero la regla sigue actuando sobre el deny nuevo de U_A, y la guarda la sigue mirando.
        self.twin_packs()
        retipo = self.write('P5', self.edge_decision(P5, U_B, 'retype', new_type='CONVICTED_OF'))
        A.apply(self.root, self.repo, [retipo], self.tag, self.upd)
        self.marcar_aplicado(P5)
        deny = self.write('P4', self.edge_decision(P4, U_A, 'deny'))
        argv = ['--root', str(self.root), '--repo', str(self.repo), 'check']
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            A.main(argv)
        self.assertIn('una regla fix de capa o tipo alcanza el objetivo de un deny (relations.yaml:fix[', out.getvalue())
        self.assertIn(f'→ lote [{U_A[:8]}])', out.getvalue())
        # Con el deny también en el overlay y los dos packs aplicados no queda lote: la guarda corre igual.
        self.assertEqual(self.gate(deny)[0], [])
        rows = A.materialize([deny], self.tag, self.upd)
        texto, _ = A._write_relations((self.repo / A.REL_PATH).read_text(encoding='utf-8'),
                                      {'deny': rows['deny'], 'fix': [], 'alias_deny': []}, self.tag)
        (self.repo / A.REL_PATH).write_text(texto, encoding='utf-8')
        self.marcar_aplicado(P4)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(A.main(argv), 1)
        self.assertIn('(relations.yaml:fix[', out.getvalue())
        self.assertIn('→ relations.yaml:deny[', out.getvalue())
        self.assertNotIn('0 decisiones', out.getvalue())


class LoadTest(EscApplyBase):
    def test_resolution_prefix_filters_the_folder(self):
        self.write('a', decision(resolution='opus-cerrada'))
        d = decision(pack_id=P3, units=[unit(U_EDGE2)], resolution='humano-2026-09-25')
        self.write('b', d)
        self.assertEqual(len(A.load_decisions(self.root)), 2)
        self.assertEqual([d['_meta']['resolution'] for d in A.load_decisions(self.root, ['humano-2026-09-25'])],
                         ['humano-2026-09-25'])
        self.assertEqual(len(A.load_decisions(self.root, ['triple-cerrada'])), 0)


class GateTest(EscApplyBase):
    def test_clean_decision_passes_with_coverage(self):
        errors, coverage = self.gate(decision())
        self.assertEqual(errors, [])
        self.assertEqual((coverage['decisiones'], coverage['unidades'], coverage['entidades']), (1, 5, 1))
        self.assertEqual(coverage['por_resolucion'], {'opus-cerrada': 1})

    def test_pack_must_exist_in_esc_packs(self):
        self.assertRejects('sin fila en esc_packs', decision(pack_id='E-noexiste'))

    def test_unit_of_the_pack_must_exist_in_the_census(self):
        conn = A.connect(self.root)
        with conn:
            conn.execute("UPDATE esc_packs SET unit_ids=? WHERE pack_id=?",
                         (json.dumps([U_EDGE2, 'z' * 64]), P3))
        conn.close()
        self.assertRejects('no existe en el censo', decision(pack_id=P3, units=[unit(U_EDGE2)]))

    def test_meta_unit_ids_bound_the_pack_and_two_disjoint_files_are_accepted(self):
        triple = partial([unit(U_SUM), unit(U_ROLE), unit(U_ID)], [U_SUM, U_ROLE, U_ID])
        humano = partial([unit(U_EDGE), unit(U_ED)], [U_EDGE, U_ED], resolution='humano-2026-09-25',
                         name=f'{P1}.humano-2026-09-25.json')
        errors, coverage = self.gate(triple, humano)
        self.assertEqual(errors, [])
        self.assertEqual(coverage['unidades'], 5)
        self.assertEqual([u['unit_id'] for u in triple['_pack']['units']], [U_SUM, U_ROLE, U_ID])

    def test_two_files_of_the_same_pack_may_not_overlap(self):
        self.assertRejects('solape de unidades',
                           partial([unit(U_SUM), unit(U_ROLE), unit(U_ID)], [U_SUM, U_ROLE, U_ID]),
                           partial([unit(U_ID), unit(U_EDGE), unit(U_ED)], [U_ID, U_EDGE, U_ED],
                                   resolution='humano-2026-09-25', name='otra.json'))

    def test_meta_unit_ids_must_belong_to_the_pack(self):
        self.assertRejects('no pertenece al pack',
                           partial([unit(U_SUM)], [U_SUM, U_EDGE2]))

    def test_units_are_not_covered_twice(self):
        a = decision(pack_id=P2, units=[unit(U_EDGE2)])
        b = decision(pack_id=P3, units=[unit(U_EDGE2)])
        self.assertRejects('repetida', a, b)

    def test_vague_press_attribution_is_rejected(self):
        d = set_unit(decision(), U_SUM, verdict='corregido', label='atrib')
        d['summary'] = {'action': 'replace', 'resumen': 'Según la prensa, cobró 5.700 euros.',
                        'sources': [], 'reason': 'atribución'}
        self.assertRejects('según la prensa', d)
        d2 = decision()
        d2['estado'] = dict(d2['estado'], nota='según los medios, está imputado')
        self.assertRejects('estado.nota con «según la prensa', d2)

    def test_remove_with_text_is_rejected(self):
        d = set_unit(decision(), U_SUM, verdict='corregido', label='retirada')
        d['summary'] = {'action': 'remove', 'resumen': 'texto que no debería ir',
                        'sources': [], 'reason': 'sin fuente'}
        self.assertRejects('remove con resumen', d)

    def test_subpack_cannot_carry_entity_actions(self):
        d = decision(pack_id=P2, units=[unit(U_EDGE2, verdict='corregido', label='arista')],
                     edges=[edge_op(U_EDGE2, 'deny')])
        d['alias_deny'] = ['Fuldo']
        self.assertRejects('sub-pack que no es el primero', d)

    def test_state_rules_per_resolution(self):
        conn = A.connect(self.root)
        with conn:
            conn.execute("UPDATE units SET state='verified', resolution='humano-2026-09-17e' "
                         'WHERE unit_id=?', (U_EDGE2,))
        conn.close()
        self.assertRejects('solo cubre pending', decision(pack_id=P3, units=[unit(U_EDGE2)]))
        # La palabra del dueño manda sobre cualquier estado, incluida una unidad que ya cerró él.
        errors, _ = self.gate(decision(pack_id=P3, units=[unit(U_EDGE2)], resolution='humano-2026-09-25'))
        self.assertEqual(errors, [])
        # Y el propio apply es re-ejecutable: la unidad que cerró esta misma resolución sigue cubierta.
        conn = A.connect(self.root)
        with conn:
            conn.execute("UPDATE units SET resolution='opus-cerrada-corregido' WHERE unit_id=?", (U_EDGE2,))
        conn.close()
        errors, _ = self.gate(decision(pack_id=P3, units=[unit(U_EDGE2)]))
        self.assertEqual(errors, [])

    def test_a_census_pseudonym_outside_the_roster_is_rejected(self):
        # Codex [alto] (spec 070): el censo marca `is_pseudonym` pero context/pseudonyms.yaml no lo declara. La
        # política del loader solo retira a los declarados: «retirar» dejaría un «suprimido» que nadie borra.
        fuera, uid, pack_id = 'Person:zz-fuera-del-roster-test', 'p' * 64, 'E-zzfuera00001'
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-f-node', 'node', 'n:f', fuera, 'Person', 'unspecified', 0, 'h',
                    json.dumps({'labels': ['Person'],
                                'props': {'name': 'Zz Fuera Del Roster Test', 'is_pseudonym': True}})))
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-f-rel', 'relationship', 'r:f', fuera, 'PAID', 'judicial', 0, 'h', '{}'))
        value = {'start': fuera, 'end': SOC, 'type': 'PAID', 'props': {
            'source_kind': 'judicial', 'source_doc': 'ATS_0000_2025.pdf', 'source_page': 3,
            'source_quote': 'cita sintética del auto'}}
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, 'o-f-rel', 'edge', '/edge@1', json.dumps(value)))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   (pack_id, fuera, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.commit()
        db.close()
        d = decision(pack_id=pack_id, entity=fuera, units=[unit(uid, 'corregido', 'arista')],
                     edges=[edge_op(uid, 'deny')], _file=f'{pack_id}.json')
        conn = A.connect(self.root)
        try:
            with mock.patch.object(A, 'materialize', wraps=A.materialize) as materialize:
                errors, _ = A.gate(conn, [d], self.root, frozenset({SEUDO}))
            self.assertEqual(errors, [f'{pack_id}.json: {fuera}: el censo lo marca como seudónimo judicial pero no '
                                      'está declarado en context/pseudonyms.yaml; decláralo antes de aplicar'])
            materialize.assert_not_called()
            # Declarado, la misma decisión pasa: su retirada va a la lista de permitidas, no a una regla pública.
            errors, _ = A.gate(conn, [d], self.root, frozenset({SEUDO, fuera}))
            self.assertEqual(errors, [])
        finally:
            conn.close()


class MaterializeTest(EscApplyBase):
    def rows(self, *decisions):
        errors, _ = self.gate(*decisions)
        self.assertEqual(errors, [])
        return A.materialize(list(decisions), self.tag, self.upd, publicos=self.publicos)

    def edge_decision(self, action, **over):
        return decision(pack_id=P3, units=[unit(U_EDGE2, verdict='corregido', label='arista')],
                        edges=[edge_op(U_EDGE2, action, **over)])

    def test_deny_rows_load_through_the_relations_loader(self):
        rows = self.rows(self.edge_decision('deny'))
        self.assertEqual(len(rows['deny']), 1)
        row = rows['deny'][0]
        self.assertEqual((row['source_id'], row['target_id'], row['type']),
                         (FULDO, 'Company:ficcionbar', 'PAID'))
        self.assertEqual(row['doc'], 'informe-uco.pdf')
        self.assertIn(row['quote_contains'], 'pago de 5.700 euros a Ficcionbar')
        prepared = relations_loader._prepare_deny(rows['deny'])
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]['source_id'], FULDO)
        self.assertEqual(prepared[0]['type'], 'PAID')

    def test_quote_contains_is_a_literal_prefix_of_the_stored_quote(self):
        quote = 'x' * 200
        conn = A.connect(self.root)
        with conn:
            conn.execute('UPDATE units SET value=? WHERE unit_id=?',
                         (json.dumps(edge_value(end='Company:ficcionbar', rtype='PAID', source_quote=quote)),
                          U_EDGE2))
        conn.close()
        row = self.rows(self.edge_decision('deny'))['deny'][0]
        self.assertEqual(len(row['quote_contains']), A.QUOTE_CHARS)
        self.assertIn(row['quote_contains'], quote)

    def test_fix_rows_cover_retype_invert_and_props(self):
        cases = [(self.edge_decision('retype', new_type='ASSOCIATED_WITH'),
                  {'corrected_type': 'ASSOCIATED_WITH'}),
                 (self.edge_decision('invert'), {'swap': True}),
                 (self.edge_decision('set_prop', prop='amount', value='5700'), {'set': {'amount': 5700.0}}),
                 (self.edge_decision('set_prop', prop='amount', value='5.700,00'), {'set': {'amount': 5700.0}}),
                 (self.edge_decision('set_prop', prop='source_kind', value='prensa'),
                  {'set': {'source_kind': 'prensa'}}),
                 (self.edge_decision('set_prop', prop='date', value='2019-04-02'),
                  {'set': {'date': '2019-04-02', 'date_norm': '2019-04-02', 'date_year': 2019}})]
        for d, expected in cases:
            with self.subTest(expected=expected):
                rows = self.rows(d)
                self.assertEqual(len(rows['fix']), 1)
                prepared = relations_loader._prepare_fix(rows['fix'])
                self.assertEqual(len(prepared), 1, rows['fix'])
                for key, value in expected.items():
                    self.assertEqual(prepared[0][key], value)
                self.assertEqual(prepared[0]['source_id'], FULDO)

    def _set_edge2(self, **over):
        conn = A.connect(self.root)
        with conn:
            conn.execute('UPDATE units SET value=? WHERE unit_id=?',
                         (json.dumps(edge_value(**over), ensure_ascii=False), U_EDGE2))
        conn.close()

    def test_non_judicial_edges_carry_kinds_or_the_loader_skips_them(self):
        # 063: el loader solo toca null/judicial salvo que la fila nombre la capa (cazado en qa063a).
        self._set_edge2(end='Company:ficcionbar', rtype='PAID', source_kind='prensa',
                        source_quote='pago de 5.700 euros a Ficcionbar')
        rows = self.rows(self.edge_decision('deny'))
        self.assertEqual(rows['deny'][0]['kinds'], ['prensa'])
        self.assertEqual(relations_loader._prepare_deny(rows['deny'])[0]['kinds'], ['prensa'])
        self.assertEqual(rows['capas'], [])  # prensa extraída de un PDF: no hay YAML de capa que tocar

    def test_judicial_edges_stay_without_kinds(self):
        rows = self.rows(self.edge_decision('deny'))
        self.assertNotIn('kinds', rows['deny'][0])
        self.assertEqual(rows['capas'], [])

    def test_layer_yaml_edges_are_listed_for_manual_edit(self):
        for kind, extra in (('ultima_hora', {}), ('acusacion', {}), ('prensa', {'url': 'https://x.example/a'})):
            with self.subTest(kind=kind):
                self._set_edge2(end='Company:ficcionbar', rtype='PAID', source_kind=kind,
                                source_quote='pago de 5.700 euros a Ficcionbar', **extra)
                rows = self.rows(self.edge_decision('deny'))
                self.assertEqual(rows['deny'][0]['kinds'], [kind])
                self.assertEqual(len(rows['capas']), 1)
                self.assertEqual(rows['capas'][0]['kind'], kind)
                self.assertIn('pago de 5.700', A._capas_md(rows['capas'], self.upd))

    def test_symmetric_rules_keep_the_census_direction(self):
        # Spec 071 AC60 (invertido tras medir): el colapso solo junta pares bidireccionales y deja las simétricas sueltas
        # como las guarda el censo (en qa067b, 487 al revés de la canónica), y el retype del loader casa por dirección.
        # Company:… < Person:…: el censo guarda esta Person→Company, al revés de la canónica, y la regla va igual.
        self._set_edge2(end='Company:innova', rtype='ASSOCIATED_WITH', source_quote='cartas de apoyo')
        for accion, over in (('retype', {'new_type': 'MEMBER_OF'}), ('set_prop', {'prop': 'note', 'value': 'alegación'}),
                             ('invert', {}), ('deny', {})):
            with self.subTest(accion=accion):
                rows = self.rows(self.edge_decision(accion, **over))
                regla = (rows['deny'] or rows['fix'])[0]
                self.assertEqual((regla['source_id'], regla['target_id']), (FULDO, 'Company:innova'))

    def test_a_set_prop_may_carry_several_props_as_one_operation(self):
        # Spec 071, enmienda 11: la regla que corrige a la vez la nota y la página sale de una sola decisión, con un mapa.
        rows = self.rows(self.edge_decision('set_prop', set={'note': 'nota corregida', 'source_page': '29'}))
        self.assertEqual(rows['fix'][0]['set'], {'note': 'nota corregida', 'source_page': 29})
        for over, error in (({'prop': 'note', 'value': 'x', 'set': {'note': 'y'}}, 'set_prop con prop/value y set a la vez'),
                            ({'set': {}}, 'set_prop con un set vacío o que no es un mapa'),
                            ({'set': {'source_quote': 'x'}}, "prop 'source_quote' fuera de la lista cerrada"),
                            ({'set': {'note': ' '}}, 'set_prop sin value'),
                            ({'set': {'amount': '5.700'}}, 'ambiguo')):
            with self.subTest(error=error):
                self.assertRejects(error, self.edge_decision('set_prop', **over))

    def test_a_retype_or_an_invert_may_carry_props_as_one_operation(self):
        # Spec 071 AC71 (decisión del 07-oct): una operación, varias fases. Un retype o un invert con su mapa `set` dan
        # una sola regla que hace las dos cosas, y el mapa pasa los mismos controles que el de un set_prop; sin él, la
        # regla solo retipa o invierte, y ninguna otra acción cambia props.
        retipo = {'new_type': 'ASSOCIATED_WITH'}
        for accion, over, fase in (('retype', retipo, {'corrected_type': 'ASSOCIATED_WITH'}),
                                   ('invert', {}, {'swap': True})):
            with self.subTest(accion=accion):
                rows = self.rows(self.edge_decision(accion, **over, set={'note': 'nota de la decisión'}))
                prepared = relations_loader._prepare_fix(rows['fix'])
                self.assertEqual(len(prepared), 1, rows['fix'])
                self.assertEqual({**{k: prepared[0][k] for k in fase}, 'set': prepared[0]['set']},
                                 {**fase, 'set': {'note': 'nota de la decisión'}})
                self.assertNotIn('set', self.rows(self.edge_decision(accion, **over))['fix'][0])
        for accion, over, error in (('retype', dict(retipo, set={}), 'retype con un set vacío o que no es un mapa'),
                                    ('invert', {'set': {'source_quote': 'x'}}, "prop 'source_quote' fuera de la lista"),
                                    ('retype', dict(retipo, set={'note': ' '}), 'retype sin value'),
                                    ('retype', dict(retipo, set={'amount': '5.700'}), 'ambiguo'),
                                    ('deny', {'set': {'note': 'x'}}, 'deny con set: solo un set_prop')):
            with self.subTest(error=error):
                self.assertRejects(error, self.edge_decision(accion, **over))

    def test_an_ambiguous_amount_is_rejected_not_cast_to_a_hundredth(self):
        # «5.700» vale 5,7 para el casteador del loader: en una auditoría de riesgo legal eso
        # es un error de 100× en un importe, así que la decisión se rechaza.
        errors, _ = self.gate(self.edge_decision('set_prop', prop='amount', value='5.700'))
        self.assertTrue(any('ambiguo' in e for e in errors), errors)
        errors, _ = self.gate(self.edge_decision('set_prop', prop='amount', value='cinco mil'))
        self.assertTrue(any('no es un número' in e for e in errors), errors)

    def test_set_status_becomes_a_set_row(self):
        conn = A.connect(self.root)
        with conn:
            conn.execute('UPDATE units SET value=? WHERE unit_id=?',
                         (json.dumps(edge_value(end='Crime:cohecho', rtype='ACCUSED_OF')), U_EDGE2))
        conn.close()
        rows = self.rows(self.edge_decision('set_status', status='investigado'))
        prepared = relations_loader._prepare_fix(rows['fix'])
        self.assertEqual(prepared[0]['set'], {'status': 'investigado'})

    def test_alias_deny_rows_load_through_the_loader(self):
        d = set_unit(decision(), U_ID, verdict='corregido', label='alias')
        d['alias_deny'] = ['Fuldo']
        rows = self.rows(d)
        prepared = relations_loader._prepare_alias_deny(rows['alias_deny'])
        self.assertEqual(len(prepared), 1)
        self.assertEqual((prepared[0]['id'], prepared[0]['drop']), (FULDO, ['Fuldo']))

    def test_rectificaciones_one_row_per_change(self):
        d = set_unit(decision(), U_SUM, verdict='corregido', label='retirada')
        d = set_unit(d, U_ROLE, verdict='corregido', label='reescrita')
        d['summary'] = {'action': 'remove', 'resumen': '', 'sources': [], 'reason': 'sin fuente'}
        d['role'] = {'action': 'replace', 'role': 'Asesor del ministro (informe UCO, p.76).',
                     'reason': 'se añade cita'}
        rows = self.rows(d)
        by_unit = {r['unidad']: r for r in rows['rectificaciones']}
        self.assertEqual(set(by_unit), {U_SUM, U_ROLE})
        self.assertEqual(by_unit[U_SUM]['decision'], 'suprimido')
        # El registro va a un repo público: no reproduce la frase retirada (revisión Codex 063).
        self.assertEqual(by_unit[U_SUM]['dato'], 'frase del resumen retirada')
        self.assertEqual(by_unit[U_ROLE]['dato'], 'rol corregido')
        self.assertEqual(by_unit[U_ROLE]['decision'], 'rectificado')
        # Sin documento en la unidad ni una `evidence` que solo sea una referencia documental: la decisión y su fecha.
        self.assertEqual(by_unit[U_SUM]['fuente'], f'decisión de la auditoría 063 ({self.upd})')
        self.assertEqual(by_unit[U_SUM]['origen'], 'auditoria-063')
        self.assertEqual(by_unit[U_SUM]['seed_version'], 'pendiente-path-a')

    def test_denied_edge_is_a_suppression_in_the_registry(self):
        rows = self.rows(self.edge_decision('deny'))
        self.assertEqual([r['decision'] for r in rows['rectificaciones']], ['suprimido'])
        self.assertIn('-[PAID]->', rows['rectificaciones'][0]['dato'])
        rows = self.rows(self.edge_decision('invert'))
        self.assertEqual([r['decision'] for r in rows['rectificaciones']], ['rectificado'])

    def test_a_retirada_label_on_a_kept_edge_is_never_a_suppression(self):
        # 071: sin acción deny ninguna regla borra la arista; la etiqueta sola publicaba un «suprimido» falso.
        d = decision(pack_id=P3, units=[unit(U_EDGE2, verdict='corregido', label='retirada')])
        self.assertRejects('las aristas se retiran con label arista + action deny', d)
        conn = A.connect(self.root)
        try:
            pack, errors = A._pack_from_ledger(conn, d['_meta'])
        finally:
            conn.close()
        self.assertEqual(errors, [])
        rows, _ = A._rectificaciones(d, pack, self.upd, frozenset(), self.publicos)   # la firma del 070
        self.assertEqual([r['decision'] for r in rows], ['rectificado'])
        self.assertTrue(rows[0]['dato'].endswith('arista corregida'))

    def test_registry_never_republishes_the_removed_text_or_third_parties(self):
        conn = A.connect(self.root)
        with conn:
            conn.execute('UPDATE units SET value=? WHERE unit_id=?',
                         (json.dumps(edge_value(end='Person:tercera-particular', rtype='PAID',
                                                source_quote='le pagó 106,88 euros'), ensure_ascii=False), U_EDGE2))
        conn.close()
        d = decision(pack_id=P3, units=[unit(U_EDGE2, verdict='corregido', label='arista',
                                             evidence='auto de 20-06-2026 p.11: «le pagó 106,88 euros»')],
                     edges=[edge_op(U_EDGE2, 'deny')])
        row = self.rows(d)['rectificaciones'][0]
        self.assertEqual(row['dato'], f'({FULDO}) -[PAID]-> (Person:[tercero]): arista retirada')
        # El documento de la arista no está en el árbol público (como el informe real, que git ignora): no se nombra.
        self.assertEqual(row['fuente'], f'decisión de la auditoría 063 ({self.upd})')
        for privado in ('106,88', 'tercera-particular', 'informe-uco'):
            self.assertNotIn(privado, json.dumps(row, ensure_ascii=False))

    def test_registry_source_is_structural_never_the_free_evidence(self):
        # Codex [alto], 6ª y 7ª pasadas: `fuente` se extraía del `evidence` libre de la decisión y podía copiar lo
        # retirado. Para toda fila nueva, una referencia acreditada (el documento y la página de la unidad del censo o,
        # si no tiene, la `evidence` si es entera una referencia documental, siempre con el documento en el árbol
        # público); si no, la decisión y su fecha.
        self.publicos |= {'ATS_8611_2025.pdf'}
        neutra = f'decisión de la auditoría 063 ({self.upd})'
        d = set_unit(decision(), U_SUM, verdict='corregido', label='retirada',
                     evidence='el informe de la UCO sobre Fulano p.76')
        d = set_unit(d, U_ROLE, verdict='corregido', label='reescrita', evidence='ATS_8611_2025.pdf')
        d = set_unit(d, U_EDGE, verdict='corregido', label='arista', evidence='dueño 2026-09-25: la cita no lo dice')
        d['summary'] = {'action': 'remove', 'resumen': '', 'sources': [], 'reason': 'sin fuente'}
        d['role'] = {'action': 'replace', 'role': 'Asesor del ministro (ATS 8611/2025).', 'reason': 'se añade cita'}
        d['edges'] = [edge_op(U_EDGE, 'deny')]
        rows = self.rows(d)
        # El documento de la arista (informe-uco.pdf) no está en el árbol público: neutra, y la referencia, descartada.
        self.assertEqual({r['unidad']: r['fuente'] for r in rows['rectificaciones']},
                         {U_SUM: neutra, U_ROLE: 'ATS_8611_2025.pdf', U_EDGE: neutra})
        # La `evidence` y la referencia descartada, enteras, solo en la traza privada, con la decisión y la unidad.
        self.assertEqual([(e['unidad'], e['descartada'], e['evidencia']) for e in rows['evidencias']],
                         [(U_SUM, '', 'el informe de la UCO sobre Fulano p.76'), (U_ROLE, '', 'ATS_8611_2025.pdf'),
                          (U_EDGE, 'informe-uco.pdf p.76', 'dueño 2026-09-25: la cita no lo dice')])
        traza = A._evidencias_md(rows['evidencias'], self.upd)
        self.assertIn(f'- unidad {U_SUM[:8]} ({P1}.json) · fuente pública «{neutra}» · '
                      'evidencia: el informe de la UCO sobre Fulano p.76', traza)
        self.assertIn(f'- unidad {U_EDGE[:8]} ({P1}.json) · fuente pública «{neutra}» · referencia descartada '
                      '«informe-uco.pdf p.76» · evidencia: dueño 2026-09-25: la cita no lo dice', traza)

    def test_fuente_registro(self):
        # Codex [alto], 7ª pasada (AC37): la forma de la referencia no acredita nada; solo se publica con el documento
        # en el árbol público y la página numérica. (fuente pública, referencia descartada).
        neutra, publicos, centinela = f'decisión de la auditoría 063 ({self.upd})', {'ATS_1.pdf', 'ATS_2.pdf'}, \
            'ALIAS_RETIRADO_SINTETICO.pdf p.7'
        for unidad, evidencia, esperado in (
                ({'edge': {'source_doc': 'ATS_1.pdf', 'source_page': 7}}, 'texto libre ATS_2.pdf p.3',
                 ('ATS_1.pdf p.7', '')),
                ({'edge': {'source_doc': ' ATS_1.pdf ', 'source_page': None}}, '', ('ATS_1.pdf', '')),
                ({'edge': {'source_doc': 'ATS_9.pdf', 'source_page': 7}}, 'ATS_2.pdf p.3', (neutra, 'ATS_9.pdf p.7')),
                ({'edge': {'source_doc': 'ATS_1.pdf', 'source_page': '7 bis'}}, '', (neutra, 'ATS_1.pdf p.7 bis')),
                ({'edge': {'source_doc': None}}, 'ATS_2.pdf p.3', ('ATS_2.pdf p.3', '')),
                ({'kind': 'summary', 'edge': None}, ' ATS_2.pdf pp.3-4 ', ('ATS_2.pdf pp.3-4', '')),
                ({'kind': 'summary'}, centinela, (neutra, centinela)),
                ({'kind': 'identity'}, 'ATS_2.pdf p.3 y el alias', (neutra, '')),
                ({'kind': 'identity'}, 'informe sobre Zz p.7', (neutra, '')),
                ({'kind': 'identity'}, 'ATS 2/2025 p.3', (neutra, '')),
                ({'kind': 'identity'}, '[ATS_2.pdf, p.3]', (neutra, '')),
                (None, None, (neutra, ''))):
            with self.subTest(evidencia=evidencia, unidad=unidad):
                self.assertEqual(A._fuente_registro(unidad, evidencia, self.upd, publicos=publicos), esperado)
        self.assertEqual(A._fuente_registro(None, centinela, self.upd, publicos=set()), (neutra, centinela))
        self.assertEqual(A._fuente_registro(None, centinela, self.upd, publicos={centinela.split(' ')[0]}),
                         (centinela, ''))

    def test_registry_hides_addresses_plates_and_phones(self):
        for end, rtype, shown in (('Location:calle-mayor-23', 'OWNS', 'Location:[dato personal]'),
                                  ('Event:1234abc', 'OWNS', 'Event:[dato personal]'),
                                  ('Location:madrid', 'OWNS', 'Location:[bien personal]'),
                                  ('Event:alquiler-de-un-piso', 'PAID', 'Event:[bien personal]'),
                                  ('Location:madrid', 'MET_WITH', 'Location:madrid')):
            conn = A.connect(self.root)
            with conn:
                conn.execute('UPDATE units SET value=? WHERE unit_id=?',
                             (json.dumps(edge_value(end=end, rtype=rtype), ensure_ascii=False), U_EDGE2))
            conn.close()
            d = decision(pack_id=P3, units=[unit(U_EDGE2, verdict='corregido', label='arista')],
                         edges=[edge_op(U_EDGE2, 'deny')])
            row = self.rows(d)['rectificaciones'][0]
            self.assertEqual(row['dato'], f'({FULDO}) -[{rtype}]-> ({shown}): arista retirada')


def full_decision():
    """Una decisión que toca los cinco destinos a la vez."""
    d = decision()
    d = set_unit(d, U_SUM, verdict='corregido', label='retirada')
    d = set_unit(d, U_ROLE, verdict='corregido', label='reescrita')
    d = set_unit(d, U_ID, verdict='corregido', label='alias')
    d = set_unit(d, U_EDGE, verdict='corregido', label='arista')
    d = set_unit(d, U_ED, verdict='corregido', label='reescrita')
    d['summary'] = {'action': 'remove', 'resumen': '', 'sources': [], 'reason': 'prosa sin fuente'}
    d['role'] = {'action': 'replace', 'role': 'Asesor del ministro (informe UCO, p.76).',
                 'reason': 'se añade documento y página'}
    d['alias_deny'] = ['Fuldo']
    d['edges'] = [edge_op(U_EDGE, 'deny')]
    d['editorial'] = [{'unit_id': U_ED, 'action': 'replace', 'text': 'Titular corregido.',
                       'reason': 'el titular no lo sostiene la fuente'}]
    return d


class ApplyTest(EscApplyBase):
    def paths(self):
        return [self.repo / A.SUM_PATH, self.repo / A.ROL_PATH, self.repo / A.REL_PATH,
                self.repo / A.REG_PATH]

    def estado_decision(self):
        d = set_unit(decision(), U_SUM, verdict='corregido', label='estado')
        d['estado'] = {'action': 'set', 'status': 'imputado', 'fecha': '2026-04-11',
                       'doc': 'auto-imputacion.pdf', 'url': '', 'tipo': 'auto',
                       'nota': 'auto de continuación'}
        return d

    def test_prose_rewritten_after_the_census_blocks_the_apply(self):
        # 063 (01-oct): el texto auditado se reescribió a mano después del censo; aplicar la
        # decisión pisaría la corrección posterior con una versión hecha sobre el texto viejo.
        path = self.repo / A.SUM_PATH
        path.write_text(path.read_text().replace('Fuldo Fíctez cobró 5.700 euros.', 'Texto corregido el 29-sep.'))
        before = [sha(p) for p in self.paths()[:3]]
        with self.assertRaises(SystemExit):
            self.apply(full_decision())
        self.assertEqual([sha(p) for p in self.paths()[:3]], before)
        self.assertFalse((self.repo / A.REG_PATH).exists())
        # …salvo que la decisión se haya rehecho a mano sobre el texto vigente y se acepte por nombre.
        d = self.write('E-c0de00000001', full_decision())
        out = self.apply(dict(d, _file='E-c0de00000001.json'), accept_drift=['E-c0de00000001.json'])
        self.assertEqual(out['written']['summaries'], 1)

    def test_a_later_fix_to_another_sentence_of_the_same_property_blocks_the_apply(self):
        # Codex (01-oct, hallazgo 4): la frase auditada sigue intacta pero OTRA frase del mismo
        # resumen se corrigió después del censo; el apply reescribe la propiedad entera y la revertiría.
        path = self.repo / A.SUM_PATH
        path.write_text(path.read_text().replace('Otra frase que nadie audita.', 'Otra frase corregida el 30-sep.'))
        d = set_unit(full_decision(), U_SUM, verdict='corregido', label='reescrita')
        d['summary'] = {'action': 'replace', 'resumen': 'Fuldo Fíctez cobró 5.700 euros (informe UCO, p.76).',
                        'sources': ['https://example.org/uco'], 'reason': 'se añade la fuente'}
        before = [sha(p) for p in self.paths()[:3]]
        self.assertEqual(self.gate(d)[0], [])
        errors = A.drift_check([d], self.repo)
        self.assertEqual(len(errors), 1)
        self.assertIn('summary', errors[0])
        with self.assertRaises(SystemExit):
            self.apply(d)
        self.assertEqual([sha(p) for p in self.paths()[:3]], before)

    def test_whitespace_and_nfc_differences_are_not_drift(self):
        path = self.repo / A.SUM_PATH
        nfd = unicodedata.normalize('NFD', 'Fuldo Fíctez cobró 5.700 euros.')
        path.write_text(path.read_text().replace('Fuldo Fíctez cobró 5.700 euros. Otra',
                                                 nfd + '\n    Otra'))
        self.apply(full_decision())

    def test_a_role_row_without_entity_id_is_checked_beside_the_summary(self):
        # Codex (01-oct, hallazgo 3): el rol sin `entity_id` (el loader lo deriva de type+name) y el
        # resumen con id son la MISMA entidad; antes el rol se indexaba aparte y nunca se comprobaba.
        path = self.repo / A.ROL_PATH
        path.write_text(path.read_text().replace(
            '  name: Fuldo Fíctez\n  entity_id: Person:fuldo-fictez\n  role: Asesor del ministro de Transportes.',
            '  name: Fuldo Fíctez\n  role: Asesor del ministro de Transportes, cesado en 2021.'))
        published = A._published_prose(self.repo)[FULDO]
        self.assertEqual(published['role'], 'Asesor del ministro de Transportes, cesado en 2021.')
        self.assertIn('Otra frase que nadie audita.', published['summary'])
        before = [sha(p) for p in self.paths()[:3]]
        with self.assertRaises(SystemExit):
            self.apply(full_decision())
        self.assertEqual([sha(p) for p in self.paths()[:3]], before)

    def test_published_prose_follows_the_loader_order_and_removals(self):
        (self.repo / 'pipeline/data/context/roles_enriched.yaml').write_text(
            'entities:\n- type: Person\n  name: Fuldo Fíctez\n  role: Rol antiguo.\n'
            '- type: Person\n  name: Otro\n  role: Rol de Otro.\n', encoding='utf-8')
        (self.repo / 'pipeline/data/context/summaries.yaml').write_text(
            'summaries:\n- entity_id: Person:otro\n  resumen: Resumen de Otro.\n', encoding='utf-8')
        path = self.repo / A.SUM_PATH
        path.write_text(path.read_text() + '- entity_id: Person:otro\n  remove: true\n')
        published = A._published_prose(self.repo)
        self.assertEqual(published[FULDO]['role'], 'Asesor del ministro de Transportes.')  # *_qa061 al final
        self.assertEqual(published['Person:otro'], {'role': 'Rol previo.', 'summary': ''})

    def test_apply_writes_the_three_overlays_and_the_registry(self):
        out = self.apply(full_decision())
        self.assertEqual(out['written']['summaries'], 1)
        summaries = yaml.safe_load((self.repo / A.SUM_PATH).read_text())['summaries']
        row = next(r for r in summaries if r['entity_id'] == FULDO)
        self.assertTrue(row['remove'])
        self.assertNotIn('resumen', row)
        self.assertIn(self.tag, row['reason'])
        roles = yaml.safe_load((self.repo / A.ROL_PATH).read_text())
        self.assertEqual(roles['relations'], [])
        role = next(r for r in roles['entities'] if r.get('entity_id') == FULDO)
        self.assertEqual(role['role'], 'Asesor del ministro (informe UCO, p.76).')
        relations = yaml.safe_load((self.repo / A.REL_PATH).read_text())
        self.assertEqual(len(relations['deny']), 2)   # la fila previa + la nueva
        self.assertEqual(relations['deny'][-1]['source_id'], FULDO)
        self.assertEqual(len(relations['alias_deny']), 2)
        self.assertEqual(relations['alias_deny'][-1]['drop'], ['Fuldo'])
        registro = yaml.safe_load((self.repo / A.REG_PATH).read_text())['rectificaciones']
        self.assertEqual([r['id'] for r in registro], [f'R-{self.upd}-0001', f'R-{self.upd}-0002',
                                                       f'R-{self.upd}-0003'])
        self.assertEqual({r['decision'] for r in registro}, {'suprimido', 'rectificado'})
        # El ítem editorial NO entra: el registro documenta lo aplicado, y eso sale a tanda.
        self.assertNotIn(U_ED, [r['unidad'] for r in registro])

    def test_new_rows_still_load_through_the_loader_from_disk(self):
        self.apply(full_decision())
        overlay = yaml.safe_load((self.repo / A.REL_PATH).read_text())
        self.assertEqual(len(relations_loader._prepare_deny(overlay['deny'])), 2)
        self.assertEqual(len(relations_loader._prepare_alias_deny(overlay['alias_deny'])), 2)

    def test_apply_is_idempotent(self):
        self.apply(full_decision())
        before = [sha(p) for p in self.paths()]
        self.apply(full_decision())
        self.assertEqual([sha(p) for p in self.paths()], before)

    def test_check_and_dry_run_write_nothing(self):
        before = [sha(p) for p in self.paths()[:3]]
        self.assertFalse((self.repo / A.REG_PATH).exists())
        out = self.apply(full_decision(), check=True)
        self.assertEqual(out['written'], {})
        self.apply(full_decision(), dry_run=True)
        self.assertEqual([sha(p) for p in self.paths()[:3]], before)
        self.assertFalse((self.repo / A.REG_PATH).exists())
        self.assertFalse((self.root / 'esc/report').exists())

    def test_gate_errors_abort_before_writing(self):
        before = [sha(p) for p in self.paths()[:3]]
        with self.assertRaises(SystemExit):
            self.apply(decision(pack_id='E-noexiste'))
        self.assertEqual([sha(p) for p in self.paths()[:3]], before)

    def test_a_retype_already_made_in_the_accusations_layer_passes(self):
        # Codex, 1ª pasada: la capa YA declara el tipo corregido; lo que recrearía `make accusations` es la arista
        # nueva, no la retipada. Solo bloquea si el cargador volvería a generar el tipo ORIGINAL.
        cases = [([{'name': 'cohecho', 'status': 'condenado'}], True),   # el cargador la genera como condena
                 ([{'name': 'cohecho'}], False),                        # ya corregida a acusación
                 ([{'name': 'prevaricación'}], False)]                  # sin la entrada
        for crimes, blocked in cases:
            with self.subTest(crimes=crimes):
                self.accusation_layer(crimes, rtype='CONVICTED_OF')
                d = decision(pack_id=P3, units=[unit(U_EDGE2, verdict='corregido', label='arista')],
                             edges=[edge_op(U_EDGE2, 'retype', new_type='ACCUSED_OF')])
                self.assertEqual(self.gate(d)[0], [])
                errors = A._accusations_check(A.materialize([d], self.tag, self.upd)['capas'], self.repo)
                self.assertEqual(len(errors), 1 if blocked else 0, errors)
                if blocked:
                    self.assertIn('la arista vive en accusations.yaml', errors[0])

    def test_a_curated_accusation_still_declared_in_its_layer_blocks_the_apply(self):
        # 071 (R-2026-09-24-0103): `make accusations` corre después de `make relations` en el Path A y
        # recrea la arista mientras accusations.yaml la declare; la regla con `kinds` no basta.
        quote = 'Fuldo Fíctez recibió 5.700 euros en efectivo de la trama'
        cases = [('deny', [{'name': 'cohecho'}], True),                            # el slug del delito
                 ('deny', [{'name': 'cohecho pasivo', 'quote': quote}], True),     # o la cita
                 ('invert', [{'name': 'cohecho'}], True),
                 # AC276: el MERGE del cargador es por persona + delito + tipo, no por documento: otro documento la recrea
                 ('deny', [{'name': 'cohecho', 'doc': 'otro-auto.pdf'}], True),
                 ('invert', [{'name': 'cohecho', 'doc': 'otro-auto.pdf'}], True),
                 # el documento solo resuelve un destino que el delito no identifica: otro documento, ni la cita lo ata
                 ('deny', [{'name': 'cohecho pasivo', 'quote': quote, 'doc': 'otro-auto.pdf'}], False),
                 ('deny', [{'name': 'prevaricación'}], False),                    # la capa ya no la declara
                 ('set_status', [{'name': 'cohecho'}], False)]                    # el loader la reescribe igual
        for action, crimes, blocked in cases:
            with self.subTest(action=action, crimes=crimes):
                self.accusation_layer(crimes)
                over = {'status': 'investigado'} if action == 'set_status' else {}
                d = decision(pack_id=P3, units=[unit(U_EDGE2, verdict='corregido', label='arista')],
                             edges=[edge_op(U_EDGE2, action, **over)])
                self.assertEqual(self.gate(d)[0], [])
                errors = A._accusations_check(A.materialize([d], self.tag, self.upd)['capas'], self.repo)
                if blocked:
                    self.assertEqual(len(errors), 1)
                    self.assertIn('la arista vive en accusations.yaml (make accusations la recrea tras make '
                                  'relations): edita la capa en el mismo PR', errors[0])
                    with self.assertRaises(SystemExit):
                        self.apply(d)
                    self.assertFalse((self.repo / A.REG_PATH).exists())
                else:
                    self.assertEqual(errors, [])
                    self.apply(d, check=True)

    def layer_errors(self, action, **over):
        """Los errores de `_accusations_check` para una decisión `action` sobre U_EDGE2."""
        d = decision(pack_id=P3, units=[unit(U_EDGE2, verdict='corregido', label='arista')],
                     edges=[edge_op(U_EDGE2, action, **over)])
        self.assertEqual(self.gate(d)[0], [])
        return A._accusations_check(A.materialize([d], self.tag, self.upd)['capas'], self.repo)

    def test_a_shared_quote_does_not_block_a_retype_already_made(self):
        # Codex, 4ª pasada: cohecho ya es ACCUSED_OF y prevaricación sigue CONVICTED_OF, con la misma cita. Manda
        # el delito: el retype de cohecho pasa y la condena por prevaricación sigue bloqueando su retirada.
        quote = 'Fuldo Fíctez recibió 5.700 euros en efectivo de la trama'
        cohecho = {'name': 'cohecho', 'quote': quote}
        prevaricacion = {'name': 'prevaricación', 'status': 'condenado', 'quote': quote}
        for caso, entradas in (('una entrada', ([cohecho, prevaricacion],)),
                               ('dos entradas', ([cohecho], [prevaricacion]))):
            with self.subTest(caso=caso):
                self.accusation_layer(*entradas, rtype='CONVICTED_OF')
                self.assertEqual(self.layer_errors('retype', new_type='ACCUSED_OF'), [])
                self.accusation_layer(*entradas, rtype='CONVICTED_OF', end='Crime:prevaricacion')
                self.assertEqual(len(self.layer_errors('deny')), 1)

    def test_a_quote_that_matches_two_curated_crimes_does_not_block(self):
        # Sin delito que resuelva el destino, la cita solo decide si señala una sola arista efectiva.
        quote = 'Fuldo Fíctez recibió 5.700 euros en efectivo de la trama'
        self.accusation_layer([{'name': 'cohecho pasivo', 'quote': quote}, {'name': 'cohecho activo', 'quote': quote}])
        self.assertEqual(self.layer_errors('deny'), [])
        self.accusation_layer([{'name': 'cohecho pasivo', 'quote': quote}])
        self.assertEqual(len(self.layer_errors('deny')), 1)

    def test_declared_by_identity_not_by_document(self):
        # AC276 (Codex, 39ª pasada del PR completo): la reproducción de A, con la función real y sin repo. Otra entrada
        # de la misma persona, delito y tipo con OTRO documento recrea la misma arista: está declarada.
        def efectiva(persona='Person:x', delito='Crime:cohecho', tipo='ACCUSED_OF', doc='b.pdf', quote=''):
            return {'person_id': persona, 'crime_id': delito, 'rel_type': tipo, 'doc': doc, 'quote': quote}
        pedida = ('Person:x', 'Crime:cohecho', 'a.pdf', 'ACCUSED_OF', '')
        for nombre, efectivas, esperada in (
                ('otro documento', [efectiva()], True),
                ('el mismo documento', [efectiva(doc='a.pdf')], True),
                ('un documento ausente', [efectiva(doc=None)], True),
                ('otro tipo', [efectiva(tipo='CONVICTED_OF')], False),
                ('otro delito', [efectiva(delito='Crime:prevaricacion')], False),
                ('otra persona', [efectiva(persona='Person:y')], False),
                ('en una de varias entradas', [efectiva(delito='Crime:prevaricacion'), efectiva(doc='c.pdf')], True),
                ('sin efectivas', [], False)):
            with self.subTest(nombre):
                self.assertIs(A._declarada(*pedida, efectivas), esperada)
                # y coincide con la identidad que usan las demás comprobaciones (`_declarada_por_identidad`)
                self.assertIs(A._declarada_por_identidad(
                    {'start': 'Person:x', 'end': 'Crime:cohecho', 'type': 'ACCUSED_OF'}, efectivas), esperada)

    def test_an_unidentified_destination_is_resolved_by_document_and_quote(self):
        # El documento y la cita siguen valiendo cuando ningún delito de la persona identifica el destino.
        cita = 'recibió 5.700 euros en efectivo de la trama'
        efectivas = [{'person_id': 'Person:x', 'crime_id': 'Crime:cohecho-pasivo', 'rel_type': 'ACCUSED_OF',
                      'doc': 'a.pdf', 'quote': 'Fulano ' + cita}]
        self.assertTrue(A._declarada('Person:x', 'Crime:cohecho', 'a.pdf', 'ACCUSED_OF', cita, efectivas))
        self.assertFalse(A._declarada('Person:x', 'Crime:cohecho', 'otro.pdf', 'ACCUSED_OF', cita, efectivas))
        self.assertFalse(A._declarada('Person:x', 'Crime:cohecho', 'a.pdf', 'ACCUSED_OF', 'otra cita', efectivas))
        dos = efectivas + [{**efectivas[0], 'crime_id': 'Crime:cohecho-activo'}]   # la cita señala dos: ninguna
        self.assertFalse(A._declarada('Person:x', 'Crime:cohecho', 'a.pdf', 'ACCUSED_OF', cita, dos))

    def test_the_accusations_check_reproduces_no_value_of_the_layer(self):
        # Codex, 4ª pasada: el rechazo del cargador llevaba el nombre del delito inválido, y su log de la entrada sin
        # persona, la entrada entera con su cita. Ni la salida ni los errores del apply (check y escritura) los llevan.
        sin_persona = {'doc': 'auto-fuldo.pdf', 'crimes': [{'name': 'cohecho', 'quote': 'CITA_PRIVADA_SINTETICA'}]}
        casos = {'delito inválido': [{'person': 'Fuldo Fíctez', 'entity_id': FULDO, 'doc': 'auto-fuldo.pdf',
                                     'crimes': [{'name': 'ALIAS_RETIRADO_SINTETICO'}]}],
                 'entrada sin persona': [sin_persona]}
        d = decision(pack_id=P3, units=[unit(U_EDGE2, verdict='corregido', label='arista')],
                     edges=[edge_op(U_EDGE2, 'deny')])
        for caso, entradas in casos.items():
            with self.subTest(caso=caso):
                self.accusation_layer([{'name': 'cohecho'}])
                (self.repo / 'pipeline/data/accusations/accusations.yaml').write_text(
                    yaml.safe_dump({'accusations': entradas}, allow_unicode=True), encoding='utf-8')
                self.assertEqual(self.gate(d)[0], [])
                out, err, errores = io.StringIO(), io.StringIO(), []
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    errores += A._accusations_check(A.materialize([d], self.tag, self.upd)['capas'], self.repo)
                    for kwargs in ({'check': True}, {}):
                        try:
                            self.apply(d, **kwargs)
                        except SystemExit as exc:
                            errores.append(str(exc.code))
                texto = '\n'.join([out.getvalue(), err.getvalue(), *errores])
                for centinela in ('ALIAS_RETIRADO_SINTETICO', 'CITA_PRIVADA_SINTETICA'):
                    self.assertNotIn(centinela, texto)
                if caso == 'delito inválido':
                    self.assertIn('accusations.yaml: el cargador la rechaza (SystemExit)', texto)

    def test_estado_is_exported_but_accusations_is_untouched(self):
        acc = self.repo / 'pipeline/data/accusations/accusations.yaml'
        before = sha(acc)
        out = self.apply(self.estado_decision())
        self.assertEqual(sha(acc), before)
        block = yaml.safe_load(Path(out['estado_path']).read_text())['accusations'][0]
        self.assertEqual(block['entity_id'], FULDO)
        self.assertEqual(block['estado']['status'], 'imputado')
        self.assertEqual(block['estado']['fuente'], {'doc': 'auto-imputacion.pdf', 'tipo': 'auto'})
        self.assertEqual(block['estado']['revisado'], self.upd)
        registro = yaml.safe_load((self.repo / A.REG_PATH).read_text())['rectificaciones']
        self.assertEqual([r['decision'] for r in registro], ['rectificado'])

    def test_editorial_is_exported_and_never_applied(self):
        out = self.apply(full_decision())
        text = Path(out['editorial_path']).read_text()
        self.assertIn('Titular corregido.', text)
        self.assertIn('No se aplican automáticamente', text)
        for path in self.paths():
            self.assertNotIn('Titular corregido.', path.read_text())

    def test_a_reference_to_a_pdf_outside_the_public_tree_is_never_published(self):
        # Codex [alto], 7ª pasada (AC37): la forma «X.pdf p.N» no acredita nada. Una retirada de resumen (civil) cuya
        # `evidence` nombra un PDF que el árbol no publica: el registro y el dry-run llevan la referencia neutra; la
        # traza privada, la `evidence` y la referencia descartada. Publicado el documento, la referencia sí sale.
        centinela = 'ALIAS_RETIRADO_SINTETICO'
        d = set_unit(decision(), U_SUM, verdict='corregido', label='retirada', evidence=f'{centinela}.pdf p.7')
        d['summary'] = {'action': 'remove', 'resumen': '', 'sources': [], 'reason': 'sin fuente'}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.apply(copy.deepcopy(d), dry_run=True)
            res = self.apply(copy.deepcopy(d))
        self.assertNotIn(centinela, out.getvalue())
        registro = (self.repo / A.REG_PATH).read_text(encoding='utf-8')
        self.assertNotIn(centinela, registro)
        fila, = yaml.safe_load(registro)['rectificaciones']
        self.assertEqual(fila['fuente'], f'decisión de la auditoría 063 ({self.upd})')
        self.assertIn(f'referencia descartada «{centinela}.pdf p.7» · evidencia: {centinela}.pdf p.7',
                      Path(res['evidencias_path']).read_text(encoding='utf-8'))
        self.publicos |= {f'{centinela}.pdf'}
        rows = self.apply(copy.deepcopy(d), dry_run=True)['rows']
        self.assertEqual([r['fuente'] for r in rows['rectificaciones']], [f'{centinela}.pdf p.7'])


U_S_ORG, U_S_SOC, U_S_ID = (f'{c * 64}' for c in 'hij')
PS = 'E-zzseudo00001'
ALIAS_S = ('don Zz Test', 'el consejero Zz Test')


class SeudonimosTest(EscApplyBase):
    """Spec 070 (AC6): sobre un seudónimo judicial la 063 nunca escribe un deny/alias_deny público.
    Retirar = quitar la excepción de la lista de permitidas; conservar = añadirla; el registro neutraliza
    la sociedad/organismo. Ids sintéticos."""

    # AC148 (enmienda 35): las citas de las dos aristas mixtas del pack nombran a su extremo civil y la lista las admite
    # (vía b, `citas_fixture`): conservarlas tiene la prueba de su coaparición.
    CITA_ORG, CITA_SOC = 'Zz Test trabajó para Zz Organismo Test.', 'Zz Sociedad Test pagó a Zz Test.'

    def setUp(self):
        super().setUp()
        self.citas_fixture = [seudonimos.entrada_cita(c, SEUDO, ['ATS_0000_2025.pdf'], 'decision-2026-10-07',
                                                      'coaparición judicial revisada; no identifica')
                              for c in (self.CITA_ORG, self.CITA_SOC)]
        db = sqlite3.connect(self.root / 'census.sqlite')
        node = json.dumps({'labels': ['Person'],
                           'props': {'name': 'Zz Test', 'is_pseudonym': True, 'aliases': list(ALIAS_S)}})
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-s-node', 'node', 'n:s', SEUDO, 'Person', 'unspecified', 0, 'h', node))
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-s-rel', 'relationship', 'r:s', SEUDO, 'WORKS_FOR', 'judicial', 0, 'h', '{}'))

        def edge(start, end, rtype, cita):
            return json.dumps({'start': start, 'end': end, 'type': rtype, 'props': {
                'source_kind': 'judicial', 'source_doc': 'ATS_0000_2025.pdf', 'source_page': 3,
                'source_quote': cita}})

        rows = [(U_S_ORG, 'o-s-rel', 'edge', '/edge@1', edge(SEUDO, ORG, 'WORKS_FOR', self.CITA_ORG)),
                (U_S_SOC, 'o-s-rel', 'edge', '/edge@2', edge(SOC, SEUDO, 'PAID', self.CITA_SOC)),
                (U_S_ID, 'o-s-node', 'identity', '',
                 json.dumps(identity_value(SEUDO, 'Zz Test', ALIAS_S, is_pseudonym=True)))]
        for uid, oid, kind, field, value in rows:
            db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                       ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, oid, kind, field, value))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   (PS, SEUDO, 'entity', 1, 1, json.dumps([U_S_ORG, U_S_SOC, U_S_ID]), 'routed'))
        db.commit()
        db.close()
        nodo_del_censo(self.root, ORG, 'Organization', 'Zz Organismo Test')
        nodo_del_censo(self.root, SOC, 'Company', 'Zz Sociedad Test')
        self.perm = self.repo / seudonimos.PERMITIDOS_REL
        self.perm.write_text(seudonimos.volcar_permitidos({'aristas': [], 'alias': [], 'citas': self.citas_fixture}),
                             encoding='utf-8')

    def gate(self, *decisions):
        """`gate` con las citas admitidas del fixture (AC148), como las lee el apply de la lista del repo."""
        conn = A.connect(self.root)
        try:
            return A.gate(conn, list(decisions), self.root, publicos=self.publicos, citas=self.citas_fixture)
        finally:
            conn.close()

    def seudo(self, edges=(), alias_deny=(), cambios=None):
        """Decisión sobre el pack del seudónimo; `cambios` = {unit_id: (verdict, label)} de lo que se corrige."""
        cambios = cambios or {}
        units = [unit(uid, *cambios.get(uid, ('verificado', 'ok'))) for uid in (U_S_ORG, U_S_SOC, U_S_ID)]
        return decision(pack_id=PS, entity=SEUDO, units=units, edges=list(edges), alias_deny=list(alias_deny))

    def retirar(self, *alias):
        cambios = {U_S_ORG: ('corregido', 'arista'), U_S_ID: ('corregido', 'alias')}
        return self.seudo(edges=[edge_op(U_S_ORG, 'deny')], alias_deny=alias or ('el consejero Zz Test',),
                          cambios=cambios)

    def identidad(self, verdict='verificado', label='ok', alias_deny=()):
        """Decisión que solo cubre la unidad `identity` del seudónimo (cierre parcial: `_meta.unit_ids`)."""
        d = decision(pack_id=PS, entity=SEUDO, units=[unit(U_S_ID, verdict, label)], alias_deny=list(alias_deny))
        d['_meta']['unit_ids'] = [U_S_ID]
        d['_file'] = f'{PS}.identidad.json'
        return d

    def alias_en_censo(self, alias):
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('UPDATE units SET value=? WHERE unit_id=?',
                   (json.dumps(identity_value(SEUDO, 'Zz Test', alias, is_pseudonym=True)), U_S_ID))
        db.commit()
        db.close()

    def lista_alias(self, *alias):
        lista = {'aristas': [], 'alias': [{'id': SEUDO, 'permitidos': list(alias)}], 'citas': self.citas_fixture}
        self.perm.write_text(seudonimos.volcar_permitidos(lista), encoding='utf-8')

    def test_la_fila_de_una_arista_corregida_nombra_el_par_de_su_regla(self):
        """Principio P (fusión 070 ⟷ 071): la «rectificado» de una arista de seudónimo que corrige una regla fix del plan
        (pública, con el par y la huella de su unidad) la nombra entera y la guardia de prosa la deja pasar; sin esa
        regla en el plan, o con la huella de otra unidad, la misma fila es hallazgo."""
        corregir = {U_S_ORG: ('corregido', 'arista'), U_S_ID: ('corregido', 'alias')}
        d = self.seudo(edges=[edge_op(U_S_ORG, 'retype', new_type='MEMBER_OF')], alias_deny=('el consejero Zz Test',),
                       cambios=corregir)
        self.gate(d)   # anota `_pack`
        rows = A.materialize([d], self.tag, self.upd, frozenset({SEUDO}), self.publicos)
        fila = next(r for r in rows['rectificaciones'] if r['unidad'] == U_S_ORG)
        self.assertEqual(fila['dato'], f'({SEUDO}) -[WORKS_FOR]-> ({ORG}): arista corregida')
        self.assertEqual(A._prosa_publica(rows, frozenset({SEUDO}), {}, self.publicos, citas=self.citas_fixture), [])
        otra = [{**r, 'reason': r['reason'].replace(f'[{U_S_ORG[:8]}]', '[00000000]')} for r in rows['fix']]
        # La guardia solo mira el plan (fail-closed): la misma regla ya en el árbol no la ampara si el plan no la trae.
        (self.repo / A.REL_PATH).write_text(yaml.safe_dump({'deny': [], 'fix': rows['fix'], 'alias_deny': []}),
                                            encoding='utf-8')
        for caso, fix in (('sin la regla', []), ('con la huella de otra unidad', otra)):
            with self.subTest(caso):
                errores = A._prosa_publica({**rows, 'fix': fix}, frozenset({SEUDO}), {}, self.publicos,
                                           citas=self.citas_fixture)
                self.assertTrue(any(e.startswith('rectificaciones[') for e in errores), errores)

    def test_la_retirada_de_un_par_que_la_lista_conserva_lo_nombra(self):
        """Principio P con la lista (fusión 070 ⟷ 071): la «suprimido» de una arista de seudónimo cuyo par (con otro
        tipo) sigue en la lista de permitidas resultante lo nombra entero, y la guardia de prosa la deja pasar con esa lista;
        con un par que la lista no conserva, o sin la lista, el marcador y la guardia como siempre."""
        d = self.retirar()
        self.gate(d)   # anota `_pack`
        seudos = frozenset({SEUDO})
        retirada = f'({SEUDO}) -[WORKS_FOR]-> (Organization:[organismo]): arista retirada'
        for otro, dato in ((ORG, f'({SEUDO}) -[WORKS_FOR]-> ({ORG}): arista retirada'), (SOC, retirada)):
            with self.subTest(otro=otro):
                rows = A.materialize([d], self.tag, self.upd, seudos, self.publicos)
                lista = {'aristas': [{'seudonimo': SEUDO, 'otro': otro, 'tipo': 'MEMBER_OF'}]}
                A._nombra_las_de_la_lista(rows, [d], seudos, lista)
                fila = next(r for r in rows['rectificaciones'] if r['unidad'] == U_S_ORG)
                self.assertEqual(fila['dato'], dato)
                errores = A._prosa_publica(rows, seudos, {}, self.publicos, citas=self.citas_fixture, permitidos=lista)
                self.assertFalse([e for e in errores if e.startswith('rectificaciones[')], errores)
        rows = A.materialize([d], self.tag, self.upd, seudos, self.publicos)
        A._nombra_las_de_la_lista(rows, [d], seudos, {'aristas': [{'seudonimo': SEUDO, 'otro': ORG, 'tipo': 'PAID'}]})
        errores = A._prosa_publica(rows, seudos, {}, self.publicos, citas=self.citas_fixture)   # sin la lista
        self.assertTrue(any(e.startswith('rectificaciones[') for e in errores), errores)

    def test_la_lista_que_cuenta_es_la_que_deja_el_plan(self):
        """Principio P con la lista: el plan que retira la única entrada del par deja la fila con marcador; si queda
        otra entrada del mismo par (otro tipo), la nombra."""
        d = self.retirar()
        self.gate(d)   # anota `_pack`
        seudos = frozenset({SEUDO})

        def entrada(tipo):
            return {'seudonimo': SEUDO, 'otro': ORG, 'tipo': tipo, 'kinds': ['judicial'],
                    'origen': 'decision-2026-10-07',
                    'motivo': 'decisión «conservar» de la auditoría (unidad aaaaaaaa)'}
        for tipos, dato in ((['WORKS_FOR'], f'({SEUDO}) -[WORKS_FOR]-> (Organization:[organismo]): arista retirada'),
                            (['WORKS_FOR', 'MEMBER_OF'], f'({SEUDO}) -[WORKS_FOR]-> ({ORG}): arista retirada')):
            with self.subTest(tipos=tipos):
                rows = A.materialize([d], self.tag, self.upd, seudos, self.publicos)
                permitidos = {'aristas': [entrada(t) for t in tipos], 'alias': [], 'citas': self.citas_fixture}
                A._aplicar_seudonimos(permitidos, rows['seudonimos'], self.upd)
                self.assertEqual(sorted(e['tipo'] for e in permitidos['aristas']), tipos[1:])
                A._nombra_las_de_la_lista(rows, [d], seudos, permitidos)
                self.assertEqual(next(r for r in rows['rectificaciones'] if r['unidad'] == U_S_ORG)['dato'], dato)

    def test_retirar_no_escribe_reglas_publicas_y_el_registro_neutraliza(self):
        rel_before = sha(self.repo / A.REL_PATH)
        out = self.apply(self.retirar())
        self.assertEqual(sha(self.repo / A.REL_PATH), rel_before)
        self.assertEqual((out['plan']['deny'], out['plan']['alias_deny']), (0, 0))
        registro = yaml.safe_load((self.repo / A.REG_PATH).read_text())['rectificaciones']
        self.assertEqual([r['dato'] for r in registro],
                         [f'({SEUDO}) -[WORKS_FOR]-> (Organization:[organismo]): arista retirada'])
        self.assertNotIn('zz-organismo-test', (self.repo / A.REG_PATH).read_text())
        # La traza de lo retirado es privada (esc/report, fuera del repo).
        traza = Path(out['seudonimos_path']).read_text()
        self.assertIn(ORG, traza)
        self.assertIn('el consejero Zz Test', traza)
        # Lo que la decisión cubre sin tocar se conserva: la arista con la sociedad y el alias de pila.
        lista = seudonimos.cargar_permitidos(self.perm)
        self.assertEqual([(e['seudonimo'], e['otro'], e['tipo'], e['kinds']) for e in lista['aristas']],
                         [(SEUDO, SOC, 'PAID', ['judicial'])])
        self.assertEqual(lista['alias'], [{'id': SEUDO, 'permitidos': ['don Zz Test']}])
        self.assertEqual(seudonimos.auditar(self.repo, self.publicos), [])

    def test_retirar_quita_la_excepcion_y_conservar_la_mantiene(self):
        semilla = {**SEMILLA, 'kinds': ['judicial']}
        self.perm.write_text(seudonimos.volcar_permitidos(
            {'aristas': [{'seudonimo': SEUDO, 'otro': ORG, 'tipo': 'WORKS_FOR', **semilla},
                         {'seudonimo': SEUDO, 'otro': SOC, 'tipo': 'PAID', **semilla}],
             'alias': [{'id': SEUDO, 'permitidos': ['Sr. Zz Test', 'don Zz Test']}], 'citas': self.citas_fixture}),
            encoding='utf-8')
        out = self.apply(self.retirar('don Zz Test', 'el consejero Zz Test'))
        lista = seudonimos.cargar_permitidos(self.perm)
        self.assertEqual([(e['otro'], e['origen']) for e in lista['aristas']], [(SOC, 'estado-2026-10-07')])
        self.assertEqual(lista['alias'], [{'id': SEUDO, 'permitidos': ['Sr. Zz Test']}])
        self.assertEqual(out['written']['permitidos'], {'aristas_quitadas': 1, 'alias_quitados': 1})
        before = [sha(self.perm), sha(self.repo / A.REG_PATH), sha(self.repo / A.REL_PATH)]
        self.apply(self.retirar('don Zz Test', 'el consejero Zz Test'))
        self.assertEqual([sha(self.perm), sha(self.repo / A.REG_PATH), sha(self.repo / A.REL_PATH)], before)

    def test_conservar_un_alias_que_no_es_el_nombre_de_pila_para_sin_escribir(self):
        before = [sha(self.repo / A.REL_PATH), sha(self.perm)]
        with self.assertRaises(SystemExit):
            self.apply(self.seudo())   # todo «ok»: conservaría el alias con cargo
        self.assertEqual([sha(self.repo / A.REL_PATH), sha(self.perm)], before)
        self.assertFalse((self.repo / A.REG_PATH).exists())

    def test_una_lista_mal_formada_para_el_apply_sin_escribir(self):
        # Codex [medio]: `aliases` (renombrada) no es «ningún alias permitido».
        self.perm.write_text('aristas: []\naliases: [{id: x, permitidos: [y]}]\n', encoding='utf-8')
        before = [sha(self.repo / p) for p in (A.SUM_PATH, A.ROL_PATH, A.REL_PATH)] + [sha(self.perm)]
        with self.assertRaises(SystemExit) as err:
            self.apply(self.retirar())
        self.assertIn('ha de tener exactamente', str(err.exception))
        self.assertEqual([sha(self.repo / p) for p in (A.SUM_PATH, A.ROL_PATH, A.REL_PATH)] + [sha(self.perm)], before)
        self.assertFalse((self.repo / A.REG_PATH).exists())

    def destinos(self):
        """El contenido de los cinco ficheros que escribe el apply (None = no existe)."""
        paths = [self.repo / p for p in (A.SUM_PATH, A.ROL_PATH, A.REL_PATH, A.REG_PATH)] + [self.perm]
        return [p.read_text(encoding='utf-8') if p.exists() else None for p in paths]

    def restos(self):
        """Temporales (su sufijo de ejecución, como `<ejecución>`), copias y marcador que un apply haya dejado."""
        return sorted(re.sub(r'\.\d+-[0-9a-f]+\.tmp$', '.<ejecución>.tmp', str(p.relative_to(self.repo)))
                      for patron in ('*.tmp', '*.bak', '.apply-en-curso.json') for p in self.repo.rglob(patron))

    def test_una_lista_invalida_para_el_check_y_el_apply_sin_escribir(self):
        # Codex [medio]: con una entrada sin `motivo` el check daba OK y el apply escribía overlays y registro antes de
        # que el volcado de la lista fallara: el registro diría «suprimido» y la lista vieja conservaría el permiso.
        # Con `doc` por `docs`, el volcado la habría reescrito sin acotar, en silencio.
        # Una clave desconocida se cuenta, no se imprime: es texto del fichero (aquí, un alias retirado sintético).
        entrada = f"{{seudonimo: '{SEUDO}', otro: '{SOC}', tipo: PAID, kinds: [judicial], origen: {SEMILLA['origen']}"
        centinela, desconocida = 'ALIAS_RETIRADO_SINTETICO', 'aristas[0]: 1 clave(s) desconocida(s); permitidas: '
        semilla = f", motivo: '{SEMILLA['motivo']}'"
        for resto, motivo in (('}', 'aristas[0]: sin `origen` o sin `motivo`'),
                              (f'{semilla}, doc: [a.pdf]}}', desconocida),
                              (f'{semilla}, {centinela}: x}}', desconocida)):
            self.perm.write_text(f'aristas:\n- {entrada}{resto}\nalias: []\n'
                                 + yaml.safe_dump({'citas': self.citas_fixture}, allow_unicode=True), encoding='utf-8')
            before = self.destinos()
            for check in (True, False):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                    self.apply(self.retirar(), check=check)
                self.assertIn(motivo, out.getvalue())
                self.assertNotIn(centinela, out.getvalue())
                self.assertNotIn('OK (check)', out.getvalue())
                self.assertEqual(self.destinos(), before, f'{motivo}, check={check}')
        self.assertFalse((self.root / 'esc/report').exists())

    def test_una_lista_resultante_invalida_para_el_apply_sin_escribir(self):
        # La lista que se va a escribir pasa la misma validación: un fallo del volcado tras escribir el registro lo
        # dejaría desacorde con la lista.
        real = A._aplicar_seudonimos

        def con_entrada_rota(permitidos, *args, **kwargs):
            out = real(permitidos, *args, **kwargs)
            permitidos['aristas'].append({'seudonimo': SEUDO, 'otro': SOC, 'tipo': 'OWNS', 'origen': 'x'})
            return out

        before = self.destinos()
        out = io.StringIO()
        with mock.patch.object(A, '_aplicar_seudonimos', con_entrada_rota), contextlib.redirect_stdout(out), \
                self.assertRaises(SystemExit):
            self.apply(self.retirar())
        self.assertIn('sin `origen` o sin `motivo`', out.getvalue())
        self.assertEqual(self.destinos(), before)

    def test_un_deny_publico_sobre_un_seudonimo_es_un_error_sin_escribir(self):
        text = (self.repo / A.REL_PATH).read_text()
        for filas in ({'deny': [{'source_id': SOC, 'target_id': SEUDO, 'type': 'PAID'}], 'fix': [], 'alias_deny': []},
                      {'deny': [], 'fix': [], 'alias_deny': [{'type': 'Person', 'name': 'Zz Seudonimo Test',
                                                              'drop': ['el consejero Zz Test']}]}):
            with self.assertRaises(ValueError):
                A._write_relations(text, filas, self.tag, frozenset({SEUDO}))
        _, counts = A._write_relations(text, {'deny': [{'source_id': FULDO, 'target_id': SOC, 'type': 'PAID'}],
                                              'fix': [], 'alias_deny': []}, self.tag, frozenset({SEUDO}))
        self.assertEqual(counts['deny'], 1)
        # Aunque algo colara un deny sensible hasta la escritura, el apply no escribe nada.
        real = A.materialize

        def con_deny(*args, **kwargs):
            rows = real(*args, **kwargs)
            rows['deny'].append({'source_id': SEUDO, 'target_id': ORG, 'type': 'WORKS_FOR', 'doc': None,
                                 'quote_contains': None, 'reason': 'x'})
            return rows

        before = [sha(self.repo / p) for p in (A.SUM_PATH, A.ROL_PATH, A.REL_PATH)] + [sha(self.perm)]
        with mock.patch.object(A, 'materialize', con_deny), self.assertRaises(ValueError):
            self.apply(self.retirar())
        self.assertEqual([sha(self.repo / p) for p in (A.SUM_PATH, A.ROL_PATH, A.REL_PATH)] + [sha(self.perm)], before)
        self.assertFalse((self.repo / A.REG_PATH).exists())

    # Codex [medio], 3ª pasada: el censo guarda UNA unidad `identity` por entidad con el bloque entero
    # ({'identity': {'aliases': […]}}), nunca un alias suelto como texto. Leída como texto, verificar la identidad no
    # conservaba nada (el loader borraba los alias en silencio) y retirarla sin `alias_deny` dejaba un «alias
    # retirado» falso en el registro.

    def test_verificar_la_identidad_conserva_sus_alias(self):
        self.alias_en_censo(['Sr. Zz Test', 'don Zz Test'])
        self.lista_alias('Sr. Zz Test')
        out = self.apply(self.identidad())
        self.assertEqual([(a['accion'], a['alias'], a['unidad']) for a in out['rows']['seudonimos']],
                         [('conservar', 'Sr. Zz Test', U_S_ID), ('conservar', 'don Zz Test', U_S_ID)])
        self.assertEqual(seudonimos.cargar_permitidos(self.perm)['alias'],
                         [{'id': SEUDO, 'permitidos': ['Sr. Zz Test', 'don Zz Test']}])
        self.assertEqual(out['written']['permitidos'], {'alias_añadidos': 1})
        # Un nodo sin alias: la clave falta en el censo y no hay nada que conservar.
        self.alias_en_censo(None)
        self.assertEqual(self.apply(self.identidad())['rows']['seudonimos'], [])

    def test_una_identidad_retirada_sin_alias_deny_para_sin_escribir(self):
        self.alias_en_censo(['don Zz Test'])
        self.lista_alias('don Zz Test')
        error = (f'{PS}.identidad.json: identidad del seudónimo {SEUDO} retirada/corregida sin `alias_deny`: '
                 'indica qué alias se retiran')
        for label in ('retirada', 'reescrita'):
            with self.subTest(label=label):
                conn = A.connect(self.root)
                try:
                    self.assertEqual(A.gate(conn, [self.identidad('corregido', label)], self.root,
                                            frozenset({SEUDO}))[0],
                                     [f'{PS}.identidad.json: unidad jjjjjjjj: las identidades se retiran con label alias '
                                      '+ alias_deny', error])
                finally:
                    conn.close()
                before = self.destinos()
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                    self.apply(self.identidad('corregido', label))
                self.assertIn(error, out.getvalue())
                self.assertEqual(self.destinos(), before)
        self.assertFalse((self.root / 'esc/report').exists())

    # Codex [medio], 4ª pasada: un `alias_deny` con una errata pasaba la puerta, retiraba una cadena que no existe y
    # conservaba el alias. Cada entrada ha de ser, salvo espacios en los bordes, un alias de una identidad que la
    # decisión cubre (mayúsculas y espacios interiores no se normalizan), y se retira tal como está en el censo.

    def test_alias_deny_con_espacios_en_los_bordes_retira_el_alias_del_censo(self):
        self.alias_en_censo(['don Zz Test'])
        self.lista_alias('don Zz Test')
        out = self.apply(self.identidad('corregido', 'alias', alias_deny=[' don Zz Test ']))
        self.assertEqual([(a['accion'], a['alias'], a['unidad']) for a in out['rows']['seudonimos']],
                         [('retirar', 'don Zz Test', U_S_ID)])
        self.assertEqual(seudonimos.cargar_permitidos(self.perm)['alias'], [])
        self.assertEqual(out['written']['permitidos'], {'alias_quitados': 1})

    def test_un_alias_deny_que_no_es_alias_de_una_identidad_cubierta_para_sin_escribir(self):
        self.alias_en_censo(['don Zz Test'])
        self.lista_alias('don Zz Test')
        aristas = decision(pack_id=PS, entity=SEUDO, units=[unit(U_S_ORG), unit(U_S_SOC)], alias_deny=['don Zz Test'])
        aristas['_meta']['unit_ids'] = [U_S_ORG, U_S_SOC]
        aristas['_file'] = f'{PS}.aristas.json'
        casos = {'mayúsculas': self.identidad('corregido', 'alias', alias_deny=['DON ZZ TEST']),
                 'espacios interiores': self.identidad('corregido', 'alias', alias_deny=['don  Zz Test']),
                 'sin cubrir la identidad': aristas}   # una retirada que otra decisión conservaría
        for caso, d in casos.items():
            with self.subTest(caso):
                error = (f'{d["_file"]}: una entrada de `alias_deny` no es alias de ninguna identidad cubierta '
                         f'de {SEUDO}')
                conn = A.connect(self.root)
                try:
                    self.assertEqual(A.gate(conn, [copy.deepcopy(d)], self.root, frozenset({SEUDO}),
                                            citas=self.citas_fixture)[0], [error])
                finally:
                    conn.close()
                before = self.destinos()
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                    self.apply(copy.deepcopy(d))
                self.assertIn(error, out.getvalue())
                for entrada in ('Zz Test', 'ZZ TEST'):
                    self.assertNotIn(entrada, out.getvalue())
                self.assertEqual(self.destinos(), before)

    def test_suprimidos_conservados_mira_los_alias(self):
        # Defensa en profundidad: con `alias_deny` exacto y una identidad por entidad ninguna tanda llega a esto por la
        # puerta, así que se llama directamente.
        seudos, vacia = frozenset({SEUDO}), {'aristas': [], 'alias': []}
        retirar = A._accion_seudonimo('retirar', SEUDO, 'D.json', U_S_ID, alias='don Zz Test')
        fila = {'entidad': SEUDO, 'unidad': U_S_ID, 'dato': 'alias retirado', 'decision': 'suprimido'}
        # La retirada se cumple y respalda la fila «alias retirado» de su identidad.
        self.assertEqual(A._suprimidos_conservados([fila], [retirar], vacia, seudos), [])
        # El alias sigue permitido: la retirada no se cumpliría.
        lista = {'aristas': [], 'alias': [{'id': SEUDO, 'permitidos': ['don Zz Test']}]}
        self.assertEqual(A._suprimidos_conservados([fila], [retirar], lista, seudos),
                         [f'D.json: unidad {U_S_ID[:8]}: un alias de `alias_deny` sigue en la lista de permitidas '
                          '(decisión incoherente, spec 070)'])
        # «alias retirado» sin una retirada de ESA identidad (la de otra unidad no la respalda).
        errores = A._suprimidos_conservados([fila], [dict(retirar, unidad='k' * 64)], vacia, seudos)
        self.assertEqual(errores, [f'{SEUDO}: unidad {U_S_ID[:8]}: el registro diría «alias retirado» sin que '
                                   '`alias_deny` retire un alias de esa identidad (decisión incoherente, spec 070)'])
        self.assertNotIn('Zz Test', ' '.join(errores))
        # De una persona civil el alias_deny es público: aquí no se mira.
        self.assertEqual(A._suprimidos_conservados([dict(fila, entidad=FULDO)], [], vacia, seudos), [])

    def test_sin_espacio_al_escribir_ningun_destino_cambia(self):
        # Codex [alto], 4ª pasada: con ENOSPC al escribir la lista (el quinto destino), los cuatro anteriores ya estaban
        # escritos y el registro decía «suprimido» con la lista vieja. Todo va antes a temporales.
        real = Path.write_text

        def sin_espacio(path, *args, **kwargs):
            if path.name.startswith(seudonimos.PERMITIDOS_NOMBRE):   # la lista o su temporal
                raise OSError(errno.ENOSPC, 'No space left on device', str(path))
            return real(path, *args, **kwargs)

        before = self.destinos()
        with mock.patch.object(Path, 'write_text', sin_espacio), self.assertRaises(OSError) as err:
            self.apply(self.retirar())
        self.assertEqual(err.exception.errno, errno.ENOSPC)
        self.assertEqual(self.destinos(), before)
        self.assertEqual(self.restos(), [])

    def test_un_replace_que_falla_deshace_los_ya_hechos(self):
        # Codex [alto], 5ª pasada: con EIO en el quinto `os.replace` los cuatro anteriores quedaban aplicados (el
        # registro decía «suprimido» con la lista vieja), con un `.tmp` huérfano, y un `check` posterior daba OK.
        real = os.replace
        for n in (1, 3, 5):
            with self.subTest(replace=n):
                llamadas = []

                def falla(src, dst, *args, n=n, **kwargs):
                    llamadas.append(dst)
                    if len(llamadas) == n:
                        raise OSError(errno.EIO, 'Input/output error', str(dst))
                    return real(src, dst, *args, **kwargs)

                before = self.destinos()
                with mock.patch.object(A.os, 'replace', falla), self.assertRaises(OSError) as err:
                    self.apply(self.retirar())
                self.assertEqual(err.exception.errno, errno.EIO)
                self.assertEqual(self.destinos(), before)
                self.assertEqual(self.restos(), [])
        self.apply(self.retirar())   # sin fallo: escribe los cinco y no deja nada
        self.assertNotEqual(self.destinos(), before)
        self.assertEqual(self.restos(), [])

    def tanda_de_cinco(self):
        """Una tanda que cambia los cinco destinos: la decisión civil completa y la retirada del seudónimo."""
        return full_decision(), self.retirar()

    def test_una_interrupcion_tras_un_renombrado_se_deshace_entera(self):
        # Codex [alto], 6ª pasada: la reversión solo deshacía lo anotado en `hechos`, y entre `os.replace` y el `append`
        # había una ventana: un KeyboardInterrupt ahí dejaba el registro nuevo con la lista vieja y borraba copias y
        # marcador. Ahora todo destino vuelve a su copia, se haya llegado a renombrar o no.
        real = os.replace
        for n in (1, 4, 5):
            with self.subTest(renombrado=n):
                hechos = []

                def corta(src, dst, *args, n=n, **kwargs):
                    real(src, dst, *args, **kwargs)
                    if str(src).endswith('.tmp'):
                        hechos.append(dst)
                        if len(hechos) == n:
                            raise KeyboardInterrupt   # ya renombrado, antes de que nadie lo anote

                before = self.destinos()
                with mock.patch.object(A.os, 'replace', corta), self.assertRaises(KeyboardInterrupt):
                    self.apply(*self.tanda_de_cinco())
                self.assertEqual(self.destinos(), before)
                self.assertEqual(self.restos(), [])
                self.assertIsNone(A._apply_interrumpido(self.repo))

    def test_si_no_se_puede_deshacer_quedan_las_copias_y_el_marcador(self):
        # La reversión sigue con las demás copias aunque una falle y verifica cada destino frente a la suya (tamaño y
        # sha256): si no vuelve, esa copia y el marcador se quedan, el error dice qué restaurar, nada arranca hasta
        # hacerlo, y hacerlo deja todo como estaba.
        real, rol = os.replace, self.repo / A.ROL_PATH
        hechos = []

        def falla(src, dst, *args, **kwargs):
            if str(src).endswith('.tmp'):
                real(src, dst, *args, **kwargs)
                hechos.append(dst)
                if len(hechos) == 4:   # el registro, nuevo, ya renombrado
                    raise OSError(errno.EIO, 'Input/output error', str(dst))
            elif Path(dst) == rol:   # la copia del rol no vuelve
                raise OSError(errno.EIO, 'Input/output error', str(dst))
            else:
                real(src, dst, *args, **kwargs)

        before = self.destinos()
        with mock.patch.object(A.os, 'replace', falla), self.assertRaises(RuntimeError) as err:
            self.apply(*self.tanda_de_cinco())
        aviso = A._apply_interrumpido(self.repo)
        self.assertEqual(str(err.exception), f'{aviso} (no se pudo deshacer)')
        self.assertEqual(aviso.split('): ', 1)[1],
                         'recuperación incompleta; no coinciden con su huella de antes del apply: '
                         f'{A.ROL_PATH} (queda su copia). Restaura cada destino desde su copia (`mv <destino>.bak '
                         '<destino>`) o, si falta, desde su versión anterior, borra los que no existían y repite el '
                         f'`check`: el marcador ({A.MARCADOR_REL}) se queda hasta que el estado coincida con el de '
                         'antes')
        # Restaurados el resumen, las relaciones (tras el fallo del rol) y la lista; el registro, que no existía, fuera.
        self.assertEqual([d == b for d, b in zip(self.destinos(), before)], [True, False, True, True, True])
        self.assertEqual(self.restos(), sorted([f'{A.ROL_PATH}.bak', A.MARCADOR_REL,
                                                f'{seudonimos.PERMITIDOS_REL}.<ejecución>.tmp']))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            self.apply(*self.tanda_de_cinco(), check=True)
        self.assertIn(aviso, out.getvalue())
        os.replace(f'{rol}.bak', rol)   # lo que dice el aviso: ya coincide con su huella, y el aviso lo reconoce
        self.assertIn('el estado es coherente: retira el marcador', A._apply_interrumpido(self.repo))
        for resto in (self.repo / A.MARCADOR_REL, *self.perm.parent.glob(f'{self.perm.name}.*.tmp')):
            resto.unlink()
        self.assertEqual((self.destinos(), self.restos()), (before, []))

    def test_una_copia_desaparecida_no_es_una_restauracion(self):
        # Codex [medio], 7ª pasada (AC39): sin copias `.bak` ni destinos nuevos, el aviso decía «los destinos ya son
        # coherentes» e invitaba a retirar el marcador. Aquí, en el segundo renombrado desaparece la copia de `a` y
        # falla la E/S: `a` queda nuevo, `b` antiguo. El marcador guarda la huella de cada destino antes del primer
        # renombrado y el aviso compara con ella: recuperación incompleta, sin invitar a retirar el marcador, y `check`
        # se niega.
        repo, real, llamadas = self.base / 'mini', os.replace, []
        a, b, nuevo = (repo / 'pipeline/data/rectificaciones' / nombre for nombre in ('a.yaml', 'b.yaml', 'nuevo.yaml'))
        a.parent.mkdir(parents=True)
        a.write_text('old-one', encoding='utf-8')
        b.write_text('old-two', encoding='utf-8')

        def corta(src, dst, *args, **kwargs):
            llamadas.append(dst)
            if len(llamadas) == 2:
                Path(f'{a}.bak').unlink()
                raise OSError(errno.EIO, 'Input/output error', str(dst))
            return real(src, dst, *args, **kwargs)

        with mock.patch.object(A.os, 'replace', corta), self.assertRaises(RuntimeError) as err:
            A._escribir_todo([(a, 'new-one'), (b, 'new-two'), (nuevo, 'new-three')], repo)
        self.assertEqual((a.read_text(), b.read_text(), nuevo.exists()), ('new-one', 'old-two', False))
        aviso = A._apply_interrumpido(repo)
        self.assertEqual(str(err.exception), f'{aviso} (no se pudo deshacer)')
        self.assertIn('recuperación incompleta; no coinciden con su huella de antes del apply: '
                      'pipeline/data/rectificaciones/a.yaml (falta su copia)', aviso)
        for invitacion in ('coherente', 'retira el marcador', 'borra los `.tmp` que queden y el marcador'):
            self.assertNotIn(invitacion, aviso)
        for comando in ('check', 'apply'):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(A.main(['--root', str(self.root), '--repo', str(repo), comando]), 1)
            self.assertIn(aviso, out.getvalue())
        # Restaurado `a` desde su versión anterior, cada destino coincide con su huella y no queda ninguno nuevo:
        # coherente, y se puede retirar el marcador (mientras siga ahí, `check` se sigue negando).
        a.write_text('old-one', encoding='utf-8')
        aviso = A._apply_interrumpido(repo)
        self.assertIn('el estado es coherente: retira el marcador', aviso)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(A.main(['--root', str(self.root), '--repo', str(repo), 'check']), 1)
        self.assertIn(aviso, out.getvalue())

    def test_ctrl_c_durante_los_renombrados_termina_la_escritura(self):
        # Codex [alto], 6ª pasada: un Ctrl-C entre dos renombrados cortaba el apply a medias. Mientras se escribe solo se
        # anota, y se relanza al final con los cinco destinos escritos, sin copias ni marcador.
        real, hechos = os.replace, []

        def con_sigint(src, dst, *args, **kwargs):
            if str(src).endswith('.tmp'):
                hechos.append(dst)
                if len(hechos) == 2:
                    os.kill(os.getpid(), signal.SIGINT)
            return real(src, dst, *args, **kwargs)

        manejador, before = signal.getsignal(signal.SIGINT), self.destinos()
        with mock.patch.object(A.os, 'replace', con_sigint), self.assertRaises(KeyboardInterrupt):
            self.apply(*self.tanda_de_cinco())
        self.assertIs(signal.getsignal(signal.SIGINT), manejador)
        self.assertEqual(len(hechos), 5)
        despues = self.destinos()
        self.assertEqual([d != b for d, b in zip(despues, before)], [True] * 5)   # los cinco, escritos
        self.assertEqual(self.restos(), [])
        self.assertIsNone(A._apply_interrumpido(self.repo))
        self.apply(*self.tanda_de_cinco())   # estaba escrita entera: repetirla no cambia nada
        self.assertEqual(self.destinos(), despues)

    def test_dos_apply_a_la_vez_el_segundo_no_toca_nada(self):
        # Codex [alto], 8ª pasada (AC41): nada impedía dos apply a la vez y compartían temporales, copias y marcador:
        # la limpieza de uno borraba los recursos del otro y quedaban destinos mezclados sin marcador. `check`, `apply`
        # y `record` toman un bloqueo exclusivo del repo antes de mirar el marcador; tomado, exit 2 sin tocar nada.
        # Aquí el segundo llega en el primer renombrado del primero: desde otro hilo (`apply`) y desde otro proceso
        # (`check`).
        real, segundos = os.replace, []

        def a_mitad(src, dst, *args, **kwargs):
            if str(src).endswith('.tmp') and not segundos:
                salida_hilo = {}

                def segundo():
                    try:
                        A.apply(self.root, self.repo, [self.retirar()], self.tag, self.upd, publicos=self.publicos)
                    except SystemExit as err:
                        salida_hilo['code'] = err.code

                hilo = threading.Thread(target=segundo)
                hilo.start()
                hilo.join()
                proceso = subprocess.run([sys.executable, '-m', 'src.qa_esc_apply', '--root', str(self.root),
                                          '--repo', str(self.repo), 'check'], cwd=REPO / 'pipeline',
                                         capture_output=True, text=True)
                segundos.append((salida_hilo.get('code'), proceso.returncode, proceso.stdout + proceso.stderr))
            return real(src, dst, *args, **kwargs)

        before = self.destinos()
        with mock.patch.object(A.os, 'replace', a_mitad), contextlib.redirect_stdout(io.StringIO()):
            self.apply(*self.tanda_de_cinco())
        (hilo, proceso, salida), = segundos
        self.assertEqual((hilo, proceso), (2, 2))
        self.assertIn('otro apply en curso', salida)
        despues = self.destinos()
        self.assertEqual([d != b for d, b in zip(despues, before)], [True] * 5)   # el primero, entero
        self.assertEqual(self.restos(), [])
        self.assertIsNone(A._apply_interrumpido(self.repo))
        self.apply(*self.tanda_de_cinco())   # y coherente: repetirlo no cambia nada
        self.assertEqual(self.destinos(), despues)

    def test_la_limpieza_de_una_ejecucion_no_borra_temporales_ajenos(self):
        # AC41: los temporales llevan el sufijo de su ejecución (`.<pid>-<token>.tmp`): la limpieza de una ejecución que
        # falla, al escribir o al deshacer, solo borra los suyos.
        resumen = self.repo / A.SUM_PATH
        ajenos = [resumen.with_name(resumen.name + sufijo) for sufijo in ('.tmp', '.99999-ajeno.tmp')]
        real_write, real_replace = Path.write_text, os.replace

        def sin_espacio(path, *args, **kwargs):
            if path.name.startswith(seudonimos.PERMITIDOS_NOMBRE):
                raise OSError(errno.ENOSPC, 'No space left on device', str(path))
            return real_write(path, *args, **kwargs)

        def falla_el_tercero(src, dst, *args, **kwargs):
            if str(src).endswith('.tmp') and Path(dst) == self.repo / A.REL_PATH:
                raise OSError(errno.EIO, 'Input/output error', str(dst))
            return real_replace(src, dst, *args, **kwargs)

        for fallo, parche in (('escritura', mock.patch.object(Path, 'write_text', sin_espacio)),
                              ('renombrado', mock.patch.object(A.os, 'replace', falla_el_tercero))):
            with self.subTest(fallo):
                for ajeno in ajenos:
                    ajeno.write_text('AJENO', encoding='utf-8')
                before = self.destinos()
                with parche, self.assertRaises(OSError):
                    self.apply(*self.tanda_de_cinco())
                self.assertEqual(self.destinos(), before)
                self.assertEqual([ajeno.read_text(encoding='utf-8') for ajeno in ajenos], ['AJENO', 'AJENO'])
                self.assertEqual(self.restos(), sorted(str(a.relative_to(self.repo)) for a in ajenos))

    def test_un_marcador_fuera_de_esquema_no_declara_nada_coherente(self):
        # Codex [medio], 8ª pasada (AC43): un marcador con `destinos` pero sin huellas ni nuevos se daba por coherente
        # sin comprobar un solo fichero. Solo uno con el esquema completo puede: destinos relativos y sin repetir,
        # huellas y nuevos como partición exacta de ellos, y cada huella [entero ≥ 0, 64 hex].
        marcador = self.repo / A.MARCADOR_REL
        marcador.parent.mkdir(parents=True, exist_ok=True)
        huella = list(A._huella(self.repo / A.SUM_PATH))
        absoluta = str(self.repo / A.SUM_PATH)
        base = {'fecha': '2026-10-07T07:00:00+00:00', 'destinos': [A.SUM_PATH, A.REG_PATH],
                'huellas': {A.SUM_PATH: huella}, 'nuevos': [A.REG_PATH]}
        casos = {'sin huellas ni nuevos': {**base, 'huellas': {}, 'nuevos': []},
                 'sin destinos': {**base, 'destinos': [], 'huellas': {}, 'nuevos': []},
                 'un destino sin huella ni nuevo': {**base, 'nuevos': []},
                 'en huellas y en nuevos': {**base, 'nuevos': [A.REG_PATH, A.SUM_PATH]},
                 'destino repetido': {**base, 'destinos': [A.SUM_PATH, A.SUM_PATH, A.REG_PATH]},
                 'ruta absoluta': {**base, 'destinos': [absoluta, A.REG_PATH], 'huellas': {absoluta: huella}},
                 'ruta con ..': {**base, 'destinos': ['../fuera.yaml', A.REG_PATH],
                                 'huellas': {'../fuera.yaml': huella}},
                 'tamaño negativo': {**base, 'huellas': {A.SUM_PATH: [-1, huella[1]]}},
                 'tamaño booleano': {**base, 'huellas': {A.SUM_PATH: [True, huella[1]]}},
                 'sha256 no hexadecimal': {**base, 'huellas': {A.SUM_PATH: [huella[0], 'z' * 64]}}}
        incompleta = ('apply interrumpido (2026-10-07T07:00:00+00:00): recuperación incompleta: el marcador '
                      f'({A.MARCADOR_REL}) no deja comprobar los destinos; compruébalos y restáuralos a mano (sus '
                      '`.bak` o su versión anterior en git)')
        for caso, estado in casos.items():
            with self.subTest(caso):
                marcador.write_text(json.dumps(estado), encoding='utf-8')
                self.assertEqual(A._apply_interrumpido(self.repo), incompleta)
        marcador.write_text(json.dumps(base), encoding='utf-8')   # el esquema completo, como hasta ahora
        self.assertIn('el estado es coherente: retira el marcador', A._apply_interrumpido(self.repo))

    def test_el_estado_de_un_seudonimo_no_cita_un_documento_no_publico(self):
        # Codex [alto], 8ª pasada (AC42): el dry-run vaciaba la URL de la fuente del estado de un seudónimo pero
        # imprimía su documento, y el bloque acaba pegado en accusations.yaml, que es público. Con un documento que el
        # árbol no publica, la decisión no pasa el gate (con su huella, nunca el nombre); publicado, igual que antes.
        centinela = 'ALIAS_PRIVADO_SINTETICO'
        privado = f'{centinela}.pdf'
        self.alias_en_censo(['don Zz Test'])
        self.lista_alias('don Zz Test')
        d = self.identidad()
        d['estado'] = {'action': 'set', 'status': 'investigado', 'fecha': '2025-04-01', 'doc': privado, 'url': '',
                       'tipo': 'auto', 'nota': 'auto que lo cita como investigado'}
        error = f'fuente no pública en estado de seudónimo ({d["_file"]}, doc {seudonimos.sha16(privado)})'
        conn = A.connect(self.root)
        try:
            self.assertEqual(A.gate(conn, [copy.deepcopy(d)], self.root, frozenset({SEUDO}), frozenset())[0], [error])
        finally:
            conn.close()
        before, out, err = self.destinos(), io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.apply(copy.deepcopy(d), dry_run=True, publicos=frozenset())
        self.assertIn(error, out.getvalue())
        self.assertNotIn(centinela, out.getvalue() + err.getvalue())
        self.assertEqual(self.destinos(), before)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.apply(copy.deepcopy(d), dry_run=True, publicos=self.publicos | {privado})
        self.assertIn(f'"doc": "{privado}"', out.getvalue())   # publicado, el documento se nombra

    def test_el_dry_run_no_nombra_un_documento_no_publico_en_ningun_estado(self):
        # AC42: el gate solo para el estado de un seudónimo, pero el dry-run de cualquier estado (aquí, de un civil) no
        # nombra un documento que el árbol no publica: «documento no público (doc <sha16>)».
        centinela = 'ALIAS_PRIVADO_SINTETICO'
        privado = f'{centinela}.pdf'
        d = set_unit(decision(), U_SUM, verdict='corregido', label='estado')
        d['estado'] = {'action': 'set', 'status': 'imputado', 'fecha': '2026-04-11', 'doc': privado, 'url': '',
                       'tipo': 'auto', 'nota': ''}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            plan = self.apply(copy.deepcopy(d), dry_run=True)
        self.assertNotIn(centinela, out.getvalue() + err.getvalue())
        self.assertIn(f'"doc": "documento no público (doc {seudonimos.sha16(privado)})"', out.getvalue())
        self.assertEqual(plan['rows']['estado'][0]['estado']['fuente']['doc'], privado)   # el informe privado, entero

    def test_conservar_un_alias_con_algo_mas_que_el_nombre_es_un_error(self):
        # AC44: `_aplicar_seudonimos` hereda la validación de la cadena completa: un alias con una fecha o en otro
        # alfabeto no se conserva.
        permitidos = {'aristas': [], 'alias': []}
        acciones = [A._accion_seudonimo('conservar', SEUDO, 'D.json', U_S_ID, alias=alias)
                    for alias in ('Don Zz Test 01/02/1978', 'Zz Test 王某')]
        _, errores = A._aplicar_seudonimos(permitidos, acciones, self.upd)
        self.assertEqual(errores, [f'D.json: unidad {U_S_ID[:8]}: se conserva un alias de seudónimo que no es su '
                                   'nombre de pila; denegarlo (alias_deny) o decidir a mano (spec 070)'] * 2)
        self.assertEqual(permitidos['alias'], [])

    def test_un_apply_interrumpido_bloquea_check_apply_y_record(self):
        # El marcador sigue ahí si el proceso cayó a mitad (SIGKILL, corte de luz): nada arranca hasta restaurar a mano,
        # y el aviso dice qué restaurar según lo que haya en disco (Codex [alto], 6ª pasada).
        marcador_rel = 'pipeline/data/rectificaciones/.apply-en-curso.json'
        marcador = self.repo / marcador_rel
        marcador.parent.mkdir(parents=True, exist_ok=True)
        resumen = self.repo / A.SUM_PATH
        marcador.write_text(json.dumps({'fecha': '2026-10-07T07:00:00+00:00', 'destinos': [A.SUM_PATH, A.REG_PATH],
                                        'nuevos': [A.REG_PATH], 'huellas': {A.SUM_PATH: list(A._huella(resumen))}}),
                            encoding='utf-8')
        aviso = 'apply interrumpido (2026-10-07T07:00:00+00:00)'
        coherente = (f'{aviso}: los destinos coinciden con su huella de antes del apply y no queda ninguno nuevo; el '
                     f'estado es coherente: retira el marcador ({marcador_rel}) y los `.tmp` y `.bak` que queden, y '
                     'repite')
        incompleta = (f'{aviso}: recuperación incompleta; no coinciden con su huella de antes del apply: {A.SUM_PATH} '
                      f'(queda su copia); no existían antes del apply: {A.REG_PATH}. Restaura cada destino desde su '
                      'copia (`mv <destino>.bak <destino>`) o, si falta, desde su versión anterior, borra los que no '
                      f'existían y repite el `check`: el marcador ({marcador_rel}) se queda hasta que el estado '
                      'coincida con el de antes')
        self.write(PS, self.retirar())
        for caso in ('coherente', 'incompleta'):
            if caso == 'incompleta':   # el resumen a medio escribir, con su copia, y el registro nuevo
                shutil.copy2(resumen, self.repo / f'{A.SUM_PATH}.bak')
                resumen.write_text(resumen.read_text(encoding='utf-8') + '# a medias\n', encoding='utf-8')
                (self.repo / A.REG_PATH).write_text(A.REGISTRO_HEADER, encoding='utf-8')
            aviso = coherente if caso == 'coherente' else incompleta
            self.assertEqual(A._apply_interrumpido(self.repo), aviso)
            before = self.destinos()
            for modo in ('check', 'dry_run', 'apply'):
                with self.subTest(caso, modo=modo):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as err:
                        self.apply(self.retirar(), **({modo: True} if modo != 'apply' else {}))
                    self.assertEqual(err.exception.code, 1)
                    self.assertIn(aviso, out.getvalue())
            for comando in ('check', 'record'):
                with self.subTest(caso, comando=comando):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        self.assertEqual(A.main(['--root', str(self.root), '--repo', str(self.repo), comando]), 1)
                    self.assertIn(aviso, out.getvalue())
            self.assertEqual(self.destinos(), before)
        self.assertEqual(self.state(U_S_ORG), ('pending', None))   # `record` no tocó el censo
        self.assertTrue(marcador.exists())                        # no se repara solo
        self.assertFalse((self.root / 'esc/report').exists())
        marcador.write_text('{', encoding='utf-8')   # ilegible: no hay huellas con que comprobar
        self.assertEqual(A._apply_interrumpido(self.repo),
                         'apply interrumpido (fecha desconocida): recuperación incompleta: el marcador '
                         f'({marcador_rel}) no deja comprobar los destinos; compruébalos y restáuralos a mano (sus '
                         '`.bak` o su versión anterior en git)')

    def test_un_yaml_roto_no_sale_en_el_error_del_apply(self):
        # Codex [alto], 5ª pasada: el texto del parser cita la línea rota (aquí, con un alias retirado sintético).
        centinela = 'ALIAS_RETIRADO_SINTETICO'
        for rel in (A.SUM_PATH, A.ROL_PATH, A.REL_PATH, A.REG_PATH):
            path = self.repo / rel
            original = path.read_text(encoding='utf-8') if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'clave: [{centinela}\n', encoding='utf-8')
            try:
                with self.subTest(rel):
                    before = self.destinos()
                    out, stderr = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(stderr), \
                            self.assertRaises(SystemExit) as err:
                        self.apply(self.retirar())
                    if rel in (A.REL_PATH, A.REG_PATH):   # el lector de main (spec 071 AC108): exit 2 y stderr, 9-nov
                        self.assertEqual(str(err.exception), '2')
                        self.assertIn(f'{Path(rel).name}: YAML inválido (línea 2, columna 1)', stderr.getvalue())
                    else:
                        self.assertEqual(str(err.exception),
                                         f'{Path(rel).name}: YAML inválido (línea 2, columna 1) (spec 070)')
                    self.assertNotIn(centinela, out.getvalue() + stderr.getvalue())
                    self.assertEqual((self.destinos(), self.restos()), (before, []))
            finally:
                if original is None:
                    path.unlink()
                else:
                    path.write_text(original, encoding='utf-8')

    def test_el_motivo_libre_de_un_seudonimo_va_solo_a_la_traza_privada(self):
        # Codex [alto], 5ª pasada: el `reason` libre de una decisión sobre un seudónimo puede citar justo lo que se
        # retira (aquí, el alias) y viajaba al overlay público y al dry-run. Allí va un motivo neutro; el libre, a la
        # traza privada.
        u_sum, u_per, pack, tercero = '1' * 64, '2' * 64, 'E-zzseudomot01', 'Person:zz-tercero-test'
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-s-per', 'relationship', 'r:per', SEUDO, 'MET_WITH', 'judicial', 0, 'h', '{}'))
        reunion = {'start': SEUDO, 'end': tercero, 'type': 'MET_WITH', 'props': {   # de prensa: sale a «capas»
            'source_kind': 'prensa', 'url': 'https://zz.example/reunion', 'source_doc': 'ATS_0000_2025.pdf',
            'source_page': 4, 'source_quote': 'cita sintética de una reunión'}}
        for uid, oid, kind, field, value in ((u_sum, 'o-s-node', 'summary', '/summary@0:21', 'Zz figura en el auto.'),
                                             (u_per, 'o-s-per', 'edge', '/edge@1', reunion)):
            db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                       ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, oid, kind, field, json.dumps(value)))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   (pack, SEUDO, 'entity', 1, 1, json.dumps([U_S_ID, u_sum, u_per]), 'routed'))
        db.commit()
        db.close()
        motivo = 'el auto lo llama «el consejero Zz Test»'
        d = decision(pack_id=pack, entity=SEUDO, alias_deny=['el consejero Zz Test'],
                     units=[unit(U_S_ID, 'corregido', 'alias'), unit(u_sum, 'corregido', 'retirada'),
                            unit(u_per, 'corregido', 'arista')],
                     edges=[edge_op(u_per, 'deny', reason=motivo)], _file=f'{pack}.json')
        d['summary'] = {'action': 'remove', 'resumen': '', 'sources': [], 'reason': motivo}
        d['role'] = {'action': 'remove', 'role': '', 'reason': motivo}
        d['estado'] = {'action': 'set', 'status': 'investigado', 'fecha': '2025-04-01', 'doc': 'ATS_0000_2025.pdf',
                       'url': '', 'tipo': 'auto', 'nota': motivo}   # su informe es privado; el dry-run no
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            plan = self.apply(copy.deepcopy(d), dry_run=True)
        for privado in ('consejero', 'zz.example'):
            self.assertNotIn(privado, out.getvalue())
        estado, = plan['rows']['estado']   # el recorte a 220 caracteres no es lo que las protege
        self.assertEqual(A._imprimible('estado', estado, frozenset({SEUDO}))['estado']['nota'],
                         'motivo en la traza privada')
        capa = A._imprimible('capas', plan['rows']['capas'][0], frozenset({SEUDO}))
        self.assertEqual((capa['source'], capa['target'], capa['quote'], capa['url']), (SEUDO, tercero, '', ''))
        res = self.apply(copy.deepcopy(d))
        self.assertNotIn('consejero', '\n'.join(t or '' for t in self.destinos()))
        neutro = 'retirada por la política de seudónimos judiciales (spec 070); motivo en la traza privada'
        summaries = yaml.safe_load((self.repo / A.SUM_PATH).read_text())['summaries']
        roles = yaml.safe_load((self.repo / A.ROL_PATH).read_text())['entities']
        deny = yaml.safe_load((self.repo / A.REL_PATH).read_text())['deny']
        self.assertEqual([r['reason'] for r in summaries + roles if r.get('entity_id') == SEUDO],
                         [f'{self.tag}: {neutro}'] * 2)
        # Spec 071 AC114: la deny que oculta al tercero es opaca: su unidad, su capa y un motivo neutro.
        self.assertEqual([r['reason'] for r in deny if r.get('unidad') == u_per], [f'[{u_per[:8]}]: {A.MOTIVO_OPACO}'])
        traza = Path(res['seudonimos_path']).read_text()
        self.assertEqual(traza.count(motivo), 4)   # summaries, roles, la arista (con su unidad) y el estado
        self.assertIn(f'**relations** · unidad {u_per[:8]} ({pack}.json): {motivo}', traza)
        self.assertIn(motivo, Path(res['estado_path']).read_text())   # el informe de estado, privado, la conserva
        # Una decisión civil conserva su motivo: lo revisa el PR.
        self.write(pack, d)   # la 071 exige la decisión vigente de cada regla etiquetada
        self.apply(full_decision())
        summaries = yaml.safe_load((self.repo / A.SUM_PATH).read_text())['summaries']
        self.assertEqual(next(r for r in summaries if r['entity_id'] == FULDO)['reason'],
                         f'{self.tag}: prosa sin fuente')

    def test_una_decision_civil_sobre_una_arista_de_seudonimo_publica_un_motivo_neutro(self):
        # Enmienda 6 (AC29): la decisión es sobre un civil, pero la arista toca al seudónimo y su motivo libre lo
        # reidentificaría igual. Persona↔seudónimo (deny) y sociedad↔seudónimo (fix sobre una arista conservada).
        u_per, u_soc, motivo = '3' * 64, '4' * 64, 'el auto lo llama «el consejero Zz Test»'
        db = sqlite3.connect(self.root / 'census.sqlite')
        for oid, entidad, tipo, value in (
                ('o-c-per', FULDO, 'MET_WITH', {'start': FULDO, 'end': SEUDO, 'type': 'MET_WITH'}),
                ('o-c-soc', SOC, 'PAID', {'start': SOC, 'end': SEUDO, 'type': 'PAID'})):
            db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                       (oid, 'relationship', f'r:{oid}', entidad, tipo, 'judicial', 0, 'h', '{}'))
            value['props'] = {'source_kind': 'judicial', 'source_doc': 'ATS_1111_2025.pdf', 'source_page': 2,
                              'source_quote': f'cita sintética {tipo}'}
            uid = u_per if tipo == 'MET_WITH' else u_soc
            db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                       ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, oid, 'edge', '/edge@1', json.dumps(value)))
            db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                       (f'E-civil-{tipo}', entidad, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.commit()
        db.close()
        persona = decision(pack_id='E-civil-MET_WITH', units=[unit(u_per, 'corregido', 'arista')],
                           edges=[edge_op(u_per, 'deny', reason=motivo)], _file='E-civil-MET_WITH.json')
        sociedad = decision(pack_id='E-civil-PAID', entity=SOC, units=[unit(u_soc, 'corregido', 'arista')],
                            edges=[edge_op(u_soc, 'set_prop', prop='amount', value='200', reason=motivo)],
                            _file='E-civil-PAID.json')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.apply(copy.deepcopy(persona), copy.deepcopy(sociedad), dry_run=True)
        self.assertNotIn('consejero', out.getvalue())
        res = self.apply(copy.deepcopy(persona), copy.deepcopy(sociedad))
        self.assertNotIn('consejero', '\n'.join(t or '' for t in self.destinos()))
        overlay = yaml.safe_load((self.repo / A.REL_PATH).read_text())
        politica = 'por la política de seudónimos judiciales (spec 070); motivo en la traza privada'
        # Spec 071 AC114: la deny que oculta al seudónimo en la fila del civil es opaca, con un motivo neutro.
        self.assertEqual([r['reason'] for r in overlay['deny'] if r.get('unidad') == u_per],
                         [f'[{u_per[:8]}]: {A.MOTIVO_OPACO}'])
        self.assertEqual([r['reason'] for r in overlay['fix'] if r['target_id'] == SEUDO],
                         [f'{self.tag} [{u_soc[:8]}]: corregida {politica}'])
        traza = Path(res['seudonimos_path']).read_text()
        for uid, ref in ((u_per, 'E-civil-MET_WITH.json'), (u_soc, 'E-civil-PAID.json')):
            self.assertIn(f'**relations** · unidad {uid[:8]} ({ref}): {motivo}', traza)
        # Una arista entre civiles conserva su motivo.
        for d in (persona, sociedad):
            self.write(d['_file'][:-len('.json')], d)   # la 071 exige la decisión vigente de cada regla etiquetada
        self.apply(full_decision())
        overlay = yaml.safe_load((self.repo / A.REL_PATH).read_text())
        self.assertEqual([r['reason'] for r in overlay['deny'] if r.get('target_id') == 'Crime:cohecho'],
                         [f'{self.tag} [{U_EDGE[:8]}]: la cita no sostiene la relación'])

    def cli(self, comando='check', *opciones):
        """(código, stdout, stderr) del CLI sobre el repo de prueba (con `opciones`, p. ej. `--snapshot`); una excepción
        que escape es un fallo del test."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(seudonimos, 'documentos_publicos', return_value=frozenset(DOCS_PUBLICOS)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                codigo = A.main(['--root', str(self.root), '--repo', str(self.repo), *map(str, opciones), comando])
            except SystemExit as fin:
                codigo = fin.code
            except Exception as exc:   # su texto podría llevar el valor: solo la clase
                self.fail(f'el CLI dejó escapar {type(exc).__name__}')
        return codigo, out.getvalue(), err.getvalue()

    def test_barrido_de_centinelas_por_campo_de_la_decision(self):
        """AC58 (enmienda 13): cada campo textual de una decisión lleva un centinela único como único contenido inválido
        (o privado) sobre una decisión válida, del seudónimo o del civil. `check` y `dry-run` terminan (0 o 1) sin el
        centinela ni sus 8 primeros caracteres (lo que se imprime de un id de unidad) en stdout ni stderr. Lo que el
        dry-run de un civil enseña porque se va a publicar (resumen, rol, motivo libre, el alias que su regla
        `alias_deny` retira) no es un dato rechazado ni privado: se prueba con su forma inválida (la vaga), que se
        rechaza. El `alias_deny` de un civil se publica en su regla (política 061-C): su caso inválido es el de AC60,
        una entrada que nombra a un seudónimo declarado (el centinela se declara en el roster)."""
        def con(base, *cambios):
            d = copy.deepcopy(base)
            for ruta, valor in cambios:
                objetivo = d
                for paso in ruta[:-1]:
                    objetivo = objetivo[paso]
                objetivo[ruta[-1]] = valor
            return d

        # El seudónimo de prueba tiene un alias que no es su nombre de pila: toda decisión válida sobre él lo retira.
        corregir = {U_S_ORG: ('corregido', 'arista'), U_S_ID: ('corregido', 'alias')}
        alias = ('el consejero Zz Test',)
        seudo = retirar = self.retirar()
        estado = con(seudo, (('estado',), {'action': 'set', 'status': 'investigado', 'fecha': '2026-04-11',
                                            'doc': 'ATS_0000_2025.pdf', 'url': '', 'tipo': 'auto',
                                            'nota': 'motivo sintético'}))
        retipo = self.seudo(edges=[edge_op(U_S_ORG, 'retype', new_type='MEMBER_OF')], alias_deny=alias,
                            cambios=corregir)
        propiedad = self.seudo(edges=[edge_op(U_S_ORG, 'set_prop', prop='source_page', value='4')], alias_deny=alias,
                               cambios=corregir)
        estado_arista = self.seudo(edges=[edge_op(U_S_ORG, 'set_status', status='imputado')], alias_deny=alias,
                                   cambios=corregir)
        borrar_prosa = con(seudo, (('summary',), {'action': 'remove', 'resumen': '', 'sources': [], 'reason': 'r'}),
                           (('role',), {'action': 'remove', 'role': '', 'reason': 'r'}))
        civil = full_decision()
        vago = 'según la prensa '
        casos = {
            'entity_id': lambda c: con(seudo, (('entity_id',), c)),
            'units.unit_id': lambda c: con(seudo, (('units', 0, 'unit_id'), c)),
            'units.verdict': lambda c: con(seudo, (('units', 0, 'verdict'), c)),
            'units.label': lambda c: con(seudo, (('units', 0, 'label'), c)),
            'units.evidence': lambda c: con(seudo, (('units', 0, 'evidence'), c)),
            'units.confidence': lambda c: con(seudo, (('units', 0, 'confidence'), c)),
            'units.requested_sources': lambda c: con(seudo, (('units', 0, 'requested_sources'), [c])),
            'summary.action': lambda c: con(seudo, (('summary', 'action'), c)),
            'summary.resumen': lambda c: con(borrar_prosa, (('summary', 'resumen'), c)),
            'summary.sources': lambda c: con(borrar_prosa, (('summary', 'sources'), [c])),
            'summary.reason': lambda c: con(borrar_prosa, (('summary', 'reason'), c)),
            'role.action': lambda c: con(seudo, (('role', 'action'), c)),
            'role.role': lambda c: con(borrar_prosa, (('role', 'role'), c)),
            'role.reason': lambda c: con(borrar_prosa, (('role', 'reason'), c)),
            'alias_deny': lambda c: con(retirar, (('alias_deny',), [c])),
            'estado.action': lambda c: con(seudo, (('estado', 'action'), c)),
            'estado.status': lambda c: con(estado, (('estado', 'status'), c)),
            'estado.fecha': lambda c: con(estado, (('estado', 'fecha'), c)),
            'estado.doc': lambda c: con(estado, (('estado', 'doc'), c)),
            'estado.url': lambda c: con(estado, (('estado', 'url'), c)),
            'estado.tipo': lambda c: con(estado, (('estado', 'tipo'), c)),
            'estado.nota': lambda c: con(estado, (('estado', 'nota'), c)),
            'edges.unit_id': lambda c: con(retirar, (('edges', 0, 'unit_id'), c)),
            'edges.action': lambda c: con(retirar, (('edges', 0, 'action'), c)),
            'edges.new_type': lambda c: con(retipo, (('edges', 0, 'new_type'), c)),
            'edges.prop': lambda c: con(propiedad, (('edges', 0, 'prop'), c)),
            'edges.value': lambda c: con(propiedad, (('edges', 0, 'value'), c)),
            'edges.status': lambda c: con(estado_arista, (('edges', 0, 'status'), c)),
            'edges.evidence': lambda c: con(retirar, (('edges', 0, 'evidence'), c)),
            'edges.reason': lambda c: con(retirar, (('edges', 0, 'reason'), c)),
            'notes': lambda c: con(seudo, (('notes',), c)),
            '_meta.resolution': lambda c: con(seudo, (('_meta', 'resolution'), c)),
            '_meta.pack_id': lambda c: con(seudo, (('_meta', 'pack_id'), c)),
            '_meta.unit_ids': lambda c: con(seudo, (('_meta', 'unit_ids'), [c])),
            '_meta.entity': lambda c: con(seudo, (('_meta', 'entity'), c)),
            '_meta.route': lambda c: con(seudo, (('_meta', 'route'), c)),
            '_meta.overlay_ref': lambda c: con(seudo, (('_meta', 'overlay_ref'), c)),
            'civil.editorial.unit_id': lambda c: con(civil, (('editorial', 0, 'unit_id'), c)),
            'civil.editorial.action': lambda c: con(civil, (('editorial', 0, 'action'), c)),
            'civil.editorial.text': lambda c: con(civil, (('editorial', 0, 'text'), c)),
            'civil.editorial.reason': lambda c: con(civil, (('editorial', 0, 'reason'), c)),
            'civil.summary.resumen': lambda c: con(civil, (('summary',), {'action': 'replace', 'resumen': vago + c,
                                                                            'sources': [], 'reason': 'r'})),
            'civil.role.role': lambda c: con(civil, (('role', 'role'), vago + c)),
            'civil.alias_deny': lambda c: con(civil, (('alias_deny',), ['Fuldo', c])),
            # AC69 (enmienda 17): el motivo de un civil se publica; nombra a un seudónimo declarado por la mención
            # explícita o por su id.
            'civil.summary.reason.mencion': lambda c: con(civil, (('summary', 'reason'),
                                                                  f'el seudónimo judicial «{c}»')),
            'civil.summary.reason.id': lambda c: con(civil, (('summary', 'reason'), f'no es Person:{c.lower()}')),
            # AC85 (enmienda 22): el id con un U+034F dentro, que no lo esconde.
            'civil.summary.reason.ignorable': lambda c: con(
                civil, (('summary', 'reason'), f'no es Person:{c.lower()[:4]}\u034f{c.lower()[4:]}')),
        }
        declarados = ('civil.alias_deny', 'civil.summary.reason.mencion', 'civil.summary.reason.id',
                      'civil.summary.reason.ignorable')
        roster = self.repo / seudonimos.PSEUDONYMS_REL
        for base in (seudo, civil):   # las bases valen: lo único inválido de cada caso es su centinela
            self.write('E-barrido', base)
            self.assertEqual(self.cli()[0], 0)
        for n, (campo, decision_con) in enumerate(casos.items()):
            centinela = f'Q{n:02d}XZ{re.sub(r"[^A-Za-z0-9]", "", campo)}CENTINELA'
            if campo in declarados:   # AC60/AC69: el centinela es el nombre de pila de un seudónimo declarado
                roster.write_text(PSEUDONYMS + f'- id: Person:{centinela.lower()}\n  note: del barrido\n',
                                  encoding='utf-8')
            self.write('E-barrido', decision_con(centinela))
            for comando in ('check', 'dry-run'):
                with self.subTest(campo, comando=comando):
                    codigo, out, err = self.cli(comando)
                    self.assertIn(codigo, (0, 1, 2))   # 2: la decisión no vale (validar_*, spec 071 AC78/AC106/AC120)
                    self.assertNotIn(centinela[:8].lower(), (out + err).lower())   # también como id (minúsculas)
            roster.write_text(PSEUDONYMS, encoding='utf-8')
        # AC83 (enmienda 22): el nombre que el censo guarda para el seudónimo tampoco sale.
        centinela = 'QNOMBREXZCENSOCENTINELA'
        self.nombre_en_censo(centinela)
        self.write('E-barrido', seudo)
        for comando in ('check', 'dry-run'):
            with self.subTest('censo.name', comando=comando):
                codigo, out, err = self.cli(comando)
                self.assertEqual(codigo, 1)
                self.assertNotIn(centinela[:8].lower(), (out + err).lower())

    def cli_proceso(self, comando='check'):
        """(código, stdout + stderr) del CLI real en un proceso aparte (AC63): el repo de prueba pasa a ser un repo git
        con sus documentos públicos, como lo ve la puerta."""
        if not (self.repo / '.git').exists():
            for doc in DOCS_PUBLICOS:
                (self.repo / 'data' / doc).parent.mkdir(parents=True, exist_ok=True)
                (self.repo / 'data' / doc).write_bytes(b'')
            for args in (['init', '-q'], ['add', '-A']):
                subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True)
        proc = subprocess.run([sys.executable, '-m', 'src.qa_esc_apply', '--root', str(self.root), '--repo',
                               str(self.repo), comando], cwd=REPO / 'pipeline', capture_output=True, text=True,
                              timeout=300)
        return proc.returncode, proc.stdout + proc.stderr

    def test_una_parada_del_loader_no_cita_el_extremo(self):
        # AC63 (enmienda 15): un `retype` sobre una arista de capa `mercantil` (fuera de `_KINDS_OK`) paraba el gate con
        # el `SystemExit` de `_prepare_kinds`, que citaba el extremo. Ahora es un error de la decisión con la huella de
        # la fila: por el CLI real (`check` y `dry-run`, en un proceso aparte), sin el extremo centinela.
        centinela, uid = 'Company:qz-empresa-privada-centinela', 'k' * 64
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-merc', 'relationship', 'r:merc', SEUDO, 'PAID', 'mercantil', 0, 'h', '{}'))
        value = {'start': centinela, 'end': SEUDO, 'type': 'PAID',
                 'props': {'source_kind': 'mercantil', 'source_doc': 'ATS_A.pdf', 'source_page': 2,
                           'source_quote': 'cita sintética'}}
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, 'o-merc', 'edge', '/edge@1', json.dumps(value)))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   ('E-merc', SEUDO, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.commit()
        db.close()
        self.write('E-merc', decision(pack_id='E-merc', entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                                      edges=[edge_op(uid, 'retype', new_type='OWNS')]))
        for comando in ('check', 'dry-run'):
            with self.subTest(comando):
                codigo, salida = self.cli_proceso(comando)
                self.assertEqual(codigo, 1)
                self.assertIn('fix[1]: el loader rechaza la fila (SystemExit)', salida)   # la forma de main, 9-nov
                self.assertIn('fix[1]: el loader rechaza la fila (SystemExit)', salida)
                self.assertNotIn('qz-empresa', salida.lower())
                self.assertNotIn('Traceback', salida)

    def test_el_roster_manda_sobre_la_marca_del_censo(self):
        # AC62 (enmienda 15): un seudónimo declarado cuyo nodo del censo no lleva la marca (ausente o `false`, un censo
        # anterior a la declaración) pasaba como civil: `summary`/`role.replace` con prosa identificativa y un estado
        # por encima de investigado. Se valida como seudónimo; nada de su prosa en la salida.
        centinela, otro, u_sum, u_rol = 'NOMBRE_PRIVADO_CENTINELA', 'Person:zz-otro-seudonimo-test', 'r' * 64, 's' * 64
        (self.repo / seudonimos.PSEUDONYMS_REL).write_text(PSEUDONYMS + f'- id: {otro}\n  note: declarado después\n',
                                                            encoding='utf-8')
        prosa = f'{centinela} es el verdadero nombre.'
        for marca, props in (('ausente', {}), ('false', {'is_pseudonym': False})):
            with self.subTest(marca=marca):
                db = sqlite3.connect(self.root / 'census.sqlite')
                for tabla, columna in (('units', 'object_id'), ('objects', 'object_id')):
                    db.execute(f'DELETE FROM {tabla} WHERE {columna}=?', ('o-otro',))
                db.execute('DELETE FROM esc_packs WHERE pack_id=?', ('E-otro',))
                node = json.dumps({'labels': ['Person'], 'props': {'name': 'Zz Otro', 'summary': 'Prosa previa.',
                                                                   'role': 'Rol previo.', **props}})
                db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                           ('o-otro', 'node', 'n:otro', otro, 'Person', 'unspecified', 0, 'h', node))
                for uid, kind, field, valor in ((u_sum, 'summary', '/summary@0:12', 'Prosa previa.'),
                                                (u_rol, 'attribute', '/role', 'Rol previo.')):
                    db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                               ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, 'o-otro', kind, field, json.dumps(valor)))
                db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                           ('E-otro', otro, 'entity', 1, 1, json.dumps([u_sum, u_rol]), 'routed'))
                db.commit()
                db.close()
                d = decision(pack_id='E-otro', entity=otro,
                             units=[unit(u_sum, 'corregido', 'reescrita'), unit(u_rol, 'corregido', 'reescrita')])
                d['summary'] = {'action': 'replace', 'resumen': prosa, 'sources': [], 'reason': 'r'}
                d['role'] = {'action': 'replace', 'role': prosa, 'reason': 'r'}
                d['estado'] = {'action': 'set', 'status': 'imputado', 'fecha': '2026-04-11', 'doc': 'ATS_0000_2025.pdf',
                               'url': '', 'tipo': 'auto', 'nota': 'n'}
                self.write('E-otro', d)
                for comando in ('check', 'dry-run'):
                    codigo, out, err = self.cli(comando)
                    self.assertEqual(codigo, 1)
                    self.assertIn('E-otro.json: seudónimo judicial: la prosa se retira (remove), nunca se reescribe',
                                  out)
                    self.assertIn('E-otro.json: seudónimo judicial: la fila de estado solo puede ser investigado', out)
                    self.assertNotIn(centinela, out + err)

    def test_una_resolucion_guardada_no_se_vuelve_a_imprimir(self):
        # AC64 (enmienda 15), la segunda entrada del campo del barrido AC58, con la 071 AC120 (fusión 070 ⟷ 071: la más
        # restrictiva): `record` solo guarda una `_meta.resolution` de su familia (una centinela para con exit 2 sin su
        # valor: `test_a_resolution_outside_its_family_stops_every_command_without_its_value`); otra decisión que cubre
        # esa unidad la cita por su forma cerrada al decir que no la puede cubrir, y nada más de la que la guardó.
        guardada = 'humano-2026-11-09'
        parcial = partial([unit(U_SUM)], [U_SUM], resolution=guardada, name='parcial.json')
        conn = A.connect(self.root)
        try:
            self.assertEqual(A.gate(conn, [parcial], self.root, frozenset({SEUDO}), self.publicos)[0], [])
            A.record(conn, self.root, self.repo, [parcial], '', 'RUNBOOK')
        finally:
            conn.close()
        self.write('otra', decision(resolution='opus-cerrada'))
        for comando in ('check', 'dry-run'):
            with self.subTest(comando):
                codigo, out, _ = self.cli(comando)
                self.assertEqual(codigo, 1)
                self.assertIn(f'unidad {U_SUM[:8]} en estado verified/{guardada}, opus-cerrada solo cubre pending', out)
        # La que ya estaba guardada en el censo fuera de su familia (un censo anterior a la 071) se cita por el marcador
        # neutro, nunca por su valor (AC64 con AC120).
        centinela = 'RESOLUCION_PRIVADA_CENTINELA'
        conn = A.connect(self.root)
        with conn:
            conn.execute('UPDATE units SET resolution=? WHERE unit_id=?', (centinela, U_SUM))
        conn.close()
        for comando in ('check', 'dry-run'):
            with self.subTest(censo=comando):
                codigo, out, err = self.cli(comando)
                self.assertEqual(codigo, 1)
                self.assertIn(f'unidad {U_SUM[:8]} en estado verified/{A.RESOLUCION_DESCONOCIDA}, opus-cerrada solo '
                              'cubre pending', out)
                self.assertNotIn(centinela[:8], out + err)

    def test_el_alias_deny_de_un_civil_no_nombra_a_un_seudonimo(self):
        # AC60 (enmienda 14): la regla `alias_deny` de un civil es pública; una entrada que es el nombre de pila de un
        # seudónimo declarado y no forma parte del nombre de la entidad ni de una identidad que la decisión cubre los
        # emparejaría. Error del gate, antes de materializar, con la huella y sin el texto; nada escrito.
        d = full_decision()
        d['alias_deny'] = ['Fuldo', 'Zz Test']
        error = ('E-ac60.json: alias_deny: una entrada nombra a un seudónimo judicial '
                 f'(huella {seudonimos.sha16("Zz Test")})')
        self.write('E-ac60', d)
        before = self.destinos()
        codigo, out, err = self.cli('check')
        self.assertEqual(codigo, 1)
        self.assertIn(f'  ❌ {error}\n', out)
        self.assertNotIn('Zz Test', out + err)
        self.assertEqual(self.destinos(), before)
        # Con una identidad cubierta que la contiene, la entrada es de la propia entidad: pasa.
        conn = A.connect(self.root)
        with conn:
            conn.execute('UPDATE units SET value=? WHERE unit_id=?',
                         (json.dumps(identity_value(FULDO, 'Fuldo Fíctez', ['Fuldo', 'Fuldo Zz Test'])), U_ID))
        conn.close()
        self.assertEqual(self.cli('check')[0], 0)
        # La misma entrada sobre un civil cuyo `entity_id` la contiene, o con un tratamiento delante, decide igual.
        self.assertFalse(seudonimos.alias_civil_nombra_seudonimo('Zz Test', 'Person:zz-test-garcia', {SEUDO}))
        self.assertTrue(seudonimos.alias_civil_nombra_seudonimo('Don Zz Test', FULDO, {SEUDO}))
        self.assertFalse(seudonimos.alias_civil_nombra_seudonimo('Fuldo', FULDO, {SEUDO}))

    def huellas_destinos(self):
        """El sha256 de los cinco ficheros que escribe el apply (None = no existe)."""
        return [hashlib.sha256(t.encode()).hexdigest() if t is not None else None for t in self.destinos()]

    def test_la_prosa_publica_de_un_civil_no_nombra_a_un_seudonimo(self):
        # AC69 (enmienda 17): antes de materializar, el gate rechaza la decisión de un civil cuyo texto publicable
        # (resumen y rol con sus motivos, motivos de arista, nota del estado, tanda editorial, `alias_deny`) nombra a un
        # seudónimo, por su id o por la mención explícita. Las `notes` y la `evidence` son razonamiento privado: no
        # cuentan. Nada escrito; ni el id ni el nombre en la salida.
        error = f'E-ac69.json: prosa civil que nombra a un seudónimo judicial (huella {seudonimos.sha16(SEUDO)})'
        before = self.huellas_destinos()
        for (bloque, campo), texto in {('role', 'reason'): f'no es {SEUDO}',
                                       ('summary', 'reason'): 'no es el seudónimo judicial «Zz Test»'}.items():
            with self.subTest(bloque=bloque):
                d = full_decision()
                d[bloque][campo] = texto
                self.write('E-ac69', d)
                codigo, out, err = self.cli('check')
                self.assertEqual(codigo, 1)
                self.assertIn(f'  ❌ {error}\n', out)
                self.assertNotIn(SEUDO, out + err)
                self.assertNotIn('zz test', (out + err).lower())
                self.assertEqual(self.huellas_destinos(), before)
        d = full_decision()
        d['notes'] = d['units'][0]['evidence'] = f'no es {SEUDO}: el seudónimo judicial «Zz Test» es otra persona'
        self.write('E-ac69', d)
        self.assertEqual(self.cli('check')[0], 0)

    def test_lo_que_se_compone_con_el_censo_tampoco_nombra_a_un_seudonimo(self):
        # AC69 (enmienda 17): tras materializar y antes de imprimir o escribir, ninguna fila de un civil nombra a un
        # seudónimo, aunque el texto no venga de la decisión: la cita de una capa de prensa sale del censo. `check`,
        # `dry-run` y `apply` paran con la posición en el plan y la huella; los destinos quedan intactos (por hash) y no
        # se escribe ningún informe.
        cita = 'según el auto, el seudónimo judicial «Zz Test» cobró'
        conn = A.connect(self.root)
        with conn:
            conn.execute('UPDATE units SET value=? WHERE unit_id=?',
                         (json.dumps(edge_value(source_kind='prensa', url='https://example.org/n', source_quote=cita),
                                     ensure_ascii=False), U_EDGE))
        conn.close()
        self.write('E-ac69', full_decision())
        before = self.huellas_destinos()
        for comando in ('check', 'dry-run', 'apply'):
            with self.subTest(comando):
                codigo, out, err = self.cli(comando)
                self.assertEqual(codigo, 1)
                self.assertIn('  ❌ capas[0]: prosa civil que nombra a un seudónimo judicial '
                              f'(huella {seudonimos.sha16(SEUDO)})\n', out)
                self.assertNotIn('zz test', (out + err).lower())
                self.assertEqual(self.huellas_destinos(), before)
        self.assertFalse((self.root / 'esc/report').exists())

    def test_el_alias_civil_marcado_con_el_nombre_invertido_no_pasa_el_gate(self):
        # AC71 (enmienda 18): «Test Zz (seudónimo TS)» es variante del seudónimo de prueba; en el `alias_deny` de un
        # civil lo nombra igual que en el orden del slug. `check` y `dry-run` paran antes de imprimir; destinos
        # intactos.
        d = full_decision()
        d['alias_deny'] = ['Fuldo', 'Test Zz (seudónimo TS)']
        self.write('E-ac71', d)
        error = ('E-ac71.json: alias_deny: una entrada nombra a un seudónimo judicial '
                 f'(huella {seudonimos.sha16("Test Zz (seudónimo TS)")})')
        before = self.huellas_destinos()
        for comando in ('check', 'dry-run'):
            with self.subTest(comando):
                codigo, out, err = self.cli(comando)
                self.assertEqual(codigo, 1)
                self.assertIn(f'  ❌ {error}\n', out)
                self.assertNotIn('test zz', (out + err).lower())
                self.assertEqual(self.huellas_destinos(), before)

    def test_la_cita_de_una_regla_civil_tampoco_nombra_a_un_seudonimo(self):
        # AC72 (enmienda 18): la regla `deny` de una arista civil publica el principio de su cita (`quote_contains`),
        # que viene del censo: el gate no la ve y el control posterior la lee. Una arista judicial (sin fila de capas)
        # cuya cita dice «es el seudónimo judicial «Zz Test»» para `check`, `dry-run` y `apply` con la posición y la
        # huella; destinos intactos (por hash) y ningún informe escrito.
        conn = A.connect(self.root)
        with conn:
            conn.execute('UPDATE units SET value=? WHERE unit_id=?',
                         (json.dumps(edge_value(source_quote='Es el seudónimo judicial «Zz Test» quien cobró'),
                                     ensure_ascii=False), U_EDGE))
        conn.close()
        self.write('E-ac72', full_decision())
        before = self.huellas_destinos()
        for comando in ('check', 'dry-run', 'apply'):
            with self.subTest(comando):
                codigo, out, err = self.cli(comando)
                self.assertEqual(codigo, 1)
                self.assertIn('  ❌ deny[0]: prosa civil que nombra a un seudónimo judicial '
                              f'(huella {seudonimos.sha16(SEUDO)})\n', out)
                self.assertNotIn('zz test', (out + err).lower())
                self.assertEqual(self.huellas_destinos(), before)
        self.assertFalse((self.root / 'esc/report').exists())

    def test_las_fuentes_del_resumen_de_un_civil_tampoco(self):
        # AC72 (enmienda 18): las `sources` de un resumen se publican con él. Una URL con el `entity_id` del seudónimo
        # la para el gate antes de materializar y, materializada, el control posterior; ni el id en la salida.
        d = full_decision()
        d['summary'] = {'action': 'replace', 'resumen': 'Prosa nueva.', 'sources': [f'https://example.org/{SEUDO}'],
                        'reason': 'r'}
        self.write('E-ac72', d)
        huella = seudonimos.sha16(SEUDO)
        before = self.huellas_destinos()
        codigo, out, err = self.cli('check')
        self.assertEqual(codigo, 1)
        self.assertIn(f'  ❌ E-ac72.json: prosa civil que nombra a un seudónimo judicial (huella {huella})\n', out)
        self.assertNotIn(SEUDO, out + err)
        self.assertEqual(self.huellas_destinos(), before)
        self.gate(d)   # anota `_pack`
        rows = A.materialize([d], self.tag, self.upd, frozenset({SEUDO}), self.publicos)
        self.assertEqual(A._prosa_publica(rows, frozenset({SEUDO}), {}, self.publicos),
                         [f'summaries[0]: prosa civil que nombra a un seudónimo judicial (huella {huella})'])

    def test_el_alias_civil_con_la_marca_intercalada_no_pasa_el_gate(self):
        # AC74 (enmienda 19): «Zz (seudónimo TS) Test» es variante del seudónimo de prueba; en el `alias_deny` de un
        # civil lo nombra aunque la marca separe sus palabras. `check` y `dry-run` paran antes de imprimir; destinos
        # intactos.
        alias = 'Zz (seudónimo TS) Test'
        d = full_decision()
        d['alias_deny'] = ['Fuldo', alias]
        self.write('E-ac74', d)
        error = ('E-ac74.json: alias_deny: una entrada nombra a un seudónimo judicial '
                 f'(huella {seudonimos.sha16(alias)})')
        before = self.huellas_destinos()
        for comando in ('check', 'dry-run'):
            with self.subTest(comando):
                codigo, out, err = self.cli(comando)
                self.assertEqual(codigo, 1)
                self.assertIn(f'  ❌ {error}\n', out)
                self.assertNotIn('zz (seud', (out + err).lower())
                self.assertEqual(self.huellas_destinos(), before)

    def test_el_alias_civil_con_marca_y_el_nombre_separado_no_pasa_el_gate(self):
        # AC77 (enmienda 20): con una marca, al alias de un civil le basta traer todas las palabras del nombre de pila
        # del seudónimo de prueba, aunque otras las separen. Error del gate antes de materializar, con la huella;
        # `check` y `dry-run` paran antes de imprimir y los destinos quedan intactos.
        alias = 'Zz (seudónimo de anonimización) Test'
        d = full_decision()
        d['alias_deny'] = ['Fuldo', alias]
        self.write('E-ac77', d)
        error = ('E-ac77.json: alias_deny: una entrada nombra a un seudónimo judicial '
                 f'(huella {seudonimos.sha16(alias)})')
        before = self.huellas_destinos()
        for comando in ('check', 'dry-run'):
            with self.subTest(comando):
                codigo, out, err = self.cli(comando)
                self.assertEqual(codigo, 1)
                self.assertIn(f'  ❌ {error}\n', out)
                self.assertNotIn('anonimizaci', (out + err).lower())
                self.assertEqual(self.huellas_destinos(), before)

    def test_la_url_del_estado_de_un_civil_tampoco(self):
        # AC75 (enmienda 19), con AC73: la URL de la fuente de un estado se publica con él. Codificada con el id del
        # seudónimo, la para el gate antes de materializar y, materializada, el control posterior; ni el id en la
        # salida (decodificado ni sin decodificar) y destinos intactos.
        d = full_decision()
        d['estado'] = {'action': 'set', 'status': 'investigado', 'fecha': '2026-04-11', 'doc': '', 'tipo': 'auto',
                       'url': 'https://example.org/Person%3Azz-seudonimo-test', 'nota': 'n'}
        self.write('E-ac75', d)
        huella = seudonimos.sha16(SEUDO)
        before = self.huellas_destinos()
        for comando in ('check', 'dry-run'):
            with self.subTest(comando):
                codigo, out, err = self.cli(comando)
                self.assertEqual(codigo, 1)
                self.assertIn(f'  ❌ E-ac75.json: prosa civil que nombra a un seudónimo judicial (huella {huella})\n',
                              out)
                self.assertNotIn('zz-seudonimo-test', (out + err).lower())
                self.assertEqual(self.huellas_destinos(), before)
        self.gate(d)   # anota `_pack`
        rows = A.materialize([d], self.tag, self.upd, frozenset({SEUDO}), self.publicos)
        self.assertEqual(A._prosa_publica(rows, frozenset({SEUDO}), {}, self.publicos),
                         [f'estado[0]: prosa civil que nombra a un seudónimo judicial (huella {huella})'])

    def arista_del_seudonimo(self, uid, otro, tipo, cita='cita sintética', doc='ATS_A.pdf'):
        """Una arista judicial del seudónimo de prueba en el censo (de `doc`; None, sin documento), en un pack propio
        (`E-<8>`); devuelve el pack."""
        pack = f'E-{uid[:8]}'
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   (f'o-{uid[:8]}', 'relationship', f'r:{uid[:8]}', SEUDO, tipo, 'judicial', 0, 'h', '{}'))
        value = {'start': SEUDO, 'end': otro, 'type': tipo, 'props': {
            'source_kind': 'judicial', **({'source_doc': doc} if doc else {}), 'source_page': 2, 'source_quote': cita}}
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, f'o-{uid[:8]}', 'edge', '/edge@1', json.dumps(value)))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   (pack, SEUDO, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.commit()
        db.close()
        return pack

    AC78 = 'texto de una fila de seudónimo nombra a un civil o a un documento (huella {})'
    COAPARICION = 'cita que nombra a su extremo civil sin prueba de coaparición (huella {})'   # AC90 (enmienda 24)

    def test_el_texto_de_una_fila_de_seudonimo_no_nombra_a_un_civil(self):
        """AC78 (enmienda 21): antes de materializar, el texto que una decisión lleva a una fila de un seudónimo (aquí,
        la nota de un `set_prop` sobre su arista ACCUSED_OF) no lleva un id ajeno a la fila. Error del gate con la
        unidad y la huella; `check` y `dry-run` paran sin el texto y sin escribir; la regla equivalente en el árbol es
        hallazgo del check. Con una nota neutra, pasa y se escribe."""
        uid, nota = 'w' * 64, 'Identidad real: Person:zz-civil-test (Civil Sintetico Apellido)'
        pack = self.arista_del_seudonimo(uid, 'Crime:zz-delito-test', 'ACCUSED_OF')
        d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                     edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
        error = f'{pack}.json: unidad {uid[:8]}: {self.AC78.format(seudonimos.sha16("Person:zz-civil-test"))}'
        before = self.destinos()
        for kwargs in ({'check': True}, {'dry_run': True}):
            with self.subTest(**kwargs):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                    self.apply(copy.deepcopy(d), **kwargs)
                self.assertIn(f'  ❌ {error}\n', out.getvalue())
                for privado in ('Identidad', 'zz-civil-test', 'Sintetico'):
                    self.assertNotIn(privado, out.getvalue() + err.getvalue())
        self.assertEqual(self.destinos(), before)
        conn = A.connect(self.root)
        try:
            self.assertEqual(A.gate(conn, [copy.deepcopy(d)], self.root, frozenset({SEUDO}), self.publicos, {})[0],
                             [error])
        finally:
            conn.close()
        equivalente = self.repo / seudonimos.RELATIONS_REL / 'equivalente.yaml'
        equivalente.write_text(yaml.safe_dump({'fix': [{'source_id': SEUDO, 'target_id': 'Crime:zz-delito-test',
                                                        'type': 'ACCUSED_OF', 'set': {'note': nota}}]},
                                              allow_unicode=True), encoding='utf-8')
        self.assertEqual([(h.clase, h.motivo) for h in seudonimos.auditar(self.repo, self.publicos)],
                         [('prosa', self.AC78.format(seudonimos.sha16('Person:zz-civil-test')))])
        equivalente.unlink()
        d['edges'][0]['value'] = 'Nota neutra del auto.'
        self.apply(copy.deepcopy(d))
        fix, = [r for r in yaml.safe_load((self.repo / A.REL_PATH).read_text())['fix'] if r['source_id'] == SEUDO]
        self.assertEqual(fix['set'], {'note': 'Nota neutra del auto.'})
        self.assertEqual(seudonimos.auditar(self.repo, self.publicos), [])

    def test_la_cita_de_una_regla_de_seudonimo_solo_nombra_a_sus_extremos(self):
        """AC78(b) (enmienda 21) con AC90 (enmienda 24): la cita que una regla de seudónimo publica (su
        `quote_contains`, del censo) no nombra a otro civil del contexto, y la que nombra a su otro extremo ya no pasa
        sin una entrada de `citas:` de la lista (huella de la cita). El gate la lee antes de materializar: `check`,
        `dry-run` y `apply` paran con la unidad y la huella; destinos intactos y ningún informe. Materializada, el
        control posterior da lo mismo con la posición en el plan."""
        propio = self.arista_del_seudonimo('x' * 64, FULDO, 'MET_WITH', cita='Fuldo Fíctez se reunió con él')
        ajeno = self.arista_del_seudonimo('y' * 64, 'Person:zz-tercero-test', 'MET_WITH',
                                          cita='Se reunió con Fuldo Fíctez')
        decisiones = [self.write(pack, decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                                                edges=[edge_op(uid, 'deny')]))
                      for pack, uid in ((propio, 'x' * 64), (ajeno, 'y' * 64))]
        huella, propia = seudonimos.sha16('Fuldo Fíctez'), seudonimos.sha16('Fuldo Fíctez se reunió con él')
        before = self.huellas_destinos()
        for comando in ('check', 'dry-run', 'apply'):
            with self.subTest(comando):
                codigo, out, err = self.cli(comando)
                self.assertEqual(codigo, 1)
                self.assertEqual([linea for linea in out.splitlines() if '❌' in linea],
                                 [f'  ❌ {propio}.json: unidad {"x" * 8}: {self.COAPARICION.format(propia)}',
                                  f'  ❌ {ajeno}.json: unidad {"y" * 8}: {self.AC78.format(huella)}'])
                self.assertNotIn('garcía', (out + err).lower())
                self.assertEqual(self.huellas_destinos(), before)
        self.assertFalse((self.root / 'esc/report').exists())
        self.gate(*decisiones)   # anota `_pack`
        rows = A.materialize(decisiones, self.tag, self.upd, frozenset({SEUDO}), self.publicos)
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), frozenset({SEUDO}))
        # Spec 071 AC114: las dos deny ocultan un extremo y son opacas: no publican la cita y su forma no la admite (la
        # del loader, `alcance.forma_deny`); el control posterior no tiene cita que juzgar.
        self.assertEqual([sorted(r) for r in rows['deny']], [['reason', 'unidad']] * 2)
        self.assertTrue(all(alcance.forma_deny({**r, 'quote_contains': 'Fuldo Fíctez'}) for r in rows['deny']))
        self.assertEqual(A._prosa_publica(rows, frozenset({SEUDO}), civiles, self.publicos), [])

    def test_la_cita_que_identifica_al_seudonimo_con_su_extremo_civil(self):
        """AC87 (enmienda 23): la exención del extremo civil en la cita de una regla de seudónimo es solo para la
        coaparición. Si la cita (el `quote_contains` de un `fix` MET_WITH) dice «seudónimo», el nombre del civil y la
        mención del seudónimo cuentan: el gate para antes de materializar con la unidad y las dos huellas, `check`,
        `dry-run` y `apply` paran sin escribir, y el control posterior da lo mismo; sin prueba, es además AC90 (AC94,
        enmienda 25). La de coaparición ya no pasa sin una entrada de `citas:` (AC90, enmienda 24): la huella de la
        cita. AC150 (enmienda 35): «seudónimo» entre el civil y el seudónimo lo identifica, con la huella de la cita."""
        identifica = self.arista_del_seudonimo('x' * 64, FULDO, 'MET_WITH',
                                               cita='Fuldo Fíctez utiliza el seudónimo «Zz Test»')
        coaparece = self.arista_del_seudonimo('y' * 64, FULDO, 'MET_WITH', cita='Se reunió con Fuldo Fíctez')
        decisiones = [decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                               edges=[edge_op(uid, 'retype', new_type='ASSOCIATED_WITH')], _file=f'{pack}.json')
                      for pack, uid in ((identifica, 'x' * 64), (coaparece, 'y' * 64))]
        huellas = [seudonimos.sha16('Fuldo Fíctez'), seudonimos.sha16(SEUDO),
                   seudonimos.sha16('Fuldo Fíctez utiliza el seudónimo «Zz Test»')]
        sin_prueba = f'{identifica}.json: unidad {"x" * 8}: ' + self.COAPARICION.format(
            seudonimos.sha16('Fuldo Fíctez utiliza el seudónimo «Zz Test»'))
        coaparicion = f'{coaparece}.json: unidad {"y" * 8}: ' + self.COAPARICION.format(
            seudonimos.sha16('Se reunió con Fuldo Fíctez'))
        for d in decisiones:
            self.write(d['_file'][:-len('.json')], d)
        before = self.huellas_destinos()
        for comando in ('check', 'dry-run', 'apply'):
            with self.subTest(comando):
                codigo, out, err = self.cli(comando)
                self.assertEqual(codigo, 1)
                self.assertEqual([linea for linea in out.splitlines() if '❌' in linea],
                                 [f'  ❌ {identifica}.json: unidad {"x" * 8}: {self.AC78.format(h)}' for h in huellas]
                                 + [f'  ❌ {sin_prueba}', f'  ❌ {coaparicion}'])
                self.assertNotIn('garcía', (out + err).lower())
                self.assertNotIn('zz test', (out + err).lower())
                self.assertEqual(self.huellas_destinos(), before)
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        nombres = seudonimos.nombres_contexto(self.repo)
        conn = A.connect(self.root)
        try:
            self.assertEqual(A.gate(conn, copy.deepcopy(decisiones), self.root, seudos, self.publicos, civiles, None,
                                    nombres, [])[0],
                             [f'{identifica}.json: unidad {"x" * 8}: {self.AC78.format(h)}' for h in huellas]
                             + [sin_prueba, coaparicion])
            self.assertEqual(A.gate(conn, copy.deepcopy(decisiones[1:]), self.root, seudos, self.publicos,
                                    civiles, None, nombres, [])[0], [coaparicion])
        finally:
            conn.close()
        self.gate(*decisiones)   # anota `_pack`
        rows = A.materialize(decisiones, self.tag, self.upd, seudos, self.publicos)
        self.assertEqual(A._prosa_publica(rows, seudos, civiles, self.publicos, nombres, []),
                         [f'fix[0]: {self.AC78.format(h)}' for h in huellas]
                         + [f'fix[0]: {self.COAPARICION.format(seudonimos.sha16(cita))}' for cita in (
                             'Fuldo Fíctez utiliza el seudónimo «Zz Test»',)]
                         + [f'fix[1]: {self.COAPARICION.format(seudonimos.sha16("Se reunió con Fuldo Fíctez"))}'])

    def test_una_fila_de_seudonimo_no_menciona_a_otro_seudonimo(self):
        """AC78(d) (enmienda 21): la nota del estado de un seudónimo (pegada en accusations, pública) no menciona a OTRO
        seudónimo del roster. Error del gate con la huella del otro; el `dry-run` para sin el nombre y sin escribir."""
        otro = 'Person:zz-otro-seudonimo-test'
        (self.repo / seudonimos.PSEUDONYMS_REL).write_text(PSEUDONYMS + f'- id: {otro}\n  note: otro sintético\n',
                                                           encoding='utf-8')
        self.alias_en_censo(['don Zz Test'])
        self.lista_alias('don Zz Test')
        d = self.identidad()
        d['estado'] = {'action': 'set', 'status': 'investigado', 'fecha': '2025-04-01', 'doc': 'ATS_0000_2025.pdf',
                       'url': '', 'tipo': 'auto', 'nota': 'Figura en el auto junto al seudónimo «Zz Otro Test».'}
        error = f'{d["_file"]}: {self.AC78.format(seudonimos.sha16(otro))}'
        before, out = self.destinos(), io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            self.apply(copy.deepcopy(d), dry_run=True)
        self.assertIn(f'  ❌ {error}\n', out.getvalue())
        self.assertNotIn('otro test', out.getvalue().lower())
        self.assertEqual(self.destinos(), before)
        conn = A.connect(self.root)
        try:
            self.assertEqual(A.gate(conn, [copy.deepcopy(d)], self.root, frozenset({SEUDO, otro}), self.publicos,
                                    {})[0], [error])
        finally:
            conn.close()

    SOSPECHOSO = 'texto con caracteres invisibles o alfabetos mezclados (huella {})'

    def posterior(self, d):
        """El control posterior (`_prosa_publica`) de la decisión `d` materializada, aunque el gate la pare antes."""
        seudos, decision = frozenset({SEUDO}), copy.deepcopy(d)
        self.gate(decision)   # anota `_pack`
        rows = A.materialize([decision], self.tag, self.upd, seudos, self.publicos)
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        return A._prosa_publica(rows, seudos, civiles, self.publicos, seudonimos.nombres_contexto(self.repo),
                                conservadas=decision.get('_conservadas'))

    def test_ningun_texto_publicado_lleva_caracteres_invisibles(self):
        """AC81 (enmienda 21) con AC105(a) (enmienda 28): un texto que se publicaría con caracteres de formato (aquí, un
        espacio de ancho cero en el resumen de un civil) para el apply ya en el gate, con la decisión y la huella, sin
        escribir; materializado, el control posterior también lo para, con la posición en el plan."""
        resumen = 'Prosa\u200b nueva.'
        d = full_decision()
        d['summary'] = {'action': 'replace', 'resumen': resumen, 'sources': [], 'reason': 'r'}
        self.write('E-ac81', d)
        before = self.huellas_destinos()
        codigo, out, _ = self.cli('check')
        self.assertEqual(codigo, 1)
        self.assertIn(f'  ❌ E-ac81.json: {self.SOSPECHOSO.format(seudonimos.sha16(resumen))}\n', out)
        self.assertEqual(self.huellas_destinos(), before)
        self.assertIn(f'summaries[0]: {self.SOSPECHOSO.format(seudonimos.sha16(resumen))}', self.posterior(d))

    def nombre_en_censo(self, nombre):
        """El `name`/`canonical_name` que el censo guarda para el nodo del seudónimo de prueba."""
        db = sqlite3.connect(self.root / 'census.sqlite')
        props = {'name': nombre, 'canonical_name': nombre, 'is_pseudonym': True, 'aliases': list(ALIAS_S)}
        db.execute("UPDATE objects SET payload=? WHERE object_id='o-s-node'",
                   (json.dumps({'labels': ['Person'], 'props': props}),))
        db.commit()
        db.close()

    def test_el_nombre_del_censo_de_un_seudonimo_no_sale(self):
        """AC83 (enmienda 22): el nombre que el censo guarda para un seudónimo (de la decisión o extremo de una arista
        suya) ha de ser una variante del suyo. Con uno civil, el gate para la decisión con su huella y el dry-run no lo
        imprime ni escribe nada; con una variante, lo que sale lleva la forma de muestra del roster, no el del censo."""
        civil = 'Zz Civil Apellido'
        self.alias_en_censo(['don Zz Test'])
        self.lista_alias('don Zz Test')
        self.nombre_en_censo(civil)
        d = self.identidad()
        d['estado'] = {'action': 'set', 'status': 'investigado', 'fecha': '2025-04-01', 'doc': 'ATS_0000_2025.pdf',
                       'url': '', 'tipo': 'auto', 'nota': 'auto que lo cita como investigado'}
        error = (f'{d["_file"]}: nombre del censo de un seudónimo fuera de sus variantes '
                 f'(huella {seudonimos.sha16(civil)})')
        before, out = self.huellas_destinos(), io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            self.apply(copy.deepcopy(d), dry_run=True)
        self.assertIn(f'  ❌ {error}\n', out.getvalue())
        self.assertNotIn('civil apellido', out.getvalue().lower())
        self.assertEqual(self.huellas_destinos(), before)
        # El de un extremo seudónimo de una arista de un civil, igual.
        u_per = 'u' * 64
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-c-per', 'relationship', 'r:o-c-per', FULDO, 'MET_WITH', 'judicial', 0, 'h', '{}'))
        reunion = {'start': FULDO, 'end': SEUDO, 'type': 'MET_WITH', 'props': {
            'source_kind': 'judicial', 'source_doc': 'ATS_1111_2025.pdf', 'source_page': 2, 'source_quote': 'cita'}}
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")', (u_per, 'o-c-per', 'edge', '/edge@1', json.dumps(reunion)))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   ('E-civil-per', FULDO, 'entity', 1, 1, json.dumps([u_per]), 'routed'))
        db.commit()
        db.close()
        persona = decision(pack_id='E-civil-per', units=[unit(u_per, 'corregido', 'arista')],
                           edges=[edge_op(u_per, 'deny')], _file='E-civil-per.json')
        conn = A.connect(self.root)
        try:
            errores, _ = A.gate(conn, [persona], self.root, frozenset({SEUDO}), self.publicos, {}, {})
        finally:
            conn.close()
        self.assertEqual(errores, [f'E-civil-per.json: nombre del censo de un seudónimo fuera de sus variantes '
                                   f'(huella {seudonimos.sha16(civil)})'])
        self.nombre_en_censo('don Zz Test')
        plan = self.apply(copy.deepcopy(d), dry_run=True)
        self.assertEqual([fila['person'] for fila in plan['rows']['estado']], ['Zz Test'])

    def test_los_ignorables_y_las_url_decodificadas_en_el_apply(self):
        """AC85 (enmienda 22): un U+034F dentro del id de un seudónimo no lo esconde del gate (el motivo de un civil lo
        nombra; un seudónimo sin marca en el slug, al que tampoco encuentra la mención) ni de la salida; y una fuente
        del resumen con una letra cirílica codificada para el apply en el gate (AC105(a)), con la decisión y la huella,
        y tras materializar, con la posición."""
        solo = 'Person:zzsolo'
        (self.repo / seudonimos.PSEUDONYMS_REL).write_text(PSEUDONYMS + f'- id: {solo}\n  note: sintético\n',
                                                           encoding='utf-8')
        d = full_decision()
        d['summary']['reason'] = 'no es ' + solo.replace('zz', 'zz\u034f')
        self.write('E-ac85', d)
        before = self.huellas_destinos()
        codigo, out, err = self.cli('check')
        self.assertEqual(codigo, 1)
        self.assertIn(f'  ❌ E-ac85.json: prosa civil que nombra a un seudónimo judicial '
                      f'(huella {seudonimos.sha16(solo)})\n', out)
        self.assertNotIn('zzsolo', (out + err).lower())
        url = 'https://example.org/z%D0%B0-test'
        d = full_decision()
        d['summary'] = {'action': 'replace', 'resumen': 'Prosa nueva.', 'sources': [url], 'reason': 'r'}
        self.write('E-ac85', d)
        codigo, out, _ = self.cli('check')
        self.assertEqual(codigo, 1)
        self.assertIn(f'  ❌ E-ac85.json: {self.SOSPECHOSO.format(seudonimos.sha16(url))}\n', out)
        self.assertEqual(self.huellas_destinos(), before)
        self.assertIn(f'summaries[0]: {self.SOSPECHOSO.format(seudonimos.sha16(url))}', self.posterior(d))

    def test_el_nombre_ficticio_no_pasa_y_citas_lo_admite_por_su_huella(self):
        """AC90 (enmienda 24): «<seudónimo> es el nombre ficticio de <civil>» no lleva la palabra ni un id (AC87 no la
        caza), pero nombra al extremo civil de la regla: el gate para antes de materializar con la huella de la cita, y
        materializada, el control posterior. Una entrada de `citas:` con su huella (seudónimo y documento de la arista)
        la admite y el apply conserva la sección al reescribir la lista; con otra huella, no."""
        uid, cita = 'x' * 64, 'Zz Test es el nombre ficticio de Fuldo Fíctez'
        pack = self.arista_del_seudonimo(uid, FULDO, 'MET_WITH', cita=cita)
        d = self.write(pack, decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                                      edges=[edge_op(uid, 'retype', new_type='ASSOCIATED_WITH')]))
        error = f'{pack}.json: unidad {uid[:8]}: {self.COAPARICION.format(seudonimos.sha16(cita))}'
        codigo, out, err = self.cli('check')
        self.assertEqual((codigo, [linea for linea in out.splitlines() if '❌' in linea]), (1, [f'  ❌ {error}']))
        self.assertNotIn('garcía', (out + err).lower())
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        self.gate(copy.deepcopy(d))
        decisiones = [copy.deepcopy(d)]
        self.gate(*decisiones)   # anota `_pack`
        rows = A.materialize(decisiones, self.tag, self.upd, seudos, self.publicos)
        self.assertEqual(A._prosa_publica(rows, seudos, civiles, self.publicos, seudonimos.nombres_contexto(self.repo)),
                         [f'fix[0]: {self.COAPARICION.format(seudonimos.sha16(cita))}'])
        lista = seudonimos.cargar_permitidos(self.perm)
        plegada = ''.join(c for c in unicodedata.normalize('NFKD', cita) if unicodedata.category(c) != 'Mn').lower()
        su_huella = hashlib.sha256(' '.join(plegada.split()).encode('utf-8')).hexdigest()   # la cita normalizada
        entrada = {'seudonimo': SEUDO, 'sha256': su_huella, 'docs': ['ATS_A.pdf'],
                   'origen': 'decision-2026-10-07', 'motivo': 'coaparición judicial revisada; no identifica'}
        for huella, vale in (('0' * 64, False), (su_huella, True)):
            with self.subTest(vale=vale):
                self.perm.write_text(seudonimos.volcar_permitidos({**lista, 'citas': [{**entrada, 'sha256': huella}]}),
                                     encoding='utf-8')
                codigo, out, _ = self.cli('check')
                self.assertEqual(codigo, 0 if vale else 1, out)
                self.assertEqual('  ❌ ' + error in out, not vale)
        codigo, out, _ = self.cli('apply')   # con la de su huella, se aplica y la lista conserva `citas:`
        self.assertEqual(codigo, 0, out)
        self.assertEqual(seudonimos.cargar_permitidos(self.perm)['citas'], [entrada])

    def test_la_sociedad_por_su_nombre_completo_en_el_gate(self):
        """AC94/AC95 (enmienda 25, reproducciones de Codex) en el apply: la cita de la regla pública de una arista del
        seudónimo con una sociedad que la nombra por su razón social sin bigramas («Centinela SA»), con la palabra
        «seudónimo» o sin ella, para el gate antes de materializar (AC87 con el nombre de la sociedad cuando lleva la
        palabra; AC90 con la huella de la cita) y el control posterior; el CLI, sin escribir y sin los nombres."""
        centinela = 'Company:centinela-sa'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Centinela SA, entity_id: Company:centinela-sa, aliases: [Nimbo], '
            'role: Sociedad.}\n', encoding='utf-8')
        palabra, nombre = 'Centinela SA es la empresa de ese seudónimo', 'Centinela SA es la empresa de Zz Test'
        p1 = self.arista_del_seudonimo('x' * 64, centinela, 'ASSOCIATED_WITH', cita=palabra)
        p2 = self.arista_del_seudonimo('y' * 64, centinela, 'ASSOCIATED_WITH', cita=nombre)
        decisiones = [decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                               edges=[edge_op(uid, 'retype', new_type='WORKS_FOR')], _file=f'{pack}.json')
                      for pack, uid in ((p1, 'x' * 64), (p2, 'y' * 64))]
        errores = [f'{p1}.json: unidad {"x" * 8}: {self.AC78.format(seudonimos.sha16("Centinela SA"))}',
                   f'{p1}.json: unidad {"x" * 8}: {self.COAPARICION.format(seudonimos.sha16(palabra))}',
                   f'{p2}.json: unidad {"y" * 8}: {self.COAPARICION.format(seudonimos.sha16(nombre))}']
        for d in decisiones:
            self.write(d['_file'][:-len('.json')], d)
        before = self.huellas_destinos()
        codigo, out, err = self.cli('check')
        self.assertEqual((codigo, [linea for linea in out.splitlines() if '❌' in linea]),
                         (1, [f'  ❌ {e}' for e in errores]))
        self.assertNotIn('centinela', (out + err).lower())
        self.assertEqual(self.huellas_destinos(), before)
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        nombres = seudonimos.nombres_contexto(self.repo)
        conn = A.connect(self.root)
        try:
            self.assertEqual(A.gate(conn, copy.deepcopy(decisiones), self.root, seudos, self.publicos, civiles, None,
                                    nombres, [])[0], errores)
        finally:
            conn.close()
        self.gate(*decisiones)   # anota `_pack`
        rows = A.materialize(decisiones, self.tag, self.upd, seudos, self.publicos)
        self.assertEqual(A._prosa_publica(rows, seudos, civiles, self.publicos, nombres, []),
                         [f'fix[0]: {self.AC78.format(seudonimos.sha16("Centinela SA"))}',
                          f'fix[0]: {self.COAPARICION.format(seudonimos.sha16(palabra))}',
                          f'fix[1]: {self.COAPARICION.format(seudonimos.sha16(nombre))}'])

    @staticmethod
    def entrada_spec(cita, motivo='coaparición judicial revisada; no identifica', **extra):
        """Una entrada de `citas:` según la spec (AC90(b)): la huella de la cita normalizada (NFKD sin marcas, sin caja,
        espacios colapsados), del seudónimo de prueba y del documento de sus aristas; `extra`, otras claves."""
        plegada = ''.join(c for c in unicodedata.normalize('NFKD', cita) if unicodedata.category(c) != 'Mn').lower()
        return {'seudonimo': SEUDO, 'sha256': hashlib.sha256(' '.join(plegada.split()).encode('utf-8')).hexdigest(),
                **extra, 'docs': ['ATS_A.pdf'], 'origen': 'estado-2026-10-07', 'motivo': motivo}

    def test_el_prefijo_publicado_solo_lo_admite_su_entrada_exacta(self):
        """AC90(b) con la corrección de la enmienda 27 (sin prefijos): la regla que el apply escribe publica como
        `quote_contains` el prefijo de 60 caracteres de la cita del censo. La entrada de `citas:` de la cita completa no
        lo admite, tampoco con la lista `prefijos` del esquema anterior (enmienda 25): AC90 con la huella del prefijo,
        en el gate y en el control posterior. La entrada del texto exacto del `quote_contains` lo admite en los dos."""
        cita = 'Fuldo Fíctez se reunió en Madrid con el investigado al que el auto da el nombre de Zz Test'
        pack = self.arista_del_seudonimo('9' * 64, FULDO, 'MET_WITH', cita=cita)
        d = decision(pack_id=pack, entity=SEUDO, units=[unit('9' * 64, 'corregido', 'arista')],
                     edges=[edge_op('9' * 64, 'deny')], _file=f'{pack}.json')
        prefijo = A._quote_contains(cita)
        self.assertLess(len(prefijo), len(cita))
        plegada = ''.join(c for c in unicodedata.normalize('NFKD', cita) if unicodedata.category(c) != 'Mn').lower()
        normalizada = ' '.join(plegada.split())
        completa = self.entrada_spec(cita, prefijos=sorted(
            {hashlib.sha256(normalizada[:k].rstrip().encode('utf-8')).hexdigest()[:16]
             for k in range(40, len(normalizada)) if len(normalizada[:k].rstrip()) >= 40}))
        exacta = self.entrada_spec(prefijo)
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        nombres = seudonimos.nombres_contexto(self.repo)
        error = self.COAPARICION.format(seudonimos.sha16(prefijo))
        casos = (([], True), ([completa], True), ([exacta], False))
        conn = A.connect(self.root)
        try:
            for citas, falla in casos:
                with self.subTest(citas=[sorted(c) for c in citas]):
                    self.assertEqual(A.gate(conn, [copy.deepcopy(d)], self.root, seudos, self.publicos, civiles, None,
                                            nombres, citas)[0], [f'{pack}.json: unidad {"9" * 8}: {error}'] * falla)
        finally:
            conn.close()
        self.gate(d)   # anota `_pack`
        rows = A.materialize([d], self.tag, self.upd, seudos, self.publicos)
        # Spec 071 AC114: la deny que oculta al civil es opaca: no publica el prefijo y su forma no lo admite.
        self.assertEqual([sorted(r) for r in rows['deny']], [['reason', 'unidad']])
        self.assertIsNotNone(alcance.forma_deny({**rows['deny'][0], 'quote_contains': prefijo}))
        for citas, _ in casos:
            with self.subTest(posterior=[sorted(c) for c in citas]):
                self.assertEqual(A._prosa_publica(rows, seudos, civiles, self.publicos, nombres, citas), [])

    def test_la_cita_recortada_no_pasa_el_gate(self):
        """Reproducción 1 de Codex (enmienda 27) en el apply, con su corrección: con la entrada de `citas:` de «Nimbo
        ... es la empresa controlada por Zz Test, afirmación desmentida por el tribunal.» (la que genera
        `seudonimos.entrada_cita`), la arista del seudónimo con `Company:nimbo` cuya cita es la recortada «Nimbo ... es
        la empresa controlada por Zz Test» (46 caracteres: la regla la publica entera como `quote_contains`) es AC90 con
        su huella en el gate y en el control posterior; la de la revisada, cuya regla publica sus 60 primeros
        caracteres, también. Ningún selector se admite por un prefijo."""
        nimbo = 'Company:nimbo'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Nimbo, entity_id: Company:nimbo, role: Sociedad.}\n', encoding='utf-8')
        revisada = 'Nimbo ... es la empresa controlada por Zz Test, afirmación desmentida por el tribunal.'
        recortada = 'Nimbo ... es la empresa controlada por Zz Test'
        p1 = self.arista_del_seudonimo('x' * 64, nimbo, 'ASSOCIATED_WITH', cita=recortada)
        p2 = self.arista_del_seudonimo('y' * 64, nimbo, 'ASSOCIATED_WITH', cita=revisada)
        decisiones = [decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                               edges=[edge_op(uid, 'retype', new_type='WORKS_FOR')], _file=f'{pack}.json')
                      for pack, uid in ((p1, 'x' * 64), (p2, 'y' * 64))]
        entrada = seudonimos.entrada_cita(revisada, SEUDO, ['ATS_A.pdf'], 'estado-2026-10-08',
                                          'cita recompuesta con elipsis marcada, revisada contra el documento judicial '
                                          'público')
        selectores = [recortada, A._quote_contains(revisada)]
        self.assertEqual(selectores[0], recortada)
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        nombres = seudonimos.nombres_contexto(self.repo)
        conn = A.connect(self.root)
        try:
            errores = A.gate(conn, copy.deepcopy(decisiones), self.root, seudos, self.publicos, civiles, None, nombres,
                             [entrada])[0]
        finally:
            conn.close()
        self.assertEqual(errores, [f'{pack}.json: unidad {uid * 8}: {self.COAPARICION.format(seudonimos.sha16(texto))}'
                                   for pack, uid, texto in ((p1, 'x', selectores[0]), (p2, 'y', selectores[1]))])
        self.gate(*decisiones)   # anota `_pack`
        rows = A.materialize(decisiones, self.tag, self.upd, seudos, self.publicos)
        self.assertEqual(A._prosa_publica(rows, seudos, civiles, self.publicos, nombres, [entrada]),
                         [f'fix[{i}]: {self.COAPARICION.format(seudonimos.sha16(texto))}'
                          for i, texto in enumerate(selectores)])

    def test_la_cita_con_elipsis_solo_por_citas_en_el_gate(self):
        """AC97/AC98 (enmienda 26, reproducciones de Codex) en el apply, con las entradas del árbol y del snapshot: la
        cita de la regla pública de una arista del seudónimo con `Company:nimbo` con una elipsis marcada («Nimbo ... es
        la empresa de Zz Test») o que acaba a media palabra de su documento («Zz Test cobró de Nimbo») para el gate
        antes de materializar (AC90 con la huella de la cita) y el control posterior. La entrada de `citas:` de la cita
        recompuesta (la plantilla cerrada nueva, su seudónimo y su documento) la admite y el `check`, que valida la
        lista, pasa; la de otro seudónimo o de otro documento, no."""
        nimbo = 'Company:nimbo'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Nimbo, entity_id: Company:nimbo, role: Sociedad.}\n', encoding='utf-8')
        elidida, cortada = 'Nimbo ... es la empresa de Zz Test', 'Zz Test cobró de Nimbo'
        p1 = self.arista_del_seudonimo('x' * 64, nimbo, 'ASSOCIATED_WITH', cita=elidida)
        p2 = self.arista_del_seudonimo('y' * 64, nimbo, 'ASSOCIATED_WITH', cita=cortada)
        decisiones = [decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                               edges=[edge_op(uid, 'retype', new_type='WORKS_FOR')], _file=f'{pack}.json')
                      for pack, uid in ((p1, 'x' * 64), (p2, 'y' * 64))]
        errores = [f'{p1}.json: unidad {"x" * 8}: {self.COAPARICION.format(seudonimos.sha16(elidida))}',
                   f'{p2}.json: unidad {"y" * 8}: {self.COAPARICION.format(seudonimos.sha16(cortada))}']
        plegada = ''.join(c for c in unicodedata.normalize('NFKD', elidida) if unicodedata.category(c) != 'Mn').lower()
        entrada = {'seudonimo': SEUDO, 'sha256': hashlib.sha256(' '.join(plegada.split()).encode('utf-8')).hexdigest(),
                   'docs': ['ATS_A.pdf'], 'origen': 'estado-2026-10-08',
                   'motivo': 'cita recompuesta con elipsis marcada, revisada contra el documento judicial público'}
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        nombres = seudonimos.nombres_contexto(self.repo)
        conn = A.connect(self.root)
        try:
            for citas, esperado in (([], errores), ([entrada], errores[1:]),
                                    ([{**entrada, 'seudonimo': 'Person:zz-otro-seudonimo-test'}], errores),
                                    ([{**entrada, 'docs': ['ATS_B.pdf']}], errores)):
                with self.subTest(citas=citas):
                    self.assertEqual(A.gate(conn, copy.deepcopy(decisiones), self.root, seudos, self.publicos, civiles,
                                            None, nombres, citas)[0], esperado)
        finally:
            conn.close()
        self.gate(*decisiones)   # anota `_pack`
        rows = A.materialize(decisiones, self.tag, self.upd, seudos, self.publicos)
        posterior = [f'fix[0]: {self.COAPARICION.format(seudonimos.sha16(elidida))}',
                     f'fix[1]: {self.COAPARICION.format(seudonimos.sha16(cortada))}']
        self.assertEqual(A._prosa_publica(rows, seudos, civiles, self.publicos, nombres, []), posterior)
        self.assertEqual(A._prosa_publica(rows, seudos, civiles, self.publicos, nombres, [entrada]), posterior[1:])
        # El CLI con la decisión de la cita elidida: sin la entrada, `check` para con su huella y sin el texto; con ella
        # en la lista (que el `check` valida antes de materializar), pasa.
        self.write(p1, decisiones[0])
        codigo, out, err = self.cli('check')
        self.assertEqual((codigo, [linea for linea in out.splitlines() if '❌' in linea]), (1, [f'  ❌ {errores[0]}']))
        self.assertNotIn('nimbo', (out + err).lower())
        lista = seudonimos.cargar_permitidos(self.perm)
        self.perm.write_text(seudonimos.volcar_permitidos({**lista, 'citas': [entrada]}), encoding='utf-8')
        codigo, out, _ = self.cli('check')
        self.assertEqual(codigo, 0, out)

    def test_la_cita_con_elipsis_sin_documento_no_se_admite(self):
        """AC101 (enmienda 27) en el apply, como en los otros caminos (`seudonimos.cita_admitida`): la regla pública de
        una arista del seudónimo con `Company:nimbo` sin documento cuya cita lleva una elipsis no se puede contrastar
        con los `docs` de su entrada de `citas:`: el gate la para con la huella de la cita (además de AC30, la cita sin
        `doc`) y el control posterior también. La misma arista con documento pasa con la entrada."""
        nimbo = 'Company:nimbo'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Nimbo, entity_id: Company:nimbo, role: Sociedad.}\n', encoding='utf-8')
        elidida = 'Nimbo ... es la empresa de Zz Test'
        p1 = self.arista_del_seudonimo('x' * 64, nimbo, 'ASSOCIATED_WITH', cita=elidida, doc=None)
        p2 = self.arista_del_seudonimo('y' * 64, nimbo, 'ASSOCIATED_WITH', cita=elidida)
        decisiones = [decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                               edges=[edge_op(uid, 'retype', new_type='WORKS_FOR')], _file=f'{pack}.json')
                      for pack, uid in ((p1, 'x' * 64), (p2, 'y' * 64))]
        plegada = ''.join(c for c in unicodedata.normalize('NFKD', elidida) if unicodedata.category(c) != 'Mn').lower()
        entrada = {'seudonimo': SEUDO, 'sha256': hashlib.sha256(' '.join(plegada.split()).encode('utf-8')).hexdigest(),
                   'docs': ['ATS_A.pdf'], 'origen': 'estado-2026-10-08',
                   'motivo': 'cita recompuesta con elipsis marcada, revisada contra el documento judicial público'}
        coaparicion = self.COAPARICION.format(seudonimos.sha16(elidida))
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        nombres = seudonimos.nombres_contexto(self.repo)
        conn = A.connect(self.root)
        try:
            errores = A.gate(conn, copy.deepcopy(decisiones), self.root, seudos, self.publicos, civiles, None, nombres,
                             [entrada])[0]
        finally:
            conn.close()
        self.assertEqual(errores, [f'{p1}.json: unidad {"x" * 8}: {coaparicion}',
                                   f'{p1}.json: unidad {"x" * 8}: regla con extremo seudónimo con cita '
                                   '(`quote_contains`) y sin `doc`'])
        self.gate(*decisiones)   # anota `_pack`
        rows = A.materialize(decisiones, self.tag, self.upd, seudos, self.publicos)
        self.assertEqual(A._prosa_publica(rows, seudos, civiles, self.publicos, nombres, [entrada]),
                         [f'fix[0]: {coaparicion}'])

    def test_las_reproducciones_de_la_enmienda_28_en_el_gate(self):
        """AC104/AC105 (enmienda 28) en el apply, con `citas: []`: la regla pública de la arista del seudónimo con
        `Company:nimbo` cuya cita parte el nombre («Nim-\\nbo ... es la empresa de Zz Test») es AC90 en el gate y en el
        control posterior; el `alias_deny` de un civil con el seudónimo partido («Zz Te-st (seudónimo TS)») o escrito
        con homógrafos griegos y cirílicos lo nombra en el gate (AC60 y su prosa, AC69) y en el control posterior (la
        fila civil), y el segundo es además sospechoso en los dos (AC105(a)). Las huellas, nunca los textos."""
        nimbo = 'Company:nimbo'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Nimbo, entity_id: Company:nimbo, role: Sociedad.}\n', encoding='utf-8')
        partida = 'Nim-\nbo ... es la empresa de Zz Test'
        pack = self.arista_del_seudonimo('x' * 64, nimbo, 'ASSOCIATED_WITH', cita=partida)
        regla = decision(pack_id=pack, entity=SEUDO, units=[unit('x' * 64, 'corregido', 'arista')],
                         edges=[edge_op('x' * 64, 'retype', new_type='WORKS_FOR')], _file=f'{pack}.json')
        alias = ('Zz Te-st (seudónimo TS)', '\u0396\u0396 \u03a4\u0395\u0405\u03a4 (seudónimo TS)')
        civil = full_decision()
        civil['alias_deny'] = list(alias)
        civil['_file'] = 'E-ac104.json'
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        nombres = seudonimos.nombres_contexto(self.repo)
        conn = A.connect(self.root)
        try:
            errores = A.gate(conn, [copy.deepcopy(regla), copy.deepcopy(civil)], self.root, seudos, self.publicos,
                             civiles, None, nombres, [])[0]
        finally:
            conn.close()
        self.assertEqual(errores, [
            f'{pack}.json: unidad {"x" * 8}: {self.COAPARICION.format(seudonimos.sha16(partida))}',
            *(f'E-ac104.json: alias_deny: una entrada nombra a un seudónimo judicial (huella {seudonimos.sha16(a)})'
              for a in alias),
            f'E-ac104.json: prosa civil que nombra a un seudónimo judicial (huella {seudonimos.sha16(SEUDO)})',
            f'E-ac104.json: {self.SOSPECHOSO.format(seudonimos.sha16(alias[1]))}'])
        self.assertEqual(self.posterior(regla), [f'fix[0]: {self.COAPARICION.format(seudonimos.sha16(partida))}'])
        # El motivo de cada fila `alias_deny` repite su alias: sale con su propia huella, y todo, en esas filas.
        nombra = f'prosa civil que nombra a un seudónimo judicial (huella {seudonimos.sha16(SEUDO)})'
        posterior = self.posterior(civil)
        for error in (f'alias_deny[0]: {nombra}', f'alias_deny[1]: {nombra}',
                      f'alias_deny[1]: {self.SOSPECHOSO.format(seudonimos.sha16(alias[1]))}'):
            self.assertIn(error, posterior)
        self.assertTrue(all(error.startswith(('alias_deny[0]: ', 'alias_deny[1]: ')) for error in posterior))

    def test_las_reproducciones_de_la_ampliacion_28_en_el_gate(self):
        """AC106/AC107 (ampliación de la enmienda 28) en el apply, con `citas: []`: la regla pública de la arista del
        seudónimo con `Company:nimbo` cuya cita escribe el nombre con un cero («Nimb0 es la empresa de Zz Test») es AC90
        en el gate y en el control posterior; el `alias_deny` de un civil con un dígito homógrafo en el seudónimo y la
        marca («Zz T3st (seudónimo)») lo nombra en los dos (AC60 y AC69 en el gate; la fila civil después), y el de una
        «O» armenia es sospechoso en los dos (AC106). Las huellas, nunca los textos."""
        nimbo = 'Company:nimbo'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Nimbo, entity_id: Company:nimbo, role: Sociedad.}\n', encoding='utf-8')
        cero = 'Nimb0 es la empresa de Zz Test'
        pack = self.arista_del_seudonimo('x' * 64, nimbo, 'ASSOCIATED_WITH', cita=cero)
        regla = decision(pack_id=pack, entity=SEUDO, units=[unit('x' * 64, 'corregido', 'arista')],
                         edges=[edge_op('x' * 64, 'retype', new_type='WORKS_FOR')], _file=f'{pack}.json')
        alias = ('Zz T3st (seudónimo)', 'Yy N\u0555mbre')
        civil = full_decision()
        civil['alias_deny'] = list(alias)
        civil['_file'] = 'E-ac106.json'
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        nombres = seudonimos.nombres_contexto(self.repo)
        conn = A.connect(self.root)
        try:
            errores = A.gate(conn, [copy.deepcopy(regla), copy.deepcopy(civil)], self.root, seudos, self.publicos,
                             civiles, None, nombres, [])[0]
        finally:
            conn.close()
        self.assertEqual(errores, [
            f'{pack}.json: unidad {"x" * 8}: {self.COAPARICION.format(seudonimos.sha16(cero))}',
            'E-ac106.json: alias_deny: una entrada nombra a un seudónimo judicial '
            f'(huella {seudonimos.sha16(alias[0])})',
            f'E-ac106.json: prosa civil que nombra a un seudónimo judicial (huella {seudonimos.sha16(SEUDO)})',
            f'E-ac106.json: {self.SOSPECHOSO.format(seudonimos.sha16(alias[1]))}'])
        self.assertEqual(self.posterior(regla), [f'fix[0]: {self.COAPARICION.format(seudonimos.sha16(cero))}'])
        # El motivo de cada fila `alias_deny` repite su alias: sale con su propia huella, y todo, en esas filas.
        posterior = self.posterior(civil)
        nombra = f'prosa civil que nombra a un seudónimo judicial (huella {seudonimos.sha16(SEUDO)})'
        armenio = self.SOSPECHOSO.format(seudonimos.sha16(alias[1]))
        for error in (f'alias_deny[0]: {nombra}', f'alias_deny[1]: {armenio}'):
            self.assertIn(error, posterior)
        self.assertTrue(all(error.startswith(('alias_deny[0]: ', 'alias_deny[1]: ')) for error in posterior))

    def test_las_reproducciones_de_la_segunda_ampliacion_28_en_el_gate(self):
        """AC108 (segunda ampliación de la enmienda 28) en el apply, con `citas: []`: la regla pública de la arista del
        seudónimo con `Company:nimbo` cuya cita lleva un cinco árabe-índico (U+0665) es sospechosa en el gate y en el
        control posterior; la de la arista con el civil Yy Sánchez cuya cita lo escribe «Yy Sánche2» es AC90 en los dos;
        el `alias_deny` de un civil con un uno devanagari (U+0967) es sospechoso en los dos. Las huellas, nunca los
        textos."""
        sanchez = 'Person:yy-sanchez'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Nimbo, entity_id: Company:nimbo, role: Sociedad.}\n'
            f'- {{type: Person, name: Yy Sánchez, entity_id: {sanchez}, role: Testigo.}}\n', encoding='utf-8')
        arabe, dos = 'Nimb\u0665 es la empresa de Zz Test', 'Zz Test se reunió con Yy Sánche2'
        p1 = self.arista_del_seudonimo('x' * 64, 'Company:nimbo', 'ASSOCIATED_WITH', cita=arabe)
        p2 = self.arista_del_seudonimo('y' * 64, sanchez, 'MET_WITH', cita=dos)
        reglas = [decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                           edges=[edge_op(uid, 'retype', new_type='WORKS_FOR')], _file=f'{pack}.json')
                  for pack, uid in ((p1, 'x' * 64), (p2, 'y' * 64))]
        alias = 'N\u0967mbo SL'
        civil = full_decision()
        civil['alias_deny'] = [alias]
        civil['_file'] = 'E-ac108.json'
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        nombres = seudonimos.nombres_contexto(self.repo)
        conn = A.connect(self.root)
        try:
            errores = A.gate(conn, [*copy.deepcopy(reglas), copy.deepcopy(civil)], self.root, seudos, self.publicos,
                             civiles, None, nombres, [])[0]
        finally:
            conn.close()
        self.assertEqual(errores, [
            f'{p1}.json: unidad {"x" * 8}: {self.SOSPECHOSO.format(seudonimos.sha16(arabe))}',
            f'{p2}.json: unidad {"y" * 8}: {self.COAPARICION.format(seudonimos.sha16(dos))}',
            f'E-ac108.json: {self.SOSPECHOSO.format(seudonimos.sha16(alias))}'])
        self.assertEqual(self.posterior(reglas[0]), [f'fix[0]: {self.SOSPECHOSO.format(seudonimos.sha16(arabe))}'])
        self.assertEqual(self.posterior(reglas[1]), [f'fix[0]: {self.COAPARICION.format(seudonimos.sha16(dos))}'])
        # El motivo de la fila `alias_deny` repite su alias: sale con su propia huella, y todo, en esa fila.
        posterior = self.posterior(civil)
        self.assertIn(f'alias_deny[0]: {self.SOSPECHOSO.format(seudonimos.sha16(alias))}', posterior)
        self.assertTrue(all(error.startswith('alias_deny[0]: ') for error in posterior))

    def test_las_reproducciones_de_la_enmienda_29_en_el_gate(self):
        """AC109 (enmienda 29) en el apply, con `citas: []`: la regla pública de la arista del seudónimo con
        `Company:nimbo` cuya cita escribe «Nimb-0 es la empresa de Zz Test» es AC90 en el gate y en el control
        posterior, y la de la arista con el civil Nimbo Stratus cuya cita escribe «Zz Test se reunió con
        Nim-bo-Stratus», también; el `alias_deny` de un civil con «Zz-Te-st (seudónimo)» nombra al seudónimo en los dos
        (AC60 y AC69 en el gate; la fila civil después). Controles: las citas «Nimb - 0 es la empresa de Zz Test» y
        «según el art. 31-bis, Zz Test pagó» no nombran a nadie. Las huellas, nunca los textos."""
        stratus = 'Person:yy-nimbo-stratus'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Nimbo, entity_id: Company:nimbo, role: Sociedad.}\n'
            f'- {{type: Person, name: Nimbo Stratus, entity_id: {stratus}, role: Testigo.}}\n', encoding='utf-8')
        cero, partida = 'Nimb-0 es la empresa de Zz Test', 'Zz Test se reunió con Nim-bo-Stratus'
        controles = ('Nimb - 0 es la empresa de Zz Test', 'según el art. 31-bis, Zz Test pagó')
        aristas = [('x' * 64, 'Company:nimbo', 'ASSOCIATED_WITH', cero), ('y' * 64, stratus, 'MET_WITH', partida),
                   *((c * 64, 'Company:nimbo', 'ASSOCIATED_WITH', cita) for c, cita in zip('zv', controles))]
        reglas = []
        for uid, otro, tipo, cita in aristas:
            pack = self.arista_del_seudonimo(uid, otro, tipo, cita=cita)
            reglas.append(decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                                   edges=[edge_op(uid, 'retype', new_type='WORKS_FOR')], _file=f'{pack}.json'))
        alias = 'Zz-Te-st (seudónimo)'
        civil = full_decision()
        civil['alias_deny'] = [alias]
        civil['_file'] = 'E-ac109.json'
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        nombres = seudonimos.nombres_contexto(self.repo)
        conn = A.connect(self.root)
        try:
            errores = A.gate(conn, [*copy.deepcopy(reglas), copy.deepcopy(civil)], self.root, seudos, self.publicos,
                             civiles, None, nombres, [])[0]
        finally:
            conn.close()
        nombra = f'prosa civil que nombra a un seudónimo judicial (huella {seudonimos.sha16(SEUDO)})'
        self.assertEqual(errores, [
            f'E-{"x" * 8}.json: unidad {"x" * 8}: {self.COAPARICION.format(seudonimos.sha16(cero))}',
            f'E-{"y" * 8}.json: unidad {"y" * 8}: {self.COAPARICION.format(seudonimos.sha16(partida))}',
            f'E-ac109.json: alias_deny: una entrada nombra a un seudónimo judicial (huella {seudonimos.sha16(alias)})',
            f'E-ac109.json: {nombra}'])
        self.assertEqual(self.posterior(reglas[0]), [f'fix[0]: {self.COAPARICION.format(seudonimos.sha16(cero))}'])
        self.assertEqual(self.posterior(reglas[1]),
                         [f'fix[0]: {self.COAPARICION.format(seudonimos.sha16(partida))}'])
        for regla in reglas[2:]:
            self.assertEqual(self.posterior(regla), [])
        posterior = self.posterior(civil)
        self.assertIn(f'alias_deny[0]: {nombra}', posterior)
        self.assertTrue(all(error.startswith('alias_deny[0]: ') for error in posterior))

    def test_el_alias_de_ocho_tokens_ambiguos_en_el_gate(self):
        """AC112 (enmienda 29) en el apply: el `alias_deny` de un civil con ocho «a1111» (17^8 combinaciones antes)
        termina el gate en menos de un segundo sin error, y el control posterior tampoco lo para."""
        en_hijo_el_cuerpo(self, 'cuerpo_el_alias_de_ocho_tokens_ambiguos_en_el_gate')

    def cuerpo_el_alias_de_ocho_tokens_ambiguos_en_el_gate(self):
        """El cuerpo de la prueba, que se ejecuta en un hijo (`en_hijo_el_cuerpo`)."""
        civil = full_decision()
        civil['alias_deny'] = [' '.join(['a1111'] * 8)]
        civil['_file'] = 'E-ac112.json'
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        conn = A.connect(self.root)
        try:
            with presupuesto(self, 1.0):
                errores = A.gate(conn, [copy.deepcopy(civil)], self.root, seudos, self.publicos, civiles, None,
                                 seudonimos.nombres_contexto(self.repo), [])[0]
        finally:
            conn.close()
        self.assertEqual(errores, [])
        with presupuesto(self, 1.0):
            self.assertEqual(self.posterior(civil), [])

    def test_un_nombre_de_seis_guiones_en_el_gate(self):
        """AC113 (ampliación de la enmienda 29) en el apply, con `citas: []`: la regla pública de la arista del
        seudónimo con `Company:nimbo` cuya cita escribe al civil «Yy-Nimbo-Stratus-Cirro-Cumulo-Alto-Nube» mezclando
        espacios y guiones lo nombra (AC78, con «Stratus Cirro», otro civil, dentro) en el gate y en el control
        posterior; la de la arista con ese civil y la misma cita exime a «Stratus Cirro» y es AC90 en los dos. Las
        huellas, nunca los textos."""
        seis, cita = 'Yy-Nimbo-Stratus-Cirro-Cumulo-Alto-Nube', 'Zz Test pagó a Yynimbo Stratus-Cirro Cumulo-Alto Nube'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Nimbo, entity_id: Company:nimbo, role: Sociedad.}\n'
            f'- {{type: Person, name: {seis}, entity_id: Person:yy-seis, role: Testigo.}}\n'
            '- {type: Person, name: Stratus Cirro, entity_id: Person:yy-stratus-cirro, role: Testigo.}\n',
            encoding='utf-8')
        reglas = []
        for uid, otro, tipo in (('x' * 64, 'Company:nimbo', 'ASSOCIATED_WITH'),
                                ('y' * 64, 'Person:yy-seis', 'MET_WITH')):
            pack = self.arista_del_seudonimo(uid, otro, tipo, cita=cita)
            reglas.append(decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                                   edges=[edge_op(uid, 'retype', new_type='WORKS_FOR')], _file=f'{pack}.json'))
        seudos = frozenset({SEUDO})
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        nombres = seudonimos.nombres_contexto(self.repo)
        conn = A.connect(self.root)
        try:
            errores = A.gate(conn, copy.deepcopy(reglas), self.root, seudos, self.publicos, civiles, None, nombres,
                             [])[0]
        finally:
            conn.close()
        nombran = [self.AC78.format(seudonimos.sha16(n)) for n in ('Stratus Cirro', seis)]
        coaparicion = self.COAPARICION.format(seudonimos.sha16(cita))
        self.assertEqual(errores, [*(f'E-{"x" * 8}.json: unidad {"x" * 8}: {e}' for e in nombran),
                                   f'E-{"y" * 8}.json: unidad {"y" * 8}: {coaparicion}'])
        self.assertEqual(self.posterior(reglas[0]), [f'fix[0]: {e}' for e in nombran])
        self.assertEqual(self.posterior(reglas[1]), [f'fix[0]: {coaparicion}'])

    def gate_del_repo(self, *decisiones, export=None, citas=()):
        """`gate` con todo lo del repo de prueba (roster, contexto, registro de documentos) y la lista `citas` (con las
        del fixture, AC148), como lo llama el apply; con `export`, la vía (a) y los nombres de un export
        (`seudonimos.literal_del_export`)."""
        seudos = seudonimos.ids_seudonimos(self.repo / seudonimos.PSEUDONYMS_REL)
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        conn = A.connect(self.root)
        try:
            return A.gate(conn, list(decisiones), self.root, seudos, self.publicos, civiles, None,
                          seudonimos.nombres_contexto(self.repo), [*self.citas_fixture, *citas],
                          seudonimos.censo_documentos(self.repo, self.publicos), export=export)[0]
        finally:
            conn.close()

    def posterior_del_repo(self, decision, export=None, citas=()):
        """El control posterior de `decision` ya pasada por `gate_del_repo` (que anota `_pack`, `_pruebas` y
        `_conservadas`), con el export y la lista `citas` si los hay."""
        seudos = seudonimos.ids_seudonimos(self.repo / seudonimos.PSEUDONYMS_REL)
        rows = A.materialize([decision], self.tag, self.upd, seudos, self.publicos)
        civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(self.repo), seudos)
        return A._prosa_publica(rows, seudos, civiles, self.publicos, seudonimos.nombres_contexto(self.repo),
                                [*self.citas_fixture, *citas], seudonimos.censo_documentos(self.repo, self.publicos),
                                export=export, conservadas=decision.get('_conservadas'))

    def export_con_chunk(self, texto, doc='ATS_A.pdf'):
        """Un export mínimo (`nodes.jsonl`) con el Document `doc` y un Chunk suyo con `texto`."""
        snap = self.base / 'snap'
        snap.mkdir(exist_ok=True)
        nodos = [{'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': doc}},
                 {'ref': 'c', 'labels': ['Chunk'], 'props': {'doc_id': doc, 'text': texto}}]
        (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                          encoding='utf-8')
        (snap / 'relationships.jsonl').write_text('', encoding='utf-8')
        return snap

    def test_un_roster_que_no_se_lee_para_el_apply_con_exit_2(self):
        """AC114 (enmienda 30, reproducción de Codex) en el apply: con `pseudonyms: null`, una lista vacía, una fila sin
        id o una raíz que no es un mapa, `check` no trata el roster como «ningún seudónimo» (antes: la decisión pasaba
        y la arista con la sociedad se conservaba sin la política): para con exit 2 y el motivo, sin valores ni escribir
        nada."""
        self.write('E-ac114', self.seudo())
        antes = self.destinos()
        for caso, texto in (('nulo', 'pseudonyms: null\n'), ('vacío', 'pseudonyms: []\n'),
                            ('sin id', PSEUDONYMS + '- note: sin id\n'), ('raíz lista', f'- id: {SEUDO}\n')):
            (self.repo / seudonimos.PSEUDONYMS_REL).write_text(texto, encoding='utf-8')
            with self.subTest(caso):
                codigo, out, err = self.cli('check')
                self.assertEqual(codigo, 2, out + err)
                self.assertIn('  ❌ pseudonyms.yaml: roster inválido (spec 070): ', out)
                self.assertNotIn('zz-seudonimo', (out + err).lower())
        self.assertEqual(self.destinos(), antes)

    def test_las_reproducciones_de_la_enmienda_30_en_el_gate(self):
        """AC116/AC117 (enmienda 30, reproducciones de Codex) en el apply, con `citas: []`: la regla pública de la
        arista del seudónimo con su sociedad cuya cita la nombra por «Ni-mb-o», por «ZX» o como «Nimb02000» (su
        nombre, «Nimbo2000») es AC90 en el gate y en el control posterior (antes, verde en los dos). Controles: la cita
        con «B01959998», «COVID-19» y «2026» no nombra a «Nimbo2000», ni la que nombra a «S.L.» a su sociedad. Y la que
        escribe «Nimb0-5tratus» nombra a «Nimbostratus» (el guion entre dos cifras de una palabra con letras une). Las
        huellas, nunca los textos."""
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Ni-mb-o, entity_id: Company:zz-nimbo, role: Sociedad.}\n'
            '- {type: Company, name: ZX, entity_id: Company:zz-zx, role: Sociedad.}\n'
            '- {type: Company, name: Nimbo2000, entity_id: Company:zz-dosmil, role: Sociedad.}\n'
            '- {type: Company, name: S.L., entity_id: Company:zz-generica, role: Sociedad.}\n'
            '- {type: Company, name: Nimbostratus, entity_id: Company:zz-estrato, role: Sociedad.}\n', encoding='utf-8')
        aristas = [('p' * 64, 'Company:zz-nimbo', 'Ni-mb-o pertenece a Zz Test.'),
                   ('q' * 64, 'Company:zz-zx', 'ZX pertenece a Zz Test.'),
                   ('r' * 64, 'Company:zz-dosmil', 'Nimb02000 pertenece a Zz Test.'),
                   ('o' * 64, 'Company:zz-estrato', 'Nimb0-5tratus pertenece a Zz Test.'),
                   ('s' * 64, 'Company:zz-dosmil', 'Con NIF B01959998, en la COVID-19 y en 2026, Zz Test pagó.'),
                   ('t' * 64, 'Company:zz-generica', 'La S.L. pertenece a Zz Test.')]
        reglas = []
        for uid, otro, cita in aristas:
            pack = self.arista_del_seudonimo(uid, otro, 'ASSOCIATED_WITH', cita=cita)
            reglas.append(decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                                   edges=[edge_op(uid, 'retype', new_type='WORKS_FOR')], _file=f'{pack}.json'))
        errores = self.gate_del_repo(*copy.deepcopy(reglas))
        self.assertEqual(errores, [f'E-{uid[:8]}.json: unidad {uid[:8]}: {self.COAPARICION.format(seudonimos.sha16(c))}'
                                   for uid, _, c in aristas[:4]])
        for regla, (_, _, cita) in zip(reglas, aristas):
            d = copy.deepcopy(regla)
            self.gate_del_repo(d)
            esperado = [f'fix[0]: {self.COAPARICION.format(seudonimos.sha16(cita))}'] if regla in reglas[:4] else []
            self.assertEqual(self.posterior_del_repo(d), esperado)

    def test_el_roster_no_exime_a_un_civil_en_el_gate(self):
        """AC118 (enmienda 30, reproducción de Codex) en el apply: (a) un civil del contexto declarado también en el
        roster con datos civiles en figures.yaml para `check` (exit 1) con la línea del roster y la huella; (b) con
        su fila de figures.yaml como mero ancla (sin rol), la nota del estado de otro seudónimo que lo nombra por su
        nombre completo es AC78 en el gate y en el control posterior (antes: el roster lo sacaba del índice); y (c),
        AC134 (enmienda 32), el nombre largo que el contexto da a un seudónimo, que nada denuncia en la misma ejecución,
        cuenta en su propia nota y en la de otro, como su nombre de pila de dos palabras. Sin valores."""
        lucio = 'Person:yy-lucio-verde-lago'
        roster = self.repo / seudonimos.PSEUDONYMS_REL
        roster.write_text(PSEUDONYMS + f'- id: {lucio}\n  note: seudónimo sintético\n', encoding='utf-8')
        figuras = self.repo / 'pipeline/data/context/figures.yaml'
        figuras.write_text(f'entities:\n- {{type: Person, name: Yy Lucio Verde Lago, entity_id: {lucio}, '
                           'role: Administrador de la sociedad.}\n', encoding='utf-8')
        self.write('E-ac118', self.seudo())
        codigo, out, err = self.cli('check')
        self.assertEqual(codigo, 1, out + err)
        self.assertIn(f'  ❌ {seudonimos.PSEUDONYMS_REL}:4: el id de una fila del roster está en context/figures.yaml '
                      f'con datos civiles (huella {seudonimos.sha16(lucio)})\n', out)
        self.assertNotIn('lucio', (out + err).lower())
        (self.root / 'esc/decisions/E-ac118.json').unlink()
        figuras.write_text(f'entities:\n- {{type: Person, name: Yy Lucio Verde Lago, entity_id: {lucio}}}\n'
                           f'- {{type: Person, name: el investigado Sr. Zz Test, entity_id: {SEUDO}}}\n',
                           encoding='utf-8')
        nota = 'Su identidad civil es Yy Lucio Verde Lago. Lo dijo el investigado Sr. Zz Test.'
        estado = {'action': 'set', 'status': 'investigado', 'fecha': '2026-04-11', 'doc': 'ATS_0000_2025.pdf',
                  'url': '', 'tipo': 'auto', 'nota': nota}
        d = decision(pack_id=PS, entity=SEUDO, units=[unit(u) for u in (U_S_ORG, U_S_SOC, U_S_ID)],
                     alias_deny=['el consejero Zz Test'], estado=estado, _file=f'{PS}.json')
        d['units'][2] = unit(U_S_ID, 'corregido', 'alias')
        # el nombre del índice (`_nombres_en`) y luego el de pila compuesto del otro seudónimo (`_pilas_en`)
        huellas = [seudonimos.sha16(n) for n in ('el investigado Sr. Zz Test', 'yy lucio verde lago')]
        self.assertEqual(self.gate_del_repo(copy.deepcopy(d)), [f'{PS}.json: {self.AC78.format(h)}' for h in huellas])
        conservada = copy.deepcopy(d)
        self.gate_del_repo(conservada)
        self.assertEqual(self.posterior_del_repo(conservada), [f'estado[0]: {self.AC78.format(h)}' for h in huellas])
        otro = 'Person:zz-otro-seudonimo-test'
        roster.write_text(PSEUDONYMS + f'- id: {otro}\n  note: seudónimo sintético\n', encoding='utf-8')
        figuras.write_text(f'entities:\n- {{type: Person, name: el investigado Sr. Zz Test, entity_id: {SEUDO}}}\n',
                           encoding='utf-8')
        propia = copy.deepcopy(d)
        propia['estado']['nota'] = 'Lo dijo el investigado Sr. Zz Test.'
        self.assertEqual(self.gate_del_repo(propia),
                         [f'{PS}.json: {self.AC78.format(seudonimos.sha16("el investigado Sr. Zz Test"))}'])
        uid = 'u' * 64
        pack = 'E-zzotro01'
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-otro', 'relationship', 'r:otro', otro, 'ACCUSED_OF', 'judicial', 0, 'h', '{}'))
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")',
                   (uid, 'o-otro', 'edge', '/edge@1', json.dumps({'start': otro, 'end': 'Crime:zz-delito-test',
                                                                  'type': 'ACCUSED_OF', 'props': {
                                                                      'source_kind': 'judicial',
                                                                      'source_doc': 'ATS_A.pdf', 'source_page': 2,
                                                                      'source_quote': 'cita sintética'}})))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   (pack, otro, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.commit()
        db.close()
        ajena = decision(pack_id=pack, entity=otro, units=[unit(uid)],
                         estado={**estado, 'nota': propia['estado']['nota']}, _file=f'{pack}.json')
        self.assertEqual(self.gate_del_repo(ajena),
                         [f'{pack}.json: {self.AC78.format(seudonimos.sha16(n))}'
                          for n in ('el investigado Sr. Zz Test', 'zz test')])

    def test_el_nombre_de_pila_de_otro_seudonimo_en_la_cita_en_el_gate(self):
        """AC118 (enmienda 30, residual aprobado) en el apply: el selector de la regla pública de la arista corregida
        de un seudónimo que nombra el nombre de pila compuesto de otro (un civil declarado en el roster) para el gate y
        el control posterior sin su entrada de `citas:` y pasa con ella; la arista que la decisión conserva con esa
        cita, sin `--snapshot`, «sin prueba disponible», y con la cita en un chunk de su documento, pasa. Sin
        valores."""
        lucio = 'Person:yy-lucio-verde-lago'
        (self.repo / seudonimos.PSEUDONYMS_REL).write_text(PSEUDONYMS + f'- id: {lucio}\n', encoding='utf-8')
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Person, name: Yy Lucio Verde Lago, entity_id: {lucio}}}\n', encoding='utf-8')
        cita = 'Lo declaró Yy Lucio Verde Lago ante el juez de la causa.'
        uid, conservada = 'q' * 64, 'u' * 64
        pack = self.arista_del_seudonimo(uid, 'Crime:zz-delito-test', 'ACCUSED_OF', cita=cita)
        corregida = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                             edges=[edge_op(uid, 'set_prop', prop='source_page', value='4')], _file=f'{pack}.json')
        selector = A._quote_contains(cita)
        pila = f'{seudonimos.MOTIVOS["pila"]} (huella {seudonimos.sha16(selector)})'
        self.assertEqual(self.gate_del_repo(copy.deepcopy(corregida)), [f'{pack}.json: unidad {uid[:8]}: {pila}'])
        materializada = copy.deepcopy(corregida)
        self.gate_del_repo(materializada)
        self.assertEqual(self.posterior_del_repo(materializada), [f'fix[0]: {pila}'])
        entrada = seudonimos.entrada_cita(selector, SEUDO, ['ATS_A.pdf'], 'decision-2026-10-08',
                                          'cita literal del documento judicial público; coaparición verificada en '
                                          'el export 20261007-qa067b')
        self.assertEqual(self.gate_del_repo(copy.deepcopy(corregida), citas=[entrada]), [])
        pack2 = self.arista_del_seudonimo(conservada, 'Crime:zz-otro-delito-test', 'ACCUSED_OF', cita=cita)
        conservar = decision(pack_id=pack2, entity=SEUDO, units=[unit(conservada)], _file=f'{pack2}.json')
        self.assertEqual(self.gate_del_repo(copy.deepcopy(conservar)),
                         [f'{pack2}.json: unidad {conservada[:8]}: {seudonimos.MOTIVOS["pila"]}: sin prueba disponible '
                          f'(pasa --snapshot) (huella {seudonimos.sha16(cita)})'])
        literal = seudonimos.literal_del_export(self.export_con_chunk(f'Consta que {cita}'))
        self.assertEqual(self.gate_del_repo(copy.deepcopy(conservar), export=literal), [])

    def test_conservar_una_arista_tambien_prueba_su_cita(self):
        """AC119 (enmienda 30) y AC122 (enmienda 31), reproducciones de Codex: la arista del seudónimo con su sociedad
        que la decisión cubre y conserva (sin acción o con «keep») se prueba con evidencia positiva, en el gate y, otra
        vez, en el control posterior. Si su cita nombra a la sociedad: sin export ni entrada de `citas:`, «sin prueba
        disponible (pasa --snapshot)» y `check` para; con `--snapshot` y la cita en un chunk de su documento, la acción
        lleva «export:<doc>:<huella>»; con un chunk sin ella, AC90; con su entrada de `citas:`, «citas:<huella>». Una
        cita vacía o solo de puntuación es «arista mixta conservada sin cita». AC148 (enmienda 35): una que no la nombra
        no se conserva («sin coaparición probada»; sin los nombres de la sociedad, «sin nombres conocidos»): la
        asociación que publica la arista no la respalda la cita; la que la nombra por un nombre que solo da el export,
        con él, necesita prueba. El control posterior no acepta la etiqueta: la recalcula. Sin valores."""
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Nimbo, entity_id: Company:zz-nimbo, role: Sociedad.}\n',
            encoding='utf-8')
        uid, cita = 'k' * 64, 'Zz Test trabajó para Nimbo.'
        pack = self.arista_del_seudonimo(uid, 'Company:zz-nimbo', 'WORKS_FOR', cita=cita)
        implicita = decision(pack_id=pack, entity=SEUDO, units=[unit(uid)], _file=f'{pack}.json')
        explicita = decision(pack_id=pack, entity=SEUDO, units=[unit(uid)], edges=[edge_op(uid, 'keep')],
                             _file=f'{pack}.json')
        sin_prueba = (f'{pack}.json: unidad {uid[:8]}: cita que nombra a su extremo civil sin prueba de coaparición: '
                      f'sin prueba disponible (pasa --snapshot) (huella {seudonimos.sha16(cita)})')
        export = seudonimos.literal_del_export(self.export_con_chunk(f'Consta que {cita} Y más.'))
        literal = f'export:ATS_A.pdf:{seudonimos.sha16(cita)}'
        for d in (implicita, explicita):
            with self.subTest(acciones=d['edges']):
                self.assertEqual(self.gate_del_repo(copy.deepcopy(d)), [sin_prueba])
                probada = copy.deepcopy(d)
                self.assertEqual(self.gate_del_repo(probada, export=export), [])
                self.assertEqual(probada['_pruebas'], {uid: literal})
                rows = A.materialize([probada], self.tag, self.upd, frozenset({SEUDO}), self.publicos)
                self.assertEqual([(a['accion'], a['unidad'], a.get('prueba')) for a in rows['seudonimos']],
                                 [('conservar', uid, literal)])
                self.assertEqual(self.posterior_del_repo(probada, export=export), [])
                # sin el export, el control posterior no puede rehacer la prueba: no se fía de la etiqueta
                self.assertEqual(self.posterior_del_repo(probada),
                                 [f'seudonimos[0]: arista conservada sin la prueba de su cita (unidad {uid[:8]})'])
        ajeno = seudonimos.literal_del_export(self.export_con_chunk('Un chunk que no la lleva.'))
        self.assertEqual(self.gate_del_repo(copy.deepcopy(implicita), export=ajeno),
                         [f'{pack}.json: unidad {uid[:8]}: {self.COAPARICION.format(seudonimos.sha16(cita))}'])
        entrada = seudonimos.entrada_cita(cita, SEUDO, ['ATS_A.pdf'], 'decision-2026-10-08',
                                          'cita literal del documento judicial público')
        por_citas = copy.deepcopy(implicita)
        self.assertEqual(self.gate_del_repo(por_citas, citas=[entrada]), [])
        self.assertEqual(por_citas['_pruebas'], {uid: f'citas:{seudonimos.huella_cita(cita)[:16]}'})
        sin_gate = copy.deepcopy(implicita)
        sin_gate['_pack'] = probada['_pack']
        rows = A.materialize([sin_gate], self.tag, self.upd, frozenset({SEUDO}), self.publicos)
        self.assertEqual(rows['seudonimos'], [])   # sin la prueba del gate, no se propone
        rows['seudonimos'] = [A._accion_seudonimo('conservar', SEUDO, f'{pack}.json', uid, otro='Company:zz-nimbo',
                                                  tipo='WORKS_FOR', kind='judicial', doc='ATS_A.pdf', prueba=literal)]
        self.assertEqual(A._prosa_publica(rows, frozenset({SEUDO}), {}, self.publicos),   # la etiqueta sola no vale
                         [f'seudonimos[0]: arista conservada sin la prueba de su cita (unidad {uid[:8]})'])
        for vacia in ('', '... !!!'):
            with self.subTest(cita=vacia):
                otra = f'k{len(vacia)}' * 32
                p = self.arista_del_seudonimo(otra, 'Company:zz-nimbo', 'OWNS', cita=vacia)
                self.assertEqual(self.gate_del_repo(decision(pack_id=p, entity=SEUDO, units=[unit(otra)],
                                                             _file=f'{p}.json')),
                                 [f'{p}.json: unidad {otra[:8]}: arista mixta conservada sin cita '
                                  f'(huella {seudonimos.sha16(vacia)})'])
        neutra = 'k2' * 32
        pack2 = self.arista_del_seudonimo(neutra, 'Company:zz-nimbo', 'PAID', cita='Zz Test cobró del auto.')
        d = decision(pack_id=pack2, entity=SEUDO, units=[unit(neutra)], _file=f'{pack2}.json')
        self.assertEqual(self.gate_del_repo(copy.deepcopy(d)),
                         [f'{pack2}.json: unidad {neutra[:8]}: cita de una arista mixta conservada sin nombres '
                          f'conocidos de su extremo: sin prueba disponible (pasa --snapshot) '
                          f'(huella {seudonimos.sha16("Zz Test cobró del auto.")})'])
        nodo_del_censo(self.root, 'Company:zz-nimbo', 'Company', 'Nimbo')
        no_nombra = (f'{pack2}.json: unidad {neutra[:8]}: arista mixta conservada sin coaparición probada{{}} '
                     f'(huella {seudonimos.sha16("Zz Test cobró del auto.")})')
        self.assertEqual(self.gate_del_repo(d), [no_nombra.format(': sin prueba disponible (pasa --snapshot)')])
        self.assertEqual(d['_pruebas'], {})   # «no-nombra» ya no es una prueba (AC148)
        con_export = copy.deepcopy(d)
        self.assertEqual(self.gate_del_repo(con_export, export=seudonimos.literal_del_export(
            self.export_con_chunk('Zz Test cobró del auto.'))), [no_nombra.format('')])
        rows = A.materialize([d], self.tag, self.upd, frozenset({SEUDO}), self.publicos)
        self.assertEqual(rows['seudonimos'], [])   # sin prueba del gate, no se propone conservarla
        solo_export = 'k3' * 32   # un nombre que solo da el export: con él, la cita lo nombra y necesita prueba
        pack3 = self.arista_del_seudonimo(solo_export, 'Company:zz-nimbo', 'ASSOCIATED_WITH', cita='Zz Test y Stratus.')
        d3 = decision(pack_id=pack3, entity=SEUDO, units=[unit(solo_export)], _file=f'{pack3}.json')
        snap = self.export_con_chunk('Otro texto.')
        with (snap / 'nodes.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'ref': 'n', 'labels': ['Company'],
                                'props': {'entity_id': 'Company:zz-nimbo', 'name': 'Stratus'}}) + '\n')
        # sin export, el censo no da «Stratus»: la cita no nombra a la sociedad y no se conserva (AC148)
        self.assertEqual(self.gate_del_repo(copy.deepcopy(d3)),
                         [f'{pack3}.json: unidad {solo_export[:8]}: arista mixta conservada sin coaparición probada: '
                          f'sin prueba disponible (pasa --snapshot) (huella {seudonimos.sha16("Zz Test y Stratus.")})'])
        self.assertEqual(self.gate_del_repo(d3, export=seudonimos.literal_del_export(snap)),
                         [f'{pack3}.json: unidad {solo_export[:8]}: '
                          f'{self.COAPARICION.format(seudonimos.sha16("Zz Test y Stratus."))}'])
        self.write(pack, implicita)
        codigo, out, err = self.cli('check')
        self.assertEqual(codigo, 1, out + err)
        self.assertIn(f'  ❌ {sin_prueba}\n', out)
        self.assertNotIn('nimbo', (out + err).lower())
        codigo, out, err = self.cli('check', '--snapshot', self.export_con_chunk(f'Consta que {cita}'))
        self.assertEqual(codigo, 0, out + err)

    def test_un_export_con_una_linea_rota_para_con_exit_2_y_su_numero_de_linea(self):
        """AC13 y AC15 (spec 072): `--snapshot` con una línea vacía, un JSON roto, bytes que no son UTF-8, un registro que no es un objeto o con `labels` o `props` de otra forma en
        `nodes.jsonl` (lo lee `literal_del_export`, en `check` y en `record`): exit 2 con «línea N» y el motivo, no «error interno»; sin el texto de la línea. Con el export entero, sin cambio."""
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Nimbo, entity_id: Company:zz-nimbo, role: Sociedad.}\n', encoding='utf-8')
        uid, cita = 'k' * 64, 'Zz Test trabajó para Nimbo.'
        pack = self.arista_del_seudonimo(uid, 'Company:zz-nimbo', 'WORKS_FOR', cita=cita)
        self.write(pack, decision(pack_id=pack, entity=SEUDO, units=[unit(uid)], _file=f'{pack}.json'))   # una decisión pendiente: sin ella `check` y `record` salen antes de leer el export
        snap = self.export_con_chunk(f'Consta que {cita}')
        ruta = snap / 'nodes.jsonl'
        buenas = ruta.read_text(encoding='utf-8').splitlines(keepends=True)
        for roto, razon in (('\n', 'vacía'), ('{"ref": CENTINELA_ROTO\n', 'no es JSON'), ('[1]\n', 'no es un objeto')):
            ruta.write_text(buenas[0] + roto + buenas[1], encoding='utf-8')
            for comando in ('check', 'record'):
                with self.subTest(comando=comando, razon=razon):
                    codigo, out, err = self.cli(comando, '--snapshot', snap)
                    self.assertEqual(codigo, 2, out + err)
                    self.assertIn(f'(nodes.jsonl, línea 2, {razon})', out)
                    self.assertNotIn('error interno', out + err)
                    self.assertNotIn('CENTINELA_ROTO', out + err)
        # AC15 (Adenda 1): bytes que no son UTF-8 (el 0xFF de «in·culpable» hacía de límite de palabra) y registros con `labels` o `props` de otra forma
        for roto, razon in ((b'{"ref": "c", "labels": ["Chunk"], "props": {"doc_id": "ATS_A.pdf", "text": "in\xffculpable"}}\n', 'no es UTF-8'),
                            (b'{"ref": "c", "labels": 5, "props": {}}\n', '`labels`, no es una lista de cadenas'),
                            (b'{"ref": "c", "labels": ["Chunk"], "props": ["a"]}\n', '`props`, no es un objeto'),
                            (b'{"ref": "c", "labels": ["Chunk"], "props": "texto"}\n', '`props`, no es un objeto'),
                            (b'{"ref": "c", "labels": ["Chunk"], "props": {"doc_id": "ATS_A.pdf", "text": "in' + bytes([92]) + b'udcffculpable"}}\n', 'sustituto suelto'),   # AC19
                            (b'{"ref": "c", "labels": ["Chunk"], "props": {"doc_id": "ATS_A.pdf", "text": "x"}, "' + bytes([92]) + b'ud800": 1}\n', 'sustituto suelto')):
            ruta.write_bytes(buenas[0].encode('utf-8') + roto + buenas[1].encode('utf-8'))
            for comando in ('check', 'record'):
                with self.subTest(comando=comando, razon=razon):
                    codigo, out, err = self.cli(comando, '--snapshot', snap)
                    self.assertEqual(codigo, 2, out + err)
                    self.assertIn(f'(nodes.jsonl, línea 2, {razon})', out)
                    self.assertNotIn('error interno', out + err)
                    self.assertNotIn('culpable', out + err)
        ruta.write_text(''.join(buenas), encoding='utf-8')
        codigo, out, err = self.cli('check', '--snapshot', snap)
        self.assertEqual(codigo, 0, out + err)
        # AC19 (límite declarado): un U+0000 en el texto de un chunk (5 líneas del export real; json.dumps lo escribe `\\u0000`) no es un error: el export se lee y la cita sigue probada
        snap2 = self.export_con_chunk(f'Consta que {cita}\x00 y otra cosa')
        self.assertIn(bytes([92]) + b'u0000', (snap2 / 'nodes.jsonl').read_bytes())
        codigo, out, err = self.cli('check', '--snapshot', snap2)
        self.assertEqual(codigo, 0, out + err)

    def test_la_prosa_de_un_seudonimo_sin_datos_civiles_en_el_gate(self):
        """AC120 (enmienda 30, reproducción de Codex) en el apply: la nota del estado de un seudónimo y la de un
        `set_prop` sobre su arista que dicen «administrador único de Nimbo SA desde el 2020-01-02» llevan un dato civil
        (una sociedad real y una fecha) en el gate y en el control posterior; `check` para sin el texto. Controles: una
        nota con solo campos cerrados (la referencia al documento y su página) pasa. AC123 (enmienda 31): la entrada de
        `citas:` del texto entero ya no lo exime; la de un tramo citado, solo a él."""
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n- {type: Company, name: Nimbo SA, entity_id: Company:zz-nimbo-sa, role: Sociedad.}\n',
            encoding='utf-8')
        nota = 'Administrador único de Nimbo SA desde el 2020-01-02.'
        civil = f'{seudonimos.MOTIVOS["dato-civil"]} (huella {seudonimos.sha16(nota)})'
        estado = {'action': 'set', 'status': 'investigado', 'fecha': '2026-04-11', 'doc': 'ATS_0000_2025.pdf',
                  'url': '', 'tipo': 'auto', 'nota': nota}
        d = decision(pack_id=PS, entity=SEUDO, units=[unit(u) for u in (U_S_ORG, U_S_SOC, U_S_ID)],
                     alias_deny=['el consejero Zz Test'], estado=estado, _file=f'{PS}.json')
        d['units'][2] = unit(U_S_ID, 'corregido', 'alias')
        uid = 'm' * 64
        pack = self.arista_del_seudonimo(uid, 'Crime:zz-delito-test', 'ACCUSED_OF')
        arista = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                          edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
        self.assertEqual(self.gate_del_repo(copy.deepcopy(d), copy.deepcopy(arista)),
                         [f'{PS}.json: {civil}', f'{pack}.json: unidad {uid[:8]}: {civil}'])
        for decision_, fila in ((d, 'estado[0]'), (arista, 'fix[0]')):
            materializada = copy.deepcopy(decision_)
            self.gate_del_repo(materializada)
            self.assertEqual(self.posterior_del_repo(materializada), [f'{fila}: {civil}'])
        self.write(pack, arista)
        codigo, out, err = self.cli('check')
        self.assertEqual(codigo, 1, out + err)
        self.assertIn(f'  ❌ {pack}.json: unidad {uid[:8]}: {civil}\n', out)
        self.assertNotIn('nimbo', (out + err).lower())
        cerrada = copy.deepcopy(arista)
        cerrada['edges'][0]['value'] = 'Según el auto (ATS 0000/2025, p.3), sin más datos.'
        self.assertEqual(self.gate_del_repo(cerrada), [])
        entrada = seudonimos.entrada_cita(nota, SEUDO, ['ATS_A.pdf'], 'decision-2026-10-08',
                                          'cita literal del documento judicial público')
        self.assertEqual(self.gate_del_repo(copy.deepcopy(arista), citas=[entrada]),
                         [f'{pack}.json: unidad {uid[:8]}: {civil}'])
        citada = copy.deepcopy(arista)
        citada['edges'][0]['value'] = f'Según el auto: «{nota}»'
        self.assertEqual(self.gate_del_repo(citada, citas=[entrada]), [])

    def pack_de_otro_seudonimo(self, sid, uid, pack):
        """Un pack de `sid` en el censo con una arista suya (AC118/AC129: la decisión sobre otro seudónimo)."""
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   (f'o-{pack}', 'relationship', f'r:{pack}', sid, 'ACCUSED_OF', 'judicial', 0, 'h', '{}'))
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")',
                   (uid, f'o-{pack}', 'edge', '/edge@1', json.dumps({'start': sid, 'end': 'Crime:zz-delito-test',
                                                                     'type': 'ACCUSED_OF', 'props': {
                                                                         'source_kind': 'judicial',
                                                                         'source_doc': 'ATS_A.pdf', 'source_page': 2,
                                                                         'source_quote': 'cita sintética'}})))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   (pack, sid, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.commit()
        db.close()

    def test_las_reproducciones_de_la_enmienda_31_en_el_gate(self):
        """AC121/AC124/AC126/AC129–AC131 (enmienda 31, reproducciones de Codex) en el apply, con `citas:` de tramos: la
        nota del estado con «Centinela S.L.» (sin índice) y la de una arista con «VIII-X-MMXXV» llevan un dato civil en
        el gate y en el control posterior; la del estado con su tramo probado para otro documento que el suyo, también,
        y sin documento, «tramo citado sin documento para la prueba»; el selector con «Antonio Pedro» y el que nombra
        «ZX SA» piden prueba; y en la nota de un seudónimo, el civil que coincide con un alias inválido suyo cuenta.
        Las huellas, nunca los textos."""
        pedro, civil, zx = 'Person:pedro-antonio', 'Person:zz-pedro-antonio-garcia', 'Company:zz-zx-sa'
        (self.repo / seudonimos.PSEUDONYMS_REL).write_text(PSEUDONYMS + f'- id: {pedro}\n', encoding='utf-8')
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Person, name: Pedro Antonio Garcia, entity_id: {civil}, role: Testigo.}}\n'
            f'- {{type: Company, name: ZX SA, entity_id: {zx}, role: Sociedad.}}\n', encoding='utf-8')
        (self.repo / 'pipeline/data/context/roles_qa061.yaml').write_text(
            f'entities:\n- {{type: Person, name: Pedro Antonio, entity_id: {pedro}, aliases: [Pedro Antonio Garcia]}}\n'
            'relations: []\n', encoding='utf-8')
        civil_ = seudonimos.MOTIVOS['dato-civil']
        sociedad, tramo = 'Administrador de Centinela S.L.', 'Administrador desde 2019.'
        citado = f'Según el auto: «{tramo}»'
        estado = {'action': 'set', 'status': 'investigado', 'fecha': '2026-04-11', 'doc': 'ATS_0000_2025.pdf',
                  'url': '', 'tipo': 'auto', 'nota': sociedad}
        d = decision(pack_id=PS, entity=SEUDO, units=[unit(u) for u in (U_S_ORG, U_S_SOC, U_S_ID)],
                     alias_deny=['el consejero Zz Test'], estado=estado, _file=f'{PS}.json')
        d['units'][2] = unit(U_S_ID, 'corregido', 'alias')
        entrada = seudonimos.entrada_cita(tramo, SEUDO, ['ATS_A.pdf'], 'decision-2026-10-08', 'cita literal')
        casos = [(sociedad, 'ATS_0000_2025.pdf', f'{civil_} (huella {seudonimos.sha16(sociedad)})'),
                 (citado, 'ATS_0000_2025.pdf', f'{civil_}: sin prueba disponible (pasa --snapshot) '
                                               f'(huella {seudonimos.sha16(citado)})'),
                 (citado, '', f'{seudonimos.MOTIVOS["sin-documento"]} (huella {seudonimos.sha16(citado)})')]
        for nota, doc, error in casos:
            with self.subTest(nota=nota, doc=doc):
                x = copy.deepcopy(d)
                x['estado'].update(nota=nota, doc=doc, url='' if doc else 'https://www.poderjudicial.es/x')
                self.assertEqual(self.gate_del_repo(x, citas=[entrada]), [f'{PS}.json: {error}'])
                self.assertEqual(self.posterior_del_repo(x), [f'estado[0]: {error}'])
        probada = copy.deepcopy(d)
        probada['estado'].update(nota=citado, doc='ATS_A.pdf')
        self.assertEqual(self.gate_del_repo(probada, citas=[entrada]), [])
        uid, romana = 'm' * 64, 'Pagado desde VIII-X-MMXXV.'
        pack = self.arista_del_seudonimo(uid, 'Crime:zz-delito-test', 'ACCUSED_OF')
        arista = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                          edges=[edge_op(uid, 'set_prop', prop='note', value=romana)], _file=f'{pack}.json')
        self.assertEqual(self.gate_del_repo(arista),
                         [f'{pack}.json: unidad {uid[:8]}: {civil_} (huella {seudonimos.sha16(romana)})'])
        selectores = [('p' * 64, 'Crime:zz-delito-test', 'MET_WITH', 'Declaró Antonio Pedro ante el juez.', 'pila'),
                      ('q' * 64, zx, 'ASSOCIATED_WITH', 'ZX SA pertenece a Zz Test.', 'coaparicion')]
        for u, otro, tipo, cita, motivo in selectores:
            with self.subTest(selector=cita):
                pk = self.arista_del_seudonimo(u, otro, tipo, cita=cita)
                regla = decision(pack_id=pk, entity=SEUDO, units=[unit(u, 'corregido', 'arista')],
                                 edges=[edge_op(u, 'retype', new_type='WORKS_FOR')], _file=f'{pk}.json')
                selector = A._quote_contains(cita)
                self.assertEqual(self.gate_del_repo(regla), [
                    f'{pk}.json: unidad {u[:8]}: {seudonimos.MOTIVOS[motivo]} (huella {seudonimos.sha16(selector)})'])
        u, pk = 'u' * 64, 'E-zzpedro01'
        self.pack_de_otro_seudonimo(pedro, u, pk)
        propia = decision(pack_id=pk, entity=pedro, units=[unit(u)], _file=f'{pk}.json',
                          estado={**estado, 'nota': 'Lo dijo Pedro Antonio Garcia.'})
        self.assertEqual(self.gate_del_repo(propia),
                         [f'{pk}.json: {self.AC78.format(seudonimos.sha16("Pedro Antonio Garcia"))}'])
        self.assertEqual(self.posterior_del_repo(propia),
                         [f'estado[0]: {self.AC78.format(seudonimos.sha16("Pedro Antonio Garcia"))}'])

    def test_la_misma_prueba_en_los_cuatro_caminos(self):
        """AC125 (enmienda 31): el mismo centinela, una nota de estado de un seudónimo con un tramo citado que lleva una
        fecha, tiene el mismo veredicto en el árbol, el export, el gate y el control posterior: con la entrada de
        `citas:` de su tramo en su documento, pasa en los cuatro; sin ella, falla en los cuatro; con el tramo literal en
        un chunk de su documento, pasan los que tienen el export (el árbol no lo tiene). Una sola función decide
        (`seudonimos.prueba_cita`)."""
        tramo = 'Administrador desde 2019.'
        nota = f'Según el auto: «{tramo}»'
        entrada = seudonimos.entrada_cita(tramo, SEUDO, ['ATS_A.pdf'], 'decision-2026-10-08',
                                          'cita literal del documento judicial público; coaparición verificada en el '
                                          'export 20261007-qa067b')
        (self.repo / 'pipeline/data/accusations/accusations.yaml').write_text(
            ACCUSATIONS + f"  - person: Zz Test\n    entity_id: {SEUDO}\n"
                          f"    estado: {{status: investigado, fuente: {{doc: ATS_A.pdf}}, nota: '{nota}'}}\n",
            encoding='utf-8')
        estado = {'action': 'set', 'status': 'investigado', 'fecha': '2026-04-11', 'doc': 'ATS_A.pdf', 'url': '',
                  'tipo': 'auto', 'nota': nota}
        d = decision(pack_id=PS, entity=SEUDO, units=[unit(u) for u in (U_S_ORG, U_S_SOC, U_S_ID)],
                     alias_deny=['el consejero Zz Test'], estado=estado, _file=f'{PS}.json')
        d['units'][2] = unit(U_S_ID, 'corregido', 'alias')

        def export(chunk):
            snap = self.base / f'snap-{len(chunk)}'
            snap.mkdir(exist_ok=True)
            nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                     {'ref': 'k', 'labels': ['Crime'], 'props': {'entity_id': 'Crime:zz-delito-test'}},
                     {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                     {'ref': 'h', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': chunk}}]
            rels = [{'ref': 'r', 'start': 's', 'end': 'k', 'type': 'ACCUSED_OF',
                     'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'note': nota}}]
            (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                              encoding='utf-8')
            (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                      encoding='utf-8')
            return snap

        def veredictos(citas, chunk):
            self.perm.write_text(seudonimos.volcar_permitidos({'aristas': [], 'alias': [], 'citas': citas}),
                                 encoding='utf-8')
            snap = export(chunk)
            exp = seudonimos.literal_del_export(snap) if 'Administrador' in chunk else None
            arbol = not any('acusaciones' in h.fichero or 'accusations' in h.fichero
                            for h in seudonimos.auditar(self.repo, self.publicos))
            snapshot = not seudonimos.verificar_snapshot(snap, frozenset({SEUDO}), {'aristas': [], 'alias': [],
                                                                                    'citas': citas})
            gate = copy.deepcopy(d)
            errores = self.gate_del_repo(gate, citas=citas, export=exp)
            return arbol, snapshot, not errores, not self.posterior_del_repo(gate, export=exp, citas=citas)
        self.assertEqual(veredictos([entrada], 'Nada.'), (True, True, True, True))
        self.assertEqual(veredictos([], 'Nada.'), (False, False, False, False))
        self.assertEqual(veredictos([], f'Consta: {tramo} Y nada más.'), (False, True, True, True))

    def test_la_denominacion_con_prefijo_y_el_dia_sin_ano_en_los_cuatro_caminos(self):
        """AC121-bis y AC126-bis (ampliación de la enmienda 31): la nota de la arista del seudónimo con su sociedad
        «Nimbo» que nombra «Hijos de Nimbo S.L.», «Grupo Nimbo S.L.» o «Viuda de Nimbo S.A.» lleva otra sociedad, y
        la que dice «el 8/10», una fecha, en el árbol (el `set.note` de su regla), el export (la nota de la arista), el
        gate y el control posterior (el `set_prop` de la decisión); «administrador de Nimbo S.L.», «Consejero en Nimbo
        S.L.» y «3/4 del capital», en ninguno. AC152 (enmienda 35): «Administrador de Talleres de Nimbo S.L.» y, con
        mayúscula, «Administrador de Nimbo S.L.», en los cuatro."""
        nimbo = 'Company:zz-nimbo'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n', encoding='utf-8')
        lista = {'aristas': [], 'alias': [], 'citas': []}
        uid = 'w' * 64
        pack = self.arista_del_seudonimo(uid, nimbo, 'WORKS_FOR')
        dato = seudonimos.MOTIVOS['dato-civil']

        def veredictos(nota):
            (self.repo / seudonimos.RELATIONS_REL / 'relations.yaml').write_text(
                f"fix:\n  - {{source_id: {SEUDO}, target_id: {nimbo}, type: WORKS_FOR, doc: ATS_A.pdf, "
                f"set: {{note: '{nota}'}}}}\n", encoding='utf-8')
            snap = self.base / f'snap-{seudonimos.sha16(nota)}'
            snap.mkdir(exist_ok=True)
            nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                     {'ref': 'n', 'labels': ['Company'], 'props': {'entity_id': nimbo, 'name': 'Nimbo'}},
                     {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}}]
            rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': 'WORKS_FOR',
                     'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'note': nota}}]
            (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                              encoding='utf-8')
            (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                      encoding='utf-8')
            arbol = any(dato in h.motivo for h in seudonimos.auditar(self.repo, self.publicos))
            export = any(dato in problema for problema in seudonimos.verificar_snapshot(
                snap, frozenset({SEUDO}), lista, publicos=self.publicos, repo=self.repo))
            d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                         edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
            gate = any(dato in error for error in self.gate_del_repo(d))
            return arbol, export, gate, any(dato in error for error in self.posterior_del_repo(d))
        # AC152 (enmienda 35): con una palabra con mayúscula inicial unida por «de» delante del nombre del extremo, la
        # denominación es otra sociedad, aunque no lleve un prefijo de la lista y también a principio de frase.
        for nota in ('Socio de Hijos de Nimbo S.L.', 'Socio de Grupo Nimbo S.L.', 'Socio de Viuda de Nimbo S.A.',
                     'Pagado el 8/10.', 'Administrador de Talleres de Nimbo S.L.', 'Administrador de Nimbo S.L.',
                     'Socio de Talleres de Nimbo.', 'Pagado el 8/`10`.'):
            with self.subTest(nota=nota):
                self.assertEqual(veredictos(nota), (True, True, True, True))
        for nota in ('administrador de Nimbo S.L.', 'Consejero en Nimbo S.L.', 'Cobró 3/4 del capital.'):
            with self.subTest(control=nota):
                self.assertEqual(veredictos(nota), (False, False, False, False))

    def test_las_reproducciones_de_la_enmienda_35_en_los_cuatro_caminos(self):
        """AC149/AC150/AC151 (enmienda 35, reproducciones de Codex) en el árbol (el `set.note` de la regla), el export
        (la nota de la arista), el gate y el control posterior (el `set_prop` de la decisión), sobre la arista del
        seudónimo con su sociedad «Nimbo»: otra sociedad por su núcleo («Socio de Cirro Estrato.») y una fecha o una
        sociedad partidas por comillas («Pagado el 8/«10».», «Socio de Cir«ro» Estrato S.L.») llevan un dato civil en
        los cuatro (antes, en ninguno); la mención de su propio extremo, no. La nota que identifica al seudónimo con su
        sociedad («Zz Test, seudónimo del administrador de Nimbo.») es hallazgo en los cuatro, con la huella de la nota;
        la que lo enumera entre los directivos («a saber»), no."""
        nimbo = 'Company:zz-nimbo'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n'
            '- {type: Company, name: Cirro Estrato S.L., entity_id: Company:zz-cirro, role: Sociedad.}\n',
            encoding='utf-8')
        lista = {'aristas': [], 'alias': [], 'citas': []}
        uid = 'v' * 64
        pack = self.arista_del_seudonimo(uid, nimbo, 'WORKS_FOR')
        dato = seudonimos.MOTIVOS['dato-civil']

        def veredictos(nota, hallazgo):
            (self.repo / seudonimos.RELATIONS_REL / 'relations.yaml').write_text(
                f"fix:\n  - {{source_id: {SEUDO}, target_id: {nimbo}, type: WORKS_FOR, doc: ATS_A.pdf, "
                f"set: {{note: '{nota}'}}}}\n", encoding='utf-8')
            snap = self.base / f'snap-{seudonimos.sha16(nota)}'
            snap.mkdir(exist_ok=True)
            nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                     {'ref': 'n', 'labels': ['Company'], 'props': {'entity_id': nimbo, 'name': 'Nimbo'}},
                     {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}}]
            rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': 'WORKS_FOR',
                     'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': 'Zz Test y Nimbo.',
                               'note': nota}}]
            chunk = {'ref': 'h', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': 'Zz Test y Nimbo.'}}
            (snap / 'nodes.jsonl').write_text(
                ''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in [*nodos, chunk]), encoding='utf-8')
            (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                      encoding='utf-8')
            arbol = any(hallazgo(h.motivo) for h in seudonimos.auditar(self.repo, self.publicos))
            export = any(hallazgo(problema) for problema in seudonimos.verificar_snapshot(
                snap, frozenset({SEUDO}), lista, publicos=self.publicos, repo=self.repo))
            d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                         edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
            gate = any(hallazgo(error) for error in self.gate_del_repo(d))
            return arbol, export, gate, any(hallazgo(error) for error in self.posterior_del_repo(d))

        def civil(motivo):
            return dato in motivo
        for nota in ('Socio de Cirro Estrato.', 'Pagado el 8/«10».', 'Socio de Cir«ro» Estrato S.L.'):
            with self.subTest(nota=nota):
                self.assertEqual(veredictos(nota, civil), (True, True, True, True))
        for nota in ('administrador de Nimbo.', 'Socio de la empresa.'):
            with self.subTest(control=nota):
                self.assertEqual(veredictos(nota, civil), (False, False, False, False))
        for nota, identifica in (('Zz Test, seudónimo del administrador de Nimbo.', True),
                                 ('Los directivos de Nimbo, a saber, don Zz Test.', False)):
            with self.subTest(identifica=nota):
                self.assertEqual(veredictos(nota, lambda m, n=nota: 'nombra a un civil' in m
                                            and f'huella {seudonimos.sha16(n)}' in m), (identifica,) * 4)

    def test_la_identificacion_en_una_fila_civil_en_los_cuatro_caminos(self):
        """AC160 (enmienda 35, residual de AC150) en el árbol (el resumen de un civil), el export (el de su nodo), el
        gate y el control posterior (el de la decisión): el nombre de pila suelto del seudónimo, con tratamiento o sin
        él, que una fórmula adyacente (seis palabras como mucho entre los dos nombres) iguala a otro civil es hallazgo
        en los cuatro, con la huella del texto (antes, en ninguno); como parte de un nombre propio más largo o con la
        fórmula a siete palabras o más, en ninguno."""
        centinela = 'Person:yy-centinela-civil'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Person, name: Centinela Civil, entity_id: {centinela}, role: Testigo.}}\n',
            encoding='utf-8')
        lista = {'aristas': [], 'alias': [], 'citas': []}

        def veredictos(resumen):
            def hallazgo(motivo):
                return 'nombra a un seudónimo judicial' in motivo and f'huella {seudonimos.sha16(resumen)}' in motivo
            (self.repo / A.SUM_PATH).write_text(
                f'summaries:\n- entity_id: {FULDO}\n  name: Fuldo Fíctez\n'
                f'  resumen: {json.dumps(resumen, ensure_ascii=False)}\n', encoding='utf-8')
            arbol = any(hallazgo(h.motivo) for h in seudonimos.auditar(self.repo, self.publicos))
            snap = self.base / f'snap-{seudonimos.sha16(resumen)}'
            snap.mkdir(exist_ok=True)
            nodos = [{'ref': 'k', 'labels': ['Person'],
                      'props': {'entity_id': FULDO, 'name': 'Fuldo Fíctez', 'summary': resumen}},
                     {'ref': 'c', 'labels': ['Person'], 'props': {'entity_id': centinela, 'name': 'Centinela Civil'}}]
            (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                              encoding='utf-8')
            (snap / 'relationships.jsonl').write_text('', encoding='utf-8')
            export = any(hallazgo(problema) for problema in seudonimos.verificar_snapshot(
                snap, frozenset({SEUDO}), lista, publicos=self.publicos, repo=self.repo))
            d = full_decision()
            d['summary'] = {'action': 'replace', 'resumen': resumen, 'sources': [], 'reason': 'r'}
            d['_file'] = 'E-ac160.json'
            gate = any(hallazgo(error) for error in self.gate_del_repo(d))
            return arbol, export, gate, any(hallazgo(error) for error in self.posterior_del_repo(d))
        for resumen in ('Centinela Civil, alias Zz Test.', 'Don Zz Test, es decir, Centinela Civil.',
                        'Zz Test, es decir, el empresario Centinela Civil.'):   # la última, a cuatro palabras
            with self.subTest(resumen=resumen):
                self.assertEqual(veredictos(resumen), (True, True, True, True))
        for resumen in ('Centinela Civil, alias Zz Test Pérez.', 'Zz Test Pérez, es decir, Centinela Civil.',
                        'Zz Test Centinela Pérez, administrador de la mercantil.',
                        'Zz Test, es decir, el socio y amigo de Centinela Civil.',   # a siete
                        'Zz Test cobró en efectivo durante meses de los socios de la mercantil, es decir, de Centinela '
                        'Civil.'):
            with self.subTest(control=resumen):
                self.assertEqual(veredictos(resumen), (False, False, False, False))

    def test_las_reproducciones_de_la_enmienda_36_en_los_cuatro_caminos(self):
        """AC166/AC168-AC171 (enmienda 36, reproducciones de Codex) en el árbol (el `set.note` de la regla), el export
        (la nota de la arista), el gate y el control posterior (el `set_prop`), sobre la arista del seudónimo con su
        sociedad «Nimbo»: una fórmula partida («al-\\nias», «es de-\\ncir») identifica al seudónimo con su sociedad
        (AC166); un civil escrito entre comillas («Cen«ti»nela Civil») es un civil en su fila (AC168); una fecha en
        superíndices, en dígitos matemáticos o con el mes partido es una fecha (AC169); una forma jurídica partida
        («Gm-\\nbH», «L-td») es una sociedad (AC170): hallazgo en los cuatro (antes, en ninguno). Y la sociedad propia
        tras un límite de frase («Pagó. Nimbo S.L.») ya no es otra sociedad (AC171), como no lo es «administrador de
        Nimbo S.L.»; «Socio de Grupo Nimbo S.L.», sí."""
        nimbo, centinela = 'Company:zz-nimbo', 'Person:yy-centinela-civil'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n'
            f'- {{type: Person, name: Centinela Civil, entity_id: {centinela}, role: Testigo.}}\n', encoding='utf-8')
        lista = {'aristas': [], 'alias': [], 'citas': []}
        uid = 'w' * 64
        pack = self.arista_del_seudonimo(uid, nimbo, 'WORKS_FOR')

        def veredictos(nota, hallazgo):
            (self.repo / seudonimos.RELATIONS_REL / 'relations.yaml').write_text(
                f"fix:\n  - {{source_id: {SEUDO}, target_id: {nimbo}, type: WORKS_FOR, doc: ATS_A.pdf, "
                f"set: {{note: {json.dumps(nota, ensure_ascii=False)}}}}}\n", encoding='utf-8')
            snap = self.base / f'snap-{seudonimos.sha16(nota)}'
            snap.mkdir(exist_ok=True)
            nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                     {'ref': 'n', 'labels': ['Company'], 'props': {'entity_id': nimbo, 'name': 'Nimbo'}},
                     {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                     {'ref': 'h', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': 'Zz Test y Nimbo.'}}]
            rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': 'WORKS_FOR',
                     'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': 'Zz Test y Nimbo.',
                               'note': nota}}]
            (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                              encoding='utf-8')
            (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                      encoding='utf-8')
            arbol = any(hallazgo(h.motivo) for h in seudonimos.auditar(self.repo, self.publicos))
            export = any(hallazgo(problema) for problema in seudonimos.verificar_snapshot(   # las personas, como el CLI
                snap, frozenset({SEUDO}), lista, personas=seudonimos.personas_contexto(self.repo),
                publicos=self.publicos, repo=self.repo))
            d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                         edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
            gate = any(hallazgo(error) for error in self.gate_del_repo(d))
            return arbol, export, gate, any(hallazgo(error) for error in self.posterior_del_repo(d))

        def identifica(nota):
            return lambda m: 'nombra a un civil' in m and f'huella {seudonimos.sha16(nota)}' in m

        def civil(m):
            return 'nombra a un civil' in m and f'huella {seudonimos.sha16("Centinela Civil")}' in m

        def dato(m):
            return seudonimos.MOTIVOS['dato-civil'] in m
        for nota in ('Zz Test, al-\nias del administrador de Nimbo.',
                     'Zz Test, es de-\ncir, el administrador de Nimbo.'):
            with self.subTest(AC166=nota):
                self.assertEqual(veredictos(nota, identifica(nota)), (True, True, True, True))
        with self.subTest(AC168='comillas'):
            self.assertEqual(veredictos('Zz Test es Cen«ti»nela Civil.', civil), (True, True, True, True))
        for nota in ('Operaba en ²⁰²⁵.', 'Operaba en \U0001d7da\U0001d7d8\U0001d7da\U0001d7dd.',
                     'Pagado el ocho de octu-\nbre.', 'Socio de Centinela Gm-\nbH.', 'Socio de Centinela L-td.',
                     'Socio de Grupo Nimbo S.L.'):
            with self.subTest(dato=nota):
                self.assertEqual(veredictos(nota, dato), (True, True, True, True))
        for nota in ('Pagó. Nimbo S.L. cobró.', 'Pagó; Nimbo S.L. cobró.', 'administrador de Nimbo S.L.'):
            with self.subTest(AC171=nota):
                self.assertEqual(veredictos(nota, dato), (False, False, False, False))

    def test_un_alias_que_identifica_en_los_cuatro_caminos(self):
        """AC167 (enmienda 36, reproducción de Codex): el civil con el alias «Fuldo Fíctez, alias Zz Test» (en el
        contexto o en su nodo del export) y ese texto en su resumen: el resumen identifica al seudónimo en el árbol, el
        export, el gate y el control posterior con el alias y sin él (antes, con él en el índice civil, en ninguno:
        la mención quedaba dentro de un nombre civil); y el alias es un alias que identifica en el árbol, en el export
        y en el gate (`check`, con los alias inválidos del contexto, AC134). Lo mismo con la fórmula partida por unas
        comillas («al«i»as», AC168), que el filtro previo de una fila civil saltaba. Sin los textos en la salida."""
        lista = {'aristas': [], 'alias': [], 'citas': []}
        identificador = f'{seudonimos.MOTIVOS["alias-identificador"]}'
        for n, con_alias in itertools.product(range(2), (False, True)):
            alias = ('Fuldo Fíctez, alias Zz Test', 'Fuldo Fíctez, al«i»as Zz Test')[n]
            texto = f'{alias}.'
            resumen = f'huella {seudonimos.sha16(texto)}'
            with self.subTest(texto=n, alias_en_el_contexto=con_alias):
                (self.repo / 'pipeline/data/context/figures.yaml').write_text(
                    f'entities:\n- {{type: Person, name: Fuldo Fíctez, entity_id: {FULDO}, role: Asesor.'
                    + (f', aliases: [{json.dumps(alias, ensure_ascii=False)}]' if con_alias else '') + '}\n',
                    encoding='utf-8')
                (self.repo / A.SUM_PATH).write_text(
                    f'summaries:\n- entity_id: {FULDO}\n  name: Fuldo Fíctez\n'
                    f'  resumen: {json.dumps(texto, ensure_ascii=False)}\n', encoding='utf-8')
                hallazgos = [h.motivo for h in seudonimos.auditar(self.repo, self.publicos)]
                self.assertTrue(any('nombra a un seudónimo judicial' in m and resumen in m for m in hallazgos))
                self.assertEqual(any(identificador in m and f'huella {seudonimos.sha16(alias)}' in m
                                     for m in hallazgos), con_alias)
                snap = self.base / f'snap-167-{n}-{con_alias}'
                snap.mkdir(exist_ok=True)
                nodo = {'entity_id': FULDO, 'name': 'Fuldo Fíctez', 'summary': texto,
                        **({'aliases': [alias]} if con_alias else {})}
                (snap / 'nodes.jsonl').write_text(json.dumps({'ref': 'k', 'labels': ['Person'], 'props': nodo},
                                                             ensure_ascii=False) + '\n', encoding='utf-8')
                (snap / 'relationships.jsonl').write_text('', encoding='utf-8')
                problemas = seudonimos.verificar_snapshot(snap, frozenset({SEUDO}), lista, publicos=self.publicos,
                                                         repo=self.repo)
                self.assertTrue(any('nombra a un seudónimo judicial' in p and resumen in p for p in problemas))
                self.assertEqual(any(identificador in p and f'huella {seudonimos.sha16(alias)}' in p
                                     for p in problemas), con_alias)
                d = full_decision()
                d['summary'] = {'action': 'replace', 'resumen': texto, 'sources': [], 'reason': 'r'}
                d['_file'] = 'E-ac167.json'
                self.assertTrue(any(resumen in e for e in self.gate_del_repo(d)))
                self.assertTrue(any(resumen in e for e in self.posterior_del_repo(d)))
                self.write('E-ac167', d)
                codigo, out, err = self.cli('check')
                self.assertEqual(codigo, 1)
                self.assertEqual(f'{identificador} (huella {seudonimos.sha16(alias)})' in out, con_alias)
                self.assertNotIn('zz test', (out + err).lower())

    def test_las_reproducciones_de_la_enmienda_37_en_los_cuatro_caminos(self):
        """AC176-AC178 (enmienda 37, reproducciones de Codex) en el árbol (el `set.note` de la regla), el export (la
        nota de la arista), el gate y el control posterior (el `set_prop`), sobre la arista del seudónimo con su
        sociedad «Nimbo»: una fórmula partida en mayúsculas («AL-IAS», «AL«I»AS») identifica al seudónimo con un civil
        (AC176);
        un civil con comillas seguidas en medio («Cen«ti»«nela» Civil») es un civil (AC177); una fecha con los puntos de
        partición decididos uno a uno («ocho-de-octu-bre»), con un dígito por letra («8 de 0ctubre», «8 d3 octubre»),
        en superíndices o con comillas seguidas es una fecha (AC178): hallazgo en los cuatro (antes, en ninguno). Sin
        las lecturas nuevas, lo de siempre."""
        nimbo, centinela = 'Company:zz-nimbo', 'Person:yy-centinela-civil'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n'
            f'- {{type: Person, name: Centinela Civil, entity_id: {centinela}, role: Testigo.}}\n', encoding='utf-8')
        lista = {'aristas': [], 'alias': [], 'citas': []}
        uid = 'w' * 64
        pack = self.arista_del_seudonimo(uid, nimbo, 'WORKS_FOR')

        def veredictos(nota, hallazgo):
            (self.repo / seudonimos.RELATIONS_REL / 'relations.yaml').write_text(
                f"fix:\n  - {{source_id: {SEUDO}, target_id: {nimbo}, type: WORKS_FOR, doc: ATS_A.pdf, "
                f"set: {{note: {json.dumps(nota, ensure_ascii=False)}}}}}\n", encoding='utf-8')
            snap = self.base / f'snap-37-{seudonimos.sha16(nota)}'
            snap.mkdir(exist_ok=True)
            nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                     {'ref': 'n', 'labels': ['Company'], 'props': {'entity_id': nimbo, 'name': 'Nimbo'}},
                     {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                     {'ref': 'h', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': 'Zz Test y Nimbo.'}}]
            rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': 'WORKS_FOR',
                     'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': 'Zz Test y Nimbo.',
                               'note': nota}}]
            (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                              encoding='utf-8')
            (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                      encoding='utf-8')
            arbol = any(hallazgo(h.motivo) for h in seudonimos.auditar(self.repo, self.publicos))
            export = any(hallazgo(problema) for problema in seudonimos.verificar_snapshot(   # las personas, como el CLI
                snap, frozenset({SEUDO}), lista, personas=seudonimos.personas_contexto(self.repo),
                publicos=self.publicos, repo=self.repo))
            d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                         edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
            gate = any(hallazgo(error) for error in self.gate_del_repo(d))
            return arbol, export, gate, any(hallazgo(error) for error in self.posterior_del_repo(d))

        def identifica(nota):
            return lambda m: 'nombra a un civil' in m and f'huella {seudonimos.sha16(nota)}' in m

        def civil(m):
            return 'nombra a un civil' in m and f'huella {seudonimos.sha16("Centinela Civil")}' in m

        def fecha(m):
            return seudonimos.MOTIVOS['dato-civil'] in m
        for nota in ('Zz Test, AL-IAS DEL ADMINISTRADOR DE NIMBO.', 'Zz Test, AL«I»AS del administrador de Nimbo.'):
            with self.subTest(AC176=nota):
                self.assertEqual(veredictos(nota, identifica(nota)), (True, True, True, True))
        with self.subTest(AC177='comillas seguidas'):
            self.assertEqual(veredictos('Zz Test es Cen«ti»«nela» Civil.', civil), (True, True, True, True))
        for nota in ('Pagado el ocho-de-octu-bre.', 'Pagado el 8 de 0ctubre.', 'Pagado el 8 d3 octubre.',
                     'Pagado el ⁸ de octubre.', 'Pagado el ocho de oc«tu»«bre».'):
            with self.subTest(AC178=nota):
                self.assertEqual(veredictos(nota, fecha), (True, True, True, True))
        for nota in ('Pagó la mitad del capital.', 'El lote 5315 se vendió.'):
            with self.subTest(control=nota):
                self.assertEqual(veredictos(nota, fecha), (False, False, False, False))

    def test_las_reproducciones_de_la_enmienda_38_en_los_cuatro_caminos(self):
        """AC188/AC189 (enmienda 38, reproducciones de Codex) en el árbol (el `set.note` de la regla), el export (la
        nota de la arista), el gate y el control posterior (el `set_prop`), sobre la arista del seudónimo con su
        sociedad: una fecha con un punto de partición por separador de componentes y un dígito por letra («8-X1-25»,
        «VI1I-X-MMXXV», también con rayas o con las comillas junto al separador), una con raya entre números («el
        8–10») y una con un ordinal por letra («8 d3 mªyo», «ºcho de 0ctubre») son fechas: hallazgo en los cuatro
        (antes, en ninguno). Un rango de páginas con raya o «nº», no."""
        nimbo = 'Company:zz-nimbo'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n', encoding='utf-8')
        lista = {'aristas': [], 'alias': [], 'citas': []}
        uid = 'v' * 64
        pack = self.arista_del_seudonimo(uid, nimbo, 'WORKS_FOR')

        def veredictos(nota):
            (self.repo / seudonimos.RELATIONS_REL / 'relations.yaml').write_text(
                f"fix:\n  - {{source_id: {SEUDO}, target_id: {nimbo}, type: WORKS_FOR, doc: ATS_A.pdf, "
                f"set: {{note: {json.dumps(nota, ensure_ascii=False)}}}}}\n", encoding='utf-8')
            snap = self.base / f'snap-38-{seudonimos.sha16(nota)}'
            snap.mkdir(exist_ok=True)
            nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                     {'ref': 'n', 'labels': ['Company'], 'props': {'entity_id': nimbo, 'name': 'Nimbo'}},
                     {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                     {'ref': 'h', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': 'Zz Test y Nimbo.'}}]
            rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': 'WORKS_FOR',
                     'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': 'Zz Test y Nimbo.',
                               'note': nota}}]
            (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                              encoding='utf-8')
            (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                      encoding='utf-8')

            def fecha(m):
                return seudonimos.MOTIVOS['dato-civil'] in m
            arbol = any(fecha(h.motivo) for h in seudonimos.auditar(self.repo, self.publicos))
            export = any(fecha(problema) for problema in seudonimos.verificar_snapshot(   # las personas, como el CLI
                snap, frozenset({SEUDO}), lista, personas=seudonimos.personas_contexto(self.repo),
                publicos=self.publicos, repo=self.repo))
            d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                         edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
            gate = any(fecha(error) for error in self.gate_del_repo(d))
            return arbol, export, gate, any(fecha(error) for error in self.posterior_del_repo(d))
        for nota in ('Pagado el 8-X1-25.', 'Pagado el VI1I-X-MMXXV.', 'Pagado el 8\u2010X1\u201025.',
                     'Pagado el 8«-»X1«-»25.', 'Pagado el 8\u201310.'):
            with self.subTest(AC188=nota):
                self.assertEqual(veredictos(nota), (True, True, True, True))
        for nota in ('Pagado el 8 d3 mªyo.', 'Pagado el ºcho de 0ctubre.'):
            with self.subTest(AC189=nota):
                self.assertEqual(veredictos(nota), (True, True, True, True))
        for nota in ('Las páginas 4\u20138 del auto.', 'El nº 5 del lote.'):
            with self.subTest(control=nota):
                self.assertEqual(veredictos(nota), (False, False, False, False))

    def test_las_lecturas_de_la_enmienda_39_en_los_cuatro_caminos(self):
        """AC191 (enmienda 39) en el árbol (el `set.note` de la regla), el export (la nota de la arista), el gate y el
        control posterior (el `set_prop`), sobre la arista del seudónimo con su sociedad «Nimbo»: una fecha con un
        confusable de puntuación («8−10−25», «8∕10∕25», «8·10·25») o con otro signo pegado a sus cifras («8→10→25»,
        «20✓25») y otra sociedad partida por uno («Cir✓ro») son datos civiles, y un civil partido por uno («Cen✓tinela
        Civil») lo nombra: hallazgo en los cuatro (antes, en ninguno). Con los signos separados por espacios, nada."""
        nimbo, cirro, centinela = 'Company:zz-nimbo', 'Company:zz-cirro', 'Person:yy-centinela-civil'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n'
            f'- {{type: Company, name: Cirro, entity_id: {cirro}, role: Sociedad.}}\n'
            f'- {{type: Person, name: Centinela Civil, entity_id: {centinela}, role: Testigo.}}\n', encoding='utf-8')
        lista = {'aristas': [], 'alias': [], 'citas': []}
        uid = 'u' * 64
        pack = self.arista_del_seudonimo(uid, nimbo, 'WORKS_FOR')

        def veredictos(nota, hallazgo):
            (self.repo / seudonimos.RELATIONS_REL / 'relations.yaml').write_text(
                f"fix:\n  - {{source_id: {SEUDO}, target_id: {nimbo}, type: WORKS_FOR, doc: ATS_A.pdf, "
                f"set: {{note: {json.dumps(nota, ensure_ascii=False)}}}}}\n", encoding='utf-8')
            snap = self.base / f'snap-39-{seudonimos.sha16(nota)}'
            snap.mkdir(exist_ok=True)
            nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                     {'ref': 'n', 'labels': ['Company'], 'props': {'entity_id': nimbo, 'name': 'Nimbo'}},
                     {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                     {'ref': 'h', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': 'Zz Test y Nimbo.'}}]
            rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': 'WORKS_FOR',
                     'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': 'Zz Test y Nimbo.',
                               'note': nota}}]
            (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                              encoding='utf-8')
            (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                      encoding='utf-8')
            arbol = any(hallazgo(h.motivo) for h in seudonimos.auditar(self.repo, self.publicos))
            export = any(hallazgo(problema) for problema in seudonimos.verificar_snapshot(   # las personas, como el CLI
                snap, frozenset({SEUDO}), lista, personas=seudonimos.personas_contexto(self.repo),
                publicos=self.publicos, repo=self.repo))
            d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                         edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
            gate = any(hallazgo(error) for error in self.gate_del_repo(d))
            return arbol, export, gate, any(hallazgo(error) for error in self.posterior_del_repo(d))

        def dato(m):
            return seudonimos.MOTIVOS['dato-civil'] in m

        def civil(m):
            return 'nombra a un civil' in m and f'huella {seudonimos.sha16("Centinela Civil")}' in m
        for nota in ('Pagado el 8\u221210\u221225.', 'Pagado el 8\u221510\u221525.', 'Pagado el 8\u00b710\u00b725.',
                     'Pagado el 8\u219210\u219225.', 'Pagado en 20\u271325.', 'Pagó a Cir\u2713ro.'):
            with self.subTest(dato=nota):
                self.assertEqual(veredictos(nota, dato), (True, True, True, True))
        with self.subTest(civil='Cen✓tinela Civil'):
            self.assertEqual(veredictos('Zz Test es Cen\u2713tinela Civil.', civil), (True, True, True, True))
        for nota in ('Pagado el 8 \u2192 10 \u2192 25.', 'Pagó a Cir \u2713 ro.'):
            with self.subTest(control=nota):
                self.assertEqual(veredictos(nota, dato), (False, False, False, False))

    def test_las_lecturas_de_la_enmienda_40_en_los_cuatro_caminos(self):
        """AC195-AC198 (enmienda 40) en el árbol (el `set.note` de la regla), el export (la nota de la arista), el gate
        y el control posterior (el `set_prop`), sobre la arista del seudónimo con su sociedad «Nimbo»: otra sociedad
        partida por comillas y un signo («Cir«→ro»», «Cir"-"ro»), el año que una moneda partida o un numeral que
        empieza otra palabra tapaban («dos-mil veinte eu→ros», «dos-mil veinte dºsier», «dos mil veinte dosier») y
        una Organization del contexto con su forma jurídica partida («Estrato Gm✓bH», por su núcleo) son datos
        civiles, y un civil partido por comillas y un signo («Cen«→ti»nela Civil») lo nombra: hallazgo en los cuatro
        (antes, en ninguno). Con un espacio en el hueco o una moneda entera, nada."""
        nimbo, cirro, centinela = 'Company:zz-nimbo', 'Company:zz-cirro', 'Person:yy-centinela-civil'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n'
            f'- {{type: Company, name: Cirro, entity_id: {cirro}, role: Sociedad.}}\n'
            '- {type: Organization, name: "Estrato Gm✓bH", entity_id: Organization:zz-estrato, role: Sociedad.}\n'
            f'- {{type: Person, name: Centinela Civil, entity_id: {centinela}, role: Testigo.}}\n', encoding='utf-8')
        lista = {'aristas': [], 'alias': [], 'citas': []}
        uid = 'u' * 64
        pack = self.arista_del_seudonimo(uid, nimbo, 'WORKS_FOR')

        def veredictos(nota, hallazgo):
            (self.repo / seudonimos.RELATIONS_REL / 'relations.yaml').write_text(
                f"fix:\n  - {{source_id: {SEUDO}, target_id: {nimbo}, type: WORKS_FOR, doc: ATS_A.pdf, "
                f"set: {{note: {json.dumps(nota, ensure_ascii=False)}}}}}\n", encoding='utf-8')
            snap = self.base / f'snap-40-{seudonimos.sha16(nota)}'
            snap.mkdir(exist_ok=True)
            nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                     {'ref': 'n', 'labels': ['Company'], 'props': {'entity_id': nimbo, 'name': 'Nimbo'}},
                     {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                     {'ref': 'h', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': 'Zz Test y Nimbo.'}}]
            rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': 'WORKS_FOR',
                     'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': 'Zz Test y Nimbo.',
                               'note': nota}}]
            (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                              encoding='utf-8')
            (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                      encoding='utf-8')
            arbol = any(hallazgo(h.motivo) for h in seudonimos.auditar(self.repo, self.publicos))
            export = any(hallazgo(problema) for problema in seudonimos.verificar_snapshot(   # las personas, como el CLI
                snap, frozenset({SEUDO}), lista, personas=seudonimos.personas_contexto(self.repo),
                publicos=self.publicos, repo=self.repo))
            d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                         edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
            gate = any(hallazgo(error) for error in self.gate_del_repo(d))
            return arbol, export, gate, any(hallazgo(error) for error in self.posterior_del_repo(d))

        def dato(m):
            return seudonimos.MOTIVOS['dato-civil'] in m

        def civil(m):
            return 'nombra a un civil' in m and f'huella {seudonimos.sha16("Centinela Civil")}' in m
        for nota in ('Pagó a Cir«→ro».', 'Pagó a Cir"-"ro.', 'Pagado en dos-mil veinte eu→ros.',
                     'Pagado en dos-mil veinte dºsier.', 'Pagado en dos mil veinte dosier.', 'Pagó a Estrato.'):
            with self.subTest(dato=nota):
                self.assertEqual(veredictos(nota, dato), (True, True, True, True))
        with self.subTest(civil='Cen«→ti»nela Civil'):
            self.assertEqual(veredictos('Zz Test es Cen«→ti»nela Civil.', civil), (True, True, True, True))
        for nota in ('Pagó a Cir «→» ro.', 'Pagado en dos-mil veinte euros.'):
            with self.subTest(control=nota):
                self.assertEqual(veredictos(nota, dato), (False, False, False, False))

    def test_una_formula_partida_por_comillas_y_signos_en_los_cuatro_caminos(self):
        """AC195 (enmienda 40, reproducción de Codex), en una fila civil: el civil con el alias «Fuldo Fíctez, al«→ias»
        ZZ TEST» (en el contexto o no) y ese texto en su resumen: el resumen identifica al seudónimo en el árbol, el
        export, el gate y el control posterior, y el alias es uno que identifica en el árbol, el export y el gate
        (`check`), también con las comillas rectas y un guion («al"-"ias») o el signo fuera («al→«ias»»); antes, los
        tres pasaban los cuatro. Sin los textos en la salida."""
        lista = {'aristas': [], 'alias': [], 'citas': []}
        identificador = f'{seudonimos.MOTIVOS["alias-identificador"]}'
        for n, con_alias in itertools.product(range(3), (False, True)):
            alias = ('Fuldo Fíctez, al«→ias» ZZ TEST', 'Fuldo Fíctez, al"-"ias ZZ TEST',
                     'Fuldo Fíctez, al→«ias» ZZ TEST')[n]
            texto = f'{alias}.'
            resumen = f'huella {seudonimos.sha16(texto)}'
            with self.subTest(texto=n, alias_en_el_contexto=con_alias):
                (self.repo / 'pipeline/data/context/figures.yaml').write_text(
                    f'entities:\n- {{type: Person, name: Fuldo Fíctez, entity_id: {FULDO}, role: Asesor.'
                    + (f', aliases: [{json.dumps(alias, ensure_ascii=False)}]' if con_alias else '') + '}\n',
                    encoding='utf-8')
                (self.repo / A.SUM_PATH).write_text(
                    f'summaries:\n- entity_id: {FULDO}\n  name: Fuldo Fíctez\n'
                    f'  resumen: {json.dumps(texto, ensure_ascii=False)}\n', encoding='utf-8')
                hallazgos = [h.motivo for h in seudonimos.auditar(self.repo, self.publicos)]
                self.assertTrue(any('nombra a un seudónimo judicial' in m and resumen in m for m in hallazgos))
                self.assertEqual(any(identificador in m and f'huella {seudonimos.sha16(alias)}' in m
                                     for m in hallazgos), con_alias)
                snap = self.base / f'snap-195-{n}-{con_alias}'
                snap.mkdir(exist_ok=True)
                nodo = {'entity_id': FULDO, 'name': 'Fuldo Fíctez', 'summary': texto,
                        **({'aliases': [alias]} if con_alias else {})}
                (snap / 'nodes.jsonl').write_text(json.dumps({'ref': 'k', 'labels': ['Person'], 'props': nodo},
                                                             ensure_ascii=False) + '\n', encoding='utf-8')
                (snap / 'relationships.jsonl').write_text('', encoding='utf-8')
                problemas = seudonimos.verificar_snapshot(snap, frozenset({SEUDO}), lista, publicos=self.publicos,
                                                         repo=self.repo)
                self.assertTrue(any('nombra a un seudónimo judicial' in p and resumen in p for p in problemas))
                self.assertEqual(any(identificador in p and f'huella {seudonimos.sha16(alias)}' in p
                                     for p in problemas), con_alias)
                d = full_decision()
                d['summary'] = {'action': 'replace', 'resumen': texto, 'sources': [], 'reason': 'r'}
                d['_file'] = 'E-ac195.json'
                self.assertTrue(any(resumen in e for e in self.gate_del_repo(d)))
                self.assertTrue(any(resumen in e for e in self.posterior_del_repo(d)))
                self.write('E-ac195', d)
                codigo, out, err = self.cli('check')
                self.assertEqual(codigo, 1)
                self.assertEqual(f'{identificador} (huella {seudonimos.sha16(alias)})' in out, con_alias)
                self.assertNotIn('zz test', (out + err).lower())

    def test_las_lecturas_de_la_enmienda_41_en_los_cuatro_caminos(self):
        """AC202/AC205 (enmienda 41, reproducciones de Codex) en el árbol, el export, el gate y el control posterior,
        sobre la arista del seudónimo con su sociedad «Nimbo» (extremo propio): «Pagó a Nim«·»bo S.L.», «Nim«∕»bo S.L.»
        y «Nim«-»bo S.L.» son otra sociedad (antes, la lectura a través de las comillas los eximía en los cuatro), y una
        Organization del contexto «Cumulo Gm"b"H», por su núcleo, también: «Pagó a Cumulo.» (antes, sin núcleo, en
        ninguno). «Pagó a Nim-bo S.L.», el extremo propio, no."""
        nimbo, cirro, centinela = 'Company:zz-nimbo', 'Company:zz-cirro', 'Person:yy-centinela-civil'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n'
            f'- {{type: Company, name: Cirro, entity_id: {cirro}, role: Sociedad.}}\n'
            '- {type: Organization, name: "Cumulo Gm\\"b\\"H", entity_id: Organization:zz-cumulo, role: Sociedad.}\n'
            f'- {{type: Person, name: Centinela Civil, entity_id: {centinela}, role: Testigo.}}\n', encoding='utf-8')
        lista = {'aristas': [], 'alias': [], 'citas': []}
        uid = 'u' * 64
        pack = self.arista_del_seudonimo(uid, nimbo, 'WORKS_FOR')

        def veredictos(nota, hallazgo):
            (self.repo / seudonimos.RELATIONS_REL / 'relations.yaml').write_text(
                f"fix:\n  - {{source_id: {SEUDO}, target_id: {nimbo}, type: WORKS_FOR, doc: ATS_A.pdf, "
                f"set: {{note: {json.dumps(nota, ensure_ascii=False)}}}}}\n", encoding='utf-8')
            snap = self.base / f'snap-41-{seudonimos.sha16(nota)}'
            snap.mkdir(exist_ok=True)
            nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                     {'ref': 'n', 'labels': ['Company'], 'props': {'entity_id': nimbo, 'name': 'Nimbo'}},
                     {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                     {'ref': 'h', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': 'Zz Test y Nimbo.'}}]
            rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': 'WORKS_FOR',
                     'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': 'Zz Test y Nimbo.',
                               'note': nota}}]
            (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                              encoding='utf-8')
            (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                      encoding='utf-8')
            arbol = any(hallazgo(h.motivo) for h in seudonimos.auditar(self.repo, self.publicos))
            export = any(hallazgo(problema) for problema in seudonimos.verificar_snapshot(   # las personas, como el CLI
                snap, frozenset({SEUDO}), lista, personas=seudonimos.personas_contexto(self.repo),
                publicos=self.publicos, repo=self.repo))
            d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                         edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
            gate = any(hallazgo(error) for error in self.gate_del_repo(d))
            return arbol, export, gate, any(hallazgo(error) for error in self.posterior_del_repo(d))

        def dato(m):
            return seudonimos.MOTIVOS['dato-civil'] in m

        for nota in ('Pagó a Nim«\u00b7»bo S.L.', 'Pagó a Nim«\u2215»bo S.L.', 'Pagó a Nim«-»bo S.L.',
                     'Pagó a Cumulo.'):
            with self.subTest(dato=nota):
                self.assertEqual(veredictos(nota, dato), (True, True, True, True))
        with self.subTest(control='Pagó a Nim-bo S.L.'):
            self.assertEqual(veredictos('Pagó a Nim-bo S.L.', dato), (False, False, False, False))

    def test_cada_lectura_con_sus_campos_cerrados_en_los_cuatro_caminos(self):
        """AC208 (enmienda 42) en el árbol, el export, el gate y el control posterior, sobre la nota de la arista del
        seudónimo: un importe que solo se cierra tal cual («ocho"1999 euros») y una huella que solo se cierra sin
        comillas («10 ab«12»cd de mayo») dejan ver su fecha en los cuatro (con los campos cerrados del texto tal cual
        como los únicos, en ninguno); «Pagó 1999 euros.», un importe, no."""
        nimbo = 'Company:zz-nimbo'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n', encoding='utf-8')
        lista = {'aristas': [], 'alias': [], 'citas': []}
        uid = 'w' * 64
        pack = self.arista_del_seudonimo(uid, nimbo, 'WORKS_FOR')

        def veredictos(nota):
            (self.repo / seudonimos.RELATIONS_REL / 'relations.yaml').write_text(
                f"fix:\n  - {{source_id: {SEUDO}, target_id: {nimbo}, type: WORKS_FOR, doc: ATS_A.pdf, "
                f"set: {{note: {json.dumps(nota, ensure_ascii=False)}}}}}\n", encoding='utf-8')
            snap = self.base / f'snap-42-{seudonimos.sha16(nota)}'
            snap.mkdir(exist_ok=True)
            nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                     {'ref': 'n', 'labels': ['Company'], 'props': {'entity_id': nimbo, 'name': 'Nimbo'}},
                     {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                     {'ref': 'h', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': 'Zz Test y Nimbo.'}}]
            rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': 'WORKS_FOR',
                     'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': 'Zz Test y Nimbo.',
                               'note': nota}}]
            (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                              encoding='utf-8')
            (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                      encoding='utf-8')
            dato = seudonimos.MOTIVOS['dato-civil']
            arbol = any(dato in h.motivo for h in seudonimos.auditar(self.repo, self.publicos))
            export = any(dato in problema for problema in seudonimos.verificar_snapshot(   # las personas, como el CLI
                snap, frozenset({SEUDO}), lista, personas=seudonimos.personas_contexto(self.repo),
                publicos=self.publicos, repo=self.repo))
            d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                         edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
            gate = any(dato in error for error in self.gate_del_repo(d))
            return arbol, export, gate, any(dato in error for error in self.posterior_del_repo(d))

        for nota in ('Pagó «ocho"1999 euros».', 'Pagado el 10 ab«12»cd de mayo.'):
            with self.subTest(dato=nota):
                self.assertEqual(veredictos(nota), (True, True, True, True))
        with self.subTest(control='Pagó 1999 euros.'):
            self.assertEqual(veredictos('Pagó 1999 euros.'), (False, False, False, False))

    def test_una_formula_con_un_menos_y_un_salto_en_los_cuatro_caminos(self):
        """AC202 (enmienda 41), en una fila civil: el alias «Fuldo Fíctez, al«−\\nias» ZZ TEST» (en el contexto o no) y
        ese texto en su resumen identifican al seudónimo en el árbol, el export, el gate y el control posterior, y el
        alias es uno que identifica: con el plegado de las fichas, «−\\n» es un guion y un salto (antes, ni en la
        enmienda 39). Sin los textos en la salida."""
        lista = {'aristas': [], 'alias': [], 'citas': []}
        identificador = f'{seudonimos.MOTIVOS["alias-identificador"]}'
        for n, con_alias in itertools.product(range(1), (False, True)):
            alias = 'Fuldo Fíctez, al«\u2212\nias» ZZ TEST'
            texto = f'{alias}.'
            resumen = f'huella {seudonimos.sha16(texto)}'
            with self.subTest(texto=n, alias_en_el_contexto=con_alias):
                (self.repo / 'pipeline/data/context/figures.yaml').write_text(
                    f'entities:\n- {{type: Person, name: Fuldo Fíctez, entity_id: {FULDO}, role: Asesor.'
                    + (f', aliases: [{json.dumps(alias, ensure_ascii=False)}]' if con_alias else '') + '}\n',
                    encoding='utf-8')
                (self.repo / A.SUM_PATH).write_text(
                    f'summaries:\n- entity_id: {FULDO}\n  name: Fuldo Fíctez\n'
                    f'  resumen: {json.dumps(texto, ensure_ascii=False)}\n', encoding='utf-8')
                hallazgos = [h.motivo for h in seudonimos.auditar(self.repo, self.publicos)]
                self.assertTrue(any('nombra a un seudónimo judicial' in m and resumen in m for m in hallazgos))
                self.assertEqual(any(identificador in m and f'huella {seudonimos.sha16(alias)}' in m
                                     for m in hallazgos), con_alias)
                snap = self.base / f'snap-202-{n}-{con_alias}'
                snap.mkdir(exist_ok=True)
                nodo = {'entity_id': FULDO, 'name': 'Fuldo Fíctez', 'summary': texto,
                        **({'aliases': [alias]} if con_alias else {})}
                (snap / 'nodes.jsonl').write_text(json.dumps({'ref': 'k', 'labels': ['Person'], 'props': nodo},
                                                             ensure_ascii=False) + '\n', encoding='utf-8')
                (snap / 'relationships.jsonl').write_text('', encoding='utf-8')
                problemas = seudonimos.verificar_snapshot(snap, frozenset({SEUDO}), lista, publicos=self.publicos,
                                                         repo=self.repo)
                self.assertTrue(any('nombra a un seudónimo judicial' in p and resumen in p for p in problemas))
                self.assertEqual(any(identificador in p and f'huella {seudonimos.sha16(alias)}' in p
                                     for p in problemas), con_alias)
                d = full_decision()
                d['summary'] = {'action': 'replace', 'resumen': texto, 'sources': [], 'reason': 'r'}
                d['_file'] = 'E-ac202.json'
                self.assertTrue(any(resumen in e for e in self.gate_del_repo(d)))
                self.assertTrue(any(resumen in e for e in self.posterior_del_repo(d)))
                self.write('E-ac202', d)
                codigo, out, err = self.cli('check')
                self.assertEqual(codigo, 1)
                self.assertEqual(f'{identificador} (huella {seudonimos.sha16(alias)})' in out, con_alias)
                self.assertNotIn('zz test', (out + err).lower())

    def veredictos_de_una_arista(self, otro, nombre_otro, tipo, uid, nota, cita_arista, hallazgo, selector=None,
                                 alias=None):
        """Si se da `hallazgo` (un predicado sobre el texto de un error) en cada uno de los cuatro caminos con que se
        revisa la arista del seudónimo con `otro`, su `nota` y su cita: el árbol (una regla fix de la nota, con
        `selector` como su `quote_contains`), el export (`verificar_snapshot`), el gate y el control posterior. Una
        tupla (árbol, export, gate, posterior). `alias`: un segundo nombre del extremo en el export (o una lista)."""
        lista = {'aristas': [], 'alias': [], 'citas': []}
        regla = f"source_id: {SEUDO}, target_id: {otro}, type: {tipo}, doc: ATS_A.pdf"
        regla += f', quote_contains: {json.dumps(selector, ensure_ascii=False)}' if selector else ''
        (self.repo / seudonimos.RELATIONS_REL / 'relations.yaml').write_text(
            f"fix:\n  - {{{regla}, set: {{note: {json.dumps(nota, ensure_ascii=False)}}}}}\n", encoding='utf-8')
        snap = self.base / f'snap-42-{seudonimos.sha16(nota + cita_arista)}'
        snap.mkdir(exist_ok=True)
        etiqueta = 'Company' if otro.startswith('Company:') else 'Person'
        nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                 {'ref': 'n', 'labels': [etiqueta], 'props': {'entity_id': otro, 'name': nombre_otro,
                                                       **({'aliases': alias if isinstance(alias, list) else [alias]}
                                                          if alias else {})}},
                 {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                 {'ref': 'h', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': cita_arista}}]
        rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': tipo,
                 'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': cita_arista,
                           'note': nota}}]
        (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                          encoding='utf-8')
        (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                  encoding='utf-8')
        arbol = any(hallazgo(h.motivo) for h in seudonimos.auditar(self.repo, self.publicos))
        export = any(hallazgo(problema) for problema in seudonimos.verificar_snapshot(
            snap, frozenset({SEUDO}), lista, personas=seudonimos.personas_contexto(self.repo),
            publicos=self.publicos, repo=self.repo))
        pack = self.arista_del_seudonimo(uid, otro, tipo, cita=cita_arista)
        d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                     edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
        gate = any(hallazgo(error) for error in self.gate_del_repo(d))
        return arbol, export, gate, any(hallazgo(error) for error in self.posterior_del_repo(d))

    def test_la_pasada_base_exime_en_los_cuatro_caminos(self):
        """AC208 (enmienda 42, reproducciones de Codex) en el árbol, el export, el gate y el control posterior. (1) La
        nota de la arista del seudónimo con su sociedad «Nimbo» (extremo propio) «Pagó a Nim"bo.» lleva un dato civil en
        los cuatro (antes, en ninguno: la lectura a través de las comillas hallaba el extremo propio y lo eximía); «Pagó
        a Nimbo.», en ninguno. (2) La cita de la arista del seudónimo con un civil de nombre «Don Centi«−\\nnela Dortega
        Segundo» (el selector de su regla en el árbol, `source_quote` en el export y en el censo), «Don Centinela
        Dortega Segundo», nombra al civil ajeno «Centinela D'Ortega» en los cuatro (antes, en ninguno: el nombre propio
        leído a través de las comillas lo contenía y lo eximía). Sin los textos en la salida."""
        nimbo, propio, ajeno = 'Company:zz-nimbo', 'Person:yy-propio', 'Person:yy-centinela'
        nombre_propio, cita = 'Don Centi«−\nnela Dortega Segundo', 'Don Centinela Dortega Segundo'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n'
            f'- {{type: Person, name: {json.dumps(nombre_propio, ensure_ascii=False)}, entity_id: {propio}, '
            'role: Testigo.}\n'
            f'- {{type: Person, name: {json.dumps("Centinela D\'Ortega")}, entity_id: {ajeno}, role: Testigo.}}\n',
            encoding='utf-8')
        veredictos = self.veredictos_de_una_arista

        def dato(m):
            return seudonimos.MOTIVOS['dato-civil'] in m

        def nombra_ajeno(m):
            return 'nombra a un civil' in m and f'huella {seudonimos.sha16("Centinela D\'Ortega")}' in m
        for k, (nota, esperado) in enumerate((('Pagó a Nim"bo.', True), ('Pagó a Nimbo.', False))):
            with self.subTest(nota=nota):
                self.assertEqual(veredictos(nimbo, 'Nimbo', 'WORKS_FOR', f'{k}' * 64, nota, 'Zz Test y Nimbo.', dato),
                                 (esperado,) * 4)
        with self.subTest(cita=cita):
            self.assertEqual(veredictos(propio, nombre_propio, 'MET_WITH', '7' * 64, 'Nota neutra.', cita,
                                        nombra_ajeno, selector=cita), (True,) * 4)

    def test_una_exencion_solo_vale_para_lo_que_la_base_halla_en_los_cuatro_caminos(self):
        """AC211 (enmienda 43, reproducciones de Codex 34a) en el árbol, el export, el gate y el control posterior. (1)
        La cita de la arista del seudónimo con el civil propio «Don Centi Nela Civil Segundo», «Don Centi«nela»
        Civil Segundo» (también el selector de su regla en el árbol), nombra al civil ajeno «Centinela Civil» en los
        cuatro (antes, en ninguno: el nombre propio que la pasada base lee con la comilla en blanco eximía por
        posición lo que solo ve la lectura a través de ella); escrita «Don «Centinela» Civil Segundo» con el propio
        «Don Centinela Civil Segundo», no (la base lee las dos y exime). (2) La nota de la arista con la sociedad
        propia «Nim Bo», «Pagó a Nim«bo».», lleva un dato civil (la ajena «Nimbo») en los cuatro; «Pagó a «Nim
        Bo».», no."""
        ajeno, nim_bo, nimbo = 'Person:yy-ajeno', 'Company:zz-nim-bo', 'Company:zz-nimbo'
        figuras = (f'entities:\n- {{type: Company, name: Nim Bo, entity_id: {nim_bo}, role: Sociedad.}}\n'
                   f'- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n'
                   f'- {{type: Person, name: Centinela Civil, entity_id: {ajeno}, role: Testigo.}}\n')
        for eid, etiqueta, nombre in ((nim_bo, 'Company', 'Nim Bo'), (nimbo, 'Company', 'Nimbo'),
                                      (ajeno, 'Person', 'Centinela Civil')):
            nodo_del_censo(self.root, eid, etiqueta, nombre)

        def nombra_ajeno(m):
            return 'nombra a un civil' in m and f'huella {seudonimos.sha16("Centinela Civil")}' in m

        def dato(m):
            return seudonimos.MOTIVOS['dato-civil'] in m
        for k, (nombre_propio, cita, esperado) in enumerate((
                ('Don Centi Nela Civil Segundo', 'Don Centi\u00abnela\u00bb Civil Segundo', True),
                ('Don Centi Nela Civil Segundo', 'Don Centi"nela" Civil Segundo', True),
                ('Don Centinela Civil Segundo', 'Don \u00abCentinela\u00bb Civil Segundo', False))):
            propio = f'Person:yy-propio-{k}'
            (self.repo / 'pipeline/data/context/figures.yaml').write_text(
                figuras + f'- {{type: Person, name: {json.dumps(nombre_propio)}, entity_id: {propio}, '
                'role: Testigo.}\n', encoding='utf-8')
            nodo_del_censo(self.root, propio, 'Person', nombre_propio)
            with self.subTest(cita=cita):
                self.assertEqual(self.veredictos_de_una_arista(propio, nombre_propio, 'MET_WITH', f'{k}c' * 32,
                                                               'Nota neutra.', cita, nombra_ajeno, selector=cita),
                                 (esperado,) * 4)
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(figuras, encoding='utf-8')
        for k, (nota, esperado) in enumerate((('Pagó a Nim\u00abbo\u00bb.', True),
                                              ('Pagó a \u00abNim Bo\u00bb.', False))):
            with self.subTest(nota=nota):
                self.assertEqual(self.veredictos_de_una_arista(nim_bo, 'Nim Bo', 'WORKS_FOR', f'{k}s' * 32, nota,
                                                               'Zz Test y Nim Bo.', dato), (esperado,) * 4)

    def test_lo_que_una_lectura_ve_fuera_de_un_extremo_propio_en_los_cuatro_caminos(self):
        """AC211 (enmienda 43, refinada al contrastar la entrega) en el árbol, el export, el gate y el control
        posterior. La nota de la arista del seudónimo con su sociedad «Nimbo» (extremo propio) «Ver Cen«»tinela y
        Nimbo.» y «Ver Cen«-»tinela y Nimbo.» lleva un dato civil en los cuatro, como «Ver Centinela y Nimbo.» (antes,
        en ninguno: la pasada base, que lee las comillas como blancos, daba por propia la mención que la lectura a
        través de ellas ve unida a «Centinela» y eximía lo que esta denuncia); «Pagó a Nimbo.», «Pagó a «Nimbo».» y
        «Ver Cen«.»tinela y Nimbo.» (el punto cierra la frase), en ninguno. Sin los textos en la salida."""
        nimbo = 'Company:zz-nimbo'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n', encoding='utf-8')

        def dato(m):
            return seudonimos.MOTIVOS['dato-civil'] in m
        for k, (nota, esperado) in enumerate((('Ver Centinela y Nimbo.', True),
                                              ('Ver Cen\u00ab\u00bbtinela y Nimbo.', True),
                                              ('Ver Cen\u00ab-\u00bbtinela y Nimbo.', True),
                                              ('Pagó a Nimbo.', False), ('Pagó a \u00abNimbo\u00bb.', False),
                                              ('Ver Cen\u00ab.\u00bbtinela y Nimbo.', False))):
            with self.subTest(nota=nota):
                self.assertEqual(self.veredictos_de_una_arista(nimbo, 'Nimbo', 'WORKS_FOR', f'{k}' * 64, nota,
                                                               'Zz Test y Nimbo.', dato), (esperado,) * 4)

    def test_la_exencion_de_personas_e_identificacion_en_los_cuatro_caminos(self):
        """AC215/AC216 (enmienda 44, reproducciones de Codex 35a) en el árbol, el export, el gate y el control
        posterior, sobre la cita de la arista del seudónimo con un civil propio (el selector de su regla en el
        árbol). (1) El extremo propio «Centinela Civil Al Ba» y el civil ajeno «Centinela Civil»: «Centinela Civil
        Al«ba»» nombra al ajeno en los cuatro (antes, en ninguno: la base lo daba por eximido por el propio, y a
        través de las comillas el texto dice «Alba»); escrita «Centinela Civil Al Ba», no. (2) El extremo propio con
        los nombres «Zz Test Al Ias Centinela Civil» y «Centinela Civil»: «Zz Test al«ias» Centinela Civil»
        identifica al seudónimo con un civil en los cuatro (antes, en ninguno: a través de las comillas aparece
        «alias», que invalida el nombre largo que eximía a «Zz Test»); «Zz Test al ias Centinela Civil», no."""
        ajeno = 'Person:yy-ajeno'
        base = (f'entities:\n- {{type: Person, name: Centinela Civil, entity_id: {ajeno}, role: Testigo.}}\n')
        nodo_del_censo(self.root, ajeno, 'Person', 'Centinela Civil')

        def nombra_ajeno(m):
            return 'nombra a un civil' in m and f'huella {seudonimos.sha16("Centinela Civil")}' in m
        comillas = ('\u00ab\u00bb', '\u201c\u201d', '\u2018\u2019', '\u2039\u203a', '\u201e\u201c', '""',
                    '\u00ab-\u00bb', '\u00ab\u00b7\u00bb', '\u2e42\u2e42', '\u275d\u275e')
        casos = [(f'Centinela Civil Al{q[0]}ba{q[-1]}', True) for q in comillas]
        casos += [('Al\u2018\u00b7\u2019ba Centinela Civil', True), ('Centinela Civil Al Ba', False)]
        for k, (cita, esperado) in enumerate(casos):
            propio = f'Person:yy-propio-{k}'
            (self.repo / 'pipeline/data/context/figures.yaml').write_text(
                base + f'- {{type: Person, name: Centinela Civil Al Ba, entity_id: {propio}, role: Testigo.}}\n',
                encoding='utf-8')
            nodo_del_censo(self.root, propio, 'Person', 'Centinela Civil Al Ba')
            with self.subTest(ajeno=cita):
                self.assertEqual(self.veredictos_de_una_arista(propio, 'Centinela Civil Al Ba', 'MET_WITH',
                                                               f'{k:02x}d' * 21 + 'd', 'Nota neutra.', cita,
                                                               nombra_ajeno, selector=cita), (esperado,) * 4)
        casos = [(f'Zz Test al{q[0]}ias{q[-1]} Centinela Civil', True) for q in comillas]
        casos += [('Zz Te\u2018-\u2019st alias Centinela Civil', True),
                  ('Zz Test, al\u00abias\u00bb: Centinela Civil', True), ('Zz Test al ias Centinela Civil', False)]
        for k, (cita, esperado) in enumerate(casos):
            propio = f'Person:yy-largo-{k}'
            (self.repo / 'pipeline/data/context/figures.yaml').write_text(
                f'entities:\n- {{type: Person, name: Zz Test Al Ias Centinela Civil, entity_id: {propio}, '
                'role: Testigo., aliases: [Centinela Civil]}\n', encoding='utf-8')
            nodo_del_censo(self.root, propio, 'Person', 'Zz Test Al Ias Centinela Civil')

            def identifica(m, cita=cita):
                return 'nombra a un civil' in m and f'huella {seudonimos.sha16(cita)}' in m
            with self.subTest(identifica=cita):
                self.assertEqual(self.veredictos_de_una_arista(propio, 'Zz Test Al Ias Centinela Civil', 'MET_WITH',
                                                               f'{k:02x}e' * 21 + 'e', 'Nota neutra.', cita,
                                                               identifica, selector=cita, alias='Centinela Civil'),
                                 (esperado,) * 4)

    def test_el_presupuesto_de_la_exencion_denuncia_en_los_cuatro_caminos(self):
        """AC224 (enmienda 47) en el árbol, el export, el gate y el control posterior, sobre la cita de la arista del
        seudónimo con un civil propio «Cirro-Estrato-Alto» y otro ajeno «Cirro-Estrato» dentro de él. «Vio a
        Cirro-Estrato-Alto ayer» no nombra al ajeno en ninguno (la exención lo decide: está dentro del propio en toda
        lectura); con el tope del presupuesto de la exención en 0 lo nombra en los cuatro (no pudo decidirlo: fallo
        cerrado, se denuncia), y el propio entero no se denuncia nunca."""
        propio, ajeno = 'Person:yy-propio', 'Person:yy-ajeno'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Person, name: Cirro-Estrato, entity_id: {ajeno}, role: Testigo.}}\n'
            f'- {{type: Person, name: Cirro-Estrato-Alto, entity_id: {propio}, role: Testigo.}}\n', encoding='utf-8')
        nodo_del_censo(self.root, ajeno, 'Person', 'Cirro-Estrato')
        nodo_del_censo(self.root, propio, 'Person', 'Cirro-Estrato-Alto')
        cita = 'Vio a Cirro-Estrato-Alto ayer'

        def nombra_ajeno(m):
            return 'nombra a un civil' in m and f'huella {seudonimos.sha16("Cirro-Estrato")}' in m
        for k, (tope, esperado) in enumerate(((seudonimos._TOPE_EXENCION, False), (0, True))):
            with self.subTest(tope=tope), mock.patch.object(seudonimos, '_TOPE_EXENCION', tope):
                self.assertEqual(self.veredictos_de_una_arista(propio, 'Cirro-Estrato-Alto', 'MET_WITH',
                                                               f'{k:02x}f' * 21 + 'f', 'Nota neutra.', cita,
                                                               nombra_ajeno, selector=cita), (esperado,) * 4)

    def test_el_presupuesto_de_la_exencion_es_determinista_en_los_cuatro_caminos(self):
        """AC226 (enmienda 48, reproducción de Codex 38a) en el árbol, el export, el gate y el control posterior, sobre
        la cita de la arista del seudónimo con un civil propio de dos nombres («Cirro-Estrato-Alto Bajo» y su alias
        «Cirro-Estrato-Alto-Bajo») y otro ajeno «Cirro-Estrato» dentro de ambos. Con topes pequeños del presupuesto de
        la exención (el borde cae donde cae), vaciar la caché de `_candidato` y darle a los candidatos otras direcciones
        (`disposicion`) no cambia el veredicto (antes, entre ninguna denuncia y denunciar al ajeno) y los cuatro caminos
        coinciden."""
        propio, ajeno = 'Person:yy-propio', 'Person:yy-ajeno'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Person, name: Cirro-Estrato, entity_id: {ajeno}, role: Testigo.}}\n'
            f'- {{type: Person, name: Cirro-Estrato-Alto Bajo, entity_id: {propio}, role: Testigo., '
            'aliases: [Cirro-Estrato-Alto-Bajo]}\n', encoding='utf-8')
        nodo_del_censo(self.root, ajeno, 'Person', 'Cirro-Estrato')
        nodo_del_censo(self.root, propio, 'Person', 'Cirro-Estrato-Alto Bajo')
        cita = 'Cirro-Estrato-Alto-Bajo'

        def nombra_ajeno(m):
            return 'nombra a un civil' in m and f'huella {seudonimos.sha16("Cirro-Estrato")}' in m
        filas = [(propio, 'Cirro-Estrato-Alto Bajo'), (propio, 'Cirro-Estrato-Alto-Bajo'), (ajeno, 'Cirro-Estrato')]

        def precalienta(azar):   # `_candidato` con las mismas filas barajadas (el roster del árbol)
            barajadas = list(filas)
            azar.shuffle(barajadas)
            seudonimos.indice_civiles(barajadas, frozenset({SEUDO}))
        for tope in (60, 110, 120, 150):
            veredictos = set()
            for k in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9):
                with self.subTest(tope=tope, disposicion=k), disposicion(k, precalienta), \
                        mock.patch.object(seudonimos, '_TOPE_EXENCION', tope):
                    uid = f'{tope:03x}{k:x}f' * 12 + 'f' * 4
                    veredictos.add(self.veredictos_de_una_arista(
                        propio, 'Cirro-Estrato-Alto Bajo', 'MET_WITH', uid[:64], 'Nota neutra.', cita, nombra_ajeno,
                        selector=cita, alias='Cirro-Estrato-Alto-Bajo'))
            with self.subTest(tope=tope):
                self.assertEqual(len(veredictos), 1)
                self.assertEqual(len(set(next(iter(veredictos)))), 1)   # los cuatro caminos coinciden

    @lento
    def test_el_presupuesto_con_el_tope_real_es_determinista_en_los_cuatro_caminos(self):
        """AC226 (enmienda 48, reproducción de Codex 38b-2) en el árbol, el export, el gate y el control posterior, con
        el tope real: un civil propio de 200 piezas distintas de tres letras, sus prefijos de 2 a 199 piezas como
        civiles ajenos y el propio entero como cita de la arista del seudónimo (799 caracteres). Sin presupuesto, ni el
        árbol ni el export denuncian a ningún ajeno; el gate y el control posterior, a 14 (los de 2 a 15 piezas, igual
        que en `e715474`): revisan el selector de la regla pública, los primeros `QUOTE_CHARS` (60) de la cita
        (`_quote_contains`), donde el propio no está entero. Con presupuesto, los ajenos que denuncia cada camino (por
        su huella; se agota y cada camino lo gasta a su manera, así que entre ellos no coinciden), siempre una parte
        mayor que sin él, no cambian con la disposición en memoria de los candidatos (`disposicion`: en frío,
        precalentada con las mismas filas barajadas y con otras direcciones; antes, de 84 a 97 según la ejecución)."""
        piezas = piezas_distintas(3, 200, 3)
        propio, nombre = 'Person:yy-propio', '-'.join(piezas)
        ajenos = {f'Person:yy-ajeno{k}': '-'.join(piezas[:k]) for k in range(2, 200)}
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            'entities:\n' + ''.join(f'- {{type: Person, name: {n}, entity_id: {e}, role: Testigo.}}\n'
                                    for e, n in {propio: nombre, **ajenos}.items()), encoding='utf-8')
        for e, n in {propio: nombre, **ajenos}.items():
            nodo_del_censo(self.root, e, 'Person', n)
        visto = []

        def con_marca(camino, funcion):
            def envuelta(*args, **kwargs):
                visto.append(camino)
                return funcion(*args, **kwargs)
            return envuelta

        def registra(mensaje):
            visto.append(mensaje)
            return False
        filas = [(propio, nombre), *ajenos.items()]

        def precalienta(azar):   # `_candidato` con las mismas filas barajadas (el roster del árbol)
            barajadas = list(filas)
            azar.shuffle(barajadas)
            seudonimos.indice_civiles(barajadas, frozenset({SEUDO}))

        unidades = itertools.count()

        def denunciados_por_camino(k, tope):
            del visto[:]
            with disposicion(k, precalienta), mock.patch.object(seudonimos, '_TOPE_EXENCION', tope), \
                    mock.patch.object(seudonimos, 'auditar', con_marca(0, seudonimos.auditar)), \
                    mock.patch.object(seudonimos, 'verificar_snapshot', con_marca(1, seudonimos.verificar_snapshot)), \
                    mock.patch.object(self, 'gate_del_repo', con_marca(2, self.gate_del_repo)), \
                    mock.patch.object(self, 'posterior_del_repo', con_marca(3, self.posterior_del_repo)):
                self.veredictos_de_una_arista(propio, nombre, 'MET_WITH', f'{next(unidades):02x}b' * 21 + 'b',
                                              'Nota neutra.', nombre, registra, selector=nombre)
            caminos, camino = {0: set(), 1: set(), 2: set(), 3: set()}, None
            for x in visto:
                if isinstance(x, int):
                    camino = x
                elif 'nombra a un civil' in x:
                    caminos[camino].add(re.search(r'huella ([0-9a-f]+)', x)[1])
            return {c: frozenset(v) for c, v in caminos.items()}
        sin_tope = denunciados_por_camino(0, float('inf'))
        self.assertEqual((sin_tope[0], sin_tope[1]), (frozenset(), frozenset()))   # sin presupuesto: árbol y export
        denunciados = [denunciados_por_camino(k, seudonimos._TOPE_EXENCION) for k in (0, 1, 2, 5, 6)]
        for c, nombre_camino in enumerate(('árbol', 'export', 'gate', 'control posterior')):
            with self.subTest(camino=nombre_camino):
                self.assertEqual(len({d[c] for d in denunciados}), 1)
                self.assertLessEqual(sin_tope[c], denunciados[0][c])   # fallo cerrado: solo añade denuncias
        self.assertGreater(len(denunciados[0][0]), 0)   # con el tope real, el propio de 200 piezas lo agota

    def denunciados_por_camino(self, propio, nombre, alias, cita, filas, k, tope, uid):
        """AC229 (enmienda 49): los ajenos (por su huella) que denuncia cada uno de los cuatro caminos (árbol, export,
        gate y control posterior) en la disposición `k` (`disposicion`: en frío, precalentada con las `filas` barajadas
        y con otras direcciones) y el `tope`, sobre la cita de la arista del seudónimo con el civil propio `propio`
        (`nombre` y `alias`): {camino: frozenset(huellas)}."""
        visto = []

        def con_marca(camino, funcion):
            def envuelta(*args, **kwargs):
                visto.append(camino)
                return funcion(*args, **kwargs)
            return envuelta

        def registra(mensaje):
            visto.append(mensaje)
            return False

        def precalienta(azar):   # `_candidato` con las mismas filas barajadas (el roster del árbol)
            barajadas = list(filas)
            azar.shuffle(barajadas)
            seudonimos.indice_civiles(barajadas, frozenset({SEUDO}))
        with disposicion(k, precalienta), mock.patch.object(seudonimos, '_TOPE_EXENCION', tope), \
                mock.patch.object(seudonimos, 'auditar', con_marca(0, seudonimos.auditar)), \
                mock.patch.object(seudonimos, 'verificar_snapshot', con_marca(1, seudonimos.verificar_snapshot)), \
                mock.patch.object(self, 'gate_del_repo', con_marca(2, self.gate_del_repo)), \
                mock.patch.object(self, 'posterior_del_repo', con_marca(3, self.posterior_del_repo)):
            self.veredictos_de_una_arista(propio, nombre, 'MET_WITH', uid, 'Nota neutra.', cita, registra,
                                          selector=cita, alias=alias)
        caminos, camino = {0: set(), 1: set(), 2: set(), 3: set()}, None
        for x in visto:
            if isinstance(x, int):
                camino = x
            elif 'nombra a un civil' in x:
                caminos[camino].add(re.search(r'huella ([0-9a-f]+)', x)[1])
        return {c: frozenset(v) for c, v in caminos.items()}

    def test_el_desempate_de_las_lecturas_del_candidato_es_determinista_en_los_cuatro_caminos(self):
        """AC229 (enmienda 49, reproducciones de Codex 39a, 39b y 39c) en el árbol, el export, el gate y el control
        posterior: el orden de las apariciones desempataba por el orden de un conjunto de candidatos (identidades en
        memoria) cuando dos lecturas del mismo nombre (el original con una comilla en medio y su lectura a través de
        ella: sus letras y fronteras) empataban en `(inicio, fin, nombre, entidad, mínimo, excluidas)`, y con un
        presupuesto que se agota lo que denunciaba dependía de la precarga de la caché y de `PYTHONHASHSEED`. Los
        casos de `_LECTURAS_DEL_MISMO_NOMBRE` (el propio con sus nombres y alias, los ajenos que caen dentro y la
        cita), con los topes de las reproducciones y vecinos. Siempre lo mismo en cada disposición en memoria de los
        candidatos y parte de lo que denuncia cada camino sin tope (fallo cerrado: el presupuesto solo añade
        denuncias)."""
        topes = {'39a': (290, 300), '39b': (480, 490), '39c': (252,), 'barrido 1': (290,), 'barrido 2': (430, 440),
                 'barrido 3': (370,)}
        unidades = itertools.count()
        for i, (caso, (propios, ajenos, cita)) in enumerate(_LECTURAS_DEL_MISMO_NOMBRE.items()):
            propio = f'Person:yy-propio{i}'
            nombres = {propio: propios, **{f'Person:yy-ajeno{i}{k}': [n] for k, n in enumerate(ajenos)}}
            (self.repo / 'pipeline/data/context/figures.yaml').write_text(
                'entities:\n' + ''.join(
                    f'- {{type: Person, name: {json.dumps(ns[0], ensure_ascii=False)}, entity_id: {e}, role: Testigo.'
                    + (f', aliases: {json.dumps(ns[1:], ensure_ascii=False)}' if len(ns) > 1 else '') + '}\n'
                    for e, ns in nombres.items()), encoding='utf-8')
            for e, ns in nombres.items():
                nodo_del_censo(self.root, e, 'Person', ns[0])
            filas = [(e, n) for e, ns in nombres.items() for n in ns]
            alias = propios[1:] or None

            def por_camino(k, tope):
                return self.denunciados_por_camino(propio, propios[0], alias, cita, filas, k, tope,
                                                   f'{next(unidades):02x}a' * 21 + 'a')
            sin_tope = por_camino(0, float('inf'))
            for tope in topes[caso]:
                denunciados = [por_camino(k, tope) for k in (0, 1, 2, 3, 5, 6)]
                for c, camino in enumerate(('árbol', 'export', 'gate', 'control posterior')):
                    with self.subTest(caso=caso, tope=tope, camino=camino):
                        self.assertEqual(len({d[c] for d in denunciados}), 1)
                        self.assertLessEqual(sin_tope[c], denunciados[0][c])   # fallo cerrado

    @lento
    def test_la_expulsion_de_la_cache_de_candidatos_no_cambia_el_veredicto_en_los_cuatro_caminos(self):
        """AC232 (enmienda 50, reproducción de Codex 40a) en el árbol, el export, el gate y el control posterior: la
        caché de `_candidato` (8.192 entradas) expulsaba el candidato del propio cuando entre sus filas se metían más
        nombres, y su homónimo ajeno (el mismo nombre) dejaba de ser «el mismo candidato desde la misma pieza»: pasaba a
        decidirse por el presupuesto de la exención, que con un tope pequeño lo agotaba y lo denunciaba. Los casos de
        `_HOMONIMOS` (la cita de la arista del seudónimo con el civil propio y un ajeno homónimo, y la ajena
        «Cirro-Estrato» dentro de ambos; el propio de 27 piezas con el tope real) con las filas juntas, con filas de
        relleno entre el propio y el ajeno en el contexto (ocho, con una caché de `_candidato` de cuatro entradas, que
        expulsa igual y deja correr todas las combinaciones; y `RELLENO`, más que las 8.192 de la real, una vez), con el
        ajeno antes, en frío, con la caché precalentada con las mismas filas barajadas, con otras direcciones para los
        candidatos (`disposicion`) y sin caché: los cuatro caminos dan lo mismo en todas, el homónimo del propio nunca
        se denuncia, y lo que denuncian es parte de lo que denuncian sin tope (fallo cerrado)."""
        topes = {'homónimos': (0, 60), 'homónimos con ajena dentro': (60, 110, 150),
                 'homónimos de 27 piezas': (seudonimos._TOPE_EXENCION,)}
        unidades = itertools.count()
        pequena = functools.lru_cache(maxsize=4)(seudonimos._candidato.__wrapped__)
        for i, caso in enumerate(topes):
            propios, ajenos, cita = _HOMONIMOS[caso]
            propio = f'Person:yy-propio{i}'
            nombres = {propio: propios[0], **{f'Person:yy-ajeno{i}{k}': n for k, n in enumerate(ajenos)}}
            for e, n in nombres.items():
                nodo_del_censo(self.root, e, 'Person', n)
            filas = list(nombres.items())

            def por_camino(k, tope, relleno=0, orden=False, candidato=None):
                entidades = list(nombres.items())
                if orden:   # el homónimo ajeno antes que el propio
                    entidades[0], entidades[1] = entidades[1], entidades[0]
                entidades[1:1] = filas_de_relleno(relleno)
                (self.repo / 'pipeline/data/context/figures.yaml').write_text(
                    'entities:\n' + ''.join(
                        f'- {{type: Person, name: {json.dumps(n, ensure_ascii=False)}, entity_id: {e}, '
                        'role: Testigo.}\n' for e, n in entidades), encoding='utf-8')
                with mock.patch.object(seudonimos, '_candidato', candidato or seudonimos._candidato):
                    return self.denunciados_por_camino(propio, propios[0], None, cita, filas, k, tope,
                                                       f'{next(unidades):02x}c' * 21 + 'c')
            sin_tope = por_camino(0, float('inf'))
            for j, tope in enumerate(topes[caso]):
                referencia = por_camino(0, tope)
                variantes = {'relleno': dict(k=0, relleno=8, candidato=pequena),
                             'relleno, el ajeno antes': dict(k=0, relleno=8, orden=True, candidato=pequena)}
                if j == 0:
                    variantes |= {'precalentada': dict(k=1, relleno=8, candidato=pequena),
                                  'otra dirección': dict(k=5, relleno=8, candidato=pequena),
                                  'sin caché': dict(k=0, candidato=seudonimos._candidato.__wrapped__)}
                if caso == 'homónimos con ajena dentro' and tope == 60:
                    variantes['RELLENO'] = dict(k=0, relleno=RELLENO)   # la caché real, expulsada de verdad
                for variante, argumentos in variantes.items():
                    resultado = por_camino(tope=tope, **argumentos)
                    for c, camino in enumerate(('árbol', 'export', 'gate', 'control posterior')):
                        with self.subTest(caso=caso, tope=tope, variante=variante, camino=camino):
                            self.assertEqual(resultado[c], referencia[c])
                            self.assertLessEqual(sin_tope[c], resultado[c])   # fallo cerrado
                            self.assertNotIn(seudonimos.sha16(propios[0]), resultado[c])   # el homónimo, nunca

    @lento
    def test_el_coste_de_la_identificacion_al_extremo_en_el_arbol_y_el_snapshot(self):
        """AC227 (enmienda 48, reproducción de Codex 38b-1) en el árbol y el export con fixtures en memoria (`auditar`
        y `verificar_snapshot`): la cita de la arista del seudónimo con un civil propio de 27 piezas «ab» (el nombre más
        largo del censo) y otro ajeno «ab-ab» dentro, de «a-b» y «a-8» al azar, con la cita una sola vez (en el
        selector de la regla del árbol; en el `source_quote` del export, sin la regla). Antes, 1,8 y 2,8 s en el árbol y
        3,2 y 4,8 s en el export con 8.000 y 12.000 caracteres (×16 al cuadruplicar las piezas de la repro); ahora el
        tiempo crece como el texto (×4/×1 ≤ 4,5 de 4.000 a 16.000 caracteres, el criterio principal, independiente de la
        máquina) y, en absoluto, el árbol tarda 0,6 y 0,9 s y el export 1,2 y 1,8 s con 8.000 y 12.000 caracteres (con y
        sin el nombre propio entero una vez más): por debajo de 3 s, un umbral provisional. La spec pide ≤ 1 s y el
        export no lo alcanza: revisa la cita dos veces (`problemas_seudonimo` y `prueba_conservada`), y el suelo
        lineal de esa revisión, con el presupuesto en 0, ya es de 0,70 s a 8.000 caracteres y 1,33 s a 12.000 (el árbol,
        0,37 y 0,68 s); cada saturación del presupuesto de la exención (100.000 unidades) suma de 0,2 a 0,25 s.
        AC230 (enmienda 49, 39b-2): también con el propio «ab…ab-zz» (sin su nombre completo en la cita, el recorrido de
        `_bigrama_en` que materializaba los tramos y daba 3,4 s en el predicado, 3,8 en el árbol y 7,6 en el snapshot
        con 12.000 caracteres). Con `_pdfs_en` (AC231: la búsqueda de ficheros «.pdf» de la cita era cuadrática y
        formaba parte del suelo lineal del export), el árbol tarda 0,2 a 0,5 s y el export 0,3 a 1,0 s a 12.000
        caracteres: el árbol, por debajo de 1 s (la spec); el export, de 3 s (el umbral revisado de la enmienda 48).
        AC235/AC236 (enmienda 52): cada medida en frío (`vaciar_caches()`), con el mínimo de dos entre medidas
        equivalentes, y afirmando que se ejecutaron `problemas_seudonimo`, `_nombres_propios` y `_menciona_extremo`.
        AC241 (enmienda 54): cada medida es un proceso hijo (`en_hijo`), con el mínimo solo entre dos repeticiones
        frías de la MISMA cita, y la medida (`medir_en_frio`) nace del servidor limpio (AC243); antes la
        medida corría en el padre con las cachés vaciadas y una memoria por texto de un control inyectado la hacía
        pagar una vez: 2 s la primera vez que `_fichas_de` lee un texto de 4.000 o de 16.000 caracteres pasaba el
        crecimiento ×4/×1. AC335 (enmienda 90): las cotas de 1 y 3 s son a factor 1 y llevan el de `SM_FACTOR_DE_CPU`
        (`escalada`); la razón ×4/×1 no se escala."""
        rutas = ('problemas_seudonimo', '_nombres_propios', '_menciona_extremo')
        for variante, nombre in enumerate(('-'.join(['ab'] * 27), '-'.join(['ab'] * 26 + ['zz']))):
            propio, ajeno = f'Person:yy-propio{variante}', f'Person:yy-ajeno{variante}'
            (self.repo / 'pipeline/data/context/figures.yaml').write_text(
                f'entities:\n- {{type: Person, name: ab-ab, entity_id: {ajeno}, role: Testigo.}}\n'
                f'- {{type: Person, name: {nombre}, entity_id: {propio}, role: Testigo.}}\n', encoding='utf-8')
            nodo_del_censo(self.root, ajeno, 'Person', 'ab-ab')
            nodo_del_censo(self.root, propio, 'Person', nombre)
            regla, snap = self.repo / seudonimos.RELATIONS_REL / 'relations.yaml', self.base / 'snap-227'
            snap.mkdir(exist_ok=True)
            lista = {'aristas': [], 'alias': [], 'citas': []}

            def cuesta(camino, caracteres, extra='', limite=None, repeticiones=2) -> list:
                """Los tiempos de CPU de hasta `repeticiones` revisiones EN FRÍO (un hijo cada una) de la MISMA cita en
                el camino, parando en la primera por debajo de `limite` si lo hay."""
                azar = random.Random(17)
                cita = '-'.join(azar.choice(['a-b', 'a-8']) for _ in range(caracteres // 3))[:caracteres] + extra
                nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                         {'ref': 'n', 'labels': ['Person'], 'props': {'entity_id': propio, 'name': nombre}},
                         {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                         {'ref': 'h', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': cita}}]
                rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': 'MET_WITH',
                         'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': cita,
                                   'note': 'Nota neutra.'}}]
                (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n) + '\n' for n in nodos), encoding='utf-8')
                (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rels), encoding='utf-8')
                if camino == 'árbol':
                    regla.parent.mkdir(parents=True, exist_ok=True)
                    regla.write_text(
                        f"fix:\n  - {{source_id: {SEUDO}, target_id: {propio}, type: MET_WITH, doc: ATS_A.pdf, "
                        f"quote_contains: {json.dumps(cita)}, set: {{note: 'Nota neutra.'}}}}\n", encoding='utf-8')
                else:
                    regla.unlink(missing_ok=True)
                tiempos = []
                for _ in range(repeticiones):
                    tiempos.append(en_hijo(medir_en_frio, camino, self.repo, snap, lista, rutas, 20, plazo=escalada(200) + 30))
                    if limite is not None and tiempos[-1] < limite:
                        break
                return tiempos
            for camino in ('árbol', 'export'):
                limite = escalada(1.0 if camino == 'árbol' else 3.0)
                for caracteres, entero in itertools.product((8000, 12000), (False, True)):
                    with self.subTest(camino=camino, caracteres=caracteres, entero=entero):
                        self.assertLess(min(cuesta(camino, caracteres, ' ' + nombre if entero else '', limite)), limite)
                with self.subTest(camino=camino, crecimiento='4.000 a 16.000 caracteres'):
                    mejor = None   # la mejor de hasta tres mediciones completas, como `mejor_razon`
                    for _ in range(3):
                        tiempos = {k: min(cuesta(camino, 4000 * k)) for k in (1, 4)}
                        mejor = min(mejor or 1e9, tiempos[4] / tiempos[1])
                        if mejor <= 4.5:
                            break
                    self.assertLessEqual(mejor, 4.5)

    def tiempos_en_frio(self, camino, rejilla, n, elecciones, limite, entrada=0, intentos=5) -> list:
        """AC235 (enmienda 52, Codex 42c), AC238 (enmienda 53, Codex 43b-3): los tiempos de CPU de hasta `intentos`
        ejecuciones de `camino` (`árbol`: `auditar`; `snapshot`: `verificar_snapshot`) sobre la cita de la rejilla
        (AC234) de UNA entrada (`ENTRADAS_DE_NOMBRE_LARGO[entrada]`: la semilla de la cita y la letra de los nombres),
        parando en la primera por debajo de `limite`. Cada ejecución es un proceso hijo nuevo (`en_hijo`) con las
        cachés vacías: el uso real mide la primera llamada de cada entrada, y ni una caché ni una memoria por texto,
        por nombre o por id deja la segunda más barata que la primera. El mínimo es, así, entre repeticiones frías de
        la MISMA entrada; con otra entrada por intento, bastaba que una cumpliera la cota (43b-3: 1,1 s en cada
        `_nombres_en` cuyo texto empieza por «a-b»; árbol y snapshot aprobaban a 96 piezas con [1,461, 0,311] y
        [2,811, 0,611] s). Quien llama exige la cota a CADA entrada. `antes()`, si lo hay, se ejecuta en el padre
        antes de cada hijo. AC236: y cada ejecución afirma que corrió las rutas de la exención (`problemas_seudonimo`,
        `_nombres_en` y `problemas_dato_civil`); si no, el caso no mide lo que dice. AC335 (enmienda 90): `limite` llega
        con el factor ya aplicado (`escalada`), igual que el plazo del hijo (la alarma de `medir_en_frio` es diez veces
        su cota)."""
        regla, snap = self.repo / seudonimos.RELATIONS_REL / 'relations.yaml', self.base / 'snap-234'
        snap.mkdir(exist_ok=True)
        lista = {'aristas': [], 'alias': [], 'citas': []}
        semilla, letra = ENTRADAS_DE_NOMBRE_LARGO[entrada]
        caso = next(CASOS_DE_NOMBRE_LARGO)
        propio, ajeno = f'Person:yy-propio{caso}', f'Person:yy-ajeno{caso}'
        nombre, _, nombre_ajeno, cita = rejilla_de_nombre_largo(rejilla, n, elecciones, semilla=semilla, letra=letra)
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Person, name: {nombre_ajeno}, entity_id: {ajeno}, role: Testigo.}}\n'
            f'- {{type: Person, name: {nombre}, entity_id: {propio}, role: Testigo.}}\n', encoding='utf-8')
        nodo_del_censo(self.root, ajeno, 'Person', nombre_ajeno)
        nodo_del_censo(self.root, propio, 'Person', nombre)
        nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                 {'ref': 'n', 'labels': ['Person'], 'props': {'entity_id': propio, 'name': nombre}},
                 {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                 {'ref': 'h', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': cita}}]
        rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': 'MET_WITH',
                 'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': cita,
                           'note': 'Nota neutra.'}}]
        (snap / 'nodes.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in nodos), encoding='utf-8')
        (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rels), encoding='utf-8')
        if camino == 'árbol':
            regla.parent.mkdir(parents=True, exist_ok=True)
            regla.write_text(
                f"fix:\n  - {{source_id: {SEUDO}, target_id: {propio}, type: MET_WITH, doc: ATS_A.pdf, "
                f"quote_contains: {json.dumps(cita)}, set: {{note: 'Nota neutra.'}}}}\n", encoding='utf-8')
        else:
            regla.unlink(missing_ok=True)
        tiempos = []
        for _ in range(intentos):
            tiempos.append(en_hijo(medir_en_frio, camino, self.repo, snap, lista, ('problemas_seudonimo', '_nombres_en',
                                   'problemas_dato_civil'), 30, plazo=escalada(300) + 30))
            if tiempos[-1] < limite:
                break
        return tiempos

    @lento
    def test_el_coste_de_un_nombre_largo_en_el_arbol_y_el_snapshot(self):
        """AC234 (enmienda 51, Codex 41b): el árbol (`auditar`) y el snapshot (`verificar_snapshot`) con la cita de la
        arista del seudónimo (en el selector de la regla del árbol, en el `source_quote` del snapshot) de «a-b» y «a-8»
        (o «ab» y «a8») contra un nombre propio de «ab» y otro ajeno de 2 o 4, en cada una de las ocho rejillas de
        AC222 y con el presupuesto real de la exención, que es cuadrática en las piezas hasta saturarse (41b midió ×11,8
        de 16 a 64) y acotada después: lo que se declara es el límite absoluto, en CPU y el mejor de hasta cinco
        ejecuciones. Con 96 piezas y 2n elecciones (la reproducción de 41b), el árbol y el snapshot a 1 s; con 400 y
        16.000 caracteres, el árbol a 1 s y el snapshot, que revisa la cita dos veces, a 3 s (1,2 a 1,4 s en cuatro
        rejillas: el 1 s literal de AC234 para el snapshot no se cumple a ese tamaño).

        AC235 (enmienda 52, Codex 42c): cada ejecución EN FRÍO (`tiempos_en_frio`). Antes repetía hasta cinco veces con
        las cachés calientes y aprobaba si una bajaba del límite: 500 * L**2 iteraciones solo en la primera llamada de
        cada entrada daban 2,59 y 3,10 s en el primer intento, 0,51 y 0,99 s en los siguientes, y el test pasaba. Ese
        control, rechazado por `test_un_sobrecoste_de_la_primera_llamada_se_rechaza_en_el_arbol_y_el_snapshot`. AC236:
        cada ejecución afirma que corrió `problemas_seudonimo`, `_nombres_en` y `problemas_dato_civil`.

        AC238 (enmienda 53, Codex 43b-3): cada ejecución es un proceso hijo nuevo y la cota vale para CADA una de las
        dos entradas (`ENTRADAS_DE_NOMBRE_LARGO`, otra cita y otros nombres de la misma familia); el mínimo de hasta
        cinco es solo entre repeticiones de la MISMA entrada. Antes cada reintento cambiaba la cita y bastaba que una
        cumpliera: 1,1 s en cada `_nombres_en` cuyo texto empieza por «a-b» pasaba a 96 piezas (árbol [1,461, 0,311] s,
        snapshot [2,811, 0,611] s, porque el segundo intento empezaba por «c-b»). Ese control, rechazado por
        `test_un_coste_en_una_entrada_se_rechaza_aunque_otra_baje_del_limite`.

        AC335 (enmienda 90): las cotas de 1 y 3 s son a factor 1 y llevan el de `SM_FACTOR_DE_CPU` (`escalada`). Un runner de
        GitHub tarda de 1,0 a 1,34 s en los casos que la cota de 1 s rechazó (medido en el CI; en `sm-dev`, como mucho 0,22 y
        0,52 s en el árbol a 96 y 400 piezas y 0,42 y 0,99 s en el snapshot a 96 y 400): con el factor 2 del CI, las cotas son
        2 y 6 s."""
        for rejilla in REJILLAS_DE_NOMBRE_LARGO:
            for n, elecciones, limite_del_snapshot in ((96, None, escalada(1.0)), (400, 4000, escalada(3.0))):
                for camino, limite in (('árbol', escalada(1.0)), ('snapshot', limite_del_snapshot)):
                    for entrada in range(len(ENTRADAS_DE_NOMBRE_LARGO)):
                        tiempos = self.tiempos_en_frio(camino, rejilla, n, elecciones, limite, entrada)
                        with self.subTest(rejilla=rejilla, piezas=n, camino=camino, entrada=entrada,
                                          segundos=round(min(tiempos), 3)):
                            self.assertLess(min(tiempos), limite)

    @lento
    def test_un_sobrecoste_de_la_primera_llamada_se_rechaza_en_el_arbol_y_el_snapshot(self):
        """AC235 (enmienda 52, Codex 42c): un sobrecoste solo en la primera llamada de cada entrada
        (`con_primera_llamada`: `seudonimos._fichas_de` por texto o `seudonimos._candidato` por nombre, con tanta CPU
        de más como el límite la primera vez que recibe una entrada larga, con su respuesta intacta y una memoria
        propia de lo que ya vio) lo rechaza el test del árbol y el snapshot a 400 piezas y 16.000 caracteres: cada
        intento en frío lo paga, así que ninguno baja del límite (1 y 3 s). Antes, el primer intento costaba el límite
        más lo suyo y los siguientes, con las cachés calientes y la misma entrada, lo suyo solo. Dos intentos, no
        cinco: el control es constante y repetir no lo baja. Una rejilla (ajeno de 4 piezas, cifras sueltas y
        espacios). AC238 (enmienda 53): cada intento es un proceso hijo, así que tampoco lo salva la memoria propia del
        control, que `vaciar_caches()` no borra y el padre no llena. AC335 (enmienda 90): el control recibe el límite a
        factor 1 y `quemar` le aplica el factor, igual que la cota con la que se compara (`escalada`): sigue costando tanto
        como el límite."""
        rejilla, n, elecciones = (4, True, True), 400, 4000
        for ruta in ('_fichas_de', '_candidato'):
            for camino, limite in (('árbol', 1.0), ('snapshot', 3.0)):
                with controlando(functools.partial(con_primera_llamada, limite, ruta)):
                    tiempos = self.tiempos_en_frio(camino, rejilla, n, elecciones, escalada(limite), intentos=2)
                with self.subTest(ruta=ruta, camino=camino, intentos=len(tiempos),
                                  segundos=[round(t, 2) for t in tiempos]):
                    self.assertEqual(len(tiempos), 2)
                    self.assertGreater(min(tiempos), escalada(limite))

    @lento
    def test_un_coste_en_una_entrada_se_rechaza_aunque_otra_baje_del_limite(self):
        """AC238 (enmienda 53, Codex 43b-3): 1,1 s en cada `_nombres_en` cuyo texto empieza por «a-b» (sin memoria: la
        cita de la primera entrada de la rejilla (4, True, False) empieza así y la de la segunda, por «c-b», no) lo
        rechaza el test del árbol y el snapshot a 96 piezas: la cota vale para la entrada con coste, que no baja de 1
        s en ninguna repetición, aunque la otra entrada, sin coste, sí (0,02 a 0,06 s). El mínimo de antes pasaba
        de una a otra entrada y aprobaba. AC335 (enmienda 90): los 1,1 s por llamada (`quemar`) y la cota de 1 s llevan el
        factor de `SM_FACTOR_DE_CPU`; la entrada sin coste tarda 1,07 a 1,10 s en un runner de GitHub (medido en el CI;
        0,23 s en el árbol y 0,41 s en el snapshot en `sm-dev`), y con el factor 2 del CI la cota es 2 s y el coste 2,2 s por
        llamada."""
        rejilla = (4, True, False)
        with controlando(functools.partial(con_coste_en_las_citas_que_empiezan_por, 1.1, 'a-b')):
            for camino in ('árbol', 'snapshot'):
                tiempos = [self.tiempos_en_frio(camino, rejilla, 96, None, escalada(1.0), entrada) for entrada in (0, 1)]
                with self.subTest(camino=camino, entradas=[[round(t, 2) for t in ts] for ts in tiempos]):
                    self.assertGreater(min(tiempos[0]), escalada(1.0))
                    self.assertLess(min(tiempos[1]), escalada(1.0))

    def test_el_mismo_contenedor_en_los_cuatro_caminos(self):
        """AC221 (enmienda 45, reproducción de Codex 36a) en el árbol, el export, el gate y el control posterior, sobre
        la cita de la arista del seudónimo con un civil propio de nombres «Zz Test Gó Mez Al Ias Centinela Civil», «Zz
        Test Gómez» y «Centinela Civil» (el selector de su regla en el árbol). «Zz Test Gó«mez» al❝ias❞ Centinela
        Civil» identifica al seudónimo con un civil en los cuatro (antes, en ninguno: la base leía las comillas como
        blancos y eximía a «Zz Test» por el nombre largo; la lectura que halla la fórmula sustituía ese nombre por «Zz
        Test Gómez», que la base no reconoce), con las comillas rectas y con las demás comillas de AC217, con la
        partición también antes y después del contenedor, y con el extremo de dos nombres (el largo y «Centinela
        Civil»); los controles sin fórmula, o con el nombre de pila dentro de un contenedor que ninguna lectura
        invalida, en ninguno."""
        nombre = 'Zz Test G\u00f3 Mez Al Ias Centinela Civil'
        pares = (('\u275d', '\u275e'), ('"', '"'), ('\u2e42', '\u2e42'), ('\U000e0022', '\U000e0022'),
                 ('\U0001f676', '\U0001f677'), ('\u276e', '\u276f'))
        plantillas = ('Zz Test G\u00f3\u00abmez\u00bb al{a}ias{b} Centinela Civil',
                      'Don Pe\u00abdro\u00bb y Zz Test G\u00f3\u00abmez\u00bb al{a}ias{b} Centinela Civil',
                      'Zz Test G\u00f3\u00abmez\u00bb al{a}ias{b} Centinela Civil Ru\u00abiz\u00bb')
        controles = ('Zz Test G\u00f3\u00abmez\u00bb al ias Centinela Civil',
                     'Zz Test G\u00f3mez alias Centinela Civil', 'Zz Test G\u00f3 mez al ias Centinela Civil')
        casos = [(plantilla.format(a=a, b=b), alias, True) for alias in (['Zz Test G\u00f3mez', 'Centinela Civil'],
                                                                         ['Centinela Civil'])
                 for plantilla in plantillas for a, b in pares]
        casos += [(cita, ['Zz Test G\u00f3mez', 'Centinela Civil'], False) for cita in controles]
        for k, (cita, alias, esperado) in enumerate(casos):
            propio = f'Person:yy-largo-{k}'
            (self.repo / 'pipeline/data/context/figures.yaml').write_text(
                f'entities:\n- {{type: Person, name: {json.dumps(nombre, ensure_ascii=False)}, entity_id: {propio}, '
                f'role: Testigo., aliases: {json.dumps(alias, ensure_ascii=False)}}}\n', encoding='utf-8')
            nodo_del_censo(self.root, propio, 'Person', nombre)

            def identifica(m, cita=cita):
                return 'nombra a un civil' in m and f'huella {seudonimos.sha16(cita)}' in m
            with self.subTest(identifica=cita, nombres=len(alias) + 1):
                self.assertEqual(self.veredictos_de_una_arista(propio, nombre, 'MET_WITH', f'{k:02x}f' * 21 + 'f',
                                                               'Nota neutra.', cita, identifica, selector=cita,
                                                               alias=alias), (esperado,) * 4)

    def test_una_formula_partida_en_mayusculas_en_los_cuatro_caminos(self):
        """AC176 (enmienda 37, reproducción de Codex), en una fila civil: el civil con el alias «Fuldo Fíctez, AL-IAS ZZ
        TEST» (en el contexto o no) y ese texto en su resumen: el resumen identifica al seudónimo en el árbol, el
        export, el gate y el control posterior, y el alias es uno que identifica en el árbol, el export y el gate
        (`check`), con las tres grafías («AL-IAS», «AL«I»AS», «ALIAS»); antes, las dos partidas pasaban los cuatro. Sin
        los textos en la salida."""
        lista = {'aristas': [], 'alias': [], 'citas': []}
        identificador = f'{seudonimos.MOTIVOS["alias-identificador"]}'
        for n, con_alias in itertools.product(range(3), (False, True)):
            alias = ('Fuldo Fíctez, AL-IAS ZZ TEST', 'Fuldo Fíctez, AL«I»AS ZZ TEST', 'Fuldo Fíctez, ALIAS ZZ TEST')[n]
            texto = f'{alias}.'
            resumen = f'huella {seudonimos.sha16(texto)}'
            with self.subTest(texto=n, alias_en_el_contexto=con_alias):
                (self.repo / 'pipeline/data/context/figures.yaml').write_text(
                    f'entities:\n- {{type: Person, name: Fuldo Fíctez, entity_id: {FULDO}, role: Asesor.'
                    + (f', aliases: [{json.dumps(alias, ensure_ascii=False)}]' if con_alias else '') + '}\n',
                    encoding='utf-8')
                (self.repo / A.SUM_PATH).write_text(
                    f'summaries:\n- entity_id: {FULDO}\n  name: Fuldo Fíctez\n'
                    f'  resumen: {json.dumps(texto, ensure_ascii=False)}\n', encoding='utf-8')
                hallazgos = [h.motivo for h in seudonimos.auditar(self.repo, self.publicos)]
                self.assertTrue(any('nombra a un seudónimo judicial' in m and resumen in m for m in hallazgos))
                self.assertEqual(any(identificador in m and f'huella {seudonimos.sha16(alias)}' in m
                                     for m in hallazgos), con_alias)
                snap = self.base / f'snap-176-{n}-{con_alias}'
                snap.mkdir(exist_ok=True)
                nodo = {'entity_id': FULDO, 'name': 'Fuldo Fíctez', 'summary': texto,
                        **({'aliases': [alias]} if con_alias else {})}
                (snap / 'nodes.jsonl').write_text(json.dumps({'ref': 'k', 'labels': ['Person'], 'props': nodo},
                                                             ensure_ascii=False) + '\n', encoding='utf-8')
                (snap / 'relationships.jsonl').write_text('', encoding='utf-8')
                problemas = seudonimos.verificar_snapshot(snap, frozenset({SEUDO}), lista, publicos=self.publicos,
                                                         repo=self.repo)
                self.assertTrue(any('nombra a un seudónimo judicial' in p and resumen in p for p in problemas))
                self.assertEqual(any(identificador in p and f'huella {seudonimos.sha16(alias)}' in p
                                     for p in problemas), con_alias)
                d = full_decision()
                d['summary'] = {'action': 'replace', 'resumen': texto, 'sources': [], 'reason': 'r'}
                d['_file'] = 'E-ac176.json'
                self.assertTrue(any(resumen in e for e in self.gate_del_repo(d)))
                self.assertTrue(any(resumen in e for e in self.posterior_del_repo(d)))
                self.write('E-ac176', d)
                codigo, out, err = self.cli('check')
                self.assertEqual(codigo, 1)
                self.assertEqual(f'{identificador} (huella {seudonimos.sha16(alias)})' in out, con_alias)
                self.assertNotIn('zz test', (out + err).lower())

    def test_la_mencion_del_seudonimo_a_traves_de_las_comillas_en_los_cuatro_caminos(self):
        """AC168 (enmienda 36), en los detectores del seudónimo: el resumen de un civil con la mención explícita del
        seudónimo partida por unas comillas («el seud«ó»nimo Zz Test») lo nombra en el árbol, el export, el gate y el
        control posterior (antes, en ninguno), con la huella del seudónimo; sin la palabra, no."""
        nombra = f'nombra a un seudónimo judicial (huella {seudonimos.sha16(SEUDO)})'
        lista = {'aristas': [], 'alias': [], 'citas': []}

        def veredictos(resumen):
            (self.repo / A.SUM_PATH).write_text(
                f'summaries:\n- entity_id: {FULDO}\n  name: Fuldo Fíctez\n'
                f'  resumen: {json.dumps(resumen, ensure_ascii=False)}\n', encoding='utf-8')
            arbol = any(nombra in h.motivo for h in seudonimos.auditar(self.repo, self.publicos))
            snap = self.base / f'snap-{seudonimos.sha16(resumen)}'
            snap.mkdir(exist_ok=True)
            (snap / 'nodes.jsonl').write_text(json.dumps({'ref': 'k', 'labels': ['Person'], 'props': {
                'entity_id': FULDO, 'name': 'Fuldo Fíctez', 'summary': resumen}}, ensure_ascii=False) + '\n',
                encoding='utf-8')
            (snap / 'relationships.jsonl').write_text('', encoding='utf-8')
            export = any(nombra.replace('(huella', '(nodo ~8254c329a92850f6, huella') in p for p in seudonimos.verificar_snapshot(
                snap, frozenset({SEUDO}), lista, publicos=self.publicos, repo=self.repo))
            d = full_decision()
            d['summary'] = {'action': 'replace', 'resumen': resumen, 'sources': [], 'reason': 'r'}
            d['_file'] = 'E-ac168.json'
            gate = any(nombra in e for e in self.gate_del_repo(d))
            return arbol, export, gate, any(nombra in e for e in self.posterior_del_repo(d))
        self.assertEqual(veredictos('Fuldo Fíctez, el seud«ó»nimo Zz Test, declaró.'), (True, True, True, True))
        self.assertEqual(veredictos('Fuldo Fíctez declaró ante el juez.'), (False, False, False, False))

    def test_un_alias_de_contexto_que_no_es_una_lista_para_el_apply(self):
        """AC161 (enmienda 36) en el apply: los `aliases` de una entidad de contexto escritos como un mapa
        (`{'[]': …}`) o como un texto paran `check` con exit 2 y el motivo, sin valores ni escribir nada."""
        self.write('E-ac161', full_decision())
        antes = self.destinos()
        for valor in ("{'[]': 'Beta Gamma'}", "'Beta Gamma'"):
            with self.subTest(valor=valor):
                (self.repo / 'pipeline/data/context/figures.yaml').write_text(
                    f'entities:\n- {{type: Person, name: Yy Civil, entity_id: Person:yy-civil, role: Testigo., '
                    f'aliases: {valor}}}\n', encoding='utf-8')
                codigo, out, err = self.cli('check')
                self.assertEqual(codigo, 2, out + err)
                self.assertIn('un campo que no es una lista', out + err)
                self.assertNotIn('beta', (out + err).lower())
        self.assertEqual(self.destinos(), antes)

    def test_un_solo_indice_de_sociedades_con_el_censo_y_el_export(self):
        """AC153 (enmienda 35, 27b-6): el gate y el control posterior juzgan la prosa con un único índice de sociedades,
        construido una vez por ejecución con las del contexto, las del censo y las del export (antes, el gate solo con
        las del contexto): una sociedad que solo da el censo, o solo el export, en la nota que la decisión lleva a la
        arista del seudónimo, es un dato civil en el gate, en el control posterior con ese índice y en `check`."""
        nimbo = 'Company:zz-nimbo'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n', encoding='utf-8')
        nodo_del_censo(self.root, 'Company:zz-solo-censo', 'Company', 'Cumulo Censo S.L.')
        uid = 'u' * 64
        pack = self.arista_del_seudonimo(uid, nimbo, 'WORKS_FOR')
        snap = self.export_con_chunk('Nada.')
        with (snap / 'nodes.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'ref': 'e', 'labels': ['Company'], 'props': {
                'entity_id': 'Company:zz-solo-export', 'name': 'Estrato Export S.L.'}}) + '\n')
        export = seudonimos.literal_del_export(snap)
        dato = seudonimos.MOTIVOS['dato-civil']
        for nota, con_export in (('Socio de Cumulo Censo.', False), ('Socio de Estrato Export.', True)):
            with self.subTest(nota=nota):
                d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                             edges=[edge_op(uid, 'set_prop', prop='note', value=nota)], _file=f'{pack}.json')
                errores = self.gate_del_repo(copy.deepcopy(d), export=export if con_export else None)
                self.assertTrue(any(dato in e for e in errores), errores)
                conn = A.connect(self.root)
                try:
                    sociedades = A.indice_sociedades(conn, seudonimos.nombres_contexto(self.repo),
                                                     export if con_export else None)
                finally:
                    conn.close()
                self.gate_del_repo(d, export=export if con_export else None)
                seudos = frozenset({SEUDO})
                rows = A.materialize([d], self.tag, self.upd, seudos, self.publicos)
                posterior = A._prosa_publica(rows, seudos, {}, self.publicos, seudonimos.nombres_contexto(self.repo),
                                             self.citas_fixture, sociedades=sociedades)
                self.assertTrue(any(dato in e for e in posterior), posterior)
                self.write(pack, d)
                codigo, out, err = self.cli('check', *(('--snapshot', snap) if con_export else ()))
                self.assertEqual(codigo, 1, out + err)
                self.assertIn(dato, out)
                for valor in ('cumulo', 'estrato'):
                    self.assertNotIn(valor, (out + err).lower())

    def test_no_nombra_ya_no_es_una_prueba_en_el_control_posterior(self):
        """AC148 (enmienda 35): el control posterior recalcula la prueba de cada arista conservada: una acción
        «conservar» con la etiqueta 'no-nombra' (la de antes) y una cita que no nombra a su extremo civil no pasa,
        aunque el censo dé los nombres de la sociedad; la misma arista, con una cita que la nombra y su entrada de
        `citas:`, pasa."""
        nimbo = 'Company:zz-nimbo'
        nodo_del_censo(self.root, nimbo, 'Company', 'Nimbo')
        seudos = frozenset({SEUDO})
        for cita, citas, errores in (('Zz Test cobró del auto.', [], 1),
                                     ('Zz Test trabajó para Nimbo.', [seudonimos.entrada_cita(
                                         'Zz Test trabajó para Nimbo.', SEUDO, ['ATS_A.pdf'], 'decision-2026-10-08',
                                         'coaparición judicial revisada; no identifica')], 0)):
            with self.subTest(cita=cita):
                uid = ('t' if errores else 's') * 64
                pack = self.arista_del_seudonimo(uid, nimbo, 'WORKS_FOR', cita=cita)
                d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid)], _file=f'{pack}.json')
                self.gate_del_repo(d, citas=citas)
                prueba = d['_pruebas'].get(uid) or 'no-nombra'
                rows = {**A.materialize([d], self.tag, self.upd, seudos, self.publicos),
                        'seudonimos': [A._accion_seudonimo('conservar', SEUDO, f'{pack}.json', uid, otro=nimbo,
                                                           tipo='WORKS_FOR', kind='judicial', doc='ATS_A.pdf',
                                                           prueba=prueba)]}
                conservadas = {uid: (cita, 'ATS_A.pdf', 'judicial', (SEUDO, nimbo), {nimbo: ('Nimbo',)})}
                posterior = A._prosa_publica(rows, seudos, {}, self.publicos, {}, [*self.citas_fixture, *citas],
                                             conservadas=conservadas)
                self.assertEqual(len(posterior), errores, posterior)

    def test_las_reproducciones_de_la_enmienda_32_en_el_gate(self):
        """AC134/AC135/AC136 (enmienda 32, reproducciones de Codex) en el apply: el alias inválido que roles_qa061.yaml
        da a un seudónimo («Beta Gamma») es error de la tanda en `check`, con su línea (antes, OK), y su mención en la
        nota del estado del seudónimo, AC78 en el gate y en el control posterior cuando nada lo denuncia; la nota con
        «Antonio d0n Pedro» (el nombre de pila de otro seudónimo), también; una clave en la raíz del roster para `check`
        con exit 2. Sin valores."""
        alfa, pedro = 'Person:alfa', 'Person:pedro-antonio'
        roster = self.repo / seudonimos.PSEUDONYMS_REL
        roster.write_text(PSEUDONYMS + f'- id: {alfa}\n- id: {pedro}\n', encoding='utf-8')
        (self.repo / 'pipeline/data/context/roles_qa061.yaml').write_text(
            f"entities:\n- {{type: Person, name: Alfa, entity_id: {alfa}, aliases: ['Beta Gamma']}}\nrelations: []\n",
            encoding='utf-8')
        u, pk = 'e32a' * 16, 'E-zzalfa001'
        self.pack_de_otro_seudonimo(alfa, u, pk)
        estado = {'action': 'set', 'status': 'investigado', 'fecha': '2026-04-11', 'doc': 'ATS_0000_2025.pdf',
                  'url': '', 'tipo': 'auto', 'nota': 'Declaró Beta Gamma.'}
        d = decision(pack_id=pk, entity=alfa, units=[unit(u)], _file=f'{pk}.json', estado=estado)
        beta = self.AC78.format(seudonimos.sha16('Beta Gamma'))
        self.assertEqual(self.gate_del_repo(copy.deepcopy(d)), [f'{pk}.json: {beta}'])
        g = copy.deepcopy(d)
        self.gate_del_repo(g)
        self.assertEqual(self.posterior_del_repo(g), [f'estado[0]: {beta}'])
        cero = copy.deepcopy(d)
        cero['estado']['nota'] = 'Declaró Antonio d0n Pedro.'
        self.assertEqual(self.gate_del_repo(cero),
                         [f'{pk}.json: {self.AC78.format(seudonimos.sha16("pedro antonio"))}'])
        self.write(pk, d)
        codigo, out, err = self.cli('check')
        self.assertEqual(codigo, 1, out + err)
        self.assertIn(f'  ❌ pipeline/data/context/roles_qa061.yaml:2: {seudonimos.MOTIVOS["alias-capa"]} '
                      f'(huella {seudonimos.sha16("Beta Gamma")})\n', out)
        self.assertNotIn('beta', (out + err).lower())
        roster.write_text(f'Company:zx-sentinel: null\n{PSEUDONYMS}', encoding='utf-8')
        codigo, out, err = self.cli('check')
        self.assertEqual(codigo, 2, out + err)
        self.assertIn('  ❌ pseudonyms.yaml: roster inválido (spec 070): clave raíz fuera del inventario (huella ', out)
        self.assertNotIn('sentinel', (out + err).lower())

    def test_el_dato_civil_que_cruza_las_comillas_en_los_cuatro_caminos(self):
        """AC138 (enmienda 32, reproducción de Codex): la nota de estado de un seudónimo «Administrador de «Centinela»
        S.L.» o «Administrador desde el «8/10».», con la entrada de `citas:` de su tramo, lleva una sociedad o una fecha
        en el árbol, el export, el gate y el control posterior (antes, pasaba en los cuatro); «Socio de «Centinela
        S.L.».», con la del suyo, pasa en los cuatro."""
        motivo = 'cita literal del documento judicial público; coaparición verificada en el export 20261007-qa067b'
        citas = [seudonimos.entrada_cita(t, SEUDO, ['ATS_A.pdf'], 'decision-2026-10-08', motivo)
                 for t in ('Centinela', '8/10', 'Centinela S.L.')]
        self.perm.write_text(seudonimos.volcar_permitidos({'aristas': [], 'alias': [], 'citas': citas}),
                             encoding='utf-8')
        dato = seudonimos.MOTIVOS['dato-civil']

        def veredictos(nota):
            (self.repo / 'pipeline/data/accusations/accusations.yaml').write_text(
                ACCUSATIONS + f"  - person: Zz Test\n    entity_id: {SEUDO}\n"
                              f"    estado: {{status: investigado, fuente: {{doc: ATS_A.pdf}}, nota: '{nota}'}}\n",
                encoding='utf-8')
            snap = self.base / f'snap-{seudonimos.sha16(nota)}'
            snap.mkdir(exist_ok=True)
            nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                     {'ref': 'k', 'labels': ['Crime'], 'props': {'entity_id': 'Crime:zz-delito-test'}},
                     {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}}]
            rels = [{'ref': 'r', 'start': 's', 'end': 'k', 'type': 'ACCUSED_OF',
                     'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'note': nota}}]
            (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                              encoding='utf-8')
            (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                      encoding='utf-8')
            arbol = any(dato in h.motivo for h in seudonimos.auditar(self.repo, self.publicos))
            export = any(dato in x for x in seudonimos.verificar_snapshot(
                snap, frozenset({SEUDO}), {'aristas': [], 'alias': [], 'citas': citas}))
            estado = {'action': 'set', 'status': 'investigado', 'fecha': '2026-04-11', 'doc': 'ATS_A.pdf', 'url': '',
                      'tipo': 'auto', 'nota': nota}
            d = decision(pack_id=PS, entity=SEUDO, units=[unit(u) for u in (U_S_ORG, U_S_SOC, U_S_ID)],
                         alias_deny=['el consejero Zz Test'], estado=estado, _file=f'{PS}.json')
            d['units'][2] = unit(U_S_ID, 'corregido', 'alias')
            gate = any(dato in e for e in self.gate_del_repo(d, citas=citas))
            return arbol, export, gate, any(dato in e for e in self.posterior_del_repo(d, citas=citas))
        for nota in ('Administrador de «Centinela» S.L.', 'Administrador desde el «8/10».'):
            with self.subTest(nota=nota):
                self.assertEqual(veredictos(nota), (True, True, True, True))
        self.assertEqual(veredictos('Socio de «Centinela S.L.».'), (False, False, False, False))

    def test_el_keep_de_una_cita_que_identifica_o_nombra_a_medias(self):
        """AC139/AC140 (enmienda 32, reproducciones de Codex): la arista del seudónimo con su sociedad que la decisión
        conserva con la cita «Zz Test, seudónimo del administrador de Nimbo.», literal en un chunk de su documento, no
        se conserva: «cita que identifica» en el gate y `check --snapshot` (antes, OK), como en el snapshot; y la que
        nombra a medias («Nimbo Stratus») a una sociedad cuyo nombre solo da el censo («Nimbo Stratus S.L.») no es
        «no-nombra»: pide su prueba en el gate, como en el snapshot. El control posterior las recalcula. Sin valores."""
        nimbo, stratus = 'Company:zz-nimbo', 'Company:zz-nimbo-stratus'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Nimbo, entity_id: {nimbo}, role: Sociedad.}}\n', encoding='utf-8')
        nodo_del_censo(self.root, nimbo, 'Company', 'Nimbo')
        nodo_del_censo(self.root, stratus, 'Company', 'Nimbo Stratus S.L.')
        uid, cita = 'e32b' * 16, 'Zz Test, seudónimo del administrador de Nimbo.'
        pack = self.arista_del_seudonimo(uid, nimbo, 'WORKS_FOR', cita=cita)
        d = decision(pack_id=pack, entity=SEUDO, units=[unit(uid)], _file=f'{pack}.json')
        snap = self.export_con_chunk(f'Consta: {cita} Y más.')
        export = seudonimos.literal_del_export(snap)
        identifica = (f'{pack}.json: unidad {uid[:8]}: {seudonimos.MOTIVOS["identifica"]} '
                      f'(huella {seudonimos.sha16(cita)})')
        g = copy.deepcopy(d)
        self.assertEqual(self.gate_del_repo(g, export=export), [identifica])
        self.assertEqual(g['_pruebas'], {})
        self.assertEqual(A.materialize([g], self.tag, self.upd, frozenset({SEUDO}), self.publicos)['seudonimos'], [])
        g['_conservadas'] = {uid: (cita, 'ATS_A.pdf', 'judicial', (SEUDO, nimbo), {nimbo: ('Nimbo',)})}
        g['_pruebas'] = {uid: f'export:ATS_A.pdf:{seudonimos.sha16(cita)}'}   # la etiqueta forjada no vale
        self.assertEqual(self.posterior_del_repo(g, export=export),
                         [f'seudonimos[0]: arista conservada sin la prueba de su cita (unidad {uid[:8]})'])
        self.write(pack, d)
        codigo, out, err = self.cli('check', '--snapshot', snap)
        self.assertEqual(codigo, 1, out + err)
        self.assertIn(f'  ❌ {identifica}\n', out)
        self.assertNotIn('nimbo', (out + err).lower())
        (self.root / f'esc/decisions/{pack}.json').unlink()
        nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                 {'ref': 'n', 'labels': ['Company'], 'props': {'entity_id': nimbo, 'name': 'Nimbo'}},
                 {'ref': 't', 'labels': ['Company'], 'props': {'entity_id': stratus, 'name': 'Nimbo Stratus S.L.'}},
                 {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                 {'ref': 'c', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': f'Consta: {cita} Y más.'}}]
        a_medias = 'Zz Test trabajó para Nimbo Stratus.'
        rels = [{'ref': 'r1', 'start': 's', 'end': 'n', 'type': 'WORKS_FOR',
                 'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': cita}},
                {'ref': 'r2', 'start': 's', 'end': 't', 'type': 'WORKS_FOR',
                 'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': a_medias}}]
        snap2 = self.base / 'snap-e32'
        snap2.mkdir()
        (snap2 / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                           encoding='utf-8')
        (snap2 / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                   encoding='utf-8')
        lista = {'aristas': [{'seudonimo': SEUDO, 'otro': o, 'tipo': 'WORKS_FOR'} for o in (nimbo, stratus)],
                 'alias': [], 'citas': []}
        problemas = seudonimos.verificar_snapshot(snap2, frozenset({SEUDO}), lista)
        self.assertIn(f'texto de una arista de seudónimo nombra a un civil o a un documento (arista ~82f3e9c695dc6b8d, huella '
                      f'{seudonimos.sha16("Nimbo")})', problemas)
        self.assertIn(f'{seudonimos.MOTIVOS["coaparicion"]} (arista ~db77fd01af957221, huella {seudonimos.sha16(a_medias)})',
                      problemas)
        uid2 = 'e32c' * 16
        pack2 = self.arista_del_seudonimo(uid2, stratus, 'WORKS_FOR', cita=a_medias)
        d2 = decision(pack_id=pack2, entity=SEUDO, units=[unit(uid2)], _file=f'{pack2}.json')
        g2 = copy.deepcopy(d2)
        self.assertEqual(self.gate_del_repo(g2),
                         [f'{pack2}.json: unidad {uid2[:8]}: {self.COAPARICION.format(seudonimos.sha16(a_medias))}'
                          .replace(' (huella', ': sin prueba disponible (pasa --snapshot) (huella')])
        g2['_conservadas'] = {uid2: (a_medias, 'ATS_A.pdf', 'judicial', (SEUDO, stratus),
                                     {stratus: ('Nimbo Stratus S.L.',)})}
        g2['_pruebas'] = {uid2: 'no-nombra'}   # la etiqueta de antes no vale
        self.assertEqual(self.posterior_del_repo(g2),
                         [f'seudonimos[0]: arista conservada sin la prueba de su cita (unidad {uid2[:8]})'])

    def test_la_coaparicion_no_depende_del_tipo_de_comilla_en_export_gate_y_posterior(self):
        """AC212 (enmienda 43, reproducción de Codex 34b-1): la prueba de que la cita de una arista mixta nombra a su
        extremo civil lee solo la pasada base, así que el veredicto es el mismo con cualquier comilla (« », “ ”, ‘
        ’, ‹ ›, „ “, y con un signo de por medio) que con la ASCII: en el snapshot, la arista pide su coaparición
        probada (antes, con una comilla Unicode pasaba sin problema); en el gate, la conservada es "sin coaparición
        probada" y no "nombra a su extremo"; el control posterior, igual. La cita sin comillas sí nombra a su
        extremo."""
        cirro = 'Company:zz-cirro'
        (self.repo / 'pipeline/data/context/figures.yaml').write_text(
            f'entities:\n- {{type: Company, name: Cirro Estrato, entity_id: {cirro}, role: Sociedad.}}\n',
            encoding='utf-8')
        nodo_del_censo(self.root, cirro, 'Company', 'Cirro Estrato')
        lista = {'aristas': [{'seudonimo': SEUDO, 'otro': cirro, 'tipo': 'WORKS_FOR'}], 'alias': [], 'citas': []}
        contador = itertools.count()

        def quita(textos):   # sin la unidad, la huella ni la arista: lo que dicen, no de qué cita
            return [re.sub(r'E-[0-9a-f]{8}|unidad [0-9a-f]{8}|\(huella [0-9a-f]{16}\)|\(arista [\w~]+\)', '', t)
                    for t in textos]

        def veredictos(cita):
            k = next(contador)
            uid = format(k, '04x') * 16
            pack = self.arista_del_seudonimo(uid, cirro, 'WORKS_FOR', cita=cita)
            g = decision(pack_id=pack, entity=SEUDO, units=[unit(uid)], _file=f'{pack}.json')
            gate = self.gate_del_repo(g)
            g['_conservadas'] = {uid: (cita, 'ATS_A.pdf', 'judicial', (SEUDO, cirro), {cirro: ('Cirro Estrato',)})}
            g['_pruebas'] = {uid: 'no-nombra'}
            posterior = self.posterior_del_repo(g)
            nodos = [{'ref': 's', 'labels': ['Person'], 'props': {'entity_id': SEUDO, 'name': 'Zz Test'}},
                     {'ref': 'n', 'labels': ['Company'], 'props': {'entity_id': cirro, 'name': 'Cirro Estrato'}},
                     {'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                     {'ref': 'c', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf', 'text': cita}}]
            rels = [{'ref': 'r', 'start': 's', 'end': 'n', 'type': 'WORKS_FOR',
                     'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_quote': cita}}]
            snap = self.base / f'snap-43-{k}'
            snap.mkdir()
            (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                              encoding='utf-8')
            (snap / 'relationships.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rels),
                                                      encoding='utf-8')
            export = seudonimos.verificar_snapshot(snap, frozenset({SEUDO}), lista,
                                                   personas=seudonimos.personas_contexto(self.repo),
                                                   publicos=self.publicos, repo=self.repo)
            return quita(gate), quita(posterior), quita(export)
        ascii_ = veredictos('Zz Test trabaj\u00f3 para Cir"ro" Estrato.')
        self.assertIn(seudonimos.MOTIVOS['no-nombra'], ascii_[0][0])
        self.assertEqual([seudonimos.MOTIVOS['no-nombra'] + ' '], ascii_[2])
        plano = veredictos('Zz Test trabaj\u00f3 para Cirro Estrato.')
        self.assertIn(seudonimos.MOTIVOS['coaparicion'], plano[0][0])
        self.assertNotEqual(plano[0], ascii_[0])
        for abre, cierra in (('\u00ab', '\u00bb'), ('\u201c', '\u201d'), ('\u2018', '\u2019'), ('\u2039', '\u203a'),
                             ('\u201e', '\u201c')):
            for medio in ('', '-', '\u00b7'):
                cita = f'Zz Test trabaj\u00f3 para Cir{abre}{medio}ro{cierra} Estrato.'
                with self.subTest(cita=cita):
                    self.assertEqual(veredictos(cita), ascii_)
        # AC217 (enmienda 44): y todas las demás: las de categoría Pi o Pf, las llamadas «… QUOTATION MARK …» (U+2E42,
        # los ornamentales U+275B a U+2760, U+301D a U+301F, la etiqueta U+E0022…) y las rectas, bajas, graves, agudas,
        # primas y de ancho completo, cada una como la recta; recorrido por todo Unicode (antes, U+2E42 y U+275D
        # concedían la coaparición). Las de ancho completo, la tilde y la etiqueta invisible las rechaza además otro
        # control (`texto_sospechoso`): en los tres caminos la cita no se conserva, por ese motivo y no por el de
        # coaparición.
        rectas = set('\'`\u00b4\u201e\u201a\uff02\uff07\u301d\u301e\u301f\u2033\u2036')
        sospechosas = 0
        for c in map(chr, range(sys.maxunicode + 1)):
            if c == '"' or not (unicodedata.category(c) in ('Pi', 'Pf') or 'QUOTATION MARK' in unicodedata.name(c, '')
                                or c in rectas):
                continue
            cita = f'Zz Test trabaj\u00f3 para Cir{c}ro{c} Estrato.'
            with self.subTest(comilla=f'U+{ord(c):04X} {unicodedata.name(c, "?")}'):
                gate, posterior, export = veredictos(cita)
                if seudonimos.texto_sospechoso(cita):
                    sospechosas += 1
                    self.assertTrue(any(seudonimos.MOTIVOS['sospechoso'] in t for t in gate))
                    self.assertTrue(any(seudonimos.MOTIVOS['sospechoso'] in t for t in export))
                    self.assertTrue(posterior)
                else:
                    self.assertEqual((gate, posterior, export), ascii_)
        self.assertGreaterEqual(sospechosas, 4)

    def test_la_regla_fix_sin_efecto_en_el_control_de_carga(self):
        """AC143 (residual de la enmienda 32) en el apply: el control de carga del gate (`_load_check`, con el
        `_prepare_fix` del loader) da por error la regla fix sin efecto (un retype a su propio tipo, sin swap ni `set`);
        con un swap verdadero o con un `set` aplicable, vale aunque el tipo no cambie. AC147 (enmienda 34): el error
        dice que el loader para en esa regla y su motivo, sin valores. AC155 (enmienda 35): un `swap_direction` de texto
        («True», «False») o un número, también."""
        base = {'source_id': SEUDO, 'target_id': 'Crime:zz-delito-test', 'type': 'ACCUSED_OF'}

        def check(**extra):
            return A._load_check({'deny': [], 'fix': [{**base, **extra}], 'alias_deny': [], 'claims': []})
        para = 'fix[1]: el loader rechaza la fila (SystemExit)'   # la forma de main (spec 071 AC87), 9-nov
        for extra in ({'corrected_type': 'ACCUSED_OF', 'swap_direction': False}, {'swap_direction': False}, {}):
            with self.subTest(sin_efecto=sorted(extra)):
                self.assertEqual(check(**extra), [para])
        for valor in ('True', 'False', 1):
            with self.subTest(swap=valor):
                self.assertEqual(check(swap_direction=valor),
                                 [para])
        for extra in ({'swap_direction': True}, {'corrected_type': 'ACCUSED_OF', 'set': {'note': 'Nota neutra.'}}):
            with self.subTest(vale=sorted(extra)):
                self.assertEqual(check(**extra), [])

    def test_una_letra_latina_confusable_en_el_rol_de_un_civil(self):
        """AC88 (enmienda 23): el rol de un civil con una letra latina confusable («\u0131» sin punto, o la alfa latina
        «\u0251») para el apply ya en el gate (AC105(a)), con la decisión y la huella, y tras materializar, con la
        posición; uno con una «ł» o con tildes, no."""
        for rol in ('Asesor de K\u0131ldo.', 'Asesor de Kold\u0251.'):
            with self.subTest(rol=rol):
                d = full_decision()
                d['role'] = {'action': 'replace', 'role': rol, 'reason': 'r'}
                self.write('E-ac88', d)
                before = self.huellas_destinos()
                for comando in ('check', 'dry-run', 'apply'):
                    codigo, out, _ = self.cli(comando)
                    self.assertEqual(codigo, 1)
                    self.assertIn(f'  ❌ E-ac88.json: {self.SOSPECHOSO.format(seudonimos.sha16(rol))}\n', out)
                    self.assertEqual(self.huellas_destinos(), before)
                self.assertIn(f'roles[0]: {self.SOSPECHOSO.format(seudonimos.sha16(rol))}', self.posterior(d))
        d = full_decision()
        d['role'] = {'action': 'replace', 'role': 'Corresponsal en Łódź; también en Málaga y A Coruña.', 'reason': 'r'}
        self.write('E-ac88', d)
        codigo, out, _ = self.cli('check')
        self.assertEqual((codigo, [linea for linea in out.splitlines() if '❌' in linea]), (0, []))

    def test_el_tag_no_llega_a_la_lista(self):
        """AC80 (enmienda 21): la lista no tiene texto libre. Una entrada nueva lleva `origen: decision-<upd>` y el
        motivo de la plantilla cerrada con su unidad, nunca el `--tag` (texto libre de quien aplica): con un tag
        centinela, la lista no lo lleva y valida sin hallazgos."""
        self.tag = 'TANDA_CENTINELA_Q21'
        self.apply(self.retirar())
        self.assertNotIn('CENTINELA', self.perm.read_text(encoding='utf-8'))
        lista = seudonimos.cargar_permitidos(self.perm)
        motivo = f'decisión «conservar» de la auditoría (unidad {U_S_SOC[:8]})'
        self.assertEqual([(e['otro'], e['origen'], e['motivo']) for e in lista['aristas']],
                         [(SOC, f'decision-{self.upd}', motivo)])
        self.assertEqual(seudonimos.auditar(self.repo, self.publicos), [])

    def test_el_dry_run_de_un_rol_conserva_el_tipo_de_entidad(self):
        # AC58: el barrido destapó que `_imprimible` (AC48) pasaba por `_tipo_impreso` el `type` de una fila de
        # `roles`, que es el tipo de la entidad («Person»), no uno de relación: el dry-run lo enseñaba como huella.
        fila = {'type': 'Person', 'name': 'Fuldo Fíctez', 'entity_id': FULDO, 'role': 'Asesor.', 'reason': 'x'}
        self.assertEqual(A._imprimible('roles', fila, frozenset({SEUDO})), fila)

    def test_un_set_prop_invalido_no_llega_a_materializar(self):
        # AC52 (enmienda 12): el gate solo miraba el importe, y citándolo: una `source_page` «DOC….pdf» llegaba a
        # `_cast_value` y el traceback del `check` la publicaba. Antes de materializar, un error con la propiedad, la
        # unidad y la huella del valor; nunca el valor.
        uid = 'p' * 64
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-prop-p', 'relationship', 'r:p', SEUDO, 'MET_WITH', 'judicial', 0, 'h', '{}'))
        value = {'start': SEUDO, 'end': 'Person:zz-tercero-test', 'type': 'MET_WITH',
                 'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_page': 2,
                           'source_quote': 'cita sintética'}}
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, 'o-prop-p', 'edge', '/edge@1', json.dumps(value)))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   ('E-prop-p', SEUDO, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.commit()
        db.close()
        before = self.destinos()
        for prop, valor in (('source_page', 'DOC_PRIVADO_CENTINELA.pdf'), ('amount', 'CENTINELA'),
                            ('source_kind', 'CENTINELA')):
            with self.subTest(prop):
                self.write('E-prop-p', decision(pack_id='E-prop-p', entity=SEUDO,
                                                units=[unit(uid, 'corregido', 'arista')],
                                                edges=[edge_op(uid, 'set_prop', prop=prop, value=valor)]))
                codigo, out, err = self.cli()
                errores = [linea for linea in err.splitlines() if 'operación con carga inválida' in linea]
                self.assertEqual(codigo, 2)   # validar_cargas (spec 071 AC78), 9-nov
                self.assertEqual(len(errores), 1)
                self.assertIn('operación con carga inválida (E-prop-p.json, ', errores[0])
                self.assertNotIn('CENTINELA', out + err)
        self.assertEqual(self.destinos(), before)

    def test_un_error_interno_del_cli_es_una_linea(self):
        # AC52: cualquier excepción que no sea SystemExit sale del CLI como una línea con su clase y exit 2, sin
        # traceback: su texto puede citar un valor de una decisión o del censo.
        self.write('P1', decision())
        with mock.patch.object(A, 'gate', side_effect=ValueError('CENTINELA_INTERNO')):
            codigo, out, err = self.cli()
        self.assertEqual((codigo, out, err), (2, '', 'qa_esc_apply: error interno (ValueError)\n'))

    def test_un_tipo_de_arista_fuera_de_la_ontologia_ni_pasa_ni_se_escribe(self):
        # AC48 (enmienda 11): el tipo de una arista del censo nadie lo validaba, y `_dato_registro` lo copiaba al
        # registro público y al dry-run (y el error de `_aplicar_seudonimos`, a la terminal). El gate exige un tipo
        # canónico a toda arista que trae la decisión; el registro, el dry-run y los errores solo escriben su huella.
        centinela = 'TIPO_PRIVADO_CENTINELA'
        uid = 'q' * 64
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-tipo-q', 'relationship', 'r:q', SEUDO, centinela, 'judicial', 0, 'h', '{}'))
        value = {'start': SEUDO, 'end': ORG, 'type': centinela,
                 'props': {'source_kind': 'judicial', 'source_doc': 'ATS_A.pdf', 'source_page': 2,
                           'source_quote': 'cita sintética'}}
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, 'o-tipo-q', 'edge', '/edge@1', json.dumps(value)))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   ('E-tipo-q', SEUDO, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.commit()
        db.close()
        d = decision(pack_id='E-tipo-q', entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                     edges=[edge_op(uid, 'deny')], _file='E-tipo-q.json')
        huella = seudonimos.sha16(centinela)
        error = f'E-tipo-q.json: unidad {uid[:8]}: tipo de relación fuera de la ontología (spec 069): huella {huella}'
        conn = A.connect(self.root)
        try:
            self.assertEqual(A.gate(conn, [copy.deepcopy(d)], self.root, frozenset({SEUDO}),
                                    frozenset(DOCS_PUBLICOS))[0], [error])
        finally:
            conn.close()
        before = self.destinos()
        for kwargs in ({}, {'dry_run': True}):
            with self.subTest(**kwargs):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                    self.apply(copy.deepcopy(d), **kwargs)
                self.assertIn(error, out.getvalue())
                self.assertNotIn(centinela, out.getvalue() + err.getvalue())
        self.assertEqual(self.destinos(), before)
        # Lo que escribe sin pasar por el gate tampoco lo nombra: la fila del registro, el error de la lista y el
        # dry-run.
        bloque = {'kind': 'edge', 'edge': {'type': centinela, 'start_entity': SEUDO, 'end_entity': ORG}}
        self.assertEqual(A._dato_registro(bloque, SEUDO, True, frozenset({SEUDO})),
                         f'({SEUDO}) -[tipo {huella}]-> (Organization:[organismo]): arista retirada')
        sin_capa = A._accion_seudonimo('conservar', SEUDO, 'D.json', uid, otro=ORG, tipo=centinela,
                                       kind=seudonimos.SIN_CAPA, doc='ATS_A.pdf')
        _, errores = A._aplicar_seudonimos({'aristas': [], 'alias': []}, [sin_capa], self.upd)
        self.assertEqual(len(errores), 1)
        self.assertIn(f'(tipo {huella}): una evidencia conservada está sin capa', errores[0])
        fila = {'source_id': SEUDO, 'target_id': 'Person:zz-tercero-test', 'type': centinela,
                'corrected_type': centinela, 'doc': 'ATS_A.pdf', 'quote_contains': None, 'reason': 'x'}
        for bucket in ('deny', 'fix'):
            self.assertNotIn(centinela, json.dumps(A._imprimible(bucket, fila, frozenset({SEUDO}))))
        self.assertNotIn(centinela, json.dumps(A._imprimible('seudonimos', sin_capa, frozenset({SEUDO}))))

    def test_una_regla_publica_de_seudonimo_sin_cita_tambien_exige_un_documento_publico(self):
        # AC45 (enmienda 10): sin cita también. La regla pública con extremo seudónimo publica su `doc`, así que ha de
        # estar en el árbol público: error del gate con la decisión, la unidad y la huella, nada escrito, y el nombre
        # del documento ni en los errores ni en la salida del apply.
        centinela = 'ALIAS_RETIRADO_SINTETICO'
        privado = f'{centinela}.pdf'
        uid = '8' * 64
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-priv-y', 'relationship', 'r:y', SEUDO, 'MET_WITH', 'judicial', 0, 'h', '{}'))
        value = {'start': SEUDO, 'end': 'Person:zz-tercero-test', 'type': 'MET_WITH',
                 'props': {'source_kind': 'judicial', 'source_doc': privado, 'source_page': 1}}
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, 'o-priv-y', 'edge', '/edge@1', json.dumps(value)))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   ('E-priv-y', SEUDO, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.commit()
        db.close()
        d = decision(pack_id='E-priv-y', entity=SEUDO, units=[unit(uid, 'corregido', 'arista')],
                     edges=[edge_op(uid, 'deny')], _file='E-priv-y.json')
        error = (f'E-priv-y.json: unidad {uid[:8]}: regla con extremo seudónimo que nombra un documento fuera del '
                 f'árbol público (doc {seudonimos.sha16(privado)})')
        conn = A.connect(self.root)
        try:
            self.assertEqual(A.gate(conn, [copy.deepcopy(d)], self.root, frozenset({SEUDO}),
                                    frozenset(DOCS_PUBLICOS))[0], [error])
        finally:
            conn.close()
        before = self.destinos()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.apply(copy.deepcopy(d))
        self.assertIn(error, out.getvalue())
        self.assertNotIn(centinela, out.getvalue() + err.getvalue())
        self.assertEqual(self.destinos(), before)
        self.publicos |= {privado}   # publicado el documento, la regla se escribe con él
        self.apply(copy.deepcopy(d))
        deny = yaml.safe_load((self.repo / A.REL_PATH).read_text(encoding='utf-8'))['deny']
        # Spec 071 AC114: la deny que oculta al tercero es opaca: no publica el documento y su forma no lo admite.
        opaca, = [r for r in deny if r.get('unidad') == uid]
        self.assertEqual(sorted(opaca), ['reason', 'unidad'])
        self.assertIsNotNone(alcance.forma_deny({**opaca, 'doc': privado}))

    def test_una_regla_publica_de_seudonimo_con_cita_de_un_documento_no_publicado_para_sin_escribir(self):
        # Enmienda 6 (AC30): la cita de una regla pública con extremo seudónimo solo puede venir de un documento que
        # el árbol publica entero; si no, error del gate con la decisión y nada escrito (fail-closed). Vale para la
        # decisión sobre el seudónimo y para la de un civil que toca su arista. Codex [medio], 7ª pasada (AC38): el
        # error no nombra el documento (su nombre puede ser lo que se retira): la huella correlaciona.
        centinela = 'ALIAS_RETIRADO_SINTETICO'
        privado = f'{centinela}.pdf'
        db = sqlite3.connect(self.root / 'census.sqlite')
        for uid, entidad, value in (
                ('5' * 64, SEUDO, {'start': SEUDO, 'end': 'Person:zz-tercero-test', 'type': 'MET_WITH'}),
                ('x' * 64, FULDO, {'start': FULDO, 'end': SEUDO, 'type': 'PAID'})):
            db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                       (f'o-priv-{uid[0]}', 'relationship', f'r:{uid[0]}', entidad, value['type'], 'judicial', 0, 'h',
                        '{}'))
            value['props'] = {'source_kind': 'judicial', 'source_doc': privado, 'source_page': 1,
                              'source_quote': 'CITA_NO_PUBLICADA'}
            db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                       ' VALUES (?,?,?,?,?,0,100,"[]")',
                       (uid, f'o-priv-{uid[0]}', 'edge', '/edge@1', json.dumps(value)))
            db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                       (f'E-priv-{uid[0]}', entidad, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.commit()
        db.close()
        decisiones = [decision(pack_id='E-priv-5', entity=SEUDO, units=[unit('5' * 64, 'corregido', 'arista')],
                               edges=[edge_op('5' * 64, 'deny')], _file='E-priv-5.json'),
                      decision(pack_id='E-priv-x', units=[unit('x' * 64, 'corregido', 'arista')],
                               edges=[edge_op('x' * 64, 'set_prop', prop='note', value='nota')], _file='E-priv-x.json')]
        for d in decisiones:
            with self.subTest(d['_file']):
                error = (f'{d["_file"]}: unidad {d["units"][0]["unit_id"][:8]}: regla con extremo seudónimo que nombra '
                         f'un documento fuera del árbol público (doc {seudonimos.sha16(privado)})')
                conn = A.connect(self.root)
                try:
                    self.assertEqual(A.gate(conn, [copy.deepcopy(d)], self.root, frozenset({SEUDO}),
                                            frozenset(DOCS_PUBLICOS))[0], [error])
                finally:
                    conn.close()
                before = self.destinos()
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                    self.apply(copy.deepcopy(d))
                self.assertIn(error, out.getvalue())
                for privada in ('CITA_NO_PUBLICADA', centinela):
                    self.assertNotIn(privada, out.getvalue())
                self.assertEqual(self.destinos(), before)
        self.publicos |= {privado}   # publicado el documento, pasan
        self.apply(*(copy.deepcopy(d) for d in decisiones))
        for d in decisiones:
            self.write(d['_file'][:-len('.json')], d)   # la 071 exige la decisión vigente de cada regla etiquetada
        # Una retirada sensible no deja regla pública: su documento no cuenta.
        db = sqlite3.connect(self.root / 'census.sqlite')
        retirada = {'start': SEUDO, 'end': ORG, 'type': 'WORKS_FOR', 'props': {
            'source_kind': 'judicial', 'source_doc': 'otro-no-publicado.pdf', 'source_page': 3,
            'source_quote': 'CITA_NO_PUBLICADA'}}
        db.execute('UPDATE units SET value=? WHERE unit_id=?', (json.dumps(retirada), U_S_ORG))
        db.commit()
        db.close()
        self.apply(self.retirar())

    def test_el_dry_run_no_imprime_lo_que_se_retira(self):
        # Codex [alto], 4ª pasada: la terminal acaba copiada en PRs. De lo que se retira a un seudónimo (alias; sociedad u
        # organismo con su cita y URL) el dry-run solo dice que se retira; lo conservado sí se nombra.
        retirada = {'start': SEUDO, 'end': ORG, 'type': 'WORKS_FOR', 'props': {
            'source_kind': 'prensa', 'url': 'https://zz.example/retirada', 'source_doc': 'ATS_0000_2025.pdf',
            'source_page': 3, 'source_quote': 'cita retirada sintética'}}
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('UPDATE units SET value=? WHERE unit_id=?', (json.dumps(retirada), U_S_ORG))
        db.commit()
        db.close()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            res = self.apply(self.retirar(), dry_run=True)
        for retirado in ('el consejero Zz Test', ORG, 'zz.example', 'cita retirada'):
            self.assertNotIn(retirado, out.getvalue())
        for conservado in ('"alias": "don Zz Test"', '"alias": "[alias]"', SOC, '"otro": "Organization:[organismo]"'):
            self.assertIn(conservado, out.getvalue())
        # La fila de «capas» (prensa con URL: su YAML de capa se edita a mano) sale entera solo en la traza privada; el
        # recorte a 220 caracteres no es lo que la protege.
        capa, = res['rows']['capas']
        impresa = A._imprimible('capas', capa, frozenset({SEUDO}))
        self.assertEqual((impresa['source'], impresa['target'], impresa['quote'], impresa['url']),
                         (SEUDO, 'Organization:[organismo]', '', ''))
        self.assertEqual((capa['target'], capa['url']), (ORG, 'https://zz.example/retirada'))   # la privada, entera

    def test_el_dry_run_no_nombra_al_titular_retirado(self):
        # Codex [medio], 6ª pasada: la decisión es del organismo y retira su arista de prensa hacia el seudónimo. El
        # dry-run neutralizaba `source`/`target` de la fila de capas, pero no `entidad`, ni la fila del registro, que la
        # unidad empareja con las de capas y seudónimos: el organismo no sale en ninguna línea (enteras, no solo en los
        # 220 caracteres impresos).
        titular, uid, pack = 'Organization:zz-retirado-test', 'q' * 64, 'E-zzretirado01'
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-r-rel', 'relationship', 'r:r', titular, 'PAID', 'prensa', 0, 'h', '{}'))
        pago = {'start': titular, 'end': SEUDO, 'type': 'PAID', 'props': {
            'source_kind': 'prensa', 'url': 'https://zz.example/pago', 'source_doc': 'ATS_1111_2025.pdf',
            'source_page': 2, 'source_quote': 'cita sintética del pago'}}
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, 'o-r-rel', 'edge', '/edge@1', json.dumps(pago)))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   (pack, titular, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.commit()
        db.close()
        d = decision(pack_id=pack, entity=titular, units=[unit(uid, 'corregido', 'arista')],
                     edges=[edge_op(uid, 'deny')], _file=f'{pack}.json')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            plan = self.apply(copy.deepcopy(d), dry_run=True)
        self.assertNotIn('zz-retirado-test', out.getvalue())
        seudos, retiradas = frozenset({SEUDO}), {uid: SEUDO}
        impresas = {b: [A._imprimible(b, r, seudos, retiradas) for r in plan['rows'][b]]
                    for b in ('summaries', 'roles', 'alias_deny', 'deny', 'fix', 'estado', 'rectificaciones', 'capas',
                              'seudonimos')}
        self.assertNotIn('zz-retirado-test', json.dumps(impresas, ensure_ascii=False))
        capa, = impresas['capas']
        self.assertEqual((capa['entidad'], capa['source'], capa['target']),
                         ('Organization:[organismo]', 'Organization:[organismo]', SEUDO))
        fila, = impresas['rectificaciones']
        self.assertEqual((fila['entidad'], fila['dato']), ('Organization:[organismo]',
                                                           '(Organization:[organismo]) -[PAID]-> (Person:[tercero]): '
                                                           'arista retirada'))
        # Lo publicado: la fila del registro es la del titular con un tercero, sin el seudónimo; las trazas, enteras.
        res = self.apply(copy.deepcopy(d))
        fila, = yaml.safe_load((self.repo / A.REG_PATH).read_text())['rectificaciones']
        self.assertEqual((fila['entidad'], fila['dato']),
                         (titular, f'({titular}) -[PAID]-> (Person:[tercero]): arista retirada'))
        self.assertNotIn(SEUDO, (self.repo / A.REG_PATH).read_text())
        self.assertEqual(seudonimos.auditar(self.repo, self.publicos), [])
        self.assertIn(titular, Path(res['capas_path']).read_text())

    def test_el_dry_run_no_imprime_la_url_del_estado_de_un_seudonimo(self):
        # Como la URL de sus filas de capa: la de la fuente de su estado puede nombrar a la persona.
        fila = {'person': 'Zz Test', 'entity_id': SEUDO, 'estado': {
            'status': 'investigado', 'fecha': '2025-04-01', 'fuente': {'url': 'https://zz.example/x', 'tipo': 'prensa'},
            'revisado': '2026-09-21'}}
        impresa = A._imprimible('estado', fila, frozenset({SEUDO}))
        self.assertEqual(impresa['estado']['fuente'], {'url': '', 'tipo': 'prensa'})
        self.assertEqual(fila['estado']['fuente']['url'], 'https://zz.example/x')   # la del informe privado, intacta
        self.assertEqual(A._imprimible('estado', dict(fila, entity_id=FULDO), frozenset({SEUDO}))['estado'],
                         fila['estado'])

    def test_la_evidence_libre_de_un_seudonimo_no_llega_al_registro(self):
        # Codex [alto], 6ª pasada: una retirada sobre un seudónimo con `evidence` «informe sobre <alias> p.7» pasaba la
        # puerta y `fuente` la copiaba entera al registro público, junto al seudónimo; `auditar` no lo ve. La fuente es
        # ahora la referencia estructural; la `evidence`, solo la traza privada.
        centinela = 'ALIAS_RETIRADO_SINTETICO'
        evidencia = f'informe sobre {centinela} p.7'
        d = self.retirar()
        for u in d['units']:
            u['evidence'] = evidencia
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.apply(copy.deepcopy(d), dry_run=True)
        self.assertNotIn(centinela, out.getvalue())
        res = self.apply(copy.deepcopy(d))
        self.assertNotIn(centinela, '\n'.join(t or '' for t in self.destinos()))
        registro = yaml.safe_load((self.repo / A.REG_PATH).read_text())['rectificaciones']
        self.assertEqual([(r['unidad'], r['dato'], r['fuente']) for r in registro], [
            (U_S_ORG, f'({SEUDO}) -[WORKS_FOR]-> (Organization:[organismo]): arista retirada', 'ATS_0000_2025.pdf p.3')])
        traza = Path(res['evidencias_path']).read_text()
        self.assertIn(f'- unidad {U_S_ORG[:8]} ({PS}.json) · fuente pública «ATS_0000_2025.pdf p.3» · evidencia: '
                      f'{evidencia}', traza)

    def test_sin_git_el_gate_y_el_apply_paran_sin_escribir(self):
        # Codex [alto], 6ª pasada: sin conjunto inyectado, gate y apply preguntan a git qué documentos publica el árbol;
        # si git falla (exit 128) paran, en vez de dar por públicos los PDF que haya en disco.
        (self.repo / 'data').mkdir(exist_ok=True)
        for doc in DOCS_PUBLICOS:   # en disco, sin git que diga cuáles se publican
            (self.repo / 'data' / doc).write_text('', encoding='utf-8')
        before = self.destinos()
        with mock.patch.object(seudonimos.subprocess, 'run', side_effect=subprocess.CalledProcessError(128, ['git'])):
            conn = A.connect(self.root)
            try:
                with self.assertRaises(SystemExit) as err:
                    A.gate(conn, [self.retirar()], self.root, frozenset({SEUDO}))
            finally:
                conn.close()
            self.assertEqual(str(err.exception),
                             f'{seudonimos.REPO}: no se puede determinar el árbol público (git) (spec 070)')
            with self.assertRaises(SystemExit) as err:
                A.apply(self.root, self.repo, [self.retirar()], self.tag, self.upd)
            self.assertEqual(str(err.exception), f'{self.repo}: no se puede determinar el árbol público (git) (spec 070)')
        self.assertEqual(self.destinos(), before)
        self.assertFalse((self.root / 'esc/report').exists())


U_PAR = dict(zip(('A', 'B', 'A2'), (f'{c * 64}' for c in 'lmn')))
PP = 'E-zzparalelas1'


class SeudonimosParalelasTest(EscApplyBase):
    """Codex [alto] (spec 070): aristas paralelas de una misma tripleta seudónimo↔organismo. La clave de la
    lista no distingue documento ni cita: conservar una evidencia no puede anular en silencio la retirada
    de otra, `docs` las separa cuando vienen de documentos distintos, y el registro solo dice «suprimido»
    de lo que la política resultante borra de verdad. Ids sintéticos."""

    DOCS = {'A': 'ATS_A.pdf', 'B': 'ATS_B.pdf', 'A2': 'ATS_A.pdf'}

    @staticmethod
    def cita(nombre):
        """AC148 (enmienda 35): la cita de cada evidencia nombra al organismo, y la lista la admite (vía b)."""
        return f'Zz Test trabajó para Zz Organismo Test (evidencia {nombre}).'

    def setUp(self):
        super().setUp()
        self.perm = self.repo / seudonimos.PERMITIDOS_REL
        self.citas_fixture = [seudonimos.entrada_cita(self.cita(n), SEUDO, [self.DOCS[n]], 'decision-2026-10-07',
                                                      'coaparición judicial revisada; no identifica') for n in U_PAR]
        self.escribir_lista()
        db = sqlite3.connect(self.root / 'census.sqlite')
        node = json.dumps({'labels': ['Person'], 'props': {'name': 'Zz Test', 'is_pseudonym': True}})
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-p-node', 'node', 'n:p', SEUDO, 'Person', 'unspecified', 0, 'h', node))
        for name, uid in U_PAR.items():   # SEUDO -[WORKS_FOR]-> ORG, una arista por evidencia
            db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                       (f'o-p-{name}', 'relationship', f'r:{name}', None, 'WORKS_FOR', 'judicial', 0, 'h', '{}'))
            value = {'start': SEUDO, 'end': ORG, 'type': 'WORKS_FOR', 'props': {
                'source_kind': 'judicial', 'source_doc': self.DOCS[name], 'source_page': 3,
                'source_quote': self.cita(name)}}
            db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                       ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, f'o-p-{name}', 'edge', '/edge@1', json.dumps(value)))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   (PP, SEUDO, 'entity', 1, 1, json.dumps(list(U_PAR.values())), 'routed'))
        db.commit()
        db.close()
        nodo_del_censo(self.root, ORG, 'Organization', 'Zz Organismo Test')
        nodo_del_censo(self.root, SOC, 'Company', 'Zz Sociedad Test')

    def lote(self, keep=(), deny=(), retype=(), retirada=()):
        """Decisión (cierre parcial: `_meta.unit_ids`) sobre esas evidencias del pack."""
        nombres = (*keep, *deny, *retype, *retirada)
        units = ([unit(U_PAR[n]) for n in keep] + [unit(U_PAR[n], 'corregido', 'arista') for n in (*deny, *retype)]
                 + [unit(U_PAR[n], 'corregido', 'retirada') for n in retirada])
        edges = ([edge_op(U_PAR[n], 'deny') for n in deny]
                 + [edge_op(U_PAR[n], 'retype', new_type='MEMBER_OF') for n in retype])
        d = decision(pack_id=PP, entity=SEUDO, units=units, edges=edges)
        d['_meta']['unit_ids'] = [U_PAR[n] for n in nombres]
        d['_file'] = f'{PP}.{"-".join(nombres)}.json'
        return d

    def lista(self):
        return [(e['otro'], e['tipo'], e.get('docs')) for e in seudonimos.cargar_permitidos(self.perm)['aristas']]

    def registro(self):
        path = self.repo / A.REG_PATH
        rows = yaml.safe_load(path.read_text())['rectificaciones'] if path.exists() else []
        return sorted((r['unidad'][:1], r['decision']) for r in rows)

    def escribir_lista(self, aristas=(), citas=()):
        """La lista del repo con las citas admitidas del fixture (AC148) y, además, `citas`."""
        self.perm.write_text(seudonimos.volcar_permitidos(
            {'aristas': list(aristas), 'alias': [], 'citas': [*self.citas_fixture, *citas]}), encoding='utf-8')

    def semilla(self, docs=None):
        self.escribir_lista([{'seudonimo': SEUDO, 'otro': ORG, 'tipo': 'WORKS_FOR', 'kinds': ['judicial'],
                              **({'docs': docs} if docs else {}), **SEMILLA}])

    def assertAbortaSinEscribir(self, fragmento, *decisions):
        before = [sha(self.perm), sha(self.repo / A.REL_PATH)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            self.apply(*decisions)
        self.assertIn(fragmento, out.getvalue())
        self.assertEqual([sha(self.perm), sha(self.repo / A.REL_PATH)], before)
        self.assertFalse((self.repo / A.REG_PATH).exists())

    def test_conservar_una_paralela_retira_la_de_otro_documento(self):
        out = self.apply(self.lote(keep=['A', 'A2'], deny=['B']))
        self.assertEqual(self.lista(), [(ORG, 'WORKS_FOR', ['ATS_A.pdf'])])
        self.assertEqual(self.registro(), [('m', 'suprimido')])       # la de B, y B desaparece de verdad
        self.assertEqual(out['written']['permitidos'], {'aristas_añadidas': 1})
        self.assertEqual(seudonimos.auditar(self.repo, self.publicos), [])

    def test_retirada_parcial_de_una_excepcion_acotada(self):
        self.semilla(docs=['ATS_A.pdf', 'ATS_B.pdf'])
        out = self.apply(self.lote(deny=['B']))
        self.assertEqual(self.lista(), [(ORG, 'WORKS_FOR', ['ATS_A.pdf'])])
        self.assertEqual(out['written']['permitidos'], {'aristas_acotadas': 1})
        self.assertEqual(self.registro(), [('m', 'suprimido')])

    def test_una_excepcion_sin_acotar_no_se_retira_a_medias(self):
        self.semilla()
        self.assertAbortaSinEscribir('acótala con `docs`', self.lote(deny=['B']))

    def test_retirar_todas_las_evidencias_quita_la_excepcion(self):
        self.semilla()
        out = self.apply(self.lote(deny=['A', 'B', 'A2']))
        self.assertEqual(self.lista(), [])
        self.assertEqual(out['written']['permitidos'], {'aristas_quitadas': 1})
        self.assertEqual(self.registro(), [('l', 'suprimido'), ('m', 'suprimido'), ('n', 'suprimido')])

    def test_misma_capa_y_documento_aborta_sin_escribir(self):
        self.assertAbortaSinEscribir('misma capa y documento', self.lote(keep=['A'], deny=['A2']))

    def test_el_error_de_una_tripleta_no_nombra_sus_extremos(self):
        # Codex [alto], 4ª pasada: el mensaje acaba en la terminal, en PRs o en el CI, y el seudónimo junto al organismo
        # es lo que se retira. Tipo, huella de la tripleta, decisión y unidades.
        huella = hashlib.sha256('|'.join((SEUDO, ORG, 'WORKS_FOR')).encode()).hexdigest()[:16]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            self.apply(self.lote(keep=['A'], deny=['A2']))
        self.assertIn(f'{PP}.A-A2.json: arista {huella} (WORKS_FOR): conserva y retira', out.getvalue())
        self.assertIn(f'(unidades {U_PAR["A"][:8]}, {U_PAR["A2"][:8]}, spec 070)', out.getvalue())
        for extremo in (SEUDO, ORG):
            self.assertNotIn(extremo, out.getvalue())

    def test_conservar_no_permite_en_silencio_una_paralela_sin_decidir(self):
        # A2 (mismo documento) no la cubre la tanda: hoy no está permitida y conservar A la permitiría.
        self.assertAbortaSinEscribir('misma capa y documento', self.lote(keep=['A']))

    def test_dos_lotes_no_se_pisan(self):
        self.apply(self.lote(keep=['A', 'A2']))        # B (otro documento) no la cubre nadie: no se permite
        self.assertEqual(self.lista(), [(ORG, 'WORKS_FOR', ['ATS_A.pdf'])])
        out = self.apply(self.lote(deny=['B']))        # ya fuera: la lista no cambia y el registro dice la verdad
        self.assertEqual(self.lista(), [(ORG, 'WORKS_FOR', ['ATS_A.pdf'])])
        self.assertEqual(out['written']['permitidos'], {})
        self.assertEqual(self.registro(), [('m', 'suprimido')])

    def test_dos_lotes_en_orden_inverso(self):
        self.apply(self.lote(deny=['B']))
        self.assertEqual(self.lista(), [])
        self.apply(self.lote(keep=['A', 'A2']))
        self.assertEqual(self.lista(), [(ORG, 'WORKS_FOR', ['ATS_A.pdf'])])

    def test_la_lista_no_se_acota_por_un_documento_no_publicado(self):
        # AC45 (enmienda 10): conservar A y A2 y retirar B acota la entrada por el documento de A; si el árbol no lo
        # publica, la lista (pública) lo pondría junto al seudónimo. Error con la decisión, la huella de la tripleta, la
        # del documento y las unidades, nada escrito; y lo mismo si la lista vigente ya lo trae (respaldo de `docs`).
        self.publicos = self.publicos - {'ATS_A.pdf'}
        privado = f'doc {seudonimos.sha16("ATS_A.pdf")}'
        # AC148 (enmienda 35): sin ATS_A.pdf en el árbol, la lista no puede admitir las citas de A y A2 (AC45): su
        # prueba es la vía (a), un export con su documento y sus citas en un chunk.
        self.citas_fixture = [e for e in self.citas_fixture if e['docs'] != ['ATS_A.pdf']]
        self.escribir_lista()
        snap = self.base / 'snap'
        snap.mkdir()
        nodos = [{'ref': 'd', 'labels': ['Document'], 'props': {'doc_id': 'ATS_A.pdf'}},
                 {'ref': 'c', 'labels': ['Chunk'], 'props': {'doc_id': 'ATS_A.pdf',
                                                             'text': f'{self.cita("A")} {self.cita("A2")}'}}]
        (snap / 'nodes.jsonl').write_text(''.join(json.dumps(n, ensure_ascii=False) + '\n' for n in nodos),
                                          encoding='utf-8')
        (snap / 'relationships.jsonl').write_text('', encoding='utf-8')
        casos = ((None, f'la entrada quedaría acotada por un documento fuera del árbol público ({privado})'),
                 (['ATS_A.pdf', 'ATS_B.pdf'], f'(vigente): aristas[0]: documento de `docs` fuera del árbol público '
                                              f'({privado})'))
        for semilla, fragmento in casos:
            with self.subTest(semilla=semilla):
                if semilla:
                    self.semilla(docs=semilla)
                before = [sha(self.perm), sha(self.repo / A.REL_PATH)]
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                    self.apply(self.lote(keep=['A', 'A2'], deny=['B']), export=snap)
                self.assertIn(fragmento, out.getvalue())
                self.assertNotIn('ATS_A.pdf', out.getvalue())
                self.assertEqual([sha(self.perm), sha(self.repo / A.REL_PATH)], before)
                self.assertFalse((self.repo / A.REG_PATH).exists())

    def test_el_registro_no_dice_suprimido_de_lo_que_se_conserva(self):
        # «retirada» sin acción sobre la arista: la lista la conservaría y el registro diría «suprimido». El esquema de
        # la 071 la rechaza antes (fusión 070 ⟷ 071: la más restrictiva), y nada se escribe.
        self.assertAbortaSinEscribir('las aristas se retiran con label arista + action deny', self.lote(retirada=['B']))

    def test_una_evidencia_sin_capa_no_se_conserva(self):
        # La política trata una arista sin `source_kind` como «sin capa»: `kinds: [judicial]` no la cubriría.
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-p-sc', 'relationship', 'r:sc', None, 'PAID', 'unspecified', 0, 'h', '{}'))
        sin_capa = 'Zz Sociedad Test pagó a Zz Test (sin capa).'   # nombra a la sociedad, y la lista la admite
        self.escribir_lista(citas=[seudonimos.entrada_cita(sin_capa, SEUDO, ['ATS_C.pdf'], 'decision-2026-10-07',
                                                           'coaparición judicial revisada; no identifica')])
        value = {'start': SOC, 'end': SEUDO, 'type': 'PAID', 'props': {'source_doc': 'ATS_C.pdf', 'source_page': 2,
                                                                       'source_quote': sin_capa}}
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                   ' VALUES (?,?,?,?,?,0,100,"[]")', ('o' * 64, 'o-p-sc', 'edge', '/edge@1', json.dumps(value)))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                   ('E-zzsincapa01', SEUDO, 'entity', 1, 1, json.dumps(['o' * 64]), 'routed'))
        db.commit()
        db.close()
        self.assertAbortaSinEscribir('sin capa', decision(pack_id='E-zzsincapa01', entity=SEUDO, units=[unit('o' * 64)]))

    def test_retype_de_una_paralela_no_toca_las_demas(self):
        self.semilla()
        self.apply(self.lote(retype=['A']))           # el fix la retipa; A2 y B siguen en WORKS_FOR
        self.assertEqual(self.lista(), [(ORG, 'MEMBER_OF', None), (ORG, 'WORKS_FOR', None)])
        self.assertEqual(self.registro(), [('l', 'rectificado')])
        self.assertEqual(seudonimos.auditar(self.repo, self.publicos), [])


class RegistroSeudonimoTest(unittest.TestCase):
    """Spec 070 (AC7): en la fila de un seudónimo el registro no publica la sociedad, el organismo ni el alias."""

    def test_extremo_neutralizado_en_ambas_direcciones(self):
        seudos = frozenset({SEUDO})
        for end, shown in ((SOC, 'Company:[sociedad]'), (ORG, 'Organization:[organismo]'),
                           ('el consejero Zz Test', '[alias]'), ('Person:zz-tercero', 'Person:[tercero]'),
                           (SEUDO, SEUDO), ('Crime:cohecho', 'Crime:cohecho'), ('', '')):
            self.assertEqual(A._extremo_registro(end, SEUDO, 'PAID', seudos), shown, end)
        self.assertEqual(A._extremo_registro(SOC, FULDO, 'PAID', seudos), SOC)  # sin seudónimo, publicable
        unidad = {'kind': 'edge', 'edge': {'type': 'PAID', 'start_entity': SOC, 'end_entity': SEUDO}}
        self.assertEqual(A._dato_registro(unidad, SEUDO, True, seudos),
                         f'(Company:[sociedad]) -[PAID]-> ({SEUDO}): arista retirada')
        self.assertEqual(A._dato_registro(unidad, SOC, True, seudos),
                         f'({SOC}) -[PAID]-> (Person:[tercero]): arista retirada')

    def test_las_filas_publicadas_ya_estan_saneadas(self):
        ids = seudonimos.ids_seudonimos()
        registro = yaml.safe_load((REPO / A.REG_PATH).read_text())['rectificaciones']
        # Principio P (fusión 070 ⟷ 071): salvo el par que ya nombran su regla pública asociada o la lista.
        explicitas = seudonimos.reglas_explicitas((r.seccion, r.fila) for r in seudonimos.reglas_publicas())
        lista = seudonimos.cargar_permitidos()['aristas']
        juzgadas = [seudonimos.fila_juzgada(r, *explicitas, lista) for r in registro]
        literales = [r['id'] for r in juzgadas if r.get('entidad') in ids
                     and re.search(r'\b(?:Company|Organization):(?!\[)', r.get('dato') or '')]
        self.assertEqual(len(literales), 0, 'filas de seudónimo con la sociedad u organismo literal')   # AC53


class RecordTest(EscApplyBase):
    def setUp(self):
        super().setUp()
        # Spec 070: el apply lee el árbol público con git, y el repo de juguete no lo es: el de los documentos del
        # fixture, como las pruebas del CLI del 070.
        publicos = mock.patch.object(seudonimos, 'documentos_publicos', return_value=frozenset(DOCS_PUBLICOS))
        publicos.start()
        self.addCleanup(publicos.stop)

    def record(self, *decisions, res_prefix='', doc='RUNBOOK-063.md'):
        errors, _ = self.gate(*decisions)
        self.assertEqual(errors, [])
        conn = A.connect(self.root)
        try:
            return A.record(conn, self.root, self.repo, list(decisions), res_prefix, doc)
        finally:
            conn.close()

    def test_record_closes_units_with_backup_and_events(self):
        d = set_unit(decision(), U_SUM, verdict='corregido', label='retirada')
        d['summary'] = {'action': 'remove', 'resumen': '', 'sources': [], 'reason': 'sin fuente'}
        out = self.record(d)
        self.assertEqual(out['unidades'], 5)
        self.assertTrue(Path(out['backup']).exists())
        self.assertEqual(self.state(U_SUM), ('verified', 'opus-cerrada-corregido'))
        self.assertEqual(self.state(U_ROLE), ('verified', 'opus-cerrada'))
        conn = A.connect(self.root)
        events = dict(conn.execute('SELECT event, count(*) FROM events GROUP BY 1').fetchall())
        payload = json.loads(conn.execute('SELECT payload FROM events WHERE unit_id=?',
                                          (U_SUM,)).fetchone()['payload'])
        pack_state = conn.execute('SELECT state FROM esc_packs WHERE pack_id=?', (P1,)).fetchone()['state']
        conn.close()
        self.assertEqual(events, {'esc-cerrada': 5})
        self.assertEqual(payload['doc'], 'RUNBOOK-063.md')
        self.assertEqual(payload['resolution'], 'opus-cerrada-corregido')
        self.assertEqual(pack_state, 'applied')

    def test_human_rounds_use_the_human_events(self):
        d = decision(pack_id=P3, units=[unit(U_EDGE2, verdict='corregido', label='arista')],
                     edges=[edge_op(U_EDGE2, 'deny')], resolution='humano-2026-09-25')
        A.apply(self.root, self.repo, [d], self.tag, self.upd)
        self.record(d)
        self.assertEqual(self.state(U_EDGE2), ('verified', 'humano-2026-09-25-corregido'))
        conn = A.connect(self.root)
        events = [r['event'] for r in conn.execute('SELECT event FROM events')]
        conn.close()
        self.assertEqual(events, ['corregido-humano'])

    def test_units_outside_the_decision_never_change(self):
        self.record(decision())
        self.assertEqual(self.state(U_OTRA), ('pending', None))
        self.assertEqual(self.state(U_EDGE2), ('pending', None))

    def test_a_pack_with_units_still_waiting_for_the_owner_is_not_applied(self):
        conn = A.connect(self.root)
        with conn:
            conn.execute("UPDATE units SET state='needs-human', resolution='tanda-2026-09-25-pendiente'"
                         ' WHERE unit_id IN (?,?)', (U_EDGE, U_ED))
        conn.close()
        out = self.record(partial([unit(U_SUM), unit(U_ROLE), unit(U_ID)], [U_SUM, U_ROLE, U_ID]))
        self.assertEqual((out['unidades'], out.get('packs', 0), out['packs_parciales']), (3, 0, 1))
        self.assertEqual(self.pack_state(P1), 'routed')
        # Cuando el dueño cierra el resto, el pack sí queda aplicado.
        out = self.record(partial([unit(U_EDGE), unit(U_ED)], [U_EDGE, U_ED],
                                  resolution='humano-2026-09-25'))
        self.assertEqual(out['packs'], 1)
        self.assertEqual(self.pack_state(P1), 'applied')

    def test_two_resolutions_of_one_pack_recorded_separately(self):
        first = partial([unit(U_SUM), unit(U_ROLE), unit(U_ID)], [U_SUM, U_ROLE, U_ID],
                        name='P1-a.json')
        second = partial([unit(U_EDGE), unit(U_ED)], [U_EDGE, U_ED], resolution='humano-2026-10-03',
                         name='P1-b.json')
        out = self.record(first)
        self.assertEqual((out.get('packs', 0), out['packs_parciales']), (0, 1))
        self.assertEqual(self.pack_state(P1), 'routed')
        # La otra resolución del mismo pack no se descarta por «pack aplicado».
        self.assertEqual(A.skip_applied(self.root, [first, second]), [first, second])
        out = self.record(second)
        self.assertEqual(out['packs'], 1)
        self.assertEqual(self.pack_state(P1), 'applied')
        self.assertEqual(A.skip_applied(self.root, [first, second]), [])

    def test_res_prefix_narrows_which_decisions_are_recorded(self):
        a = decision()
        b = decision(pack_id=P3, units=[unit(U_EDGE2)], resolution='humano-2026-09-25')
        out = self.record(a, b, res_prefix='humano-')
        self.assertEqual(out['unidades'], 1)
        self.assertEqual(self.state(U_EDGE2)[1], 'humano-2026-09-25')
        self.assertEqual(self.state(U_SUM), ('pending', None))


class MaterializacionTest(EscApplyBase):
    """Spec 071 AC43–AC45 (enmienda 8): lo decidido está aplicado. Una decisión de arista deny/retype/invert de un pack
    `applied` acaba en una regla del overlay que la alcanza, en una fila del registro que la nombra o fuera de la capa
    de acusaciones; `record` no cierra un pack sin eso y `--decisiones` repara los que se cerraron así (13 en la 063)."""

    VALORES = ('pago de 5.700', 'Ficcionbar', 'ficcionbar', 'informe-uco.pdf', 'auto-fuldo.pdf', FULDO, 'Fuldo Fíctez')

    def setUp(self):
        super().setUp()
        # Spec 070: el apply lee el árbol público con git, y el repo de juguete no lo es: el de los documentos del
        # fixture, como las pruebas del CLI del 070.
        publicos = mock.patch.object(seudonimos, 'documentos_publicos', return_value=frozenset(DOCS_PUBLICOS))
        publicos.start()
        self.addCleanup(publicos.stop)
        self.snap = self.base / 'snap'
        self.snap.mkdir()

    def retirada(self, action='deny', resolution='humano-2026-09-25', **over):
        """La decisión del pack P3 sobre U_EDGE2 (el pago de Fuldo a Ficcionbar), escrita en esc/decisions."""
        d = decision(pack_id=P3, units=[unit(U_EDGE2, verdict='corregido', label='arista')],
                     edges=[edge_op(U_EDGE2, action, **over)], resolution=resolution, _file=f'{P3}.json')
        return self.write(P3, d)

    def cerrada_sin_aplicar(self, resolution='humano-2026-09-25'):
        """Lo que hacía `record` antes de AC44: la unidad `verified` y el pack `applied` sin que el apply escribiera."""
        db = A.connect(self.root)
        with db:
            db.execute("UPDATE units SET state='verified', resolution=? WHERE unit_id=?",
                       (f'{resolution}-corregido', U_EDGE2))
            db.execute("UPDATE esc_packs SET state='applied' WHERE pack_id=?", (P3,))
        db.close()

    def censo(self, uid=U_EDGE2):
        db = A.connect(self.root)
        try:
            return json.loads(db.execute('SELECT value FROM units WHERE unit_id=?', (uid,)).fetchone()['value'])
        finally:
            db.close()

    def exportar(self, *uids, rtype=None, invertidas=()):
        """El export de la puerta con Fuldo, el delito, Ficcionbar y un Chunk, y vivas las aristas del censo de `uids`
        (con `rtype`, bajo otro tipo; las de `invertidas`, al revés). Siempre lleva también la de U_EDGE: un export sin
        aristas no vale (AC35)."""
        nodos = [(FULDO, 'Person'), ('Crime:cohecho', 'Crime'), ('Company:ficcionbar', 'Company')]
        refs = {eid: f'4:x:{n}' for n, (eid, _) in enumerate(nodos)}
        with (self.snap / 'nodes.jsonl').open('w', encoding='utf-8') as f:
            for eid, label in nodos:
                f.write(json.dumps({'labels': [label], 'props': {'entity_id': eid}, 'ref': refs[eid]}) + '\n')
            f.write(json.dumps({'labels': ['Chunk'], 'props': {'chunk_id': 'c1'}, 'ref': '4:x:99'}) + '\n')
        with (self.snap / 'relationships.jsonl').open('w', encoding='utf-8') as f:
            for n, uid in enumerate((U_EDGE, *uids)):
                value = self.censo(uid)
                tipo = rtype if rtype and uid != U_EDGE else value['type']
                start, end = (value['end'], value['start']) if uid in invertidas else (value['start'], value['end'])
                f.write(json.dumps({'start': refs[start], 'end': refs[end], 'type': tipo,
                                    'props': value['props'], 'ref': f'5:x:{n}'}, ensure_ascii=False) + '\n')

    def correr(self, *argv):
        """El CLI con el censo y el repo de prueba; (exit, salida). Nunca un valor de la arista en la salida."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = A.main(['--root', str(self.root), '--repo', str(self.repo), *argv])
            except SystemExit as exc:
                code = exc.code
        texto = out.getvalue()
        for valor in self.VALORES:
            self.assertNotIn(valor, texto)
        return code, texto

    def materializacion(self):
        return self.correr('materializacion', '--snapshot', str(self.snap))

    AVISO = f'MATERIALIZACION: retirada decidida sin materializar ({U_EDGE2[:8]}, {P3})'

    # --- AC43 ---------------------------------------------------------------------------------------------------

    def test_a_withdrawal_closed_without_being_applied_and_still_alive_fails(self):
        self.retirada()
        self.cerrada_sin_aplicar()
        self.exportar(U_EDGE2)
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(self.AVISO, texto)
        self.assertIn('sin materializar: 1 con copias vivas, 0 sin copias', texto)

    def test_a_live_edge_is_never_covered_and_a_gone_one_by_its_rule_or_its_exact_row(self):
        # Spec 071 AC47 y AC53: la regla prepara y el export prueba. Con la arista viva nada la acredita, ni su regla ni
        # una fila; sin ella, su regla o una fila con su `unidad` exacta. Un hash en el texto de una fila no nombra nada.
        d = self.retirada()
        A.apply(self.root, self.repo, [d], self.tag, self.upd)
        self.cerrada_sin_aplicar()
        reglas, registro = self.repo / A.REL_PATH, self.repo / A.REG_PATH
        con_regla, con_fila = reglas.read_text(encoding='utf-8'), registro.read_text(encoding='utf-8')
        filas = yaml.safe_load(con_fila)['rectificaciones']
        por_texto = yaml.safe_dump({'rectificaciones': [dict(f, unidad=None, fuente=f'{f["fuente"]} ({U_EDGE2[:12]})')
                                                        for f in filas]}, allow_unicode=True, sort_keys=False)
        sin_fila = f'materializacion: sin copias (sin fila) ({U_EDGE2[:8]}, {P3})'
        # La fila que pierde su unidad es una «suprimido» sin unidad que nombra una arista: sin una regla pública que la
        # retire, no acredita nada y es un problema por sí misma (AC183).
        sin_regla = 'MATERIALIZACION: retirada sin regla pública (R-2026-09-21-0001)'
        for caso, regla, fila, viva, problema, informativo in (
                ('regla y fila, arista viva', con_regla, con_fila, True, True, False),
                ('solo la regla, arista viva', con_regla, None, True, True, False),
                ('solo la fila, arista viva', RELATIONS, con_fila, True, True, False),
                ('un hash en el texto de una fila, arista viva', RELATIONS, por_texto, True, True, False),
                ('ninguna, arista viva', RELATIONS, None, True, True, False),
                ('regla y fila, arista muerta', con_regla, con_fila, False, False, False),
                ('solo la regla, arista muerta', con_regla, None, False, False, False),
                ('solo la fila, arista muerta', RELATIONS, con_fila, False, False, False),
                ('un hash en el texto de una fila, arista muerta', RELATIONS, por_texto, False, False, True),
                ('ninguna, arista muerta', RELATIONS, None, False, False, True)):
            with self.subTest(caso=caso):
                reglas.write_text(regla, encoding='utf-8')
                if fila is None:
                    registro.unlink(missing_ok=True)
                else:
                    registro.write_text(fila, encoding='utf-8')
                self.exportar(*([U_EDGE2] if viva else []))
                code, texto = self.materializacion()
                self.assertEqual(code, 1 if problema or fila is por_texto else 0, texto)
                self.assertEqual(self.AVISO in texto, problema, texto)
                self.assertEqual(sin_fila in texto, informativo, texto)
                self.assertEqual(sin_regla in texto, fila is por_texto, texto)

    def test_a_curated_accusation_its_layer_no_longer_declares_is_materialized(self):
        for crimes, materializada in (([{'name': 'prevaricación'}], True), ([{'name': 'cohecho'}], False)):
            with self.subTest(crimes=crimes):
                self.accusation_layer(crimes)
                self.retirada()
                self.cerrada_sin_aplicar()
                self.exportar(*([] if materializada else [U_EDGE2]))
                code, texto = self.materializacion()
                self.assertEqual(code, 0 if materializada else 1, texto)
                self.assertEqual(self.AVISO in texto, not materializada, texto)

    def test_a_retype_or_an_invert_needs_its_fix_rule(self):
        for accion, over in (('retype', {'new_type': 'ASSOCIATED_WITH'}), ('invert', {})):
            with self.subTest(accion=accion):
                (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
                (self.repo / A.REG_PATH).unlink(missing_ok=True)
                d = self.retirada(accion, **over)
                self.cerrada_sin_aplicar()
                self.exportar(U_EDGE2)
                code, texto = self.materializacion()
                self.assertEqual(code, 1, texto)
                self.assertIn(self.AVISO, texto)
                # Con su regla `fix` (y solo ella: sin la fila) y el export que deja el Path A, ya está aplicada; con la
                # regla pero la arista como estaba, no (AC53).
                A.apply(self.root, self.repo, [d], self.tag, self.upd)
                (self.repo / A.REG_PATH).unlink()
                self.assertEqual(self.materializacion()[0], 1)
                if accion == 'retype':
                    self.exportar(U_EDGE2, rtype='ASSOCIATED_WITH')
                else:
                    self.exportar(U_EDGE2, invertidas={U_EDGE2})
                self.assertEqual(self.materializacion()[0], 0)

    def test_without_live_copies_it_only_counts(self):
        self.retirada()
        self.cerrada_sin_aplicar()
        self.exportar()
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertIn('sin materializar: 0 con copias vivas, 1 sin copias', texto)

    def test_only_the_packs_already_applied_count(self):
        self.retirada()
        self.exportar(U_EDGE2)
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertIn('0 operaciones vigentes de arista deny/retype/invert en 0 packs aplicados', texto)

    def test_an_invalid_export_exits_2(self):
        self.retirada()
        self.cerrada_sin_aplicar()
        (self.snap / 'nodes.jsonl').write_text('', encoding='utf-8')
        (self.snap / 'relationships.jsonl').write_text('', encoding='utf-8')
        code, texto = self.materializacion()
        self.assertEqual(code, 2, texto)
        self.assertIn('materializacion: el export no vale', texto)

    def test_a_registry_that_does_not_load_stops_without_its_value(self):
        # Como en `registro_check` (3ª pasada): `!!int "…"` lanza un ValueError con el valor dentro. El registro se lee
        # con su lector neutro, que da el fichero y la posición.
        self.retirada()
        self.cerrada_sin_aplicar()
        self.exportar(U_EDGE2)
        registro = self.repo / A.REG_PATH
        registro.parent.mkdir(parents=True, exist_ok=True)
        registro.write_text('rectificaciones:\n- id: !!int "CENTINELA_REGISTRO"\n', encoding='utf-8')
        neutro = 'registro.yaml: YAML inválido (posición desconocida)'
        code, texto = self.materializacion()
        self.assertEqual(code, 2, texto)  # no se puede comprobar, como uno con una clave repetida (AC108)
        self.assertIn(neutro, texto)
        self.assertNotIn('CENTINELA_REGISTRO', texto)
        # `record` no lee el registro: sin el export, una fila no acredita nada (AC47). Se niega por la arista.
        code, texto = self.record(self.retirada())
        self.assertEqual(code, 1)
        self.assertIn(f'decisión sin materializar ({P3}.json {U_EDGE2[:8]})', texto)
        self.assertNotIn('CENTINELA_REGISTRO', texto)

    # --- AC46–AC49 (7ª pasada de Codex) -------------------------------------------------------------------------

    def registrar(self, *filas):
        """El registro del repo con estas filas (las claves que el check y la puerta leen)."""
        registro = self.repo / A.REG_PATH
        registro.parent.mkdir(parents=True, exist_ok=True)
        base = {'fecha': '2026-10-01', 'origen': A.ORIGEN, 'entidad': FULDO, 'unidad': U_EDGE2, 'dato': '',
                'fuente': 'informe-uco.pdf p.76', 'decision': 'suprimido', 'fecha_resolucion': '2026-10-01',
                'seed_version': A.SEED_PENDIENTE}
        filas = [dict(base, id=f'R-2026-10-01-{n:04d}', **f) for n, f in enumerate(filas, 1)]
        registro.write_text(yaml.safe_dump({'rectificaciones': filas}, allow_unicode=True, sort_keys=False),
                            encoding='utf-8')

    def otra_decision(self, nombre, accion, resolution, decided_at, **over):
        d = decision(pack_id=P3, units=[unit(U_EDGE2, verdict='verificado' if accion == 'keep' else 'corregido',
                                              label='ok' if accion == 'keep' else 'arista')],
                     edges=[edge_op(U_EDGE2, accion, **over)], resolution=resolution)
        d['_meta']['decided_at'] = decided_at
        return self.write(nombre, d)

    def test_the_operation_in_force_is_the_one_of_the_highest_resolution(self):
        # Spec 071 AC46: manda la resolución humana sobre el arbitraje y este sobre las cerradas; a igual familia, la
        # decisión más reciente. Un keep vigente no deja nada que materializar.
        casos = (('arbitraje deny + humano keep', ('arbitraje-2026-10-03', 'deny', '2026-10-03T09:00:00+00:00'),
                  ('humano-2026-10-07', 'keep', '2026-10-07T09:00:00+00:00'), 0),
                 ('humano keep anterior + arbitraje deny posterior',
                  ('arbitraje-2026-10-08', 'deny', '2026-10-08T09:00:00+00:00'),
                  ('humano-2026-10-07', 'keep', '2026-10-07T09:00:00+00:00'), 0),
                 ('arbitraje keep reciente + arbitraje deny antiguo',
                  ('arbitraje-2026-10-01', 'deny', '2026-10-01T09:00:00+00:00'),
                  ('arbitraje-2026-10-05', 'keep', '2026-10-05T09:00:00+00:00'), 0),
                 ('arbitraje keep antiguo + arbitraje deny reciente',
                  ('arbitraje-2026-10-05', 'deny', '2026-10-05T09:00:00+00:00'),
                  ('arbitraje-2026-10-01', 'keep', '2026-10-01T09:00:00+00:00'), 1),
                 ('dual deny + arbitraje keep', ('dual-cerrada', 'deny', '2026-10-03T09:00:00+00:00'),
                  ('arbitraje-2026-10-01', 'keep', '2026-10-01T09:00:00+00:00'), 0))
        self.cerrada_sin_aplicar()
        self.exportar(U_EDGE2)
        for caso, primera, segunda, esperado in casos:
            with self.subTest(caso=caso):
                for viejo in (self.root / 'esc/decisions').glob('*.json'):
                    viejo.unlink()
                self.otra_decision(P3, primera[1], primera[0], primera[2])
                self.assertEqual(self.materializacion()[0], 1)  # solo la primera: el deny está sin materializar
                self.otra_decision(f'{P3}.segunda', segunda[1], segunda[0], segunda[2])
                code, texto = self.materializacion()
                self.assertEqual(code, esperado, texto)

    def test_a_rectified_row_of_an_earlier_set_prop_does_not_cover_a_retype(self):
        # Codex, 7ª pasada: el retype no tiene regla y la arista sigue viva con su tipo; la fila «rectificado» es de un
        # set_prop anterior. Bastaba con que la fila nombrara la unidad.
        self.retirada('retype', new_type='ASSOCIATED_WITH')
        self.cerrada_sin_aplicar()
        self.exportar(U_EDGE2)
        self.registrar({'decision': 'rectificado',
                        'dato': f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista corregida'})
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(self.AVISO, texto)

    def test_two_units_sharing_their_first_eight_hex_are_not_covered_by_a_row(self):
        # Codex, 7ª pasada: una fila con un hash de 8 en su texto acreditaba toda unidad que empezara por él.
        gemelas = {('cafebabe' + c * 56): (pack, cita) for c, pack, cita in (
            ('1', 'E-c0de00000001-6', 'primer pago ficticio de la trama'),
            ('2', 'E-c0de00000001-7', 'segundo pago ficticio de la trama'))}
        db = A.connect(self.root)
        with db:
            for uid, (pack, cita) in gemelas.items():
                valor = edge_value(end='Company:ficcionbar', rtype='PAID', source_quote=cita)
                db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior,state,resolution)'
                           ' VALUES (?,?,?,?,?,0,100,"[]","verified","humano-2026-09-25-corregido")',
                           (uid, 'o-rel', 'edge', f'/edge@{uid[-1]}9', json.dumps(valor, ensure_ascii=False)))
                db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                           (pack, FULDO, 'entity', 1, 1, json.dumps([uid]), 'applied'))
        db.close()
        for uid, (pack, _) in gemelas.items():
            self.write(pack, decision(pack_id=pack, units=[unit(uid, verdict='corregido', label='arista')],
                                      edges=[edge_op(uid, 'deny')], resolution='humano-2026-09-25'))
        self.exportar(*gemelas)
        self.registrar({'unidad': '', 'dato': 'filas de la tanda cafebabe'})
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertEqual(texto.count('MATERIALIZACION: retirada decidida sin materializar (cafebabe'), 2, texto)

    def test_a_withdrawn_row_does_not_cover_a_retype_whose_edge_is_gone(self):
        # Spec 071 AC93: una fila «suprimido» no acredita un retype; contradice la decisión, que conserva la arista.
        self.retirada('retype', new_type='ASSOCIATED_WITH')
        self.cerrada_sin_aplicar()
        self.exportar()
        self.registrar({'decision': 'suprimido', 'dato': f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista retirada'})
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn('sin materializar: 0 con copias vivas, 0 sin copias (sin fila)', texto)
        self.assertIn(f'MATERIALIZACION: conservación decidida, arista retirada, sin regla que la explique '
                      f'({U_EDGE2[:8]}, {P3})', texto)

    def test_a_rectified_row_does_not_cover_a_deny(self):
        # Una fila «rectificado» dice que la arista se corrigió, no que se retiró: sin regla, un deny sin copias vivas
        # queda «sin copias (sin fila)».
        self.retirada()
        self.cerrada_sin_aplicar()
        self.exportar()
        self.registrar({'decision': 'rectificado',
                        'dato': f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista corregida'})
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertIn(f'materializacion: sin copias (sin fila) ({U_EDGE2[:8]}, {P3})', texto)

    def test_a_rule_the_loader_would_not_apply_does_not_materialize(self):
        # Spec 071 AC48: el loader compara la cita en minúsculas y nada más. Una regla cuyo trozo solo difiere en un
        # espacio no la borra: la arista sigue viva y la puerta lo dice.
        self.retirada()
        self.cerrada_sin_aplicar()
        self.exportar(U_EDGE2)
        reglas = self.repo / A.REL_PATH
        deny = {'source_id': FULDO, 'target_id': 'Company:ficcionbar', 'type': 'PAID', 'doc': 'informe-uco.pdf',
                'quote_contains': 'pago de 5.700  euros', 'reason': f'063 prueba [{U_EDGE2[:8]}]: regla con otro espacio'}
        texto_reglas, _ = A._write_relations(reglas.read_text(encoding='utf-8'),
                                             {'deny': [deny], 'fix': [], 'alias_deny': []}, self.tag)
        reglas.write_text(texto_reglas, encoding='utf-8')
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(self.AVISO, texto)

    def test_an_overlay_the_loader_rejects_stops_without_values(self):
        # Spec 071 AC49: un `!!int` roto es un ValueError con el valor dentro, y un `kinds` desconocido, un SystemExit
        # del loader con él. Paran con exit 2, el fichero y la posición o la fila, sin el valor.
        reglas = self.repo / A.REL_PATH
        original = reglas.read_text(encoding='utf-8')
        self.retirada()
        self.cerrada_sin_aplicar()
        self.exportar(U_EDGE2)
        fila = ('  - {source_id: "Person:fuldo-fictez", target_id: "Company:ficcionbar", type: PAID, '
                '%s, reason: "fila de prueba"}\n')
        for caso, extra, esperado in (
                ('!!int roto', 'quote_contains: !!int "CENTINELA_OVERLAY"', 'relations.yaml: YAML inválido ('),
                ('kinds desconocido', 'kinds: [CENTINELA_OVERLAY]',
                 'relations.yaml:deny[1]: fila rechazada por el loader (SystemExit)')):
            with self.subTest(caso=caso):
                reglas.write_text(original.replace('\nfix:\n', '\n' + fila % extra + '\nfix:\n', 1), encoding='utf-8')
                code, texto = self.materializacion()
                self.assertEqual(code, 2, texto)
                self.assertIn(esperado, texto)
                self.assertNotIn('CENTINELA_OVERLAY', texto + str(code))

    def test_an_internal_error_exits_2_without_values(self):
        self.retirada()
        self.cerrada_sin_aplicar()
        self.exportar(U_EDGE2)
        with mock.patch.object(A, 'load_decisions', side_effect=RuntimeError('CENTINELA_INTERNO')):
            code, texto = self.materializacion()
        self.assertEqual((code, texto.strip()), (2, 'materializacion: error interno (RuntimeError)'))

    # --- AC51–AC55 (8ª pasada de Codex) -------------------------------------------------------------------------

    def afirmacion(self, uid, claim_id):
        """La postura `uid` del censo apunta, como en el real, al nodo Claim de su afirmación por el objeto de su
        relación (`units` no guarda el claim_id: spec 071 AC104)."""
        db = A.connect(self.root)
        with db:
            db.execute('INSERT OR IGNORE INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                       (f'o-{claim_id}', 'node', f'n:{claim_id}', None, 'Claim', 'unspecified', 0, 'h',
                        json.dumps({'labels': ['Claim'], 'props': {'claim_id': claim_id, 'text': 't'}})))
            db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                       (f'o-{uid[:8]}', 'relationship', f'r:{uid[:8]}', FULDO, 'ASSERTS', 'judicial', 0, 'h',
                        json.dumps({'start': 'n:1', 'end': f'n:{claim_id}'})))
            db.execute('UPDATE units SET object_id=? WHERE unit_id=?', (f'o-{uid[:8]}', uid))
        db.close()

    def nueva_arista(self, uid, valor, pack):
        """Una arista más en el censo, en su propio pack aplicado."""
        db = A.connect(self.root)
        with db:
            db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior,state,resolution)'
                       ' VALUES (?,?,?,?,?,0,100,"[]","verified","arbitraje-2026-10-03-corregido")',
                       (uid, 'o-rel', 'edge', f'/edge@{uid[:6]}', json.dumps(valor, ensure_ascii=False)))
            db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                       (pack, FULDO, 'entity', 1, 1, json.dumps([uid]), 'applied'))
        db.close()

    def decidir(self, pack, uid, accion, cuando='2026-10-03T10:00:00+00:00', **over):
        d = decision(pack_id=pack, units=[unit(uid, verdict='verificado' if accion == 'keep' else 'corregido',
                                              label='ok' if accion == 'keep' else 'arista')],
                     edges=[edge_op(uid, accion, **over)], resolution='arbitraje-2026-10-03')
        d['_meta']['decided_at'] = cuando
        return self.write(pack, d)

    def regla(self, seccion, **regla):
        reglas = self.repo / A.REL_PATH
        texto, _ = A._write_relations(reglas.read_text(encoding='utf-8'),
                                      {'deny': [], 'fix': [], 'alias_deny': [], seccion: [regla]}, self.tag)
        reglas.write_text(texto, encoding='utf-8')

    DOS_TROZOS = 'first fragment and second fragment'

    def dos_trozos(self, uid, pack):
        """Codex, 8ª pasada (AC51): un deny con «first fragment» y un retype manual con «second fragment». Los trozos
        no se contienen, pero casan los dos la misma arista: el fix corre antes y el deny ya no la encuentra."""
        self.nueva_arista(uid, edge_value(end='Company:ficcionbar', rtype='OWNS', source_quote=self.DOS_TROZOS), pack)
        self.decidir(pack, uid, 'deny')
        self.regla('deny', source_id=FULDO, target_id='Company:ficcionbar', type='OWNS', doc='informe-uco.pdf',
                   quote_contains='first fragment', reason=f'063 prueba [{uid[:8]}]: retirada')
        self.regla('fix', source_id=FULDO, target_id='Company:ficcionbar', type='OWNS', corrected_type='SUBSIDIARY_OF',
                   doc='informe-uco.pdf', quote_contains='second fragment', reason='063 prueba: retipo manual')

    def test_two_fragments_of_one_quote_are_a_conflict_by_evidence(self):
        uid = 'f1' * 32
        self.dos_trozos(uid, 'E-c0de00000001-8')
        conflicto = 'una regla fix de capa o tipo alcanza el objetivo de un deny (relations.yaml:fix[1] → relations.yaml:deny[1])'
        self.assertEqual(A._guarda_fix_deny({}, self.repo), [])
        self.assertEqual(A._guarda_fix_deny({}, self.repo, A._aristas_censo(self.root)), [conflicto])
        code, texto = self.correr('check')
        self.assertEqual(code, 1, texto)
        self.assertIn(conflicto, texto)

    def test_a_manual_retype_that_reaches_a_withdrawn_edge_leaves_it_alive(self):
        uid = 'f2' * 32
        self.dos_trozos(uid, 'E-c0de00000001-8')
        self.exportar(uid, rtype='SUBSIDIARY_OF')
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, E-c0de00000001-8)', texto)

    def test_a_reversed_twin_with_a_note_does_not_excuse_the_withdrawn_edge(self):
        # Codex, 8ª pasada (AC52): la OWNS b→a con su set_prop de nota no es la OWNS a→b retirada; en un tipo simétrico,
        # sí puede ser la copia que queda.
        for rtype, n, problema in (('OWNS', '1', True), ('ASSOCIATED_WITH', '2', False)):
            with self.subTest(tipo=rtype):
                ua, ub = ('a' + n) * 32, ('b' + n) * 32
                a = edge_value(end='Company:ficcionbar', rtype=rtype)
                b = {'start': 'Company:ficcionbar', 'end': FULDO, 'type': rtype, 'props': dict(a['props'])}
                self.nueva_arista(ua, a, f'E-c0de0000000a-{n}')
                self.nueva_arista(ub, b, f'E-c0de0000000b-{n}')
                self.decidir(f'E-c0de0000000a-{n}', ua, 'deny')
                self.decidir(f'E-c0de0000000b-{n}', ub, 'set_prop', prop='note', value='nota ficticia')
                self.exportar(ua)
                code, texto = self.materializacion()
                aviso = f'MATERIALIZACION: retirada decidida sin materializar ({ua[:8]}, E-c0de0000000a-{n})'
                self.assertEqual(aviso in texto, problema, texto)

    def test_a_rule_does_not_cover_a_symmetric_edge_alive_in_the_other_direction(self):
        # Codex, 8ª pasada (AC53), con la forma de fdf3f1e3: ASSOCIATED_WITH guardada en el censo al revés de la
        # canónica y su retype escrito como el censo. Un grafo que la tiene en la canónica no la retipa (`_RETYPE_Q`
        # casa por dirección): la arista sigue viva y la regla no acredita nada.
        uid, pack = 'd1' * 32, 'E-c0de00000001-9'
        valor = {'start': FULDO, 'end': 'Company:ficcionbar', 'type': 'ASSOCIATED_WITH',
                 'props': {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf', 'source_page': 12,
                           'source_quote': 'Fuldo Fíctez y Ficcionbar mantienen relación comercial'}}
        self.nueva_arista(uid, valor, pack)
        self.decidir(pack, uid, 'retype', new_type='MEMBER_OF')
        self.regla('fix', source_id=FULDO, target_id='Company:ficcionbar', type='ASSOCIATED_WITH',
                   corrected_type='MEMBER_OF', doc='informe-uco.pdf', quote_contains='Fuldo Fíctez y Ficcionbar',
                   reason=f'063 prueba [{uid[:8]}]: retipo')
        self.exportar(uid, invertidas={uid})
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})', texto)
        self.exportar(uid, rtype='MEMBER_OF')
        self.assertEqual(self.materializacion()[0], 0)

    def test_a_rule_whose_unit_was_later_kept_has_no_decision_in_force(self):
        # Codex, 8ª pasada (AC54): el deny de U_EDGE2 sigue en el overlay, pero una resolución humana posterior la
        # conserva: la regla ya no sale de ninguna operación vigente. Lo dicen la puerta y el check.
        d = self.retirada()
        A.apply(self.root, self.repo, [d], self.tag, self.upd)
        self.cerrada_sin_aplicar()
        self.otra_decision(f'{P3}.humano', 'keep', 'humano-2026-10-07', '2026-10-07T10:00:00+00:00')
        self.exportar(U_EDGE2)
        aviso = f'regla sin decisión vigente (relations.yaml:deny[1], {U_EDGE2[:8]})'
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: {aviso}', texto)
        self.assertNotIn(self.AVISO, texto)
        code, texto = self.correr('check')
        self.assertEqual(code, 1, texto)
        self.assertIn(aviso, texto)

    def test_instants_are_compared_in_utc_and_a_tie_that_cannot_be_ordered_stops(self):
        # Spec 071 AC55. Un keep a las 10:30+02:00 (08:30 UTC) es anterior a un deny a las 09:00 UTC: manda el deny.
        self.cerrada_sin_aplicar()
        self.exportar(U_EDGE2)
        self.otra_decision(P3, 'deny', 'arbitraje-2026-10-03', '2026-10-03T09:00:00+00:00')
        self.otra_decision(f'{P3}.b', 'keep', 'arbitraje-2026-10-03', '2026-10-03T10:30:00+02:00')
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(self.AVISO, texto)
        # El mismo instante y operaciones distintas no se pueden ordenar; la misma operación, sí.
        self.otra_decision(f'{P3}.b', 'keep', 'arbitraje-2026-10-03', '2026-10-03T09:00:00+00:00')
        self.assertEqual(self.materializacion(), (2, f'precedencia irresoluble (unidad {U_EDGE2[:8]})\n'))
        self.otra_decision(f'{P3}.b', 'deny', 'arbitraje-2026-10-03', '2026-10-03T09:00:00+00:00')
        self.exportar()
        self.assertEqual(self.materializacion()[0], 0)
        # Sin decided_at no hay instante.
        sin = self.otra_decision(f'{P3}.b', 'deny', 'arbitraje-2026-10-03', '2026-10-03T09:00:00+00:00')
        del sin['_meta']['decided_at']
        self.write(f'{P3}.b', sin)
        self.assertEqual(self.materializacion(), (2, f'decisión con decided_at inválido ({P3}.b.json)\n'))

    # --- AC57–AC60 (9ª pasada de Codex, enmienda 11) ------------------------------------------------------------

    def exportar_aristas(self, *aristas, nodos=(), nombres=None):
        """El export con estas aristas (inicio, fin, tipo, props), sus nodos, los `nodos` sueltos que se pidan y la de
        U_EDGE (un export sin aristas no vale, AC35). Un extremo `Claim:<id>` es un Claim con su claim_id, sin
        entity_id, como en el grafo; un nodo de `nombres` lleva su `name`."""
        base = self.censo(U_EDGE)
        filas = [(base['start'], base['end'], base['type'], base['props']), *aristas]
        nodos = sorted({x for start, end, _, _ in filas for x in (start, end)} | set(nodos))
        refs = {eid: f'4:x:{n}' for n, eid in enumerate(nodos)}
        with (self.snap / 'nodes.jsonl').open('w', encoding='utf-8') as f:
            for eid in nodos:
                etiqueta, _, clave = eid.partition(':')
                props = {'claim_id': clave} if etiqueta == 'Claim' else {'entity_id': eid}
                if (nombres or {}).get(eid):
                    props['name'] = nombres[eid]
                f.write(json.dumps({'labels': [etiqueta], 'props': props, 'ref': refs[eid]}) + '\n')
        with (self.snap / 'relationships.jsonl').open('w', encoding='utf-8') as f:
            for n, (start, end, tipo, props) in enumerate(filas):
                f.write(json.dumps({'start': refs[start], 'end': refs[end], 'type': tipo, 'props': props,
                                    'ref': f'5:x:{n}'}, ensure_ascii=False) + '\n')

    def test_a_retype_followed_by_another_rule_that_changes_its_layer_leaves_it_alive(self):
        # Codex, 9ª pasada (AC57): deny OWNS, retype manual OWNS→CONTROLS y `set` manual sobre CONTROLS a prensa. El loader
        # compone las fases: la retirada sigue viva como CONTROLS/prensa y su regla deny ya no la encuentra.
        uid, pack = 'e3' * 32, 'E-c0de00000001-10'
        valor = edge_value(end='Company:ficcionbar', rtype='OWNS', source_quote='Fuldo Fíctez controla la sociedad')
        self.nueva_arista(uid, valor, pack)
        self.decidir(pack, uid, 'deny')
        par = {'source_id': FULDO, 'target_id': 'Company:ficcionbar', 'doc': 'informe-uco.pdf'}
        self.regla('deny', **par, type='OWNS', quote_contains='Fuldo Fíctez controla',
                   reason=f'063 prueba [{uid[:8]}]: retirada')
        self.regla('fix', **par, type='OWNS', corrected_type='CONTROLS', quote_contains='controla la sociedad',
                   reason='063 prueba: retipo manual')
        self.regla('fix', **par, type='CONTROLS', set={'source_kind': 'prensa'}, quote_contains='controla la sociedad',
                   reason='063 prueba: capa manual')
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'CONTROLS', {**valor['props'], 'source_kind': 'prensa'}))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})', texto)

    def test_a_rule_that_retypes_to_another_type_than_the_decision_is_incompatible(self):
        # Codex, 9ª pasada (AC58): la regla etiquetada tiene que hacer lo que dice la operación vigente, no solo ser de su
        # clase. Vigente retype→CONTROLS y regla que retipa a SUBSIDIARY_OF; publicada así y con su fila «rectificado»,
        # la copia SUBSIDIARY_OF es un estado alcanzable que no es el decidido: incompatible, viva (AC64), y la fila
        # no la acredita.
        uid, pack = 'e4' * 32, 'E-c0de00000001-11'
        valor = edge_value(end='Company:ficcionbar', rtype='OWNS', source_quote='Fuldo Fíctez participa en la sociedad')
        self.nueva_arista(uid, valor, pack)
        self.decidir(pack, uid, 'retype', new_type='CONTROLS')
        self.regla('fix', source_id=FULDO, target_id='Company:ficcionbar', type='OWNS', corrected_type='SUBSIDIARY_OF',
                   doc='informe-uco.pdf', quote_contains='Fuldo Fíctez participa',
                   reason=f'063 prueba [{uid[:8]}]: retipo')
        incompatible = f'regla incompatible con la decisión vigente (relations.yaml:fix[1], {uid[:8]})'
        code, texto = self.correr('check')
        self.assertEqual(code, 1, texto)
        self.assertIn(incompatible, texto)
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'SUBSIDIARY_OF', valor['props']))
        self.registrar({'unidad': uid, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: {incompatible}', texto)
        self.assertIn(f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})', texto)

    def test_a_set_rule_must_carry_the_props_and_values_the_decision_sets(self):
        # Codex, 9ª pasada (AC58): una regla `set` con otro valor u otros props que la decisión vigente es incompatible.
        # La fecha la completa el loader (`date_norm`, `date_year`) en los dos lados; un mapa es una sola operación.
        uid, pack = 'e5' * 32, 'E-c0de00000001-12'
        self.nueva_arista(uid, edge_value(end='Company:ficcionbar', rtype='PAID',
                                          source_quote='pago ficticio a la sociedad'), pack)
        par = {'source_id': FULDO, 'target_id': 'Company:ficcionbar', 'type': 'PAID', 'doc': 'informe-uco.pdf',
               'quote_contains': 'pago ficticio'}
        mensaje = f'regla incompatible con la decisión vigente (relations.yaml:fix[1], {uid[:8]})'
        for caso, op, carga, incompatible in (
                ('la nota decidida', {'prop': 'note', 'value': 'nota decidida'}, {'note': 'nota decidida'}, False),
                ('otra nota', {'prop': 'note', 'value': 'nota decidida'}, {'note': 'otra nota publicada'}, True),
                ('una fecha', {'prop': 'date', 'value': '2021-03-04'}, {'date': '2021-03-04'}, False),
                ('un mapa', {'set': {'note': 'nota', 'source_page': '29'}}, {'note': 'nota', 'source_page': 29}, False),
                ('el mapa sin la página', {'set': {'note': 'nota', 'source_page': '29'}}, {'note': 'nota'}, True)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
                self.decidir(pack, uid, 'set_prop', **op)
                self.regla('fix', **par, set=carga, reason=f'063 prueba [{uid[:8]}]: props')
                code, texto = self.correr('check')
                self.assertEqual((code, mensaje in texto), (1, True) if incompatible else (0, False), texto)

    def test_a_retype_its_rule_covers_without_a_copy_of_the_decided_type_is_only_counted(self):
        # Codex, 9ª pasada (AC58): la regla acredita el retype cuando la arista ya no sigue con su tipo del censo; si
        # tampoco hay copia con el tipo decidido y no tiene fila, lo dice como informativo; con su fila «rectificado»,
        # que afirma la arista corregida, es un error (AC92). AC60: con un tipo decidido simétrico la copia cuenta en
        # cualquier dirección (el colapso puede haberla llevado a la canónica).
        d = self.retirada('retype', new_type='ASSOCIATED_WITH')
        A.apply(self.root, self.repo, [d], self.tag, self.upd)
        self.cerrada_sin_aplicar()
        informativo = f'materializacion: retype sin copia bajo el tipo decidido ({U_EDGE2[:8]}, {P3})'
        error = f'MATERIALIZACION: rectificación decidida sin arista corregida ({U_EDGE2[:8]}, {P3})'
        self.exportar()
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertEqual((error in texto, informativo in texto), (True, False), texto)
        con_fila = (self.repo / A.REG_PATH).read_text(encoding='utf-8')
        (self.repo / A.REG_PATH).unlink()
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertEqual((error in texto, informativo in texto), (False, True), texto)
        (self.repo / A.REG_PATH).write_text(con_fila, encoding='utf-8')
        self.exportar(U_EDGE2, rtype='ASSOCIATED_WITH', invertidas={U_EDGE2})
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertNotIn(informativo, texto)

    def test_a_rectified_row_covers_a_retype_only_with_a_copy_of_the_decided_type(self):
        # Codex, 9ª pasada (AC58): sin regla, una fila «rectificado / arista corregida» acredita el retype solo si el
        # export tiene la copia con el tipo decidido; si no, «rectificación decidida sin arista corregida», con el texto
        # y la condición del registro (AC92).
        self.retirada('retype', new_type='ASSOCIATED_WITH')
        self.cerrada_sin_aplicar()
        self.registrar({'decision': 'rectificado',
                        'dato': f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista corregida'})
        self.exportar(U_EDGE2, rtype='ASSOCIATED_WITH', invertidas={U_EDGE2})
        self.assertEqual(self.materializacion()[0], 0)
        self.exportar()
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: rectificación decidida sin arista corregida ({U_EDGE2[:8]}, {P3})', texto)

    def test_a_withdrawn_accusation_declared_or_alive_under_another_quote_is_not_materialized(self):
        # Codex, 9ª pasada (AC59): el MERGE de accusations_loader conserva persona, delito y documento y actualiza la
        # cita. Declarada en la capa con otra cita, o fuera de ella pero viva con otra cita y página, sigue sin
        # materializar, y su fila «suprimido» no la acredita.
        otra = 'Fuldo Fíctez, acusado de cohecho en el auto'
        for caso, crimes, viva in (('declarada con otra cita', [{'name': 'cohecho', 'quote': otra}], False),
                                   ('fuera de la capa y viva con otra cita', [{'name': 'prevaricación'}], True)):
            with self.subTest(caso=caso):
                self.accusation_layer(crimes)
                self.retirada()
                self.cerrada_sin_aplicar()
                self.registrar({'decision': 'suprimido',
                                'dato': f'({FULDO}) -[ACCUSED_OF]-> (Crime:cohecho): arista retirada'})
                valor = self.censo()
                if viva:
                    self.exportar_aristas((FULDO, 'Crime:cohecho', 'ACCUSED_OF',
                                           {**valor['props'], 'source_quote': otra, 'source_page': 9}))
                else:
                    self.exportar()
                code, texto = self.materializacion()
                self.assertEqual(code, 1, texto)
                self.assertIn(self.AVISO, texto)

    def test_a_symmetric_retype_is_written_and_judged_as_the_census_keeps_it(self):
        # Spec 071 AC60 (invertido tras medir): el censo guarda esta ASSOCIATED_WITH Fuldo→Ficcionbar, al revés de la
        # canónica, y el colapso deja sueltas las simétricas como están. El apply escribe el retype así y `record` lo da
        # por materializado; si un colapso posterior la llevara a la canónica, la regla ya no la casaría y el export la
        # lista viva.
        db = A.connect(self.root)
        with db:
            db.execute('UPDATE units SET value=? WHERE unit_id=?',
                       (json.dumps(edge_value(end='Company:ficcionbar', rtype='ASSOCIATED_WITH',
                                              source_quote='pago de 5.700 euros a Ficcionbar'), ensure_ascii=False),
                        U_EDGE2))
        db.close()
        d = self.retirada('retype', new_type='MEMBER_OF')
        A.apply(self.root, self.repo, [d], self.tag, self.upd)
        regla, = [r for r in yaml.safe_load((self.repo / A.REL_PATH).read_text(encoding='utf-8'))['fix']
                  if f'[{U_EDGE2[:8]}]' in r['reason']]
        self.assertEqual((regla['source_id'], regla['target_id']), (FULDO, 'Company:ficcionbar'))
        out, _ = self.record(d)
        self.assertEqual(out['packs'], 1)
        self.exportar(U_EDGE2, invertidas={U_EDGE2})
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(self.AVISO, texto)
        self.exportar(U_EDGE2, rtype='MEMBER_OF')
        self.assertEqual(self.materializacion()[0], 0)

    REIDENTIFICADA = {'start': FULDO, 'end': 'Company:ficcionbar-antes', 'type': 'PAID',
                      'props': {**edge_value()['props'], 'source_quote': 'pago ficticio a la sociedad'}}

    def test_a_set_whose_census_endpoint_was_reidentified_is_judged_by_its_signature(self):
        # Spec 071, enmienda 11: un extremo del censo ya no está en el export (re-identificado después del censo) y la
        # regla apunta a la entidad actual. Sin el export es un aviso; con él, la firma del censo sobre el par de la regla
        # acredita el set si la copia lleva la nota decidida.
        uid, pack = 'e6' * 32, 'E-c0de00000001-13'
        self.nueva_arista(uid, self.REIDENTIFICADA, pack)
        self.decidir(pack, uid, 'set_prop', prop='note', value='nota decidida')
        self.regla('fix', source_id=FULDO, target_id='Company:ficcionbar', type='PAID', set={'note': 'nota decidida'},
                   doc='informe-uco.pdf', quote_contains='pago ficticio', reason=f'063 prueba [{uid[:8]}]: nota')
        regla = f'(relations.yaml:fix[1], {uid[:8]})'
        code, texto = self.correr('check')
        self.assertEqual(code, 0, texto)
        self.assertIn(f'par distinto del censo: se evalúa en materializacion {regla}', texto)
        props = self.REIDENTIFICADA['props']
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'PAID', {**props, 'note': 'nota decidida'}))
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertIn(f'materializacion: par distinto del censo: acreditada por firma {regla}', texto)
        # Enmienda 12b: sin la nota todavía, la regla alcanza su copia en su propio par: pendiente del Path A, aviso. Y
        # esa copia sin la nota es una corrección decidida que el export no muestra (AC79: la regla prepara, el export
        # prueba), como la retirada pendiente de un deny sigue viva hasta el Path A.
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'PAID', props))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'materializacion: pendiente, par re-identificado {regla}', texto)
        self.assertIn(f'MATERIALIZACION: corrección decidida sin materializar ({uid[:8]}, {pack})', texto)
        self.assertNotIn('sin el estado decidido en el export', texto)
        # Con la copia en otro par, la regla no alcanza nada y su estado decidido no está: apunta a nada, error.
        self.exportar_aristas((FULDO, 'Crime:cohecho', 'PAID', props))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: par distinto del censo, sin el estado decidido en el export {regla}', texto)

    def test_a_deny_whose_census_endpoint_was_reidentified_is_judged_by_its_signature(self):
        # Spec 071, enmienda 11: el deny se acredita si no queda ninguna copia con la firma del censo en ningún par; con
        # una viva en el par de la regla, sigue sin materializar.
        uid, pack = 'e7' * 32, 'E-c0de00000001-14'
        self.nueva_arista(uid, self.REIDENTIFICADA, pack)
        self.decidir(pack, uid, 'deny')
        self.regla('deny', source_id=FULDO, target_id='Company:ficcionbar', type='PAID', doc='informe-uco.pdf',
                   quote_contains='pago ficticio', reason=f'063 prueba [{uid[:8]}]: retirada')
        self.exportar()
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertIn(f'materializacion: par distinto del censo: acreditada por firma (relations.yaml:deny[1], '
                      f'{uid[:8]})', texto)
        regla = f'(relations.yaml:deny[1], {uid[:8]})'
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'PAID', self.REIDENTIFICADA['props']))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})', texto)
        # Enmienda 12b: la regla alcanza esa copia en su propio par: pendiente (aviso); la viva la cuenta el sentido
        # directo hasta el Path A.
        self.assertIn(f'materializacion: pendiente, par re-identificado {regla}', texto)
        self.assertNotIn('sin el estado decidido en el export', texto)
        # La copia en otro par: la regla no la alcanza, apunta a nada, y la copia sigue viva.
        self.exportar_aristas((FULDO, 'Crime:cohecho', 'PAID', self.REIDENTIFICADA['props']))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: par distinto del censo, sin el estado decidido en el export {regla}', texto)
        self.assertIn(f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})', texto)

    # --- AC61–AC62 (enmienda 12) ------------------------------------------------------------------------------

    def test_a_withdrawn_claim_posture_needs_its_row_as_well_as_its_layer(self):
        # Spec 071 AC62: claims.yaml ya no declara la postura retirada, pero sin su fila «suprimido» no está registrada:
        # «sin copias (sin fila)». Con la fila, materializada.
        uid, pack = 'e8' * 32, 'E-c0de00000001-15'
        postura = {'start': FULDO, 'end': None, 'type': 'ASSERTS',
                   'props': {'source_kind': 'prensa', 'source_quote': 'una versión ficticia sostenida en prensa'}}
        self.nueva_arista(uid, postura, pack)
        self.afirmacion(uid, 'c-ficticia')
        self.decidir(pack, uid, 'deny')
        self.exportar()
        sin_fila = f'materializacion: sin copias (sin fila) ({uid[:8]}, {pack})'
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertIn(sin_fila, texto)
        self.registrar({'unidad': uid, 'decision': 'suprimido', 'dato': f'({FULDO}) -[ASSERTS]-> (): arista retirada'})
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertNotIn(sin_fila, texto)

    def test_a_reidentified_accusation_is_matched_by_its_quote_not_by_its_layer_identity(self):
        # Spec 071 AC61: con un extremo re-identificado no hay par en que buscar. La acusación se reconoce por su cita;
        # por la identidad de su capa (documento y tipo) casaría la de cualquier otra persona del mismo auto.
        uid, pack = 'e9' * 32, 'E-c0de00000001-16'
        antes = {'start': 'Person:fuldo-de-antes-ficticio', 'end': 'Crime:cohecho', 'type': 'ACCUSED_OF',
                 'props': {'source_kind': 'acusacion', 'source_doc': 'auto-fuldo.pdf',
                           'source_quote': 'acusado de cohecho en la pieza ficticia'}}
        self.nueva_arista(uid, antes, pack)
        self.decidir(pack, uid, 'deny')
        otra = ('Person:otra-persona-ficticia', 'Crime:cohecho', 'ACCUSED_OF',
                {**antes['props'], 'source_quote': 'otra persona, acusada de cohecho'})
        self.exportar_aristas(otra)
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertNotIn(f'retirada decidida sin materializar ({uid[:8]}', texto)
        self.exportar_aristas(otra, ('Person:fuldo-ahora-ficticio', 'Crime:cohecho', 'ACCUSED_OF',
                                     dict(antes['props'])))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})', texto)

    # --- AC63–AC65 (10ª pasada de Codex, enmienda 13) -----------------------------------------------------------

    ANTES = {'start': 'Person:fuldo-de-antes-ficticio', 'end': 'Company:ficcionbar', 'type': 'OWNS',
             'props': {**edge_value()['props'], 'source_quote': 'participación ficticia en la sociedad'}}

    def test_a_retype_on_the_current_pair_of_a_reidentified_edge_leaves_it_alive(self):
        # Codex, 10ª pasada (AC63): deny OWNS A→B; A se re-identificó a A2; overlay con el deny y un fix OWNS→CONTROLS
        # sobre A2→B; el export tiene CONTROLS A2→B con la misma firma. Las fases solo se componían en el par viejo y el
        # deny se acreditaba buscando OWNS: certificado.
        uid, pack = 'ea' * 32, 'E-c0de00000001-17'
        self.nueva_arista(uid, self.ANTES, pack)
        self.decidir(pack, uid, 'deny')
        par = {'source_id': FULDO, 'target_id': 'Company:ficcionbar', 'type': 'OWNS', 'doc': 'informe-uco.pdf',
               'quote_contains': 'participación ficticia'}
        self.regla('deny', **par, reason=f'063 prueba [{uid[:8]}]: retirada sobre el par vigente')
        self.regla('fix', **par, corrected_type='CONTROLS', reason='063 prueba: retipo posterior sobre el par vigente')
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'CONTROLS', dict(self.ANTES['props'])))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})', texto)

    def test_a_partial_retype_accredited_by_its_signature_still_counts_the_old_copy(self):
        # Codex, 10ª pasada (AC64): retype OWNS→CONTROLS con extremo re-identificado y su regla sobre el par actual. Con
        # OWNS y CONTROLS vivas de la misma firma, la acreditación por firma encontraba CONTROLS y no miraba OWNS. Con
        # solo CONTROLS, el retype completo sigue acreditado.
        uid, pack = 'eb' * 32, 'E-c0de00000001-18'
        self.nueva_arista(uid, self.ANTES, pack)
        self.decidir(pack, uid, 'retype', new_type='CONTROLS')
        self.regla('fix', source_id=FULDO, target_id='Company:ficcionbar', type='OWNS', corrected_type='CONTROLS',
                   doc='informe-uco.pdf', quote_contains='participación ficticia',
                   reason=f'063 prueba [{uid[:8]}]: retipo sobre el par vigente')
        acreditada = f'materializacion: par distinto del censo: acreditada por firma (relations.yaml:fix[1], {uid[:8]})'
        viva = f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})'
        props = dict(self.ANTES['props'])
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'OWNS', props),
                              (FULDO, 'Company:ficcionbar', 'CONTROLS', props))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(viva, texto)
        self.assertIn(acreditada, texto)
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'CONTROLS', props))
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertNotIn(viva, texto)
        self.assertIn(acreditada, texto)

    def test_an_accusation_redeclared_from_another_document_is_still_alive(self):
        # Codex, 10ª pasada (AC65): el MERGE de accusations_loader es por persona, delito y tipo; el documento lo
        # sobrescribe. Cambiar solo el documento en la capa y en el export acreditaba la retirada. Guardas: una capa que
        # ya no declara persona + delito + tipo la materializa, y una decisión humana posterior que la conserva deja de
        # contarla, con una fila «rectificado» que complete la «suprimido» (sin ella, la fila contradice la decisión:
        # AC93).
        self.accusation_layer([{'name': 'cohecho'}])
        documents = self.repo / 'pipeline/data/documents/documents.yaml'
        documents.write_text(documents.read_text(encoding='utf-8')
                             + '  - {doc_id: otro-auto-fuldo.pdf, doc_type: auto}\n', encoding='utf-8')
        acusaciones = self.repo / 'pipeline/data/accusations/accusations.yaml'
        acusaciones.write_text(acusaciones.read_text(encoding='utf-8').replace('auto-fuldo.pdf', 'otro-auto-fuldo.pdf'),
                               encoding='utf-8')
        self.retirada()
        self.cerrada_sin_aplicar()
        self.registrar({'decision': 'suprimido', 'dato': f'({FULDO}) -[ACCUSED_OF]-> (Crime:cohecho): arista retirada'})
        valor = self.censo()
        self.exportar_aristas((FULDO, 'Crime:cohecho', 'ACCUSED_OF',
                               {**valor['props'], 'source_doc': 'otro-auto-fuldo.pdf'}))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(self.AVISO, texto)
        self.otra_decision(f'{P3}.humano', 'keep', 'humano-2026-10-07', '2026-10-07T10:00:00+00:00')
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertNotIn(self.AVISO, texto)
        self.assertIn(f'MATERIALIZACION: conservación decidida, arista retirada, sin regla que la explique '
                      f'({U_EDGE2[:8]}, {P3})', texto)
        self.registrar({'decision': 'suprimido', 'dato': f'({FULDO}) -[ACCUSED_OF]-> (Crime:cohecho): arista retirada'},
                       {'fecha': '2026-10-08', 'decision': 'rectificado', 'fuente': 'decisión del dueño (2026-10-07)',
                        'dato': f'({FULDO}) -[ACCUSED_OF]-> (Crime:cohecho): arista corregida'})
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertNotIn(self.AVISO, texto)
        (self.root / 'esc/decisions' / f'{P3}.humano.json').unlink()
        self.registrar({'decision': 'suprimido', 'dato': f'({FULDO}) -[ACCUSED_OF]-> (Crime:cohecho): arista retirada'})
        self.accusation_layer([{'name': 'prevaricación'}])
        self.exportar()
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertNotIn(self.AVISO, texto)

    # --- AC68–AC71 (11ª pasada de Codex, enmienda 14) -----------------------------------------------------------

    SOCIEDAD = {'source_id': FULDO, 'target_id': 'Company:ficcionbar', 'type': 'OWNS', 'doc': 'informe-uco.pdf',
                'quote_contains': 'participación ficticia'}

    def test_a_reidentified_accusation_is_found_by_its_loader_identity_on_the_current_pair(self):
        # Codex, 11ª pasada (AC68): con el extremo antiguo fuera del export, un deny etiquetado sobre el par actual y la
        # misma acusación publicada con otro documento y otra cita, la firma documental del censo no encontraba nada y
        # el deny se acreditaba por ausencia. El MERGE de accusations_loader la sigue publicando: persona, delito y
        # tipo. La acusación de otra persona por el mismo delito no es ella.
        uid, pack = 'e0' * 32, 'E-c0de00000001-21'
        antes = {'start': 'Person:fuldo-de-antes-ficticio', 'end': 'Crime:cohecho', 'type': 'ACCUSED_OF',
                 'props': {'source_kind': 'acusacion', 'source_doc': 'auto-fuldo.pdf',
                           'source_quote': 'acusado de cohecho en la pieza ficticia'}}
        self.nueva_arista(uid, antes, pack)
        self.decidir(pack, uid, 'deny')
        self.regla('deny', source_id=FULDO, target_id='Crime:cohecho', type='ACCUSED_OF', doc='auto-fuldo.pdf',
                   quote_contains='acusado de cohecho', kinds=['acusacion'],
                   reason=f'063 prueba [{uid[:8]}]: retirada sobre el par vigente')
        otra = {'source_kind': 'acusacion', 'source_doc': 'otro-auto-fuldo.pdf', 'source_quote': 'otra cita del auto'}
        viva = f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})'
        self.exportar_aristas((FULDO, 'Crime:cohecho', 'ACCUSED_OF', otra))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(viva, texto)
        self.assertNotIn('acreditada por firma', texto)
        self.exportar_aristas(('Person:otra-persona-ficticia', 'Crime:cohecho', 'ACCUSED_OF', otra))
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertIn(f'acreditada por firma (relations.yaml:deny[1], {uid[:8]})', texto)

    def test_a_rectified_retype_completing_a_withdrawal_needs_its_resulting_copy(self):
        # Codex, 11ª pasada (AC69): «suprimido» antigua, «rectificado» vigente, retype OWNS→CONTROLS vigente y ninguna
        # copia de los dos tipos en el export: se aceptaba cualquier «suprimido» histórica. La fila vigente afirma la
        # copia resultante, desde el documento que cita; con ella, materializada.
        self.retirada('retype', new_type='CONTROLS')
        self.cerrada_sin_aplicar()
        self.registrar({'fecha': '2026-09-24', 'dato': f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista retirada'},
                       {'fecha': '2026-10-07', 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista corregida'})
        self.exportar()
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: rectificación decidida sin materializar ({U_EDGE2[:8]}, {P3})', texto)
        self.exportar(U_EDGE2, rtype='CONTROLS')
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)

    def test_a_human_keep_after_a_withdrawal_is_published_in_its_direction(self):
        # Codex, 11ª pasada (AC70): una decisión humana conserva la OWNS A→B después de su «suprimido»; un export con
        # solo la B→A pasaba, porque un keep no era candidata. Un keep anterior a la «suprimido» (el caso de dos
        # seudónimos de la spec 070) no cuenta: a esa arista la retiró la tanda posterior.
        uid, pack = 'ef' * 32, 'E-c0de00000001-22'
        valor = edge_value(end='Company:ficcionbar', rtype='OWNS', source_quote='participación ficticia en la sociedad')
        self.nueva_arista(uid, valor, pack)
        self.decidir(pack, uid, 'deny')
        self.registrar({'unidad': uid, 'fecha': '2026-09-24',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista retirada'},
                       {'unidad': uid, 'fecha': '2026-10-07', 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
        aviso = f'MATERIALIZACION: rectificación decidida sin materializar ({uid[:8]}, {pack})'
        for caso, cuando, inversa, problema in (('posterior, solo la inversa', '2026-09-30', True, True),
                                                ('posterior, en su sentido', '2026-09-30', False, False),
                                                ('anterior, solo la inversa', '2026-09-20', True, False)):
            with self.subTest(caso=caso):
                keep = decision(pack_id=pack, units=[unit(uid)], edges=[edge_op(uid, 'keep')],
                                resolution=f'humano-{cuando}')
                keep['_meta']['decided_at'] = f'{cuando}T10:00:00+00:00'
                self.write(f'{pack}.humano', keep)
                start, end = ('Company:ficcionbar', FULDO) if inversa else (FULDO, 'Company:ficcionbar')
                self.exportar_aristas((start, end, 'OWNS', valor['props']))
                code, texto = self.materializacion()
                self.assertEqual((code, aviso in texto), (1, True) if problema else (0, False), texto)

    def test_a_retype_rule_that_also_inverts_leaves_the_inverse_copy_alive(self):
        # Codex, 11ª pasada (AC71): vigente retype OWNS→CONTROLS y una regla etiquetada que además invierte. Se
        # comparaba solo el tipo nuevo, el loader publica CONTROLS al revés y la copia resultante admitía el sentido que
        # añade la propia regla: con esa copia inversa sola, 0. Guarda: el retype sin swap, con su copia en su sentido.
        uid, pack = 'f0' * 32, 'E-c0de00000001-23'
        valor = edge_value(end='Company:ficcionbar', rtype='OWNS', source_quote='participación ficticia en la sociedad')
        self.nueva_arista(uid, valor, pack)
        self.decidir(pack, uid, 'retype', new_type='CONTROLS')
        self.regla('fix', **self.SOCIEDAD, corrected_type='CONTROLS', swap_direction=True,
                   reason=f'063 prueba [{uid[:8]}]: retipo')
        self.exportar_aristas(('Company:ficcionbar', FULDO, 'CONTROLS', valor['props']))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: regla con fase no decidida (relations.yaml:fix[1], {uid[:8]})', texto)
        self.assertIn(f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})', texto)
        (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
        self.regla('fix', **self.SOCIEDAD, corrected_type='CONTROLS', reason=f'063 prueba [{uid[:8]}]: retipo')
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'CONTROLS', valor['props']))
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)

    def test_every_phase_of_a_tagged_rule_needs_its_operation(self):
        # Spec 071 AC71: la regla etiquetada hace exactamente las fases de su operación vigente. Un swap o una nota
        # junto a un retype que no los decidió: «regla con fase no decidida» (los dos casos reales del 07-oct eran una
        # nota). Un retype con la nota en su mapa `set` autoriza las dos fases; una regla que no la escribe, o escribe
        # otra, no hace lo decidido.
        uid, pack = 'f1' * 32, 'E-c0de00000001-24'
        self.nueva_arista(uid, edge_value(end='Company:ficcionbar', rtype='OWNS',
                                          source_quote='participación ficticia en la sociedad'), pack)
        fase = f'regla con fase no decidida (relations.yaml:fix[1], {uid[:8]})'
        incompatible = f'regla incompatible con la decisión vigente (relations.yaml:fix[1], {uid[:8]})'
        for caso, decidida, regla, error in (
                ('retype con swap', {}, {'swap_direction': True}, fase),
                ('retype con una nota no decidida', {}, {'set': {'note': 'nota'}}, fase),
                ('retype y nota decididos', {'set': {'note': 'nota'}}, {'set': {'note': 'nota'}}, None),
                ('retype y otra nota', {'set': {'note': 'nota'}}, {'set': {'note': 'otra nota'}}, incompatible),
                ('retype sin la nota decidida', {'set': {'note': 'nota'}}, {}, fase),  # AC74: la fase que falta
                ('solo el retype', {}, {}, None)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
                self.decidir(pack, uid, 'retype', new_type='CONTROLS', **decidida)
                self.regla('fix', **self.SOCIEDAD, corrected_type='CONTROLS', **regla,
                           reason=f'063 prueba [{uid[:8]}]: retipo')
                code, texto = self.correr('check')
                if error:
                    self.assertEqual(code, 1, texto)
                    self.assertIn(error, texto)
                else:
                    self.assertEqual(code, 0, texto)

    # --- AC73–AC74 (pistas de la 12ª pasada de Codex, enmienda 15) ---------------------------------------------

    NOTA = {'note': 'NOTA_DECIDIDA_SINTETICA'}

    def test_a_reidentified_retype_is_accredited_only_in_its_direction_and_with_its_set(self):
        # AC73: con el extremo antiguo fuera del export, la firma acreditaba el retype sin mirar los props de su
        # `set`, y la copia resultante valía en cualquier sentido. En el par actual, la del tipo nuevo al revés o sin la
        # nota decidida sigue viva; una simétrica al revés y la completa la acreditan.
        uid, pack = 'f4' * 32, 'E-c0de00000001-25'
        self.nueva_arista(uid, self.ANTES, pack)
        regla = f'(relations.yaml:fix[1], {uid[:8]})'
        viva = f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})'
        for caso, nuevo, (inicio, fin), nota, codigo in (
                ('sin la nota decidida', 'CONTROLS', (FULDO, 'Company:ficcionbar'), {}, 1),
                ('al revés', 'CONTROLS', ('Company:ficcionbar', FULDO), self.NOTA, 1),
                ('simétrica al revés', 'ASSOCIATED_WITH', ('Company:ficcionbar', FULDO), self.NOTA, 0),
                ('completa', 'CONTROLS', (FULDO, 'Company:ficcionbar'), self.NOTA, 0)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
                self.decidir(pack, uid, 'retype', new_type=nuevo, set=self.NOTA)
                self.regla('fix', **self.SOCIEDAD, corrected_type=nuevo, set=self.NOTA,
                           reason=f'063 prueba [{uid[:8]}]: retipo sobre el par vigente')
                self.exportar_aristas((inicio, fin, nuevo, {**self.ANTES['props'], **nota}))
                code, texto = self.materializacion()
                self.assertEqual(code, codigo, texto)
                self.assertNotIn('NOTA_DECIDIDA_SINTETICA', texto)
                self.assertEqual(viva in texto, bool(codigo), texto)
                self.assertEqual(f'acreditada por firma {regla}' in texto, not codigo, texto)

    def test_a_retype_whose_set_is_dropped_is_not_materialized(self):
        # AC74: un retype con mapa `set` cuya regla etiquetada descarta la nota es «regla con fase no decidida» (también
        # la fase que falta), y la copia del tipo nuevo sin la nota no lo materializa aunque su regla sea exacta: con la
        # fila «rectificado» o sin ella, «rectificación decidida sin materializar». Con la copia completa, hecho.
        uid, pack = 'f5' * 32, 'E-c0de00000001-26'
        valor = edge_value(end='Company:ficcionbar', rtype='OWNS', source_quote='participación ficticia en la sociedad')
        self.nueva_arista(uid, valor, pack)
        self.decidir(pack, uid, 'retype', new_type='CONTROLS', set=self.NOTA)
        fase = f'MATERIALIZACION: regla con fase no decidida (relations.yaml:fix[1], {uid[:8]})'
        sin_hacer = f'MATERIALIZACION: rectificación decidida sin materializar ({uid[:8]}, {pack})'
        for caso, regla, fila, nota, errores in (
                ('la regla descarta la nota, con su fila', {}, True, {}, (fase, sin_hacer)),
                ('regla exacta, copia sin la nota', self.NOTA, False, {}, (sin_hacer,)),
                ('regla exacta, copia completa', self.NOTA, False, self.NOTA, ())):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
                (self.repo / A.REG_PATH).unlink(missing_ok=True)
                self.regla('fix', **self.SOCIEDAD, corrected_type='CONTROLS', **({'set': regla} if regla else {}),
                           reason=f'063 prueba [{uid[:8]}]: retipo')
                if fila:
                    self.registrar({'unidad': uid, 'decision': 'rectificado',
                                    'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
                self.exportar_aristas((FULDO, 'Company:ficcionbar', 'CONTROLS', {**valor['props'], **nota}))
                code, texto = self.materializacion()
                self.assertEqual(code, 1 if errores else 0, texto)
                self.assertNotIn('NOTA_DECIDIDA_SINTETICA', texto)
                for error in (fase, sin_hacer):
                    self.assertEqual(error in texto, error in errores, texto)

    # --- AC76–AC79 (12ª pasada de Codex, enmienda 16) --------------------------------------------------------------

    OTRA_NOTA = {'note': 'NOTA_EQUIVOCADA_SINTETICA'}

    def test_a_correct_copy_does_not_excuse_another_in_the_decided_state_without_the_load(self):
        # Codex, 12ª pasada (AC76): retype OWNS→CONTROLS con su nota en el mapa `set`, su regla exacta y DOS copias
        # CONTROLS con la misma firma, una con la nota y otra con otra: la correcta lo acreditaba y la otra no se
        # miraba. Igual con un invert. Con la correcta sola, hecho.
        uid, pack = 'f6' * 32, 'E-c0de00000001-27'
        valor = edge_value(end='Company:ficcionbar', rtype='OWNS', source_quote='participación ficticia en la sociedad')
        self.nueva_arista(uid, valor, pack)
        sin_hacer = f'MATERIALIZACION: rectificación decidida sin materializar ({uid[:8]}, {pack})'
        for accion, campos, fase, (inicio, fin, tipo) in (
                ('retype', {'new_type': 'CONTROLS'}, {'corrected_type': 'CONTROLS'},
                 (FULDO, 'Company:ficcionbar', 'CONTROLS')),
                ('invert', {}, {'swap_direction': True}, ('Company:ficcionbar', FULDO, 'OWNS'))):
            (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
            self.decidir(pack, uid, accion, set=self.NOTA, **campos)
            self.regla('fix', **self.SOCIEDAD, **fase, set=self.NOTA, reason=f'063 prueba [{uid[:8]}]: {accion}')
            for caso, notas, codigo in (('una con la nota y otra con otra', (self.NOTA, self.OTRA_NOTA), 1),
                                        ('solo la de la nota', (self.NOTA,), 0)):
                with self.subTest(accion=accion, caso=caso):
                    self.exportar_aristas(*[(inicio, fin, tipo, {**valor['props'], **nota}) for nota in notas])
                    code, texto = self.materializacion()
                    self.assertEqual(code, codigo, texto)
                    self.assertEqual(sin_hacer in texto, bool(codigo), texto)
                    self.assertNotIn('SINTETICA', texto)

    def test_a_reidentified_retype_is_not_accredited_by_one_correct_copy_beside_one_without_its_load(self):
        # AC76 en `_por_firma`: con el extremo antiguo fuera del export, una copia correcta en el par de la regla lo
        # acreditaba aunque otra del tipo nuevo no llevara la nota. Esa sigue viva (AC73) y ya no se acredita por firma;
        # con la correcta sola, sí.
        uid, pack = 'f7' * 32, 'E-c0de00000001-28'
        self.nueva_arista(uid, self.ANTES, pack)
        self.decidir(pack, uid, 'retype', new_type='CONTROLS', set=self.NOTA)
        self.regla('fix', **self.SOCIEDAD, corrected_type='CONTROLS', set=self.NOTA,
                   reason=f'063 prueba [{uid[:8]}]: retipo sobre el par vigente')
        props = dict(self.ANTES['props'])
        acreditada = f'materializacion: par distinto del censo: acreditada por firma (relations.yaml:fix[1], {uid[:8]})'
        viva = f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})'
        correcta = (FULDO, 'Company:ficcionbar', 'CONTROLS', {**props, **self.NOTA})
        self.exportar_aristas(correcta, (FULDO, 'Company:ficcionbar', 'CONTROLS', {**props, **self.OTRA_NOTA}))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(viva, texto)
        self.assertNotIn(acreditada, texto)
        self.assertNotIn('SINTETICA', texto)
        self.exportar_aristas(correcta)
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertIn(acreditada, texto)

    def test_the_load_the_export_must_show_is_the_loaders_with_its_derived_props(self):
        # Codex, 12ª pasada (AC77): retype con `set.date`, regla exacta y una copia del tipo nuevo con la fecha decidida
        # pero `date_norm`/`date_year` viejos o ausentes: la API publica la fecha vieja. Con las derivadas correctas,
        # hecho.
        uid, pack = 'f8' * 32, 'E-c0de00000001-29'
        valor = edge_value(end='Company:ficcionbar', rtype='OWNS', source_quote='participación ficticia en la sociedad')
        self.nueva_arista(uid, valor, pack)
        self.decidir(pack, uid, 'retype', new_type='CONTROLS', set={'date': '2026-01-01'})
        self.regla('fix', **self.SOCIEDAD, corrected_type='CONTROLS', set={'date': '2026-01-01'},
                   reason=f'063 prueba [{uid[:8]}]: retipo con fecha')
        sin_hacer = f'MATERIALIZACION: rectificación decidida sin materializar ({uid[:8]}, {pack})'
        for caso, fecha, codigo in (
                ('derivadas viejas', {'date': '2026-01-01', 'date_norm': '1999-01-01', 'date_year': 1999}, 1),
                ('sin derivadas', {'date': '2026-01-01'}, 1),
                ('derivadas correctas', {'date': '2026-01-01', 'date_norm': '2026-01-01', 'date_year': 2026}, 0)):
            with self.subTest(caso=caso):
                self.exportar_aristas((FULDO, 'Company:ficcionbar', 'CONTROLS', {**valor['props'], **fecha}))
                code, texto = self.materializacion()
                self.assertEqual(code, codigo, texto)
                self.assertEqual(sin_hacer in texto, bool(codigo), texto)

    def test_a_status_that_is_not_canonical_does_not_accredit_a_set_status(self):
        # Guarda de AC77: la carga de un set_status es el estado en su forma canónica, la que escribe el loader. Una
        # copia con otra forma no lo acredita por firma (queda pendiente: la regla la alcanza); con la canónica, sí.
        uid, pack = 'f9' * 32, 'E-c0de00000001-30'
        antes = {'start': 'Person:fuldo-de-antes-ficticio', 'end': 'Crime:cohecho', 'type': 'ACCUSED_OF',
                 'props': {**edge_value()['props'], 'source_quote': 'investigado por cohecho en la pieza ficticia'}}
        self.nueva_arista(uid, antes, pack)
        self.decidir(pack, uid, 'set_status', status='imputado')
        self.regla('fix', source_id=FULDO, target_id='Crime:cohecho', type='ACCUSED_OF', set={'status': 'imputado'},
                   doc='informe-uco.pdf', quote_contains='investigado por cohecho',
                   reason=f'063 prueba [{uid[:8]}]: estado')
        regla = f'(relations.yaml:fix[1], {uid[:8]})'
        for estado, aviso in (('Imputado', f'materializacion: pendiente, par re-identificado {regla}'),
                              ('imputado', f'materializacion: par distinto del censo: acreditada por firma {regla}')):
            with self.subTest(estado=estado):
                self.exportar_aristas((FULDO, 'Crime:cohecho', 'ACCUSED_OF', {**antes['props'], 'status': estado}))
                code, texto = self.materializacion()
                self.assertEqual(code, 0, texto)
                self.assertIn(aviso, texto)

    CENTINELA = 'VALOR_CENTINELA_SINTETICO'

    def cargas_invalidas(self):
        """(acción, campos, motivo) de cada rechazo del loader, con un centinela en el valor donde cabe."""
        c = self.CENTINELA
        return (('retype', {'new_type': 'CONTROLS', 'set': {'source_page': '0', 'note': c}},
                 'source_page: tiene que ser un entero ≥ 1'),
                ('retype', {'new_type': 'CONTROLS', 'set': {'source_quote': c}},
                 'source_quote: fuera de la lista cerrada del loader'),
                # AC82: una clave con forma de identificador fuera del vocabulario cerrado se nombra por su posición.
                ('set_prop', {'prop': c, 'value': 'x'},
                 'prop desconocido (posición 1): fuera de la lista cerrada del loader'),
                ('retype', {'new_type': 'CONTROLS', 'set': {'note': 'x', c: 'y'}},
                 'prop desconocido (posición 2): fuera de la lista cerrada del loader'),
                ('set_prop', {'set': {'source_kind': 'judicial', 'note': c}},
                 'source_kind: solo puede degradar la capa de una arista judicial'),
                ('set_status', {'status': c}, 'status: fuera de los estados procesales de la ontología'),
                ('invert', {'set': {}}, 'el bloque `set` está vacío o no es un mapa'),
                ('set_prop', {'prop': 'amount', 'value': c}, 'amount: no es un número'),
                ('set_prop', {'prop': 'source_page', 'value': c}, 'source_page: no es un entero'))

    def en_memoria(self):
        """El censo del disco, copiado a una base SQLite en memoria."""
        disco, memoria = A.connect(self.root), sqlite3.connect(':memory:')
        disco.backup(memoria)
        disco.close()
        memoria.row_factory = sqlite3.Row
        self.addCleanup(memoria.close)
        return memoria

    def marcas(self, conn):
        """Lo que `record` escribiría: el estado de la unidad, el del pack y los eventos."""
        return (tuple(conn.execute('SELECT state, resolution FROM units WHERE unit_id=?', (U_EDGE2,)).fetchone()),
                conn.execute('SELECT state FROM esc_packs WHERE pack_id=?', (P3,)).fetchone()['state'],
                conn.execute('SELECT count(*) FROM events').fetchone()[0])

    def test_a_load_the_loader_rejects_is_a_decision_that_does_not_count(self):
        # Codex, 12ª pasada (AC78): `_carga` devolvía None tanto sin `set` como con uno que el loader descarta; la fase
        # decidida desaparecía, una regla sin set «coincidía» y `record` marcaba la unidad y el pack. Ahora el esquema
        # la rechaza, `_carga_coincide` y `_fases_no_decididas` no dicen nada, y `record`, `check` y `materializacion`
        # paran con exit 2, con la unidad, el prop y el motivo, nunca el valor.
        from src.alcance import CargaInvalida
        from src.qa_esc_schema import validate_decision
        for accion, campos, motivo in self.cargas_invalidas():
            with self.subTest(accion=accion, motivo=motivo):
                for viejo in (self.root / 'esc/decisions').glob('*.json'):
                    viejo.unlink()
                d = self.retirada(accion, **campos)
                neutro = f'operación con carga inválida ({P3}.json, unidad {U_EDGE2[:8]}: {motivo})'
                conn = A.connect(self.root)
                pack, _ = A._pack_from_ledger(conn, d['_meta'])
                conn.close()
                errores = validate_decision(copy.deepcopy(d), pack)
                self.assertIn(f'edges {U_EDGE2[:8]}: operación con carga inválida ({motivo})', errores)
                self.assertNotIn(self.CENTINELA, '\n'.join(errores))
                regla = {'corrected_type': campos.get('new_type'), 'swap': accion == 'invert', 'set': None}
                for comparar in (A._carga_coincide, A._fases_no_decididas):
                    with self.assertRaises(CargaInvalida):
                        comparar(regla, d['edges'][0])
                memoria = self.en_memoria()
                antes = self.marcas(memoria)
                salida = io.StringIO()
                with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida), \
                        self.assertRaises(SystemExit) as parada:
                    A.record(memoria, self.root, self.repo, [d], '', 'RUNBOOK-063.md')
                self.assertEqual(parada.exception.code, 2)
                self.assertEqual(self.marcas(memoria), antes)
                self.assertEqual(list(self.root.glob('census.pre-063-*.sqlite')), [])
                code, texto = self.correr('check')
                self.assertEqual(code, 2, texto)
                for texto in (salida.getvalue(), texto):
                    self.assertIn(neutro, texto)
                    self.assertNotIn(self.CENTINELA, texto)
                self.cerrada_sin_aplicar()
                self.exportar(U_EDGE2)
                code, texto = self.materializacion()
                self.assertEqual(code, 2, texto)
                self.assertIn(neutro, texto)
                self.assertNotIn(self.CENTINELA, texto)

    def test_a_rejected_load_leaves_no_value_in_the_report_of_record(self):
        # Receta de AC66/AC72: `record` corre dentro de otra suite con el censo en memoria; ni su informe completo ni
        # stdout ni stderr llevan el centinela, y la unidad y el pack siguen sin marcar.
        d = self.retirada('retype', new_type='CONTROLS', set={'source_page': '0', 'note': self.CENTINELA})
        memoria, root, repo = self.en_memoria(), self.root, self.repo
        antes = self.marcas(memoria)

        class Registro(unittest.TestCase):
            def test_record(self):
                A.record(memoria, root, repo, [d], '', 'RUNBOOK-063.md')

        informe, salida, errores = io.StringIO(), io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(errores):
            resultado = unittest.TextTestRunner(stream=informe, verbosity=2).run(Registro('test_record'))
        self.assertEqual(len(resultado.errors), 1)  # el SystemExit(2) de la decisión que no vale
        todo = salida.getvalue() + errores.getvalue() + informe.getvalue()
        self.assertNotIn(self.CENTINELA, todo)
        self.assertIn(f'operación con carga inválida ({P3}.json, unidad {U_EDGE2[:8]}: source_page: tiene que ser un '
                      'entero ≥ 1)', todo)
        self.assertEqual(self.marcas(memoria), antes)

    def test_a_decided_props_correction_is_proved_on_every_copy(self):
        # AC79: una corrección de props (set_prop) de un pack aplicado también se prueba en el export: cada copia de la
        # arista en su par lleva la carga del loader, haya regla o fila o no; si alguna no, «corrección decidida sin
        # materializar»; sin ninguna copia, informativo, salvo si su fila «rectificado» afirma la corrección (AC100).
        sin_corregir = f'MATERIALIZACION: corrección decidida sin materializar ({U_EDGE2[:8]}, {P3})'
        sin_copias = f'materializacion: corrección decidida sin copias ({U_EDGE2[:8]}, {P3})'
        sin_arista = f'MATERIALIZACION: rectificación decidida sin arista corregida ({U_EDGE2[:8]}, {P3})'
        nota = {'note': 'NOTA_DECIDIDA_SINTETICA'}
        d = self.retirada('set_prop', prop='note', value=nota['note'])
        self.cerrada_sin_aplicar()
        props = self.censo()['props']
        con_nota = (FULDO, 'Company:ficcionbar', 'PAID', {**props, **nota})
        sin_nota = (FULDO, 'Company:ficcionbar', 'PAID', props)
        casos = (('sin la nota', [sin_nota], 1, sin_corregir), ('con la nota', [con_nota], 0, None),
                 ('una con la nota y otra sin ella', [con_nota, sin_nota], 1, sin_corregir),
                 ('sin copias', [], 0, sin_copias))
        for regla in (False, True):
            if regla:  # con su regla y su fila «rectificado»: la regla prepara, el export prueba
                A.apply(self.root, self.repo, [d], self.tag, self.upd)
            for caso, aristas, codigo, aviso in casos:
                if regla and not aristas:
                    codigo, aviso = 1, sin_arista
                with self.subTest(regla=regla, caso=caso):
                    self.exportar_aristas(*aristas)
                    code, texto = self.materializacion()
                    self.assertEqual(code, codigo, texto)
                    for linea in (sin_corregir, sin_copias, sin_arista):
                        self.assertEqual(linea in texto, linea == aviso, texto)
                    self.assertIn(f'correcciones de props sin materializar: {int(aviso == sin_corregir)} de 1', texto)
                    self.assertNotIn('SINTETICA', texto)

    def test_a_decided_date_correction_needs_its_derived_props_on_the_copy(self):
        # AC79 con AC77: la fecha corregida sin su `date_norm`/`date_year` no es lo que la API publica.
        d = self.retirada('set_prop', prop='date', value='2026-01-01')
        A.apply(self.root, self.repo, [d], self.tag, self.upd)
        self.cerrada_sin_aplicar()
        props = self.censo()['props']
        for caso, fecha, codigo in (('derivadas viejas', {'date': '2026-01-01', 'date_norm': '1999-01-01',
                                                          'date_year': 1999}, 1),
                                    ('derivadas correctas', {'date': '2026-01-01', 'date_norm': '2026-01-01',
                                                             'date_year': 2026}, 0)):
            with self.subTest(caso=caso):
                self.exportar_aristas((FULDO, 'Company:ficcionbar', 'PAID', {**props, **fecha}))
                code, texto = self.materializacion()
                self.assertEqual(code, codigo, texto)

    def test_record_refuses_a_props_correction_whose_rule_is_missing(self):
        # AC79: `record` no marca una unidad set_prop sin una regla que la materialice (su carga y el alcance del
        # loader), como AC44 con un retype o un invert; tras el apply, sí.
        d = self.retirada('set_prop', prop='note', value='nota ficticia')
        self.assertEqual(self.gate(d)[0], [])
        code, texto = self.record(d)
        self.assertEqual(code, 1, texto)
        self.assertIn(f'decisión sin materializar ({P3}.json {U_EDGE2[:8]})', texto)
        self.assertEqual((self.state(U_EDGE2), self.pack_state(P3)), (('pending', None), 'routed'))
        A.apply(self.root, self.repo, [d], self.tag, self.upd)
        out, _ = self.record(d)
        self.assertEqual(out['packs'], 1)
        self.assertEqual(self.pack_state(P3), 'applied')

    # --- AC80–AC83 (13ª pasada de Codex, enmienda 17) --------------------------------------------------------------

    PRENSA = {'source_kind': 'prensa'}

    def registro_check(self):
        """(exit, salida) de `registro_check --snapshot` sobre el mismo censo, el mismo registro, el mismo overlay y el
        mismo export que `materializacion`: los dos checks cuentan la misma copia igual (AC83)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = RC.main(['--registro', str(self.repo / A.REG_PATH), '--snapshot', str(self.snap), '--root',
                                str(self.root), '--overlay', str(self.repo)])
            except SystemExit as exc:  # un overlay que sus cargadores rechazan
                code = exc.code
        texto = out.getvalue()
        for valor in self.VALORES:
            self.assertNotIn(valor, texto)
        return code, texto

    def los_dos(self, uid, pack, problema, codigo, materializacion):
        """Los dos checks sobre el export actual: los dos fallan (o los dos pasan) por la misma copia."""
        code, texto = self.materializacion()
        self.assertEqual(code, codigo, texto)
        self.assertEqual(f'MATERIALIZACION: {materializacion} ({uid[:8]}, {pack})' in texto, bool(codigo), texto)
        self.assertNotIn('SINTETICA', texto)
        code, texto = self.registro_check()
        self.assertEqual(code, codigo, texto)
        self.assertEqual(f'arista «rectificada» viva sin corregir ({problema}; tolerancia 0)' in texto, bool(codigo),
                         texto)
        self.assertNotIn('SINTETICA', texto)

    def test_a_half_transformed_copy_does_not_hide_behind_the_decided_signature(self):
        # Codex, 13ª pasada (AC80): retype OWNS→CONTROLS con `set={source_kind: prensa}`, regla exacta y fila
        # «rectificado». La CONTROLS que conserva la capa judicial no estaba entre las candidatas del estado decidido
        # (buscadas con la firma de después de la carga) y nunca llegaba a `_lleva`: los dos checks, 0, sola o junto a
        # la bien degradada. Igual con un invert (sin regla: el loader descarta un swap con `set.source_kind`). Con la
        # degradada sola, los dos pasan.
        uid, pack = 'fa' * 32, 'E-c0de00000001-31'
        valor = edge_value(end='Company:ficcionbar', rtype='OWNS', source_quote='participación ficticia en la sociedad')
        self.nueva_arista(uid, valor, pack)
        for accion, campos, fase, (inicio, fin, tipo) in (
                ('retype', {'new_type': 'CONTROLS'}, {'corrected_type': 'CONTROLS'},
                 (FULDO, 'Company:ficcionbar', 'CONTROLS')),
                ('invert', {}, None, ('Company:ficcionbar', FULDO, 'OWNS'))):
            (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
            self.decidir(pack, uid, accion, set=self.PRENSA, **campos)
            if fase:
                self.regla('fix', **self.SOCIEDAD, **fase, set=self.PRENSA, reason=f'063 prueba [{uid[:8]}]: {accion}')
            self.registrar({'unidad': uid, 'decision': 'rectificado',
                            'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
            for caso, capas, codigo in (('solo la que conserva la capa', ('judicial',), 1),
                                        ('la que conserva la capa junto a la degradada', ('judicial', 'prensa'), 1),
                                        ('solo la degradada', ('prensa',), 0)):
                with self.subTest(accion=accion, caso=caso):
                    self.exportar_aristas(*[(inicio, fin, tipo, {**valor['props'], 'source_kind': capa})
                                            for capa in capas])
                    self.los_dos(uid, pack, '1 copia', codigo, 'rectificación decidida sin materializar')

    def test_a_layer_correction_without_a_retype_is_counted_alike_by_both_checks(self):
        # AC80 con AC83: un set_prop que degrada la capa a prensa, con su regla y su fila. La copia que conserva la
        # judicial no está corregida, sola o junto a la degradada: «corrección decidida sin materializar» en
        # `materializacion` y «viva sin corregir» en el registro, que no evaluaba las filas de un set_prop.
        d = self.retirada('set_prop', set=self.PRENSA)
        A.apply(self.root, self.repo, [d], self.tag, self.upd)
        self.cerrada_sin_aplicar()
        props = self.censo()['props']
        for caso, capas, codigo in (('solo la que conserva la capa', ('judicial',), 1),
                                    ('la que conserva la capa junto a la degradada', ('judicial', 'prensa'), 1),
                                    ('solo la degradada', ('prensa',), 0)):
            with self.subTest(caso=caso):
                self.exportar_aristas(*[(FULDO, 'Company:ficcionbar', 'PAID', {**props, 'source_kind': capa})
                                        for capa in capas])
                self.los_dos(U_EDGE2, P3, '1 copia', codigo, 'corrección decidida sin materializar')

    def test_an_accusation_correction_is_proved_on_its_copy_by_its_loader_identity(self):
        # Codex, 13ª pasada (AC81): una acusación de la capa `acusacion` con un set_prop de nota, regla válida y pack
        # aplicado. `accusations_loader` sobrescribe la cita y el documento sobre la misma arista, pero la copia se
        # buscaba por la firma documental: con otra cita u otro documento, «corrección decidida sin copias» y 0. Por la
        # identidad del cargador (persona, delito, tipo y capa) es ella: sin la nota, sin materializar en los dos
        # checks; con ella, materializada. La judicial del mismo par y tipo no es ella.
        self.accusation_layer([{'name': 'cohecho'}])
        d = self.retirada('set_prop', prop='note', value='NOTA_DECIDIDA_SINTETICA')
        A.apply(self.root, self.repo, [d], self.tag, self.upd)
        self.cerrada_sin_aplicar()
        props = self.censo()['props']
        sin_copias = f'materializacion: corrección decidida sin copias ({U_EDGE2[:8]}, {P3})'
        for caso, otra, nota, codigo in (
                ('otra cita, sin la nota', {'source_quote': 'Fuldo, acusado de cohecho'}, {}, 1),
                ('otro documento, sin la nota', {'source_doc': 'otro-auto-fuldo.pdf'}, {}, 1),
                ('otra cita, con la nota', {'source_quote': 'Fuldo, acusado de cohecho'},
                 {'note': 'NOTA_DECIDIDA_SINTETICA'}, 0)):
            with self.subTest(caso=caso):
                self.exportar_aristas((FULDO, 'Crime:cohecho', 'ACCUSED_OF', {**props, **otra, **nota}))
                self.los_dos(U_EDGE2, P3, '1 copia', codigo, 'corrección decidida sin materializar')
                self.assertNotIn(sin_copias, self.materializacion()[1])

    def test_a_reidentified_accusation_correction_is_proved_on_its_current_pair_by_its_identity(self):
        # AC81 en un par re-identificado: la carga se comprueba sobre la copia del par actual que dice la regla
        # etiquetada, por la identidad del cargador, con cualquier documento y cita.
        uid, pack = 'fb' * 32, 'E-c0de00000001-32'
        antes = {'start': 'Person:fuldo-de-antes-ficticio', 'end': 'Crime:cohecho', 'type': 'ACCUSED_OF',
                 'props': {'source_kind': 'acusacion', 'source_doc': 'auto-fuldo.pdf',
                           'source_quote': 'acusado de cohecho en la pieza ficticia'}}
        self.nueva_arista(uid, antes, pack)
        self.decidir(pack, uid, 'set_prop', prop='note', value='NOTA_DECIDIDA_SINTETICA')
        self.regla('fix', source_id=FULDO, target_id='Crime:cohecho', type='ACCUSED_OF', doc='auto-fuldo.pdf',
                   quote_contains='acusado de cohecho', kinds=['acusacion'], set={'note': 'NOTA_DECIDIDA_SINTETICA'},
                   reason=f'063 prueba [{uid[:8]}]: nota sobre el par vigente')
        self.exportar_aristas((FULDO, 'Crime:cohecho', 'ACCUSED_OF',
                               {'source_kind': 'acusacion', 'source_doc': 'otro-auto-fuldo.pdf',
                                'source_quote': 'otra cita del auto'}))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: corrección decidida sin materializar ({uid[:8]}, {pack})', texto)
        self.assertNotIn(f'corrección decidida sin copias ({uid[:8]}', texto)
        self.assertNotIn('SINTETICA', texto)

    # --- AC84–AC88 (14ª pasada de Codex, enmienda 18) --------------------------------------------------------------

    def los_dos_dicen(self, codigo, materializacion=None, registro=None):
        """Los dos checks sobre el mismo export: los dos con exit `codigo`, cada uno con su línea si la hay, y ninguno
        con un valor sintético."""
        for (code, texto), linea in ((self.materializacion(), materializacion), (self.registro_check(), registro)):
            self.assertEqual(code, codigo, texto)
            if linea:
                self.assertIn(linea, texto)
            self.assertNotIn('SINTETICA', texto)

    def test_the_old_type_or_direction_with_the_new_layer_is_alive_in_both_checks(self):
        # Codex, 14ª pasada (AC84): retype OWNS→CONTROLS con `set={source_kind: prensa}`, regla exacta y fila
        # «rectificado»; la OWNS que ya lleva la capa nueva no era incompatible en ningún check, sola o junto a la
        # CONTROLS correcta. Igual con un invert (sin regla: el loader descarta un swap con `set.source_kind`) y la
        # copia sin invertir con la capa nueva. Con la correcta sola, los dos pasan.
        uid, pack = 'fc' * 32, 'E-c0de00000001-33'
        valor = edge_value(end='Company:ficcionbar', rtype='OWNS', source_quote='participación ficticia en la sociedad')
        self.nueva_arista(uid, valor, pack)
        prensa = {**valor['props'], **self.PRENSA}
        antigua = (FULDO, 'Company:ficcionbar', 'OWNS', prensa)
        for accion, campos, fase, correcta in (
                ('retype', {'new_type': 'CONTROLS'}, {'corrected_type': 'CONTROLS'},
                 (FULDO, 'Company:ficcionbar', 'CONTROLS', prensa)),
                ('invert', {}, None, ('Company:ficcionbar', FULDO, 'OWNS', prensa))):
            (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
            self.decidir(pack, uid, accion, set=self.PRENSA, **campos)
            if fase:
                self.regla('fix', **self.SOCIEDAD, **fase, set=self.PRENSA, reason=f'063 prueba [{uid[:8]}]: {accion}')
            self.registrar({'unidad': uid, 'decision': 'rectificado',
                            'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
            for caso, aristas, codigo in (('la antigua con la capa nueva', [antigua], 1),
                                          ('la antigua junto a la correcta', [antigua, correcta], 1),
                                          ('la correcta sola', [correcta], 0)):
                with self.subTest(accion=accion, caso=caso):
                    self.exportar_aristas(*aristas)
                    self.los_dos(uid, pack, '1 copia', codigo, 'retirada decidida sin materializar')

    def por_firma(self, uid, valor):
        """`_por_firma` sobre el export actual, con la regla etiquetada de `uid`, su operación vigente y lo que la
        búsqueda libre no le atribuye (`formas_ajenas`, como en `_contraste_reglas`)."""
        index, nodos = RC.export_vivo(self.snap)
        deny, fix = A._reglas_del_overlay(self.repo)
        regla = next(r for _, r in fix if f'[{uid[:8]}]' in r['reason'])
        _, op = A.vigentes(A.load_decisions(self.root))[uid]
        overlay = [r for _, r in deny + fix]
        _, acciones, sets, retipos = RC.decisiones(self.root, set())
        db = A.connect(self.root)
        try:
            trans = RC.transformadoras(db, acciones, sets, retipos)
            valores = {r['unit_id']: json.loads(r['value'])
                       for r in db.execute("SELECT unit_id, value FROM units WHERE kind='edge'")}
        finally:
            db.close()
        ajenas = RC.formas_ajenas(valores, trans, acciones, sets, retipos, [r for _, r in fix], nodos, overlay)
        return A._por_firma(regla, valor, op, A._clase('fix', regla), index, set(),
                            RC.pares_actuales(uid, valor, nodos, overlay), ajenas.get(uid))

    def test_a_reidentified_accusation_with_another_quote_and_a_wrong_load_is_not_accredited(self):
        # Codex, 14ª pasada (AC85): acusación de la capa `acusacion` retipada CONVICTED_OF→ACCUSED_OF con su nota, con
        # la persona re-identificada y la regla en el par actual. Dos copias: la de la cita del censo con la nota y otra
        # con la cita cambiada (la misma arista para su cargador) y otra nota. `_por_firma` y `sin_su_carga` filtraban
        # por documento y cita: acreditada por firma y 0 en los dos checks. Sin re-identificar, el mismo caso ya daba 1.
        # Con una sola identificación (la del cargador), los dos dan 1 y nada se acredita; con la cita cambiada y la
        # nota decidida, acreditada.
        cita = 'condenado por cohecho en la pieza ficticia'
        cambiada = {'source_quote': 'Fuldo, condenado en otra pieza ficticia'}
        for caso, inicio, uid, pack, mat, reg in (
                ('re-identificada', 'Person:fuldo-de-antes-ficticio', 'fd' * 32, 'E-c0de00000001-34',
                 'retirada decidida sin materializar', 'par re-identificado; 1 copia'),
                ('sin re-identificar', FULDO, 'fe' * 32, 'E-c0de00000001-35', 'rectificación decidida sin materializar',
                 '1 copia')):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
                props = {'source_kind': 'acusacion', 'source_doc': 'auto-fuldo.pdf', 'source_quote': cita}
                valor = {'start': inicio, 'end': 'Crime:cohecho', 'type': 'CONVICTED_OF', 'props': props}
                self.nueva_arista(uid, valor, pack)
                self.decidir(pack, uid, 'retype', new_type='ACCUSED_OF', set=self.NOTA)
                self.regla('fix', source_id=FULDO, target_id='Crime:cohecho', type='CONVICTED_OF',
                           corrected_type='ACCUSED_OF', set=self.NOTA, doc='auto-fuldo.pdf',
                           quote_contains='condenado por cohecho', kinds=['acusacion'],
                           reason=f'063 prueba [{uid[:8]}]: retipo')
                self.registrar({'unidad': uid, 'decision': 'rectificado',
                                'dato': f'({FULDO}) -[CONVICTED_OF]-> (Crime:cohecho): arista corregida'})
                acreditada = f'acreditada por firma (relations.yaml:fix[1], {uid[:8]})'
                self.exportar_aristas((FULDO, 'Crime:cohecho', 'ACCUSED_OF', {**props, **self.NOTA}),
                                      (FULDO, 'Crime:cohecho', 'ACCUSED_OF', {**props, **cambiada, **self.OTRA_NOTA}))
                self.los_dos_dicen(1, f'MATERIALIZACION: {mat} ({uid[:8]}, {pack})',
                                   f'arista «rectificada» viva sin corregir ({reg}; tolerancia 0)')
                self.assertNotIn(acreditada, self.materializacion()[1])
                if inicio != FULDO:
                    self.assertFalse(self.por_firma(uid, valor))
                self.exportar_aristas((FULDO, 'Crime:cohecho', 'ACCUSED_OF', {**props, **cambiada, **self.NOTA}))
                self.los_dos_dicen(0)
                if inicio != FULDO:
                    self.assertTrue(self.por_firma(uid, valor))
                    self.assertIn(f'materializacion: par distinto del censo: {acreditada}', self.materializacion()[1])

    def test_a_props_correction_whose_rule_keeps_the_old_pair_is_found_by_its_signature(self):
        # Codex, 14ª pasada (AC86): set_prop de nota sobre una arista re-identificada; la regla sigue apuntando al par
        # del censo (o no hay regla) y el export tiene la copia en el par actual, con el tipo, el documento y la cita
        # del censo, sin la nota: «corrección decidida sin copias» y 0 en los dos checks. Se busca por documento y cita
        # en todo el export: una copia sin la carga en un par distinto del de la regla está sin materializar. Con la
        # regla alineada, la misma copia (sin el calificativo); con la nota, nada.
        uid, pack = 'c3' * 32, 'E-c0de00000001-36'
        self.nueva_arista(uid, self.REIDENTIFICADA, pack)
        self.decidir(pack, uid, 'set_prop', prop='note', value=self.NOTA['note'])
        self.registrar({'unidad': uid, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista corregida'})
        props = self.REIDENTIFICADA['props']
        fuera = (f'MATERIALIZACION: corrección decidida sin materializar (par distinto del de la regla; {uid[:8]}, '
                 f'{pack})',
                 'arista «rectificada» viva sin corregir (par re-identificado; par distinto del de la regla; 1 copia; '
                 'tolerancia 0)')
        dentro = (f'MATERIALIZACION: corrección decidida sin materializar ({uid[:8]}, {pack})',
                  'arista «rectificada» viva sin corregir (par re-identificado; 1 copia; tolerancia 0)')
        for caso, destino, nota, lineas in (('regla en el par del censo', 'Company:ficcionbar-antes', {}, fuera),
                                            ('sin regla', None, {}, fuera),
                                            ('regla alineada', 'Company:ficcionbar', {}, dentro),
                                            ('regla alineada, con la nota', 'Company:ficcionbar', self.NOTA, None)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
                if destino:
                    self.regla('fix', source_id=FULDO, target_id=destino, type='PAID', set=self.NOTA,
                               doc='informe-uco.pdf', quote_contains='pago ficticio',
                               reason=f'063 prueba [{uid[:8]}]: nota')
                self.exportar_aristas((FULDO, 'Company:ficcionbar', 'PAID', {**props, **nota}))
                self.los_dos_dicen(1 if lineas else 0, *(lineas or ()))
                self.assertNotIn(f'corrección decidida sin copias ({uid[:8]}', self.materializacion()[1])

    def test_a_layer_correction_completing_a_withdrawal_is_published_in_both_checks(self):
        # Codex, 14ª pasada (AC88): «suprimido» completada por una «rectificado», con un set_prop vigente que degrada la
        # capa a prensa y su regla, y la arista publicada en prensa: el registro la daba por «ausente» (comparaba la
        # capa del censo) y `materializacion` daba 0. Con la degradada, los dos pasan; con la que aún es judicial, los
        # dos dicen que está sin corregir, y ninguno que falta.
        d = self.retirada('set_prop', set=self.PRENSA)
        A.apply(self.root, self.repo, [d], self.tag, self.upd)
        self.cerrada_sin_aplicar()
        self.registrar({'fecha': '2026-09-24', 'dato': f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista retirada'},
                       {'fecha': '2026-10-07', 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista corregida'})
        props = self.censo()['props']
        for caso, capa, codigo in (('degradada', 'prensa', 0), ('aún judicial', 'judicial', 1)):
            with self.subTest(caso=caso):
                self.exportar_aristas((FULDO, 'Company:ficcionbar', 'PAID', {**props, 'source_kind': capa}))
                self.los_dos(U_EDGE2, P3, '1 copia', codigo, 'corrección decidida sin materializar')
                self.assertNotIn('ausente del export', self.registro_check()[1])

    # --- AC87 ---------------------------------------------------------------------------------------------------

    # Ids con la forma del registro de entidades (la regla del 070 rechaza otra: D7, 9-nov).
    ORIGEN, DESTINO = 'Person:persona-centinela-origen', 'Company:empresa-centinela-destino'
    CENTINELAS = ('persona-centinela-origen', 'empresa-centinela-destino', 'TIPO_CENTINELA', 'ESTADO_CENTINELA')

    def sin_centinelas(self, llamada):
        """Receta de AC66/AC72: `llamada` corre dentro de otra suite; ni su informe completo ni stdout ni stderr llevan
        un centinela. Devuelve todo lo que sale."""
        class Llamada(unittest.TestCase):
            def test_llamada(self):
                llamada()

        informe, salida, errores = io.StringIO(), io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(errores):
            unittest.TextTestRunner(stream=informe, verbosity=2).run(Llamada('test_llamada'))
        todo = salida.getvalue() + errores.getvalue() + informe.getvalue()
        for centinela in self.CENTINELAS:
            self.assertNotIn(centinela, todo)
        return todo

    def pendiente(self, uid, valor, pack):
        """Una arista más en el censo, pendiente, en su propio pack sin aplicar."""
        db = A.connect(self.root)
        with db:
            db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                       ' VALUES (?,?,?,?,?,0,100,"[]")',
                       (uid, 'o-rel', 'edge', f'/edge@{uid[:6]}', json.dumps(valor, ensure_ascii=False)))
            db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                       (pack, FULDO, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        db.close()

    def test_no_identity_or_value_reaches_the_diagnostics(self):
        # Codex, 14ª pasada (AC87): `_load_check` nombraba la fila que el loader descarta por sus extremos y su tipo
        # (una invert con `set.source_kind`, que `_prepare_fix` descarta), y el esquema citaba `new_type` y
        # `estado.status` tal cual; redirigir la puerta no protege las invocaciones directas. Con centinelas en los
        # extremos, el tipo y el estado: ni `check`, ni el apply en modo check, ni `record`, ni `materializacion` los
        # imprimen, ni en su informe de unittest.
        ui, ut = 'c4' * 32, 'c5' * 32
        valor = {'start': self.ORIGEN, 'end': self.DESTINO, 'type': 'OWNS',
                 'props': {**edge_value()['props'], 'source_quote': 'participación ficticia en la sociedad'}}
        self.pendiente(ui, valor, 'E-ce1e00000000-1')
        self.pendiente(ut, dict(valor, props={**valor['props'], 'source_quote': 'otra participación ficticia'}),
                       'E-ce1e00000000-2')
        invierte = self.write('E-ce1e00000000-1', decision(pack_id='E-ce1e00000000-1', units=[
            unit(ui, verdict='corregido', label='arista')], edges=[edge_op(ui, 'invert', set=self.PRENSA)]))
        retipa = self.write('E-ce1e00000000-2', decision(
            pack_id='E-ce1e00000000-2', units=[unit(ut, verdict='corregido', label='arista')],
            edges=[edge_op(ut, 'retype', new_type='TIPO_CENTINELA')],
            estado={'action': 'set', 'status': 'ESTADO_CENTINELA', 'fecha': '2026-10-01', 'doc': 'auto-fuldo.pdf',
                    'url': '', 'tipo': 'auto', 'nota': 'fila de prueba'}))
        invierte['_file'], retipa['_file'] = 'E-ce1e00000000-1.json', 'E-ce1e00000000-2.json'
        argv = ['--root', str(self.root), '--repo', str(self.repo)]
        todo = self.sin_centinelas(lambda: A.main([*argv, 'check']))
        for neutro in (f'fix[1] [{ui[:8]}]: el loader rechaza la fila (SystemExit)', 'new_type fuera del vocabulario',
                       'estado.status fuera de vocabulario'):
            self.assertIn(neutro, todo)
        self.assertIn('fix[1] [', self.sin_centinelas(
            lambda: A.apply(self.root, self.repo, [invierte, retipa], self.tag, self.upd, check=True)))
        self.assertIn('new_type fuera del vocabulario', self.sin_centinelas(lambda: A.main([*argv, 'record'])))
        memoria = self.en_memoria()
        self.assertIn(f'decisión sin materializar (E-ce1e00000000-1.json {ui[:8]})', self.sin_centinelas(
            lambda: A.record(memoria, self.root, self.repo, [invierte], '', 'RUNBOOK-063.md')))
        db = A.connect(self.root)
        with db:
            db.execute("UPDATE esc_packs SET state='applied' WHERE pack_id LIKE 'E-ce1e00000000-%'")
        db.close()
        self.exportar_aristas((self.ORIGEN, self.DESTINO, 'OWNS', dict(valor['props'])))
        self.assertIn(f'retirada decidida sin materializar ({ui[:8]}, E-ce1e00000000-1)',
                      self.sin_centinelas(lambda: A.main([*argv, 'materializacion', '--snapshot', str(self.snap)])))

    def test_the_load_check_names_section_position_and_unit_never_the_row(self):
        # AC87 en `_load_check`: un tipo fuera de la lista (el loader lo descarta y lo anota), un `kinds` desconocido
        # (un SystemExit del loader con un extremo) y un alias_deny sin id. Cada rechazo, por sección, posición en el
        # lote y unidad si la regla la lleva; los logs del loader callan.
        filas = {'claims': [], 'alias_deny': [{'drop': ['ALIAS_CENTINELA'], 'reason': 'x'}],
                 'deny': [{'source_id': self.ORIGEN, 'target_id': self.DESTINO, 'type': 'TIPO_CENTINELA',
                           'reason': '063 prueba [c4c4c4c4]: retirada'}],
                 'fix': [{'source_id': self.ORIGEN, 'target_id': self.DESTINO, 'type': 'OWNS',
                          'corrected_type': 'CONTROLS', 'kinds': ['ESTADO_CENTINELA'],
                          'reason': '063 prueba [c5c5c5c5]: retipo'}]}
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
            errores = A._load_check(filas)
        # Spec 071 AC159: una deny con un tipo fuera de la lista ya no se descarta: el loader la rechaza.
        self.assertEqual(errores, ['deny[1] [c4c4c4c4]: el loader rechaza la fila (SystemExit)',
                                   'fix[1] [c5c5c5c5]: el loader rechaza la fila (SystemExit)',
                                   'alias_deny[1]: el loader rechaza la fila (SystemExit)'])
        for centinela in (*self.CENTINELAS, 'ALIAS_CENTINELA'):
            self.assertNotIn(centinela, salida.getvalue() + '\n'.join(errores))

    def test_the_apply_diagnostics_carry_no_value(self):
        # AC87 en el resto de `qa_esc_apply`: un importe ambiguo o que no es un número, una postura de claims.yaml, una
        # decisión sin fichero (su entity_id no es su nombre en un informe) y la deriva de la prosa.
        for valor, motivo in (('5.700', 'ambiguo'), ('CENTINELA_IMPORTE', 'no es un número')):
            with self.subTest(importe=valor):
                problema = A._amount_error(valor)
                self.assertIn(motivo, problema)
                self.assertNotIn(valor, problema)
        claims = A._claims_check([{'entity_id': self.ORIGEN, 'type': 'DISPUTES', 'action': 'set_prop',
                                   'quote': 'cita', 'ref': 'P.json', 'unit': 'abcd1234'}], self.base / 'no-esta.yaml')
        self.assertEqual(claims, ['claims: P.json [abcd1234] -[DISPUTES]-> Claim: «set_prop» sobre una postura se '
                                  'edita a mano en claims.yaml'])
        sin_fichero = decision(entity=self.ORIGEN, pack_id='E-noexiste')
        errores, _ = self.gate(sin_fichero)
        self.assertTrue(errores, errores)
        self.assertNotIn('persona-centinela-origen', '\n'.join(errores))
        con_prosa = full_decision()
        con_prosa['_pack'] = {'entity': {'id': self.ORIGEN},
                              'census_prose': {'summary': 'texto del censo', 'role': 'rol del censo'}}
        with mock.patch.object(A, '_published_prose', return_value={self.ORIGEN: {'summary': 'otro texto publicado'}}):
            deriva = A.drift_check([con_prosa], self.repo)
        self.assertEqual(len(deriva), 1)
        self.assertNotIn('persona-centinela-origen', deriva[0])

    # --- AC89–AC90 (ampliación de la enmienda 18) -------------------------------------------------------------------

    OTRO = 'Person:otra-persona-ficticia'

    def del_pasaje(self, start, end, rtype='OWNS'):
        """Una arista del mismo pasaje que ANTES: mismo documento y misma cita."""
        return {'start': start, 'end': end, 'type': rtype, 'props': dict(self.ANTES['props'])}

    def test_a_retype_or_an_invert_is_also_judged_outside_the_pairs_of_its_rule_in_both_checks(self):
        # AC89: la regla apunta al par del censo, que ya no tiene ninguna copia (o el extremo se re-identificó y no hay
        # regla). La búsqueda libre halla la copia en otro par que comparte exactamente un extremo: con el tipo antiguo
        # (o sin invertir) o en el estado decidido sin su carga, sin materializar en un par distinto del de la regla en
        # los dos checks; la correcta se acepta en los dos (antes, la re-identificada caía en «rectificación decidida
        # sin materializar» por no ver su copia resultante). Cada caso, con su propio pasaje: las aristas de los casos
        # anteriores siguen en el censo, con un extremo fuera de este export, y del mismo pasaje harían ambigua la copia
        # (AC91).
        for n, (caso, accion, inicio, fin, regla) in enumerate((
                ('par del censo vivo', 'retype', FULDO, 'Company:ficcionbar-vieja-r', True),
                ('par del censo vivo', 'invert', FULDO, 'Company:ficcionbar-vieja-i', True),
                ('re-identificada, sin regla', 'retype', 'Person:fuldo-de-antes-r', 'Company:ficcionbar', False),
                ('re-identificada, sin regla', 'invert', 'Person:fuldo-de-antes-i', 'Company:ficcionbar', False))):
            uid, pack = f'd{n + 2}' * 32, f'E-c0de00000001-5{n}'
            for vieja in (self.root / 'esc/decisions').glob('*.json'):
                vieja.unlink()
            (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
            props = {**self.ANTES['props'], 'source_quote': f'participación ficticia en la sociedad, pasaje {n}'}
            self.nueva_arista(uid, {'start': inicio, 'end': fin, 'type': 'OWNS', 'props': props}, pack)
            campos, fase = ({'new_type': 'CONTROLS'}, {'corrected_type': 'CONTROLS'}) if accion == 'retype' \
                else ({}, {'swap_direction': True})
            self.decidir(pack, uid, accion, set=self.NOTA, **campos)
            if regla:
                self.regla('fix', source_id=inicio, target_id=fin, type='OWNS', **fase, set=self.NOTA,
                           doc='informe-uco.pdf', quote_contains='participación ficticia',
                           reason=f'063 prueba [{uid[:8]}]: {accion}')
            self.registrar({'unidad': uid, 'decision': 'rectificado',
                            'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
            reid = '' if regla else 'par re-identificado; '
            decidida = (FULDO, 'Company:ficcionbar', 'CONTROLS') if accion == 'retype' \
                else ('Company:ficcionbar', FULDO, 'OWNS')
            for copia, carga, codigo, que in (((FULDO, 'Company:ficcionbar', 'OWNS'), {}, 1, 'retirada'),
                                              (decidida, self.OTRA_NOTA, 1, 'retirada' if reid else 'rectificación'),
                                              (decidida, self.NOTA, 0, None)):
                with self.subTest(caso=caso, accion=accion, tipo=copia[2], carga=sorted(carga)):
                    self.exportar_aristas((*copia, {**props, **carga}), nodos=(inicio, fin) if regla else ())
                    mat = f'MATERIALIZACION: {que} decidida sin materializar ({RC.OTRO_PAR}{uid[:8]}, {pack})'
                    reg = f'arista «rectificada» viva sin corregir ({reid}{RC.OTRO_PAR}1 copia; tolerancia 0)'
                    self.los_dos_dicen(codigo, que and mat, codigo and reg)

    def test_a_copy_that_is_another_edge_of_the_census_is_not_attributed_in_both_checks(self):
        # AC89 (corrección): la firma documental no es identidad. Con el extremo re-identificado y sin regla, la
        # búsqueda por firma le atribuía a la arista las copias de otras aristas del mismo pasaje: la de otra persona
        # (su forma censal) y la que la otra unidad ya retipó (su forma resultante). No son suyas: con su copia correcta
        # en el par actual, los dos checks pasan. Sin el retype de la otra unidad, su copia del tipo antiguo no la
        # explica ninguna arista del censo y sí se le atribuye.
        uid, pack, otra, retipada = 'd6' * 32, 'E-c0de00000001-40', 'd7' * 32, 'd8' * 32
        tercera = 'Person:tercera-persona-ficticia'
        self.nueva_arista(uid, self.ANTES, pack)
        self.nueva_arista(otra, self.del_pasaje(self.OTRO, 'Company:ficcionbar'), 'E-c0de00000001-41')
        self.nueva_arista(retipada, self.del_pasaje(tercera, 'Company:ficcionbar', rtype='PAID'), 'E-c0de00000001-42')
        props = dict(self.ANTES['props'])
        ajenas = ((self.OTRO, 'Company:ficcionbar', 'OWNS', props), (tercera, 'Company:ficcionbar', 'OWNS', props))
        for accion, campos, suya in (
                ('retype', {'new_type': 'CONTROLS'}, (FULDO, 'Company:ficcionbar', 'CONTROLS', props)),
                ('invert', {}, ('Company:ficcionbar', FULDO, 'OWNS', props))):
            self.decidir(pack, uid, accion, **campos)
            self.registrar({'unidad': uid, 'decision': 'rectificado',
                            'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
            for caso, retipo, codigo in (('la censal de una y la que le deja a otra su retype', True, 0),
                                         ('la otra sin su retype: su copia OWNS ya no es de nadie más', False, 1)):
                with self.subTest(accion=accion, caso=caso):
                    decision = self.root / 'esc/decisions/E-c0de00000001-42.json'
                    if retipo:
                        self.decidir('E-c0de00000001-42', retipada, 'retype', new_type='OWNS')
                    else:
                        decision.unlink(missing_ok=True)
                    self.exportar_aristas(suya, *ajenas)
                    self.los_dos_dicen(codigo, codigo and f'MATERIALIZACION: retirada decidida sin materializar (par '
                                                          f'distinto del de la regla; {uid[:8]}, {pack})',
                                       codigo and 'arista «rectificada» viva sin corregir (par re-identificado; par '
                                                  'distinto del de la regla; 1 copia; tolerancia 0)')
                    if not codigo:
                        self.assertNotIn(f'({uid[:8]}, {pack})', self.materializacion()[1])

    def test_a_correct_copy_outside_the_pair_of_the_rule_is_accredited_and_said_so(self):
        # AC89: con el extremo re-identificado y la regla etiquetada en un tercer par sin copias, la copia correcta en
        # otro par acredita el retype, pero el aviso lo dice; antes, «sin el estado decidido en el export». Sin su
        # carga no acredita y está sin materializar en los dos checks.
        uid, pack = 'd7' * 32, 'E-c0de00000001-43'
        self.nueva_arista(uid, self.ANTES, pack)
        self.decidir(pack, uid, 'retype', new_type='CONTROLS', set=self.NOTA)
        self.regla('fix', source_id='Person:fuldo-tercero-ficticio', target_id='Company:ficcionbar', type='OWNS',
                   corrected_type='CONTROLS', set=self.NOTA, doc='informe-uco.pdf',
                   quote_contains='participación ficticia', reason=f'063 prueba [{uid[:8]}]: retipo en un tercer par')
        self.registrar({'unidad': uid, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
        props = dict(self.ANTES['props'])
        acreditada = f'acreditada por firma (par distinto del de la regla; relations.yaml:fix[1], {uid[:8]})'
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'CONTROLS', {**props, **self.NOTA}),
                              nodos=('Person:fuldo-tercero-ficticio',))
        self.los_dos_dicen(0)
        self.assertIn(f'materializacion: par distinto del censo: {acreditada}', self.materializacion()[1])
        self.assertEqual(self.por_firma(uid, self.ANTES), 'otro par')
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'CONTROLS', {**props, **self.OTRA_NOTA}),
                              nodos=('Person:fuldo-tercero-ficticio',))
        self.los_dos_dicen(1, f'MATERIALIZACION: retirada decidida sin materializar (par distinto del de la regla; '
                              f'{uid[:8]}, {pack})',
                           'arista «rectificada» viva sin corregir (par re-identificado; par distinto del de la regla; '
                           '1 copia; tolerancia 0)')
        self.assertNotIn(acreditada, self.materializacion()[1])
        self.assertIsNone(self.por_firma(uid, self.ANTES))

    def test_a_props_correction_completing_a_withdrawal_fails_like_the_registry_when_unpublished(self):
        # AC90: la «rectificado» de un set_prop que completa una «suprimido» afirma la arista publicada. Sin ninguna
        # copia (o con una que no está publicada como la cuenta el registro: desde el documento que cita la fila),
        # `materializacion` lo da como error, igual que el «ausente» del registro; antes era informativo. Sin la
        # «suprimido» que completa, la «rectificado» sin ninguna copia también es un error en los dos (AC100); sin fila,
        # sigue informativo.
        d = self.retirada('set_prop', prop='note', value=self.NOTA['note'])
        A.apply(self.root, self.repo, [d], self.tag, self.upd)
        self.cerrada_sin_aplicar()
        props = self.censo()['props']
        suprimida = {'fecha': '2026-09-24', 'dato': f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista retirada'}
        rectificada = {'fecha': '2026-10-07', 'decision': 'rectificado',
                       'dato': f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista corregida'}
        error = (f'MATERIALIZACION: corrección decidida sin copias; la fila afirma que sigue publicada ({U_EDGE2[:8]}, '
                 f'{P3})')
        ausente = (error, 'arista «rectificada» ausente del export')
        sin_arista = (f'MATERIALIZACION: rectificación decidida sin arista corregida ({U_EDGE2[:8]}, {P3})',
                      'rectificación decidida sin arista corregida')
        for caso, filas, aristas, codigo, lineas in (
                ('completa una «suprimido», sin copias', (suprimida, rectificada), (), 1, ausente),
                ('completa una «suprimido», la copia desde otro documento que el que cita',
                 (suprimida, dict(rectificada, fuente='otro-informe-ficticio.pdf p.2')),
                 ((FULDO, 'Company:ficcionbar', 'PAID', {**props, **self.NOTA}),), 1, ausente),
                ('sin «suprimido», sin copias', (rectificada,), (), 1, sin_arista),
                ('sin fila, sin copias', (), (), 0, (None, None))):
            with self.subTest(caso=caso):
                self.registrar(*filas)
                self.exportar_aristas(*aristas)
                self.los_dos_dicen(codigo, *lineas)
                code, texto = self.materializacion()
                self.assertEqual(error in texto, lineas is ausente, texto)
                self.assertEqual(f'corrección decidida sin copias ({U_EDGE2[:8]}' in texto, not filas, texto)

    # --- AC91–AC93 (segunda ampliación de la enmienda 18) -----------------------------------------------------------

    ABIERTA, TERCERA = 'Person:otra-persona-de-antes-ficticia', 'Person:tercera-persona-ficticia'

    def test_a_copy_that_can_also_be_another_reidentified_edge_is_ambiguous_in_both_checks(self):
        # AC91: en su par del censo no queda ninguna copia y la búsqueda libre halla una correcta en otro par; otra
        # arista del censo del mismo pasaje tiene un extremo fuera del export y comparte con la copia el que le queda,
        # así que la copia puede ser cualquiera de las dos. Se le atribuía a la primera y los dos checks pasaban; ahora
        # no se atribuye a ninguna y los dos la cuentan, en un retype y en un set_prop, sin sumarle la falta de la
        # arista corregida (AC92). Control: con los dos extremos de la otra en el export, rige el criterio (ii) de AC89
        # y la copia es de la primera. La otra llega a CONTROLS por una regla sin etiqueta sobre su propio par: la
        # etiquetada de la primera es suya y no le da un par a la otra (AC103).
        for n, (accion, campos, fase, tipo) in enumerate((
                ('retype', {'new_type': 'CONTROLS', 'set': self.NOTA}, {'corrected_type': 'CONTROLS'}, 'CONTROLS'),
                ('set_prop', {'prop': 'note', 'value': self.NOTA['note']}, {}, 'OWNS'))):
            uid, otra, pack = f'a{n + 1}' * 32, f'b{n + 1}' * 32, f'E-c0de00000001-6{n}'
            for vieja in (self.root / 'esc/decisions').glob('*.json'):
                vieja.unlink()
            (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
            props = {**self.ANTES['props'], 'source_quote': f'participación ficticia en la sociedad, pasaje {n}'}
            self.nueva_arista(uid, {'start': FULDO, 'end': 'Company:ficcionbar', 'type': 'OWNS', 'props': props}, pack)
            self.nueva_arista(otra, {'start': self.ABIERTA, 'end': 'Company:ficcionbar', 'type': 'OWNS',
                                     'props': props}, f'{pack}-otra')
            self.decidir(pack, uid, accion, **campos)
            self.regla('fix', **self.SOCIEDAD, **fase, set=self.NOTA, reason=f'063 prueba [{uid[:8]}]: {accion}')
            self.regla('fix', **{**self.SOCIEDAD, 'source_id': self.ABIERTA}, corrected_type='CONTROLS',
                       reason='regla previa, sin etiqueta')
            self.registrar({'unidad': uid, 'decision': 'rectificado',
                            'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
            copia = (self.TERCERA, 'Company:ficcionbar', tipo, {**props, **self.NOTA})
            for caso, nodos, codigo in (('la otra, con un extremo fuera del export', (), 1),
                                        ('la otra, con los dos extremos en el export', (self.ABIERTA,), 0)):
                with self.subTest(accion=accion, caso=caso):
                    self.exportar_aristas(copia, nodos=nodos)
                    unidades = f'{uid[:8]}, {otra[:8]}'
                    self.los_dos_dicen(codigo, codigo and f'MATERIALIZACION: copia ambigua entre unidades ({unidades}; '
                                                          f'{pack})',
                                       codigo and f'copia ambigua entre unidades ({unidades})')
                    texto = self.materializacion()[1] + self.registro_check()[1]
                    for otro_motivo in ('rectificación decidida sin arista corregida', 'viva sin corregir',
                                        'decidida sin materializar', 'corrección decidida sin copias'):
                        self.assertNotIn(otro_motivo, texto)

    def test_an_ambiguous_copy_does_not_accredit_a_reidentified_retype_by_its_signature(self):
        # AC91 en `_por_firma`: con el extremo re-identificado y la regla etiquetada en un tercer par sin copias, la
        # copia correcta en otro par acreditaba el retype por firma (AC89) aunque otra arista del censo del mismo
        # pasaje, con un extremo fuera del export, también pueda ser ella (llega a CONTROLS por una regla sin etiqueta
        # sobre su par: la etiquetada de la primera no es suya, AC103). No se atribuye: `_por_firma` no la acredita y
        # los dos checks la cuentan. Control: con los dos extremos de la otra en el export, acreditada por firma (par
        # distinto del de la regla) y nada en los dos checks.
        uid, otra, pack = 'a3' * 32, 'b3' * 32, 'E-c0de00000001-62'
        self.nueva_arista(uid, self.ANTES, pack)
        self.nueva_arista(otra, self.del_pasaje(self.ABIERTA, 'Company:ficcionbar'), 'E-c0de00000001-63')
        self.decidir(pack, uid, 'retype', new_type='CONTROLS', set=self.NOTA)
        self.regla('fix', source_id='Person:fuldo-tercero-ficticio', target_id='Company:ficcionbar', type='OWNS',
                   corrected_type='CONTROLS', set=self.NOTA, doc='informe-uco.pdf',
                   quote_contains='participación ficticia', reason=f'063 prueba [{uid[:8]}]: retipo en un tercer par')
        self.regla('fix', source_id=self.ABIERTA, target_id='Company:ficcionbar', type='OWNS',
                   corrected_type='CONTROLS', doc='informe-uco.pdf', quote_contains='participación ficticia',
                   reason='regla previa, sin etiqueta')
        self.registrar({'unidad': uid, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
        acreditada = f'acreditada por firma (par distinto del de la regla; relations.yaml:fix[1], {uid[:8]})'
        copia = (FULDO, 'Company:ficcionbar', 'CONTROLS', {**self.ANTES['props'], **self.NOTA})
        self.exportar_aristas(copia, nodos=('Person:fuldo-tercero-ficticio',))
        self.los_dos_dicen(1, f'MATERIALIZACION: copia ambigua entre unidades ({uid[:8]}, {otra[:8]}; {pack})',
                           f'copia ambigua entre unidades ({uid[:8]}, {otra[:8]})')
        self.assertNotIn(acreditada, self.materializacion()[1])
        self.assertIsNone(self.por_firma(uid, self.ANTES))
        self.exportar_aristas(copia, nodos=('Person:fuldo-tercero-ficticio', self.ABIERTA))
        self.los_dos_dicen(0)
        self.assertIn(f'materializacion: par distinto del censo: {acreditada}', self.materializacion()[1])
        self.assertEqual(self.por_firma(uid, self.ANTES), 'otro par')

    def test_a_rectified_retype_or_invert_needs_its_corrected_edge_in_both_checks(self):
        # AC92: la «rectificado» de un retype o un invert afirma la arista corregida. Con su regla y sin ninguna copia
        # en el estado resultante, `materializacion` lo daba como informativo («retype sin copia») y el registro no
        # exigía nada; los dos dicen ahora «rectificación decidida sin arista corregida». Con la copia aún en el estado
        # anterior manda la viva sin corregir (una categoría por fila); con la correcta, nada; sin fila, sigue
        # informativo; la que completa una «suprimido» conserva su motivo (AC69).
        uid, pack = 'a4' * 32, 'E-c0de00000001-64'
        valor = self.del_pasaje(FULDO, 'Company:ficcionbar')
        self.nueva_arista(uid, valor, pack)
        props = dict(valor['props'])
        corregida = {'unidad': uid, 'decision': 'rectificado', 'fecha': '2026-10-08',
                     'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'}
        suprimida = {'unidad': uid, 'fecha': '2026-09-24',
                     'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista retirada'}
        for accion, campos, fase, correcta, informativo in (
                ('retype', {'new_type': 'CONTROLS'}, {'corrected_type': 'CONTROLS'},
                 (FULDO, 'Company:ficcionbar', 'CONTROLS', props), 'retype sin copia bajo el tipo decidido'),
                ('invert', {}, {'swap_direction': True}, ('Company:ficcionbar', FULDO, 'OWNS', props),
                 'invert sin copia en el sentido decidido')):
            (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
            self.decidir(pack, uid, accion, **campos)
            self.regla('fix', **self.SOCIEDAD, **fase, reason=f'063 prueba [{uid[:8]}]: {accion}')
            for caso, filas, aristas, codigo, mat, reg in (
                    ('sin ninguna copia', [corregida], [], 1,
                     f'MATERIALIZACION: rectificación decidida sin arista corregida ({uid[:8]}, {pack})',
                     'rectificación decidida sin arista corregida'),
                    ('con la copia en el estado anterior', [corregida], [(FULDO, 'Company:ficcionbar', 'OWNS', props)],
                     1, f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})',
                     'arista «rectificada» viva sin corregir (1 copia; tolerancia 0)'),
                    ('con la correcta', [corregida], [correcta], 0, None, None),
                    ('sin fila', [], [], 0, f'materializacion: {informativo} ({uid[:8]}, {pack})', None),
                    ('completa una «suprimido»', [suprimida, corregida], [], 1,
                     f'MATERIALIZACION: rectificación decidida sin materializar ({uid[:8]}, {pack})',
                     'arista «rectificada» ausente del export')):
                with self.subTest(accion=accion, caso=caso):
                    self.registrar(*filas)
                    self.exportar_aristas(*aristas, nodos=('Company:ficcionbar',))
                    self.los_dos_dicen(codigo, mat, reg)
                    texto = self.materializacion()[1] + self.registro_check()[1]
                    self.assertEqual('rectificación decidida sin arista corregida' in texto,
                                     caso == 'sin ninguna copia', texto)
                    self.assertEqual(informativo in texto, caso == 'sin fila', texto)

    def test_a_withdrawn_row_on_an_edge_its_decision_keeps_contradicts_it_in_both_checks(self):
        # AC93: una fila «suprimido» vigente sobre una arista cuya operación vigente la conserva (retype, invert,
        # set_prop o keep, también uno anterior a la fila) no la veía ningún check: el registro la daba por retirada
        # (sin copia viva) y `materializacion` la acreditaba por la fila. Los dos la cuentan; `materializacion` nombra
        # la regla ajena que alcanza su arista censal con el predicado del loader (el deny de su gemela, con la etiqueta
        # de esta; uno sin etiqueta, solo por su posición) o dice que ninguna la explica: la regla propia no cuenta. Un
        # set_status o un deny con su «suprimido», no.
        retipada, gemela, conservada, corregida, estado = ('c1' * 32, 'c2' * 32, 'c3' * 32, 'c4' * 32, 'c5' * 32)
        otra_sociedad, tercera_sociedad = 'Company:otra-sociedad-ficticia', 'Company:tercera-sociedad-ficticia'
        aristas = {retipada: self.del_pasaje(FULDO, 'Company:ficcionbar'),
                   gemela: self.del_pasaje(FULDO, 'Company:ficcionbar'),
                   conservada: edge_value(end=otra_sociedad, rtype='ASSOCIATED_WITH',
                                          source_quote='reunión ficticia en la sede'),
                   corregida: edge_value(end=tercera_sociedad, rtype='PAID', source_quote='pago ficticio a la tercera'),
                   estado: edge_value(end='Crime:prevaricacion-ficticia', source_quote='acusación ficticia')}
        for n, (uid, valor) in enumerate(aristas.items()):
            self.nueva_arista(uid, valor, f'E-c0de00000001-7{n}')
        self.decidir('E-c0de00000001-70', retipada, 'retype', new_type='ASSOCIATED_WITH')
        self.decidir('E-c0de00000001-71', gemela, 'deny')
        self.decidir('E-c0de00000001-72', conservada, 'keep', cuando='2026-09-25T10:00:00+00:00')
        self.decidir('E-c0de00000001-73', corregida, 'set_prop', prop='note', value=self.NOTA['note'])
        self.decidir('E-c0de00000001-74', estado, 'set_status', status='investigado')
        self.regla('deny', **self.SOCIEDAD, reason=f'063 prueba [{gemela[:8]}]: duplicada')
        self.regla('deny', source_id=FULDO, target_id=otra_sociedad, type='ASSOCIATED_WITH', doc='informe-uco.pdf',
                   reason='063 prueba: retirada por documento, sin etiqueta')
        self.regla('fix', source_id=FULDO, target_id=tercera_sociedad, type='PAID', doc='informe-uco.pdf',
                   quote_contains='pago ficticio', set=self.NOTA, reason=f'063 prueba [{corregida[:8]}]: nota')
        self.registrar(*({'unidad': uid, 'fecha': '2026-10-07', 'dato': 'arista retirada'} for uid in aristas))
        self.exportar_aristas(nodos=('Company:ficcionbar', otra_sociedad, tercera_sociedad,
                                     'Crime:prevaricacion-ficticia'))
        lineas = {retipada: ' por regla ajena (relations.yaml:deny[1] [c2c2c2c2]; ',
                  conservada: ' por regla ajena (relations.yaml:deny[2]; ',
                  corregida: ', sin regla que la explique ('}
        filas = dict(zip(aristas, (f'R-2026-10-01-000{n}' for n in range(1, 6))))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        for uid, porque in lineas.items():
            self.assertIn(f'MATERIALIZACION: conservación decidida, arista retirada{porque}{uid[:8]}, '
                          f'E-c0de00000001-7{list(aristas).index(uid)})', texto)
        self.assertEqual(texto.count('conservación decidida'), 3, texto)
        code, texto = self.registro_check()
        self.assertEqual(code, 1, texto)
        for uid, accion in ((retipada, 'retype'), (conservada, 'keep'), (corregida, 'set_prop')):
            self.assertIn(f'REGISTRO {filas[uid]}: fila contradice la decisión (suprimido; decisión: {accion})', texto)
        self.assertEqual(texto.count('fila contradice la decisión'), 3, texto)

    def test_the_new_diagnostics_carry_no_identity_or_value(self):
        # AC87 en los motivos de AC91–AC93: con centinelas en los extremos de las aristas del censo, ni
        # `materializacion` ni el registro los imprimen, ni en su informe de unittest; nombran unidades y packs por su
        # hash y las reglas por su posición.
        ambigua, otra, sin_corregida, contradice = 'd1' * 32, 'd2' * 32, 'd3' * 32, 'd4' * 32
        pasaje = {**self.ANTES['props'], 'source_quote': 'participación ficticia, pasaje centinela'}
        self.nueva_arista(ambigua, {'start': self.ORIGEN, 'end': self.DESTINO, 'type': 'OWNS', 'props': pasaje},
                          'E-ce1e00000000-91')
        self.nueva_arista(otra, {'start': self.ABIERTA, 'end': self.DESTINO, 'type': 'OWNS', 'props': pasaje},
                          'E-ce1e00000000-92')
        self.nueva_arista(sin_corregida, {'start': self.ORIGEN, 'end': self.DESTINO, 'type': 'CONTROLS',
                                          'props': {**pasaje, 'source_quote': 'control ficticio, pasaje centinela'}},
                          'E-ce1e00000000-93')
        self.nueva_arista(contradice, {'start': self.ORIGEN, 'end': self.DESTINO, 'type': 'PAID',
                                       'props': {**pasaje, 'source_quote': 'pago ficticio, pasaje centinela'}},
                          'E-ce1e00000000-94')
        self.decidir('E-ce1e00000000-91', ambigua, 'set_prop', prop='note', value=self.NOTA['note'])
        self.decidir('E-ce1e00000000-93', sin_corregida, 'retype', new_type='SUBSIDIARY_OF')
        self.decidir('E-ce1e00000000-94', contradice, 'retype', new_type='ASSOCIATED_WITH')
        par = {'source_id': self.ORIGEN, 'target_id': self.DESTINO, 'doc': 'informe-uco.pdf'}
        self.regla('fix', **par, type='OWNS', quote_contains='pasaje centinela', set=self.NOTA,
                   reason=f'063 prueba [{ambigua[:8]}]: nota')
        self.regla('fix', **par, type='CONTROLS', quote_contains='control ficticio', corrected_type='SUBSIDIARY_OF',
                   reason=f'063 prueba [{sin_corregida[:8]}]: retipo')
        self.regla('deny', **par, type='PAID', reason='063 prueba: retirada sin etiqueta')
        self.registrar({'unidad': ambigua, 'decision': 'rectificado', 'dato': 'arista corregida'},
                       {'unidad': sin_corregida, 'decision': 'rectificado', 'dato': 'arista corregida'},
                       {'unidad': contradice, 'dato': 'arista retirada'})
        self.exportar_aristas((self.TERCERA, self.DESTINO, 'OWNS', {**pasaje, **self.NOTA}), nodos=(self.ORIGEN,))
        argv = ['--root', str(self.root), '--repo', str(self.repo)]
        todo = self.sin_centinelas(lambda: A.main([*argv, 'materializacion', '--snapshot', str(self.snap)]))
        todo += self.sin_centinelas(lambda: RC.main(['--registro', str(self.repo / A.REG_PATH), '--snapshot',
                                                     str(self.snap), '--root', str(self.root), '--overlay',
                                                     str(self.repo)]))
        for linea in (f'MATERIALIZACION: copia ambigua entre unidades ({ambigua[:8]}, {otra[:8]}; E-ce1e00000000-91)',
                      f'MATERIALIZACION: rectificación decidida sin arista corregida ({sin_corregida[:8]}, '
                      'E-ce1e00000000-93)',
                      'MATERIALIZACION: conservación decidida, arista retirada por regla ajena '
                      f'(relations.yaml:deny[1]; {contradice[:8]}, E-ce1e00000000-94)',
                      f'REGISTRO R-2026-10-01-0001: copia ambigua entre unidades ({ambigua[:8]}, {otra[:8]})',
                      'REGISTRO R-2026-10-01-0002: rectificación decidida sin arista corregida',
                      'REGISTRO R-2026-10-01-0003: fila contradice la decisión (suprimido; decisión: retype)'):
            self.assertIn(linea, todo)

    # --- AC284–AC287 (enmienda 44: la cita que reescribe una migración del Path A) --------------------------------

    GUARDADA = 'la sociedad ficticia recibió un pago del consejero'
    MIGRADA = GUARDADA + ' tras la reunión ficticia del consejo de administración'

    def migracion_de_cita(self, uid, antes, despues):
        """`esc/migraciones_citas.yaml` con la declaración de `uid`: las huellas de las citas de su firma (AC284)."""
        def firma(cita):
            return hashlib.sha256(RC._cita(cita).encode('utf-8')).hexdigest()
        (self.root / 'esc/migraciones_citas.yaml').write_text(yaml.safe_dump(
            {uid: {'documento': 'informe-uco.pdf', 'antes': firma(antes), 'despues': firma(despues)}}),
            encoding='utf-8')

    def test_a_declared_quote_migration_identifies_its_unit_alike_in_both_checks(self):
        # AC285–AC287: un set_prop «rectificado» cuya copia corregida lleva la cita que escribió una migración. Sin
        # declaración, «sin arista corregida» en los dos checks; con la de su unidad, ninguno dice nada; con una que no
        # vale, los dos dan el mismo motivo y vuelve el «sin arista corregida»; la gemela de su firma (otra página), con
        # su propia «rectificado», no se atribuye la copia en ninguno de los dos.
        uid, gemela, pack, pack_gemela = '7e' * 32, '7f' * 32, 'E-c0de00000001-90', 'E-c0de00000001-91'
        valor = edge_value(end='Company:ficcionbar', rtype='RECEIVED', source_quote=self.GUARDADA)
        self.nueva_arista(uid, valor, pack)
        self.decidir(pack, uid, 'set_prop', prop='note', value=self.NOTA['note'])
        corregida = {'decision': 'rectificado', 'dato': f'({FULDO}) -[RECEIVED]-> (Company:ficcionbar): arista corregida'}
        self.registrar(dict(corregida, unidad=uid))
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'RECEIVED',
                               {**valor['props'], 'source_quote': self.MIGRADA, **self.NOTA}))
        self.los_dos_dicen(1, f'MATERIALIZACION: rectificación decidida sin arista corregida ({uid[:8]}, {pack})',
                           'REGISTRO R-2026-10-01-0001: rectificación decidida sin arista corregida')
        self.migracion_de_cita(uid, self.GUARDADA, self.MIGRADA)
        self.los_dos_dicen(0)
        self.migracion_de_cita(uid, 'otra cita ficticia', self.MIGRADA)
        motivo = (f'migración de cita declarada no válida: unidad {uid[:8]}: la huella de antes no es la de la cita de '
                  'su arista en el censo')
        self.los_dos_dicen(1, f'MATERIALIZACION: {motivo} (esc/migraciones_citas.yaml)',
                           f'REGISTRO esc/migraciones_citas.yaml: {motivo}')
        self.los_dos_dicen(1, f'MATERIALIZACION: rectificación decidida sin arista corregida ({uid[:8]}, {pack})',
                           'REGISTRO R-2026-10-01-0001: rectificación decidida sin arista corregida')
        self.migracion_de_cita(uid, self.GUARDADA, self.MIGRADA)
        self.nueva_arista(gemela, dict(valor, props={**valor['props'], 'source_page': 77}), pack_gemela)
        self.decidir(pack_gemela, gemela, 'set_prop', prop='note', value=self.NOTA['note'])
        self.registrar(dict(corregida, unidad=uid), dict(corregida, unidad=gemela))
        self.los_dos_dicen(1, f'MATERIALIZACION: rectificación decidida sin arista corregida ({gemela[:8]}, '
                              f'{pack_gemela})',
                           'REGISTRO R-2026-10-01-0002: rectificación decidida sin arista corregida')
        for code, texto in (self.materializacion(), self.registro_check()):
            self.assertNotIn(f'({uid[:8]}, {pack})', texto)
            self.assertNotIn('R-2026-10-01-0001', texto)
            self.assertEqual(texto.count('rectificación decidida sin arista corregida'), 1, texto)

    def test_a_declaration_that_does_not_hold_is_by_itself_a_problem_in_both_checks(self):
        # AC286 (superviviente M20 de la enmienda 44): con la copia aún con la cita guardada y su nota, nada más falla;
        # una declaración que no vale es, ella sola, un problema: exit 1 en los dos, con su línea y ninguna otra.
        uid, pack = '7c' * 32, 'E-c0de00000001-93'
        valor = edge_value(end='Company:ficcionbar', rtype='RECEIVED', source_quote=self.GUARDADA)
        self.nueva_arista(uid, valor, pack)
        self.decidir(pack, uid, 'set_prop', prop='note', value=self.NOTA['note'])
        self.registrar({'unidad': uid, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[RECEIVED]-> (Company:ficcionbar): arista corregida'})
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'RECEIVED', {**valor['props'], **self.NOTA}))
        self.los_dos_dicen(0)
        self.migracion_de_cita(uid, 'otra cita ficticia', self.MIGRADA)
        motivo = (f'migración de cita declarada no válida: unidad {uid[:8]}: la huella de antes no es la de la cita de '
                  'su arista en el censo')
        self.los_dos_dicen(1, f'MATERIALIZACION: {motivo} (esc/migraciones_citas.yaml)',
                           f'REGISTRO esc/migraciones_citas.yaml: {motivo}')
        for (_, texto), prefijo in ((self.materializacion(), 'MATERIALIZACION: '), (self.registro_check(), 'REGISTRO ')):
            self.assertEqual(sum(l.startswith(prefijo) for l in texto.splitlines()), 1, texto)
        # AC293 (e44 bis): el resumen de `materializacion` las cuenta, como su exit.
        self.assertIn('migraciones de cita declaradas no válidas: 1;', self.materializacion()[1])
        self.migracion_de_cita(uid, self.GUARDADA, self.MIGRADA)
        self.assertIn('migraciones de cita declaradas no válidas: 0;', self.materializacion()[1])

    def test_a_deny_in_the_batch_voids_the_declaration_in_the_same_pass(self):
        # AC292 (e44 bis): `_contraste_reglas` contrasta la declaración con las decisiones EFECTIVAS. Con un lote que pone
        # un deny a la unidad declarada (re-identificada), la declaración cae en esa misma pasada: la copia migrada no es
        # suya y el deny etiquetado se acredita por la firma del censo. Si la declaración siguiera valiendo, la copia
        # migrada sería suya y el deny quedaría pendiente.
        uid, pack = '7b' * 32, 'E-c0de00000001-94'
        guardada, migrada = 'pago ficticio a la sociedad', 'pago ficticio a la sociedad en la sede ficticia'
        self.nueva_arista(uid, self.REIDENTIFICADA, pack)
        self.decidir(pack, uid, 'set_prop', prop='note', value='nota decidida')
        self.regla('deny', source_id=FULDO, target_id='Company:ficcionbar', type='PAID', doc='informe-uco.pdf',
                   quote_contains='pago ficticio', reason=f'063 prueba [{uid[:8]}]: retirada')
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'PAID',
                               {**self.REIDENTIFICADA['props'], 'source_quote': migrada}))
        self.migracion_de_cita(uid, guardada, migrada)
        lote = [decision(pack_id=pack, units=[unit(uid, verdict='corregido', label='arista')],
                         edges=[edge_op(uid, 'deny')], resolution='humano-2026-10-05', _file=f'{pack}.lote.json')]
        regla = f'(relations.yaml:deny[1], {uid[:8]})'
        for caso, decisiones, acreditada in (('sin lote', (), False), ('con el deny en el lote', lote, True)):
            with self.subTest(caso=caso):
                errores, avisos, _ = A._contraste_reglas(self.root, self.repo, decisiones,
                                                         vivo=RC.export_vivo(self.snap))
                texto = '\n'.join(errores + avisos)
                self.assertEqual(f'par distinto del censo: acreditada por firma {regla}' in texto, acreditada, texto)
                self.assertNotIn(f'pendiente, par re-identificado {regla}', texto)
                self.assertEqual(f'regla sin decisión vigente {regla}' in texto, not acreditada, texto)

    def test_a_declared_quote_migration_keeps_a_reidentified_rule_pending(self):
        # AC287 (`_pendiente`): con el extremo del censo re-identificado, la regla etiquetada alcanza en su propio par
        # la copia con la cita migrada y sin la nota todavía: pendiente del Path A (aviso), con su corrección sin
        # materializar. Sin la declaración no es suya: la regla apunta a nada, error.
        uid, pack = '7d' * 32, 'E-c0de00000001-92'
        guardada, migrada = 'pago ficticio a la sociedad', 'pago ficticio a la sociedad en la sede ficticia'
        self.nueva_arista(uid, self.REIDENTIFICADA, pack)
        self.decidir(pack, uid, 'set_prop', prop='note', value='nota decidida')
        self.regla('fix', source_id=FULDO, target_id='Company:ficcionbar', type='PAID', set={'note': 'nota decidida'},
                   doc='informe-uco.pdf', quote_contains='pago ficticio', reason=f'063 prueba [{uid[:8]}]: nota')
        regla = f'(relations.yaml:fix[1], {uid[:8]})'
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'PAID',
                               {**self.REIDENTIFICADA['props'], 'source_quote': migrada}))
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'MATERIALIZACION: par distinto del censo, sin el estado decidido en el export {regla}', texto)
        self.assertNotIn('pendiente, par re-identificado', texto)
        self.migracion_de_cita(uid, guardada, migrada)
        code, texto = self.materializacion()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'materializacion: pendiente, par re-identificado {regla}', texto)
        self.assertIn(f'MATERIALIZACION: corrección decidida sin materializar ({uid[:8]}, {pack})', texto)
        self.assertNotIn('sin el estado decidido en el export', texto)

    # --- AC94–AC101 (enmienda 19, 15ª pasada de Codex) ------------------------------------------------------------

    ESCINDIDA = 'Company:ficcionbar-escindida'
    ESCISION = ('splits:\n  - canonical: {type: Company, name: ficcionbar}\n'
                '    namesake: {type: Company, name: ficcionbar escindida}\n    by_doc: informe-uco.pdf\n')

    def test_a_correct_copy_does_not_hide_a_wrong_one_in_a_pair_its_split_declares(self):
        # Codex, 15ª pasada (H1, AC94): con la copia correcta en su par, la búsqueda libre se apagaba y una copia
        # incorrecta del mismo hecho en otro par (una escisión que conserva los nodos originales) pasaba en los dos
        # checks. Ahora busca siempre, y con copia en sus pares atribuye la de otro par solo si el overlay declara la
        # re-identificación (aquí, la escisión): sin declarar es otro hecho del pasaje, un aviso en los dos. Sin la
        # correcta, la incorrecta es suya aunque no se declare nada (AC89); sin la incorrecta, nada. Retype, invert y
        # set_prop.
        for n, (accion, campos, fase, correcta, incorrecta, que) in enumerate((
                ('retype', {'new_type': 'CONTROLS'}, {'corrected_type': 'CONTROLS'},
                 (FULDO, 'Company:ficcionbar', 'CONTROLS', {}), (FULDO, self.ESCINDIDA, 'OWNS', {}), 'retirada'),
                ('invert', {}, {'swap_direction': True}, ('Company:ficcionbar', FULDO, 'OWNS', {}),
                 (FULDO, self.ESCINDIDA, 'OWNS', {}), 'retirada'),
                ('set_prop', {'prop': 'note', 'value': self.NOTA['note']}, {'set': self.NOTA},
                 (FULDO, 'Company:ficcionbar', 'OWNS', self.NOTA), (FULDO, self.ESCINDIDA, 'OWNS', {}), 'corrección'))):
            uid, pack = f'e{n + 1}' * 32, f'E-c0de00000001-8{n}'
            for vieja in (self.root / 'esc/decisions').glob('*.json'):
                vieja.unlink()
            props = {**self.ANTES['props'], 'source_quote': f'participación ficticia en la sociedad, pasaje {n}'}
            self.nueva_arista(uid, {'start': FULDO, 'end': 'Company:ficcionbar', 'type': 'OWNS', 'props': props}, pack)
            self.decidir(pack, uid, accion, **campos)
            self.registrar({'unidad': uid, 'decision': 'rectificado',
                            'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
            buena, mala = (*correcta[:3], {**props, **correcta[3]}), (*incorrecta[:3], {**props, **incorrecta[3]})
            viva = (f'MATERIALIZACION: {que} decidida sin materializar ({RC.OTRO_PAR}{uid[:8]}, {pack})',
                    f'arista «rectificada» viva sin corregir ({RC.OTRO_PAR}1 copia; tolerancia 0)')
            aviso = (f'materializacion: copia del mismo pasaje en otro par, hecho no atribuido ({uid[:8]}, {pack})',
                     f'registro-check: aviso R-2026-10-01-0001: copia del mismo pasaje en otro par, hecho no atribuido '
                     f'({uid[:8]})')
            for caso, escision, aristas, codigo, lineas in (
                    ('escisión declarada', True, (buena, mala), 1, viva),
                    ('sin declarar', False, (buena, mala), 0, aviso),
                    ('sin la correcta', False, (mala,), 1, viva),
                    ('sin la incorrecta', True, (buena,), 0, (None, None))):
                with self.subTest(accion=accion, caso=caso):
                    (self.repo / A.REL_PATH).write_text(
                        RELATIONS.replace('splits: []\n', self.ESCISION if escision else 'splits: []\n'),
                        encoding='utf-8')
                    self.regla('fix', **self.SOCIEDAD, **fase, reason=f'063 prueba [{uid[:8]}]: {accion}')
                    self.exportar_aristas(*aristas, nodos=('Company:ficcionbar',))
                    self.los_dos_dicen(codigo, *lineas)
                    texto = self.materializacion()[1] + self.registro_check()[1]
                    self.assertEqual('hecho no atribuido' in texto, caso == 'sin declarar', texto)

    def test_a_unit_id_without_the_unit_form_never_reaches_a_diagnostic(self):
        # Codex, 15ª pasada (H2, AC95): `validar_cargas` corre antes que el esquema y citaba los 8 primeros caracteres
        # de cualquier unit_id; con un valor en ese campo y una carga inválida, `check`, `record`, el registro y
        # `materializacion` salían con 2 y lo imprimían. Se nombra por el fichero y la posición de la operación; tampoco
        # sale en el informe de unittest.
        centinela = 'XCENTINX' + 'f' * 56
        self.write(P3, decision(pack_id=P3, units=[unit(U_EDGE2, verdict='corregido', label='arista')],
                                edges=[edge_op(centinela, 'set_prop', set={'amount': 'no es un número'})]))
        self.exportar_aristas()
        argv = ['--root', str(self.root), '--repo', str(self.repo)]
        # Y `privatizar`, que valida las decisiones como los demás mandos (AC139; residual de la 18ª pasada).
        mandos = (lambda: A.main([*argv, 'check']), lambda: A.main([*argv, 'record']),
                  lambda: RC.main(['--registro', str(self.repo / A.REG_PATH), '--snapshot', str(self.snap), '--root',
                                   str(self.root), '--overlay', str(self.repo)]),
                  lambda: A.main([*argv, 'materializacion', '--snapshot', str(self.snap)]),
                  lambda: A.main([*argv, '--snapshot', str(self.snap), 'privatizar', '--patch',
                                  str(self.base / 'privatizar-carga')]))
        self.registrar()
        for n, mando in enumerate(mandos):
            with self.subTest(mando=n):
                informe = io.StringIO()
                resultado = []

                class Mando(unittest.TestCase):
                    def test_mando(self):
                        salida = io.StringIO()
                        with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
                            try:
                                codigo = mando()
                            except SystemExit as exc:
                                codigo = exc.code
                        resultado.append((codigo, salida.getvalue()))

                unittest.TextTestRunner(stream=informe, verbosity=2).run(Mando('test_mando'))
                codigo, salida = resultado[0]
                self.assertEqual(codigo, 2, salida)
                self.assertIn('operación con carga inválida (E-c0de00000001-3.json, operación 1: ', salida)
                self.assertNotIn('XCENTINX', salida + informe.getvalue())

    def test_another_unit_never_tolerates_an_incomplete_load(self):
        # Codex, 15ª pasada (M1, AC96): dos OWNS del mismo par, documento y cita (páginas distintas), las dos retipadas
        # a CONTROLS y solo una con su nota. Con una copia corregida y otra con otra nota, el registro daba 0 porque la
        # otra unidad le prestaba tolerancia y `materializacion` 1. La tolerancia solo exime copias incompatibles: la
        # del estado decidido sin su carga es un problema en los dos. Con las dos corregidas, nada.
        con_nota, sin_ella, pack, otro = 'f4' * 32, 'f5' * 32, 'E-c0de00000001-90', 'E-c0de00000001-91'
        for uid, pagina, paquete in ((con_nota, 1, pack), (sin_ella, 2, otro)):
            self.nueva_arista(uid, {'start': FULDO, 'end': 'Company:ficcionbar', 'type': 'OWNS',
                                    'props': {**self.ANTES['props'], 'source_page': pagina}}, paquete)
        self.decidir(pack, con_nota, 'retype', new_type='CONTROLS', set=self.NOTA)
        self.decidir(otro, sin_ella, 'retype', new_type='CONTROLS')
        self.regla('fix', **self.SOCIEDAD, corrected_type='CONTROLS', set=self.NOTA,
                   reason=f'063 prueba [{con_nota[:8]}]: retipo con nota')
        self.registrar({'unidad': con_nota, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
        props = dict(self.ANTES['props'])
        for caso, notas, codigo in (('una con la nota y otra con otra', (self.NOTA, self.OTRA_NOTA), 1),
                                    ('las dos con la nota', (self.NOTA, self.NOTA), 0)):
            with self.subTest(caso=caso):
                self.exportar_aristas(*[(FULDO, 'Company:ficcionbar', 'CONTROLS', {**props, 'source_page': n, **nota})
                                        for n, nota in enumerate(notas, 1)])
                self.los_dos_dicen(codigo, codigo and f'MATERIALIZACION: rectificación decidida sin materializar '
                                                      f'({con_nota[:8]}, {pack})',
                                   codigo and 'arista «rectificada» viva sin corregir (1 copia; tolerancia 0)')

    def test_a_correction_found_by_the_free_search_is_published(self):
        # Codex, 15ª pasada (M2, AC97): set_prop sobre una arista re-identificada, sin regla que deduzca su par actual,
        # con su única copia corregida en él (desde el documento que cita la fila) e historia suprimido→rectificado. La
        # búsqueda libre la reconocía, pero `_sigue_publicada` solo miraba los pares deducidos: «ausente» en los dos. La
        # copia libre identificada sin ambigüedad cuenta; sin ella, sí falta.
        uid, pack = 'f6' * 32, 'E-c0de00000001-92'
        self.nueva_arista(uid, self.ANTES, pack)
        self.decidir(pack, uid, 'set_prop', prop='note', value=self.NOTA['note'])
        self.registrar({'unidad': uid, 'fecha': '2026-09-24',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista retirada'},
                       {'unidad': uid, 'fecha': '2026-10-07', 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
        copia = (FULDO, 'Company:ficcionbar', 'OWNS', {**self.ANTES['props'], **self.NOTA})
        for caso, aristas, codigo in (('con su copia corregida en el par actual', (copia,), 0), ('sin ella', (), 1)):
            with self.subTest(caso=caso):
                self.exportar_aristas(*aristas, nodos=('Company:ficcionbar',))
                self.los_dos_dicen(codigo, codigo and f'MATERIALIZACION: corrección decidida sin copias; la fila '
                                                      f'afirma que sigue publicada ({uid[:8]}, {pack})',
                                   codigo and 'arista «rectificada» ausente del export')

    def test_a_reversed_copy_in_its_pair_does_not_hide_its_correction_in_another(self):
        # AC94 y AC97, con la clasificación de AC96: para un set_prop, la copia al revés de su par es otro hecho
        # (`clasificar`), pero `_sigue_publicada` la contaba como copia local; con ella, la corregida que la búsqueda
        # libre halla en otro par no se le atribuía y los dos checks daban «ausente» mientras la corrección contaba como
        # publicada. Solo es local la copia en su sentido; con ella, la de otro par es otro hecho del pasaje (un aviso).
        uid, pack = '9a' * 32, 'E-c0de00000001-97'
        self.nueva_arista(uid, self.ANTES, pack)
        self.decidir(pack, uid, 'set_prop', prop='note', value=self.NOTA['note'])
        self.registrar({'unidad': uid, 'fecha': '2026-09-24',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista retirada'},
                       {'unidad': uid, 'fecha': '2026-10-07', 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
        inicio, fin, props = self.ANTES['start'], self.ANTES['end'], {**self.ANTES['props'], **self.NOTA}
        al_reves, al_derecho = (fin, inicio, 'OWNS', props), (inicio, fin, 'OWNS', props)
        libre = (FULDO, fin, 'OWNS', props)
        for caso, aristas, codigo in (('al revés en su par y corregida en otro', (al_reves, libre), 0),
                                      ('solo al revés', (al_reves,), 1),
                                      ('en su sentido en su par y otra en otro par', (al_derecho, libre), 0)):
            with self.subTest(caso=caso):
                self.exportar_aristas(*aristas, nodos=(inicio,))
                self.los_dos_dicen(codigo, codigo and f'MATERIALIZACION: corrección decidida sin copias; la fila '
                                                      f'afirma que sigue publicada ({uid[:8]}, {pack})',
                                   codigo and 'arista «rectificada» ausente del export')

    def test_a_unit_whose_states_cannot_produce_the_copy_does_not_make_it_ambiguous(self):
        # Codex, 15ª pasada (M3, AC98): set_prop sobre una OWNS re-identificada, con su copia corregida en el par
        # actual; otra arista del censo del mismo pasaje, WORKS_FOR con un keep y un extremo fuera del export, que no
        # puede llegar a OWNS. Los dos checks la daban por ambigua. Solo compite la que tiene un estado alcanzable con
        # el tipo y el sentido de la copia: una OWNS sí, y una sin forma censal legible (sin tipo), por fallo cerrado.
        uid, otra, pack = 'f7' * 32, 'f8' * 32, 'E-c0de00000001-93'
        self.nueva_arista(uid, self.ANTES, pack)
        self.decidir(pack, uid, 'set_prop', prop='note', value=self.NOTA['note'])
        self.registrar({'unidad': uid, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
        copia = (FULDO, 'Company:ficcionbar', 'OWNS', {**self.ANTES['props'], **self.NOTA})
        for caso, tipo, codigo in (('la otra no puede ser OWNS', 'WORKS_FOR', 0), ('la otra puede', 'OWNS', 1),
                                   ('la otra sin forma censal legible', None, 1)):
            with self.subTest(caso=caso):
                db = A.connect(self.root)
                with db:
                    db.execute('DELETE FROM units WHERE unit_id=?', (otra,))
                    db.execute('DELETE FROM esc_packs WHERE pack_id=?', ('E-c0de00000001-94',))
                db.close()
                self.nueva_arista(otra, self.del_pasaje(self.ABIERTA, 'Company:ficcionbar', rtype=tipo),
                                  'E-c0de00000001-94')
                self.decidir('E-c0de00000001-94', otra, 'keep')
                self.exportar_aristas(copia)
                self.los_dos_dicen(codigo, codigo and f'MATERIALIZACION: copia ambigua entre unidades ({uid[:8]}, '
                                                      f'{otra[:8]}; {pack})',
                                   codigo and f'copia ambigua entre unidades ({uid[:8]}, {otra[:8]})')

    def test_the_operation_in_force_is_resolved_once_over_all_decisions(self):
        # Codex, 15ª pasada (M4, AC99): un retype a CONTROLS en un pack aplicado y otro humano posterior a
        # ASSOCIATED_WITH en un pack cerrado; fila «rectificado» y el export retipado a ASSOCIATED_WITH. El registro
        # daba 0 y `materializacion`, que resolvía la operación vigente solo entre los packs aplicados, evaluaba
        # CONTROLS y daba 1. Se resuelve una vez sobre todas: la vigente está en un pack no aplicado, un aviso, y la
        # sustituida no vuelve.
        uid, aplicado, cerrado = 'f9' * 32, 'E-c0de00000001-95', 'E-c0de00000001-96'
        self.nueva_arista(uid, self.del_pasaje(FULDO, 'Company:ficcionbar'), aplicado)
        self.decidir(aplicado, uid, 'retype', new_type='CONTROLS')
        db = A.connect(self.root)
        with db:
            db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                       (cerrado, FULDO, 'entity', 1, 1, json.dumps([uid]), 'closed'))
        db.close()
        humano = decision(pack_id=cerrado, units=[unit(uid, verdict='corregido', label='arista')],
                          edges=[edge_op(uid, 'retype', new_type='ASSOCIATED_WITH')], resolution='humano-2026-10-07')
        humano['_meta']['decided_at'] = '2026-10-07T10:00:00+00:00'
        self.write(cerrado, humano)
        self.registrar({'unidad': uid, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'ASSOCIATED_WITH', dict(self.ANTES['props'])))
        self.los_dos_dicen(0, f'materializacion: operación vigente en pack no aplicado ({uid[:8]}, {cerrado})')
        self.assertNotIn(f'({uid[:8]}, {aplicado})', self.materializacion()[1])

    def test_a_feed_layer_edge_is_found_by_its_loader_key_with_a_rewritten_quote(self):
        # AC101: `press_loader`, `ultima_hora_loader` y `context_loader` recrean la arista por par, tipo y capa y
        # sobrescriben la cita: con la cita reescrita en el feed, una retirada viva se daba por hecha y una corrección
        # bien publicada no se encontraba. Por la clave de su cargador: viva, sin corregir o corregida, como es. Codex,
        # 16ª pasada (16b-H2, AC110): también con documento, que el cargador fusiona por esa clave sobre la que ya está.
        for n, (capa, documento) in enumerate((('prensa', None), ('ultima_hora', None), ('contexto', None),
                                                ('prensa', 'informe-uco.pdf'), ('ultima_hora', 'informe-uco.pdf'))):
            uid, pack = f'a{n + 5}' * 32, f'E-c0de00000001-7{n + 5}'
            props = {'source_kind': capa, 'source_quote': 'cita original del feed ficticio'}
            if documento:
                props['source_doc'] = documento
            reescrita = {**props, 'source_quote': 'cita reescrita del feed ficticio'}
            self.nueva_arista(uid, {'start': FULDO, 'end': f'Company:feed-{n}', 'type': 'ASSOCIATED_WITH',
                                    'props': props}, pack)
            for accion, campos, fila, copia, codigo, lineas in (
                    ('deny', {}, {'dato': 'arista retirada'}, reescrita, 1,
                     (f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, {pack})',
                      'arista «suprimida» viva (1 copia; tolerancia 0)')),
                    ('set_prop', {'prop': 'note', 'value': self.NOTA['note']},
                     {'decision': 'rectificado', 'dato': 'arista corregida'}, {**reescrita, **self.NOTA}, 0,
                     (None, None))):
                with self.subTest(capa=capa, documento=bool(documento), accion=accion):
                    for vieja in (self.root / 'esc/decisions').glob('*.json'):
                        vieja.unlink()
                    self.decidir(pack, uid, accion, **campos)
                    self.registrar({'unidad': uid, **fila})
                    self.exportar_aristas((FULDO, f'Company:feed-{n}', 'ASSOCIATED_WITH', copia))
                    self.los_dos_dicen(codigo, *lineas)

    # --- AC102–AC111 (enmienda 20, 16ª pasada de Codex) -----------------------------------------------------------

    def test_a_foreign_form_only_excludes_a_copy_in_its_own_direction(self):
        # Codex, 16ª pasada (16a-H1, AC102): `formas_ajenas` guardaba las formas de las otras aristas del censo sin su
        # dirección. OWNS A→B, con B fuera del export y retipada a CONTROLS, y otra arista del censo OWNS C→A del mismo
        # pasaje; el export, CONTROLS A→C (la correcta) y OWNS A→C (la del tipo antiguo). La OWNS A→C no es la C→A de la
        # otra unidad, pero su par sí, y se excluía: los dos checks daban 0. Solo se excluye la de su dirección: viva
        # sin corregir en los dos; sin la otra unidad, igual; con la del tipo antiguo de verdad al revés (C→A, la otra),
        # nada.
        uid, otra, pack, suyo = 'c6' * 32, 'c7' * 32, 'E-c0de00000001-101', 'E-c0de00000001-102'
        antes, sociedad = 'Company:ficcionbar-de-antes-ficticia', 'Company:sociedad-c-ficticia'
        props = dict(self.ANTES['props'])
        self.nueva_arista(uid, {'start': FULDO, 'end': antes, 'type': 'OWNS', 'props': props}, pack)
        self.decidir(pack, uid, 'retype', new_type='CONTROLS')
        self.registrar({'unidad': uid, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[OWNS]-> ({antes}): arista corregida'})
        correcta = (FULDO, sociedad, 'CONTROLS', props)
        viva = (f'MATERIALIZACION: retirada decidida sin materializar ({RC.OTRO_PAR}{uid[:8]}, {pack})',
                f'arista «rectificada» viva sin corregir (par re-identificado; {RC.OTRO_PAR}1 copia; tolerancia 0)')
        for caso, con_otra, antigua, codigo in (('con la otra unidad', True, (FULDO, sociedad, 'OWNS', props), 1),
                                                ('sin la otra unidad', False, (FULDO, sociedad, 'OWNS', props), 1),
                                                ('la antigua al revés, que es la otra', True,
                                                 (sociedad, FULDO, 'OWNS', props), 0)):
            with self.subTest(caso=caso):
                db = A.connect(self.root)
                with db:
                    db.execute('DELETE FROM units WHERE unit_id=?', (otra,))
                    db.execute('DELETE FROM esc_packs WHERE pack_id=?', (suyo,))
                db.close()
                if con_otra:
                    self.nueva_arista(otra, {'start': sociedad, 'end': FULDO, 'type': 'OWNS', 'props': props}, suyo)
                self.exportar_aristas(correcta, antigua)
                self.los_dos_dicen(codigo, *(viva if codigo else (None, None)))

    def test_a_tag_belongs_to_its_unit_and_a_pair_its_signature_infers_can_be_ambiguous(self):
        # Codex, 16ª pasada (16a-H2, AC103): OWNS A→B y A→D, misma firma y la misma nota decidida, B y D fuera del
        # export, una sola copia corregida A→C y una regla A→C etiquetada solo para la primera. `pares_actuales` daba
        # A→C a las dos por la firma y la copia acreditaba las dos filas: los dos checks, 0. La etiqueta es de su
        # unidad: la primera se acredita y la segunda no tiene arista corregida. Con la regla sin etiqueta, el par A→C
        # solo lo deduce la firma, que las dos comparten: la copia es ambigua en las dos.
        primera, segunda = 'c8' * 32, 'c9' * 32
        packs = ('E-c0de00000001-103', 'E-c0de00000001-104')
        props = dict(self.ANTES['props'])
        for uid, fin, pack in ((primera, 'Company:ficcionbar-b-ficticia', packs[0]),
                               (segunda, 'Company:ficcionbar-d-ficticia', packs[1])):
            self.nueva_arista(uid, {'start': FULDO, 'end': fin, 'type': 'OWNS', 'props': props}, pack)
            self.decidir(pack, uid, 'set_prop', prop='note', value=self.NOTA['note'])
        self.registrar(*({'unidad': uid, 'decision': 'rectificado', 'dato': f'({FULDO}) -[OWNS]-> (): arista corregida'}
                         for uid in (primera, segunda)))
        copia = (FULDO, 'Company:ficcionbar', 'OWNS', {**props, **self.NOTA})
        for caso, marca, lineas in (
                ('etiquetada para la primera', f' [{primera[:8]}]',
                 (f'MATERIALIZACION: {RC.SIN_CORREGIDA} ({segunda[:8]}, {packs[1]})',
                  f'REGISTRO R-2026-10-01-0002: {RC.SIN_CORREGIDA}')),
                ('sin etiqueta', '',
                 (f'MATERIALIZACION: {RC.AMBIGUA} ({primera[:8]}, {segunda[:8]}; {packs[0]})',
                  f'REGISTRO R-2026-10-01-0001: {RC.AMBIGUA} ({primera[:8]}, {segunda[:8]})'))):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
                self.regla('fix', source_id=FULDO, target_id='Company:ficcionbar', type='OWNS', set=self.NOTA,
                           doc='informe-uco.pdf', quote_contains='participación ficticia',
                           reason=f'063 prueba{marca}: nota')
                self.exportar_aristas(copia)
                self.los_dos_dicen(1, *lineas)
                texto = self.materializacion()[1] + self.registro_check()[1]
                self.assertEqual(f'({primera[:8]}, {packs[0]})' in texto, False, texto)  # la primera, sin otro motivo

    def test_a_claim_posture_is_identified_by_its_claim(self):
        # Codex, 16ª pasada (16a-H3, AC104): el export cambiaba todo Claim por None y una postura casaba con cualquier
        # Claim de su titular. Una ASSERTS que corrige su página: su arista desaparece y otra postura del titular hacia
        # otra afirmación (con su propia cita) tiene la página decidida; los dos checks daban 0. Con la corregida y otra
        # legítima con otra página, 1. La afirmación es su claim_id, recuperado del censo: lo contrario en los dos. Una
        # postura cuya afirmación no se recupera es indeterminada en los dos, nunca la acredita cualquier Claim.
        uid, pack = 'ca' * 32, 'E-c0de00000001-105'
        props = {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf', 'source_page': 3,
                 'source_quote': 'Fuldo sostuvo una versión ficticia'}
        self.nueva_arista(uid, {'start': FULDO, 'end': None, 'type': 'ASSERTS', 'props': props}, pack)
        self.afirmacion(uid, 'c-suya-ficticia')
        self.decidir(pack, uid, 'set_prop', prop='source_page', value='5')
        self.registrar({'unidad': uid, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[ASSERTS]-> (): arista corregida'})
        suya, otra, corregida = 'Claim:c-suya-ficticia', 'Claim:c-otra-ficticia', {**props, 'source_page': 5}
        ajena = {**corregida, 'source_quote': 'Fuldo sostuvo otra versión ficticia'}
        for caso, aristas, codigo in (
                ('solo otra afirmación, con la página decidida', ((FULDO, otra, 'ASSERTS', ajena),), 1),
                ('la corregida y otra afirmación con otra página',
                 ((FULDO, suya, 'ASSERTS', corregida), (FULDO, otra, 'ASSERTS', {**ajena, 'source_page': 7})), 0)):
            with self.subTest(caso=caso):
                self.exportar_aristas(*aristas)
                self.los_dos_dicen(codigo, codigo and f'MATERIALIZACION: {RC.SIN_CORREGIDA} ({uid[:8]}, {pack})',
                                   codigo and RC.SIN_CORREGIDA)
        db = A.connect(self.root)
        with db:
            db.execute('UPDATE units SET object_id=? WHERE unit_id=?', ('o-rel', uid))
        db.close()
        self.exportar_aristas((FULDO, suya, 'ASSERTS', corregida))
        self.los_dos_dicen(1, f'MATERIALIZACION: {RC.INDETERMINADA} ({uid[:8]}, {pack})', RC.INDETERMINADA)

    def test_a_copy_of_the_old_type_is_its_own_in_any_layer(self):
        # Codex, 16ª pasada (16a-H4, AC105): la rama de los tipos incompatibles buscaba con la firma de
        # `firmas_posibles`, que lleva la capa, mientras `_es_suya` identifica una copia documental con cualquier capa.
        # OWNS judicial retipada a CONTROLS; el export, la correcta y otra OWNS del mismo par, documento y cita en
        # prensa: los dos checks daban 0. Se identifica primero y se juzga después: viva sin corregir en los dos. Con
        # otra cita, otro hecho: nada.
        uid, pack = 'cb' * 32, 'E-c0de00000001-106'
        valor = edge_value(end='Company:ficcionbar', rtype='OWNS', source_quote='participación ficticia en la sociedad')
        self.nueva_arista(uid, valor, pack)
        self.decidir(pack, uid, 'retype', new_type='CONTROLS')
        self.regla('fix', **self.SOCIEDAD, corrected_type='CONTROLS', reason=f'063 prueba [{uid[:8]}]: retipo')
        self.registrar({'unidad': uid, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista corregida'})
        correcta = (FULDO, 'Company:ficcionbar', 'CONTROLS', valor['props'])
        for caso, cita, codigo in (('la misma cita, en prensa', valor['props']['source_quote'], 1),
                                   ('otra cita, en prensa', 'otra cita ficticia de prensa', 0)):
            with self.subTest(caso=caso):
                antigua = (FULDO, 'Company:ficcionbar', 'OWNS', {**valor['props'], **self.PRENSA, 'source_quote': cita})
                self.exportar_aristas(correcta, antigua)
                self.los_dos_dicen(codigo, codigo and f'MATERIALIZACION: retirada decidida sin materializar '
                                                      f'({uid[:8]}, {pack})',
                                   codigo and 'arista «rectificada» viva sin corregir (1 copia; tolerancia 0)')

    def test_a_pack_that_does_not_exist_or_does_not_hold_its_unit_stops_every_command(self):
        # Codex, 16ª pasada (16a-H5 y 16b-M1, AC106): con un `_meta.pack_id` que no está en el censo, `materializacion`
        # daba la operación vigente por pendiente en un pack no aplicado (exit 0) e imprimía el pack tal cual, y con él
        # cualquier valor. Una decisión de un pack que no existe, o que no contiene su unidad, no vale: exit 2 en check,
        # apply, record y materializacion, con el fichero y la posición, nunca el pack ni la unidad. El aviso queda para
        # un pack que existe y la contiene.
        self.retirada()
        self.cerrada_sin_aplicar()
        self.exportar(U_EDGE2)
        centinela = 'E-PACK_CENTINELA_INEXISTENTE'
        for caso, pack, esperado in (
                ('un pack que no existe', centinela, f'{P3}.json: _meta.pack_id sin fila en esc_packs'),
                ('un pack sin su unidad', P1, f'{P3}.json: edges[1]: la unidad no pertenece a su pack')):
            decision_ = json.loads((self.root / f'esc/decisions/{P3}.json').read_text(encoding='utf-8'))
            decision_['_meta']['pack_id'] = pack
            (self.root / f'esc/decisions/{P3}.json').write_text(json.dumps(decision_), encoding='utf-8')
            for mando in (('check',), ('apply',), ('record',), ('materializacion', '--snapshot', str(self.snap)),
                          ('--snapshot', str(self.snap), 'privatizar', '--patch', str(self.base / 'privatizar-pack'))):
                with self.subTest(caso=caso, mando=mando[0] if mando[0] != '--snapshot' else 'privatizar'):
                    code, texto = self.correr(*mando)
                    self.assertEqual(code, 2, texto)
                    self.assertIn(esperado, texto)
                    self.assertNotIn('CENTINELA', texto)
        decision_['_meta']['pack_id'] = P2
        (self.root / f'esc/decisions/{P3}.json').write_text(json.dumps(decision_), encoding='utf-8')
        code, texto = self.materializacion()
        self.assertEqual(code, 0, texto)
        self.assertIn(f'materializacion: operación vigente en pack no aplicado ({U_EDGE2[:8]}, {P2})', texto)

    def test_a_quote_in_another_unicode_form_is_the_same_quote(self):
        # Codex, 16ª pasada (16a-M1, AC107): `_cita` colapsaba espacios y mayúsculas pero no normalizaba Unicode. Una
        # retirada viva con la misma cita en NFD (las tildes descompuestas) se daba por hecha en los dos checks, y una
        # corrección bien publicada con la cita en NFD no se encontraba. NFKC antes de comparar: viva y corregida, como
        # con la cita en NFC.
        retirada, corregida = '0c' * 32, '0d' * 32
        packs = ('E-c0de00000001-107', 'E-c0de00000001-108')
        cita = 'participación ficticia en la sociedad'
        valor = edge_value(end='Company:ficcionbar', rtype='OWNS', source_quote=cita)
        otro = edge_value(end='Company:sociedad-nfd-ficticia', rtype='OWNS', source_quote=cita)
        self.nueva_arista(retirada, valor, packs[0])
        self.nueva_arista(corregida, otro, packs[1])
        self.decidir(packs[0], retirada, 'deny')
        self.decidir(packs[1], corregida, 'set_prop', prop='note', value=self.NOTA['note'])
        self.regla('deny', **self.SOCIEDAD, reason=f'063 prueba [{retirada[:8]}]: retirada')
        self.registrar({'unidad': retirada, 'dato': f'({FULDO}) -[OWNS]-> (Company:ficcionbar): arista retirada'},
                       {'unidad': corregida, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[OWNS]-> (Company:sociedad-nfd-ficticia): arista corregida'})
        for forma in ('NFD', 'NFC'):
            with self.subTest(forma=forma):
                normalizada = unicodedata.normalize(forma, cita)
                self.exportar_aristas(
                    (FULDO, 'Company:ficcionbar', 'OWNS', {**valor['props'], 'source_quote': normalizada}),
                    (FULDO, 'Company:sociedad-nfd-ficticia', 'OWNS',
                     {**otro['props'], **self.NOTA, 'source_quote': normalizada}))
                self.los_dos_dicen(1, f'MATERIALIZACION: retirada decidida sin materializar ({retirada[:8]}, '
                                      f'{packs[0]})',
                                   'REGISTRO R-2026-10-01-0001: arista «suprimida» viva (1 copia; tolerancia 0)')
                texto = self.materializacion()[1] + self.registro_check()[1]
                self.assertNotIn(corregida[:8], texto)

    def test_a_repeated_key_stops_every_reader_with_only_its_position(self):
        # Codex, 16ª pasada (16a-M2 y 16c-M, AC108): PyYAML se queda con la última de dos claves iguales, en silencio.
        # Un segundo bloque `rectificaciones:` vacío escondía las filas del primero (una inválida) a `--tree` y a la
        # puerta; una `decision` repetida dejaba la última; un `deny:` o un `fix:` repetido, o una clave repetida dentro
        # de un `set`, borraban reglas. Un solo lector las rechaza en cualquier nivel: exit 2 en `--tree`, `--snapshot`,
        # check, materializacion y el loader, con el fichero y la posición, nunca la clave ni el valor.
        self.retirada()
        self.cerrada_sin_aplicar()
        self.exportar(U_EDGE2)
        registro, reglas = self.repo / A.REG_PATH, self.repo / A.REL_PATH
        registro.parent.mkdir(parents=True, exist_ok=True)
        fila = ("- {id: R-2026-10-08-0001, fecha: '2026-10-08', origen: auditoria-063, entidad: x, unidad: '', "
                "dato: x, fuente: x, decision: %s, fecha_resolucion: '2026-10-08', seed_version: pendiente-path-a%s}\n")
        original = reglas.read_text(encoding='utf-8')
        regla = ('  - {source_id: "Person:fuldo-fictez", target_id: "Company:ficcionbar", type: PAID, '
                 'set: {note: VALOR_CENTINELA_A, note: VALOR_CENTINELA_B}, reason: "fila de prueba"}\n')
        casos = (('registro', 'rectificaciones con dos bloques',
                  'rectificaciones:\n' + fila % ('CENTINELA_DECISION', '') + 'rectificaciones: []\n', None),
                 ('registro', 'una decision repetida',
                  'rectificaciones:\n' + fila % ('suprimido', ', decision: CENTINELA_DECISION'), None),
                 ('reglas', 'dos bloques deny', None, original + '\ndeny: []\n'),
                 ('reglas', 'una clave repetida en un set', None, original.replace('\nfix:\n', '\nfix:\n' + regla, 1)))
        for donde, caso, texto_registro, texto_reglas in casos:
            registro.write_text(texto_registro or 'rectificaciones: []\n', encoding='utf-8')
            reglas.write_text(texto_reglas or original, encoding='utf-8')
            fichero = 'registro.yaml' if donde == 'registro' else 'relations.yaml'
            mandos = [('materializacion', lambda: self.materializacion())]
            if donde == 'registro':
                mandos += [(modo, lambda modo=modo: self.registro_cli(modo)) for modo in ('--tree', '--snapshot')]
            else:
                mandos += [('check', lambda: self.correr('check')), ('loader', self.cargar_reglas)]
            for mando, llamada in mandos:
                with self.subTest(caso=caso, mando=mando):
                    code, texto = llamada()
                    self.assertEqual(code, 2, texto)
                    self.assertIn(f'{fichero}: YAML inválido (línea ', texto)
                    self.assertNotIn('CENTINELA', texto)
                    self.assertNotIn('note', texto)
        registro.write_text('rectificaciones: []\n', encoding='utf-8')
        reglas.write_text(original, encoding='utf-8')
        self.assertEqual(self.registro_cli('--tree')[0], 0)
        self.assertEqual(self.cargar_reglas()[0], 0)

    def registro_cli(self, modo):
        """(exit, salida) del registro del repo con `--tree` o `--snapshot`, con su stderr."""
        out = io.StringIO()
        extra = ['--snapshot', str(self.snap), '--root', str(self.root), '--overlay', str(self.repo)] \
            if modo == '--snapshot' else ['--tree']
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = RC.main(['--registro', str(self.repo / A.REG_PATH), *extra])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def cargar_reglas(self):
        """(exit, salida) de `relations_loader.load_files` sobre el overlay del repo: 0 si carga."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                relations_loader.load_files(self.repo / A.REL_DIR)
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def test_a_withdrawal_moved_by_a_declared_split_is_still_alive(self):
        # Codex, 16ª pasada (16b-H1, AC109): deny aplicado, con su regla y su fila «suprimido»; una escisión declarada
        # traslada la copia al homónimo y conserva los dos nodos originales. La retirada solo se buscaba fuera de su par
        # si faltaba un extremo: los dos checks daban 0. El loader escinde antes del deny y la copia trasladada escapa a
        # su regla: viva en los dos, en un par distinto del de la regla. Sin la escisión declarada, otro hecho: nada.
        uid, pack = 'ce' * 32, 'E-c0de00000001-109'
        valor = edge_value(end='Company:ficcionbar', rtype='PAID', source_quote='pago ficticio a la sociedad')
        self.nueva_arista(uid, valor, pack)
        self.decidir(pack, uid, 'deny')
        self.registrar({'unidad': uid, 'dato': f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista retirada'})
        trasladada = (FULDO, self.ESCINDIDA, 'PAID', valor['props'])
        for caso, escision, codigo in (('escisión declarada', True, 1), ('sin declarar', False, 0)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(
                    RELATIONS.replace('splits: []\n', self.ESCISION if escision else 'splits: []\n'), encoding='utf-8')
                self.regla('deny', source_id=FULDO, target_id='Company:ficcionbar', type='PAID', doc='informe-uco.pdf',
                           quote_contains='pago ficticio', reason=f'063 prueba [{uid[:8]}]: retirada')
                self.exportar_aristas(trasladada, nodos=('Company:ficcionbar',))
                self.los_dos_dicen(codigo, codigo and f'MATERIALIZACION: retirada decidida sin materializar '
                                                      f'({RC.OTRO_PAR}{uid[:8]}, {pack})',
                                   codigo and f'arista «suprimida» viva ({RC.OTRO_PAR}1 copia; tolerancia 0)')

    def test_record_does_not_take_the_absence_of_an_accusation_for_its_retype(self):
        # Codex, 16ª pasada (16b-H3, AC111): retype sobre una acusación, accusations.yaml sin ella y ni regla ni fila:
        # `record` lo daba por materializado porque la capa ya no la declara, y marcaba el pack. La ausencia solo prueba
        # una retirada; un retype necesita una regla o la capa que declare su resultado: `record` se niega y el pack
        # sigue sin aplicar, y con la regla, lo registra. En la puerta, sin la arista resultante, sin fila es un aviso
        # (ya no se acredita) y con su fila «rectificado», un problema en los dos checks.
        self.accusation_layer()
        d = self.retirada(action='retype', new_type='CONVICTED_OF')
        code, texto = self.record(d)
        self.assertEqual(code, 1, texto)
        self.assertIn(f'decisión sin materializar ({P3}.json {U_EDGE2[:8]})', texto)
        self.assertEqual(self.pack_state(P3), 'routed')
        self.cerrada_sin_aplicar()
        self.exportar()
        self.registrar()
        self.los_dos_dicen(0, f'materializacion: sin copias (sin fila) ({U_EDGE2[:8]}, {P3})')
        self.registrar({'unidad': U_EDGE2, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[ACCUSED_OF]-> (Crime:cohecho): arista corregida'})
        self.los_dos_dicen(1, f'MATERIALIZACION: {RC.SIN_CORREGIDA} ({U_EDGE2[:8]}, {P3})', RC.SIN_CORREGIDA)
        conn = A.connect(self.root)
        with conn:
            conn.execute("UPDATE esc_packs SET state='routed' WHERE pack_id=?", (P3,))
        conn.close()
        self.regla('fix', source_id=FULDO, target_id='Crime:cohecho', type='ACCUSED_OF', corrected_type='CONVICTED_OF',
                   kinds=['acusacion'], doc='auto-fuldo.pdf', quote_contains='Fuldo Fíctez recibió',
                   reason=f'063 prueba [{U_EDGE2[:8]}]: retipo')
        out, _ = self.record(d)
        self.assertEqual((out['packs'], self.pack_state(P3)), (1, 'applied'))

    # --- AC44 ---------------------------------------------------------------------------------------------------

    def record(self, *decisions):
        out = io.StringIO()
        conn = A.connect(self.root)
        try:
            with contextlib.redirect_stdout(out):
                try:
                    return A.record(conn, self.root, self.repo, list(decisions), '', 'RUNBOOK-063.md'), out.getvalue()
                except SystemExit as exc:
                    return exc.code, out.getvalue()
        finally:
            conn.close()

    def test_record_refuses_a_pack_whose_apply_did_not_run(self):
        d = self.retirada()
        self.assertEqual(self.gate(d)[0], [])
        code, texto = self.record(d)
        self.assertEqual(code, 1, texto)
        self.assertIn(f'decisión sin materializar ({P3}.json {U_EDGE2[:8]})', texto)
        for valor in self.VALORES:
            self.assertNotIn(valor, texto)
        self.assertEqual((self.state(U_EDGE2), self.pack_state(P3)), (('pending', None), 'routed'))
        A.apply(self.root, self.repo, [d], self.tag, self.upd)
        out, _ = self.record(d)
        self.assertEqual(out['packs'], 1)
        self.assertEqual((self.state(U_EDGE2), self.pack_state(P3)),
                         (('verified', 'humano-2026-09-25-corregido'), 'applied'))

    def test_record_also_needs_the_other_files_of_the_pack(self):
        # Un cierre parcial (063): otro fichero del mismo pack retiró una arista y nadie corrió su apply. Registrar el
        # resto cerraría el pack con esa decisión dentro.
        pendiente = partial([unit(U_EDGE, verdict='corregido', label='arista'), unit(U_ED)], [U_EDGE, U_ED],
                            resolution='humano-2026-10-03', name='P1-b.json', edges=[edge_op(U_EDGE, 'deny')])
        self.write('P1-b', pendiente)
        resto = partial([unit(U_SUM), unit(U_ROLE), unit(U_ID)], [U_SUM, U_ROLE, U_ID], name='P1-a.json')
        self.assertEqual(self.gate(resto)[0], [])
        code, texto = self.record(resto)
        self.assertEqual(code, 1, texto)
        self.assertIn(f'decisión sin materializar (P1-b.json {U_EDGE[:8]})', texto)
        self.assertEqual(self.state(U_SUM), ('pending', None))

    # --- AC45 ---------------------------------------------------------------------------------------------------

    def test_the_repair_materializes_the_decisions_of_an_applied_pack_once(self):
        self.retirada()
        self.cerrada_sin_aplicar()
        self.exportar(U_EDGE2)
        self.assertEqual(self.materializacion()[0], 1)
        reparar = ('--upd', '2026-10-07', 'apply', '--decisiones', f'{P3}.json')
        code, texto = self.correr(*reparar)
        self.assertEqual(code, 0, texto)
        reglas = yaml.safe_load((self.repo / A.REL_PATH).read_text(encoding='utf-8'))
        self.assertEqual([r for r in reglas['deny'] if f'[{U_EDGE2[:8]}]' in r['reason']][0]['type'], 'PAID')
        filas = yaml.safe_load((self.repo / A.REG_PATH).read_text(encoding='utf-8'))['rectificaciones']
        self.assertEqual([(f['unidad'], f['decision'], f['fecha'], f['fecha_resolucion']) for f in filas],
                         [(U_EDGE2, 'suprimido', '2026-10-07', '2026-09-25')])
        traza = self.root / 'esc/report/reparacion-2026-10-07.md'
        self.assertIn(f'deny unidad {U_EDGE2[:8]}', traza.read_text(encoding='utf-8'))
        self.assertEqual(self.materializacion()[0], 1)  # la regla prepara: hasta el Path A la arista sigue viva
        self.exportar()
        self.assertEqual(self.materializacion()[0], 0)
        # Una segunda pasada no duplica ni la regla ni la fila.
        antes = [sha(self.repo / p) for p in (A.REL_PATH, A.REG_PATH)]
        self.assertEqual(self.correr(*reparar)[0], 0)
        self.assertEqual([sha(self.repo / p) for p in (A.REL_PATH, A.REG_PATH)], antes)

    def test_the_repair_writes_neutral_reasons_to_the_public_overlays(self):
        # El texto libre de la decisión puede nombrar a personas: la reparación deja en los overlays solo la etiqueta,
        # la resolución y dónde está el motivo.
        d = full_decision()
        d['summary']['reason'], d['role']['reason'] = 'MOTIVO_RESUMEN_SINTETICO', 'MOTIVO_ROL_SINTETICO'
        d['edges'] = [edge_op(U_EDGE, 'deny', reason='MOTIVO_ARISTA_SINTETICO')]
        self.write(P1, dict(d, _file=f'{P1}.json'))
        db = A.connect(self.root)
        with db:
            db.execute("UPDATE esc_packs SET state='applied' WHERE pack_id=?", (P1,))
        db.close()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = A.main(['--root', str(self.root), '--repo', str(self.repo), '--upd', '2026-10-07', 'apply',
                           '--decisiones', f'{P1}.json'])
        self.assertEqual(code, 0)
        escrito = ''.join((self.repo / p).read_text(encoding='utf-8') for p in (A.REL_PATH, A.SUM_PATH, A.ROL_PATH))
        for centinela in ('MOTIVO_RESUMEN_SINTETICO', 'MOTIVO_ROL_SINTETICO', 'MOTIVO_ARISTA_SINTETICO'):
            self.assertNotIn(centinela, escrito)
        reglas = yaml.safe_load((self.repo / A.REL_PATH).read_text(encoding='utf-8'))
        self.assertEqual([r['reason'] for r in reglas['deny'] if f'[{U_EDGE[:8]}]' in r['reason']],
                         [f'{self.tag_reparacion} [{U_EDGE[:8]}]: decisión opus-cerrada; motivo en la decisión '
                          'interna de la auditoría'])

    tag_reparacion = '063 auditoría escalonada 2026-10-07'

    def test_a_dual_closed_decision_is_dated_by_when_it_was_decided(self):
        self.retirada(resolution='dual-cerrada')
        self.cerrada_sin_aplicar(resolution='dual-cerrada')
        self.assertEqual(self.correr('--upd', '2026-10-07', 'apply', '--decisiones', f'{P3}.json')[0], 0)
        filas = yaml.safe_load((self.repo / A.REG_PATH).read_text(encoding='utf-8'))['rectificaciones']
        self.assertEqual([f['fecha_resolucion'] for f in filas], ['2026-09-21'])

    def test_the_repair_keeps_the_gates(self):
        # Una regla `fix` del overlay que retipa la misma arista la sacaría del deny nuevo (AC40): la reparación no
        # escribe nada.
        self.retirada()
        self.cerrada_sin_aplicar()
        reglas = self.repo / A.REL_PATH
        fix = {'source_id': FULDO, 'target_id': 'Company:ficcionbar', 'type': 'PAID',
               'corrected_type': 'ASSOCIATED_WITH', 'doc': 'informe-uco.pdf', 'quote_contains': 'pago de 5.700 euros',
               'reason': 'otra tanda'}
        texto, _ = A._write_relations(reglas.read_text(encoding='utf-8'), {'deny': [], 'fix': [fix], 'alias_deny': []},
                                      self.tag)
        reglas.write_text(texto, encoding='utf-8')
        antes = sha(reglas)
        code, salida = self.correr('--upd', '2026-10-07', 'apply', '--decisiones', f'{P3}.json')
        self.assertEqual(code, 1, salida)
        self.assertIn('una regla fix de capa o tipo alcanza el objetivo de un deny (relations.yaml:fix[', salida)
        self.assertEqual(sha(reglas), antes)
        self.assertFalse((self.repo / A.REG_PATH).exists())

    def test_the_repair_takes_only_existing_decision_files(self):
        code, texto = self.correr('check', '--decisiones', 'E-noexiste.json')
        self.assertEqual(code, '--decisiones: no existe E-noexiste.json en esc/decisions')

    # --- AC113–AC126 (enmienda 21: el anonimato del registro frente al overlay público) ------------------------------

    RETIRADA = f'({FULDO}) -[PAID]-> (Company:ficcionbar): arista retirada'

    def retirada_aplicada(self, dato):
        """La retirada de U_EDGE2 (P3) con su regla, su pack aplicado y su fila «suprimido» con `dato`."""
        A.apply(self.root, self.repo, [self.retirada()], self.tag, self.upd)
        self.cerrada_sin_aplicar()
        self.registrar({'dato': dato})

    def test_a_withdrawal_whose_fact_another_layer_still_publishes_is_reported_by_both_checks(self):
        # Spec 071 AC119 (sustituye la decisión de que una copia de otra capa no cuenta): la retirada judicial de
        # U_EDGE2 con una copia viva del mismo par orientado, tipo, documento y cita en la capa `acusacion`. La fila
        # dice «arista retirada» y la asociación sigue publicada con la misma fuente: problema en los dos checks, salvo
        # que la fila lo declare con la fórmula cerrada; con ella, problema si ya no queda ninguna copia así. La que
        # explica otra arista del censo del par por la identidad de su capa (otra de prensa, de otro documento: su
        # cargador la recrea por par, tipo y capa) es de esa otra; la de una que es la misma asociación (mismo documento
        # y cita) no excusa.
        self.retirada_aplicada(self.RETIRADA)
        props = self.censo()['props']
        en_acusacion = (FULDO, 'Company:ficcionbar', 'PAID', {**props, 'source_kind': 'acusacion'})
        viva = 'arista «suprimida» viva en otra capa (1 copia; tolerancia 0)'
        self.exportar_aristas(en_acusacion)
        self.los_dos_dicen(1, f'MATERIALIZACION: {viva} ({U_EDGE2[:8]}, {P3})', f'REGISTRO R-2026-10-01-0001: {viva}')
        self.registrar({'dato': self.RETIRADA[:-len('arista retirada')] + RC.DUPLICADA})
        self.los_dos_dicen(0)
        self.exportar_aristas()
        self.los_dos_dicen(1, f'MATERIALIZACION: {RC.SIN_DUPLICADA} ({U_EDGE2[:8]}, {P3})', RC.SIN_DUPLICADA)
        self.registrar({'dato': self.RETIRADA})
        otra, duplicada = 'cd' * 32, 'ce' * 32
        self.nueva_arista(otra, edge_value(end='Company:ficcionbar', rtype='PAID', source_kind='prensa',
                                           source_doc='otro-documento.pdf', source_quote='otra crónica ficticia'),
                          'E-c0de00000001-107')
        self.decidir('E-c0de00000001-107', otra, 'keep')
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'PAID', {**props, 'source_kind': 'prensa'}))
        self.los_dos_dicen(0)
        self.nueva_arista(duplicada, {**self.censo(), 'props': {**props, 'source_kind': 'acusacion'}},
                          'E-c0de00000001-108')
        self.decidir('E-c0de00000001-108', duplicada, 'keep')
        self.exportar_aristas(en_acusacion)
        self.los_dos_dicen(1, f'MATERIALIZACION: {viva} ({U_EDGE2[:8]}, {P3})', f'REGISTRO R-2026-10-01-0001: {viva}')

    def test_a_withdrawal_whose_association_another_edge_publishes_is_true_only_while_it_does(self):
        # Spec 071 AC125: una fila «suprimido» que ya no oculta nada lleva la fórmula de que otra arista del mismo
        # documento sigue publicando su asociación (con su tipo o con el retipo de una regla `fix`, que la fórmula
        # nombra). Los dos checks la aceptan si y solo si el grafo, tras el overlay, publica una copia de su par, en su
        # sentido, desde su documento y con uno de esos tipos.
        publicada = self.RETIRADA[:-len('arista retirada')] + RC.PUBLICADA
        self.retirada_aplicada(publicada)
        props = self.censo()['props']
        gemela = (FULDO, 'Company:ficcionbar', 'PAID', {**props, 'source_quote': 'otro pasaje del mismo informe'})
        otro_doc = (FULDO, 'Company:ficcionbar', 'PAID', {**props, 'source_doc': 'otro-documento.pdf',
                                                          'source_quote': 'otro pasaje del mismo informe'})
        al_reves = ('Company:ficcionbar', FULDO, 'PAID', {**props, 'source_quote': 'otro pasaje del mismo informe'})
        retipada = (FULDO, 'Company:ficcionbar', 'CONTROLS', {**props, 'source_quote': 'otro pasaje del mismo informe'})
        base = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        linea = f'MATERIALIZACION: {RC.SIN_PUBLICADA} ({U_EDGE2[:8]}, {P3})'
        for caso, aristas, dato, codigo in (
                ('otra arista del mismo documento', [gemela], publicada, 0),
                ('solo desde otro documento', [otro_doc], publicada, 1),
                ('solo en el otro sentido', [al_reves], publicada, 1),
                ('ninguna', [], publicada, 1),
                ('retipada, con su tipo en la fórmula', [retipada], publicada + ', rectificada como CONTROLS', 0),
                ('retipada, sin él', [retipada], publicada, 1)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')
                if aristas == [retipada]:
                    # El retipo que nombra la fórmula lo alcanza una regla `fix` del overlay (AC136).
                    self.regla('fix', source_id=FULDO, target_id='Company:ficcionbar', type='PAID',
                               corrected_type='CONTROLS', doc=props['source_doc'], quote_contains='otro pasaje',
                               reason='063 prueba: retipo')
                self.registrar({'dato': dato})
                self.exportar_aristas(*aristas)
                self.los_dos_dicen(codigo, codigo and linea, codigo and RC.SIN_PUBLICADA)

    TERCERO, NOMBRE_TERCERO = 'Person:tercero-ficticio', 'Tercero Ficticio'
    CITA_TERCERO = 'Fuldo coincidió con el tercero ficticio en la reunión del hotel'

    def tercero_retirado(self, uid='d1' * 32):
        """Una ASSOCIATED_WITH judicial de Fuldo a un tercero, retirada con su regla opaca y su fila «suprimido» que
        oculta al tercero: lo que AC122-bis protege. Devuelve su unidad."""
        pack = 'E-c0de00000001-110'
        self.nueva_arista(uid, {'start': FULDO, 'end': self.TERCERO, 'type': 'ASSOCIATED_WITH',
                                'props': {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf',
                                          'source_quote': self.CITA_TERCERO}}, pack)
        self.decidir(pack, uid, 'deny')
        self.regla('deny', unidad=uid, reason=f'063 auditoría escalonada 2026-09-26 [{uid[:8]}]: {A.MOTIVO_OPACO}')
        self.registrar({'unidad': uid, 'fuente': 'informe-uco.pdf p.76',
                        'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista retirada'})
        return uid

    def test_a_public_entry_that_rebuilds_a_withdrawn_hidden_edge_is_reported_by_id_or_by_name(self):
        # Spec 071 AC122-bis (a): el marcador protege la arista retirada, no el par. Una regla sin etiqueta que nombra
        # al tercero oculto (por su id, o por su nombre con el resolver del loader) junto al extremo visible y el tipo
        # de la arista retirada la reconstruye. Si el grafo publica el par por otra arista, el marcador no protege nada
        # (D1): la fila tiene que nombrar sus dos extremos, y no se busca más. Una regla `fix` cuya revelación publica
        # el grafo en la arista que corrige es un aviso (AC132).
        self.tercero_retirado()
        base = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        por_id = {'source_id': self.TERCERO, 'target_id': FULDO, 'type': 'ASSOCIATED_WITH', 'reason': 'higiene'}
        por_nombre = {'source': self.NOMBRE_TERCERO, 'target_id': FULDO, 'type': 'ASSOCIATED_WITH', 'reason': 'x'}
        otra_publicada = (FULDO, self.TERCERO, 'ASSOCIATED_WITH', {'source_kind': 'judicial',
                                                                  'source_doc': 'otro-documento.pdf',
                                                                  'source_quote': 'otro pasaje ficticio'})
        ficcionbar = (FULDO, 'Company:ficcionbar', 'PAID', self.censo()['props'])
        cita = {**self.SOCIEDAD, 'type': 'PAID', 'quote_contains': 'pago de', 'set': {'note': 'nota corregida'},
                'reason': f'cita: {self.CITA_TERCERO} ({self.NOMBRE_TERCERO})'}
        # AC132: la copia que la regla corrige tiene que publicar lo que la regla revela (la cita retirada y al
        # tercero).
        publicada = {**self.SOCIEDAD, 'type': 'PAID', 'quote_contains': self.CITA_TERCERO[:40],
                     'set': {'note': 'nota corregida'}, 'reason': 'nota'}
        ficcionbar_cita = (FULDO, 'Company:ficcionbar', 'PAID', {**self.censo()['props'],
                                                                  'source_quote': f'{self.CITA_TERCERO}, y pagó'})
        nombres = {self.TERCERO: self.NOMBRE_TERCERO}
        reconstruible = 'fila anonimizada reconstruible (relations/relations.yaml:'
        # Con un trozo de cita ajeno la regla no alcanza la otra arista publicada, que sigue viva tras el overlay.
        ajena = {**por_id, 'quote_contains': 'fragmento ajeno'}
        for caso, seccion, regla, aristas, problema, aviso, par in (
                ('por su id', 'deny', por_id, [], True, False, False),
                ('por su nombre, con el resolver del loader', 'deny', por_nombre, [], True, False, False),
                ('el par publicado por otra arista', 'deny', {**ajena, 'doc': 'informe-uco.pdf'}, [otra_publicada],
                 False, False, True),
                ('una fix cuyo motivo cita lo que su arista no publica', 'fix', cita, [ficcionbar], True, False,
                 False),
                ('una fix cuya cita publica la arista que corrige', 'fix', publicada, [ficcionbar_cita], False, True,
                 False)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')
                self.regla(seccion, **regla)
                self.exportar_aristas(*aristas, nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'), nombres=nombres)
                code, texto = self.registro_check()
                self.assertEqual(reconstruible in texto, problema, texto)
                self.assertEqual(RC.CITA_PUBLICADA in texto, aviso, texto)
                self.assertEqual(MARCADO_PUBLICADO in texto, par, texto)
                self.assertNotIn(self.TERCERO, texto)
                self.assertNotIn(self.NOMBRE_TERCERO, texto)

    def test_a_public_entry_with_the_unpublished_pair_of_an_anonymized_row(self):
        # Spec 071 AC122-bis (b): si el grafo no publica el par {oculto, visible} tras el overlay, toda entrada que
        # nombra los dos es un problema, salvo un ítem del feed y un texto curado de contexto o de acusaciones, que son
        # avisos del dueño con la huella de la fila; y un texto curado que nombra los dos publica el par: entonces la
        # regla que también los nombra ya no es problema.
        uid = self.tercero_retirado()
        con_otro_tipo = {'source_id': self.TERCERO, 'target_id': FULDO, 'type': 'MET_WITH', 'reason': 'higiene'}
        self.regla('deny', **con_otro_tipo)
        self.exportar_aristas(nodos=(FULDO, self.TERCERO), nombres={self.TERCERO: self.NOMBRE_TERCERO})
        par = 'fila anonimizada de-anonimizable, par no publicado (relations/relations.yaml:deny['
        code, texto = self.registro_check()
        self.assertEqual(code, 1, texto)
        self.assertIn(par, texto)
        feed = self.repo / 'pipeline/data/ultima_hora/feed.yaml'
        feed.parent.mkdir(parents=True, exist_ok=True)
        items = [{'headline': 'Titular ficticio', 'entities': [FULDO, self.TERCERO]}]
        feed.write_text(yaml.safe_dump({'items': items}), encoding='utf-8')
        code, texto = self.registro_check()
        self.assertIn(f'{RC.FEED_PAR} ({uid[:8]}, ultima_hora/feed.yaml:items[0])', texto)
        self.assertIn(par, texto)
        resumen = self.repo / A.SUM_PATH
        resumen.write_text(resumen.read_text(encoding='utf-8') + f'- entity_id: {FULDO}\n  name: Fuldo\n'
                           f'  resumen: Se reunió con {self.NOMBRE_TERCERO} en un hotel ficticio.\n'
                           f"  updated: '2026-10-01'\n  reason: prueba\n", encoding='utf-8')
        code, texto = self.registro_check()
        self.assertEqual(code, 0, texto)
        self.assertIn(f'{RC.EDITORIAL} ({uid[:8]}, context/summaries_qa061.yaml:summaries[1])', texto)
        self.assertNotIn(par, texto)
        self.assertNotIn(self.NOMBRE_TERCERO, texto)

    def test_privatizar_writes_the_data_patch_and_the_private_declarations_without_any_value(self):
        # Spec 071 AC117 (con AC113, AC119, AC121, AC124, AC127, AC138, AC140 y D1): `privatizar` deja fuera del repo el
        # parche y las declaraciones privadas y solo imprime recuentos. Una opaca por unidad (la segunda regla de la
        # misma retirada, con otro trozo de su cita, va a lo privado) que dice cuántos predicados privados resuelve; la
        # fila marcada cuyo par publica el grafo nombra sus dos extremos (la «suprimido», con la fórmula de su caso: una
        # copia de su tipo, sentido y documento es una duplicada) y su regla no se hace opaca; una regla sin etiqueta
        # pedida pasa a una clave privada nueva, que lleva su fila; una deny sin etiqueta que reconstruye la retirada se
        # borra si un predicado de la opaca la subsume y, si no, va a lo privado como ajena; una fix muerta en
        # cualquier grafo que la reconstruye se borra; la «rectificado» cuyo par no publica el grafo se queda y se
        # lista. Repetido sobre su resultado, no cambia nada.
        def arista(uid, fin, tipo, cita, pack):
            self.nueva_arista(uid, {'start': FULDO, 'end': fin, 'type': tipo, 'props': {
                'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf', 'source_quote': cita}}, pack)

        uno, dos, tres, seis = 'e1' * 32, 'e2' * 32, 'e3' * 32, 'e6' * 32
        cita_uno = 'Fuldo y el tercero uno ficticio se asociaron para el reparto de la obra'
        arista(uno, 'Person:tercero-uno', 'ASSOCIATED_WITH', cita_uno, 'E-c0de00000001-111')
        arista(dos, 'Person:tercero-dos', 'ASSOCIATED_WITH', 'Fuldo y el tercero dos ficticio', 'E-c0de00000001-112')
        arista(tres, 'Person:tercero-tres', 'MET_WITH', 'Fuldo se reunió con el tercero tres', 'E-c0de00000001-113')
        arista(seis, 'Person:tercero-seis', 'ASSOCIATED_WITH', 'Fuldo y el tercero seis', 'E-c0de00000001-116')
        for pack, uid, accion, campos in (('E-c0de00000001-111', uno, 'deny', {}),
                                          ('E-c0de00000001-112', dos, 'set_prop', {'prop': 'note', 'value': 'x'}),
                                          ('E-c0de00000001-113', tres, 'deny', {}),
                                          ('E-c0de00000001-116', seis, 'set_prop', {'prop': 'note', 'value': 'x'})):
            self.decidir(pack, uid, accion, **campos)
        lote = '063 auditoría escalonada 2026-09-26'
        regla_uno = A.regla_de(self.censo(uno))
        self.regla('deny', **regla_uno, reason=f'{lote} [{uno[:8]}]: Fuldo y Tercero Uno, motivo con nombres')
        self.regla('deny', **{**regla_uno, 'quote_contains': 'reparto de la obra'},
                   reason=f'{lote} [{uno[:8]}]: el mismo hecho, otro trozo')
        self.regla('deny', **A.regla_de(self.censo(tres)), reason=f'{lote} [{tres[:8]}]: Fuldo y Tercero Tres')
        self.regla('deny', source_id='Person:tercero-cuatro', target_id=FULDO, type='ASSOCIATED_WITH',
                   doc='informe-uco.pdf', reason='higiene sin etiqueta')
        # Una sin etiqueta con un trozo que contiene el de la segunda: la subsume un predicado de la opaca, en
        # cualquier grafo. Otra desde otro documento alcanza una copia que la opaca no: va a lo privado. Y una fix con
        # ese mismo trozo, que solo pone una nota: lo que alcanza lo retira la deny tal como entra y tal como sale.
        self.regla('deny', **{**regla_uno, 'quote_contains': 'para el reparto de la obra'},
                   reason='higiene sin etiqueta, la misma')
        self.regla('deny', **{**regla_uno, 'doc': 'otro-documento.pdf', 'quote_contains': None},
                   reason='higiene sin etiqueta, otro documento')
        self.regla('fix', source_id=FULDO, target_id='Person:tercero-uno', type='ASSOCIATED_WITH',
                   doc='informe-uco.pdf', quote_contains='reparto de la obra', set={'note': 'nota'},
                   reason='nota sobre una arista que ya no está')
        retirada = '-[{t}]-> (Person:[tercero]): arista retirada'
        self.registrar({'unidad': uno, 'dato': f'({FULDO}) ' + retirada.format(t='ASSOCIATED_WITH')},
                       {'unidad': dos, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista corregida'},
                       {'unidad': tres, 'dato': f'({FULDO}) ' + retirada.format(t='MET_WITH')},
                       {'unidad': '', 'dato': f'(Person:[tercero]) -[ASSOCIATED_WITH]-> ({FULDO}): arista retirada'},
                       {'dato': self.RETIRADA},
                       {'unidad': seis, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista corregida'})
        cabecera = '# Registro de prueba\n#     unidad: <unit_id del censo, o "" (solicitud o tanda del dueño)>\n'
        registro = self.repo / A.REG_PATH
        registro.write_text(cabecera + registro.read_text(encoding='utf-8'), encoding='utf-8')
        props = {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf'}
        self.exportar_aristas((FULDO, 'Person:tercero-dos', 'ASSOCIATED_WITH', {**props, 'source_quote': 'c2'}),
                              (FULDO, 'Person:tercero-tres', 'MET_WITH', {**props, 'source_quote': 'otro encuentro'}),
                              (FULDO, 'Person:tercero-uno', 'ASSOCIATED_WITH',
                               {**props, 'source_doc': 'otro-documento.pdf', 'source_quote': 'otro pasaje'}))
        salida = self.base / 'privatizar'
        code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', str(salida),
                                  '--privada', 'relations.yaml:deny[4]=R-2026-10-01-0004',
                                  '--duplicada', 'R-2026-10-01-0005')
        self.assertEqual(code, 0, texto)
        cuenta = json.loads(texto.splitlines()[0].split('privatizar: ', 1)[1])
        self.assertEqual(cuenta, {'reglas opacas': 2, 'reglas opacas repetidas quitadas': 1,
                                  'declaraciones privadas de un predicado explícito': 1, 'clave privada nueva': 1,
                                  'filas que nombran sus extremos: su par está publicado (D1)': 2,
                                  'filas con la fórmula de duplicada': 1, 'filas con una clave privada': 1,
                                  'AC124: deny redundantes borradas': 1, 'AC124: deny a predicado privado': 1,
                                  'fix muertas borradas (remedio 1)': 1,
                                  'reglas opacas con declaraciones privadas': 2}, texto)
        self.assertIn('sin tocar: 1 (R-2026-10-01-0006)', texto)
        self.assertNotIn('sin remedio', texto)
        for valor in ('tercero', 'Tercero', 'nombres', 'otro-documento'):
            self.assertNotIn(valor, texto)
        self.assertFalse((self.repo / 'pipeline/data/relations/relations.yaml.orig').exists())
        proc = subprocess.run(['git', 'apply', str(salida / 'datos-071-e21.patch')], cwd=self.repo, capture_output=True,
                              encoding='utf-8')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        reglas = yaml.safe_load((self.repo / A.REL_PATH).read_text(encoding='utf-8'))
        deny = reglas['deny']
        self.assertEqual(len(deny), 4, deny)
        self.assertEqual(deny[1], {'unidad': uno, 'declaraciones': 2,
                                   'reason': f'{lote} [{uno[:8]}]: {A.MOTIVO_OPACO}'})
        self.assertEqual(deny[2]['reason'], f'{lote} [{tres[:8]}]: Fuldo y Tercero Tres')
        clave = deny[3]['unidad']
        self.assertEqual(set(deny[3]), {'unidad', 'declaraciones', 'reason'})
        self.assertEqual(deny[3]['declaraciones'], 1)
        self.assertEqual(reglas['fix'], yaml.safe_load(RELATIONS)['fix'])
        privadas = yaml.safe_load((salida / 'reglas_opacas.yaml').read_text(encoding='utf-8'))
        self.assertEqual(sorted(privadas), sorted([uno, clave]))
        self.assertEqual([p.get('quote_contains') for p in privadas[uno]], ['reparto de la obra', None])
        self.assertEqual([p.get('ajena') for p in privadas[uno]], [None, True])
        self.assertEqual(privadas[clave], [{'source_id': 'Person:tercero-cuatro', 'target_id': FULDO,
                                            'type': 'ASSOCIATED_WITH', 'doc': 'informe-uco.pdf'}])
        filas = yaml.safe_load(registro.read_text(encoding='utf-8'))['rectificaciones']
        self.assertEqual([f['dato'] for f in filas], [
            f'({FULDO}) ' + retirada.format(t='ASSOCIATED_WITH'),
            f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:tercero-dos): arista corregida',
            f'({FULDO}) -[MET_WITH]-> (Person:tercero-tres): {RC.DUPLICADA}',
            f'(Person:[tercero]) -[ASSOCIATED_WITH]-> ({FULDO}): arista retirada',
            self.RETIRADA[:-len('arista retirada')] + RC.DUPLICADA,
            f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista corregida'])
        self.assertEqual(filas[3]['unidad'], clave)
        self.assertIn('#     unidad: <clave de la decisión: unit_id del censo o clave privada, o ""',
                      registro.read_text(encoding='utf-8'))
        # Con las declaraciones privadas en la raíz de la auditoría, el contraste de las reglas con las decisiones no ve
        # ninguna incompatible: el predicado ajeno se resuelve sin la etiqueta de la unidad, como la regla que
        # sustituye.
        shutil.copy(salida / 'reglas_opacas.yaml', self.root / alcance.REGLAS_OPACAS)
        errores, _, _ = A._contraste_reglas(self.root, self.repo, vivo=RC.export_vivo(self.snap))
        self.assertEqual([e for e in errores if 'incompatible' in e], [])
        # AC140: repetido igual sobre su resultado (con sus declaraciones en la raíz), no cambia nada.
        otra = self.base / 'privatizar-2'
        code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', str(otra),
                                  '--privada', 'relations.yaml:deny[4]=R-2026-10-01-0004',
                                  '--duplicada', 'R-2026-10-01-0005')
        self.assertEqual(code, 0, texto)
        self.assertEqual((otra / 'datos-071-e21.patch').read_text(encoding='utf-8'), '')
        self.assertEqual((otra / 'reglas_opacas.yaml').read_bytes(), (salida / 'reglas_opacas.yaml').read_bytes())

    # --- enmienda 21-bis (spec 071 AC127–AC142, D1 y D2; 17ª pasada de Codex) -----------------------------------

    def motivo_opaco(self, uid, motivo):
        """El overlay con el motivo de la regla opaca de `uid` cambiado por `motivo`."""
        reglas = self.repo / A.REL_PATH
        doc = yaml.safe_load(reglas.read_text(encoding='utf-8'))
        for regla in doc['deny']:
            if regla.get('unidad') == uid:
                regla['reason'] = motivo
        reglas.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=1000), encoding='utf-8')

    def arbol(self):
        """(exit, salida) de `registro_check --tree` sobre el registro y el overlay del repo de prueba."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = RC.main(['--registro', str(self.repo / A.REG_PATH), '--tree', '--overlay', str(self.repo)])
        return code, out.getvalue()

    def nodo(self, eid, **props):
        """Un nodo más en el export, con estos props de texto."""
        with (self.snap / 'nodes.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'labels': [eid.split(':')[0]], 'props': {'entity_id': eid, **props},
                                'ref': f'4:x:{900 + len(props)}'}, ensure_ascii=False) + '\n')

    def test_an_opaque_rule_whose_reason_carries_what_its_row_hides_is_reconstructible(self):
        # Spec 071 AC130: `referencias` ya no salta las reglas opacas: barre todo lo suyo salvo su clave y sus
        # declaraciones. Un motivo con el id oculto y la cita retirada reconstruye la fila en --snapshot, y en --tree
        # el motivo está fuera de su fórmula cerrada.
        uid = self.tercero_retirado()
        formula = f'063 auditoría escalonada 2026-09-26 [{uid[:8]}]: {A.MOTIVO_OPACO}'
        for caso, motivo, problema in (('fórmula', formula, False),
                                       ('con lo oculto', f'{formula} ({self.TERCERO}: {self.CITA_TERCERO})', True)):
            with self.subTest(caso=caso):
                self.motivo_opaco(uid, motivo)
                self.exportar_aristas(nodos=(FULDO, self.TERCERO))
                _, texto = self.registro_check()
                self.assertEqual('fila anonimizada reconstruible (relations/relations.yaml:deny[' in texto, problema,
                                 texto)
                _, arbol = self.arbol()
                self.assertEqual(f'{RC.RECONSTRUIBLE} (relations/relations.yaml:deny[' in arbol, problema, arbol)
                for valor in (self.TERCERO, self.CITA_TERCERO):
                    self.assertNotIn(valor, texto + arbol)

    def test_the_snapshot_also_finds_the_fingerprint_of_an_anonymized_row(self):
        # Spec 071 AC131: --snapshot también busca la huella (de 8 a 64 hex, prefijo de la unidad) fuera de los campos
        # estructurales de su regla opaca y de su fila.
        uid = self.tercero_retirado()
        resumen = self.repo / A.SUM_PATH
        resumen.write_text(resumen.read_text(encoding='utf-8') + f'# revisado con la unidad {uid[:9]}\n',
                           encoding='utf-8')
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        code, texto = self.registro_check()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'REGISTRO R-2026-10-01-0001: {HUELLA} (context/summaries_qa061.yaml:', texto)

    def test_what_the_graph_publishes_about_a_hidden_third_party_is_a_warning_and_a_public_json_is_swept(self):
        # Spec 071 AC133 y D2: los textos del export cuentan como públicos. Una arista viva con el tercero oculto como
        # extremo y el visible en su texto, del tipo de la retirada, es un aviso («par reconstruible desde el grafo»):
        # texto judicial citado, que no se reescribe; con la cita retirada sin el visible, nada (ninguna fila lleva su
        # cita); el texto de un nodo que nombra a los dos, un aviso editorial. Un JSON público que los nombra con el
        # tipo, en cambio, reconstruye la fila: lo escribimos nosotros.
        uid = self.tercero_retirado()
        otra, judicial = 'Company:sociedad-ficticia', {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf'}
        nombres = {FULDO: 'Fuldo Fíctez', self.TERCERO: self.NOMBRE_TERCERO}
        casos = (('arista del tipo con el visible en su cita',
                  [(self.TERCERO, otra, 'ASSOCIATED_WITH', {**judicial, 'source_quote': 'con Fuldo Fíctez en ella'})],
                  None, f'{PAR_DESDE_GRAFO} ({uid[:8]}, export:arista['),
                 ('arista con la cita retirada sin el visible',
                  [(self.TERCERO, otra, 'MET_WITH', {**judicial, 'source_quote': self.CITA_TERCERO})], None, None),
                 ('nodo que los nombra', [], 'Sociedad de Tercero Ficticio y Fuldo Fíctez',
                  f'{RC.EDITORIAL} ({uid[:8]}, export:nodo['))
        for caso, aristas, resumen, aviso in casos:
            with self.subTest(caso=caso):
                self.exportar_aristas(*aristas, nodos=(FULDO, self.TERCERO, otra), nombres=nombres)
                if resumen:
                    self.nodo('Company:otra-ficticia', summary=resumen)
                code, texto = self.registro_check()
                self.assertEqual(code, 0, texto)
                if aviso:
                    self.assertIn(aviso, texto)
                self.assertNotIn(RC.RECONSTRUIBLE, texto)
        publico = self.repo / 'pipeline/data/boe/ficticio.json'
        publico.parent.mkdir(parents=True, exist_ok=True)
        publico.write_text(json.dumps([{'nota': f'{self.TERCERO} ASSOCIATED_WITH {FULDO}'}]), encoding='utf-8')
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        code, texto = self.registro_check()
        self.assertIn(f'{RC.RECONSTRUIBLE} (boe/ficticio.json:[0])', texto)

    def test_a_marked_row_without_unit_is_checked_with_its_private_declaration(self):
        # Spec 071 AC134: una fila con marcador sin unidad se comprueba con la declaración privada de su id (`filas:`
        # del fichero privado); sin ella, su anonimato no se puede comprobar.
        self.registrar({'unidad': '', 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): resumen reescrito'})
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        par = {'source_id': self.TERCERO, 'target_id': FULDO, 'type': 'MET_WITH', 'reason': 'higiene'}
        # Sin ella, además, el fichero privado no cubre el registro y la puerta para antes (AC183).
        for caso, declarada, regla, problema in (
                ('sin declaración', False, False, 'filas del registro que admiten una declaración privada sin la suya'),
                ('con su declaración y una regla con el par', True, True, RC.PAR_NO_PUBLICADO),
                ('con su declaración y nada público', True, False, None)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(RELATIONS, encoding='utf-8')
                if regla:
                    self.regla('deny', **par)
                (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump({'filas': {'R-2026-10-01-0001': [
                    {'source_id': self.TERCERO, 'target_id': FULDO, 'type': 'ASSOCIATED_WITH'}]}} if declarada
                    else {}), encoding='utf-8')
                code, texto = self.registro_check()
                if not declarada:
                    self.assertEqual(code, 2, texto)
                    self.assertIn(problema, texto)
                    continue
                self.assertEqual(f'REGISTRO R-2026-10-01-0001: {problema}' in texto if problema else
                                 'REGISTRO R-2026-10-01-0001:' not in texto, True, texto)

    def test_a_retype_named_by_the_formula_must_be_reached_by_a_rule(self):
        # Spec 071 AC136: el retipo de la fórmula de AC125 («…, rectificada como T») tiene que alcanzarlo una regla
        # `fix` con el predicado del loader desde su par y su documento; uno que solo dice la prosa es un problema, en
        # los dos checks.
        self.retirada_aplicada(self.RETIRADA[:-len('arista retirada')] + RC.PUBLICADA + ', rectificada como CONTROLS')
        props = self.censo()['props']
        retipada = (FULDO, 'Company:ficcionbar', 'CONTROLS', {**props, 'source_quote': 'otro pasaje del mismo informe'})
        base = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        regla = dict(source_id=FULDO, target_id='Company:ficcionbar', type='PAID', corrected_type='CONTROLS',
                     doc=props['source_doc'], quote_contains='otro pasaje', reason='063 prueba: retipo')
        linea = f'MATERIALIZACION: {RETIPO_NO_ALCANZABLE} ({U_EDGE2[:8]}, {P3})'
        for caso, fix, codigo in (('solo en la prosa', None, 1),
                                  ('una regla de otro documento', {**regla, 'doc': 'otro-documento.pdf'}, 1),
                                  ('su regla', regla, 0)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')
                if fix:
                    self.regla('fix', **fix)
                self.exportar_aristas(retipada)
                self.los_dos_dicen(codigo, codigo and linea, codigo and RETIPO_NO_ALCANZABLE)
                # Sin las reglas del overlay (`--overlay`), el registro no puede saber si una la retipa: no corre
                # (AC179).
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit) as salida:
                    RC.main(['--registro', str(self.repo / A.REG_PATH), '--snapshot', str(self.snap), '--root',
                             str(self.root)])
                self.assertEqual(salida.exception.code, 2)

    def test_a_public_rule_naming_the_hidden_third_party_is_resolved_like_the_loader(self):
        # Spec 071 AC137: los extremos de una entrada pública se resuelven como en el loader (`_resolve`: el tipo sin
        # blancos, Person si no lo dice): una regla que nombra al tercero oculto por su nombre con « Person » lo nombra,
        # aunque el export no publique su nombre.
        self.tercero_retirado()
        self.regla('deny', source=self.NOMBRE_TERCERO, source_type=' Person ', target_id=FULDO, type='ASSOCIATED_WITH',
                   reason='higiene')
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        _, texto = self.registro_check()
        self.assertIn('fila anonimizada reconstruible (relations/relations.yaml:deny[', texto)

    def test_each_formula_of_a_published_association_is_literally_true_in_both_checks(self):
        # Spec 071 D1 y AC142: la copia duplicada (AC119) exige una arista viva de su tipo, en su sentido y desde su
        # documento; con su tipo desde otro documento o en el otro sentido, «arista retirada; la asociación sigue
        # publicada»; bajo otros tipos, nombrándolos, y de la ontología. Cada una, verdad o un problema en los dos.
        self.retirada_aplicada(self.RETIRADA)
        props = self.censo()['props']
        pasaje = {**props, 'source_quote': 'otro pasaje del mismo informe'}
        gemela = (FULDO, 'Company:ficcionbar', 'PAID', pasaje)
        otro_doc = (FULDO, 'Company:ficcionbar', 'PAID', {**pasaje, 'source_doc': 'otro-documento.pdf'})
        al_reves = ('Company:ficcionbar', FULDO, 'PAID', pasaje)
        otro_tipo = (FULDO, 'Company:ficcionbar', 'CONTROLS', pasaje)
        duplicada = self.RETIRADA[:-len('arista retirada')] + RC.DUPLICADA
        sigue = f'{self.RETIRADA}; {SIGUE}'
        for caso, arista, dato, motivo in (
                ('duplicada de verdad', gemela, duplicada, None),
                ('duplicada desde otro documento', otro_doc, duplicada, RC.SIN_DUPLICADA),
                ('duplicada al revés', al_reves, duplicada, RC.SIN_DUPLICADA),
                ('sigue, desde otro documento', otro_doc, sigue, None),
                ('sigue, al revés', al_reves, sigue, None),
                ('sigue, sin su tipo', otro_tipo, sigue, SIN_SIGUE),
                ('bajo otro tipo', otro_tipo, f'{sigue} bajo CONTROLS', None),
                ('bajo un tipo que no está', gemela, f'{sigue} bajo CONTROLS', SIN_SIGUE),
                ('bajo un tipo que no es de la ontología', otro_tipo, f'{sigue} bajo INVENTADO', SIN_SIGUE)):
            with self.subTest(caso=caso):
                self.registrar({'dato': dato})
                self.exportar_aristas(arista)
                self.los_dos_dicen(1 if motivo else 0, motivo and f'MATERIALIZACION: {motivo} ({U_EDGE2[:8]}, {P3})',
                                   motivo)

    def test_a_marker_over_a_pair_the_graph_publishes_is_a_problem(self):
        # Spec 071 D1: un marcador sobre un par que el grafo publica tras el overlay (alguna arista viva entre sus dos
        # nodos, en cualquier sentido, tipo, documento o capa) no protege nada: la fila nombra sus dos extremos.
        self.tercero_retirado()
        otra = (self.TERCERO, FULDO, 'MET_WITH', {'source_kind': 'judicial', 'source_doc': 'otro-documento.pdf',
                                                  'source_quote': 'se vieron una vez'})
        for caso, aristas, problema in (('sin el par', [], False), ('con el par en otro sentido y tipo', [otra], True)):
            with self.subTest(caso=caso):
                self.exportar_aristas(*aristas, nodos=(FULDO, self.TERCERO))
                _, texto = self.registro_check()
                self.assertEqual(f'REGISTRO R-2026-10-01-0001: {MARCADO_PUBLICADO}' in texto, problema, texto)

    def test_an_anonymized_row_never_carries_a_piece_of_its_withdrawn_quote(self):
        # Spec 071 D2: ninguna fila marcada lleva un trozo de seis palabras seguidas de su cita retirada (por eso la
        # cita retirada en una arista del grafo no la reconstruye); cinco no son un trozo.
        uid = self.tercero_retirado()
        palabras = self.CITA_TERCERO.split()
        for caso, n, problema in (('seis palabras', 6, True), ('cinco', 5, False)):
            with self.subTest(caso=caso):
                self.registrar({'unidad': uid, 'fuente': f'informe-uco.pdf p.76 «{" ".join(palabras[1:1 + n])}»',
                                'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista retirada'})
                self.exportar_aristas(nodos=(FULDO, self.TERCERO))
                _, texto = self.registro_check()
                self.assertEqual(f'REGISTRO R-2026-10-01-0001: {TROZO_CITA}' in texto, problema, texto)

    def test_a_private_key_row_does_not_replace_the_overlay_rules_for_the_other_rows(self):
        # Spec 071, 21-bis: `supervivientes` reasignaba `reglas` (las `fix` del overlay) con el predicado privado de
        # una fila con clave privada (AC121): las filas que la seguían perdían las copias posibles de las `fix` (AC51) y
        # una retirada cuya arista sigue viva retipada por una `fix` pasaba por buena.
        privada = 'f0' * 32
        (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump({privada: [
            {'source_id': 'Person:otro-ficticio', 'target_id': FULDO, 'type': 'MET_WITH', 'doc': 'informe-uco.pdf'}]}),
            encoding='utf-8')
        # Su regla opaca, en el overlay: una clave privada que ninguna resuelve no se carga (AC171).
        self.regla('deny', unidad=privada, declaraciones=1, reason=A.MOTIVO_OPACO)
        A.apply(self.root, self.repo, [self.retirada()], self.tag, self.upd)
        self.cerrada_sin_aplicar()
        self.regla('fix', source_id=FULDO, target_id='Company:ficcionbar', type='PAID', corrected_type='RECEIVED',
                   doc='informe-uco.pdf', reason='063 prueba: retipo')
        self.registrar({'unidad': privada, 'dato': f'(Person:[tercero]) -[MET_WITH]-> ({FULDO}): arista retirada'},
                       {'dato': self.RETIRADA})
        self.exportar_aristas((FULDO, 'Company:ficcionbar', 'RECEIVED', self.censo()['props']))
        _, texto = self.registro_check()
        self.assertIn('REGISTRO R-2026-10-01-0002: arista «suprimida» viva', texto)

    CABECERA = '# Registro de prueba\n#     unidad: <unit_id del censo, o "" (solicitud o tanda del dueño)>\n'

    def con_cabecera(self):
        registro = self.repo / A.REG_PATH
        registro.write_text(self.CABECERA + registro.read_text(encoding='utf-8'), encoding='utf-8')

    def tercero(self, uid, pack, fin, tipo, cita, accion='deny', **campos):
        """Una arista judicial de Fuldo a un tercero ficticio, en su pack, con su decisión."""
        self.nueva_arista(uid, {'start': FULDO, 'end': fin, 'type': tipo, 'props': {
            'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf', 'source_quote': cita}}, pack)
        self.decidir(pack, uid, accion, **campos)

    def supervivientes(self):
        """Lo que sobrevive al overlay del repo de prueba en su export, como lo aplica el loader."""
        deny, fix = A._reglas_del_overlay(self.repo, self.root)
        return A._supervivientes(RC.tras_el_overlay(RC.export_vivo(self.snap)[0], [r for _, r in deny],
                                                    [r for _, r in fix]))

    def test_privatizar_only_deletes_a_rule_another_one_covers_in_any_graph(self):
        # Spec 071 AC138 (17c): `privatizar` borraba una deny porque no alcanzaba nada en el export y el censo de hoy,
        # antes de los retipos. Con PAID→RECEIVED, una deny por tipo y otra RECEIVED desde otro documento, borraba la
        # RECEIVED y la retirada reaparecía al aplicar el parche. Ahora solo borra lo que un predicado de la opaca
        # subsume en cualquier grafo, y una `fix` solo si está muerta en cualquier grafo; lo demás pasa a privado o se
        # queda, y lo que sobrevive al overlay es lo mismo con el parche que sin él.
        uid, fin = 'e7' * 32, 'Person:tercero-siete'
        cita = 'Fuldo pagó al tercero siete ficticio la comisión de la obra'
        self.tercero(uid, 'E-c0de00000001-117', fin, 'PAID', cita)
        lote = '063 auditoría escalonada 2026-09-26'
        self.regla('deny', **A.regla_de(self.censo(uid)), reason=f'{lote} [{uid[:8]}]: retirada')
        self.regla('fix', source_id=FULDO, target_id=fin, type='PAID', corrected_type='RECEIVED',
                   doc='informe-uco.pdf', reason='higiene: retipo')
        for doc in ('informe-uco.pdf', 'otro-documento.pdf'):
            self.regla('deny', source_id=FULDO, target_id=fin, type='RECEIVED', doc=doc,
                       quote_contains='la comisión de la obra', reason='higiene sin etiqueta')
        self.registrar({'unidad': uid, 'dato': f'({FULDO}) -[PAID]-> (Person:[tercero]): arista retirada'})
        self.con_cabecera()
        self.exportar_aristas((FULDO, fin, 'PAID', self.censo(uid)['props']))
        antes = self.supervivientes()
        salida = self.base / 'privatizar'
        code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', str(salida))
        self.assertEqual(code, 0, texto)
        proc = subprocess.run(['git', 'apply', str(salida / 'datos-071-e21.patch')], cwd=self.repo,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        shutil.copy(salida / 'reglas_opacas.yaml', self.root / alcance.REGLAS_OPACAS)
        self.assertEqual(self.supervivientes(), antes)
        reglas = yaml.safe_load((self.repo / A.REL_PATH).read_text(encoding='utf-8'))
        self.assertIn('RECEIVED', [regla.get('corrected_type') for regla in reglas['fix']])
        privadas = yaml.safe_load((salida / 'reglas_opacas.yaml').read_text(encoding='utf-8'))
        self.assertEqual(sorted(p['doc'] for p in privadas[uid]), ['informe-uco.pdf', 'otro-documento.pdf'])

    def test_privatizar_validates_the_decisions_and_names_only_a_pair_the_graph_publishes(self):
        # Spec 071 AC139 (17c): `privatizar` valida cargas, resoluciones y packs como los demás mandos (exit 2 sin el
        # valor); y una fila marcada nombra sus extremos solo si el grafo publica su par tras el overlay (D1): no si el
        # overlay mata su única arista; sí con una arista del par en el otro sentido y desde otro documento; o si el
        # dueño lo decide (`--nombrar`), sin fórmula, y entonces su regla se queda explícita.
        uid, fin = 'e8' * 32, 'Person:tercero-ocho'
        self.tercero(uid, 'E-c0de00000001-118', fin, 'ASSOCIATED_WITH', 'Fuldo y el tercero ocho ficticio',
                     accion='set_prop', prop='note', value='nota')
        self.registrar({'unidad': uid, 'decision': 'rectificado',
                        'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista corregida'})
        self.con_cabecera()
        base, registro = (self.repo / A.REL_PATH).read_text(encoding='utf-8'), (self.repo / A.REG_PATH).read_text(
            encoding='utf-8')
        props = self.censo(uid)['props']
        nombrada = f'({FULDO}) -[ASSOCIATED_WITH]-> ({fin}): arista corregida'
        for caso, aristas, regla, nombrar, dato in (
                ('el overlay mata su única arista', [(FULDO, fin, 'ASSOCIATED_WITH', props)], True, False, None),
                ('el par, al revés y desde otro documento',
                 [(fin, FULDO, 'MET_WITH', {**props, 'source_doc': 'otro-documento.pdf'})], False, False, nombrada),
                ('por decisión del dueño', [], False, True, nombrada)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')
                (self.repo / A.REG_PATH).write_text(registro, encoding='utf-8')
                if regla:
                    self.regla('deny', source_id=FULDO, target_id=fin, type='ASSOCIATED_WITH', doc='informe-uco.pdf',
                               reason='higiene')
                self.exportar_aristas(*aristas, nodos=(FULDO, fin))
                salida = self.base / f'privatizar-{len(caso)}'
                code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', str(salida),
                                          *(('--nombrar', 'R-2026-10-01-0001') if nombrar else ()))
                self.assertEqual(code, 0, texto)
                parche = (salida / 'datos-071-e21.patch').read_text(encoding='utf-8')
                self.assertEqual(f'+  dato: {A._escalar(dato)}' in parche if dato else 'sin tocar: 1' in texto, True,
                                 texto)
        decision_ = json.loads((self.root / 'esc/decisions/E-c0de00000001-118.json').read_text(encoding='utf-8'))
        decision_['_meta']['resolution'] = 'RESOLUCION_CENTINELA'
        (self.root / 'esc/decisions/E-c0de00000001-118.json').write_text(json.dumps(decision_), encoding='utf-8')
        code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', str(self.base / 'p'))
        self.assertEqual(code, 2, texto)
        self.assertIn(A.RESOLUCION_DESCONOCIDA, texto)
        self.assertNotIn('CENTINELA', texto)

    def test_privatizar_writes_nothing_inside_the_repo_or_its_worktrees(self):
        # Spec 071 AC141 (17c): el parche lleva lo retirado en sus líneas quitadas, las declaraciones privadas sus
        # extremos y el export todo: `--patch` y `--snapshot`, con sus enlaces resueltos, fuera del repo y de sus
        # worktrees, o exit 2 sin escribir nada. Con un registro y un export que se leen (también el de dentro del
        # repo): lo que falla es la ruta, no la lectura.
        self.exportar_aristas()
        self.registrar()
        shutil.copytree(self.snap, self.repo / 'snap')
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'add', '-A'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.email=prueba@example.com', '-c',
                        'user.name=prueba', 'commit', '-qm', 'prueba'], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'worktree', 'add', '-q', str(self.base / 'otro-worktree')],
                       check=True)
        enlace = self.base / 'enlace'
        enlace.symlink_to(self.repo / 'pipeline')
        dentro = 'dentro del repo o de uno de sus worktrees'
        # Por un enlace, se rechaza el enlace antes de mirar dónde cae (AC177).
        for caso, snapshot, patch, motivo in (
                ('--patch dentro del repo', self.snap, self.repo / 'artefactos', dentro),
                ('--patch por un enlace', self.snap, enlace / 'artefactos', 'con un enlace en su ruta'),
                ('--patch en un worktree', self.snap, self.base / 'otro-worktree/x', dentro),
                ('--snapshot dentro del repo', self.repo / 'snap', self.base / 'fuera', dentro)):
            with self.subTest(caso=caso):
                code, texto = self.correr('--snapshot', str(snapshot), 'privatizar', '--patch', str(patch))
                self.assertEqual(code, 2, texto)
                self.assertIn(motivo, texto)
                self.assertFalse(Path(patch).exists())

    def test_a_resolution_outside_its_family_stops_every_command_without_its_value(self):
        # Spec 071 AC120: una resolución se cita en los diagnósticos solo si es de su familia cerrada. Una decisión con
        # otra no vale (exit 2 en check, apply, record y materializacion, con su fichero y nunca el valor); la del censo
        # que cita el error del gate sale como «resolución con forma no reconocida».
        self.retirada()
        self.cerrada_sin_aplicar()
        self.exportar(U_EDGE2)
        decision_ = json.loads((self.root / f'esc/decisions/{P3}.json').read_text(encoding='utf-8'))
        decision_['_meta']['resolution'] = 'RESOLUCION_CENTINELA'
        (self.root / f'esc/decisions/{P3}.json').write_text(json.dumps(decision_), encoding='utf-8')
        for mando in (('check',), ('apply',), ('record',), ('materializacion', '--snapshot', str(self.snap)),
                      ('--snapshot', str(self.snap), 'privatizar', '--patch',
                       str(self.base / 'privatizar-resolucion'))):
            with self.subTest(mando=mando[0] if mando[0] != '--snapshot' else 'privatizar'):
                code, texto = self.correr(*mando)
                self.assertEqual(code, 2, texto)
                self.assertIn(f'{A.RESOLUCION_DESCONOCIDA} ({P3}.json)', texto)
                self.assertNotIn('CENTINELA', texto)
        self.assertEqual(A.nombre_de_resolucion('humano-2026-10-08'), 'humano-2026-10-08')
        self.assertEqual(A.nombre_de_resolucion('arbitraje-2026-10-03-prudente-corregido'),
                         'arbitraje-2026-10-03-prudente-corregido')
        self.assertEqual(A.nombre_de_resolucion('RESOLUCION_CENTINELA'), A.RESOLUCION_DESCONOCIDA)
        self.assertEqual(A.nombre_de_resolucion('humano-2026-10-08-corregido', A.FAMILIA_DECISION),
                         A.RESOLUCION_DESCONOCIDA)

    # --- enmienda 21-ter (spec 071 AC143–AC154; 18ª pasada de Codex) -------------------------------------------

    def test_the_own_row_of_an_anonymized_withdrawal_is_swept_but_for_its_unit(self):
        # Spec 071 AC143 (18b-1): la propia fila marcada solo deja fuera el valor de su `unidad`. Su `fuente` con el id
        # del tercero oculto la reconstruye en los dos checks y en --tree; con su nombre, en el que tiene el censo; con
        # su extremo visible, en ninguno.
        uid = self.tercero_retirado()
        dato = f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista retirada'
        propia = f'{RC.RECONSTRUIBLE} (rectificaciones/registro.yaml:rectificaciones[0])'
        # Los mensajes, por su literal: contra el commit anterior el test falla por lo que hace, no por un nombre.
        fuera = ('fila anonimizada que nombra un extremo fuera de su dato '
                 '(rectificaciones/registro.yaml:rectificaciones[0])')
        for caso, fuente, con_censo, sin_censo in (
                ('su id', f'informe-uco.pdf p.76; extremo retirado: {self.TERCERO}', True, True),
                ('su nombre', f'informe-uco.pdf p.76 ({self.NOMBRE_TERCERO})', True, False),
                ('su extremo visible', f'informe-uco.pdf p.76 ({FULDO})', False, False)):
            with self.subTest(caso=caso):
                self.registrar({'unidad': uid, 'fuente': fuente, 'dato': dato})
                self.exportar_aristas(nodos=(FULDO, self.TERCERO), nombres={self.TERCERO: self.NOMBRE_TERCERO})
                code, texto = self.registro_check()
                self.assertEqual(code, 1 if con_censo else 0, texto)
                self.assertEqual(propia in texto, con_censo, texto)
                self.assertEqual(fuera in texto, sin_censo, texto)
                code, puerta = self.materializacion()
                self.assertEqual(f'anonimato del registro: {fuera}' in puerta, sin_censo, puerta)
                _, arbol = self.arbol()
                self.assertEqual(fuera in arbol, sin_censo, arbol)
                for valor in (self.TERCERO, self.NOMBRE_TERCERO):
                    self.assertNotIn(valor, texto + puerta + arbol)

    def test_a_row_without_edge_shape_publishes_its_entity_not_what_it_hides(self):
        # AC143 con una fila marcada sin forma de arista (AC123), verificada por su declaración privada (AC134): su
        # `entidad` es lo que publica, no lo que oculta; nombrarla no la reconstruye, y nombrar al tercero, sí.
        uid = self.tercero_retirado()
        (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump({'filas': {'R-2026-10-01-0002': [
            {'source_id': FULDO, 'target_id': self.TERCERO, 'type': 'ASSOCIATED_WITH'}]}}), encoding='utf-8')
        retirada = {'unidad': uid, 'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista retirada'}
        resumen = {'unidad': '', 'decision': 'rectificado',
                   'dato': 'resumen corregido: retirada la identificación de Person:[tercero]'}
        propia = f'{RC.RECONSTRUIBLE} (rectificaciones/registro.yaml:rectificaciones[1])'
        for caso, fuente, problema in (('su entidad', f'resumen de {FULDO}', False),
                                       ('el tercero', f'resumen; antes nombraba a {self.TERCERO}', True)):
            with self.subTest(caso=caso):
                self.registrar(retirada, {**resumen, 'fuente': fuente})
                self.exportar_aristas(nodos=(FULDO, self.TERCERO), nombres={self.TERCERO: self.NOMBRE_TERCERO})
                code, texto = self.registro_check()
                self.assertEqual(code, 1 if problema else 0, texto)
                self.assertEqual(propia in texto, problema, texto)
                self.assertNotIn(self.TERCERO, texto)

    def test_the_opaque_rule_of_a_withdrawal_never_names_what_its_row_hides(self):
        # Spec 071 AC144 (18b-2): de la regla opaca de una unidad solo se dejan fuera su clave, sus declaraciones y la
        # etiqueta de su motivo; el id oculto en cualquier otro sitio suyo, también en su comentario de cola, une la
        # unidad (y con ella la fila, que publica el extremo visible y el tipo) a lo que la fila oculta. Con el censo,
        # es la fila reconstruible; el extremo visible en el comentario, no.
        uid = self.tercero_retirado()
        reglas = self.repo / A.REL_PATH
        base = reglas.read_text(encoding='utf-8')
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        linea = next(n for n, texto in enumerate(base.split('\n')) if f'unidad: {uid}' in texto)
        for caso, comentario, problema in (('el oculto', self.TERCERO, True), ('el visible', FULDO, False)):
            with self.subTest(caso=caso):
                lineas = base.split('\n')
                lineas[linea] += f'  # {comentario}'
                reglas.write_text('\n'.join(lineas), encoding='utf-8')
                code, texto = self.registro_check()
                self.assertEqual(code, 1 if problema else 0, texto)
                self.assertEqual(f'{RC.RECONSTRUIBLE} (relations/relations.yaml:deny[' in texto, problema, texto)
                self.assertNotIn(self.TERCERO, texto)

    def test_the_same_opaque_sentinel_by_tree_snapshot_and_gate(self):
        # Spec 071 AC149 (18b-7): la forma del anonimato corre con --tree, con --snapshot y en la puerta del Path A: una
        # regla opaca con un motivo libre, fuera de su fórmula cerrada (AC130), es el mismo problema en las tres.
        uid = self.tercero_retirado()
        self.motivo_opaco(uid, 'MOTIVO_LIBRE_CENTINELA')
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        linea = f'{RC.RECONSTRUIBLE} (relations/relations.yaml:deny[1]): motivo de regla opaca fuera de su fórmula'
        for modo, (code, texto) in (('--tree', self.arbol()), ('--snapshot', self.registro_check()),
                                    ('materializacion', self.materializacion())):
            with self.subTest(modo=modo):
                self.assertEqual(code, 1, texto)
                self.assertIn(linea, texto)
                self.assertNotIn('CENTINELA', texto)

    def test_a_node_without_entity_id_is_public_text_too(self):
        # Spec 071 AC147 (18b-5): todo nodo del export es texto público, también uno sin entity_id (una afirmación, un
        # documento, un fragmento): con el par no publicado de una fila anonimizada, un aviso editorial; con la huella
        # de su unidad, un problema.
        uid = self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO), nombres={self.TERCERO: self.NOMBRE_TERCERO})
        with (self.snap / 'nodes.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'labels': ['Chunk'], 'ref': '4:x:901', 'props': {
                'chunk_id': 'c-ficticio', 'text': f'{FULDO} coincidió con {self.NOMBRE_TERCERO} en un hotel'}},
                ensure_ascii=False) + '\n')
        code, texto = self.registro_check()
        self.assertEqual(code, 0, texto)
        self.assertIn(f'{RC.EDITORIAL} ({uid[:8]}, export:nodo[', texto)
        with (self.snap / 'nodes.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'labels': ['Document'], 'ref': '4:x:902', 'props': {
                'doc_id': 'doc-ficticio', 'title': f'expediente {uid[:10]}'}}) + '\n')
        code, texto = self.registro_check()
        self.assertEqual(code, 1, texto)
        self.assertIn(f'{HUELLA} (export:nodo[', texto)
        self.assertNotIn(self.NOMBRE_TERCERO, texto)

    def test_a_private_withdrawal_states_its_formula_truthfully(self):
        # Spec 071 AC150 (18b-8): la fila de una retirada sin unidad del censo, bajo una clave privada (AC121) o sin
        # unidad (por su regla pública: AC183), pasa por las fórmulas como la de una unidad del censo: «sigue publicada
        # bajo T» sin ninguna arista viva de tipo T entre su par es un problema; con una, nada. La que nombra sus
        # extremos sin unidad, en la puerta, con la regla deny explícita que la retira; la de la clave, en
        # `supervivientes`: en la puerta su clave necesita su regla opaca (AC171) y esta, una fila marcada, que no puede
        # decir que su par sigue publicado (D1).
        clave = 'b7' * 32
        predicado = {'source_id': FULDO, 'target_id': self.TERCERO, 'type': 'ASSOCIATED_WITH',
                     'doc': 'informe-uco.pdf'}
        self.regla('deny', **predicado, reason='063 prueba: retirada')
        formula = f'({FULDO}) -[ASSOCIATED_WITH]-> ({self.TERCERO}): arista retirada; {SIGUE} bajo MET_WITH'
        self.registrar({'unidad': '', 'dato': formula})
        con_clave = {'id': 'R-2026-10-01-0002', 'unidad': clave, 'decision': 'suprimido', 'entidad': FULDO,
                     'dato': formula}
        reunion = (FULDO, self.TERCERO, 'MET_WITH', {'source_kind': 'judicial', 'source_doc': 'otro-documento.pdf',
                                                    'source_quote': 'se reunieron en un hotel ficticio'})
        for caso, aristas, problema in (('sin la reunión', (), True), ('con la reunión', (reunion,), False)):
            with self.subTest(caso=caso):
                self.exportar_aristas(*aristas, nodos=(FULDO, self.TERCERO))
                code, texto = self.registro_check()
                self.assertEqual(code, 1 if problema else 0, texto)
                self.assertEqual(f'REGISTRO R-2026-10-01-0001: {SIN_SIGUE}' in texto, problema, texto)
                conn = sqlite3.connect(self.root / 'census.sqlite')
                try:
                    _, problemas, _ = RC.supervivientes([con_clave], conn, self.root, *RC.export_vivo(self.snap),
                                                        privadas={clave: [predicado]})
                finally:
                    conn.close()
                self.assertEqual(('R-2026-10-01-0002', SIN_SIGUE) in problemas, problema, problemas)

    def test_a_private_predicate_with_padded_ids_withdraws_like_the_loader(self):
        # Spec 071 AC152 (18a-2): un predicado privado con blancos alrededor de sus ids y de su tipo se prepara como la
        # deny explícita del loader (AC151): la arista que alcanza y sigue viva es una «suprimida» viva.
        clave = 'b8' * 32
        (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump({clave: [
            {'source_id': f' {FULDO} ', 'target_id': f'  {self.TERCERO} ', 'type': ' ASSOCIATED_WITH ',
             'doc': 'informe-uco.pdf'}]}), encoding='utf-8')
        self.regla('deny', unidad=clave, declaraciones=1, reason=A.MOTIVO_OPACO)
        self.registrar({'unidad': clave, 'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista retirada'})
        self.exportar_aristas((FULDO, self.TERCERO, 'ASSOCIATED_WITH', {
            'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf', 'source_quote': self.CITA_TERCERO}),
            nodos=(FULDO, self.TERCERO))
        code, texto = self.registro_check()
        self.assertEqual(code, 1, texto)
        self.assertIn('REGISTRO R-2026-10-01-0001: arista «suprimida» viva (1 copia; tolerancia 0)', texto)
        self.assertNotIn(self.TERCERO, texto)

    def test_privatizar_names_each_endpoint_once_in_the_orientation_of_its_row(self):
        # Spec 071 AC153 (18c-1): la regla puede llevar el par al revés que el dato de su fila. `[tercero]` toma el
        # extremo de la regla que no es el visible (el de la `entidad`): cada extremo una vez, en la orientación del
        # dato. El mismo extremo dos veces no es la fila de esa regla.
        dato = f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista retirada'
        for caso, inicio, fin in (('en su orden', FULDO, self.TERCERO), ('al revés', self.TERCERO, FULDO)):
            with self.subTest(caso=caso):
                extremos, nuevo = A._dato_nombrado(dato, {'type': 'ASSOCIATED_WITH', 'start': inicio, 'end': fin},
                                                   FULDO)
                self.assertEqual(extremos, [FULDO, self.TERCERO])
                self.assertEqual(nuevo, f'({FULDO}) -[ASSOCIATED_WITH]-> ({self.TERCERO}): arista retirada')
        self.assertIsNone(A._dato_nombrado(f'({FULDO}) -[ASSOCIATED_WITH]-> ({FULDO}): arista retirada',
                                           {'type': 'ASSOCIATED_WITH', 'start': self.TERCERO, 'end': FULDO}, FULDO))

    def test_privatizar_never_writes_through_a_link(self):
        # Spec 071 AC154 (18c-2): AC141 llega a la ruta final de cada salida. Un enlace en el destino, al repo o a
        # cualquier otro sitio, para con exit 2 sin escribir nada; sin él, cada fichero se escribe nuevo.
        self.tercero_retirado()
        registro = self.repo / A.REG_PATH
        cabecera = '# Registro de prueba\n#     unidad: <unit_id del censo, o "" (solicitud o tanda del dueño)>\n'
        registro.write_text(cabecera + registro.read_text(encoding='utf-8'), encoding='utf-8')
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        dentro = self.repo / A.REL_PATH
        antes = dentro.read_bytes()
        fuera = self.base / 'fuera.txt'
        fuera.write_text('previo', encoding='utf-8')
        for caso, destino in (('al repo', dentro), ('fuera del repo', fuera)):
            for nombre in ('reglas_opacas.yaml', 'datos-071-e21.patch'):
                with self.subTest(caso=caso, salida=nombre):
                    salida = self.base / f'privatizar-{len(caso)}-{nombre}'
                    salida.mkdir()
                    (salida / nombre).symlink_to(destino)
                    code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', str(salida))
                    self.assertEqual(code, 2, texto)
                    self.assertEqual(dentro.read_bytes(), antes)
                    self.assertEqual(fuera.read_text(encoding='utf-8'), 'previo')
        salida = self.base / 'privatizar-limpia'
        code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', str(salida))
        self.assertEqual(code, 0, texto)
        for nombre in ('reglas_opacas.yaml', 'datos-071-e21.patch'):
            self.assertTrue((salida / nombre).is_file() and not (salida / nombre).is_symlink())

    # --- enmienda 21-quater (spec 071 AC157–AC167; 19ª pasada de Codex) ----------------------------------------
    # Los mensajes van por su literal: contra el commit anterior, cada test falla por lo que hace, no por un nombre.

    FUERA = ('fila anonimizada que nombra un extremo fuera de su dato '
             '(rectificaciones/registro.yaml:rectificaciones[0])')

    def test_a_key_with_only_foreign_predicates_stops_every_command(self):
        # Spec 071 AC157 (19a-1, la reproducción de Codex): una clave opaca sin unidad del censo cuyo único predicado
        # privado es `ajena` no dice qué retira, y los checks no tenían qué mirar: con la arista viva en prensa y la
        # fila «suprimido» marcada, todo daba 0. Sin identidad propia la clave no vale: exit 2 en el registro, la puerta
        # y privatizar (y en el loader: `OpacasTest`).
        clave, otro = 'b9' * 32, 'Person:otro-centinela'
        ajena = {'source_id': FULDO, 'target_id': otro, 'type': 'MET_WITH', 'kinds': ['acusacion'], 'ajena': True}
        (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump({clave: [ajena]}), encoding='utf-8')
        self.regla('deny', unidad=clave, declaraciones=1, reason=A.MOTIVO_OPACO)
        self.registrar({'unidad': clave, 'dato': f'({FULDO}) -[MET_WITH]-> (Person:[tercero]): arista retirada'})
        self.con_cabecera()
        self.exportar_aristas((FULDO, otro, 'MET_WITH', {'source_kind': 'prensa', 'source_doc': None,
                                                        'source_quote': 'se vieron en la sede'}), nodos=(FULDO, otro))
        for mando, (code, texto) in (('--snapshot', self.registro_check()), ('materializacion', self.materializacion()),
                                     ('privatizar', self.correr('--snapshot', str(self.snap), 'privatizar', '--patch',
                                                                str(self.base / 'privatizar-ajena')))):
            with self.subTest(mando=mando):
                self.assertEqual(code, 2, texto)
                self.assertNotIn('centinela', texto)

    # --- enmienda 21-quinquies (spec 071 AC171; pre-hallazgo de la 20ª pasada de Codex) -------------------------

    def test_the_last_opaque_rule_gone_with_its_private_declarations_stops_every_command(self):
        # Spec 071 AC171 (20a): el lector de las reglas resolvía el fichero privado al encontrar la primera opaca, así
        # que, si desaparece la última del overlay (y quedan sus deny explícitas y sus fix), la clave que el fichero
        # sigue declarando (una retirada que ya no se aplica) pasaba. Con la raíz privada, por `--root` o por
        # QA_CENSO_ROOT, se contrasta siempre: la puerta, la materialización y privatizar paran con exit 2, sin el
        # valor; igual una entrada de `filas:` que no es una fila del registro que la admita. Sin raíz, nada que
        # contrastar; vacío, cargan como siempre.
        clave, fila = 'c3' * 32, 'R-2026-10-01-0009'
        predicado = {'source_id': FULDO, 'target_id': 'Person:otro-centinela', 'type': 'MET_WITH'}
        (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump({clave: [predicado]}), encoding='utf-8')
        base = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        self.regla('deny', unidad=clave, declaraciones=1, reason=A.MOTIVO_OPACO)
        self.registrar({'unidad': clave, 'dato': f'({FULDO}) -[MET_WITH]-> (Person:[tercero]): arista retirada'})
        self.con_cabecera()
        self.exportar_aristas(nodos=(FULDO,))
        n = iter(range(100))

        def ordenes():
            return (('--snapshot', self.registro_check()), ('materializacion', self.materializacion()),
                    ('privatizar', self.correr('--snapshot', str(self.snap), 'privatizar', '--patch',
                                               str(self.base / f'privatizar-{next(n)}'))))

        def leidas(raiz):
            return _salida_de(lambda: A._reglas_del_overlay(self.repo, raiz))
        con_su_opaca = {orden: code for orden, (code, _) in ordenes()}
        self.assertNotIn(2, con_su_opaca.values(), con_su_opaca)
        (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')  # sin la opaca; sus deny explícitas y sus fix
        for caso, contenido, motivo in (
                ('una clave', {clave: [predicado]}, 'declaraciones privadas de una clave que ninguna regla opaca '
                                                    'resuelve'),
                ('una fila', {'filas': {fila: [predicado]}}, 'declaraciones privadas de una fila que ninguna fila '
                                                             'del registro resuelve')):
            (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump(contenido), encoding='utf-8')
            for orden, (code, texto) in ordenes():
                with self.subTest(caso=caso, orden=orden):
                    self.assertEqual(code, 2, texto)
                    self.assertIn(motivo, texto)
                    for valor in (clave[:8], fila, 'centinela'):
                        self.assertNotIn(valor, texto)
            with self.subTest(caso=caso, orden='las reglas, con la raíz por QA_CENSO_ROOT'), \
                    mock.patch.dict(os.environ, {'QA_CENSO_ROOT': str(self.root)}):
                self.assertEqual(leidas(None), 2)
            with self.subTest(caso=caso, orden='las reglas, sin raíz'), mock.patch.dict(os.environ):
                os.environ.pop('QA_CENSO_ROOT', None)
                self.assertIsNone(leidas(None))
        (self.root / alcance.REGLAS_OPACAS).write_text('{}\n', encoding='utf-8')
        vacias = {orden: code for orden, (code, _) in ordenes()}
        self.assertNotIn(2, vacias.values(), vacias)

    # --- enmienda 21-sexies (spec 071 AC173–AC177; 20ª pasada de Codex) ------------------------------------------

    def tres_puertas(self, rel, contenido):
        """El fichero público `rel` con `contenido` y (árbol, puerta del registro, materialización): la salida de cada
        una."""
        path = self.repo / 'pipeline/data' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contenido, encoding='utf-8')
        try:
            return self.arbol()[1], self.registro_check()[1], self.materializacion()[1]
        finally:
            path.unlink()

    def en_las_puertas(self, salidas, rid, motivo, puertas=(0, 1, 2)):
        """Que `motivo` (de la fila o el fichero `rid`) esté en las salidas de `puertas`, y en ninguna otra."""
        lineas = (f'REGISTRO {rid}: {motivo}', f'REGISTRO {rid}: {motivo}',
                  f'MATERIALIZACION: anonimato del registro: {motivo} ({rid})')
        for n, (linea, salida) in enumerate(zip(lineas, salidas)):
            self.assertEqual(linea in salida, n in puertas, salida)

    def test_a_binary_scalar_is_swept_with_what_it_decodes_to(self):
        # Spec 071 AC173 (20b-1): un `!!binary` se lee como sus bytes; en lo leído también va lo que decodifica. La
        # huella de la fila anonimizada o su par con el tipo, en base64, son el mismo problema que escritos tal cual: la
        # huella en las tres puertas, el par en las dos con el censo. Un binario sin nada de eso, ninguno.
        uid = self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))

        def binario(texto):
            return f'items:\n- note: !!binary {base64.b64encode(texto.encode()).decode()}\n'
        rid, posicion = 'R-2026-10-01-0001', 'boe/binario.yaml:items[0]'
        self.en_las_puertas(self.tres_puertas('boe/binario.yaml', binario(uid[:8])), rid, f'{HUELLA} ({posicion})')
        salidas = self.tres_puertas('boe/binario.yaml', binario(f'{self.TERCERO} {FULDO} ASSOCIATED_WITH'))
        self.en_las_puertas(salidas, rid, f'fila anonimizada reconstruible ({posicion})', puertas=(1, 2))
        for salida in self.tres_puertas('boe/binario.yaml', binario('nada que ver con la fila')):
            self.assertNotIn('boe/binario.yaml', salida)

    def test_every_document_of_a_public_yaml_and_a_scalar_root_are_swept(self):
        # Spec 071 AC174 (20b-2): todos los documentos de un YAML, cada uno con sus secciones, y una raíz escalar, con
        # lo leído: la huella escrita con un escape (`\\u0064` es `d`) en el segundo documento o como raíz es un
        # problema en las tres puertas. Un YAML que no se compone no se da por limpio. Uno de un documento, como
        # siempre.
        uid = self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        self.assertEqual(uid[:8], 'd1d1d1d1')
        escapada, rid = '"\\u00641d1d1d1"', 'R-2026-10-01-0001'
        dos = self.tres_puertas('boe/docs.yaml', f'nota: nada\n---\nnota: {escapada}\n')
        self.en_las_puertas(dos, rid, f'{HUELLA} (boe/docs.yaml:documento 2:sección 1)')
        raiz = self.tres_puertas('boe/raiz.yaml', f'{escapada}\n')
        self.en_las_puertas(raiz, rid, f'{HUELLA} (boe/raiz.yaml:documento 1)')
        self.en_las_puertas(self.tres_puertas('boe/roto.yaml', 'nota: [sin cerrar\n'), 'boe/roto.yaml',
                            'fichero público no verificable (AC174)')
        for salida in self.tres_puertas('boe/uno.yaml', 'nota: nada\n'):
            self.assertNotIn('boe/uno.yaml', salida)

    def test_a_json_with_a_repeated_key_is_read_with_every_pair(self):
        # Spec 071 AC175 (20b-3): un JSON que repite una clave ya no se barre solo por líneas, donde un escape lo
        # esconde: cada objeto con todos sus pares, leídos. La huella con un escape, en las tres puertas; el par
        # repartido en dos valores de la misma clave, con una letra del oculto escapada, en las dos con el censo. Sin
        # claves repetidas, como siempre.
        uid = self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        self.assertEqual(uid[:8], 'd1d1d1d1')
        rid = 'R-2026-10-01-0001'
        huella = self.tres_puertas('boe/repetida.json', '[{"note": "\\u00641d1d1d1", "note": "ok"}]')
        self.en_las_puertas(huella, rid, f'{HUELLA} (boe/repetida.json:[0])')
        oculto = self.TERCERO.replace('t', '\\u0074', 1)
        par = self.tres_puertas('boe/repetida.json', f'[{{"nota": "{oculto}", "nota": "{FULDO} ASSOCIATED_WITH"}}]')
        self.en_las_puertas(par, rid, 'fila anonimizada reconstruible (boe/repetida.json:[0])', puertas=(1, 2))
        for salida in self.tres_puertas('boe/sin-repetidas.json', '[{"note": "ok"}]'):
            self.assertNotIn('boe/sin-repetidas.json', salida)

    def test_privatizar_takes_its_output_path_as_written(self):
        # Spec 071 AC177 (20c-1): `--patch` se valida, se comprueba fuera del repo y se abre con la misma ruta, la que
        # se escribe. Un `..` tras un enlace (el intérprete del venv apunta fuera del repo) la sacaba del repo al
        # resolverla y la dejaba dentro al abrirla (`pipeline/data`); un `.` o un `..`, exit 2 antes de abrir nada. En
        # un disco que no distingue mayúsculas, el repo escrito de otra manera es el repo. Una ruta de fuera, limpia, se
        # escribe.
        self.tercero_retirado()
        self.con_cabecera()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        interprete = self.base / 'fuera/x/y/python'
        interprete.parent.mkdir(parents=True)
        interprete.write_text('', encoding='utf-8')
        venv = self.repo / 'pipeline/.venv/bin'
        venv.mkdir(parents=True)
        (venv / 'python').symlink_to(interprete)
        datos = sorted(p for p in (self.repo / 'pipeline/data').rglob('*'))
        casos = [('un «..» tras el enlace del venv', f'{self.repo}/pipeline/.venv/bin/python/../../../data',
                  'con un componente «.» o «..»'),
                 ('un «.»', f'{self.base}/./privatizar-punto', 'con un componente «.» o «..»')]
        if Path(str(self.repo).upper()).exists():
            casos.append(('el repo en mayúsculas', f'{str(self.repo).upper()}/artefactos',
                          'dentro del repo o de uno de sus worktrees'))
        for caso, patch, motivo in casos:
            with self.subTest(caso=caso):
                code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', patch)
                self.assertEqual(code, 2, texto)
                self.assertIn(motivo, texto)
                self.assertEqual(sorted(p for p in (self.repo / 'pipeline/data').rglob('*')), datos)
                self.assertFalse((self.base / 'fuera/data').exists() or (self.base / 'privatizar-punto').exists())
        limpia = self.base / 'privatizar-limpia'
        code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', str(limpia))
        self.assertEqual(code, 0, texto)
        self.assertTrue((limpia / 'datos-071-e21.patch').is_file())

    def test_a_marked_row_without_a_verifiable_predicate_is_never_skipped(self):
        # AC157: lo que los dos checks hacen con una fila marcada cuya clave no deja ningún predicado suyo (solo
        # `ajena`; el fichero privado que la declara ya no vale, esto es lo que hacen con lo que les llegue): la dicen
        # sin comprobar, nunca la dan por buena.
        clave, rid = 'b8' * 32, 'R-2026-10-01-0001'
        ajena = {'source_id': FULDO, 'target_id': 'Person:otro', 'type': 'MET_WITH', 'ajena': True}
        fila = {'id': rid, 'unidad': clave, 'decision': 'suprimido', 'entidad': FULDO,
                'dato': f'({FULDO}) -[MET_WITH]-> (Person:[tercero]): arista retirada'}
        sin_predicado = (rid, 'retirada sin predicado verificable')
        problemas, _ = RC.reconstrucciones([fila], {}, {clave: [ajena]}, [], {}, {})
        self.assertIn(sin_predicado, problemas)
        conn = sqlite3.connect(self.root / 'census.sqlite')
        try:
            _, problemas, _ = RC.supervivientes([fila], conn, self.root, {}, set(), privadas={clave: [ajena]})
        finally:
            conn.close()
        self.assertIn(sin_predicado, problemas)

    def test_a_free_dato_or_an_incoherent_marker_never_skips_the_check(self):
        # Spec 071 AC161 (19b-1): los extremos visibles de una fila marcada salen solo de las posiciones de su dato y de
        # su entidad. Un dato libre que nombra al oculto lo revela: con el censo, la fila es reconstruible; sin él, otro
        # extremo fuera de sus posiciones, en los tres puntos de entrada. Y una fila marcada que no oculta ninguno de
        # sus extremos es incoherente: no se salta.
        uid = self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        libre = f'Retirada de [tercero] respecto a {FULDO}; anteriormente {self.TERCERO}'
        self.registrar({'unidad': uid, 'dato': libre})
        code, texto = self.registro_check()
        self.assertEqual(code, 1, texto)
        self.assertIn('fila anonimizada reconstruible (rectificaciones/registro.yaml:rectificaciones[0])', texto)
        self.assertIn(self.FUERA, texto)
        self.assertIn(self.FUERA, self.arbol()[1])
        self.assertIn(f'anonimato del registro: {self.FUERA}', self.materializacion()[1])
        incoherente = f'({FULDO}) -[ASSOCIATED_WITH]-> ({self.TERCERO}): arista retirada [tercero]'
        self.registrar({'unidad': uid, 'dato': incoherente})
        code, texto = self.registro_check()
        self.assertEqual(code, 1, texto)
        self.assertIn('REGISTRO R-2026-10-01-0001: marcador incoherente: no oculta nada', texto)
        self.assertNotIn(self.TERCERO, texto)

    def test_the_key_of_a_public_map_goes_with_what_it_says(self):
        # Spec 071 AC162 (19b-2): cada entrada de un YAML lleva las claves de la sección en que está. Un mapa cuya clave
        # es el oculto y cuyo valor dice el visible y el tipo reconstruye la retirada, como lista o como mapa; en un
        # JSON ya iba la clave (AC145).
        self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        dice = f'{FULDO} ASSOCIATED_WITH'
        casos = (('mapa→lista en YAML', 'boe/mapa.yaml', yaml.safe_dump({self.TERCERO: [{'note': dice}]}),
                  'boe/mapa.yaml:sección 1[0]'),
                 ('mapa→mapa en YAML', 'boe/mapa.yaml', yaml.safe_dump({self.TERCERO: {'note': dice}}),
                  'boe/mapa.yaml:sección 1'),
                 ('mapa→lista en JSON', 'boe/mapa.json', json.dumps({self.TERCERO: [{'note': dice}]}),
                  'boe/mapa.json:clave 1[0]'))
        for caso, rel, contenido, posicion in casos:
            with self.subTest(caso=caso):
                for viejo in (self.repo / 'pipeline/data/boe').glob('mapa.*'):
                    viejo.unlink()
                path = self.repo / 'pipeline/data' / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contenido, encoding='utf-8')
                code, texto = self.registro_check()
                self.assertEqual(code, 1, texto)
                self.assertIn(f'REGISTRO R-2026-10-01-0001: fila anonimizada reconstruible ({posicion})', texto)

    def test_the_gate_finds_with_the_census_what_the_snapshot_finds(self):
        # Spec 071 AC169 (amplía AC149): `materializacion` tiene el censo y busca también las reconstrucciones en las
        # entradas públicas y en las del export, con el mismo resultado que `--snapshot`: un mapa público cuya clave es
        # el oculto y cuyo valor dice el visible y el tipo, como lista o como mapa, es el mismo problema en las dos; y
        # un texto curado con el par, el mismo aviso. (El árbol, sin censo, no sabe qué oculta una fila.)
        uid = self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO), nombres={self.TERCERO: self.NOMBRE_TERCERO})
        mapa = self.repo / 'pipeline/data/boe/mapa.yaml'
        mapa.parent.mkdir(parents=True, exist_ok=True)
        dice = f'{FULDO} ASSOCIATED_WITH'
        casos = (('mapa→lista', {self.TERCERO: [{'note': dice}]}, 'boe/mapa.yaml:sección 1[0]'),
                 ('mapa→mapa', {self.TERCERO: {'note': dice}}, 'boe/mapa.yaml:sección 1'))
        for caso, contenido, posicion in casos:
            with self.subTest(caso=caso):
                mapa.write_text(yaml.safe_dump(contenido), encoding='utf-8')
                linea = f'fila anonimizada reconstruible ({posicion})'
                _, registro = self.registro_check()
                code, puerta = self.materializacion()
                self.assertIn(f'REGISTRO R-2026-10-01-0001: {linea}', registro)
                self.assertIn(f'MATERIALIZACION: anonimato del registro: {linea} (R-2026-10-01-0001)', puerta)
                self.assertEqual(code, 1, puerta)
        mapa.unlink()
        resumen = self.repo / A.SUM_PATH
        resumen.write_text(resumen.read_text(encoding='utf-8') + f'- entity_id: {FULDO}\n  name: Fuldo\n'
                           f'  resumen: Se reunió con {self.NOMBRE_TERCERO} en un hotel ficticio.\n'
                           f"  updated: '2026-10-01'\n  reason: prueba\n", encoding='utf-8')
        aviso = (f'texto público curado con el par de una fila anonimizada (decisión editorial del dueño) ({uid[:8]}, '
                 'context/summaries_qa061.yaml:summaries[1])')
        _, registro = self.registro_check()
        _, puerta = self.materializacion()
        self.assertIn(f'registro-check: aviso R-2026-10-01-0001: {aviso}', registro)
        self.assertIn(f'materializacion: anonimato del registro: {aviso} (R-2026-10-01-0001)', puerta)
        self.assertNotIn(self.NOMBRE_TERCERO, puerta)

    def test_escapes_comments_and_keys_of_the_row_at_every_entry_point(self):
        # Spec 071 AC163 y AC164 (19b-3 y 19b-4), en los tres puntos de entrada (AC149): la huella de la unidad escrita
        # con un escape (`\u0064` es `d`) en la fuente de su fila; el oculto en un comentario de la fila; y el oculto
        # como una clave suya.
        uid = self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        registro = self.repo / A.REG_PATH
        base = registro.read_text(encoding='utf-8')
        fuente = '  fuente: informe-uco.pdf p.76\n'
        self.assertIn(fuente, base)
        self.assertEqual(uid[:8], 'd1d1d1d1')
        huella = ('huella de una fila anonimizada fuera de su regla opaca '
                  '(rectificaciones/registro.yaml:rectificaciones[0])')
        casos = (('su huella con un escape', base.replace(fuente, '  fuente: "informe-uco.pdf \\u00641d1d1d1"\n'),
                  huella),
                 ('el oculto en un comentario', base.replace(fuente, f'{fuente[:-1]}  # {self.TERCERO}\n'), self.FUERA),
                 ('el oculto como clave', base.replace(fuente, f'{fuente}  {self.TERCERO}: x\n'), self.FUERA))
        for caso, texto_registro, linea in casos:
            registro.write_text(texto_registro, encoding='utf-8')
            for modo, (_, salida) in (('--tree', self.arbol()), ('--snapshot', self.registro_check()),
                                      ('materializacion', self.materializacion())):
                with self.subTest(caso=caso, modo=modo):
                    self.assertIn(linea, salida)
                    self.assertNotIn(self.TERCERO, salida)

    def test_privatizar_sees_the_withdrawal_its_opaque_rule_would_widen(self):
        # Spec 071 AC158 (19a-2): la reproducción de Codex. La unidad del censo es una PAID y su deny etiquetada es
        # ASSOCIATED_WITH; dos fix retipan MET_WITH→PAID y PAID→ASSOCIATED_WITH en los dos sentidos. El loader no
        # encadena retipos: una MET_WITH del mismo documento y cita queda PAID y esa deny no la retira, y la opaca de la
        # unidad (que resuelve también la PAID del censo) sí: si esa MET_WITH es otra arista del censo, la retirada se
        # ampliaría y privatizar para (antes la simulación la llevaba hasta ASSOCIATED_WITH y lo certificaba). Si está
        # en el export, el par sigue publicado: la fila nombra sus extremos (D1) y la regla se queda explícita.
        uid, gemela, otro = 'd7' * 32, 'd8' * 32, 'Person:otro-ficticio'
        props = {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf',
                 'source_quote': 'Fuldo y el otro ficticio coincidieron en la sede'}
        self.nueva_arista(uid, {'start': FULDO, 'end': otro, 'type': 'PAID', 'props': props}, 'E-c0de00000001-117')
        self.decidir('E-c0de00000001-117', uid, 'deny')
        for inicio, fin in ((FULDO, otro), (otro, FULDO)):
            self.regla('fix', source_id=inicio, target_id=fin, type='MET_WITH', corrected_type='PAID', reason='retipo')
            self.regla('fix', source_id=inicio, target_id=fin, type='PAID', corrected_type='ASSOCIATED_WITH',
                       reason='retipo')
        self.regla('deny', source_id=FULDO, target_id=otro, type='ASSOCIATED_WITH',
                   reason=f'063 auditoría escalonada 2026-09-26 [{uid[:8]}]: retirada')
        self.registrar({'unidad': uid, 'dato': f'({FULDO}) -[PAID]-> (Person:[tercero]): arista retirada'})
        self.con_cabecera()
        reglas, registro = (self.repo / A.REL_PATH).read_text(encoding='utf-8'), \
            (self.repo / A.REG_PATH).read_text(encoding='utf-8')
        # Otra arista del censo, MET_WITH, que el export no publica.
        self.nueva_arista(gemela, {'start': FULDO, 'end': otro, 'type': 'MET_WITH', 'props': props},
                          'E-c0de00000001-118')
        self.exportar_aristas(nodos=(FULDO, otro))
        code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', str(self.base / 'censo'))
        self.assertEqual(code, 2, texto)
        self.assertIn('privatizar: el parche cambia lo que sobrevive al overlay', texto)
        self.assertNotIn('otro-ficticio', texto)
        # La misma MET_WITH en el export: el par queda publicado (PAID) y la fila lo nombra, sin opaca.
        self.exportar_aristas((FULDO, otro, 'MET_WITH', props), nodos=(FULDO, otro))
        salida = self.base / 'export'
        code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', str(salida))
        self.assertEqual(code, 0, texto)
        subprocess.run(['git', 'apply', str(salida / 'datos-071-e21.patch')], cwd=self.repo, check=True)
        self.assertNotIn(f'unidad: {uid}', (self.repo / A.REL_PATH).read_text(encoding='utf-8'))
        fila = yaml.safe_load((self.repo / A.REG_PATH).read_text(encoding='utf-8'))['rectificaciones'][0]
        self.assertEqual(fila['dato'], f'({FULDO}) -[PAID]-> ({otro}): {RC.DUPLICADA}')
        (self.repo / A.REL_PATH).write_text(reglas, encoding='utf-8')
        (self.repo / A.REG_PATH).write_text(registro, encoding='utf-8')

    def test_privatizar_writes_nothing_through_a_linked_directory_and_never_half_a_pair(self):
        # Spec 071 AC167 (19c-3): un enlace en cualquier componente de la ruta de la salida (el propio directorio) para
        # con exit 2 sin escribir nada; y los dos ficheros se validan antes de escribir ninguno: con el segundo
        # enlazado, el parche que ya había se queda intacto.
        self.tercero_retirado()
        self.con_cabecera()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        fuera = self.base / 'fuera'
        fuera.mkdir()
        enlazada = self.base / 'salida-enlazada'
        enlazada.symlink_to(fuera, target_is_directory=True)
        code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', str(enlazada))
        self.assertEqual(code, 2, texto)
        self.assertEqual(list(fuera.iterdir()), [])
        salida = self.base / 'salida'
        salida.mkdir()
        (salida / 'datos-071-e21.patch').write_text('parche previo', encoding='utf-8')
        (salida / 'reglas_opacas.yaml').symlink_to(self.base / 'otro-destino.yaml')
        code, texto = self.correr('--snapshot', str(self.snap), 'privatizar', '--patch', str(salida))
        self.assertEqual(code, 2, texto)
        self.assertEqual((salida / 'datos-071-e21.patch').read_text(encoding='utf-8'), 'parche previo')
        self.assertFalse((self.base / 'otro-destino.yaml').exists())
        self.assertEqual(sorted(p.name for p in salida.iterdir()), ['datos-071-e21.patch', 'reglas_opacas.yaml'])

    # --- enmienda 23 (spec 071 AC183–AC189; 21ª pasada de Codex) -----------------------------------------------------

    def las_dos_puertas(self):
        """{puerta: (exit, salida)} de la puerta del registro y de la materialización sobre lo mismo."""
        return {'registro': self.registro_check(), 'materializacion': self.materializacion()}

    def test_a_lost_declaration_of_a_marked_row_without_unit_stops_both_gates(self):
        # Spec 071 AC183 (21a-1, los centinelas de Codex): cada fila sin unidad con marcador tiene su declaración
        # privada en `filas:`. Perderlas todas (sin fichero, vacío, `{}` o `filas: {}`) o la de una sola de las dos para
        # la puerta del registro y la materialización con exit 2, el motivo y ningún valor; con las dos, corren.
        predicado = {'source_id': FULDO, 'target_id': self.TERCERO, 'type': 'ASSOCIATED_WITH'}
        dato = f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista retirada'
        self.registrar({'unidad': '', 'dato': dato}, {'unidad': '', 'dato': dato})
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        privado, filas = self.root / alcance.REGLAS_OPACAS, ('R-2026-10-01-0001', 'R-2026-10-01-0002')
        motivo = 'filas del registro que admiten una declaración privada sin la suya'
        for caso, contenido in (('sin fichero', None), ('vacío', ''), ('{}', '{}\n'), ('filas: {}', 'filas: {}\n'),
                                ('la de una sola', yaml.safe_dump({'filas': {filas[0]: [predicado]}}))):
            privado.unlink(missing_ok=True)
            if contenido is not None:
                privado.write_text(contenido, encoding='utf-8')
            for puerta, (code, texto) in self.las_dos_puertas().items():
                with self.subTest(caso=caso, puerta=puerta):
                    self.assertEqual(code, 2, texto)
                    self.assertIn(motivo, texto)
                    for valor in (self.TERCERO, *filas):
                        self.assertNotIn(valor, texto)
        privado.write_text(yaml.safe_dump({'filas': {fila: [predicado] for fila in filas}}), encoding='utf-8')
        for puerta, (code, texto) in self.las_dos_puertas().items():
            with self.subTest(caso='las dos', puerta=puerta):
                self.assertNotEqual(code, 2, texto)
                self.assertNotIn(motivo, texto)

    def test_both_gates_check_a_withdrawal_without_unit_alike(self):
        # Spec 071 AC183 y AC184 (21a-2): la materialización comprueba las «suprimido» sin unidad con la misma función
        # que la puerta del registro, y las dos dicen lo mismo con el mismo exit. Con marcador, con su declaración de
        # `filas:`. Sin él, la de una arista con las reglas deny explícitas del overlay que la retiran (sin ninguna,
        # «retirada sin regla pública») y la fórmula cerrada de un seudónimo, que comprueba su política (spec 070).
        # Viva en el export, «arista suprimida viva».
        predicado = {'source_id': FULDO, 'target_id': self.TERCERO, 'type': 'ASSOCIATED_WITH'}
        viva = (FULDO, self.TERCERO, 'ASSOCIATED_WITH', {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf',
                                                         'source_quote': 'cita ficticia de la reunión'})
        marcada = f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista retirada'
        nombrada = f'({FULDO}) -[ASSOCIATED_WITH]-> ({self.TERCERO}): arista retirada'
        seudonimo = '(Person:fulano): relación institucional retirada (seudónimo judicial)'
        base = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        viva_linea = 'arista «suprimida» viva (1 copia; tolerancia 0)'
        for caso, dato, declarada, regla, aristas, motivo in (
                ('marcada, viva', marcada, True, False, [viva], viva_linea),
                ('marcada, retirada', marcada, True, False, [], None),
                ('con su regla, viva', nombrada, False, True, [viva], viva_linea),
                ('con su regla, retirada', nombrada, False, True, [], None),
                ('sin regla', nombrada, False, False, [], 'retirada sin regla pública'),
                ('un seudónimo', seudonimo, False, False, [], None)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')
                if regla:
                    self.regla('deny', **predicado, doc='informe-uco.pdf', reason='063 prueba: retirada')
                (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump(
                    {'filas': {'R-2026-10-01-0001': [predicado]}} if declarada else {}), encoding='utf-8')
                self.registrar({'unidad': '', 'dato': dato})
                self.exportar_aristas(*aristas, nodos=(FULDO, self.TERCERO))
                puertas = self.las_dos_puertas()
                self.assertEqual(puertas['registro'][0], puertas['materializacion'][0], puertas)
                lineas = (f'REGISTRO R-2026-10-01-0001: {motivo}', f'MATERIALIZACION: {motivo} (R-2026-10-01-0001)')
                for linea, (code, texto) in zip(lineas, puertas.values()):
                    self.assertEqual(linea in texto, motivo is not None, texto)
                    if motivo is None:
                        self.assertNotIn('R-2026-10-01-0001', texto)

    def test_no_command_line_narrows_the_coverage_of_the_private_file(self):
        # Spec 071 AC183: solo quien escribe las declaraciones (el apply, `check`, `privatizar`) contrasta el fichero
        # privado en una sola dirección, por dentro; ninguna orden lo ofrece, y el loader, la puerta del registro y la
        # materialización lo contrastan siempre en las dos.
        ayudas = []
        for main, argv in ((RC.main, ['--help']), (A.main, ['--help']),
                           *((A.main, ['--root', str(self.root), orden, '--help'])
                             for orden in ('check', 'apply', 'record', 'materializacion', 'privatizar')),
                           (lambda argv: relations_loader.main(), None)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), mock.patch.object(sys, 'argv', ['relations_loader', '--help']), \
                    self.assertRaises(SystemExit) as salida:
                main(argv)
            self.assertEqual(salida.exception.code, 0)
            ayudas.append(out.getvalue())
        self.assertTrue(all('--' in ayuda for ayuda in ayudas), ayudas)
        self.assertFalse(any('cobertura' in ayuda.lower() or 'coverage' in ayuda.lower() for ayuda in ayudas))
        dato = f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista retirada'
        self.registrar({'unidad': '', 'dato': dato})
        (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump({'filas': {'R-2026-10-01-0001': [
            {'source_id': FULDO, 'target_id': self.TERCERO, 'type': 'ASSOCIATED_WITH'}]}}), encoding='utf-8')
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        original = alcance.Opacas.verificar
        for entrada, correr in (
                ('loader', lambda: relations_loader._prepare_deny([], str(self.root), completo=True,
                                                                  registro=self.repo / A.REG_PATH)),
                ('registro', self.registro_check), ('materializacion', self.materializacion)):
            llamadas = []

            def espia(opacas, claves, registro, *resto, **nombrados):
                llamadas.append(nombrados.get('cobertura', resto[0] if resto else True))
                return original(opacas, claves, registro, *resto, **nombrados)
            with self.subTest(entrada=entrada), mock.patch.object(alcance.Opacas, 'verificar', espia):
                correr()
                self.assertTrue(llamadas)
                self.assertTrue(all(llamadas), llamadas)

    def test_each_document_of_a_yaml_is_built_apart_and_one_that_does_not_build_is_not_clean(self):
        # Spec 071 AC185 (21b-1): los valores de cada documento se construyen aparte, y cada uno resuelve sus nombres
        # por su estructura: el par oculto por los nombres de sus dos extremos en un segundo documento es tan
        # reconstruible como en el primero. Un documento que no se construye (una etiqueta desconocida, un `!!binary`
        # que no es base64) no se da por limpio en ninguna puerta.
        self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        rid = 'R-2026-10-01-0001'
        relaciones = ('relations:\n- source: Fuldo Fíctez\n  target: Tercero Ficticio\n  type: ASSOCIATED_WITH\n'
                      '  reason: higiene\n')
        for caso, contenido, posicion in (('un documento', relaciones, 'relations[0]'),
                                          ('tras otro', f'x: benigno\n---\n{relaciones}', 'documento 2:relations[0]')):
            with self.subTest(caso=caso):
                self.en_las_puertas(self.tres_puertas('boe/nombres.yaml', contenido), rid,
                                    f'fila anonimizada reconstruible (boe/nombres.yaml:{posicion})', puertas=(1, 2))
        for caso, prefijo in (('una etiqueta desconocida', 'x: !x benigno\n'),
                              ('un binario que no es base64', 'x: !!binary abc\n')):
            with self.subTest(caso=caso):
                self.en_las_puertas(self.tres_puertas('boe/roto.yaml', prefijo + relaciones), 'boe/roto.yaml',
                                    NO_VERIFICABLE_FICHERO)

    def test_every_pair_of_a_repeated_json_key_keeps_the_identity_its_key_gives(self):
        # Spec 071 AC186 (21b-2): un objeto JSON que repite `target` se lee con cada par en el contexto de su clave: el
        # primer `target` (el oculto, por su nombre) sigue siendo un extremo aunque el segundo lo pise, también con la
        # clave escrita con un escape y en una lista anidada. Sin repetir la clave, como siempre.
        self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        rid, motivo = 'R-2026-10-01-0001', 'fila anonimizada reconstruible (boe/repetida.json:clave 1[0])'

        def objeto(*pares):
            return '{"entries": [[{' + ', '.join(f'"{k}": "{v}"' for k, v in pares) + '}]]}'
        par = (('source', 'Fuldo Fíctez'), ('type', 'ASSOCIATED_WITH'))
        for caso, pares in (('sin repetir', (*par, ('target', 'Tercero Ficticio'))),
                            ('el oculto, pisado', (*par, ('target', 'Tercero Ficticio'), ('target', 'Otro Ficticio'))),
                            ('con la clave escapada', (*par, ('\\u0074arget', 'Tercero Ficticio'),
                                                       ('target', 'Otro Ficticio')))):
            with self.subTest(caso=caso):
                self.en_las_puertas(self.tres_puertas('boe/repetida.json', objeto(*pares)), rid, motivo,
                                    puertas=(1, 2))

    def test_the_withdrawn_quote_is_looked_for_in_everything_the_row_carries(self):
        # Spec 071 AC187 (21b-3): ninguna fila marcada lleva un trozo de seis palabras de su cita retirada en ninguna
        # parte: un campo anidado, una clave, un comentario o un `!!binary` (lo que decodifica). En las dos puertas con
        # el censo (el árbol no tiene la cita); cinco palabras no son un trozo.
        uid = self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        palabras = self.CITA_TERCERO.split()
        registro = self.repo / A.REG_PATH
        fila = registro.read_text(encoding='utf-8')
        self.assertTrue(fila.endswith('\n'))
        seis, cinco = ' '.join(palabras[5:11]), ' '.join(palabras[6:11])
        for caso, extra, problema in (
                ('anidado', f'  extra:\n    nota: {seis}\n', True),
                ('una clave', f'  {seis}: x\n', True),
                ('un comentario', f'  # {seis}\n', True),
                ('un binario', f'  nota: !!binary {base64.b64encode(seis.encode()).decode()}\n', True),
                ('cinco palabras, en un binario', f'  nota: !!binary {base64.b64encode(cinco.encode()).decode()}\n',
                 False)):
            with self.subTest(caso=caso):
                registro.write_text(fila + extra, encoding='utf-8')
                arbol, *con_censo = self.arbol()[1], self.registro_check()[1], self.materializacion()[1]
                self.assertNotIn(TROZO_CITA, arbol)
                self.assertEqual(f'REGISTRO R-2026-10-01-0001: {TROZO_CITA}' in con_censo[0], problema, con_censo[0])
                self.assertEqual(f'MATERIALIZACION: anonimato del registro: {TROZO_CITA} (R-2026-10-01-0001)'
                                 in con_censo[1], problema, con_censo[1])
                self.assertNotIn(uid, con_censo[0] + con_censo[1])

    def test_a_binary_is_read_by_its_bom_or_as_strict_utf8(self):
        # Spec 071 AC188 (21b-4): un `!!binary` se decodifica por su BOM (UTF-16 o UTF-32, en los dos órdenes de
        # bytes; UTF-8) y sin él como UTF-8 estricto: la huella de la fila anonimizada en UTF-16 con BOM es un problema
        # en las tres puertas. Lo que no se decodifica sin pérdidas, o pasa del tope, no se da por limpio.
        uid = self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))

        def binario(datos):
            return f'items:\n- note: !!binary {base64.b64encode(datos).decode()}\n'
        rid, posicion = 'R-2026-10-01-0001', 'boe/bom.yaml:items[0]'
        for caso, datos in (('UTF-16 LE', codecs.BOM_UTF16_LE + uid[:8].encode('utf-16-le')),
                            ('UTF-16 BE', codecs.BOM_UTF16_BE + uid[:8].encode('utf-16-be')),
                            ('UTF-32 LE', codecs.BOM_UTF32_LE + uid[:8].encode('utf-32-le')),
                            ('UTF-32 BE', codecs.BOM_UTF32_BE + uid[:8].encode('utf-32-be')),
                            ('UTF-8 con BOM', codecs.BOM_UTF8 + uid[:8].encode())):
            with self.subTest(caso=caso):
                self.en_las_puertas(self.tres_puertas('boe/bom.yaml', binario(datos)), rid, f'{HUELLA} ({posicion})')
        self.assertEqual(base64.b64encode(codecs.BOM_UTF16_LE + 'abcdef01'.encode('utf-16-le')).decode(),
                         '//5hAGIAYwBkAGUAZgAwADEA')
        # Sin BOM, un texto en UTF-16 o UTF-32 de solo ASCII es UTF-8 válido con NUL intercalados: se barre también sin
        # ellos. Uno con otros caracteres no es UTF-8 válido.
        for caso, datos in (('UTF-16 sin BOM', uid[:8].encode('utf-16-be')),
                            ('UTF-32 sin BOM', uid[:8].encode('utf-32-le'))):
            with self.subTest(caso=caso):
                self.en_las_puertas(self.tres_puertas('boe/bom.yaml', binario(datos)), rid, f'{HUELLA} ({posicion})')
        for caso, datos in (('UTF-8 inválido', b'\xc3\x28' + uid[:8].encode()),
                            ('UTF-16 sin BOM, con una tilde', f'í {uid[:8]}'.encode('utf-16-le'))):
            with self.subTest(caso=caso):
                self.en_las_puertas(self.tres_puertas('boe/bom.yaml', binario(datos)), 'boe/bom.yaml',
                                    NO_VERIFICABLE_FICHERO)
        with mock.patch.object(RC, 'BINARIO_MAX', 4, create=True):
            self.en_las_puertas(self.tres_puertas('boe/bom.yaml', binario(b'texto de mas de cuatro bytes')),
                                'boe/bom.yaml', NO_VERIFICABLE_FICHERO)

    def test_a_tagged_scalar_is_swept_with_the_value_it_builds(self):
        # Spec 071 AC189 (21b-5): además de su texto, un escalar etiquetado se barre con el valor que construye el
        # cargador: `!!int 12_345_678` y `0xbc614e` son 12345678, la huella de la fila anonimizada, en las tres
        # puertas; `!!str` ya lo era. Otro número, nada.
        uid = self.tercero_retirado('12345678' + 'a' * 56)
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        rid, posicion = 'R-2026-10-01-0001', 'boe/numeros.yaml:items[0]'
        for caso, valor in (('un entero con guiones bajos', '!!int 12_345_678'), ('en hexadecimal', '0xbc614e'),
                            ('un real', '!!float 12345678'), ('como texto', '!!str 12345678')):
            with self.subTest(caso=caso):
                self.en_las_puertas(self.tres_puertas('boe/numeros.yaml', f'items:\n- note: {valor}\n'), rid,
                                    f'{HUELLA} ({posicion})')
        for salida in self.tres_puertas('boe/numeros.yaml', 'items:\n- note: !!int 12_345_679\n'):
            self.assertNotIn('boe/numeros.yaml', salida)
        self.assertNotIn(uid, ''.join(self.tres_puertas('boe/numeros.yaml', 'items:\n- note: 0xbc614e\n')))

    def test_an_opaque_rule_spanning_a_migration_needs_one_predicate_that_reaches_its_edge(self):
        # Decisión del 08-10-2026 sobre AC157 (aplicar11): las declaraciones de una clave pueden abarcar una migración,
        # el par del censo (el que alcanza hoy la arista) y el de después (con un extremo que el export aún no tiene):
        # este no es incompatible mientras otro predicado de la misma regla alcance la arista del censo, y la
        # materialización lo deja en un aviso. Uno con sus dos extremos vivos sigue siendo incompatible: el predicado
        # del censo la alcanza siempre y no puede tapar uno que retiraría otra arista.
        uid = self.tercero_retirado()
        nuevo, vivo_otro = 'Person:fuldo-migrado', 'Person:otro-vivo'
        regla, reglas = f'  - unidad: {uid}\n', self.repo / A.REL_PATH
        texto = reglas.read_text(encoding='utf-8')
        self.assertEqual(texto.count(regla), 1)
        reglas.write_text(texto.replace(regla, f'{regla}    declaraciones: 1\n'), encoding='utf-8')
        incompatible = 'regla incompatible con la decisión vigente (relations.yaml:deny['
        for caso, origen, nodos, problema in (('el de después de una migración', nuevo, (FULDO, self.TERCERO), False),
                                              ('el de otro nodo vivo', vivo_otro, (FULDO, self.TERCERO, vivo_otro),
                                               True)):
            with self.subTest(caso=caso):
                (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump({uid: [
                    {'source_id': origen, 'target_id': self.TERCERO, 'type': 'ASSOCIATED_WITH'}]}), encoding='utf-8')
                self.exportar_aristas(nodos=nodos)
                code, texto = self.materializacion()
                self.assertEqual(incompatible in texto, problema, texto)
                self.assertEqual('par de después de una migración, sin su nodo en el export' in texto, not problema,
                                 texto)
                self.assertNotIn(origen, texto)

    # --- enmienda 24 y 24 bis (spec 071 AC194–AC199; 22ª pasada de Codex) -----------------------------------------
    # Los mensajes, por su literal: contra el commit anterior, cada test falla por lo que hace, no por un nombre.

    CORTO = 'fila anonimizada reconstruible por un nombre de una palabra junto al otro extremo'
    EDITORIAL = 'texto público curado con el par de una fila anonimizada (decisión editorial del dueño)'

    def test_a_withdrawal_that_a_fix_carries_to_another_type_is_alive_in_both_gates(self):
        # Spec 071 AC194 (22a-1, la reproducción de Codex): la retirada sin unidad «(A) -[ASSOCIATED_WITH]-> (B)», con
        # su deny y un fix ASSOCIATED_WITH→MET_WITH del mismo par: el loader retipa antes de la deny, que ya no la
        # alcanza. Su única copia, bajo MET_WITH con el mismo documento y la misma cita, está viva en las dos puertas.
        # Sin el fix, o con una deny de otro pasaje, esa copia es otra arista.
        par = {'source_id': FULDO, 'target_id': self.TERCERO}
        copia = (FULDO, self.TERCERO, 'MET_WITH', {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf',
                                                     'source_quote': self.CITA_TERCERO})
        base = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        viva = 'arista «suprimida» viva (1 copia; tolerancia 0)'
        for caso, fix, pasaje, problema in (('la deny y el fix', True, None, True),
                                           ('sin el fix', False, None, False),
                                           ('una deny de otro pasaje', True, 'otro pasaje ficticio', False)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')
                self.regla('deny', **par, type='ASSOCIATED_WITH', doc='informe-uco.pdf',
                           **({'quote_contains': pasaje} if pasaje else {}), reason='063 prueba: retirada')
                if fix:
                    self.regla('fix', **par, type='ASSOCIATED_WITH', corrected_type='MET_WITH', doc='informe-uco.pdf',
                               reason='063 prueba: retipo')
                self.registrar({'unidad': '',
                                'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> ({self.TERCERO}): arista retirada'})
                self.exportar_aristas(copia, nodos=(FULDO, self.TERCERO))
                lineas = (f'REGISTRO R-2026-10-01-0001: {viva}', f'MATERIALIZACION: {viva} (R-2026-10-01-0001)')
                for linea, (_, texto) in zip(lineas, self.las_dos_puertas().values()):
                    self.assertEqual(linea in texto, problema, texto)

    def test_a_json_object_repeating_its_identity_keys_is_ambiguous_and_keeps_its_first_identity(self):
        # Spec 071 AC195 (22a-2, la reproducción de Codex): un objeto JSON que repite a la vez `target` y `target_type`
        # conserva la identidad de cada estado por el que pasa (la sociedad oculta, con su tipo), aunque los segundos la
        # pisen; y la entrada que repite una clave de identidad es ella misma un problema en los tres puntos de
        # entrada. Sin repetir nada, solo la reconstrucción.
        uid, pack, oculta = 'e7' * 32, 'E-c0de00000001-111', 'Company:empresa-oculta'
        cita = 'Fuldo coincidió con la empresa oculta en una reunión'
        self.nueva_arista(uid, {'start': FULDO, 'end': oculta, 'type': 'ASSOCIATED_WITH',
                                'props': {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf',
                                          'source_quote': cita}}, pack)
        self.decidir(pack, uid, 'deny')
        self.regla('deny', unidad=uid, reason=f'063 auditoría escalonada 2026-09-26 [{uid[:8]}]: {A.MOTIVO_OPACO}')
        self.registrar({'unidad': uid, 'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> (Company:[sociedad]): arista retirada'})
        self.exportar_aristas(nodos=(FULDO, oculta))
        rel, posicion = 'boe/ambigua.json', 'boe/ambigua.json:clave 1[0]'

        def objeto(*pares):
            return '{"entries": [{' + ', '.join(f'"{k}": "{v}"' for k, v in pares) + '}]}'
        base = (('source', 'Fuldo Fíctez'), ('target', 'Empresa Oculta'), ('target_type', 'Company'))
        ambigua = 'entrada ambigua (clave de identidad repetida)'
        reconstruible = f'fila anonimizada reconstruible ({posicion})'
        repetidas = self.tres_puertas(rel, objeto(*base, ('target', 'Otro'), ('target_type', 'Person'),
                                                 ('type', 'ASSOCIATED_WITH')))
        self.en_las_puertas(repetidas, posicion, ambigua)
        self.en_las_puertas(repetidas, 'R-2026-10-01-0001', reconstruible, puertas=(1, 2))
        sin_repetir = self.tres_puertas(rel, objeto(*base, ('type', 'ASSOCIATED_WITH')))
        for salida in sin_repetir:
            self.assertNotIn(ambigua, salida)
        self.en_las_puertas(sin_repetir, 'R-2026-10-01-0001', reconstruible, puertas=(1, 2))

    def test_the_rows_of_another_registry_are_swept_from_that_same_file(self):
        # Spec 071 AC196 (22a-3, la reproducción de Codex): con el registro del overlay limpio, `--registro` señala una
        # copia cuya fila marcada lleva seis palabras seguidas de su cita retirada en `nota`: la fila se barre desde su
        # propio fichero, no por su posición en el del overlay. La copia sin la nota, nada.
        self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        seis = ' '.join(self.CITA_TERCERO.split()[5:11])
        otro = self.base / 'otro-registro.yaml'
        limpia = (self.repo / A.REG_PATH).read_text(encoding='utf-8')
        for caso, texto, problema in (('con seis palabras de la cita', limpia + f'  nota: {seis}\n', True),
                                      ('sin la nota', limpia, False)):
            with self.subTest(caso=caso):
                otro.write_text(texto, encoding='utf-8')
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    RC.main(['--registro', str(otro), '--snapshot', str(self.snap), '--root', str(self.root),
                             '--overlay', str(self.repo)])
                self.assertEqual(f'REGISTRO R-2026-10-01-0001: {TROZO_CITA}' in out.getvalue(), problema,
                                 out.getvalue())

    def con_alias(self, eid, *alias, **props):
        """Un nodo más del export para `eid` con estos alias (o props)."""
        self.nodo(eid, **({'aliases': list(alias)} if alias else {}), **props)

    def test_a_one_word_name_of_the_hidden_endpoint_next_to_the_visible_one_rebuilds_the_row(self):
        # Spec 071 AC197 y enmienda 24 bis: el nombre de una palabra de un oculto que es una persona cuenta junto al
        # visible (su id, su estructura o un nombre completo suyo): en un texto nuestro es un problema; en prosa curada
        # o solo en el texto de la fuente, un aviso. No cuenta solo, sin el visible; ni una palabra que también es parte
        # del nombre del visible; ni el visible por una parte de su nombre; ni dentro de un identificador.
        self.tercero_retirado()
        regla_visible = {'source_id': FULDO, 'target_id': 'Company:ficcionbar', 'type': 'OWNS', 'doc': 'otro.pdf'}
        ajena = {'source_id': 'Company:ficcionbar', 'target_id': 'Company:otra-ficticia', 'type': 'OWNS'}
        base_reglas = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        base_resumenes = (self.repo / A.SUM_PATH).read_text(encoding='utf-8')
        resumen = ("- entity_id: Company:ficcionbar\n  name: Ficcionbar\n  resumen: Terciano y Fuldo Fíctez la "
                   "fundaron.\n  updated: '2026-10-01'\n  reason: prueba\n")
        corto = f'{self.CORTO} (relations/relations.yaml:'
        for caso, seccion, regla, curado, alias, esperado in (
                ('el oculto por una palabra en un motivo, el visible por su estructura', 'deny',
                 {**regla_visible, 'reason': 'Terciano lo negoció'}, False, 'Terciano', 'problema'),
                ('sin el visible', 'deny', {**ajena, 'reason': 'Terciano lo negoció con Fíctez'}, False, 'Terciano',
                 None),
                ('en prosa curada', None, None, True, 'Terciano', 'aviso'),
                ('solo en la cita de la fuente', 'fix',
                 {**regla_visible, 'corrected_type': 'CONTROLS', 'quote_contains': 'lo acordó con Terciano en el hotel',
                  'reason': 'retipo'}, False, 'Terciano', 'aviso'),
                ('una palabra que también nombra al visible', 'deny', {**regla_visible, 'reason': 'Fuldo lo negoció'},
                 False, 'Fuldo', None),
                ('dentro de un identificador', 'deny',
                 {**regla_visible, 'doc': 'informe-terciano-2026.pdf', 'reason': 'otra cosa'}, False, 'Terciano',
                 None)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base_reglas, encoding='utf-8')
                (self.repo / A.SUM_PATH).write_text(base_resumenes + (resumen if curado else ''), encoding='utf-8')
                if seccion:
                    self.regla(seccion, **regla)
                self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                      nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                self.con_alias(self.TERCERO, alias)
                (_, registro), (_, puerta) = self.registro_check(), self.materializacion()
                self.assertEqual(corto in registro, esperado == 'problema', registro)
                self.assertEqual(f'anonimato del registro: {corto}' in puerta, esperado == 'problema', puerta)
                self.assertEqual(f'aviso R-2026-10-01-0001: {self.EDITORIAL}' in registro, esperado == 'aviso',
                                 registro)
                self.assertNotIn(self.TERCERO, registro + puerta)
        (self.repo / A.SUM_PATH).write_text(base_resumenes, encoding='utf-8')

    def test_a_one_word_name_counts_only_for_a_hidden_person(self):
        # Enmienda 24 bis (VP): el nombre de una palabra de un oculto que no es una persona (un evento, un organismo)
        # es un sustantivo común: no lo nombra. El mismo texto con una persona oculta, sí.
        uid, pack, evento = 'e8' * 32, 'E-c0de00000001-112', 'Event:reunion-ficticia'
        self.nueva_arista(uid, {'start': FULDO, 'end': evento, 'type': 'PARTICIPATED_IN',
                                'props': {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf',
                                          'source_quote': 'Fuldo participó en la reunión ficticia del hotel'}}, pack)
        self.decidir(pack, uid, 'deny')
        self.regla('deny', unidad=uid, reason=f'063 auditoría escalonada 2026-09-26 [{uid[:8]}]: {A.MOTIVO_OPACO}')
        self.registrar({'unidad': uid, 'dato': f'({FULDO}) -[PARTICIPATED_IN]-> (Event:[evento]): arista retirada'})
        self.regla('deny', source_id=FULDO, target_id='Company:ficcionbar', type='OWNS', doc='otro.pdf',
                   reason='Cumbrera lo negoció')
        self.exportar_aristas(nodos=(FULDO, evento, 'Company:ficcionbar'), nombres={FULDO: 'Fuldo Fíctez'})
        self.con_alias(evento, 'Cumbrera')
        _, texto = self.registro_check()
        self.assertNotIn(self.CORTO, texto)

    def test_a_private_reference_names_the_hidden_endpoint(self):
        # Enmienda 24 bis: las referencias privadas de una clave (palabras sueltas que nombran a su oculto en su
        # contexto; en el fichero privado, nunca en el repo) cuentan como un nombre del oculto, y no son declaraciones
        # (AC127). Sin ellas, esa palabra no es nadie. Una referencia de una clave que ninguna opaca resuelve para las
        # puertas sin ningún valor.
        uid = self.tercero_retirado()
        self.regla('deny', source_id=FULDO, target_id='Company:ficcionbar', type='OWNS', doc='otro.pdf',
                   reason='Ternario lo negoció')
        self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                              nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
        privado, corto = self.root / alcance.REGLAS_OPACAS, f'{self.CORTO} (relations/relations.yaml:'
        for caso, contenido, problema in (('sin referencias', '{}\n', False),
                                          ('con la referencia', yaml.safe_dump({'referencias': {uid: ['Ternario']}}),
                                           True)):
            with self.subTest(caso=caso):
                privado.write_text(contenido, encoding='utf-8')
                (code, registro), (_, puerta) = self.registro_check(), self.materializacion()
                self.assertNotEqual(code, 2, registro)
                self.assertEqual(corto in registro, problema, registro)
                self.assertEqual(f'anonimato del registro: {corto}' in puerta, problema, puerta)
                self.assertNotIn('Ternario', registro + puerta)
        privado.write_text(yaml.safe_dump({'referencias': {'f' * 64: ['Ternario']}}), encoding='utf-8')
        code, registro = self.registro_check()
        self.assertEqual(code, 2, registro)
        self.assertIn('referencias privadas de una clave que ninguna regla opaca resuelve', registro)
        self.assertNotIn('Ternario', registro)

    def test_a_quote_of_our_text_that_its_document_says_literally_is_the_source(self):
        # Enmienda 24 bis: un tramo entre comillas de un texto nuestro (un motivo) de una entrada que cita un
        # documento es texto de la fuente si el documento lo dice literalmente (sus fragmentos del export,
        # normalizados; la elipsis lo parte): aviso. Si no lo dice, sigue siendo nuestro: problema.
        self.tercero_retirado()
        self.regla('deny', source_id=FULDO, target_id='Company:ficcionbar', type='OWNS', doc='informe-uco.pdf',
                   reason='el informe dice «quedó acordado… con Terciano en el hotel de la playa»')
        corto = f'{self.CORTO} (relations/relations.yaml:'
        for caso, fragmento, problema in (
                ('literal en su documento', 'Según el informe, quedó acordado entonces con Terciano en el hotel de la '
                 'playa, como consta.', False),
                ('no lo dice', 'Según el informe, quedó pendiente.', True)):
            with self.subTest(caso=caso):
                self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                      nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                self.con_alias(self.TERCERO, 'Terciano')
                with (self.snap / 'nodes.jsonl').open('a', encoding='utf-8') as f:
                    f.write(json.dumps({'labels': ['Chunk'], 'ref': '4:x:950', 'props': {
                        'chunk_id': 'c-literal', 'doc_id': 'informe-uco.pdf', 'chunk_index': 3, 'text': fragmento}},
                        ensure_ascii=False) + '\n')
                _, registro = self.registro_check()
                self.assertEqual(corto in registro, problema, registro)
                self.assertEqual(f'aviso R-2026-10-01-0001: {self.EDITORIAL}' in registro, not problema, registro)

    def test_a_pair_the_context_layer_loads_is_published(self):
        # D1 por la capa de contexto (enmienda 24 bis): el par de una relación que carga `make context` (con su
        # `source_url` https) está publicado aunque el export aún no la lleve, y la fila marcada tiene que nombrar sus
        # dos extremos. Sin `source_url`, el cargador no la carga y no publica nada.
        self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        contexto = self.repo / 'pipeline/data/context/prueba_contexto.yaml'
        entidades = [{'type': 'Person', 'name': 'Fuldo Fíctez'}, {'type': 'Person', 'name': self.NOMBRE_TERCERO}]
        for caso, url, publicado in (('con su source_url', 'https://fuente.invalid/ficticia', True),
                                     ('sin source_url', None, False)):
            with self.subTest(caso=caso):
                relacion = {'source': 'Fuldo Fíctez', 'target': self.NOMBRE_TERCERO, 'type': 'ASSOCIATED_WITH',
                            **({'source_url': url} if url else {})}
                contexto.write_text(yaml.safe_dump({'entities': entidades, 'relations': [relacion]},
                                                   allow_unicode=True), encoding='utf-8')
                (_, registro), (_, puerta) = self.registro_check(), self.materializacion()
                self.assertEqual(f'REGISTRO R-2026-10-01-0001: {MARCADO_PUBLICADO}' in registro, publicado, registro)
                self.assertEqual(MARCADO_PUBLICADO in puerta, publicado, puerta)
        contexto.unlink()

    def test_the_former_identity_of_a_node_whose_identification_is_withdrawn_is_published_nowhere(self):
        # Spec 071 AC199 (22b-1) y enmienda 24 bis: la fila «rectificado» de la fórmula cerrada de una identificación
        # retirada. La identidad anterior del nodo (la de su unidad de identidad del censo: id y nombres) en un fichero
        # de datos versionado es un problema en las dos puertas con el censo; en el export, mientras el nodo del id
        # anterior siga en él (la migración del Path A pendiente), un aviso, y sin él, un problema (un fragmento,
        # aviso).
        viejo, nuevo, uid = 'Person:viejo-ficticio', 'Person:nuevo-ficticio', 'e9' * 32
        db = A.connect(self.root)
        with db:
            db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                       ('o-identidad', 'node', 'n:identidad', viejo, 'Person', 'unspecified', 0, 'h', '{}'))
            db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior,state,resolution)'
                       ' VALUES (?,?,?,?,?,0,100,"[]","verified","arbitraje-2026-10-03")',
                       (uid, 'o-identidad', 'identity', '/identity', json.dumps(
                           {'type': 'Person', 'identity': {'entity_id': viejo, 'name': 'Viejo Ficticio Apellido',
                                                            'aliases': []}})))
        db.close()
        self.registrar({'unidad': uid, 'entidad': nuevo, 'decision': 'rectificado',
                        'dato': f'({nuevo}): ficha corregida: identificación con un tercero retirada; la fuente la '
                                'daba solo como posible'})
        catalogo = self.repo / 'pipeline/data/boe/vigia_targets.json'
        catalogo.parent.mkdir(parents=True, exist_ok=True)
        retirada, pendiente = 'identidad retirada todavía publicada', 'identidad retirada: pendiente del Path A'
        fragmento = {'labels': ['Chunk'], 'ref': '4:x:951',
                     'props': {'chunk_id': 'c-id', 'doc_id': 'informe-uco.pdf', 'chunk_index': 0,
                               'text': 'Se reunió con Viejo Ficticio Apellido.'}}

        def puertas(con_el_viejo, con_catalogo, con_ficha_chunk=False):
            catalogo.write_text(json.dumps({'targets': [{'id': viejo if con_catalogo else nuevo, 'name': 'x'}]}),
                                encoding='utf-8')
            self.exportar_aristas(nodos=(FULDO, nuevo, *((viejo,) if con_el_viejo else ())))
            self.nodo('Person:otro-ficticio', community_label='Viejo Ficticio Apellido · Otro')
            with (self.snap / 'nodes.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(fragmento) + '\n')
                if con_ficha_chunk:   # una ficha con la etiqueta Chunk de más no es un fragmento: problema, no aviso
                    f.write(json.dumps({**fragmento, 'labels': ['Person', 'Chunk'], 'ref': '4:x:952',
                                        'props': {**fragmento['props'], 'entity_id': 'Person:mixto-ficticio'}}) + '\n')
            return self.registro_check()[1], self.materializacion()[1]
        registro, puerta = puertas(con_el_viejo=True, con_catalogo=True)
        self.assertIn(f'REGISTRO R-2026-10-01-0001: {retirada} (pipeline/data/boe/vigia_targets.json', registro)
        self.assertIn(f'MATERIALIZACION: {retirada} (pipeline/data/boe/vigia_targets.json', puerta)
        self.assertNotIn(f'REGISTRO R-2026-10-01-0001: {retirada} (export:', registro)
        self.assertEqual(registro.count(f'aviso R-2026-10-01-0001: {pendiente}'), 3, registro)
        registro, puerta = puertas(con_el_viejo=False, con_catalogo=False)
        self.assertNotIn('vigia_targets.json', registro + puerta)
        self.assertNotIn(pendiente, registro + puerta)
        self.assertEqual(registro.count(f'REGISTRO R-2026-10-01-0001: {retirada} (export:nodo['), 1, registro)
        self.assertEqual(puerta.count(f'MATERIALIZACION: {retirada} (export:nodo['), 1, puerta)
        self.assertEqual(registro.count(f'aviso R-2026-10-01-0001: {retirada} (export:nodo['), 1, registro)
        registro, puerta = puertas(con_el_viejo=False, con_catalogo=False, con_ficha_chunk=True)
        self.assertEqual(registro.count(f'REGISTRO R-2026-10-01-0001: {retirada} (export:nodo['), 2, registro)
        self.assertEqual(puerta.count(f'MATERIALIZACION: {retirada} (export:nodo['), 2, puerta)
        self.assertEqual(registro.count(f'aviso R-2026-10-01-0001: {retirada} (export:nodo['), 1, registro)
        catalogo.unlink()

    # --- enmienda 25 (spec 071 AC212–AC215; 23ª pasada de Codex, 11-10-2026) ------------------------------------
    # Cada test falla contra a63b5f9 por lo que hace (un problema que no sale, un aviso donde había un problema), no por
    # un nombre: los mensajes, por su literal.

    PROPIA = 'fila anonimizada reconstruible (rectificaciones/registro.yaml:rectificaciones[0])'
    REGLA_PROPIA = 'fila anonimizada reconstruible (relations/relations.yaml:deny[1])'

    def test_the_row_itself_and_the_opaque_rule_of_its_unit_do_not_hide_a_short_name_or_a_private_reference(self):
        # Spec 071 AC212 (23a-1 y 23b-1): la propia fila marcada publica el extremo visible y el tipo, y la regla opaca
        # de su unidad la une a ella: un nombre de una palabra del oculto (el alias corto del export) o una referencia
        # privada de su clave, en cualquier campo o comentario de las dos, la reconstruye, como en cualquier otra
        # entrada. Quedan fuera su unidad, su clave y su etiqueta; y siguen sin contar una palabra que también es del
        # visible, un identificador y un nombre que nadie lleva.
        uid = self.tercero_retirado()
        privado = self.root / alcance.REGLAS_OPACAS
        base_reglas = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        dato = f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista retirada'

        def fila(**campos):
            self.registrar({'unidad': uid, 'fuente': 'informe-uco.pdf p.76', 'dato': dato, **campos})

        def con_comentario():
            fila()
            texto = (self.repo / A.REG_PATH).read_text(encoding='utf-8')
            (self.repo / A.REG_PATH).write_text(texto.replace('fuente: informe-uco.pdf p.76',
                                                              'fuente: informe-uco.pdf p.76  # Terciano'),
                                                encoding='utf-8')

        def regla_con_comentario():
            fila()
            (self.repo / A.REL_PATH).write_text(base_reglas.replace(f'unidad: {uid}\n', f'unidad: {uid}  # Terciano\n'),
                                                encoding='utf-8')
        for caso, alias, referencia, preparar, problema in (
                ('una nota con la referencia privada', None, 'Ternario',
                 lambda: fila(nota='Ternario lo negoció'), True),
                ('una fuente con el alias corto', 'Terciano', None,
                 lambda: fila(fuente='auto-ficticio.pdf (Terciano)'), True),
                ('el dato con el alias corto', 'Terciano', None, lambda: fila(dato=f'{dato}; Terciano'), True),
                ('la entidad con el alias corto', 'Terciano', None, lambda: fila(entidad='Terciano'), True),
                ('un comentario YAML de la fila', 'Terciano', None, con_comentario, True),
                ('un comentario de la regla opaca de su unidad', 'Terciano', None, regla_con_comentario, 'regla'),
                ('una palabra que también es del visible', 'Fuldo', None, lambda: fila(nota='Fuldo lo negoció'),
                 False),
                ('dentro de un identificador', 'Terciano', None,
                 lambda: fila(fuente='informe-terciano-2026.pdf p.76'), False),
                ('un nombre que nadie lleva', None, None, lambda: fila(nota='Terciano lo negoció'), False)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base_reglas, encoding='utf-8')
                preparar()
                privado.write_text(yaml.safe_dump({'referencias': {uid: [referencia]}}) if referencia else '{}\n',
                                   encoding='utf-8')
                self.exportar_aristas(nodos=(FULDO, self.TERCERO),
                                      nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                if alias:
                    self.con_alias(self.TERCERO, alias)
                (_, registro), (_, puerta), (_, arbol) = self.registro_check(), self.materializacion(), self.arbol()
                donde = self.REGLA_PROPIA if problema == 'regla' else self.PROPIA
                self.assertEqual(f'REGISTRO R-2026-10-01-0001: {donde}' in registro, bool(problema), registro)
                self.assertEqual(f'anonimato del registro: {donde} (R-2026-10-01-0001)' in puerta, bool(problema),
                                 puerta)
                # Sin el censo, `--tree` no sabe qué oculta la fila: no dice nada de esto y no se rompe.
                self.assertNotIn('reconstruible', arbol)
                for valor in (self.TERCERO, 'Terciano', 'Ternario'):
                    self.assertNotIn(valor, registro + puerta)
        (self.repo / A.REL_PATH).write_text(base_reglas, encoding='utf-8')

    def test_a_retype_that_also_changes_the_layer_leaves_the_withdrawal_alive_in_both_gates(self):
        # Spec 071 AC213 (23a-2, la reproducción de Codex): la deny (capa judicial) y un fix ASSOCIATED_WITH→MET_WITH
        # que además pone `source_kind` en `set`. El loader parte de la arista judicial, la retipa, le cambia la capa y
        # la deny (otro tipo, ya otra capa) no la alcanza: la copia, bajo MET_WITH con la capa que dejó el fix, sigue
        # viva. El estado inicial no es la copia final: se reconstruye con las capas que la deny alcanza y se simula el
        # overlay entero. Con la capa del export distinta de la que deja el fix, o un fix que no llega a la arista, esa
        # copia es otra.
        par = {'source_id': FULDO, 'target_id': self.TERCERO}
        propiedades = {'source_doc': 'informe-uco.pdf', 'source_quote': self.CITA_TERCERO}
        base = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        viva = 'arista «suprimida» viva (1 copia; tolerancia 0)'
        for caso, tipo, capa_del_fix, capa_de_la_copia, pasaje, problema in (
                ('a prensa', 'MET_WITH', 'prensa', 'prensa', None, True),
                ('a otra capa', 'MET_WITH', 'contexto', 'contexto', None, True),
                ('la copia lleva otra capa que la que deja el fix', 'MET_WITH', 'prensa', 'contexto', None, False),
                ('un fix que no llega a la arista', 'MET_WITH', 'prensa', 'prensa', 'otro pasaje ficticio', False),
                ('sin cambio de capa', 'MET_WITH', None, 'judicial', None, True),
                # La misma clase sin retipo: un `set` que solo cambia la capa deja viva la copia que la deny ya no ve.
                ('solo un set de capa, sin retipo', 'ASSOCIATED_WITH', 'prensa', 'prensa', None, True),
                ('solo un set de capa, la copia en otra capa', 'ASSOCIATED_WITH', 'prensa', 'contexto', None, False)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')
                self.regla('deny', **par, type='ASSOCIATED_WITH', doc='informe-uco.pdf', reason='063 prueba: retirada')
                self.regla('fix', **par, type='ASSOCIATED_WITH', doc='informe-uco.pdf',
                           **({'corrected_type': tipo} if tipo != 'ASSOCIATED_WITH' else {}),
                           **({'set': {'source_kind': capa_del_fix}} if capa_del_fix else {}),
                           **({'quote_contains': pasaje} if pasaje else {}), reason='063 prueba: retipo')
                self.registrar({'unidad': '',
                                'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> ({self.TERCERO}): arista retirada'})
                self.exportar_aristas((FULDO, self.TERCERO, tipo, {'source_kind': capa_de_la_copia, **propiedades}),
                                      nodos=(FULDO, self.TERCERO))
                lineas = (f'REGISTRO R-2026-10-01-0001: {viva}', f'MATERIALIZACION: {viva} (R-2026-10-01-0001)')
                for linea, (_, texto) in zip(lineas, self.las_dos_puertas().values()):
                    self.assertEqual(linea in texto, problema, texto)

    def test_a_private_withdrawal_whose_fix_changes_its_layer_is_alive_too(self):
        # Spec 071 AC213, la misma clase en la retirada de una clave privada sin unidad en el censo (AC121): la copia que un
        # fix retipa y pasa a prensa sigue viva, como en la retirada sin unidad. Sin el fix, o sin la copia, nada.
        clave = 'b9' * 32
        predicado = {'source_id': FULDO, 'target_id': self.TERCERO, 'type': 'ASSOCIATED_WITH',
                     'doc': 'informe-uco.pdf'}
        (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump({clave: [predicado]}), encoding='utf-8')
        base = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        copia = (FULDO, self.TERCERO, 'MET_WITH', {'source_kind': 'prensa', 'source_doc': 'informe-uco.pdf',
                                                    'source_quote': self.CITA_TERCERO})
        for caso, con_fix, aristas, problema in (('retipo y capa', True, (copia,), True),
                                                 ('sin el fix', False, (copia,), False),
                                                 ('sin la copia', True, (), False)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')
                self.regla('deny', unidad=clave, declaraciones=1, reason=A.MOTIVO_OPACO)
                if con_fix:
                    self.regla('fix', **{k: v for k, v in predicado.items() if k != 'type'}, type='ASSOCIATED_WITH',
                               corrected_type='MET_WITH', set={'source_kind': 'prensa'}, reason='063 prueba: retipo')
                self.registrar({'unidad': clave,
                                'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista retirada'})
                self.exportar_aristas(*aristas, nodos=(FULDO, self.TERCERO))
                code, texto = self.registro_check()
                self.assertEqual('REGISTRO R-2026-10-01-0001: arista «suprimida» viva (1 copia; tolerancia 0)' in texto,
                                 problema, texto)
                self.assertNotIn(self.TERCERO, texto)
        (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')

    def identidad_de(self, viejo, nombre, uid='e9' * 32):
        """La unidad de identidad del censo de `viejo` (`nombre`) y la fila «rectificado» de su ficha corregida
        (`R-2026-10-01-0001`), como en AC199."""
        nuevo = 'Person:nuevo-ficticio'
        db = A.connect(self.root)
        with db:
            db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                       ('o-identidad', 'node', 'n:identidad', viejo, 'Person', 'unspecified', 0, 'h', '{}'))
            db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior,state,resolution)'
                       ' VALUES (?,?,?,?,?,0,100,"[]","verified","arbitraje-2026-10-03")',
                       (uid, 'o-identidad', 'identity', '/identity', json.dumps(
                           {'type': 'Person', 'identity': {'entity_id': viejo, 'name': nombre, 'aliases': []}})))
        db.close()
        self.registrar({'unidad': uid, 'entidad': nuevo, 'decision': 'rectificado',
                        'dato': f'({nuevo}): ficha corregida: identificación con un tercero retirada; la fuente la '
                                'daba solo como posible'})
        return nuevo

    def test_the_former_identity_is_swept_in_the_decoded_values_not_only_in_the_bytes(self):
        # Spec 071 AC214 (23a-3, la reproducción de Codex): `José Ficticio` en un JSON con `ensure_ascii` se escribe
        # `Jos\u00e9 Ficticio` y los bytes no lo nombran; con `ensure_ascii=False`, sí. Lo mismo con los escapes de YAML
        # y con el id escapado. El barrido examina además los VALORES decodificados (cada cadena, clave y elemento de
        # lista, anidados) de los ficheros de datos y del export; un fichero que no se decodifica se barre solo literal
        # y se avisa; y un valor decodificado que no nombra al retirado, nada.
        viejo, nombre = 'Person:jose-ficticio', 'José Ficticio Apellido'
        nuevo = self.identidad_de(viejo, nombre)
        retirada = 'identidad retirada todavía publicada'
        ruta = 'pipeline/data/boe/escapes'

        def fichero(extension, contenido):
            (self.repo / f'{ruta}.{extension}').parent.mkdir(parents=True, exist_ok=True)
            (self.repo / f'{ruta}.{extension}').write_text(contenido, encoding='utf-8')

        def objeto(**campos):
            return {'targets': [{'id': 'Person:otro-ficticio', 'name': 'x', **campos}]}
        for caso, extension, contenido, problema in (
                ('JSON con ensure_ascii', 'json', json.dumps(objeto(name=nombre)), True),
                ('JSON sin ensure_ascii', 'json', json.dumps(objeto(name=nombre), ensure_ascii=False), True),
                ('JSON con el id escapado', 'json', json.dumps(objeto(id=viejo)).replace('jose', r'\u006aose'), True),
                ('JSON anidado en una lista', 'json', json.dumps(objeto(otros=[[nombre]])), True),
                ('YAML con \\u', 'yaml', 'targets:\n- name: "Jos\\u00e9 Ficticio Apellido"\n', True),
                ('YAML con \\x', 'yaml', 'targets:\n- name: "Jos\\xE9 Ficticio Apellido"\n', True),
                ('YAML con el id escapado', 'yaml', 'targets:\n- id: "Person:\\x6aose-ficticio"\n', True),
                ('YAML con el nombre plegado en comillas dobles', 'yaml',
                 'targets:\n- name: "Jos\\u00e9\n    Ficticio\n    Apellido"\n', True),
                ('JSON con otro valor escapado', 'json', json.dumps(objeto(name='José Otro')), False),
                ('YAML con otro valor escapado', 'yaml', 'targets:\n- name: "Jos\\u00e9 Otro"\n', False)):
            with self.subTest(caso=caso):
                fichero(extension, contenido)
                self.exportar_aristas(nodos=(FULDO, nuevo))
                registro, puerta = self.registro_check()[1], self.materializacion()[1]
                destino = f'{ruta}.{extension}'
                self.assertEqual(f'REGISTRO R-2026-10-01-0001: {retirada} ({destino}' in registro, problema, registro)
                self.assertEqual(f'MATERIALIZACION: {retirada} ({destino}' in puerta, problema, puerta)
                (self.repo / destino).unlink()
        # Un fichero que no se decodifica se barre solo literal, y se avisa; no se da por limpio.
        fichero('json', '{"name": "José Ficticio Apellido"')
        self.exportar_aristas(nodos=(FULDO, nuevo))
        registro = self.registro_check()[1]
        self.assertIn(f'REGISTRO R-2026-10-01-0001: {retirada} ({ruta}.json', registro)
        fichero('json', '{"name": "Jos\\u00e9 Ficticio Apellido"')
        self.assertIn('identidad retirada: fichero no decodificable', self.registro_check()[1])
        (self.repo / f'{ruta}.json').unlink()
        # El export: un valor anidado (un mapa en una propiedad) o una clave, no solo las cadenas de primer nivel.
        for caso, props in (('un mapa anidado', {'extra': {'nombre': nombre}}), ('una clave', {'extra': {nombre: 1}}),
                            ('una lista de listas', {'extra': [[nombre]]})):
            with self.subTest(caso=caso):
                self.exportar_aristas(nodos=(FULDO, nuevo))
                self.nodo('Person:otro-ficticio', **props)
                registro = self.registro_check()[1]
                self.assertEqual(registro.count(f'REGISTRO R-2026-10-01-0001: {retirada} (export:nodo['), 1, registro)
        self.assertNotIn(viejo, registro)
        self.assertNotIn(nombre, registro)

    def test_the_quote_exemption_is_by_position_not_by_content(self):
        # Spec 071 AC215 (23a-4 y 23b-2): lo que se exime es el TRAMO de la entrada que es una cita verificada (el
        # valor de un campo de fuente, o el texto entre comillas que su documento dice literalmente), no cada aparición de lo que
        # esa cita contiene. «Terciano se reunió con Fuldo Fíctez» fuera de una cita bloquea aunque «Terciano … salió»
        # esté dentro de una; un `quote_contains` de una palabra no borra esa palabra del resto del texto; y una cita
        # repetida, dentro de comillas y fuera, exime la que es cita y no la que es texto nuestro.
        self.tercero_retirado()
        base = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        regla_visible = {'source_id': FULDO, 'target_id': 'Company:ficcionbar', 'type': 'OWNS',
                         'doc': 'informe-uco.pdf'}
        cita = '«Terciano … salió sin declarar»'
        corto = f'{self.CORTO} (relations/relations.yaml:'
        for caso, seccion, regla, esperado in (
                ('el nombre fuera y dentro de la cita', 'deny',
                 {**regla_visible, 'reason': f'Terciano se reunió con Fuldo Fíctez. El informe dice {cita}'},
                 'problema'),
                ('el nombre solo dentro de la cita', 'deny',
                 {**regla_visible, 'reason': f'El informe y Fuldo Fíctez: {cita}'}, 'aviso'),
                ('un quote_contains de una palabra y el nombre fuera', 'fix',
                 {**regla_visible, 'corrected_type': 'CONTROLS', 'quote_contains': 'Terciano',
                  'reason': 'Terciano lo negoció con Fuldo Fíctez'}, 'problema'),
                ('un quote_contains de una palabra y el nombre solo en él', 'fix',
                 {**regla_visible, 'corrected_type': 'CONTROLS', 'quote_contains': 'Terciano',
                  'reason': 'retipo de Fuldo Fíctez'}, 'aviso'),
                ('la cita repetida, dentro de comillas y fuera', 'deny',
                 {**regla_visible, 'reason': f'Fuldo Fíctez: Terciano salió sin declarar. Y {cita}'}, 'problema'),
                ('la cita solo entre comillas', 'deny',
                 {**regla_visible, 'reason': f'Fuldo Fíctez, informe: {cita}'}, 'aviso')):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')
                self.regla(seccion, **regla)
                self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                      nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                self.con_alias(self.TERCERO, 'Terciano')
                with (self.snap / 'nodes.jsonl').open('a', encoding='utf-8') as f:
                    f.write(json.dumps({'labels': ['Chunk'], 'ref': '4:x:952', 'props': {
                        'chunk_id': 'c-pos', 'doc_id': 'informe-uco.pdf', 'chunk_index': 0,
                        'text': 'El informe dice que Terciano, tras la reunión, salió sin declarar nada.'}},
                        ensure_ascii=False) + '\n')
                (_, registro), (_, puerta) = self.registro_check(), self.materializacion()
                self.assertEqual(corto in registro, esperado == 'problema', registro)
                self.assertEqual(f'anonimato del registro: {corto}' in puerta, esperado == 'problema', puerta)
                self.assertEqual(f'aviso R-2026-10-01-0001: {self.EDITORIAL}' in registro, esperado == 'aviso',
                                 registro)
        (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')

    # --- enmienda 26 (spec 071 AC220–AC222; 24ª pasada de Codex, 11-10-2026) ------------------------------------
    # Cada test falla contra 5d5a11f por lo que hace, no por un nombre: los mensajes, por su literal.

    def test_a_chain_of_retypes_across_successive_loads_leaves_the_withdrawal_alive_in_both_gates(self):
        # Spec 071 AC220 (24a-1, la reproducción del orquestador): la deny sobre ASSOCIATED_WITH y dos fix válidos,
        # ASSOCIATED_WITH→MET_WITH y MET_WITH→WORKS_FOR. Una carga deja la arista como MET_WITH; la siguiente, como
        # WORKS_FOR, y la tercera ya no la cambia. Con una sola carga, la copia WORKS_FOR no era la retirada: la
        # retirada incumplida se daba por cumplida. La simulación repite el overlay (con las deny en cada carga, una
        # sola transformación por arista dentro de cada una) hasta un estado estable; la copia cuenta si es lo que
        # deja alguna carga.
        par = {'source_id': FULDO, 'target_id': self.TERCERO}
        propiedades = {'source_doc': 'informe-uco.pdf', 'source_quote': self.CITA_TERCERO}
        base = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        viva = 'arista «suprimida» viva (1 copia; tolerancia 0)'
        primero, segundo = ('ASSOCIATED_WITH', 'MET_WITH'), ('MET_WITH', 'WORKS_FOR')
        for caso, retipos, capa_del_segundo, tipo, capa, problema in (
                ('dos retipos encadenados, la copia tras dos cargas', (primero, segundo), None, 'WORKS_FOR',
                 'judicial', True),
                ('dos retipos encadenados, la copia tras una carga', (primero, segundo), None, 'MET_WITH', 'judicial',
                 True),
                ('el segundo retipo cambia la capa', (primero, segundo), 'prensa', 'WORKS_FOR', 'prensa', True),
                ('el segundo retipo cambia la capa, la copia en otra', (primero, segundo), 'prensa', 'WORKS_FOR',
                 'contexto', False),
                ('sin el segundo retipo, una copia WORKS_FOR es otra arista', (primero,), None, 'WORKS_FOR',
                 'judicial', False),
                ('un solo retipo', (primero,), None, 'MET_WITH', 'judicial', True),
                ('un ciclo de retipos que no se estabiliza, la copia en uno de sus tipos', (primero, segundo,
                 ('WORKS_FOR', 'MET_WITH')), None, 'WORKS_FOR', 'judicial', True),
                ('un par sin reglas', (), None, 'WORKS_FOR', 'judicial', False)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')
                self.regla('deny', **par, type='ASSOCIATED_WITH', doc='informe-uco.pdf', reason='063 prueba: retirada')
                for n, (de, a) in enumerate(retipos):
                    self.regla('fix', **par, type=de, corrected_type=a, doc='informe-uco.pdf',
                               **({'set': {'source_kind': capa_del_segundo}} if capa_del_segundo and n else {}),
                               reason=f'063 prueba: retipo {n}')
                self.registrar({'unidad': '',
                                'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> ({self.TERCERO}): arista retirada'})
                self.exportar_aristas((FULDO, self.TERCERO, tipo, {'source_kind': capa, **propiedades}),
                                      nodos=(FULDO, self.TERCERO))
                lineas = (f'REGISTRO R-2026-10-01-0001: {viva}', f'MATERIALIZACION: {viva} (R-2026-10-01-0001)')
                for linea, (_, texto) in zip(lineas, self.las_dos_puertas().values()):
                    self.assertEqual(linea in texto, problema, texto)

    def test_the_successive_loads_stop_at_a_stable_state_or_count_the_copy_as_alive(self):
        # AC220: la cota de cargas es el número de reglas más una; un estado que se repite sin ser estable (dos
        # retipos que se deshacen uno al otro) no certifica nada.
        a, b = 'Person:zz-a', 'Person:zz-b'
        props = {'source_doc': 'd.pdf', 'source_quote': 'q', 'source_kind': 'judicial'}

        def regla(de, a_, **mas):
            return {'source_id': a, 'target_id': b, 'type': de, 'corrected_type': a_, 'doc': 'd.pdf', **mas}
        deny = [{'source_id': a, 'target_id': b, 'type': 'ASSOCIATED_WITH', 'doc': 'd.pdf'}]
        index = {RC._par('ASSOCIATED_WITH', a, b): [(a, b, dict(props))]}
        fix = [regla('ASSOCIATED_WITH', 'MET_WITH'), regla('MET_WITH', 'WORKS_FOR')]
        estados, estable = RC._cargas_sucesivas(index, deny, fix)
        self.assertTrue(estable)
        self.assertEqual([sorted(tipo for tipo, _ in estado) for estado in estados],
                         [['MET_WITH'], ['WORKS_FOR'], ['WORKS_FOR']])
        # Dos retipos que se deshacen uno al otro, sin deny que los alcance: nunca se estabiliza.
        ciclo = [regla('ASSOCIATED_WITH', 'MET_WITH'), regla('MET_WITH', 'ASSOCIATED_WITH')]
        _, estable = RC._cargas_sucesivas(index, [], ciclo)
        self.assertFalse(estable)
        # Un par sin reglas es estable a la primera carga.
        estados, estable = RC._cargas_sucesivas(index, [], [])
        self.assertTrue(estable)
        self.assertEqual(len(estados), 1)

    def test_an_overlay_whose_successive_loads_never_settle_is_a_problem_in_both_gates(self):
        # AC220: si ninguna carga repite la anterior dentro de la cota (un ciclo de retipos), la simulación no puede
        # decir que la copia no sea la retirada en alguna: problema, también con una copia de otro tipo del par.
        par = {'source_id': FULDO, 'target_id': self.TERCERO}
        base = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        inestable = 'las reglas fix del overlay no se estabilizan (cargas sucesivas en ciclo)'
        ciclo = (('ASSOCIATED_WITH', 'MET_WITH'), ('MET_WITH', 'WORKS_FOR'), ('WORKS_FOR', 'MET_WITH'))
        for caso, retipos, problema in (('un ciclo', ciclo, True), ('el mismo sin cerrar el ciclo', ciclo[:2], False)):
            with self.subTest(caso=caso):
                (self.repo / A.REL_PATH).write_text(base, encoding='utf-8')
                self.regla('deny', **par, type='ASSOCIATED_WITH', doc='informe-uco.pdf', reason='063 prueba: retirada')
                for n, (de, a) in enumerate(retipos):
                    self.regla('fix', **par, type=de, corrected_type=a, doc='informe-uco.pdf',
                               reason=f'063 prueba: retipo {n}')
                self.registrar({'unidad': '',
                                'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> ({self.TERCERO}): arista retirada'})
                self.exportar_aristas((FULDO, self.TERCERO, 'MEMBER_OF', {
                    'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf', 'source_quote': self.CITA_TERCERO}),
                    nodos=(FULDO, self.TERCERO))
                lineas = (f'REGISTRO R-2026-10-01-0001: {inestable}',
                          f'MATERIALIZACION: {inestable} (R-2026-10-01-0001)')
                for linea, (_, texto) in zip(lineas, self.las_dos_puertas().values()):
                    self.assertEqual(linea in texto, problema, texto)

    def test_the_first_level_keys_of_a_node_or_an_edge_are_swept_for_a_withdrawn_identity(self):
        # Spec 071 AC221 (24a-2, la reproducción de Codex): el id o el nombre retirados como CLAVE directa de `props`
        # de un nodo o de una arista (no como valor ni como clave anidada) no se barrían. Se barre el mapa entero,
        # claves incluidas; en una arista, solo se separa el valor de `source_quote`. Contraria: las claves
        # estructurales conocidas (`source_quote`, `qa_set_run`) no dan problema.
        viejo, nombre = 'Person:jose-ficticio', 'José Ficticio Apellido'
        nuevo = self.identidad_de(viejo, nombre)
        retirada = 'identidad retirada todavía publicada'
        propiedades = {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf', 'source_quote': self.CITA_TERCERO}
        for caso, clave, donde in (('el nombre, en un nodo', nombre, 'export:nodo['),
                                   ('el id, en un nodo', viejo, 'export:nodo['),
                                   ('el nombre, en una arista', nombre, 'export:arista['),
                                   ('el id, en una arista', viejo, 'export:arista['),
                                   ('las claves estructurales', 'qa_set_run', None),
                                   ('otra clave', 'otra clave cualquiera', None)):
            with self.subTest(caso=caso):
                if 'arista' in caso:
                    self.exportar_aristas((FULDO, nuevo, 'MET_WITH', {**propiedades, clave: 'retirado'}),
                                          nodos=(FULDO, nuevo))
                else:
                    self.exportar_aristas(nodos=(FULDO, nuevo))
                    self.nodo('Person:otro-ficticio', **{clave: 'retirado'})
                registro, puerta = self.registro_check()[1], self.materializacion()[1]
                self.assertEqual(f'REGISTRO R-2026-10-01-0001: {retirada} ({donde}' in registro, donde is not None,
                                 registro)
                self.assertEqual(f'MATERIALIZACION: {retirada} ({donde}' in puerta, donde is not None, puerta)
                self.assertNotIn(viejo, registro + puerta)
                self.assertNotIn(nombre, registro + puerta)
        self.exportar_aristas((FULDO, nuevo, 'MET_WITH', {**propiedades, 'source_quote': 'una cita ficticia'}),
                              nodos=(FULDO, nuevo))
        self.assertNotIn(retirada, self.registro_check()[1] + self.materializacion()[1])

    def test_the_first_level_keys_of_a_node_are_text_for_the_reconstruction_of_a_row(self):
        # AC221, la clase: el texto del export que se compara con lo que oculta una fila (`entradas_del_export`)
        # también lleva las claves de primer nivel de `props`: un nodo con el nombre del oculto como clave y el visible
        # en un valor es el texto de un nodo que nombra los dos, un aviso editorial.
        self.tercero_retirado()
        for caso, props, aviso in (('el nombre del oculto como clave', {self.NOMBRE_TERCERO: 'Fuldo Fíctez'}, True),
                                   ('el nombre del oculto como valor', {'nota': f'{self.NOMBRE_TERCERO}; Fuldo Fíctez'},
                                    True),
                                   ('sin el oculto', {'otra clave': 'Fuldo Fíctez'}, False)):
            with self.subTest(caso=caso):
                self.exportar_aristas(nodos=(FULDO, self.TERCERO),
                                      nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: self.NOMBRE_TERCERO})
                self.nodo('Company:ficcionbar', **props)
                registro = self.registro_check()[1]
                self.assertEqual(f'aviso R-2026-10-01-0001: {self.EDITORIAL}' in registro, aviso, registro)

    def entrada_con_cita(self, formato, reason, nota=None):
        """Un fichero público (JSON o YAML) con una entrada que nombra a Fuldo por su estructura, cita el documento
        y lleva `quote_contains: Terciano`; devuelve (ruta, contenido). `yaml entrecomillado`: cada valor entre
        comillas dobles de la sintaxis."""
        campos = [('source_id', FULDO), ('target_id', 'Company:ficcionbar'), ('type', 'OWNS'),
                  ('doc', 'informe-uco.pdf'), ('reason', reason), *([('nota', nota)] if nota else []),
                  ('quote_contains', 'Terciano')]
        if formato == 'json':
            return 'boe/citas.json', json.dumps({'items': [dict(campos)]}, ensure_ascii=False)
        comillas = '"' if formato == 'yaml entrecomillado' else ''
        return 'boe/citas.yaml', 'items:\n- ' + '\n  '.join(f'{k}: {comillas}{v}{comillas}' for k, v in campos) + '\n'

    def test_the_quote_exemption_is_decided_per_decoded_scalar_in_json_and_yaml(self):
        # Spec 071 AC222 (24b-1, los dos casos de Codex): en JSON, `json.dumps` pone comillas alrededor de cada valor y
        # basta que el documento contenga la frase para eximir el motivo entero («Terciano salió sin declarar»); y un
        # tramo que empieza en un campo y termina en otro («Terciano …» … «… salió sin declarar») se eximía como cita.
        # La exención se decide por cada escalar decodificado: ni las comillas de la sintaxis ni un tramo entre campos
        # cuentan. Mismo veredicto en JSON y en YAML. Contraria: una cita real dentro de un único campo sigue eximida.
        self.tercero_retirado()
        corto = f'{self.CORTO} (boe/citas.'
        fragmento = 'El informe, con una nota: Terciano salió sin declarar nada.'
        for formato in ('json', 'yaml', 'yaml entrecomillado'):
            for caso, reason, nota, esperado in (
                    ('el valor sin comillas con la frase del documento', 'Terciano salió sin declarar', None,
                     'problema'),
                    ('un tramo que cruza dos campos', '«Terciano …', '… salió sin declarar»', 'problema'),
                    ('una cita real dentro de un único campo', 'Fuldo informado, «Terciano … salió sin declarar» '
                     'según el informe', None, 'aviso')):
                with self.subTest(formato=formato, caso=caso):
                    self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                          nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                    self.con_alias(self.TERCERO, 'Terciano')
                    with (self.snap / 'nodes.jsonl').open('a', encoding='utf-8') as f:
                        f.write(json.dumps({'labels': ['Chunk'], 'ref': '4:x:953', 'props': {
                            'chunk_id': 'c-esc', 'doc_id': 'informe-uco.pdf', 'chunk_index': 0, 'text': fragmento}},
                            ensure_ascii=False) + '\n')
                    rel, contenido = self.entrada_con_cita(formato, reason, nota)
                    salidas = self.tres_puertas(rel, contenido)
                    for salida in salidas[1:]:
                        self.assertEqual(corto in salida, esperado == 'problema', salida)
                    self.assertEqual(f'aviso R-2026-10-01-0001: {self.EDITORIAL}' in salidas[1], esperado == 'aviso',
                                     salidas[1])

    # --- enmienda 27 (spec 071 AC226 y AC227; 25ª pasada de Codex, 12-10-2026) ---------------------------------------

    @staticmethod
    def variantes_del_id(viejo):
        """El identificador `viejo` escrito de maneras que lo dejan legible y no son los mismos caracteres (AC226)."""
        corte = len('Person:jo')
        ancho = ''.join(chr(ord(c) + 0xFEE0) if '!' <= c <= '~' else c for c in viejo)
        return {'ZWSP': viejo[:corte] + '\u200b' + viejo[corte:],
                'ZWJ': viejo[:corte] + '\u200d' + viejo[corte:],
                'guion blando': viejo[:corte] + '\u00ad' + viejo[corte:],
                'ancho completo': ancho,
                'unificador de grafemas': viejo[:corte] + '\u034f' + viejo[corte:],
                'selector de variación': viejo[:corte] + '\ufe0f' + viejo[corte:],
                'mayúsculas': viejo.upper(),
                'ZWSP y ancho completo': ancho[:corte] + '\u200b' + ancho[corte:]}

    def test_a_withdrawn_id_is_found_through_invisible_and_width_variants_in_the_export(self):
        # Spec 071 AC226 (25a-1, la reproducción de Codex): `nombra()` normalizaba los NOMBRES y comparaba el
        # identificador con el texto crudo, así que un ZWSP, un guion blando o el ancho completo dentro de la clave
        # de un nodo o de una arista (o de su valor) daba 0 problemas y 0 avisos. El identificador y el texto se
        # comparan normalizados igual (sin caracteres Cf, NFKC, y mayúsculas solo para comparar), conservando los
        # límites de token de `_nombra`.
        viejo, nombre = 'Person:jose-ficticio', 'José Ficticio Apellido'
        nuevo = self.identidad_de(viejo, nombre)
        retirada = 'identidad retirada todavía publicada'
        propiedades = {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf', 'source_quote': self.CITA_TERCERO}

        def exportar(escrito, como):
            clave, valor = (escrito, 'retirado') if como == 'clave' else ('nota', f'ver {escrito} y más')
            return clave, valor
        for variante, escrito in self.variantes_del_id(viejo).items():
            for contexto, donde in (('la clave de un nodo', 'export:nodo['), ('el valor de un nodo', 'export:nodo['),
                                    ('la clave de una arista', 'export:arista['),
                                    ('el valor de una arista', 'export:arista[')):
                with self.subTest(variante=variante, contexto=contexto):
                    clave, valor = exportar(escrito, 'clave' if 'clave' in contexto else 'valor')
                    if 'arista' in contexto:
                        self.exportar_aristas((FULDO, nuevo, 'MET_WITH', {**propiedades, clave: valor}),
                                              nodos=(FULDO, nuevo))
                    else:
                        self.exportar_aristas(nodos=(FULDO, nuevo))
                        self.nodo('Person:otro-ficticio', **{clave: valor})
                    registro, puerta = self.registro_check()[1], self.materializacion()[1]
                    self.assertIn(f'REGISTRO R-2026-10-01-0001: {retirada} ({donde}', registro)
                    self.assertIn(f'MATERIALIZACION: {retirada} ({donde}', puerta)
                    self.assertNotIn(viejo, registro + puerta)
        # Las contrarias, con los límites de token de siempre: otro id, uno más largo, uno que lo lleva dentro de una
        # palabra y la parte sin tipo, no son el retirado.
        for escrito in ('Person:jose-ficticio-2', 'Person:jose-ficticiox', 'xPerson:jose-ficticio',
                        'Person:otro-ficticio', 'jose-ficticio', 'Ｐｅｒｓｏｎ：ｏｔｒｏ-ｆｉｃｔｉｃｉｏ'):
            with self.subTest(contraria=escrito):
                self.exportar_aristas((FULDO, nuevo, 'MET_WITH', {**propiedades, 'nota': f'ver {escrito} y más'}),
                                      nodos=(FULDO, nuevo))
                self.nodo('Person:otro-ficticio', **{escrito: 'x'})
                self.assertNotIn(retirada, self.registro_check()[1] + self.materializacion()[1])

    def test_a_withdrawn_id_is_found_through_variants_in_a_data_file_or_a_comment(self):
        # AC226, los ficheros de datos y sus comentarios: la misma primitiva en el barrido de lo publicado (`nombra()`
        # sobre el texto del fichero, sobre cada línea y sobre cada valor leído).
        viejo, nombre = 'Person:jose-ficticio', 'José Ficticio Apellido'
        nuevo = self.identidad_de(viejo, nombre)
        retirada = 'identidad retirada todavía publicada'
        ruta = 'pipeline/data/boe/variantes'
        for variante, escrito in self.variantes_del_id(viejo).items():
            for caso, extension, contenido in (
                    ('un valor YAML', 'yaml', f'targets:\n- id: Person:otro-ficticio\n  nota: ver {escrito} y más\n'),
                    ('un comentario YAML', 'yaml', f'targets:\n- id: Person:otro-ficticio\n  # ver {escrito} y más\n'),
                    ('una clave YAML', 'yaml', f'targets:\n- id: Person:otro-ficticio\n  {escrito}: x\n'),
                    ('un valor JSON', 'json', json.dumps({'targets': [{'nota': f'ver {escrito} y más'}]},
                                                         ensure_ascii=False)),
                    ('una clave JSON', 'json', json.dumps({'targets': [{escrito: 'x'}]}, ensure_ascii=False))):
                with self.subTest(variante=variante, caso=caso):
                    destino = f'{ruta}.{extension}'
                    (self.repo / destino).parent.mkdir(parents=True, exist_ok=True)
                    (self.repo / destino).write_text(contenido, encoding='utf-8')
                    try:
                        self.exportar_aristas(nodos=(FULDO, nuevo))
                        registro, puerta = self.registro_check()[1], self.materializacion()[1]
                    finally:
                        (self.repo / destino).unlink()
                    self.assertIn(f'REGISTRO R-2026-10-01-0001: {retirada} ({destino}', registro)
                    self.assertIn(f'MATERIALIZACION: {retirada} ({destino}', puerta)
                    self.assertNotIn(viejo, registro + puerta)
        destino = f'{ruta}.yaml'
        (self.repo / destino).write_text('targets:\n- id: Person:otro-ficticio\n'
                                         '  nota: ver Person:jose-ficticio-2 y más\n  # y Person:jose-ficticiox\n',
                                         encoding='utf-8')
        try:
            self.exportar_aristas(nodos=(FULDO, nuevo))
            self.assertNotIn(retirada, self.registro_check()[1] + self.materializacion()[1])
        finally:
            (self.repo / destino).unlink()

    def test_a_document_name_with_an_invisible_character_does_not_vouch_for_a_quote(self):
        # AC226, medido: el documento que cita una entrada se compara con el de los fragmentos del export tal cual, sin
        # normalizar. Uno escrito con un carácter invisible no encuentra su documento, la cita no se verifica y el texto
        # queda como nuestro: fallo cerrado (problema), nunca una exención que no se ha comprobado.
        self.tercero_retirado()
        corto = f'{self.CORTO} (boe/doc.'
        fragmento = 'El informe, con una nota: Terciano salió sin declarar nada.'
        for doc, esperado in (('informe-uco.pdf', 'aviso'), ('informe\u200b-uco.pdf', 'problema'),
                              ('informe\u00ad-uco.pdf', 'problema')):
            with self.subTest(doc=doc):
                self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                      nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                self.con_alias(self.TERCERO, 'Terciano')
                with (self.snap / 'nodes.jsonl').open('a', encoding='utf-8') as f:
                    f.write(json.dumps({'labels': ['Chunk'], 'ref': '4:x:955', 'props': {
                        'chunk_id': 'c-doc', 'doc_id': 'informe-uco.pdf', 'chunk_index': 0, 'text': fragmento}},
                        ensure_ascii=False) + '\n')
                contenido = ('items:\n- source_id: Person:fuldo-fictez\n  target_id: Company:ficcionbar\n'
                             f'  type: OWNS\n  doc: {doc}\n'
                             '  reason: Fuldo informado, «Terciano … salió sin declarar» según el informe\n')
                salidas = self.tres_puertas('boe/doc.yaml', contenido)
                for salida in salidas[1:]:
                    self.assertEqual(corto in salida, esperado == 'problema', salida)

    ENTRADAS_CON_CABECERA = (
        ('un escalar plegado', 'reason: >-{c}\n    cuerpo neutro\n'),
        ('un escalar literal', 'reason: |{c}\n    cuerpo neutro\n'),
        ('un indicador de sangría y de conservación', 'reason: >+2{c}\n    cuerpo neutro\n'),
        ('un ancla', 'reason: &q{c}\n    cuerpo neutro\n'),
        ('una etiqueta', 'reason: !!str{c}\n    cuerpo neutro\n'),
        ('un ancla y una etiqueta, el comentario en su línea', 'reason: &q !!str\n    {c}\n    cuerpo neutro\n'))

    def test_a_header_comment_of_a_block_or_anchored_scalar_is_our_own_text_in_both_gates(self):
        # Spec 071 AC227 (25b, la reproducción de Codex): `reason: >- # Terciano se reunió con Fuldo` seguido de un
        # cuerpo neutro: PyYAML cuenta el comentario DENTRO del tramo del nodo, `_comentarios` descarta los `#` dentro
        # de tramos de escalares y el valor leído tampoco lo lleva, así que AC222 (que mira solo `propio.escalares`)
        # no bloqueaba una fila que oculta a Terciano. Lo del tramo literal que no es sintaxis ni valor leído es texto
        # propio. Contraria:
        # una cita verificada en ese comentario sigue eximida (la exención es por escalar o comentario), y el mismo
        # comentario sin el oculto no da nada.
        self.tercero_retirado()
        corto = f'{self.CORTO} (boe/cabecera.'
        fragmento = 'El informe, con una nota: Terciano salió sin declarar nada.'
        for caso, forma in self.ENTRADAS_CON_CABECERA:
            for comentario, esperado in (('# Terciano se reunió con Fuldo', 'problema'),
                                         ('# Fuldo informado: «Terciano … salió sin declarar» según el informe',
                                          'aviso'),
                                         ('# nada que ver con el oculto', None)):
                with self.subTest(caso=caso, comentario=comentario):
                    self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                          nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                    self.con_alias(self.TERCERO, 'Terciano')
                    with (self.snap / 'nodes.jsonl').open('a', encoding='utf-8') as f:
                        f.write(json.dumps({'labels': ['Chunk'], 'ref': '4:x:954', 'props': {
                            'chunk_id': 'c-cab', 'doc_id': 'informe-uco.pdf', 'chunk_index': 0, 'text': fragmento}},
                            ensure_ascii=False) + '\n')
                    campo = forma.replace('{c}', f' {comentario}' if '\n    {c}' not in forma else comentario)
                    contenido = ('items:\n- source_id: Person:fuldo-fictez\n  target_id: Company:ficcionbar\n'
                                 '  type: OWNS\n  doc: informe-uco.pdf\n  ' + campo + '  n: 1\n')
                    salidas = self.tres_puertas('boe/cabecera.yaml', contenido)
                    for salida in salidas[1:]:
                        self.assertEqual(corto in salida, esperado == 'problema', salida)
                    self.assertEqual(f'aviso R-2026-10-01-0001: {self.EDITORIAL}' in salidas[1], esperado == 'aviso',
                                     salidas[1])

    # --- enmienda 28 (spec 071 AC230–AC233; 26ª pasada de Codex, 12-10-2026) --------------------------------------

    # Un carácter de cada familia de los que no se ven (AC230): Cf y los que no lo son.
    INVISIBLES = {'anchura cero': '\u200b', 'guion blando': '\u00ad', 'unión de palabras': '\u2060',
                  'BOM': '\ufeff', 'separador de vocal mongol': '\u180e', 'unificador de grafemas': '\u034f',
                  'selector de variación 1': '\ufe00', 'selector de variación 16': '\ufe0f',
                  'selector de variación 17': '\U000e0100', 'selector de variación 256': '\U000e01ef',
                  'relleno de hangul (choseong)': '\u115f', 'relleno de hangul (jungseong)': '\u1160',
                  'relleno de hangul (compatibilidad)': '\u3164', 'relleno de hangul de media anchura': '\uffa0',
                  'vocal inherente de jemer 1': '\u17b4', 'vocal inherente de jemer 2': '\u17b5',
                  'selector mongol 1': '\u180b', 'selector mongol 2': '\u180c', 'selector mongol 3': '\u180d',
                  'selector mongol 4': '\u180f'}

    def test_a_withdrawn_name_hidden_by_any_invisible_character_is_found_in_both_gates(self):
        # Spec 071 AC230 (26a-1): `_llano` solo quitaba los caracteres de formato (Cf) y `_formas`, además, el
        # unificador de grafemas, los rellenos de hangul y los selectores de variación: «José<U+034F> Ficticio Apellido»
        # no era el nombre del nodo retirado y daba 0 problemas y 0 avisos, en un fichero de datos y en el export.
        viejo, nombre = 'Person:jose-ficticio', 'José Ficticio Apellido'
        nuevo = self.identidad_de(viejo, nombre)
        retirada = 'identidad retirada todavía publicada'
        destino = 'pipeline/data/boe/invisible.yaml'
        (self.repo / destino).parent.mkdir(parents=True, exist_ok=True)
        for familia, c in self.INVISIBLES.items():
            for escrito, esperado in ((f'Jos\u00e9{c} Ficticio Apellido', True),
                                      (f'Jos{c}\u00e9 Ficticio {c}Apellido', True),
                                      (f'Jos\u00e9{c} Otro Apellido', False)):
                for contexto in ('un fichero de datos', 'el export'):
                    with self.subTest(familia=familia, escrito=escrito, contexto=contexto):
                        self.exportar_aristas(nodos=(FULDO, nuevo))
                        if contexto == 'el export':
                            self.nodo('Person:otro-ficticio', nota=f'ver {escrito} y más')
                            donde = 'export:nodo['
                        else:
                            (self.repo / destino).write_text(
                                f'targets:\n- id: Person:otro-ficticio\n  nota: ver {escrito} y más\n',
                                encoding='utf-8')
                            donde = destino
                        try:
                            registro, puerta = self.registro_check()[1], self.materializacion()[1]
                        finally:
                            (self.repo / destino).unlink(missing_ok=True)
                        self.assertEqual(f'REGISTRO R-2026-10-01-0001: {retirada} ({donde}' in registro, esperado,
                                         registro)
                        self.assertEqual(f'MATERIALIZACION: {retirada} ({donde}' in puerta, esperado, puerta)

    def test_a_short_name_hidden_by_any_invisible_character_still_rebuilds_the_row(self):
        # AC230 (26a-1): «Terci<U+034F>ano» evitaba la detección del nombre de una palabra del extremo oculto: el
        # filtro previo usa `e.llano` y retornaba antes de `_visible`. El nombre y el filtro, con la misma definición de
        # lo que no se ve.
        self.tercero_retirado()
        corto = f'{self.CORTO} (boe/invisible.'
        for familia, c in self.INVISIBLES.items():
            for nombre, esperado in ((f'Terci{c}ano', True), (f'{c}Terciano', True), (f'Terciano{c}', True),
                                     (f'Terci{c}anos', False)):
                with self.subTest(familia=familia, nombre=nombre):
                    self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                          nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                    self.con_alias(self.TERCERO, 'Terciano')
                    contenido = ('items:\n- source_id: Person:fuldo-fictez\n  target_id: Company:ficcionbar\n'
                                 '  type: OWNS\n  doc: informe-uco.pdf\n'
                                 f'  reason: {nombre} se reunió con Fuldo\n  n: 1\n')
                    salidas = self.tres_puertas('boe/invisible.yaml', contenido)
                    for salida in salidas[1:]:
                        self.assertEqual(corto in salida, esperado, salida)

    def test_a_private_reference_hidden_by_any_invisible_character_is_found_in_all_gates(self):
        # AC230: la huella de una fila anonimizada escrita con un invisible dentro, de cualquier familia.
        uid = self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        rid, posicion = 'R-2026-10-01-0001', 'boe/huella.yaml:items[0]'
        for familia, c in self.INVISIBLES.items():
            with self.subTest(familia=familia):
                texto = f'items:\n- note: {uid[:4]}{c}{uid[4:8]}\n'
                self.en_las_puertas(self.tres_puertas('boe/huella.yaml', texto), rid, f'{HUELLA} ({posicion})')

    def test_an_id_in_a_row_is_read_folded_like_the_comparison_and_never_fails_the_gates(self):
        # AC231 (26a-2): en una fila anonimizada cuyo extremo visible es `Person:fuldo-fictez`, `…-ß` pasaba (se leía
        # el id visible) y `…-ss` bloqueaba, aunque `_nombra` los toma por el mismo; y `Crıme` o `Crİme` hacían caer el
        # CLI con un `KeyError`. Las dos puertas leen el mismo id y ningún texto las tira.
        uid = self.tercero_retirado()
        self.exportar_aristas(nodos=(FULDO, self.TERCERO))
        fuera = f'{EXTREMO_FUERA} (rectificaciones/registro.yaml:rectificaciones[0])'
        for escrito, problema in ((f'{FULDO}-\u00df', True), (f'{FULDO}-ss', True), (f'{FULDO}-\u1e9e', True),
                                  (f'{FULDO}-\ufb03', True), (f'{FULDO}', False), (f'{FULDO.upper()}', False),
                                  ('Cr\u0131me:x', False), ('Cr\u0130me:x', False), ('Cr\u01c5me:x', False),
                                  ('Cr\ufb03me:x', False), ('\u212aime:x', False), ('Per\u0131on:x', False)):
            with self.subTest(escrito=escrito):
                self.registrar({'unidad': uid, 'fuente': f'informe-uco.pdf p.76; {escrito}',
                                'dato': f'({FULDO}) -[ASSOCIATED_WITH]-> (Person:[tercero]): arista retirada'})
                registro, puerta = self.registro_check(), self.materializacion()
                for codigo, salida in (registro, puerta):  # un texto cualquiera no tira el CLI con «error interno»
                    self.assertNotIn('error interno', salida)
                    self.assertIn(codigo, (0, 1), salida)
                self.assertEqual(f'REGISTRO R-2026-10-01-0001: {fuera}' in registro[1], problema, registro[1])
                self.assertEqual(f'MATERIALIZACION: anonimato del registro: {fuera} (R-2026-10-01-0001)' in puerta[1],
                                 problema, puerta[1])

    # Una ficha de relación que publica el extremo visible de la retirada con su fuente (AC232 y AC233).
    FICHA_28 = ('source_id: Person:fuldo-fictez, target_id: Company:ficcionbar, type: OWNS, doc: informe-uco.pdf, '
                'reason: neutro')

    def con_chunk_de_cita(self, fragmento):
        with (self.snap / 'nodes.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'labels': ['Chunk'], 'ref': '4:x:955', 'props': {
                'chunk_id': 'c-28', 'doc_id': 'informe-uco.pdf', 'chunk_index': 0, 'text': fragmento}},
                ensure_ascii=False) + '\n')

    SEPARADORES = {'LF': '\n', 'CR': '\r', 'CRLF': '\r\n', 'NEL': '\x85', 'LS': '\u2028', 'PS': '\u2029'}

    def test_the_body_of_a_source_block_is_not_our_own_text_with_any_line_break_pyyaml_accepts(self):
        # Spec 071 AC232 (26a-3): `_tokens_de_texto` buscaba solo `\n` para el final de la cabecera de un escalar de
        # bloque; con CR, NEL, LS o PS el cuerpo de `quote_contains: |` (el texto de la fuente) se tomaba por un
        # comentario nuestro: aviso editorial con LF, problema bloqueante con los otros. El mismo contenido da lo mismo
        # con cualquier separador, solo en el bloque o en todo el fichero, y con los indicadores de sangría y de
        # conservación.
        self.tercero_retirado()
        fragmento = 'Resumen: # Terciano salió sin declarar nada.'
        corto = f'{self.CORTO} (boe/bloque.'
        resultados = {}
        for nombre, sep in self.SEPARADORES.items():
            for indicador in ('|', '>-', '|2', '>+'):
                for ambito in ('solo el bloque', 'todo el fichero'):
                    cabecera = f'  quote_contains: {indicador}\n    # Terciano salió sin declarar nada.\n  n: 1\n'
                    contenido = ('items:\n- source_id: Person:fuldo-fictez\n  target_id: Company:ficcionbar\n'
                                 '  type: OWNS\n  doc: informe-uco.pdf\n' + cabecera)
                    if ambito == 'todo el fichero':
                        contenido = contenido.replace('\n', sep)
                    else:
                        contenido = contenido.replace(f'{indicador}\n    # Terciano salió sin declarar nada.\n',
                                                      f'{indicador}{sep}    # Terciano salió sin declarar nada.{sep}')
                    with self.subTest(separador=nombre, indicador=indicador, ambito=ambito):
                        self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                              nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                        self.con_alias(self.TERCERO, 'Terciano')
                        self.con_chunk_de_cita(fragmento)
                        salidas = self.tres_puertas('boe/bloque.yaml', contenido)
                        visto = tuple((corto in s, f'aviso R-2026-10-01-0001: {self.EDITORIAL}' in s)
                                      for s in salidas[1:])
                        resultados.setdefault((indicador, ambito), {})[nombre] = visto
        for (indicador, ambito), por_separador in resultados.items():
            with self.subTest(indicador=indicador, ambito=ambito):
                distintos = {n: v for n, v in por_separador.items() if v != por_separador['LF']}
                self.assertEqual(distintos, {}, por_separador)
                self.assertFalse(any(corto_ for corto_, _ in por_separador['LF']), por_separador)

    def entradas_con_alias(self, comentario='# Terciano se reunió con él'):
        """(caso, contenido, si debe bloquear): un comentario junto a un alias de la ficha de Fuldo (AC233)."""
        b, c = self.FICHA_28, comentario
        return (
            ('un alias', f'base: &b {{{b}}}\nitems:\n- *b {c}\n', True),
            ('varios alias del mismo ancla', f'base: &b {{{b}}}\nitems:\n- *b\n- *b {c}\n- *b\n', True),
            ('el ancla en el mismo elemento', f'items:\n- &b {{{b}}}\n- *b {c}\n', True),
            ('el comentario de la línea siguiente', f'base: &b {{{b}}}\nitems:\n- *b\n  {c}\n- n: 1\n', True),
            ('una clave de fusión', f'base: &b {{{b}}}\nitems:\n- <<: *b {c}\n  n: 1\n', True),
            ('un alias en una secuencia de flujo', f'base: &b {{{b}}}\nitems: [*b] {c}\n', True),
            ('un alias en un mapa de flujo', f'base: &b {{{b}}}\nitems:\n- {{<<: *b, n: 1}} {c}\n', True),
            ('el alias en su propia línea', f'base: &b {{{b}}}\nitems:\n-\n  *b {c}\n', True),
            ('un bloque con cabecera tras un alias',
             f'base: &b {{{b}}}\nitems:\n- *b\n- <<: *b\n  reason: >- {c}\n    cuerpo\n', True),
            ('una sección que es un alias', f'base: &s\n- {{{b}}}\nitems: *s {c}\n', True),
            ('el ancla después: no se compone', f'items:\n- *b {c}\nbase: &b {{{b}}}\n', False),
            ('el ancla en otro documento: no se compone', f'base: &b {{{b}}}\n---\nitems:\n- *b {c}\n', False),
            # Contrarias: el mismo comentario sin el oculto, o junto a un par que no identifica al extremo visible.
            ('sin el oculto', f'base: &b {{{b}}}\nitems:\n- *b # nada que ver con el oculto\n', False),
            ('un par ajeno', 'base: &d {source_id: Company:ficcionbar, target_id: Company:otra-ficticia, type: OWNS}\n'
                             f'items:\n- *d {c}\n', False))

    def test_the_comment_next_to_an_alias_rebuilds_the_row_it_expands_in_both_gates(self):
        # Spec 071 AC233 (26b-1, la reproducción de Codex): PyYAML devuelve para `*b` el MISMO nodo que el ancla, con
        # sus marcas, y el filtro por líneas dejaba el comentario junto al alias fuera de la entrada que expande el
        # valor: 0 problemas y 0 avisos, y con el alias expandido bloquea. La posición de cada aparición; el comentario
        # va con el valor expandido de esa entrada. Los ficheros que no se componen no se dan por limpios.
        self.tercero_retirado()
        corto = f'{self.CORTO} (boe/alias.'
        for caso, contenido, bloquea in self.entradas_con_alias():
            with self.subTest(caso=caso):
                self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                      nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                self.con_alias(self.TERCERO, 'Terciano')
                salidas = self.tres_puertas('boe/alias.yaml', contenido)
                if 'no se compone' in caso:
                    for salida in salidas:
                        self.assertIn('no verificable', salida)
                else:
                    for salida in salidas[1:]:
                        self.assertEqual(corto in salida, bloquea, salida)

    def test_a_comment_next_to_an_alias_blocks_like_the_same_text_with_the_alias_expanded(self):
        # AC233: la propiedad detrás del caso de Codex: con el alias o con su valor escrito en el sitio, las dos puertas
        # dicen lo mismo.
        self.tercero_retirado()
        b, c = self.FICHA_28, '# Terciano se reunió con él'
        self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                              nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
        self.con_alias(self.TERCERO, 'Terciano')
        con_alias = self.tres_puertas('boe/alias.yaml', f'base: &b {{{b}}}\nitems:\n- *b {c}\n')
        expandido = self.tres_puertas('boe/alias.yaml', f'base: &b {{{b}}}\nitems:\n- {{{b}}} {c}\n')
        corto = f'{self.CORTO} (boe/alias.'
        self.assertIn(corto, expandido[1])
        self.assertEqual([corto in s for s in con_alias], [corto in s for s in expandido])

    # --- enmienda 29 (spec 071 AC235; 27ª pasada de Codex, 16-10-2026) -----------------------------------------------

    def rangos_de_los_elementos(self):
        """(caso, contenido, si debe bloquear): el comentario junto al extremo visible, en la cabecera, en la cola o
        junto a la clave de un elemento o de una sección (AC235): los siete de la tabla de Codex y sus controles."""
        c, base = '# Terciano se reunió con él', f'base: &b {{{self.FICHA_28}}}\n'
        ficha = ('source_id: Person:fuldo-fictez\n  target_id: Company:ficcionbar\n  type: OWNS\n'
                 '  doc: informe-uco.pdf\n  reason: neutro\n')
        return (
            ('la cabecera del alias', f'{base}items:\n- {c}\n  *b\n', True),
            ('bajo el último alias, con sangría', f'{base}items:\n- *b\n  {c}\n', True),
            ('bajo el último alias, sin sangría', f'{base}items:\n- *b\n{c}\n', True),
            ('la cabecera de un elemento sin alias', f'items:\n- {c}\n  {ficha}', True),
            ('entre la clave de la sección y el primer guion', f'items:\n{c}\n- {ficha}', True),
            ('una sección que no es lista, con un alias', f'{base}valor: *b\n{c}\n', True),
            ('el último alias de un segundo documento', f'n: 1\n---\n{base}items:\n- *b\n{c}\n', True),
            ('una lista de flujo, la última sección', f'{base}items: [*b]\n{c}\n', True),
            ('bajo un alias intermedio', f'{base}items:\n- *b\n  {c}\n- n: 2\n', True),
            ('bajo el último elemento expandido', f'items:\n- {ficha}  {c}\n', True),
            ('junto al alias (control)', f'{base}items:\n- *b {c}\n', True),
            ('bajo el último alias, sin el oculto', f'{base}items:\n- *b\n  # nada que ver con el oculto\n', False),
            ('bajo el último alias, en un par ajeno',
             'base: &d {source_id: Company:ficcionbar, target_id: Company:otra-ficticia, type: OWNS}\n'
             f'items:\n- *d\n  {c}\n', False))

    def test_the_comment_of_an_element_or_a_section_goes_with_it_from_its_indicator_to_the_next_in_both_gates(self):
        # Spec 071 AC235 (27a y 27b, la tabla de Codex): el comentario de cabecera (`- # …`), el de cola del último
        # elemento y el de una sección que no es lista quedaban como entradas sueltas, sin el extremo visible: 0
        # problemas y 0 avisos, y con el comentario en la línea del alias bloquea. Con el alias al final, en medio o
        # expandido, el mismo resultado en las dos puertas.
        self.tercero_retirado()
        corto = f'{self.CORTO} (boe/rangos.'
        for caso, contenido, bloquea in self.rangos_de_los_elementos():
            with self.subTest(caso=caso):
                self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                      nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                self.con_alias(self.TERCERO, 'Terciano')
                salidas = self.tres_puertas('boe/rangos.yaml', contenido)
                for salida in salidas[1:]:
                    self.assertEqual(corto in salida, bloquea, salida)

    # --- enmienda 30 (spec 071 AC237; 28ª pasada de Codex, 17-10-2026) -----------------------------------------------

    def rangos_de_los_indicadores(self):
        """(caso, contenido, si debe bloquear): el comentario con el nombre corto del extremo oculto y el extremo
        visible de la ficha, con la ficha detrás de un alias y el comentario donde la expansión de su ancla, la
        apertura de una raíz de flujo, una lista vacía o una clave explícita lo ponen (AC237)."""
        c, V, f = '# Terciano se reunió con él', 'Person:fuldo-fictez', self.FICHA_28
        bloque = ('  source_id: Person:fuldo-fictez\n  target_id: Company:ficcionbar\n  type: OWNS\n'
                  '  doc: informe-uco.pdf\n  reason: neutro\n')
        return (
            ('la sección vecina no aporta su comentario al valor expandido',
             f'base: &b\n{bloque}other: {c}\n  x: neutro\nvalue: *b\n', False),
            ('la sección vecina, tras la que usa el alias',
             f'base: &b\n{bloque}value: *b\nother: {c}\n  x: neutro\n', False),
            ('la cola de un ancla de lista antes de la clave que la usa', f'base: &b\n- neutro\n{c}\n{V}: *b\n', True),
            ('la cola de un ancla de flujo antes de la clave que la usa', f'base: &b [neutro]\n{c}\n{V}: *b\n', True),
            ('la cola de un ancla anidada en otro alias',
             f'base: &b\n- neutro\n{c}\nmid: &m\n  y: *b\n{V}: *m\n', True),
            ('la apertura de una secuencia de flujo raíz', f'[ {c}\n {{{f}}}\n]\n', True),
            ('una sección con lista vacía', f'{V}: []\n{c}\n', True),
            ('la clave explícita', f'? {c}\n  items\n:\n-{bloque[1:]}', True),
            ('el alias de una sección de lista lleva su ancla entera',
             f'base: &s\n- {{{f}}}\n- neutro\n{c}\nitems: *s\n', True),
            ('el alias como clave', f'base: &k clave {c}\n*k :\n{bloque}', True),
            ('la apertura de un mapa de flujo raíz',
             f'{{ {c}\n  source_id: {V}, target_id: Company:ficcionbar}}\n', True),
            ('la apertura de una secuencia raíz con ancla', f'&a {c}\n-{bloque[1:]}', True),
            ('AC241: la directiva con el visible y una secuencia raíz vacía', f'%TAG !x! {V} {c}\n---\n[]\n', True),
            ('AC241: la directiva de un segundo documento con la raíz vacía',
             f'x: neutral\n---\n%TAG !x! {V} {c}\n---\n{{}}\n', True),
            ('AC242: el nombre de una directiva y el visible en la misma entrada',
             f'%TAG !Terciano! tag:yaml.org,2002:\n---\nitems:\n-{bloque[1:]}', True),
            ('el --- de un segundo documento',
             f'x: neutral\n--- {c}\n' + bloque.replace('\n  ', '\n').lstrip(), True),
            ('un escalar de bloque raíz y el comentario del documento siguiente',
             f'|\n  {V}\n--- {c}\nx: neutral\n', False),
            ('control: el comentario en la línea del alias', f'base: &b {{{f}}}\nvalue: *b {c}\n', True),
            ('control: la sección con mapa vacío', f'{V}: {{}}\n{c}\n', True),
            ('contrario: el comentario de la sección vecina', f'a: x {c}\nb: {V}\n', False))

    def test_the_range_of_each_construction_starts_at_its_indicator_in_both_gates(self):
        # Spec 071 AC237 (28a y 28b, la tabla de Codex): la expansión de un alias usa el rango lógico completo de su
        # ancla, cada rango empieza en el indicador de su construcción y una sección vacía genera entrada. El mismo
        # resultado en las dos puertas, y los controles (el comentario junto al alias, un mapa vacío, una sección
        # vecina) no cambian.
        self.tercero_retirado()
        corto = f'{self.CORTO} (boe/indicadores.'
        for caso, contenido, bloquea in self.rangos_de_los_indicadores():
            with self.subTest(caso=caso):
                self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                      nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                self.con_alias(self.TERCERO, 'Terciano')
                salidas = self.tres_puertas('boe/indicadores.yaml', contenido)
                for salida in salidas[1:]:
                    self.assertEqual(corto in salida, bloquea, salida)

    def test_the_text_between_two_quotes_is_not_a_quote_even_when_a_quote_crosses_a_line_in_both_gates(self):
        # Spec 071 AC242 (30b-1): una cita que cruza un salto de línea no casaba con `"([^"\\n]+)"` y la búsqueda seguía
        # en su comilla de cierre: el texto ENTRE dos citas, que el documento también dice, quedaba exento y la fila con
        # el nombre corto y el extremo visible pasaba sin problema. Las comillas se emparejan por pares completos.
        self.tercero_retirado()
        corto = f'{self.CORTO} (boe/citas.'
        entre = 'Terciano salió sin declarar'
        fragmento = f'Una declaración con salto de línea {entre} Otra declaración'
        for caso, razon, bloquea in (
                ('el texto entre dos citas',
                 f'"\\"Una declaración con\\nsalto de línea\\" {entre} \\"Otra declaración\\""', True),
                ('el nombre dentro de una cita', f'"\\"{fragmento}\\""', False)):
            with self.subTest(caso=caso):
                self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                      nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                self.con_alias(self.TERCERO, 'Terciano')
                self.con_chunk_de_cita(fragmento)
                contenido = ('items:\n- source_id: Person:fuldo-fictez\n  target_id: Company:ficcionbar\n  type: OWNS\n'
                             f'  doc: informe-uco.pdf\n  reason: {razon}\n')
                for salida in self.tres_puertas('boe/citas.yaml', contenido)[1:]:
                    self.assertEqual(corto in salida, bloquea, salida)

    def test_the_alias_terminal_in_the_middle_nested_and_expanded_block_alike_in_both_gates(self):
        # AC237: con el alias al final, en medio, anidado en otro ancla o expandido en el sitio, y con el comentario en
        # la cola del elemento, el mismo resultado en las dos puertas.
        self.tercero_retirado()
        corto = f'{self.CORTO} (boe/formas.'
        c, f = '# Terciano se reunió con él', self.FICHA_28
        for caso, contenido in (
                ('el alias al final', f'base: &b {{{f}}}\nitems:\n- *b\n  {c}\n'),
                ('el alias en medio', f'base: &b {{{f}}}\nitems:\n- *b\n  {c}\n- n: 2\n'),
                ('el alias anidado en otro ancla', f'base: &b {{{f}}}\nmid: &m\n  y: *b\nitems:\n- *m\n  {c}\n'),
                ('el alias anidado en un mapa de flujo', f'base: &b {{{f}}}\nitems:\n- {{y: *b}}\n  {c}\n'),
                ('el valor expandido', f'items:\n- {{{f}}}\n  {c}\n')):
            with self.subTest(caso=caso):
                self.exportar_aristas(nodos=(FULDO, self.TERCERO, 'Company:ficcionbar'),
                                      nombres={FULDO: 'Fuldo Fíctez', self.TERCERO: 'Tercero Ficticio'})
                self.con_alias(self.TERCERO, 'Terciano')
                for salida in self.tres_puertas('boe/formas.yaml', contenido)[1:]:
                    self.assertIn(corto, salida)


class AlcanzaLoaderTest(unittest.TestCase):
    """Spec 071 AC48: para afirmar que una regla materializa una decisión, el predicado exacto del Cypher del loader
    (`alcance.alcanza_loader`); `casa`, que normaliza, es un superconjunto para conflictos y tolerancia."""

    ARISTA = {'type': 'ASSOCIATED_WITH', 'start': 'Person:a-ficticia', 'end': 'Person:b-ficticia',
              'props': {'source_kind': 'judicial', 'source_doc': 'auto-ficticio.pdf',
                        'source_quote': 'La Cita  ficticia del auto, de Ramón'}}

    def regla(self, **over):
        return {'source_id': 'Person:a-ficticia', 'target_id': 'Person:b-ficticia', 'type': 'ASSOCIATED_WITH',
                'doc': 'auto-ficticio.pdf', 'quote_contains': None, 'kinds': None, **over}

    def test_the_quote_is_compared_literally_once_lowered(self):
        from src.alcance import alcanza_loader, casa, regla_de, solapan
        un_espacio = self.regla(quote_contains='Cita ficticia')
        self.assertFalse(alcanza_loader(un_espacio, self.ARISTA, 'deny'))
        self.assertTrue(casa(un_espacio, self.ARISTA, exacta=True))
        self.assertTrue(solapan(un_espacio, regla_de(self.ARISTA)))
        self.assertTrue(alcanza_loader(self.regla(quote_contains='CITA  FICTICIA'), self.ARISTA, 'deny'))

    def test_a_decomposed_accent_is_another_quote(self):
        from src.alcance import alcanza_loader, casa
        nfd = self.regla(quote_contains=unicodedata.normalize('NFD', 'de Ramón'))
        self.assertFalse(alcanza_loader(nfd, self.ARISTA, 'deny'))
        self.assertTrue(casa(nfd, self.ARISTA, exacta=True))

    def test_a_deny_has_no_direction_but_a_retype_or_an_invert_does_even_on_a_symmetric_type(self):
        from src.alcance import alcanza_loader, casa
        al_reves = self.regla(source_id='Person:b-ficticia', target_id='Person:a-ficticia')
        self.assertTrue(alcanza_loader(al_reves, self.ARISTA, 'deny'))
        self.assertFalse(alcanza_loader({**al_reves, 'corrected_type': 'MET_WITH'}, self.ARISTA, 'retype'))
        self.assertFalse(alcanza_loader({**al_reves, 'swap': True}, self.ARISTA, 'invert'))
        self.assertTrue(casa(al_reves, self.ARISTA, exacta=True))

    def test_layer_document_and_the_corrected_type_of_a_swap(self):
        from src.alcance import alcanza_loader
        prensa = dict(self.ARISTA, props={**self.ARISTA['props'], 'source_kind': 'prensa'})
        self.assertFalse(alcanza_loader(self.regla(), prensa, 'deny'))
        self.assertTrue(alcanza_loader(self.regla(kinds=['prensa']), prensa, 'deny'))
        self.assertFalse(alcanza_loader(self.regla(doc='otro-auto.pdf'), self.ARISTA, 'deny'))
        self.assertTrue(alcanza_loader(self.regla(doc=None), self.ARISTA, 'deny'))
        # `_SWAP_Q` corre tras el retype: la fila que también retipa invierte las aristas del tipo corregido.
        swap = self.regla(type='WORKS_FOR', corrected_type='ASSOCIATED_WITH', swap=True)
        self.assertTrue(alcanza_loader(swap, self.ARISTA, 'invert'))
        self.assertFalse(alcanza_loader(swap, self.ARISTA, 'retype'))

    def test_a_symmetric_edge_is_matched_in_the_direction_the_census_keeps(self):
        # El loader casa por dirección también en las simétricas, y el grafo no siempre las guarda en la canónica: la
        # regla tiene que ir como la arista, no como el orden de sus ids.
        inversa = dict(self.ARISTA, start='Person:b-ficticia', end='Person:a-ficticia')
        retipo = {'unit_id': 'u', 'action': 'retype', 'new_type': 'MET_WITH'}

        def contexto(regla):
            return ([], [('relations.yaml:fix[0]', regla)]), None, set()

        canonica = self.regla(corrected_type='MET_WITH')
        como_el_censo = self.regla(source_id='Person:b-ficticia', target_id='Person:a-ficticia', corrected_type='MET_WITH')
        self.assertTrue(A._materializada(retipo, inversa, contexto(como_el_censo)))
        self.assertFalse(A._materializada(retipo, inversa, contexto(canonica)))
        self.assertTrue(A._materializada(retipo, self.ARISTA, contexto(canonica)))


class OpacasTest(EscApplyBase):
    """Spec 071 AC114 y AC121: una regla deny opaca (`{unidad, kinds?, reason}`) no nombra su par, su tipo, su documento
    ni su cita; el loader la resuelve contra la raíz privada de la auditoría (`--root` o QA_CENSO_ROOT): la unidad del
    censo con esa clave, como escribe el apply su regla, más lo que la clave declara en `esc/reglas_opacas.yaml`. Sin la
    raíz, con una clave que no resuelve o sin la forma de una opaca, aborta: saltarla resucitaría lo retirado."""

    def preparar(self, filas, raiz=None, completo=False):
        with mock.patch.dict(os.environ):
            os.environ.pop('QA_CENSO_ROOT', None)
            previo, relations_loader.log = relations_loader.log, A._SinLog()
            try:
                return relations_loader._prepare_deny(filas, raiz, **({'completo': True} if completo else {}))
            finally:
                relations_loader.log = previo

    def declarar(self, contenido):
        (self.root / 'esc/reglas_opacas.yaml').write_text(yaml.safe_dump(contenido, allow_unicode=True),
                                                         encoding='utf-8')

    @staticmethod
    def predicado(regla):
        return tuple(regla.get(k) for k in ('source_id', 'target_id', 'type', 'doc', 'quote_contains', 'kinds'))

    def test_an_opaque_deny_resolves_through_the_census_like_the_explicit_rule(self):
        conn = A.connect(self.root)
        value = json.loads(conn.execute('SELECT value FROM units WHERE unit_id=?', (U_EDGE2,)).fetchone()['value'])
        conn.close()
        explicita = self.preparar([{**A.regla_de(value), 'reason': 'x'}])
        opaca = self.preparar([{'unidad': U_EDGE2, 'reason': f'063 [{U_EDGE2[:8]}]: retirada'}], str(self.root))
        self.assertEqual([self.predicado(r) for r in opaca], [self.predicado(r) for r in explicita])
        with mock.patch.dict(os.environ, {'QA_CENSO_ROOT': str(self.root)}):
            previo, relations_loader.log = relations_loader.log, A._SinLog()
            try:
                por_entorno = relations_loader._prepare_deny([{'unidad': U_EDGE2, 'reason': 'x'}])
            finally:
                relations_loader.log = previo
        self.assertEqual([self.predicado(r) for r in por_entorno], [self.predicado(r) for r in explicita])
        for caso, fila, raiz in (('sin la raíz de la auditoría', {'unidad': U_EDGE2, 'reason': 'x'}, None),
                                 ('una clave sin unidad ni declaración', {'unidad': 'f' * 64, 'reason': 'x'},
                                  self.root),
                                 ('una opaca que nombra su par', {'unidad': U_EDGE2, 'source_id': FULDO, 'reason': 'x'},
                                  self.root),
                                 ('una clave sin la forma de 64 hex', {'unidad': U_EDGE2[:8], 'reason': 'x'},
                                  self.root)):
            with self.subTest(caso=caso), self.assertRaises(SystemExit) as salida:
                self.preparar([fila], raiz and str(raiz))
            self.assertEqual(salida.exception.code, 2)

    def test_a_private_declaration_adds_to_its_key_and_a_private_key_needs_its_file(self):
        # AC121: lo declarado en privado SE SUMA a lo que da la unidad del censo (el par actual de una arista
        # re-identificada); una clave privada sin unidad solo tiene lo declarado, y sin el fichero aborta. Un predicado
        # `ajena: true` (el de una regla sin etiqueta de otro hecho hecha opaca bajo esa clave, AC124) se resuelve sin
        # la etiqueta de la unidad, como la regla explícita que sustituye.
        actual = {'source_id': FULDO, 'target_id': 'Company:ficcionbar-actual', 'type': 'PAID',
                  'doc': 'informe-uco.pdf'}
        privada, ajena = 'b' * 64, {'source_id': FULDO, 'target_id': 'Person:otro', 'type': 'MET_WITH', 'ajena': True}
        self.declarar({U_EDGE2: [actual, ajena], privada: [{**actual, 'target_id': 'Person:sin-unidad'}]})
        filas = self.preparar([{'unidad': U_EDGE2, 'declaraciones': 2, 'reason': f'063 [{U_EDGE2[:8]}]: retirada'},
                               {'unidad': privada, 'declaraciones': 1, 'reason': 'retirada'}], str(self.root))
        self.assertEqual([(f['target_id'], f['unidad']) for f in filas],
                         [('Company:ficcionbar', U_EDGE2), ('Company:ficcionbar-actual', U_EDGE2),
                          ('Person:otro', U_EDGE2), ('Person:sin-unidad', privada)])
        self.assertEqual([f'[{U_EDGE2[:8]}]' in f['reason'] for f in filas[:3]], [True, True, False])
        self.assertEqual(filas[2]['reason'], alcance.MOTIVO_AJENA)
        for caso, contenido in (('sin el fichero', None),
                                ('una clave sin la forma de 64 hex', {U_EDGE2[:8]: [actual]}),
                                ('un predicado sin tipo', {privada: [{'source_id': FULDO, 'target_id': 'Person:x'}]})):
            with self.subTest(caso=caso):
                (self.root / 'esc/reglas_opacas.yaml').unlink(missing_ok=True)
                if contenido is not None:
                    self.declarar(contenido)
                with self.assertRaises(SystemExit) as salida:
                    self.preparar([{'unidad': privada, 'reason': 'retirada'}], str(self.root))
                self.assertEqual(salida.exception.code, 2)

    def test_the_apply_names_a_corrected_edge_whole_and_withdraws_a_hidden_third_party_opaquely(self):
        # AC113 y AC114: la fila de una arista que sigue publicada (corregida) nombra sus dos extremos; la retirada que
        # oculta un tercero se escribe opaca, con la etiqueta de su unidad y un motivo neutro, y el loader la resuelve
        # con el censo como la explícita.
        tercero, uid, pack = 'Person:tercero-ficticio', 'd0' * 32, 'E-c0de00000001-109'
        valor = {'start': FULDO, 'end': tercero, 'type': 'ASSOCIATED_WITH',
                 'props': {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf',
                           'source_quote': 'Fuldo coincidió con un tercero ficticio en la reunión'}}
        conn = A.connect(self.root)
        with conn:
            conn.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior) '
                         'VALUES (?,?,?,?,?,0,100,"[]")', (uid, 'o-rel', 'edge', '/edge@9', json.dumps(valor)))
            conn.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,state) VALUES (?,?,?,?,?,?,?)',
                         (pack, FULDO, 'entity', 1, 1, json.dumps([uid]), 'routed'))
        conn.close()

        def filas(accion, **over):
            d = decision(pack_id=pack, units=[unit(uid, verdict='corregido', label='arista')],
                         edges=[edge_op(uid, accion, **over)])
            errors, _ = self.gate(d)
            self.assertEqual(errors, [])
            return A.materialize([d], self.tag, self.upd)

        retirada = filas('deny')
        # La etiqueta de esta tanda no es la de un lote: la opaca no la lleva (AC130).
        self.assertEqual(retirada['deny'], [{'unidad': uid, 'reason': f'[{uid[:8]}]: {A.MOTIVO_OPACO}'}])
        self.assertIn('(Person:[tercero])', retirada['rectificaciones'][0]['dato'])
        self.assertNotIn(tercero, json.dumps(retirada['deny'] + retirada['rectificaciones'], ensure_ascii=False))
        resuelta = self.preparar(retirada['deny'], str(self.root))
        self.assertEqual([(r['source_id'], r['target_id'], r['type']) for r in resuelta],
                         [(FULDO, tercero, 'ASSOCIATED_WITH')])
        corregida = filas('set_prop', prop='note', value='nota corregida')
        self.assertEqual(corregida['rectificaciones'][0]['dato'],
                         f'({FULDO}) -[ASSOCIATED_WITH]-> ({tercero}): arista corregida')

    ACTUAL = {'source_id': FULDO, 'target_id': 'Company:ficcionbar-actual', 'type': 'PAID', 'doc': 'informe-uco.pdf'}

    def test_an_opaque_rule_needs_its_private_file_and_exactly_its_declarations(self):
        # Spec 071 AC127 (17ª pasada de Codex): perder el fichero privado reducía la retirada en silencio. Con una regla
        # opaca el fichero es obligatorio, y cada opaca dice cuántos predicados privados resuelve su clave
        # (`declaraciones`; sin el campo, ninguno): el loader exige exactamente esos. Un fichero que falta, uno vacío o
        # incompleto, una clave con una lista vacía o con un predicado de más abortan con exit 2.
        motivo = f'063 auditoría escalonada 2026-09-26 [{U_EDGE2[:8]}]: {A.MOTIVO_OPACO}'
        otro = {**self.ACTUAL, 'doc': 'otro-documento.pdf'}
        casos = (('sin el fichero', None, 0, False),
                 ('vacío y sin declaraciones', {}, 0, True),
                 ('vacío con una declarada', {}, 1, False),
                 ('exacto', {U_EDGE2: [self.ACTUAL]}, 1, True),
                 ('incompleto', {U_EDGE2: [self.ACTUAL]}, 2, False),
                 ('con uno de más', {U_EDGE2: [self.ACTUAL, otro]}, 1, False),
                 ('una clave con la lista vacía', {U_EDGE2: []}, 0, False))
        for caso, contenido, n, resuelve in casos:
            with self.subTest(caso=caso):
                (self.root / alcance.REGLAS_OPACAS).unlink(missing_ok=True)
                if contenido is not None:
                    self.declarar(contenido)
                regla = {'unidad': U_EDGE2, **({'declaraciones': n} if n else {}), 'reason': motivo}
                if resuelve:
                    self.assertEqual(len(self.preparar([regla], str(self.root))), 1 + n)
                    continue
                with self.assertRaises(SystemExit) as salida:
                    self.preparar([regla], str(self.root))
                self.assertEqual(salida.exception.code, 2)

    def test_a_key_the_private_file_declares_and_no_opaque_rule_resolves_stops_the_loader_and_the_checks(self):
        # AC127: una clave que el fichero privado declara y ninguna regla opaca del overlay resuelve es una retirada que
        # no se aplica: el loader, con el overlay entero, y los dos checks, que leen las reglas como él, paran con exit
        # 2.
        self.declarar({U_EDGE2: [self.ACTUAL], 'c' * 64: [self.ACTUAL]})
        regla = {'unidad': U_EDGE2, 'declaraciones': 1, 'reason': A.MOTIVO_OPACO}
        self.assertEqual(len(self.preparar([regla], str(self.root))), 2)
        with self.assertRaises(SystemExit) as salida:
            self.preparar([regla], str(self.root), completo=True)
        self.assertEqual(salida.exception.code, 2)
        reglas = self.repo / A.REL_PATH
        reglas.write_text(reglas.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        texto, _ = A._write_relations(reglas.read_text(encoding='utf-8'), {'deny': [regla], 'fix': [],
                                                                           'alias_deny': []}, self.tag)
        reglas.write_text(texto, encoding='utf-8')
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as salida:
            A._reglas_del_overlay(self.repo, self.root)
        self.assertEqual(salida.exception.code, 2)
        self.assertIn('declaraciones privadas de una clave que ninguna regla opaca resuelve', err.getvalue())
        self.declarar({U_EDGE2: [self.ACTUAL]})
        self.assertEqual(len([r for _, r in A._reglas_del_overlay(self.repo, self.root)[0] if r.get('unidad')]), 2)

    def test_a_merge_key_stops_the_overlay_and_the_private_file(self):
        # Spec 071 AC128: una clave de fusión `<<` mezcla en una regla lo que dice otra sin que se vea; en el overlay y
        # en el fichero privado, exit 2 con el fichero y la posición, nunca el valor.
        (self.repo / A.REL_PATH).write_text('base: &b {source_id: Person:centinela-fusion, target_id: Company:x, '
                                            'type: PAID}\ndeny:\n  - <<: *b\n    reason: x\n', encoding='utf-8')
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as salida:
            relations_loader.load_files(self.repo / 'pipeline/data/relations')
        self.assertEqual(salida.exception.code, 2)
        self.assertIn('YAML inválido (línea 3', err.getvalue())
        self.assertNotIn('centinela', err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as salida:
            A._reglas_del_overlay(self.repo, self.root)
        self.assertEqual(salida.exception.code, 2)
        (self.root / alcance.REGLAS_OPACAS).write_text(
            f'base: &b {{source_id: Person:centinela-fusion, target_id: {FULDO}, type: PAID}}\n{"d" * 64}:\n'
            '  - <<: *b\n', encoding='utf-8')
        with self.assertRaises(alcance.ReglaOpacaInvalida) as error:
            alcance.Opacas(self.root)
        self.assertNotIn('centinela', str(error.exception))

    def test_a_private_predicate_is_prepared_like_an_explicit_deny(self):
        # Spec 071 AC129: los extremos y el tipo de un predicado privado pasan por la misma preparación que una deny
        # explícita (`_resolve`: el id sin blancos; el tipo, también): con blancos alrededor, la explícita y la privada
        # retiran la misma arista.
        con_blancos = {'source_id': f'  {FULDO} ', 'target_id': ' Company:ficcionbar-actual ', 'type': ' PAID ',
                       'doc': 'informe-uco.pdf'}
        self.declarar({'b' * 64: [con_blancos]})
        explicita = self.preparar([{**con_blancos, 'reason': 'x'}])
        privada = self.preparar([{'unidad': 'b' * 64, 'declaraciones': 1, 'reason': A.MOTIVO_OPACO}], str(self.root))
        self.assertEqual([self.predicado(r) for r in privada], [self.predicado(r) for r in explicita])
        arista = {'type': 'PAID', 'start': FULDO, 'end': 'Company:ficcionbar-actual',
                  'props': {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf', 'source_quote': 'x'}}
        self.assertTrue(alcance.alcanza_loader(privada[0], arista, 'deny'))

    def test_a_private_wildcard_is_an_absent_field_never_an_empty_one(self):
        # Spec 071 AC151 (18a-1): el loader lee `doc: ''` como el documento vacío (solo casa una arista escrita así), no
        # como un comodín, y un trozo de cita vacío casa cualquier cita. En el fichero privado un comodín es un campo
        # ausente: un documento, un trozo de cita o unas capas vacíos, en blanco o nulos no valen (exit 2).
        base = {'source_id': FULDO, 'target_id': 'Company:ficcionbar-actual', 'type': 'PAID'}
        regla = {'unidad': 'b' * 64, 'declaraciones': 1, 'reason': A.MOTIVO_OPACO}
        for caso, campos in (('documento vacío', {'doc': ''}), ('documento en blanco', {'doc': '  '}),
                             ('documento nulo', {'doc': None}), ('trozo vacío', {'quote_contains': ''}),
                             ('capas vacías', {'kinds': []}), ('capas nulas', {'kinds': None})):
            with self.subTest(caso=caso):
                self.declarar({'b' * 64: [{**base, **campos}]})
                with self.assertRaises(SystemExit) as salida:
                    self.preparar([regla], str(self.root))
                self.assertEqual(salida.exception.code, 2)
        self.declarar({'b' * 64: [base]})
        self.assertEqual(len(self.preparar([regla], str(self.root))), 1)

    def test_a_private_predicate_is_resolved_exactly_as_its_explicit_deny(self):
        # AC151: el predicado privado se resuelve tal como se declaró, capas incluidas (no toma las de la opaca: sin
        # ellas, judicial o nula, como la deny explícita sin `kinds`), y alcanza lo mismo que su deny explícita en
        # aristas de los documentos '', 'x.pdf', 'otro.pdf' y ninguno, de cualquier capa y en los dos sentidos.
        otro = 'Person:otro-ficticio'
        predicados = ({'source_id': FULDO, 'target_id': otro, 'type': 'MET_WITH', 'doc': 'x.pdf'},
                      {'source_id': FULDO, 'target_id': otro, 'type': 'MET_WITH'},
                      {'source_id': FULDO, 'target_id': otro, 'type': 'MET_WITH', 'kinds': ['prensa'],
                       'quote_contains': 'hotel'})
        self.declarar({'b' * 64: list(predicados)})
        opacas = self.preparar([{'unidad': 'b' * 64, 'declaraciones': 3, 'kinds': ['acusacion'],
                                 'reason': A.MOTIVO_OPACO}], str(self.root))
        explicitas = self.preparar([{**p, 'reason': 'x'} for p in predicados])
        self.assertEqual([self.predicado(r) for r in opacas], [self.predicado(r) for r in explicitas])
        aristas = [{'type': 'MET_WITH', 'start': a, 'end': b, 'props': {'source_kind': capa, 'source_doc': doc,
                                                                         'source_quote': 'en el hotel ficticio'}}
                   for doc in ('', 'x.pdf', 'otro.pdf', None) for capa in ('judicial', 'acusacion', 'prensa')
                   for a, b in ((FULDO, otro), (otro, FULDO))]
        for opaca, explicita in zip(opacas, explicitas):
            self.assertEqual([alcance.alcanza_loader(opaca, v, 'deny') for v in aristas],
                             [alcance.alcanza_loader(explicita, v, 'deny') for v in aristas])

    def test_privatizar_never_declares_an_empty_document_in_private(self):
        # AC151: una deny explícita con `doc: ''` no puede pasar a lo privado (allí un comodín es un campo ausente, y
        # sin el documento retiraría de más): privatizar para con exit 2.
        explicita = {'source_id': FULDO, 'target_id': 'Person:otro-ficticio', 'type': 'MET_WITH', 'doc': '',
                     'quote_contains': None, 'kinds': None, 'reason': 'x'}
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as salida:
            A._predicado_privado(explicita)
        self.assertEqual(salida.exception.code, 2)
        self.assertEqual(A._predicado_privado({**explicita, 'doc': 'x.pdf'}),
                         {'source_id': FULDO, 'target_id': 'Person:otro-ficticio', 'type': 'MET_WITH', 'doc': 'x.pdf'})

    def test_an_opaque_key_needs_an_identity_of_its_own(self):
        # Spec 071 AC157 (19a-1): una clave opaca dice qué retira por su arista del censo o por un predicado privado
        # suyo; con solo predicados `ajena` (otros hechos que se hicieron opacos bajo ella) no retira nada propio y
        # ninguna comprobación de su fila tendría qué mirar: el loader aborta. Con uno suyo además, o siendo una unidad
        # del censo, resuelve.
        clave = 'b7' * 32
        ajena = {'source_id': FULDO, 'target_id': 'Person:otro-ficticio', 'type': 'MET_WITH', 'ajena': True}
        propio = {'source_id': FULDO, 'target_id': 'Person:sin-unidad', 'type': 'PAID'}
        for caso, declaradas, regla, resuelve in (
                ('solo ajenos', {clave: [ajena]}, {'unidad': clave, 'declaraciones': 1}, None),
                ('uno suyo además', {clave: [ajena, propio]}, {'unidad': clave, 'declaraciones': 2}, 2),
                ('una unidad del censo con solo ajenos', {U_EDGE2: [ajena]}, {'unidad': U_EDGE2, 'declaraciones': 1},
                 2)):
            with self.subTest(caso=caso):
                self.declarar(declaradas)
                filas = []
                salida = _salida_de(lambda: filas.extend(self.preparar([{**regla, 'reason': A.MOTIVO_OPACO}],
                                                                       str(self.root))))
                if resuelve:
                    self.assertEqual((salida, len(filas)), (None, resuelve))
                else:
                    self.assertEqual(salida, 2)


def _salida_de(llamada):
    """El exit de `llamada` (SystemExit), la clase de otra excepción o None si termina: un test que espera exit 2 falla
    por lo que hace, nunca con un error."""
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            llamada()
    except SystemExit as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001 (lo que hiciera el commit anterior: se compara, no se propaga)
        return type(exc).__name__
    return None


class CargadorTest(EscApplyBase):
    """Spec 071 AC157, AC159 y AC160 (19a): el loader de relaciones aplica un overlay con reglas opacas entero o nada,
    valida la forma de cada entrada deny antes de clasificarla y no acepta una clave opaca sin identidad propia."""

    def setUp(self):
        super().setUp()
        self.dir = self.base / 'overlay'
        self.dir.mkdir()
        # Spec 070: el loader no arranca sin la lista de permitidas de los seudónimos (sin ella, la denegación por
        # defecto lo retiraría todo); la del repo, que vale contra su roster.
        shutil.copy(REPO / seudonimos.PERMITIDOS_REL, self.dir)
        previo, relations_loader.log = relations_loader.log, A._SinLog()
        self.addCleanup(setattr, relations_loader, 'log', previo)

    EXPLICITA = {'source_id': FULDO, 'target_id': 'Company:ficcionbar', 'type': 'PAID', 'reason': 'x'}

    def test_a_deny_row_with_neither_form_stops_the_loader_and_the_checks(self):
        # AC159 (19a-3): una entrada deny que no es ni una opaca exacta ni una explícita completa (una variante de
        # `unidad`, una clave desconocida, sin tipo o con uno fuera de la lista, un extremo sin resolver, algo que no es
        # un mapa) ya no se descarta en silencio: exit 2 en el loader (al leer los ficheros y al preparar las reglas) y
        # en los checks, que leen el overlay como él, con la posición.
        motivo = f'063 auditoría escalonada 2026-09-24 [{U_EDGE2[:8]}]: {A.MOTIVO_OPACO}'
        casos = (('Unidad', {'Unidad': U_EDGE2, 'reason': motivo}),
                 ('unidad con un blanco', {' unidad': U_EDGE2, 'reason': motivo}),
                 ('una clave desconocida', {**self.EXPLICITA, 'nota': 'x'}),
                 ('sin tipo', {k: v for k, v in self.EXPLICITA.items() if k != 'type'}),
                 ('un tipo fuera de la lista', {**self.EXPLICITA, 'type': 'NO_ES_UN_TIPO'}),
                 ('un extremo sin resolver', {**self.EXPLICITA, 'source_id': ''}),
                 ('no es un mapa', 'deny suelta'))
        base = (self.repo / A.REL_PATH).read_text(encoding='utf-8')
        for caso, fila in casos:
            with self.subTest(caso=caso):
                self.assertEqual(_salida_de(lambda: relations_loader._prepare_deny([fila], str(self.root))), 2)
                (self.dir / 'relations.yaml').write_text(yaml.safe_dump({'deny': [fila]}), encoding='utf-8')
                self.assertEqual(_salida_de(lambda: relations_loader.load_files(self.dir)), 2)
                doc = yaml.safe_load(base)
                doc['deny'].append(fila)
                (self.repo / A.REL_PATH).write_text(yaml.safe_dump(doc, allow_unicode=True), encoding='utf-8')
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    salida = _salida_de(lambda: A._reglas_del_overlay(self.repo, self.root))
                self.assertEqual(salida, 2)
        self.assertEqual(_salida_de(lambda: relations_loader._prepare_deny([self.EXPLICITA], str(self.root))), None)

    def main_con(self, deny, *banderas, raiz='--root'):
        """El loader entero (`relations_loader.main`) sobre un overlay con estas deny y una fix, sin grafo: los pasos
        que lo tocan, simulados; la raíz privada, por `--root` o por QA_CENSO_ROOT (`raiz`). (resultado de
        `_salida_de`, {paso: llamadas}); las conexiones al grafo que abrió, en `self.conexiones`."""
        fix = [{'source_id': FULDO, 'target_id': 'Company:ficcionbar', 'type': 'PAID', 'corrected_type': 'OWNS',
                'reason': 'retipo'}]
        (self.dir / 'relations.yaml').write_text(yaml.safe_dump({'deny': deny, 'fix': fix}), encoding='utf-8')
        argv = ['relations_loader', '--dir', str(self.dir), *(('--root', str(self.root)) if raiz == '--root' else ()),
                *banderas]
        pasos = {}
        with mock.patch.object(sys, 'argv', argv), mock.patch.dict(os.environ), mock.patch('structlog.configure'), \
                mock.patch.object(relations_loader.neo4j_loader, 'get_driver') as conexion, \
                mock.patch.object(relations_loader.neo4j_loader, 'compute_degree'):
            os.environ.pop('QA_CENSO_ROOT', None)
            if raiz == 'QA_CENSO_ROOT':
                os.environ['QA_CENSO_ROOT'] = str(self.root)
            for paso in ('apply_split', 'apply_fix', 'apply_deny', 'apply_alias_deny'):
                pasos[paso] = mock.patch.object(relations_loader, paso).start()
            try:
                resultado = _salida_de(relations_loader.main)
            finally:
                mock.patch.stopall()
        self.conexiones = conexion.call_count
        return resultado, {paso: simulado.call_count for paso, simulado in pasos.items()}

    def test_the_last_opaque_rule_gone_with_its_private_declarations_stops_the_loader(self):
        # Spec 071 AC171 (20a): el loader construía el resolutor de las opacas al encontrar la primera, así que, si
        # desaparece la última del overlay (y quedan sus deny explícitas y sus fix), la clave que el fichero privado
        # sigue declarando (una retirada que ya no se aplica) pasaba. Con la raíz privada, por `--root` o por
        # QA_CENSO_ROOT, se contrasta siempre: exit 2 antes de abrir el grafo, sin ninguna fase aplicada y sin el valor
        # en el log; igual una entrada de `filas:` que no es una fila del registro de su mismo árbol que la admita.
        # Vacío, o sin raíz, carga como siempre.
        predicado = {'source_id': FULDO, 'target_id': 'Person:otro-centinela', 'type': 'MET_WITH'}
        fila, nada = 'R-2026-10-01-0009', {'apply_split': 0, 'apply_fix': 0, 'apply_deny': 0, 'apply_alias_deny': 0}
        opaca = {'unidad': U_EDGE2, 'declaraciones': 1, 'reason': f'[{U_EDGE2[:8]}]: {A.MOTIVO_OPACO}'}
        registro = self.base / 'rectificaciones/registro.yaml'
        registro.parent.mkdir()
        for raiz in ('--root', 'QA_CENSO_ROOT'):
            registro.unlink(missing_ok=True)
            (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump({U_EDGE2: [predicado]}), encoding='utf-8')
            with self.subTest(raiz=raiz, caso='con su opaca'):
                resultado, pasos = self.main_con([opaca, self.EXPLICITA], raiz=raiz)
                self.assertEqual((resultado, pasos['apply_fix'], pasos['apply_deny']), (None, 1, 1))
            for caso, contenido, decision in (('la última opaca, fuera', {U_EDGE2: [predicado]}, None),
                                              ('una fila sin registro', {'filas': {fila: [predicado]}}, None),
                                              ('una fila «rectificado» sin marcador', {'filas': {fila: [predicado]}},
                                               'rectificado')):
                (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump(contenido), encoding='utf-8')
                registro.unlink(missing_ok=True)
                if decision:
                    registro.write_text(yaml.safe_dump({'rectificaciones': [
                        {'id': fila, 'unidad': '', 'decision': decision, 'dato': 'arista retirada'}]}),
                        encoding='utf-8')
                with self.subTest(raiz=raiz, caso=caso), mock.patch.object(relations_loader, 'log') as log:
                    resultado, pasos = self.main_con([self.EXPLICITA], raiz=raiz)
                    self.assertEqual((resultado, pasos, self.conexiones), (2, nada, 0))
                    for valor in (U_EDGE2[:8], fila, 'centinela'):
                        self.assertNotIn(valor, str(log.mock_calls))
            # AC183: la cobertura de `filas:` es exacta, en las dos direcciones, con las filas sin unidad y con
            # marcador; una «suprimido» sin marcador se comprueba con su regla pública y no lleva declaración.
            marcada = {'id': fila, 'unidad': '', 'decision': 'suprimido',
                       'dato': f'({FULDO}) -[MET_WITH]-> (Person:[tercero]): arista retirada'}
            sin_marcador = {'id': fila, 'unidad': '', 'decision': 'suprimido', 'dato': 'arista retirada'}
            for caso, filas, contenido, resultado_esperado in (
                    ('una fila con marcador sin su declaración', [marcada], {}, 2),
                    ('una declaración de una «suprimido» sin marcador', [sin_marcador], {'filas': {fila: [predicado]}},
                     2),
                    ('vacío, con una «suprimido» sin marcador', [sin_marcador], {}, None),
                    ('una fila con marcador y su declaración', [marcada], {'filas': {fila: [predicado]}}, None)):
                registro.write_text(yaml.safe_dump({'rectificaciones': filas}), encoding='utf-8')
                (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump(contenido), encoding='utf-8')
                with self.subTest(raiz=raiz, caso=caso):
                    resultado, pasos = self.main_con([self.EXPLICITA], raiz=raiz)
                    if resultado_esperado:
                        self.assertEqual((resultado, pasos, self.conexiones), (2, nada, 0))
                    else:
                        self.assertEqual((resultado, pasos['apply_fix'], pasos['apply_deny']), (None, 1, 1))
        (self.root / alcance.REGLAS_OPACAS).write_text(yaml.safe_dump({U_EDGE2: [predicado]}), encoding='utf-8')
        with self.subTest(caso='sin raíz'), mock.patch.dict(os.environ):
            os.environ.pop('QA_CENSO_ROOT', None)
            self.assertEqual(len(relations_loader._prepare_deny([self.EXPLICITA], None, completo=True)), 1)

    def test_a_lost_declaration_of_a_marked_row_stops_the_loader_before_the_graph(self):
        # Spec 071 AC183 (21a-1, los centinelas de Codex): con dos filas sin unidad con marcador en el registro del
        # mismo árbol, perder las declaraciones de las dos (sin fichero, vacío, `{}` o `filas: {}`: pérdida total) o la
        # de una (parcial) para el loader con exit 2 antes de abrir el grafo, sin ninguna fase y sin valores en el log,
        # con la raíz por `--root` o por QA_CENSO_ROOT. Con las dos, aplica.
        predicado = {'source_id': FULDO, 'target_id': 'Person:otro-centinela', 'type': 'MET_WITH'}
        filas = ('R-2026-10-01-0008', 'R-2026-10-01-0009')
        nada = {'apply_split': 0, 'apply_fix': 0, 'apply_deny': 0, 'apply_alias_deny': 0}
        registro = self.base / 'rectificaciones/registro.yaml'
        registro.parent.mkdir()
        registro.write_text(yaml.safe_dump({'rectificaciones': [
            {'id': fila, 'unidad': '', 'decision': 'suprimido',
             'dato': f'({FULDO}) -[MET_WITH]-> (Person:[tercero]): arista retirada'} for fila in filas]}),
            encoding='utf-8')
        privado = self.root / alcance.REGLAS_OPACAS
        for raiz in ('--root', 'QA_CENSO_ROOT'):
            for caso, contenido in (('sin fichero', None), ('vacío', ''), ('{}', '{}\n'), ('filas: {}', 'filas: {}\n'),
                                    ('la de una sola', yaml.safe_dump({'filas': {filas[1]: [predicado]}}))):
                privado.unlink(missing_ok=True)
                if contenido is not None:
                    privado.write_text(contenido, encoding='utf-8')
                with self.subTest(raiz=raiz, caso=caso), mock.patch.object(relations_loader, 'log') as log:
                    resultado, pasos = self.main_con([self.EXPLICITA], raiz=raiz)
                    self.assertEqual((resultado, pasos, self.conexiones), (2, nada, 0))
                    for valor in (*filas, 'centinela'):
                        self.assertNotIn(valor, str(log.mock_calls))
            privado.write_text(yaml.safe_dump({'filas': {fila: [predicado] for fila in filas}}), encoding='utf-8')
            with self.subTest(raiz=raiz, caso='las dos'):
                resultado, pasos = self.main_con([self.EXPLICITA], raiz=raiz)
                self.assertEqual((resultado, pasos['apply_fix'], pasos['apply_deny']), (None, 1, 1))

    def test_an_overlay_with_opaque_rows_is_applied_whole_or_not_at_all(self):
        # AC160 (19a-4): con una regla opaca en el overlay, cualquier bandera que deje fuera una parte (`--no-deny`,
        # `--no-fix`, `--no-splits`, `--no-alias-deny`) aborta con exit 2 antes de tocar el grafo; sin opacas, valen.
        opaca = {'unidad': U_EDGE2, 'reason': f'063 auditoría escalonada 2026-09-24 [{U_EDGE2[:8]}]: {A.MOTIVO_OPACO}'}
        for bandera in ('--no-deny', '--no-fix', '--no-splits', '--no-alias-deny'):
            with self.subTest(bandera=bandera):
                resultado, pasos = self.main_con([opaca], bandera)
                self.assertEqual(resultado, 2)
                self.assertEqual(pasos, {'apply_split': 0, 'apply_fix': 0, 'apply_deny': 0, 'apply_alias_deny': 0})
        resultado, pasos = self.main_con([opaca])
        self.assertEqual((resultado, pasos['apply_fix'], pasos['apply_deny']), (None, 1, 1))
        resultado, pasos = self.main_con([self.EXPLICITA], '--no-deny')
        self.assertEqual((resultado, pasos['apply_fix'], pasos['apply_deny']), (None, 1, 0))


def _censo_real():
    raiz = Path(os.environ.get('REGISTRO_ROOT') or '/nonexistent')
    return raiz if (raiz / 'census.sqlite').is_file() else None


@unittest.skipUnless(_censo_real(), 'sin el censo privado de la 063 (REGISTRO_ROOT)')
class MaterializacionRealTest(unittest.TestCase):
    """Spec 071 AC53 con la decisión y la regla reales de fdf3f1e3 (el retype de una ASSOCIATED_WITH que el censo guarda
    al revés de la canónica, con su regla en la dirección del censo) y un export sintético donde la arista sigue viva
    en la canónica: la regla no la acredita. En qa067b sí se aplicó; el hueco se prueba aquí. Solo nombra la unidad."""

    def test_the_real_rule_of_fdf3f1e3_does_not_cover_it_while_it_is_alive(self):
        raiz = _censo_real()
        conn = sqlite3.connect(f'file:{raiz / "census.sqlite"}?mode=ro', uri=True)
        try:
            (uid, valor), = conn.execute("SELECT unit_id, value FROM units WHERE unit_id LIKE 'fdf3f1e3%'").fetchall()
        finally:
            conn.close()
        valor = json.loads(valor)
        snap = Path(tempfile.mkdtemp(prefix='materializacion-real-'))
        self.addCleanup(shutil.rmtree, snap, ignore_errors=True)
        refs = {valor['start']: '4:x:1', valor['end']: '4:x:2'}
        (snap / 'nodes.jsonl').write_text(''.join(
            json.dumps({'labels': [eid.split(':')[0]], 'props': {'entity_id': eid}, 'ref': ref}) + '\n'
            for eid, ref in refs.items()), encoding='utf-8')
        (snap / 'relationships.jsonl').write_text(json.dumps(
            {'start': refs[valor['end']], 'end': refs[valor['start']], 'type': valor['type'], 'props': valor['props'],
             'ref': '5:x:1'}, ensure_ascii=False) + '\n', encoding='utf-8')
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            A.main(['--root', str(raiz), '--repo', str(REPO), 'materializacion', '--snapshot', str(snap)])
        self.assertIn(f'MATERIALIZACION: retirada decidida sin materializar ({uid[:8]}, ', out.getvalue())


class RealOverlayTest(unittest.TestCase):
    """El insertador de secciones, contra el relations.yaml de verdad (solo lectura)."""

    def test_sections_of_the_real_relations_yaml(self):
        text = (REPO / A.REL_PATH).read_text(encoding='utf-8')
        before = yaml.safe_load(text)
        row = {'source_id': 'Person:fuldo-fictez', 'target_id': 'Company:ficcionbar', 'type': 'PAID',
               'doc': 'informe-uco.pdf', 'quote_contains': 'pago de 5.700 euros',
               'reason': '063 test (no se escribe)'}
        out = text
        for section in ('deny', 'fix', 'alias_deny'):
            block = {'id': 'Person:fuldo-fictez', 'drop': ['Fuldo'], 'reason': 'x'} if section == 'alias_deny' else row
            out = A._insert_into_section(out, section, '\n  # 063 test\n' + A._indented(block))
        after = yaml.safe_load(out)
        # AC53 (spec 070): al fallar, nada de relations.yaml sale al log público; recuentos y motivos fijos.
        for section in ('deny', 'fix', 'alias_deny'):
            self.assertEqual(len(after[section]), len(before[section]) + 1, section)
        self.assertTrue(after['deny'][-1]['source_id'] == 'Person:fuldo-fictez', 'deny: la fila no quedó al final')
        self.assertTrue(after['fix'][-1]['type'] == 'PAID', 'fix: la fila no quedó al final')
        self.assertTrue(after['alias_deny'][-1]['drop'] == ['Fuldo'], 'alias_deny: la fila no quedó al final')
        self.assertTrue(after['splits'] == before['splits'], 'el insertador tocó los splits')
        self.assertEqual(sha(REPO / A.REL_PATH), hashlib.sha256(text.encode()).hexdigest())

    def test_the_shipped_registry_is_loadable_and_well_formed(self):
        # AC53 (spec 070): al fallar, ningún valor del registro sale al log público; la posición de la fila y el motivo.
        data = yaml.safe_load((REPO / A.REG_PATH).read_text(encoding='utf-8'))
        self.assertTrue(list(data) == ['rectificaciones'], 'el registro no es solo `rectificaciones`')
        rows = data['rectificaciones'] or []
        keys = ['id', 'fecha', 'origen', 'entidad', 'unidad', 'dato', 'fuente', 'decision', 'fecha_resolucion',
                'seed_version']
        ids = [row['id'] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        for i, row in enumerate(rows):
            self.assertTrue(list(row) == keys, f'fila {i}: claves fuera del esquema')
            self.assertTrue(row['decision'] in ('rectificado', 'suprimido', 'desestimado'), f'fila {i}: decision')
            self.assertTrue(row['origen'] in ('auditoria-063', 'auditoria-067', 'solicitud'), f'fila {i}: origen')
            # La fórmula de una asociación que el grafo sigue publicando se añade al dato (spec 071 D1 y AC142).
            self.assertLessEqual(len(row['dato']), 250, f'fila {i}')


    def test_no_fix_of_the_real_overlay_reaches_a_deny(self):
        # Spec 071 AC40: el único conflicto que hubo (una regla del enjambre que retipaba aristas que un deny de la 063
        # retira) se resolvió retirando la regla del enjambre: la denegación manda. Las reglas opacas del overlay real
        # se resuelven contra la raíz privada de la auditoría, con su fichero privado (AC127): sin ella, no se puede.
        raiz = Path(os.environ.get('REGISTRO_ROOT') or REPO / 'pipeline/data/audit/dual/20260909-20260909-qa061')
        opacas = any(alcance.es_opaca(regla) for path in (REPO / 'pipeline/data/relations').glob('relations*.yaml')
                     for regla in (yaml.safe_load(path.read_text(encoding='utf-8')) or {}).get('deny') or [])
        if opacas and not (raiz / alcance.REGLAS_OPACAS).is_file():
            self.skipTest('el overlay real tiene reglas opacas y no está la raíz privada de la auditoría')
        self.assertEqual(A._guarda_fix_deny({}, REPO, raiz=raiz if opacas else None), [])


class VigilanciaDeRelojesTest(_plazo_duro.PlazoDuro, unittest.TestCase):
    """AC253, AC254 (enmienda 58). AC299, AC302 (enmiendas 73, 74): cada test corre bajo el plazo duro de proceso, igual que los de
    `GuardaDeRelojesTest` (`test_seudonimos.py`, donde están sus controles): lo arma `run` del mixin `_plazo_duro.PlazoDuro` (primero
    en las bases; esta clase no sobrescribe `run`, ni define `setUp`/`tearDown` que toquen el plazo)."""

    def test_la_guarda_de_relojes_esta_instalada_y_actua_en_este_proceso(self):
        """AC253, AC254 (enmienda 58): este módulo importa la guarda en su primera línea; en este proceso está instalada
        y un fichero de `pipeline/tests` no lee ningún reloj."""
        self.assertTrue(_vigilancia.instalada())
        self.assertIsInstance(lectura_desde_un_fichero_de_pruebas(), AssertionError)


if __name__ == '__main__':
    unittest.main()


class ClaimsCheckTest(unittest.TestCase):
    """Una postura de afirmación enfrentada se retira en claims.yaml, no en el overlay de relaciones."""

    def _yaml(self, quote):
        import tempfile
        path = Path(tempfile.mkdtemp()) / 'claims.yaml'
        path.write_text('claims:\n  - claim_id: c1\n    text: t\n    disputes:\n'
                        f'      - person: X\n        entity_id: Person:x\n        quote: "{quote}"\n')
        return path

    def _row(self, action='deny'):
        return {'entity_id': 'Person:x', 'type': 'DISPUTES', 'action': action,
                'quote': 'X niega todo.', 'ref': 'P.json', 'unit': 'abcd1234'}

    def test_retired_posture_still_in_yaml_fails(self):
        from src import qa_esc_apply as A
        errors = A._claims_check([self._row()], self._yaml('X niega todo.'))
        self.assertEqual(len(errors), 1)
        self.assertIn('sigue en claims.yaml', errors[0])

    def test_retired_posture_gone_from_yaml_passes(self):
        from src import qa_esc_apply as A
        self.assertEqual(A._claims_check([self._row()], self._yaml('Otra cosa.')), [])

    def test_other_actions_on_postures_are_manual(self):
        from src import qa_esc_apply as A
        errors = A._claims_check([self._row('set_prop')], self._yaml('Otra cosa.'))
        self.assertIn('a mano', errors[0])


class ArbitrajeSinValoresTest(unittest.TestCase):
    def test_el_error_del_arbitraje_no_cita_valores(self):
        # AC61 (enmienda 14): `validate_arbitration` citaba `choice_id` con `!r`, y los 8 primeros caracteres de un
        # `unit_id` ajeno al pack. Ahora el valor de vocabulario o su huella, y el prefijo solo de una unidad del pack.
        from src import qa_esc_schema
        uid, centinela, ajena = 'a' * 64, 'CHOICE_PRIVADO_CENTINELA', 'UID_PRIVADO_CENTINELA'
        fila = {'unit_id': uid, 'choice_id': centinela, 'text': '', 'sostenido': True, 'evidence': 'e',
                'citas': [], 'al_dueno': False, 'motivo': ''}
        errores = qa_esc_schema.validate_arbitration({'units': [fila, {**fila, 'unit_id': ajena}]},
                                                     {'units': [{'unit_id': uid, 'kind': 'summary'}]}, {})
        self.assertIn('unidad aaaaaaaa: choice_id: valor fuera del vocabulario', errores)
        self.assertIn('units[2]: la unidad no pertenece al pack', errores)
        for texto in (centinela, ajena[:8]):
            self.assertNotIn(texto, ' '.join(errores))


class CastValueTest(unittest.TestCase):
    def test_source_page_es_entero(self):
        from pipeline.src.qa_esc_apply import _cast_value
        self.assertEqual(_cast_value('source_page', '81'), 81)
        self.assertIsInstance(_cast_value('source_page', ' 7 '), int)


class SkipAppliedTest(unittest.TestCase):
    def test_las_decisiones_de_packs_aplicados_no_se_reaplican(self):
        import sqlite3, tempfile
        from pathlib import Path
        from pipeline.src import qa_esc_apply
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / 'census.sqlite')
            conn.execute('CREATE TABLE esc_packs (pack_id TEXT, state TEXT)')
            conn.executemany('INSERT INTO esc_packs VALUES (?,?)', [('P1', 'applied'), ('P2', 'closed')])
            conn.commit(); conn.close()
            ds = [{'_meta': {'pack_id': 'P1'}}, {'_meta': {'pack_id': 'P2'}}]
            out = qa_esc_apply.skip_applied(tmp, ds)
        self.assertEqual([d['_meta']['pack_id'] for d in out], ['P2'])
