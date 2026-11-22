"""Seudónimos judiciales (spec 070): denegación por defecto con excepciones públicas positivas.

Los autos del Tribunal Supremo sustituyen el nombre real de cada investigado por un nombre de pila
ficticio (`context/pseudonyms.yaml`). Un seudónimo solo conserva las aristas con sociedades u
organismos y los alias que enumera `relations/seudonimos_permitidos.yaml`; `make relations` retira
todo lo demás, de cualquier capa. Ningún fichero público lista lo retirado.

Predicado compartido por el loader de relaciones, el lint L26, el apply de la 063, el check del CI y
la puerta del Path A: una sola enumeración de ficheros del overlay y una sola resolución de extremos.
Solo stdlib + PyYAML, para que el CI lo importe sin las dependencias del pipeline.
"""
from __future__ import annotations

import array
import bisect
from collections import Counter, namedtuple
import fnmatch
import functools
import hashlib
import heapq
import itertools
import json
import math
import operator
from pathlib import Path
import re
import subprocess
import unicodedata
import urllib.parse

import yaml

from . import alcance
from .entity_ids import entity_id
from .ontologia import ontologia

PIPELINE = Path(__file__).resolve().parents[1]
REPO = PIPELINE.parent
PSEUDONYMS_REL = 'pipeline/data/context/pseudonyms.yaml'
RELATIONS_REL = 'pipeline/data/relations'
PERMITIDOS_NOMBRE = 'seudonimos_permitidos.yaml'
PERMITIDOS_REL = f'{RELATIONS_REL}/{PERMITIDOS_NOMBRE}'
REGISTRO_REL = 'pipeline/data/rectificaciones/registro.yaml'
PSEUDONYMS_PATH = REPO / PSEUDONYMS_REL
RELATIONS_DIR = REPO / RELATIONS_REL
PERMITIDOS_PATH = REPO / PERMITIDOS_REL

# El otro extremo que convierte una arista de seudónimo en sensible, por el prefijo de su entity_id. La política del
# loader lo reconoce por etiqueta, así que un nodo de esas etiquetas sin entity_id también lo es (el snapshot lo
# denuncia).
SENSIBLES = ('Company:', 'Organization:')
ETIQUETAS_SENSIBLES = frozenset({'Company', 'Organization'})
SECCIONES = ('splits', 'deny', 'fix', 'alias_deny')
SECCIONES_PERMITIDOS = ('aristas', 'alias')
# AC90: la sección de las citas admitidas es opcional (ausente = ninguna): faltar solo deniega más.
SECCIONES_OPCIONALES = ('citas',)
# Las claves de cada entrada, cerradas: una errata (`doc`, `kind`) se leería como «cualquier documento/capa».
CLAVES_ARISTA = ('seudonimo', 'otro', 'tipo', 'kinds', 'docs', 'origen', 'motivo')
CLAVES_ALIAS = ('id', 'permitidos')
CLAVES_CITA = ('seudonimo', 'sha256', 'docs', 'origen', 'motivo')
# La capa de una arista sin `source_kind`, para la política: una marca que ninguna entrada puede listar en
# `kinds`, así que solo una entrada sin `kinds` (cualquier capa) la conserva.
SIN_CAPA = 'sin-capa'
# Marcas que la puerta del Path A inyecta para probar la política (`_qa063b_dev.sh fixture-up`).
FIXTURE_NODO = 'Company:__fixture_070__'
FIXTURE_DOC = 'FIXTURE_070'
FIXTURE_ALIAS = '__alias_fixture_070__'
FIXTURE_SIN_ID = '__fixture_070_sin_id__'   # `name` de un Organization sin entity_id

# Un alias permitido es el nombre de pila, con un tratamiento delante («don X», «el Sr. X», «Dª X») o con la
# marca del roster («X (seudónimo TS)»): nada más que lo distinga, ni dígitos, ni otro alfabeto, ni otra palabra.
_TRATAMIENTO = frozenset({'d', 'a', 'da', 'dna', 'don', 'dona', 'sr', 'sra', 'srta', 'sres', 'sras',
                          'senor', 'senora', 'el', 'la'})
_MARCAS = frozenset({'seudonimo', 'ts', 'ats'})
# AC66: la palabra que anuncia una mención explícita de un seudónimo en prosa, plegada (`_palabras`), con su plural y
# la variante «pseudónimo».
_SEUDONIMO = frozenset({'seudonimo', 'seudonimos', 'pseudonimo', 'pseudonimos'})
# AC70: un alias de un civil con una de estas marcas declara un seudónimo: la contención en su nombre no lo exime.
_MARCAS_ALIAS = _MARCAS | _SEUDONIMO | {'cendoj'}
# AC74: lo que no es nombre en un alias marcado o en torno a una mención: las marcas, «judicial» y los tratamientos. Una
# marca intercalada («X (seudónimo TS) Y») no separa las palabras del nombre.
_SIN_NOMBRE = _MARCAS_ALIAS | {'judicial'} | _TRATAMIENTO
_SEPARADORES = re.compile(r'[\s.\-]+')
_PALABRA = re.compile(r'[a-z]+')
_PALABRA_PROSA = re.compile(r'[a-z0-9]+')
_LITERAL_ORG = re.compile(r'\b(?:Company|Organization):(?!\[)')
_PERSONA = re.compile(r'\bPerson:[a-z0-9][a-z0-9-]*')
_ANCHO_COMPLETO = re.compile('[\uff01-\uff5e]')   # AC81: «Person» en ancho completo
# AC85: los puntos de código Default_Ignorable de Unicode que no son de formato (Cf): no se ven ni ocupan sitio.
_IGNORABLES = frozenset([0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x3164, 0xFEFF, 0xFFA0, *range(0x180B, 0x180F),
                         *range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2060, 0x2070), *range(0xFE00, 0xFE10),
                         *range(0x1BCA0, 0x1BCA4), *range(0x1D173, 0x1D17B), *range(0xE0000, 0xE1000)])
# AC88: las letras latinas que, sin diacríticos, no son ASCII y sí legítimas (nombres del norte de Europa en prensa).
# Las demás («\u0131», «\u0251»…) son confusables de una letra ASCII.
_LATIN_LEGITIMAS = frozenset('ßæœøðþłđẞÆŒØÐÞŁĐ')
# AC105(a)/AC106: el corpus es castellano; una letra de otro alfabeto, en cualquier palabra, exige revisión humana. Los
# ordinales ª y º son letras (Lo) sin «LATIN» en su nombre, pero del castellano («Dª», «nº»).
_ORDINALES = frozenset('\u00aa\u00ba')
# AC105(b): los homógrafos griegos y cirílicos de una letra latina, al estilo del esqueleto de UTS #39 (lista cerrada):
# el reconocimiento de nombres los lee como esa letra. Por punto de código y antes de pasar a minúsculas: la nu
# mayúscula es una N y la minúscula, una v.
_HOMOGRAFOS = str.maketrans({
    # griegas: 14 mayúsculas, la sigma lunada y la yot; 9 minúsculas, la sigma lunada y la yot
    '\u0391': 'A', '\u0392': 'B', '\u0395': 'E', '\u0396': 'Z', '\u0397': 'H', '\u0399': 'I', '\u039a': 'K',
    '\u039c': 'M', '\u039d': 'N', '\u039f': 'O', '\u03a1': 'P', '\u03a4': 'T', '\u03a5': 'Y', '\u03a7': 'X',
    '\u03f9': 'C', '\u037f': 'J', '\u03b1': 'a', '\u03b3': 'y', '\u03b9': 'i', '\u03ba': 'k', '\u03bd': 'v',
    '\u03bf': 'o', '\u03c1': 'p', '\u03c5': 'u', '\u03c7': 'x', '\u03f2': 'c', '\u03f3': 'j',
    # cirílicas: 20 mayúsculas y 16 minúsculas
    '\u0410': 'A', '\u0412': 'B', '\u0415': 'E', '\u041a': 'K', '\u041c': 'M', '\u041d': 'H', '\u041e': 'O',
    '\u0420': 'P', '\u0421': 'C', '\u0422': 'T', '\u0423': 'Y', '\u0425': 'X', '\u0405': 'S', '\u0406': 'I',
    '\u0408': 'J', '\u04ae': 'Y', '\u04ba': 'H', '\u04c0': 'I', '\u051a': 'Q', '\u051c': 'W', '\u0430': 'a',
    '\u0435': 'e', '\u043e': 'o', '\u0440': 'p', '\u0441': 'c', '\u0443': 'y', '\u0445': 'x', '\u0455': 's',
    '\u0456': 'i', '\u0458': 'j', '\u04af': 'y', '\u04bb': 'h', '\u04cf': 'l', '\u0501': 'd', '\u051b': 'q',
    '\u051d': 'w',
})
# AC107/AC108(b)/AC117: los dígitos homógrafos de una letra (lista cerrada). En una palabra que mezcla letras y
# dígitos, cada dígito es él mismo o una de sus letras (el 1, l o i; el 9, g o q), por posición: lo decide el nombre con
# que se compara (`_es_forma`, `_letra`), sin enumerar combinaciones. Sin guarda para los dígitos ASCII: un NIF, un
# artículo o una fecha no son sospechosos.
_DIGITO_LETRAS = {'0': 'o', '1': 'li', '2': 'z', '3': 'e', '4': 'a', '5': 's', '6': 'g', '7': 't', '8': 'b', '9': 'gq'}
_ID_ENTIDAD = re.compile(r'(?<![A-Za-z0-9_-])(?:Person|Company|Organization):[a-z0-9][a-z0-9-]*', re.I)   # AC78(a)
# AC80: la lista no lleva texto libre. `origen`, una de estas formas; `motivo`, una de las plantillas que escriben la
# siembra del estado publicado y el apply de la 063 (`qa_esc_apply._aplicar_seudonimos`: `decision-<fecha>` y la
# tercera plantilla).
_ORIGENES = re.compile(r'(?:estado|decision)-\d{4}-\d{2}-\d{2}')
_MOTIVOS = (re.compile(r'estado publicado del seed \d{8}-[a-z0-9]+, sin revisar \(spec \d{3}, Q\d+\)'),
            re.compile(r'estado publicado del seed \d{8}-[a-z0-9]+; conservada por la \d{3} '
                       r'\(R-\d{4}-\d{2}-\d{2}-\d{4}\)'),
            re.compile(r'decisión «conservar» de la auditoría \(unidad [0-9a-z]{8}(?:, [0-9a-z]{8})*\)'))
# AC90: el `motivo` de una cita admitida, una plantilla cerrada, y su `sha256`, el de la cita normalizada
# (`huella_cita`): la entrada admite ese texto y ningún otro (corrección de la enmienda 27). AC98: la de una cita
# recompuesta con elipsis, que la vía (a) nunca admite.
_MOTIVOS_CITA = (re.compile(r'coaparición judicial revisada; no identifica'),
                 re.compile(r'cita literal del documento judicial público; coaparición verificada en el export '
                            r'\d{8}-[a-z0-9]+'),
                 re.compile(r'cita recompuesta con elipsis marcada, revisada contra el documento judicial público'))
_SHA256 = re.compile(r'[0-9a-f]{64}')

Regla = namedtuple('Regla', 'fichero linea seccion indice fila')
Hallazgo = namedtuple('Hallazgo', 'fichero linea clase seudonimo motivo')


class ErrorDeForma(SystemExit):
    """AC114/AC115: un YAML de la política o del inventario que no se puede leer como tal (un roster vacío o con filas
    inválidas; una raíz, una sección o una fila de otro tipo): leído a la ligera, la política se apagaría o una fila
    se quedaría sin auditar, en silencio. Sale con 2; sus `motivos` (fichero, sección y posición, sin valores: el log
    del CI es público) los imprime quien la recibe."""

    def __init__(self, motivos):
        self.motivos = list(motivos)
        super().__init__(2)

    def __str__(self):
        return '; '.join(self.motivos)

CABECERA_PERMITIDOS = """\
# Seudónimos judiciales: lista PÚBLICA de lo que se conserva (spec 070).
#
# Los autos del Tribunal Supremo sustituyen el nombre real de cada investigado por un nombre de
# pila ficticio (context/pseudonyms.yaml). Al construir el grafo, `make relations` borra toda arista
# entre un seudónimo y una sociedad u organismo, de cualquier capa, y todo alias de un seudónimo que
# NO figure aquí: denegación por defecto. Lo retirado no se enumera en ningún fichero público; consta
# en el registro de rectificaciones con los extremos neutralizados.
#
#   aristas: {seudonimo, otro, tipo, kinds, docs, origen, motivo}: `otro` es una Company u Organization;
#            `kinds` limita las capas que se conservan (sin él, cualquiera) y `docs` los documentos
#            (`source_doc`): con él, solo las aristas de esos documentos (sin él, de cualquiera).
#   alias:   {id, permitidos}: solo el nombre de pila con tratamiento («don X», «Sr. X»).
# Las dos secciones son obligatorias (vacías: `aristas: []`, `alias: []`): faltar no es «nada».
#
# Lo reescribe `qa_esc_apply` (063: «conservar» añade la excepción; «retirar» la quita o la acota por
# documento), ordenado y sin más comentarios que esta cabecera; `pipeline/scripts/seudonimos_check.py`
# lo valida en el CI.
"""


# AC90/AC98: la sección opcional `citas:`, documentada junto a ella cuando tiene entradas.
CABECERA_CITAS = """\
# citas: {seudonimo, sha256, docs, origen, motivo}: una cita de una regla, una capa o una arista no judicial
#        que nombra al extremo civil de un seudónimo, revisada como coaparición, o una cita judicial recompuesta
#        con elipsis marcada, revisada contra el documento público. `sha256` es el de la cita normalizada
#        (`seudonimos.entrada_cita`): la entrada admite ese texto exacto y ningún otro; `docs`, sus documentos.
#        Sin ella, la cita es un hallazgo.
"""


# --- seudónimos y predicado ---------------------------------------------------------------------

# AC114: el id de una fila del roster: Person: y un slug con su nombre de pila (`_nombre`).
_ID_ROSTER = re.compile(r'Person:[a-z0-9]+(?:-[a-z0-9]+)*')


def _roster(path=None) -> list[dict]:
    """AC114: las filas de `context/pseudonyms.yaml`, validadas: raíz mapa, `pseudonyms` una lista con filas, cada una
    un mapa con un `id` `Person:<slug>` con nombre de pila, sin repetir, y, si lo lleva, un `name` de texto no vacío;
    AC127/AC142: con solo las claves del inventario, sin separadores reservados; AC136: y la raíz, solo con
    `pseudonyms`. Si no, ErrorDeForma (roster inválido, exit 2): un roster vacío o una fila sin id dejarían la política
    apagada en silencio (el loader recibiría `ids: []`), y una clave de más, en una fila o en la raíz (aunque su valor
    no sea texto), publicaría lo que ningún control lee."""
    path = Path(path or PSEUDONYMS_PATH)
    doc = leer_yaml(path.read_text(encoding='utf-8'), path.name)
    filas = doc.get('pseudonyms') if isinstance(doc, dict) else None
    if not isinstance(doc, dict):
        motivos = ['la raíz no es un mapa']
    elif not isinstance(filas, list) or not filas:
        motivos = ['`pseudonyms` no es una lista con filas']
    else:
        motivos, vistos = [], set()
        for i, fila in enumerate(filas):
            if not isinstance(fila, dict):
                motivos.append(f'`pseudonyms`[{i}] no es un mapa')
                continue
            sid = fila.get('id').strip() if isinstance(fila.get('id'), str) else ''
            if not _ID_ROSTER.fullmatch(sid) or not _nombre(sid):
                motivos.append(f'`pseudonyms`[{i}]: sin `id` Person:<nombre de pila>')
            elif sid in vistos:
                motivos.append(f'`pseudonyms`[{i}]: `id` repetido')
            vistos.add(sid)
            if 'name' in fila and not (isinstance(fila['name'], str) and fila['name'].strip()):
                motivos.append(f'`pseudonyms`[{i}]: `name` vacío o que no es texto')
            # AC127: solo su vocabulario de claves, sea cual sea el tipo del valor; la clave, por su huella. AC157: y
            # cada hoja, del tipo de su ruta (texto, en el roster).
            campos = CAPAS[PSEUDONYMS_REL]['pseudonyms'].campos
            motivos += [f'`pseudonyms`[{i}]: clave fuera del inventario (huella {sha16(ruta)})'
                        for ruta in _fuera_del_inventario(campos, fila)]
            motivos += [f'`pseudonyms`[{i}]: {motivo} (huella {sha16(ruta)})'
                        for ruta, motivo in [*problemas_hojas(campos, fila), *problemas_contenedores(campos, fila)]]
    if isinstance(doc, dict):   # AC136: la raíz, exactamente `pseudonyms`; otra clave, por su huella
        motivos += [f'clave raíz fuera del inventario (huella {sha16(str(clave))})' for clave in doc
                    if clave != 'pseudonyms']
    if motivos:
        raise ErrorDeForma(f'{path.name}: roster inválido (spec 070): {m}' for m in motivos)
    return filas


def ids_seudonimos(path=None) -> frozenset[str]:
    """Los entity_id declarados en `context/pseudonyms.yaml` (`_roster`: uno inválido para con exit 2)."""
    return frozenset(fila['id'].strip() for fila in _roster(path))


def forma_de_muestra(fila) -> str:
    """AC83: el nombre con que se publica o se imprime un seudónimo, de su fila del roster (`fila`, de pseudonyms.yaml):
    su `name` si lo lleva; si no, su nombre de pila con mayúscula inicial («Miguel Luis»). Nunca el del censo ni el del
    grafo."""
    nombre = fila.get('name')
    if isinstance(nombre, str) and nombre.strip():
        return nombre.strip()
    return ' '.join(p.capitalize() for p in _nombre(str(fila.get('id') or '').strip()))


def muestras_seudonimos(path=None) -> dict[str, str]:
    """{entity_id: forma de muestra} de los seudónimos de `context/pseudonyms.yaml` (`forma_de_muestra`, `_roster`)."""
    return {fila['id'].strip(): forma_de_muestra(fila) for fila in _roster(path)}


def es_par_sensible(a, b, ids=None) -> bool:
    """Un extremo es un seudónimo declarado y el otro una Company u Organization."""
    ids = ids_seudonimos() if ids is None else ids
    a, b = str(a or ''), str(b or '')
    return bool(a and b) and ((a in ids and b.startswith(SENSIBLES))
                              or (b in ids and a.startswith(SENSIBLES)))


def orientar(a, b, ids) -> tuple[str, str]:
    """(seudónimo, otro extremo) de un par sensible, en cualquier dirección."""
    return (a, b) if a in ids else (b, a)


# --- el overlay de relaciones, tal como lo lee el loader -----------------------------------------

def ficheros_overlay(directory) -> list[Path]:
    """Ficheros del overlay en orden de aplicación: alfabético, `*.yaml` y `*.yml`, con los
    `*_qa061` SIEMPRE al final (pisan lo que dijo cualquier fichero anterior)."""
    directory = Path(directory)
    files = [*directory.glob('*.yaml'), *directory.glob('*.yml')]
    return sorted(files, key=lambda f: (f.stem.endswith('_qa061'), f.name))


def extremo(item: dict, side: str, tipos=None) -> str | None:
    """entity_id de un extremo de regla: `<side>_id`, o `<side>` (nombre) + `<side>_type` (Person
    por defecto). Con `tipos`, un tipo fuera del conjunto no resuelve (así lo descarta el loader)."""
    eid = (item.get(f'{side}_id') or '').strip()
    if eid:
        return eid
    name = (item.get(side) or '').strip()
    etype = (item.get(f'{side}_type') or 'Person').strip()
    if not name or (tipos is not None and etype not in tipos):
        return None
    return entity_id(etype, name)


def entidad(item: dict, tipos=None) -> str | None:
    """entity_id de una fila `{id}` o `{type, name}` (alias_deny, lados de un split)."""
    eid = (item.get('id') or '').strip()
    if eid:
        return eid
    name, etype = (item.get('name') or '').strip(), (item.get('type') or '').strip()
    if not name or not etype or (tipos is not None and etype not in tipos):
        return None
    return entity_id(etype, name)


def _linea(lineas, clave, i):
    filas = lineas.get(clave) or []
    return filas[i] if i < len(filas) else 0


class _ClaveRepetida(yaml.constructor.ConstructorError):
    """Una clave repetida en un mapping (AC47)."""


def posicion_yaml(err) -> str:
    """Dónde está un error YAML («línea L, columna C»), nunca su texto: el del parser cita la línea, que puede llevar un
    alias o un extremo retirado, y estos mensajes llegan al log público del CI. Una clave repetida lo dice (es un
    motivo fijo, no texto del fichero)."""
    mark = getattr(err, 'problem_mark', None) or getattr(err, 'context_mark', None)
    donde = f'línea {mark.line + 1}, columna {mark.column + 1}' if mark else 'posición desconocida'
    return f'clave repetida en {donde}' if isinstance(err, _ClaveRepetida) else donde


class _Cargador(yaml.SafeLoader):
    """`yaml.SafeLoader` cuyos errores de constructor llevan la posición del nodo, como los de sintaxis: `!!int X` no
    falla en el parser sino al construir (ValueError, cuyo texto cita el valor). Y sin claves repetidas: `SafeLoader`
    se queda con el último valor, así que el primero seguiría publicado en el fichero sin que nada lo audite (un
    `doc` privado tapado por otro público, AC47). También a través de `<<:`: un valor fusionado que una clave explícita
    pisa sigue en el fichero."""

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            self.flatten_mapping(node)
            vistas = set()
            for clave, _ in node.value:
                valor = self.construct_object(clave, deep=deep)
                try:
                    repetida = valor in vistas
                except TypeError:
                    continue   # no hashable: lo rechaza `construct_mapping` con su propio error
                if repetida:
                    raise _ClaveRepetida(problem='clave repetida', problem_mark=clave.start_mark)
                vistas.add(valor)
        return super().construct_mapping(node, deep=deep)

    def construct_object(self, node, deep=False):
        try:
            return super().construct_object(node, deep=deep)
        except yaml.YAMLError:
            raise
        except Exception:
            raise yaml.constructor.ConstructorError(problem='valor no construible',
                                                    problem_mark=node.start_mark) from None


def leer_yaml(texto, fichero):
    """`yaml.safe_load(texto)`; con un YAML inválido (sintaxis o constructor), SystemExit con `fichero` y la posición
    (`posicion_yaml`), sin encadenar el error (su texto cita la línea o el valor). Lo usan los cargadores y
    verificaciones de la política."""
    try:
        loader = _Cargador(texto)
        try:
            return loader.get_single_data()
        finally:
            loader.dispose()
    except Exception as err:
        raise SystemExit(f'{fichero}: YAML inválido ({posicion_yaml(err)}) (spec 070)') from None


def _rel_datos(path) -> str | None:
    """La ruta de un fichero bajo `pipeline/data` como la escribe el inventario (`CAPAS`), o None."""
    partes = Path(path).parts
    for i in range(len(partes) - 2, -1, -1):
        if partes[i:i + 2] == ('pipeline', 'data'):
            return '/'.join(partes[i:])
    return None


def problemas_forma(rel, data, claves=True, filas=True) -> list[str]:
    """AC115: por qué un YAML del inventario (`CAPAS`) no tiene la forma que leen sus loaders, sin valores: la raíz ha
    de ser un mapa (un fichero vacío no tiene filas) y cada sección suya del inventario, una lista (o vacía) de mapas;
    la sección `None` es el documento entero. AC127 (con `claves`): y ni la raíz ni una fila llevan una clave que el
    inventario no clasifica, sea cual sea el tipo de su valor (nulo, número, lista, mapa o texto), ni, AC142, una clave
    con un separador reservado: la clave, por su huella; AC157, ni una hoja de un tipo que no es el de su ruta
    (`problemas_hojas`, también en la lista de permitidas: su forma la juzga AC80); y, AC144, ninguna fila del overlay
    de relaciones es una que el loader no aplicaría entera (`FORMAS`, la misma función con que la juzga el loader, que
    también juzga el tipo de cada hoja: AC154-AC157). Sin `filas` (AC156, el loader de relaciones antes de juntar las
    filas de sus ficheros), solo la raíz, sus secciones y el contenedor de cada una. AC161 (también sin `claves`: es un
    tipo): un campo que el inventario declara lista es una lista (`problemas_listas`; las de una regla de relations,
    su forma). AC164: un fichero de reglas sin raíz (vacío o `null`) no es «sin reglas»: su raíz no es un mapa. AC180
    (también sin `claves`): un contenedor donde el inventario declara un escalar (`problemas_contenedores`); en la lista
    de permitidas, también una fila que no es un mapa y un campo lista que no lo es. AC184: en el contexto, lo que su
    loader abortaría antes del driver (`forma_contexto`)."""
    capa = capa_de(rel)
    if capa is None or rel == PSEUDONYMS_REL:   # su validación: `_roster`
        return []
    reglas = capa is CAPAS[f'{RELATIONS_REL}/*.y*ml']
    if data is None and not reglas:
        return []
    if rel == PERMITIDOS_REL:   # AC161/AC180: también un campo lista y un contenedor, y una fila que no es un mapa
        out = []
        for seccion in (capa if claves and filas and isinstance(data, dict) else ()):
            for i, fila in enumerate(data[seccion] if isinstance(data.get(seccion), list) else ()):
                if not isinstance(fila, dict):
                    out.append(f'{rel}: `{seccion}`[{i}] no es un mapa')
                    continue
                out += [f'{rel}: `{seccion}`[{i}]: {motivo} (huella {sha16(ruta)})'
                        for ruta, motivo in [*problemas_hojas(capa[seccion].campos, fila),
                                             *problemas_listas(capa[seccion].campos, fila),
                                             *problemas_contenedores(capa[seccion].campos, fila)]]
        return out
    if not isinstance(data, dict):
        return [f'{rel}: la raíz no es un mapa']
    if None in capa:
        out = [f'{rel}: {motivo} (huella {sha16(ruta)})'
               for ruta, motivo in [*problemas_listas(capa[None].campos, data),
                                    *problemas_contenedores(capa[None].campos, data)]] if filas else []
        return out + ([*(f'{rel}: clave fuera del inventario (huella {sha16(ruta)})'
                         for ruta in _fuera_del_inventario(capa[None].campos, data)),
                       *(f'{rel}: {motivo} (huella {sha16(ruta)})'
                         for ruta, motivo in problemas_hojas(capa[None].campos, data))] if claves and filas else [])
    out = [f'{rel}: sección fuera del inventario (huella {sha16(str(seccion))})' for seccion in data
           if claves and seccion not in capa]
    for seccion in capa:
        en_seccion = data.get(seccion)
        if en_seccion is None:
            continue
        if not isinstance(en_seccion, list):
            out.append(f'{rel}: `{seccion}` no es una lista')
            continue
        for i, fila in enumerate(en_seccion if filas else ()):
            if not isinstance(fila, dict):
                out.append(f'{rel}: `{seccion}`[{i}] no es un mapa')
                continue
            if not reglas:   # AC161, AC180 (las de reglas, su forma)
                out += [f'{rel}: `{seccion}`[{i}]: {motivo} (huella {sha16(ruta)})'
                        for ruta, motivo in [*problemas_listas(capa[seccion].campos, fila),
                                             *problemas_contenedores(capa[seccion].campos, fila)]]
                aborto = forma_contexto(seccion, fila) if capa is CAPAS[f'{CONTEXT_REL}/*.y*ml'] else None
                if aborto:
                    out.append(f'{rel}: `{seccion}`[{i}]: {aborto}')
            if not claves:
                continue
            out += [f'{rel}: `{seccion}`[{i}]: clave fuera del inventario (huella {sha16(ruta)})'
                    for ruta in _fuera_del_inventario(capa[seccion].campos, fila)]
            if not reglas:
                out += [f'{rel}: `{seccion}`[{i}]: {motivo} (huella {sha16(ruta)})'
                        for ruta, motivo in problemas_hojas(capa[seccion].campos, fila)]
                continue
            motivo = FORMAS[seccion](fila)   # AC142-AC145, AC154-AC157: la del loader, también el tipo de cada hoja
            if motivo:
                out.append(f'{rel}: `{seccion}`[{i}]: {motivo}')
    return out


def _documento(path, claves=True):
    """(datos, {clave: [línea de cada elemento]}) del YAML en una sola pasada del parser. Inválido (sintaxis o
    constructor) → SystemExit con fichero, línea y columna (`posicion_yaml`). AC115/AC127: un fichero del inventario
    con otra forma o una clave que no clasifica (`problemas_forma`) → ErrorDeForma (exit 2): leído a la ligera, una
    sección escrita como mapa, una fila sin su guion o una clave de más se quedarían sin auditar. Sin `claves`, solo
    los tipos (AC115): el inventario (`campos_sin_clasificar`) lista las claves que el árbol pararía."""
    texto = Path(path).read_text(encoding='utf-8')
    try:
        loader = _Cargador(texto)
        try:
            node = loader.get_single_node()
            data = loader.construct_document(node) if node is not None else None
        finally:
            loader.dispose()
    except Exception as err:
        raise SystemExit(f'{Path(path).name}: YAML inválido ({posicion_yaml(err)}) (spec 070)') from None
    rel = _rel_datos(path)
    problemas = problemas_forma(rel, data, claves) if rel else []
    if problemas:
        raise ErrorDeForma(f'forma inválida (spec 070): {m}' for m in problemas)
    lineas = {}
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if isinstance(value, yaml.SequenceNode):
                lineas[key.value] = [item.start_mark.line + 1 for item in value.value]
    return (data if isinstance(data, dict) else {}), lineas


def reglas_publicas(directory=None) -> list[Regla]:
    """Cada fila splits/deny/fix/alias_deny de cada fichero del overlay, con su línea."""
    out = []
    for path in ficheros_overlay(directory or RELATIONS_DIR):
        data, lineas = _documento(path)
        for seccion in SECCIONES:
            for i, fila in enumerate(data.get(seccion) or []):
                if isinstance(fila, dict):
                    out.append(Regla(path, _linea(lineas, seccion, i), seccion, i, fila))
    return out


# --- la lista de permitidas ----------------------------------------------------------------------

def forma_permitidos(data) -> str | None:
    """Por qué la lista no tiene la forma exigida, o None: un mapa con exactamente `aristas` y `alias`, las
    dos listas (vacías solo si se declaran así), y si acaso `citas` (AC90), también una lista. Una sección ausente,
    renombrada o de otro tipo no puede leerse como «nada permitido»: la denegación por defecto lo retiraría todo; sin
    `citas`, en cambio, no hay ninguna cita admitida, que solo deniega más. La usan el loader, el apply de la 063, el CI
    y L26."""
    if not isinstance(data, dict):
        return 'ha de ser un mapa con las secciones `aristas` y `alias` (está vacío o es de otro tipo)'
    faltan = [k for k in SECCIONES_PERMITIDOS if k not in data]
    sobran = sum(1 for k in data if k not in SECCIONES_PERMITIDOS + SECCIONES_OPCIONALES)
    if faltan or sobran:   # las que sobran se cuentan: una clave es texto del fichero
        return ('ha de tener exactamente las secciones `aristas` y `alias` ('
                + '; '.join(([f'faltan: {", ".join(faltan)}'] if faltan else [])
                            + ([f'{sobran} clave(s) desconocida(s)'] if sobran else [])) + ')')
    for clave in (*SECCIONES_PERMITIDOS, *(k for k in SECCIONES_OPCIONALES if k in data)):
        if not isinstance(data[clave], list):
            return f'`{clave}` ha de ser una lista (vacía: `{clave}: []`)'
    return None


def _lista_tal_cual(repo) -> dict | None:
    """La lista de permitidas de `repo` como la escribe el YAML, si es un mapa con su forma (`forma_permitidos`,
    `problemas_forma`: la juzga `auditar`, que con otra para con exit 2); si no se puede leer o no la tiene, None:
    ningún alias de más vale (AC129)."""
    path = Path(repo) / PERMITIDOS_REL
    try:
        data = leer_yaml(path.read_text(encoding='utf-8'), PERMITIDOS_NOMBRE) if path.exists() else None
    except SystemExit:
        return None
    if not isinstance(data, dict) or forma_permitidos(data) or problemas_forma(PERMITIDOS_REL, data):
        return None
    return data


def cargar_permitidos(path=None) -> dict:
    """{'aristas': [...], 'alias': [...], 'citas': [...]} de la lista pública (`citas`, opcional: sin ella, ninguna
    cita admitida). No existe → FileNotFoundError; YAML inválido o mal formada (`forma_permitidos`) → SystemExit, sin
    valores: sin lista, la denegación por defecto lo retiraría todo. AC157: una hoja que no es texto → ErrorDeForma
    (exit 2), como en el árbol (`problemas_forma`)."""
    data = leer_yaml(Path(path or PERMITIDOS_PATH).read_text(encoding='utf-8'), PERMITIDOS_NOMBRE)
    problema = forma_permitidos(data)
    if problema:
        raise SystemExit(f'{PERMITIDOS_NOMBRE} (spec 070): {problema}')
    problemas = problemas_forma(PERMITIDOS_REL, data)
    if problemas:
        raise ErrorDeForma(f'forma inválida (spec 070): {m}' for m in problemas)
    return {'aristas': list(data['aristas']), 'alias': list(data['alias']), 'citas': list(data.get('citas') or [])}


def indice_aristas(data) -> dict:
    """{(seudonimo, otro, tipo): (frozenset(kinds) | None, frozenset(docs) | None)}; None = cualquiera."""
    def conjunto(value):
        return frozenset(value) if value else None

    return {(e.get('seudonimo'), e.get('otro'), e.get('tipo')): (conjunto(e.get('kinds')), conjunto(e.get('docs')))
            for e in data.get('aristas') or []}


def indice_alias(data) -> dict:
    return {e.get('id'): frozenset(e.get('permitidos') or []) for e in data.get('alias') or []}


def arista_permitida(indice, seudonimo, otro, tipo, kind=None, doc=None) -> bool:
    """¿Conserva la lista esta arista? `kind`/`doc`: su capa y su `source_doc` (None = sin filtrar; una
    arista sin documento se comprueba con '', que ninguna entrada acotada por `docs` contiene)."""
    key = (seudonimo, otro, tipo)
    if key not in indice:
        return False
    kinds, docs = indice[key]
    return (kinds is None or kind is None or kind in kinds) and (docs is None or doc is None or doc in docs)


def aristas_esperadas(data) -> int:
    """Aristas seudónimo↔sociedad/organismo que el grafo ha de tener tras la política (puerta del Path A):
    una por entrada, o una por documento si la entrada los acota."""
    return sum(len(e['docs']) if e.get('docs') else 1 for e in data.get('aristas') or [])


def alias_permitido(seudonimo_id, alias, permitidos=None) -> bool:
    """¿Conserva el seudónimo este alias? `permitidos` = la lista cargada (por defecto, la del repo)."""
    data = cargar_permitidos() if permitidos is None else permitidos
    return alias in indice_alias(data).get(seudonimo_id, frozenset())


def _ignorable(c) -> bool:
    """AC81/AC85: ¿es `c`, ya en NFKC, un carácter que no se ve? De formato (Cf), una marca combinante (Mn/Me: tras NFKC
    el español no deja ninguna suelta, é y ñ se precomponen) o un Default_Ignorable (`_IGNORABLES`)."""
    return unicodedata.category(c) in ('Cf', 'Mn', 'Me') or ord(c) in _IGNORABLES


# AC191: los signos de puntuación que imitan a uno ASCII, plegados a él antes de cualquier lectura y también tras NFKC
# (el punto alto griego, U+0387, es U+00B7): el menos, los guiones de otros bloques y los de ancho completo, `-`; las
# barras de división, de fracción y de ancho completo, `/`; el punto medio, el de guion, el de una línea y el de ancho
# completo, `.`; los dos puntos de ancho completo, `:`. «8−10−25», «8∕10∕25» y «8·10·25» se leen «8-10-25», «8/10/25» y
# «8.10.25». Uno a uno: no mueven ninguna posición.
_CONFUSABLES = str.maketrans({'\u2212': '-', '\u2215': '/', '\u2044': '/', '\uff0f': '/', '\u00b7': '.',
                              '\u2027': '.', '\u2024': '.', '\uff0d': '-', '\u2043': '-', '\ufe63': '-',
                              '\ufe58': '-', '\uff0e': '.', '\uff1a': ':'})


def _normalizar(text) -> str:
    """AC81/AC85: el texto en NFKC y sin los caracteres que no se ven (`_ignorable`: U+200B–U+200F, U+034F, U+FEFF,
    U+00AD…), que parten palabras e ids: «Person:…» en ancho completo es «Person:…», y «Zz» con un U+200B o un U+034F
    entre sus letras es «Zz»; AC191: y con los confusables de puntuación en su signo ASCII (`_CONFUSABLES`). Sobre él
    se buscan ids y menciones, y de él parten las demás lecturas."""
    text = unicodedata.normalize('NFKC', str(text or '').translate(_CONFUSABLES))   # antes: U+FE58 no es una raya
    return ''.join(c for c in text if not _ignorable(c)).translate(_CONFUSABLES)


def _confusable(c) -> bool:
    """AC88: ¿es `c` una letra latina que sin sus diacríticos (NFKD sin Mn) no es ASCII ni de `_LATIN_LEGITIMAS`?"""
    if not c.isalpha() or not unicodedata.name(c, '').startswith('LATIN') or c in _LATIN_LEGITIMAS:
        return False
    base = ''.join(b for b in unicodedata.normalize('NFKD', c) if unicodedata.category(b) != 'Mn')
    return not (base and all('A' <= b <= 'Z' or 'a' <= b <= 'z' for b in base))


def _otro_alfabeto(c) -> bool:
    """AC105(a)/AC106: ¿es `c` una letra (categoría L*) cuyo nombre Unicode no dice LATIN, salvo los ordinales
    (`_ORDINALES`), o un carácter sin nombre que no es un espacio (fallo cerrado: un código sin asignar o de uso
    privado)? Los dígitos y la puntuación, no."""
    try:
        nombre = unicodedata.name(c)
    except ValueError:
        return not c.isspace()
    return unicodedata.category(c).startswith('L') and 'LATIN' not in nombre.split() and c not in _ORDINALES


def _numero_no_ascii(c) -> bool:
    """AC108(a): ¿es `c`, ya en NFKC, un número (categoría N*) que no es un dígito ASCII? Los árabe-índicos, devanagari
    o bengalíes (homógrafos de una letra o de otro dígito) exigen revisión humana; \u00b2, \u00b3, los de ancho
    completo o los matemáticos, que NFKC lleva a dígitos ASCII, y \u00bd o \u216b, que descompone en dígitos y una
    barra o en letras, no llegan aquí."""
    return unicodedata.category(c).startswith('N') and not '0' <= c <= '9'


def texto_sospechoso(text, url=False) -> bool:
    """AC81/AC85/AC88/AC105/AC106: ¿lleva el texto caracteres de ancho completo (U+FF01–U+FF5E), alguno que tras NFKC no
    se ve (`_ignorable`), una letra latina confusable (`_confusable`: «\u0131» por «i») o, en él o en su NFKC, una letra
    de otro alfabeto o un carácter sin nombre (`_otro_alfabeto`), mezclada con letras latinas o no
    («\u0396\u0396 \u03a4\u0395\u0405\u03a4», sin una sola letra latina, se lee ZZ TEST; el armenio, el cherokee
    o el copto, igual)? Una URL (`url`) se examina además decodificada (`_decodificar_url`): «%D0%B0» es la «a»
    cirílica (U+0430). AC108(a): también un número que tras NFKC no es un dígito ASCII (`_numero_no_ascii`). Un NBSP,
    unos puntos suspensivos, una ligadura tipográfica, una tilde, una «ł», los ordinales, los dígitos ASCII y la
    puntuación, no."""
    textos = [str(text or '')]
    if url:
        textos.append(_decodificar_url(textos[0]))
    for texto in textos:
        nfkc = unicodedata.normalize('NFKC', texto)
        if _ANCHO_COMPLETO.search(texto) or any(_ignorable(c) or _confusable(c) for c in nfkc):
            return True
        if any(_otro_alfabeto(c) for c in texto + nfkc) or any(_numero_no_ascii(c) for c in nfkc):
            return True
    return False


def _plegar(text) -> str:
    """El texto normalizado (`_normalizar`) y plegado NFKD, sin marcas diacríticas y en minúsculas («Dª» → «da»,
    «Rubén» → «ruben»)."""
    text = unicodedata.normalize('NFKD', _normalizar(text))
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn').lower()


def _palabras(text) -> list[str]:
    """Las palabras de una prosa plegada (`_plegar`): las letras y cifras seguidas, sin comillas, paréntesis ni
    puntuación."""
    return _PALABRA_PROSA.findall(_plegar(text))


def normalizar_cita(texto) -> str:
    """AC90: una cita tal como se busca en los chunks de su documento y se pone en `citas:` de la lista: plegada
    (`_plegar`: NFKD sin marcas y sin caja) y con los espacios colapsados."""
    return ' '.join(_plegar(texto).split())


# AC102: las marcas de una elipsis en una cita, sobre su texto normalizado (`_cita_legible`: NFKC, así que U+2026 es
# «...» y U+2024 es «.») y con la puntuación: tres puntos o más con espacios opcionales entre ellos, o U+22EF, con o sin
# «[ ]» o «( )» alrededor. Parten la cita en segmentos. «S.L.», «etc.» o «3.5» no lo son.
_PUNTOS_ELIPSIS = r'\.(?:\s*\.){2,}|\u22ef'
_ELIPSIS = re.compile(rf'[\[(]\s*(?:{_PUNTOS_ELIPSIS})\s*[\])]|{_PUNTOS_ELIPSIS}')
# AC100/AC110: una palabra partida con guion (-, U+2010, U+2011; el blando, U+00AD, como «-») o con raya (U+2012 a
# U+2015): letra, guion o raya y, tras espacios o un salto de línea opcionales, letra. Un guion o una raya con un
# espacio delante («Nimbo - stratus») es un límite.
_PARTICION = re.compile(r'(?<=[^\W\d_])[-\u2010-\u2015]\s*(?=[^\W\d_])')
# AC109: el separador de dos piezas que es un punto de partición del reconocimiento de nombres (`_fichas`): el guion o
# la raya de `_PARTICION`, pegado a la pieza anterior. Y los cortes de un alias en tokens (`_fichas_alias`): un punto
# de partición entre dos letras o cifras (grupo 1; AC191, también uno de la guarda de cierre, `_PARTIDA`) o, como en
# `_tokens_de`, espacios, puntos y guiones.
_UNION = re.compile(r'[-\u2010-\u2015]\s*')
# AC191: la guarda de cierre de las lecturas: cualquier otro signo no ASCII (ni letra ni cifra, ordinal, espacio ni uno
# de uso privado: las marcas de la lectura de las fechas), uno o varios seguidos, con guiones o sin ellos, pegado a una
# letra o cifra por cada lado, es un punto de partición (`_PARTIDA`): separa y une, las dos lecturas, como un guion
# («Nim✓bo» es «nimbo» y «nim bo»). Sin espacios: tras uno, como «Nimbo» S.L.», no parte nada.
_OTRO_SIGNO = r'[^\x00-\x7f\w\s\ue000-\uf8ff]'
_PARTIDA = re.compile(rf'-*{_OTRO_SIGNO}(?:-|{_OTRO_SIGNO})*')
_CORTE_ALIAS = re.compile(rf'(?<=[^\W_])([-\u2010-\u2015]\s*|-*{_OTRO_SIGNO}(?:-|{_OTRO_SIGNO})*)(?=[^\W_])'
                          r'|[\s.\-]+')
# AC98 (enmienda 26): palabras del chunk entre dos segmentos que la migración de citas rellena con el texto literal; con
# más, los separa con «...». Es la frontera entre relleno y elipsis de la migración, no una condición de
# `segmentos_literales`.
HUECO_ELIPSIS = 40


def _cita_legible(texto) -> str:
    """AC100/AC102: una cita o un chunk normalizados (`_normalizar`) y con su puntuación, con el guion blando (U+00AD,
    que `_normalizar` quitaría por invisible) como un guion: una palabra partida con él al final de línea no se lee
    como dos."""
    return _normalizar(str(texto or '').replace('\u00ad', '-'))


def cita_con_elipsis(texto) -> bool:
    """AC98/AC102: ¿lleva la cita una marca de elipsis (`_ELIPSIS`, sobre `_cita_legible`)?"""
    return bool(_ELIPSIS.search(_cita_legible(texto)))


def _literal_plegado(texto) -> str:
    """AC96/AC100/AC110: un texto como lo compara la vía (a): plegado (`_plegar` de `_cita_legible`), con cada palabra
    partida con guion o raya unida en una (`_PARTICION`: «in-\\n culpable» = «inculpable», «Pérez-Llorca» =
    «perezllorca»; solo entre letras: «Nimb-0» sigue siendo «nimb 0», AC109 no cambia lo que admite una cita) ANTES de
    quitar la puntuación, y sin ella, con los espacios colapsados («SERVINABAR, 2000, S.L.U.» = «servinabar 2000 s l
    u»)."""
    return ' '.join(re.sub(r'[\W_]+', ' ', _PARTICION.sub('', _plegar(_cita_legible(texto)))).split())


def _plegar_homografos(texto) -> str:
    """AC105(b): `_plegar` con cada homógrafo griego o cirílico de una letra latina (`_HOMOGRAFOS`) en esa letra, antes
    de pasar a minúsculas."""
    texto = unicodedata.normalize('NFKD', _normalizar(texto))
    return ''.join(c for c in texto if unicodedata.category(c) != 'Mn').translate(_HOMOGRAFOS).lower()


def _mezcla(palabra) -> bool:
    """¿Mezcla la palabra letras y dígitos? Solo en una así un dígito puede leerse como letra: una solo de letras o solo
    de dígitos («2026») es solo ella."""
    return palabra.isalnum() and not palabra.isalpha() and not palabra.isdigit()


def _es_forma(palabra, objetivo) -> bool:
    """AC107/AC112/AC117: ¿es `objetivo` una forma de la palabra del texto `palabra`? La misma o, si mezcla letras y
    dígitos (`_mezcla`), posición a posición: cada letra la misma y cada dígito él mismo o una de sus letras
    homógrafas (`_DIGITO_LETRAS`), cada uno por su cuenta: «nimb0» es «nimbo»; «nimb02000», «nimbo2000»; «1g1esias»,
    «iglesias»."""
    if palabra == objetivo:
        return True
    return (len(palabra) == len(objetivo) and _mezcla(palabra)
            and all(o == c or o in _DIGITO_LETRAS.get(c, '') for c, o in zip(palabra, objetivo)))


@functools.lru_cache(maxsize=8192)
def _sin_nombre(palabra) -> bool:
    """AC135: ¿es una palabra del texto una marca o un tratamiento (`_SIN_NOMBRE`), con la misma regla por posición que
    los nombres (`_es_forma`: «d0n» es «don»; «seud0nimo», «seudonimo»)?"""
    return palabra in _SIN_NOMBRE or (_mezcla(palabra) and any(_es_forma(palabra, p) for p in _SIN_NOMBRE))


def _comunes(a, b) -> list[str] | None:
    """AC117: posición a posición, las letras o cifras en que pueden leerse a la vez dos palabras del mismo largo (cada
    una, ella misma y, si mezcla letras y dígitos, cada dígito también como sus letras homógrafas); None si en alguna
    posición no hay ninguna. Lo que comparten un nombre y una palabra del texto, sin enumerar sus formas."""
    def opciones(palabra):
        return [c + _DIGITO_LETRAS.get(c, '') for c in palabra] if _mezcla(palabra) else list(palabra)
    out = []
    for x, y in zip(opciones(a), opciones(b)):
        comun = ''.join(c for c in x if c in y)
        if not comun:
            return None
        out.append(comun)
    return out


# AC109/AC113: unas fichas como cadena: sus letras seguidas, lo que hay en cada posición entre dos piezas (`tipos`: «J»,
# un punto de partición; «H», un límite, también en los dos extremos; None, dentro de una pieza) y dónde empieza cada
# pieza.
_Cadena = namedtuple('_Cadena', 'letras tipos inicios')


class _Fichas(namedtuple('_Fichas', 'piezas unibles')):
    """AC109: un texto como lo lee el reconocimiento de nombres: sus piezas (letras y cifras seguidas) y, entre cada dos
    seguidas, si las separa un punto de partición (`unibles[g]`, entre la pieza g y la g+1)."""

    @functools.cached_property
    def cadena(self) -> _Cadena:
        """AC113: su cadena (`_Cadena`), una vez por fichas: sin recorrerlas en cada consulta."""
        tipos, inicios, posicion = ['H'], [], 0
        for g, pieza in enumerate(self.piezas):
            if g:
                tipos[-1] = 'J' if self.unibles[g - 1] else 'H'
            inicios.append(posicion)
            tipos += [None] * len(pieza)
            posicion += len(pieza)
        tipos[-1] = 'H'
        return _Cadena(''.join(self.piezas), tuple(tipos), tuple(inicios))

    @functools.cached_property
    def unidas(self) -> tuple[int, ...]:
        """AC222: cuántos puntos de partición hay entre las piezas anteriores a cada una (el de la pieza k, en la
        posición k): si hay alguno en un tramo, sin recorrerlo."""
        return (0, *itertools.accumulate(self.unibles))

    @functools.cached_property
    def hashes(self) -> array.array:
        """AC213: los hashes de los prefijos del esqueleto de su cadena (`_hashes_de`), una vez por fichas."""
        return _hashes_de(_esqueleto(self.cadena.letras))

    @functools.cached_property
    def limites(self) -> tuple[bytes, bytes]:
        """AC213: de su cadena, como máscaras de bits en octetos (el bit o, en la posición o; `_bits_de` las lee por
        ventanas), dónde no hay límite entre piezas (dentro de una) y dónde hay uno duro, una vez por fichas."""
        tipos = self.cadena.tipos
        octetos = (len(tipos) + 7) >> 3
        return tuple(int(''.join('1' if es(t) else '0' for t in reversed(tipos)), 2).to_bytes(octetos, 'little')
                     for es in (lambda t: t is None, lambda t: t == 'H'))

    @functools.cached_property
    def mixto(self) -> str:
        """AC213: su cadena como un solo texto, el límite («n» dentro de una pieza, «J» un punto de partición, «H» un
        límite duro) y la letra de cada posición: lo que decide si un candidato alinea desde una letra, por ventanas
        (`_fin_alineado`). El de la posición o está en 2o y su letra en 2o + 1."""
        return _mixto(self.cadena)

    @functools.cached_property
    def hashes_mixto(self) -> array.array:
        """AC213: los hashes de los prefijos de su `mixto` (`_hashes_de`), una vez por fichas."""
        return _hashes_de(self.mixto)

    @functools.cached_property
    def digitos(self) -> array.array:
        """AC213: de su cadena, cuántas cifras hay antes de cada posición (el de largo k, en la posición k)."""
        return array.array('L', [0, *itertools.accumulate(c.isdigit() for c in self.cadena.letras)])

    @functools.cached_property
    def con_marcas(self) -> bool:
        """AC338 (6ª pasada): si alguna pieza lleva la marca de un signo que puede leerse como letra o como puntuación
        (`_fichas_con_marcas`)."""
        return any(c in _CIFRA_DE_MARCA for pieza in self.piezas for c in pieza)


def _fichas(texto) -> _Fichas:
    """AC104/AC105(b)/AC109: las piezas (`_PALABRA_PROSA`) de un texto plegado con los homógrafos (`_plegar_homografos`
    de `_cita_legible`) y sus puntos de partición: dos piezas separadas solo por un guion o una raya pegados a la
    primera (`_UNION`) o, AC191, por otro signo no ASCII pegado a las dos (`_PARTIDA`, «Nim✓bo»), salvo entre dos
    cifras de dos números («2020-2021»; AC117: en una pieza que mezcla letras y cifras, la cifra puede ser una letra,
    «Nimb0-5tratus»). Cada punto se decide por separado: unido, las dos piezas
    son una palabra; separado, dos. Lo que se halla en alguna de esas lecturas se halla, y ninguna se enumera: lo que
    reconoce nombres o menciones compara por posición contra cada nombre candidato."""
    return _fichas_de(str(texto or ''))


@functools.lru_cache(maxsize=4096)
def _fichas_de(texto) -> _Fichas:
    """`_fichas` de un texto ya en cadena, en caché: lo leen el índice civil, la mención, los bigramas y los nombres."""
    return _trocear(_plegar_homografos(_cita_legible(texto)))


def _trocear(plegado, numeros=True) -> _Fichas:
    """Las piezas y los puntos de partición de un texto ya plegado (`_fichas_de`). Con `numeros`, dos números seguidos no se unen; sin él
    (`_fichas_con_marcas`), esa regla la aplica `_lee_como_marcas` a las rachas que deja cada signo."""
    piezas, unibles, fin = [], [], 0
    for m in _PALABRA_PROSA.finditer(plegado):
        if piezas:
            punto = _UNION.fullmatch(plegado, fin, m.start()) or _PARTIDA.fullmatch(plegado, fin, m.start())
            unibles.append(bool(punto)
                           and not (numeros and piezas[-1][-1].isdigit() and m.group()[0].isdigit()
                                    and not _mezcla(piezas[-1]) and not _mezcla(m.group())))
        piezas.append(m.group())
        fin = m.end()
    return _Fichas(tuple(piezas), tuple(unibles))


def _palabras_desde(fichas, inicio, largo) -> list[tuple[int, str]]:
    """[(fin, palabra)] de cada palabra que puede empezar en la pieza `inicio`: ella sola y, por puntos de partición,
    unida a las siguientes mientras no pase de `largo` letras."""
    piezas, unibles = fichas
    palabra, out = piezas[inicio], [(inicio + 1, piezas[inicio])]
    for fin in range(inicio + 1, len(piezas)):
        if not unibles[fin - 1] or len(palabra) + len(piezas[fin]) > largo:
            break
        palabra += piezas[fin]
        out.append((fin + 1, palabra))
    return out


def _palabra_en(fichas, inicio, palabra) -> int | None:
    """AC109/AC112: la pieza que sigue a `palabra` si empieza en la pieza `inicio`: las piezas desde ella, unidas por
    puntos de partición, que suman su largo, si `palabra` es una forma de lo que forman (`_es_forma`: los dígitos se
    leen después de unir, «nimb» y «0» son «nimb0», que es «nimbo»); si no, None."""
    piezas, unibles = fichas
    fin, largo = inicio, 0
    while fin < len(piezas) and largo < len(palabra):
        largo += len(piezas[fin])
        fin += 1
        if largo < len(palabra) and (fin == len(piezas) or not unibles[fin - 1]):
            return None
    if largo != len(palabra) or not _es_forma(''.join(piezas[inicio:fin]), palabra):
        return None
    return fin


def _tramo(fichas, inicio, palabras) -> list[int] | None:
    """Los cortes ([inicio, fin de cada palabra]) de `palabras` seguidas desde la pieza `inicio`, cada una por posición
    (`_palabra_en`); None si alguna no está."""
    cortes = [inicio]
    for palabra in palabras:
        fin = _palabra_en(fichas, cortes[-1], palabra)
        if fin is None:
            return None
        cortes.append(fin)
    return cortes


def _apariciones(fichas, palabras) -> list[tuple[int, int]]:
    """[(inicio, fin)] de cada palabra del texto, en alguna lectura, que tiene por forma una de `palabras`: por largo,
    sin enumerar formas."""
    largos = {len(p) for p in palabras}
    return [(inicio, fin) for inicio in range(len(fichas.piezas))
            for fin, palabra in _palabras_desde(fichas, inicio, max(largos))
            if len(palabra) in largos and any(_es_forma(palabra, p) for p in palabras)]


def _dice(texto, palabras) -> bool:
    """¿Lleva el texto, en alguna lectura (`_fichas`; AC195, también a través de sus comillas, `_lecturas_texto`:
    «seud"ó"nimo»), una palabra que tiene por forma una de `palabras` («seudónimo»)?"""
    return any(_apariciones(_fichas(lectura), palabras) for lectura in _lecturas_texto(texto))


def _palabras_entre(duros, inicio, fin) -> int:
    """Las palabras que forman como mínimo las piezas de `inicio` a `fin` (excluida), con `duros[j]` los separadores
    que no son puntos de partición entre las piezas 0 y j: una, y otra más por cada uno de ellos (los demás, unidos);
    ninguna si no hay piezas."""
    return 0 if inicio >= fin else 1 + duros[fin - 1] - duros[inicio]


# AC113: un nombre candidato del reconocimiento: su cadena; sus palabras, si no tiene puntos de partición (una sola
# lectura); cuántas palabras ha de tener una lectura para contar (`minimo`); los cortes de cada lectura que no cuenta
# (`excluidas`, posiciones en su cadena); y su primera pieza (`primeras`), su clave en el índice (AC209: el que tiene
# puntos de partición se busca por el árbol de letras de `_derivado`, no por cada palabra con que puede empezar).
# AC229/AC232: y su CONTENIDO (`clave`), con que se ordena y se tabula todo lo que decide un resultado.
class _Candidato(namedtuple('_Candidato', 'cadena palabras minimo excluidas primeras clave')):
    """Uno por nombre (`_candidato`, en caché): se compara y se tabula por identidad, sin recorrer su cadena. AC232: la
    identidad no es del contenido (la caché expulsa entradas y el mismo nombre, o dos que solo difieren en la caja, dan
    otro objeto con el mismo contenido): nada de lo que decide un resultado o un presupuesto se indexa ni se compara por
    ella, sino por `_clave_candidato`; por identidad solo se guardan las tablas de coste, que no cambian ningún
    resultado si fallan."""
    __slots__ = ()
    __hash__ = object.__hash__

    def __eq__(self, otro):
        return self is otro

    def __ne__(self, otro):
        return self is not otro


def _nuevo_candidato(cadena, palabras, minimo, excluidas, primeras) -> _Candidato:
    """AC229/AC232: un `_Candidato` con su `clave`, una vez por candidato: su contenido entero, `(minimo, lecturas
    excluidas ordenadas, _mixto de la cadena)`. Las lecturas excluidas llegan en el orden de un conjunto (de un
    `frozenset` de tuplas de cadenas: el de `PYTHONHASHSEED`), así que se ordenan; la cadena, con el límite de cada
    posición (dentro de una pieza, punto de partición o duro), es todo el texto del nombre, y de ella se derivan los
    demás campos: las palabras (las piezas, que existen si y solo si ninguna posición es un punto de partición) y la
    primera pieza. Dos candidatos con la misma clave son intercambiables."""
    clave = (minimo, tuple(sorted(tuple(sorted(e)) for e in excluidas)), _mixto(cadena))
    return _Candidato(cadena, palabras, minimo, excluidas, primeras, clave)


@functools.lru_cache(maxsize=8192)
def _candidato(nombre, minimo=1, excluidas=frozenset()) -> _Candidato | None:
    """AC104/AC109/AC113: un nombre como candidato del reconocimiento (`_Candidato`), sin enumerar sus lecturas: cada
    una es una decisión en cada punto de partición de sus fichas, y el alineamiento con un texto (`_hay_lectura`) las
    toma a la vez que las del texto. Cuentan las de `minimo` palabras o más que no son una de `excluidas` (tuplas de
    palabras: los nombres de pila del roster, en el índice civil); None si no puede contar ninguna. Puede empezar por su
    primera pieza y por su unión con las siguientes por puntos de partición (AC209: sin materializar cada unión)."""
    fichas = _fichas(nombre)
    if len(fichas.piezas) < max(minimo, 1):
        return None
    cadena, primeras = fichas.cadena, (fichas.piezas[0],)
    if not any(fichas.unibles):
        return None if fichas.piezas in excluidas else _nuevo_candidato(cadena, fichas.piezas, minimo, (), primeras)
    duros = {o for o in range(1, len(cadena.letras)) if cadena.tipos[o] == 'H'}
    cortes = (frozenset(itertools.accumulate(map(len, palabras[:-1]))) for palabras in excluidas
              if ''.join(palabras) == cadena.letras)
    return _nuevo_candidato(cadena, None, minimo,
                            tuple(c for c in cortes if duros <= c and all(cadena.tipos[o] for o in c)), primeras)


# AC113: el esqueleto de un texto plegado: cada dígito homógrafo de una letra en ella y las letras que comparten uno (l
# e i, g y q) en una: una letra o cifra que casa con otra (`_letra`) tiene su mismo esqueleto. Sirve para descartar
# deprisa lo que no puede casar.
_ESQUELETO = str.maketrans({'0': 'o', '1': 'i', 'l': 'i', '2': 'z', '3': 'e', '4': 'a', '5': 's', '6': 'g', '7': 't',
                            '8': 'b', '9': 'g', 'q': 'g'})


def _esqueleto(texto) -> str:
    """El esqueleto (`_ESQUELETO`) de un texto plegado."""
    return texto.translate(_ESQUELETO)


# AC213: el hash de las ventanas del esqueleto de un texto (polinómico, módulo un primo de Mersenne): que el esqueleto
# de la ventana de un texto, del largo de un candidato y desde una pieza, sea el del candidato es condición NECESARIA
# para que case desde ella (una letra o cifra solo casa con otra de su mismo esqueleto), y se comprueba en O(1) tras
# unos prefijos calculados una vez por texto. Solo poda: lo que pasa se comprueba tal cual, así que una colisión no
# cambia ningún resultado.
_HASH_M, _HASH_B = (1 << 61) - 1, 1_000_003_007
_POTENCIAS = [1]


def _potencia(k) -> int:
    """`_HASH_B` elevado a `k`, módulo `_HASH_M`."""
    while len(_POTENCIAS) <= k:
        _POTENCIAS.append(_POTENCIAS[-1] * _HASH_B % _HASH_M)
    return _POTENCIAS[k]


def _mixto(cadena) -> str:
    """Una cadena (`_Cadena`) como un solo texto: el límite de cada posición («n» dentro de una pieza, «J» un punto de
    partición, «H» un límite duro) y la letra que sigue, de modo que el de la posición o está en 2o y su letra en
    2o + 1."""
    out = [''] * (2 * len(cadena.letras) + 1)
    out[0::2] = ('n' if t is None else t for t in cadena.tipos)
    out[1::2] = cadena.letras
    return ''.join(out)


def _mixto_candidato(candidato) -> str:
    """El `_mixto` de la cadena de un candidato (en su `clave`, calculado una vez con él)."""
    return candidato.clave[2]


def _hashes_de(esqueleto) -> array.array:
    """Los hashes de los prefijos de un esqueleto (el de largo k, en la posición k), compactos (8 bytes cada uno: se
    guardan con las fichas de cada texto)."""
    out, acumulado = array.array('Q', [0]), 0
    for letra in esqueleto:
        acumulado = (acumulado * _HASH_B + ord(letra)) % _HASH_M
        out.append(acumulado)
    return out


@functools.lru_cache(maxsize=8192)
def _hash_esqueleto(candidato) -> int:
    """El hash del esqueleto de las letras de un candidato."""
    return _hashes_de(_esqueleto(candidato.cadena.letras))[-1]


@functools.lru_cache(maxsize=8192)
def _mascaras(candidato) -> tuple[int, int]:
    """Los límites de un candidato como máscaras de bits (el bit o, en su posición o de la cadena): donde el texto ha de
    tener un límite entre piezas (`tipos` «H», un corte suyo) y donde no puede tener uno duro (`tipos` None, dentro de
    una pieza suya)."""
    tipos = candidato.cadena.tipos
    return tuple(int(''.join('1' if es(t) else '0' for t in reversed(tipos)), 2)
                 for es in (lambda t: t == 'H', lambda t: t is None))


def _bits_de(octetos, desde, hasta) -> int:
    """Los bits de `desde` a `hasta` (inclusive) de una máscara en octetos, como un entero con el de `desde` de menor
    peso; los que sobran por arriba, hasta el final del octeto, los deja fuera quien llama con una máscara de su largo.
    Cuesta el largo de la ventana, no el de la máscara: desplazarla entera costaba el del texto en cada ventana."""
    return int.from_bytes(octetos[desde >> 3:(hasta >> 3) + 1], 'little') >> (desde & 7)


def _ventana_posible(fichas, desde, candidato) -> bool:
    """AC213: ¿puede el candidato casar desde la letra `desde` de las fichas? Condiciones necesarias, en el tiempo del
    largo del candidato por ventana (no del texto: `_bits_de`) tras unos prefijos y unas máscaras calculados una vez por
    texto: el esqueleto de la ventana, del largo del candidato, es el suyo (una letra o cifra solo casa con otra de su
    mismo esqueleto), y los límites entre piezas son compatibles (donde el candidato corta, el texto tiene un límite;
    donde el candidato está dentro de una pieza, el texto no tiene un límite duro: un punto de partición vale para los
    dos)."""
    hasta = desde + len(candidato.cadena.letras)
    hashes = fichas.hashes
    if hasta >= len(hashes) or (hashes[hasta] - hashes[desde] * _potencia(hasta - desde)) % _HASH_M \
            != _hash_esqueleto(candidato):
        return False
    corta, dentro = _mascaras(candidato)
    sin_limite, duro = fichas.limites
    return not _bits_de(sin_limite, desde, hasta) & corta and not _bits_de(duro, desde, hasta) & dentro


# AC113/AC117: la palabra en curso de un alineamiento: ¿sigue siendo la del candidato tal cual (`exacta`: ningún dígito
# leído como letra)?, ¿lleva alguna letra? Lo que mira `_es_forma` en la palabra entera.
_PALABRA0 = (True, False)
_ANTES, _MUERTA, _HECHA = 'antes', 'muerta', 'hecha'


def _letra(palabra, t, c):
    """La palabra en curso (`_PALABRA0`) tras alinear la letra o cifra `t` del texto con la `c` del candidato, o None si
    no casan: la misma, o un dígito leído como una de sus letras homógrafas (`_DIGITO_LETRAS`), por posición (AC117)."""
    exacta, letra = palabra
    if t == c:
        return exacta, letra or not t.isdigit()
    if c in _DIGITO_LETRAS.get(t, ''):
        return False, letra
    return None


def _vale(palabra) -> bool:
    """¿Es la palabra alineada una forma de la del candidato (`_es_forma`)? La misma o, si lee algún dígito como letra,
    una palabra que también lleva letras."""
    exacta, letra = palabra
    return exacta or letra


def _frontera(estado, inicio, candidato, o, corta):
    """El estado de una aparición (el `candidato` desde la letra `inicio` del texto) en la posición `o` del texto, que
    la lectura corta (`corta`) o no: empieza si corta en su inicio; termina si corta en su fin y su lectura cuenta
    (`_vale`, `minimo`, no `excluidas`); por dentro, el candidato corta donde corta el texto y solo ahí: en un punto de
    partición suyo, como el texto; en uno de sus límites, solo si el texto corta; dentro de una pieza suya, solo si
    no."""
    if estado in (_MUERTA, _HECHA) or not inicio <= o <= inicio + len(candidato.cadena.letras):
        return estado
    if o == inicio:
        return (_PALABRA0, 0, frozenset(range(len(candidato.excluidas)))) if corta else _MUERTA
    palabra, hechas, excluidas = estado
    if o == inicio + len(candidato.cadena.letras):
        return _HECHA if corta and _vale(palabra) and hechas + 1 >= candidato.minimo and not excluidas else _MUERTA
    tipo = candidato.cadena.tipos[o - inicio]
    if (tipo == 'H' and not corta) or (corta and (tipo is None or not _vale(palabra))):
        return _MUERTA
    if tipo == 'J':
        excluidas = frozenset(k for k in excluidas if (o - inicio in candidato.excluidas[k]) == corta)
    return (_PALABRA0, min(hechas + 1, candidato.minimo), excluidas) if corta else (palabra, hechas, excluidas)


def _paso(estado, inicio, candidato, o, t):
    """El estado de una aparición tras alinear la letra o cifra `t` de la posición `o` del texto con la suya."""
    if not isinstance(estado, tuple) or not inicio <= o < inicio + len(candidato.cadena.letras):
        return estado
    palabra = _letra(estado[0], t, candidato.cadena.letras[o - inicio])
    return _MUERTA if palabra is None else (palabra, *estado[1:])


def _hay_lectura(fichas, positivas, negativas=(), presupuesto=None) -> bool:
    """AC109/AC113: ¿hay una lectura del texto (`fichas`: cada punto de partición unido o separado) en la que aparecen
    todas las `positivas` y ninguna de las `negativas`? Cada aparición, (letra de inicio en el texto, `_Candidato`):
    alineados letra a letra, cada punto de partición del candidato decide como el del texto en esa posición
    (`_frontera`, `_paso`). Por las posiciones del tramo, con los estados de todas a la vez: de las negativas, solo las
    que siguen vivas, y de dos estados iguales en las positivas, el que tiene vivas solo parte de las negativas del otro
    basta (`_minimos`): sin enumerar lecturas del texto ni del candidato. AC224 (enmienda 47): con `presupuesto`
    (`_Presupuesto`), cada posición gasta los estados vivos por las apariciones y por los demás estados vivos, y,
    agotado, lo interrumpe."""
    texto = fichas.cadena
    if any(inicio + len(c.cadena.letras) > len(texto.letras) for inicio, c in positivas):
        return False
    negativas = [n for n in negativas if n[0] + len(n[1].cadena.letras) <= len(texto.letras)]
    todas = [*positivas, *negativas]
    desde = min(inicio for inicio, _ in todas)
    hasta = max(inicio + len(c.cadena.letras) for inicio, c in todas)
    frente = {((_ANTES,) * len(positivas), frozenset((j, _ANTES) for j in range(len(negativas))))}
    for o in range(desde, hasta + 1):
        cortes = (False, True) if texto.tipos[o] == 'J' else (texto.tipos[o] == 'H',)
        if presupuesto is not None:
            presupuesto.gasta(len(frente) * (len(todas) + len(frente)))   # (`_minimos` compara los estados entre sí)
        frente = _avanza(frente, positivas, negativas, lambda e, i, c: [_frontera(e, i, c, o, corta)
                                                                        for corta in cortes])
        if o < hasta:
            frente = _avanza(frente, positivas, negativas, lambda e, i, c: [_paso(e, i, c, o, texto.letras[o])])
        if not frente:
            return False
    return any(all(e == _HECHA for e in pos) for pos, _ in frente)


def _avanza(frente, positivas, negativas, paso) -> set:
    """Los estados (positivas, negativas vivas) de `_hay_lectura` tras un `paso` (estado, inicio, candidato → los
    siguientes; uno por decisión del texto, en el mismo orden para todas): sin los que pierden una positiva o completan
    una negativa, y solo los mínimos (`_minimos`)."""
    nuevos = set()
    for pos, neg in frente:
        siguientes = [paso(e, i, c) for e, (i, c) in zip(pos, positivas)]
        de_negativas = [(j, paso(e, *negativas[j])) for j, e in neg]
        for k in range(len(siguientes[0])):   # siempre hay una positiva
            p = tuple(s[k] for s in siguientes)
            n = [(j, s[k]) for j, s in de_negativas]
            if _MUERTA not in p and all(e != _HECHA for _, e in n):
                nuevos.add((p, frozenset((j, e) for j, e in n if e != _MUERTA)))
    return _minimos(nuevos)


def _menos(estado, otro) -> bool:
    """¿Acepta una negativa en el estado `otro` en toda lectura en que acepta en `estado`? Si son iguales, o con la
    misma palabra en curso, menos palabras hechas (cuentan hasta el `minimo`) y más lecturas excluidas aún posibles."""
    return estado == otro or (isinstance(estado, tuple) and isinstance(otro, tuple) and estado[0] == otro[0]
                              and estado[1] <= otro[1] and estado[2] >= otro[2])


def _minimos(estados) -> set:
    """De los estados (positivas, negativas vivas) con las mismas positivas, los que no domina otro: uno domina a otro
    si sus negativas vivas son parte de las del otro, cada una en un estado que acepta en menos lecturas (`_menos`): lo
    que mata a todas las del segundo mata a las del primero."""
    por_positivas = {}
    for pos, neg in estados:
        por_positivas.setdefault(pos, []).append(dict(neg))
    out = set()
    for pos, negs in por_positivas.items():
        minimos = []
        for neg in sorted(negs, key=lambda n: (len(n), sum(e[1] for e in n.values() if isinstance(e, tuple)))):
            if not any(m.keys() <= neg.keys() and all(_menos(e, neg[j]) for j, e in m.items()) for m in minimos):
                minimos.append(neg)
        out.update((pos, frozenset(m.items())) for m in minimos)
    return out


def _encadenados(palabras, segmentos) -> bool:
    """AC97/AC98: ¿están los `segmentos` (listas de palabras) en `palabras` (las de un chunk) como ventanas de palabras
    enteras, cada uno después del anterior, a cualquier distancia? La primera aparición de cada uno deja a los
    siguientes todo el sitio posible."""
    desde = 0
    for segmento in segmentos:
        n = len(segmento)
        desde = next((i + n for i in range(desde, len(palabras) - n + 1)
                      if palabras[i] == segmento[0] and palabras[i:i + n] == segmento), None)
        if desde is None:
            return False
    return True


def segmentos_literales(texto, chunks, plegados=False) -> bool:
    """AC97/AC98/AC102: ¿están los segmentos de la cita (los tramos entre sus marcas de elipsis, `_ELIPSIS` sobre
    `_cita_legible`; uno solo si no tiene) en uno de los `chunks`, en orden y como secuencias de palabras completas, con
    límite en los dos extremos de cada uno (`_literal_plegado`; con `plegados`, los chunks ya lo están)? Sin límite de
    hueco: HUECO_ELIPSIS es la frontera entre relleno y elipsis de la migración, no una condición de esta comprobación.
    NO prueba la coaparición: orden y distancia no dicen si lo omitido era una negación, una atribución o una reserva;
    la prueba de una cita con elipsis es su entrada revisada de `citas:`. La usan la migración de citas, para comprobar
    una cita recompuesta, y quien revisa esa entrada."""
    segmentos = [s.split() for s in (_literal_plegado(x) for x in _ELIPSIS.split(_cita_legible(texto))) if s]
    if not segmentos:
        return False
    return any(_encadenados((chunk if plegados else _literal_plegado(chunk)).split(), segmentos) for chunk in chunks)


def cita_literal(texto, chunks, plegados=False) -> bool:
    """AC90(a)/AC97/AC98: ¿está la cita, CONTINUA, literal en uno de los `chunks` (textos) de su documento? Plegadas las
    dos (`_literal_plegado`; con `plegados`, los chunks ya lo están) y comparadas como secuencias de palabras completas
    (`segmentos_literales` con un solo segmento): «Nimbo» no está en «Nimbostratus» ni «culpable» en «inculpable», ni
    en «in-\\n culpable» (AC100). Una cita con una marca de elipsis (`cita_con_elipsis`, AC102) nunca lo está, aunque
    sus segmentos lo estén: la elipsis puede borrar una negación, una atribución o una reserva, y solo la admite una
    entrada de `citas:` (vía b)."""
    return not cita_con_elipsis(texto) and segmentos_literales(texto, chunks, plegados)


class ExportCitas:
    """AC90(a)/AC119/AC125: lo que un export del grafo (sus nodos de `nodes.jsonl`) prueba de una cita: si está,
    continua y por palabras completas (`cita_literal`), en un chunk de un documento que el export tiene como nodo
    Document (`literal`); y los nombres (nombre, nombre canónico y alias) que da a cada entidad (`nombres`, AC122)."""

    def __init__(self, nodos):
        self.documentos, self._chunks, self._plegados, self._nombres = set(), {}, {}, {}
        for numero, nodo in enumerate(nodos, 1):
            etiquetas, props = nodo.get('labels'), nodo.get('props')
            if not (etiquetas is None or isinstance(etiquetas, (list, tuple)) and all(isinstance(e, str) for e in etiquetas)):   # AC15: ni TypeError ni AttributeError
                raise ErrorDeForma([f'{MOTIVOS["forma-registro"]} (nodes.jsonl, línea {numero}, `labels`, no es una lista de cadenas)'])
            if not (props is None or isinstance(props, dict)):
                raise ErrorDeForma([f'{MOTIVOS["forma-registro"]} (nodes.jsonl, línea {numero}, `props`, no es un objeto)'])
            etiquetas, props = etiquetas or (), props or {}
            doc = props.get('doc_id').strip() if isinstance(props.get('doc_id'), str) else None
            documental = solo_documental(etiquetas)   # el `text` de una ficha (Person+Chunk) no es el documento fuente
            if doc and documental and 'Document' in etiquetas:
                self.documentos.add(doc)
            if doc and documental and 'Chunk' in etiquetas:
                self._chunks.setdefault(doc, []).append(str(props.get('text') or ''))
            if isinstance(props.get('entity_id'), str) and props['entity_id'].strip():
                self._nombres.setdefault(props['entity_id'].strip(), set()).update(
                    v.strip() for k in ('name', 'canonical_name', 'aliases')
                    for v in (props.get(k) if isinstance(props.get(k), list) else [props.get(k)])
                    if isinstance(v, str) and v.strip())

    def literal(self, texto, doc) -> bool:
        if doc not in self.documentos:
            return False
        if doc not in self._plegados:
            self._plegados[doc] = [_literal_plegado(c) for c in self._chunks.get(doc, ())]
        return cita_literal(texto, self._plegados[doc], plegados=True)

    def nombres(self, eid) -> set[str]:
        return self._nombres.get(eid, set())

    def por_entidad(self) -> dict:
        """AC153: {entity_id: nombres} de todas sus entidades."""
        return self._nombres


def con_sustituto_suelto(valor) -> bool:
    """AC19 (spec 072): ¿lleva alguna cadena o clave de `valor` (un registro JSON ya leído) un sustituto UTF-16 suelto (U+D800 a U+DFFF)? Sin recursión."""
    pila = [valor]
    while pila:
        v = pila.pop()
        if isinstance(v, str):
            if _SUSTITUTO.search(v):
                return True
        elif isinstance(v, dict):
            pila.extend(v)
            pila.extend(v.values())
        elif isinstance(v, list):
            pila.extend(v)
    return False


def literal_del_export(snapdir) -> ExportCitas:
    """AC119: el export del grafo de `snapdir` (`--snapshot`) como prueba de citas (`ExportCitas`). AC13 y AC15 (spec 072): una línea vacía, con bytes que no son UTF-8, un
    JSON roto o un registro que no es un objeto en `nodes.jsonl` es un `ErrorDeForma` (exit 2) con el número de línea, nunca su texto ni un `JSONDecodeError`: no se
    sigue con un export roto (un byte roto no hace de límite de palabra, ni un sustituto suelto escapado: AC19). Lectura binaria, línea a línea: el número es el de la línea que falla."""
    def registros(f):
        for numero, linea in enumerate(f, 1):
            try:
                registro = json.loads(linea.decode('utf-8'))
            except UnicodeDecodeError:
                raise ErrorDeForma([f'{MOTIVOS["linea-jsonl"]} (nodes.jsonl, línea {numero}, no es UTF-8)']) from None
            except RecursionError:
                raise ErrorDeForma([f'{MOTIVOS["linea-jsonl"]} (nodes.jsonl, línea {numero}, anidamiento excesivo)']) from None
            except ValueError:   # JSONDecodeError: también la línea vacía
                raise ErrorDeForma([f'{MOTIVOS["linea-jsonl"]} (nodes.jsonl, línea {numero}, {"no es JSON" if linea.strip() else "vacía"})']) from None
            if not isinstance(registro, dict):
                raise ErrorDeForma([f'{MOTIVOS["linea-jsonl"]} (nodes.jsonl, línea {numero}, no es un objeto)'])
            if con_sustituto_suelto(registro):   # AC19: un `\udcff` o `\ud800` escapado tampoco: haría de límite de palabra; el U+0000 (el texto de un PDF) sí se lee
                raise ErrorDeForma([f'{MOTIVOS["linea-jsonl"]} (nodes.jsonl, línea {numero}, sustituto suelto)'])
            yield registro
    with (Path(snapdir) / 'nodes.jsonl').open('rb') as f:
        return ExportCitas(registros(f))


def prueba_cita(texto, doc, seudos, citas=(), export=None, judicial=True) -> str | None:
    """AC125: la prueba de una cita, o de un tramo citado de una prosa, de una fila de los seudónimos `seudos`, la misma
    en el árbol, el export, el gate y el control posterior: (a) continua y literal en un chunk de su documento `doc`
    del export (`export`, `ExportCitas`; solo en la capa judicial), 'export:<documento>:<huella>'; (b) la entrada de
    `citas:` de ese texto exacto con `doc` entre sus `docs` (`cita_admitida`), 'citas:<huella>'; si no, None."""
    if export is not None and judicial and doc and export.literal(texto, doc):
        return f'export:{doc}:{sha16(texto)}'
    if cita_admitida(citas or (), texto, seudos, doc):
        return f'citas:{huella_cita(texto)[:16]}'
    return None


def huella_cita(texto) -> str:
    """AC90: el sha256 (hex) de la cita normalizada, la clave de una entrada de `citas:`. AC352: un sustituto UTF-16 suelto (que un JSON con
    `\\ud800` produce) tiene huella, como en `sha16`: el texto de una cita de la puerta nunca la hace caer con una excepción."""
    return hashlib.sha256(normalizar_cita(texto).encode('utf-8', 'surrogatepass')).hexdigest()


def cita_admitida(citas, texto, seudonimos, doc=None) -> bool:
    """AC90(b): ¿admite la sección `citas:` de la lista (`citas`) esta cita de una fila de los `seudonimos` dados? Solo
    si la huella de la cita normalizada es el `sha256` de una entrada de su seudónimo (corrección de la enmienda 27: la
    revisión es sobre ESE texto, el que se publica; ni un prefijo ni una cita recortada, tampoco el selector de una
    regla); si la fila trae documento (`doc`), ha de estar en sus `docs`. AC101: una cita con una marca de elipsis
    (`cita_con_elipsis`) de una fila sin documento no se puede contrastar con los `docs` de la entrada: no se admite."""
    if doc is None and cita_con_elipsis(texto):
        return False
    huella = huella_cita(texto)
    return any(isinstance(e, dict) and e.get('seudonimo') in seudonimos and e.get('sha256') == huella
               and (doc is None or doc in (e.get('docs') or ())) for e in citas or ())


def entrada_cita(cita, seudonimo, docs, origen, motivo) -> dict:
    """AC90(b): la entrada de `citas:` que admite exactamente esta cita: su huella, nunca su texto."""
    return {'seudonimo': seudonimo, 'sha256': huella_cita(cita), 'docs': sorted(set(docs)), 'origen': origen,
            'motivo': motivo}


def _tokens(text):
    """Los tokens de un alias, plegado (`_plegar`: «Dª» → «da», «D.ª» → «d.a»), partido solo por espacios, guiones y
    puntos. La puntuación del borde de cada token (una coma suelta, un paréntesis) no dice nada y se quita; la de dentro
    («01/02/1978») se queda, y el token no será una palabra."""
    return _tokens_de(_plegar(text))


def _tokens_de(plegado):
    """Los tokens (`_tokens`) de un texto ya plegado, sin la puntuación de sus bordes (`_sin_bordes`)."""
    return [token for token in map(_sin_bordes, _SEPARADORES.split(plegado)) if token]


def _sin_bordes(token) -> str:
    """Un token sin la puntuación (categoría P) de sus bordes."""
    inicio, fin = 0, len(token)
    while inicio < fin and unicodedata.category(token[inicio]).startswith('P'):
        inicio += 1
    while fin > inicio and unicodedata.category(token[fin - 1]).startswith('P'):
        fin -= 1
    return token[inicio:fin]


@functools.lru_cache(maxsize=1024)
def _fichas_alias(entrada) -> _Fichas:
    """AC60/AC109: los tokens de un alias como los corta `_tokens_de` (espacios, puntos y guiones; sin la puntuación de
    sus bordes), sobre su texto plegado con los homógrafos (`_plegar_homografos` de `_cita_legible`), y sus puntos de
    partición, como en `_fichas`: dos tokens separados solo por un guion o una raya entre dos letras o cifras, salvo
    entre dos cifras de dos números (`_CORTE_ALIAS`, grupo 1; AC132: la misma regla que `_fichas`, una cifra de un
    token que mezcla letras y cifras puede ser una letra: «Nimb0-5tratus»)."""
    plegado = _plegar_homografos(_cita_legible(entrada))
    tokens, unibles, unible, desde = [], [], False, 0
    for m in (*_CORTE_ALIAS.finditer(plegado), None):
        token = _sin_bordes(plegado[desde:m.start() if m else len(plegado)])
        if token:
            if tokens:   # entre dos cifras, solo si uno de los dos tokens mezcla letras y cifras
                unibles.append(unible and not (tokens[-1][-1].isdigit() and token[0].isdigit()
                                               and not _mezcla(tokens[-1]) and not _mezcla(token)))
            tokens.append(token)
        unible = bool(token and m and m.group(1))
        desde = m.end() if m else desde
    return _Fichas(tuple(tokens), tuple(unibles))


def _nombre(seudonimo_id) -> tuple[str, ...]:
    """El nombre de pila del roster en el orden del slug: sus palabras sin las marcas ni los números."""
    slug = str(seudonimo_id).split(':', 1)[-1]
    return tuple(t for t in slug.split('-') if t and t not in _MARCAS and not t.isdigit())


def _es_nombre(palabras, nombre) -> bool:
    """¿Son `palabras` el nombre de pila `nombre` (`_nombre`): todas sus palabras y solo ellas, en cualquier orden? El
    mismo reconocimiento para una variante permitida (AC44), un alias civil sin marca (AC60) y la mención explícita
    (AC66): de un nombre compuesto, «segundo primero» también es él (AC71). Un alias civil con marca basta con que las
    contenga todas (AC77)."""
    return bool(nombre) and Counter(palabras) == Counter(nombre)


def es_variante_nombre(seudonimo_id, alias) -> bool:
    """El alias es exactamente el nombre de pila del seudónimo: quitados un tratamiento inicial, las marcas del roster
    y (AC44 ampliado, enmienda 22) los demás tokens del slug del propio id («ats», números, año: «X (ATS nnnn/2025)» ≡
    `Person:x-ats-nnnn-2025`), con cualquier puntuación entre ellos, el resto de sus tokens (`_tokens`) son las palabras
    del nombre, ni una más ni una menos (`_es_nombre`). Un token con dígitos que no son del slug, de otro alfabeto o una
    palabra ajena («el investigado Sr. X», «X 01/02/1978», «X (Sociedad)») no lo es."""
    tokens = _tokens(alias)
    inicio = 0
    while inicio < len(tokens) and tokens[inicio] in _TRATAMIENTO:
        inicio += 1
    slug = str(seudonimo_id).split(':', 1)[-1].split('-')
    propios = _MARCAS | (set(slug) - set(_nombre(seudonimo_id)))

    def sobra(token):   # una marca o una pieza del slug, también «nnnn/2025»: todas sus partes lo son
        partes = [p for p in re.split(r'\W+', token) if p]
        return bool(partes) and all(p in propios for p in partes)

    resto = [t for t in tokens[inicio:] if not sobra(t)]
    return all(_PALABRA.fullmatch(t) for t in resto) and _es_nombre(resto, _nombre(seudonimo_id))


# AC338 (4ª pasada de Codex): la colisión de un nombre civil con el de un seudónimo se juzga por lo que SE VE, con el reconocimiento de
# nombres del proyecto (`_fichas`: homógrafos griegos y cirílicos, formas de compatibilidad, invisibles, diacríticos y los puntos de
# partición de un guion o una comilla; `_es_forma`: cada dígito, él mismo o una de sus letras) y no solo por las variantes AC44, que
# siguen siendo lo único que el roster permite a sus propios seudónimos (`es_variante_nombre`). Los signos que se leen como una letra
# («Silv!a», «@na», «$ilvia», «Si|via», «°scar») se leen como su dígito: «!», «|», 1; «@», 4; «$», 5; «°», 0 (6ª pasada: o como
# puntuación, abajo). Y la «I» mayúscula y la «l» minúscula, que casi ninguna tipografía distingue («SiIvia», «lsabel»), son también un
# 1, que se lee «l» o «i» (`_DIGITO_LETRAS`); la «l» solo al empezar palabra, donde la «i» es mayúscula: «Sllvia» no se lee «Silvia»
# (la «i» lleva punto).
# Spec 072: una «l» minúscula dentro de una palabra o tirada en mayúsculas es una «I» (`variante`, fuera de este bloque): la colisión es
# este reconocimiento sobre el texto o sobre cualquiera de sus lecturas (`_textos_a_leer`); el reconocimiento no cambia.
# 5ª pasada: cualquier otra puntuación ASCII (`_PUNTUACION_QUE_UNE`: «Os.car», «Os_car», «Os,car», «Os/car», «Os*car», «Os&car»…) se
# lee como lo que el proyecto lee de cualquier signo que no es un guion (`_PARTIDA`, AC191): pegada a una letra o cifra por cada
# lado, parte la palabra («Os•car» es «Oscar» y «Os car»); con un espacio al lado no parte nada («Os. car» son dos palabras). Pasa
# antes a «•», un signo de `_OTRO_SIGNO`, y su forma de ancho completo o su confusable ya es el signo ASCII (`_normalizar`:
# «Os．car», «Os·car»). Una tirada de guiones sin espacio detrás es un solo corte («Os--car»). Es la regla del proyecto: choca lo
# que se lee como UNA palabra, sin tocar el espacio. Cuesta que una sigla («A.N.A.») choque con un seudónimo («Ana»); sobre los
# exports reales, cero.
_PUNTUACION_QUE_UNE = '#%&()*+,./:;<=>?[\\]^_{}~'
# AC348: la «Í» (U+00CD) tiene la forma de la «I» con una tilde y, como la «I», se lee «l» o «i» (37 valores del export real la llevan: no se
# puede sacar del literal, así que choca): «SiÍvia» como «SiIvia».
_SIMBOLOS_LEET = str.maketrans({'I': '1', '\u00cd': '1', **dict.fromkeys(_PUNTUACION_QUE_UNE, '\u2022')})
_TIRADA_DE_GUIONES = re.compile(r'-{2,}(?!\s)')
_L_INICIAL = re.compile(r'(?<![^\W_])l')
# 6ª pasada: los signos que se leen como una letra (`_SIGNO_A_CIFRA`) son también puntuación ASCII o de otro alfabeto: «0scar!» es «0scar»
# con un cierre, «Os!car» es «Oscar» partido por un signo, y la lectura como letra no deja de valer («Silv!a»). Cada signo es una u otra
# por su cuenta («Silv!a!»: una letra y un cierre): el valor se lee con una marca de uso privado por signo (`_MARCA_DE`, la de su
# cifra; ninguna otra cosa del módulo las usa: `_OTRO_SIGNO` las excluye), dentro de su pieza, y `_lee_como_marcas` decide cada una al
# recorrer el texto: su cifra, parte de la palabra que no se puede cortar junto a ella («Isabe!» con un «!» que sea una «l» no corta otra palabra), o puntuación,
# que junto a un espacio o en un borde no hace nada y pegada a una letra o cifra por cada lado parte la palabra, como cualquier otra
# puntuación (`_PARTIDA`). Sin enumerar los 2^k modos de leer k signos.
# 9ª pasada (AC346-AC349, lista blanca): la tabla no crece más. Los parecidos no ASCII de una raya o una exclamación ya no se enumeran ni se
# definen por categorías Unicode: un nombre solo lleva los caracteres del literal `_ALFABETO_NOMBRES` (`caracteres_no_permitidos`). De los no
# ASCII permitidos, solo el grado, «°», es un signo que se lee como una letra (la «o») y por eso es el único de la tabla; los indicadores
# ordinales («ª», «º») y las letras con diacrítico ya los lee `_fichas` como su letra base.
_SIGNOS_DE_CIFRA = {'1': '!|', '4': '@', '5': '$', '0': '\u00b0'}
_SIGNO_A_CIFRA = {signo: cifra for cifra, signos in _SIGNOS_DE_CIFRA.items() for signo in signos}
_SIGNOS_LEET = re.compile('[' + ''.join(_SIGNO_A_CIFRA) + ']')
_CIFRAS_DE_SIGNO = str.maketrans(_SIGNO_A_CIFRA)
_MARCA_DE = {'0': '\uf8f0', '1': '\uf8f1', '4': '\uf8f4', '5': '\uf8f5'}
_CIFRA_DE_MARCA = {marca: cifra for cifra, marca in _MARCA_DE.items()}
_L_TRAS_SIGNO = '\uf8f6'   # una «l» minúscula tras un signo: empieza palabra (`_L_INICIAL`) solo si el signo es puntuación
_CIFRAS_DE_MARCA = str.maketrans({**_CIFRA_DE_MARCA, _L_TRAS_SIGNO: 'l'})
# AC338 (5ª pasada de Codex): ni un tope de piezas ni una enumeración de segmentaciones. Un nombre con tantas puntuaciones que supere un
# presupuesto no es «ninguna colisión»: se lee por programación dinámica sobre las piezas (`_lee_como`), acotada por el candidato (una
# palabra del texto solo puede ser una forma de un tratamiento, una marca, una pieza del slug o una palabra del nombre de su MISMO largo:
# `_es_forma`), y el único presupuesto que queda, los estados que alcanza, se cuenta como colisión (falla cerrado), nunca como silencio.
_MAX_ESTADOS = 200_000


class _Agotado(Exception):
    """8ª pasada: un presupuesto (los estados de la lectura, `_MAX_ESTADOS`, o las lecturas junto a comillas) se agotó y no se puede decidir si
    el valor se lee como el nombre de un seudónimo. `forma_visual_de_nombre` lo cuenta como colisión (falla cerrado); `homonimos_snapshot`
    lo denuncia aparte, con su propio motivo (`agotamiento`): sigue siendo un problema, pero no finge una colisión con cada seudónimo."""
# 7ª pasada: las lecturas a través de comillas (`_lecturas_texto`) dependen de si un signo pegado a un hueco con comillas es una letra
# (alfanumérico: la comilla une) o puntuación (parte del hueco: «AB-"-!CDE» es «AB-"-.CDE»). Los signos de un hueco con comillas se
# fijan por enumeración de sus modos (la lectura de siempre, exacta); los demás los decide `_lee_como_marcas`. 8ª pasada: el tope es POR
# HUECO (`_MAX_SIGNOS_POR_HUECO`), y el de lo que cuesta enumerarlos todos, el producto de los modos de cada hueco, es el de lecturas
# (`_MAX_LECTURAS_CON_COMILLAS`). Pasado cualquiera: `_Agotado`, nunca silencio.
_HUECO_CON_SIGNOS = re.compile(rf'(?:[\s"\'`\u2010-\u2015-]|{_OTRO_SIGNO}|[!@$|])+')
_MAX_SIGNOS_POR_HUECO = 8
_MAX_LECTURAS_CON_COMILLAS = 1024
_UNION_CON_ESPACIO = re.compile(r'[-\u2010-\u2015]\s+')   # el corte de palabra de un PDF: con un signo pegado (que sería «•») ya no lo es


_LECTURA_AGOTADA = _Fichas(('\uf8f1',), ())   # la lectura que vale por «agotado»: demasiados signos junto a comillas para enumerarlos


def _fichas_visuales(texto) -> tuple[_Fichas, ...]:
    """Las lecturas de un nombre (`_lecturas_texto`: también a través de sus comillas) como piezas con sus puntos de partición, tras
    plegar su forma de ancho completo y sus confusables (`_normalizar`), leer la puntuación ASCII como un signo de partición pegado
    (`_PARTIDA`, sin espacios) y juntar las tiradas de guiones en un solo corte. Un signo que se lee como letra o como puntuación
    (`_SIGNO_A_CIFRA`) lleva su marca en la pieza (`_fichas_con_marcas`), salvo los de un hueco con comillas: esos se fijan, uno a uno, como
    su cifra o como «•» (`_HUECO_CON_SIGNOS`), y cada modo es una lectura entera, la de siempre. Sin caché propia: cada lectura sin
    marcas ya está en la de `_fichas_de` y no hay otra caché que clasificar (`CLASES_DE_CACHE`)."""
    leido = _TIRADA_DE_GUIONES.sub('-', _normalizar(texto).translate(_SIMBOLOS_LEET))
    if not _SIGNOS_LEET.search(leido):
        return _lecturas_con_marcas(leido)
    huecos = [[i for i in range(m.start(), m.end()) if leido[i] in _SIGNO_A_CIFRA]
              for m in _HUECO_CON_SIGNOS.finditer(leido) if any(_es_comilla(c) for c in m.group())]
    sitios = [i for hueco in huecos for i in hueco]
    if max(map(len, huecos), default=0) > _MAX_SIGNOS_POR_HUECO or 2 ** len(sitios) > _MAX_LECTURAS_CON_COMILLAS:
        return (_LECTURA_AGOTADA,)
    lecturas = {}
    for modos in itertools.product((True, False), repeat=len(sitios)):
        fijo = list(leido)
        for i, como_letra in zip(sitios, modos):
            fijo[i] = _SIGNO_A_CIFRA[leido[i]] if como_letra else '\u2022'
        lecturas.update(dict.fromkeys(_lecturas_con_marcas(''.join(fijo))))
    return tuple(lecturas)


def _lecturas_con_marcas(leido) -> tuple[_Fichas, ...]:
    """Las lecturas de un texto ya leído (`_fichas_visuales`): cada signo que quede en él con su marca; las comillas, con cada signo como
    su cifra."""
    if not _SIGNOS_LEET.search(leido):
        return tuple(_fichas(lectura) for lectura in _lecturas_texto(_L_INICIAL.sub('1', leido)))
    es_signo = [c in _SIGNO_A_CIFRA for c in leido]
    tras_signo = [c == 'l' and i > 0 and es_signo[i - 1] for i, c in enumerate(leido)]
    return tuple(_fichas_con_marcas(''.join(_MARCA_DE[_SIGNO_A_CIFRA[o]] if signo else _L_TRAS_SIGNO if tras else c
                                            for o, c, signo, tras in zip(leido, lectura, es_signo, tras_signo)))
                 for lectura in _lecturas_texto(_L_INICIAL.sub('1', leido.translate(_CIFRAS_DE_SIGNO))))


def _fichas_con_marcas(marcado) -> _Fichas:
    """Las fichas de una lectura con la marca de cada signo (`_MARCA_DE`) donde estaba: las piezas y los puntos de partición son los de
    la lectura con cada marca como su cifra (`_trocear`, sin la regla de los números: cada signo puede partir una pieza), y cada pieza
    guarda sus marcas. Un punto de partición que es un guion con espacios detrás (`_UNION_CON_ESPACIO`) vale 2, no True: un signo suelto
    pegado a él (que sería «•») lo deja de ser (`_lee_como_marcas`)."""
    plegado = _plegar_homografos(_cita_legible(marcado))
    cifras = plegado.translate(_CIFRAS_DE_MARCA)
    trozos = list(_PALABRA_PROSA.finditer(cifras))
    unibles = [2 if unible and _UNION_CON_ESPACIO.fullmatch(cifras, a.end(), b.start()) else unible
               for unible, a, b in zip(_trocear(cifras, numeros=False).unibles, trozos, trozos[1:])]
    return _Fichas(tuple(plegado[m.start():m.end()] for m in trozos), tuple(unibles))


def _propios(seudonimo_id) -> frozenset:
    """Lo que sobra de un nombre de pila en un alias del propio seudónimo (`es_variante_nombre`): las marcas del roster y las
    demás piezas de su slug («ats», números, año)."""
    slug = str(seudonimo_id).split(':', 1)[-1].split('-')
    return frozenset(_MARCAS | (set(slug) - set(_nombre(seudonimo_id))))


def _por_largo(palabras) -> dict:
    """{largo: [(índice, palabra)]}: una palabra del texto solo puede ser una forma (`_es_forma`) de las de su mismo largo."""
    out = {}
    for k, p in enumerate(palabras):
        out.setdefault(len(p), []).append((k, p))
    return out


def _lee_como(fichas, nombre, propios) -> bool:
    """¿Se lee el texto de `fichas` (`_fichas_visuales`: sus piezas y los puntos de partición entre ellas) como el nombre de pila `nombre`
    (`_nombre`) con las marcas y piezas del slug `propios`? Lo que se decía enumerando cada segmentación posible (unidas, las piezas que
    separa solo un punto de partición, o no): un tratamiento inicial (todos los que haya, `_TRATAMIENTO`), las marcas y piezas del slug
    (en cualquier sitio) y, el resto, las palabras del nombre, ni una más ni una menos y en cualquier orden, cada una una forma
    (`_es_forma`) de la que le toca. Pero por programación dinámica: el estado tras las piezas anteriores a `i` es qué palabras del
    nombre están asignadas y si aún se está en el tratamiento inicial; de cada pieza salen las palabras (unidas por puntos de partición)
    que no pasan del largo de la mayor palabra que algo puede ser. Lineal en las piezas, sin tope de piezas. Con más de `_MAX_ESTADOS`
    estados no se puede decidir: `_Agotado`."""
    piezas, unibles = fichas.piezas, fichas.unibles
    n = len(piezas)
    if not n:
        return False
    tratamientos, de_slug, del_nombre = _por_largo(_TRATAMIENTO), _por_largo(propios), _por_largo(nombre)
    largo = max(len(p) for p in (*nombre, *propios, *_TRATAMIENTO))
    alcanzables = [set() for _ in range(n + 1)]   # {(palabras del nombre asignadas como máscara, ¿aún en el tratamiento inicial?)}
    alcanzables[0].add((0, True))
    estados = 0
    for i in range(n):
        if not alcanzables[i]:
            continue
        estados += len(alcanzables[i])
        if estados > _MAX_ESTADOS:
            raise _Agotado
        palabra, j = '', i
        while True:
            palabra += piezas[j]
            if len(palabra) > largo:
                break
            es_tratamiento = any(_es_forma(palabra, t) for _, t in tratamientos.get(len(palabra), ()))
            es_del_slug = any(_es_forma(palabra, q) for _, q in de_slug.get(len(palabra), ()))
            del_nombre_k = [k for k, w in del_nombre.get(len(palabra), ()) if _es_forma(palabra, w)]
            for usadas, inicial in alcanzables[i]:
                if inicial and es_tratamiento:
                    alcanzables[j + 1].add((usadas, True))
                elif es_del_slug:
                    alcanzables[j + 1].add((usadas, False))
                else:
                    alcanzables[j + 1].update((usadas | 1 << k, False) for k in del_nombre_k if not usadas >> k & 1)
            if j + 1 >= n or not unibles[j]:
                break
            j += 1
    todas = (1 << len(nombre)) - 1
    return any(usadas == todas for usadas, _ in alcanzables[n])


def _lee_como_marcas(fichas, nombre, propios) -> bool:
    """`_lee_como` de una lectura con las marcas de los signos que se leen como letra o como puntuación (`_fichas_con_marcas`). Un solo
    recorrido por los caracteres de la lectura (`fichas.cadena`), que decide cada signo por su cuenta, sin enumerar los 2^k modos de
    leer k signos: su cifra (un carácter más de la palabra que se está leyendo, que no se puede cortar junto a él), o puntuación, que no
    es nada y deja cortar la palabra a un lado y a otro (`tipos`: entre dos piezas un punto de partición, o un límite; dentro de una,
    nada). Una palabra en curso es el conjunto de sus alineaciones (palabra del tratamiento, de las marcas o del nombre, y por dónde va),
    y se cierra con la regla de `_lee_como` (`_es_forma`: «0scar» es «oscar» solo con una letra de verdad). Un signo suelto no es el
    límite de una palabra que nunca empezó, y una «l» tras él empieza palabra (`_L_INICIAL`). Y una puntuación, como en `_trocear`, no une
    dos números: «0!5!car» no es «oscar» (la racha de la palabra, y si lo que une es un número a otro, `una`). Con más de `_MAX_ESTADOS`
    estados no se puede decidir: `_Agotado`."""
    letras, tipos, inicios = fichas.cadena
    n = len(letras)
    solo_union = {inicios[g + 1] for g, unible in enumerate(fichas.unibles) if unible == 2}   # dónde cruza un guion con espacios detrás
    metas = ([('t', 0, p) for p in sorted(_TRATAMIENTO)] + [('s', 0, p) for p in sorted(propios)]
             + [('n', k, p) for k, p in enumerate(nombre)])
    inicio = frozenset((t, 0, False, True) for t in range(len(metas)))   # (meta, letras leídas, ¿alguna letra de verdad?, ¿todas iguales?)
    memo_avance, memo_cierre = {}, {}

    def avanza(alineaciones, c):
        if (alineaciones, c) not in memo_avance:
            memo_avance[alineaciones, c] = frozenset(
                (t, p + 1, letra or c.isalpha(), llana and c == metas[t][2][p]) for t, p, letra, llana in alineaciones
                if p < len(metas[t][2]) and (c == metas[t][2][p] or metas[t][2][p] in _DIGITO_LETRAS.get(c, '')))
        return memo_avance[alineaciones, c]

    def cierra(usadas, inicial, alineaciones):
        if (usadas, inicial, alineaciones) not in memo_cierre:
            completas = [metas[t] for t, p, letra, llana in alineaciones if p == len(metas[t][2]) and (llana or letra)]
            clases = {clase for clase, _, _ in completas}
            if inicial and 't' in clases:
                memo_cierre[usadas, inicial, alineaciones] = ((usadas, True),)
            elif 's' in clases:
                memo_cierre[usadas, inicial, alineaciones] = ((usadas, False),)
            else:
                memo_cierre[usadas, inicial, alineaciones] = tuple(
                    (usadas | 1 << k, False) for clase, k, _ in completas if clase == 'n' and not usadas >> k & 1)
        return memo_cierre[usadas, inicial, alineaciones]

    # Un estado es (palabras del nombre asignadas, ¿aún en el tratamiento inicial?, alineaciones de la palabra en curso, ¿acaba de dejar un signo
    # suelto?, racha, una). `racha` es la del trozo de la palabra desde su último corte: 0 vacío, 1 solo cifras, 2 con alguna letra; `una`,
    # que el último corte unió un trozo de solo cifras con el siguiente, que ha de llevar una letra (como en `_trocear`, dos números seguidos
    # no se unen). Un corte es un signo suelto o un punto de partición entre dos piezas (`corta`).
    def corta(racha, una):
        return None if racha == 1 and una else (0, racha == 1 or (racha == 0 and una))

    def paso(estado, c, soltable=True):
        usadas, inicial, alineaciones, suelto, racha, una = estado
        if c in _CIFRA_DE_MARCA:
            letra = avanza(alineaciones, _CIFRA_DE_MARCA[c])
            corte = corta(racha, una) if soltable else None
            return ([(usadas, inicial, alineaciones, True, *corte)] if corte else []) + (
                [(usadas, inicial, letra, False, 2 if racha == 2 else 1, una)] if letra else [])
        c = ('1' if suelto else 'l') if c == _L_TRAS_SIGNO else c
        siguiente = avanza(alineaciones, c)
        if not siguiente:
            return []
        return [(usadas, inicial, siguiente, False, 2 if c.isalpha() or racha == 2 else 1, una and not c.isalpha())]

    libres = [set() for _ in range(n + 1)]   # {(palabras del nombre asignadas, ¿aún en el tratamiento inicial?, ¿tras un signo suelto?)}
    libres[0].add((0, True, False))
    en_curso, estados = set(), 0
    for o in range(n + 1):
        for usadas, inicial, alineaciones, suelto, racha, una in en_curso:
            if (tipos[o] is not None or suelto) and not (una and racha == 1):   # acaba en un límite, un punto de partición o tras un signo suelto
                libres[o].update((u, i, suelto) for u, i in cierra(usadas, inicial, alineaciones))
        if o == n:
            break
        estados += len(libres[o]) + len(en_curso)
        if estados > _MAX_ESTADOS:
            raise _Agotado
        c = letras[o]
        siguientes = set()
        for estado in (en_curso if tipos[o] != 'H' else ()):   # una palabra no cruza un límite
            if tipos[o] == 'J':   # un punto de partición entre dos piezas: el trozo acaba; el siguiente empieza
                corte = corta(estado[4], estado[5])
                if not corte or (o in solo_union and estado[3]):   # con un signo suelto a un lado, el guion con espacios no une
                    continue
                estado = (*estado[:4], *corte)
            siguientes.update(paso(estado, c, o not in solo_union))
        for usadas, inicial, suelto in libres[o]:
            siguientes.update(paso((usadas, inicial, inicio, suelto, 0, False), c))
            if c in _CIFRA_DE_MARCA:
                libres[o + 1].add((usadas, inicial, True))
        en_curso = siguientes
    todas = (1 << len(nombre)) - 1
    return any(usadas == todas for usadas, _, _ in libres[n])


def _lee_como_nombre(seudonimo_id, lecturas) -> bool:
    """`_lee_como` en alguna de las `lecturas` (`_fichas_visuales`) de un texto, para el nombre de pila de un seudónimo. `_Agotado` si no
    se puede decidir."""
    nombre = _nombre(seudonimo_id)
    if not nombre:
        return False
    propios = _propios(seudonimo_id)
    for fichas in lecturas:
        if fichas is _LECTURA_AGOTADA:
            raise _Agotado
        if (_lee_como_marcas if fichas.con_marcas else _lee_como)(fichas, nombre, propios):
            return True
    return False


def forma_visual_de_nombre(seudonimo_id, texto) -> bool:
    """Spec 072 (AC2): `_forma_visual_de_texto` del texto o de cualquiera de sus lecturas (`_textos_a_leer`: una «l» minúscula por «I» en una palabra
    en mayúsculas, de cinco maneras). Un agotamiento sobre cualquiera de ellas es una colisión (AC4)."""
    return any(_forma_visual_de_texto(seudonimo_id, t) for t in _textos_a_leer(texto))


def _forma_visual_de_texto(seudonimo_id, texto) -> bool:
    """AC338: ¿se lee `texto`, como lo lee el reconocimiento de nombres, como el nombre de pila del seudónimo? Lo mismo que
    `es_variante_nombre` (un tratamiento inicial, las marcas y las piezas del slug que sobran, y el resto, las palabras del nombre, ni una
    más ni una menos y en cualquier orden) pero cada palabra, por su forma visual: sus homógrafos de otro alfabeto como letras latinas,
    sus dígitos y signos como la letra que parecen (un signo que se lee como letra, `_SIGNO_A_CIFRA`, es también puntuación, cada uno por
    su cuenta: `_lee_como_marcas`), y sus piezas unidas por un guion, una comilla o cualquier puntuación ASCII pegada
    (`_PUNTUACION_QUE_UNE`) como una palabra o como dos, sean cuantas sean (`_lee_como`: sin tope de piezas). El nombre de pila DENTRO
    de un nombre más largo no es una colisión: es el nombre de otra persona."""
    if not isinstance(texto, str):
        return False
    try:
        return _lee_como_nombre(seudonimo_id, _fichas_visuales(texto))
    except _Agotado:   # no se puede descartar: falla cerrado (`homonimos_snapshot` lo denuncia aparte)
        return True


def _forma_corta(nucleo, palabras) -> bool:
    """¿Es `nucleo` una forma corta de un nombre (`palabras`)? Sus palabras enteras, en cualquier orden, o prefijos de
    palabras seguidas del nombre («adrian» de «adriana», «fran» de «francisco»); un infijo o un sufijo («ana» en
    «cabanas») no lo es."""
    if Counter(nucleo) <= Counter(palabras):
        return True
    n = len(nucleo)
    return any(all(p.startswith(t) for t, p in zip(nucleo, palabras[k:k + n])) for k in range(len(palabras) - n + 1))


# AC70: las marcas que declaran un seudónimo en un alias civil.
_MARCAS_O_JUDICIAL = _MARCAS_ALIAS | {'judicial'}


def _marcado_con_nombre(fichas, nombre, marcas, donde) -> bool:
    """AC70/AC77/AC109: ¿lleva el texto, en alguna lectura (`_fichas`), una marca y todas las palabras del nombre de
    pila `nombre`, cada una en una palabra distinta que la tiene por forma, en cualquier orden y aunque otras las
    separen? `marcas`: {pieza: [fin]} de las marcas (`_MARCAS_O_JUDICIAL`); `donde`: {pieza: {palabra: [fin]}} de las
    palabras de los nombres de pila. De izquierda a derecha por las piezas, con los estados vistos: cada una se salta o
    empieza la marca o una palabra del nombre que falta."""
    completo = (1 << len(nombre)) - 1
    vistos, pendientes = set(), [(0, 0, False)]
    while pendientes:
        estado = pendientes.pop()
        if estado in vistos:
            continue
        vistos.add(estado)
        pieza, hechas, marca = estado
        if hechas == completo and marca:
            return True
        if pieza == len(fichas.piezas):
            continue
        pendientes.append((pieza + 1, hechas, marca))
        if not marca:
            pendientes += [(fin, hechas, True) for fin in marcas.get(pieza, ())]
        pendientes += [(fin, hechas | 1 << j, marca) for j, p in enumerate(nombre)
                       if not hechas >> j & 1 and p not in _SIN_NOMBRE for fin in donde.get(pieza, {}).get(p, ())]
    return False


def _donde(fichas, palabras) -> dict:
    """{pieza: {palabra: [fin]}} de cada una de `palabras` que aparece en el texto, en alguna lectura, como forma de una
    palabra suya (`_es_forma`) que empieza en esa pieza: por su esqueleto y luego por posición."""
    por_esqueleto = {}
    for palabra in set(palabras):
        por_esqueleto.setdefault(_esqueleto(palabra), []).append(palabra)
    largo, donde = max(map(len, palabras), default=0), {}
    for inicio in range(len(fichas.piezas)):
        for fin, palabra in _palabras_desde(fichas, inicio, largo):
            for objetivo in por_esqueleto.get(_esqueleto(palabra), ()):
                if _es_forma(palabra, objetivo):
                    donde.setdefault(inicio, {}).setdefault(objetivo, []).append(fin)
    return donde


def _tras_tratamientos(fichas) -> list[int]:
    """Los tokens de un alias (`_fichas_alias`) en que puede empezar su núcleo: el primero y los que siguen a uno o más
    tratamientos (`_TRATAMIENTO`, con cualquier forma y sin la de una marca)."""
    largo = max(map(len, _TRATAMIENTO))
    vistos, pendientes = {0}, [0]
    while pendientes:
        inicio = pendientes.pop()
        if inicio == len(fichas.piezas):
            continue
        for fin, palabra in _palabras_desde(fichas, inicio, largo):
            if (any(_es_forma(palabra, t) for t in _TRATAMIENTO)
                    and not any(_es_forma(palabra, m) for m in _MARCAS_O_JUDICIAL) and fin not in vistos):
                vistos.add(fin)
                pendientes.append(fin)
    return sorted(vistos)


def _alias_es_nombre(fichas, inicio, nombre, cortas) -> bool:
    """AC44/AC60/AC109/AC112: ¿son los tokens de un alias (`_fichas_alias`) desde el token `inicio` (tras los
    tratamientos del principio, `_tras_tratamientos`), en alguna lectura, las palabras del nombre de pila `nombre` y
    solo ellas, en cualquier orden (`_es_nombre`), sin una marca y sin ser una forma corta (`_forma_corta`) de los
    nombres de `cortas`? Cada orden de las palabras del nombre contra los tokens, por posición (`_tramo`): sin el
    producto de las formas de los tokens."""
    tokens = fichas.piezas
    for orden in dict.fromkeys(itertools.permutations(nombre)):
        cortes = None if orden[0] in _TRATAMIENTO else _tramo(fichas, inicio, orden)
        if (cortes and cortes[-1] == len(tokens)
                and not any(_es_forma(''.join(tokens[a:b]), m) for a, b in zip(cortes, cortes[1:])
                            for m in _MARCAS_O_JUDICIAL)
                and not any(_forma_corta(list(orden), c) for c in cortas)):
            return True
    return False


def alias_civil_nombra_seudonimo(entrada, entidad_id, ids, identidades=()) -> bool:
    """AC60/AC70: ¿empareja esta entrada de `alias_deny` (`drop` o `keep_only`) de una entidad civil con un seudónimo
    judicial (`ids`)? Sí si contiene el `entity_id` de uno; si lleva una marca (`_MARCAS_ALIAS` o «judicial») y entre
    sus palabras (`_palabras`, sin las de `_SIN_NOMBRE`) están todas las del nombre de pila de uno, en cualquier orden y
    aunque otras las separen (AC77: «X (seudónimo de anonimización) Y»), sin excepción (`_marcado_con_nombre`); y, sin
    marcas, si sus palabras (`_tokens`, sin un tratamiento inicial) son el nombre de pila de uno (los alias de la lista
    de permitidas son variantes de él, AC44) y no son una forma corta (`_forma_corta`) del nombre canónico de la entidad
    (el de `entidad_id`) ni de ninguno de los textos de `identidades` (nombre y alias de las identidades cubiertas)
    (`_alias_es_nombre`). La forma de los alias civiles es la política 061-C. AC104/AC105(b)/AC107/AC109: en cualquier
    lectura de la entrada (`_fichas`, `_fichas_alias`: cada punto de partición unido o separado, con los homógrafos
    griegos y cirílicos como letras latinas) y con cualquier forma de cada palabra o token (los dígitos homógrafos de
    una letra, leídos después de unir); basta una. AC112: por posición contra el nombre de pila de cada seudónimo, sin
    enumerar el producto de las formas de los tokens. AC195: también a través de sus comillas (`_lecturas_texto`:
    «Be"ni"to», «Be"→ni"to», «seudónimo Be"ni"to»), como los nombres de un texto; basta una lectura."""
    entrada = str(entrada or '')
    del_roster = {s.lower() for s in ids}   # AC133: el texto se normaliza una vez y se recorre una
    if any(m[0].lower() in del_roster for m in _ID_EN_TEXTO.finditer(_normalizar(entrada))):
        return True
    nombres = [n for n in dict.fromkeys(_nombre(sid) for sid in sorted(ids)) if n]
    canonico = ' '.join(str(entidad_id or '').split(':', 1)[-1].split('-'))
    cortas = [_tokens(t) for t in (canonico, *identidades)]
    por_largo = {}
    for nombre in nombres:
        por_largo.setdefault(sum(map(len, nombre)), []).append(nombre)
    lecturas = _lecturas_texto(entrada)
    for lectura in lecturas:
        fichas = _fichas(lectura)
        marcas = _donde(fichas, _MARCAS_O_JUDICIAL)
        if marcas:
            marcas = {pieza: [fin for fines in por_marca.values() for fin in fines]
                      for pieza, por_marca in marcas.items()}
            donde = _donde(fichas, {p for nombre in nombres for p in nombre if p not in _SIN_NOMBRE})
            presentes = {p for por_pieza in donde.values() for p in por_pieza}   # AC137: antes de recorrer estados
            if any(_marcado_con_nombre(fichas, nombre, marcas, donde) for nombre in nombres
                   if all(p in presentes for p in nombre)):
                return True
        alias = _fichas_alias(lectura)   # (descartado antes el largo total: el de las letras que quedan)
        restantes = list(itertools.accumulate(map(len, reversed(alias.piezas)), initial=0))[::-1]
        if any(_alias_es_nombre(alias, inicio, nombre, cortas if lectura is lecturas[0] else ())   # AC208: la base
               for inicio in _tras_tratamientos(alias) for nombre in por_largo.get(restantes[inicio], ())):
            return True
    return False


def _texto(value) -> str:
    return value.strip() if isinstance(value, str) else ''


def _desconocidas(entrada, claves) -> str | None:
    """Cuántas claves de más trae una entrada y cuáles valen; nunca cuáles sobran (son texto del fichero)."""
    sobran = sum(1 for k in entrada if k not in claves)
    return f'{sobran} clave(s) desconocida(s); permitidas: {", ".join(claves)}' if sobran else None


def validar_permitidos(data, ids, lineas=None, publicos=None, civiles=None) -> list[tuple]:
    """[(línea, seudónimo, motivo)] de una lista mal formada. Ni los motivos ni el seudónimo (None si no está
    declarado) reproducen texto del fichero: posición, recuentos y claves permitidas; un documento o un `tipo` que no
    vale, por su huella (`problemas_tipo`). El `tipo` ha de ser un tipo de relación de la ontología (spec 069) escrito
    como ella, y cada elemento de `kinds`, una de sus capas (AC55). Cada valor que consume la política
    (`parametros_politica`, `indice_alias`) es la misma cadena que se valida: con espacios en los bordes, «valor no
    canónico (espacios)» (AC51; la política compara literalmente). Con `publicos` (los documentos del árbol público),
    cada documento de `docs` ha de ser uno de ellos: la lista es pública y lo pondría junto al seudónimo (AC45). Y la
    lista no lleva texto libre (AC80): `origen` en una de las formas cerradas (`_ORIGENES`), `motivo` en una de las
    plantillas (`_MOTIVOS`), y los dos pasan AC78 (`problemas_seudonimo`; `civiles`, el índice de las personas
    civiles)."""
    lineas = lineas or {}
    problemas, vistas, vistos = [], set(), set()
    for i, e in enumerate(data.get('aristas') or []):
        if not isinstance(e, dict):
            problemas.append((_linea(lineas, 'aristas', i), None, f'aristas[{i}]: no es un mapa'))
            continue
        sid, otro, tipo = (_texto(e.get(k)) for k in ('seudonimo', 'otro', 'tipo'))
        kinds, desconocidas = e.get('kinds'), _desconocidas(e, CLAVES_ARISTA)
        motivos = [desconocidas] if desconocidas else []
        motivos += _no_canonicos(e, ('seudonimo', 'otro', 'tipo'), ('kinds', 'docs'))
        if sid not in ids:
            motivos.append('seudónimo no declarado en context/pseudonyms.yaml')
        if not otro.startswith(SENSIBLES):
            motivos.append('`otro` no es una Company ni una Organization')
        motivos += problemas_tipo(tipo)
        if kinds is not None and (not isinstance(kinds, list) or not kinds
                                  or not all(isinstance(k, str) and k for k in kinds)):
            motivos.append('`kinds` ha de ser una lista no vacía de capas')
        elif kinds is not None and SIN_CAPA in kinds:
            motivos.append(f'`kinds` no admite la marca reservada «{SIN_CAPA}»')
        elif kinds is not None:   # AC55: la política compara `source_kind` literalmente (espacios: `_no_canonicos`)
            motivos += [f'kinds[{j}] no es una capa canónica (huella {sha16(k)})' for j, k in enumerate(kinds)
                        if not ontologia().validar('capas', k.strip()).ok]
        docs = e.get('docs')
        if docs is not None and (not isinstance(docs, list) or not docs or len(set(map(str, docs))) != len(docs)
                                 or not all(isinstance(d, str) and d.strip() for d in docs)):
            motivos.append('`docs` ha de ser una lista no vacía de documentos (`source_doc`) sin repetir')
        elif docs is not None and publicos is not None:
            motivos += [f'documento de `docs` fuera del árbol público ({huella_doc(d)})' for d in docs
                        if not doc_publico(d, publicos)]
        origen, motivo = e.get('origen'), e.get('motivo')
        if not str(origen or '').strip() or not str(motivo or '').strip():
            motivos.append('sin `origen` o sin `motivo`')
        else:   # AC80: sin texto libre
            if not (isinstance(origen, str) and _ORIGENES.fullmatch(origen)):
                motivos.append(f'`origen` fuera de las formas cerradas (huella {sha16(origen)})')
            if not (isinstance(motivo, str) and any(p.fullmatch(motivo) for p in _MOTIVOS)):
                motivos.append(f'`motivo` fuera de las plantillas cerradas (huella {sha16(motivo)})')
            motivos += [f'`origen`/`motivo` nombra a un civil, otra entidad o un documento (huella {h})'
                        for h in problemas_seudonimo([(str(origen), PROSA), (str(motivo), PROSA)], {sid, otro}, ids,
                                                     civiles or {}, publicos)]
        if (sid, otro, tipo) in vistas:
            motivos.append('entrada repetida')
        vistas.add((sid, otro, tipo))
        declarado = sid if sid in ids else None
        problemas += [(_linea(lineas, 'aristas', i), declarado, f'aristas[{i}]: {m}') for m in motivos]
    for i, e in enumerate(data.get('alias') or []):
        if not isinstance(e, dict):
            problemas.append((_linea(lineas, 'alias', i), None, f'alias[{i}]: no es un mapa'))
            continue
        sid, permitidos = _texto(e.get('id')), e.get('permitidos')
        declarado, linea = (sid if sid in ids else None), _linea(lineas, 'alias', i)
        desconocidas = _desconocidas(e, CLAVES_ALIAS)
        if desconocidas:
            problemas.append((linea, declarado, f'alias[{i}]: {desconocidas}'))
        problemas += [(linea, declarado, f'alias[{i}]: {m}') for m in _no_canonicos(e, ('id',), ('permitidos',))]
        if sid not in ids:
            problemas.append((linea, declarado, f'alias[{i}]: seudónimo no declarado'))
        if sid in vistos:
            problemas.append((linea, declarado, f'alias[{i}]: entrada repetida'))
        vistos.add(sid)
        if not isinstance(permitidos, list) or not permitidos:
            problemas.append((linea, declarado, f'alias[{i}]: `permitidos` vacío'))
            continue
        for alias in permitidos:
            if not isinstance(alias, str) or not es_variante_nombre(sid, alias):
                problemas.append((linea, declarado,
                                  f'alias[{i}]: un alias permitido no es variante del nombre de pila'))
    vistas_citas = set()
    for i, e in enumerate(data.get('citas') or []):   # AC90: sin texto libre, como las aristas
        linea = _linea(lineas, 'citas', i)
        if not isinstance(e, dict):
            problemas.append((linea, None, f'citas[{i}]: no es un mapa'))
            continue
        sid, huella, docs = _texto(e.get('seudonimo')), e.get('sha256'), e.get('docs')
        motivos = [m for m in [_desconocidas(e, CLAVES_CITA)] if m]
        motivos += _no_canonicos(e, ('seudonimo', 'sha256'), ('docs',))
        if sid not in ids:
            motivos.append('seudónimo no declarado en context/pseudonyms.yaml')
        if not (isinstance(huella, str) and _SHA256.fullmatch(huella)):
            motivos.append('`sha256` ha de ser el sha256 (64 hex en minúsculas) de la cita normalizada')
        if (not isinstance(docs, list) or not docs or len(set(map(str, docs))) != len(docs)
                or not all(isinstance(d, str) and d.strip() for d in docs)):
            motivos.append('`docs` ha de ser una lista no vacía de documentos sin repetir')
        elif publicos is not None:
            motivos += [f'documento de `docs` fuera del árbol público ({huella_doc(d)})' for d in docs
                        if not doc_publico(d, publicos)]
        origen, motivo = e.get('origen'), e.get('motivo')
        if not (isinstance(origen, str) and _ORIGENES.fullmatch(origen)):
            motivos.append(f'`origen` fuera de las formas cerradas (huella {sha16(origen)})')
        if not (isinstance(motivo, str) and any(p.fullmatch(motivo) for p in _MOTIVOS_CITA)):
            motivos.append(f'`motivo` fuera de las plantillas cerradas (huella {sha16(motivo)})')
        if (sid, huella) in vistas_citas:
            motivos.append('entrada repetida')
        vistas_citas.add((sid, huella))
        problemas += [(linea, sid if sid in ids else None, f'citas[{i}]: {m}') for m in motivos]
    return problemas


def _no_canonicos(entrada, claves, listas) -> list[str]:
    """Qué valores de `entrada` llevan espacios en los bordes: la política compara cadenas literalmente (`type(r)`, el
    `entity_id`, la capa, el alias), así que validar la forma recortada y aplicar la original borraría lo que se cree
    permitido (AC51). Por clave y posición, nunca el valor."""
    def sucia(valor):
        return isinstance(valor, str) and valor != valor.strip()

    motivos = [f'`{c}`: valor no canónico (espacios)' for c in claves if sucia(entrada.get(c))]
    for c in listas:
        valores = entrada.get(c)
        if isinstance(valores, list):
            motivos += [f'`{c}`[{j}]: valor no canónico (espacios)' for j, v in enumerate(valores) if sucia(v)]
    return motivos


def problemas_tipo(tipo) -> list[str]:
    """Por qué el `tipo` de una entrada no vale: ha de estar en la ontología (spec 069) y escrito como ella, porque la
    política compara `type(r)` literalmente. Un valor fuera de la ontología nunca se nombra (podría ser cualquier texto
    del fichero o de una decisión): va su huella y la posición. La forma canónica sí, porque es de la ontología."""
    if not tipo:
        return ['sin tipo de relación']
    res = ontologia().validar('tipos_relacion', tipo)
    if not res.ok:
        return [f'tipo de relación fuera de la ontología (spec 069): huella {sha16(tipo)}']
    if res.valor != tipo:
        return [f'tipo de relación no canónico: la ontología lo escribe {res.valor}']
    return []


def volcar_permitidos(data) -> str:
    """El fichero canónico: cabecera fija y entradas ordenadas, una por línea; `citas:` solo si tiene entradas."""
    def flow(value):
        return yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, sort_keys=False,
                              width=4096).strip()

    aristas = []
    for e in sorted(data.get('aristas') or [], key=lambda e: (e['seudonimo'], e['otro'], e['tipo'])):
        fila = {'seudonimo': e['seudonimo'], 'otro': e['otro'], 'tipo': e['tipo']}
        for clave in ('kinds', 'docs'):
            if e.get(clave):
                fila[clave] = sorted(set(e[clave]))
        fila.update(origen=e['origen'], motivo=e['motivo'])
        aristas.append(fila)
    alias = [{'id': e['id'], 'permitidos': sorted(set(e['permitidos']))}
             for e in sorted(data.get('alias') or [], key=lambda e: e['id']) if e.get('permitidos')]
    citas = [{'seudonimo': e['seudonimo'], 'sha256': e['sha256'], 'docs': sorted(set(e['docs'])),
              'origen': e['origen'], 'motivo': e['motivo']}
             for e in sorted(data.get('citas') or [], key=lambda e: (e['seudonimo'], e['sha256']))]
    out = [CABECERA_PERMITIDOS + ('aristas:' if aristas else 'aristas: []')]
    out += [f'- {flow(fila)}' for fila in aristas]
    out += ['', 'alias:' if alias else 'alias: []']
    out += [f'- {flow(fila)}' for fila in alias]
    if citas:   # AC90: opcional; sin entradas no se escribe (la lista vigente no cambia)
        out += ['', CABECERA_CITAS + 'citas:'] + [f'- {flow(fila)}' for fila in citas]
    return '\n'.join(out) + '\n'


def _permite(entrada, evidencia) -> bool:
    return (entrada is not None
            and (entrada.get('kinds') is None or evidencia['kind'] in entrada['kinds'])
            and (entrada.get('docs') is None or evidencia['doc'] in entrada['docs']))


def resolver_arista(entrada, conservar=(), retirar=(), mover=(), otras=(), nueva=None):
    """La entrada de una tripleta (seudonimo, otro, tipo) tras una tanda de la 063, o None si ya no hace
    falta. La clave no distingue cita: aristas paralelas de la tripleta comparten entrada, y solo `docs`
    (mundo cerrado) separa lo que se conserva de lo que se retira.

    Cada evidencia es {'kind', 'doc'}. `conservar` ha de seguir permitida; `retirar` ha de desaparecer;
    `mover` deja la tripleta o la capa por un fix (retype, source_kind), así que no obliga a nada y solo
    poda lo que ya nadie necesita; `otras` son las del censo que la tanda no cubre y no cambian de suerte,
    ni la que hoy se conserva ni la que hoy se retira. Las capas solo crecen. `nueva` = la entrada a crear
    si no existe. Devuelve (entrada, None), o (la entrada sin tocar, motivo) si la lista no puede expresar
    la decisión: nunca la ignora en silencio. Los motivos no nombran extremos."""
    if any(e['kind'] == SIN_CAPA for e in conservar):
        return entrada, 'una evidencia conservada está sin capa (`source_kind`): corrígela antes de conservarla'
    vivas = [o for o in otras if _permite(entrada, o)]
    muertas = [o for o in otras if not _permite(entrada, o)]
    docs_c = {e['doc'] for e in conservar}
    if entrada is None:
        kinds = {e['kind'] for e in conservar}
    elif entrada.get('kinds') is None:
        kinds = None
    else:
        kinds = {*entrada['kinds'], *(e['kind'] for e in conservar)}
    if entrada is not None and entrada.get('docs') is not None:
        if None in docs_c:
            return entrada, 'una evidencia conservada no trae documento y la entrada está acotada por `docs`'
        # Lo movido se lleva su documento si ninguna otra evidencia conocida lo necesita.
        sobran = {e['doc'] for e in mover} - {o['doc'] for o in vivas}
        docs = ((set(entrada['docs']) - sobran) | docs_c) - {e['doc'] for e in retirar}
    elif retirar and vivas:
        return entrada, ('decisión insuficiente: excepción sin acotar por `docs` y la tripleta tiene evidencias que la '
                         'tanda no cubre; acótala con `docs` o retíralas todas')
    elif not conservar and not vivas:
        return None, None   # se retira todo lo conocido, o lo movido era lo único
    elif retirar or (entrada is None and any(e['kind'] in kinds for e in muertas)):
        # Mezcla, o una entrada nueva que permitiría otra evidencia que hoy no lo está: mundo cerrado.
        if None in docs_c:
            return entrada, 'una evidencia conservada no trae documento: no se puede acotar la entrada por `docs`'
        docs = docs_c
    else:
        docs = None
    if docs is not None and not docs and not conservar and not vivas:
        return None, None
    out = dict(entrada) if entrada is not None else dict(nueva or {})
    for clave, valor in (('kinds', kinds), ('docs', docs)):
        if valor is None:
            out.pop(clave, None)
        else:
            out[clave] = sorted(valor)
    if not all(_permite(out, e) for e in (*conservar, *vivas)) or any(_permite(out, e) for e in (*retirar, *muertas)):
        return entrada, ('conserva y retira (o volvería a permitir) evidencias de la misma capa y documento: la lista '
                         'no puede separarlas; decidir a mano')
    return out, None


def conservar_alias(data, seudonimo_id, alias) -> bool:
    for e in data['alias']:
        if e['id'] == seudonimo_id:
            if alias in e['permitidos']:
                return False
            e['permitidos'] = sorted({*e['permitidos'], alias})
            return True
    data['alias'].append({'id': seudonimo_id, 'permitidos': [alias]})
    return True


def retirar_alias(data, seudonimo_id, alias) -> bool:
    for e in list(data['alias']):
        if e['id'] == seudonimo_id and alias in e['permitidos']:
            e['permitidos'] = [a for a in e['permitidos'] if a != alias]
            if not e['permitidos']:
                data['alias'].remove(e)
            return True
    return False


def parametros_politica(ids, data) -> dict:
    """Los parámetros Cypher de la política del loader: qué aristas y qué alias se conservan."""
    aristas = [{'seudonimo': e['seudonimo'], 'otro': e['otro'], 'tipo': e['tipo'],
                'kinds': sorted(e['kinds']) if e.get('kinds') else None,
                'docs': sorted(e['docs']) if e.get('docs') else None} for e in data.get('aristas') or []]
    permitidos = indice_alias(data)
    return {'ids': sorted(ids), 'aristas': aristas,
            'alias': [{'id': sid, 'permitidos': sorted(permitidos.get(sid, ()))} for sid in sorted(ids)]}


# --- auditoría del árbol público (AC1 · L26 · CI) ------------------------------------------------

def sha16(texto) -> str:
    """Huella de un texto (sha256, 16 hex): correlaciona en un mensaje lo que no se puede nombrar. Un sustituto UTF-16 suelto (U+D800-U+DFFF, que un
    JSON con `\\ud800` sí produce) también tiene huella: AC349, un nombre con él es un problema de la puerta, no una excepción."""
    return hashlib.sha256(str(texto).encode('utf-8', 'surrogatepass')).hexdigest()[:16]


def huella_doc(doc) -> str:
    """Un documento en un mensaje: su huella, nunca el nombre (el de uno no publicado puede ser lo que se retira)."""
    doc = str(doc or '').strip()
    return f'doc {sha16(doc)}' if doc else 'sin doc'


def doc_publico(doc, publicos) -> bool:
    """¿Publica el árbol el documento `doc` (un `source_doc`)? La misma normalización para AC45 (check y gate), la
    fuente del registro de rectificaciones y la del estado de un seudónimo."""
    return str(doc or '').strip() in publicos


def documentos_regla(fila) -> list[str]:
    """El documento que nombra una regla deny/fix del overlay: su `doc`, el único campo de documento de su forma
    (AC146: una clave que el loader no lee, o una cita con forma de mapa, ya paran el árbol y el loader, AC144/AC145).
    También la procedencia de su cita (AC101)."""
    return sorted(_ids(fila.get('doc')))


def problemas_documentos(fila, publicos) -> list[str]:
    """Por qué una regla deny/fix con extremo seudónimo no puede publicarse por su documento (AC45): ha de estar en el
    árbol público (`publicos`), y una cita (`quote_contains`) exige el suyo, así que AC30 es el caso particular de la
    cita. Motivos constantes; un documento, por su huella."""
    motivos = [f'regla con extremo seudónimo que nombra un documento fuera del árbol público ({huella_doc(d)})'
               for d in documentos_regla(fila) if not doc_publico(d, publicos)]
    if fila.get('quote_contains') and not documentos_regla(fila):
        motivos.append('regla con extremo seudónimo con cita (`quote_contains`) y sin `doc`')
    return motivos


def documentos_publicos(repo=None) -> frozenset[str]:
    """Los documentos (`source_doc`) que publica el árbol: los ficheros de `data/` que sigue git, por su ruta bajo
    `data/`. Uno ignorado (los informes de sumario, ver .gitignore) puede estar en disco sin publicarse, así que si git
    no responde (exit ≠ 0, permisos, sin binario) no hay respuesta: SystemExit sin el error de git (fail-closed). Un
    export sin git pasa el conjunto a `auditar`/`gate`."""
    repo = Path(repo or REPO)
    try:
        salida = subprocess.run(['git', '-C', str(repo), 'ls-files', '-z', '--', 'data'],
                                check=True, capture_output=True).stdout.decode('utf-8', 'surrogateescape')
    except (OSError, subprocess.SubprocessError):
        raise SystemExit(f'{repo}: no se puede determinar el árbol público (git) (spec 070)') from None
    return frozenset(str(Path(p).relative_to('data')) for p in salida.split('\0') if p)


# --- AC79: el inventario de las capas públicas ---------------------------------------------------
# Cada fichero versionado bajo pipeline/data que lee un loader (o la API: el feed y los patrones), con sus secciones de
# filas. Por sección: de dónde salen las entidades de una fila (la partición civil/seudónimo) y la clase de cada campo
# de texto. Un campo de texto que no esté aquí lo denuncia el test del inventario: se clasifica antes de publicarlo.
# Clases: `prosa` (texto libre), `cita` (literal de un documento o de un medio), `url`, `doc` (nombre de un documento:
# solo cuenta junto a un seudónimo, AC78(c)), `alias` (lo juzgan AC44/AC60/AC70, no el detector de prosa), `nombre`
# (el de la propia entidad de la fila: estructura si es civil; si es un seudónimo, vocabulario cerrado, una variante
# AC44 de su nombre de pila, AC83) y `estructura` (ids, tipos, fechas, vocabulario cerrado: no se lee). `**` clasifica
# el fichero entero (el vocabulario de la ontología). La lista de permitidas (AC80) tiene su propia comprobación; el
# roster (AC68), las reglas de relations y el registro, la suya además de esta.
PROSA, CITA, URL, DOC, ALIAS, NOMBRE, ESTRUCTURA = 'prosa', 'cita', 'url', 'doc', 'alias', 'nombre', 'estructura'
# AC157: las rutas de un número (int o float) o de un booleano: en ellas solo cabe ese tipo, o nulo; en las demás,
# texto.
NUMERO, BOOLEANO = 'numero', 'booleano'
# AC181: un opcional (una lista o un mapa) que su cargador lee nulo como ausente: el inventario lo declara con esta
# clase en su propia ruta («kinds»), además de lo que lleva dentro («kinds[]»), y la forma compartida decide el resto.
NULO = 'nulo'
# El selector de una regla (`quote_contains`), el texto que el loader busca dentro de la cita de la arista: se lee como
# una cita (AC78/AC87) y se prueba como ella (AC90: por la vía (a) o con su propia entrada exacta de `citas:`).
SELECTOR = 'selector'
# AC91: el nombre con que una regla o una postura escribe uno de sus extremos: se juzga contra ESE extremo
# (`problemas_extremos`), no contra la fila.
EXTREMO = 'extremo'
# AC101: `documentos` (fila → documentos), los de la procedencia de sus citas, que `auditar` contrasta con los `docs` de
# una entrada de `citas:`. Sin él, la sección no trae ninguno (sus filas citan prensa: `outlet`, `url`).
Seccion = namedtuple('Seccion', 'entidades campos extremos documentos', defaults=(None, None))
CONTEXT_REL = 'pipeline/data/context'
_E = ESTRUCTURA


def _ids(*valores) -> set[str]:
    return {v.strip() for v in valores if isinstance(v, str) and v.strip()}


def _lista(fila, clave) -> list:
    valor = fila.get(clave) if isinstance(fila, dict) else None
    return valor if isinstance(valor, list) else []


def _id_o_nombre(explicito, tipo, nombre) -> set[str]:
    """La entidad de una referencia como la resuelven los loaders: su id explícito o, sin él, el de su tipo y nombre."""
    explicito, tipo, nombre = (str(v).strip() if isinstance(v, str) else '' for v in (explicito, tipo, nombre))
    return {explicito} if explicito else ({entity_id(tipo, nombre)} if tipo and nombre else set())


def _id_contexto(fila):
    """El entity_id de una fila de contexto como lo resuelve `context_loader`: su `entity_id:` explícito o, sin él, el
    de su tipo y nombre con `entity_id` (la función que el loader importa, vía `entity_resolution`)."""
    return str(fila.get('entity_id') or '').strip() or entidad(fila)


def _extremos_regla(fila, nombres=None) -> set[str]:
    return _ids(extremo(fila, 'source'), extremo(fila, 'target'))


def _extremos_texto_regla(fila) -> list[tuple]:
    """AC91: [(texto, entity_id resuelto, id explícito del mismo lado o None)] de los extremos que una regla escribe
    por nombre (`source`/`target`). El loader resuelve por el id cuando lo hay (`extremo`): gana el id."""
    return [(fila[lado], extremo(fila, lado), _texto(fila.get(f'{lado}_id')) or None)
            for lado in ('source', 'target') if isinstance(fila.get(lado), str) and fila[lado].strip()]


def _extremos_texto_claim(fila) -> list[tuple]:
    """AC91: lo mismo para el `person` de cada postura de una afirmación (`asserts`/`disputes`), con su `entity_id`."""
    out = []
    for clave in ('asserts', 'disputes'):
        for postura in _lista(fila, clave):
            if isinstance(postura, dict) and isinstance(postura.get('person'), str) and postura['person'].strip():
                explicito = _texto(postura.get('entity_id')) or None
                out.append((postura['person'], explicito or entity_id('Person', postura['person']), explicito))
    return out


def _ids_merge(fila, nombres=None) -> set[str]:
    """Los entity_id de un grupo de `merges` como los deriva `context_loader.load_merges`: el canónico por su tipo y su
    nombre, y cada duplicado por los suyos (uno sin tipo, o solo un nombre, es del tipo del canónico)."""
    can = fila.get('canonical') if isinstance(fila.get('canonical'), dict) else {}
    tipo = str(can.get('type') or '').strip()
    duplicados = fila.get('duplicates') if isinstance(fila.get('duplicates'), list) else []
    lados = [can, *({'type': tipo, 'name': d} if isinstance(d, str) else {**d, 'type': d.get('type') or tipo}
                    for d in duplicados if isinstance(d, (str, dict)))]
    return _ids(*(entidad(lado) for lado in lados))


def _por_nombre(*lados):
    """Las entidades de una fila cuyos extremos son nombres declarados en la sección `entities` de la capa."""
    return lambda fila, nombres: _ids(*(nombres.get(str(fila.get(lado) or '').strip()) for lado in lados))


_CITAS_CLAIM = {f'{k}[].{c}': clase for k in ('asserts', 'disputes')
                for c, clase in (('date', _E), ('entity_id', _E), ('outlet', PROSA), ('person', EXTREMO),
                                 ('quote', CITA), ('source_kind', _E))}
# AC143/AC154: lo que un `fix: {set: …}` puede reescribir (spec 061), con el tipo de cada propiedad: ni la procedencia
# documental ni claves de registro; `source_page`, sí (es el localizador de la cita). El loader
# (`relations_loader._prepare_set`) toma de aquí la lista y la regla (`motivo_set_fix`): el árbol y el loader juzgan
# igual un `set`.
SET_PROPS_FIX = {'note': 'texto', 'currency': 'texto', 'date': 'fecha', 'amount': 'numero', 'source_page': 'pagina',
                 'status': 'estado', 'source_kind': 'capa'}
# AC145: las claves de cada sección del overlay de relaciones, con la clase de su texto, se definen solo aquí: el
# inventario del árbol (`CAPAS`, AC127) y la forma que aplica el loader (`FORMAS`, sus `CLAVES_*`) salen de ellas.
_CAMPOS_DENY = {'source_id': _E, 'target_id': _E, 'source': EXTREMO, 'target': EXTREMO, 'source_type': _E,
                'target_type': _E, 'type': _E, 'kinds': NULO, 'kinds[]': _E, 'reason': PROSA,
                'quote_contains': SELECTOR, 'doc': _E}   # el documento: AC45, solo `doc` (AC146)
# AC78: de `set`, solo `status` y `source_kind` son vocabulario cerrado; AC157: `amount` y `source_page`, números.
_CAMPOS_FIX = {**_CAMPOS_DENY, 'corrected_type': _E, 'swap_direction': BOOLEANO, 'set': NULO,
               **{f'set.{p}': {'estado': _E, 'capa': _E, 'numero': NUMERO, 'pagina': NUMERO}.get(t, PROSA)
                  for p, t in SET_PROPS_FIX.items()}}
_CAMPOS_ALIAS_DENY = {'id': _E, 'name': NOMBRE, 'type': _E, 'reason': PROSA, 'drop': NULO, 'drop[]': ALIAS,
                      'keep_only': NULO, 'keep_only[]': ALIAS}
_CAMPOS_SPLIT = {'canonical.name': NOMBRE, 'canonical.type': _E, 'namesake.name': NOMBRE, 'namesake.type': _E,
                 'namesake.role': PROSA, 'by_doc': _E, 'by_quote': NULO, 'by_quote[]': CITA, 'by_quote_exclude': NULO,
                 'by_quote_exclude[]': CITA, 'move_aliases': NULO, 'move_aliases[]': ALIAS, 'drop_aliases': NULO,
                 'drop_aliases[]': ALIAS, 'reason': PROSA}
_DESTACADOS = {'organos_destacados[].ambito': _E, 'organos_destacados[].ejemplo_link': URL,
               'organos_destacados[].ejemplo_titulo': PROSA, 'organos_destacados[].fuente': PROSA,
               'organos_destacados[].note': PROSA, 'organos_destacados[].organo': _E,
               'organos_destacados[].organo_detalle': PROSA, 'organos_destacados[].rango': _E,
               'organos_destacados[].via_ute': PROSA, 'organos_destacados[].n_contratos': NUMERO,
               'organos_destacados[].total_eur': NUMERO, 'organos_destacados[].n_emergencia': NUMERO,
               'organos_destacados[].n_negociado_sp': NUMERO, 'organos_destacados[].presupuesto_total_eur': NUMERO}
_ORGANOS = Seccion(lambda f, n: _ids(f.get('id')), {'id': _E, 'name': _E, 'role': PROSA})
_SIN_ADJUDICACIONES = Seccion(lambda f, n: _ids(f.get('entity_id')), {'entity_id': _E, 'nota': PROSA})
_ENTIDADES_NOMBRE = Seccion(lambda f, n: _id_o_nombre(None, f.get('type'), f.get('name')),
                            {'name': NOMBRE, 'type': _E, 'role': PROSA})
_RELACIONES_NOMBRE = Seccion(_por_nombre('source', 'target'),
                             {'source': _E, 'target': _E, 'type': _E, 'status': _E, 'outlet': PROSA, 'date': _E,
                              'url': URL, 'quote': CITA, 'note': PROSA})

CAPAS = {
    PSEUDONYMS_REL: {'pseudonyms': Seccion(None, {'id': _E, 'name': NOMBRE, 'note': PROSA, 'curacion': PROSA})},
    PERMITIDOS_REL: {'aristas': Seccion(None, {'seudonimo': _E, 'otro': _E, 'tipo': _E, 'kinds[]': _E, 'docs[]': DOC,
                                               'origen': _E, 'motivo': _E}),
                     'alias': Seccion(None, {'id': _E, 'permitidos[]': ALIAS}),
                     'citas': Seccion(None, {'seudonimo': _E, 'sha256': _E, 'docs[]': DOC, 'origen': _E,
                                             'motivo': _E})},
    REGISTRO_REL: {'rectificaciones': Seccion(
        lambda f, n: _ids(f.get('entidad')),
        {'id': _E, 'fecha': _E, 'origen': _E, 'entidad': _E, 'unidad': _E, 'dato': PROSA, 'fuente': PROSA,
         'decision': _E, 'fecha_resolucion': _E, 'seed_version': _E})},
    f'{RELATIONS_REL}/*.y*ml': {
        # Spec 071 (AC114): una regla deny opaca nombra su clave (`unidad`) y no su par; su forma, la de `alcance`.
        'deny': Seccion(_extremos_regla, {**_CAMPOS_DENY, 'unidad': _E, 'declaraciones': NUMERO}, _extremos_texto_regla,
                        documentos_regla),
        'fix': Seccion(_extremos_regla, _CAMPOS_FIX, _extremos_texto_regla, documentos_regla),
        'alias_deny': Seccion(lambda f, n: _ids(entidad(f)), _CAMPOS_ALIAS_DENY),
        'splits': Seccion(lambda f, n: _ids(entidad(f.get('canonical') or {}), entidad(f.get('namesake') or {})),
                          _CAMPOS_SPLIT, documentos=lambda f: sorted(_ids(f.get('by_doc'))))},
    f'{CONTEXT_REL}/*.y*ml': {
        'entities': Seccion(lambda f, n: _ids(_id_contexto(f)),
                            {'name': NOMBRE, 'type': _E, 'entity_id': _E, 'role': PROSA, 'reason': PROSA,
                             'aliases': NULO, 'aliases[]': PROSA, 'sources': NULO, 'sources[]': URL}),
        'relations': Seccion(_por_nombre('source', 'target'),
                             {'source': _E, 'target': _E, 'type': _E, 'kind': _E, 'status': _E, 'note': PROSA,
                              'source_url': URL, 'source_date': _E}),
        'summaries': Seccion(lambda f, n: _ids(f.get('entity_id')),
                             {'entity_id': _E, 'name': NOMBRE, 'updated': _E, 'resumen': PROSA, 'reason': PROSA,
                              'sources': NULO, 'sources[]': URL, 'remove': BOOLEANO}),
        'revisados': Seccion(lambda f, n: _ids(f.get('entity_id')),
                             {'entity_id': _E, 'decision': _E, 'fecha': _E, 'reason': PROSA}),
        'merges': Seccion(_ids_merge, {'canonical.name': NOMBRE, 'canonical.type': _E, 'duplicates': NULO,
                                       'duplicates[]': NOMBRE, 'duplicates[].name': NOMBRE,
                                       'duplicates[].type': _E})},
    'pipeline/data/accusations/*.y*ml': {'accusations': Seccion(
        lambda f, n: _id_o_nombre(f.get('entity_id'), 'Person', f.get('person')),
        {'person': NOMBRE, 'entity_id': _E, 'doc': DOC, 'status': _E, 'estado.fecha': _E, 'estado.fuente.doc': DOC,
         'estado.fuente.tipo': _E, 'estado.fuente.url': URL, 'estado.nota': PROSA, 'estado.revisado': _E,
         'estado.status': _E, 'crimes[].article': _E, 'crimes[].doc': DOC, 'crimes[].name': _E,
         'crimes[].quote': CITA, 'crimes[].status': _E,
         'crimes[].page': NUMERO},   # spec 071: la página de la cita, que carga como `source_page`
        # AC101: las citas son las de sus delitos, de su `doc` o del de la fila (`estado.fuente.doc` es la del estado).
        documentos=lambda f: sorted(_ids(f.get('doc'), *(c.get('doc') for c in _lista(f, 'crimes')
                                                        if isinstance(c, dict)))))},
    'pipeline/data/boe/*.y*ml': {
        'organismos': Seccion(lambda f, n: _ids(f.get('id')), {'id': _E, 'name': _E, 'label': _E, 'role': PROSA}),
        'trayectorias': Seccion(
            lambda f, n: _ids(f.get('entity_id'), *(c.get('organismo') for c in _lista(f, 'cargos')
                                                    if isinstance(c, dict))),
            {'entity_id': _E, 'nombre_grafo': NOMBRE, 'boe_rango': _E, 'boe_n_cargos': NUMERO, 'nota': PROSA,
             'cargos[].boe_id_cese': _E, 'cargos[].boe_id_nombramiento': _E, 'cargos[].boe_key': _E,
             'cargos[].cargo': PROSA, 'cargos[].fecha_cese': _E, 'cargos[].fecha_nombramiento': _E,
             'cargos[].nota': PROSA, 'cargos[].organismo': _E, 'cargos[].titulo_cese': PROSA,
             'cargos[].titulo_nombramiento': PROSA, 'cargos[].url_cese': URL, 'cargos[].url_nombramiento': URL,
             'incidencias[].boe_id': _E, 'incidencias[].fecha': _E, 'incidencias[].nota': PROSA,
             'incidencias[].titulo': PROSA, 'incidencias[].url': URL})},
    'pipeline/data/claims/*.y*ml': {'claims': Seccion(
        lambda f, n: _ids(*_lista(f, 'about')).union(*(_id_o_nombre(p.get('entity_id'), 'Person', p.get('person'))
                                                       for k in ('asserts', 'disputes') for p in _lista(f, k)
                                                       if isinstance(p, dict))),
        # `asserts` vacío (spec 071: una postura retirada deja solo su comentario) lo lee `claims_loader` como ausente.
        {'claim_id': _E, 'text': PROSA, 'topic': PROSA, 'about[]': _E, 'asserts': NULO, **_CITAS_CLAIM},
        _extremos_texto_claim)},
    'pipeline/data/contracts/*.y*ml': {'contracts': Seccion(
        lambda f, n: (_id_o_nombre(f.get('body_id'), f.get('body_type') or 'Organization', f.get('awarding_body'))
                      | _id_o_nombre(f.get('awardee_id'), f.get('awardee_type') or 'Company', f.get('awardee'))),
        {'contract': _E, 'awarding_body': _E, 'body_type': _E, 'awardee': _E, 'awardee_type': _E, 'currency': _E,
         'date': _E, 'doc': DOC, 'object': PROSA, 'quote': CITA, 'note': PROSA, 'amount': NUMERO, 'page': NUMERO},
        documentos=lambda f: sorted(_ids(f.get('doc'))))},
    'pipeline/data/contratacion/ampliada/*.y*ml': {
        'ampliada_organos': _ORGANOS,
        'ampliada_adjudicatarias': Seccion(
            lambda f, n: _ids(f.get('adjudicataria'), *(o.get('organo') for o in _lista(f, 'organos_destacados')
                                                        if isinstance(o, dict))),
            {'adjudicataria': _E, 'nif': _E, 'denominacion': _E, 'nota': PROSA, 'ambitos.*.nota': PROSA,
             'ambitos.*.rango': _E, 'ambitos.*.n': NUMERO, 'ambitos.*.total_eur': NUMERO, **_DESTACADOS}),
        'ampliada_sin_adjudicaciones': _SIN_ADJUDICACIONES},
    'pipeline/data/contratacion/*.y*ml': {
        'organos': _ORGANOS,
        'adjudicatarias': Seccion(
            lambda f, n: _ids(f.get('adjudicataria'), *(o.get('organo') for o in _lista(f, 'organos_destacados')
                                                        if isinstance(o, dict))),
            {'adjudicataria': _E, 'nif': _E, 'denominacion_placsp': _E, 'rango': _E, 'organos_top': PROSA,
             'nota': PROSA, 'n_contratos': NUMERO, 'total_eur': NUMERO, **_DESTACADOS}),
        'sin_menores': _SIN_ADJUDICACIONES},
    'pipeline/data/documents/*.y*ml': {'documents': Seccion(
        lambda f, n: set(), {'doc_id': _E, 'doc_type': _E, 'court': PROSA, 'date': _E, 'sentencia_firme': BOOLEANO,
                             'curated_edges': BOOLEANO})},
    'pipeline/data/mercantil/*.y*ml': {
        'companies': Seccion(lambda f, n: _ids(f.get('entity_id')),
                             {'entity_id': _E, 'type': _E, 'hoja': _E, 'provincia': _E, 'fecha_constitucion': _E,
                              'domicilio': PROSA, 'borme_id': _E, 'liquidacion_fecha': _E, 'objeto_social': PROSA,
                              'concurso_fecha': _E, 'concurso_juzgado': PROSA, 'nif': _E, 'cnae': _E,
                              'disuelta_fecha': _E, 'capital_historico': PROSA, 'capital_social': NUMERO,
                              'en_liquidacion': BOOLEANO, 'en_concurso': BOOLEANO, 'disuelta': BOOLEANO}),
        'network': Seccion(
            lambda f, n: _ids(f.get('anchor')).union(*(
                _id_o_nombre((f.get(lado) or {}).get('id'), (f.get(lado) or {}).get('type') or 'Company',
                             (f.get(lado) or {}).get('name'))
                for lado in ('source', 'target') if isinstance(f.get(lado), dict))),
            {'anchor': _E, 'rel': _E, 'edge.note': PROSA, 'edge.rol': PROSA, 'edge.desde': _E, 'edge.borme_id': _E,
             **{f'{lado}.{c}': _E for lado in ('source', 'target')
                for c in ('id', 'name', 'type', 'provincia', 'hoja', 'fecha_constitucion')},
             **{f'{lado}.{c}': clase for lado in ('source', 'target')
                for c, clase in (('exists', BOOLEANO), ('capital_social', NUMERO))},
             'source.domicilio': PROSA, 'target.domicilio': PROSA})},
    'pipeline/data/patrones/*.y*ml': {'patrones': Seccion(
        lambda f, n: _ids(*_lista(f, 'entities')),
        {'slug': _E, 'titulo': PROSA, 'tesis': PROSA, 'tipos[]': _E, 'estado': _E, 'estado_fecha': _E,
         'descubierto': _E, 'contratos_tope': _E, 'entities[]': _E, 'actores_sin_grafo[]': PROSA,
         'hitos[].fecha': _E, 'hitos[].fuentes[]': _E, 'hitos[].hecho': PROSA, 'fuentes[].id': _E,
         'fuentes[].etiqueta': PROSA, 'fuentes[].acredita': PROSA, 'fuentes[].doc': DOC, 'fuentes[].url': URL,
         'fuentes[].pagina': NUMERO, 'disclaimer': PROSA})},
    'pipeline/data/press/*.y*ml': {'entities': _ENTIDADES_NOMBRE, 'relations': _RELACIONES_NOMBRE},
    'pipeline/data/subvenciones/*.y*ml': {
        'convocantes': _ORGANOS,
        'subvenciones': Seccion(
            lambda f, n: _ids(f.get('beneficiario'), *(c.get('convocante') for c in _lista(f, 'concesiones')
                                                       if isinstance(c, dict))),
            {'beneficiario': _E, 'nif': _E, 'denominacion_bdns': _E, 'nota': PROSA, 'tercero_id': NUMERO,
             'concesiones[].importe': NUMERO, 'concesiones[].en_flujos': BOOLEANO,
             'concesiones[].bdns_endpoint': _E, 'concesiones[].convocante': _E,
             'concesiones[].convocante_detalle': PROSA, 'concesiones[].fecha': _E, 'concesiones[].instrumento': _E,
             'concesiones[].note': PROSA})},
    'pipeline/data/ultima_hora/feed.yaml': {'items': Seccion(
        lambda f, n: _ids(*_lista(f, 'entities'), f.get('focus')),
        {'iso': _E, 'date': _E, 'headline': PROSA, 'dek': PROSA, 'outlet': PROSA, 'url': URL, 'status': _E,
         'case_tag': PROSA, 'entities[]': _E, 'focus': _E, 'briefing_slug': _E, 'quote': CITA})},
    'pipeline/data/ultima_hora/*.y*ml': {'entities': _ENTIDADES_NOMBRE, 'relations': _RELACIONES_NOMBRE},
    'pipeline/data/ontologia/*.y*ml': {None: Seccion(lambda f, n: set(), {
        '**': PROSA, 'version': NUMERO, 'capas.*.rango': NUMERO, 'estados_procesales.*.orden': NUMERO,
        'estados_procesales.*.admite_prensa': BOOLEANO, 'tipos_entidad.*.extraible': BOOLEANO,
        **{f'tipos_relacion.*.{b}': BOOLEANO for b in ('simetrica', 'solo_loader', 'lleva_estado')}})},
    'pipeline/data/pii_allowlist.y*ml': {'allow': Seccion(lambda f, n: set(),
                                                          {'match': PROSA, 'where': _E, 'why': PROSA})},
}


def capa_de(rel):
    """La entrada de `CAPAS` de un fichero (ruta relativa al repo), o None si el inventario no lo clasifica."""
    return next((secciones for patron, secciones in CAPAS.items() if fnmatch.fnmatchcase(rel, patron)), None)


def _rutas(valor, ruta=''):
    """(ruta, cadena) de cada cadena de un valor: un elemento de lista, `[]`; una clave de mapa, `.clave`."""
    if isinstance(valor, str):
        yield ruta, valor
    elif isinstance(valor, dict):
        for clave, v in valor.items():
            yield from _rutas(v, f'{ruta}.{clave}' if ruta else str(clave))
    elif isinstance(valor, list):
        for v in valor:
            yield from _rutas(v, f'{ruta}[]')


class _ElementoDeLista:
    """AC161: el segmento de una ruta (`_hojas`, `_patrones`) que es un elemento de lista: un valor que ninguna clave
    YAML puede producir. La clave literal «[]» es una clave con separadores reservados: fuera del inventario."""
    __slots__ = ()

    def __repr__(self):
        return '[]'


_ELEMENTO = _ElementoDeLista()


def _hojas(valor, ruta=()):
    """AC127/AC142: (ruta, valor) de cada hoja de un valor, sea del tipo que sea (texto, número, booleano o nulo), y de
    cada contenedor vacío. La ruta, en segmentos (una tupla): cada clave tal cual y `_ELEMENTO` por un elemento de
    lista, sin unirlos: la clave literal «set.note» no es la ruta `set` → `note`."""
    if isinstance(valor, dict) and valor:
        for clave, v in valor.items():
            yield from _hojas(v, (*ruta, clave))
    elif isinstance(valor, list) and valor:
        for v in valor:
            yield from _hojas(v, (*ruta, _ELEMENTO))
    else:
        yield ruta, valor


def _nodos(valor, ruta=()):
    """AC161: (ruta, valor) de cada nodo de un valor, también de cada contenedor, con las rutas de `_hojas`."""
    yield ruta, valor
    if isinstance(valor, dict):
        for clave, v in valor.items():
            yield from _nodos(v, (*ruta, clave))
    elif isinstance(valor, list):
        for v in valor:
            yield from _nodos(v, (*ruta, _ELEMENTO))


def _unida(ruta) -> str:
    """Una ruta en segmentos (`_hojas`) escrita como las del inventario («cargos[].cargo»), para su huella; la clave
    literal «[]», como una clave más («aliases.[]»)."""
    out = ''
    for segmento in ruta:
        out += '[]' if segmento is _ELEMENTO else (f'.{segmento}' if out else str(segmento))
    return out


@functools.lru_cache(maxsize=256)
def _patrones(campos) -> tuple:
    """AC142: los patrones del inventario de una sección (`campos`, sus claves) en segmentos: «cargos[].cargo» es
    ('cargos', `_ELEMENTO`, 'cargo'); `*` vale por una clave."""
    out = []
    for patron in campos:
        segmentos = []
        for parte in patron.split('.'):
            nombre, marcas = re.fullmatch(r'(.*?)((?:\[\])*)', parte).groups()
            segmentos += [nombre] * bool(nombre) + [_ELEMENTO] * (len(marcas) // 2)
        out.append(tuple(segmentos))
    return tuple(out)


_RESERVADOS = re.compile(r'[.\[\]]')


def _casa(segmento, patron) -> bool:
    """AC142: ¿casa un segmento de una ruta (`_hojas`) con uno de un patrón (`_patrones`)? Un elemento de lista, solo
    con un elemento de lista (AC161). Una clave ha de ser texto sin los separadores reservados (`.`, `[`, `]`): con
    ellos no casa con nada, ni con `*` («[]» es una clave así)."""
    if segmento is _ELEMENTO or patron is _ELEMENTO:
        return segmento is patron
    if not isinstance(segmento, str) or _RESERVADOS.search(segmento):
        return False
    return patron == '*' or segmento == patron


def _fuera_del_inventario(campos, valor) -> list[str]:
    """AC127/AC142: las rutas de las hojas de un valor (`_hojas`) que el inventario de su sección no clasifica, casadas
    por segmentos, sin repetir (unidas, `_unida`); un contenedor vacío, si el inventario no conoce lo que llevaría
    dentro; una clave con un separador reservado o que no es texto, siempre (también con `**`)."""
    patrones, todo = _patrones(tuple(campos)), '**' in campos

    def conocida(ruta, hoja):
        if not all(_casa(s, s if s is _ELEMENTO else '*') for s in ruta):
            return False
        if todo or any(len(p) == len(ruta) and all(map(_casa, ruta, p)) for p in patrones):
            return True
        if not isinstance(hoja, (list, dict)):
            return False
        sonda = (*ruta, _ELEMENTO) if isinstance(hoja, list) else ruta
        return any(len(p) >= len(sonda) and all(map(_casa, sonda, p)) for p in patrones)
    return list(dict.fromkeys(_unida(ruta) for ruta, hoja in _hojas(valor) if not conocida(ruta, hoja)))


@functools.lru_cache(maxsize=256)
def _listas(campos) -> tuple:
    """AC161: las rutas donde el inventario de una sección (`campos`, sus claves) declara una lista: los prefijos de
    sus patrones (`_patrones`) que siguen un elemento de lista («aliases[]» → ('aliases',))."""
    return tuple(dict.fromkeys(p[:j] for p in _patrones(campos) for j, s in enumerate(p) if s is _ELEMENTO))


def problemas_listas(campos, valor) -> list[tuple[str, str]]:
    """AC161: [(ruta, motivo)] de los campos de un valor donde el inventario de su sección declara una lista (`_listas`)
    y hay otra cosa que no es nula —un mapa (también `{'[]': …}`), un texto, un número…—, con la ruta unida (`_unida`)
    y sin el valor: leída a la ligera, de un mapa saldrían sus claves y de un texto, sus letras."""
    listas = _listas(tuple(campos))
    return [(_unida(ruta), 'un campo que no es una lista') for ruta, nodo in _nodos(valor)
            if nodo is not None and not isinstance(nodo, list)
            and any(len(p) == len(ruta) and all(map(_casa, ruta, p)) for p in listas)]


# AC184: los publicadores oficiales de sentencias, la fuente que ha de citar un estado procesal que exige la sentencia
# (`exige_fuente: sentencia`); el loader de contexto la lee de aquí.
DOMINIOS_SENTENCIA = frozenset({'www.boe.es', 'boe.es', 'www.tribunalconstitucional.es', 'hj.tribunalconstitucional.es',
                                'www.poderjudicial.es'})


def _texto_recortado(valor) -> str:
    return valor.strip() if isinstance(valor, str) else ''


# AC187/AC192: los motivos fijos de lo que el loader de contexto no carga de una fila, sin valores; los usan el árbol,
# su `load_files`, `_prepare` y `qa_lint` (L9).
URL_INVALIDA = 'una fuente con una URL inválida (spec 069)'
EXTREMO_SIN_RESOLVER = 'una relación con un extremo que no es una entidad del contexto (spec 061)'
SIN_FUENTE_HTTPS = 'una relación sin fuente https: no se carga (spec 061)'


def _de_la_ontologia(valor, tipos) -> bool:
    return isinstance(valor, str) and valor in tipos


def forma_contexto(seccion, fila) -> str | None:
    """AC184 (y la retirada con resumen de la enmienda 37): por qué el loader de contexto abortaría con la fila `fila`
    de la sección `seccion` (`context_loader._prepare` y `_split_summaries`), o None, sin valores; el loader lo
    descubre con los nodos ya escritos o a punto de escribirse, y el árbol y su `load_files` lo paran antes del driver.
    Con las condiciones del loader: un delito (`type: Crime`, con nombre) sin forma canónica en la ontología (spec
    069, con su `article`); una relación de un tipo de la ontología y con una fuente https (las demás no las carga)
    con un estado procesal fuera de la ontología, o con uno que exige la sentencia (`exige_fuente: sentencia`) y una
    fuente que no es de un publicador oficial (`DOMINIOS_SENTENCIA`; AC187: o una URL que el analizador no lee, con un
    motivo fijo: su error lleva la URL); una retirada (`remove: true`) de un resumen con `entity_id` y un `resumen` que
    no está vacío. AC192: y lo que el loader no cargaría de la fila, que no salta nunca en silencio: una entidad sin un
    tipo de la ontología o sin nombre, o con un alias vacío; una relación de un tipo fuera de la ontología; una fusión
    sin un canónico de un tipo de la ontología y con nombre, sin duplicados o con uno sin nombre o de un tipo fuera de
    ella; un resumen sin `entity_id`, sin `resumen` ni retirada o con una fuente vacía. Si los extremos de una relación
    resuelven y si tiene fuente https lo deciden todos los ficheros juntos (`problemas_contexto`)."""
    tipos = ontologia().tipos_entidad
    if seccion == 'entities':
        nombre = _texto_recortado(fila.get('name'))
        if not _de_la_ontologia(fila.get('type'), tipos) or not nombre:
            return 'una entidad sin un tipo de la ontología o sin nombre (spec 061)'
        if any(isinstance(a, str) and not a.strip() for a in _lista(fila, 'aliases')):
            return 'un alias vacío'
        if fila.get('type') == 'Crime' \
                and not ontologia().validar('delitos', nombre, articulo=fila.get('article')).ok:
            return 'un delito sin forma canónica en la ontología (spec 069)'
    elif seccion == 'relations':
        if not _de_la_ontologia(fila.get('type'), ontologia().tipos_relacion):
            return 'una relación de un tipo fuera de la ontología (spec 069)'
        fuente = _texto_recortado(fila.get('source_url'))
        if fila.get('status') is None or not fuente.startswith('https://'):
            return None
        estado = ontologia().validar('estados_procesales', fila['status'])
        if not estado.ok:
            return 'un estado procesal fuera de la ontología (spec 069)'
        if ontologia().fuente_exigida[estado.valor] != 'sentencia':
            return None
        try:
            servidor = urllib.parse.urlparse(fuente).hostname
        except ValueError:   # AC187: su mensaje lleva la URL
            return URL_INVALIDA
        if servidor not in DOMINIOS_SENTENCIA:
            return 'un estado que exige la sentencia con una fuente que no es de un publicador oficial (spec 069)'
    elif seccion == 'merges':
        canonico = fila.get('canonical')
        if not isinstance(canonico, dict) or not _de_la_ontologia(canonico.get('type'), tipos) \
                or not _texto_recortado(canonico.get('name')):
            return 'una fusión sin un canónico de un tipo de la ontología y con nombre (spec 061)'
        duplicados = _lista(fila, 'duplicates')
        if not duplicados:
            return 'una fusión sin duplicados (spec 061)'
        for d in duplicados:
            nombre = d if isinstance(d, str) else d.get('name') if isinstance(d, dict) else None
            tipo = d.get('type') if isinstance(d, dict) else None
            if not _texto_recortado(nombre) or (tipo is not None and not _de_la_ontologia(tipo, tipos)):
                return 'un duplicado sin nombre o de un tipo fuera de la ontología (spec 061)'
    elif seccion == 'summaries':
        if not _texto_recortado(fila.get('entity_id')):
            return 'un resumen sin `entity_id` (spec 061)'
        if fila.get('remove') is True and _texto_recortado(fila.get('resumen')):
            return 'una retirada (`remove`) con `resumen`'
        if not fila.get('remove') and not _texto_recortado(fila.get('resumen')):
            return 'un resumen sin `resumen` ni retirada (spec 061)'
        if any(isinstance(u, str) and not u.strip() for u in _lista(fila, 'sources')):
            return 'una fuente vacía'
    return None


def problemas_contexto(documentos) -> tuple[list[str], list[str]]:
    """AC192: con todos los ficheros de contexto juntos (`documentos`: [(ruta, datos)] en el orden de su loader, sin el
    roster), lo que el loader no carga de una relación, sin valores y con su posición: (problemas, avisos). Un extremo
    que no es el nombre de una entidad de ningún fichero (el loader no la resuelve), un problema; sin fuente https, un
    aviso: por diseño no se carga (fail-closed de la 061), y no es silencioso. Una relación de un tipo fuera de la
    ontología ya es un problema de su forma (`forma_contexto`)."""
    nombres = {_texto_recortado(f.get('name')) for _, data in documentos for f in _lista(data, 'entities')
               if isinstance(f, dict)} - {''}
    problemas, avisos = [], []
    for rel, data in documentos:
        for i, fila in enumerate(_lista(data, 'relations')):
            if not isinstance(fila, dict) or not _de_la_ontologia(fila.get('type'), ontologia().tipos_relacion):
                continue
            if not {_texto_recortado(fila.get('source')), _texto_recortado(fila.get('target'))} <= nombres:
                problemas.append(f'{rel}: `relations`[{i}]: {EXTREMO_SIN_RESOLVER}')
            elif not _texto_recortado(fila.get('source_url')).startswith('https://'):
                avisos.append(f'{rel}: `relations`[{i}]: {SIN_FUENTE_HTTPS}')
    return problemas, avisos


def documentos_contexto(repo=None) -> list[tuple[str, dict]]:
    """AC192: [(ruta relativa, datos)] de los ficheros de contexto del árbol en el orden de su loader, sin el roster;
    cada uno con su forma (`_documento`)."""
    repo = Path(repo or REPO)
    contexto = repo / CONTEXT_REL
    return [(str(path.relative_to(repo)), _documento(path)[0])
            for path in (ficheros_overlay(contexto) if contexto.is_dir() else []) if path != repo / PSEUDONYMS_REL]


def problemas_contenedores(campos, valor) -> list[tuple[str, str]]:
    """AC180: [(ruta, motivo)] de los contenedores de un valor (una lista o un mapa, vacíos o no) en una ruta que el
    inventario de su sección declara escalar (un patrón suyo que no es `**` ni `NULO`; también el elemento de una lista
    de escalares, «aliases[]») sin declarar lo que lleva dentro (una lista, un patrón que sigue con `[]`; un mapa, uno
    que sigue con una clave): «un campo que no es un texto» (o un número, o un booleano, según su clase), con la ruta
    unida y sin el valor. Leído a la ligera, el cargador descartaría sus elementos («aliases: [[], {}]»), tomaría una
    rama («remove: {}») o escribiría un mapa en el grafo («note: {}»)."""
    patrones = [(p, clase) for p, clase in zip(_patrones(tuple(campos)), campos.values()) if p != ('**',)]
    out = []
    for ruta, nodo in _nodos(valor):
        if not ruta or not isinstance(nodo, (list, dict)):
            continue
        clase = next((c for p, c in patrones if len(p) == len(ruta) and all(map(_casa, ruta, p))), None)
        dentro = any(len(p) > len(ruta) and all(map(_casa, ruta, p))
                     and (p[len(ruta)] is _ELEMENTO) == isinstance(nodo, list) for p, _ in patrones)
        if clase not in (None, NULO) and not dentro:
            out.append((_unida(ruta), _NO_ES[clase if clase in (NUMERO, BOOLEANO) else None]))
    return list(dict.fromkeys(out))


# AC154: la fecha de una corrección, la que el loader normaliza siempre (`cleanup._normalize_date`, sin `dateparser`,
# que el árbol no carga): ISO, un año suelto o un texto con un año de cuatro cifras (su último recurso). Una sin año,
# dateparser la completaría con un año que el texto no dice.
_FECHA_LEIBLE = re.compile(r'\d{4}-\d{2}-\d{2}|\d{4}')
_ANIO_LEIBLE = re.compile(r'(19|20)\d{2}')


def fecha_normalizable(valor) -> bool:
    return isinstance(valor, str) and bool(_FECHA_LEIBLE.fullmatch(valor.strip()) or _ANIO_LEIBLE.search(valor))


# AC154: el tipo de las propiedades de `SET_PROPS_FIX` que no tienen su propio motivo (capa, estado, página).
_DEL_TIPO = {'texto': lambda v: isinstance(v, str), 'fecha': fecha_normalizable,
             'numero': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)}


def cabe_en_el_grafo(valor) -> bool:
    """AC163: ¿puede guardar Bolt este número? Un entero, de 64 bits con signo; un flotante, finito (YAML lee `.inf` y
    `.nan`, y un entero de cualquier tamaño): fuera de eso, el codificador falla con el driver ya abierto y la carga
    a medias. Lo que no es un número, sí (su tipo lo juzga otra regla)."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return True
    return math.isfinite(valor) if isinstance(valor, float) else -2 ** 63 <= valor < 2 ** 63


def motivo_set_fix(props) -> str | None:
    """AC143/AC144/AC154: por qué el loader no aplicaría el `set:` de una corrección, sin su valor, o None: no es un
    mapa con propiedades, lleva una fuera de `SET_PROPS_FIX` o una que no es de su tipo. `source_kind`, una capa que
    degrada una arista judicial (una regla solo toca aristas extraídas: fuera de las capas de la ontología hasta
    `contexto`, la judicial o ninguna, sería fabricar respaldo); `status`, uno del vocabulario de estados procesales;
    `source_page`, un entero ≥ 1; `date`, un texto que el loader normaliza (`fecha_normalizable`); `amount`, un número;
    `note` y `currency`, texto. Un nulo borra la propiedad, salvo en `source_kind`, `status` y `source_page`: quitar
    la capa, el estado o la página (el `[doc, p.N]` de la cita, enmienda 89) no es una corrección que se pueda escribir
    así. AC163: un número, uno que el grafo guarda
    (`cabe_en_el_grafo`): el árbol y el loader paran antes de abrir el driver."""
    if not isinstance(props, dict) or not props:
        return 'corrección con un `set` que no es un mapa con propiedades'
    if set(props) - SET_PROPS_FIX.keys():
        return 'corrección con un `set` con una propiedad fuera de su lista'
    rango, capa = ontologia().rango_capa, props.get('source_kind')
    if 'source_kind' in props and not (isinstance(capa, str) and capa in rango
                                       and rango['judicial'] < rango[capa] <= rango['contexto']):
        return 'corrección con un `set` cuya capa no degrada una arista judicial'
    if 'status' in props and not ontologia().validar('estados_procesales', props['status']).ok:
        return 'corrección con un `set` cuyo estado no es de la ontología'
    pagina = props.get('source_page')
    if 'source_page' in props and (isinstance(pagina, bool) or not isinstance(pagina, int) or pagina < 1):
        return 'corrección con un `set` cuya página no es un entero ≥ 1'
    if any(valor is not None and SET_PROPS_FIX[prop] in _DEL_TIPO and not _DEL_TIPO[SET_PROPS_FIX[prop]](valor)
           for prop, valor in props.items()):
        return 'corrección con un `set` cuya propiedad no es del tipo admitido'
    if not all(map(cabe_en_el_grafo, props.values())):
        return 'corrección con un `set` con un número que el grafo no guarda'
    return None


# --- la forma que el loader de relaciones aplica (AC144) ---------------------------------------------------------
# El loader (`relations_loader._prepare_*`) y el árbol (`problemas_forma`) juzgan cada fila del overlay con la misma
# función por sección (`FORMAS`): la que el loader no aplicaría entera no se salta con un aviso, le para (exit 2), y el
# árbol para antes; un salto silencioso es una retirada o una corrección que no ocurre mientras el CI da verde. Cada
# función da el primer motivo, nunca un valor (el log del CI es público); la de `deny` tiene el orden y los motivos de
# la del 071 (`alcance.forma_deny`, AC159), con su forma opaca. Los tipos de procedencia nunca se reescriben
# ni se borran como una arista entre entidades.
PROCEDENCIA = ('MENTIONED_IN', 'PART_OF')


def _claves(campos, lado=None) -> frozenset:
    """AC145: las claves de una fila de una sección del inventario (las de primer nivel o, con `lado`, las del mapa de
    esa clave), sin `[]`: la lista cerrada que admite su forma."""
    rutas = [ruta.split('.', 1) for ruta in campos]
    if lado is None:
        return frozenset(ruta[0].removesuffix('[]') for ruta in rutas)
    return frozenset(ruta[1].split('.')[0].removesuffix('[]') for ruta in rutas if len(ruta) == 2 and ruta[0] == lado)


CLAVES_DENY, CLAVES_FIX = _claves(_CAMPOS_DENY), _claves(_CAMPOS_FIX)
CLAVES_ALIAS_DENY, CLAVES_SPLIT = _claves(_CAMPOS_ALIAS_DENY), _claves(_CAMPOS_SPLIT)
_TEXTOS_FIX = CLAVES_FIX - {'kinds', 'swap_direction', 'set'}
# Enmienda 34: la forma de un id que acuña el registro de entidades (`entity_ids.entity_id`): un tipo de entidad de la
# ontología y su slug (minúsculas sin tildes, cifras y guiones, sin guion inicial). Otro no casa ningún nodo.
_SLUG = re.compile(r'[a-z0-9][a-z0-9-]*')


def es_id_de_entidad(eid) -> bool:
    tipo, dos_puntos, slug = str(eid).partition(':')
    return bool(dos_puntos) and tipo in ontologia().tipos_entidad and bool(_SLUG.fullmatch(slug))


def rango_capas_regla() -> dict:
    """Las capas que una regla deny/fix puede tocar con `kinds`, con su rango (menor, más autoritativa): las de la
    ontología hasta `contexto`; las de los registros oficiales (mercantil, subvención, contratación, BOE) las recrea su
    propio loader y una regla no las toca."""
    rango = ontologia().rango_capa
    return {capa: r for capa, r in rango.items() if r <= rango['contexto']}


def textos_de_lista(valor) -> list[str]:
    """Los elementos no vacíos de una lista de una regla (`drop`, `keep_only`, `by_quote`…), como texto y recortados."""
    return [str(v).strip() for v in (valor or []) if str(v).strip()]


def _no_texto(fila, claves) -> bool:
    return any(fila.get(k) is not None and not isinstance(fila[k], str) for k in claves)


def _no_lista(fila, claves) -> bool:
    return any(fila.get(k) is not None and not isinstance(fila[k], list) for k in claves)


def _no_texto_en_lista(fila, claves) -> bool:
    """AC157: ¿lleva una de las listas `claves` de la fila un elemento que no es un texto (el loader lo leería con
    `str`: una fecha YAML o un número pasarían por un alias o una cita)?"""
    return any(not isinstance(v, str) for k in claves if isinstance(fila.get(k), list) for v in fila[k])


def _tipo_de_regla(tipo) -> bool:
    """¿Es `tipo`, recortado, uno que el loader aplica: de la ontología (spec 069) y no de procedencia?"""
    tipo = (tipo or '').strip()
    return tipo in ontologia().tipos_relacion and tipo not in PROCEDENCIA


def _capas_de_regla(kinds) -> bool:
    """¿Son los `kinds` de una regla, recortados, capas que puede tocar? Sin ellos (ausentes o nulos), solo las aristas
    extraídas. AC165: cualquier otro valor ha de ser una lista no vacía de capas conocidas: `false`, `0`, `''` o `{}`
    no son «sin kinds», y `[]` (ninguna capa) dejaría la regla sin efecto."""
    return kinds is None or (isinstance(kinds, list) and bool(kinds)
                             and all(isinstance(k, str) and k.strip() in rango_capas_regla() for k in kinds))


def _extremos_resueltos(fila) -> bool:
    """¿Resuelven los dos extremos (`extremo`, con los tipos de entidad de la ontología) a un id con la forma de los del
    registro (`es_id_de_entidad`)?"""
    tipos = ontologia().tipos_entidad
    return all((eid := extremo(fila, lado, tipos)) and es_id_de_entidad(eid) for lado in ('source', 'target'))


def forma_deny(fila) -> str | None:
    """AC144: por qué el loader no aplicaría la regla deny `fila`, o None (la forma explícita del 071, AC159): un mapa
    con claves de una regla deny y campos de texto, un tipo de relación que el loader aplica, los dos extremos resueltos
    (`_extremos_resueltos`) y capas que una regla puede tocar."""
    if not isinstance(fila, dict):
        return 'no es un mapa'
    if alcance.es_opaca(fila):   # la forma opaca EXACTA del 071 (AC114, AC159): la de `alcance.forma_deny`
        return alcance.forma_deny(fila)
    if set(fila) - CLAVES_DENY:
        return 'una clave que no es de una regla deny'
    if _no_texto(fila, CLAVES_DENY - {'kinds'}):
        return 'un campo que no es un texto'
    if not _tipo_de_regla(fila.get('type')):
        return 'sin un tipo de relación válido'
    if not _extremos_resueltos(fila):
        return 'un extremo sin resolver'
    if not _capas_de_regla(fila.get('kinds')):
        return 'capas desconocidas'
    return None


def forma_fix(fila) -> str | None:
    """AC143-AC145: por qué el loader no aplicaría entera la corrección `fila`, o None. Como una regla deny, con las
    claves de una regla fix, su `corrected_type` y su `set` (`motivo_set_fix`). Un `set` de capa baja por debajo de
    todas sus `kinds`, que la regla también casa (prensa → acusación sería fabricar respaldo), y no va con el swap (el
    SET va entre el retype y el swap, que se quedaría sin arista que casar). Y la regla hace algo: retipa a otro tipo,
    invierte (AC155: `swap_direction`, un booleano YAML; la cadena «False» leída como texto era verdadera y armaba el
    swap en reglas de solo retype, cazado el 2026-07-09) o escribe un `set`; si no, es una regla fix sin efecto."""
    if not isinstance(fila, dict):
        return 'no es un mapa'
    if set(fila) - CLAVES_FIX:
        return 'una clave que no es de una regla fix'
    if _no_texto(fila, _TEXTOS_FIX):
        return 'un campo que no es un texto'
    if fila.get('swap_direction') is not None and not isinstance(fila['swap_direction'], bool):
        return 'una corrección cuyo `swap_direction` no es un booleano'
    if not _tipo_de_regla(fila.get('type')):
        return 'sin un tipo de relación válido'
    corregido = (fila.get('corrected_type') or '').strip()
    if corregido and not _tipo_de_regla(corregido):
        return 'sin un tipo corregido válido'
    if not _extremos_resueltos(fila):
        return 'un extremo sin resolver'
    kinds, conjunto, swap = fila.get('kinds'), fila.get('set'), fila.get('swap_direction') is True
    if not _capas_de_regla(kinds):
        return 'capas desconocidas'
    motivo = None if conjunto is None else motivo_set_fix(conjunto)
    if motivo:
        return motivo
    capa, rango = (conjunto or {}).get('source_kind'), ontologia().rango_capa
    if capa and kinds and rango[capa] <= max(rango[k.strip()] for k in kinds):
        return 'corrección con un `set` cuya capa no baja de la de sus `kinds`'
    if capa and swap:
        return 'corrección con un `set` de capa y un swap (van en dos reglas)'
    if corregido in ('', fila['type'].strip()) and not swap and conjunto is None:
        return 'regla fix sin efecto'
    return None


def forma_alias_deny(fila) -> str | None:
    """AC144/AC145: por qué el loader no aplicaría la regla alias_deny `fila`, o None: un mapa con las claves de una
    regla alias_deny, su entidad resuelta (`entidad`: su `id`, o su `type` de la ontología y su `name`) a un id con la
    forma de los del registro (`es_id_de_entidad`: así, `alias_deny_node_missing` del loader es solo el de un id que el
    grafo no tiene) y exactamente un modo, `keep_only` o `drop`, una lista con algún alias."""
    if not isinstance(fila, dict):
        return 'no es un mapa'
    if set(fila) - CLAVES_ALIAS_DENY:
        return 'una clave que no es de una regla alias_deny'
    if _no_texto(fila, CLAVES_ALIAS_DENY - {'keep_only', 'drop'}):
        return 'un campo que no es un texto'
    if _no_lista(fila, ('keep_only', 'drop')):
        return 'un campo que no es una lista'
    if _no_texto_en_lista(fila, ('keep_only', 'drop')):
        return 'un campo que no es un texto'
    eid = entidad(fila, ontologia().tipos_entidad)
    if not eid or not es_id_de_entidad(eid):
        return 'una entidad sin resolver'
    if bool(textos_de_lista(fila.get('keep_only'))) == bool(textos_de_lista(fila.get('drop'))):
        return 'sin un único modo (keep_only o drop)'
    return None


def forma_split(fila) -> str | None:
    """AC144/AC145: por qué el loader no aplicaría el split `fila`, o None: un mapa con las claves de un split (también
    las de cada lado), sus dos lados, `canonical` y `namesake`, cada uno un mapa con su `type` de la ontología, tal cual
    (el loader no lo recorta), y su `name`; y un criterio, `by_doc` o alguna cita en `by_quote`."""
    if not isinstance(fila, dict):
        return 'no es un mapa'
    lados = (fila.get('canonical'), fila.get('namesake'))
    if set(fila) - CLAVES_SPLIT or any(isinstance(lado, dict) and set(lado) - _claves(_CAMPOS_SPLIT, nombre)
                                       for nombre, lado in zip(('canonical', 'namesake'), lados)):
        return 'una clave que no es de una regla split'
    if _no_texto(fila, ('by_doc', 'reason')) or any(isinstance(lado, dict) and _no_texto(lado, ('type', 'name', 'role'))
                                                     for lado in lados):
        return 'un campo que no es un texto'
    if _no_lista(fila, ('by_quote', 'by_quote_exclude', 'move_aliases', 'drop_aliases')):
        return 'un campo que no es una lista'
    if _no_texto_en_lista(fila, ('by_quote', 'by_quote_exclude', 'move_aliases', 'drop_aliases')):
        return 'un campo que no es un texto'
    if not all(isinstance(lado, dict) and lado.get('type') in ontologia().tipos_entidad
               and (lado.get('name') or '').strip() for lado in lados):
        return 'una entidad sin resolver'
    if not ((fila.get('by_doc') or '').strip() or textos_de_lista(fila.get('by_quote'))):
        return 'sin un criterio (by_doc o by_quote)'
    return None


FORMAS = {'splits': forma_split, 'deny': forma_deny, 'fix': forma_fix, 'alias_deny': forma_alias_deny}



# AC157: el tipo de una hoja según su ruta. Texto en cualquier campo del inventario; un número o un booleano, solo donde
# lo declara (`NUMERO`, `BOOLEANO`: recuentos, importes, páginas, banderas, la configuración de la ontología), y ahí
# solo él; nulo, en cualquiera. Cualquier otro tipo YAML (`!!set`, `!!binary`, una fecha sin comillas) no lo audita
# ningún recorrido: nunca pasa.
_NO_ES = {NUMERO: 'un campo que no es un número', BOOLEANO: 'un campo que no es un booleano',
          None: 'un campo que no es un texto'}


def problemas_hojas(campos, valor) -> list[tuple[str, str]]:
    """AC157: [(ruta, motivo)] de las hojas de un valor (`_hojas`) que no son del tipo que el inventario de su sección
    admite en su ruta (casada por segmentos; el patrón `**`, el último), con la ruta unida (`_unida`) y sin el valor. La
    hoja de una ruta que el inventario no clasifica la denuncia AC127. AC163: un número, uno que el grafo guarda
    (`cabe_en_el_grafo`), donde se declare. AC186: un nulo es un campo ausente, salvo como elemento de una lista que
    el inventario declara (`duplicates: [null]`): ahí no es de su tipo («un campo que no es un texto»); la lista entera
    nula, si la declara opcional (`NULO`), sí está ausente."""
    patrones = [(p, clase) for p, clase in zip(_patrones(tuple(campos)), campos.values()) if p != ('**',)]
    out = []
    for ruta, hoja in _hojas(valor):
        if isinstance(hoja, (list, dict)):
            continue
        explicita = next((c for p, c in patrones if len(p) == len(ruta) and all(map(_casa, ruta, p))), None)
        clase = campos.get('**') if explicita is None else explicita
        if clase in (None, NULO):   # AC181: lo que no es nulo ahí lo juzga la lista o el mapa que declara
            continue
        if hoja is None:
            if ruta and ruta[-1] is _ELEMENTO and explicita is not None:
                out.append((_unida(ruta), _NO_ES[clase if clase in (NUMERO, BOOLEANO) else None]))
            continue
        if not isinstance(hoja, (str, int, float)):
            out.append((_unida(ruta), 'valor no auditable'))
            continue
        tipo = BOOLEANO if isinstance(hoja, bool) else None if isinstance(hoja, str) else NUMERO
        declarado = clase if clase in (NUMERO, BOOLEANO) else None
        if tipo != declarado:
            out.append((_unida(ruta), _NO_ES[declarado]))
        elif not cabe_en_el_grafo(hoja):
            out.append((_unida(ruta), 'un número que el grafo no guarda'))
    return out

def clase_campo(campos, ruta) -> str | None:
    """La clase de una ruta en el inventario de una sección (`*` vale por una clave de mapa), o None."""
    if '**' in campos:
        return campos['**']
    if ruta in campos:
        return campos[ruta]
    return next((clase for patron, clase in campos.items() if '*' in patron and fnmatch.fnmatchcase(ruta, patron)),
                None)


def textos_fila(campos, fila) -> list[tuple[str, str]]:
    """[(texto, clase)] de una fila según el inventario de su sección: lo que no es estructura. Un campo que el
    inventario no clasifica se lee como prosa (fail-closed; el test del inventario lo denuncia)."""
    return [(texto, clase) for ruta, texto in _rutas(fila)
            for clase in [_leida(clase_campo(campos, ruta))] if clase != ESTRUCTURA]


def _leida(clase) -> str:
    """La clase con que se lee un texto de una ruta: la suya; sin clase, o un texto donde va un número, un booleano o
    una lista (AC157/AC181: ya es un error de forma), prosa."""
    return PROSA if clase in (None, NUMERO, BOOLEANO, NULO) else clase


def _yaml_de_datos(repo) -> list[Path]:
    """Los YAML de pipeline/data que el árbol publica o publicaría: los que sigue git y los nuevos que no ignora
    (`git ls-files --cached --others --exclude-standard`); fuera quedan los volcados locales ignorados. Sin git (un
    árbol de prueba), todos los del disco: se revisa de más, nunca de menos."""
    repo = Path(repo)
    try:
        salida = subprocess.run(['git', '-C', str(repo), 'ls-files', '-z', '--cached', '--others', '--exclude-standard',
                                 '--', 'pipeline/data'], check=True, capture_output=True).stdout
        rutas = [repo / r for r in salida.decode('utf-8', 'surrogateescape').split('\0') if r]
    except (OSError, subprocess.SubprocessError):
        datos = repo / 'pipeline/data'
        rutas = [*datos.rglob('*.yaml'), *datos.rglob('*.yml')] if datos.is_dir() else []
    return sorted({r for r in rutas if r.suffix in ('.yaml', '.yml') and r.is_file()})


def campos_sin_clasificar(repo=None) -> list[tuple[str, int, str]]:
    """[(fichero, línea, huella de la clave)] de los ficheros YAML de pipeline/data (`_yaml_de_datos`) y de los campos
    que el inventario (`CAPAS`) no clasifica, de cualquier tipo (AC127, `_fuera_del_inventario`): un fichero nuevo
    (línea 0, huella de «*»), una sección nueva o un campo nuevo (su ruta, en la primera fila que lo trae). Lo comprueba
    un test (AC79); la clave es texto del fichero y nunca sale, ni en el fallo del test (AC92)."""
    repo = Path(repo or REPO)
    primera = {}
    for path in _yaml_de_datos(repo):
        rel = str(path.relative_to(repo))
        capa = capa_de(rel)
        data, lineas = _documento(path, claves=False)   # las claves que el árbol pararía (AC127), aquí se listan
        if capa is None:
            primera[(rel, '*')] = 0
            continue
        if None in capa:
            for ruta in _fuera_del_inventario(capa[None].campos, data):
                primera.setdefault((rel, ruta), 1)
            continue
        for seccion, filas in data.items():
            if seccion not in capa:
                primera.setdefault((rel, str(seccion)), _linea(lineas, seccion, 0))
                continue
            for i, fila in enumerate(filas if isinstance(filas, list) else [filas]):
                for ruta in _fuera_del_inventario(capa[seccion].campos, fila):
                    linea = _linea(lineas, seccion, i)
                    clave = (rel, f'{seccion}[].{ruta}')
                    primera[clave] = min(primera.get(clave, linea), linea)
    return sorted((rel, linea, sha16(ruta)) for (rel, ruta), linea in primera.items())


def _personas(data) -> list[tuple]:
    """[(entity_id, nombre)] de las personas que nombra un fichero de contexto (AC68): el `name` y los alias de sus
    filas `entities` de tipo Person, el `name` de sus `summaries` de una Person y los de los grupos de `merges`."""
    out = []
    for fila in data.get('entities') or []:
        if isinstance(fila, dict) and fila.get('type') == 'Person':
            aliases = fila.get('aliases') if isinstance(fila.get('aliases'), list) else []
            out += [(_id_contexto(fila), n) for n in (fila.get('name'), *aliases) if isinstance(n, str)]
    for fila in data.get('summaries') or []:
        if isinstance(fila, dict) and str(fila.get('entity_id') or '').startswith('Person:'):
            out += [(str(fila['entity_id']).strip(), fila['name'])] if isinstance(fila.get('name'), str) else []
    for grupo in data.get('merges') or []:
        duplicados = grupo.get('duplicates') if isinstance(grupo, dict) else None
        for lado in ((grupo.get('canonical'), *(duplicados if isinstance(duplicados, list) else []))
                     if isinstance(grupo, dict) else ()):
            if isinstance(lado, dict) and lado.get('type') == 'Person' and isinstance(lado.get('name'), str):
                out.append((entidad(lado), lado['name']))
    return out


def personas_contexto(repo=None) -> list[tuple]:
    """[(entity_id, nombre)] de las personas que nombran los ficheros de contexto del árbol, salvo el roster."""
    repo = Path(repo or REPO)
    contexto = repo / CONTEXT_REL
    return [p for path in (ficheros_overlay(contexto) if contexto.is_dir() else [])
            if path != repo / PSEUDONYMS_REL for p in _personas(_documento(path)[0])]


def extremos_por_nombre(repo=None, ids=frozenset()) -> list[tuple]:
    """AC91(iii)/(iv): [(fichero, línea, texto, entity_id, conocido)] de los extremos civiles que las reglas deny/fix y
    las posturas de las afirmaciones escriben por nombre, para cotejarlos con el export (`verificar_snapshot`);
    `conocido`: el texto es uno de los nombres que el contexto da a esa entidad (`nombres_contexto`)."""
    repo = Path(repo or REPO)
    contexto = {eid: {tuple(_palabras(n)) for n in nombres} for eid, nombres in nombres_contexto(repo).items()}
    filas = [(str(Path(r.fichero).relative_to(repo)), r.linea, _extremos_texto_regla(r.fila))
             for r in reglas_publicas(repo / RELATIONS_REL) if r.seccion in ('deny', 'fix')]
    for patron, ficheros in _ficheros_por_capa(repo):
        seccion = next((s for s, spec in CAPAS[patron].items() if spec.extremos), None)
        for path in ficheros if seccion else ():
            data, lineas = _documento(path)
            filas += [(str(path.relative_to(repo)), _linea(lineas, seccion, i), CAPAS[patron][seccion].extremos(fila))
                      for i, fila in enumerate(data.get(seccion) or []) if isinstance(fila, dict)]
    return [(fichero, linea, texto, eid, tuple(_palabras(texto)) in contexto.get(eid, ()))
            for fichero, linea, pares in filas for texto, eid, _ in pares if eid not in ids]


def nombres_contexto(repo=None) -> dict:
    """AC90/AC91: {entity_id: {nombres}} de las entidades que nombran los ficheros de contexto (salvo el roster), de
    cualquier tipo: el `name` y los alias de sus `entities`, el `name` de sus `summaries` y los de sus grupos de
    `merges`, con los ids que derivan sus loaders. En el árbol, lo que se sabe de cada extremo."""
    repo = Path(repo or REPO)
    contexto = repo / CONTEXT_REL
    out = {}
    for path in (ficheros_overlay(contexto) if contexto.is_dir() else []):
        if path == repo / PSEUDONYMS_REL:
            continue
        data, pares = _documento(path)[0], []
        for fila in data.get('entities') or []:
            if isinstance(fila, dict):
                aliases = fila.get('aliases') if isinstance(fila.get('aliases'), list) else []
                pares += [(_id_contexto(fila), n) for n in (fila.get('name'), *aliases)]
        pares += [(_texto(fila.get('entity_id')), fila.get('name')) for fila in data.get('summaries') or []
                  if isinstance(fila, dict)]
        for grupo in data.get('merges') or []:
            if not isinstance(grupo, dict):
                continue
            can = grupo.get('canonical') if isinstance(grupo.get('canonical'), dict) else {}
            tipo = str(can.get('type') or '').strip()
            duplicados = grupo.get('duplicates') if isinstance(grupo.get('duplicates'), list) else []
            for lado in (can, *duplicados):
                lado = {'type': tipo, 'name': lado} if isinstance(lado, str) else lado
                if isinstance(lado, dict):
                    pares.append((entidad({**lado, 'type': lado.get('type') or tipo}), lado.get('name')))
        for eid, nombre in pares:
            if eid and isinstance(nombre, str) and nombre.strip():
                out.setdefault(eid, set()).add(nombre.strip())
    return out


class _Indice(dict):
    """El índice de nombres de `_indice_nombres`, que guarda lo que su recorrido calcula una vez (`_derivado`), el
    índice sin las lecturas añadidas de sus nombres (`base`, AC202; None si no las hay: él mismo) y, el civil, los
    alias inválidos de un seudónimo que la misma ejecución denuncia (`denunciados`, AC134)."""
    derivado = None
    base = None
    denunciados = frozenset()


def _base(indice):
    """AC202: un índice de nombres sin las lecturas añadidas de sus nombres (`_Indice.base`); otro, él mismo."""
    base = getattr(indice, 'base', None)
    return indice if base is None else base


def _indice_nombres(personas, minimo=1, excluidas=frozenset(), anadidas=()) -> dict:
    """{primera palabra: [(candidato, nombre, entity_id)]} de los nombres de unas personas ([(entity_id, nombre)]), para
    buscarlos en un texto (`_apariciones_indice`): AC104/AC105(b)/AC109/AC113, cada nombre como candidato (`_candidato`:
    cuentan sus lecturas de `minimo` palabras o más que no son una de `excluidas`), bajo cada palabra con que puede
    empezar. AC201: y, si da otras fichas, el de su lectura a través de las comillas (`_comillas_partidas`), con el
    mismo nombre: «Nim'bo» también es «nimbo»; AC205: y los de `anadidas` ([(entity_id, lectura, nombre)], las de un
    nombre que solo existen a través de sus comillas). AC202: las lecturas añadidas solo están en el índice completo; el
    índice sin ellas, en su `base`, con el que se buscan las lecturas del texto que no pueden perder un hallazgo."""
    base, completo, excluidas = _Indice(), _Indice(), frozenset(excluidas)

    def poner(eid, lectura, nombre, indices):
        candidato = _candidato(lectura, minimo, excluidas)
        for primera in candidato.primeras if candidato else ():
            for indice in indices:
                indice.setdefault(primera, []).append((candidato, nombre.strip(), eid))
    anadidas = list(anadidas)
    for eid, nombre in personas:
        poner(eid, nombre, nombre, (base, completo))
        otra = _comillas_partidas(nombre)
        if _fichas(otra) != _fichas(nombre):
            anadidas.append((eid, otra, nombre))
    for eid, lectura, nombre in anadidas:
        poner(eid, lectura, nombre, (completo,))
    if not anadidas:
        return base
    completo.base = base
    return completo


def _derivado(indice) -> tuple[int, tuple, dict, dict]:
    """AC113/AC117: de un índice, hasta dónde unir piezas del texto en busca de una primera palabra: el largo de la más
    larga de los candidatos de una sola lectura; de los demás, el esqueleto (`_esqueleto`) de su primer tramo de piezas
    unibles como un árbol de letras ((hijos {letra: nodo}, candidatos que empiezan así, candidatos con un límite de
    pieza justo ahí: una palabra con que pueden empezar)) y sus filas ({candidato: [(nombre, entity_id)]}); y las claves
    de los de una sola lectura por esqueleto ({esqueleto: [clave]}): una palabra del texto solo puede ser forma de una
    de su mismo esqueleto. En un `_Indice`, una sola vez. AC203/AC209: cada candidato una vez, y ni sus comienzos ni las
    palabras con que puede empezar se materializan: un nodo por letra."""
    derivado = getattr(indice, 'derivado', None)
    if derivado is None:
        largo, prefijos, claves, filas_de = 0, ({}, set(), set()), {}, {}
        for clave, filas in indice.items():
            if any(candidato.palabras is not None for candidato, _, _ in filas):
                claves.setdefault(_esqueleto(clave), []).append(clave)
            for candidato, nombre, eid in filas:
                if candidato.palabras is not None:
                    largo = max(largo, len(candidato.palabras[0]))
                    continue
                if candidato in filas_de:
                    filas_de[candidato].append((nombre, eid))
                    continue
                filas_de[candidato] = [(nombre, eid)]
                cadena = candidato.cadena
                fin = next(o for o in range(1, len(cadena.tipos)) if cadena.tipos[o] == 'H')
                nodo = prefijos
                for o, letra in enumerate(_esqueleto(cadena.letras[:fin]), 1):
                    nodo = nodo[0].setdefault(letra, ({}, set(), set()))
                    nodo[1].add(candidato)
                    if cadena.tipos[o]:
                        nodo[2].add(candidato)
        derivado = (largo, prefijos, claves, filas_de)
        if isinstance(indice, _Indice):
            indice.derivado = derivado
    return derivado


# AC129: la entidad con que el índice civil guarda el alias de un seudónimo que no es válido (ni variante AC44 de su
# nombre de pila ni de la lista de permitidas): no lo exime en sus filas ni exime lo que coincide con él.
_ALIAS_INVALIDO = '#alias-invalido'


def indice_civiles(personas, ids, permitidos=None, denunciados=()) -> dict:
    """AC68(b)/AC78(b)/AC118: el índice de los nombres de las personas (`personas`: [(entity_id, nombre)]): los de dos o
    más palabras, sin excluir a las del roster: solo se exime el nombre de pila de cada seudónimo (sus lecturas, en
    todas las entidades, y sus variantes AC44, en la suya). Un nombre del roster que no es variante de su nombre de pila
    sigue en el índice y cuenta en las filas de cualquier otro seudónimo; en las suyas no cuenta, y AC129 solo lo exime
    si es un alias válido, de la sección `alias:` de la lista (`permitidos`): si no, se guarda como de nadie
    (`_ALIAS_INVALIDO`) y no exime al civil que se llama igual. AC134: su mención en las filas del seudónimo cuenta
    como nombre, salvo la de un alias que la misma ejecución denuncia (`denunciados`: [(seudónimo, alias)], los de
    `alias_invalidos_contexto` y los del export): ese ya hace fallar la comprobación. Un nombre de una palabra no se
    distingue de un nombre de pila (residual: los apellidos sueltos). El nombre de pila de otro seudónimo, en la prosa
    de una fila: `_pilas_en`."""
    propios = {_nombre(s) for s in ids}
    validos = {e.get('id'): {str(a).strip() for a in e.get('permitidos') or ()}
               for e in (permitidos or {}).get('alias') or () if isinstance(e, dict)}

    def suya(eid, nombre):
        return eid if eid not in ids or nombre.strip() in validos.get(eid, ()) else f'{eid}{_ALIAS_INVALIDO}'
    indice = _indice_nombres(((suya(eid, n), n) for eid, n in personas
                              if isinstance(n, str) and not (eid in ids and es_variante_nombre(eid, n))),
                             minimo=2, excluidas=propios)
    indice.denunciados = _base(indice).denunciados = frozenset((s, str(a).strip()) for s, a in denunciados)
    return indice


def alias_invalidos_contexto(repo=None, ids=frozenset(), permitidos=None) -> list[tuple[str, int, str, str]]:
    """AC134: [(fichero, línea, seudónimo, alias)] de los alias que una fila `entities` de un fichero de contexto (salvo
    el roster) da a un id del roster y que no son una variante AC44 de su nombre de pila ni están en la sección `alias:`
    de la lista (`permitidos`): el fichero es público aunque el loader no los lleve al grafo. Su `name`, como el de
    `summaries` y los de `merges`, lo juzga AC83."""
    repo = Path(repo or REPO)
    validos = {e.get('id'): {str(a).strip() for a in e.get('permitidos') or ()}
               for e in (permitidos or {}).get('alias') or () if isinstance(e, dict)}
    contexto, out = repo / CONTEXT_REL, []
    for path in (ficheros_overlay(contexto) if contexto.is_dir() else []):
        if path == repo / PSEUDONYMS_REL:
            continue
        data, lineas = _documento(path)
        for i, fila in enumerate(data.get('entities') or []):
            sid = _id_contexto(fila) if isinstance(fila, dict) else None
            if sid not in ids:
                continue
            out += [(str(path.relative_to(repo)), _linea(lineas, 'entities', i), sid, a.strip())
                    for a in (fila.get('aliases') if isinstance(fila.get('aliases'), list) else [])
                    if isinstance(a, str) and a.strip() and not es_variante_nombre(sid, a)
                    and a.strip() not in validos.get(sid, ())]
    return out


def alias_identificadores_contexto(repo=None, ids=frozenset()) -> list[tuple[str, int, str]]:
    """AC167: [(fichero, línea, alias)] de los alias que una fila `entities` de un fichero de contexto (salvo el roster)
    da a una entidad civil (fuera del roster) y que la identifican con un seudónimo (`nombre_identificador`, frente a
    los demás nombres de la fila): como AC134 para los de un seudónimo, el fichero es público. Su `name` lo juzga la
    fila (`identificaciones_civiles`)."""
    repo = Path(repo or REPO)
    contexto, out = repo / CONTEXT_REL, []
    for path in (ficheros_overlay(contexto) if contexto.is_dir() else []):
        if path == repo / PSEUDONYMS_REL:
            continue
        data, lineas = _documento(path)
        for i, fila in enumerate(data.get('entities') or []):
            eid = _id_contexto(fila) if isinstance(fila, dict) else None
            if not eid or eid in ids:
                continue
            aliases = [a.strip() for a in (fila.get('aliases') if isinstance(fila.get('aliases'), list) else [])
                       if isinstance(a, str) and a.strip()]
            nombres = [n.strip() for n in (fila.get('name'), *aliases) if isinstance(n, str) and n.strip()]
            out += [(str(path.relative_to(repo)), _linea(lineas, 'entities', i), a) for a in aliases
                    if nombre_identificador(a, ids, nombres)]
    return out


@functools.lru_cache(maxsize=64)
def _pilas_compuestas(ids) -> tuple:
    """AC118/AC130: ((palabras ordenadas, (seudónimos)) de cada nombre de pila de dos palabras o más del roster
    (`ids`, un frozenset); los homónimos (las mismas palabras) juntos. El índice civil los exime en toda fila; en la de
    otro seudónimo cuentan (`_pilas_en`): en su prosa, siempre (`problemas_seudonimo`), y en sus citas, sin la prueba de
    que son literales (`citas_con_pila_ajena`; un auto nombra a varios seudónimos por su nombre ficticio). Declarar a un
    civil en el roster con su nombre completo como «nombre de pila» no lo exime en la fila de otro."""
    por_palabras = {}
    for sid in sorted(ids):
        if len(_nombre(sid)) >= 2:
            por_palabras.setdefault(tuple(sorted(_nombre(sid))), []).append(sid)
    return tuple((palabras, tuple(sids)) for palabras, sids in sorted(por_palabras.items()))


def _pila_en(fichas, palabras, largo) -> bool:
    """AC130: ¿están en el texto (`fichas`), en alguna lectura, las palabras de un nombre de pila compuesto, cada una
    en una palabra que la tiene por forma (`_es_forma`), todas, seguidas y en cualquier orden, salvo marcas o
    tratamientos intercalados (`_sin_nombre`, AC135: también «d0n»), como la mención (AC71)? Con las palabras del
    nombre como bits y un recorrido de los estados (pieza, palabras hechas), sin enumerar permutaciones ni lecturas."""
    completo = (1 << len(palabras)) - 1
    pendientes = [(inicio, 0) for inicio in range(len(fichas.piezas))]
    vistos = set(pendientes)
    while pendientes:
        pieza, hechas = pendientes.pop()
        if pieza == len(fichas.piezas):
            continue
        for fin, palabra in _palabras_desde(fichas, pieza, largo):
            if _sin_nombre(palabra):
                siguientes = [hechas] if hechas else []
            else:
                siguientes = [hechas | 1 << j for j, p in enumerate(palabras)
                              if not hechas >> j & 1 and _es_forma(palabra, p)]
            for siguiente in siguientes:
                if siguiente == completo:
                    return True
                if (fin, siguiente) not in vistos:
                    vistos.add((fin, siguiente))
                    pendientes.append((fin, siguiente))
    return False


def _pilas_en(texto, ids, exentos=frozenset()) -> set[str]:
    """AC118/AC130: los nombres de pila de dos palabras o más del roster (`_pilas_compuestas`) que nombra un texto, con
    sus palabras completas en cualquier orden y con «don», «Sr.» o «doña» delante o no (`_pila_en`), salvo los de un
    seudónimo de `exentos` (los de la fila y sus homónimos): cada uno con sus palabras en el orden del slug, su
    huella. También a través de sus comillas (AC168; AC202: en cada lectura del texto, `_lecturas_texto`, sumadas)."""
    out = set()
    for lectura in _lecturas_texto(texto):
        fichas = _fichas(lectura)
        if not fichas.piezas:
            continue
        for palabras, sids in _pilas_compuestas(frozenset(ids)):
            if set(sids) & set(exentos):
                continue
            largo = max(map(len, palabras))
            presentes = {palabra for inicio in range(len(fichas.piezas))
                         for _, palabra in _palabras_desde(fichas, inicio, largo)}
            if (all(any(_es_forma(w, p) for w in presentes) for p in set(palabras))
                    and _pila_en(fichas, palabras, largo)):
                out.add(' '.join(_nombre(sids[0])))
    return out


# AC109/AC113: una aparición de un nombre del índice en un texto: sus piezas (de `inicio` a `fin`, excluida), el nombre,
# su entidad y su candidato (`_Candidato`).
_Aparicion = namedtuple('_Aparicion', 'inicio fin nombre eid candidato')


def _clave_candidato(c) -> tuple:
    """AC229/AC232: un candidato (`_Candidato`) por su CONTENIDO completo (`_nuevo_candidato`): mínimo, lecturas
    excluidas (ordenadas) y cadena entera. Se tabulan por ella los candidatos de todo lo que decide un resultado o un
    presupuesto, nunca por su identidad (la caché de `_candidato` expulsa entradas y da otro objeto con el mismo
    contenido). Dos lecturas del mismo nombre (el original y su lectura a través de las comillas) difieren en la
    cadena. Se hashea en O(lecturas excluidas): la cadena es un solo texto, cuyo hash se calcula una vez."""
    return c.clave


def _clave_aparicion(a) -> tuple:
    """AC226/AC229: el orden total de las apariciones por su CONTENIDO (dónde están, qué nombre y de qué entidad, y el
    candidato entero, `_clave_candidato`), nunca por identidades de objetos ni por el orden de un conjunto
    (`_Candidato` se tabula por identidad: las direcciones en memoria no son del texto ni de las filas). Dos
    apariciones que empatan son indistinguibles para el presupuesto de la exención."""
    return a.inicio, a.fin, a.nombre, a.eid or '', *a.candidato.clave


def _apariciones_indice(fichas, indice) -> list[_Aparicion]:
    """AC104/AC107/AC109/AC112/AC113: cada aparición en un texto (`fichas`) de un nombre del índice (`_recorre_indice`),
    una vez, en el orden canónico de su contenido (`_clave_aparicion`; AC226: el presupuesto de la exención se consume
    en ese orden, así que lo que denuncia es función del texto y las filas). AC232: una vez por CONTENIDO (la misma
    fila con dos objetos candidato iguales, uno por cada expulsión de la caché de `_candidato`, es una sola)."""
    unicas = {}
    for a in _recorre_indice(fichas, indice):
        unicas.setdefault(_clave_aparicion(a), a)
    return [unicas[clave] for clave in sorted(unicas)]


# AC218: las coincidencias exactas de un candidato en todas las ventanas de un texto a la vez, con máscaras de bits
# (el bit s de una máscara de texto, la posición s; la de un candidato, su posición j), sin recorrer el candidato
# desde cada aparición. La relación es la de `_letra`/`_vale`: la letra o cifra del texto es la del candidato, o un
# dígito suyo leído como una de sus letras homógrafas (`_DIGITO_LETRAS`); y cada palabra de una lectura, entera o con
# un dígito leído como letra solo si lleva alguna letra (`_mezcla`). Una palabra de solo cifras es siempre una pieza
# del texto (dos piezas de cifras nunca se unen, `_fichas_de`): con una sustitución no vale, y solo puede ser una
# palabra si sus dos límites son cortes; en la lectura más gruesa (la que solo corta donde es forzoso: donde el texto
# tiene un límite duro o el candidato uno de los suyos), una lectura válida lo es también en cualquier otra con menos
# cortes, así que existe una lectura válida si y solo si lo es esa.
_ALFABETO = 'abcdefghijklmnopqrstuvwxyz0123456789'
_SOLO_CIFRAS = str.maketrans({c: '1' if c.isdigit() else '0' for c in _ALFABETO})


@functools.lru_cache(maxsize=64)
def _tabla_bits(x) -> dict:
    """La tabla de `str.translate` que da «1» a la letra o cifra `x` y «0» a las demás."""
    return {ord(c): '1' if c == x else '0' for c in _ALFABETO}


def _bit(octetos, k) -> int:
    """El bit `k` de una máscara en octetos (el de menor peso primero), en O(1), sin desplazar la máscara entera."""
    return octetos[k >> 3] >> (k & 7) & 1


def _bit_de(valor, k) -> int:
    """El bit `k` de una máscara entera."""
    return valor >> k & 1


def _desplaza(valor, k) -> int:
    """`valor` desplazado `k` bits hacia la derecha (a la izquierda si `k` es negativo)."""
    return valor >> k if k >= 0 else valor << -k


class _Mascaras:
    """AC218: las máscaras de un texto (`fichas`) y las coincidencias exactas de sus candidatos, una vez por recorrido
    (`_recorre_indice`): lo que se calcula de un texto o de un candidato se guarda aquí, no en las fichas."""

    def __init__(self, fichas):
        self.fichas = fichas
        self._simbolos, self._casan, self._exactas = {}, {}, {}

    def simbolo(self, x) -> int:
        """La máscara de las posiciones del texto con la letra o cifra `x`."""
        mascara = self._simbolos.get(x)
        if mascara is None:
            mascara = self._simbolos[x] = int(self.fichas.cadena.letras[::-1].translate(_tabla_bits(x)), 2)
        return mascara

    @functools.cached_property
    def cifras(self) -> tuple[int, list[tuple[int, int, bool, bool]]]:
        """(las posiciones de cifras que pueden leerse como letra, las piezas de solo cifras que no están aisladas, con
        si tienen un límite duro a su izquierda y a su derecha): una pieza de solo cifras con un límite duro a cada lado
        es una palabra por sí sola, y una cifra suya es ella misma (la lectura no la cambia); con un punto de partición,
        su palabra depende del corte que haga el candidato."""
        fichas = self.fichas
        piezas, unibles = fichas.piezas, fichas.unibles
        trozos, sueltas, posicion = [], [], 0
        for g, pieza in enumerate(piezas):
            duro_izq, duro_der = g == 0 or not unibles[g - 1], g == len(piezas) - 1 or not unibles[g]
            if pieza.isdigit() and duro_izq and duro_der:
                trozos.append('0' * len(pieza))
            else:
                trozos.append(pieza.translate(_SOLO_CIFRAS))
                if pieza.isdigit():
                    sueltas.append((posicion, posicion + len(pieza), duro_izq, duro_der))
            posicion += len(pieza)
        return int(''.join(trozos)[::-1], 2), sueltas

    @functools.cached_property
    def tipos(self) -> tuple[int, int]:
        """(las posiciones del texto con un límite duro, las que tienen un punto de partición)."""
        tipos = self.fichas.cadena.tipos
        return tuple(int(''.join('1' if t == c else '0' for t in reversed(tipos)), 2) for c in ('H', 'J'))

    def casan(self, c) -> int:
        """La máscara de las posiciones del texto que pueden ser la letra o cifra `c` de un candidato: ella misma o un
        dígito (no el de una pieza aislada de solo cifras) del que `c` es una letra homógrafa."""
        mascara = self._casan.get(c)
        if mascara is None:
            mascara = self.simbolo(c)
            for digito, letras in _DIGITO_LETRAS.items():
                if c in letras:
                    mascara |= self.simbolo(digito) & self.cifras[0]
            self._casan[c] = mascara
        return mascara

    def exactas(self, candidato) -> tuple[bytes, bytes]:
        """(las ventanas, por la letra de comienzo, en que el candidato casa seguro, las que habría que decidir con
        una lectura concreta, `_hay_lectura`), como máscaras en octetos (`_bit`): ambas suponen los límites
        compatibles (`_ventana_posible`). Un candidato sin restricciones (`_sin_restricciones`: ni lecturas excluidas
        ni más palabras que las que cortan sus límites duros) casa seguro si se cumple el resto de condiciones; con
        restricciones, falta que haya el corte que le da sus palabras, y si no se sabe sin elegir lectura (una pieza
        de cifras que lee otra cosa, o lecturas excluidas), es de las segundas."""
        hecho = self._exactas.get(candidato)
        if hecho is not None:
            return hecho
        texto, cand = self.fichas.cadena, candidato.cadena
        n, m = len(texto.letras), len(cand.letras)
        validas = (1 << (n - m + 1)) - 1 if m <= n else 0
        casa, por_pieza = validas, {}
        for a, b in zip(cand.inicios, (*cand.inicios[1:], m)):   # la pieza de cada candidato una vez por contenido
            if not casa:
                break
            pieza = cand.letras[a:b]
            en = por_pieza.get(pieza)
            if en is None:
                en = (1 << n) - 1
                for i, c in enumerate(pieza):
                    en &= self.casan(c) >> i
                por_pieza[pieza] = en
            casa &= en >> a
        fallo = sustituidas = 0
        if casa:
            fallo, sustituidas = self._cifras_sustituidas(candidato, casa, n, m)
        seguras = casa & ~fallo
        duros = sum(1 for t in cand.tipos[1:-1] if t == 'H')
        if candidato.excluidas:
            hecho = 0, seguras
        elif candidato.minimo - 1 - duros <= 0:
            hecho = seguras, 0
        elif candidato.minimo - 1 - duros == 1 and seguras:
            hecho = self._con_un_corte(candidato, seguras, n, m), 0
        else:
            hecho = 0, seguras
        hecho = tuple(m.to_bytes((n + 8) // 8, 'little') for m in hecho)
        self._exactas[candidato] = hecho
        return hecho

    def _con_un_corte(self, candidato, seguras, n, m) -> int:
        """AC222: de las ventanas `seguras` (sin una pieza de cifras sustituida entre dos cortes forzosos), las que
        tienen una lectura con el corte más que piden las palabras de un candidato sin límites duros suyos, sin recorrer
        cada ventana. Una pieza de solo cifras con una cifra leída como letra (`_cifras_sustituidas`) no puede quedar
        sola en una palabra, y dos de ellas nunca se unen (`_fichas_de`): la lectura que solo corta donde es forzoso (el
        texto tiene un límite duro) es la mejor, y vale si ya tiene el corte. Si no lo tiene, vale cualquier corte libre
        (donde el candidato y el texto tienen un punto de partición) salvo el que deja sola a una pieza sustituida que
        abre o cierra la ventana (la otra cara de la pieza es el borde): como mucho dos por ventana, así que basta saber
        si hay uno, dos o más (contadores saturados en tres) y quitarlos de las ventanas que los tienen."""
        texto, cand = self.fichas.cadena, candidato.cadena
        duros, juntas = self.tipos
        forzado = uno = dos = tres = 0
        for j in range(1, m):
            if cand.tipos[j] == 'J':
                forzado |= duros >> j
                libres = juntas >> j
                tres |= dos & libres
                dos |= uno & libres
                uno |= libres
        validas = seguras & (forzado | uno)
        bloqueadas = {}   # ventana: las posiciones del candidato cuyo corte libre dejaría sola una pieza sustituida
        for inicio, fin, _, _ in self.cifras[1]:
            largo = fin - inicio
            if largo >= m:
                continue
            for s, posicion in ((inicio, largo), (fin - m, m - largo)):   # la pieza abre la ventana, o la cierra
                if (0 <= s <= n - m and _bit_de(validas, s) and not _bit_de(forzado, s) and cand.tipos[posicion] == 'J'
                        and texto.tipos[s + posicion] == 'J'
                        and texto.letras[inicio:fin] != cand.letras[inicio - s:inicio - s + largo]):
                    bloqueadas.setdefault(s, set()).add(posicion)
        fallan = [s for s, posiciones in bloqueadas.items()
                  if not (_bit_de(tres, s) or (_bit_de(dos, s) and len(posiciones) <= 1))]
        return validas & ~sum(1 << s for s in fallan)

    def _cifras_sustituidas(self, candidato, casa, n, m) -> tuple[int, int]:
        """(las ventanas que dejan una pieza de solo cifras con una cifra leída como letra entre dos cortes forzosos, y
        las que dejan alguna sin más): de las piezas de solo cifras con un punto de partición. Un corte es forzoso donde
        el texto tiene un límite duro o el candidato uno suyo."""
        texto, cand = self.fichas.cadena, candidato.cadena
        sueltas = self.cifras[1]
        if not sueltas:
            return 0, 0
        duros = int(''.join('1' if t == 'H' else '0' for t in cand.tipos), 2)   # el bit m - j, el corte duro j suyo
        inversos, fallo, sustituidas = {}, 0, 0
        for inicio, fin, duro_izq, duro_der in sueltas:
            lo, hi = max(fin - m, 0), min(inicio, n - m)   # las ventanas que contienen la pieza
            if fin - inicio > m or lo > hi:
                continue
            ventana = ((1 << (hi - lo + 1)) - 1) << lo
            if not casa & ventana:
                continue
            iguales = ventana
            for q in range(inicio, fin):
                x = texto.letras[q]
                if x not in inversos:
                    inversos[x] = int(cand.letras.translate(_tabla_bits(x)), 2)   # el bit m - 1 - j, la letra j suya
                iguales &= _desplaza(inversos[x], m - 1 - q)
                if not iguales:
                    break
            distintas = ventana & ~iguales & casa
            if distintas:
                sustituidas |= distintas
                fallo |= (distintas & (ventana if duro_izq else _desplaza(duros, m - inicio))
                          & (ventana if duro_der else _desplaza(duros, m - fin)))
        return fallo, sustituidas


# AC213: de cuántas palabras en adelante se decide un candidato de una sola lectura sin compararlo palabra a palabra.
_PALABRAS_SIN_FILTRO = 3


def _casa_largo(fichas, inicio, fin, candidato, mascaras) -> int | None:
    """La pieza que sigue a un candidato de una sola lectura (`_Candidato.palabras`) si casa desde la pieza `inicio`
    (cuya primera palabra acaba en la `fin`), o None. Los de pocas palabras, palabra a palabra (`_tramo`). AC213: los
    demás, en O(1) por ventana (`_ventana_posible`) mientras sea posible: sin su esqueleto o con límites
    incompatibles, no casan; con sus mismas letras, casan (los límites ya son compatibles: cada palabra es entera una
    forma de sí misma); con otras letras y sin cifras, no. AC218: con cifras de por medio (una cifra puede ser una
    letra, por palabra), lo deciden las máscaras de todas las ventanas a la vez (`_Mascaras.exactas`), no el candidato
    palabra a palabra desde cada aparición."""
    palabras = candidato.palabras
    if len(palabras) <= _PALABRAS_SIN_FILTRO:
        cortes = _tramo(fichas, fin, palabras[1:])
        return cortes[-1] if cortes is not None else None
    texto = fichas.cadena
    desde = texto.inicios[inicio]
    hasta = desde + len(candidato.cadena.letras)
    if fichas.mixto.startswith(_mixto_candidato(candidato), 2 * desde):   # la misma ventana, letras y límites
        return bisect.bisect_left(texto.inicios, hasta)
    iguales = texto.letras.startswith(candidato.cadena.letras, desde)
    if not iguales and hasta <= len(texto.letras) and fichas.digitos[hasta] == fichas.digitos[desde]:
        return None
    if not _ventana_posible(fichas, desde, candidato):
        return None
    if iguales or _bit(mascaras.exactas(candidato)[0], desde):
        return bisect.bisect_left(texto.inicios, hasta)
    return None


def _recorre_indice(fichas, indice):
    """Las apariciones en un texto (`fichas`) de los nombres del índice, por posición: en cada pieza, cada palabra que
    puede empezar en ella, unida a las siguientes por puntos de partición mientras pueda ser la primera de un candidato
    (`_derivado`: hasta el largo de la de una sola lectura más larga, o mientras su esqueleto empiece la de un candidato
    con puntos de partición aún sin alinear en esa pieza); las claves del índice de las que es forma (`_es_forma`, entre
    las de su esqueleto) y los candidatos que empiezan por ellas: el de una sola lectura, palabra a palabra (`_tramo`);
    el que tiene puntos de partición, alineado con el texto (`_fin_alineado`), una vez por contenido del tramo. AC232:
    las tablas de aquí (`_derivado`, `aqui`, `alineados`, las máscaras) se tabulan por la identidad del candidato y solo
    guardan coste: dos objetos de igual contenido dan las mismas apariciones, que `_apariciones_indice` une por
    contenido."""
    piezas, unibles = fichas
    largo, prefijos, claves, filas_de = _derivado(indice)
    esqueleto = _esqueleto(fichas.cadena.letras) if prefijos[0] else ''
    inicios = fichas.cadena.inicios if esqueleto else ()
    alineados, mascaras = {}, _Mascaras(fichas)
    for inicio in range(len(piezas)):
        fin, palabra, aqui = inicio + 1, piezas[inicio], {}
        nodo, visto = prefijos, 0   # AC203: el comienzo de la palabra en el árbol, letra a letra
        while True:
            for forma in (c for c in claves.get(_esqueleto(palabra), ()) if _es_forma(palabra, c)):
                for candidato, nombre, eid in indice[forma]:
                    if candidato.palabras is not None:
                        cierre = _casa_largo(fichas, inicio, fin, candidato, mascaras)
                        if cierre is not None:
                            yield _Aparicion(inicio, cierre, nombre, eid, candidato)
            # AC209: los de puntos de partición con un límite de pieza donde acaba la palabra, alineados una vez
            while esqueleto and nodo is not None and visto < len(palabra):
                nodo = nodo[0].get(esqueleto[inicios[inicio] + visto])
                visto += 1
            for candidato in (nodo[2] if esqueleto and nodo is not None else ()):
                if candidato not in aqui:
                    aqui[candidato] = final = _fin_alineado(fichas, inicio, candidato, alineados, mascaras)
                    if final is not None:
                        yield from (_Aparicion(inicio, final, nombre, eid, candidato)
                                    for nombre, eid in filas_de[candidato])
            if fin == len(piezas) or not unibles[fin - 1]:
                break
            palabra += piezas[fin]
            fin += 1
            if len(palabra) > largo:
                while esqueleto and nodo is not None and visto < len(palabra):
                    nodo = nodo[0].get(esqueleto[inicios[inicio] + visto])
                    visto += 1
                pendientes = nodo[1] if esqueleto and nodo is not None else None
                if not pendientes or pendientes <= aqui.keys():
                    break


@functools.lru_cache(maxsize=8192)
def _sin_restricciones(candidato) -> bool:
    """AC213: ¿alinea un candidato en toda ventana de sus mismas letras con límites compatibles (`_ventana_posible`)?
    Sí si ninguna de sus lecturas está excluida y sus cortes forzosos (los límites duros) bastan para sus `minimo`
    palabras: en cada punto de partición el texto decide, y un punto del texto lo deja a cualquiera de los dos."""
    return not candidato.excluidas and candidato.minimo <= 1 + sum(1 for t in candidato.cadena.tipos[1:-1] if t == 'H')


def _fin_alineado(fichas, inicio, candidato, alineados, mascaras) -> int | None:
    """AC113: la pieza que sigue a `candidato` si aparece en el texto desde la pieza `inicio` en alguna lectura
    (`_hay_lectura`): su largo acaba donde acaba una pieza; si no, None. El alineamiento depende solo de las letras y
    los cortes del tramo: `alineados` lo guarda por ellos (un texto que se repite no lo repite; AC213: por el hash de la
    ventana, comprobada tal cual, sin copiarla en cada consulta). AC218: lo que las máscaras de todas las ventanas a la
    vez (`_Mascaras.exactas`) deciden (un dígito leído como letra, o un candidato con restricciones) no se recorre; solo
    lo que piden una lectura concreta (`_hay_lectura`)."""
    texto = fichas.cadena
    desde = texto.inicios[inicio]
    hasta = desde + len(candidato.cadena.letras)
    fin = bisect.bisect_left(texto.inicios, hasta)
    if hasta != (texto.inicios[fin] if fin < len(texto.inicios) else len(texto.letras)):
        return None
    if _sin_restricciones(candidato) and fichas.mixto.startswith(_mixto_candidato(candidato), 2 * desde):
        return fin   # AC213: la misma ventana, letras y límites
    if not _ventana_posible(fichas, desde, candidato):   # AC213: sin su esqueleto, sin alinear
        return None
    if texto.letras.startswith(candidato.cadena.letras, desde):
        if _sin_restricciones(candidato):   # las mismas letras y límites compatibles: alinea
            return fin
    elif fichas.digitos[hasta] == fichas.digitos[desde]:   # otras letras y ninguna cifra que lea otra: no alinea
        return None
    seguras, dudosas = mascaras.exactas(candidato)
    if _bit(seguras, desde):
        return fin
    if not _bit(dudosas, desde):
        return None
    mixto, hashes = fichas.mixto, fichas.hashes_mixto
    a, b = 2 * desde, 2 * hasta + 1
    clave = (candidato, (hashes[b] - hashes[a] * _potencia(b - a)) % _HASH_M)
    visto = alineados.get(clave)
    if visto is None or not mixto.startswith(visto[0], a):   # el hash elige; las letras y límites, exactas
        visto = alineados[clave] = (mixto[a:b], _hay_lectura(fichas, [(desde, candidato)]))
    return fin if visto[1] else None


# AC224 (enmienda 47), AC228 (enmienda 48): el tope del presupuesto de la exención, en unidades de `_Presupuesto` (unos
# 2,5 µs cada una: 100.000 son ~0,25 s). Lo real no lo roza: el árbol, el snapshot qa067b y el gate gastan 0, los tests
# de comportamiento 113 como mucho, y un nombre de 27 piezas (el más largo del censo) con cuatro nombres contenidos
# 841 (unas 119 veces de margen). Un adversario lo agota y paga como mucho ese tiempo más lo lineal del texto. Una
# constante, no una variable de entorno.
_TOPE_EXENCION = 100_000
# AC228: lo que cuesta en unidades el trabajo lineal en las contenedoras de una aparición (`_Contenedoras.de` y la
# clave y los recorridos de `_a_la_vez`): una unidad por tantas contenedoras, y por tantas letras de su tramo.
_CONTENEDORAS_POR_UNIDAD, _LETRAS_POR_UNIDAD = 8, 512


class _TopeAgotado(Exception):
    """AC224 (enmienda 47): se agotó el presupuesto de trabajo de la exención; lo que se calculaba queda a medias."""


class _Presupuesto:
    """AC224 (enmienda 47): el trabajo de la exención (`_exenta`) de una llamada a `_nombres_en`, a `_datos_en` (las
    sociedades) o a `_nombres_propios` (el conjunto de nombres), acotado por construcción: un contador, compartido por
    todas sus pasadas y apariciones, de unidades abstractas (no hay reloj: es determinista) que gastan los cálculos que
    no son lineales en el texto: cada estado por posición de un `_Recorrido` (al construirlo), cada par (estado de la
    aparición contenida, estado de la contenedora) por posición de su `con`, en el respaldo de `_a_la_vez`, cada estado
    vivo por aparición y por posición de `_hay_lectura`, y (AC228) lo que cuesta crear y recorrer las contenedoras de
    cada aparición: el recuento de `_Contenedoras.de` (una unidad por `_CONTENEDORAS_POR_UNIDAD`) y la clave de
    `_a_la_vez` (sus apariciones y su tramo). Lo que cuesta cero (`_Contenedoras.propia`, un tramo sin puntos de
    partición, una aparición sin contenedoras posibles) no gasta. Pasado `_TOPE_EXENCION`, `gasta` interrumpe lo que se
    calcula (`_TopeAgotado`) y todo cálculo nuevo de la llamada: `_exenta` no exime lo que no pudo decidir (fallo
    cerrado: solo añade denuncias, nunca exime) y nada a medias entra en la memoria. AC226: se consume en el orden
    canónico de las apariciones (`_clave_aparicion`), así que lo que agota es función del texto y las filas."""

    def __init__(self):
        self.gastado, self.tope, self.agotado = 0, _TOPE_EXENCION, False

    def gasta(self, unidades):
        if self.agotado or self.gastado + unidades > self.tope:
            self.agotado = True
            raise _TopeAgotado
        self.gastado += unidades


class _Contenedoras:
    """AC228: las apariciones de las entidades `propias` (con `filtro`, las que lo cumplen) que contienen a cada
    aparición del texto, sin materializar la relación entera: es, en el peor caso, cuadrática (n apariciones dentro de
    n propias solapadas). `propia` responde sin recorrer nada si la aparición es una de ellas (el mismo comienzo y el
    mismo candidato: la misma aparición), y `de` entrega las que la contienen, entre las que empiezan como
    mucho el tramo más largo de ellas antes (por bisección), cobrando su recuento en el `presupuesto` ANTES de crear
    la lista (`_CONTENEDORAS_POR_UNIDAD` por unidad): agotado, no se calcula. Están en el orden canónico de
    `_clave_aparicion` (AC226). AC232: «el mismo candidato» es el mismo CONTENIDO (`_clave_candidato`), no el mismo
    objeto: una expulsión de la caché de `_candidato` no cambia qué es propio ni, con él, lo que se cobra."""

    def __init__(self, apariciones, propias, filtro=None):
        self.suyas = sorted((a for a in apariciones if a.eid in propias and (filtro is None or filtro(a))),
                            key=_clave_aparicion)
        self.inicios = [a.inicio for a in self.suyas]
        self.tramo = max((a.fin - a.inicio for a in self.suyas), default=0)
        self.enteras = {}   # (comienzo, contenido del candidato) → el mayor fin: la propia con ese comienzo y candidato
        for a in self.suyas:
            clave = a.inicio, _clave_candidato(a.candidato)
            self.enteras[clave] = max(self.enteras.get(clave, a.fin), a.fin)

    def propia(self, aparicion) -> bool:
        """¿Es `aparicion` una de las propias, salvo el nombre o la entidad (el mismo candidato desde la misma
        pieza)?"""
        return self.enteras.get((aparicion.inicio, _clave_candidato(aparicion.candidato)), -1) >= aparicion.fin

    def de(self, aparicion, presupuesto=None) -> list:
        """Las propias cuyo tramo contiene el de `aparicion`; con `presupuesto`, cobra su recuento antes de crearlas."""
        desde = bisect.bisect_left(self.inicios, aparicion.fin - self.tramo)
        hasta = bisect.bisect_right(self.inicios, aparicion.inicio)
        if hasta <= desde:
            return []
        if presupuesto is not None:
            presupuesto.gasta(1 + (hasta - desde) // _CONTENEDORAS_POR_UNIDAD)
        return [s for s in self.suyas[desde:hasta] if aparicion.fin <= s.fin]


class _Recorrido:
    """AC222: los estados por los que pasa una aparición sola (`_Aparicion`, `_hay_lectura` con un solo candidato) en
    cada posición de su tramo, hacia adelante (los alcanzables desde el comienzo) y hacia atrás (de ellos, los que
    alcanzan el final con la aparición, `bien`, o sin ella, `mal`), una vez por aparición. Con ellos, que otra aparición
    que cae dentro esté a la vez en una lectura (`con`) o que haya una lectura con ella y sin esta (`con(…, sin=True)`)
    se decide recorriendo solo el tramo de la de dentro, con los dos candidatos a la vez y los estados de este al
    empezar y al acabar: el coste de la aparición contenida, no el del candidato entero por cada una."""

    def __init__(self, fichas, aparicion, presupuesto=None):
        self.fichas, self.candidato = fichas, aparicion.candidato
        texto = fichas.cadena
        self.inicio = texto.inicios[aparicion.inicio]
        self.hasta = self.inicio + len(aparicion.candidato.cadena.letras)
        self.antes = [{_ANTES}]   # antes[k]: los estados antes de decidir la posición inicio + k
        self.pasos = []   # pasos[k]: {estado: los que le siguen tras decidir la posición y alinear su letra}
        for o in range(self.inicio, self.hasta + 1):
            if presupuesto is not None:
                presupuesto.gasta(len(self.antes[-1]))
            paso = {e: self.siguientes(e, o) for e in self.antes[-1]}
            self.pasos.append(paso)
            self.antes.append({n for ns in paso.values() for n in ns})
        testigos = self.testigos_de_corte(aparicion)
        self.corte_antes = min((hi for _, hi in testigos), default=None)   # el testigo que acaba antes
        self.corte_despues = max((lo for lo, _ in testigos), default=None)   # y el que empieza después
        unidos = self.testigos_de_union(aparicion)
        self.union_antes, self.union_despues = min(unidos, default=None), max(unidos, default=None)
        self.bien = [None] * len(self.antes)
        self.mal = [None] * len(self.antes)
        self.bien[-1] = {e for e in self.antes[-1] if e == _HECHA}
        self.mal[-1] = self.antes[-1] - self.bien[-1]
        for k in range(len(self.pasos) - 1, -1, -1):
            self.bien[k] = {e for e, ns in self.pasos[k].items() if any(n in self.bien[k + 1] for n in ns)}
            self.mal[k] = {e for e, ns in self.pasos[k].items() if any(n in self.mal[k + 1] for n in ns)}

    def siguientes(self, estado, o, corte=None) -> list:
        """Los estados que siguen a `estado` tras decidir la posición `o` (cada corte que el texto deja) y alinear su
        letra; con `corte`, el de esa decisión."""
        texto = self.fichas.cadena
        cortes = (False, True) if texto.tipos[o] == 'J' else (texto.tipos[o] == 'H',)
        out = []
        for corta in cortes if corte is None else (corte,):
            nuevo = _frontera(estado, self.inicio, self.candidato, o, corta)
            out.append(_paso(nuevo, self.inicio, self.candidato, o, texto.letras[o]) if o < self.hasta else nuevo)
        return out

    def testigos_de_corte(self, aparicion) -> list[tuple[int, int]]:
        """Los límites entre piezas (primero y último, de la pieza `b` es el límite `b`) que la lectura descarta a este
        candidato solo con cortarlos: uno donde el candidato está dentro de una pieza suya, y los dos de una pieza de
        solo cifras con una cifra leída como letra (sola en una palabra no vale)."""
        texto, piezas, unibles = self.fichas.cadena, self.fichas.piezas, self.fichas.unibles
        cand, primera, ultima = self.candidato.cadena, aparicion.inicio, aparicion.fin
        testigos = [(b, b) for b in range(primera + 1, ultima)
                    if unibles[b - 1] and cand.tipos[texto.inicios[b] - self.inicio] is None]
        for g in range(primera, ultima):
            letras = texto.letras[texto.inicios[g]:texto.inicios[g] + len(piezas[g])]
            con_vecina = (g > primera and unibles[g - 1]) or (g + 1 < ultima and unibles[g])
            donde = texto.inicios[g] - self.inicio
            if piezas[g].isdigit() and con_vecina and letras != cand.letras[donde:donde + len(piezas[g])]:
                testigos.append((g, g + 1))
        return testigos

    def testigos_de_union(self, aparicion) -> list[int]:
        """Los límites entre piezas donde el candidato tiene un límite duro suyo y el texto un punto de partición: la
        lectura que los une descarta a este candidato (que ahí corta)."""
        texto, unibles, cand = self.fichas.cadena, self.fichas.unibles, self.candidato.cadena
        return [b for b in range(aparicion.inicio + 1, aparicion.fin)
                if unibles[b - 1] and cand.tipos[texto.inicios[b] - self.inicio] == 'H']

    def une_fuera(self, aparicion) -> bool:
        """¿Hay un testigo de unión (`testigos_de_union`) estrictamente fuera de los límites de `aparicion`, que cae
        dentro? Unir ahí no toca al de dentro (que corta en los suyos), y con ello este candidato no está."""
        return ((self.union_antes is not None and self.union_antes < aparicion.inicio)
                or (self.union_despues is not None and self.union_despues > aparicion.fin))

    def corta_fuera(self, aparicion) -> bool:
        """¿Hay un testigo de corte (`testigos_de_corte`) fuera del tramo de `aparicion`, que cae dentro? El corte no
        toca al de dentro, y con él este candidato no está: sin recorrer."""
        return ((self.corte_antes is not None and self.corte_antes <= aparicion.inicio)
                or (self.corte_despues is not None and self.corte_despues >= aparicion.fin))

    def con(self, aparicion, sin=False, presupuesto=None) -> bool:
        """¿Hay una lectura en que está `aparicion` (que cae dentro del tramo) y está este candidato (con `sin`, y no
        está)? Los pares (estado de ella, estado de este) a lo largo del tramo de ella, desde los estados de este que
        alcanza el comienzo, y al final los de este que alcanzan el final bien (o mal)."""
        texto, candidato = self.fichas.cadena, aparicion.candidato
        desde = texto.inicios[aparicion.inicio]
        hasta = desde + len(candidato.cadena.letras)
        pares = {(_ANTES, e) for e in self.antes[desde - self.inicio]}
        for o in range(desde, hasta + 1):
            if presupuesto is not None:
                presupuesto.gasta(len(pares))
            cortes = (False, True) if texto.tipos[o] == 'J' else (texto.tipos[o] == 'H',)
            nuevos = set()
            for suyo, otro in pares:
                for corta in cortes:
                    mio = _frontera(suyo, desde, candidato, o, corta)
                    if mio == _MUERTA:
                        continue
                    nuevos.update((_paso(mio, desde, candidato, o, texto.letras[o]) if o < hasta else mio, n)
                                  for n in self.siguientes(otro, o, corta))
            pares = nuevos
        buscados = self.mal if sin else self.bien
        return any(suyo == _HECHA and otro in buscados[hasta + 1 - self.inicio] for suyo, otro in pares)


def _a_la_vez(fichas, positivas, negativas=(), memoria=None, presupuesto=None) -> bool:
    """`_hay_lectura` de unas apariciones (`_Aparicion`) del texto. Sin puntos de partición entre la primera pieza y la
    última, la lectura del tramo es una sola, y en ella están todas las halladas. Depende solo de los candidatos, su
    disposición y las letras y los cortes del tramo: `memoria` lo guarda por ellos (un texto que se repite no lo
    repite; AC232: por el CONTENIDO de los candidatos, `_clave_candidato`, no por su identidad, que la caché de
    `_candidato` cambia al expulsar una entrada: lo que se cobra no depende de ella). AC222: la que cae dentro de otra
    (`con` de su `_Recorrido`, una vez por aparición) a la vez que ella, o sin
    ella, se decide recorriendo solo su tramo. Sin varias a la vez: si una de ellas está siempre que está la de dentro,
    no hay lectura sin todas; si cada una tiene algo que se descarta con solo cortar (`corta_fuera`) o con solo unir
    (`une_fuera`) fuera del tramo de la de dentro, la hay; lo demás, la lectura entera. AC224 (enmienda 47): con
    `presupuesto`, lo que calcula de nuevo lo gasta (`_TopeAgotado` al agotarse, sin guardar nada a medias)."""
    todas = [*positivas, *negativas]
    primera, ultima = min(a.inicio for a in todas), max(a.fin for a in todas)
    if fichas.unidas[min(ultima, len(fichas.unibles))] == fichas.unidas[max(primera - 1, 0)]:
        return not negativas
    texto = fichas.cadena
    desde = texto.inicios[primera]
    hasta = texto.inicios[ultima] if ultima < len(texto.inicios) else len(texto.letras)
    if presupuesto is not None:   # AC228: la clave (las apariciones y el tramo) y los recorridos de las contenedoras
        presupuesto.gasta(len(todas) // _CONTENEDORAS_POR_UNIDAD + (hasta - desde) // _LETRAS_POR_UNIDAD)
    clave = (tuple((_clave_candidato(a.candidato), texto.inicios[a.inicio] - desde) for a in positivas),
             tuple((_clave_candidato(a.candidato), texto.inicios[a.inicio] - desde) for a in negativas),
             texto.letras[desde:hasta], texto.tipos[desde:hasta + 1])
    if memoria is None or clave not in memoria:
        if presupuesto is not None:
            presupuesto.gasta(0)   # agotado, ningún cálculo nuevo
        resultado = None
        if memoria is not None and ((len(positivas) == 2 and not negativas) or (len(positivas) == 1 and negativas)):
            dentro = positivas[0]
            fuera = [positivas[1]] if not negativas else negativas
            if all(f.inicio <= dentro.inicio and dentro.fin <= f.fin for f in fuera):   # el tramo de la de dentro
                recorridos = []
                for f in fuera:   # (por las fichas: el recorrido las guarda, así que mientras esté su id es de ellas)
                    clave_recorrido = ('recorrido', id(fichas), f.inicio, _clave_candidato(f.candidato))
                    if clave_recorrido not in memoria:
                        memoria[clave_recorrido] = _Recorrido(fichas, f, presupuesto)
                    recorridos.append(memoria[clave_recorrido])
                if not negativas:
                    resultado = recorridos[0].con(dentro, presupuesto=presupuesto)
                elif len(recorridos) == 1:
                    resultado = recorridos[0].con(dentro, sin=True, presupuesto=presupuesto)
                elif any(not r.con(dentro, sin=True, presupuesto=presupuesto) for r in recorridos):
                    resultado = False   # con la de dentro está siempre alguna de ellas
                elif (all(r.corta_fuera(dentro) for r in recorridos)
                      or all(r.une_fuera(dentro) for r in recorridos)):
                    resultado = True   # un corte o una unión fuera de ella descarta a cada una: hay lectura sin ninguna
        if resultado is None:
            resultado = _hay_lectura(fichas, [(texto.inicios[a.inicio], a.candidato) for a in positivas],
                                     [(texto.inicios[a.inicio], a.candidato) for a in negativas], presupuesto)
        if memoria is None:
            return resultado
        memoria[clave] = resultado
    return memoria[clave]


def _exenta(fichas, aparicion, contenedoras, memoria=None, presupuesto=None) -> bool:
    """AC78(b)/AC109/AC113: ¿cae `aparicion` dentro de una de las de los extremos de la fila (`contenedoras`, una
    `_Contenedoras`: las que contienen su tramo) en TODAS las lecturas del texto en que está? Sí si es una de ellas o si
    no hay una lectura en la que esté ella y no esté ninguna de ellas (`_a_la_vez`): lo que exime, solo dentro de la
    misma lectura. AC224 (enmienda 47): si el `presupuesto` se agota antes de decidirlo, no está exenta (se denuncia);
    nunca al revés. AC228: la propia no cuesta, y las contenedoras se cobran antes de crearlas."""
    if contenedoras.propia(aparicion):
        return True
    try:
        suyas = contenedoras.de(aparicion, presupuesto)
        return bool(suyas) and not _a_la_vez(fichas, [aparicion], suyas, memoria, presupuesto)
    except _TopeAgotado:
        return False


def _nombres_en(texto, indice, propias=frozenset(), calladas=frozenset()) -> set[str]:
    """Los nombres del índice que están enteros, palabra a palabra y sin caja ni acentos, en alguna lectura de un texto
    (`_apariciones_indice`: cada punto de partición del texto y del nombre unido o separado, AC104/AC109/AC113) y con
    cualquier forma de cada palabra (AC105(b)/AC107). Salvo, con `propias` (AC78(b): los extremos de la fila de una
    cita), los de esas entidades y los que caen dentro de uno de ellos en todas las lecturas en que están (`_exenta`):
    un nombre que es el de un extremo es el suyo, aunque otra persona lo comparta o lleve parte de él. Los de
    `calladas` ([(entidad, nombre)]) no se cuentan, pero tampoco eximen (AC134: el alias inválido de un seudónimo de la
    fila que la misma ejecución ya denuncia). AC168: también a través de sus comillas («Cen«ti»nela Civil» nombra a
    Centinela Civil); AC202/AC208: en cada pasada (`_pasadas`: cada lectura del texto con el índice base y la de AC195
    con el completo), sumadas; lo que otra pasada halla cuenta salvo si la propia lectura lo exime (`_exenta`: en ella
    cae dentro de uno de los extremos de la fila) y, además, cae en un tramo que la pasada base (el texto sin leer a
    través de comillas, con el índice base) exime y, si la lectura no es en él la de la base (una comilla del texto), la
    base halla esa misma entidad y la exime (AC211: una posición no es una identidad; AC215: ni una exención de la
    base borra lo que otra lectura ve fuera del extremo propio). AC224/AC226/AC228: el trabajo de la exención de toda
    la llamada (todas sus pasadas) gasta un solo `_Presupuesto`, en el orden canónico de las apariciones
    (`_apariciones_indice`) y con las contenedoras de cada una cobradas al pedirlas (`_Contenedoras`)."""
    (lectura_base, (base,)), *otras = _pasadas(texto, indice)
    fichas, memoria, presupuesto = _fichas(lectura_base), {}, _Presupuesto()
    posiciones = _posiciones(lectura_base)
    out, eximentes = set(), []
    apariciones = _apariciones_indice(fichas, base)
    contenedoras = _Contenedoras(apariciones, propias)
    for a in apariciones:
        if (a.eid, a.nombre) in calladas:
            continue
        if not _exenta(fichas, a, contenedoras, memoria, presupuesto):
            out.add(a.nombre)
        elif posiciones is not None:   # AC208/AC211: lo que exime, de la pasada base, con su identidad y su tramo
            eximentes.append((a.eid, *_tramo_de(posiciones, a)))
    exento = _exentos_de(eximentes)
    for lectura, (indice_lectura,) in otras:
        fichas = _fichas(lectura)
        posiciones = _posiciones(lectura)

        def eximida(a):
            inicio, fin = _tramo_de(posiciones, a)
            return exento(a.eid, inicio, fin, lectura_base[inicio:fin] == lectura[inicio:fin])
        apariciones = _apariciones_indice(fichas, indice_lectura)
        contenedoras, memoria = _Contenedoras(apariciones, propias), {}
        out |= {a.nombre for a in apariciones if (a.eid, a.nombre) not in calladas
                and not (posiciones is not None and eximida(a)
                         and _exenta(fichas, a, contenedoras, memoria, presupuesto))}
    return out


# AC133: un `entity_id` entero entre separadores, sin caja: el de `_patron_id` de cualquier entidad, en un recorrido.
_ID_EN_TEXTO = re.compile(r'(?<![A-Za-z0-9_-])(?:Person|Company|Organization):[A-Za-z0-9-]+(?![A-Za-z0-9_-])', re.I)


def _patron_id(sid):
    """El `entity_id` entero de un seudónimo, entre separadores y sin caja (AC73: «PERSON:…» también lo es)."""
    return re.compile(rf'(?<![A-Za-z0-9_-])(?i:{re.escape(sid)})(?![A-Za-z0-9_-])')


def _decodificar_url(url) -> str:
    """Una URL tal como la lee el detector (AC73): `unquote` hasta un punto fijo, como mucho tres vueltas, con el `+` de
    la query como espacio. Un id (`%3A`) o «seudónimo» codificados no se esconden."""
    texto = str(url or '')
    for _ in range(3):
        sin_fragmento, almohadilla, fragmento = texto.partition('#')
        ruta, interrogacion, query = sin_fragmento.partition('?')
        nuevo = (urllib.parse.unquote(ruta) + interrogacion + urllib.parse.unquote_plus(query) + almohadilla
                 + urllib.parse.unquote(fragmento))
        if nuevo == texto:
            break
        texto = nuevo
    return texto


def _ventana(fichas, nombre, inicio, previo, orden, despues, largo) -> int | None:
    """AC66/AC74/AC109/AC112: la primera palabra «seudónimo» (su número en `orden`, {(inicio, fin): número}) a la que
    alcanza una ventana del nombre de pila `nombre` que empieza en la pieza `inicio`, en alguna lectura del texto: sus
    palabras, cada una en una palabra que la tiene por forma, en cualquier orden y seguidas salvo marcas o tratamientos
    intercalados (`_sin_nombre`, AC135: también «seud0nimo»). La alcanzan `previo` (la primera que acaba como mucho seis
    palabras antes de su inicio), las que contiene y `despues[fin]` (la primera que empieza como mucho cuatro después de
    su fin); None si no alcanza a ninguna o no hay ventana. De palabra en palabra (`_palabras_desde`, hasta `largo`
    letras), por orden de pieza y con el menor número de cada estado: sin enumerar lecturas ni el producto de las
    formas."""
    ninguna = len(orden)
    completo = (1 << len(nombre)) - 1
    mejor = {(inicio, 0): ninguna if previo is None else previo}
    pendientes, vistos, primera = [(inicio, 0)], set(), ninguna
    while pendientes:
        estado = heapq.heappop(pendientes)
        if estado in vistos:
            continue
        vistos.add(estado)
        pieza, hechas = estado
        if hechas == completo:
            primera = min(primera, mejor[estado], ninguna if despues[pieza] is None else despues[pieza])
            continue
        if pieza == len(fichas.piezas):
            continue
        for fin, palabra in _palabras_desde(fichas, pieza, largo):
            numero = min(mejor[estado], orden.get((pieza, fin), ninguna))
            if _sin_nombre(palabra):
                siguientes = [hechas] if hechas else []
            else:
                siguientes = [hechas | 1 << j for j, p in enumerate(nombre)
                              if not hechas >> j & 1 and p not in _SIN_NOMBRE and _es_forma(palabra, p)]
            for siguiente in siguientes:
                if numero < mejor.get((fin, siguiente), ninguna + 1):
                    mejor[(fin, siguiente)] = numero
                    heapq.heappush(pendientes, (fin, siguiente))
    return None if primera == ninguna else primera


def _mencion(fichas, por_palabra, largo, por_esqueleto) -> str | None:
    """AC66/AC74/AC107/AC109/AC112: el seudónimo de una mención explícita del texto (`fichas`), o None: una ventana de
    su nombre de pila (`_ventana`) que empieza en una palabra que puede serlo (sin `_SIN_NOMBRE`) como mucho seis
    palabras después de una palabra «seudónimo», o que acaba como mucho cuatro antes o la contiene. El de la primera
    palabra «seudónimo» que tiene alguna y, de sus ventanas, el de la que empieza antes (el orden de
    `nombra_seudonimo`). En una pasada, sin recorrer las ventanas por cada palabra «seudónimo»: de cada pieza, la
    primera que acaba a la distancia de una ventana que empieza en ella (`antes`) y la primera que empieza a la de una
    que acaba en ella (`despues`), con los separadores que no son puntos de partición acumulados (`duros`). Las
    palabras de los nombres, por su esqueleto (`por_esqueleto`) y por posición (`_es_forma`), la misma primero."""
    piezas, unibles = fichas
    seudonimos = sorted(_apariciones(fichas, _SEUDONIMO))
    if not seudonimos:
        return None
    duros = list(itertools.accumulate((not unible for unible in unibles), initial=0))
    por_fin = sorted(range(len(seudonimos)), key=lambda k: seudonimos[k][1])
    antes, despues, acabadas, j, k = [], [], [], 0, 0
    for pieza in range(len(piezas) + 1):
        while j < len(por_fin) and seudonimos[por_fin[j]][1] <= pieza:
            heapq.heappush(acabadas, (por_fin[j], seudonimos[por_fin[j]][1]))
            j += 1
        while acabadas and _palabras_entre(duros, acabadas[0][1], pieza) > 5:
            heapq.heappop(acabadas)   # una que acaba más lejos no vuelve a estar a la distancia
        antes.append(acabadas[0][0] if acabadas else None)
        while k < len(seudonimos) and seudonimos[k][0] < pieza:
            k += 1
        despues.append(k if k < len(seudonimos) and _palabras_entre(duros, pieza, seudonimos[k][0]) <= 3 else None)
    orden = {tramo: k for k, tramo in enumerate(seudonimos)}
    ultima = max(fin for _, fin in seudonimos)
    mejor = None
    for inicio in range(len(piezas)):
        if antes[inicio] is None and inicio >= ultima:
            break
        candidatos = dict.fromkeys(x for _, palabra in _palabras_desde(fichas, inicio, largo)
                                   for forma in sorted(por_esqueleto.get(_esqueleto(palabra), ()),
                                                       key=lambda clave, p=palabra: clave != p)
                                   if forma not in _SIN_NOMBRE and _es_forma(palabra, forma)
                                   for x in por_palabra[forma])
        for rango, (nombre, sid) in enumerate(candidatos):
            primera = _ventana(fichas, nombre, inicio, antes[inicio], orden, despues, largo)
            if primera is not None and (mejor is None or (primera, inicio, rango) < mejor[0]):
                mejor = ((primera, inicio, rango), sid)
    return mejor[1] if mejor else None


@functools.lru_cache(maxsize=16)
def _detector(ids: frozenset, modo: str):
    """texto → el seudónimo del roster (`ids`) que nombra, o None: ver `nombra_seudonimo`. `modo`: `prosa` (id, slug y
    mención explícita), `url` (id y mención) o `mencion` (solo la mención explícita). El id y el slug, sobre el texto
    normalizado; la mención, en cualquiera de sus lecturas (`_fichas`, AC104/AC105(b)/AC109), por posición
    (`_mencion`), también a través de sus comillas (`_lecturas_texto`, como el índice civil, AC168: «el seud«ó»nimo
    Benito»)."""
    patrones = [] if modo == 'mencion' else [
        (sid, re.compile(_patron_id(sid).pattern + (
            rf'|(?<![A-Za-z0-9_:-]){re.escape(sid.split(":", 1)[-1])}(?![A-Za-z0-9_-])' if modo == 'prosa' else '')))
        for sid in sorted(ids)]
    por_palabra = {}   # cada palabra de un nombre de pila → (nombre, seudónimo): la ventana del nombre la contiene
    for sid in sorted(ids):
        nombre = _nombre(sid)
        for palabra in dict.fromkeys(nombre):
            por_palabra.setdefault(palabra, []).append((nombre, sid))
    largo = max(map(len, (*por_palabra, *_SIN_NOMBRE)))
    por_esqueleto = {}
    for palabra in por_palabra:
        por_esqueleto.setdefault(_esqueleto(palabra), []).append(palabra)

    def nombra(texto):
        legible = _normalizar(texto)
        return (next((sid for sid, patron in patrones if patron.search(legible)), None)
                or next((sid for lectura in _lecturas_texto(texto)
                         if (sid := _mencion(_fichas(lectura), por_palabra, largo, por_esqueleto))), None))
    return nombra


def nombra_seudonimo(texto, ids, url=False) -> str | None:
    """AC65/AC66: el seudónimo del roster (`ids`) que nombra un texto, o None, sobre el texto normalizado (AC81:
    `_normalizar`). (i) Su `entity_id` entero, sin caja, o su slug entre separadores; el slug, con la caja de los ids:
    el nombre de pila escrito en la prosa («X …») no es su slug. Una URL (`url`) se lee decodificada (AC73) y en ella
    solo cuenta el `entity_id`: un nombre de pila es un segmento de ruta corriente (AC72). (ii) Una mención explícita:
    la palabra «seudónimo» (`_SEUDONIMO`; sin acentos ni caja, `_palabras`) seguida a ≤ 6 palabras de su nombre de pila
    entero, en cualquier orden (`_es_nombre`) y sin contar marcas ni tratamientos (`_SIN_NOMBRE`), con o sin comillas o
    paréntesis; el nombre seguido a ≤ 4 palabras de «seudónimo»; o la marca intercalada entre sus palabras (AC74). Que
    el nombre sea también el de la fila que lo escribe no la exime: «el seudónimo «X»» nombra a un seudónimo aunque el
    civil se llame X. AC104/AC105(b)/AC109: la mención, en cualquier lectura del texto (`_fichas`): con cada punto de
    partición unido o separado y con los homógrafos griegos y cirílicos como letras latinas; por posición (AC112)."""
    return _detector(frozenset(ids), 'url' if url else 'prosa')(_decodificar_url(texto) if url else texto)


def nombra_en(textos, ids) -> str | None:
    """El primer seudónimo que nombran unos textos [(texto, es_url)], o None."""
    return next((s for s in (nombra_seudonimo(t, ids, url=u) for t, u in textos) if s), None)


# AC150: las fórmulas cerradas con que un texto identifica a un seudónimo con un civil, como palabras plegadas (cada
# una por posición, `_es_forma`): «seudónimo (de)», «alias», «es decir», «o sea», «que es», «conocido como»,
# «identificado como» y «también llamado» (en género y número). «A saber» no: enumera los miembros de un conjunto («los
# directivos de X, a saber, don Y»), la asociación que la arista ya publica, no la identidad de dos nombres.
_FORMULAS_IDENTIDAD = tuple(sorted({
    *((s,) for s in _SEUDONIMO), ('alias',), ('es', 'decir'), ('o', 'sea'), ('que', 'es'),
    *((f'{p}{g}', 'como') for p in ('conocid', 'identificad') for g in ('o', 'a', 'os', 'as')),
    *(('tambien', f'llamad{g}') for g in ('o', 'a', 'os', 'as'))}))
_PALABRAS_FORMULA = frozenset(p for f in _FORMULAS_IDENTIDAD for p in f)
# AC160: en una fila civil, cuántas palabras caben, como mucho, entre la mención del seudónimo y el nombre civil (las
# de la fórmula incluidas: «X, es decir, el empresario Y» lleva cuatro), y lo que puede haber entre dos piezas de un
# mismo nombre propio: espacios y un guion.
_ADYACENTE = 6
_JUNTO = re.compile(r'\s*(?:[-\u2010-\u2015]\s*)?')


@functools.lru_cache(maxsize=1)
def _indice_formulas() -> dict:
    """AC166: las fórmulas de identidad (`_FORMULAS_IDENTIDAD`) como un índice de nombres (`_indice_nombres`): se buscan
    con su misma maquinaria, en todas las lecturas del texto: cada punto de partición unido o separado («al-\\nias»,
    «es de-\\ncir»), los homógrafos de otro alfabeto como letras latinas y cada dígito de una palabra con letras como él
    mismo o una de sus letras homógrafas («a1ias»)."""
    return _indice_nombres(((' '.join(f), ' '.join(f)) for f in _FORMULAS_IDENTIDAD), minimo=1)


def _formulas_en(fichas) -> list[tuple[int, int]]:
    """AC150/AC166: [(inicio, fin)] de las piezas de cada fórmula de identidad del texto (`_indice_formulas`), en
    cualquiera de sus lecturas (`_apariciones_indice`)."""
    return [(a.inicio, a.fin) for a in _apariciones_indice(fichas, _indice_formulas())]


def lleva_formula(texto) -> bool:
    """AC150/AC160: ¿lleva el texto, en alguna lectura (`_fichas`; AC168, también a través de sus comillas,
    `_lecturas_texto`, las de `identifica_explicitamente`: «al«i»as»), una fórmula de identidad? Sin ella, ninguna
    identificación explícita: quien la busca no necesita los índices."""
    return any(_formulas_en(_fichas(lectura)) for lectura in _lecturas_texto(texto))


@functools.lru_cache(maxsize=16)
def _indice_pilas(ids) -> dict:
    """AC150: el índice (`_indice_nombres`) de lo que nombra a un seudónimo del roster (`ids`, un frozenset): su id y
    su nombre de pila, con sus palabras en cualquier orden (de cuatro como mucho; más, en el del slug)."""
    def ordenes(nombre):
        return itertools.permutations(nombre) if len(nombre) <= 4 else [nombre]
    return _indice_nombres(((sid, n) for sid in sorted(ids)
                            for n in sorted({sid, *(' '.join(o) for o in ordenes(_nombre(sid)))} - {''})), minimo=1)


def identifica_explicitamente(texto, ids, civiles, sociedades=None, nombres=None, entidades=(),
                              acotada=False) -> bool:
    """AC150: ¿identifica el texto a un seudónimo del roster (`ids`: su nombre de pila, en cualquier orden, o su id;
    `_indice_pilas`) con un civil, con una fórmula cerrada (`_FORMULAS_IDENTIDAD`) entre los dos? El civil: una persona
    del índice civil (`civiles`; un alias permitido de un seudónimo es suyo), una sociedad del índice de sociedades
    (`sociedades`, con su núcleo, AC149) o con forma jurídica (`_formas_en`), un extremo civil de la fila (`entidades`)
    por sus nombres, alias o núcleo (`nombres`), o el id de una entidad fuera del roster. En cualquier lectura del texto
    (`_fichas`) y antes de cualquier exención: ni la prueba de la cita, ni el extremo propio, ni el tramo probado la
    salvan. Una mención del seudónimo dentro de la de un civil («David» en «David Gómez») no es la suya; AC167: salvo
    si ese nombre es uno que identifica (`nombre_identificador`: «Centinela Civil, alias Benito», el alias de un civil
    en el índice), que no exime nada, ni como nombre completo de (i). AC160 (`acotada`, la de una fila civil): solo el
    nombre de pila suelto (`_sueltos`, frente a los nombres completos de su fila y de los civiles que el texto nombra)
    y con la fórmula adyacente: como mucho seis palabras entre los dos nombres (`_ADYACENTE`, las de la fórmula
    incluidas). Sin recorrer cada trío de mención, civil y fórmula (`_formula_entre`). AC168: en la lectura del texto a
    través de sus comillas (`_comillas_partidas`: «Cen«ti»nela Civil» es «Centinela Civil»). AC176: las piezas de una
    fórmula, en cualquiera de sus lecturas («AL-IAS», «AL«I»AS»), no son de un nombre propio ni de un civil que exima:
    «Centinela Civil, AL-IAS BENITO» identifica como «Centinela Civil, ALIAS BENITO». AC202: en cada pasada
    (`_pasadas`: cada lectura del texto con los índices base y la de AC195 con los completos), con sus propias
    exenciones; basta una. AC205: los nombres de los extremos civiles, también a través de sus comillas, con el
    filtro y el núcleo en esa lectura (`_anadidas`), solo en los índices completos."""
    if not ids or not lleva_formula(texto):
        return False
    del_roster = {i.lower() for i in ids}
    propios = [(e, n) for e in sorted(_extremos_civiles(entidades, ids)) for n in sorted((nombres or {}).get(e, ()))]
    suyos = [(e, n) for e in sorted(_extremos_civiles(entidades, ids))
             for n in sorted(nombres_con_nucleo(e, (nombres or {}).get(e, ())))
             if _nombre_cuenta(n, 2 if e.startswith('Person:') else 1)]
    suyos += [(i, i) for i in dict.fromkeys(_ID_ENTIDAD.findall(_normalizar(texto))) if i.lower() not in del_roster]
    indice_suyos = _indice_nombres(suyos, minimo=1, anadidas=_anadidas(propios))
    (lectura_base, base), *otras = _pasadas(texto, civiles, sociedades or {}, indice_suyos)
    eximentes = _contenedores_de(lectura_base, ids, base)   # AC208: solo de la pasada base
    completos = [n for _, n in suyos]
    return any(_identifica_en(texto, lectura, ids, idx, completos, acotada, eximentes, lectura is lectura_base)
               for lectura, idx in ((lectura_base, base), *otras))


def _contenedores_de(texto, ids, indices) -> tuple:
    """AC208/AC211: de la pasada base de `identifica_explicitamente` (su lectura sin comillas, con los índices base),
    sus contenedores eximentes ((inicio, fin) en piezas, las menciones de un civil que no identifica y las
    denominaciones con forma jurídica, fuera de una fórmula), las menciones del seudónimo que caen dentro de uno de
    ellos, con su identidad y su tramo del texto (con `_posiciones`; ninguna sin ellas) como el predicado
    `_exentos_de` (que, para una lectura igual a la base, vale con cualquier tramo de un contenedor), los nombres de
    esos civiles (los que hacen que un nombre de pila no sea suelto), la lectura base y, AC221, la identidad de cada
    contenedor (`_identidad_de_contenedor`: su entidad y su tramo en el texto; ninguna sin posiciones)."""
    fichas = _fichas(texto)
    formulas = _formulas_en(fichas)
    en_formula, roster = _piezas_de(formulas), frozenset(ids)
    nombrados = [a for indice in indices for a in _apariciones_indice(fichas, indice) if a.eid not in ids]
    validos = [a for a in nombrados if not _es_identificador(a.nombre, roster)
               and not any(g in en_formula for g in range(a.inicio, a.fin))]
    piezas = sorted([(a.inicio, a.fin) for a in validos]
                    + [(i, f) for i, f, _ in _formas_en(fichas) if not any(g in en_formula for g in range(i, f))])
    posiciones, contenida = _posiciones(texto), _contenida_en(piezas)
    pilas = _apariciones_indice(fichas, _indice_pilas(roster))
    exentas = [(a.eid, *_tramo_de(posiciones, a)) for a in pilas if contenida(a)] if posiciones is not None else []
    tramos = [(posiciones[i][0], posiciones[f - 1][1]) for i, f in piezas] if posiciones is not None else []
    identidades = frozenset()
    if posiciones is not None:
        identidades = frozenset(_identidad_de_contenedor(posiciones, c.eid, c.inicio, c.fin) for c in validos) \
            | frozenset(_identidad_de_contenedor(posiciones, '', i, f) for i, f, _ in _formas_en(fichas)
                        if not any(g in en_formula for g in range(i, f)))
    return piezas, _exentos_de(exentas, tramos), [a.nombre for a in validos], texto, identidades


def _identidad_de_contenedor(posiciones, eid, inicio, fin) -> tuple:
    """AC221: la identidad de un contenedor eximente, la misma en cualquier lectura de un texto: su entidad (vacía en
    una denominación con forma jurídica) y su tramo en el texto original (las lecturas son del mismo largo). Una
    posición no es una identidad, y un nombre que una lectura invalida no lo sustituye otro que la base no reconoce."""
    return eid, posiciones[inicio][0], posiciones[fin - 1][1]


def _contenida_en(piezas):
    """El predicado de si una aparición cae dentro de alguna de las `piezas` ([(inicio, fin)] ordenadas, en piezas del
    texto): una empieza antes (o donde ella) y acaba después (o donde ella), por bisección."""
    inicios = [c for c, _ in piezas]
    mayor_fin = list(itertools.accumulate((d for _, d in piezas), max))

    def dentro(a):
        k = bisect.bisect_right(inicios, a.inicio) - 1
        return k >= 0 and mayor_fin[k] >= a.fin
    return dentro


def _anadidas(nombres) -> list[tuple[str, str, str]]:
    """AC205: [(entity_id, lectura, nombre)] de las lecturas a través de las comillas (`_comillas_partidas`) de unos
    nombres ([(entity_id, nombre)]) que dan otras fichas: la lectura entera y, de una sociedad u organismo, su núcleo
    distintivo en ella (`nucleo_distintivo`), cada una si distingue en esa lectura (`_nombre_cuenta`, dos palabras de
    una persona): «N'i'm'b'o» es «nimbo» aunque su lectura tal cual no distinga. Para el índice completo
    (`_indice_nombres`), con el nombre original, o el núcleo, como nombre."""
    out = []
    for eid, nombre in nombres:
        lectura = _comillas_partidas(nombre)
        if _fichas(lectura) == _fichas(nombre):
            continue
        nucleo = nucleo_distintivo(lectura) if eid.startswith(SENSIBLES) else None
        out += [(eid, n, nombre if n == lectura else n) for n in dict.fromkeys((lectura, nucleo))
                if n and _nombre_cuenta(n, 2 if eid.startswith('Person:') else 1)]
    return out


def _identifica_en(texto, lectura, ids, indices, completos, acotada, eximentes, es_base=False) -> bool:
    """`identifica_explicitamente` en una pasada: la lectura del texto y sus índices (civil, de sociedades y de los
    extremos de la fila); `completos`, los nombres de los extremos; `eximentes`, los de la pasada base
    (`_contenedores_de`): una mención del seudónimo dentro de uno de ellos no es la suya (AC208: solo ellos eximen;
    AC211: en las demás pasadas, solo la que la base halla dentro de uno, la misma, por identidad y tramo; AC216: y
    que esa lectura también ve dentro de uno, de los suyos), y sus nombres son los de los civiles que el texto nombra;
    `es_base`: esta es la pasada base."""
    civiles, sociedades, indice_suyos = indices
    fichas = _fichas(lectura)
    formulas = _formulas_en(fichas)
    if not formulas:
        return False
    pilas = _apariciones_indice(fichas, _indice_pilas(frozenset(ids)))
    if not pilas:
        return False
    nombrados = [a for indice in (civiles, sociedades, indice_suyos)
                 for a in _apariciones_indice(fichas, indice) if a.eid not in ids]
    formas = [(inicio, fin) for inicio, fin, _ in _formas_en(fichas)]
    civil = [(a.inicio, a.fin) for a in nombrados] + formas
    roster, en_formula = frozenset(ids), _piezas_de(formulas)

    def de_formula(inicio, fin):   # AC176: un tramo con una pieza de una fórmula no exime
        return any(g in en_formula for g in range(inicio, fin))
    piezas_base, exento, nombres_base, lectura_base, identidades = eximentes
    suelto = _sueltos(texto, fichas, pilas, [n for n in completos if not _es_identificador(n, roster)]
                      + nombres_base, lectura, en_formula) if acotada else None
    # Una mención dentro de la de un civil (salvo un nombre que identifica, AC167) o de una denominación: un tramo que
    # empieza antes y acaba después; AC208/AC211: los de la pasada base, en ella por piezas y en las demás solo la
    # misma mención que la base halla dentro de uno (`exento`); AC216: y que la propia lectura ve dentro de uno suyo
    # (los de esa lectura, sin los que atraviesan una fórmula suya); AC221: y que es el MISMO contenedor que en la base
    # (su identidad: la entidad y el tramo del texto): un nombre que la lectura invalida por una fórmula no lo
    # sustituye otro que la base no reconoce.
    if es_base:
        dentro = _contenida_en(piezas_base)
    else:
        posiciones, contenedores = _posiciones(lectura), []

        def dentro(a):
            if posiciones is None:
                return False
            inicio, fin = _tramo_de(posiciones, a)
            if not exento(a.eid, inicio, fin, lectura_base[inicio:fin] == lectura[inicio:fin]):
                return False
            if not contenedores:   # una vez por pasada, y solo si la base exime algo
                validos = [(c.inicio, c.fin, c.eid) for c in nombrados if not _es_identificador(c.nombre, roster)
                           and not de_formula(c.inicio, c.fin)] + [(i, f, '') for i, f in formas
                                                                   if not de_formula(i, f)]
                contenedores.append(_contenida_en(sorted(
                    (i, f) for i, f, eid in validos if _identidad_de_contenedor(posiciones, eid, i, f) in identidades)))
            return contenedores[0](a)
    seudonimos = [(a.inicio, a.fin) for a in pilas if not dentro(a) and (not acotada or suelto(a))]
    return _formula_entre(seudonimos, civil, formulas, fichas, _ADYACENTE if acotada else None)


def _formula_entre(seudonimos, civil, formulas, fichas, tope=None) -> bool:
    """AC150/AC160: ¿cae entera alguna fórmula ([(inicio, fin)] de piezas) entre la mención de un seudónimo y la de un
    civil, en cualquier orden y, con `tope`, con `tope` palabras como mucho entre los dos (`_palabras_entre`)? De cada
    mención, por cada lado, solo el civil más lejano dentro del tope (el hueco más ancho) y, desde cada pieza, el fin
    más cercano de una fórmula que empieza en ella o después: sin recorrer cada trío de mención, civil y fórmula."""
    if not seudonimos or not civil or not formulas:
        return False
    n = len(fichas.piezas)
    duros = list(itertools.accumulate((not unible for unible in fichas.unibles), initial=0))
    fin_desde = [n + 1] * (n + 2)   # fin_desde[x]: el menor fin de una fórmula que empieza en la pieza x o después
    for f, g in formulas:
        fin_desde[f] = min(fin_desde[f], g)
    for x in range(n, -1, -1):
        fin_desde[x] = min(fin_desde[x], fin_desde[x + 1])
    inicios, fines = sorted(c for c, _ in civil), sorted(d for _, d in civil)
    for p, q in seudonimos:
        if q < n:   # el civil detrás: el inicio más lejano c ≥ q con 1 + duros[c-1] - duros[q] ≤ tope
            limite = n if tope is None else bisect.bisect_right(duros, duros[q] + tope - 1)
            k = bisect.bisect_right(inicios, limite) - 1
            if k >= 0 and inicios[k] >= q and fin_desde[q] <= inicios[k]:
                return True
        if p > 0:   # el civil delante: el fin más lejano d ≤ p con 1 + duros[p-1] - duros[d] ≤ tope
            limite = 0 if tope is None else bisect.bisect_left(duros, duros[p - 1] - tope + 1)
            k = bisect.bisect_left(fines, limite)
            if k < len(fines) and fines[k] <= p and fin_desde[fines[k]] <= p:
                return True
    return False


def _piezas_de(formulas) -> frozenset:
    """AC176: las piezas que cubren las fórmulas ([(inicio, fin)] de `_formulas_en`)."""
    return frozenset(g for inicio, fin in formulas for g in range(inicio, fin))


def _sueltos(texto, fichas, pilas, completos, lectura=None, en_formula=frozenset()):
    """AC160 (i): el predicado, una vez por texto, de si una mención de un seudónimo (una de `pilas`, de
    `_indice_pilas`) es su nombre de pila suelto. No lo es si sus palabras son, en cualquier orden, las primeras de un
    nombre más largo de `completos` (los de las entidades de la fila y los de los civiles que nombra el texto:
    «Evaristo» en la fila de «Evaristo Valentí López»), ni si una pieza de nombre propio la precede o la sigue sin más
    que espacios o un guion entre las dos («Pablo Casado», «Carlos Alfonso Parra»): con mayúscula inicial en el texto y
    que no es un artículo, una preposición, un tratamiento, una marca ni una palabra de una fórmula (`_NO_DISTINTIVAS`,
    `_sin_nombre`, `_PALABRAS_FORMULA`: «don Benito» y «Según Benito» son sueltos). Sin las posiciones del texto
    (`_posiciones`), suelto: fallo cerrado. Las fichas, las de `lectura` (AC168: `_comillas_partidas`, del mismo largo
    que el texto) si se da; la mayúscula y lo que une dos piezas, del texto original. AC176: ni una pieza de una
    fórmula (`en_formula`, `_piezas_de`) en cualquiera de sus lecturas («IAS» de «AL-IAS», «AS» de «AL«I»AS»)."""
    largo = max(len(_nombre(a.eid)) for a in pilas)
    iniciales = {tuple(sorted(p[:k])) for p in map(_palabras, completos) for k in range(1, min(largo + 1, len(p)))}
    posiciones, piezas = _posiciones(texto if lectura is None else lectura), fichas.piezas

    def propia(g, vecina):   # la pieza g es de un nombre propio y está unida a la `vecina`
        lo, hi = sorted((g, vecina))
        return (g not in en_formula and _JUNTO.fullmatch(texto, posiciones[lo][1], posiciones[hi][0]) is not None
                and texto[posiciones[g][0]].isupper() and piezas[g] not in _NO_DISTINTIVAS
                and not _sin_nombre(piezas[g]) and piezas[g] not in _PALABRAS_FORMULA)

    def suelto(aparicion):
        if tuple(sorted(_nombre(aparicion.eid))) in iniciales:
            return False
        return posiciones is None or not ((aparicion.inicio > 0 and propia(aparicion.inicio - 1, aparicion.inicio))
                                          or (aparicion.fin < len(piezas) and propia(aparicion.fin, aparicion.fin - 1)))
    return suelto


def nombre_identificador(texto, ids, completos=()) -> bool:
    """AC167: ¿es un nombre o un alias de un civil (`texto`) uno que lo identifica con un seudónimo del roster (`ids`)?
    Si lleva el id de uno o su mención explícita (AC66: «seudónimo» y su nombre de pila, también con marcas o
    tratamientos en medio), o una fórmula de identidad (`_formulas_en`, en cualquier lectura) y su nombre de pila suelto
    (`_sueltos`, frente a `completos`: los demás nombres de su entidad): «Centinela Civil, alias Benito», «alias
    Benito». Como AC134 para el alias de un seudónimo; «Benito Zeta, alias el Gordo» (parte de un nombre más largo),
    no. AC176: la fórmula en cualquiera de sus lecturas («Centinela Civil, AL-IAS BENITO»). AC202: en cada lectura del
    texto (`_lecturas_texto`); basta una."""
    if not ids or not isinstance(texto, str):
        return False
    del_roster = {i.lower() for i in ids}
    if any(i.lower() in del_roster for i in _ID_ENTIDAD.findall(_normalizar(texto))):
        return True
    if _detector(frozenset(ids), 'mencion')(texto):
        return True
    roster = frozenset(ids)
    otros = [n for n in completos if n != texto and not _es_identificador(n, roster)]
    for lectura in _lecturas_texto(texto):
        fichas = _fichas(lectura)
        formulas = _formulas_en(fichas)
        pilas = _apariciones_indice(fichas, _indice_pilas(roster)) if formulas else ()
        if pilas:
            suelto = _sueltos(texto, fichas, pilas, otros, lectura, _piezas_de(formulas))
            if any(suelto(a) for a in pilas):
                return True
    return False


@functools.lru_cache(maxsize=8192)
def _es_identificador(nombre, ids) -> bool:
    """`nombre_identificador` de un nombre del índice (sin los demás de su entidad), en caché por nombre y roster."""
    return nombre_identificador(nombre, ids)


def podria_identificar(texto, ids) -> bool:
    """AC150/AC160/AC167: ¿lleva el texto una fórmula de identidad (`lleva_formula`) o el id de un seudónimo del roster?
    Sin ninguna de las dos no identifica a nadie: quien lo comprueba no necesita los índices."""
    del_roster = {i.lower() for i in ids}
    return lleva_formula(texto) or any(i.lower() in del_roster for i in _ID_ENTIDAD.findall(_normalizar(texto)))


def identificaciones_civiles(textos, entidades, ids, civiles, sociedades=None, nombres=None) -> list[tuple[str, str]]:
    """[(motivo, huella)] de los textos [(texto, clase)] de una fila civil (ninguna entidad en `ids`) que identifican a
    un seudónimo con un civil, con la huella del texto. AC160 ('civil'): AC150 acotada (`identifica_explicitamente`),
    en una cita, un selector o la prosa. AC167 ('alias-identificador'): un nombre o un alias de la fila que lo
    identifica (`nombre_identificador`, frente a los demás). Los nombres de sus entidades civiles: los de `nombres` y
    los que la fila declara (`NOMBRE`, `ALIAS`)."""
    revisar = [(t, c) for t, c in textos if c in (PROSA, CITA, SELECTOR, NOMBRE, ALIAS) and isinstance(t, str)
               and podria_identificar(t, ids)]
    if not revisar:
        return []
    declarados = {t.strip() for t, c in textos if c in (NOMBRE, ALIAS) and isinstance(t, str) and t.strip()}
    de_fila = {e: set((nombres or {}).get(e, ())) | declarados for e in _extremos_civiles(entidades, ids)}
    completos = sorted({n for nombres_e in de_fila.values() for n in nombres_e} | declarados)
    out = [('alias-identificador', sha16(t)) for t, c in revisar
           if c in (NOMBRE, ALIAS) and nombre_identificador(t, ids, completos)]
    out += [('civil', sha16(t)) for t, c in revisar
            if c in (PROSA, CITA, SELECTOR)
            and identifica_explicitamente(t, ids, civiles, sociedades, de_fila, entidades, acotada=True)]
    return list(dict.fromkeys(out))


def problemas_seudonimo(textos, entidades, ids, civiles, publicos=None, nombres=None, sociedades=None) -> list[str]:
    """AC78: las huellas de lo que los textos [(texto, clase)] de una fila de seudónimo (`entidades`) no pueden nombrar:
    (a) el id `Person:`/`Company:`/`Organization:` de una entidad que no es de la fila; (b) una persona civil
    (`civiles`, `indice_civiles`), salvo, en una cita, un extremo de la fila (la cita judicial nombra a sus extremos y
    la arista ya publica el par; en prosa, también ellos son hallazgo) que solo coaparece: si la cita de una fila de un
    seudónimo y un civil dice «seudónimo» o lleva un id, el extremo civil cuenta (también una sociedad u organismo por
    su nombre o alias completos, `nombres`, AC94), y la mención del seudónimo de la fila también (AC87); (c) un
    documento fuera del árbol público
    (`publicos`; None: sin comprobar): el de un campo de documento y el que se nombra dentro del texto; (d) otro
    seudónimo, por mención explícita. Sobre el texto normalizado y, en una URL, decodificado: en ella solo (a) y (d),
    un segmento con un nombre o un PDF ajeno no es prosa. Los nombres, «seudónimo» y las menciones, en cualquier
    lectura del texto (`_fichas`, AC104/AC105(b)/AC109); los ids y los documentos, tal cual. Un alias lo juzgan
    AC44/AC60 y el `nombre`, AC83 (`revisar_textos`). Un seudónimo homónimo de uno de la fila (el mismo nombre de pila)
    no es «otro»: su nombre no los distingue. AC118: en (b), los nombres de los seudónimos de la fila (su nombre
    canónico y sus alias, que el índice lleva si no son variantes de su nombre de pila) no cuentan en ella; en la de
    otro seudónimo, sí; y, en prosa, también el nombre de pila de dos palabras o más de otro seudónimo que no es
    homónimo de uno de la fila (`_pilas_en`, en cualquier orden, AC130). AC150: y ninguno identifica a un seudónimo con
    un civil (`identifica_explicitamente`, con las sociedades de `sociedades`): la huella del texto, sin exención."""
    propias = {str(e).lower() for e in entidades if e}
    nombres_propios = {tuple(sorted(_nombre(e))) for e in entidades if e in ids}
    otros = frozenset(s for s in ids if s not in entidades and tuple(sorted(_nombre(s))) not in nombres_propios)
    seudonimos_propios = frozenset(e for e in entidades if e in ids)
    mixta = bool(seudonimos_propios) and any(e and e not in ids for e in entidades)
    huellas = []
    for texto, clase in textos:
        if clase in (ALIAS, NOMBRE, ESTRUCTURA, EXTREMO):
            continue
        if clase == DOC:
            if publicos is not None and str(texto).strip() and not doc_publico(texto, publicos):
                huellas.append(sha16(str(texto).strip()))
            continue
        bruto = _decodificar_url(texto) if clase == URL else texto
        legible = _normalizar(bruto)
        sid = _detector(otros, 'mencion')(bruto)
        huellas += [sha16(i) for i in _ID_ENTIDAD.findall(legible) if i.lower() not in propias]
        if clase != URL:
            # AC87: la exención de los extremos en una cita es solo para la coaparición. La de una fila de un seudónimo
            # y un civil que dice «seudónimo» o lleva un id los identifica: cuentan el civil y la mención del seudónimo.
            cita = clase in (CITA, SELECTOR)
            identifica = cita and mixta and bool(_dice(bruto, _SEUDONIMO) or _ID_ENTIDAD.search(legible))
            extremos = (set(entidades) if cita and not identifica else set()) | seudonimos_propios
            calladas = frozenset((f'{s}{_ALIAS_INVALIDO}', a) for s, a in getattr(civiles, 'denunciados', ())
                                 if s in ids - otros)   # AC134: solo los alias inválidos ya denunciados
            huellas += [sha16(n) for n in sorted(_nombres_en(bruto, civiles, extremos, calladas))]
            if not cita:   # AC118: en prosa, el nombre de pila de otro seudónimo (no homónimo; mencionado, ya sale)
                huellas += [sha16(n) for n in sorted(_pilas_en(bruto, ids, (ids - otros) | {sid}))]
            if identifica:   # AC94: el extremo propio por sus nombres completos, no solo una persona del índice
                huellas += [sha16(n) for n in sorted(_nombres_propios(bruto, _extremos_civiles(entidades, ids),
                                                                      civiles, nombres))]
            if publicos is not None:
                huellas += [sha16(d) for d in _pdfs_en(legible) if not doc_publico(d, publicos)]
            propio = _detector(seudonimos_propios, 'mencion')(bruto) if identifica else None
            huellas += [sha16(propio)] if propio else []
            if identifica_explicitamente(bruto, ids, civiles, sociedades, nombres, entidades):   # AC150
                huellas.append(sha16(bruto))
        huellas += [sha16(sid)] if sid else []
    return list(dict.fromkeys(huellas))


def fuera_del_roster(entidades, textos, ids) -> set[str]:
    """AC89: los entity_id con forma de seudónimo (`seudonimo`, también `pseudonimo`, en el slug) de una fila (sus
    `entidades` y los ids de sus textos [(texto, clase)], normalizados y, en una URL, decodificados) que el roster
    (`ids`) no declara: fail closed."""
    del_roster = {i.lower() for i in ids}
    candidatos = {e for e in entidades if isinstance(e, str) and e}
    candidatos |= {x.rstrip('-') for texto, clase in textos
                   for x in _ID_ENTIDAD.findall(_normalizar(_decodificar_url(texto) if clase == URL else texto))}
    return {e for e in candidatos if 'seudonimo' in e.split(':', 1)[-1].lower() and e.lower() not in del_roster}


# --- AC120: la prosa que se publica junto a un seudónimo -------------------------------------------------------------
# Lo que re-identifica a un seudónimo es un dato civil concreto: una sociedad real, un nombre civil (AC78) o una fecha
# de hechos. No lo hacen la referencia al documento público que ya lo publica, el número de causa, una página, un
# importe, la huella de una arista ni la fecha de una decisión de la auditoría: son campos cerrados (`_sin_cerrados`).
_MESES = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
          'septiembre': 9, 'setiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12}
_MES = '(?:' + '|'.join(_MESES) + ')'
_FECHA_COMPLETA = (rf'(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{4}}'
                   rf'|\d{{1,2}}\s+de\s+{_MES}\s+de\s+\d{{4}})')
_FECHA = re.compile(rf'\b{_FECHA_COMPLETA}\b', re.I)
# Cualquier fecha, día y mes o mes y año, o un año de cuatro cifras; también dentro de un identificador con guiones
# bajos («ATS_4444_2025»: el de un documento que no resuelve).
# AC126: también en otras grafías, fallo cerrado: el mes en romanos («8-X-2025», «8-X-25», «VIII-X-MMXXV»), el año en
# romanos o en letra junto a un mes, tras «año» o en una fecha («octubre de MMXIX», «el año dos mil», «octubre de dos
# mil diecinueve»), el de dos cifras tras un mes («octubre del 19»), el día en letra, en romanos u ordinal («ocho de
# octubre», «XX de octubre», «1.º de octubre») y el año en letra suelto («dos mil diecinueve», «mil novecientos noventa
# y nueve»; seguido de una moneda es un importe: «dos mil veinte euros»). AC126-bis: el día y el mes sin año («8/10»,
# «8-10»), solo con un marcador de fecha justo delante («el», «día», «fecha», «desde el», «hasta el», «a», y las
# contracciones «del» y «al»): sin él son una fracción, un recuento o un rango («3/4 del capital», «páginas 8-10»).
_ROMANO_MES = r'(?:XII|XI|IX|X|VIII|VII|VI|IV|V|III|II|I)'
_ROMANO_DIA = r'(?:XXX(?:I)?|XX(?:IX|IV|V?I{0,3})|X(?:IX|IV|V?I{0,3})|IX|IV|V?I{1,3}|V)'
_ROMANO_ANIO = r'(?:M{1,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))'
_NUMERO_LETRA = (r'(?:uno|un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|'
                 r'dieci(?:s[eé]is|siete|ocho|nueve)|veinte|veinti(?:uno|ún|un|d[oó]s|tr[eé]s|cuatro|cinco|s[eé]is|'
                 r'siete|ocho|nueve)|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa)')
_DIA_LETRA = r'(?:primero|treinta\s+y\s+uno|' + _NUMERO_LETRA[3:]
# AC197: el año en letra lleva sus numerales enteros (hasta un límite de palabra, `_NUMERAL`) seguidos: tres o, con
# menos, todos (sin otro numeral detrás); y si una moneda los sigue, es un importe («dos mil veinte y tres euros» no es
# el año «dos mil veinte»). Sin un grupo posesivo, que no devolvía lo tomado: uno que empieza otra palabra («dos mil
# veinte dosier») o que solo lo es en una lectura («vein✓te», unido) tapaba el año ya válido.
_NUMERAL = rf'\s+(?:y\s+)?{_NUMERO_LETRA}(?![0-9a-z])'
_ANIO_LETRA = (rf'(?:dos\s+mil|mil\s+novecientos)(?:(?:{_NUMERAL}){{3}}|(?:{_NUMERAL}){{1,2}}(?!{_NUMERAL}))'
               r'(?!\s+(?:de\s+)?(?:euros?|€|d[oó]lares?|pesetas?|libras?)(?![0-9a-z]))')
_ANIO_TRAS_MES = rf'(?:\d{{4}}|{_ROMANO_ANIO}|(?:dos\s+mil|mil\s+novecientos)(?:\s+(?:y\s+)?{_NUMERO_LETRA}){{0,3}})'
_FECHA_SUELTA = re.compile(rf'(?<![0-9a-z])(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}'
                           rf'|\d{{1,2}}(?:\.?[º°ª])?\s+de\s+{_MES}|{_MES}\s+(?:de\s+|del\s+)?{_ANIO_TRAS_MES}'
                           rf"|{_MES}\s+(?:de|del)\s+['’]?\d{{2}}(?![.,]\d)|a[ñn]o\s+(?:de\s+|del\s+)?{_ANIO_TRAS_MES}"
                           rf'|(?:\d{{1,2}}|{_ROMANO_DIA})[/.-]{_ROMANO_MES}[/.-](?:\d{{2}}|\d{{4}}|{_ROMANO_ANIO})'
                           rf'|(?:{_DIA_LETRA}|{_ROMANO_DIA})\s+de\s+{_MES}|{_ANIO_LETRA}|(?:1[5-9]|20)\d{{2}}'
                           r'|(?:el|d[ií]a|fecha:?|desde\s+el|hasta\s+el|a|del|al)\s+\d{1,2}[/-]\d{1,2})'
                           r'(?![0-9a-z])', re.I)
_REF_NUMERO = re.compile(r'\b(A?ATS)[ _](\d+)[/_](\d{4})(?:\.pdf)?\b')
_REF_FICHERO = re.compile(r'\b[A-Za-z0-9][A-Za-z0-9_.-]*\.pdf\b')
_REF_FECHA = re.compile(rf'\b[Aa]uto\s+(?:del\s+)?(?:TS|Tribunal Supremo)\s+de\s+({_FECHA_COMPLETA})\b', re.I)
_IMPORTE = re.compile(r'\b\d+(?:[.,]\d+)*\s?(?:€|euros?\b|EUR\b)', re.I)
_CAUSA = re.compile(r'\b(?:CE|[Cc]ausa [Ee]special|[Dd]iligencias [Pp]revias|DP|[Ss]umario)'
                    r'(?:\s+n\.?\s?º)?\s+\d+/\d{4}\b')
_CERRADOS = (_CAUSA,
             re.compile(r'\bpp?\.\s?\d+(?:\s?-\s?\d+)?\b'),                       # páginas
             _IMPORTE,                                                          # importes (`_importes_en`)
             re.compile(r'\[[0-9a-z]{8}\]|\b(?=[0-9a-f]*[a-f])[0-9a-f]{5,8}\b…?'))     # huellas de unidades y aristas
_ISO = re.compile(r'\b\d{4}-\d{2}-\d{2}\b')
# La procedencia generada (registro, `reason` de las reglas, `qa_set_run`): sus prefijos y fórmulas cerradas, también
# las de versiones anteriores del apply; la parte libre pasa la regla de contenido. Varias decisiones encadenadas en un
# mismo campo, separadas por « · », cada una con su prefijo.
_PREFIJOS = (re.compile(r'(?:\d{3} auditoría escalonada \d{4}-\d{2}-\d{2}|\d{3} (?:tramo \d+|pendientes) '
                        r'\(\d{4}-\d{2}-\d{2}\))(?: \[[0-9a-z]{8}\])?: |swarm: '),
             re.compile(r'\([A-Z][A-Za-z]*:[^()\s]*\)(?: -\[[A-Z_]+\]-> \([A-Z][A-Za-z]*:[^()\s]*\))?: '))
_FORMULAS = re.compile('|'.join((
    r'tanda \d{4}-\d{2}-\d{2}: decisión del dueño', r'decisión del dueño \(\d{4}-\d{2}-\d{2}\)',
    r'motivo en la decisión interna de la auditoría', r'relación institucional retirada \(seudónimo judicial\)',
    r'relaciones institucionales retiradas \(seudónimo judicial\)',
    r'relación institucional duplicada retirada \(seudónimo judicial\)',
    r'cita con cargo identificativo retirada \(seudónimo judicial\)', r'nota de seudónimo corregida',
    r'marcado como seudónimo judicial', r'arista (?:corregida|retirada)',
    r'seudónimo judicial: (?:relación retirada|cita con cargo retirada|motivo de la corrección retirado) por riesgo '
    r'de reidentificación(?: \(criterio spec 061 §D5\))?\.?',
    r'(?:retirada|corregida) por la política de seudónimos judiciales \(spec 070\); motivo en la traza privada',
    r'frase del resumen (?:retirada|corregida)', r'(?:rol|alias|dato de ficha) (?:retirado|corregido)',
    r'decisión de la auditoría \d{3} \(\d{4}-\d{2}-\d{2}\)')))
# Las formas jurídicas de una sociedad mercantil, al final de su nombre (o la UTE, al principio), como piezas
# (`_fichas`: con o sin puntos, en cualquier caja; «S.à r.l.» son «s», «a», «r», «l»).
_FORMAS_JURIDICAS = (('s', 'l'), ('s', 'a'), ('s', 'l', 'u'), ('s', 'a', 'u'), ('sl',), ('sa',), ('slu',), ('sau',),
                     ('s', 'c'), ('sc',), ('s', 'coop'), ('scoop',), ('coop',), ('ute',), ('aie',), ('a', 'i', 'e'),
                     ('ltd',), ('llc',), ('inc',), ('corp',), ('gmbh',), ('ag',), ('srl',), ('s', 'r', 'l'), ('spa',),
                     ('s', 'p', 'a'), ('plc',), ('bv',), ('b', 'v'), ('nv',), ('n', 'v'), ('sas',), ('s', 'a', 's'),
                     ('sll',), ('s', 'l', 'l'), ('slne',), ('s', 'l', 'n', 'e'), ('sarl',), ('s', 'a', 'r', 'l'),
                     ('scp',), ('s', 'c', 'p'))
# AC179: las formas jurídicas por la primera letra o cifra con que puede empezar la pieza que las abre (la de su primera
# palabra o un dígito que la lee, `_es_forma`), en su orden: `_formas_en` solo prueba en cada pieza las que pueden
# casar.
_FORMAS_POR_INICIAL = {c: [f for f in _FORMAS_JURIDICAS if f[0][0] == c or f[0][0] in _DIGITO_LETRAS.get(c, '')]
                       for c in {f[0][0] for f in _FORMAS_JURIDICAS} | set(_DIGITO_LETRAS)}
# Los nombres genéricos de nodos de relleno o de alias genéricos del grafo (lista cerrada de huellas, medida el 08-oct
# sobre el export y el contexto): no son una sociedad concreta. Cualquier otro nombre cuenta (fail-closed). AC153: y un
# alias de una sola palabra del censo que es un sustantivo común del español.
_GENERICAS_HUELLAS = frozenset({'de4743c879734dc3', 'd764d42592fc9687', '9cb403795f3e2673', 'ef46d887b93b39f0',
                                '8676b73384a972c9', '0bec84246d4c54be'})
# AC118: la nota de un seudónimo (y el rol del ancla de su nodo en el contexto) con su plantilla cerrada: sus únicos
# campos abiertos son las referencias a documentos públicos y el número de causa.
_REF_PLANTILLA = r'(?:A?ATS[ _]\d+[/_]\d{4}(?:\.pdf)?|resoluciones judiciales publicadas \(CENDOJ\))'
_PLANTILLA_SEUDONIMO = re.compile(
    rf'Seudónimo de anonimización judicial \({_REF_PLANTILLA}(?:(?:, | y ){_REF_PLANTILLA})*(?:, (?:CE|Causa Especial) '
    r'\d+/\d{4})?\)\. Nombre de pila ficticio del roster del CENDOJ: persona real distinta de cualquier homónimo con '
    r'nombre civil; no atribuir identidad ni cargo\.')
# AC120: los textos de cada sección del inventario que juzga la regla de contenido (`contenido`; en `curacion`, con su
# fecha ISO de curación) o la de la procedencia generada (`procedencia`), en una fila con un seudónimo.
PROSA_SEUDONIMO = {
    (PSEUDONYMS_REL, 'pseudonyms'): {'note': 'contenido', 'curacion': 'curacion'},
    (REGISTRO_REL, 'rectificaciones'): {'dato': 'procedencia', 'fuente': 'procedencia'},
    (f'{RELATIONS_REL}/*.y*ml', 'deny'): {'reason': 'procedencia'},
    (f'{RELATIONS_REL}/*.y*ml', 'fix'): {'reason': 'procedencia', 'set.note': 'contenido'},
    ('pipeline/data/accusations/*.y*ml', 'accusations'): {'estado.nota': 'contenido'},
    (f'{CONTEXT_REL}/*.y*ml', 'entities'): {'role': 'contenido'}}
# Y los props del export: los del nodo de un seudónimo y los de una arista que lo toca.
PROPS_SEUDONIMO = {'nodo': {'pseudonym_note': 'contenido', 'role': 'contenido'},
                   'arista': {'note': 'contenido', 'qa_set_run': 'procedencia'}}


def _fecha(texto):
    """(año, mes, día) de una fecha completa escrita (ISO, numérica o «d de mes de aaaa»), o None."""
    texto = texto.strip()
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', texto)
    if m:
        return int(m[1]), int(m[2]), int(m[3])
    m = re.fullmatch(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})', texto)
    if m:
        return int(m[3]), int(m[2]), int(m[1])
    m = re.fullmatch(rf'(\d{{1,2}})\s+de\s+({_MES})\s+de\s+(\d{{4}})', texto, re.I)
    return (int(m[3]), _MESES[m[2].lower()], int(m[1])) if m else None


def censo_documentos(repo=None, publicos=()) -> tuple[dict, dict]:
    """AC120: ({documento: su fecha o None}, {(tribunal, fecha): [documento]}) del registro de documentos
    (`documents/documents.yaml`, la fecha y el tribunal curados) y de los documentos del árbol público (`publicos`, sin
    fecha): lo que resuelve una referencia a un documento."""
    path = Path(repo or REPO) / 'pipeline/data/documents/documents.yaml'
    filas = _documento(path)[0].get('documents') or [] if path.exists() else []
    por_id, por_fecha = {d: None for d in publicos}, {}
    for fila in filas:
        if isinstance(fila, dict) and isinstance(fila.get('doc_id'), str) and fila['doc_id'].strip():
            fecha = _fecha(str(fila.get('date') or ''))
            por_id[fila['doc_id'].strip()] = fecha
            if fecha and isinstance(fila.get('court'), str):
                por_fecha.setdefault((fila['court'].strip(), fecha), []).append(fila['doc_id'].strip())
    return por_id, por_fecha


def _en_blanco(texto, tramos) -> str:
    """El texto con cada tramo (inicio, fin) en blanco, de una vez (en orden y sin solapes)."""
    partes, previo = [], 0
    for inicio, fin in tramos:
        partes += [texto[previo:inicio], ' ' * (fin - inicio)]
        previo = fin
    return ''.join([*partes, texto[previo:]])


# AC213: `_REF_FICHERO` y los importes de `_CERRADOS`, en una pasada. Con una racha larga que no acaba en lo que piden
# («a-a-a-…», «1.1.1.…»), la expresión entera vuelve a recorrerla desde cada comienzo posible (cuadrático), y solo el
# primero de una racha puede casar: todos los comienzos de una racha ven el mismo final. AC231: igual el nombre de un
# fichero «.pdf» de AC78(c) (`_pdfs_en`).
_RACHA_FICHERO = re.compile(r'[A-Za-z0-9_.-]+')
_FIN_FICHERO = re.compile(r'\.pdf\b')
_RACHA_PDF = re.compile(r'[A-Za-z0-9_.-]+', re.I)
_FIN_PDF = re.compile(r'\.pdf', re.I)
_CADENA_IMPORTE = re.compile(r'\d+(?:[.,]\d+)*')
_UNIDAD_IMPORTE = re.compile(r'\s?(?:€|euros?\b|EUR\b)', re.I)
_SEPARADOR_IMPORTE = re.compile(r'[.,]\d')


def _ficheros_en(texto) -> list[re.Match]:
    """Lo que `_REF_FICHERO.finditer` halla en un texto, en tiempo lineal: de cada racha de sus caracteres, solo si
    acaba en algún «.pdf», y hasta el último (el que una búsqueda voraz acaba tomando)."""
    out = []
    for racha in _RACHA_FICHERO.finditer(texto):
        inicio, fin = racha.span()
        ultimo = None
        for m in _FIN_FICHERO.finditer(texto, inicio, min(fin + 1, len(texto))):
            if m.end() <= fin:
                ultimo = m
        if ultimo is not None:
            out.extend(_REF_FICHERO.finditer(texto, inicio, ultimo.end()))
    return out


def _pdfs_en(texto) -> list[str]:
    """AC231 (AC78(c)): lo que `re.findall(r'[A-Za-z0-9_.-]+\\.pdf', texto, re.I)` halla en un texto, en tiempo lineal:
    de cada racha de sus caracteres (con la misma caja que la expresión), si lleva algún «.pdf» que no la empiece,
    desde su comienzo hasta el último (el que una búsqueda voraz acaba tomando; tras él no queda otro en la racha)."""
    out = []
    for racha in _RACHA_PDF.finditer(texto):
        inicio, fin = racha.span()
        ultimo = None
        for m in _FIN_PDF.finditer(texto, inicio + 1, fin):
            ultimo = m
        if ultimo is not None:
            out.append(texto[inicio:ultimo.end()])
    return out


def _importes_en(texto) -> list[tuple[int, int]]:
    """Los tramos que casa `\\b\\d+(?:[.,]\\d+)*\\s?(?:€|euros?\\b|EUR\\b)` (sin distinguir mayúsculas) en un texto, en
    tiempo lineal: de cada cadena de cifras con separadores, si la unidad la sigue, desde su primer comienzo con límite
    de palabra (su primera cifra, o la que sigue a un separador)."""
    out = []
    for cadena in _CADENA_IMPORTE.finditer(texto):
        inicio, fin = cadena.span()
        unidad = _UNIDAD_IMPORTE.match(texto, fin)
        if unidad is None:
            continue
        if inicio == 0 or not (texto[inicio - 1].isalnum() or texto[inicio - 1] == '_'):
            comienzo = inicio
        else:
            separador = _SEPARADOR_IMPORTE.search(texto, inicio, fin)
            if separador is None:
                continue
            comienzo = separador.start() + 1
        out.append((comienzo, unidad.end()))
    return out


def _sin_cerrados(texto, censo, iso=False) -> str:
    """AC120: el texto con sus campos cerrados en blanco: una referencia a un documento que resuelve a uno solo del
    censo (`censo_documentos`: por su número, su fichero o su fecha y tribunal; si no resuelve, o resuelve a varios, no
    es cerrada), una fecha igual a la de un documento referenciado en el mismo texto, un número de causa, una página, un
    importe, la huella de una unidad o de una arista y, con `iso` (la `curacion` del roster), las fechas ISO."""
    por_id, por_fecha = censo
    fechas = set()
    for referencias, doc_de in ((_REF_NUMERO.finditer, lambda m: f'{m[1].upper()}_{m[2]}_{m[3]}.pdf'),
                                (_ficheros_en, lambda m: m[0])):
        tramos = []
        for m in referencias(texto):
            if doc_de(m) in por_id:
                tramos.append(m.span())
                fechas.add(por_id[doc_de(m)])
        texto = _en_blanco(texto, tramos)
    tramos = []
    for m in _REF_FECHA.finditer(texto):
        if len(por_fecha.get(('Tribunal Supremo', _fecha(m[1])), ())) == 1:
            tramos.append(m.span())
            fechas.add(_fecha(m[1]))
    texto = _en_blanco(texto, tramos)
    texto = _en_blanco(texto, [m.span() for m in _FECHA.finditer(texto) if _fecha(m[0]) in fechas - {None}])
    for patron in (*_CERRADOS, *((_ISO,) if iso else ())):
        texto = _en_blanco(texto, _importes_en(texto) if patron is _IMPORTE
                           else [m.span() for m in patron.finditer(texto)])
    return texto


def _forma_juridica(nombre) -> bool:
    """¿Lleva el nombre una forma jurídica de sociedad mercantil (`_FORMAS_JURIDICAS`) al final, o la UTE al principio?
    En sus palabras y, AC198, en sus fichas (`_fichas`), con la forma como la reconoce `_formas_en` («Nimbo Gm✓bH»,
    «Nimbo Gm«b»H»). Sus lecturas a través de las comillas las mira quien la llama (`indice_sociedades`, AC205)."""
    palabras = tuple(_palabras(nombre))
    if palabras[:1] == ('ute',) or any(len(palabras) > len(f) and palabras[-len(f):] == f for f in _FORMAS_JURIDICAS):
        return True
    fichas = _fichas(nombre)
    return any(fin == len(fichas.piezas) or (ute and inicio == 0) for inicio, fin, ute in _formas_en(fichas))


def indice_sociedades(nombres) -> dict:
    """AC120: el índice (`_indice_nombres`) de los nombres de las sociedades reales de `nombres` ({entity_id: nombres},
    del contexto y, en el snapshot, del export): las sociedades mercantiles (`Company:`) y las entidades con forma
    jurídica en el nombre; no las instituciones públicas ni los partidos. Sin los nombres que no distinguen (AC116,
    `_nombre_cuenta`) ni los genéricos del grafo (`_GENERICAS_HUELLAS`). AC205: la forma jurídica, el núcleo y la
    distintividad, también en la lectura de cada nombre a través de sus comillas (`_comillas_partidas`: «Nimbo Gm"b"H»
    es «Nimbo GmbH», de núcleo «nimbo»; «N'i'm'b'o» distingue); lo que solo existe en ella, solo en el índice completo
    (`_indice_nombres`, AC202)."""
    base, anadidas = [], []
    for eid, ns in nombres.items():
        if not eid.startswith(SENSIBLES):
            continue
        for entero in sorted(ns):
            if sha16(entero) in _GENERICAS_HUELLAS:
                continue
            for lectura in dict.fromkeys((entero, _comillas_partidas(entero))):
                if lectura != entero and _fichas(lectura) == _fichas(entero):
                    continue
                if not (eid.startswith('Company:') or _forma_juridica(lectura)):
                    continue
                for n in sorted({lectura, nucleo_distintivo(lectura)} - {None}):
                    if _nombre_cuenta(n, 1) and sha16(n) not in _GENERICAS_HUELLAS:
                        if lectura == entero:
                            base.append((eid, n))
                        else:
                            anadidas.append((eid, n, entero if n == lectura else n))
    return _indice_nombres(base, minimo=1, anadidas=anadidas)


def _formas_en(fichas, frontera=None) -> list[tuple[int, int, bool]]:
    """AC121: [(inicio, fin, ute)] de las piezas de cada denominación con forma jurídica del texto, con independencia
    del índice de sociedades: la forma (`_FORMAS_JURIDICAS`; partida por un salto de línea o un guion blando, igual)
    tras una palabra que distingue (no `_NO_DISTINTIVAS`, de dos o más letras o con cifras), o la UTE delante de una
    (`ute`); sin la palabra, la forma sola («una S.L.», «en UTE con») no denomina a nadie. AC170: cada palabra de la
    forma, en cualquier lectura, como las de un nombre (`_tramo`: cada punto de partición unido o separado, «Gm-\\nbH»,
    «L-td»; los homógrafos y los dígitos por posición). AC171: con `frontera` (la pieza g → ¿hay un límite de frase
    entre la g-1 y la g?, `_limite_de_frase`), la palabra al otro lado de un límite no la denomina."""
    piezas, out = fichas.piezas, []
    cruza = frontera or (lambda g: False)

    def distingue(i):
        return 0 <= i < len(piezas) and piezas[i] not in _NO_DISTINTIVAS and (len(piezas[i]) >= 2
                                                                               or piezas[i].isdigit())
    for i in range(len(piezas)):
        for forma in _FORMAS_POR_INICIAL.get(piezas[i][0], ()):   # AC179: las que pueden empezar en ella
            cortes = _tramo(fichas, i, forma)
            if cortes is None:
                continue
            fin = cortes[-1]
            if distingue(i - 1) and not cruza(i):
                out.append((i - 1, fin, False))
            elif forma == ('ute',) and distingue(fin) and not cruza(fin):
                out.append((i, fin + 1, True))
    return list(dict.fromkeys(out))


# AC171: lo que cierra una frase entre dos palabras: «;», «:», «!», «?», «…», un salto de párrafo (una línea en blanco)
# o un punto que no es el de una abreviatura: de una letra (una inicial, una forma jurídica), un tratamiento («D.»,
# «Sr.») o la de un prefijo de denominación o una forma («Hnos.», «Vda.», «Cía.», «Coop.»).
_CIERRE_FRASE = re.compile(r'[;:!?\u2026]|\n[^\S\n]*\n')
_ABREVIADAS = _TRATAMIENTO | frozenset({'hnos', 'vda', 'cia', 'coop'})


def _limite_de_frase(entre, anterior) -> bool:
    """AC171: ¿cierra una frase lo que hay (`entre`, el texto original entre dos piezas) tras la pieza `anterior`
    (plegada)? Una palabra así nunca es prefijo, introductora ni parte de la denominación que la sigue."""
    return bool(_CIERRE_FRASE.search(entre)) or ('.' in entre and len(anterior) > 1 and anterior not in _ABREVIADAS)


# AC152: lo que une el nombre de un extremo a una palabra anterior en una sola denominación («Talleres de Nimbo S.L.»,
# «Cirro y Nimbo S.L.»), como piezas.
_UNIONES = (('de',), ('del',), ('de', 'la'), ('de', 'los'), ('de', 'las'), ('y',), ('e',))
# AC138/AC151: las comillas de una prosa: las de apertura y de cierre de Unicode (categorías Pi y Pf: «», “”, ‘’, ‹›…)
# y las rectas, las bajas, las graves y agudas, las primas y las de ancho completo. No parten un dato civil: la fecha se
# busca en el texto sin ellas («8/«10»» es «8/10») y la sociedad, con cada una entre dos letras o cifras como un punto
# de partición («Nim«bo»», en sus dos lecturas); cada dato, con su posición en el texto original, la de los tramos.
# AC217: no es una lista a mano: todo carácter de Unicode cuyo nombre es una «QUOTATION MARK» (U+2E42, los ornamentales
# U+275B a U+2760 y U+276E y U+276F, U+301D a U+301F, la etiqueta U+E0022…) también lo es.
_COMILLAS_DATO = frozenset('"\'`´„‚＂＇〝〞〟″‶')


@functools.lru_cache(maxsize=4096)
def _es_comilla(c) -> bool:
    return c in _COMILLAS_DATO or unicodedata.category(c) in ('Pi', 'Pf') or 'QUOTATION MARK' in unicodedata.name(c, '')


# AC195: lo que puede ir en el hueco entre dos piezas junto a una comilla: comillas (las rectas y las graves; las demás
# son signos de la guarda de cierre), guiones y rayas, los demás signos de la guarda de cierre (`_OTRO_SIGNO`) y
# espacios.
_HUECO = re.compile(rf'(?:[\s"\'`\u2010-\u2015-]|{_OTRO_SIGNO})+')


def _comillas_solas(texto) -> str:
    """AC138/AC151/AC168/AC177: el texto con cada secuencia de comillas (`_es_comilla`) entre dos letras o cifras como
    un solo punto de partición («Cen«ti»nela» se lee «Cen-ti-nela»: unido, «centinela»; «Cen«ti»«nela»», «Cen-ti- nela
    ») y las demás comillas como espacios, del mismo largo (las posiciones son las del original). AC202: la lectura a
    través de las comillas de la enmienda 39, la referencia de lo que no se pierde (`_lecturas_texto`)."""
    out, i = [], 0
    while i < len(texto):
        j = i
        while j < len(texto) and _es_comilla(texto[j]):
            j += 1
        if j == i:
            out.append(texto[i])
            i += 1
            continue
        partida = 0 < i and j < len(texto) and texto[i - 1].isalnum() and texto[j].isalnum()
        out.append('-' + ' ' * (j - i - 1) if partida else ' ' * (j - i))
        i = j
    return ''.join(out)


def _comillas_partidas(texto) -> str:
    """AC195: el texto con cada hueco entre dos letras o cifras hecho de comillas (`_es_comilla`), guiones, rayas,
    signos de la guarda de cierre y espacios (`_HUECO`), con alguna comilla, como un punto de partición si sin sus
    comillas lo es para las fichas (nada, un guion o una raya con espacios detrás, `_UNION`, o signos de `_PARTIDA`):
    «al«→ias»», «Nim«-»bo», «al«-\\nias»; si no, sus comillas son espacios y lo demás queda («Nimbo» - «S.L.»); del
    mismo largo (las posiciones son las del original). AC202: lo que queda sin las comillas se decide con el plegado de
    las fichas (`_plegar_homografos` de `_cita_legible`: confusables, invisibles, guion blando): «Nim«·»bo» es «Nim.bo»,
    que no parte, y «al«−\\nias»», «al-\\nias». Una lectura que solo añade (`_lecturas_texto`)."""
    out, desde = [], 0
    for m in _HUECO.finditer(texto):
        hueco = m.group()
        sin = ''.join(c for c in hueco if not _es_comilla(c))
        if sin == hueco:
            continue
        i, j = m.span()
        plegado = _plegar_homografos(_cita_legible(sin))
        if (0 < i and j < len(texto) and texto[i - 1].isalnum() and texto[j].isalnum()
                and (not plegado or _UNION.fullmatch(plegado) or _PARTIDA.fullmatch(plegado))):
            out += [texto[desde:i], '-' + ' ' * (j - i - 1)]
        else:
            out += [texto[desde:i], ''.join(' ' if _es_comilla(c) else c for c in hueco)]
        desde = j
    out.append(texto[desde:])
    return ''.join(out)


def _comillas_en_blanco(texto) -> str:
    """AC208: el texto con cada comilla (`_es_comilla`) como un espacio, del mismo largo: la lectura en que ninguna
    comilla une ni parte un nombre (las fichas leen «, » y las demás de Unicode como signos que parten, AC191). La de
    las exenciones."""
    return ''.join(' ' if _es_comilla(c) else c for c in texto)


def _lecturas_texto(texto) -> tuple[str, ...]:
    """AC202/AC208: las lecturas de un texto cuyos hallazgos se suman: la primera, la base (`_comillas_en_blanco`, sin
    leer a través de ninguna comilla), la única que exime; el texto tal cual (las fichas), a través de sus comillas
    solas (`_comillas_solas`, la de la enmienda 39) y con los huecos de AC195 (`_comillas_partidas`). Una lectura a
    través de comillas solo añade hallazgos."""
    return tuple(dict.fromkeys((_comillas_en_blanco(texto), texto, _comillas_solas(texto), _comillas_partidas(texto))))


def _pasadas(texto, *indices):
    """AC202/AC208: [(lectura, índices)] de un texto (`_lecturas_texto`) con los índices de nombres base (`_base`: sin
    las lecturas añadidas de sus nombres) y la de AC195 con los índices completos (AC201/AC205), sin repetir un par. El
    primero, la pasada base (sin leer a través de comillas, con los índices base): la única que exime; lo que hallan
    las demás cuenta salvo si cae dentro de lo que ella exime."""
    bases = tuple(map(_base, indices))
    pares = [(lectura, bases) for lectura in _lecturas_texto(texto)] + [(_comillas_partidas(texto), indices)]
    return list({(lectura, tuple(map(id, idx))): (lectura, idx) for lectura, idx in pares}.values())


def _dentro_de(tramos):
    """AC208: el predicado de si un tramo (inicio, fin) de un texto cae dentro de alguno de `tramos` (los eximentes de
    la pasada base), por bisección: los inicios ordenados y, de cada uno, el mayor fin de los que empiezan en él o
    antes."""
    orden = sorted(tramos)
    inicios, fines = [a for a, _ in orden], list(itertools.accumulate((b for _, b in orden), max))

    def dentro(inicio, fin):
        k = bisect.bisect_right(inicios, inicio) - 1
        return k >= 0 and fines[k] >= fin
    return dentro


def _exentos_de(eximentes, planos=None):
    """AC211: el predicado `(clave, inicio, fin, igual) -> bool` de si una detección de una lectura que no es la base
    cae dentro de un tramo que la pasada base exime. `eximentes`: [(clave, inicio, fin)], la identidad de lo que la base
    halla y exime (la entidad) y su tramo en el texto original. Si la lectura es, en el tramo de la detección, la de la
    base (`igual`: solo cambia el índice, que añade a los nombres su lectura a través de sus comillas, AC201), vale
    cualquier tramo que exime (`planos`; por defecto, los de `eximentes`): como en AC208. Si no, lo que ve es por una
    comilla del texto, y solo vale un tramo que la base exime de la misma clave: una posición no es una identidad, y
    lo que la base no ve no se exime nunca, aunque su tramo caiga dentro de uno que exime a otra cosa."""
    por_clave = {}
    for clave, inicio, fin in eximentes:
        por_clave.setdefault(clave, []).append((inicio, fin))
    dentro = {clave: _dentro_de(tramos) for clave, tramos in por_clave.items()}
    cualquiera = _dentro_de([(i, f) for _, i, f in eximentes] if planos is None else planos)

    def exento(clave, inicio, fin, igual):
        return cualquiera(inicio, fin) if igual else clave in dentro and dentro[clave](inicio, fin)
    return exento


def _tramo_de(posiciones, aparicion) -> tuple[int, int] | None:
    """El tramo en el texto original (`_posiciones` de la lectura) de una aparición, o None sin posiciones."""
    return (posiciones[aparicion.inicio][0], posiciones[aparicion.fin - 1][1]) if posiciones else None


def _sin_comillas(texto) -> tuple[str, list[int]]:
    """AC151: el texto sin sus comillas (`_es_comilla`) y, de cada uno de sus caracteres, su posición en él."""
    origen = [i for i, c in enumerate(texto) if not _es_comilla(c)]
    return ''.join(texto[i] for i in origen), origen


def _sin_partidas(texto) -> tuple[str, list[int]]:
    """AC191: el texto (ya sin comillas) sin los signos de la guarda de cierre pegados a una letra o cifra por cada
    lado (`_PARTIDA_ENTRE`): la lectura que los lee como ausentes («20✓25» es «2025», «oc✓tubre» es «octubre»), que se
    suma a la que los lee como separadores; y, de cada uno de sus caracteres, su posición en el texto."""
    fuera = {i for m in _PARTIDA_ENTRE.finditer(texto) for i in range(m.start(), m.end())}
    quedan = [i for i in range(len(texto)) if i not in fuera]
    return ''.join(texto[i] for i in quedan), quedan


@functools.lru_cache(maxsize=65536)
def _plegada(c) -> str:
    """Un carácter como lo pliegan las fichas (`_plegar_homografos` de `_cita_legible`), en caché: `_posiciones` pliega
    el texto carácter a carácter."""
    return _plegar_homografos(_cita_legible(c))


def _posiciones(texto) -> list[tuple[int, int]] | None:
    """AC151/AC152: [(inicio, fin)] en `texto` de cada pieza de `_fichas(texto)`, con el texto plegado carácter a
    carácter; None si ese plegado no da las mismas piezas que el del texto entero."""
    plegado, origen = [], []
    for i, c in enumerate(texto):
        letras = _plegada(c)
        plegado.append(letras)
        origen += [i] * len(letras)
    piezas = list(_PALABRA_PROSA.finditer(''.join(plegado)))
    if tuple(m.group() for m in piezas) != _fichas(texto).piezas:
        return None
    return [(origen[m.start()], origen[m.end() - 1] + 1) for m in piezas]


def _datos_en(texto, censo, sociedades, propios, iso=False) -> list[tuple[str, int, int]]:
    """AC120/AC138/AC151: [(motivo, inicio, fin)] de cada dato civil de un texto, con su posición en él y sin sus campos
    cerrados (`_sin_cerrados`, con `iso` también las fechas ISO): cada fecha o año (`_FECHA_SUELTA`, AC126 en otras
    grafías), 'fecha', en el texto sin comillas (`_sin_comillas`); cada sociedad real del índice (`sociedades`; AC149,
    también por su núcleo distintivo) o denominación con forma jurídica (`_formas_en`, AC121) que no es de `propios`
    (los extremos de su fila: la arista ya los publica), 'sociedad', con cada comilla entre dos letras o cifras como un
    punto de partición. La de un extremo es la que su nombre completo, un alias o su núcleo cubre sin una palabra con
    mayúscula inicial unida delante por «de», «del», «de la», «de los», «de las», «y» o «e» (AC152, `_UNIONES`:
    «Talleres de Nimbo»; «administrador de Nimbo», sí es la suya), y lo que cae dentro de ella en todas sus lecturas
    (`_exenta`: otra sociedad con el mismo núcleo); la que lleva una forma jurídica, la que su nombre cubre con la forma
    justo detrás («Nimbo» + «S.L.»; la UTE, no: es otra entidad), sin esa palabra, sin una palabra delante que pueda ser
    parte de ella (`_INTRODUCTORAS`: «Grupo Nimbo S.L.» o «Cirro y Nimbo S.L.» solo comparten palabras con «Nimbo») ni
    un prefijo de denominación (`_prefijada`, AC121-bis: «Hijos de Nimbo S.L.») ni, la de una UTE, una palabra
    detrás. AC169: las fechas, en todas las lecturas del texto (`_fechas_en`: NFKC y las palabras partidas unidas).
    AC171: al otro lado de un límite de frase (`_limite_de_frase`), una palabra no es prefijo, introductora ni parte
    de la denominación: «Pagó. Nimbo S.L. cobró.» es el extremo propio; «Hnos. Nimbo S.L.», otra sociedad. AC178: las
    fechas, también en la lectura de los nombres (`_fechas_en`), sobre el texto con sus comillas y sus campos cerrados
    en blanco. AC179: la exención de una forma jurídica por un extremo propio, sin recorrer cada par."""
    # AC202/AC208: lo que exime una sociedad sale solo de la pasada base (el texto sin leer a través de ninguna
    # comilla, con el índice base y sus campos cerrados), con su identidad (la entidad) y su tramo: las menciones
    # del extremo propio y lo que ellas eximen (`_sociedades_en`). Las demás pasadas (`_pasadas`) no eximen nada por
    # su cuenta: lo que hallan cuenta salvo si lo eximen ellas mismas y además, en un tramo que la base exime y, si
    # la lectura no es en él la de la base (una comilla del texto), por la misma sociedad (o una forma jurídica), lo
    # exime la base (AC211: «Pagó a Nim"bo.» o «Nim«-»bo S.L.» con Nimbo de extremo propio, y «Pagó a Nim«bo».» con
    # «Nim Bo» propia y «Nimbo» ajena: otra lectura lo halla, y la base no lo ve; y «Cen«-»tinela y Nimbo.»: la base no
    # une «Centinela» a «Nimbo» y lo da por propio, la otra sí y no). Los campos cerrados, los del texto tal cual y los
    # de su lectura sin comillas (la de la enmienda 39), cada uno sobre el texto con los suyos en blanco y los
    # hallazgos sumados: un campo que solo se cierra en una no exime lo que la otra ve («ocho"1999 euros»: tal cual,
    # un importe; sin comillas, un año) ni lo que ella halla a través de él («10 d3»1999.de ma«y»o»: sin comillas,
    # «d31999» se cierra y queda «10 … de mayo»).
    tapados = [frozenset(j for j, (antes, despues) in enumerate(zip(texto, _sin_cerrados(texto, censo, iso)))
                         if antes != despues)]
    junto, origen = _sin_comillas(texto)
    if junto != texto:
        tapados = list(dict.fromkeys([*tapados, frozenset(
            origen[j] for j, (antes, despues) in enumerate(zip(junto, _sin_cerrados(junto, censo, iso)))
            if antes != despues)]))

    def blanco(lectura, tapado):
        return ''.join(' ' if i in tapado else c for i, c in enumerate(lectura))
    out = [('fecha', *f) for tapado in tapados for f in _fechas_en(blanco(texto, tapado))]
    (base, (indice_base,)), *otras = _pasadas(texto, sociedades)
    eximentes, presupuesto = [], _Presupuesto()   # AC224: el de la exención de todas las lecturas de la llamada
    out += [('sociedad', i, f) for _, i, f in
            _sociedades_en(texto, blanco(base, tapados[0]), indice_base, propios, eximentes, presupuesto)]
    exento = _exentos_de([e for e in eximentes if e[2] <= len(texto)])   # sin posiciones, ningún tramo exime
    for k, tapado in enumerate(tapados):
        for lectura, (indice,) in (otras if k == 0 else [(base, (indice_base,)), *otras]):
            eximidas = []   # AC211: las que la propia lectura exime; solo exime si además las exime la base
            out += [('sociedad', i, f) for _, i, f in
                    _sociedades_en(texto, blanco(lectura, tapado), indice, propios, eximidas, presupuesto)]
            out += [('sociedad', i, f) for clave, i, f in eximidas
                    if not exento(clave, i, f, base[i:f] == lectura[i:f])]
    return list(dict.fromkeys(out))


def _sociedades_en(texto, partido, sociedades, propios, eximentes=None,
                   presupuesto=None) -> list[tuple[tuple, int, int]]:
    """[(clave, inicio, fin)] de las sociedades de una lectura (`partido`, del largo de `texto`, con sus campos
    cerrados en blanco) con un índice de sociedades (`sociedades`), salvo las de los extremos propios (`propios`): ver
    `_datos_en`. La clave es la identidad de lo hallado: ('indice', entidad) o ('forma',) (una denominación con forma
    jurídica, sin entidad). La mayúscula y los límites de frase, del texto original. AC208/AC211: con
    `eximentes` (una lista), le añade [(clave, inicio, fin)] de lo que eximen: cada mención propia que exime y cada
    sociedad o forma jurídica eximida; sin posiciones, con el tramo (0, largo + 1), que ningún tramo del texto
    contiene. AC224: la exención gasta el `presupuesto` de la llamada a `_datos_en`, como la de `_nombres_en`."""
    fichas, posiciones = _fichas(partido), _posiciones(partido)
    piezas = fichas.piezas

    def donde(inicio, fin):   # sin posiciones (no ocurre con un texto corriente), en ningún tramo
        return (posiciones[inicio][0], posiciones[fin - 1][1]) if posiciones else (0, len(texto) + 1)

    # AC152: de cada pieza g, si alguna de la palabra que acaba en ella (las unidas a ella por puntos de partición)
    # empieza en mayúscula; AC199: en una pasada, sin recorrer hacia atrás el tramo unible en cada consulta.
    mayuscula = []
    for g in range(len(piezas)):
        mayuscula.append(posiciones is None or texto[posiciones[g][0]].isupper()
                         or (g > 0 and fichas.unibles[g - 1] and mayuscula[g - 1]))
    limites = [posiciones is not None and 0 < g
               and _limite_de_frase(texto[posiciones[g - 1][1]:posiciones[g][0]], piezas[g - 1])
               for g in range(len(piezas))]

    def frontera(g):   # AC171: un límite de frase entre la pieza g-1 y la g, en el texto original
        return 0 < g < len(piezas) and limites[g]

    def unida(a):
        return any(a > len(u) and all(_es_forma(t, q) for t, q in zip(piezas[a - len(u):a], u))
                   and mayuscula[a - len(u) - 1] and not any(frontera(g) for g in range(a - len(u), a + 1))
                   for u in _UNIONES)
    unidas_ya = {}   # AC224: una vez por pieza, no por cada aparición ni cada contenedora que la contiene

    def suya(a):   # AC152: la del extremo propio, sin una palabra con mayúscula inicial unida delante
        if a.eid not in propios:
            return False
        if a.inicio not in unidas_ya:
            unidas_ya[a.inicio] = unida(a.inicio)
        return not unidas_ya[a.inicio]
    apariciones = _apariciones_indice(fichas, sociedades)
    contenedoras, memoria = _Contenedoras(apariciones, propios, suya), {}   # AC228: las suyas, filtradas una vez
    # AC232: una lista en el orden canónico, no un conjunto de apariciones (se tabulan por la identidad de su candidato)
    eximida = [suya(a) or _exenta(fichas, a, contenedoras, memoria, presupuesto) for a in apariciones]
    exentas = [a for a, exime in zip(apariciones, eximida) if exime]
    out = [(('indice', a.eid), *donde(a.inicio, a.fin)) for a, exime in zip(apariciones, eximida) if not exime]
    suyas = [(a.inicio, a.fin) for a in apariciones if a.eid in propios]
    libres = {a: (a == 0 or frontera(a) or piezas[a - 1] in _INTRODUCTORAS) and not _prefijada(piezas, a, frontera)
              and not unida(a) for a in {a for a, _ in suyas}}   # una vez por inicio, no por forma
    # La forma (inicio, fin) es de un extremo si una mención propia libre empieza en su inicio o antes y acaba en su fin
    # o después, o justo tras su inicio; la de una UTE, si además acaba donde acaba el texto o ante una introductora.
    validas = sorted((a, b) for a, b in suyas if libres[a])
    cerradas = [(a, b) for a, b in validas if b == len(piezas) or piezas[b] in _INTRODUCTORAS]
    acaban = {b for _, b in validas}
    hasta = [([a for a, _ in menciones], list(itertools.accumulate((b for _, b in menciones), max)))
             for menciones in (validas, cerradas)]   # de cada inicio, el mayor fin de las que empiezan en él o antes

    def de_un_extremo(inicio, fin, ute):
        inicios, fines = hasta[ute]
        k = bisect.bisect_right(inicios, inicio) - 1
        return (k >= 0 and fines[k] >= fin) or (not ute and inicio + 1 in acaban)
    formas = _formas_en(fichas, frontera)
    out += [(('forma',), *donde(inicio, fin)) for inicio, fin, ute in formas if not de_un_extremo(inicio, fin, ute)]
    if eximentes is not None:
        eximentes += [(('indice', a.eid), *donde(a.inicio, a.fin)) for a in exentas]
        eximentes += [(('forma',), *donde(inicio, fin)) for inicio, fin, ute in formas
                      if de_un_extremo(inicio, fin, ute)]
    return out


def _nfkc_con_mapa(texto, plegar=True) -> tuple[str, list[int]]:
    """AC169: el texto en NFKC, carácter a carácter (la lectura de la guarda Unicode, `texto_sospechoso`: «²⁰²⁵» y
    «𝟚𝟘𝟚𝟝» son «2025»), y de cada carácter suyo la posición del que lo produce. AC191: con `plegar`, con los
    confusables de puntuación en su signo ASCII antes y tras NFKC (`_CONFUSABLES`)."""
    out, mapa = [], []
    for i, c in enumerate(texto):
        n = unicodedata.normalize('NFKC', c.translate(_CONFUSABLES)).translate(_CONFUSABLES) if plegar \
            else unicodedata.normalize('NFKC', c)
        out.append(n)
        mapa += [i] * len(n)
    return ''.join(out), mapa


def _particiones_unidas(texto) -> tuple[str, list[int]]:
    """AC169: el texto con cada palabra partida por un guion o una raya unida (`_PARTICION`, con el guion blando como
    un guion: «octu-\\nbre» es «octubre»), y de cada carácter suyo su posición en el texto."""
    texto = texto.replace('\u00ad', '-')
    out, mapa, desde = [], [], 0
    for m in _PARTICION.finditer(texto):
        out.append(texto[desde:m.start()])
        mapa += range(desde, m.start())
        desde = m.end()
    out.append(texto[desde:])
    mapa += range(desde, len(texto))
    return ''.join(out), mapa


# AC178: la lectura de las fechas que comparte las de los nombres (`_lectura_de_fechas`): un punto de partición es una
# marca (`_MARCA`) que el patrón (`_FECHA_LECTURAS`) une o separa en cada sitio por su cuenta; un dígito de una palabra
# con letras, uno ambiguo (`_AMBIGUOS`) que lee como él mismo o como una de sus letras (`_DIGITO_LETRAS`); AC189: un
# ordinal, uno ambiguo (`_ORDINALES_AMBIGUOS`) que lee como él mismo o como su letra; una comilla, mientras se lee,
# `_COMILLA`. Son de uso privado: uno así en el texto se lee como un espacio.
_MARCA, _COMILLA = '\ue000', '\ue001'
_AMBIGUOS = {d: chr(0xe010 + int(d)) for d in '0123456789'}
_ORDINALES_AMBIGUOS = {'\u00aa': '\ue01a', '\u00ba': '\ue01b'}
_LEE_COMO = {**{_AMBIGUOS[d]: d + letras for d, letras in _DIGITO_LETRAS.items()},
             _ORDINALES_AMBIGUOS['\u00aa']: '\u00aaa', _ORDINALES_AMBIGUOS['\u00ba']: '\u00bao'}
_A_AMBIGUOS = str.maketrans({**_AMBIGUOS, **_ORDINALES_AMBIGUOS})
_PRIVADOS = frozenset(map(chr, range(0xe000, 0xe020)))
_CUANTIFICADOR = re.compile(r'(?:[?*+]|\{\d+(?:,\d*)?\})[?+]?')
# AC178: lo que separa dos palabras en la lectura de las fechas, como entre las piezas de los nombres (`_fichas`):
# cualquier carácter que no es letra ni cifra («ocho_de_octubre», «ocho.de.octubre») y la marca (un punto de partición
# separado); no un dígito ambiguo.
_SEPARADOR = f'(?:[^\\w{chr(0xe001)}-{chr(0xe01f)}]|_)'
# AC196: el de una búsqueda que ha de casar en todas las lecturas: sin la marca (en la lectura que une, no separa).
_SEPARADOR_EN_TODAS = f'(?:[^\\w{chr(0xe000)}-{chr(0xe01f)}]|_)'
# AC188: los separadores de los componentes de una fecha numérica o romana («8-X-25», «8/10/25», «8.10.2025»): una clase
# del patrón hecha solo de ellos también admite la marca (un punto de partición separado: «8-X1-25»).
_SEPARADORES_DE_COMPONENTES = frozenset('/.-')


def _con_lecturas(fuente, separar=False) -> str:
    """AC178: el patrón `fuente` (el de `_FECHA_SUELTA`) para la lectura de `_lectura_de_fechas`: tras cada letra, una
    marca opcional (la palabra sigue tras un punto de partición unido; AC188: con `separar`, solo si hace falta, y si
    no, la marca separa: en cada sitio, también la coincidencia que la deja separada, «dos mil-veinte
    treinta«-»agos-to»); cada `\\s`, cualquier separador de palabras (`_SEPARADOR`: el de los nombres,
    también una marca); cada letra, también los dígitos y ordinales ambiguos que la leen (`_LEE_COMO`); cada dígito,
    cada ordinal y cada clase con ellos o con letras, también los suyos; AC188: cada clase solo de separadores de
    componentes (`_SEPARADORES_DE_COMPONENTES`), también la marca. Con espacios entre las palabras, sin marcas ni
    dígitos u ordinales ambiguos, casa exactamente lo mismo que `fuente`. En una búsqueda negativa («(?!…)», «(?<!…)»),
    un ambiguo cuenta solo si todas sus lecturas casan, y la marca no: la fecha se ve si alguna lectura la deja ver
    («octubre.mªyo», con la ª como ordinal, sigue siendo «octubre» y el año «M»). AC196: tampoco tras una letra ni
    como separador (`_SEPARADOR_EN_TODAS`): lo que la búsqueda casa ha de casar en la lectura que une cada punto de
    partición y en la que lo separa («dos-mil veinte eu→ros»: separado, «eu ros» no es una moneda y el año se ve); en
    una búsqueda negativa dentro de otra, al revés (basta una lectura), y una clase con letras o cifras admite también
    la marca (en la lectura que une, le sigue una letra o una cifra: «euros✓x»). De su sintaxis, la que usa
    `_FECHA_SUELTA`: escapes de un carácter, clases sin escapes ni negación, grupos (también búsquedas), alternativas
    y cuantificadores."""
    def leidos(caracteres, todas):   # los dígitos y ordinales ambiguos que se leen como uno de `caracteres`
        plegados = set(caracteres) | {x.lower() for x in caracteres}
        return ''.join(a for a, lecturas in _LEE_COMO.items()
                       if (all if todas else any)(lectura in plegados for lectura in lecturas))
    # por cada grupo abierto: si ha de casar en todas las lecturas (dentro de un número impar de búsquedas negativas)
    # y si está dentro de una búsqueda
    out, i, grupos = [], 0, [(False, False)]
    while i < len(fuente):
        c, (todas, busqueda) = fuente[i], grupos[-1]
        if c == '\\':
            atomo = {r'\d': f'[0-9{leidos("0123456789", todas)}]',
                     r'\s': _SEPARADOR_EN_TODAS if todas else _SEPARADOR}.get(fuente[i:i + 2], fuente[i:i + 2])
            letra, i = False, i + 2
        elif c == '[':
            fin = fuente.index(']', i + 1)
            cuerpo, caracteres = fuente[i + 1:fin], []
            if '\\' in cuerpo or cuerpo.startswith('^'):
                raise ValueError('una clase con escapes o negada')
            k = 0
            while k < len(cuerpo):
                rango = k + 2 < len(cuerpo) and cuerpo[k + 1] == '-'
                caracteres += map(chr, range(ord(cuerpo[k]), ord(cuerpo[k + 2]) + 1)) if rango else cuerpo[k]
                k += 3 if rango else 1
            separa = set(caracteres) <= _SEPARADORES_DE_COMPONENTES
            marca = _MARCA if not todas and (separa or busqueda and any(x.isalnum() for x in caracteres)) else ''
            # lo añadido, delante: tras un «-» final («[/.-]») sería un rango
            atomo = '[' + marca + leidos(caracteres, todas) + cuerpo + ']'
            letra, i = cuerpo.isalpha(), fin + 1
        elif c.isalnum():
            atomo, letra, i = (f'[{c}{leidos(c, todas)}]' if leidos(c, todas) else c), c.isalpha(), i + 1
        else:   # un grupo, una alternativa o un literal que no es letra ni cifra; tras un grupo, su cuantificador tal
            # cual («){1,2}»: sus cifras no son átomos)
            if c == '(':
                grupos.append((todas != fuente.startswith(('(?!', '(?<!'), i),
                               busqueda or fuente.startswith(('(?=', '(?!', '(?<=', '(?<!'), i)))
            elif c == ')':
                grupos.pop()
            cuantificador = _CUANTIFICADOR.match(fuente, i + 1) if c == ')' else None
            out.append(c + (cuantificador.group() if cuantificador else ''))
            i += 1 + (len(cuantificador.group()) if cuantificador else 0)
            continue
        cuantificador = _CUANTIFICADOR.match(fuente, i)
        q = cuantificador.group() if cuantificador else ''
        i += len(q)
        marca = '' if todas else f'{_MARCA}??' if separar else f'{_MARCA}?'
        out.append((f'(?:{atomo}{marca}){q}' if q else f'{atomo}{marca}') if letra and marca else atomo + q)
    return ''.join(out)


_FECHA_LECTURAS = re.compile(_con_lecturas(_FECHA_SUELTA.pattern), re.I)
# AC188: cada fecha de la lectura, también la que empieza dentro de otra («ºcho de octubre MMXXV»: «ocho de octubre»
# y «octubre MMXXV»), como la ven por separado las demás lecturas: una no tapa a otra; y en cada sitio, la que une cada
# punto de partición que puede («MMXXV-1ª», el año MMXXVI) y la que lo separa si no hace falta.
_FECHA_SOLAPADAS = tuple(re.compile(f'(?=({_con_lecturas(_FECHA_SUELTA.pattern, separar)}))', re.I)
                         for separar in (False, True))
_PUNTO_DE_PARTICION = re.compile(rf'(?<=[^\W_])(?:(?P<raya>[-\u2010-\u2015])\s*|-*{_OTRO_SIGNO}(?:-|{_OTRO_SIGNO})*)'
                                 r'(?=[^\W_])')
# AC191: un signo de la guarda de cierre pegado a una letra o cifra por cada lado (`_PARTIDA`), en la lectura que lo lee
# como ausente (`_sin_partidas`).
_PARTIDA_ENTRE = re.compile(rf'(?<=[^\W_])-*{_OTRO_SIGNO}(?:-|{_OTRO_SIGNO})*(?=[^\W_])')
_PALABRA_DE_FECHA = re.compile(rf'(?:[^\W_]|{_MARCA})+')
_COMILLAS_SEGUIDAS = re.compile(f'{_COMILLA}+')


@functools.lru_cache(maxsize=65536)
@functools.lru_cache(maxsize=65536)
def _plegable(c) -> bool:
    """AC191: ¿es `c`, o tras NFKC, un confusable de puntuación (`_CONFUSABLES`)?"""
    return c.translate(_CONFUSABLES) != c or unicodedata.normalize('NFKC', c).translate(_CONFUSABLES) != \
        unicodedata.normalize('NFKC', c)


@functools.lru_cache(maxsize=65536)
def _plegado_de_fecha(c, plegar=True) -> str:
    """AC178: un carácter como lo leen los nombres (`_plegar_homografos` de `_cita_legible`, sin pasar a minúsculas: el
    patrón no distingue caja): en NFKC y sin lo que no se ve, sin diacríticos, con los homógrafos de otro alfabeto
    como letras latinas y, AC191, con `plegar`, los confusables de puntuación en su signo ASCII (`_CONFUSABLES`); el
    guion blando, un guion; los ordinales («º», «ª»), tal cual, como los lee el patrón; una comilla (`_es_comilla`),
    `_COMILLA`; uno de uso privado de la lectura, un espacio."""
    if _es_comilla(c):
        return _COMILLA
    if c in _PRIVADOS:
        return ' '
    if c == '\u00ad':
        return '-'
    if c in _ORDINALES:
        return c
    confusables = _CONFUSABLES if plegar else {}
    c = ''.join(x for x in unicodedata.normalize('NFKC', c.translate(confusables)) if not _ignorable(x))
    c = ''.join(x for x in unicodedata.normalize('NFKD', c) if unicodedata.category(x) != 'Mn')
    return c.translate(_HOMOGRAFOS).translate(confusables)


def _sustituida(texto, mapa, patron, reemplazo) -> tuple[str, list[int]]:
    """AC178: `texto` con cada coincidencia de `patron` cambiada por `reemplazo(texto, coincidencia)` (None: tal cual)
    y, de cada carácter, su posición en el original (`mapa`): la de cada uno si el cambio es del mismo largo; si no, la
    del primero de la coincidencia."""
    out, nuevo, desde = [], [], 0
    for m in patron.finditer(texto):
        cambio = reemplazo(texto, m)
        if cambio is None:
            continue
        out += [texto[desde:m.start()], cambio]
        nuevo += mapa[desde:m.start()]
        nuevo += mapa[m.start():m.end()] if len(cambio) == m.end() - m.start() else [mapa[m.start()]] * len(cambio)
        desde = m.end()
    out.append(texto[desde:])
    nuevo += mapa[desde:]
    return ''.join(out), nuevo


def _comillas_de_fecha(texto, m):
    """AC177/AC178: una secuencia de comillas entre dos letras o cifras, un punto de partición; AC188: las demás, nada,
    como en la lectura sin comillas (AC151): junto a un separador, no lo apartan de los componentes que une
    («8«-»X1«-»25»)."""
    entre = 0 < m.start() and m.end() < len(texto) and texto[m.start() - 1].isalnum() and texto[m.end()].isalnum()
    return _MARCA if entre else ''


def _particion_de_fecha(texto, m):
    """AC178: un guion o una raya entre dos letras o cifras, un punto de partición, como en `_fichas`: salvo entre dos
    números («2020-2021»); ahí, AC188, la raya es el guion que separa los componentes de una fecha («el 8–10»), como en
    los nombres una raya es un guion. AC191: igual un signo de la guarda de cierre (`_PARTIDA`): entre dos números, el
    guion de una fecha («8✓10✓25»; su lectura como ausente, en `_sin_partidas`)."""
    i, j = m.start(), m.end()
    while i > 0 and texto[i - 1].isalnum():
        i -= 1
    while j < len(texto) and texto[j].isalnum():
        j += 1
    antes, despues = texto[i:m.start()], texto[m.end():j]
    numeros = antes[-1].isdigit() and despues[0].isdigit() and not _mezcla(antes) and not _mezcla(despues)
    if not numeros:
        return _MARCA
    if m.group('raya') is None:
        return '-'
    return None if m.group('raya') == '-' else '-' + m.group()[1:]


def _ambiguos_de_fecha(texto, m):
    """AC178: en una palabra con letras (unida por puntos de partición o no), cada dígito, ambiguo; AC189: y cada
    ordinal («mªyo», «ºcho»; «1.º» sigue leyéndose como ordinal)."""
    palabra = m.group()
    return palabra.translate(_A_AMBIGUOS) if any(c.isalpha() for c in palabra) else None


def _lectura_de_fechas(texto, plegar=True) -> tuple[str, list[int]]:
    """AC178: la lectura de un texto que comparte las de los nombres (`_fichas`), para `_FECHA_LECTURAS`: plegado
    carácter a carácter como ellos (`_plegado_de_fecha`), con cada punto de partición —una secuencia de comillas entre
    dos letras o cifras (AC177) y un guion o una raya entre dos, salvo entre dos números, donde una raya es un guion
    (AC188)— como una marca (`_MARCA`), las demás comillas como nada (AC188) y cada dígito y cada ordinal de una
    palabra con letras como ambiguo (AC189, `_A_AMBIGUOS`); y, de cada carácter suyo, la posición del que lo
    produce. AC191: con `plegar`, con los confusables de puntuación en su signo ASCII."""
    partes = [_plegado_de_fecha(c, plegar) for c in texto]
    lectura, mapa = ''.join(partes), [i for i, parte in enumerate(partes) for _ in parte]
    for patron, reemplazo in ((_COMILLAS_SEGUIDAS, _comillas_de_fecha), (_PUNTO_DE_PARTICION, _particion_de_fecha),
                              (_PALABRA_DE_FECHA, _ambiguos_de_fecha)):
        lectura, mapa = _sustituida(lectura, mapa, patron, reemplazo)
    return lectura, mapa


def _fechas_en(texto) -> list[tuple[int, int]]:
    """AC126/AC169/AC178: [(inicio, fin)] en el texto (sus campos cerrados ya en blanco) de cada fecha o año
    (`_FECHA_SUELTA`) en cualquiera de sus lecturas: sin sus comillas (AC151), tal cual y en NFKC (`_nfkc_con_mapa`),
    con las palabras partidas unidas o no (`_particiones_unidas`); y, AC178, en la de los nombres (`_lectura_de_fechas`,
    con `_FECHA_LECTURAS`): cada punto de partición unido o separado por su cuenta («ocho-de-octu-bre»), una secuencia
    de comillas como uno (AC177) y cada dígito de una palabra con letras como él mismo o una de sus letras («8 de
    0ctubre»); AC188/AC189: también entre los componentes («8-X1-25»), con los ordinales («8 d3 mªyo») y cada fecha,
    también la que empieza dentro de otra, uniendo y separando primero (`_FECHA_SOLAPADAS`). AC191: con los
    confusables de puntuación en su signo ASCII (`_CONFUSABLES`) y con los signos de la guarda de cierre pegados a una
    letra o cifra por cada lado como ausentes (`_sin_partidas`) y como separadores; cada lectura suma, ninguna quita:
    con un confusable, también las de antes del pliegue («del 10·8» es el año 10; plegado, «10.8» es un decimal)."""
    texto, out = str(texto), set()
    plegado = texto.translate(_CONFUSABLES)
    sin_plegar = any(_plegable(c) for c in texto if c > '\x7f')   # también uno que lo es tras NFKC (U+0387)
    for t, plegar in ((plegado, True), *(((texto, False),) if sin_plegar else ())):
        junto, origen = _sin_comillas(t)
        bases = [(junto, origen)]
        sin, quedan = _sin_partidas(junto) if plegar else (junto, None)
        if sin != junto:
            bases.append((sin, [origen[i] for i in quedan]))
        for base, origen in bases:
            nfkc, a_base = _nfkc_con_mapa(base, plegar)
            for lectura, mapa in ((base, range(len(base))), (nfkc, a_base)):
                unida, a_lectura = _particiones_unidas(lectura)
                for u, m in ((lectura, mapa), (unida, [mapa[k] for k in a_lectura])):
                    out.update((origen[m[x.start()]], origen[m[x.end() - 1]] + 1) for x in _FECHA_SUELTA.finditer(u))
        lectura, mapa = _lectura_de_fechas(t, plegar)
        patrones = _FECHA_SOLAPADAS if _MARCA in lectura else _FECHA_SOLAPADAS[:1]   # sin marcas, casan igual
        for x in (x for patron in patrones for x in patron.finditer(lectura)):
            fin = x.end(1)
            while lectura[fin - 1] == _MARCA:   # la marca del final es la de una palabra que sigue, no de la fecha
                fin -= 1
            out.add((mapa[x.start()], mapa[fin - 1] + 1))
    return sorted(out)


_COMILLAS = {'«': '»', '“': '”', '"': '"'}


def _tramos_citados(texto) -> list[tuple[int, int]] | None:
    """AC123: [(inicio, fin)] del interior de cada tramo entre comillas «…», “…” o "…" de un texto, si todas están
    equilibradas y ninguna anidada; None si no: entonces el campo entero es prosa, sin exención."""
    out, cierre, inicio = [], None, 0
    for i, ch in enumerate(texto):
        if cierre is None and ch in _COMILLAS:
            cierre, inicio = _COMILLAS[ch], i + 1
        elif cierre is not None and ch == cierre:
            out.append((inicio, i))
            cierre = None
        elif ch in _COMILLAS or ch in _COMILLAS.values():
            return None
    return None if cierre is not None else out


def problemas_dato_civil(texto, tipo, censo, sociedades, propios=frozenset(), doc=None, prueba=None) -> list[str]:
    """AC120: por qué un texto público junto a un seudónimo lleva un dato civil, o []: la regla de contenido
    (`_datos_en`; en una `curacion`, su fecha ISO es campo cerrado). AC123/AC138/AC151: en la prosa, la regla mira el
    texto entero, sin que sus comillas partan un dato, y la prueba de que un tramo entre comillas equilibradas
    (`_tramos_citados`) es cita literal (`prueba(tramo, doc)`: vía (a) del export o (b) de `citas:`, `prueba_cita`)
    exime solo el dato que está entero dentro de él: el que cruza su límite («de «Centinela» S.L.», «el «8/10»»,
    «08»/«10»/«2025»») o está fuera, nunca. Un dato dentro de un tramo sin prueba: 'tramo'; sin documento del campo en
    que probarlo (AC124): 'sin-documento'. En la procedencia generada (`tipo` 'procedencia'), sin exención: cada
    decisión encadenada sin sus prefijos cerrados (`_PREFIJOS`) es una fórmula cerrada (`_FORMULAS`) o pasa la regla de
    contenido. El nombre civil, AC78."""
    texto = str(texto or '').strip()
    if tipo != 'procedencia':
        tramos = _tramos_citados(texto) or []
        inicios, out = [a for a, _ in tramos], []   # disjuntos y en orden: el único que puede contener un dato
        for motivo, x, y in _datos_en(texto, censo, sociedades, propios, iso=tipo == 'curacion'):
            k = bisect.bisect_right(inicios, x) - 1   # AC179: sin recorrerlos todos por cada dato
            k = k if k >= 0 and y <= tramos[k][1] else None
            if k is None:
                out.append(motivo)
            elif doc is None:
                out.append('sin-documento')
            elif not (prueba and prueba(texto[tramos[k][0]:tramos[k][1]], doc)):
                out.append('tramo')
        return list(dict.fromkeys(out))
    out = []
    for segmento in texto.split(' · '):
        resto, cambio = segmento.strip(), True
        while cambio:
            cambio = False
            for prefijo in _PREFIJOS:
                m = prefijo.match(resto)
                if m:
                    resto, cambio = resto[m.end():], True
        if not _FORMULAS.fullmatch(resto):
            out += [m for m in dict.fromkeys(m for m, _, _ in _datos_en(resto, censo, sociedades, propios))
                    if m not in out]
    return out


def _doc_del_campo(fila, ruta) -> str | None:
    """AC124: el documento en que se prueba un tramo citado de un campo de prosa: el de la fuente del estado para su
    nota (`estado.fuente.doc`), el de la regla para su `set.note`; los demás, ninguno."""
    if ruta == 'estado.nota':
        estado = fila.get('estado') if isinstance(fila.get('estado'), dict) else {}
        fuente = estado.get('fuente') if isinstance(estado.get('fuente'), dict) else {}
        return _texto(fuente.get('doc')) or None
    if ruta == 'set.note':
        return next(iter(documentos_regla(fila)), None)
    return None


def textos_dato_civil(rel, seccion, fila) -> list[tuple[str, str, str | None]]:
    """AC120: [(texto, tipo, documento)] de una fila de la sección `seccion` del fichero `rel` que juzga
    `problemas_dato_civil` (`PROSA_SEUDONIMO`), con el documento de su prueba (`_doc_del_campo`, AC124); sin el `remove`
    de un rol retirado."""
    patron = next((p for p in CAPAS if fnmatch.fnmatchcase(rel, p)), None)
    campos = PROSA_SEUDONIMO.get((patron, seccion), {})
    return [(texto, campos[ruta], _doc_del_campo(fila, ruta)) for ruta, texto in _rutas(fila)
            if ruta in campos and texto.strip() and not (ruta == 'role' and texto.strip() == 'remove')]


def problemas_roster(repo=None, ids=frozenset(), civiles=()) -> list[tuple[int, str]]:
    """AC118: [(línea, motivo)] de las filas del roster que no pueden ser de un seudónimo: (a) su id es el de una fila
    de `context/figures.yaml` `entities:` que hace algo más que anclar el nodo al cluster del caso (un campo que no sea
    `type`, `name`, `entity_id` o `role`, o un `role` que no esté vacío, retirado por `roles_qa061.yaml` o en la
    plantilla de seudónimo); (b) su `name` lleva apellido (no es variante AC44 de su nombre de pila); (c) su nombre
    completo, de dos palabras o más, es el nombre o un alias de dos o más palabras de una persona civil (`civiles`:
    [(entity_id, nombre)], las del contexto y, en el snapshot, las del export; las del roster no cuentan). Huellas,
    nunca el texto."""
    repo = Path(repo or REPO)
    data, lineas = _documento(repo / PSEUDONYMS_REL)
    contexto = repo / CONTEXT_REL
    figuras = _documento(contexto / 'figures.yaml')[0] if (contexto / 'figures.yaml').exists() else {}
    retirados = _documento(contexto / 'roles_qa061.yaml')[0] if (contexto / 'roles_qa061.yaml').exists() else {}
    retirados = {_id_contexto(f) for f in retirados.get('entities') or []
                 if isinstance(f, dict) and str(f.get('role') or '').strip() == 'remove'}
    anclas = set()   # las filas de figures.yaml de un id del roster con algo más que el ancla
    for fila in figuras.get('entities') or []:
        if isinstance(fila, dict) and _id_contexto(fila) in ids:
            rol = str(fila.get('role') or '').strip()
            if (set(fila) - {'type', 'name', 'entity_id', 'role'}
                    or (rol and _id_contexto(fila) not in retirados and not _PLANTILLA_SEUDONIMO.fullmatch(rol))):
                anclas.add(_id_contexto(fila))
    nombres_civiles = {tuple(_palabras(n)) for e, n in civiles
                       if e not in ids and isinstance(n, str) and len(_palabras(n)) >= 2}
    out = []
    for i, fila in enumerate(data.get('pseudonyms') or []):
        sid, linea = str(fila.get('id') or '').strip(), _linea(lineas, 'pseudonyms', i)
        if sid in anclas:
            out.append((linea, f'el id de una fila del roster está en context/figures.yaml con datos civiles '
                               f'(huella {sha16(sid)})'))
        if isinstance(fila.get('name'), str) and not es_variante_nombre(sid, fila['name']):
            out.append((linea, f'el `name` de una fila del roster lleva más que su nombre de pila '
                               f'(huella {sha16(sid)})'))
        completo = tuple(_palabras(forma_de_muestra(fila)))
        if len(completo) >= 2 and completo in nombres_civiles:
            out.append((linea, f'el nombre completo de una fila del roster es el de una persona civil '
                               f'(huella {sha16(sid)})'))
    return out


# Palabras de tres o más letras que no distinguen un nombre: un par que las lleva no lo nombra a medias.
_FUNCIONALES = frozenset({'del', 'las', 'los', 'por', 'con', 'que', 'para', 'una', 'unos', 'unas', 'sus'})
# AC116: las denominaciones genéricas (lista cerrada, con sus piezas unidas): una sociedad u organismo cuyo nombre es
# solo una de ellas («S.L.», «Fundación») no se distingue por él.
_GENERICAS = frozenset({
    'sl', 'sa', 'slu', 'sau', 'sc', 'scp', 'sll', 'slne', 'scoop', 'coop', 'aie', 'ute', 'cb', 'srl', 'spa', 'ltd',
    'llc', 'inc', 'gmbh', 'ag', 'bv', 'nv', 'plc', 'sas', 'fundacion', 'asociacion', 'ayuntamiento', 'diputacion',
    'ministerio', 'gobierno', 'consejeria', 'junta', 'universidad', 'sociedad', 'empresa', 'compania', 'grupo',
    'holding', 'partido', 'sindicato', 'comunidad', 'cabildo', 'estado'})
# AC121/AC131: las palabras que no distinguen una denominación: funcionales, artículos, preposiciones, tratamientos y
# los nombres comunes de una sociedad («la mercantil», «una empresa»).
_NO_DISTINTIVAS = _FUNCIONALES | _TRATAMIENTO | frozenset({
    'de', 'del', 'la', 'el', 'las', 'los', 'lo', 'un', 'una', 'unos', 'unas', 'en', 'a', 'al', 'y', 'e', 'o', 'u',
    'con', 'por', 'para', 'sin', 'sobre', 'su', 'sus', 'esta', 'este', 'esa', 'ese', 'dicha', 'dicho', 'otra', 'otro',
    'misma', 'mismo', 'cada', 'toda', 'todo', 'como', 'que', 'ni', 'entre', 'tras', 'ante', 'bajo', 'desde', 'hasta',
    'hacia', 'segun', 'mediante', 'sociedad', 'sociedades', 'empresa', 'empresas', 'mercantil', 'mercantiles',
    'compania', 'companias', 'entidad', 'entidades', 'tipo', 'forma'})
# AC121: lo que puede ir justo antes del nombre de una sociedad sin ser parte de él: lo que no distingue, salvo las
# conjunciones, que hacen de dos nombres uno («Cirro y Nimbo S.L.»).
_INTRODUCTORAS = _NO_DISTINTIVAS - {'y', 'e', 'o', 'u', 'ni'}
# AC121-bis: los prefijos de una denominación (lista cerrada, como piezas: sin caja ni acentos): justo delante del
# nombre de un extremo, la denominación es otra sociedad («Hijos de Nimbo S.L.», «Grupo Nimbo S.L.»). El «de» solo no
# es prefijo («administrador de Nimbo S.L.»). Con las variantes de género y las abreviaturas «Hnos.» y «Vda.».
_PREFIJOS_DENOMINACION = (
    ('hijos', 'de'), ('hijo', 'de'), ('hijas', 'de'), ('hija', 'de'), ('viuda', 'de'), ('vda', 'de'),
    ('sucesores', 'de'), ('herederos', 'de'), ('hermanos',), ('hermanas',), ('hnos',), ('grupo',), ('holding',),
    ('talleres',), ('construcciones',), ('promociones',), ('inversiones',), ('inmobiliaria',), ('gestion',),
    ('servicios',), ('comercial',), ('industrias',), ('laboratorios',), ('bodegas',), ('transportes',),
    ('explotaciones',), ('agropecuaria',), ('patrimonial',), ('participaciones',), ('corporacion',))


def _prefijada(piezas, inicio, frontera=None) -> bool:
    """AC121-bis: ¿lleva la pieza `inicio` un prefijo de denominación (`_PREFIJOS_DENOMINACION`) justo delante, con
    sus palabras por posición (`_es_forma`, AC135: «H1jos de»)? AC171: con `frontera`, sin un límite de frase entre el
    prefijo y la pieza ni dentro de él («Pagó a sus hijos. De Nimbo S.L.» no lo lleva)."""
    return any(inicio >= len(p) and all(_es_forma(t, q) for t, q in zip(piezas[inicio - len(p):inicio], p))
               and not (frontera and any(frontera(g) for g in range(inicio - len(p) + 1, inicio + 1)))
               for p in _PREFIJOS_DENOMINACION)


def _tramos_palabra(fichas) -> list[list[str]]:
    """Las palabras que pueden formar unas fichas: sus piezas unidas por puntos de partición, como tramos."""
    if not fichas.piezas:
        return []
    tramos, actual = [], [fichas.piezas[0]]
    for pieza, unible in zip(fichas.piezas[1:], fichas.unibles):
        if unible:
            actual.append(pieza)
        else:
            tramos.append(actual)
            actual = [pieza]
    tramos.append(actual)
    return tramos


def _sin_forma(tramos) -> list[list[str]]:
    """Las palabras (tramos) de una denominación sin su forma jurídica del final (`_FORMAS_JURIDICAS`) ni la UTE del
    principio."""
    palabras = [''.join(tramo) for tramo in tramos]
    forma = next((f for f in sorted(_FORMAS_JURIDICAS, key=len, reverse=True)
                  if len(palabras) > len(f) and tuple(palabras[-len(f):]) == f), ())
    tramos = tramos[:len(tramos) - len(forma)]
    return tramos[1:] if [''.join(t) for t in tramos[:1]] == ['ute'] else tramos


@functools.lru_cache(maxsize=8192)
def nucleo_distintivo(nombre) -> str | None:
    """AC149: el núcleo distintivo del nombre de una sociedad u organismo, o None: sus palabras sin la forma jurídica ni
    la UTE (`_sin_forma`, el mismo cálculo con que `_nombre_cuenta` sabe si el nombre distingue) y sin las
    denominaciones genéricas ni las palabras que no distinguen de sus bordes (`_GENERICAS`, `_NO_DISTINTIVAS`:
    «Grupo Nimbo S.L.» → «Nimbo»), con alguna pieza distintiva de dos letras o más. Escrito con sus puntos de partición
    («Ni-mb-o»), lo leen como un nombre más el índice de sociedades (`indice_sociedades`) y la cita que nombra a su
    extremo (`_nombres_propios`). Si es el nombre entero, None: ya se lee."""
    enteras = _tramos_palabra(_fichas(nombre))
    tramos = _sin_forma(enteras)
    while tramos and (''.join(tramos[0]) in _GENERICAS or ''.join(tramos[0]) in _NO_DISTINTIVAS):
        tramos = tramos[1:]
    while tramos and (''.join(tramos[-1]) in _GENERICAS or ''.join(tramos[-1]) in _NO_DISTINTIVAS):
        tramos = tramos[:-1]
    palabras = [''.join(tramo) for tramo in tramos]
    if tramos == enteras or not any(sum(c.isalpha() for c in p) >= 2 and p not in _GENERICAS
                                    and p not in _NO_DISTINTIVAS for p in palabras):
        return None
    return ' '.join('-'.join(tramo) for tramo in tramos)


def nombres_con_nucleo(eid, nombres) -> set[str]:
    """AC149: los nombres de una entidad y, de una sociedad u organismo, su núcleo distintivo (`nucleo_distintivo`)."""
    nombres = set(nombres or ())
    return nombres | {n for n in map(nucleo_distintivo, nombres) if n} if eid.startswith(SENSIBLES) else nombres


@functools.lru_cache(maxsize=16384)
def _nombre_cuenta(nombre, minimo=1) -> bool:
    """AC116: ¿distingue a su entidad un nombre de un extremo, en alguna lectura suya (`_fichas`: cada punto de
    partición unido o separado)? No si es una denominación genérica (`_GENERICAS`). Sí si alguna lectura de `minimo`
    palabras o más lleva una con contenido (tres letras o cifras o más, no funcional): «Ni-mb-o» leído «nimbo»; o, de
    una sociedad u organismo (`minimo` 1), si se lee como una sola palabra de dos o más («ZX», también «Z-X») o si, sin
    sus genéricos (una forma jurídica al final, la UTE delante, una denominación genérica), le queda una palabra de dos
    letras o más que distingue (AC131: «ZX SA», «ZX S.L.»; «SA», no). Una `_FUNCIONALES` no se descarta como nombre
    entero. Una persona necesita dos palabras: una sola no se distingue de un nombre de pila."""
    fichas = _fichas(nombre)
    if not fichas.piezas or ''.join(fichas.piezas) in _GENERICAS:
        return False
    tramos = _tramos_palabra(fichas)
    if minimo <= 1 and len(tramos) == 1 and len(''.join(fichas.piezas)) >= 2:
        return True
    if minimo <= 1 and any(len(p) >= 2 and p not in _GENERICAS and p not in _NO_DISTINTIVAS
                           for p in map(''.join, _sin_forma(tramos))):
        return True
    return any(len(palabra) >= 3 and palabra not in _FUNCIONALES
               and (minimo <= 1 or len(tramos) >= 2 or (i, j) != (0, len(tramo)))
               for tramo in tramos for i in range(len(tramo)) for j in range(i + 1, len(tramo) + 1)
               for palabra in [''.join(tramo[i:j])])


def _saltos(fichas) -> dict:
    """Las palabras sin contenido de unas fichas (de menos de tres letras o funcionales; cuatro como mucho), tramos de
    piezas unidas por puntos de partición, como saltos {pieza: [pieza siguiente]}."""
    saltos = {}
    for inicio in range(len(fichas.piezas)):
        for fin, palabra in _palabras_desde(fichas, inicio, 4):
            if len(palabra) < 3 or palabra in _FUNCIONALES:
                saltos.setdefault(inicio, []).append(fin)
    return saltos


def _con_contenido(comunes) -> bool:
    """¿Hay, entre las lecturas comunes de dos palabras (`_comunes`), una con contenido: de tres letras o más y no
    funcional? Las funcionales tienen cuatro como mucho: de cinco en adelante, siempre; de tres o cuatro, se miran todas
    (81 como mucho)."""
    if comunes is None or len(comunes) < 3:
        return False
    return len(comunes) > 4 or any(''.join(p) not in _FUNCIONALES for p in itertools.product(*comunes))


# AC230 (enmienda 49): `_bigrama_en` sin enumerar tramos ni combinaciones. Una palabra del nombre y una del texto
# casan si tienen el mismo largo y `_comunes` y `_con_contenido` lo dicen. `_comunes` lee cada dígito de una palabra
# que mezcla letras y dígitos (`_mezcla`) también como sus letras homógrafas, así que la relación de una letra del
# texto `t` con una del nombre `n` depende de si cada palabra mezcla: 'E' (ninguna: la misma), 'X' (la del texto: `n`
# es `t` o una de sus letras), 'Y' (la del nombre: `t` es `n` o una de sus letras) y 'Z' (las dos: leídas pueden
# coincidir). Una palabra mezcla si cruza de letra a dígito o al revés (`cambios`). Dos palabras iguales casan siempre
# ('E' vale para todas); las demás, solo con el cruce que su relación pide. Las de cinco letras o más tienen contenido
# siempre; las de tres y cuatro (que pueden ser funcionales) se miran palabra a palabra (`_cortas_de`).
_HIPOTESIS = (('E', False, False), ('X', True, False), ('Y', False, True), ('Z', True, True))
_LETRAS_MIN, _LETRAS_CORTAS, _LETRAS_LARGAS = 3, 4, 5


def _relacionadas(relacion, t, n) -> bool:
    """AC230: ¿puede la letra o cifra `t` del texto leerse igual que la `n` del nombre en la relación `relacion`?"""
    if relacion == 'E':
        return t == n
    del_texto, del_nombre = t + _DIGITO_LETRAS.get(t, ''), n + _DIGITO_LETRAS.get(n, '')
    if relacion == 'X':
        return n in del_texto
    return t in del_nombre if relacion == 'Y' else any(c in del_nombre for c in del_texto)


def _mascara(posiciones, ancho) -> int:
    """AC230: el entero de `ancho` bits con puestos los de `posiciones` (sin sumar potencias de dos una a una)."""
    bits = bytearray(b'0' * ancho)
    for o in posiciones:
        bits[ancho - 1 - o] = 49
    return int(bits, 2)


class _Cinta:
    """AC230: las letras de unas fichas y el límite de cada posición (`_Cadena`) como máscaras de bits sobre sus
    posiciones (el bit o es la frontera antes de la letra o): dónde empieza una pieza (`inicios`), dónde acaba
    (`finales`), dónde hay un límite duro (`duros`; `libres`, su complemento) y dónde se cruza de letra a dígito o al
    revés (`cambios`; `iguales`, su complemento); y, de una letra del texto, las posiciones cuya letra se relaciona con
    ella (`casan`)."""

    def __init__(self, letras, tipos):
        self.m, self.letras = len(letras), letras
        ancho = self.m + 1
        self.inicios = _mascara((o for o in range(self.m) if tipos[o] is not None), ancho)
        self.finales = _mascara((o for o in range(1, ancho) if tipos[o] is not None), ancho)
        self.duros = _mascara((o for o in range(ancho) if tipos[o] == 'H'), ancho)
        self.cambios = _mascara((o for o in range(1, self.m) if letras[o - 1].isdigit() != letras[o].isdigit()), ancho)
        self.libres, self.iguales = (1 << ancho) - 1 ^ self.duros, (1 << ancho) - 1 ^ self.cambios   # (positivas)
        self.mezcla = any(c.isdigit() for c in letras) and any(not c.isdigit() for c in letras)
        self._casan = {}

    def casan(self, relacion, t) -> int:
        mascara = self._casan.get((relacion, t))
        if mascara is None:
            mascara = self._casan[relacion, t] = _mascara(
                (p for p, n in enumerate(self.letras) if _relacionadas(relacion, t, n)), self.m + 1)
        return mascara


def _cambios_de(letras) -> list[bool]:
    """AC230: de cada posición, si su letra es de otra clase (letra o dígito) que la anterior."""
    return [o > 0 and letras[o - 1].isdigit() != letras[o].isdigit() for o in range(len(letras))]


def _lee_letra(estado, hipotesis, cinta, t, cambio, duro, continuan) -> list[int]:
    """AC230: el estado (cuatro máscaras sobre las posiciones del nombre, según haya cruzado ya el texto y el nombre:
    `2 * texto + nombre`; el bit e, una ventana cuya palabra del nombre acaba antes de la letra e) tras leer la letra
    `t` del texto en la `hipotesis`. Las ventanas que continúan (`continuan`; no las recién abiertas) mueren en un
    límite duro del texto (`duro`) o del nombre, y cruzan donde la letra es de otra clase (`cambio`, en el texto;
    `cinta.cambios`, en el nombre)."""
    relacion, mezcla_texto, mezcla_nombre = hipotesis
    if continuan:
        if duro:
            return [0, 0, 0, 0]
        if mezcla_texto and cambio:
            estado[2] |= estado[0]
            estado[3] |= estado[1]
            estado[0] = estado[1] = 0
        if mezcla_nombre:
            c = cinta.cambios
            estado[1] |= estado[0] & c
            estado[3] |= estado[2] & c
            estado[0] &= cinta.iguales
            estado[2] &= cinta.iguales
        estado = [m & cinta.libres for m in estado]
    casan = cinta.casan(relacion, t)
    return [(m & casan) << 1 for m in estado]


def _finales_largos(letras, tipos, cinta, hipotesis) -> dict[int, int]:
    """AC230: {frontera k del texto (`tipos[k]` no es None): máscara de las posiciones e de la cinta} donde acaba una
    palabra del nombre de `_LETRAS_LARGAS` letras o más que casa con una del texto que acaba en k: ventanas del texto
    que empiezan en una pieza y acaban en una frontera, unidas por sus puntos de partición, y del nombre desde una
    pieza hasta el final de otra. Un solo recorrido del texto con todas las ventanas abiertas a la vez como máscaras
    de bits, sin enumerarlas; las que llegan al largo mínimo entran en el estado de una vez, tras leer sus primeras
    letras al abrirlas (solo en las piezas)."""
    largo, cambios = len(letras), _cambios_de(letras)
    estado = [[0, 0, 0, 0] for _ in hipotesis]
    pendientes, out = {}, {}
    for k in range(largo + 1):
        for abiertas, nuevas in zip(estado, pendientes.pop(k, ())):
            for i in range(4):
                abiertas[i] |= nuevas[i]
        borde = tipos[k]
        if borde is not None:
            acepta = 0
            for abiertas, (_, mezcla_texto, mezcla_nombre) in zip(estado, hipotesis):
                acepta |= abiertas[2 * mezcla_texto + mezcla_nombre]
            if acepta & cinta.finales:
                out[k] = acepta & cinta.finales
        if k == largo:
            break
        duro = borde == 'H'
        for i, h in enumerate(hipotesis):
            if any(estado[i]):
                estado[i] = _lee_letra(estado[i], h, cinta, letras[k], cambios[k], duro, True)
        if borde is not None and k + _LETRAS_LARGAS <= largo:
            nuevas = []
            for h in hipotesis:
                abiertas = [cinta.inicios, 0, 0, 0]
                for q in range(_LETRAS_LARGAS):
                    abiertas = _lee_letra(abiertas, h, cinta, letras[k + q], cambios[k + q], tipos[k + q] == 'H', q > 0)
                    if not any(abiertas):
                        break
                nuevas.append(abiertas)
            if any(any(abiertas) for abiertas in nuevas):
                pendientes[k + _LETRAS_LARGAS] = nuevas
    return out


def _cortas_de(fichas, cadena) -> dict[str, list[tuple[str, int, int]]]:
    """AC230: {esqueleto: [(palabra, bits de inicio, bits de fin)]} de las palabras de tres y cuatro letras de unas
    fichas (una por contenido), con las posiciones de su cadena donde empiezan y acaban."""
    inicios, palabras = [*cadena.inicios, len(cadena.letras)], {}
    for inicio in range(len(fichas.piezas)):
        for fin, palabra in _palabras_desde(fichas, inicio, _LETRAS_CORTAS):
            if len(palabra) >= _LETRAS_MIN:
                bits = palabras.setdefault(palabra, [0, 0])
                bits[0] |= 1 << inicios[inicio]
                bits[1] |= 1 << inicios[fin]
    out = {}
    for palabra, (desde, hasta) in palabras.items():
        out.setdefault(_esqueleto(palabra), []).append((palabra, desde, hasta))
    return out


def _largo_unido(fichas, inicio) -> int:
    """AC230: las letras de la pieza `inicio` y de las que le siguen unidas a ella por puntos de partición, hasta
    `_LETRAS_MIN` (sin pasarse mucho: lo justo para saber si hay una palabra de ese largo)."""
    piezas, unibles = fichas
    total = len(piezas[inicio])
    while total < _LETRAS_MIN and inicio < len(unibles) and unibles[inicio]:
        inicio += 1
        total += len(piezas[inicio])
    return total


_Bigrama = namedtuple('_Bigrama', 'adelante atras cortas llegan')


@functools.lru_cache(maxsize=4096)
def _bigrama_nombre(nombre) -> _Bigrama | None:
    """AC230: un nombre listo para `_bigrama_en`, en caché: su cinta de izquierda a derecha y de derecha a izquierda,
    sus palabras cortas (`_cortas_de`) y, de cada posición donde empieza una pieza, desde qué fronteras se llega a ella
    saltando palabras sin contenido del nombre (`_saltos`), como máscaras de posiciones; None si no tiene ninguna
    palabra de `_LETRAS_MIN` letras o más."""
    fichas = _fichas(nombre)
    piezas = fichas.piezas
    if not any(_largo_unido(fichas, inicio) >= _LETRAS_MIN for inicio in range(len(piezas))):
        return None
    cadena = fichas.cadena
    inicios = [*cadena.inicios, len(cadena.letras)]
    anteriores = {}   # de cada pieza, desde qué piezas se salta a ella de una palabra sin contenido
    for pieza, fines in _saltos(fichas).items():
        for fin in fines:
            anteriores.setdefault(fin, []).append(pieza)
    llegan = [0] * (len(cadena.letras) + 1)
    for c in range(len(piezas) + 1):   # (los saltos van siempre hacia delante)
        llegan[inicios[c]] = 1 << inicios[c]
        for b in anteriores.get(c, ()):
            llegan[inicios[c]] |= llegan[inicios[b]]
    return _Bigrama(_Cinta(cadena.letras, cadena.tipos), _Cinta(cadena.letras[::-1], cadena.tipos[::-1]),
                    _cortas_de(fichas, cadena), llegan)


def _invierte(mascara, m) -> int:
    """AC230: una máscara de m + 1 bits con el bit o en la posición m - o."""
    return int(format(mascara, f'0{m + 1}b')[::-1], 2)


def _bigrama_en(texto, nombre) -> bool:
    """AC90/AC109/AC112/AC113/AC117/AC230: ¿nombra el texto a medias a `nombre`, con dos palabras con contenido seguidas
    en alguna lectura de cada uno (solo palabras sin contenido entre ellas) y que se leen igual, cada una con cualquier
    forma (`_comunes`, `_con_contenido`)? Con contenido, las de tres o más letras que no son funcionales, así que «Juan
    de la Torre» da (juan, torre) y «S. A.» nada: un nombre nombrado a medias («Delgado Crego») comparte uno de esos
    pares con el nombre entero. AC230: exacta, sin presupuesto y en tiempo casi lineal en el texto y en el nombre. De
    cada frontera del texto, en qué posiciones del nombre acaba una palabra que casa con una del texto que acaba en
    ella (`acaban`) y en cuáles empieza una que casa con una de las que empiezan en ella (`empiezan`): las de cinco
    letras o más, a la vez con máscaras de bits recorriendo el texto en los dos sentidos (`_finales_largos`); las de
    tres y cuatro, palabra a palabra (`_cortas_de`). Y el par: una palabra que acaba en una frontera y, tras saltar
    palabras del texto sin contenido (`_saltos`), otra que empieza en un lugar del nombre al que se llega desde donde
    acaba la primera saltando palabras sin contenido del nombre (`llegan`)."""
    nombre_b, fichas = _bigrama_nombre(nombre), _fichas(texto)
    if nombre_b is None or not fichas.piezas:
        return False
    cadena, piezas = fichas.cadena, len(fichas.piezas)
    largo, m, cinta = len(cadena.letras), nombre_b.adelante.m, nombre_b.adelante
    borde = {inicio: j for j, inicio in enumerate(cadena.inicios)} | {largo: piezas}
    acaban, empiezan = [0] * (piezas + 1), [0] * (piezas + 1)
    mezcla_texto = any(c.isdigit() for c in cadena.letras) and any(not c.isdigit() for c in cadena.letras)
    hipotesis = [h for h in _HIPOTESIS if (mezcla_texto or not h[1]) and (cinta.mezcla or not h[2])]
    if cinta.m >= _LETRAS_LARGAS:
        for k, mascara in _finales_largos(cadena.letras, cadena.tipos, cinta, hipotesis).items():
            acaban[borde[k]] |= mascara
        for k, mascara in _finales_largos(cadena.letras[::-1], cadena.tipos[::-1], nombre_b.atras,
                                          hipotesis).items():
            empiezan[borde[largo - k]] |= _invierte(mascara, m)
    memoria = {}
    for desde in range(piezas):
        palabra = ''
        for hasta in range(desde, piezas):
            palabra += fichas.piezas[hasta]
            if len(palabra) > _LETRAS_CORTAS:
                break
            if len(palabra) >= _LETRAS_MIN:
                if palabra not in memoria:
                    pares = [(inicio, fin) for w, inicio, fin in nombre_b.cortas.get(_esqueleto(palabra), ())
                             if _con_contenido(_comunes(w, palabra))]
                    memoria[palabra] = (functools.reduce(operator.or_, (i for i, _ in pares), 0),
                                        functools.reduce(operator.or_, (f for _, f in pares), 0))
                empiezan[desde] |= memoria[palabra][0]
                acaban[hasta + 1] |= memoria[palabra][1]
            if hasta + 1 == piezas or not fichas.unibles[hasta]:
                break
    saltos, siguen, cierres = _saltos(fichas), [0] * (piezas + 1), {}
    for j in range(piezas, -1, -1):
        if empiezan[j] not in cierres:
            cierre, resto = 0, empiezan[j]
            while resto:
                bajo = resto & -resto
                cierre |= nombre_b.llegan[bajo.bit_length() - 1]
                resto ^= bajo
            cierres[empiezan[j]] = cierre
        siguen[j] = cierres[empiezan[j]]
        for fin in saltos.get(j, ()):
            siguen[j] |= siguen[fin]
        if acaban[j] & siguen[j]:
            return True
    return False


def _contenido(nombre, otro) -> bool:
    """¿Son las palabras de `nombre` un tramo seguido de las de `otro` («Alejandro Soler» en «Alejandro Soler X»), en
    alguna lectura de cada uno (AC104/AC105(b)/AC109/AC113): `nombre` como candidato (`_candidato`), buscado como los
    del índice (`_apariciones_indice`), con cualquier forma de cada palabra de `otro` (AC107)?"""
    candidato = _candidato(nombre)
    return bool(candidato) and next(_recorre_indice(_fichas(otro), {p: [(candidato, nombre, None)]
                                                                     for p in candidato.primeras}), None) is not None


def citas_de_split(fila, ids, civiles, sociedades=None) -> list[str]:
    """AC87/AC90 en un split con un seudónimo del roster (enmienda 24): cada `by_quote`/`by_quote_exclude` es un
    localizador que solo puede nombrar al propio namesake civil de un split cuyo canónico es el seudónimo (su nombre ya
    es público en `namesake.name` y `move_aliases` de la regla, decisión Q2), por el índice civil, entero o por dos
    palabras seguidas. Huellas: la de la cita si dice «seudónimo», lleva un id o (AC150) identifica a un seudónimo con
    un civil (`identifica_explicitamente`, con su namesake y las sociedades de `sociedades`); la del nombre de cualquier
    otro civil (el índice civil, o un lado civil del split que no es ese namesake, también por dos palabras seguidas) y
    la del nombre de pila compuesto de otro seudónimo (AC118/AC130, `_pilas_en`: no el de un lado ni el de un homónimo
    suyo). La sección `citas:` de la lista no vale aquí: ninguna prueba los admite."""
    canon_item = fila.get('canonical') if isinstance(fila.get('canonical'), dict) else {}
    ns_item = fila.get('namesake') if isinstance(fila.get('namesake'), dict) else {}
    canon, ns = entidad(canon_item), entidad(ns_item)
    propio = ns if canon in ids and ns and ns not in ids else None
    lados = {eid: [n for n in (item.get('name'), *alias) if isinstance(n, str) and len(_palabras(n)) >= 2]
             for eid, item, alias in ((canon, canon_item, ()), (ns, ns_item, _lista(fila, 'move_aliases')))
             if eid and eid not in ids}
    indice, base = _Indice(civiles), _Indice(_base(civiles))   # AC202: con su base, sin las lecturas añadidas
    for eid, nombres in lados.items():
        lado = _indice_nombres(((eid, n) for n in nombres), minimo=2)
        for destino, origen in ((indice, lado), (base, _base(lado))):
            for clave, filas in origen.items():
                destino[clave] = [*destino.get(clave, ()), *filas]
    indice.base = base
    exentos = lados.get(propio, ())
    propios = {tuple(sorted(_nombre(e))) for e in {canon, ns} & ids}
    suyos = frozenset(s for s in ids if tuple(sorted(_nombre(s))) in propios)
    huellas = []
    for cita in (*_lista(fila, 'by_quote'), *_lista(fila, 'by_quote_exclude')):
        if not isinstance(cita, str):
            continue
        if (_dice(cita, _SEUDONIMO) or _ID_ENTIDAD.search(_normalizar(cita))
                or identifica_explicitamente(cita, ids, indice, sociedades, lados, {canon, ns})):
            huellas.append(sha16(cita))
            continue
        otros = {n for n in _nombres_en(cita, indice, {propio, *suyos} - {None},   # AC129: los suyos válidos eximen
                                        frozenset((f'{s}{_ALIAS_INVALIDO}', a)   # AC134: los inválidos denunciados
                                                  for s, a in getattr(civiles, 'denunciados', ()) if s in suyos))
                 if not any(_contenido(n, m) for m in exentos)}
        otros |= {n for eid, nombres in lados.items() if eid != propio for n in nombres   # AC195: y por sus comillas
                  if any(_bigrama_en(lectura, n) for lectura in _lecturas_texto(cita))}
        otros |= _pilas_en(cita, ids, suyos)
        huellas += [sha16(n) for n in sorted(otros)]
    return list(dict.fromkeys(huellas))


def _extremos_civiles(entidades, ids) -> set[str]:
    """Los extremos de una fila que pueden reidentificar a un seudónimo: personas, sociedades u organismos fuera del
    roster (un delito o un tribunal, no)."""
    return {e for e in entidades if e and e not in ids and e.startswith(('Person:', *SENSIBLES))}


def _nombres_propios(texto, propios, civiles, nombres=None, evidencia=False, existe=False) -> set[str]:
    """AC90/AC95: los nombres de los extremos `propios` que nombra un texto, en cualquiera de sus lecturas (`_fichas`,
    AC104/AC105(b)/AC107/AC109/AC113: «Nim-\\nbo», «Nimb0» y «Nimb-0» nombran a Nimbo): los del índice civil que son de
    uno de ellos o caen dentro de uno en una misma lectura (`_a_la_vez`) y, de `nombres` ({entity_id: nombres}, del
    contexto o del export), sus nombres y alias completos, con límites de palabra sobre el texto plegado: de una
    sociedad u organismo, si tiene alguna palabra con contenido («Nimbo» sí, «SA» no), y, del nombre que no está entero,
    su núcleo distintivo (AC149, `nucleo_distintivo`: «Cirro Estrato» de «Cirro Estrato S.L.»); de una persona, si tiene
    dos palabras o más (una sola no se distingue de un nombre de pila, como en el índice). AC116: lo que distingue al
    nombre se mira en sus lecturas (`_nombre_cuenta`): «Ni-mb-o» y «ZX» nombran a su sociedad; «S.L.», no. AC168:
    también a través de las comillas del texto, como el índice civil; AC202: en cada pasada (`_pasadas`), sumadas, y
    AC205: los nombres, también a través de sus comillas (`_anadidas`), con el índice completo. Como prueba de que una
    cita nombra a su extremo (`evidencia`, `_nombra_extremo`), solo la pasada base (sin leer a través de comillas, con
    el índice base): una lectura a través de comillas solo añade hallazgos, y la prueba exime.

    AC227: el trabajo queda acotado. (1) La existencia (`existe`: ¿hay algún nombre?, para `_nombra_extremo` y
    `_menciona_extremo`) se decide sin recorrer ningún par: una aparición de una entidad de un extremo se contiene a sí
    misma, así que hay nombre si y solo si hay una (o una lectura de `nombres`) y todo par lo implica; exacta, no se
    agota. (2) El conjunto de nombres (la identificación explícita de `problemas_seudonimo`) resuelve cada nombre una
    vez y sus pares, cobrados con un `_Presupuesto` de la llamada (`_a_la_vez`, `_Contenedoras.de`) en el orden canónico
    de las apariciones (AC226). Agotado, falla cerrado en el sentido de lo que se usa: el DETECTOR (sin `evidencia`)
    cuenta como presente el nombre que no llegó a decidir (el conjunto crece, y con él las denuncias); la PRUEBA
    (`evidencia`) lo cuenta como ausente (el conjunto se encoge: menos prueba, más denuncias)."""
    pasadas = _pasadas(texto, civiles)[:1] if evidencia else _pasadas(texto, civiles)
    pares = [(e, n) for e in propios for n in (nombres or {}).get(e, ())]
    buscados = [(e, n, n) for e, n in pares if _nombre_cuenta(n, 2 if e.startswith('Person:') else 1)]
    buscados += [] if evidencia else _anadidas(pares)   # (entidad, lectura que se busca, nombre que se informa)
    hallados, presupuesto = set(), _Presupuesto()
    for lectura, (indice,) in pasadas:
        fichas, memoria = _fichas(lectura), {}
        apariciones = _apariciones_indice(fichas, indice)
        contenedoras = _Contenedoras(apariciones, propios)
        hallados |= {a.nombre for a in apariciones if contenedoras.propia(a)}   # (no cuesta)
        for a in () if existe else apariciones:   # los pares, solo para el conjunto: la existencia no los necesita
            if a.nombre in hallados:   # un nombre, una vez
                continue
            try:
                coaparece = any(_a_la_vez(fichas, [a, suya], (), memoria, presupuesto)
                                for suya in contenedoras.de(a, presupuesto))
            except _TopeAgotado:
                coaparece = not evidencia   # sin decidir: el detector lo cuenta, la prueba no
            if coaparece:
                hallados.add(a.nombre)
        if existe and hallados:
            return hallados
        for e, n, nombre in buscados:
            if _contenido(n, lectura):
                hallados.add(nombre)
            elif e.startswith(SENSIBLES) and (nucleo := nucleo_distintivo(n)) and _contenido(nucleo, lectura):
                hallados.add(nucleo)
        if existe and hallados:
            return hallados
    return hallados


def _nombra_extremo(texto, propios, civiles, nombres=None) -> bool:
    """AC90: ¿nombra un texto a uno de sus extremos `propios`? Por un nombre completo (`_nombres_propios`) o, a medias,
    por dos palabras con contenido seguidas de uno de sus nombres (`_bigrama_en`). AC212: es la PRUEBA de que la cita
    nombra a su extremo (`prueba_conservada`: sin ella, 'no-nombra'), así que, de las lecturas del texto, solo la base
    (`_comillas_en_blanco`: ninguna comilla une ni parte un nombre), en los dos caminos: una comilla («Cir«ro» Estrato»)
    no concede la coaparición que la misma cita con otra comilla («Cir"ro" Estrato») no da. Lo que se halla en
    cualquier lectura, `_menciona_extremo`. AC227: la pregunta se responde exacta y sin presupuesto (`existe`): ninguna
    consulta de existencia puede agotarse, así que no hay sentido del fallo cerrado que elegir."""
    if _nombres_propios(texto, propios, civiles, nombres, evidencia=True, existe=True):
        return True
    base = _comillas_en_blanco(texto)
    return any(_bigrama_en(base, n) for e in propios for n in (nombres or {}).get(e, ()))


def _menciona_extremo(texto, propios, civiles, nombres=None) -> bool:
    """AC212: ¿menciona un texto a uno de sus extremos `propios` en alguna de sus lecturas (AC104/AC105(b)/AC109),
    también a través de sus comillas? Es el HALLAZGO (`citas_sin_prueba`: la cita que lo nombra pide su prueba): se
    suman todas las lecturas, como en las enmiendas 39 y 41; la prueba de que lo nombra, `_nombra_extremo`, es solo la
    base. AC227: exacta y sin presupuesto, como ella (`existe`)."""
    if _nombres_propios(texto, propios, civiles, nombres, existe=True):
        return True
    return any(_bigrama_en(lectura, n) for lectura in _lecturas_texto(texto)
               for e in propios for n in (nombres or {}).get(e, ()))


def citas_con_pila_ajena(textos, entidades, ids, admitida=None) -> list[str]:
    """AC118 (enmienda 30, residual): las huellas de las citas [(texto, clase)] de una fila de seudónimo que nombran el
    nombre de pila de dos palabras o más de otro seudónimo (`_pilas_en`, en cualquier orden, AC130: no de uno de la
    fila ni de un homónimo suyo, ni el que la cita ya menciona como tal, que es AC78(d)) sin prueba de que la cita es
    literal: `admitida(cita)`, la vía (a) del export o la entrada de `citas:` de ese texto exacto (b), como AC90.
    Denegación por defecto: sin `admitida`, ninguna."""
    seudos = {e for e in entidades if e in ids}
    if not seudos:
        return []
    propios = {tuple(sorted(_nombre(e))) for e in seudos}
    exentos = frozenset(s for s in ids if s in seudos or tuple(sorted(_nombre(s))) in propios)
    otros = frozenset(ids) - exentos
    huellas = []
    for texto, clase in textos:
        if clase in (CITA, SELECTOR) and _pilas_en(texto, ids, exentos | {_detector(otros, 'mencion')(texto)}) \
                and not (admitida and admitida(texto)):
            huellas.append(sha16(texto))
    return list(dict.fromkeys(huellas))


def prueba_conservada(cita, doc, judicial, extremos, ids, civiles, nombres=None, citas=(), export=None,
                      conocidos=None, publicos=None, sociedades=None) -> tuple[str | None, list[str]]:
    """AC119/AC122/AC125: (prueba, motivos) de la cita de una arista de un seudónimo que una decisión conserva tal cual,
    la misma en el gate y en el control posterior. La de una arista mixta (un seudónimo y un civil: persona, sociedad u
    organismo) ha de tener al menos una palabra ('sin-cita'). AC139: la prueba exime solo la coaparición: antes, la cita
    pasa lo demás que el snapshot le pide (`revisar_textos`: AC78/AC87, «seudónimo» con el civil, un id, otro seudónimo;
    caracteres sospechosos; AC150, la identificación explícita, con las sociedades de `sociedades`), y la que identifica
    no se conserva nunca ('identifica', 'sospechoso'). AC140: los nombres de su extremo civil que dan el censo o el
    export (`conocidos`, {entity_id: nombres}) se suman a `nombres` antes del detector entero (`citas_sin_prueba`:
    enteros, a medias, `_bigrama_en`, o por su núcleo, AC149). Si nombra a su extremo civil o el nombre de pila
    compuesto de otro seudónimo (AC118), su prueba (`prueba_cita`: vía (a) 'export:…' o (b) 'citas:…') o el motivo
    ('coaparicion', 'pila'). AC148: la de una arista mixta que no nombra a su extremo civil no se conserva: la
    asociación que la arista publica no la respalda la cita ('no-nombra'; si los nombres de su extremo no se conocen,
    'sin-nombres': la falta de reconocimiento no es una prueba)."""
    seudos, civiles_extremo = frozenset(e for e in extremos if e in ids), _extremos_civiles(extremos, ids)
    texto, conocidos = str(cita or '').strip(), conocidos or {}
    if civiles_extremo and not _palabras(texto):
        return None, ['sin-cita']
    if not texto:
        return None, []
    todos = {e: set(v) for e, v in (nombres or {}).items()}
    for e, valores in conocidos.items():
        todos.setdefault(e, set()).update(valores)
    vetos = {m for m, _ in revisar_textos(extremos, [(texto, CITA)], ids, civiles, publicos, todos, lambda t: True,
                                          sociedades)}
    if vetos:
        return None, sorted({'identifica' if m == 'seudonimo' else m for m in vetos})
    menciona = bool(citas_sin_prueba([(texto, CITA)], extremos, ids, civiles, todos))
    nombra = menciona and bool(citas_sin_prueba([(texto, CITA)], extremos, ids, civiles, todos, prueba=True))
    pila = bool(citas_con_pila_ajena([(texto, CITA)], extremos, ids))
    motivos = []
    if civiles_extremo and not nombra:   # AC148, AC212: nombrarlo por la pasada base es lo que la prueba
        motivos.append('no-nombra' if all(conocidos.get(e) for e in civiles_extremo) else 'sin-nombres')
    prueba = prueba_cita(texto, doc, seudos, citas, export, judicial) if menciona or pila else None
    if (menciona or pila) and not prueba:
        motivos += [m for m, si in (('coaparicion', nombra), ('pila', pila)) if si]
    return (None, motivos) if motivos else (prueba, [])


def citas_sin_prueba(textos, entidades, ids, civiles, nombres=None, admitida=None, prueba=False) -> list[str]:
    """AC90: las huellas de las citas [(texto, clase)] de una fila con un seudónimo y una entidad que no lo es que
    nombran a ese extremo propio no seudónimo (`_menciona_extremo`: el índice civil y sus nombres, del contexto o del
    export; AC212, con `prueba`, solo si lo nombran por la pasada base, `_nombra_extremo`) sin prueba de coaparición:
    `admitida(cita)`, la vía (a) del export (capa judicial, documento del export y cita continua literal, por palabras
    completas, en uno de sus chunks: `cita_literal`; con una elipsis marcada, nunca) o la entrada de `citas:` de la
    lista de ese texto exacto (b). El selector de una regla (SELECTOR) se prueba igual. Denegación por defecto: sin
    `admitida`, ninguna. Se evalúa siempre (AC94): la cita que además dice «seudónimo» o lleva un id es también
    hallazgo de AC87 (`problemas_seudonimo`), pero eso no la exime de esta prueba. Solo cuentan los extremos personas,
    sociedades u organismos (`_extremos_civiles`): un delito o un tribunal no reidentifican."""
    propios = _extremos_civiles(entidades, ids)
    if not (set(entidades) & ids) or not propios:
        return []
    huellas = []
    for texto, clase in textos:   # AC104: el texto tal cual, con sus lecturas; la huella de `admitida`, la de él
        if clase not in (CITA, SELECTOR):
            continue
        nombra = _nombra_extremo if prueba else _menciona_extremo
        if nombra(texto, propios, civiles, nombres) and not (admitida is not None and admitida(texto)):
            huellas.append(sha16(texto))
    return list(dict.fromkeys(huellas))


def problemas_extremos(pares, ids, nombres=None) -> list[tuple[str, str]]:
    """AC91: [(motivo, huella)] de los extremos escritos por nombre [(texto, entity_id resuelto, id explícito del mismo
    lado o None)], cada uno contra SU extremo: el de un seudónimo es una variante AC44 de su nombre de pila (`nombre`);
    el de un civil no nombra a un seudónimo (`civil`). Con id en el mismo lado (de un civil), el texto concuerda con él
    (`discordante`): deriva ese id o es uno de sus nombres o alias conocidos (`nombres`: {entity_id: nombres}); un id
    sin nombres conocidos no se juzga aquí (el loader resuelve por el id: gana el id)."""
    out = []
    for texto, eid, explicito in pares:
        if not isinstance(texto, str) or not texto.strip() or not eid:
            continue
        if eid in ids:
            if not es_variante_nombre(eid, texto):
                out.append(('nombre', sha16(texto)))
            continue
        sid = nombra_seudonimo(texto, ids)
        if sid:
            out.append(('civil', sha16(sid)))
        conocidos = {tuple(_palabras(n)) for n in (nombres or {}).get(explicito, ())} if explicito else set()
        if (conocidos and entity_id(explicito.split(':', 1)[0], texto) != explicito
                and tuple(_palabras(texto)) not in conocidos):
            out.append(('discordante', sha16(texto)))
    return out


# Los motivos de `revisar_textos`, con la huella de lo que nombra.
MOTIVOS = {'civil': 'que nombra a un seudónimo judicial',
           'seudonimo': 'nombra a un civil o a un documento',
           'nombre': 'nombre de un seudónimo fuera de sus variantes',
           'roster-ausente': 'id con forma de seudónimo fuera del roster',
           'coaparicion': 'cita que nombra a su extremo civil sin prueba de coaparición',
           'discordante': 'nombre y id de un extremo que no concuerdan (el loader resuelve por el id)',
           'sospechoso': 'texto con caracteres invisibles o alfabetos mezclados',
           'dato-civil': 'texto junto a un seudónimo con una sociedad o una fecha fuera de sus campos cerrados',
           'pila': 'cita que nombra a otro seudónimo por su nombre de pila sin prueba de que es literal',
           'sin-documento': 'tramo citado sin documento para la prueba',
           'sin-cita': 'arista mixta conservada sin cita',
           'sin-nombres': 'cita de una arista mixta conservada sin nombres conocidos de su extremo',
           'alias-capa': 'alias de un seudónimo que no es variante de su nombre de pila ni está en la lista',
           'alias-identificador': 'nombre o alias de un civil que lo identifica con un seudónimo judicial',
           'identifica': 'arista conservada con una cita que identifica al seudónimo',
           'no-nombra': 'arista mixta conservada sin coaparición probada',
           'homonimo': 'nombre, nombre canónico o alias de un nodo civil que es variante del nombre de pila de un '
                       'seudónimo judicial',
           'homonimo-slug': 'entity_id de un nodo civil cuyo slug es el nombre de pila de un seudónimo judicial',
           'tipo-nombre': 'nombre, nombre canónico, nombre original o denominación que no es una cadena, o alias que no son '
                          'una lista de cadenas',
           'agotamiento': 'nombre, nombre canónico, alias o slug de un nodo civil que no se puede comparar con los seudónimos judiciales '
                          '(presupuesto de la comparación agotado: demasiados signos o estados)',
           'caracter-no-permitido': 'nombre, nombre canónico, nombre original, alias, denominación o slug con un carácter fuera del conjunto '
                                    'permitido (lista blanca, en crudo o tras NFKC)',
           'entity-id': 'entity_id de una ficha que no es una clave pública válida (no es una cadena, sin «:», prefijo fuera del literal, '
                        'o slug vacío o sin ninguna letra ni cifra)',
           'sustituto-suelto': 'cadena de un nodo o una arista del export con un sustituto UTF-16 suelto (U+D800-U+DFFF), que ningún texto '
                               'válido lleva',
           'linea-jsonl': 'línea de un fichero del export (nodes.jsonl o relationships.jsonl) que está vacía, no es JSON o anida más de lo que se lee',
           'forma-registro': 'registro del export (nodo o arista) con un campo estructural de una forma que no es la del export (el registro '
                             'no es un objeto, o `ref`, `start`, `end`, `type`, `labels`, `props` o la capa y el documento de una arista no '
                             'son una cadena, una lista de cadenas o un objeto, o faltan; o dos nodos llevan la misma `ref`)'}


def revisar_textos(entidades, textos, ids, civiles, publicos=None, nombres=None,
                   admitida=None, sociedades=None, caracteres=()) -> list[tuple[str, str]]:
    """[(motivo, huella)] de una fila pública por la partición de sus entidades (AC66/AC78/AC81/AC83/AC86/AC90): una
    fila civil no nombra a un seudónimo (`civil`: su prosa, sus citas, sus URL y sus nombres y alias); la de un
    seudónimo (alguna entidad en `ids`) no nombra lo que veda AC78 (`seudonimo`), no cita a su extremo civil sin prueba
    de coaparición (`coaparicion`, `citas_sin_prueba` con `nombres` y `admitida`) ni el nombre de pila de otro seudónimo
    sin la de que la cita es literal (`pila`, `citas_con_pila_ajena`, AC118) y su `nombre` es una variante AC44 del
    de pila de uno de ellos (`nombre`: nunca el del censo ni otro, AC83); y ningún texto lleva caracteres invisibles, de
    ancho completo, letras latinas confusables o alfabetos mezclados (`sospechoso`; una URL, también decodificada). El
    nombre de un extremo (EXTREMO) se juzga contra su extremo (`problemas_extremos`), aquí solo sus caracteres. AC150:
    ninguno de los de un seudónimo lo identifica con un civil (`seudonimo`, con las sociedades de `sociedades`); AC160:
    ni los de una fila civil, por el nombre de pila suelto y la fórmula adyacente (`civil`, `identificaciones_civiles`,
    la huella del texto), ni, AC167, su nombre o un alias que lo identifica (`alias-identificador`). `caracteres`: textos
    que solo se leen por sus caracteres (`sospechoso`), además de los de `textos`: los nombres y denominaciones que son
    estructura (`textos_props`, `_PROPS_CARACTERES`) pero la API sirve como nombre. Huellas, nunca el texto."""
    leidos = [(t, c) for t, c in textos if c != ESTRUCTURA]
    if set(entidades) & ids:
        out = [('seudonimo', h) for h in problemas_seudonimo(leidos, entidades, ids, civiles, publicos, nombres,
                                                              sociedades)]
        out += [('coaparicion', h) for h in citas_sin_prueba(leidos, entidades, ids, civiles, nombres, admitida)]
        out += [('pila', h) for h in citas_con_pila_ajena(leidos, entidades, ids, admitida)]
        out += [('nombre', sha16(t)) for t, c in leidos
                if c == NOMBRE and not any(es_variante_nombre(e, t) for e in entidades if e in ids)]
    else:   # AC86: también sus nombres y alias, con el mismo detector
        sid = nombra_en([(t, c == URL) for t, c in leidos if c in (PROSA, CITA, SELECTOR, URL, NOMBRE, ALIAS)], ids)
        out = [('civil', sha16(sid))] if sid else []
        out += identificaciones_civiles(leidos, entidades, ids, civiles, sociedades, nombres)
    return out + [('sospechoso', sha16(t)) for t in dict.fromkeys(
        t for t, url in [*((t, c == URL) for t, c in leidos if c != DOC), *((t, False) for t in caracteres)]
        if texto_sospechoso(t, url))]


def _ficheros_por_capa(repo) -> list[tuple[str, list[Path]]]:
    """[(patrón, ficheros)] de las capas del inventario con su propia lectura de filas: todas salvo el roster, la lista,
    el registro y las reglas (cada una con su comprobación). Sus ficheros, los de `_yaml_de_datos`; los de una capa, en
    el orden de su loader (el contexto: `ficheros_overlay`, con los `*_qa061` al final; el último que declara una
    entidad manda)."""
    especiales = {PSEUDONYMS_REL, PERMITIDOS_REL, REGISTRO_REL, f'{RELATIONS_REL}/*.y*ml'}
    todos = _yaml_de_datos(repo)
    out = []
    for patron in CAPAS:
        if patron in especiales:
            continue
        propios = [p for p in todos if capa_de(str(p.relative_to(repo))) is CAPAS[patron]]
        if patron == f'{CONTEXT_REL}/*.y*ml':
            orden = {p: i for i, p in enumerate(ficheros_overlay(repo / CONTEXT_REL))}
            propios.sort(key=lambda p: orden.get(p, len(orden)))
        out.append((patron, propios))
    return out


def _nombres_capa(secciones, documentos) -> dict:
    """{nombre: entity_id} de las filas `entities` de una capa (el último que la declara manda): los extremos de sus
    `relations` son nombres."""
    nombres = {}
    for data in documentos:
        for fila in data.get('entities') or [] if 'entities' in secciones else []:
            entidades = secciones['entities'].entidades(fila, {}) if isinstance(fila, dict) else set()
            if entidades:
                nombres[str(fila.get('name') or '').strip()] = next(iter(entidades))
    return nombres


def reglas_explicitas(reglas) -> tuple[list[dict], list[dict]]:
    """(deny, fix): las reglas explícitas de `reglas` ([(sección, fila)] del overlay o de un plan del apply), con su par
    resuelto (`extremo`), su tipo, su documento y su motivo; una deny opaca no nombra su par y no entra."""
    out = {'deny': [], 'fix': []}
    for seccion, f in reglas:
        if seccion in out and isinstance(f, dict) and not alcance.es_opaca(f):
            out[seccion].append({'source_id': extremo(f, 'source'), 'target_id': extremo(f, 'target'),
                                 'type': (f.get('type') or '').strip(), 'doc': f.get('doc'), 'reason': f.get('reason')})
    return out['deny'], out['fix']


# El marcador de la clase de un extremo de la fila del registro, el que escribe el apply (`_extremo_registro`).
_MARCA_DE_CLASE = {'Person': 'Person:[tercero]', 'Company': 'Company:[sociedad]',
                   'Organization': 'Organization:[organismo]'}


def fila_juzgada(fila, deny, fix, lista=()) -> dict:
    """La fila del registro `fila` como la juzga la guardia de prosa (AC53, AC78, AC120): cada extremo de la arista de
    su `dato` que ya nombra una regla pública asociada a ella (`registro_check.nombrados_por_su_regla`, con las reglas
    explícitas `deny` y `fix` y las entradas `aristas` de la lista de permitidas `lista`: principio P de la fusión
    070 ⟷ 071) pasa a ser el marcador de su clase, solo en su posición. Lo demás de la fila se juzga tal cual: el mismo
    id en otro sitio del dato, su `fuente`, y la fila entera si ni una regla asociada ni la lista nombran su par."""
    from scripts import registro_check as RC
    nombrados = RC.nombrados_por_su_regla(fila, deny, fix, lista)
    if not nombrados:
        return fila
    m = RC._DATO_ARISTA.fullmatch(str(fila.get('dato') or ''))
    a, b = (_MARCA_DE_CLASE.get(x.partition(':')[0], x) if x in nombrados else x for x in (m['a'], m['b']))
    return {**fila, 'dato': f'({a}) -[{m["tipo"]}]-> ({b}){m["resto"]}'}


def auditar(repo=None, publicos=None) -> list[Hallazgo]:
    """Lo que el repo público no puede contener (AC1): reglas deny/alias_deny sobre un seudónimo
    (la retirada es la política del loader), fix sobre un par seudónimo↔sociedad/organismo fuera de
    la lista, splits seudónimo↔sociedad/organismo, filas del registro de un seudónimo con la sociedad
    u organismo literal, una lista de permitidas mal formada o con texto libre (AC80) y (AC45) un documento fuera del
    árbol público (`publicos`; por defecto, `documentos_publicos`) en una regla deny/fix con extremo seudónimo o en los
    `docs` de la lista (AC30, la cita, es un caso particular). Los splits seudónimo→nombre civil quedan fuera hasta la
    decisión Q2, salvo sus caracteres y sus citas que identifican (AC87, `citas_de_split`). Y (AC79) cada fila de cada
    capa del inventario (`CAPAS`) por la partición de sus entidades
    (`revisar_textos`): la de una entidad civil no nombra a un seudónimo, tampoco en sus nombres y alias (AC65/AC66/
    AC86); la de un seudónimo, ni a un civil (en una cita, solo a un extremo que coaparece, AC87), ni a otra entidad u
    otro seudónimo, ni un documento fuera del árbol público (AC78), y su `name` es una variante del nombre de pila
    (AC83); y ningún texto lleva caracteres invisibles, de ancho completo, letras latinas confusables o alfabetos
    mezclados (AC81/AC85/AC88). El roster pasa ese mismo cierre, con sus homónimos exentos (AC84), y además no nombra
    una entidad fuera de él por su id ni a una persona civil del contexto por su nombre (AC68), ni tiene una fila que
    no pueda ser de un seudónimo (AC118, `problemas_roster`). Ninguna fila lleva un id con forma de seudónimo que el
    roster no declare (AC89). AC120: la prosa junto a un seudónimo (`textos_dato_civil`) no lleva un dato civil
    (`problemas_dato_civil`, con el núcleo de las sociedades, AC149). AC150: ningún texto de una fila de seudónimo
    (cita, selector o prosa) lo identifica con un civil (`identifica_explicitamente`), con o sin prueba; AC160: ni el
    de una fila civil, por su nombre de pila suelto y una fórmula adyacente (`identificaciones_civiles`). AC134: ningún
    fichero de contexto da a un seudónimo un alias que no es variante de su nombre de pila ni está en la lista
    (`alias_invalidos_contexto`). Un roster o un YAML del inventario que no se
    pueden leer paran con exit 2 (AC114/AC115, `ErrorDeForma`); AC192: también una relación de contexto con un
    extremo que ningún fichero de contexto declara (`problemas_contexto`, con todos juntos). Los motivos no nombran
    extremos, citas ni documentos (el log del CI es público)."""
    repo = Path(repo or REPO).resolve()
    ids = ids_seudonimos(repo / PSEUDONYMS_REL)
    publicos = documentos_publicos(repo) if publicos is None else frozenset(publicos)
    personas = personas_contexto(repo)
    juntos = problemas_contexto(documentos_contexto(repo))[0]   # AC192: con todos los ficheros de contexto
    if juntos:
        raise ErrorDeForma(f'forma inválida (spec 070): {m}' for m in juntos)
    lista = _lista_tal_cual(repo)
    invalidos = alias_invalidos_contexto(repo, ids, lista)   # AC134
    civiles = indice_civiles(personas, ids, lista, [(s, a) for _, _, s, a in invalidos])   # AC129/AC134
    nombres = nombres_contexto(repo)
    censo, sociedades = censo_documentos(repo, publicos), indice_sociedades(nombres)   # AC120
    citas = []   # AC90(b): las citas admitidas de la lista, si es válida (abajo)
    hallazgos = []

    def rel(path):
        return str(Path(path).relative_to(repo))

    def revisar(fichero, linea, entidades, textos, solo_caracteres=False, docs=(), extremos=(), dato_civil=()):
        """AC66/AC78/AC81/AC90 sobre una fila: la de una entidad civil, la de un seudónimo y todo su texto (una cita
        que nombra a su extremo civil, solo con la entrada de `citas:` de ese texto exacto; AC101: con cada documento de
        la fila, `docs`, los de su sección en `CAPAS`, en los de la entrada, y sin ninguno, nunca una cita con elipsis);
        AC89: ningún id con forma de seudónimo que el roster no declare; AC91: el nombre de cada extremo escrito por
        nombre (`extremos`), contra su extremo; y AC120: en la de un seudónimo, sus textos de `dato_civil`
        (`textos_dato_civil`) sin una sociedad real ni una fecha fuera de sus campos cerrados, salvo que la vía (b) los
        admita como cita literal."""
        entidades = set(entidades) - {None}
        seudos = entidades & ids

        def admitida(texto):   # AC125: la prueba única, sin export (vía b), con cada documento de la fila
            return all(prueba_cita(texto, d, seudos, citas) for d in docs or [None])
        for texto, tipo, doc in dato_civil if seudos else ():   # AC120/AC123/AC124
            hallazgos.extend(Hallazgo(fichero, linea, 'prosa', None, f'{MOTIVOS[motivo]} (huella {sha16(texto)})')
                             for motivo in dict.fromkeys(
                                 'sin-documento' if m == 'sin-documento' else 'dato-civil'
                                 for m in problemas_dato_civil(texto, tipo, censo, sociedades, frozenset(entidades),
                                                               doc, lambda t, d: prueba_cita(t, d, seudos, citas))))
        for motivo, huella in revisar_textos(entidades, textos, ids, civiles, publicos, nombres, admitida, sociedades):
            if solo_caracteres and motivo != 'sospechoso':
                continue
            texto = {'civil': f'fila civil {MOTIVOS["civil"]}',
                     'seudonimo': f'texto de una fila de seudónimo {MOTIVOS["seudonimo"]}'}.get(motivo, MOTIVOS[motivo])
            hallazgos.append(Hallazgo(fichero, linea, 'prosa', None, f'{texto} (huella {huella})'))
        for motivo, huella in problemas_extremos(extremos, ids, nombres):
            texto = f'nombre de un extremo civil {MOTIVOS["civil"]}' if motivo == 'civil' else MOTIVOS[motivo]
            hallazgos.append(Hallazgo(fichero, linea, 'prosa', None, f'{texto} (huella {huella})'))
        hallazgos.extend(Hallazgo(fichero, linea, 'roster-ausente', None,
                                  f'{MOTIVOS["roster-ausente"]} (huella {sha16(e)})')
                         for e in sorted(fuera_del_roster(entidades, textos, ids)))

    perm_path = repo / PERMITIDOS_REL
    if perm_path.exists():
        data, lineas = _documento(perm_path)
        problema = forma_permitidos(leer_yaml(perm_path.read_text(encoding='utf-8'), perm_path.name))
        listadas = []
        if problema:   # la misma validación que `cargar_permitidos`: aquí es un hallazgo, no una parada
            hallazgos.append(Hallazgo(rel(perm_path), 0, 'permitidos', None, problema))
        else:
            invalidas = validar_permitidos(data, ids, lineas=lineas, publicos=publicos, civiles=civiles)
            hallazgos += [Hallazgo(rel(perm_path), linea, 'permitidos', sid, motivo)
                          for linea, sid, motivo in invalidas]
            citas = list(data.get('citas') or [])
            # Principio P: solo una entrada `aristas` sin hallazgo publica su par.
            con_hallazgo = {motivo.partition(':')[0] for _, _, motivo in invalidas}
            listadas = [e for i, e in enumerate(data.get('aristas') or []) if f'aristas[{i}]' not in con_hallazgo]
        indice = {} if problema else indice_aristas(data)
    else:
        hallazgos.append(Hallazgo(PERMITIDOS_REL, 0, 'permitidos', None, 'falta la lista de permitidas'))
        indice, listadas = {}, []
    reglas = CAPAS[f'{RELATIONS_REL}/*.y*ml']
    publicas = reglas_publicas(repo / RELATIONS_REL)
    for regla in publicas:
        fila, motivo, sid = regla.fila, None, None
        spec = reglas[regla.seccion]
        textos, docs = textos_fila(spec.campos, fila), (spec.documentos(fila) if spec.documentos else ())
        if regla.seccion in ('deny', 'fix'):
            a, b = extremo(fila, 'source'), extremo(fila, 'target')
            revisar(rel(regla.fichero), regla.linea, {a, b}, textos, docs=docs, extremos=_extremos_texto_regla(fila),
                    dato_civil=textos_dato_civil(rel(regla.fichero), regla.seccion, fila))
            seudo = next((x for x in (a, b) if x in ids), None)
            if seudo:
                # AC54/AC63: un tipo o una página que el loader no aplica ya paran el árbol en su forma (AC144).
                hallazgos += [Hallazgo(rel(regla.fichero), regla.linea, regla.seccion, seudo, m)
                              for m in problemas_documentos(fila, publicos)]
            if es_par_sensible(a, b, ids):
                sid, otro = orientar(a, b, ids)
                tipo = (fila.get('corrected_type') or fila.get('type') or '').strip()
                if regla.seccion == 'deny':
                    motivo = 'deny sobre un par seudónimo↔sociedad/organismo (la retirada es la política del loader)'
                elif not arista_permitida(indice, sid, otro, tipo, doc=fila.get('doc') or None):
                    motivo = 'fix sobre un par seudónimo↔sociedad/organismo fuera de la lista de permitidas'
        elif regla.seccion == 'alias_deny':
            sid = entidad(fila)
            revisar(rel(regla.fichero), regla.linea, {sid}, textos)
            if sid in ids:
                motivo = 'alias_deny sobre un seudónimo (sus alias los fija la lista de permitidas)'
            else:   # AC60/AC70: la regla de un civil no nombra a un seudónimo, ni al retirar ni al conservar
                hallazgos += [Hallazgo(rel(regla.fichero), regla.linea, regla.seccion, None,
                                       f'una entrada nombra a un seudónimo judicial (huella {sha16(str(a).strip())})')
                              for modo in ('drop', 'keep_only')
                              for a in (fila.get(modo) if isinstance(fila.get(modo), list) else [fila.get(modo)])
                              if alias_civil_nombra_seudonimo(a, sid, ids)]
        else:
            canon, ns = entidad(fila.get('canonical') or {}), entidad(fila.get('namesake') or {})
            # Un split entre un seudónimo y su homónimo civil nombra al civil por definición (decisión Q2): de él
            # cuentan los caracteres (AC81) y sus citas, que solo pueden nombrar a su propio namesake civil (AC87/AC90).
            revisar(rel(regla.fichero), regla.linea, {canon, ns}, textos, solo_caracteres=bool({canon, ns} & ids),
                    docs=docs)
            if {canon, ns} & ids:
                hallazgos += [Hallazgo(rel(regla.fichero), regla.linea, 'prosa', None,
                                       f'texto de una fila de seudónimo {MOTIVOS["seudonimo"]} (huella {huella})')
                              for huella in citas_de_split(fila, ids, civiles, sociedades)]
            if es_par_sensible(canon, ns, ids):
                sid = orientar(canon, ns, ids)[0]
                motivo = 'split entre un seudónimo y una sociedad/organismo'
        if motivo:
            hallazgos.append(Hallazgo(rel(regla.fichero), regla.linea, regla.seccion, sid, motivo))
    reg_path = repo / REGISTRO_REL
    if reg_path.exists():
        data, lineas = _documento(reg_path)
        registro = CAPAS[REGISTRO_REL]['rectificaciones']
        explicitas = reglas_explicitas((r.seccion, r.fila) for r in publicas)
        for i, fila in enumerate(data.get('rectificaciones') or []):
            if not isinstance(fila, dict):
                continue
            fila = fila_juzgada(fila, *explicitas, listadas)   # principio P (fusión 070 ⟷ 071)
            revisar(rel(reg_path), _linea(lineas, 'rectificaciones', i), registro.entidades(fila, {}),
                    textos_fila(registro.campos, fila),
                    dato_civil=textos_dato_civil(REGISTRO_REL, 'rectificaciones', fila))
            dato = str(fila.get('dato') or '')
            seudos = {fila.get('entidad')} | set(_PERSONA.findall(dato))
            sid = next((s for s in sorted(seudos, key=str) if s in ids), None)
            if sid and _LITERAL_ORG.search(dato):
                hallazgos.append(Hallazgo(rel(reg_path), _linea(lineas, 'rectificaciones', i), 'registro', sid,
                                          'fila del registro de un seudónimo con la sociedad u organismo literal'))
    for patron, ficheros in _ficheros_por_capa(repo):   # AC79: las demás capas, con la misma partición
        secciones = CAPAS[patron]
        documentos = [(path, *_documento(path)) for path in ficheros]
        de_capa = _nombres_capa(secciones, [data for _, data, _ in documentos])
        for path, data, lineas in documentos:
            for seccion, spec in secciones.items():
                filas = [data] if seccion is None else data.get(seccion) or []
                for i, fila in enumerate(filas if isinstance(filas, list) else []):
                    if isinstance(fila, dict):
                        revisar(rel(path), 1 if seccion is None else _linea(lineas, seccion, i),
                                spec.entidades(fila, de_capa), textos_fila(spec.campos, fila),
                                docs=spec.documentos(fila) if spec.documentos else (),
                                extremos=spec.extremos(fila) if spec.extremos else (),
                                dato_civil=textos_dato_civil(rel(path), seccion, fila))
    roster = repo / PSEUDONYMS_REL
    if roster.exists():
        # AC84: cada fila del roster pasa el cierre común como fila de su seudónimo (su `name`, AC83; su prosa, AC78 con
        # los documentos del árbol; sus caracteres, AC81), sin contar el id ni el nombre de un seudónimo homónimo:
        # desambiguar homónimos es la función del roster. Y además AC68: no nombra una entidad fuera de él por su id
        # (sin caja, sobre el texto normalizado) ni a una persona civil del contexto por su nombre. Huellas.
        data, lineas = _documento(roster)
        campos, del_roster = CAPAS[PSEUDONYMS_REL]['pseudonyms'].campos, {s.lower() for s in ids}
        for i, fila in enumerate(data.get('pseudonyms') or []):
            if not isinstance(fila, dict):
                continue
            sid, linea = str(fila.get('id') or '').strip(), _linea(lineas, 'pseudonyms', i)
            textos = textos_fila(campos, fila)
            homonimos = {s for s in ids if sorted(_nombre(s)) == sorted(_nombre(sid))}
            revisar(rel(roster), linea, {sid} | homonimos, textos,
                    dato_civil=textos_dato_civil(PSEUDONYMS_REL, 'pseudonyms', fila))
            legibles = [_normalizar(t) for t, _ in textos]
            ajenos = {x.rstrip('-') for t in legibles for x in _ID_ENTIDAD.findall(t)}
            for eid in sorted(x for x in ajenos if x.lower() not in del_roster):
                hallazgos.append(Hallazgo(rel(roster), linea, 'roster', None,
                                          f'una fila nombra a una entidad civil (huella {sha16(eid)})'))
            # Con sus lecturas (AC104); los nombres de su propia entidad no cuentan (AC118).
            for nombre in sorted({n for t, _ in textos for n in _nombres_en(t, civiles, {sid} | homonimos)}):
                hallazgos.append(Hallazgo(rel(roster), linea, 'roster', None,
                                          f'una fila nombra a una persona civil (huella {sha16(nombre)})'))
        # AC118: las filas que no pueden ser de un seudónimo (un civil declarado en el roster).
        hallazgos += [Hallazgo(rel(roster), linea, 'roster', None, motivo)
                      for linea, motivo in problemas_roster(repo, ids, personas)]
    # AC134: el alias inválido que un fichero de contexto da a un seudónimo, en su sitio.
    hallazgos += [Hallazgo(fichero, linea, 'alias', sid, f'{MOTIVOS["alias-capa"]} (huella {sha16(alias)})')
                  for fichero, linea, sid, alias in invalidos]
    hallazgos += [Hallazgo(fichero, linea, 'alias', None, f'{MOTIVOS["alias-identificador"]} (huella {sha16(alias)})')
                  for fichero, linea, alias in alias_identificadores_contexto(repo, ids)]   # AC167
    return sorted(hallazgos, key=lambda h: (h.fichero, h.linea, h.clase, h.motivo))


# --- puerta del Path A: el grafo exportado frente a la lista -------------------------------------

# AC79: los props de cadena del export. Por defecto, prosa; estos son estructura (ids, nombres, fechas, vocabulario,
# claves de fuente) y estos otros, documento, cita o URL. Los nodos `Chunk`/`Document` no cuentan: su `text`/`title` es
# el documento fuente. `community_label` tampoco (la etiqueta de comunidad que escribe `make centralidad` con nombres de
# miembros: copertenencia, no identificación; seguimiento abierto). La procedencia (`source_doc`, `doc`) no es texto:
# AC78(c) mira los documentos nombrados en prosa o citas, y de la procedencia de una fila de seudónimo basta con que sea
# un nodo Document del export (`verificar_snapshot`): el grafo cita documentos que la app sirve sin versionarlos.
_ETIQUETAS_DOCUMENTO = frozenset({'Chunk', 'Document'})


def solo_documental(etiquetas) -> bool:
    """Un nodo EXCLUSIVAMENTE documental: etiquetas no vacías y todas Chunk o Document. Solo esos quedan fuera de las guardas
    de seudónimos (su `text`/`title` es el documento fuente); un nodo que además lleva una etiqueta de entidad (Person+Document),
    o que no lleva ninguna, es una ficha como las demás (falla cerrado: `entityNames` y la búsqueda lo sirven)."""
    etiquetas = frozenset(etiquetas or ())
    return bool(etiquetas) and etiquetas <= _ETIQUETAS_DOCUMENTO
_PROPS_FUERA = frozenset({'community_label'})
_PROPS_PROCEDENCIA = ('source_doc', 'doc')
_PROPS_ESTRUCTURA = frozenset({
    'entity_id', 'name', 'canonical_name', 'name_original', 'type', 'claim_id', 'source_kind', 'source_chunk_id',
    'source_date', 'date', 'date_norm', 'currency', 'status', 'status_fecha', 'status_fuente_tipo', 'status_revisado',
    'summary_updated', 'nif', 'provincia', 'hoja_registral', 'borme_id', 'cnae', 'fecha', 'desde', 'fecha_constitucion',
    'concurso_fecha', 'disuelta_fecha', 'liquidacion_fecha', 'fecha_cese', 'fecha_nombramiento', 'boe_key',
    'boe_id_cese', 'boe_id_nombramiento', 'ambito', 'article', 'bdns_endpoint', 'bdns_key', 'instrumento',
    'placsp_key', 'rango', 'denominacion', 'denominacion_bdns', 'denominacion_placsp', 'amp_denominacion',
    'placsp_denominacion', 'amp_nif', 'placsp_nif', 'boe_rango'})


def clase_prop(clave, valor) -> str | None:
    """La clase de un prop de cadena de un nodo o de una arista del export (None: fuera de la comprobación)."""
    if clave in _PROPS_FUERA:
        return None
    if clave in _PROPS_ESTRUCTURA or clave in _PROPS_PROCEDENCIA or clave.endswith('_rango'):
        return ESTRUCTURA
    if clave == 'status_fuente':
        return URL if str(valor).startswith(('http://', 'https://')) else DOC
    if clave in ('source_quote', 'quote'):
        return CITA
    if clave in ('url', 'source_url', 'sources', 'summary_sources') or clave.startswith('url_') or 'link' in clave:
        return URL
    return PROSA


# AC86: el nombre y los alias de un nodo de persona, sociedad u organismo fuera del roster se leen como en el árbol.
_PROPS_NOMBRE = {'name': NOMBRE, 'canonical_name': NOMBRE, 'name_original': NOMBRE, 'aliases': ALIAS}
# AC338 (3ª pasada de Codex): los nombres que `clase_prop` deja como estructura se leen por sus caracteres: el de CUALQUIER
# ficha, sea cual sea su etiqueta (`name`, `canonical_name`, `name_original`), y las denominaciones oficiales de la
# sociedad que sirve la API (nodos y aristas de BDNS/PLACSP/ampliada). Los alias ya son prosa en todas.
_PROPS_CARACTERES = (*(k for k, c in _PROPS_NOMBRE.items() if c == NOMBRE), 'denominacion', 'denominacion_bdns',
                     'denominacion_placsp', 'amp_denominacion', 'placsp_denominacion')
_ETIQUETAS_NOMBRADAS = frozenset({'Person', *ETIQUETAS_SENSIBLES})

# AC346-AC349 (enmienda 91, adenda 5; decisión del dueño): LISTA BLANCA de caracteres en nombres y slugs, en lugar de reglas por categorías
# Unicode, que cayeron tres veces (formas que NFKC lleva a un permitido, marcas Mc, letras latinas con forma de otra letra, U+0131 en el slug).
# Un nombre, un nombre canónico, un nombre original, un alias o una denominación (nodos y aristas) solo lleva ASCII imprimible (U+0020-U+007E)
# y los no ASCII de este literal, que son EXACTAMENTE los de esas propiedades del export real (snap-qa071a: 6 851 valores de nodos no
# documentales, 63 de aristas; code point: valores en que aparece): ª U+00AA 32, « U+00AB 2, ° U+00B0 25, º U+00BA 51, » U+00BB 2, Á U+00C1 103,
# É U+00C9 12, Í U+00CD 37, Ñ U+00D1 16, Ó U+00D3 53, Ú U+00DA 11, Ü U+00DC 1, à U+00E0 1, á U+00E1 394, ä U+00E4 1, ç U+00E7 1, è U+00E8 1,
# é U+00E9 319, ê U+00EA 1, í U+00ED 677, ñ U+00F1 251, ó U+00F3 1245, ö U+00F6 1, ú U+00FA 171, ü U+00FC 9, – U+2013 21, — U+2014 5,
# “ U+201C 1, ” U+201D 1, ﬁ U+FB01 1. Un slug (el `entity_id` tras «:») solo lleva lo que el generador de ids produce: a-z, 0-9 y «-»
# (`entity_ids.slug`, pipeline/src/entity_ids.py:17-20; los 3 568 del export y los 1 366 `entity_id` explícitos de los YAML de datos: sin
# mayúsculas, puntos ni no ASCII; el fixture `FIXTURE_NODO` sí lleva guion bajo, y es justo lo que la puerta ha de denunciar). El prefijo del
# id (lo anterior a «:») es de otro literal, `_PREFIJOS_ID` (AC351, `razon_entity_id`). La comprobación es sobre el texto EN CRUDO y sobre su imagen NFKC; basta que falle una. Un
# nombre real con un carácter nuevo para la puerta lo añade al literal con su decisión (AC348: o choca con el nombre de un seudónimo, o no se
# lee como esa letra; el test lo fija carácter a carácter). Es deliberado.
_NO_ASCII_NOMBRES = frozenset('\u00aa\u00ab\u00b0\u00ba\u00bb\u00c1\u00c9\u00cd\u00d1\u00d3\u00da\u00dc\u00e0\u00e1\u00e4\u00e7\u00e8\u00e9\u00ea'
                              '\u00ed\u00f1\u00f3\u00f6\u00fa\u00fc\u2013\u2014\u201c\u201d\ufb01')
_ALFABETO_NOMBRES = frozenset(map(chr, range(0x20, 0x7F))) | _NO_ASCII_NOMBRES
_ALFABETO_SLUGS = frozenset('abcdefghijklmnopqrstuvwxyz0123456789-')


# AC351 (adenda 6): el `entity_id` ENTERO es la clave pública de una ficha. Su prefijo (lo anterior a «:») es uno de los 9 del export real
# (snap-qa071a, 3 568 fichas no documentales: Person 584, Organization 643, Event 1135, Location 461, Company 416, LegalCase 217, Court 49,
# Contract 42, Crime 21) y los `entity_id` explícitos de los YAML de datos (1 366, todos de los 8 primeros); un tipo de la ontología sin ejemplo
# en el export (MoneyAmount) no está. Literal cerrado: un prefijo nuevo se añade aquí a propósito, como un carácter nuevo en el de los nombres.
_PREFIJOS_ID = frozenset({'Company', 'Contract', 'Court', 'Crime', 'Event', 'LegalCase', 'Location', 'Organization', 'Person'})
_ALFANUMERICOS_SLUG = frozenset('abcdefghijklmnopqrstuvwxyz0123456789')
_PAREJA_SOCIEDAD = frozenset({'Company', 'Organization'})
# AC364: las etiquetas de un nodo del export (snap-qa071a y qa067b, el mismo conjunto: 12, una por nodo; las 9 de los prefijos más Chunk, Document y Claim). Una
# etiqueta que no está aquí (un tipo de la ontología sin ejemplo, MoneyAmount; otra caja; un homógrafo) no se cruza con el prefijo y es un problema: literal
# cerrado, como `_PREFIJOS_ID`; un tipo nuevo se añade aquí a propósito.
_ETIQUETAS_NODO = _PREFIJOS_ID | {'Chunk', 'Claim', 'Document'}


def razon_entity_id(eid, etiquetas=None) -> str | None:
    """AC351: por qué un `entity_id` no es una clave pública válida (la razón, nunca el valor), o None si lo es. No es una cadena; no lleva «:»;
    su prefijo no es uno de `_PREFIJOS_ID`; su slug (lo posterior al primer «:») está vacío o no lleva ninguna letra ni cifra («Court:---»); o,
    con las `etiquetas` del nodo, entre las de tipo de entidad (`_PREFIJOS_ID`) no lleva solo la de su prefijo (AC360; en snap-qa071a los 3 568
    nodos con `entity_id` llevan exactamente una, la de su prefijo; un Person+Document lleva la suya y la documental), salvo la pareja
    Company+Organization, la única que la política del loader trata como una sola clase. Los caracteres del slug los revisa aparte
    `caracteres_no_permitidos`."""
    if not isinstance(eid, str):
        return f'no es una cadena (tipo {type(eid).__name__})'
    prefijo, dos_puntos, slug = eid.partition(':')
    if not dos_puntos:
        return 'sin «:»'
    if prefijo not in _PREFIJOS_ID:
        return 'prefijo fuera del literal'
    if not _ALFANUMERICOS_SLUG & set(slug):
        return 'slug vacío o sin ninguna letra ni cifra'
    if etiquetas is not None:
        tipos = frozenset(etiquetas) & _PREFIJOS_ID
        if prefijo not in tipos or (tipos != {prefijo} and tipos != _PAREJA_SOCIEDAD):
            return 'prefijo y etiquetas discordantes'
    return None


# AC352 (adenda 6): un sustituto UTF-16 suelto (U+D800-U+DFFF) no es texto: un JSON con `\ud800` lo produce y lo escribe en un mensaje rompería la
# salida de la puerta. Cualquier cadena de cualquier registro del export (props, claves, etiquetas, tipo, referencias) que lo lleve es un problema
# con su huella y su code point, y una referencia con uno se lee como `~huella` en los mensajes. El export real tiene 0.
_SUSTITUTO = re.compile('[\ud800-\udfff]')
# AC358/AC359 (adenda 7): un mensaje de la puerta nunca lleva el valor de un campo inválido ni el nombre de una clave que no es del export; un
# anidamiento más hondo que el del export (1 nivel en snap-qa071a y en qa067b) es un problema y no se recorre (ni `RecursionError`); y los
# únicos campos de primer nivel de un registro son los del export (nodos: ref, labels, props; aristas: ref, start, end, type, props).
_TOPE_PROFUNDIDAD = 32
# Las 154 claves de `props` de nodos y aristas de snap-qa071a y de qa067b (el mismo conjunto en los dos): lo único que un mensaje nombra; cualquier otra clave, por su huella.
_CLAVES_DE_PROPS = frozenset((
    'aliases ambito amount amp_agr_n amp_agr_nota amp_agr_total_eur amp_ccaa_andalucia_n amp_ccaa_andalucia_nota amp_ccaa_andalucia_rango '
    'amp_ccaa_andalucia_total_eur amp_ccaa_euskadi_n amp_ccaa_euskadi_nota amp_ccaa_euskadi_rango amp_ccaa_euskadi_total_eur amp_ccaa_galicia_n '
    'amp_ccaa_galicia_nota amp_ccaa_galicia_rango amp_ccaa_galicia_total_eur amp_denominacion amp_lic_n amp_lic_nota amp_lic_rango '
    'amp_lic_total_eur amp_nif amp_nota amp_sin_adjudicaciones anonymized article bdns_endpoint bdns_key betweenness boe_id_cese '
    'boe_id_nombramiento boe_incidencias boe_key boe_n_cargos boe_nota boe_rango borme_id canonical_name capital_historico capital_social cargo '
    'chunk_id chunk_index claim_id closeness cnae community community_label concurso_fecha concurso_juzgado convocante_detalle court currency '
    'date date_norm date_year degree denominacion denominacion_bdns denominacion_placsp desde disuelta disuelta_fecha doc_date doc_id doc_title '
    'doc_type domicilio ejemplo_link ejemplo_titulo embedding en_concurso en_liquidacion entity_id fecha fecha_cese fecha_constitucion '
    'fecha_nombramiento firme fuente hoja_registral importe ingested_at instrumento is_boe is_context is_contratacion is_contratacion_amp '
    'is_mercantil is_press is_pseudonym is_ultima_hora liquidacion_fecha low_text_pages mentions n_contratos n_emergencia n_negociado_sp name '
    'name_original nif note object objeto_social organo_detalle outlet page_count page_end page_start placsp_denominacion placsp_key '
    'placsp_n_contratos placsp_nif placsp_nota placsp_organos_top placsp_rango placsp_sin_menores placsp_total_eur presupuesto_total_eur '
    'provincia pseudonym_note qa_set_run rango rol role source_chunk_id source_date source_doc source_kind source_page source_quote source_url '
    'status status_explicit status_fecha status_fuente status_fuente_tipo status_revisado summary summary_sources summary_updated tercero_id text '
    'title titulo_cese titulo_nombramiento topic total_eur url url_cese url_nombramiento via_ute').split())
_CAMPOS_DE_REGISTRO = {'nodo': ('ref', 'labels', 'props'), 'arista': ('ref', 'start', 'end', 'type', 'props')}
_CAMPOS_DEL_REGISTRO = frozenset(c for campos in _CAMPOS_DE_REGISTRO.values() for c in campos)


# AC365/AC369: lo que un mensaje escribe de un registro es texto imprimible de UNA línea: una referencia (o un tipo) con controles (Cc), de formato o
# bidireccionales (Cf: U+202E, U+2066, U+061C, U+200B…), sin asignar o de uso privado (Cn, Co), un sustituto suelto (Cs) o un separador de línea o de
# párrafo (Zl, Zp: U+2028, U+2029) se escribe por su huella, como la que lleva un sustituto suelto (una referencia con un salto de línea inyectaría líneas
# en el registro de la puerta, y un control bidireccional cambiaría cómo se lee). Los espacios (Zs) no parten la línea: se escriben tal cual.
_CATEGORIAS_NO_IMPRIMIBLES = frozenset({'Cc', 'Cf', 'Cn', 'Co', 'Cs', 'Zl', 'Zp'})
_NO_IMPRIMIBLE_ASCII = re.compile('[\x00-\x1f\x7f]')


def _no_imprimible(texto) -> bool:
    """¿Lleva `texto` un carácter que un mensaje de una línea no puede escribir (`_CATEGORIAS_NO_IMPRIMIBLES`)?"""
    if texto.isascii():
        return bool(_NO_IMPRIMIBLE_ASCII.search(texto))
    return any(unicodedata.category(c) in _CATEGORIAS_NO_IMPRIMIBLES for c in texto)


# AC11 (spec 072): las referencias (`ref`, `start`, `end`) de snap-qa071a son los `elementId` de Neo4j, `4:<uuid>:<n>`: medido, solo `0-9`, `a-e`, `:` y `-` (las
# 8 851 de nodos y las 36 318 de aristas; los uuid son hexadecimales: la `f` es el resto de su alfabeto, que otro export puede llevar). Una referencia solo se escribe tal
# cual en un mensaje si TODOS sus caracteres son de este literal cerrado; cualquier otra, por su `~huella`. Así ninguna escrita tal cual lleva un carácter invisible o
# combinante (U+3164, U+0301, un espacio que no es ASCII…), ni empieza por `~`: no se confunde con la `~huella` de otra.
_ALFABETO_REFERENCIAS = frozenset('0123456789abcdef:-')


def _ref_seguro(ref):
    """La referencia (`ref`, `start`, `end`) de un nodo o una arista como se escribe en un mensaje: la misma si todos sus caracteres son del literal cerrado
    (`_ALFABETO_REFERENCIAS`); si no, su `~huella`. Solo para escribir: la identidad de un registro es su referencia original (AC368)."""
    return ref if not isinstance(ref, str) or all(c in _ALFABETO_REFERENCIAS for c in ref) else f'~{sha16(ref)}'


def _tipo_seguro(tipo):
    """El `type` de una arista como se escribe en un mensaje: el mismo, salvo que lleve un sustituto suelto o un carácter no imprimible de una línea
    (`~huella`, AC365/AC369)."""
    return f'~{sha16(tipo)}' if isinstance(tipo, str) and _no_imprimible(tipo) else tipo


def _excede_profundidad(valor, tope=_TOPE_PROFUNDIDAD) -> bool:
    """AC359: ¿anida `valor` (listas y objetos) más de `tope` niveles? Sin recursión: un anidamiento de miles de niveles no hace caer la puerta."""
    pila = [(valor, 1)]
    while pila:
        v, d = pila.pop()
        if isinstance(v, (dict, list, tuple)):
            if d > tope:
                return True
            pila.extend((x, d + 1) for x in (v.values() if isinstance(v, dict) else v) if isinstance(x, (dict, list, tuple)))
    return False


def _huella_de_valor(v) -> str:
    """AC358: la huella de un valor de cualquier forma (una lista, un objeto, otro tipo), sin lanzar nunca: uno más hondo de lo que se serializa
    se resume por su tipo."""
    try:
        return sha16(json.dumps(v, sort_keys=True, default=str))
    except (RecursionError, ValueError, TypeError):
        return sha16(f'{type(v).__name__}:profundo')


def _nombre_de_clave(k) -> str:
    """AC358: cómo se nombra en un mensaje una clave de `props`: ella misma si es una de las del export (`_CLAVES_DE_PROPS`: el nombre de una propiedad, esquema
    y no dato); si no, `<clave huella>` (nunca su texto: una clave ajena puede llevar cualquier cosa, también un sustituto o un valor, aunque parezca un
    identificador)."""
    return k if isinstance(k, str) and k in _CLAVES_DE_PROPS else f'<clave {sha16(k)}>'


def sustitutos_sueltos(registro) -> list[tuple[str, str, list[str]]]:
    """AC352: [(clave, cadena, code points)] de cada cadena de un registro del export (nodo o arista) que lleva un sustituto UTF-16 suelto, con el
    nombre del campo (el de la propiedad si está en `props`; `<clave huella>` si el nombre no es el de un campo o el sustituto está en una clave).
    Una vez por clave y cadena. Recorrido sin recursión y sin pasar del tope de profundidad (`_excede_profundidad` lo denuncia aparte)."""
    out, vistos = [], set()
    def de_primer_nivel(campo):   # el nombre de un campo del registro; el de una clave ajena, por su huella
        return campo if campo in _CAMPOS_DEL_REGISTRO else f'<clave {sha16(campo)}>'
    pila = [(valor, de_primer_nivel(campo), 1, True) for campo, valor in registro.items()]
    pila += [(campo, de_primer_nivel(campo), 1, False) for campo in registro if isinstance(campo, str) and _SUSTITUTO.search(campo)]
    while pila:
        obj, clave, d, de_primer_nivel = pila.pop()
        if isinstance(obj, str):
            if (m := _SUSTITUTO.findall(obj)) and (clave, obj) not in vistos:
                vistos.add((clave, obj))
                out.append((clave, obj, sorted({f'U+{ord(c):04X}' for c in m})))
        elif d <= _TOPE_PROFUNDIDAD and isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and _SUSTITUTO.search(k):
                    pila.append((k, f'<clave {sha16(k)}>', d + 1, False))
                pila.append((v, _nombre_de_clave(k) if clave == 'props' and de_primer_nivel else clave, d + 1, False))
        elif d <= _TOPE_PROFUNDIDAD and isinstance(obj, (list, tuple)):
            pila.extend((v, clave, d + 1, False) for v in obj)
    return sorted(out, key=lambda x: (x[0], x[1]))


def _problemas_de_sustitutos(que, registro, ref) -> list[str]:
    """Los problemas de sustitutos sueltos de un registro (el original), citándolo por su referencia SEGURA `ref` (AC358)."""
    return [f'{MOTIVOS["sustituto-suelto"]} ({que} {ref}, `{clave}`, huella {sha16(valor)}, {", ".join(cps[:8])})'
            for clave, valor, cps in sustitutos_sueltos(registro)]


def normalizar_registro(registro, que) -> tuple[dict, list[tuple[str, str]]]:
    """Ampliación de la ronda 5 y AC359: (registro seguro, [(campo, razón)]) de un nodo (`que` = 'nodo') o una arista ('arista') del export. Un
    campo estructural con otra forma que la del export es un problema, nunca una excepción ni un silencio: el registro que no es un objeto; una
    clave de primer nivel ajena al export (`_CAMPOS_DE_REGISTRO`: se descarta); `ref` de un nodo y `ref`, `start`, `end` y `type` de una arista que
    no son una cadena o faltan; `labels` que no es una lista de cadenas, o con una etiqueta fuera de `_ETIQUETAS_NODO` (AC364, por su huella); `props` que no es un objeto; en una arista, un `source_kind` o un
    `source_doc` que no es una cadena (una lista no es hashable); y un valor de `props` o un campo más hondo de `_TOPE_PROFUNDIDAD` niveles. El
    registro seguro lleva en su lugar una referencia `~huella` (la de un `ref` o un registro inválido; también una cadena con controles o saltos de línea, AC365), '' (`start`, `end`, `type`), las
    etiquetas que sí son cadenas, `{}` o nada. `labels` y `props` pueden faltar. Las referencias que son cadenas con un sustituto suelto se leen
    `~huella`. Las razones no llevan nunca el valor; el nombre del campo, solo si es del export (`_nombre_de_clave`)."""
    def forma(v):
        return f'no es una cadena (tipo {type(v).__name__})'
    if not isinstance(registro, dict):
        seguro = {'ref': f'~{_huella_de_valor(registro)}', 'labels': [], 'props': {}, **({'start': '', 'end': '', 'type': ''} if que == 'arista' else {})}
        return seguro, [('registro', f'no es un objeto (tipo {type(registro).__name__})')]
    permitidos = _CAMPOS_DE_REGISTRO[que]
    seguro, razones = {k: v for k, v in registro.items() if k in permitidos}, []
    razones += [(f'<clave {sha16(k)}>', 'clave de primer nivel ajena al export') for k in registro if k not in permitidos]
    hondos = set()   # AC14: lo que ya se denunció por hondo no se denuncia otra vez como «no es una cadena (NoneType)»
    for campo in permitidos:
        if campo in seguro and campo != 'props' and _excede_profundidad(seguro[campo]):
            razones.append((campo, f'anidamiento de más de {_TOPE_PROFUNDIDAD} niveles'))
            seguro[campo] = [] if campo == 'labels' else None
            hondos.add(campo)
    for campo in permitidos[:1] if que == 'nodo' else ('ref', 'start', 'end', 'type'):
        v = seguro.get(campo)
        if not isinstance(v, str):
            if campo not in hondos:
                razones.append((campo, 'falta' if campo not in registro else forma(v)))
            seguro[campo] = f'~{_huella_de_valor(registro.get(campo))}' if campo == 'ref' else ''
        else:
            seguro[campo] = _tipo_seguro(v) if campo == 'type' else _ref_seguro(v)
    if 'labels' in seguro:
        etiquetas = seguro['labels']
        if not isinstance(etiquetas, list) or not all(isinstance(e, str) for e in etiquetas):
            if not any(r[0] == 'labels' for r in razones):
                razones.append(('labels', f'no es una lista de cadenas (tipo {type(etiquetas).__name__})'))
            seguro['labels'] = [e for e in etiquetas if isinstance(e, str)] if isinstance(etiquetas, list) else []
    if que == 'nodo':   # AC364: las etiquetas de texto que no son del export, cada una por su huella (nunca su valor)
        razones += [('labels', f'etiqueta fuera del literal (huella {sha16(e)})') for e in dict.fromkeys(seguro.get('labels') or ()) if e not in _ETIQUETAS_NODO]
    if 'props' in seguro:
        if not isinstance(seguro['props'], dict):
            razones.append(('props', f'no es un objeto (tipo {type(seguro["props"]).__name__})'))
            seguro['props'] = {}
        else:
            hondos = [k for k, v in seguro['props'].items() if _excede_profundidad(v)]
            razones += [(_nombre_de_clave(k), f'anidamiento de más de {_TOPE_PROFUNDIDAD} niveles') for k in hondos]
            seguro['props'] = {k: v for k, v in seguro['props'].items() if k not in hondos}
    if que == 'arista':   # la capa y el documento de una arista (los lee la lista de permitidas, que los pone en un conjunto): una cadena, o ausentes
        for clave in ('source_kind', 'source_doc'):
            v = seguro.get('props', {}).get(clave)
            if v is not None and not isinstance(v, str):
                razones.append((clave, forma(v)))
                seguro['props'] = {k: x for k, x in seguro['props'].items() if k != clave}
    return seguro, razones


def _problemas_de_forma(que, original, razones, seguro) -> list[str]:
    """Los problemas de `normalizar_registro` de un registro y los de sustitutos sueltos (con el original, que es donde están), siempre citándolo
    por su referencia SEGURA (AC358): nunca el valor de una referencia o de un campo inválidos."""
    return [f'{MOTIVOS["forma-registro"]} ({que} {seguro["ref"]}, `{campo}`, {razon})' for campo, razon in razones] + (
        _problemas_de_sustitutos(que, original, seguro['ref']) if isinstance(original, dict) else [])


def _leer_jsonl(ruta, fichero) -> tuple[list, list[str]]:
    """AC359: los registros de un fichero del export (uno por línea) y los problemas de lo que no se pudo leer: una línea vacía, un JSON roto o más
    hondo de lo que se lee, con su número de línea (nunca su texto). La puerta sigue con el resto. Los bytes que no son UTF-8 se leen como
    sustitutos sueltos (`errors='surrogateescape'`), que la guarda de sustitutos denuncia."""
    registros, problemas = [], []
    with ruta.open(encoding='utf-8', errors='surrogateescape') as f:
        for numero, linea in enumerate(f, 1):
            try:
                if not linea.strip():
                    problemas.append(f'{MOTIVOS["linea-jsonl"]} ({fichero}, línea {numero}, vacía)')
                else:
                    registros.append(json.loads(linea))
            except RecursionError:
                problemas.append(f'{MOTIVOS["linea-jsonl"]} ({fichero}, línea {numero}, anidamiento excesivo)')
            except ValueError:   # JSONDecodeError
                problemas.append(f'{MOTIVOS["linea-jsonl"]} ({fichero}, línea {numero}, no es JSON)')
    return registros, problemas


def caracteres_no_permitidos(texto, alfabeto) -> list[str]:
    """AC346: los code points («U+0131»), distintos y ordenados, que `texto` lleva fuera de `alfabeto` (`_ALFABETO_NOMBRES` o `_ALFABETO_SLUGS`), en
    crudo o en su imagen NFKC. Nunca el texto. Vacío: todo está permitido."""
    fuera = {c for c in texto if c not in alfabeto}
    if not texto.isascii():
        fuera |= {c for c in unicodedata.normalize('NFKC', texto) if c not in alfabeto}
    return sorted(f'U+{ord(c):04X}' for c in fuera)


def tipos_de_nombre(props) -> list[tuple[str, str]]:
    """AC338 (4ª pasada de Codex): [(clave, tipo)] de lo que la web pinta como un nombre y no es lo que se lee: `name`, `canonical_name`,
    `name_original` y las denominaciones (`_PROPS_CARACTERES`) han de ser UNA cadena (una lista de fragmentos se pinta igual que su
    texto y ni los caracteres ni el homónimo la leen entera), y `aliases` una lista de cadenas, sin anidar. Solo las claves presentes:
    una ausente no es un tipo. Nunca el valor."""
    out = [(k, type(props[k]).__name__) for k in _PROPS_CARACTERES if k in props and not isinstance(props[k], str)]
    alias = props.get('aliases')
    if 'aliases' in props:
        if not isinstance(alias, list):
            out.append(('aliases', type(alias).__name__))
        elif any(not isinstance(a, str) for a in alias):
            out.append(('aliases', 'lista con elementos que no son una cadena'))
    return out


def con_nombres_validos(props) -> dict:
    """`props` sin lo que `tipos_de_nombre` denuncia (los alias, solo sus cadenas): para seguir leyendo el nodo sin que un valor de otro
    tipo rompa una guarda; el problema ya está dado."""
    out = {k: v for k, v in props.items() if k not in _PROPS_CARACTERES or isinstance(v, str)}
    if 'aliases' in out:
        out['aliases'] = [a for a in out['aliases'] if isinstance(a, str)] if isinstance(out['aliases'], list) else []
    return out


def textos_props(props, nombres=False) -> list[tuple[str, str]]:
    """[(texto, clase)] de los props de cadena (y de las listas de cadenas) de un nodo o de una arista del export. Con
    `nombres` (un nodo de persona, sociedad u organismo fuera del roster, AC86), su nombre y sus alias son
    `NOMBRE`/`ALIAS` y no estructura: el detector los lee como los de una fila civil del árbol."""
    def clase(clave, v):
        return _PROPS_NOMBRE[clave] if nombres and clave in _PROPS_NOMBRE else clase_prop(clave, v)
    return [(v, c) for clave, valor in props.items() for v in (valor if isinstance(valor, list) else [valor])
            if isinstance(v, str) for c in [clase(clave, v)] if c]


# AC338 (enmienda 91): la ficha civil cuyo `entity_id` es el nombre de pila de un seudónimo y no se re-keyea (su slug
# es la URL pública de la ficha: un re-key la rompería; decisión del dueño, 12-nov). Se exime por la huella de su
# entity_id, nunca por recuento: otro civil con un slug así es un problema.
SLUG_CIVIL_EXENTO = frozenset({'efd79400d984c7cd'})


# Spec 072 (AC17, AC18): los guiones y rayas con los que el lector (`_UNION_CON_ESPACIO`) une una palabra partida al final de una línea («Sl- LVIA») y las comillas que lee a través
# de ellos («Sl'- LVIA»): las 7 que permite la lista blanca de nombres (`_ALFABETO_NOMBRES`). Literales propios: `variante` no usa las tablas del lector, y una prueba compara los dos.
_GUIONES_DE_FIN_DE_LINEA = frozenset('-\u2010\u2011\u2012\u2013\u2014\u2015')
_COMILLAS_DE_UNION = frozenset('"\'`\u00ab\u00bb\u201c\u201d')


def _tiradas_de_mayusculas(texto) -> bytearray:
    """Spec 072: una marca por posición de `texto` que está en una tirada máxima de letras (`str.isalpha`) en mayúsculas: al menos 2 letras mayúsculas (`str.isupper`) y ninguna
    otra letra que no sea la U+006C."""
    n = len(texto)
    marcas = bytearray(n)
    inicio = 0
    while inicio < n:
        if not texto[inicio].isalpha():
            inicio += 1
            continue
        fin, mayusculas, otra = inicio, 0, False
        while fin < n and texto[fin].isalpha():
            if texto[fin].isupper():
                mayusculas += 1
            elif texto[fin] != 'l':
                otra = True
            fin += 1
        if mayusculas >= 2 and not otra:
            marcas[inicio:fin] = b'\x01' * (fin - inicio)
        inicio = fin
    return marcas


def _palabras_en_mayusculas(texto, une=True, con_mayuscula=False) -> bytearray:
    """Spec 072 (AC17, AC18, AC21, AC23): una marca por posición de `texto` que está en una racha de trozos en mayúsculas. Un TROZO es una tirada máxima de caracteres que no son blancos;
    es LIMPIO si no tiene otra letra minúscula que la U+006C. Dos trozos vecinos son UNIBLES cuando entre una letra o cifra y la siguiente hay un tramo formado SOLO por guiones
    (`_GUIONES_DE_FIN_DE_LINEA`), comillas (`_COMILLAS_DE_UNION`) y blancos, con al menos un guion y un blanco (el blanco lo da el corte entre trozos): un superconjunto deliberado
    de lo que une el lector, porque unir de más solo propone un texto más y la colisión la decide el lector sobre él. Una RACHA es una sucesión máxima de trozos limpios
    consecutivos y unibles; un trozo no limpio la corta y no arrastra a sus vecinos. Se marca la racha que suma al menos 2 letras mayúsculas (`str.isupper`): un trozo solo es una
    racha de uno. Sin `une` cada trozo es su propia racha (la palabra de `08a30cb`). Con `con_mayuscula` solo se marcan los trozos de la racha con al menos una mayúscula: una «l» real
    de un trozo sin mayúsculas (una palabra con cifras, «10l4») se queda como está. Lineal: trozos, prefijos y saltos calculados una vez."""
    n = len(texto)
    marcas = bytearray(n)
    trozos, de = [], [0] * n   # (inicio, fin, mayúsculas, limpio); el trozo de cada posición
    i = 0
    while i < n:
        if texto[i].isspace():
            i += 1
            continue
        fin, mayusculas, limpio = i, 0, True
        while fin < n and not texto[fin].isspace():
            c = texto[fin]
            if c.isalpha():
                if c.isupper():
                    mayusculas += 1
                elif c != 'l':
                    limpio = False
            de[fin] = len(trozos)
            fin += 1
        trozos.append((i, fin, mayusculas, limpio))
        i = fin
    salto = [0] * (len(trozos) + 1)   # diferencias: une[k] = el trozo k es unible al k + 1
    if une:
        guiones, sigue, ant = [0] * (n + 1), [n] * (n + 1), [-1] * n   # recuento de guiones; primer índice sin guion, comilla ni blanco; último sin guion ni comilla
        for i, c in enumerate(texto):
            guion = c in _GUIONES_DE_FIN_DE_LINEA
            guiones[i + 1] = guiones[i] + guion
            ant[i] = (ant[i - 1] if i else -1) if guion or c in _COMILLAS_DE_UNION else i
        for i in range(n - 1, -1, -1):
            c = texto[i]
            sigue[i] = sigue[i + 1] if c in _GUIONES_DE_FIN_DE_LINEA or c in _COMILLAS_DE_UNION or c.isspace() else i
        for k, (_, fin, _, _) in enumerate(trozos):
            antes, despues = ant[fin - 1], sigue[fin]   # `sigue[n]` es `n`: tras el último trozo no hay a qué unir
            if antes >= 0 and despues < n and texto[antes].isalnum() and texto[despues].isalnum() and guiones[despues] > guiones[antes + 1]:
                salto[k] += 1
                salto[de[despues]] -= 1
    k, unible = 0, 0
    while k < len(trozos):
        if not trozos[k][3]:
            unible += salto[k]
            k += 1
            continue
        j, suma = k, trozos[k][2]
        unible += salto[j]
        while unible > 0 and j + 1 < len(trozos) and trozos[j + 1][3]:
            j += 1
            suma += trozos[j][2]
            unible += salto[j]
        if suma >= 2 and not con_mayuscula:
            marcas[trozos[k][0]:trozos[j][1]] = b'\x01' * (trozos[j][1] - trozos[k][0])
        elif suma >= 2:
            for m in range(k, j + 1):
                if trozos[m][2]:
                    marcas[trozos[m][0]:trozos[m][1]] = b'\x01' * (trozos[m][1] - trozos[m][0])
        k = j + 1
    return marcas


def _sustituir_l(texto, en_palabra) -> str:
    """`texto` con cada U+006C sustituida por U+0049 si está marcada en `en_palabra` o en una tirada de letras en mayúsculas."""
    en_tirada = _tiradas_de_mayusculas(texto)
    return ''.join('I' if c == 'l' and (en_palabra[i] or en_tirada[i]) else c for i, c in enumerate(texto))


def _variante_solo_tirada(texto) -> str:
    """Spec 072 (AC25, V0): solo la «l» de una tirada de letras (`str.isalpha`: sin cifras ni signos) en mayúsculas pasa a «I», sin mirar el trozo ni la racha: una «l» real de una palabra con cifras
    («10l4») pegada por un signo a una tirada en mayúsculas («GARClA») se queda como está."""
    return _sustituir_l(texto, bytearray(len(texto))) if 'l' in texto else texto


def _variante_sin_unir(texto) -> str:
    """Spec 072 (AC23, V1): la variante de `08a30cb`: la «l» de una palabra (un trozo sin unir) o de una tirada de letras en mayúsculas pasa a «I»."""
    return _sustituir_l(texto, _palabras_en_mayusculas(texto, une=False)) if 'l' in texto else texto


def _variante_con_mayuscula(texto) -> str:
    """Spec 072 (AC23, V3): como `variante` (V2), pero en una racha solo cambian los trozos con al menos una mayúscula: una «l» real de una palabra con cifras («10l4») se queda."""
    return _sustituir_l(texto, _palabras_en_mayusculas(texto, con_mayuscula=True)) if 'l' in texto else texto


def variante(texto) -> str:
    """Spec 072 (AC1, AC17, AC18, AC21, V2): `texto` con cada U+006C sustituida por U+0049 cuando su racha de trozos (los de caracteres que no son blancos, unidos a los vecinos que parte
    un guion de fin de línea: «Sl- LVIA», «Sl'- LVIA», «Sl- LVIA'- garcia») o su tirada de letras está en mayúsculas: al menos 2 letras mayúsculas y ninguna otra minúscula que la «l»
    («SlLVIA», «Sl.LVIA»; ver `_palabras_en_mayusculas`). Función pura de la cadena cruda, de su misma longitud; no usa el lector ni sus tablas, su normalización ni su troceo."""
    return _sustituir_l(texto, _palabras_en_mayusculas(texto)) if 'l' in texto else texto


def _textos_a_leer(texto) -> tuple:
    """Spec 072 (AC2, AC3, AC23, AC25): los textos que juzga el lector para decidir una colisión, sin repetidos y en este orden: el texto; `V0` (`_variante_solo_tirada`, solo las tiradas de
    letras); `V1` (`_variante_sin_unir`, la de `08a30cb`); `V3` (`_variante_con_mayuscula`) y `V2` (`variante`, las rachas completas): a lo sumo cinco. Marcar más «l» no conserva las colisiones
    (en la misma racha una «l» puede tener que ser «I» y otra seguir siendo «l»): cada lectura es una alternativa, y nada de lo que choca en `74fa789` (el texto), en `08a30cb` (`V1`) ni en
    `bbdc6cf` (el texto, `V1`, `V3` y `V2`) se pierde. Es el ÚNICO punto donde se aplica la regla de la «l» por «I»: lo usan `forma_visual_de_nombre` y `homonimos_snapshot`, que cuenta como
    colisión la de cualquiera (el reconocimiento se llama una vez por texto distinto)."""
    if not isinstance(texto, str):
        return (texto,)
    lecturas = [texto]
    for otra in (_variante_solo_tirada(texto), _variante_sin_unir(texto), _variante_con_mayuscula(texto), variante(texto)):
        if otra not in lecturas:
            lecturas.append(otra)
    return tuple(lecturas)


def homonimos_snapshot(nodos, ids, aristas=()) -> list[str]:
    """AC338 (enmienda 91): un problema por cada par (civil, seudónimo) del export cuyo `name`, `canonical_name`, `name_original`,
    denominación (`_PROPS_CARACTERES`) o algún alias del civil es una variante AC44 del nombre de pila del seudónimo (`es_variante_nombre`) o se lee como él
    (`forma_visual_de_nombre`: homógrafos, dígitos y signos por letras o por puntuación, piezas unidas por un guion): dos fichas públicas
    con el mismo nombre, una de ellas la de un investigado bajo seudónimo. Seudónimo: un nodo con `entity_id` en `ids` o
    con `is_pseudonym` verdadero; civil: cualquier otro nodo (de cualquier etiqueta de entidad, no solo Person; `is_pseudonym` ausente o nulo es
    civil), también el que no tiene `entity_id` o lo tiene vacío (AC354), y cada arista de `aristas` ((ref, props), con los tipos ya validados):
    sus denominaciones y alias son textos públicos como los de un nodo. De un nodo sin `entity_id` no hay slug que leer. Solo quedan fuera los nodos exclusivamente documentales (etiquetas
    no vacías y todas Chunk o Document): un Person que además lleva Document o Chunk, o un nodo sin etiquetas, sigue
    siendo una ficha (falla cerrado). Y un problema aparte por
    cada par cuyo slug (sus palabras unidas por los guiones, como las piezas de un nombre: `_lee_como`) se lee como el nombre de pila del
    seudónimo, salvo el id eximido (`SLUG_CIVIL_EXENTO`). Spec 072: cada valor y cada slug se lee también por sus lecturas (una «l» minúscula por «I»
    en una palabra en mayúsculas, `_textos_a_leer`); la huella y la clave del problema son siempre las del valor.
    Huellas, nunca el texto: la del primer valor que choca (`name`, `canonical_name`, `name_original`, denominación, alias) y la del
    entity_id del seudónimo. AC361: una arista entre dos seudónimos cuyo `name` o alias iguala el nombre de su propio extremo da `homonimo` A
    PROPÓSITO (falla cerrado: toda arista es un texto público civil; 0 casos en el export real, cuyas 63 denominaciones de arista son de
    BDNS y PLACSP entre sociedades)."""
    seudonimos, civiles = [], []   # (qué, ref, entity_id o None, props)
    for n in nodos:
        props = n.get('props') or {}
        if solo_documental(n.get('labels')):
            continue
        eid = props.get('entity_id')
        eid = eid if isinstance(eid, str) and eid else None
        if eid is not None and (eid in ids or props.get('is_pseudonym')):
            seudonimos.append(('nodo', n['ref'], eid, props))
        elif eid is not None or not props.get('is_pseudonym'):   # un `is_pseudonym` sin id ya es un problema aparte (fuera del roster)
            civiles.append(('nodo', n['ref'], eid, props))
    civiles += [('arista', ref, None, props) for ref, props in aristas]
    por_palabra, por_esqueleto = {}, {}   # palabra del nombre de pila → seudónimos que la llevan (toda variante contiene alguna); y por su esqueleto
    for sid in sorted({sid for _, _, sid, _ in seudonimos}):
        for palabra in set(_nombre(sid)):
            por_palabra.setdefault(palabra, []).append(sid)
            por_esqueleto.setdefault(_esqueleto(palabra), set()).add(sid)

    largo_indice = max(map(len, por_esqueleto), default=0)   # una palabra del texto de más largo no puede ser forma de ninguna del índice

    todos = sorted({sid for _, _, sid, _ in seudonimos})

    def candidatos(valor, lecturas):
        """Los seudónimos que `valor` puede leerse como: por una palabra suya, o por el esqueleto (`_esqueleto`, que conserva el largo: el de
        varias piezas es la suma de los de cada una) de una pieza o de varias unidas por un punto de partición (`lecturas`, de
        `_fichas_visuales`), cada recorrido cortado al pasar del largo de la mayor palabra del índice: una condición necesaria de
        `forma_visual_de_nombre`, lineal en las piezas. Un valor con signos que se leen como letra o como puntuación (`con_marcas`) no
        tiene un esqueleto: todos (en el export real, 3 valores de 3 568 nodos)."""
        if any(fichas.con_marcas for fichas in lecturas):
            return todos
        sids = {s for t in set(_tokens(valor)) for s in por_palabra.get(t, ())}
        for fichas in lecturas:
            esqueletos = [_esqueleto(pieza) for pieza in fichas.piezas]
            for i in range(len(esqueletos)):
                palabra = ''
                for j in range(i, len(esqueletos)):
                    palabra += esqueletos[j]
                    if len(palabra) > largo_indice:
                        break
                    sids |= por_esqueleto.get(palabra, set())
                    if j >= len(fichas.unibles) or not fichas.unibles[j]:
                        break
        return sorted(sids)
    problemas = []
    for que, ref, eid, props in civiles:
        choques = {}   # seudónimo → (clave, valor) del primer valor que choca
        agotados = {}   # huella del valor → clave del valor que no se pudo comparar (`_Agotado`: un problema aparte, no una colisión)
        for clave in (*_PROPS_CARACTERES, 'aliases'):
            for valor in props.get(clave) if isinstance(props.get(clave), list) else [props.get(clave)]:
                if isinstance(valor, str) and valor.strip():
                    for texto in _textos_a_leer(valor):   # spec 072: el valor y sus lecturas; la huella y la clave son siempre las del valor
                        lecturas = _fichas_visuales(texto)   # una vez por texto, no por candidato
                        for sid in candidatos(texto, lecturas):
                            if sid in choques:
                                continue
                            try:
                                if es_variante_nombre(sid, texto) or _lee_como_nombre(sid, lecturas):
                                    choques[sid] = (clave, valor)
                            except _Agotado:
                                agotados.setdefault(sha16(valor), clave)
        problemas += [f'{MOTIVOS["homonimo"]} ({que} {ref}, `{clave}`, huella {sha16(valor)}, '
                      f'seudónimo huella {sha16(sid)})' for sid, (clave, valor) in sorted(choques.items())]
        if eid is not None and sha16(eid) not in SLUG_CIVIL_EXENTO:   # AC338 (6ª pasada): el slug se lee como el name (sus guiones, puntos de partición; `_lee_como`), no solo palabra a palabra
            ya = set()   # un problema por seudónimo aunque el slug y alguna de sus lecturas lo lean
            for texto in _textos_a_leer(eid.split(':', 1)[-1]):   # spec 072: el slug y sus lecturas
                lecturas = _fichas_visuales(texto)
                for sid in candidatos(texto, lecturas):
                    try:
                        if sid not in ya and _lee_como_nombre(sid, lecturas):
                            ya.add(sid)
                            problemas.append(f'{MOTIVOS["homonimo-slug"]} (nodo {ref}, huella {sha16(eid)}, '
                                             f'seudónimo huella {sha16(sid)})')
                    except _Agotado:
                        agotados.setdefault(sha16(eid), 'entity_id')
        problemas += [f'{MOTIVOS["agotamiento"]} ({que} {ref}, `{clave}`, huella {huella})' for huella, clave in agotados.items()]
    return problemas


def verificar_snapshot(snapdir, ids, data, cuentas=None, personas=(), publicos=None, extremos=(),
                       repo=None) -> list[str]:
    """Problemas de un export del grafo (`nodes.jsonl`/`relationships.jsonl`) frente a la política:
    fixtures supervivientes, nodos marcados `is_pseudonym` fuera del roster (la política no los alcanza),
    aristas seudónimo↔sociedad/organismo fuera de la lista (por tripleta, capa y documento), entradas de la
    lista sin arista en el grafo (o, si acotan `docs`, documentos sin arista) y alias no permitidos. La
    sociedad u organismo se reconoce como en la política del loader, por etiqueta: un nodo de esas etiquetas
    sin entity_id unido a un seudónimo se denuncia (ninguna entrada puede listarlo), y uno unido a un
    seudónimo cuya etiqueta y prefijo de entity_id no dicen lo mismo, también (en un sentido la política lo
    retira aunque el id no lo diga; en el otro, lo deja). Y (AC79) todos los props de cadena de los nodos de
    entidad y de las aristas (`textos_props`) por la partición de sus entidades (`revisar_textos`): los de un
    nodo o una arista civil no nombran a un seudónimo (los de un nodo de persona, sociedad u organismo, tampoco su
    nombre ni sus alias, AC86); los de un seudónimo, ni a un civil (`personas`, las del contexto, más las Person
    civiles del export con sus alias, AC111; en una cita, solo el extremo que coaparece, AC87), ni a otra entidad u
    otro seudónimo, ni un documento fuera del árbol público (`publicos`; None: sin comprobar); y ninguno lleva
    caracteres invisibles, letras latinas confusables o alfabetos mezclados (también el `name`, el `canonical_name` y el
    `name_original` de toda ficha, sea cual sea su etiqueta de entidad, y las denominaciones oficiales de BDNS/PLACSP:
    `_PROPS_CARACTERES`). Con `publicos`, la procedencia
    (`source_doc`/`doc`) de un nodo o una arista de seudónimo ha de ser un nodo Document del export (AC78(c)). El
    `name`/`canonical_name` de un nodo del roster es una variante de su nombre de pila (AC83), y ningún id con forma de
    seudónimo, el de un nodo o uno nombrado en un texto, queda fuera del roster (AC89). AC120: la prosa de un nodo de
    seudónimo y de una arista que lo toca (`PROPS_SEUDONIMO`) no lleva un dato civil (`problemas_dato_civil`, con las
    sociedades del export y del contexto de `repo`, con su núcleo, AC149, y su registro de documentos; por defecto, los
    de este repo); AC150, ningún texto de un seudónimo lo identifica con un civil, ni (AC160) uno de un nodo o una
    arista civil por su nombre de pila suelto y una fórmula adyacente. AC148: cada arista mixta (un
    seudónimo y una persona, sociedad u organismo civil) es una arista conservada, y su cita ha de nombrar a su extremo
    civil con prueba (`prueba_conservada`, la del gate y el control posterior): sin cita, sin nombrarlo o sin sus
    nombres, problema. AC338, AC354: ningún nodo civil (de cualquier etiqueta de entidad, con o sin
    `entity_id`) ni ninguna arista lleva en `name`, `canonical_name`, `name_original`, una denominación o un alias una variante del
    nombre de pila de un seudónimo ni algo que se lee como él (`forma_visual_de_nombre`), ni un `entity_id` cuyo slug lo es
    (`homonimos_snapshot`); y todo `name`, `canonical_name`, `name_original` y denominación es una cadena y `aliases` una lista de
    cadenas (`tipos_de_nombre`: un valor de otro tipo es un problema y el nodo se lee sin él). AC346-AC351 (lista blanca): el
    nombre, el nombre canónico, el original, cada alias (de nodos y de aristas), las denominaciones y el slug del `entity_id` solo llevan
    los caracteres de su literal (`caracteres_no_permitidos`: ASCII imprimible y 30 no ASCII; el slug, `a-z0-9-`; en crudo y tras NFKC), y
    el `entity_id` entero es `Prefijo:slug` con un prefijo del literal y un slug con alguna letra o cifra (`razon_entity_id`; también el de un nodo
    solo documental; uno que no es una cadena se lee como ausente). AC352: un sustituto UTF-16 suelto en cualquier cadena de un nodo o una arista
    (props y sus claves, etiquetas, tipo, referencias) es un problema (`sustitutos_sueltos`), nunca una excepción: las referencias que lo llevan
    se escriben `~huella` en los mensajes. Un campo estructural de un registro con otra forma que la del export (un registro que no es un objeto;
    `ref`, `start`, `end`, `type` que no son una cadena o faltan; `labels` que no es una lista de cadenas; `props` que no es un objeto; el
    `source_kind` o el `source_doc` de una arista que no es una cadena; una clave de primer nivel que no es del export; un anidamiento de más de
    `_TOPE_PROFUNDIDAD` niveles) es un problema `forma-registro`, nunca una excepción (`normalizar_registro`: también en los nodos solo documentales), y
    el prefijo del `entity_id` ha de ser la única etiqueta de tipo de entidad del nodo, salvo la pareja Company+Organization (AC360). Una línea vacía o
    un JSON roto de `nodes.jsonl` o `relationships.jsonl` es un problema `linea-jsonl` con su número de línea (`_leer_jsonl`) y la puerta sigue (AC359).
    Ningún mensaje lleva el valor de una referencia, una clave o un campo inválidos: la referencia segura (`~huella`) o una huella (AC358), y ninguno ocupa
    más de una línea (AC365 y AC369: una referencia o un tipo con controles, saltos de línea, caracteres de formato o bidireccionales va por huella). La huella solo
    se escribe en los mensajes: la identidad de un nodo (índices, extremos de aristas) es su referencia ORIGINAL, y la de una arista su `start`, `end` y `type`
    originales (AC368); dos nodos con la misma `ref` son un problema. Las etiquetas de un nodo son las 12 del export (`_ETIQUETAS_NODO`, AC364).
    Con `repo`, además AC118: las filas de su roster que no pueden ser de un seudónimo (`problemas_roster`, con las
    personas civiles del export). `cuentas` (un dict), si se pasa, recibe `org_sin_entity_id`: los nodos
    Company/Organization del export sin entity_id (informativo). Sin extremos en los mensajes."""
    snapdir = Path(snapdir)
    nodos, problemas = _leer_jsonl(snapdir / 'nodes.jsonl', 'nodes.jsonl')   # AC359: una línea vacía o rota es un problema con su número; la puerta sigue
    identidades = []   # AC368: la identidad de un nodo es su referencia ORIGINAL; la `~huella` de `nodos[i]['ref']` solo se escribe en los mensajes
    for i, original in enumerate(nodos):   # un campo estructural de otra forma, o un sustituto UTF-16 suelto (AC352), es un problema; el nodo se lee seguro
        nodos[i], razones = normalizar_registro(original, 'nodo')
        problemas += _problemas_de_forma('nodo', original, razones, nodos[i])
        identidades.append(original['ref'] if isinstance(original, dict) and isinstance(original.get('ref'), str) else ('sin-ref', i))
    problemas += [f'{MOTIVOS["forma-registro"]} (nodo {_ref_seguro(ref)}, `ref`, referencia repetida en {veces} nodos)'
                  for ref, veces in sorted(Counter(i for i in identidades if isinstance(i, str)).items()) if veces > 1]
    for n in nodos:   # AC338: un nombre que no es una cadena, o unos alias que no son una lista de cadenas, es un problema (falla cerrado) y el nodo se lee sin ellos
        if isinstance(n.get('props'), dict) and (malos := tipos_de_nombre(n['props'])):   # AC359: también un nodo solo documental (los índices lo leen)
            problemas += [f'{MOTIVOS["tipo-nombre"]} (nodo {n["ref"]}, `{clave}`, tipo {tipo})' for clave, tipo in malos]
            n['props'] = con_nombres_validos(n['props'])
        # AC351: el entity_id entero es una clave pública, también el de un nodo documental (los 5 282 Document y Chunk del export no lo llevan): un
        # valor que no es una cadena (un `entity_id` ausente o nulo no es un valor) o que no tiene la forma `Prefijo:slug` es un problema; el que no
        # es una cadena se lee como ausente (una lista no es hashable: rompería los índices)
        eid = n['props'].get('entity_id') if isinstance(n.get('props'), dict) else None
        if eid is not None:
            if razon := razon_entity_id(eid, n.get('labels') or []):
                problemas.append(f'{MOTIVOS["entity-id"]} (nodo {n["ref"]}, `entity_id`, '
                                 f'huella {sha16(eid if isinstance(eid, str) else json.dumps(eid, sort_keys=True, default=str))}, {razon})')
            if not isinstance(eid, str):
                n['props'] = {k: v for k, v in n['props'].items() if k != 'entity_id'}
    # AC111/AC118: del export, el nombre, el nombre canónico y los alias de cada Person (las del roster también).
    del_export = [(p.get('entity_id'), v) for n in nodos for p in [n.get('props') or {}]
                  if 'Person' in (n.get('labels') or ()) for k in ('name', 'canonical_name', 'aliases')
                  for v in (p.get(k) if isinstance(p.get(k), list) else [p.get(k)]) if isinstance(v, str)]
    # AC134: los alias inválidos que esta comprobación denuncia: los de un nodo del roster fuera de la lista (abajo, el
    # último nodo de cada id) y, con `repo`, los que le da un fichero de contexto; solo la mención de esos se calla.
    ultimos = {p['entity_id']: list(p.get('aliases') or []) for n in nodos for p in [n.get('props') or {}]
               if p.get('entity_id') in ids}
    del_indice = indice_alias(data)
    invalidos = alias_invalidos_contexto(repo, ids, data) if repo is not None else []
    civiles = indice_civiles([*personas, *del_export], ids, data,
                             [*((s, a) for s, lista in ultimos.items() for a in lista
                                if isinstance(a, str) and a not in del_indice.get(s, frozenset())),
                              *((s, a) for _, _, s, a in invalidos)])
    export = ExportCitas(nodos)   # AC90(a)/AC125: sus documentos y el texto de sus chunks, la prueba única
    documentos = export.documentos
    # AC90: los nombres de cada persona, sociedad u organismo del export.
    nombres_de = {}
    for n in nodos:
        etiqueta, p = frozenset(n.get('labels') or ()), n.get('props') or {}
        if etiqueta & _ETIQUETAS_NOMBRADAS and p.get('entity_id'):
            nombres_de.setdefault(p['entity_id'], set()).update(
                v for k in ('name', 'canonical_name', 'aliases')
                for v in (p.get(k) if isinstance(p.get(k), list) else [p.get(k)]) if isinstance(v, str) and v.strip())
    contexto = Path(repo or REPO)   # AC120: las sociedades reales (export y contexto) y el registro de documentos
    censo = censo_documentos(contexto, publicos or ())
    de_sociedades = {e: set(v) for e, v in nombres_contexto(contexto).items()}
    for eid, valores in nombres_de.items():
        de_sociedades.setdefault(eid, set()).update(valores)
    sociedades = indice_sociedades(de_sociedades)

    eids, etiquetas, aliases, sin_id = {}, {}, {}, set()

    def revisar(que, ref, entidades, props, nombres=False):
        entidades = set(entidades) - {None}
        textos = textos_props(props, nombres)
        caracteres = [v for k in _PROPS_CARACTERES for v in [props.get(k)] if isinstance(v, str)]
        # AC346: los nombres, los alias y las denominaciones (nodos y aristas), y el slug del `entity_id`, solo con los caracteres de su lista blanca
        a_ver = [(k, v, _ALFABETO_NOMBRES) for k in _PROPS_CARACTERES for v in [props.get(k)] if isinstance(v, str)]
        a_ver += [('aliases', a, _ALFABETO_NOMBRES) for a in props.get('aliases') or () if isinstance(a, str)]   # AC350: también los de una arista
        if que == 'nodo':
            if isinstance(props.get('entity_id'), str):
                a_ver.append(('entity_id', props['entity_id'].split(':', 1)[-1], _ALFABETO_SLUGS))
        vistos = set()
        for clave, v, alfabeto in a_ver:
            if (v, alfabeto is _ALFABETO_SLUGS) in vistos:   # una vez por valor y por alfabeto
                continue
            vistos.add((v, alfabeto is _ALFABETO_SLUGS))
            if fuera := caracteres_no_permitidos(v, alfabeto):
                problemas.append(f'{MOTIVOS["caracter-no-permitido"]} ({que} {ref}, `{clave}`, huella {sha16(v)}, '
                                 f'{", ".join(fuera[:8])}{f" y {len(fuera) - 8} más" if len(fuera) > 8 else ""})')
        doc = _texto(props.get('source_doc')) or None
        judicial = que == 'arista' and props.get('source_kind') == 'judicial'

        def admitida(texto, d=doc):   # AC90/AC125: (a) la coaparición que publicó el tribunal, o (b) `citas:`
            return prueba_cita(texto, d, entidades & ids, data.get('citas'), export, judicial)
        for motivo, huella in revisar_textos(entidades, textos, ids, civiles, publicos, nombres_de, admitida,
                                             sociedades, caracteres):
            texto = {'civil': f'{"prosa de un nodo civil" if que == "nodo" else "texto de una arista civil"} '
                              f'{MOTIVOS["civil"]}',
                     'seudonimo': f'texto de {"un nodo" if que == "nodo" else "una arista"} de seudónimo '
                                  f'{MOTIVOS["seudonimo"]}'}.get(motivo, MOTIVOS[motivo])
            problemas.append(f'{texto} ({que} {ref}, huella {huella})')
        for clave, tipo in PROPS_SEUDONIMO[que].items() if entidades & ids else ():   # AC120/AC123
            valor = props.get(clave)
            if isinstance(valor, str) and valor.strip():
                problemas.extend(f'{MOTIVOS[motivo]} ({que} {ref}, huella {sha16(valor)})' for motivo in dict.fromkeys(
                    'sin-documento' if m == 'sin-documento' else 'dato-civil'
                    for m in problemas_dato_civil(
                        valor, tipo, censo, sociedades, frozenset(entidades), doc if que == 'arista' else None,
                        lambda t, d: prueba_cita(t, d, entidades & ids, data.get('citas'), export))))
        # AC89: un id con forma de seudónimo fuera del roster, el de un nodo o uno que se nombra en un texto (el de un
        # extremo de una arista ya sale en su nodo).
        problemas.extend(f'{MOTIVOS["roster-ausente"]} ({que} {ref}, huella {sha16(e)})'
                         for e in sorted(fuera_del_roster(entidades if que == 'nodo' else (), textos, ids)))
        if publicos is None or not entidades & ids:
            return
        for clave in _PROPS_PROCEDENCIA:
            doc = props.get(clave)
            if isinstance(doc, str) and doc.strip() and doc.strip() not in documentos:
                problemas.append(f'procedencia (`{clave}`) de {"un nodo" if que == "nodo" else "una arista"} de '
                                 f'seudónimo sin nodo Document en el export ({que} {ref}, huella {sha16(doc.strip())})')

    for record, nid in zip(nodos, identidades):   # `nid` (la referencia original) es la clave de los índices; `record['ref']`, solo para los mensajes
        props = record.get('props') or {}
        eid = props.get('entity_id')
        etiquetas[nid] = frozenset(record.get('labels') or ())
        if props.get('is_pseudonym') and eid not in ids:
            problemas.append(f'nodo marcado `is_pseudonym` fuera del roster (nodo {record["ref"]})')
        if eid in ids:   # AC83: su nombre es una variante del de pila (la forma de muestra del roster), nunca otro
            for clave in ('name', 'canonical_name'):
                valor = props.get(clave)
                if isinstance(valor, str) and valor.strip() and not es_variante_nombre(eid, valor):
                    problemas.append(f'{MOTIVOS["nombre"]} (nodo {record["ref"]}, `{clave}`, huella {sha16(valor)})')
        if not solo_documental(etiquetas[nid]):   # un Person+Document también se revisa
            revisar('nodo', record['ref'], {eid}, props,
                    nombres=eid not in ids and bool(etiquetas[nid] & _ETIQUETAS_NOMBRADAS))
        if not eid:
            if ETIQUETAS_SENSIBLES & etiquetas[nid]:
                sin_id.add(nid)
                if props.get('name') == FIXTURE_SIN_ID:
                    problemas.append('el nodo fixture sin entity_id sigue en el grafo')
            continue
        eids[nid] = eid
        if eid == FIXTURE_NODO:
            problemas.append('el nodo fixture sigue en el grafo')
        if FIXTURE_ALIAS in (props.get('aliases') or []):
            problemas.append(f'alias fixture superviviente (nodo {record["ref"]})')
        if eid in ids:
            aliases[eid] = (record['ref'], list(props.get('aliases') or []))
    aristas_leidas, claves_de_arista = [], []   # leídas una vez, normalizadas (campos estructurales de otra forma y sustitutos sueltos: problemas, nunca excepciones)
    originales, errores = _leer_jsonl(snapdir / 'relationships.jsonl', 'relationships.jsonl')
    problemas += errores
    for original in originales:
        registro, razones = normalizar_registro(original, 'arista')
        problemas += _problemas_de_forma('arista', original, razones, registro)
        aristas_leidas.append(registro)
        claves_de_arista.append(tuple(original.get(c) if isinstance(original, dict) and isinstance(original.get(c), str) else None
                                      for c in ('start', 'end', 'type')))   # AC368: los originales (None si no son una cadena), nunca la `~huella` ni ''
    # AC354: los nombres de las aristas, para la homonimia (el bucle de abajo las repasa con sus problemas de tipo)
    aristas_nombres = [(r['ref'], con_nombres_validos(r['props'])) for r in aristas_leidas if isinstance(r.get('props'), dict)]
    problemas += homonimos_snapshot(nodos, ids, aristas_nombres)   # AC338, AC354
    indice, presentes, denunciados = indice_aristas(data), Counter(), set()
    for record, (inicio, fin, tipo_arista) in zip(aristas_leidas, claves_de_arista):
        props = record.get('props') or {}
        if isinstance(props, dict) and (malos := tipos_de_nombre(props)):   # AC338: las denominaciones de una arista, como las de un nodo
            problemas += [f'{MOTIVOS["tipo-nombre"]} (arista {record["ref"]}, `{clave}`, tipo {tipo})' for clave, tipo in malos]
            props = con_nombres_validos(props)
        if props.get('source_doc') == FIXTURE_DOC:
            problemas.append(f'arista fixture superviviente ({record["ref"]})')
            continue
        a, b = eids.get(inicio), eids.get(fin)
        revisar('arista', record['ref'], {a, b}, props)
        sensible = None
        for seudo, ref in ((a, fin), (b, inicio)):
            if seudo not in ids:
                continue
            otro, por_etiqueta = eids.get(ref), bool(ETIQUETAS_SENSIBLES & etiquetas.get(ref, frozenset()))
            if otro is None:
                if ref in sin_id and ref not in denunciados:
                    denunciados.add(ref)
                    problemas.append(f'nodo Company/Organization sin entity_id unido a un seudónimo (nodo {_ref_seguro(ref)})')
                continue
            if por_etiqueta != otro.startswith(SENSIBLES) and ref not in denunciados:
                denunciados.add(ref)
                problemas.append(f'nodo con etiqueta y entity_id discordantes (nodo {_ref_seguro(ref)})')
            if por_etiqueta and sensible is None:
                sensible = (seudo, otro)
        if {a, b} & ids and _extremos_civiles({a, b}, ids):   # AC148, como el gate y el control posterior
            _, motivos = prueba_conservada(
                props.get('source_quote'), _texto(props.get('source_doc')) or None,
                props.get('source_kind') == 'judicial', {a, b}, ids, civiles, nombres_de, data.get('citas'), export,
                {e: nombres_de.get(e, set()) for e in _extremos_civiles({a, b}, ids)}, publicos, sociedades)
            problemas += [f'{MOTIVOS[m]} (arista {record["ref"]})' for m in motivos
                          if m in ('sin-cita', 'no-nombra', 'sin-nombres')]
        if sensible is None:
            continue
        sid, otro = sensible
        key, doc = (sid, otro, tipo_arista), props.get('source_doc') or ''
        if arista_permitida(indice, *key, props.get('source_kind') or SIN_CAPA, doc):
            presentes[(key, doc if indice[key][1] is not None else None)] += 1
        else:
            problemas.append(f'arista seudónimo↔sociedad/organismo fuera de la lista ({record["ref"]})')
    # `volcar_permitidos` ordena por (seudónimo, otro, tipo): el índice es el de la línea en el fichero.
    for i, key in enumerate(sorted(indice)):
        docs = indice[key][1]
        if docs is None and not presentes[(key, None)]:
            problemas.append(f'aristas[{i}] de la lista sin arista en el grafo')
        faltan = [d for d in sorted(docs or ()) if not presentes[(key, d)]]
        if faltan:
            problemas.append(f'aristas[{i}]: {len(faltan)} documento(s) de `docs` sin arista en el grafo')
    permitidos = indice_alias(data)
    for sid, (ref, lista) in sorted(aliases.items()):
        sobran = [a for a in lista if a not in permitidos.get(sid, frozenset())]
        if sobran:
            problemas.append(f'{len(sobran)} alias no permitido(s) en un seudónimo (nodo {ref})')
    if repo is not None:   # AC118, con las personas civiles del contexto y del export; AC134, los alias del contexto
        problemas += [f'{motivo} (roster, línea {linea})'
                      for linea, motivo in problemas_roster(repo, ids, [*personas, *del_export])]
        problemas += [f'{MOTIVOS["alias-capa"]} ({fichero}, línea {linea}, huella {sha16(alias)})'
                      for fichero, linea, _, alias in invalidos]
        problemas += [f'{MOTIVOS["alias-identificador"]} ({fichero}, línea {linea}, huella {sha16(alias)})'
                      for fichero, linea, alias in alias_identificadores_contexto(repo, ids)]   # AC167
    if cuentas is not None:
        cuentas['org_sin_entity_id'] = len(sin_id)
        # AC91(iii)/(iv): el nombre con que el árbol escribe un extremo civil, frente al nodo que resuelve. Distinto de
        # su nombre, de sus alias y de los que le da el contexto, aviso (el loader resuelve por el id); sin nodo en el
        # export (regla gastada), recuento.
        etiquetas_de = {eid: etiquetas[ref] for ref, eid in eids.items()}
        cuentas['avisos'], cuentas['extremos_sin_nodo'] = [], 0
        for fichero, linea, texto, eid, conocido in extremos:
            if eid not in etiquetas_de:
                cuentas['extremos_sin_nodo'] += 1
            elif (etiquetas_de[eid] & _ETIQUETAS_NOMBRADAS and not conocido and tuple(_palabras(texto))
                  not in {tuple(_palabras(n)) for n in nombres_de.get(eid, ())}):
                cuentas['avisos'].append(f'nombre de un extremo civil que no es su nombre ni un alias en el export '
                                         f'({fichero}:{linea}, huella {sha16(texto)})')
    return problemas
