"""Aplica las decisiones finales de la 063: overlays durables, registro de rectificaciones y censo.

Generaliza `specs/061-auditoria-dual-fable-astra/tools-T0e/{apply_t0e,census_t0e}.py` para el bucle
escalonado: la entrada ya no es la carpeta de una ronda humana sino `ROOT/esc/decisions/*.json`
(CONTRACTS.md §«Decisiones finales»), cada fichero = el JSON del esquema `qa_esc_schema` más `_meta`.

Escribe (NUNCA toca Neo4j ni producción; los overlays los aplica un humano por Path A):
  · `pipeline/data/context/summaries_qa061.yaml`  — una fila por entidad (`remove: true` en retiradas)
  · `pipeline/data/context/roles_qa061.yaml`      — dentro de `entities:`
  · `pipeline/data/relations/relations.yaml`      — secciones `deny:`, `fix:` y `alias_deny:`
  · `pipeline/data/relations/seudonimos_permitidos.yaml` — excepciones de los seudónimos judiciales (spec 070:
    «conservar» las añade, «retirar» las quita o las acota por documento; nunca un deny/alias_deny público
    sobre un seudónimo)
  · `pipeline/data/rectificaciones/registro.yaml` — una fila por retirada/reescritura/estado/arista
Solo exporta (los aplica el dueño a mano, por diseño):
  · bloques `estado` para `accusations.yaml`      → `esc/report/estado-<fecha>.yaml`
  · decisiones editoriales                        → `esc/report/tanda-editorial-<fecha>.md`
Trazas privadas: `esc/report/seudonimos-<fecha>.md` (spec 070) y `esc/report/evidencias-<fecha>.md` (la `evidence`
libre de cada fila del registro, cuya `fuente` pública es estructural).

Uso: `python -m src.qa_esc_apply --root ROOT --repo REPO [--resolutions …] check|dry-run|apply|record`
     `… --snapshot EXPORT materializacion` y `… --snapshot EXPORT privatizar --patch DIR` (spec 071 AC117: el parche
     de datos que hace opacas las retiradas que oculta el registro y las declaraciones privadas, fuera del repo).
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import contextlib
import datetime
import difflib
import fcntl
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile

import yaml

from . import relations_loader
from .alcance import (LOTE, MOTIVO_AJENA, POSTURAS, QUOTE_CHARS, Opacas, ReglaOpacaInvalida, YamlInvalido,
                      alcanza_loader, cargar_yaml, carga, declaraciones_de, es_opaca, filas_declarables, huella,
                      identidad_propia, nombre_de_pack, nombre_de_unidad, precedencia, props_del_set, regla_de, solapan,
                      validar_cargas, vigentes)
from .alcance import NUMERIC_PROPS, cast_value as _cast_value, norm_prose as _norm_prose, trozo as _quote_contains
from .qa_esc_schema import SET_PROPS, VAGUE, ref_unidad, validate_decision
from .qa_integral import canonical, connect, now
from . import seudonimos

ORIGEN = 'auditoria-063'
SEED_PENDIENTE = 'pendiente-path-a'
# El seed aún no se ha vuelto a cortar cuando se escriben las filas: el Path A lo sella después.
SUM_PATH = 'pipeline/data/context/summaries_qa061.yaml'
ROL_PATH = 'pipeline/data/context/roles_qa061.yaml'
REL_PATH = 'pipeline/data/relations/relations.yaml'
REG_PATH = 'pipeline/data/rectificaciones/registro.yaml'
# Marca de un apply que pudo cortarse entre dos `os.replace` (`_escribir_todo`): mientras exista, `check`, `apply` y
# `record` no arrancan (`_apply_interrumpido`).
MARCADOR_REL = 'pipeline/data/rectificaciones/.apply-en-curso.json'
# Bloqueo exclusivo de `check`, `dry-run`, `apply` y `record` sobre un repo (`_bloqueo`): uno solo a la vez.
BLOQUEO_REL = 'pipeline/data/rectificaciones/apply.lock'
# Etiquetas de unidad que dejan rastro en el registro público de rectificaciones.
REC_LABELS = {'retirada', 'reescrita', 'estado'}

REGISTRO_HEADER = """# Registro público de rectificaciones de El Entramado (spec 063 AC13).
#
# Una fila por cada dato RETIRADO, REESCRITO o cuyo ESTADO PROCESAL se ha corregido en el grafo
# publicado, y por cada solicitud externa resuelta, aunque se desestime. Es la prueba de diligencia
# que puede citar el aviso legal del sitio: qué se corrigió, con qué fuente y cuándo. NO guarda datos
# de quien lo solicita; `origen` solo dice si la corrección nació de la auditoría o de una solicitud.
#
# Lo rellena `python -m src.qa_esc_apply … apply` desde las decisiones de la auditoría; es
# idempotente por (entidad, unidad, decision, dato). Formato de cada fila:
#   - id: R-YYYY-MM-DD-NNNN        # secuencial por fecha
#     fecha: 'YYYY-MM-DD'
#     origen: auditoria-063 | auditoria-067 | solicitud
#     entidad: Person:…
#     unidad: <clave de la decisión: unit_id del censo o clave privada, o "">
#     dato: '<clase de dato y de quién — NUNCA el texto erróneo o retirado; terceros como [tercero]>'
#     fuente: '<doc p.N (documento del árbol público) | medio (fecha) | decisión del dueño (fecha) |
#              decisión de la auditoría 063 (fecha) — sin el texto citado>'
#     decision: rectificado | suprimido | desestimado
#     fecha_resolucion: 'YYYY-MM-DD'
#     seed_version: '<SEED_VERSION vigente o pendiente-path-a>'
#
# Solicitud externa (rectificación o supresión pedida por correo a contacto@entramado.org, ver
# CONTRIBUTING.md): la fila se añade a mano al final de la lista, con los mismos campos:
#   id                R-<fecha>-NNNN con el siguiente número libre de ese día (el apply sigue la serie)
#   fecha             día en que se recibió la solicitud
#   origen            solicitud: es todo lo que consta de quién pide; ni nombre, ni correo, ni si
#                     escribe el afectado o un tercero (eso se queda en el correo, fuera del repo)
#   entidad           la entidad del grafo a la que se refiere el dato
#   unidad            "" (no es una unidad del censo de la auditoría)
#   dato              qué clase de dato se pidió corregir o retirar y de quién, con las fórmulas del
#                     apply («frase del resumen retirada», «rol corregido», «(Person:x) -[TIPO]->
#                     (Person:[tercero]): arista retirada»); NUNCA el texto erróneo o retirado, ni
#                     el de la solicitud
#   fuente            en qué se apoya la resolución (doc p.N | medio (fecha) | decisión del dueño
#                     (fecha)), sin citar texto
#   decision          rectificado | suprimido | desestimado (el dato sigue publicado)
#   fecha_resolucion  día en que se resuelve y se contesta
#   seed_version      el SEED_VERSION que lo aplica (pendiente-path-a mientras no se corte), o el
#                     vigente si la resolución no cambia el grafo


rectificaciones: []
"""


def _today():
    return datetime.date.today().isoformat()


_FECHA = r'\d{4}-\d{2}-\d{2}'
# Las formas de `_meta.resolution` de una decisión y de `units.resolution` del censo (spec 071 AC120; inventario del
# 08-oct: 6 y 26 formas, todas en estas familias): lo único que un diagnóstico cita de una resolución.
FAMILIA_DECISION = re.compile(rf'humano-{_FECHA}|arbitraje-{_FECHA}(?:-prudente)?|(?:dual|opus|triple)-cerrada')
FAMILIA_CENSO = re.compile(
    rf'(?:humano-{_FECHA}[a-z]?|arbitraje-{_FECHA}(?:-prudente)?|(?:dual|opus|triple)-cerrada|dual-(?:fable|jev)-astra'
    rf'|salvaguarda-editorial|lint-L\d+-{_FECHA}|patch-061-superado-063|patch-061-aplicado-{_FECHA})'
    rf'(?:-corregido|-verificado)?')
RESOLUCION_DESCONOCIDA = 'resolución con forma no reconocida'
ESTADOS_UNIDAD = frozenset({'pending', 'needs-human', 'verified'})


def nombre_de_resolucion(valor, familia=FAMILIA_CENSO):
    """Cómo cita un diagnóstico una resolución (spec 071 AC120): tal cual si es de su `familia` cerrada y, si no, el
    marcador neutro, nunca el valor."""
    return valor if isinstance(valor, str) and familia.fullmatch(valor) else RESOLUCION_DESCONOCIDA


def validar_resoluciones(decisions):
    """Cada decisión lleva una `_meta.resolution` de la familia de decisión (spec 071 AC120). Una que no la lleva no
    vale: para con exit 2 y la nombra por su fichero, nunca por el valor."""
    errores = [f'{RESOLUCION_DESCONOCIDA} ({d.get("_file") or "?"})' for d in decisions
               if not FAMILIA_DECISION.fullmatch(str((d.get('_meta') or {}).get('resolution') or ''))]
    for error in errores:
        print(error, file=sys.stderr)
    if errores:
        raise SystemExit(2)


def _dump_block(row):
    return yaml.safe_dump([row], allow_unicode=True, sort_keys=False, width=110, default_flow_style=False)


def _indented(row):
    """El bloque de una fila con la sangría de dos espacios de `relations.yaml`."""
    return ''.join('  ' + line + '\n' for line in _dump_block(row).rstrip('\n').split('\n'))


def _clip(text, limit=200):
    return ' '.join(str(text or '').split())[:limit]


# «5.700» es cinco mil setecientos en la prosa del corpus y 5,7 para `cast_prop`: un importe
# así no se castea a ciegas (un error de 100× en una cifra es exactamente el daño de esta
# auditoría). La decisión se rechaza y el lector lo escribe sin ambigüedad («5700» o «5.700,00»).
_AMBIGUOUS_AMOUNT = re.compile(r'^\d{1,3}(?:\.\d{3})+$')


def _amount_error(value):
    """Por qué no vale el importe de un set_prop, sin el valor (spec 070 AC52, spec 071 AC87: los diagnósticos
    acaban en informes y logs); None si vale."""
    text = str(value or '').strip()
    if _AMBIGUOUS_AMOUNT.match(text):
        return 'ambiguo (separador de miles sin decimales): escríbelo sin separadores o con decimales'
    try:
        _cast_value('amount', text)
    except ValueError:
        return 'no es un número'
    return None


def _problema_set_prop(prop, value):
    """Por qué el loader descartaría este `set_prop`, o `_cast_value` no podría convertirlo, sin citar el valor: las
    reglas de `relations_loader._prepare_set` (AC52). None si vale. Una capa fuera de vocabulario ya la rechaza
    `validate_decision`; aquí, que solo baje desde judicial. `status` no llega por `set_prop` (lo veta la lista cerrada
    del esquema) y `set_status` ya exige un estado canónico."""
    text = str(value if value is not None else '').strip()
    if prop == 'source_page':
        return None if _PAGINA.fullmatch(text) and int(text) >= 1 else 'no es un entero ≥ 1'
    if prop == 'amount':
        return _amount_error(text)
    if prop == 'source_kind':
        rango = relations_loader._KIND_RANK
        return 'la capa solo puede bajar desde judicial' if text in rango and rango[text] <= rango['judicial'] else None
    return None


def _procedencia(edge):
    """`doc` y `quote_contains` de la regla pública de una arista: lo que `materialize` escribe y el gate comprueba."""
    return {'doc': edge.get('source_doc') or None, 'quote_contains': _quote_contains(edge.get('source_quote'))}


def _regla_publica(accion, edge, seudos) -> bool:
    """¿Escribe `materialize` una regla deny/fix pública (con `_procedencia`) para esta acción sobre la arista? No la
    de «keep», ni la postura de una afirmación sin destino (va a claims.yaml), ni la retirada de un par seudónimo↔
    sociedad/organismo (la lista de permitidas la deja fuera)."""
    source, target = edge.get('start_entity'), edge.get('end_entity')
    return (accion != 'keep' and not (edge.get('type') in CLAIM_STANCES and not target)
            and not (accion == 'deny' and seudonimos.es_par_sensible(source, target, seudos)))


# --- reconstrucción del pack desde el ledger ---------------------------------------------------
# El apply no depende de `qa_esc_packs`: para gatear le basta lo que el ledger ya guarda
# (`esc_packs.unit_ids` + `units` + `objects.payload`), que es justamente lo que el manifest
# hasheó. Así una decisión se puede re-gatear aunque el fichero del pack ya no esté en disco.

def _entity_block(conn, entity):
    row = conn.execute("SELECT payload FROM objects WHERE object_kind='node' AND entity=? LIMIT 1",
                       (entity,)).fetchone()
    props = (json.loads(row['payload']).get('props') or {}) if row else {}
    return {'id': entity, 'name': props.get('name') or props.get('canonical_name') or entity.split(':', 1)[-1],
            'is_pseudonym': bool(props.get('is_pseudonym')),
            'pseudonym_note': props.get('pseudonym_note') or ''}


def _nombres_censo(conn, entity, alias=False):
    """[`name`, `canonical_name`] del nodo de `entity` en el censo (AC83: el de un seudónimo ha de ser una variante del
    suyo); con `alias`, también sus alias (AC122: los nombres conocidos del extremo civil de una arista conservada)."""
    row = conn.execute("SELECT payload FROM objects WHERE object_kind='node' AND entity=? LIMIT 1",
                       (entity,)).fetchone()
    props = (json.loads(row['payload']).get('props') or {}) if row else {}
    alias_ = props.get('aliases') if alias and isinstance(props.get('aliases'), list) else []
    return [props.get(k) for k in ('name', 'canonical_name')] + list(alias_)


_SIN_EXPORT = ': sin prueba disponible (pasa --snapshot)'


def _motivos_dato_civil(texto, tipo, doc, documentos, sociedades, propios, seudos, citas, export):
    """AC120/AC123/AC124/AC125: los mensajes del gate y del control posterior para un texto de prosa junto a un
    seudónimo (`seudonimos.problemas_dato_civil`, con la prueba única de sus tramos citados, `seudonimos.prueba_cita`):
    el de dato civil (y, si lo que falta es la prueba de un tramo y no hay export, «sin prueba disponible»), o el de un
    tramo sin documento en que probarlo."""
    motivos = seudonimos.problemas_dato_civil(texto, tipo, documentos, sociedades, propios, doc,
                                              lambda t, d: seudonimos.prueba_cita(t, d, seudos, citas, export))
    return list(dict.fromkeys(
        seudonimos.MOTIVOS['sin-documento'] if m == 'sin-documento'
        else seudonimos.MOTIVOS['dato-civil'] + (_SIN_EXPORT if m == 'tramo' and export is None else '')
        for m in motivos))


def _motivo_conservada(motivo, export):
    """AC119/AC122/AC139/AC148: el mensaje de una arista conservada sin evidencia (`seudonimos.prueba_conservada`): sin
    cita, con una cita que identifica o sospechosa (ninguna prueba la salva) o, sin export, sin la prueba disponible
    (también la que no nombra a su extremo civil: el export puede dar el nombre que la cita lleva)."""
    if motivo in ('sin-cita', 'identifica', 'sospechoso'):
        return seudonimos.MOTIVOS[motivo]
    if motivo == 'sin-nombres':
        return seudonimos.MOTIVOS['sin-nombres'] + _SIN_EXPORT
    return seudonimos.MOTIVOS[motivo] + ('' if export is not None else _SIN_EXPORT)


def indice_sociedades(conn, nombres, export=None):
    """AC153: el índice de sociedades de una ejecución (`seudonimos.indice_sociedades`, con su núcleo, AC149), uno solo
    para el gate, el control posterior y `--snapshot`: las del contexto (`nombres`, {entity_id: nombres}), las del
    censo (nombre, nombre canónico y alias de cada nodo) y, con el export (`export`), las suyas."""
    todos = {e: set(v) for e, v in (nombres or {}).items()}
    for row in conn.execute("SELECT entity, payload FROM objects WHERE object_kind='node'"):
        if not (isinstance(row['entity'], str) and row['entity'].startswith(seudonimos.SENSIBLES)):
            continue
        props = json.loads(row['payload']).get('props') or {}
        todos.setdefault(row['entity'], set()).update(
            v.strip() for k in ('name', 'canonical_name', 'aliases')
            for v in (props.get(k) if isinstance(props.get(k), list) else [props.get(k)])
            if isinstance(v, str) and v.strip())
    for eid, valores in (export.por_entidad() if export is not None else {}).items():
        todos.setdefault(eid, set()).update(valores)
    return seudonimos.indice_sociedades(todos)


def _census_prose(conn, entity):
    """{'summary', 'role'}: el valor COMPLETO de cada propiedad en el censo, sobre el que se tomó la
    decisión. Es el que guarda `objects.payload` (las unidades `/summary@a:b` son trozos sin pérdida
    de él), y no depende de qué trozo del pack cubra esta decisión."""
    row = conn.execute("SELECT payload FROM objects WHERE object_kind='node' AND entity=? LIMIT 1",
                       (entity,)).fetchone()
    props = (json.loads(row['payload']).get('props') or {}) if row else {}
    return {which: props.get(which) or '' for which in ('summary', 'role')}


# Capas cuyo loader RECREA la arista desde su YAML: la regla de relations borra lo que ya está en el
# seed, pero la fila de la capa hay que quitarla/editarla a mano (esc/report/capas-<fecha>.md).
LAYER_KINDS = {'acusacion', 'ultima_hora', 'contexto'}  # ontologia-ok: capas que recrea su loader

_EDGE_PROPS = ('source_kind', 'source_doc', 'source_page', 'source_quote', 'status', 'firme',
               'amount', 'currency', 'date', 'note')


def _unit_block(row):
    value = json.loads(row['value'])
    block = {'unit_id': row['unit_id'], 'kind': row['kind'], 'field': row['field'], 'value': value,
             'state': row['state'], 'resolution': row['resolution'], 'edge': None}
    if row['kind'] == 'edge' and isinstance(value, dict):
        props = value.get('props') or {}
        block['edge'] = {'type': value.get('type'), 'start_entity': value.get('start'),
                         'end_entity': value.get('end'), **{k: props.get(k) for k in _EDGE_PROPS}}
    return block


def _pack_from_ledger(conn, meta):
    """(pack, errores) — el pack mínimo que `validate_decision` necesita, desde el ledger.

    `_meta.unit_ids` acota el pack a las unidades que ESTA decisión cierra: en un cierre parcial
    (063 AC7) el pack se reparte entre la decisión del acuerdo dual (o triple, histórico), las del árbitro
    y la del dueño, y cada una se valida —y se cubre— contra su trozo. Vacío o ausente = el pack entero.
    Un pack que no está en el censo o una unidad que no es del pack se nombran por su campo y su posición, nunca por su
    valor (spec 071 AC87).
    """
    pack_id = meta.get('pack_id')
    row = conn.execute('SELECT * FROM esc_packs WHERE pack_id=?', (pack_id,)).fetchone()
    if row is None:
        return None, ['_meta.pack_id sin fila en esc_packs']
    errors, units = [], []
    ids = json.loads(row['unit_ids'])
    wanted = [uid for uid in (meta.get('unit_ids') or []) if uid]
    if wanted:
        errors += [f'_meta.unit_ids[{n}] no pertenece al pack {nombre_de_pack(pack_id, "de la decisión")}'
                   for n, uid in enumerate(wanted, 1) if uid not in ids]
        ids = [uid for uid in ids if uid in set(wanted)]
    for n, uid in enumerate(ids, 1):
        unit = conn.execute('SELECT unit_id, kind, field, value, state, resolution FROM units WHERE unit_id=?',
                            (uid,)).fetchone()
        if unit is None:
            pack_ = nombre_de_pack(pack_id, 'de la decisión')
            errors.append(f'{nombre_de_unidad(uid, f"unidad {n}")} del pack {pack_} no existe en el censo')
            continue
        units.append(_unit_block(unit))
    pack = {'pack_id': row['pack_id'], 'kind': row['kind'], 'index': row['idx'] or 1, 'total': row['total'] or 1,
            'entity': _entity_block(conn, row['entity']), 'units': units,
            'census_prose': _census_prose(conn, row['entity'])}
    return pack, errors


def _decisiones_por_fichero(root, nombres):
    """Las decisiones de esos ficheros de `esc/decisions` (por nombre o ruta), con su `_file`. Uno que no está aborta."""
    out = []
    for nombre in nombres:
        path = Path(nombre) if Path(nombre).is_file() else Path(root) / 'esc/decisions' / Path(nombre).name
        if not path.is_file():
            raise SystemExit(f'--decisiones: no existe {Path(nombre).name} en esc/decisions')
        decision = json.loads(path.read_text(encoding='utf-8'))
        decision['_file'] = path.name
        out.append(decision)
    return out


def load_decisions(root, resolutions=None):
    """Las decisiones de `esc/decisions/*.json`, filtradas por prefijo de `_meta.resolution`."""
    wanted = tuple(r.strip() for r in (resolutions or []) if str(r).strip())
    out = []
    for path in sorted(glob.glob(str(Path(root) / 'esc/decisions/*.json'))):
        decision = json.loads(Path(path).read_text(encoding='utf-8'))
        resolution = str((decision.get('_meta') or {}).get('resolution') or '')
        if wanted and not resolution.startswith(wanted):
            continue
        decision['_file'] = Path(path).name
        out.append(decision)
    return out


# Las familias de resolución que escriben el bucle, el árbitro, el dueño (`qa_esc.RESOLUTIONS`, T9) y las auditorías
# anteriores en el censo, con el sufijo `-corregido` de `record`, se nombran en la terminal; cualquier otra cosa es
# texto de una decisión y va su huella (spec 070, AC58, AC64). Los estados de una unidad, igual.
_RESOLUCION = re.compile(
    r'(?:(?:opus|dual|triple)-cerrada|dual-(?:fable|jev)-astra|salvaguarda-editorial'
    r'|arbitraje-(?:pendiente|\d{4}-\d{2}-\d{2}(?:-prudente)?)|tanda-\d{4}-\d{2}-\d{2}-pendiente'
    r'|humano-\d{4}-\d{2}-\d{2}[a-z]?(?:-verificado)?|lint-L\d+-\d{4}-\d{2}-\d{2}'
    r'|patch-\d{3}-(?:aplicado-\d{4}-\d{2}-\d{2}|superado-\d{3}))(?:-corregido)?')
_ESTADOS_UNIDAD = ('pending', 'verified', 'needs-human', 'patch', 'gone', 'technical-open')


def _resolucion_impresa(resolution):
    return resolution if _RESOLUCION.fullmatch(resolution) else f'huella {seudonimos.sha16(resolution)}'


def validar_packs(conn, decisions):
    """Cada decisión es de un pack del censo que contiene las unidades que toca (spec 071 AC106): las de `units`, las de
    `edges` y las de `_meta.unit_ids`. Una que no lo es no es una decisión que valga (su operación no puede ser la
    vigente de nadie, ni quedarse en «pack no aplicado»): para con exit 2, como una carga inválida, y la nombra por su
    fichero y su posición, nunca por el pack ni por la unidad."""
    unidades, errores = {}, []
    for decision in decisions:
        fichero, meta = decision.get('_file') or '?', decision.get('_meta') or {}
        pack = meta.get('pack_id') if isinstance(meta.get('pack_id'), str) else None
        if pack not in unidades:
            fila = conn.execute('SELECT unit_ids FROM esc_packs WHERE pack_id=?', (pack,)).fetchone() if pack else None
            unidades[pack] = None if fila is None else set(json.loads(fila[0] or '[]'))
        suyas = unidades[pack]
        if suyas is None:
            errores.append(f'{fichero}: _meta.pack_id sin fila en esc_packs')
            continue
        for campo, entradas in (('units', decision.get('units')), ('edges', decision.get('edges')),
                                ('_meta.unit_ids', meta.get('unit_ids'))):
            for n, entrada in enumerate(entradas or [], 1):
                uid = entrada.get('unit_id') if isinstance(entrada, dict) else entrada
                if not isinstance(uid, str) or uid not in suyas:
                    errores.append(f'{fichero}: {campo}[{n}]: la unidad no pertenece a su pack')
    for error in errores:
        print(error, file=sys.stderr)
    if errores:
        raise SystemExit(2)


# Qué estado del censo puede cubrir cada familia de resolución. La palabra del dueño manda
# sobre cualquier estado (pendiente, needs-human o ya verificada por un lector); un cierre
# automático solo toca lo pendiente, o lo que él mismo cerró (re-ejecución del apply).
def _coverable(resolution, unit):
    state, current = unit['state'], unit['resolution'] or ''
    if resolution.startswith('humano-'):
        return state in ('pending', 'needs-human', 'verified'), 'pending/needs-human/verified'
    return (state == 'pending'
            or (state == 'verified' and current in (resolution, resolution + '-corregido'))), 'pending'


def _textos_publicos(decision, alias=True):
    """AC69/AC72/AC75: [(texto, es_url)] de lo que `materialize` copia de una decisión a una fila pública o impresa:
    el resumen y el rol con sus motivos, el motivo y el valor de cada arista, la nota del estado, la tanda editorial,
    las entradas de `alias_deny` (sin `alias`, no: son alias, no prosa) y, como URL, las fuentes del resumen y la del
    estado (`estado.url`, `estado.fuente.url`). Las `notes` y la `evidence` son razonamiento privado: ni se imprimen
    ni se escriben."""
    def bloque(clave):
        valor = decision.get(clave)
        return valor if isinstance(valor, dict) else {}

    def filas(clave):
        valor = decision.get(clave)
        return [f for f in valor if isinstance(f, dict)] if isinstance(valor, list) else []

    summary, role, estado, entradas = bloque('summary'), bloque('role'), bloque('estado'), decision.get('alias_deny')
    textos = [summary.get('resumen'), summary.get('reason'), role.get('role'), role.get('reason'), estado.get('nota'),
              *(e.get(k) for e in filas('edges') for k in ('reason', 'value')),
              *(e.get(k) for e in filas('editorial') for k in ('text', 'reason')),
              *((entradas if isinstance(entradas, list) else [entradas]) if alias else ())]
    fuentes = summary.get('sources') if isinstance(summary.get('sources'), list) else []
    fuente_estado = estado.get('fuente') if isinstance(estado.get('fuente'), dict) else {}
    urls = [*fuentes, estado.get('url'), fuente_estado.get('url')]   # AC75: la URL del estado, también
    return [(t, False) for t in textos if isinstance(t, str)] + [(u, True) for u in urls if isinstance(u, str)]


# AC78: los `set_prop` que se publican como texto; `amount` y `source_page` salen como número (`_cast_value`, tras
# `_problema_set_prop`) y `source_kind` es vocabulario cerrado.
_SET_TEXTO = frozenset(SET_PROPS) - NUMERIC_PROPS - {'source_page', 'source_kind'}


def _filas_seudonimo(decision, eid, by_id, seudos):
    """AC78 (spec 070): [(unidad, entidades, [(texto, clase)], documento, [(texto, tipo, doc)])] del texto que una
    decisión lleva a una fila de un seudónimo, antes de `materialize`. Si la decisión es sobre uno (`eid`), su resumen
    con sus fuentes, su rol, sus motivos, la nota y la URL de su estado y su tanda editorial (sin unidad); de cada
    arista que toca a uno, su motivo, el valor de un `set_prop` de texto (`_SET_TEXTO`) y la cita (`quote_contains`) de
    su regla pública, si la hay (`_regla_publica`; AC87/AC90: con los extremos de la arista entre las entidades y su
    documento, el de la regla; es su selector, `seudonimos.SELECTOR`). Sus alias los juzga AC44. AC120: lo que de ello
    se publica como prosa descriptiva junto al seudónimo (la nota del estado y el rol, en `contenido`; la nota de una
    arista, su `set.note`) sale aparte, con su tipo para `seudonimos.problemas_dato_civil` y el documento en que se
    prueba un tramo citado suyo (AC124: el del estado para su nota, el de la regla para la de una arista); el motivo, no
    (en la fila de un seudónimo se publica el neutro de `_motivo`)."""
    def bloque(clave):
        valor = decision.get(clave)
        return valor if isinstance(valor, dict) else {}

    def filas(clave):
        valor = decision.get(clave)
        return [f for f in valor if isinstance(f, dict)] if isinstance(valor, list) else []

    prosa, url = seudonimos.PROSA, seudonimos.URL
    out = []
    if eid in seudos:
        summary, role, estado = bloque('summary'), bloque('role'), bloque('estado')
        fuentes = summary.get('sources') if isinstance(summary.get('sources'), list) else []
        fuente = estado.get('fuente') if isinstance(estado.get('fuente'), dict) else {}
        textos = [(summary.get('resumen'), prosa), (summary.get('reason'), prosa), (role.get('role'), prosa),
                  (role.get('reason'), prosa), (estado.get('nota'), prosa), *((u, url) for u in fuentes),
                  (estado.get('url'), url), (fuente.get('url'), url),
                  *((f.get(k), prosa) for f in filas('editorial') for k in ('text', 'reason'))]
        doc_estado = str(estado.get('doc') or '').strip() or None
        dato_civil = [(t, 'contenido', d) for t, d in ((estado.get('nota'), doc_estado),
                                                       (role.get('role') if role.get('action') == 'replace' else None,
                                                        None))
                      if isinstance(t, str) and t.strip()]
        out.append((None, {eid}, [(t, c) for t, c in textos if isinstance(t, str)], None, dato_civil))
    for arista in filas('edges'):
        edge = (by_id.get(arista.get('unit_id')) or {}).get('edge') or {}
        entidades = {x for x in (eid, edge.get('start_entity'), edge.get('end_entity')) if x}
        if not entidades & seudos:
            continue
        textos = [(arista.get('reason'), prosa)]
        if arista.get('action') == 'set_prop' and arista.get('prop') in _SET_TEXTO:
            textos.append((arista.get('value'), prosa))
        if _regla_publica(arista.get('action') or 'keep', edge, seudos):
            textos.append((_procedencia(edge)['quote_contains'], seudonimos.SELECTOR))
        nota = arista.get('value') if arista.get('action') == 'set_prop' and arista.get('prop') == 'note' else None
        doc = _procedencia(edge)['doc']
        out.append((arista.get('unit_id'), entidades, [(t, c) for t, c in textos if isinstance(t, str)], doc,
                    [(nota, 'contenido', doc)] if isinstance(nota, str) and nota.strip() else []))
    return out


def _conservadas(decision, by_id, seudos):
    """AC119 (spec 070): [(unidad, extremos, cita, documento, capa)] de cada arista de un seudónimo que la decisión
    cubre y conserva tal cual: sin acción («keep» implícito) o con «keep». Su cita se prueba como en el snapshot
    (`gate`): la de una arista mixta (un seudónimo y un civil: persona, sociedad u organismo), si nombra a su extremo
    civil (AC90); la de cualquiera, si nombra el nombre de pila de otro seudónimo (AC118). La de una arista corregida la
    publica su regla, y la prueba ya su selector (`_filas_seudonimo`)."""
    acciones = {e.get('unit_id'): e.get('action') or 'keep' for e in decision.get('edges') or [] if isinstance(e, dict)}
    out = []
    for uid in dict.fromkeys([*(u.get('unit_id') for u in decision.get('units') or [] if isinstance(u, dict)),
                              *acciones]):
        edge = (by_id.get(uid) or {}).get('edge') or {}
        extremos = {edge.get('start_entity'), edge.get('end_entity')} - {None}
        if acciones.get(uid, 'keep') == 'keep' and extremos & seudos:
            out.append((uid, extremos, str(edge.get('source_quote') or '').strip(), edge.get('source_doc') or None,
                        edge.get('source_kind')))
    return out


def gate(conn, decisions, root=None, seudos=None, publicos=None, civiles=None, muestras=None, nombres=None,
         citas=None, documentos=None, roster=(), export=None, sociedades=None):
    """(errores, cobertura). Reconstruye el pack de cada decisión, la valida contra el esquema,
    comprueba estados y unicidad de unidades, y prueba que las filas de overlay CARGAN por
    `relations_loader._prepare_*` (sin driver de grafo). Anota `_pack` en cada decisión.
    Un pack puede traer varias decisiones (cierre parcial) mientras sus unidades sean DISJUNTAS.
    `seudos`: los seudónimos declarados del repo que se aplica (por defecto, este). Una entidad que el
    censo marca `is_pseudonym` fuera de ellos es un error: la política del loader no la alcanzaría.
    `publicos`: los documentos de su árbol público (`seudonimos.documentos_publicos`, por defecto los de este).
    `civiles`: el índice de las personas civiles de su contexto (`seudonimos.indice_civiles`; por defecto, el de este,
    construido solo si alguna decisión lleva texto a una fila de seudónimo). `muestras`: la forma de muestra de cada
    seudónimo de su roster (`seudonimos.muestras_seudonimos`, por defecto la de este), el único nombre con que sale.
    `documentos`: su registro de documentos (`seudonimos.censo_documentos`, AC120). `roster`: los problemas de su roster
    (`seudonimos.problemas_roster`, AC118), errores de la tanda. `export`: el export del grafo (`--snapshot`,
    `seudonimos.literal_del_export`), la vía (a) de AC90 y los nombres de sus entidades (AC119/AC122/AC125); sin él,
    solo la entrada de `citas:` y los nombres del censo. `sociedades`: el índice de sociedades de la ejecución
    (`indice_sociedades`, AC153: contexto, censo y export); por defecto, se construye una vez si hace falta. Los
    errores nombran la decisión por su fichero, nunca por su entity_id (spec 071 AC87)."""
    seudos = seudonimos.ids_seudonimos() if seudos is None else frozenset(seudos)
    publicos = seudonimos.documentos_publicos() if publicos is None else frozenset(publicos)
    muestras = seudonimos.muestras_seudonimos() if muestras is None else muestras
    errors, seen, covered = list(roster), {}, {}
    coverage = {'decisiones': len(decisions), 'unidades': 0, 'entidades': set(),
                'por_resolucion': Counter(), 'por_estado': Counter()}
    ok = []
    for decision in decisions:
        ref = decision.get('_file') or '?'
        meta = decision.get('_meta') or {}
        resolution = str(meta.get('resolution') or '')
        if not resolution:
            errors.append(f'{ref}: sin _meta.resolution')
            continue
        pack, pack_errors = _pack_from_ledger(conn, meta)
        errors.extend(f'{ref}: {e}' for e in pack_errors)
        if pack is None:
            continue
        decision['_pack'] = pack
        before = len(errors)
        eid = pack['entity']['id']
        if pack['entity'].get('is_pseudonym') and eid not in seudos:
            errors.append(f'{ref}: {eid}: el censo lo marca como seudónimo judicial pero no está declarado en '
                          'context/pseudonyms.yaml; decláralo antes de aplicar')
        # AC62 (spec 070): el roster manda. Un seudónimo declarado cuyo nodo del censo no lleva la marca (un censo
        # anterior a la declaración) se valida como seudónimo: sin prosa identificativa ni estado por encima de
        # investigado. `materialize` decide por el roster, el mismo predicado.
        pack['entity']['is_pseudonym'] = bool(pack['entity'].get('is_pseudonym')) or eid in seudos
        # AC83 (spec 070): el nombre de un seudónimo nunca viene del censo. El que guarda el censo para la entidad y
        # para cada extremo seudónimo de sus aristas ha de ser una variante del de pila (si no, el censo lo empareja con
        # otro: error con su huella, sin imprimirlo), y la decisión se valida, se imprime y se escribe con la forma de
        # muestra del roster.
        extremos = {eid} | {x for u in pack['units'] for x in ((u.get('edge') or {}).get('start_entity'),
                                                                 (u.get('edge') or {}).get('end_entity'))}
        for sid in sorted(extremos & seudos):
            errors.extend(f'{ref}: nombre del censo de un seudónimo fuera de sus variantes '
                          f'(huella {seudonimos.sha16(v)})' for v in dict.fromkeys(_nombres_censo(conn, sid))
                          if isinstance(v, str) and v.strip() and not seudonimos.es_variante_nombre(sid, v))
        if eid in seudos:
            pack['entity']['name'] = muestras.get(eid) or seudonimos.forma_de_muestra({'id': eid})
        mine = [u['unit_id'] for u in pack['units']]
        prior = covered.setdefault(pack['pack_id'], {})
        overlap = [uid for uid in mine if uid in prior]
        if overlap:
            errors.append(f'{ref}: solape de unidades con {prior[overlap[0]]}: '
                          + ', '.join(uid[:8] for uid in overlap[:5]))
        prior.update({uid: ref for uid in mine})
        errors.extend(f'{ref}: {e}' for e in validate_decision(decision, pack))
        by_id = {u['unit_id']: u for u in pack['units']}
        # AC48 (spec 070): toda arista que trae la decisión, la toque o no, lleva un tipo canónico. Si no, error con
        # su huella: el registro, el dry-run y los errores lo reproducirían.
        for block in pack['units']:
            tipo = (block.get('edge') or {}).get('type')
            if block.get('kind') == 'edge' and not _tipo_canonico(tipo):
                errors.extend(f'{ref}: unidad {str(block["unit_id"])[:8]}: {motivo}'
                              for motivo in seudonimos.problemas_tipo(tipo))
        for n, unit in enumerate(decision.get('units') or [], 1):
            uid = unit.get('unit_id')
            if uid in seen:
                errors.append(f'{ref}: units[{n}]: unidad repetida (también en {seen[uid]})')
                continue
            seen[uid] = ref
            if uid not in by_id:
                continue  # ya lo dijo validate_decision
            coverable, expected = _coverable(resolution, by_id[uid])
            if not coverable:
                estado = by_id[uid]['state'] if by_id[uid]['state'] in ESTADOS_UNIDAD else 'estado no reconocido'
                errors.append(f'{ref}: unidad {uid[:8]} en estado {estado}/'
                              f'{nombre_de_resolucion(by_id[uid]["resolution"])}, '
                              f'{nombre_de_resolucion(resolution, FAMILIA_DECISION)} solo cubre {expected}')
            coverage['unidades'] += 1
            coverage['por_estado'][by_id[uid]['state']] += 1
        if eid in seudos:
            # Un alias de seudónimo solo se retira nombrándolo como está en una identidad que la decisión cubre (salvo
            # espacios en los bordes): sin `alias_deny` se conservarían todos y el registro diría «alias retirado», y
            # con una errata se retiraría una cadena que no existe y se conservaría el alias (spec 070).
            identidades = [(u, by_id[u.get('unit_id')]) for u in decision.get('units') or []
                           if (by_id.get(u.get('unit_id')) or {}).get('kind') == 'identity']
            pedidos = [str(a).strip() for a in decision.get('alias_deny') or [] if str(a).strip()]
            if not pedidos and any(u.get('label') == 'retirada' or u.get('verdict') == 'corregido'
                                   for u, _ in identidades):
                errors.append(f'{ref}: identidad del seudónimo {eid} retirada/corregida sin `alias_deny`: '
                              'indica qué alias se retiran')
            cubiertos = {str(alias).strip() for _, block in identidades
                         for alias in block['value']['identity'].get('aliases') or []}
            if any(alias not in cubiertos for alias in pedidos):
                errors.append(f'{ref}: una entrada de `alias_deny` no es alias de ninguna identidad cubierta de {eid}')
        else:
            # AC60 (spec 070): el `alias_deny` de un civil se publica en su regla; no puede nombrar a un seudónimo
            # judicial salvo que sea el propio nombre de la entidad o de una identidad cubierta. La huella, no el texto.
            textos = []
            for u in decision.get('units') or []:
                block = by_id.get(u.get('unit_id')) or {}
                value = block.get('value') if block.get('kind') == 'identity' else None
                identidad = value.get('identity') if isinstance(value, dict) else None
                if isinstance(identidad, dict):
                    textos += [str(t) for t in (identidad.get('name'), *(identidad.get('aliases') or [])) if t]
            entradas = decision.get('alias_deny') or []
            errors.extend(f'{ref}: alias_deny: una entrada nombra a un seudónimo judicial '
                          f'(huella {seudonimos.sha16(str(a).strip())})'
                          for a in (entradas if isinstance(entradas, list) else [entradas])
                          if seudonimos.alias_civil_nombra_seudonimo(a, eid, seudos, textos))
            # AC69: ni lo que de su prosa se publica (el detector del check, AC66). La huella del seudónimo.
            sid = seudonimos.nombra_en(_textos_publicos(decision), seudos)
            if sid:
                errors.append(f'{ref}: prosa civil que nombra a un seudónimo judicial (huella {seudonimos.sha16(sid)})')
            # AC160: ni identifica a un seudónimo con un civil (la regla del check en una fila civil, con su nombre, el
            # que `materialize` le pone), ni, AC167, ese nombre lo identifica. La huella del texto.
            prosa = [(t, seudonimos.PROSA) for t, es_url in _textos_publicos(decision, alias=False) if not es_url]
            propios = [*prosa, (pack['entity'].get('name') or '', seudonimos.NOMBRE)]
            if any(seudonimos.podria_identificar(t, seudos) for t, _ in propios):
                if civiles is None:
                    civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(), seudos,
                                                        seudonimos.cargar_permitidos())
                if nombres is None:
                    nombres = seudonimos.nombres_contexto()
                if sociedades is None:
                    sociedades = indice_sociedades(conn, nombres, export)
                errors.extend(f'{ref}: {_MOTIVOS_PROSA[motivo]} (huella {huella})'
                              for motivo, huella in seudonimos.identificaciones_civiles(
                                  propios, {eid}, seudos, civiles, sociedades, nombres))
            # AC105(a): ni caracteres invisibles, de ancho completo o de otro alfabeto (la guarda del check).
            errors.extend(f'{ref}: {seudonimos.MOTIVOS["sospechoso"]} (huella {seudonimos.sha16(t)})'
                          for t, es_url in dict.fromkeys(_textos_publicos(decision))
                          if seudonimos.texto_sospechoso(t, es_url))
        # AC78: el texto que la decisión lleva a una fila de un seudónimo no nombra a un civil, otra entidad, un
        # documento fuera del árbol público u otro seudónimo. La huella, nunca el texto.
        filas = _filas_seudonimo(decision, eid, by_id, seudos)
        conservadas = _conservadas(decision, by_id, seudos)
        if (filas or conservadas) and civiles is None:
            civiles = seudonimos.indice_civiles(seudonimos.personas_contexto(), seudos, seudonimos.cargar_permitidos())
        if (filas or conservadas) and nombres is None:
            nombres = seudonimos.nombres_contexto()
        if (filas or conservadas) and citas is None:
            citas = seudonimos.cargar_permitidos().get('citas') or []
        if filas and documentos is None:
            documentos = seudonimos.censo_documentos(None, publicos)
        if (filas or conservadas) and sociedades is None:
            sociedades = indice_sociedades(conn, nombres, export)
        for uid, entidades, textos, doc, dato_civil in filas:
            donde = '' if uid is None else f'unidad {ref_unidad(uid, by_id)}: '
            errors.extend(f'{ref}: {donde}texto de una fila de seudónimo nombra a un civil o a un documento '
                          f'(huella {huella})'
                          for huella in seudonimos.problemas_seudonimo(textos, entidades, seudos, civiles, publicos,
                                                                       nombres, sociedades))
            # AC90: la cita de su regla que nombra a su extremo civil, solo con la entrada de `citas:` de la lista de
            # ese texto exacto; AC118: la que nombra el nombre de pila de otro seudónimo, igual. AC125: la prueba única,
            # sin el export: el selector se publica en el árbol, que solo lo prueba por `citas:`.
            def por_citas(texto, s=frozenset(entidades & seudos), d=doc):
                return seudonimos.prueba_cita(texto, d, s, citas)
            errors.extend(f'{ref}: {donde}{seudonimos.MOTIVOS["coaparicion"]} (huella {huella})'
                          for huella in seudonimos.citas_sin_prueba(textos, entidades, seudos, civiles, nombres,
                                                                    por_citas))
            errors.extend(f'{ref}: {donde}{seudonimos.MOTIVOS["pila"]} (huella {huella})'
                          for huella in seudonimos.citas_con_pila_ajena(textos, entidades, seudos, por_citas))
            # AC105(a): la guarda del check, también sobre el selector de su regla.
            errors.extend(f'{ref}: {donde}{seudonimos.MOTIVOS["sospechoso"]} (huella {seudonimos.sha16(t)})'
                          for t, c in dict.fromkeys(textos) if seudonimos.texto_sospechoso(t, c == seudonimos.URL))
            # AC120/AC123/AC124: la prosa que se publicará junto al seudónimo, sin una sociedad real ni una fecha
            # fuera de sus campos cerrados, salvo en un tramo citado que la vía (a) o la (b) prueban en su documento.
            errors.extend(f'{ref}: {donde}{motivo} (huella {seudonimos.sha16(t)})'
                          for t, tipo, d in dato_civil
                          for motivo in _motivos_dato_civil(t, tipo, d, documentos, sociedades, frozenset(entidades),
                                                            frozenset(entidades & seudos), citas, export))
        # AC119/AC122: conservar también se prueba (`_conservadas`), con evidencia positiva: la de una mixta queda en la
        # decisión (`_pruebas`) y, con lo que la produce, en `_conservadas`, para que el control posterior la recalcule.
        decision['_pruebas'], decision['_conservadas'] = {}, {}
        for uid, extremos, cita, doc, capa in conservadas:
            conocidos = {e: {n for n in _nombres_censo(conn, e, alias=True) if isinstance(n, str) and n.strip()}
                         | (export.nombres(e) if export is not None else set())
                         for e in seudonimos._extremos_civiles(extremos, seudos)}
            prueba, motivos = seudonimos.prueba_conservada(cita, doc, capa == 'judicial', extremos, seudos, civiles,
                                                           nombres, citas, export, conocidos, publicos, sociedades)
            errors.extend(f'{ref}: unidad {ref_unidad(uid, by_id)}: {_motivo_conservada(m, export)} '
                          f'(huella {seudonimos.sha16(cita)})' for m in motivos)
            if prueba and not motivos:
                decision['_pruebas'][uid] = prueba
                decision['_conservadas'][uid] = (cita, doc, capa, tuple(sorted(extremos)),
                                                 {e: tuple(sorted(v)) for e, v in conocidos.items()})
        estado = decision.get('estado') or {}
        if VAGUE.search(estado.get('nota') or ''):
            errors.append(f'{ref}: estado.nota con «según la prensa/los medios» sin medio nominal')
        if (estado.get('action') == 'set' and eid in seudos and str(estado.get('doc') or '').strip()
                and not seudonimos.doc_publico(estado.get('doc'), publicos)):
            # El bloque `estado` acaba pegado en accusations.yaml, que es público: junto a un seudónimo, solo un
            # documento que el árbol publica (como AC30 y la fuente del registro); el nombre de otro, ni en el error.
            errors.append(f'fuente no pública en estado de seudónimo ({ref}, '
                          f'{seudonimos.huella_doc(estado.get("doc"))})')
        for n, edge_decision in enumerate(decision.get('edges') or [], 1):
            accion = edge_decision.get('action')
            for prop, value in (props_del_set(edge_decision).items()
                                if accion in ('set_prop', 'retype', 'invert') else ()):
                problema = _problema_set_prop(prop, value) if prop in SET_PROPS else None
                if problema:
                    # AC52 (spec 070): antes de `materialize`, que lo convertiría (`_cast_value`), y sin el valor, que
                    # puede ser justo lo que se retira; la unidad, por su posición si no tiene su forma (spec 071 AC87).
                    errors.append(f'{ref}: {nombre_de_unidad(edge_decision.get("unit_id"), f"operación {n}")}: '
                                  f'{accion} {prop} inválido: {problema}')
            # AC45 (spec 070; AC30 es el caso de la cita): una regla pública con extremo seudónimo solo nombra
            # documentos que el árbol ya publica, y su cita exige el suyo. Fail-closed: la decisión queda para
            # tratamiento manual; nunca el nombre de un documento no publicado (puede ser lo que se retira).
            edge = (by_id.get(edge_decision.get('unit_id')) or {}).get('edge') or {}
            source, target = edge.get('start_entity'), edge.get('end_entity')
            if _regla_publica(edge_decision.get('action') or 'keep', edge, seudos) and {source, target} & seudos:
                errors.extend(f'{ref}: unidad {ref_unidad(edge_decision.get("unit_id"), by_id)}: {motivo}'
                              for motivo in seudonimos.problemas_documentos(_procedencia(edge), publicos))
        coverage['entidades'].add(pack['entity']['id'])
        coverage['por_resolucion'][_resolucion_impresa(resolution)] += 1   # sale por la terminal (AC58)
        if len(errors) == before:  # solo lo sano llega a la prueba de carga
            ok.append(decision)
    coverage['entidades'] = len(coverage['entidades'])
    coverage['por_resolucion'] = dict(coverage['por_resolucion'])
    coverage['por_estado'] = dict(coverage['por_estado'])
    errors.extend(_load_check(materialize(ok, 'gate', _today(), seudos, publicos), root) if ok else [])
    return errors, coverage


CLAIM_STANCES = POSTURAS
CLAIMS_REL = 'pipeline/data/claims/claims.yaml'
CLAIMS_YAML = Path(__file__).resolve().parents[1] / 'data' / 'claims' / 'claims.yaml'


def _tipo_canonico(tipo) -> bool:
    """¿Es `tipo` un tipo de arista escrito como lo escribe el grafo? Uno de la ontología (spec 069) en su forma
    canónica, o una de las dos posturas ante una afirmación (`CLAIM_STANCES`, que no son relaciones entre entidades)."""
    return tipo in CLAIM_STANCES or not seudonimos.problemas_tipo(tipo)


def _tipo_impreso(tipo) -> str:
    """Un tipo de arista tal como lo escriben el registro, el dry-run y los errores: el canónico, o su huella (AC48)."""
    return tipo if _tipo_canonico(tipo) else f'tipo {seudonimos.sha16(tipo)}'


def _leer_claims(path):
    """claims.yaml (vacío si no está), con el lector neutro de `registro_check` (spec 071 AC75 y AC108): un YAML que no
    carga, o con una clave repetida, para con exit 2, su fichero y su posición, nunca con el valor (un `!!int` roto es
    un ValueError con él, y un error de sintaxis cita la línea entera)."""
    from scripts import registro_check as RC
    return (RC._leer_yaml(path) or {}) if Path(path).exists() else {}


def _posturas_vivas(path):
    """{(entity_id, postura, cita)} de las posturas que claims.yaml declara (ninguna si no está)."""
    return {(p.get('entity_id'), stance, (p.get('quote') or '').strip())
            for claim in _leer_claims(path).get('claims') or []
            for stance in CLAIM_STANCES.values() for p in claim.get(stance) or []}


def _posturas_por_identidad(path):
    """{(entity_id, tipo, capa, afirmación)}: las posturas que claims.yaml declara, por la identidad con que las recrea
    `claims_loader` (un MERGE por titular, postura y capa sobre la afirmación, que actualiza la cita; sin capa, la
    judicial), con la afirmación como la fusiona su `_prepare` (su claim_id, o el de su tema o su texto) y como la ve el
    export (`registro_check.afirmacion`, spec 071 AC104). Sin los logs del cargador, que registran entradas enteras."""
    from scripts import registro_check as RC
    from . import claims_loader
    previo, claims_loader.log = claims_loader.log, _SinLog()
    try:
        _, posturas = claims_loader._prepare(_leer_claims(path).get('claims') or [])
    finally:
        claims_loader.log = previo
    return {(p['person_id'], p['rel_type'], p['source_kind'], RC.afirmacion(p['claim_id'])) for p in posturas}


def _claims_check(rows, path=CLAIMS_YAML):
    """Una postura retirada no puede seguir en claims.yaml; cualquier otro cambio se hace allí a mano. El error nombra
    la decisión, la unidad y el tipo de la postura, nunca a su titular (spec 071 AC87)."""
    live = _posturas_vivas(path)
    errors = []
    for row in rows:
        where = f'{row["ref"]} [{row["unit"]}] -[{_tipo_impreso(row["type"])}]-> Claim'
        if row['action'] != 'deny':
            errors.append(f'claims: {where}: «{row["action"]}» sobre una postura se edita a mano en claims.yaml')
        elif (row['entity_id'], CLAIM_STANCES[row['type']], row['quote']) in live:
            errors.append(f'claims: {where}: retirada, pero la postura sigue en claims.yaml')
    return errors


ACC_DIR = 'pipeline/data/accusations'
DOCS_DIR = 'pipeline/data/documents'
# Lo que saca la arista curada de donde la deja `make accusations`, que la recrea tal como la declara.
ACC_LAYER_ACTIONS = ('deny', 'retype', 'invert')


class _SinLog:
    """Logger nulo: los cargadores registran entradas enteras (citas y nombres incluidos) al validarlas."""

    def __getattr__(self, _nombre):
        return lambda *args, **kwargs: None


# Lo que una regla `fix` cambia de una arista y la saca de la regla `deny` de otra: su capa (el deny sin `kinds` solo
# toca judicial/null), su documento o su cita (los filtros del deny) y su tipo (retype).
FIRMA_PROPS = ('source_kind', 'source_doc', 'source_quote')
REL_DIR = 'pipeline/data/relations'
_TAG = re.compile(r'\[([0-9a-f]{8})\]')


def _reglas_del_overlay(repo, raiz=None, registro=None, cobertura=True):
    """([(origen, regla deny)], [(origen, regla fix)]) de todos los ficheros del overlay que lee el loader, preparadas
    como él las prepara (extremos resueltos; una deny opaca, contra la raíz privada de la auditoría `raiz`, con la
    misma resolución que el loader: spec 071 AC114 y AC121); `origen` = «fichero:sección[índice]». Sin logs: el loader
    registra las filas que descarta con sus nombres. Cada fichero se lee con el lector neutro de `registro_check` y cada
    fila se prepara aparte: un YAML que no carga (un `!!int` roto es un ValueError con el valor) o una fila que el
    loader rechaza (un `kinds` desconocido es un SystemExit con él; una opaca que no resuelve, también) paran con exit 2
    y el fichero, la sección y el índice por stderr, nunca el valor (spec 071 AC49); una clave repetida en cualquier
    nivel, también (AC108). Con la raíz privada (`raiz` o QA_CENSO_ROOT), haya reglas opacas o no, el fichero privado no
    puede declarar una clave que ninguna resuelve, ni una fila que el registro (`registro`, por defecto el de `repo`)
    no admite, y cada fila que la admite tiene la suya (AC127, AC171 y AC183), como en el loader; sin `cobertura`,
    solo lo primero (quien escribe las declaraciones: el apply y `privatizar`)."""
    from scripts import registro_check as RC
    deny, fix = [], []
    opacas, claves = [], []

    def preparar_deny(filas):
        if any(es_opaca(f) for f in filas) and not opacas:
            opacas.append(relations_loader._opacas(raiz))
        claves.extend(f['unidad'] for f in filas if es_opaca(f))
        return relations_loader._prepare_deny(filas, opacas[0] if opacas else raiz)

    previo, relations_loader.log = relations_loader.log, _SinLog()
    try:
        for path in relations_loader._ordered(Path(repo) / REL_DIR):
            doc = RC._leer_yaml(path) or {}
            if not isinstance(doc, dict):
                _overlay_rechazado(f'{path.name}: no tiene la forma de un overlay')
            for seccion, preparar, destino in (('deny', preparar_deny, deny),
                                               ('fix', relations_loader._prepare_fix, fix)):
                for i, entry in enumerate(doc.get(seccion) or []):
                    try:
                        filas = preparar([entry])
                    except (SystemExit, Exception) as exc:
                        _overlay_rechazado(f'{path.name}:{seccion}[{i}]: fila rechazada por el loader '
                                           f'({_clase_neutra(exc)})')
                    destino += [(f'{path.name}:{seccion}[{i}]', r) for r in filas]
        if opacas or raiz or os.environ.get('QA_CENSO_ROOT'):
            # AC171: con la raíz privada, el fichero privado se contrasta siempre con el overlay entero, tenga opacas o
            # no, como en el loader.
            if not opacas:
                try:
                    opacas.append(relations_loader._opacas(raiz))
                except SystemExit:
                    _overlay_rechazado('reglas opacas: la raíz privada de la auditoría no se lee')
            try:
                opacas[0].verificar(claves, registro or Path(repo) / REG_PATH, cobertura)
            except ReglaOpacaInvalida as exc:
                _overlay_rechazado(f'reglas opacas: {exc}')
    finally:
        relations_loader.log = previo
    return deny, fix


def _clase_neutra(exc):
    """La clase con que un diagnóstico nombra una excepción del loader (spec 071 AC87): la parada del 070 por la forma
    de una fila (`seudonimos.ErrorDeForma`) es un SystemExit, y se nombra así."""
    return 'SystemExit' if isinstance(exc, SystemExit) else type(exc).__name__


def _overlay_rechazado(motivo):
    print(motivo, file=sys.stderr)
    raise SystemExit(2) from None


CTX_DIR = 'pipeline/data/context'


def _reidentificaciones(repo):
    """frozenset({entity_id, entity_id}): las re-identificaciones que declara el overlay de `repo` (spec 071 AC94), con
    los entity_id que calculan sus cargadores: cada escisión de los ficheros de relaciones (`splits:`, el canónico y el
    homónimo, como `relations_loader.apply_split`) y cada merge de los de contexto (`merges:`, el canónico y sus
    duplicados entre sí, como `context_loader.load_merges`); una entrada que su cargador salta tampoco cuenta. Cada
    fichero se lee con el lector neutro de `registro_check`: uno que no carga para con exit 2 y su posición (AC49)."""
    from scripts import registro_check as RC
    from . import context_loader
    from .entity_resolution import entity_id

    def leer(path):
        doc = RC._leer_yaml(path) or {}
        return doc if isinstance(doc, dict) else {}

    def nombre(entrada):
        return str(entrada.get('name') or '').strip() if isinstance(entrada, dict) else ''

    def tipo(entrada):
        return entrada.get('type') if isinstance(entrada, dict) else None

    enlaces = set()
    for path in relations_loader._ordered(Path(repo) / REL_DIR):
        for sp in leer(path).get('splits') or []:
            if not isinstance(sp, dict):
                continue
            canon, homonimo = sp.get('canonical') or {}, sp.get('namesake') or {}
            por_cita = [s for s in sp.get('by_quote') or [] if str(s).strip()]
            if tipo(canon) in relations_loader._ENTITY_TYPES and nombre(canon) \
                    and tipo(homonimo) in relations_loader._ENTITY_TYPES and nombre(homonimo) \
                    and (str(sp.get('by_doc') or '').strip() or por_cita):
                enlaces.add(frozenset((entity_id(tipo(canon), nombre(canon)),
                                       entity_id(tipo(homonimo), nombre(homonimo)))))
    for path in context_loader._ordered(Path(repo) / CTX_DIR):
        for grupo in leer(path).get('merges') or []:
            canon = grupo.get('canonical') if isinstance(grupo, dict) else None
            if tipo(canon) not in context_loader._ENTITY_TYPES or not nombre(canon):
                continue
            ids = {entity_id(tipo(canon), nombre(canon))}
            for dup in grupo.get('duplicates') or []:
                dname = dup.strip() if isinstance(dup, str) else nombre(dup)
                if dname:
                    ids.add(entity_id(tipo(canon) if isinstance(dup, str) else (tipo(dup) or tipo(canon)), dname))
            enlaces.update(frozenset((x, y)) for x in ids for y in ids if x < y)
    return frozenset(enlaces)


def _guarda_fix_deny(rows, repo, aristas=(), raiz=None):
    """`relations_loader` aplica los `fix` antes que los `deny`, y sobre TODAS las aristas, sin packs (5ª y 6ª pasadas
    de Codex): una regla que cambia la capa o el tipo de una arista y alcanza el objetivo de un deny la saca de él y la
    deja publicada. Compara, todas con todas, las reglas que escribiría el lote (`rows` de `materialize`) y las que ya
    están en el overlay (`alcance.solapan`), con `aristas` (las del censo, y las del export cuando las hay) como
    evidencia: dos trozos distintos de una misma cita no se contienen y casan la misma arista (AC51). Una deny opaca,
    del overlay o del lote, por lo que resuelve contra la raíz privada `raiz` (AC114). Los errores nombran de dónde sale
    cada regla (fichero, sección e índice, o la unidad del lote), nunca un valor."""
    deny, fix = _reglas_del_overlay(repo, raiz, cobertura=False)
    for seccion, destino in (('deny', deny), ('fix', fix)):
        for r in rows.get(seccion) or []:
            origen = f'lote [{"".join(_TAG.findall(r.get("reason") or "")[:1]) or "?"}]'
            destino += [(origen, resuelta) for resuelta in (relations_loader._prepare_deny([r], raiz) if es_opaca(r)
                                                            else [r])]
    por_par = defaultdict(list)
    for value in aristas:
        por_par[(value.get('type'), frozenset((value.get('start'), value.get('end'))))].append(value)
    errors = []
    for origen_f, f in fix:
        if f.get('corrected_type') or set(f.get('set') or {}) & set(FIRMA_PROPS):
            del_par = por_par.get((f.get('type'), frozenset((f.get('source_id'), f.get('target_id')))), ())
            errors += [f'una regla fix de capa o tipo alcanza el objetivo de un deny ({origen_f} → {origen_d})'
                       for origen_d, d in deny if solapan(f, d, del_par)]
    return list(dict.fromkeys(errors))


def _aristas_censo(root):
    """Los valores de todas las aristas del censo (la evidencia de AC51), sin abrirlo para escribir."""
    census = sqlite3.connect((Path(root) / 'census.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return [json.loads(value) for (value,) in census.execute("SELECT value FROM units WHERE kind='edge'")]
    finally:
        census.close()


# Qué operación vigente produce cada clase de regla del overlay (spec 071 AC54).
_PRODUCE = {'deny': ('deny',), 'retype': ('retype',), 'invert': ('invert',), 'set': ('set_prop', 'set_status')}


def _clase(seccion, regla):
    return ('deny' if seccion == 'deny' else 'retype' if regla.get('corrected_type')
            else 'invert' if regla.get('swap') else 'set')


def _fases_regla(regla):
    """Las fases de una regla preparada (`relations_loader._prepare_fix`): el tipo al que retipa, si invierte y los
    props que escribe. Una regla deny no tiene ninguna."""
    return {'retype': regla.get('corrected_type') or None, 'swap': bool(regla.get('swap')),
            'set': regla.get('set') or None}


def _fases_decididas(op):
    """Las fases que autoriza la operación vigente `op` (spec 071 AC71: una operación, varias fases): el tipo nuevo de
    un retype, la inversión de un invert y sus props (`alcance.carga`: las de un set_prop o un set_status, o el mapa
    `set` de un retype o un invert, como las escribe el loader). Una carga que el loader descarta no es una fase
    ausente: es `alcance.CargaInvalida` (AC78), y ni `_fases_no_decididas` ni `_carga_coincide` dicen que coincide."""
    accion = op.get('action')
    return {'retype': (op.get('new_type') or None) if accion == 'retype' else None, 'swap': accion == 'invert',
            'set': carga(op)}


def _fases_no_decididas(regla, op):
    """Las fases en que la regla y la operación vigente no coinciden: una que la regla hace y la operación no autoriza
    (AC71: un swap que un retype no decidió, una nota que un retype sin mapa `set` no lleva) o una que la operación
    decide y la regla no hace (AC74: el mapa `set` de un retype o un invert que su regla descarta)."""
    decididas = _fases_decididas(op)
    return [fase for fase, hace in _fases_regla(regla).items() if bool(hace) != bool(decididas[fase])]


def _carga_coincide(regla, op):
    """Si la regla preparada hace exactamente lo que dice la operación vigente (spec 071 AC58 b y AC71): cada una de sus
    fases, la decidida (un retype, al tipo nuevo; un invert, la inversión; las props, con sus valores: `alcance.carga`),
    y ninguna más. Un deny no tiene fases."""
    return _fases_regla(regla) == _fases_decididas(op)


def _por_firma(regla, value, op, clase, index, posibles, actuales=None, ajenas=None, inferidos=frozenset()):
    """Una operación sobre una arista con un extremo del censo que ya no está en el export (re-identificado después del
    censo) se evalúa por la firma del censo, su tipo, su documento y su cita (spec 071, enmienda 11): un deny está
    hecho si no queda ninguna copia con ella en ningún par, bajo ninguno de los tipos a que pueden llevarla las fases
    (`posibles`, compuestas también sobre sus pares actuales: AC63), ni, si es una acusación o una postura, ninguna con
    la identidad de su cargador en sus pares actuales (`actuales`, AC68); un set, un retype o un invert, si hay una
    copia viva en el estado decidido (`registro_check._decidido`: con el tipo nuevo o al revés, en ese sentido salvo en
    un tipo simétrico) con la carga de la fase `set` de la operación (`alcance.carga`, la que escribe el loader con sus
    props derivadas, también en un retype o un invert: AC73 y AC77) y ninguna en ese estado sin ella, ni en el par de
    la regla ni en los demás pares actuales (AC76: una copia correcta no exime a las demás) ni en otro par
    (`registro_check.copias_libres`, AC89, con lo que no le atribuye: `ajenas`), salvo la ambigua entre unidades, que
    no es suya ni de la otra (AC91: la cuenta `materializacion`), y la de otro par que no se le atribuye porque ya tiene
    copia en los buscados y el overlay no declara esa re-identificación (AC94). Las copias, clasificadas como en los dos
    checks (`registro_check.clasificar`, AC85 y AC96, sobre el par de la regla y sus pares actuales): una de una capa
    que su cargador recrea por su identidad o una postura, por esa identidad, con cualquier documento y cita; el resto
    por su documento y su cita; la de un par que solo deduce la firma y que puede ser de otra unidad, ninguna
    (`inferidos`, AC103); la de un deny, también en los pares que el overlay declara de la arista (AC109). Devuelve
    «regla» si la acredita una copia del par de la regla, «otro par» si solo una de otro par (AC89: se acepta, pero el
    aviso lo dice) y None si no está hecha."""
    from scripts import registro_check as RC
    ajenas = RC.SIN_AJENAS if ajenas is None else ajenas
    if clase == 'deny':
        return None if RC.clasificar(index, value, 'deny', None, None, posibles, actuales, ajenas).incompatibles \
            else 'regla'
    decidida = carga(op) or {}
    par = {'start': regla.get('source_id'), 'end': regla.get('target_id')}
    copias = RC.clasificar(index, value, op.get('action'), op.get('new_type'), decidida, posibles,
                           [par, *(actuales or [value])], ajenas, inferidos)
    if copias.sin_carga or not copias.decididas:
        return None
    tipo, sentido = RC._decidido(value, op.get('action'), op.get('new_type'))
    if any(s == sentido and RC._lleva(props, decidida)
           for s, _, _, props in RC.copias_identificadas(index, value, tipo, [par], decidida)):
        return 'regla'
    return 'otro par' if any(fuera for _, _, fuera in copias.decididas) else None


def _pendiente(regla, value, clase, index):
    """Si la regla alcanza, en su propio par y con el predicado del loader, una copia viva con la fuente del censo de la
    arista `value` (spec 071, enmienda 12b; `registro_check.de_su_fuente`, con la cita de después de la migración
    declarada de su unidad, AC287): todavía no actuó y el Path A la aplicará; el sentido directo ya cuenta esa copia
    como viva."""
    from scripts import registro_check as RC
    return any(RC.de_su_fuente(value, props)
               and alcanza_loader(regla, {'type': regla.get('type'), 'start': inicio, 'end': fin, 'props': props}, clase)
               for inicio, fin, props in index.get(RC._par(regla.get('type'), regla.get('source_id'),
                                                           regla.get('target_id')), ()))


def _contraste_reglas(root, repo, lote=(), vivo=None, cobertura=False):
    """(errores, avisos, acreditadas): el sentido inverso de la materialización (spec 071 AC54 y AC58). Cada regla del
    overlay etiquetada `[hash8]` sale de la operación vigente de su unidad y hace lo mismo que ella: su clase (un deny,
    de un deny; un fix que retipa, de un retype; uno que invierte, de un invert; uno que solo cambia props, de un
    set_prop o un set_status), su carga (`_carga_coincide`), el tipo del censo y un alcance que casa su arista con el
    predicado del loader (`alcanza_loader`). Las decisiones son las de esc/decisions y las del `lote` que se aplica (la
    misma, si está en los dos). Sin operación de su clase, o con una etiqueta que no resuelve a una sola unidad del
    censo: «regla sin decisión vigente»; con una fase que su operación no autoriza (un swap o un `set` junto a un retype
    que no los decidió, AC71) o sin una que decide (el `set` de un retype o un invert, AC74): «regla con fase no
    decidida»; con otra carga, otro tipo u otro alcance: «regla incompatible con la decisión vigente». Una regla cuyo
    par no es el del censo es un aviso sin el export: lo evalúa `materializacion`, que con él
    (`vivo`: el índice y los entity_id del export), si un extremo del censo ya no está, la acredita por la firma del
    censo (`_por_firma`, con las fases compuestas también sobre sus pares actuales; su unidad va a `acreditadas`, y si
    solo la acredita una copia de otro par el aviso lo dice, AC89), la da por pendiente si en su par alcanza la copia
    viva con esa firma (`_pendiente`, aviso) y si no apunta a nada da el error; con los dos extremos vivos, es
    incompatible, salvo el par de después de una migración: con un extremo que el export aún no tiene, no alcanza nada
    hoy y es un aviso si otro predicado de la misma regla alcanza la arista del censo (un juego de declaraciones puede
    abarcar una migración, AC157). Que esté acreditada no exime a sus copias incompatibles: las
    cuenta `materializacion` (AC64). Los mensajes nombran la regla y la etiqueta, nunca un valor. `cobertura`: el
    fichero privado se contrasta con el registro en las dos direcciones (AC183), como en la puerta; sin ella, solo
    en la de quien escribe las declaraciones (`check` y el apply)."""
    from scripts import registro_check as RC
    deny, fix = _reglas_del_overlay(repo, root, cobertura=cobertura)
    reglas_fix = [regla for _, regla in fix]
    overlay = [regla for _, regla in deny] + reglas_fix
    transformaciones = []

    def del_censo():
        """(acciones, sets, retipos, trans, formas ajenas): solo con un extremo re-identificado; se leen una vez."""
        if not transformaciones:
            _, acciones, sets, retipos = RC.decisiones(root, set())
            trans = RC.transformadoras(census, acciones, sets, retipos)
            valores = RC.aristas_censales(census, migradas)
            transformaciones.append((acciones, sets, retipos, trans, RC.formas_ajenas(
                valores, trans, acciones, sets, retipos, reglas_fix, vivo[1], overlay, _reidentificaciones(repo))))
        return transformaciones[0]

    def por_firma(regla, uid, value, op, clase):
        acciones, sets, retipos, trans, ajenas = del_censo()
        posibles = RC.firmas_posibles(uid, value, trans, acciones, sets, retipos, reglas_fix, nodos=vivo[1],
                                      overlay=overlay)
        actuales, inferidos = RC.pares_y_origen(uid, value, vivo[1], overlay)
        return _por_firma(regla, value, op, clase, vivo[0], posibles, actuales, ajenas.get(uid, RC.SIN_AJENAS),
                          inferidos)
    census = sqlite3.connect((Path(root) / 'census.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        por_prefijo = defaultdict(list)
        for (uid,) in census.execute('SELECT unit_id FROM units'):
            por_prefijo[uid[:8]].append(uid)
        todas = {d['_file']: d for d in load_decisions(root)}
        todas.update({d.get('_file') or f'lote-{n}': d for n, d in enumerate(lote)})
        vigente = vigentes(list(todas.values()))
        # AC287 y AC292: con la firma de después de las migraciones de cita declaradas, como en el registro, contrastadas
        # con las decisiones efectivas (las del lote incluidas); una declaración que no vale no cuenta (su problema lo da
        # `materializacion`).
        migradas, _ = RC.migraciones_citas(root, census, list(todas.values()))
        # Lo que dice cada predicado de cada regla, por regla y etiqueta: una opaca resuelve a varios (su unidad del
        # censo y sus declaraciones privadas). El de un par que el export aún no tiene, con el del censo vivo, es el de
        # después de una migración: vale si otro predicado de la misma regla alcanza la arista del censo (AC157).
        # Cualquier otro error de un predicado lo es de la regla: el del censo la alcanza siempre, y no puede tapar uno
        # que retiraría otra arista.
        veredictos = defaultdict(list)
        acreditadas = set()
        for seccion, reglas in (('deny', deny), ('fix', fix)):
            for origen, regla in reglas:
                clase = _clase(seccion, regla)
                for marca in _TAG.findall(regla.get('reason') or ''):
                    dice = veredictos[(origen, marca)]
                    unidades = por_prefijo.get(marca, [])
                    uid = unidades[0] if len(unidades) == 1 and unidades[0] in vigente else None
                    op = vigente[uid][1] if uid else {}
                    if (op.get('action') or '') not in _PRODUCE[clase]:
                        dice.append(('error', f'regla sin decisión vigente ({origen}, {marca})'))
                        continue
                    if _fases_no_decididas(regla, op):
                        dice.append(('error', f'regla con fase no decidida ({origen}, {marca})'))
                        continue
                    incompatible = ('error', f'regla incompatible con la decisión vigente ({origen}, {marca})')
                    row = census.execute('SELECT kind, value FROM units WHERE unit_id=?', (uid,)).fetchone()
                    value = RC.con_migracion(uid, RC.con_afirmacion(census, uid, json.loads(row[1])), migradas) \
                        if row is not None and row[0] == 'edge' else None
                    if value is None or regla.get('type') != value.get('type') or not _carga_coincide(regla, op):
                        dice.append(incompatible)
                        continue
                    if alcanza_loader(regla, value, clase):
                        dice.append(('ok', None))
                        continue
                    extremos = {value.get('start'), value.get('end')}
                    if {regla.get('source_id'), regla.get('target_id')} == extremos:
                        dice.append(incompatible)
                    elif vivo is None:
                        dice.append(('aviso', 'par distinto del censo: se evalúa en materializacion '
                                              f'({origen}, {marca})'))
                    elif extremos - {None} <= vivo[1]:
                        suyos = {regla.get('source_id'), regla.get('target_id')} - {None}
                        dice.append(incompatible if suyos <= vivo[1] else ('migracion', incompatible[1]))
                    elif acreditada := por_firma(regla, uid, value, op, clase):
                        acreditadas.add(uid)
                        otro = RC.OTRO_PAR if acreditada == 'otro par' else ''
                        dice.append(('aviso', 'par distinto del censo: acreditada por firma '
                                              f'({otro}{origen}, {marca})'))
                    elif _pendiente(regla, value, clase, vivo[0]):
                        dice.append(('aviso', f'pendiente, par re-identificado ({origen}, {marca})'))
                    else:
                        dice.append(('error', f'par distinto del censo, sin el estado decidido en el export ({origen}, '
                                              f'{marca})'))
    finally:
        census.close()
    errores, avisos = [], []
    for (origen, marca), dice in veredictos.items():
        casa = any(clase == 'ok' for clase, _ in dice)
        for clase, texto in dice:
            if clase == 'error' or (clase == 'migracion' and not casa):
                errores.append(texto)
            elif clase == 'migracion':
                avisos.append(f'par de después de una migración, sin su nodo en el export: se evalúa tras ella '
                              f'({origen}, {marca})')
            elif clase == 'aviso':
                avisos.append(texto)
    return list(dict.fromkeys(errores)), list(dict.fromkeys(avisos)), acreditadas


def _accusations_check(capas, repo):
    """Una arista de la capa `acusacion` que se retira, se retipa o se invierte no puede estar entre las que
    genera la capa de acusaciones del repo con su tipo ORIGINAL: `make accusations` corre después de
    `make relations` en el Path A y la recrea (063, R-2026-09-24-0103). Las aristas efectivas salen de la
    preparación del propio cargador (`accusations_loader._prepare`, con la firmeza de documents.yaml: el
    tipo depende del estado), así que un retype que la capa ya declara pasa. Entre las efectivas de la misma
    persona y documento manda el delito (el `Crime:` destino); la cita solo cuenta si ninguna lo resuelve, y
    tiene que señalar exactamente una. Bloquea si la que casa tiene el tipo original. Ni los logs del
    cargador ni su rechazo llegan a la salida con sus valores: del rechazo, solo la clase. Las capas
    `contexto` y `ultima_hora` siguen saliendo solo en `capas-<fecha>.md`, para editarlas a mano."""
    rows = [r for r in capas if r['kind'] == 'acusacion' and r['accion'] in ACC_LAYER_ACTIONS]
    if not rows:
        return []
    try:
        efectivas = _acusaciones_efectivas(repo)
    except (SystemExit, Exception) as exc:
        return [f'accusations.yaml: el cargador la rechaza ({type(exc).__name__})']
    return [f'{row["ref"]} [{str(row["unidad"])[:8]}]: la arista vive en accusations.yaml '
            '(make accusations la recrea tras make relations): edita la capa en el mismo PR'
            for row in rows if _declarada(row['source'], row['target'], row['doc'], row['type'], row['quote'], efectivas)]


def _acusaciones_efectivas(repo):
    """Las aristas que genera hoy la capa de acusaciones del repo, preparadas por su propio cargador (con la firmeza de
    documents.yaml). Sin sus logs, que llevan entradas enteras; si el cargador la rechaza, su excepción."""
    from . import accusations_loader, documents_loader
    logs = accusations_loader.log, documents_loader.log
    accusations_loader.log = documents_loader.log = _SinLog()
    try:
        return accusations_loader._prepare(accusations_loader.load_files(Path(repo) / ACC_DIR),
                                           documents_loader.sentencia_firme_map(Path(repo) / DOCS_DIR))
    finally:
        accusations_loader.log, documents_loader.log = logs


# Lo que una decisión de arista quita o transforma tiene que acabar en una regla del overlay, fuera de la capa de
# acusaciones o de claims.yaml, o fuera del grafo (spec 071 AC43–AC50: 13 retiradas decididas en packs `applied` no
# estaban en ninguna).
ACCIONES_REGLA = ('deny', 'retype', 'invert')
# Y una corrección de props decidida también: su regla en el repo y su carga en el export (spec 071 AC79). El
# set_status no, a conciencia: ver `materializacion`.
CORRECCIONES = ('set_prop',)


def _contexto_materializacion(repo, raiz=None, cobertura=True):
    """(reglas del overlay, aristas efectivas de la capa de acusaciones —None si su cargador la rechaza: entonces
    ninguna de las suyas se da por retirada—, posturas que declara claims.yaml por su identidad)."""
    try:
        efectivas = _acusaciones_efectivas(repo)
    except (SystemExit, Exception):
        efectivas = None
    return _reglas_del_overlay(repo, raiz, cobertura=cobertura), efectivas, \
        _posturas_por_identidad(Path(repo) / CLAIMS_REL)


def _declarada_por_identidad(value, efectivas):
    """Si la capa de acusaciones genera la arista curada `value`: una efectiva de la misma persona, el mismo delito y el
    mismo tipo, la clave del MERGE de `accusations_loader` (spec 071 AC59 y AC65). La cita, la página y el documento no
    cuentan: el MERGE los sobrescribe sobre la misma arista, así que una declaración desde otro documento la sigue
    publicando; conservarla así exige una decisión humana posterior."""
    return any(e['person_id'] == value.get('start') and e['crime_id'] == value.get('end')
               and e['rel_type'] == value.get('type') for e in efectivas)


def _por_capa(op, value, contexto, actuales=None):
    """Si la operación sobre una arista que su cargador recrea desde un YAML (una postura de claims.yaml, una acusación
    de la capa `acusacion`) está materializada por esa capa. Una retirada, si ese YAML ya no la declara por su identidad
    (spec 071 AC59; una postura, por titular, afirmación, postura y capa: AC104), ni en sus pares actuales si un extremo
    se re-identificó (`actuales`, de `registro_check.pares_actuales`, AC68). La ausencia solo prueba una retirada
    (AC111): un retype o un invert sobre una acusación que la capa ya no declara no está hecho por eso. Lo hace la capa
    si declara su resultado (el tipo nuevo, sin otra carga) y, si no, deciden las reglas (None); la que la capa aún
    declara tal cual no está hecha (False): la recrea. Una postura sin su afirmación (`registro_check.indeterminada`)
    nunca lo está. None si la arista no es de esas o la capa de acusaciones no carga; entonces deciden las reglas."""
    from scripts import registro_check as RC
    _, efectivas, posturas = contexto
    props, aristas, accion = value.get('props') or {}, actuales or [value], op.get('action')
    if value.get('type') in CLAIM_STANCES:
        capa = str(props.get('source_kind') or 'judicial').strip().lower()
        return accion == 'deny' and not RC.indeterminada(value) and all(
            (a.get('start'), value.get('type'), capa, a.get('end')) not in posturas for a in aristas)
    if props.get('source_kind') == 'acusacion' and efectivas is not None:
        if any(_declarada_por_identidad(a, efectivas) for a in aristas):
            return False
        if accion == 'deny':
            return True
        if accion == 'retype' and op.get('new_type') and not carga(op):
            return any(_declarada_por_identidad({**a, 'type': op['new_type']}, efectivas) for a in aristas) or None
    return None


def _por_regla(op, value, contexto):
    """Si una regla del overlay materializa la operación vigente `op` (deny/retype/invert o una corrección de props,
    set_prop) sobre la arista `value` del censo (spec 071 AC48, AC58 y AC79): con su tipo, su carga (`_carga_coincide`)
    y el predicado exacto del loader (`alcance.alcanza_loader`; el de una corrección, el de la fase `set`) sobre la
    arista con la dirección que guarda el censo, también en las simétricas: el colapso deja sueltas las inversas como
    estaban, y el apply escribe así su regla (AC60)."""
    (deny, fix), _, _ = contexto
    accion = op.get('action')
    predicado = 'set' if accion in CORRECCIONES else accion
    return any(regla.get('type') == value.get('type') and _carga_coincide(regla, op)
               and alcanza_loader(regla, value, predicado) for _, regla in (deny if accion == 'deny' else fix))


def _materializada(op, value, contexto):
    """Si la capa que la recrea (`_por_capa`: una retirada por su ausencia; un retype, si la capa declara su resultado,
    AC111) o una regla del overlay (`_por_regla`) materializan ya la operación vigente `op` (deny/retype/invert) sobre
    la arista `value` del censo; una corrección de props (set_prop), solo su regla (AC79): que su capa ya no la declare
    no la corrige. Una fila del registro nunca basta (AC47): la contrasta `materializacion` con el export."""
    capa = None if op.get('action') in CORRECCIONES else _por_capa(op, value, contexto)
    return _por_regla(op, value, contexto) if capa is None else capa


def sin_materializar(conn, decisions, contexto):
    """[(ref, unit_id, pack_id, value, op)]: las unidades cuya operación vigente (AC46, `alcance.vigentes`) es
    deny/retype/invert o una corrección de props (set_prop, AC79; el set_status no: ver `materializacion`) y que nada
    materializa en el repo (`_materializada`), con el valor del censo de cada arista. Es lo que mira `record`, que no
    tiene el export; la puerta, `materializacion`, contrasta además cada una con él. La arista, con su extremo Claim
    recuperado del censo (`registro_check.con_afirmacion`, AC104)."""
    from scripts import registro_check as RC
    out = []
    for uid, (decision, op) in vigentes(decisions).items():
        if (op.get('action') or 'keep') not in (*ACCIONES_REGLA, *CORRECCIONES):
            continue
        row = conn.execute('SELECT kind, value FROM units WHERE unit_id=?', (uid,)).fetchone()
        if row is None or row['kind'] != 'edge':
            continue
        value = RC.con_afirmacion(conn, uid, json.loads(row['value']))
        if not _materializada(op, value, contexto):
            out.append((decision.get('_file') or '?', uid, (decision.get('_meta') or {}).get('pack_id'), value, op))
    return out


def _posterior(decision, fecha):
    """Si la decisión es humana y de un día posterior a `fecha`, la de la última fila «suprimido» de su unidad (spec 071
    AC70). Las filas solo llevan el día: a igual día no se sabe cuál fue antes, y no cuenta."""
    if not fecha:
        return False
    familia, instante = precedencia(decision)
    return familia == 2 and instante.date().isoformat() > fecha


def materializacion(root, repo, snapshot):
    """AC43/AC47/AC53/AC57–AC60, en la puerta del Path A: la operación vigente (AC46) de cada arista de un pack `applied`
    que dice deny/retype/invert se contrasta con el export, tenga regla o no: la regla prepara, el export prueba. La
    operación vigente se resuelve una vez, sobre todas las decisiones, como en el registro, y la cobertura de packs va
    después: la vigente de un pack no aplicado no se contrasta todavía ni devuelve la que sustituye («operación vigente
    en pack no aplicado», informativo, AC99). Una decisión de un pack que no está en el censo o que no contiene su
    unidad no vale (`validar_packs`, exit 2, AC106), y un pack o una unidad solo se nombran con su forma. Las
    copias vivas que dicen que no está hecha son las de la firma del censo y las de los estados a los que la llevan las
    reglas `fix` que la alcanzan, compuestas como el loader (de otras decisiones y del overlay, AC51 y AC57), menos la
    tolerancia de las conservadas que nombra la decisión (con dirección, AC52, como en `registro_check`); en un retype,
    con el tipo del censo, también con la capa que deja la carga (AC84), o con el nuevo al revés (AC71), y en un
    invert, en su sentido del censo; la tolerancia solo exime las incompatibles, nunca una en el estado decidido sin su
    carga (AC96). Una acusación de la capa `acusacion`, una arista de prensa, de última hora o de contexto (con
    documento o sin él) y una postura de claims.yaml cuentan por la identidad de su cargador, con cualquier cita (AC59,
    AC101 y AC110; la de una postura lleva su afirmación, recuperada del censo, y sin ella es «postura indeterminada»,
    un error: AC104), y las de YAML siguen vivas mientras su YAML las declare (AC59), también en sus pares actuales si
    un extremo se re-identificó (AC68). Una retirada se busca también en los pares que el overlay declara de la arista
    con una escisión o un merge, aunque sus nodos sigan (AC109). Con copias vivas,
    «retirada decidida sin materializar», haya regla o fila. Sin ellas la acreditan la capa que ya no la declara, solo
    a una retirada (una postura de claims.yaml, además con su fila «suprimido»: AC62; un retype o un invert necesita su
    resultado en el export, AC111) o una regla que hace exactamente sus fases con el
    predicado del loader (AC58 y AC71; un retype o un invert sin copia en el estado decidido y sin fila «rectificado»
    queda como informativo), o la fila vigente del registro con su `unidad` exacta (AC69: la historia no acredita):
    «suprimido» acredita una retirada (la que contradice una conservación la cuenta AC93, abajo); «rectificado» de un
    retype, un invert o un set_prop solo con una copia en el estado decidido, y si no, «rectificación decidida sin
    arista corregida», un error, haya regla o no, con la misma condición que el registro
    (`registro_check.sin_arista_corregida`, AC92 y AC100; detrás de las copias vivas y de las rectificaciones sin
    materializar); la «rectificado» que completa una «suprimido» sin la copia resultante desde el documento que cita,
    «rectificación decidida sin materializar»; si nada, «sin copias (sin fila)», informativo.
    Una conservación humana posterior a la última «suprimido» de su arista exige el hecho publicado en su sentido
    (AC70). Un retype o un invert con mapa `set` no está hecho si alguna copia en el estado decidido no lleva esa carga
    tal como la escribe el loader, con sus props derivadas, aunque otra la lleve (AC74, AC76 y AC77: «rectificación
    decidida sin materializar»), también la que conserva la capa del censo (AC80); re-identificado, la del tipo nuevo
    al revés o sin ella en su par actual sigue viva (AC73). Las copias se clasifican una vez, igual que en el registro
    (`registro_check.clasificar`, AC96), en el par del censo y en los actuales, con la misma identificación
    (`registro_check.copias_identificadas`, AC85): una de una capa que su cargador recrea por su identidad o una
    postura, por esa identidad, con cualquier documento y cita (AC81 y AC101); el resto, por documento y cita
    (normalizada con NFKC, AC107), con cualquier capa, también la de un estado incompatible (AC105). Las de un retype,
    un invert o un set_prop se buscan siempre, además, por documento y cita en todo el export, con el criterio de
    identidad de `registro_check.copias_libres` (comparte exactamente un extremo, en el sentido que da su posición, y no
    es otra arista del censo de su firma, censal o resultante, con su dirección: AC102): una del tipo antiguo o
    del estado decidido sin su carga, «(par distinto del de la regla; …)» (AC89); una correcta se acepta; con copia en
    sus pares, solo la de un par que el overlay declara suyo (una escisión o un merge, o una regla con su etiqueta), y
    las demás, otros hechos de su pasaje, son un aviso («copia del mismo pasaje en otro par, hecho no atribuido», AC94).
    La que también puede ser otra arista del censo de su firma con un extremo fuera del export (con un estado alcanzable
    de su tipo y su sentido) no se atribuye a ninguna: «copia ambigua entre unidades», un error, como en el registro
    (AC91 y AC98); tampoco la de un par actual que solo deduce la firma de una regla sin etiqueta: la etiqueta de una
    regla es de su unidad (AC103). Una arista con un extremo del censo que ya no está en el export se evalúa por su
    firma (`_por_firma`; acreditada solo por una copia de otro par, el aviso lo dice). Una corrección de props decidida
    (set_prop) se prueba igual en el export (AC79): cada copia de la arista lleva su carga efectiva; si alguna no,
    «corrección decidida sin materializar», haya regla o fila o no, como la cuenta el registro (AC83), con la misma
    búsqueda libre («(par distinto del de la regla; …)», AC86); sin ninguna copia, informativo, salvo si su fila
    «rectificado» afirma la corrección (AC100, arriba) o completa una «suprimido» y la arista no está publicada como la
    cuenta el registro (`registro_check._sigue_publicada`, que también ve la copia de la búsqueda libre: AC97):
    «corrección decidida sin copias; la fila afirma que sigue publicada», un error (AC90). El set_status queda fuera a
    conciencia: la capa `acusacion` recrea y escala el estado procesal desde accusations.yaml después de la decisión, y
    el export no la prueba (spec 071 enmienda 16; un seguimiento de datos, no de este check).
    Una fila vigente «suprimido» sobre cualquier arista cuya operación vigente la conserva (un retype, un invert, un
    set_prop o un keep, también anterior a la fila) contradice la decisión, como la cuenta el registro (AC93):
    «conservación decidida, arista retirada por regla ajena (<regla> [<unidad>])», con las reglas del overlay sin su
    etiqueta que alcanzan su arista censal con el predicado del loader, o «…, sin regla que la explique».
    La fila «suprimido» de un deny con una copia viva del mismo par orientado, tipo, documento y cita en otra capa,
    salvo que lo declare con la fórmula de la copia duplicada, y la que lleva una fórmula de asociación publicada que no
    es literalmente verdad tras el overlay (la copia duplicada sin una arista de su tipo, sentido y documento; la de
    AC125 sin su retipo alcanzable por una regla o sin su copia; la de AC142 sin una arista viva del tipo que dice) son
    errores con el mismo texto que en el registro (`registro_check.copias_en_otra_capa` y `motivo_publicada`, spec 071
    AC119, AC125, AC136, AC142 y D1). Las reglas deny opacas se resuelven contra la raíz de la auditoría, como el
    loader, con su fichero privado y exactamente sus declaraciones (AC114, AC121 y AC127), y la forma del anonimato del
    registro se comprueba como en `--tree` y `--snapshot` (`registro_check.anonimato`, AC149) y, con el censo, que
    ningún texto público ni del export reconstruya una retirada anonimizada, con el mismo resultado que `--snapshot`
    (`registro_check.anonimato_con_censo`, AC169): «anonimato del registro: …», un error; sus avisos, avisos.
    Además, cada regla del overlay etiquetada tiene que salir de la operación vigente de su unidad y hacer lo mismo
    (AC54, AC58, AC71). La arista de una unidad cuya cita reescribe una migración del Path A se identifica también por
    la cita de después que declara la raíz (`registro_check.migraciones_citas`, AC284–AC287), y solo ella; una
    declaración que no vale no cuenta y es un error con el motivo del registro (AC286). Exit 0, 1 con problemas, 2 si
    el export, el overlay, el registro o una decisión no valen (también con una carga que el loader descarta, AC78, una
    clave repetida en un YAML, AC108, o una resolución fuera de su familia, AC120) o ante un error interno, que sale con
    su clase y nunca con un valor (AC49, AC55)."""
    try:
        return _materializacion(root, repo, snapshot)
    except Exception as exc:  # un SystemExit (overlay, registro o decisión que no valen) sale tal cual
        print(f'materializacion: error interno ({type(exc).__name__})', file=sys.stderr)
        return 2


def _materializacion(root, repo, snapshot):
    from scripts import registro_check as RC
    registro = Path(repo) / REG_PATH
    leidas = (RC.cargar(registro) or []) if registro.exists() else []
    filas = [f for f in leidas if isinstance(f, dict)]
    # La fila vigente de cada unidad (AC67/AC69: la historia no acredita) y el día de su última «suprimido» (AC70).
    vigentes_filas, suprimida = RC.vigentes_por_unidad(filas), {}
    for fila in filas:
        if fila.get('unidad') and fila.get('decision') == 'suprimido':
            suprimida[fila['unidad']] = max(suprimida.get(fila['unidad'], ''), str(fila.get('fecha') or ''))
    census = sqlite3.connect((Path(root) / 'census.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    census.row_factory = sqlite3.Row
    try:
        aplicados = {r[0] for r in census.execute("SELECT pack_id FROM esc_packs WHERE state='applied'")}
        # AC99: la operación vigente se resuelve una vez, sobre todas las decisiones, como en el registro; la cobertura
        # de packs va después y nunca devuelve la operación que esa sustituye. Una decisión de un pack que no existe o
        # que no contiene su unidad no vale (AC106).
        todas = load_decisions(root)
        en_vigor = vigentes(todas)
        validar_resoluciones(todas)
        validar_packs(census, todas)
        contexto = _contexto_materializacion(repo, root)
        # AC284–AC287: la arista de una unidad con una migración de cita declarada lleva su firma de después, como en el
        # registro; una declaración que no vale no cuenta y es un problema, con el mismo motivo.
        migradas, no_validas = RC.migraciones_citas(root, census)
        candidatas, sin_aplicar = [], []
        for uid, (decision, op) in en_vigor.items():
            accion = op.get('action') or 'keep'
            if accion not in (*ACCIONES_REGLA, *CORRECCIONES) \
                    and not (accion == 'keep' and _posterior(decision, suprimida.get(uid))):
                continue
            row = census.execute('SELECT kind, value FROM units WHERE unit_id=?', (uid,)).fetchone()
            if row is None or row['kind'] != 'edge':
                continue
            pack = (decision.get('_meta') or {}).get('pack_id')
            etiqueta = nombre_de_pack(pack, decision.get('_file') or '?')
            if pack in aplicados:
                candidatas.append((uid, etiqueta, RC.con_migracion(uid, RC.con_afirmacion(
                    census, uid, json.loads(row['value'])), migradas), {**op, 'action': accion}))
            else:
                sin_aplicar.append(('operación vigente en pack no aplicado', uid, etiqueta))
        porque, acciones, sets, retipos = RC.decisiones(root, {uid for uid, *_ in candidatas})
        trans = RC.transformadoras(census, acciones, sets, retipos)
        valores = RC.aristas_censales(census, migradas)
        paquetes = defaultdict(set)
        for entidad, unit_ids in census.execute('SELECT entity, unit_ids FROM esc_packs'):
            paquetes[entidad].update(json.loads(unit_ids or '[]'))
        nombradas = {uid: RC.conservadas(census, paquetes, uid, *porque.get(uid, ('', None)))[0]
                     for uid, *_ in candidatas}
    finally:
        census.close()
    try:
        index, nodos = RC.export_vivo(snapshot)
    except RC.ExportInvalido as exc:
        print(f'materializacion: el export no vale ({exc})')
        return 2
    errores, avisos, por_firma = _contraste_reglas(root, repo, vivo=(index, nodos), cobertura=True)
    reglas_fix = [regla for _, regla in contexto[0][1]]
    overlay = [regla for _, regla in contexto[0][0]] + reglas_fix
    # Lo que la búsqueda libre sabe de cada arista: las formas de las otras aristas del censo de su firma (AC89), las
    # que, con un extremo fuera del export, pueden hacer ambigua una copia (AC91 y AC98) y los pares que el overlay
    # declara suyos (AC94).
    ajenas = RC.formas_ajenas(valores, trans, acciones, sets, retipos, reglas_fix, nodos, overlay,
                              _reidentificaciones(repo))
    # Las aristas del censo por par (AC119: la copia de otra capa que explica otra del par es suya) y lo que el grafo
    # publica tras el overlay (AC125), como en el registro.
    por_par = defaultdict(list)
    for otra, otro_value in valores.items():
        por_par[frozenset((otro_value.get('start'), otro_value.get('end')))].append((otra, otro_value))
    publicado = RC.tras_el_overlay(index, [regla for _, regla in contexto[0][0]], reglas_fix)
    vivas, sin_hacer, sin_corregir, ausentes, sin_fila = [], [], [], [], []
    informativos, ambiguas, sin_arista, indeterminadas, otra_capa = list(sin_aplicar), [], [], [], []
    sin_publicada = []
    for uid, pack, value, op in candidatas:
        accion = op['action']
        if RC.indeterminada(value):
            indeterminadas.append((uid, pack))  # AC104: sin su afirmación, cualquier postura de su titular pasaría
            continue
        # Con un extremo del censo fuera del export (re-identificado después del censo), las fases se componen también
        # sobre sus pares actuales (AC63) y las copias se buscan por su firma en cualquier par (AC61); en un par que
        # solo deduce la firma de una regla sin etiqueta, una copia que puede ser de otra unidad no se atribuye (AC103).
        reidentificada = bool({value.get('start'), value.get('end')} - {None} - nodos)
        actuales, inferidos = RC.pares_y_origen(uid, value, nodos, overlay) if reidentificada \
            else (None, frozenset())
        _, fila, anteriores = vigentes_filas.get(uid, (None, None, []))
        completa = bool(fila) and fila.get('decision') == 'rectificado' and 'suprimido' in anteriores
        mias = ajenas.get(uid, RC.SIN_AJENAS)
        if accion == 'keep':
            # AC70: una conservación humana posterior a una supresión deja el hecho publicado en su sentido; desde el
            # documento que cita la «rectificado» que completa la supresión, si la hay (AC69). La «suprimido» cita la
            # fuente de lo que se retiró, no la de lo que se conserva.
            if not RC._sigue_publicada(index, value, fila if completa else None, actuales, ajenas=mias,
                                       inferidos=inferidos):
                sin_hacer.append((uid, pack, False))
            continue
        posibles = set() if accion in CORRECCIONES else RC.firmas_posibles(
            uid, value, trans, acciones, sets, retipos, reglas_fix, nodos=nodos, overlay=overlay)
        # AC96: las copias de la arista, clasificadas una vez y como en el registro.
        copias = RC.clasificar(index, value, accion, op.get('new_type'), sets.get(uid), posibles, actuales, mias,
                               inferidos)
        if accion == 'deny' and fila and fila.get('decision') == 'suprimido':
            # AC119: la misma asociación, con la misma fuente, sigue publicada por otra capa; la fila lo declara con su
            # fórmula, o no dice la verdad; con la misma condición que el registro.
            motivo = RC.motivo_otra_capa(fila, RC.copias_en_otra_capa(
                uid, value, index, posibles, por_par, trans, acciones, sets, retipos, reglas_fix, nodos, overlay))
            if motivo:
                otra_capa.append((uid, pack, motivo))
            # AC125: la fila dice que otra arista del mismo documento sigue publicando su asociación; el grafo, tras el
            # overlay, también.
            motivo = RC.motivo_publicada(fila, value, publicado, reglas_fix)
            if motivo:
                sin_publicada.append((uid, pack, motivo))
        if copias.ambiguas:
            ambiguas.append((uid, pack, copias.ambiguas))  # AC91: no se atribuye a ninguna; se resuelve a mano
        if copias.no_atribuidas:
            informativos.append((RC.NO_ATRIBUIDA, uid, pack))  # AC94: otro hecho de su pasaje, no suyo
        if accion in CORRECCIONES:
            # AC79: cada copia de la arista lleva la carga que escribe el loader (derivadas incluidas), haya regla o
            # fila o no: la regla prepara, el export prueba. Una de una capa que su cargador recrea por su identidad o
            # una postura, con cualquier documento y cita (AC81 y AC101), y con la capa de antes o de después de la
            # carga (AC80); las mismas que cuenta el registro (AC83), también las de un par distinto del de la regla
            # (AC86 y AC89). Sin ninguna, una «rectificado» afirma una corrección que no se publica (AC100).
            if completa and not RC._sigue_publicada(index, value, fila, actuales, accion, carga=sets.get(uid),
                                                    ajenas=mias, inferidos=inferidos):
                ausentes.append((uid, pack))  # AC90: la misma condición que el «ausente» del registro
            elif not copias.ambiguas and RC.sin_arista_corregida(fila, anteriores, accion, copias):
                sin_arista.append((uid, pack))
            elif not (copias.decididas or copias.sin_carga or copias.ambiguas):
                informativos.append(('corrección decidida sin copias', uid, pack))
            if copias.sin_carga:
                sin_corregir.append((uid, pack, any(fuera for _, fuera in copias.sin_carga)))
            continue
        # AC96: la tolerancia solo exime copias incompatibles; una del estado decidido sin la carga de su `set` (AC74,
        # AC76 y AC77) no la exime ninguna: re-identificada, sigue viva como la del tipo nuevo al revés (AC73); si no,
        # deja la rectificación sin materializar, haya regla, fila u otra copia que la lleve.
        sin_carga = copias.sin_carga
        tolera = RC.tolerancia([clave for clave, _ in copias.incompatibles], RC.legitimas(
            uid, value, nombradas[uid], acciones, trans, sets, retipos, por_firma=reidentificada))
        vivas_aqui = copias.incompatibles + (sin_carga if reidentificada else [])
        # Las copias en el estado decidido, con los props de su `set` (AC74) y nunca ampliado con la regla que se valida
        # (AC71); las de una «rectificado» que completa una «suprimido», desde el documento que cita (AC69).
        docs = RC.documentos(fila) if completa else ()
        hechas = [clave for clave, props, _ in copias.decididas if not docs or props.get('source_doc') in docs]
        incompleta = bool(sin_carga)
        # AC92: la «rectificado» afirma la arista corregida; con la misma condición que el registro.
        sin_corregida = RC.sin_arista_corregida(fila, anteriores, accion, copias)
        if uid in por_firma and len(copias.incompatibles) <= tolera and not (completa or incompleta or sin_corregida):
            continue  # la acredita su firma sobre el par de la regla (aviso) y no le queda ninguna copia incompatible
        capa = _por_capa(op, value, contexto, actuales)
        postura = value.get('type') in CLAIM_STANCES
        if capa is False or len(copias.incompatibles) > tolera or (reidentificada and sin_carga):
            # La capa la sigue declarando (AC59) o sigue viva: ni regla ni fila la cubren.
            vivas.append((uid, pack, any(fuera for _, fuera in vivas_aqui)))
        elif accion != 'deny' and (incompleta or (completa and not hechas)):
            # Una copia sin su set (AC74, AC76) o sin la resultante de la fila (AC69).
            sin_hacer.append((uid, pack, any(fuera for _, fuera in sin_carga)))
        elif copias.ambiguas:
            continue  # la copia ambigua ya cuenta y es otro problema de copias: AC92 no se suma
        elif sin_corregida:
            sin_arista.append((uid, pack))  # detrás de las copias vivas y de las rectificaciones sin materializar
        elif capa and not postura and accion == 'deny':
            # La capa ya no la declara: una retirada hecha; a una postura le hace falta además su fila «suprimido»
            # (AC62). Un retype o un invert necesita su resultado en el export, como cualquiera (AC111).
            continue
        elif not reidentificada and _por_regla(op, value, contexto):
            if accion != 'deny' and not hechas:
                informativos.append(('retype sin copia bajo el tipo decidido' if accion == 'retype'
                                     else 'invert sin copia en el sentido decidido', uid, pack))
        elif fila and fila.get('decision') == 'suprimido':
            continue  # una retirada, o una conservación que la fila contradice (AC93, abajo)
        elif accion != 'deny' and fila and fila.get('decision') == 'rectificado' \
                and str(fila.get('dato') or '').endswith('arista corregida'):
            continue  # su fila y su copia en el estado decidido (sin ella, AC92)
        else:
            sin_fila.append((uid, pack))
    # AC93: una «suprimido» vigente sobre una arista cuya operación vigente la conserva contradice la decisión (la misma
    # condición que el registro, sobre todas las aristas, también la de un keep anterior a la fila); las reglas del
    # overlay sin su etiqueta que alcanzan su arista censal con el predicado del loader dicen quién la retiró.
    (reglas_deny, reglas_de_fix), _, _ = contexto
    contradicen = []
    for uid, (_, fila, _) in vigentes_filas.items():
        decision, op = en_vigor.get(uid, (None, None))
        if fila.get('decision') != 'suprimido' or uid not in valores or op is None \
                or (op.get('action') or 'keep') not in RC.CONSERVAN:
            continue
        reglas_ajenas = [origen + ''.join(f' [{m}]' for m in _TAG.findall(regla.get('reason') or ''))
                         for seccion, reglas in (('deny', reglas_deny), ('fix', reglas_de_fix))
                         for origen, regla in reglas
                         if uid[:8] not in _TAG.findall(regla.get('reason') or '')
                         and alcanza_loader(regla, valores[uid], _clase(seccion, regla))]
        contradicen.append((uid, nombre_de_pack((decision.get('_meta') or {}).get('pack_id'),
                                               decision.get('_file') or '?'), reglas_ajenas))
    for uid, pack, fuera in vivas:
        print(f'MATERIALIZACION: retirada decidida sin materializar ({RC.OTRO_PAR if fuera else ""}{uid[:8]}, {pack})')
    for uid, pack, fuera in sin_hacer:
        print(f'MATERIALIZACION: rectificación decidida sin materializar ({RC.OTRO_PAR if fuera else ""}{uid[:8]}, '
              f'{pack})')
    for uid, pack in sin_arista:
        print(f'MATERIALIZACION: {RC.SIN_CORREGIDA} ({uid[:8]}, {pack})')
    for uid, pack, fuera in sin_corregir:
        print(f'MATERIALIZACION: corrección decidida sin materializar ({RC.OTRO_PAR if fuera else ""}{uid[:8]}, '
              f'{pack})')
    for uid, pack in ausentes:
        print(f'MATERIALIZACION: corrección decidida sin copias; la fila afirma que sigue publicada ({uid[:8]}, '
              f'{pack})')
    for uid, pack, otras in ambiguas:
        print(f'MATERIALIZACION: {RC.AMBIGUA} ({", ".join(huella(u, "?") for u in (uid, *otras))}; {pack})')
    for uid, pack in indeterminadas:
        print(f'MATERIALIZACION: {RC.INDETERMINADA} ({uid[:8]}, {pack})')
    for uid, pack, motivo in otra_capa + sin_publicada:
        print(f'MATERIALIZACION: {motivo} ({uid[:8]}, {pack})')
    for uid, pack, reglas in contradicen:
        porque = f' por regla ajena ({", ".join(reglas)}; ' if reglas else ', sin regla que la explique ('
        print(f'MATERIALIZACION: conservación decidida, arista retirada{porque}{uid[:8]}, {pack})')
    for motivo in errores:
        print(f'MATERIALIZACION: {motivo}')
    for motivo in no_validas:
        print(f'MATERIALIZACION: {RC.MIGRACION_NO_VALIDA}: {motivo} ({RC.MIGRACIONES_CITAS})')
    # AC149 y AC169: la puerta del Path A comprueba también el anonimato del registro: su forma, como `--tree`, y, con
    # el censo, que ningún texto público ni del export reconstruya una retirada anonimizada, con el mismo resultado que
    # `registro_check --snapshot` (sus problemas, sin repetir los de la forma; sus avisos, aparte).
    try:
        opacas = Opacas(root)
    except ReglaOpacaInvalida as exc:
        print(f'materializacion: las reglas opacas no se resuelven ({exc})', file=sys.stderr)
        return 2
    reconstruidas, avisos_anonimato = RC.anonimato_con_censo(leidas, valores, opacas.privadas, repo, snapshot,
                                                             publicado, RC.nombres_del_export(snapshot), opacas.filas,
                                                             referencias_privadas=opacas.referencias)
    anonimato = reconstruidas + [p for p in RC.anonimato(leidas, repo, registro) if p not in reconstruidas]
    # AC183 y AC184: las «suprimido» sin unidad, por su declaración de `filas:` (con marcador) o por la forma de su
    # dato y las reglas públicas (sin él), con la misma comprobación que `registro_check --snapshot --overlay`.
    _, declaradas = RC.retiradas_sin_unidad(leidas, RC.preparadas(opacas.filas), [r for _, r in contexto[0][0]],
                                            index, publicado, reglas_fix)
    for rid, motivo in declaradas:
        print(f'MATERIALIZACION: {motivo} ({rid})')
    # AC199: la identidad anterior de un nodo cuya identificación se retira, en ningún fichero de datos ni en el export.
    censo_ro = sqlite3.connect((Path(root) / 'census.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        identidades, avisos_identidad = RC.identidades_retiradas(leidas, censo_ro, repo, snapshot)
    finally:
        censo_ro.close()
    for rid, motivo in identidades:
        print(f'MATERIALIZACION: {motivo} ({rid})')
    for rid, motivo in anonimato:
        print(f'MATERIALIZACION: anonimato del registro: {motivo} ({rid})')
    for motivo in avisos:
        print(f'materializacion: {motivo}')
    for rid, aviso in avisos_anonimato:
        print(f'materializacion: anonimato del registro: {aviso} ({rid})')
    for rid, aviso in avisos_identidad:
        print(f'materializacion: {aviso} ({rid})')
    for motivo, uid, pack in informativos:
        print(f'materializacion: {motivo} ({uid[:8]}, {pack})')
    for uid, pack in sin_fila:
        print(f'materializacion: sin copias (sin fila) ({uid[:8]}, {pack})')
    conservaciones = sum(1 for *_, op in candidatas if op['action'] == 'keep')
    correcciones = sum(1 for *_, op in candidatas if op['action'] in CORRECCIONES)
    sin_materializar_props = {uid for uid, *_ in sin_corregir} | {uid for uid, _ in ausentes}
    print(f'materializacion: {len(candidatas) - conservaciones - correcciones} operaciones vigentes de arista '
          f'deny/retype/invert en {len(aplicados)} packs aplicados; sin materializar: {len(vivas)} con copias vivas, '
          f'{len(sin_fila)} sin copias (sin fila); rectificaciones sin materializar: {len(sin_hacer)} (con '
          f'{conservaciones} conservaciones humanas tras una «suprimido»); correcciones de props sin materializar: '
          f'{len(sin_materializar_props)} de {correcciones}; rectificaciones sin arista corregida: {len(sin_arista)}; '
          f'copias ambiguas entre unidades: {len(ambiguas)}; posturas indeterminadas: {len(indeterminadas)}; filas '
          f'«suprimido» que contradicen una conservación: {len(contradicen)}; con la asociación en otra capa: '
          f'{len(otra_capa)}; con la fórmula de otra arista publicada sin ella: {len(sin_publicada)}; reglas sin '
          f'decisión vigente o incompatibles: {len(errores)}; retiradas sin unidad sin materializar: '
          f'{len(declaradas)}; identidades retiradas publicadas: {len(identidades)}; anonimato del registro: '
          f'{len(anonimato)}; migraciones de cita declaradas no válidas: {len(no_validas)}; avisos: '
          f'{len(avisos) + len(informativos) + len(avisos_anonimato) + len(avisos_identidad)}')
    return 1 if vivas or sin_hacer or sin_corregir or ausentes or sin_arista or ambiguas or indeterminadas \
        or otra_capa or sin_publicada or contradicen or errores or declaradas or identidades or anonimato \
        or no_validas else 0


def _declarada(source, target, doc, rtype, quote, efectivas):
    """Si la capa de acusaciones genera la arista `source -[rtype]-> target`, por su IDENTIDAD (spec 071 AC276): persona +
    delito + tipo en TODAS las entradas efectivas, la clave del MERGE de `accusations_loader`; otra entrada, con otro
    documento, recrea la misma arista, así que el documento no cuenta. El documento y la cita solo resuelven un destino
    que ninguna efectiva identifica por su delito: entre las de la misma persona y documento, la cita cuenta si señala
    exactamente una."""
    mismas = [e for e in efectivas if e['person_id'] == source]
    casan = [e for e in mismas if e['crime_id'] == target]
    if not casan:
        cita = _norm_prose(quote).casefold()
        por_cita = [e for e in mismas if (e['doc'] or None) == doc and cita
                    and cita in _norm_prose(e['quote']).casefold()]
        casan = por_cita if len(por_cita) == 1 else []
    return any(e['rel_type'] == rtype for e in casan)


def _load_check(rows, raiz=None):
    """Cada fila de overlay pasa por el `_prepare_*` del loader; la que se cae es un error. El error la nombra por su
    sección, su posición en el lote y su unidad si la lleva (la marca `[hash8]` de su motivo), nunca por sus extremos,
    su tipo ni otro valor (spec 071 AC87); una que el loader rechaza con una excepción (un `kinds` desconocido es un
    SystemExit con un extremo) cuenta igual, por su clase; una deny opaca se resuelve contra la raíz privada `raiz`
    (AC114). Los logs del loader, que registran la fila entera, callan. Spec 070 (AC144, AC147): la que no aplicaría
    entera le para (`ErrorDeForma`), y el error lleva el motivo de su forma, que no cita valores."""
    errors = _claims_check(rows.get('claims') or [])
    previo, relations_loader.log = relations_loader.log, _SinLog()
    try:
        for seccion, preparar in (('deny', lambda filas: relations_loader._prepare_deny(filas, raiz)),
                                  ('fix', relations_loader._prepare_fix),
                                  ('alias_deny', relations_loader._prepare_alias_deny)):
            for n, row in enumerate(rows[seccion], 1):
                try:
                    motivo = None if preparar([row]) else 'el loader descarta la fila'
                except (SystemExit, Exception) as exc:   # la parada del 070 (`ErrorDeForma`) es un SystemExit
                    motivo = f'el loader rechaza la fila ({_clase_neutra(exc)})'
                if motivo:
                    marca = ''.join(f' [{m}]' for m in _TAG.findall(str(row.get('reason') or ''))[:1])
                    errors.append(f'{seccion}[{n}]{marca}: {motivo}')
    finally:
        relations_loader.log = previo
    return errors


# --- materialización ---------------------------------------------------------------------------

def _dato(unit):
    if not unit:
        return ''
    if unit.get('kind') == 'edge':
        edge = unit.get('edge') or {}
        text = f'({edge.get("start_entity")}) -[{edge.get("type")}]-> ({edge.get("end_entity")})'
        quote = str(edge.get('source_quote') or '').strip()
        return _clip(f'{text}: «{quote}»' if quote else text)
    value = unit.get('value')
    return _clip(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))


# Una `evidence` con forma de referencia documental: el PDF y, si acaso, su página o páginas (numéricas).
_REFERENCIA_DOC = re.compile(r'([A-Za-z0-9_.-]+\.pdf)(?: p\.[0-9]+| pp\.[0-9]+-[0-9]+)?')
_PAGINA = re.compile(r'[0-9]+')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_KIND_WORD = {'summary': ('frase del resumen', 'a'), 'attribute': ('rol', 'o'), 'identity': ('alias', 'o'),
              'node': ('dato de ficha', 'o')}
# Un extremo Location:/Event: con dígitos puede ser un domicilio (calle y número), una referencia
# catastral, una matrícula o un teléfono: el registro no lo reproduce (revisión Codex del PR #86).
_DATO_PERSONAL = re.compile(r'^(Location|Event):.*\d')
# Sin dígitos, un bien particular (vivienda, vehículo, hipoteca, línea) tampoco se reproduce: el
# registro diría «X era dueño de / pagó tal bien» justo del dato que se retiró. Lo mismo con el
# extremo de una arista OWNS hacia un lugar o evento.
_BIEN_PERSONAL = re.compile(r'^(Location|Event):.*\b(chalet|piso|vivienda|casa|finca|inmueble|apartamento|'
                            r'hipoteca|alquiler|vehiculo|coche|moto|matricula|tarjeta-sim|telefono|domicilio)')
_ENTIDAD = re.compile(r'^[A-Z][A-Za-z]*:')


def _extremo_registro(end, entity, rel_type=None, seudos=frozenset()):
    """El extremo tal como lo publica el registro. `seudos` = los seudónimos judiciales declarados: junto
    a uno de ellos la sociedad, el organismo o el alias es justo lo que lo reidentificaría (spec 070)."""
    end = str(end or '')
    if end == entity:
        return end
    if end.startswith('Person:'):
        return 'Person:[tercero]'
    if entity in seudos:
        if end.startswith('Company:'):
            return 'Company:[sociedad]'
        if end.startswith('Organization:'):
            return 'Organization:[organismo]'
        if end and not _ENTIDAD.match(end):
            return '[alias]'
    if _DATO_PERSONAL.match(end):
        return f'{end.split(":", 1)[0]}:[dato personal]'
    if _BIEN_PERSONAL.match(end) or (rel_type == 'OWNS' and end.startswith(('Location:', 'Event:'))):
        return f'{end.split(":", 1)[0]}:[bien personal]'
    return end


def _dato_registro(unit, entity, suprimido, seudos=frozenset(), nombrada=False):
    """El registro va a un repo público: NO reproduce el dato erróneo o retirado (sería volver a
    publicarlo), solo qué clase de dato se tocó y de quién; los terceros de una arista retirada quedan
    como «[tercero]», sin cita ni importes (revisión Codex 063, R-2026-09-24-0043), los extremos que
    pueden ser datos personales (domicilio, vehículo, teléfono) como «[dato personal]» y, en la fila de
    un seudónimo judicial, la sociedad u organismo como «[sociedad]»/«[organismo]» (spec 070). Solo una
    retirada oculta un extremo: una arista corregida sigue publicada en el grafo y su fila la nombra
    entera (spec 071 AC113). En la fila de un seudónimo judicial, solo si su par ya lo publica otra cosa (`nombrada`,
    principio P de la fusión 070 ⟷ 071): la regla `fix` del plan que la corrige (pública, con el par y la huella de su
    unidad) o una entrada de la lista de permitidas resultante con los mismos seudónimo y otro extremo
    (`_nombra_las_de_la_lista`); si no, no lo empareja con nadie (spec 070, AC1)."""
    if not unit:
        return ''
    if unit.get('kind') == 'edge':
        edge = unit.get('edge') or {}
        oculta = not nombrada and (suprimido or entity in seudos)
        ends = [_extremo_registro(x, entity, edge.get('type'), seudos) if oculta
                else str(x or '') for x in (edge.get('start_entity'), edge.get('end_entity'))]
        return (f'({ends[0]}) -[{_tipo_impreso(edge.get("type"))}]-> ({ends[1]}): '
                f'arista {"retirada" if suprimido else "corregida"}')
    word, g = _KIND_WORD.get(unit.get('kind'), (unit.get('kind') or 'dato', 'o'))
    return f'{word} {"retirad" if suprimido else "corregid"}{g}'


def _anonimiza(unit, entity, seudos=frozenset()):
    """Si la fila de la retirada de la arista `unit` oculta alguno de sus extremos (`_dato_registro`): su regla deny
    tiene que ser opaca, que no lo nombre (spec 071 AC114)."""
    edge = (unit or {}).get('edge') or {}
    return (unit or {}).get('kind') == 'edge' and any(
        _extremo_registro(x, entity, edge.get('type'), seudos) != str(x or '')
        for x in (edge.get('start_entity'), edge.get('end_entity')))


# El motivo público de una regla deny opaca: el de la decisión puede nombrar a quien la fila oculta, y se queda en ella.
MOTIVO_OPACO = MOTIVO_AJENA


def _fuente_registro(unit, evidence, upd, publicos):
    """(fuente pública, referencia descartada) de una fila del registro. Solo se publica una referencia acreditada: el
    documento y la página de la unidad del censo o, si no tiene documento, la `evidence` con forma exacta de referencia
    («X.pdf», «X.pdf p.7», «X.pdf pp.7-9»), y en los dos casos con el documento en el árbol público (`publicos`, con la
    normalización de AC30: `seudonimos.doc_publico`) y la página numérica. Si no, la decisión de la auditoría y su
    fecha: un nombre de fichero fuera del árbol público, o la `evidence` libre, pueden citar justo lo que se retira, y
    van solo a la traza privada (`_evidencias_md`), la referencia descartada incluida."""
    neutra = f'decisión de la auditoría 063 ({upd})'
    edge = (unit or {}).get('edge') or {}
    doc = str(edge.get('source_doc') or '').strip()
    if doc:
        page = str(edge.get('source_page') or '').strip()
        referencia = f'{doc} p.{page}' if page else doc
        acreditada = seudonimos.doc_publico(doc, publicos) and (not page or _PAGINA.fullmatch(page))
    else:
        referencia = str(evidence or '').strip()
        forma = _REFERENCIA_DOC.fullmatch(referencia)
        if not forma:
            return neutra, ''
        acreditada = seudonimos.doc_publico(forma.group(1), publicos)
    return (referencia, '') if acreditada else (neutra, referencia)


def _fecha_original(decision):
    """La fecha de la resolución de una decisión: la de su `_meta.resolution` (`arbitraje-2026-10-03`) o, sin ella
    (`dual-cerrada`), la de `_meta.decided_at`."""
    meta = decision.get('_meta') or {}
    fecha = re.search(r'\d{4}-\d{2}-\d{2}', str(meta.get('resolution') or '')) or \
        re.match(r'\d{4}-\d{2}-\d{2}', str(meta.get('decided_at') or ''))
    return fecha.group(0) if fecha else None


def _rectificaciones(decision, pack, upd, seudos, publicos, resuelta=None):
    """(filas del registro, su traza privada): la `evidence` libre y la referencia descartada de cada fila."""
    by_id = {u['unit_id']: u for u in pack['units']}
    edge_actions = {e.get('unit_id'): (e.get('action') or 'keep') for e in (decision.get('edges') or [])}
    summary_remove = (decision.get('summary') or {}).get('action') == 'remove'
    role_remove = (decision.get('role') or {}).get('action') == 'remove'
    rows, privadas = [], []
    for unit in decision.get('units') or []:
        uid, label = unit.get('unit_id'), unit.get('label')
        action = edge_actions.get(uid, 'keep')
        if label not in REC_LABELS and action == 'keep':
            continue
        kind = (by_id.get(uid) or {}).get('kind')
        if kind == 'editorial':
            continue  # AC13 registra lo APLICADO; lo editorial sale a tanda y lo decide el dueño
        # Una arista solo sale del grafo con su acción deny: la etiqueta sola no escribe ninguna regla.
        suprimido = ((label == 'retirada' and kind != 'edge') or action == 'deny'
                     or (kind == 'summary' and summary_remove) or (kind == 'attribute' and role_remove))
        entity = pack['entity']['id']
        edge = (by_id.get(uid) or {}).get('edge') or {}
        # `materialize` escribe la regla fix de esa corrección (con su par y su huella), salvo la de una postura.
        con_regla = action in ('retype', 'invert', 'set_prop', 'set_status') \
            and not (edge.get('type') in CLAIM_STANCES and not edge.get('end_entity'))
        dato = _dato_registro(by_id.get(uid), entity, suprimido, seudos, con_regla)
        fuente, descartada = _fuente_registro(by_id.get(uid), unit.get('evidence'), upd, publicos)
        rows.append({'fecha': upd, 'origen': ORIGEN, 'entidad': entity, 'unidad': uid,
                     'dato': dato, 'fuente': fuente,
                     'decision': 'suprimido' if suprimido else 'rectificado',
                     'fecha_resolucion': resuelta or upd, 'seed_version': SEED_PENDIENTE})
        privadas.append({'unidad': uid, 'fuente': fuente, 'descartada': descartada,
                         'evidencia': str(unit.get('evidence') or '').strip()})
    return rows, privadas


def _capas_md(rows, upd):
    """Aristas de capas con YAML propio (prensa con URL, última hora, acusación, contexto) que el apply
    solo puede borrar del seed: su fila en la YAML de la capa hay que quitarla o corregirla a mano."""
    lines = [f'# Capas a editar a mano — 063 · {upd}', '',
             'La regla de `relations.yaml` (con `kinds:`) borra o corrige la arista que ya está en el seed, '
             'pero el loader de la capa la RECREA mientras su YAML la siga declarando. Quitar o corregir la fila en '
             '`pipeline/data/{press,ultima_hora,accusations,context}/…` antes del Path A.', '']
    for r in rows:
        lines.append(f'- **{r["accion"]}** `{r["kind"]}` {r["source"]} -[{r["type"]}]-> {r["target"]} · '
                     f'«{r["quote"]}» · {r.get("url") or ""} · unidad {str(r["unidad"])[:8]} ({r["ref"]})')
    if not rows:
        lines.append('_(ninguna)_')
    return '\n'.join(lines) + '\n'


def _accion_seudonimo(accion, seudonimo, ref, unidad='', **detalle):
    """Una decisión sobre un seudónimo judicial: va a la lista de permitidas, nunca a una regla pública."""
    return {'accion': accion, 'seudonimo': seudonimo, 'unidad': str(unidad or ''), 'ref': ref, **detalle}


def _motivo(out, seudonimo, tag, libre, destino, accion, ref, uid=None):
    """El `reason` de una fila pública (`tag`, y la unidad si es de arista). En una fila de un seudónimo judicial (la
    decisión es sobre él, o la arista lo toca) es neutro: el de la decisión es texto libre y puede citar justo lo que
    se retira (un alias, una sociedad u organismo), así que va solo a la traza privada, con la decisión y la unidad
    (spec 070)."""
    marca, libre = (f'{tag} [{str(uid)[:8]}]' if uid else tag), (libre or '').strip()
    if not seudonimo:
        return f'{marca}: {libre}'
    out['motivos'].append({'ref': ref, 'unidad': str(uid or ''), 'destino': destino, 'motivo': libre})
    return f'{marca}: {accion} por la política de seudónimos judiciales (spec 070); motivo en la traza privada'


def materialize(decisions, tag, upd, seudos=None, publicos=None, reparacion=False):
    """Las filas que cada destino recibe. Exige que `gate()` haya anotado `_pack`. `seudos` = los
    seudónimos judiciales declarados (por defecto, los de este repo): sobre ellos no sale ninguna regla
    deny/alias_deny pública sino una acción sobre `seudonimos_permitidos.yaml` (spec 070). La marca
    `is_pseudonym` del censo no amplía el conjunto: fuera del roster, `gate()` ya la ha rechazado.
    `publicos`: los documentos del árbol público (por defecto, los de este repo), los únicos que la fuente del
    registro puede nombrar. AC119: una arista seudónimo↔sociedad/organismo que queda tal cual solo se propone a la
    lista con la prueba de su cita que anotó `gate()` (`_pruebas`); la acción la lleva (`prueba`; la de una corregida,
    su regla). En una `reparacion` (spec 071 AC45) las filas del registro llevan la fecha de la resolución
    original, y el motivo de lo que va a un overlay público es neutro: el texto libre de la decisión puede
    nombrar a personas y queda en la decisión interna."""
    seudos = seudonimos.ids_seudonimos() if seudos is None else frozenset(seudos)
    publicos = seudonimos.documentos_publicos() if publicos is None else frozenset(publicos)
    out = {'summaries': [], 'roles': [], 'alias_deny': [], 'deny': [], 'fix': [], 'estado': [], 'editorial': [],
           'rectificaciones': [], 'capas': [], 'claims': [], 'seudonimos': [], 'motivos': [], 'evidencias': []}
    for decision in decisions:
        pack = decision.get('_pack')
        if pack is None:
            raise ValueError(f'{decision.get("_file", "?")}: materialize() exige el pack de gate()')
        eid, name = pack['entity']['id'], pack['entity']['name']
        ref = decision.get('_file') or f'{pack["pack_id"]}.json'
        by_id = {u['unit_id']: u for u in pack['units']}
        pruebas = decision.get('_pruebas') or {}   # AC119: lo que el gate probó
        resolution = (decision.get('_meta') or {}).get('resolution')

        def motivo(texto):
            if reparacion:
                return f'decisión {resolution}; motivo en la decisión interna de la auditoría'
            return (texto or '').strip()

        summary = decision.get('summary') or {}
        if summary.get('action') in ('replace', 'remove'):
            reason = _motivo(out, eid in seudos, tag, motivo(summary.get('reason')), 'summaries',
                             'retirada' if summary['action'] == 'remove' else 'corregida', ref)
        if summary.get('action') == 'replace':
            out['summaries'].append(dict(entity_id=eid, name=name, resumen=summary['resumen'].strip(),
                                         sources=[str(s) for s in (summary.get('sources') or [])],
                                         updated=upd, reason=reason))
        elif summary.get('action') == 'remove':
            out['summaries'].append(dict(entity_id=eid, name=name, remove=True, reason=reason))

        role = decision.get('role') or {}
        if role.get('action') in ('replace', 'remove'):
            reason = _motivo(out, eid in seudos, tag, motivo(role.get('reason')), 'roles',
                             'retirada' if role['action'] == 'remove' else 'corregida', ref)
            out['roles'].append(dict(type=eid.split(':')[0], name=name, entity_id=eid,
                                     role='remove' if role['action'] == 'remove' else role['role'].strip(),
                                     reason=reason))

        denegados = list(decision.get('alias_deny') or [])
        # Alias de un seudónimo → la unidad `identity` del censo que lo lleva ({'identity': {'aliases': […]}}):
        # lo que la decisión cubre y no deniega se conserva; lo denegado se retira a cuenta de esa unidad, con el
        # alias tal como está en el censo (`gate()` exige que cada entrada lo sea, salvo espacios en los bordes).
        portadora = {}
        if eid in seudos:
            for unit in decision.get('units') or []:
                block = by_id.get(unit.get('unit_id')) or {}
                if block.get('kind') == 'identity':
                    for alias in block['value']['identity'].get('aliases') or []:
                        portadora.setdefault(alias, unit.get('unit_id'))
            pedidos = {str(a).strip() for a in denegados if str(a).strip()}
            denegados = [alias for alias in portadora if str(alias).strip() in pedidos]
        for alias in denegados:
            if eid in seudos:
                out['seudonimos'].append(_accion_seudonimo('retirar', eid, ref, portadora[alias], alias=alias))
                continue
            out['alias_deny'].append(dict(
                id=eid, drop=[alias],
                reason=f'{tag}: alias «{alias}» denegado (nombre de pila suelto, DNI, descripción, otra '
                       f'persona/referente u homónimo, o sin fuente en corpus ni fuentes web) — motivo en '
                       f'esc/decisions/{ref}'))
        for alias, uid in portadora.items():
            if alias not in denegados:
                out['seudonimos'].append(_accion_seudonimo('conservar', eid, ref, uid, alias=alias))

        acted = set()
        for edge_decision in decision.get('edges') or []:
            action = edge_decision.get('action') or 'keep'
            if action == 'keep':
                continue
            uid = edge_decision.get('unit_id')
            acted.add(uid)
            edge = (by_id.get(uid) or {}).get('edge') or {}
            source, target = edge.get('start_entity'), edge.get('end_entity')
            if edge.get('type') in CLAIM_STANCES and not target:
                # Postura de una afirmación enfrentada: su destino es un Claim sin entity_id, que el
                # overlay de relaciones no alcanza. Su fuente es claims.yaml (el cargador sincroniza).
                out['claims'].append({'entity_id': source, 'type': edge.get('type'), 'action': action,
                                      'quote': (edge.get('source_quote') or '').strip(), 'ref': ref,
                                      'unit': str(uid)[:8]})
                continue
            # La regla va en la dirección del censo, también en las simétricas (spec 071 AC60): el colapso solo junta
            # los pares bidireccionales y deja las inversas sueltas como están, y el retype del loader casa por
            # dirección; el deny y el set no la miran en una simétrica.
            base = {'source_id': source, 'target_id': target, 'type': edge.get('type'), **_procedencia(edge),
                    # Neutro también si la decisión es sobre un civil y la arista toca a un seudónimo (enmienda 6).
                    'reason': _motivo(out, bool({eid, source, target} & seudos), tag,
                                      motivo(edge_decision.get('reason')), 'relations',
                                      'retirada' if action == 'deny' else 'corregida', ref, uid)}
            kind = edge.get('source_kind')
            if kind and kind != 'judicial':
                # Sin `kinds` el loader solo toca aristas extraídas (null/judicial): la regla sería un no-op.
                base = {**base, 'kinds': [kind]}
                props = ((by_id.get(uid) or {}).get('value') or {}).get('props') or {}
                if kind in LAYER_KINDS or (kind == 'prensa' and props.get('url')):
                    out['capas'].append({'entidad': eid, 'unidad': uid, 'accion': action, 'kind': kind,
                                         'type': edge.get('type'), 'source': source, 'target': target,
                                         'doc': edge.get('source_doc') or None,
                                         'quote': (edge.get('source_quote') or '')[:160],
                                         'url': props.get('url'), 'ref': ref})
            if seudonimos.es_par_sensible(source, target, seudos):
                sid, otro = seudonimos.orientar(source, target, seudos)
                # La capa como la ve la política del loader: sin `source_kind`, «sin capa» (no judicial).
                tipo, capa, doc = edge.get('type'), kind or seudonimos.SIN_CAPA, edge.get('source_doc') or None
                if action == 'deny':
                    # Retirar = dejarla fuera de la lista; la regla pública diría justo lo que se retira.
                    out['seudonimos'].append(_accion_seudonimo('retirar', sid, ref, uid, otro=otro, tipo=tipo,
                                                               kind=capa, doc=doc))
                    continue
                nuevo_tipo = edge_decision.get('new_type') if action == 'retype' else tipo
                # La capa que deja la operación: la de un set_prop o la del mapa `set` de un retype o un invert
                # (spec 071 AC71).
                nueva_capa = str(props_del_set(edge_decision).get('source_kind') or '').strip() or capa
                # AC119: la prueba de una arista corregida es la de su regla (AC90, en el gate).
                out['seudonimos'].append(_accion_seudonimo('conservar', sid, ref, uid, otro=otro, tipo=nuevo_tipo,
                                                           kind=nueva_capa, doc=doc, prueba='regla'))
                if (nuevo_tipo, nueva_capa) != (tipo, capa):
                    # El fix la saca de su tripleta o capa: no hay que borrarla, solo podar lo que nadie más use.
                    out['seudonimos'].append(_accion_seudonimo('mover', sid, ref, uid, otro=otro, tipo=tipo,
                                                               kind=capa, doc=doc))
            # Las props que cambia la operación (un set_prop, o un retype o un invert con su mapa `set`, spec 071 AC71)
            # van en la misma fila de la regla: una operación, varias fases.
            cambia = {prop: _cast_value(prop, valor) for prop, valor in props_del_set(edge_decision).items()}
            fases = {'set': cambia} if cambia else {}
            if action == 'deny' and _anonimiza(by_id.get(uid), eid, seudos):
                # AC114: la fila del registro oculta un extremo y la regla tampoco lo nombra: solo su unidad, sus capas
                # y un motivo neutro; el loader la resuelve contra el censo. Del motivo, la etiqueta de la tanda solo si
                # es la de un lote: una etiqueta libre puede nombrar lo que la fila oculta (AC130).
                lote = f'{tag} ' if _PREFIJO_LOTE.fullmatch(tag) else ''
                out['deny'].append({'unidad': uid, **({'kinds': base['kinds']} if base.get('kinds') else {}),
                                    'reason': f'{lote}[{str(uid)[:8]}]: {MOTIVO_OPACO}'})
            elif action == 'deny':
                out['deny'].append(base)
            elif action == 'retype':
                out['fix'].append({**base, 'corrected_type': edge_decision.get('new_type'), **fases})
            elif action == 'invert':
                out['fix'].append({**base, 'swap_direction': True, **fases})
            elif action == 'set_prop':
                out['fix'].append({**base, 'set': cambia})
            elif action == 'set_status':
                out['fix'].append({**base, 'set': {'status': str(edge_decision.get('status') or '').strip()}})
        # «Conservar» una arista sensible = cubrirla sin tocarla: la lista de permitidas la mantiene.
        for unit in decision.get('units') or []:
            edge = (by_id.get(unit.get('unit_id')) or {}).get('edge')
            if not edge or unit.get('unit_id') in acted:
                continue
            source, target = edge.get('start_entity'), edge.get('end_entity')
            if seudonimos.es_par_sensible(source, target, seudos) and unit.get('unit_id') in pruebas:   # AC119
                sid, otro = seudonimos.orientar(source, target, seudos)
                out['seudonimos'].append(_accion_seudonimo('conservar', sid, ref, unit.get('unit_id'), otro=otro,
                                                           tipo=edge.get('type'),
                                                           kind=edge.get('source_kind') or seudonimos.SIN_CAPA,
                                                           doc=edge.get('source_doc') or None,
                                                           prueba=pruebas[unit.get('unit_id')]))

        estado = decision.get('estado') or {}
        if estado.get('action') == 'set':
            doc, url = (estado.get('doc') or '').strip(), (estado.get('url') or '').strip()
            fuente = {'doc': doc} if doc else {'url': url}
            fuente['tipo'] = (estado.get('tipo') or '').strip()
            block = {'person': name, 'entity_id': eid,
                     'estado': {'status': estado.get('status'), 'fecha': estado.get('fecha'),
                                'fuente': fuente, 'revisado': upd}}
            if (estado.get('nota') or '').strip():
                block['estado']['nota'] = estado['nota'].strip()
                if eid in seudos:   # el informe de estado es privado; el dry-run la neutraliza (`_imprimible`)
                    out['motivos'].append({'ref': ref, 'unidad': '', 'destino': 'estado',
                                           'motivo': block['estado']['nota']})
            out['estado'].append(block)

        for item in decision.get('editorial') or []:
            if (item.get('action') or 'keep') == 'keep':
                continue
            out['editorial'].append({'entidad': eid, 'unidad': item.get('unit_id'), 'accion': item.get('action'),
                                     'decision': ref, 'actual': _dato(by_id.get(item.get('unit_id'))),
                                     'texto': (item.get('text') or '').strip(),
                                     'motivo': (item.get('reason') or '').strip()})

        filas, privadas = _rectificaciones(decision, pack, upd, seudos, publicos,
                                          _fecha_original(decision) if reparacion else None)
        out['rectificaciones'].extend(filas)
        out['evidencias'].extend({'ref': ref, **privada} for privada in privadas)
    return out


def _tripleta(accion):
    return (accion['seudonimo'], accion['otro'], accion['tipo'])


def _evidencias_censo(conn, acciones):
    """{(seudonimo, otro, tipo): [{'unidad', 'kind', 'doc'}]}: cada arista del censo entre los extremos de
    una tripleta que la tanda toca, en cualquier dirección. Es lo que la auditoría conoce de esa tripleta:
    las evidencias que la tanda no cubre no pueden cambiar de suerte en silencio (spec 070)."""
    out = {_tripleta(a): [] for a in acciones if 'alias' not in a}
    if not out:
        return out
    for row in conn.execute("SELECT unit_id, value FROM units WHERE kind='edge'"):
        value = json.loads(row['value'])
        if not isinstance(value, dict):
            continue
        props = value.get('props') or {}
        evidencia = {'unidad': row['unit_id'], 'kind': props.get('source_kind') or seudonimos.SIN_CAPA,
                     'doc': props.get('source_doc') or None}
        for clave in {(value.get('start'), value.get('end'), value.get('type')),
                      (value.get('end'), value.get('start'), value.get('type'))}:
            if clave in out:
                out[clave].append(evidencia)
    return out


def _aplicar_seudonimos(permitidos, acciones, upd, evidencias=None, publicos=None):
    """Lleva las decisiones sobre seudónimos a la lista de permitidas (en memoria). Devuelve (cambios, errores).

    Aristas, tripleta a tripleta (`seudonimos.resolver_arista`): la lista no distingue cita, así que conservar
    una evidencia no puede anular en silencio la retirada de otra; `docs` las separa cuando vienen de
    documentos distintos y, si no puede, error sin escribir. Con `publicos`, tampoco si `docs` nombraría un documento
    que el árbol no publica (AC45): la lista es pública. `evidencias` = las del censo por tripleta
    (`_evidencias_censo`): las que la tanda no cubre no cambian de suerte. «mover» = el lado viejo de un
    retype o de un cambio de capa. Una entrada nueva lleva `origen: decision-<upd>` y el motivo de la plantilla cerrada
    con sus unidades (AC80: la lista no tiene texto libre, así que nunca el `--tag`). Alias: si una unidad conserva lo
    que otra retira, gana conservar (y `_suprimidos_conservados` rechaza la tanda); solo se conserva el nombre de pila
    con tratamiento."""
    evidencias = evidencias or {}
    cambios, errores = Counter(), []
    grupos = {}
    for a in acciones:
        if 'alias' not in a:
            grupos.setdefault(_tripleta(a), {'conservar': [], 'retirar': [], 'mover': []})[a['accion']].append(a)
    for clave, grupo in sorted(grupos.items()):
        propias = [a for lista in grupo.values() for a in lista]
        cubiertas = {a['unidad'] for a in propias}
        otras = [e for e in evidencias.get(clave, ()) if e['unidad'] not in cubiertas]
        pos = next((i for i, e in enumerate(permitidos['aristas']) if _tripleta(e) == clave), None)
        antes = None if pos is None else permitidos['aristas'][pos]
        nueva = {'seudonimo': clave[0], 'otro': clave[1], 'tipo': clave[2], 'origen': f'decision-{upd}',
                 'motivo': 'decisión «conservar» de la auditoría (unidad '
                           + ', '.join(sorted(a['unidad'][:8] for a in grupo['conservar'])) + ')'}
        despues, motivo = seudonimos.resolver_arista(antes, grupo['conservar'], grupo['retirar'], grupo['mover'],
                                                     otras, nueva)
        privados = [] if publicos is None else [d for d in (despues or {}).get('docs') or ()
                                                 if not seudonimos.doc_publico(d, publicos)]
        if not motivo and privados:
            motivo = ('la entrada quedaría acotada por un documento fuera del árbol público ('
                      + ', '.join(seudonimos.huella_doc(d) for d in privados) + ')')
        if motivo:
            # Sin extremos: este mensaje acaba en la terminal, en PRs o en el CI, y el seudónimo junto a la sociedad u
            # organismo es justo lo que puede estar retirándose. La huella y las unidades bastan para localizarla.
            refs = ', '.join(sorted({a['ref'] for a in propias}))
            huella = seudonimos.sha16('|'.join(map(str, clave)))
            errores.append(f'{refs}: arista {huella} ({_tipo_impreso(clave[2])}): {motivo} (unidades '
                           + ', '.join(sorted(u[:8] for u in cubiertas)) + ', spec 070)')
        elif despues is None and antes is not None:
            del permitidos['aristas'][pos]
            cambios['aristas_quitadas'] += 1
        elif antes is None and despues is not None:
            permitidos['aristas'].append(despues)
            cambios['aristas_añadidas'] += 1
        elif despues != antes:
            permitidos['aristas'][pos] = despues
            cambios['aristas_acotadas'] += 1
    conservar = {(a['seudonimo'], a['alias']) for a in acciones if 'alias' in a and a['accion'] == 'conservar'}
    for a in acciones:
        if 'alias' not in a:
            continue
        if a['accion'] == 'retirar':
            if (a['seudonimo'], a['alias']) not in conservar \
                    and seudonimos.retirar_alias(permitidos, a['seudonimo'], a['alias']):
                cambios['alias_quitados'] += 1
        elif not (seudonimos.alias_permitido(a['seudonimo'], a['alias'], permitidos)
                  or seudonimos.es_variante_nombre(a['seudonimo'], a['alias'])):
            errores.append(f'{a["ref"]}: unidad {a["unidad"][:8]}: se conserva un alias de seudónimo que no '
                           f'es su nombre de pila; denegarlo (alias_deny) o decidir a mano (spec 070)')
        elif seudonimos.conservar_alias(permitidos, a['seudonimo'], a['alias']):
            cambios['alias_añadidos'] += 1
    return cambios, errores


def _nombra_las_de_la_lista(rows, decisions, seudos, permitidos):
    """Principio P (fusión 070 ⟷ 071): la fila del registro de una arista de un seudónimo cuyo par (el seudónimo y
    el otro extremo, con cualquier tipo) sigue en la lista de permitidas resultante (`permitidos`, tras el plan), y cuya
    entidad es uno de los dos, la nombra entera, como la juzgan la guardia (`seudonimos.fila_juzgada`) y el D1 de la
    071: la lista ya publica el par. Cambia el `dato` de esas filas en su sitio."""
    pares = {frozenset((e.get('seudonimo'), e.get('otro'))) for e in permitidos.get('aristas') or []}
    unidades = {u['unit_id']: u for d in decisions for u in (d.get('_pack') or {}).get('units') or []}
    for row in rows['rectificaciones']:
        unit = unidades.get(row['unidad']) or {}
        edge = unit.get('edge') or {}
        par = frozenset((edge.get('start_entity'), edge.get('end_entity')))
        if unit.get('kind') == 'edge' and par in pares and row['entidad'] in par:
            row['dato'] = _dato_registro(unit, row['entidad'], row['decision'] == 'suprimido', seudos, nombrada=True)


def _suprimidos_conservados(rectificaciones, acciones, permitidos, seudos):
    """Lo retirado de un seudónimo ha de quedar fuera de la lista resultante (spec 070): ni la arista
    seudónimo↔sociedad/organismo de una fila «suprimido», ni un alias que retira un `alias_deny`; y una fila
    «alias retirado» exige que su unidad `identity` retire algún alias. Si no, la decisión es incoherente y no
    se escribe nada. Los mensajes no nombran el alias."""
    indice, alias_finales = seudonimos.indice_aristas(permitidos), seudonimos.indice_alias(permitidos)
    final = {a['unidad']: a for a in acciones if 'alias' not in a and a['accion'] in ('conservar', 'retirar')}
    retiradas = [a for a in acciones if 'alias' in a and a['accion'] == 'retirar']
    conservadas = {(a['seudonimo'], a['alias']): a for a in acciones if 'alias' in a and a['accion'] == 'conservar'}
    errores = []
    for a in retiradas:
        if a['alias'] in alias_finales.get(a['seudonimo'], frozenset()):
            otra = conservadas.get((a['seudonimo'], a['alias']))
            quien = f'; lo conserva {otra["ref"]} (unidad {otra["unidad"][:8]})' if otra else ''
            errores.append(f'{a["ref"]}: unidad {a["unidad"][:8]}: un alias de `alias_deny` sigue en la lista de '
                           f'permitidas{quien} (decisión incoherente, spec 070)')
    alias_retirado = _dato_registro({'kind': 'identity'}, None, True)
    respaldadas = {(a['seudonimo'], a['unidad']) for a in retiradas}
    for fila in rectificaciones:
        if fila['decision'] != 'suprimido':
            continue
        if fila['entidad'] in seudos and fila['dato'] == alias_retirado:
            if (fila['entidad'], fila['unidad']) not in respaldadas:
                errores.append(f'{fila["entidad"]}: unidad {fila["unidad"][:8]}: el registro diría «alias retirado» sin '
                               'que `alias_deny` retire un alias de esa identidad (decisión incoherente, spec 070)')
            continue
        a = final.get(fila['unidad'])
        if a and seudonimos.arista_permitida(indice, *_tripleta(a), a['kind'], a['doc'] or ''):
            errores.append(f'{a["ref"]}: unidad {a["unidad"][:8]}: el registro diría «suprimido» de una arista '
                           f'que la lista de permitidas conserva (decisión incoherente, spec 070)')
    return errores


def _errores_permitidos(permitidos, seudos, cual, publicos=None, civiles=None):
    """La validación del loader y del CI (`seudonimos.validar_permitidos`, con los documentos de `docs` contra
    `publicos` y el `origen`/`motivo` contra las personas civiles de `civiles`, como `auditar`) sobre la lista `cual`
    (vigente o resultante): inválida, el apply no escribe nada."""
    return [f'{seudonimos.PERMITIDOS_NOMBRE} ({cual}): {motivo}'
            for _, _, motivo in seudonimos.validar_permitidos(permitidos, seudos, publicos=publicos, civiles=civiles)]


# AC79 (spec 070): el fichero público que recibe cada cubo materializado (el estado lo pega el dueño en accusations) y
# su sección, que da en el inventario del check (`seudonimos.CAPAS`) las entidades de la fila y la clase de cada campo.
_DESTINO = {'summaries': (SUM_PATH, 'summaries'), 'roles': (ROL_PATH, 'entities'),
            'alias_deny': (REL_PATH, 'alias_deny'), 'deny': (REL_PATH, 'deny'), 'fix': (REL_PATH, 'fix'),
            'estado': ('pipeline/data/accusations/accusations.yaml', 'accusations'),
            'rectificaciones': (REG_PATH, 'rectificaciones')}
_MOTIVOS_PROSA = {'civil': f'prosa civil {seudonimos.MOTIVOS["civil"]}',
                  'seudonimo': f'texto de una fila de seudónimo {seudonimos.MOTIVOS["seudonimo"]}',
                  'nombre': seudonimos.MOTIVOS['nombre'], 'sospechoso': seudonimos.MOTIVOS['sospechoso'],
                  'coaparicion': seudonimos.MOTIVOS['coaparicion'], 'pila': seudonimos.MOTIVOS['pila'],
                  'alias-identificador': seudonimos.MOTIVOS['alias-identificador']}


def _prosa_de(bucket, row):
    """(entidades, [(texto, clase)]) de una fila materializada que el dry-run imprime o el apply escribe: las de su
    sección en el inventario del check (`_DESTINO`) y, de una fila de capas, la cita y la URL."""
    if bucket == 'capas':
        return ({row.get('entidad'), row.get('source'), row.get('target')} - {None},
                [(row.get('quote') or '', seudonimos.CITA), (row.get('url') or '', seudonimos.URL)])
    fichero, seccion = _DESTINO[bucket]
    spec = seudonimos.capa_de(fichero)[seccion]
    return spec.entidades(row, {}), seudonimos.textos_fila(spec.campos, row)


def _prosa_publica(rows, seudos, civiles, publicos, nombres=None, citas=(), documentos=None, roster=(), export=None,
                   conservadas=None, sociedades=None, permitidos=None):
    """AC69/AC72/AC78/AC81 (spec 070): tras `materialize` y antes de imprimir o escribir nada, cada fila pasa la
    partición del check (`seudonimos.revisar_textos`), con el texto de la decisión y el que se compone con el censo (la
    cita de una regla o de una capa, el alias en el `reason`): la de una entidad civil no nombra a un seudónimo
    judicial; la de un seudónimo no nombra a un civil, otra entidad, un documento fuera del árbol público u otro
    seudónimo; ni cita a su extremo civil sin una entrada de `citas:` de la lista (`citas`, AC90; `nombres`, los del
    contexto); y ninguna lleva caracteres invisibles o alfabetos mezclados. La fila de capas de un seudónimo no se
    publica: el dry-run la imprime sin cita ni URL (`_imprimible`) y su informe es privado. La posición en el plan y la
    huella, nunca el texto. AC118: los problemas del roster (`roster`, `seudonimos.problemas_roster`). AC119/AC122:
    ninguna arista que la lista conservaría sin la prueba de su cita, que se recalcula con lo que la produjo en el gate
    (`conservadas`, {unidad: (cita, documento, capa, extremos, nombres conocidos)}): la etiqueta no vale. AC120: la
    prosa de las filas de un seudónimo (`seudonimos.textos_dato_civil`) sin un dato civil, con su registro de documentos
    (`documentos`). AC125: con la prueba única de citas (`seudonimos.prueba_cita`), la del gate: la prosa y las aristas
    conservadas, también por la vía (a) si recibe el export (`export`, `--snapshot`); el selector de una regla, como en
    el árbol, solo por su entrada de `citas:`. AC153: con el índice de sociedades del gate (`sociedades`, el de
    `indice_sociedades`); sin él, el del contexto."""
    errores = list(roster)
    documentos = seudonimos.censo_documentos(None, publicos) if documentos is None else documentos
    sociedades = seudonimos.indice_sociedades(nombres or {}) if sociedades is None else sociedades
    # Principio P (fusión 070 ⟷ 071): la fila del registro, con las reglas explícitas del mismo plan y la lista de
    # permitidas que deja (`permitidos`; sin ella, ninguna entrada).
    explicitas = seudonimos.reglas_explicitas([(s, r) for s in ('deny', 'fix') for r in rows.get(s) or []])
    listadas = (permitidos or {}).get('aristas') or []
    for bucket in (*_DESTINO, 'capas'):
        for i, row in enumerate(rows[bucket]):
            if bucket == 'rectificaciones':
                row = seudonimos.fila_juzgada(row, *explicitas, listadas)
            entidades, textos = _prosa_de(bucket, row)
            if bucket == 'capas' and entidades & seudos:
                continue
            doc = str(row.get('doc') or '').strip() or None

            # AC125: la prueba única, sin el export: el selector se publica en el árbol, que solo lo lee de `citas:`.
            def admitida(texto, s=frozenset(entidades & seudos), d=doc):
                return seudonimos.prueba_cita(texto, d, s, citas)
            errores += [f'{bucket}[{i}]: {_MOTIVOS_PROSA[motivo]} (huella {huella})'
                        for motivo, huella in seudonimos.revisar_textos(entidades, textos, seudos, civiles, publicos,
                                                                        nombres, admitida, sociedades)]
            if bucket != 'capas' and entidades & seudos:
                errores += [f'{bucket}[{i}]: {motivo} (huella {seudonimos.sha16(t)})'
                            for t, tipo, d in seudonimos.textos_dato_civil(*_DESTINO[bucket], row)
                            for motivo in _motivos_dato_civil(t, tipo, d, documentos, sociedades,
                                                              frozenset(entidades), frozenset(entidades & seudos),
                                                              citas, export)]
    for i, accion in enumerate(rows['seudonimos']):
        if accion['accion'] != 'conservar' or 'otro' not in accion or accion.get('prueba') == 'regla':
            continue   # un alias, o una arista corregida: la prueba es su regla (su selector, arriba)
        dato = (conservadas or {}).get(accion['unidad'])
        prueba, motivos = (None, ['sin-dato']) if dato is None else seudonimos.prueba_conservada(
            dato[0], dato[1], dato[2] == 'judicial', set(dato[3]), seudos, civiles, nombres, citas, export,
            {e: set(v) for e, v in dato[4].items()}, publicos, sociedades)
        if motivos or not prueba or prueba != accion.get('prueba'):
            errores.append(f'seudonimos[{i}]: arista conservada sin la prueba de su cita '
                           f'(unidad {str(accion["unidad"])[:8]})')
    return errores


def _imprimible(bucket, row, seudos, retiradas=None, publicos=frozenset()):
    """La fila tal como la imprime el dry-run: la terminal acaba copiada en PRs, así que lo que se retira de un
    seudónimo (el alias, o la sociedad u organismo) sale neutralizado, como en el registro, y de sus filas de capa no
    sale la cita ni la URL, ni la nota ni la URL de su estado (spec 070). Ningún campo de una fila que toque a un
    seudónimo nombra la sociedad u organismo retirado: tampoco el titular de la decisión, en su fila de capas ni en la
    del registro de la arista retirada (`retiradas` = {unidad: seudónimo}), que la unidad empareja con las filas de
    capas y de seudónimos del mismo dry-run. Un documento que el árbol no publica (`publicos`) no se nombra: ni el de la
    fuente de un estado, de quien sea, ni el de una acción sobre un seudónimo (`_doc_impreso`). Los informes privados de
    esc/report lo guardan entero; lo que se conserva se nombra. Los `reason` ya salen neutros de `materialize`
    (`_motivo`). Un tipo de arista, solo canónico (`_tipo_impreso`, AC48)."""
    retiradas = retiradas or {}
    if bucket in ('deny', 'fix', 'capas', 'seudonimos'):   # en `roles`, `type` es el de la entidad
        row = {**row, **{k: _tipo_impreso(row[k]) for k in ('type', 'corrected_type', 'tipo') if row.get(k)}}
    if bucket == 'seudonimos':
        row = {**row, 'doc': _doc_impreso(row.get('doc'), publicos)} if row.get('doc') else row
        if row['accion'] != 'retirar':
            return row
        if 'alias' in row:
            return {**row, 'alias': '[alias]'}
        return {**row, 'otro': _extremo_registro(row['otro'], row['seudonimo'], row['tipo'], seudos)}
    if bucket == 'capas':
        sid = next((x for x in (row['source'], row['target'], row['entidad']) if x in seudos), None)
        if sid is None:
            return row
        out = {**row, 'quote': '', 'url': ''}   # enteras en el informe privado
        if row['entidad'] not in seudos:   # el id de un seudónimo es público; el titular retirado, no
            out['entidad'] = _extremo_registro(row['entidad'], sid, row['type'], seudos)
        if row['accion'] == 'deny' and seudonimos.es_par_sensible(row['source'], row['target'], seudos):
            out['source'], out['target'] = (_extremo_registro(x, sid, row['type'], seudos)
                                            for x in (row['source'], row['target']))
        return out
    if bucket == 'rectificaciones' and row['unidad'] in retiradas and row['entidad'] not in seudos:
        neutro = _extremo_registro(row['entidad'], retiradas[row['unidad']], None, seudos)
        return {**row, 'entidad': neutro, 'dato': row['dato'].replace(row['entidad'], neutro)}
    if bucket == 'estado':
        estado, fuente = dict(row['estado']), dict(row['estado'].get('fuente') or {})
        if fuente.get('doc'):
            fuente['doc'] = _doc_impreso(fuente['doc'], publicos)
        if row['entity_id'] in seudos:
            if 'nota' in estado:
                estado['nota'] = 'motivo en la traza privada'
            if fuente.get('url'):
                fuente['url'] = ''
        return {**row, 'estado': {**estado, 'fuente': fuente}}
    return row


def _doc_impreso(doc, publicos):
    """Un documento tal como lo imprime el dry-run: su nombre si el árbol lo publica; si no, su huella."""
    return doc if seudonimos.doc_publico(doc, publicos) else f'documento no público ({seudonimos.huella_doc(doc)})'


def _seudonimos_md(acciones, cambios, upd, motivos=()):
    """La traza PRIVADA de las decisiones sobre seudónimos: lo retirado no se lista en ningún fichero público, y los
    motivos libres de sus filas públicas (que allí llevan uno neutro, `_motivo`) solo están aquí."""
    lines = [f'# Seudónimos judiciales — 063 · {upd} (privado, spec 070)', '',
             'Ninguna de estas decisiones escribe una regla pública: la política del loader retira por defecto lo '
             'que no esté en `seudonimos_permitidos.yaml`, y el registro neutraliza el otro extremo. «mover» es el '
             'lado viejo de un retype o de un cambio de capa.', '',
             f'Cambios en la lista: {json.dumps(dict(cambios), ensure_ascii=False)}', '']
    for a in acciones:
        objeto = (f'alias «{a["alias"]}»' if 'alias' in a
                  else f'-[{a["tipo"]}]- {a["otro"]} ({a["kind"]}, {a.get("doc") or "sin documento"})')
        lines.append(f'- **{a["accion"]}** {a["seudonimo"]} {objeto} · unidad {a["unidad"][:8]} ({a["ref"]})')
    if not acciones:
        lines.append('_(ninguna)_')
    if motivos:
        lines += ['', '## Motivos de las decisiones', '',
                  'Las filas públicas (summaries, roles, relations) y el dry-run llevan un motivo neutro; estos son '
                  'los de las decisiones.', '']
        lines += [f'- **{m["destino"]}**' + (f' · unidad {m["unidad"][:8]}' if m['unidad'] else '')
                  + f' ({m["ref"]}): {m["motivo"] or "(sin motivo)"}' for m in motivos]
    return '\n'.join(lines) + '\n'


def _evidencias_md(rows, upd):
    """La traza PRIVADA de las filas del registro: su `fuente` pública es una referencia acreditada
    (`_fuente_registro`), y la `evidence` libre de la decisión y la referencia descartada, que pueden citar lo
    retirado, solo están aquí, con la decisión y la unidad."""
    lines = [f'# Registro de rectificaciones — evidencias de la 063 · {upd} (privado)', '',
             'La `fuente` de cada fila nueva del registro público es el documento y la página de la unidad del censo, '
             'o la `evidence` si es solo una referencia documental, siempre de un documento del árbol público y con la '
             'página numérica; si no, «decisión de la auditoría 063 (fecha)», y la referencia descartada consta aquí. '
             'Esta es la `evidence` de cada decisión.', '']
    lines += [f'- unidad {r["unidad"][:8]} ({r["ref"]}) · fuente pública «{r["fuente"]}» · '
              + (f'referencia descartada «{r["descartada"]}» · ' if r['descartada'] else '')
              + f'evidencia: {r["evidencia"] or "(sin evidencia)"}' for r in rows]
    if not rows:
        lines.append('_(ninguna)_')
    return '\n'.join(lines) + '\n'


# --- escritura de los overlays ------------------------------------------------------------------

def replace_or_append_summary(text, row):
    eid = row['entity_id']
    block = _dump_block(row)
    pat = re.compile(rf'^- entity_id: {re.escape(eid)}\n(?:(?!^- entity_id:)(?!^#).*\n?)*', re.M)
    if pat.search(text):
        text, n = pat.subn(lambda m: block, text, count=1)
        assert n == 1
        return text, 'replaced'
    return text + ('' if text.endswith('\n') else '\n') + block, 'appended'


def replace_or_insert_role(text, row):
    eid = row['entity_id']
    block = _dump_block(row)
    pat = re.compile(rf'^- type: \w+\n  name: [^\n]+\n  entity_id: {re.escape(eid)}\n'
                     rf'(?:(?!^- type:)(?!^relations:)(?!^#).*\n?)*', re.M)
    if pat.search(text):
        text, n = pat.subn(lambda m: block, text, count=1)
        assert n == 1
        return text, 'replaced'
    # Filas antiguas sin entity_id (el loader casa por nombre): también se sustituyen.
    pat2 = re.compile(rf'^- type: \w+\n  name: {re.escape(row["name"])}\n'
                      rf'(?:(?!^- type:)(?!^relations:)(?!^#).*\n?)*', re.M)
    if pat2.search(text):
        text, n = pat2.subn(lambda m: block, text, count=1)
        assert n == 1
        return text, 'replaced-by-name'
    idx = text.index('\nrelations:')
    return text[:idx + 1] + block + text[idx + 1:], 'inserted'


def _insert_into_section(text, section, block):
    """Inserta `block` al final de la lista de primer nivel `section:` de relations.yaml,
    por delante de las líneas en blanco y del comentario que encabezan la sección siguiente."""
    lines = text.split('\n')
    start = next((i for i, line in enumerate(lines) if re.match(rf'^{re.escape(section)}:\s*$', line)), None)
    if start is None:
        raise ValueError(f'relations.yaml: falta la sección `{section}:`')
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r'^[A-Za-z_][\w-]*:', lines[i]):
            end = i
            break
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith('#')):
        end -= 1
    return '\n'.join(lines[:end] + block.rstrip('\n').split('\n') + lines[end:])


def _relations_key(row, keys):
    return tuple(json.dumps(row.get(k), ensure_ascii=False, sort_keys=True) for k in keys)


_DENY_KEYS = ('source_id', 'target_id', 'type', 'doc', 'quote_contains')
_FIX_KEYS = _DENY_KEYS + ('corrected_type', 'swap_direction', 'set')


def _estricto(texto, nombre):
    """El YAML `texto` del fichero `nombre` con el lector de la política (`seudonimos.leer_yaml`, spec 070: el fichero y
    la posición, y si es una clave repetida, que lo es) y el que además rechaza una clave de fusión (`alcance.cargar_yaml`,
    spec 071 AC108 y AC128): uno que no carga para con exit 2, nunca con el valor."""
    try:
        datos = cargar_yaml(texto, nombre)
    except YamlInvalido as exc:
        _overlay_rechazado(str(exc))
    try:
        seudonimos.leer_yaml(texto, nombre)
    except SystemExit as exc:
        raise seudonimos.ErrorDeForma([str(exc)]) from None
    return datos


def _clave_deny(row):
    """Lo que identifica una regla deny para no escribirla dos veces: una opaca, su unidad y sus capas (AC114)."""
    if es_opaca(row):
        return ('opaca', row.get('unidad'), tuple(row.get('kinds') or ()))
    return _relations_key(row, _DENY_KEYS)


def _write_relations(text, rows, tag, seudos=None):
    """Anexa deny/fix/alias_deny a sus secciones, saltando lo que ya está. (texto, contadores).
    Un deny o alias_deny público sobre un seudónimo judicial es un error y no se escribe nada: diría
    justo lo que se retira (spec 070; la retirada es la política del loader)."""
    seudos = seudonimos.ids_seudonimos() if seudos is None else seudos
    prohibidas = ([r for r in rows['deny'] if seudonimos.es_par_sensible(
                      seudonimos.extremo(r, 'source'), seudonimos.extremo(r, 'target'), seudos)]
                  + [r for r in rows['alias_deny'] if seudonimos.entidad(r) in seudos])
    if prohibidas:
        raise ValueError(f'relations.yaml: {len(prohibidas)} regla(s) deny/alias_deny pública(s) sobre un '
                         f'seudónimo judicial (spec 070): no se escribe nada')
    current = _estricto(text, Path(REL_PATH).name) or {}
    counts = Counter()
    have_deny = {_clave_deny(r) for r in (current.get('deny') or [])}
    have_fix = {_relations_key(r, _FIX_KEYS) for r in (current.get('fix') or [])}
    have_alias = {(r.get('id'), alias) for r in (current.get('alias_deny') or [])
                  for alias in (r.get('drop') or [])}
    for row in rows['deny']:
        key = _clave_deny(row)
        if key in have_deny:
            continue
        have_deny.add(key)
        text = _insert_into_section(text, 'deny', f'\n  # 063 {tag}\n' + _indented(row))
        counts['deny'] += 1
    for row in rows['fix']:
        key = _relations_key(row, _FIX_KEYS)
        if key in have_fix:
            continue
        have_fix.add(key)
        text = _insert_into_section(text, 'fix', f'\n  # 063 {tag}\n' + _indented(row))
        counts['fix'] += 1
    for row in rows['alias_deny']:
        key = (row['id'], row['drop'][0])
        if key in have_alias:
            continue
        have_alias.add(key)
        text = _insert_into_section(text, 'alias_deny', f'\n  # 063 {tag}\n' + _indented(row))
        counts['alias_deny'] += 1
    return text, counts


def _write_registro(text, rows, upd):
    data = _estricto(text, Path(REG_PATH).name) or {}
    if 'rectificaciones' not in data:
        raise ValueError('registro.yaml: falta la clave `rectificaciones:`')
    existing = data.get('rectificaciones') or []
    have = {(r.get('entidad'), r.get('unidad'), r.get('decision'), r.get('dato')) for r in existing}
    seq = 0
    for row in existing:
        match = re.match(rf'^R-{re.escape(upd)}-(\d+)$', str(row.get('id') or ''))
        if match:
            seq = max(seq, int(match.group(1)))
    fresh = []
    for row in rows:
        key = (row['entidad'], row['unidad'], row['decision'], row['dato'])
        if key in have:
            continue
        have.add(key)
        seq += 1
        fresh.append({'id': f'R-{upd}-{seq:04d}', **row})
    if not fresh:
        return text, 0
    text = re.sub(r'^rectificaciones:[ \t]*\[[ \t]*\][ \t]*$', 'rectificaciones:', text, count=1, flags=re.M)
    if not re.search(r'^rectificaciones:[ \t]*$', text, re.M):
        raise ValueError('registro.yaml: `rectificaciones:` no es una lista en bloque')
    if not text.endswith('\n'):
        text += '\n'
    return text + ''.join(_dump_block(row) for row in fresh), len(fresh)


def _escribir_sincronizado(path, text):
    path.write_text(text, encoding='utf-8')
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _borrar(*paths):
    for path in paths:
        path.unlink(missing_ok=True)


def _marcador_valido(estado):
    """¿Deja el marcador comprobar los destinos? `destinos`, una lista no vacía de rutas relativas al repo (ni absolutas
    ni con `..`), sin repetir; `huellas` (los que existían) y `nuevos` (los que no), una partición exacta de `destinos`;
    cada huella, [tamaño entero ≥ 0, sha256 en 64 hex]. Un marcador sin esto no puede declarar nada coherente."""
    destinos, huellas, nuevos = estado.get('destinos'), estado.get('huellas'), estado.get('nuevos')
    if not (isinstance(destinos, list) and destinos and isinstance(huellas, dict) and isinstance(nuevos, list)):
        return False
    if not all(isinstance(rel, str) and rel and not Path(rel).is_absolute() and '..' not in Path(rel).parts
               for rel in (*destinos, *huellas, *nuevos)):
        return False
    if len(set(destinos)) != len(destinos) or len(set(nuevos)) != len(nuevos):
        return False
    if set(huellas) & set(nuevos) or set(huellas) | set(nuevos) != set(destinos):
        return False
    return all(isinstance(huella, list) and len(huella) == 2 and type(huella[0]) is int and huella[0] >= 0
               and isinstance(huella[1], str) and _SHA256.fullmatch(huella[1]) for huella in huellas.values())


def _diagnostico(repo):
    """El estado de un apply cortado según su marcador, o None si no lo hay: (fecha, distintos, nuevos presentes).
    `distintos` = los destinos que no coinciden con la huella (tamaño y sha256) que el marcador guardó antes del
    primer renombrado, cada uno con si queda su copia `.bak`; None si el marcador no deja comprobarlo (ilegible o fuera
    del esquema, `_marcador_valido`). Lo usan el aviso (`_apply_interrumpido`) y la reversión (`_deshacer`): que falten
    copias no demuestra una restauración."""
    repo = Path(repo)
    marcador = repo / MARCADOR_REL
    if not marcador.exists():
        return None
    try:
        estado = json.loads(marcador.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        estado = None
    estado = estado if isinstance(estado, dict) else {}
    fecha, huellas, nuevos = estado.get('fecha') or 'fecha desconocida', estado.get('huellas'), estado.get('nuevos')
    if not _marcador_valido(estado):
        return fecha, None, []
    distintos = []
    for rel, huella in sorted(huellas.items()):
        try:
            igual = list(_huella(repo / rel)) == list(huella)
        except OSError:
            igual = False
        if not igual:
            distintos.append((rel, (repo / f'{rel}.bak').exists()))
    return fecha, distintos, [rel for rel in nuevos if (repo / rel).exists()]


def _apply_interrumpido(repo):
    """El aviso de un apply cortado (su marcador sigue en el repo), o None. No se repara solo: `check`, `apply` y
    `record` se niegan mientras exista el marcador. Si cada destino coincide con su huella de antes del apply y no queda
    ninguno nuevo, el estado es coherente y se puede retirar el marcador; si no, la recuperación está incompleta: qué
    destinos no cuadran y si queda su copia, sin invitar a retirar el marcador (`_diagnostico`)."""
    diagnostico = _diagnostico(repo)
    if diagnostico is None:
        return None
    fecha, distintos, presentes = diagnostico
    aviso = f'apply interrumpido ({fecha})'
    if distintos is None:
        return (f'{aviso}: recuperación incompleta: el marcador ({MARCADOR_REL}) no deja comprobar los destinos; '
                'compruébalos y restáuralos a mano (sus `.bak` o su versión anterior en git)')
    if not distintos and not presentes:
        return (f'{aviso}: los destinos coinciden con su huella de antes del apply y no queda ninguno nuevo; el '
                f'estado es coherente: retira el marcador ({MARCADOR_REL}) y los `.tmp` y `.bak` que queden, y repite')
    partes = ([f'no coinciden con su huella de antes del apply: '
               + ', '.join(f'{rel} ({"queda su copia" if copia else "falta su copia"})' for rel, copia in distintos)]
              if distintos else [])
    partes += [f'no existían antes del apply: {", ".join(presentes)}'] if presentes else []
    return (f'{aviso}: recuperación incompleta; {"; ".join(partes)}. Restaura cada destino desde su copia (`mv '
            '<destino>.bak <destino>`) o, si falta, desde su versión anterior, borra los que no existían y repite el '
            f'`check`: el marcador ({MARCADOR_REL}) se queda hasta que el estado coincida con el de antes')


@contextlib.contextmanager
def _bloqueo(repo):
    """Exclusividad sobre el repo (spec 070): `flock` exclusivo y sin espera sobre `BLOQUEO_REL`, tomado antes de mirar
    el marcador o leer los overlays y soltado al terminar, haya ido bien, con error o tras deshacer. Si lo tiene otra
    ejecución (otro proceso, u otro hilo con su propio descriptor) → «otro apply en curso» y exit 2, sin tocar nada: dos
    a la vez compartirían copias y marcador, y la limpieza de una borraría los recursos de la otra."""
    path = Path(repo) / BLOQUEO_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f'  ❌ otro apply en curso ({BLOQUEO_REL} tomado): espera a que termine y repite')
            raise SystemExit(2) from None
        yield
    finally:
        os.close(fd)   # suelta el bloqueo


def _huella(path):
    datos = Path(path).read_bytes()
    return len(datos), hashlib.sha256(datos).hexdigest()


@contextlib.contextmanager
def _sigint_diferido():
    """Ctrl-C dentro del bloque solo se anota; si el bloque termina bien, se relanza como KeyboardInterrupt, ya con todo
    hecho. Lo que no se puede diferir (SIGKILL, un corte de luz) lo cubre el marcador de `_escribir_todo`."""
    recibidas = []
    anterior = signal.signal(signal.SIGINT, lambda signum, frame: recibidas.append(signum))
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, anterior)
    if recibidas:
        raise KeyboardInterrupt


def _deshacer(pares, copias, temporales, marcador, repo):
    """Devuelve TODOS los destinos a como estaban, se hayan llegado a renombrar o no: borra los que no existían y pone
    cada copia sobre su destino (`os.replace`). Solo si el diagnóstico del marcador (`_diagnostico`: cada destino con su
    huella de antes del apply y ningún nuevo) lo confirma, borra temporales, copias y marcador; si no, se quedan y el
    error es el aviso de `_apply_interrumpido`."""
    try:
        for path, _ in pares:   # antes que las copias: si no se puede, siguen todas para restaurar a mano
            if path not in copias:
                path.unlink(missing_ok=True)
    except OSError:
        raise RuntimeError(f'{_apply_interrumpido(repo)} (no se pudo deshacer)')
    for path, copia in copias.items():
        with contextlib.suppress(OSError):   # lo dice el diagnóstico
            os.replace(copia, path)
    _, distintos, presentes = _diagnostico(repo) or (None, None, [])
    if distintos is None or distintos or presentes:
        aviso = _apply_interrumpido(repo) or 'apply interrumpido: sin marcador, la reversión no se puede comprobar'
        raise RuntimeError(f'{aviso} (no se pudo deshacer)')
    _borrar(*temporales, *copias.values())
    marcador.unlink()


def _escribir_todo(pares, repo):
    """Escribe todos los destinos `(path, texto)` o ninguno (spec 070).

    1. Cada texto va a `<destino>.<pid>-<token>.tmp` en su mismo directorio (mismo sistema de ficheros), con fsync; el
       sufijo es de esta ejecución y su limpieza no toca otros temporales. Cada destino que ya existe se copia a
       `<destino>.bak`, y el marcador `MARCADOR_REL` guarda, antes del primer renombrado, los destinos, cuáles no
       existían y la huella (tamaño y sha256) de los que sí. Si algo de esto falla, se borra lo creado y se relanza:
       ningún destino ha cambiado. Copias y marcador no llevan sufijo: el bloqueo del repo (`_bloqueo`) impide dos
       ejecuciones a la vez.
    2. Cada temporal se renombra sobre su destino (`os.replace`, atómico por fichero). Si algo se interrumpe aquí,
       `_deshacer` devuelve TODOS los destinos a su copia (renombrados o no) y se relanza; si el diagnóstico no lo
       confirma, copias y marcador se quedan y el error dice qué no cuadra.
    3. Con todos renombrados se quita el marcador y después las copias: una caída entre los dos deja copias viejas,
       inofensivas, y no un marcador que mandaría deshacer lo ya escrito.
    Ctrl-C durante 1-3 solo se anota (`_sigint_diferido`) y se relanza al final, con todo escrito. Residual: una caída
    del proceso (SIGKILL, corte de luz) durante 1-2 deja el marcador; `check`, `apply` y `record` no arrancan mientras
    exista, y su aviso compara cada destino con su huella (`_apply_interrumpido`)."""
    repo = Path(repo)
    marcador = repo / MARCADOR_REL
    sufijo = f'.{os.getpid()}-{secrets.token_hex(4)}.tmp'   # de esta ejecución: su limpieza no toca otros temporales
    temporales = [path.with_name(path.name + sufijo) for path, _ in pares]
    copias = {path: path.with_name(path.name + '.bak') for path, _ in pares if path.exists()}
    with _sigint_diferido():
        try:
            for (_, text), tmp in zip(pares, temporales):
                _escribir_sincronizado(tmp, text)
            for path, copia in copias.items():
                shutil.copy2(path, copia)
            _escribir_sincronizado(marcador, json.dumps(
                {'fecha': now(), 'destinos': [str(path.relative_to(repo)) for path, _ in pares],
                 'nuevos': [str(path.relative_to(repo)) for path, _ in pares if path not in copias],
                 'huellas': {str(path.relative_to(repo)): list(_huella(path)) for path in copias}},
                ensure_ascii=False))
        except BaseException:
            _borrar(*temporales, *copias.values(), marcador)
            raise
        try:
            for (path, _), tmp in zip(pares, temporales):
                os.replace(tmp, path)
        except BaseException:
            _deshacer(pares, copias, temporales, marcador, repo)
            raise
        marcador.unlink()
        _borrar(*copias.values())


def _editorial_md(rows, upd):
    out = [f'# Tanda editorial 063 — {upd}', '',
           'Decisiones editoriales de la auditoría escalonada. **No se aplican automáticamente**',
           '(spec 063 AC9): cada ítem va por su ruta propia (feed del vigía, briefings, cases,',
           'patrones) y lo decide el dueño.', '', f'{len(rows)} ítem(s) con acción ≠ keep.', '']
    for row in rows:
        out += [f'## {row["unidad"]} — {row["entidad"]} · `{row["accion"]}`', '',
                f'- **Actual:** {row["actual"]}',
                f'- **Propuesto:** {row["texto"] or "(retirar)"}',
                f'- **Motivo:** {row["motivo"]}',
                f'- **Decisión:** `esc/decisions/{row["decision"]}`', '']
    return '\n'.join(out) + '\n'


def _published_prose(repo):
    """{entity_id: {'summary': texto, 'role': texto}} tal como lo publica HOY el overlay de contexto.

    Mismas reglas que `context_loader` (por eso se reutilizan sus funciones): el id de un rol es su
    `entity_id:` explícito o el derivado de type+name; gana el último fichero (los `*_qa061.yaml`
    al final); una fila de entidad sin `role` lo deja en '' (onMatch `coalesce(row.role, '')`); y
    las retiradas (`role: remove`, `remove: true`) ganan sobre cualquier escritura. Solo aparece la
    propiedad que alguna fila del overlay publica: sin fila, manda el texto del grafo."""
    from .context_loader import _prepare, _split_summaries, load_files
    entities, _, _, summaries, _ = load_files(Path(repo) / 'pipeline/data/context')
    nodes, _ = _prepare(entities, [])
    rows, removes = _split_summaries(summaries)
    out = {}
    for node in nodes:
        if not node['role_remove']:
            out.setdefault(node['entity_id'], {})['role'] = node['role'] or ''
    for node in nodes:
        if node['role_remove']:
            out.setdefault(node['entity_id'], {})['role'] = ''
    for row in rows:
        out.setdefault(row['entity_id'], {})['summary'] = row['resumen']
    for row in removes:
        out.setdefault(row['entity_id'], {})['summary'] = ''
    return out


def drift_check(decisions, repo, accept=()):
    """Una decisión de prosa reescribe el resumen/rol ENTERO de la entidad a partir del texto del
    censo. Si lo publicado ya no es ese texto (se reescribió después: 063, 29/30-sep), aplicarla
    deshacería la corrección posterior, aunque la frase auditada siga intacta: por eso se compara
    la propiedad COMPLETA del censo (`pack['census_prose']`) con la propiedad completa publicada,
    normalizadas (`_norm_prose`), y no cada unidad como subcadena. Si difieren, la decisión se
    rehace a mano sobre el texto vigente y se acepta por nombre (`--accept-drift`). El error nombra la
    decisión por su fichero, nunca la entidad (spec 071 AC87)."""
    published, errors = _published_prose(repo), []
    for decision in decisions:
        if decision.get('_file') in set(accept):
            continue  # decisión rehecha a mano sobre el texto vigente (lista explícita, revisada)
        pack = decision.get('_pack') or {}
        eid = (pack.get('entity') or {}).get('id')
        current = published.get(eid) or {}
        base = pack.get('census_prose') or {}
        summary, role = decision.get('summary') or {}, decision.get('role') or {}
        # Re-ejecución: si lo publicado ya es lo que la decisión escribe, no hay deriva.
        target = {'summary': '' if summary.get('action') == 'remove' else summary.get('resumen'),
                  'role': '' if role.get('action') == 'remove' else role.get('role')}
        for which, d in (('summary', summary), ('role', role)):
            # Sin fila en el overlay el texto viene del grafo (extracción): el del censo sigue siendo el vigente.
            if d.get('action') not in ('replace', 'remove') or which not in current:
                continue
            now_text = _norm_prose(current[which])
            if now_text == _norm_prose(target[which]) or now_text == _norm_prose(base.get(which)):
                continue
            errors.append(f'{decision.get("_file", "?")}: deriva — el {which} publicado de la entidad ya no es '
                          f'el del censo sobre el que se decidió; rehacer sobre el texto vigente')
    return errors


def apply(root, repo, decisions, tag, upd, dry_run=False, check=False, accept_drift=(), publicos=None, export=None,
          reparacion=False):
    """Gatea y escribe los overlays. `check` solo valida; `dry_run` imprime el plan. Idempotente. `publicos`: los
    documentos del árbol público de `repo` (por defecto, los que sigue git: `seudonimos.documentos_publicos`). `export`:
    el export del grafo con el que se prueba una cita conservada (AC119, `--snapshot`). Todo bajo el bloqueo del repo
    (`_bloqueo`): con otro apply en curso, exit 2 sin tocar nada. Una `reparacion` (spec 071 AC45, `--decisiones`)
    materializa decisiones de packs ya aplicados con los mismos gates y deja traza privada. Una operación con una
    carga que el loader descarta para antes del gate, con exit 2 (spec 071 AC78), y una decisión de un pack que no
    existe o que no contiene su unidad, también (AC106)."""
    with _bloqueo(repo):
        return _apply(root, repo, decisions, tag, upd, dry_run, check, accept_drift, publicos, export, reparacion)


def _apply(root, repo, decisions, tag, upd, dry_run, check, accept_drift, publicos, export=None,
           reparacion=False):
    """`apply` con el bloqueo ya tomado (también lo usa `main`, que lo toma antes de mirar el marcador)."""
    root, repo = Path(root), Path(repo)
    interrumpido = _apply_interrumpido(repo)
    if interrumpido:
        print('  ❌', interrumpido)
        raise SystemExit(1)
    validar_cargas(decisions)
    validar_resoluciones(decisions)
    seudos = seudonimos.ids_seudonimos(repo / seudonimos.PSEUDONYMS_REL)
    publicos = seudonimos.documentos_publicos(repo) if publicos is None else frozenset(publicos)
    personas = seudonimos.personas_contexto(repo)
    vigente = seudonimos.cargar_permitidos(repo / seudonimos.PERMITIDOS_REL)
    invalidos = seudonimos.alias_invalidos_contexto(repo, seudos, vigente)   # AC134: errores de la tanda (abajo)
    civiles = seudonimos.indice_civiles(personas, seudos, vigente, [(s, a) for _, _, s, a in invalidos])
    muestras = seudonimos.muestras_seudonimos(repo / seudonimos.PSEUDONYMS_REL)
    nombres = seudonimos.nombres_contexto(repo)
    citas = vigente.get('citas') or []
    documentos = seudonimos.censo_documentos(repo, publicos)
    roster = [f'{seudonimos.PSEUDONYMS_REL}:{linea}: {motivo}'
              for linea, motivo in seudonimos.problemas_roster(repo, seudos, personas)]
    roster += [f'{fichero}:{linea}: {seudonimos.MOTIVOS["alias-capa"]} (huella {seudonimos.sha16(alias)})'
               for fichero, linea, _, alias in invalidos]
    roster += [f'{fichero}:{linea}: {seudonimos.MOTIVOS["alias-identificador"]} (huella {seudonimos.sha16(alias)})'
               for fichero, linea, alias in seudonimos.alias_identificadores_contexto(repo, seudos)]   # AC167
    export = seudonimos.literal_del_export(export) if export else None
    conn = connect(root)
    try:
        validar_packs(conn, decisions)
        sociedades = indice_sociedades(conn, nombres, export)   # AC153: uno por ejecución, para el gate y el control
        errors, coverage = gate(conn, decisions, root, seudos, publicos, civiles, muestras, nombres, citas, documentos,
                                roster, export, sociedades)
    finally:
        conn.close()
    if not errors:
        errors = drift_check(decisions, repo, accept_drift)
    if not errors:
        permitidos = seudonimos.cargar_permitidos(repo / seudonimos.PERMITIDOS_REL)
        errors = _errores_permitidos(permitidos, seudos, 'vigente', publicos, civiles)
    if not errors:
        rows = materialize(decisions, tag, upd, seudos, publicos, reparacion=reparacion)
        conn = connect(root)
        try:
            evidencias = _evidencias_censo(conn, rows['seudonimos'])
        finally:
            conn.close()
        seudo_cambios, errors = _aplicar_seudonimos(permitidos, rows['seudonimos'], upd, evidencias, publicos)
        _nombra_las_de_la_lista(rows, decisions, seudos, permitidos)
        if not errors:
            errors = _suprimidos_conservados(rows['rectificaciones'], rows['seudonimos'], permitidos, seudos)
        if not errors:
            errors = _errores_permitidos(permitidos, seudos, 'resultante', publicos, civiles)
        if not errors:
            conservadas = {uid: dato for d in decisions for uid, dato in (d.get('_conservadas') or {}).items()}
            errors = _prosa_publica(rows, seudos, civiles, publicos, nombres, citas, documentos, roster, export,
                                    conservadas, sociedades, permitidos)
    if not errors:
        errors = _accusations_check(materialize(decisions, tag, upd, seudos, publicos)['capas'], repo)
    if not errors:
        errors = _guarda_fix_deny(materialize(decisions, tag, upd, seudos, publicos), repo, _aristas_censo(root), root)
    if not errors:
        errors, avisos, _ = _contraste_reglas(root, repo, decisions)
        for aviso in avisos:
            print('  aviso:', aviso)
    for error in errors:
        print('  ❌', error)
    if errors:
        raise SystemExit(1)
    plan = {k: len(v) for k, v in rows.items()}
    print(f'{coverage["decisiones"]} decisiones · {coverage["unidades"]} unidades · '
          f'{coverage["entidades"]} entidades · {coverage["por_resolucion"]}')
    print('plan:', json.dumps(plan, ensure_ascii=False))
    if check or dry_run:
        if dry_run:
            # Las aristas seudónimo↔sociedad/organismo que la tanda retira, por unidad (`_imprimible`).
            retiradas = {a['unidad']: a['seudonimo'] for a in rows['seudonimos']
                         if a['accion'] == 'retirar' and 'otro' in a}
            for bucket in ('summaries', 'roles', 'alias_deny', 'deny', 'fix', 'estado', 'rectificaciones', 'capas',
                           'seudonimos'):
                for row in rows[bucket]:
                    print(f'  {bucket:15s}',
                          json.dumps(_imprimible(bucket, row, seudos, retiradas, publicos), ensure_ascii=False)[:220])
        print('OK (check)' if check else 'OK (dry-run) — nada escrito')
        return {'plan': plan, 'coverage': coverage, 'written': {}, 'rows': rows}

    sum_path, rol_path, rel_path = repo / SUM_PATH, repo / ROL_PATH, repo / REL_PATH
    summaries, roles = sum_path.read_text(encoding='utf-8'), rol_path.read_text(encoding='utf-8')
    for row in rows['summaries']:
        summaries, how = replace_or_append_summary(summaries, row)
        print(f'summaries {how:15s} {row["entity_id"]}')
    for row in rows['roles']:
        roles, how = replace_or_insert_role(roles, row)
        print(f'roles     {how:15s} {row["entity_id"]}')
    relations, counts = _write_relations(rel_path.read_text(encoding='utf-8'), rows, tag, seudos)

    reg_path = repo / REG_PATH
    registro = reg_path.read_text(encoding='utf-8') if reg_path.exists() else REGISTRO_HEADER
    registro, n_reg = _write_registro(registro, rows['rectificaciones'], upd)
    perm_path = repo / seudonimos.PERMITIDOS_REL
    texto_permitidos = seudonimos.volcar_permitidos(permitidos)

    # Los YAML tienen que seguir cargando, sin claves repetidas (AC108) y sin duplicar entidades.
    data = _estricto(summaries, sum_path.name)['summaries']
    ids = [row['entity_id'] for row in data]
    assert len(ids) == len(set(ids)), 'summaries duplicados'
    for row in data:
        assert not (row.get('remove') and (row.get('resumen') or '').strip())
    rdata = _estricto(roles, rol_path.name)
    assert 'entities' in rdata and rdata.get('relations') == []
    rids = [row.get('entity_id') or row['name'] for row in rdata['entities']]
    assert len(rids) == len(set(rids)), 'roles duplicados'
    _estricto(relations, rel_path.name)
    _estricto(registro, reg_path.name)

    # Todo serializado y comprobado antes de escribir, y escrito todo o nada: un fallo a mitad dejaría el registro
    # diciendo «suprimido» de lo que la lista vieja aún conserva.
    escribir = [(sum_path, summaries), (rol_path, roles), (rel_path, relations), (reg_path, registro)]
    if perm_path.read_text(encoding='utf-8') != texto_permitidos:   # solo si cambia: idempotente
        escribir.append((perm_path, texto_permitidos))
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    _escribir_todo(escribir, repo)

    report = root / 'esc/report'
    report.mkdir(parents=True, exist_ok=True)
    estado_path = report / f'estado-{upd}.yaml'
    estado_path.write_text(
        f'# Bloques `estado:` para pegar en pipeline/data/accusations/accusations.yaml — {tag}.\n'
        '# El apply NO toca accusations.yaml: el registro de estado procesal lo edita el dueño.\n'
        + (yaml.safe_dump({'accusations': rows['estado']}, allow_unicode=True, sort_keys=False, width=110)
           if rows['estado'] else 'accusations: []\n'), encoding='utf-8')
    capas_path = report / f'capas-{upd}.md'
    capas_path.write_text(_capas_md(rows['capas'], upd), encoding='utf-8')
    editorial_path = report / f'tanda-editorial-{upd}.md'
    editorial_path.write_text(_editorial_md(rows['editorial'], upd), encoding='utf-8')
    seudonimos_path = report / f'seudonimos-{upd}.md'
    seudonimos_path.write_text(_seudonimos_md(rows['seudonimos'], seudo_cambios, upd, rows['motivos']),
                               encoding='utf-8')
    evidencias_path = report / f'evidencias-{upd}.md'
    evidencias_path.write_text(_evidencias_md(rows['evidencias'], upd), encoding='utf-8')

    written = {'summaries': len(rows['summaries']), 'roles': len(rows['roles']), **dict(counts),
               'rectificaciones': n_reg, 'estado': len(rows['estado']), 'editorial': len(rows['editorial']),
               'permitidos': dict(seudo_cambios)}
    print('escrito:', json.dumps(written, ensure_ascii=False))
    print(f'estado (a mano, NO aplicado): {estado_path}')
    print(f'tanda editorial (NO aplicada): {editorial_path}')
    print(f'capas a editar a mano ({len(rows["capas"])}): {capas_path}')
    print(f'seudónimos (privado, sin reglas públicas): {seudonimos_path}')
    print(f'evidencias del registro (privado): {evidencias_path}')
    out = {'plan': plan, 'coverage': coverage, 'written': written, 'rows': rows,
           'estado_path': str(estado_path), 'editorial_path': str(editorial_path), 'capas_path': str(capas_path),
           'seudonimos_path': str(seudonimos_path), 'evidencias_path': str(evidencias_path)}
    if reparacion:
        traza = report / f'reparacion-{upd}.md'
        traza.write_text(f'# Reparación {upd} (spec 071 AC45): decisiones de packs ya aplicados\n\n' + ''.join(
            f'- `{d.get("_file", "?")}` ({(d.get("_meta") or {}).get("resolution")}): '
            + ', '.join(f'{op.get("action")} {nombre_de_unidad(op.get("unit_id"), f"operación {n}")}'
                        for n, op in enumerate(d.get('edges') or [], 1) if op.get('action') in ACCIONES_REGLA) + '\n'
            for d in decisions)
            + f'\nescrito: {json.dumps(written, ensure_ascii=False)}\n', encoding='utf-8')
        print(f'traza de la reparación: {traza}')
        out['traza_path'] = str(traza)
    return out


# --- censo ---------------------------------------------------------------------------------------

def _still_pending(conn, pack_id):
    """Unidades del pack aún sin cerrar (`pending` o `needs-human`): con ellas el pack no se marca
    `applied`. Un apply acotado con --resolutions cierra solo UNA de las decisiones del pack, y
    `skip_applied` descartaría después las demás (revisión Codex del PR #86)."""
    row = conn.execute('SELECT unit_ids FROM esc_packs WHERE pack_id=?', (pack_id,)).fetchone()
    pending = []
    for uid in json.loads(row['unit_ids'] or '[]') if row else []:
        unit = conn.execute('SELECT state FROM units WHERE unit_id=?', (uid,)).fetchone()
        if unit is not None and unit['state'] in ('pending', 'needs-human'):
            pending.append(uid)
    return pending


def record(conn, root, repo, decisions, res_prefix='', doc=''):
    """Cierra en el censo las unidades de las decisiones: backup, `verified`, resolución con
    sufijo `-corregido` si el veredicto lo es, un evento por unidad y `esc_packs.state='applied'`.
    `res_prefix` acota qué decisiones se registran (prefijo de `_meta.resolution`). AC44: si la operación vigente
    (AC46) de alguna arista de los packs que se registran (las de esta tanda y las de sus ficheros en disco) dice
    deny/retype/invert o corrige props (set_prop, AC79: solo su regla la materializa) y nada la materializa en el repo
    (una regla con su carga y el predicado del loader, o la capa de acusaciones o claims.yaml que ya no la declaran),
    no registra nada: «decisión sin materializar» y exit 1. Sin el export, una fila del registro no acredita nada
    (AC47). Una operación con una carga que el loader descarta tampoco: exit 2 (`alcance.vigentes`, AC78); ni una
    decisión de un pack que no existe o que no contiene su unidad (`validar_packs`, AC106). Un retype o un invert no lo
    materializa que la capa que lo recrea ya no declare la arista: solo una regla o la capa que declara su resultado
    (AC111)."""
    root = Path(root)
    wanted = [d for d in decisions
              if str((d.get('_meta') or {}).get('resolution') or '').startswith(res_prefix or '')]
    packs = {(d.get('_meta') or {}).get('pack_id') for d in wanted}
    en_disco = {d['_file']: d for d in load_decisions(root) if (d.get('_meta') or {}).get('pack_id') in packs}
    candidatas = list(en_disco.values()) + [d for d in wanted if d.get('_file') not in en_disco]
    validar_resoluciones(candidatas)
    validar_packs(conn, candidatas)
    pendientes = sin_materializar(conn, candidatas, _contexto_materializacion(repo, root, cobertura=False))
    for ref, uid, *_ in pendientes:
        print('  ❌', f'decisión sin materializar ({ref} {huella(uid, "?")})')
    if pendientes:
        raise SystemExit(1)
    plan = {}
    for decision in wanted:
        resolution = decision['_meta']['resolution']
        for unit in decision.get('units') or []:
            plan[unit['unit_id']] = (resolution, unit.get('verdict') == 'corregido', unit.get('evidence') or '')
    at = now()
    backup = root / f'census.pre-063-{at[:19].replace(":", "")}.sqlite'
    shutil.copy(root / 'census.sqlite', backup)
    counts = Counter()
    seq = conn.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
    for uid, (resolution, corrected, evidence) in plan.items():
        full = resolution + ('-corregido' if corrected else '')
        conn.execute("UPDATE units SET state='verified', resolution=? WHERE unit_id=?", (full, uid))
        seq += 1
        human = resolution.startswith('humano-')
        event = ('corregido-humano' if corrected else 'verificado-humano') if human else 'esc-cerrada'
        payload = {'evidence': evidence, 'resolution': full}
        if doc:
            payload['doc'] = doc
        conn.execute('INSERT INTO events(seq,at,unit_id,event,payload) VALUES (?,?,?,?,?)',
                     (seq, at, uid, event, canonical(payload)))
        counts[event] += 1
        counts['corregidas' if corrected else 'verificadas'] += 1
    for decision in wanted:
        pack_id = decision['_meta']['pack_id']
        if _still_pending(conn, pack_id):
            # Cierre parcial: al pack le quedan unidades sin cerrar (otra decisión o el dueño).
            counts['packs_parciales'] += 1
            continue
        conn.execute("UPDATE esc_packs SET state='applied' WHERE pack_id=?", (pack_id,))
        counts['packs'] += 1
    conn.commit()
    out = {'unidades': len(plan), 'backup': str(backup), **dict(counts)}
    print('censo:', json.dumps(out, ensure_ascii=False))
    return out


# --- privatización del overlay público (spec 071 AC117, AC119 y AC121) -----------------------------------------------

def _predicado(regla):
    """Lo que hace una regla deny en el grafo: su par sin dirección (el deny no la mira), su tipo, su documento y su
    trozo de cita tal como los lee el loader (un vacío no es un comodín, spec 071 AC151) y sus capas."""
    return (frozenset((regla.get('source_id'), regla.get('target_id'))), regla.get('type'), regla.get('doc'),
            regla.get('quote_contains'), tuple(regla.get('kinds') or ()))


def _escalar(texto):
    """Un escalar YAML entre comillas simples: vale para cualquier texto en una línea."""
    return "'" + str(texto).replace("'", "''") + "'"


def _opaca_en_flujo(regla):
    """La regla opaca `{unidad, declaraciones?, kinds?, reason}` en una línea de flujo, como las demás reglas del
    overlay; sin `declaraciones` si no resuelve ningún predicado privado (spec 071 AC127)."""
    partes = [f'unidad: "{regla["unidad"]}"']
    if regla.get('declaraciones'):
        partes.append(f'declaraciones: {int(regla["declaraciones"])}')
    if regla.get('kinds'):
        partes.append(f'kinds: [{", ".join(regla["kinds"])}]')
    partes.append(f'reason: {json.dumps(regla["reason"], ensure_ascii=False)}')
    return '{' + ', '.join(partes) + '}'


def _con_ediciones(texto, ediciones):
    """`texto` con cada (inicio, fin, nuevo) sustituido, de la última a la primera para no mover las demás."""
    for inicio, fin, nuevo in sorted(ediciones, reverse=True):
        texto = texto[:inicio] + nuevo + texto[fin:]
    return texto


def _seccion(nodo, nombre):
    return dict((k.value, v) for k, v in nodo.value).get(nombre) if isinstance(nodo, yaml.MappingNode) else None


def _fin_contenido(nodo, texto):
    """Dónde acaba en `texto` lo que escribe un nodo YAML: el de una colección en bloque termina donde empieza la
    siguiente, con el salto de línea, la sangría y los comentarios que la separan; aquí, al final de su último valor,
    sin los blancos que lo siguen."""
    fin = nodo.end_mark.index
    if isinstance(nodo, (yaml.MappingNode, yaml.SequenceNode)) and not nodo.flow_style and nodo.value:
        ultimo = nodo.value[-1]
        fin = _fin_contenido(ultimo[1] if isinstance(nodo, yaml.MappingNode) else ultimo, texto)
    while fin > nodo.start_mark.index and texto[fin - 1].isspace():
        fin -= 1
    return fin


# La etiqueta de lote con que el apply empieza el motivo de una regla («063 auditoría escalonada 2026-09-26», «063 tramo
# 6 (2026-10-03)»): lo único del motivo de una deny etiquetada que conserva su opaca (`alcance.LOTE`, spec 071 AC130).
_PREFIJO_LOTE = re.compile(LOTE)


def _dato_nombrado(dato, value, entidad):
    """([extremos], dato): el `dato` de una fila de arista con cada extremo anonimizado («Clase:[marcador]») sustituido
    por el de la arista `value` del censo, y el resto del texto intacto (spec 071 AC113), con los extremos que nombra
    (`registro_check.extremos_de_fila`). None si no tiene la forma de la fila de esa arista."""
    from scripts import registro_check as RC
    nombrados = RC.extremos_de_fila(dato, value, entidad)
    if nombrados is None:
        return None
    extremos, _, resto = nombrados
    nuevo = f'({extremos[0]}) -[{value.get("type")}]-> ({extremos[1]}){resto}'
    return None if RC.MARCADOR.search(nuevo) else (extremos, nuevo)


def _quitar(texto, item):
    """(inicio, fin, ''): la edición que borra de `texto` el elemento de lista `item` entero, desde el principio de su
    línea (su guion y su sangría) hasta el salto de línea que sigue a su último valor; los comentarios que lo siguen se
    quedan, los de dentro se van con él."""
    inicio, fin = texto.rfind('\n', 0, item.start_mark.index) + 1, _fin_contenido(item, texto)
    return inicio, fin + 1 if texto[fin:fin + 1] == '\n' else fin, ''


def _deny_del_texto(texto, nombre):
    """[(índice, nodo, entrada)] de la sección deny del overlay `texto`."""
    nodo, doc = yaml.compose(texto, Loader=yaml.SafeLoader), cargar_yaml(texto, nombre) or {}
    items = _seccion(nodo, 'deny')
    return list(zip(range(len(items.value)), items.value, doc.get('deny') or [])) if isinstance(
        items, yaml.SequenceNode) else []


def _predicados_de(clave, kinds, opacas, declaraciones):
    """Las reglas explícitas en que se resuelve la opaca de `clave` con `kinds`, preparadas como el loader prepara una
    deny explícita (`relations_loader._preparar_deny`): la de su unidad del censo, con esas capas, y las que declara en
    privado (`declaraciones`, aún sin escribir), cada una con las suyas, como `alcance.Opacas.resolver` (spec 071
    AC151). Una que el loader no prepara, o una clave sin identidad propia (solo predicados `ajena`, AC157): exit 2."""
    value = opacas.arista(clave)
    if not identidad_propia(value, declaraciones.get(clave, ())):
        _overlay_rechazado('privatizar: una clave privada sin identidad propia: ni arista del censo ni un predicado '
                           'suyo')
    crudas = ([{**regla_de(value), 'kinds': kinds}] if value is not None else []) + [
        {'source_id': d['source_id'], 'target_id': d['target_id'], 'type': d['type'], 'doc': d.get('doc'),
         'quote_contains': d.get('quote_contains'), 'kinds': d.get('kinds')} for d in declaraciones.get(clave, ())]
    out = []
    for cruda in crudas:
        fila = relations_loader._preparar_deny(cruda, registrar=False)
        if fila is None:
            _overlay_rechazado('privatizar: un predicado resuelto sin la forma de un deny')
        out.append(fila)
    return out


def _supervivientes(vivas):
    """Lo que sobrevive tras el overlay (`registro_check.tras_el_overlay`), comparable."""
    return sorted((clave[0], inicio, fin, json.dumps(props, sort_keys=True, default=str))
                  for clave, copias in vivas.items() for inicio, fin, props in copias)


def _predicado_privado(explicita):
    """El predicado que declara en privado la regla deny explicita `explicita`, ya preparada: sus campos tal como los
    lee el loader, sin los ausentes. Un documento o un trozo de cita vacíos no se pueden declarar (el fichero privado no
    los admite: en él, un comodín es un campo ausente; spec 071 AC151): exit 2."""
    for campo in ('doc', 'quote_contains'):
        if explicita.get(campo) is not None and not str(explicita[campo]).strip():
            _overlay_rechazado(f'privatizar: una regla deny con «{campo}» vacío no se puede declarar en privado')
    return {k: v for k, v in explicita.items()
            if k in ('source_id', 'target_id', 'type', 'doc', 'quote_contains', 'kinds') and v not in (None, [])}


def _fuera_del_repo(repo, rutas, validadas=False):
    """Para (exit 2) si alguna de `rutas` ({opción: ruta}, o pares) cae dentro del repo `repo` o de uno de sus
    worktrees: el parche lleva en sus líneas quitadas lo retirado, las declaraciones privadas sus extremos y el export
    todo (spec 071 AC141; las rutas finales de cada salida también, AC154). Con `validadas`, cada ruta tal cual la dejó
    `_salida_validada`, sin resolver nada después: la misma que se abrirá (AC177); si no, con sus enlaces resueltos. La
    pertenencia se mira también por identidad (dispositivo e inodo) de cada directorio de la ruta que ya existe, no
    solo por cómo se escribe: en un disco que no distingue mayúsculas, `/users/…` es el mismo directorio que
    `/Users/…`."""
    raices = {Path(repo).resolve()}
    try:
        lista = subprocess.run(['git', '-C', str(repo), 'worktree', 'list', '--porcelain'], capture_output=True,
                               text=True, check=True).stdout
        raices |= {Path(linea.split(' ', 1)[1]).resolve() for linea in lista.splitlines()
                   if linea.startswith('worktree ')}
    except (OSError, subprocess.CalledProcessError):
        pass
    identidades = set()
    for raiz in raices:
        try:
            st = os.stat(raiz)
        except OSError:
            continue
        identidades.add((st.st_dev, st.st_ino))
    for opcion, ruta in (rutas.items() if isinstance(rutas, dict) else rutas):
        destino = Path(ruta) if validadas else Path(ruta).resolve()
        if any(destino == raiz or raiz in destino.parents for raiz in raices) \
                or any((st.st_dev, st.st_ino) in identidades for st in _existentes(destino)):
            _overlay_rechazado(f'privatizar: {opcion} dentro del repo o de uno de sus worktrees')


def _existentes(ruta):
    """El `lstat` de cada directorio de la ruta `ruta` que existe (ella misma incluida), del más profundo a la raíz."""
    for parte in (ruta, *ruta.parents):
        try:
            yield os.lstat(parte)
        except OSError:
            continue


def _salida_validada(salida):
    """La ruta de `--patch` tal como se comprueba fuera del repo y se abre (spec 071 AC177): absoluta (una relativa,
    desde el directorio actual), sin ningún componente `.` ni `..` en lo escrito (exit 2 antes de normalizar nada:
    `abspath` los colapsa a la letra y `resolve` tras los enlaces, y lo que uno daba por fuera del repo el otro lo abría
    dentro) y sin un enlace en ninguno de sus componentes (`_sin_enlaces`)."""
    literal = os.fspath(salida)
    if any(parte in ('.', '..') for parte in literal.split(os.sep)):
        _overlay_rechazado('privatizar: --patch con un componente «.» o «..» (pásala resuelta)')
    if not os.path.isabs(literal):
        literal = os.path.join(os.getcwd(), literal)
    _sin_enlaces(literal, '--patch')
    return Path(literal)


def _sin_enlaces(ruta, opcion):
    """Para (exit 2) si algún componente que ya existe de la ruta `ruta` (absoluta, sin `..`) es un enlace: lo que se
    validó con sus enlaces resueltos (`_fuera_del_repo`) es lo que se abrirá (spec 071 AC167). En macOS `/tmp` y `/var`
    lo son: se pasa la ruta real."""
    actual = Path('/')
    for parte in Path(os.path.abspath(ruta)).parts[1:]:
        actual = actual / parte
        try:
            modo = os.lstat(actual).st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(modo):
            _overlay_rechazado(f'privatizar: {opcion} con un enlace en su ruta (pásala resuelta)')


def _directorio(ruta):
    """Un descriptor del directorio `ruta`, abierto componente a componente desde la raíz sin seguir ningún enlace
    (`O_NOFOLLOW`) y creando los que falten: lo que se escribe después, relativo a él, no puede acabar en otro sitio
    aunque alguien cambie la ruta mientras tanto (spec 071 AC167). Un enlace u otra cosa que no es un directorio: exit
    2."""
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for parte in Path(os.path.abspath(ruta)).parts[1:]:
            try:
                siguiente = os.open(parte, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(parte, 0o700, dir_fd=fd)
                siguiente = os.open(parte, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError:
                _overlay_rechazado('privatizar: --patch con un enlace (o algo que no es un directorio) en su ruta')
            os.close(fd)
            fd = siguiente
        return fd
    except BaseException:
        os.close(fd)
        raise


def _escribir_fuera(salida, contenidos):
    """Escribe cada `contenidos` ({nombre: texto}) en el directorio `salida` sin seguir ningún enlace (spec 071 AC154 y
    AC167): valida todos los destinos antes de escribir ninguno (un enlace o algo que no es un fichero: exit 2, sin
    tocar nada), los prepara en temporales nuevos, creados en exclusiva y relativos al descriptor del directorio
    validado, y solo entonces sustituye cada destino de una vez. Un rechazo deja los que había intactos."""
    fd = _directorio(salida)
    temporales = {}
    try:
        for nombre in contenidos:
            try:
                modo = os.stat(nombre, dir_fd=fd, follow_symlinks=False).st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(modo):
                _overlay_rechazado(f'privatizar: {nombre} es un enlace (o no es un fichero)')
        for nombre, contenido in contenidos.items():
            temporal = f'.{nombre}.{secrets.token_hex(8)}'
            descriptor = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            temporales[nombre] = temporal
            with os.fdopen(descriptor, 'w', encoding='utf-8') as fichero:
                fichero.write(contenido)
                fichero.flush()
                os.fsync(fichero.fileno())
        # `os.rename` (no `os.replace`, que en macOS no admite `dir_fd`) sustituye el destino de una vez en POSIX.
        for nombre in list(temporales):
            os.rename(temporales[nombre], nombre, src_dir_fd=fd, dst_dir_fd=fd)
            del temporales[nombre]
    finally:
        for temporal in temporales.values():
            try:
                os.unlink(temporal, dir_fd=fd)
            except FileNotFoundError:
                pass
        os.close(fd)


def _capas(kinds):
    """Las capas que alcanza una regla con esas `kinds`, como `alcance.alcanza_loader`: la judicial y la nula
    siempre."""
    return {None, 'judicial'} | set(kinds or ())


def _subsume(general, especifica):
    """Si la regla deny preparada `general` alcanza, en cualquier grafo, toda arista que alcanza `especifica` (spec 071
    AC138): su mismo tipo y par, su documento o ninguno, un trozo de cita contenido en el suyo o ninguno (sin
    mayúsculas, como el loader) y sus capas o más. No mira lo que hay en el export: lo que no alcanza hoy puede
    alcanzarlo después de un retipo o en otro export."""
    if general.get('type') != especifica.get('type') or {general.get('source_id'), general.get('target_id')} \
            != {especifica.get('source_id'), especifica.get('target_id')}:
        return False
    if general.get('doc') is not None and general.get('doc') != especifica.get('doc'):
        return False
    trozo = general.get('quote_contains')
    if trozo is not None and (especifica.get('quote_contains') is None
                              or str(trozo).lower() not in str(especifica['quote_contains']).lower()):
        return False
    return _capas(especifica.get('kinds')) <= _capas(general.get('kinds'))


def _muerta(regla, deny, otras):
    """Si la regla `fix` preparada `regla` no cambia nada en ningún grafo (spec 071 AC138): todo lo que alcanza lo
    retira una regla deny (`_subsume`) tal como entra en ella y tal como sale (con su tipo nuevo y lo que escribe su
    `set`), y ninguna de las `otras` reglas `fix` de su par toca esos tipos (no se encadena con otra)."""
    entra = {'source_id': regla['source_id'], 'target_id': regla['target_id'], 'type': regla['type'],
             'doc': regla.get('doc'), 'quote_contains': regla.get('quote_contains'), 'kinds': regla.get('kinds')}
    pone = regla.get('set') or {}
    sale = {**entra, 'type': regla.get('corrected_type') or regla['type']}
    if 'source_doc' in pone:
        sale['doc'] = pone['source_doc']
    if 'source_quote' in pone:
        sale['quote_contains'] = pone['source_quote']
    if 'source_kind' in pone:
        sale['kinds'] = [pone['source_kind']]
    par = {regla['source_id'], regla['target_id']}
    if any({otra['source_id'], otra['target_id']} == par and otra.get('type') in (entra['type'], sale['type'])
           for otra in otras):
        return False
    return any(_subsume(d, entra) for d in deny) and any(_subsume(d, sale) for d in deny)


def _formula_d1(value, vivos, fix, tras):
    """El final de la fila «suprimido» de la arista `value` del censo cuyo par publica el grafo tras el overlay (`tras`)
    bajo los tipos `vivos` (spec 071 D1), literalmente verdad en cada caso: con una arista de su tipo, en su sentido y
    desde su documento, la fórmula de la copia duplicada (AC119); con una de un tipo al que una regla `fix` la retipa
    desde su documento (AC136), la de AC125; con una de su tipo desde otro documento o en el otro sentido, la de AC142
    sin tipos; y si no, la de AC142 con los tipos vivos (D3)."""
    from scripts import registro_check as RC
    tipo = value.get('type')
    if RC.tipos_publicados(value, tras, {tipo}):
        return RC.DUPLICADA
    retipos = RC.tipos_publicados(value, tras, RC.tipos_alcanzables(value, fix, tras) - {tipo})
    if len(retipos) == 1:
        return f'{RC.PUBLICADA}, rectificada como {next(iter(retipos))}'
    return f'arista retirada; {_sigue(tipo, vivos)}'


def _sigue(tipo, vivos):
    """La fórmula de AC142 para una arista de tipo `tipo` cuyo par publica el grafo bajo `vivos`: sin tipos si uno es el
    suyo, y si no, con ellos."""
    from scripts import registro_check as RC
    return RC.SIGUE if tipo in vivos else f'{RC.SIGUE} bajo {", ".join(sorted(vivos))}'


def _reglas_de_textos(textos, opacas, declaraciones):
    """(deny, fix) preparadas como el loader de los ficheros de relaciones `textos` ({ruta: texto}, aún sin escribir),
    cada opaca resuelta con las declaraciones privadas `declaraciones` (las nuevas, aún sin escribir)."""
    deny, fix = [], []
    for path, texto in textos.items():
        doc = cargar_yaml(texto, path.name) or {}
        for entrada in doc.get('deny') or []:
            if not es_opaca(entrada):
                deny += relations_loader._prepare_deny([entrada])
                continue
            deny += _predicados_de(entrada['unidad'], entrada.get('kinds'), opacas, declaraciones)
        fix += relations_loader._prepare_fix(doc.get('fix') or [])
    return deny, fix


def _universo(index, valores, reglas):
    """{(tipo, par): [(inicio, fin, props)]}: las aristas sobre las que se compara el overlay antes y después del parche
    (spec 071 AC138): las del export, las del censo y, por cada regla, sintéticas en cada sentido con su tipo, su trozo
    de cita como cita y la primera de sus capas (o la judicial), desde su documento y desde los documentos '', 'x.pdf',
    'otro.pdf' y ninguno (AC151: un documento vacío no es un comodín)."""
    from scripts import registro_check as RC
    out = defaultdict(list)
    for clave, copias in index.items():
        out[clave] += [(inicio, fin, dict(props)) for inicio, fin, props in copias]
    for value in valores.values():
        out[RC._par(value.get('type'), value.get('start'), value.get('end'))].append(
            (value.get('start'), value.get('end'), dict(value.get('props') or {})))
    for regla in reglas:
        for doc in dict.fromkeys((regla.get('doc'), '', 'x.pdf', 'otro.pdf', None)):
            props = {'source_kind': (regla.get('kinds') or ['judicial'])[0], 'source_doc': doc,
                     'source_quote': regla.get('quote_contains') or ''}
            for inicio, fin in ((regla['source_id'], regla['target_id']), (regla['target_id'], regla['source_id'])):
                out[RC._par(regla['type'], inicio, fin)].append((inicio, fin, dict(props)))
    return out


CABECERA_UNIDAD = '#     unidad: <unit_id del censo, o ""'
CABECERA_CLAVE = '#     unidad: <clave de la decisión: unit_id del censo o clave privada, o ""'


def privatizar(root, repo, salida, snapshot, privadas_pedidas=(), duplicadas=(), filas_privadas=None, nombrar=()):
    """Escribe en `salida`, sin tocar nada del repo ni de la auditoría, el parche de datos de la spec 071 AC117
    (`datos-071-e21.patch`, sobre el overlay de relaciones y el registro) y las declaraciones privadas de las reglas
    opacas y de las filas con marcador sin unidad (`reglas_opacas.yaml`, para la raíz de la auditoría, AC121 y AC134).
    `salida`, la ruta final de cada fichero (que se escribe sin seguir un enlace: AC154) y el export `snapshot` van
    fuera del repo y de sus worktrees (AC141), y las decisiones del censo se validan como en los demás mandos: sus
    cargas, sus resoluciones y sus packs (AC139). Por pasos sobre el texto:
    0. D1: la fila «suprimido» marcada cuyo par publica el grafo tras el overlay (alguna arista viva entre sus dos
       nodos, en cualquier dirección, tipo, documento o capa) no oculta nada: nombra sus dos extremos, cada uno una vez
       y en la orientación de su dato (AC153), con la fórmula de su caso (`_formula_d1`: AC119, AC125 o AC142), y
       también sus filas anteriores marcadas; su regla no se hace
       opaca. Si su par no está publicado y una gemela suya que su decisión conserva está en el export y el overlay la
       mata, es un hecho perdido. Cada `nombrar` (R-…, una fila marcada de una arista del censo) nombra sus extremos
       igual, por decisión del dueño, sin fórmula, y su regla se queda explícita;
    1. cada regla deny etiquetada con la huella de una fila «suprimido» que sigue marcada pasa a opaca (`{unidad,
       kinds?, reason}`, con la etiqueta de su lote, la de su unidad y un motivo neutro, AC114), una por unidad y capas;
       si su predicado no es el que da el censo (el par actual de una arista re-identificada, otro trozo de su cita), va
       a las declaraciones privadas de su unidad, con sus capas (un documento o un trozo de cita vacíos no se pueden
       declarar: allí un comodín es un campo ausente, AC151). Cada `privadas_pedidas` («fichero:deny[n]=R-…») cuyo par
       no publica el grafo pasa a opaca bajo una clave privada nueva (un sha256 al azar, que nada público deriva), con
       su par, su tipo, su documento y sus capas en privado, sin cita, y la fila R-… lleva esa clave en `unidad`; si el
       grafo publica su par, la regla se queda y la fila nombra sus extremos con los tipos vivos (AC142);
    2. en el registro: cada fila marcada de una unidad cuya fila vigente no la retira nombra sus dos extremos (AC113)
       si la decisión vigente de su unidad la conserva y el grafo publica tras el overlay una copia identificada suya en
       el estado decidido (AC139; si no, se queda y se lista por su id; la que no tiene unidad conserva el marcador,
       AC123); cada `duplicadas` (R-…, una «suprimido» que acaba en «arista retirada») lleva la fórmula de AC119; y la
       cabecera dice que `unidad` es la clave de la decisión;
    3. AC124: con el registro y el overlay del paso 2, cada regla deny de los ficheros de relaciones que permite
       reconstruir una fila anonimizada (`registro_check.hallazgos`, AC122-bis) se borra si un predicado de la opaca de
       su unidad la subsume (`_subsume`, AC138) y, si no, pasa a privado bajo esa clave; y cada regla `fix` que la
       reconstruye se borra solo si está muerta en cualquier grafo (`_muerta`);
    4. cada opaca dice cuántos predicados privados resuelve su clave (AC127) y las declaraciones privadas llevan, en
       `filas:`, las de las filas que siguen marcadas sin unidad (AC134), que da `filas_privadas` (un YAML privado
       `{R-…: [predicado]}`, fuera del repo): tiene que haber una para cada una;
    5. antes de escribir nada, lo que sobrevive al overlay con el parche es lo mismo que sin él (`_universo`: las
       aristas del export, las del censo y sintéticas por regla, también desde los documentos '', 'x.pdf', 'otro.pdf' y
       ninguno: AC138 y AC151).
    Idempotente (AC140): lo ya hecho (una opaca, una fila que ya nombra sus extremos o lleva su fórmula, una pedida cuya
    fila ya tiene clave o nombre, la cabecera nueva) se reconoce y no se toca, y repetirlo sobre su resultado da un
    parche vacío. Para (exit 2, sin ningún valor) si algo pedido no está o no tiene la forma esperada, si hay un hecho
    perdido, si borrar una `fix` cambiaría algo o si el parche cambia lo que sobrevive. Imprime los recuentos y lo que
    AC124 no remedia, por fichero y sección."""
    from scripts import registro_check as RC
    root, repo, snapshot = Path(root), Path(repo), Path(snapshot)
    salida = _salida_validada(salida)
    _fuera_del_repo(repo, {'--patch': salida}, validadas=True)
    _fuera_del_repo(repo, {'--snapshot': snapshot})
    previo, relations_loader.log = relations_loader.log, _SinLog()
    sin_publicar, perdidos, residuo, cuenta, muertas_vivas = [], [], Counter(), Counter(), []
    try:
        decisiones = load_decisions(root)
        validar_cargas(decisiones)
        validar_resoluciones(decisiones)
        conn = connect(root)
        try:
            validar_packs(conn, decisiones)
        finally:
            conn.close()
        en_vigor = vigentes(decisiones)
        index, _ = RC.export_vivo(snapshot)
        opacas = Opacas(root)
        valores = RC.aristas_censales(opacas.conn)
        filas_decl = {}
        if filas_privadas is not None:
            _, filas_decl, _ = declaraciones_de({'filas': cargar_yaml(Path(filas_privadas).read_text(encoding='utf-8'),
                                                                   Path(filas_privadas).name)},
                                             Path(filas_privadas).name)
        filas_decl = {**opacas.filas, **filas_decl}
        deny0, fix0 = _reglas_del_overlay(repo, root, cobertura=False)
        reglas_fix = [r for _, r in fix0]
        tras = RC.tras_el_overlay(index, [r for _, r in deny0], reglas_fix)
        vivos_por_par = defaultdict(set)
        for (tipo, par), copias in tras.items():
            if copias:
                vivos_por_par[par].add(tipo)
        reg_path = repo / REG_PATH
        rutas = [*relations_loader._ordered(repo / REL_DIR), reg_path]
        originales = {path: path.read_text(encoding='utf-8') for path in rutas}
        filas = (cargar_yaml(originales[reg_path], reg_path.name) or {}).get('rectificaciones') or []
        por_id = {f.get('id'): f for f in filas if isinstance(f, dict)}
        vigentes_ = RC.vigentes_por_unidad([f for f in filas if isinstance(f, dict)])

        # 0. D1, antes que nada: la unidad de una fila marcada cuyo par (los extremos que la fila nombra) publica el
        # grafo tras el overlay; su «suprimido» vigente acaba con la fórmula de su caso.
        asociadas = {}
        for f in filas:
            value = opacas.arista(f['unidad']) if isinstance(f, dict) and RC._forma(RC._UNIDAD, f.get('unidad')) \
                else None
            if value is None or not RC.MARCADOR.search(str(f.get('dato') or '')):
                continue
            nombrados = RC.extremos_de_fila(f['dato'], value, f.get('entidad'))
            vivos = vivos_por_par.get(frozenset(nombrados[0]), set()) if nombrados else set()
            if vivos:
                vigente = vigentes_[f['unidad']][0] == f.get('id') and f.get('decision') == 'suprimido' \
                    and str(f['dato']).endswith(': arista retirada')
                asociadas.setdefault(f['unidad'], None)
                if vigente:
                    asociadas[f['unidad']] = _formula_d1(value, vivos, reglas_fix, tras)
                continue
            if f.get('decision') != 'suprimido' or vigentes_[f['unidad']][0] != f.get('id') \
                    or not str(f['dato']).endswith(': arista retirada'):
                continue
            tipo, par = value.get('type'), {value.get('start'), value.get('end')}
            doc = (value.get('props') or {}).get('source_doc') or None
            retipadas = RC.tipos_alcanzables(value, reglas_fix, tras)
            gemelas = [otra for otra, otro in valores.items() if otra != f['unidad']
                       and {otro.get('start'), otro.get('end')} == par
                       and ((otro.get('props') or {}).get('source_doc') or None) == doc
                       and otro.get('type') in retipadas
                       and (en_vigor.get(otra) or (None, {}))[1].get('action') in RC.CONSERVAN
                       and index.get(RC._par(otro.get('type'), otro.get('start'), otro.get('end')))]
            if gemelas:
                perdidos.append(f'{f.get("id")} [{f["unidad"][:8]}] ← '
                                + ', '.join(f'[{g[:8]}]' for g in sorted(gemelas)))
        if perdidos:
            print('privatizar: hechos perdidos (una gemela conservada que el overlay mata con la retirada; '
                  'reposición en el Path A, nunca por prosa):', '; '.join(perdidos), file=sys.stderr)
            raise SystemExit(2)
        for rid in nombrar:
            fila = por_id.get(rid)
            if fila is None:
                _overlay_rechazado('privatizar: una --nombrar nombra una fila que no está en el registro')
            if not RC.MARCADOR.search(str(fila.get('dato') or '')):
                cuenta['nombradas ya aplicadas'] += 1  # AC140
            elif not RC._forma(RC._UNIDAD, fila.get('unidad')) or opacas.arista(fila['unidad']) is None:
                _overlay_rechazado(f'privatizar: --nombrar {rid} no es una fila de una arista del censo')
            elif fila['unidad'] not in asociadas:
                asociadas[fila['unidad']] = None
                cuenta['unidades nombradas por decisión del dueño'] += 1
        retiradas = {f['unidad']: f for f in filas if isinstance(f, dict) and f.get('decision') == 'suprimido'
                     and RC.MARCADOR.search(str(f.get('dato') or '')) and RC._forma(RC._UNIDAD, f.get('unidad'))
                     and opacas.arista(f['unidad']) is not None and f['unidad'] not in asociadas}
        por_huella = defaultdict(list)
        for unidad in retiradas:
            por_huella[unidad[:8]].append(unidad)
        pedidas = {}
        for pedida in privadas_pedidas:
            m = re.fullmatch(r'([\w.\-]+):deny\[(\d+)\]=(R-\d{4}-\d{2}-\d{2}-\d{4})', pedida)
            if not m:
                _overlay_rechazado('privatizar: --privada sin la forma «fichero:deny[n]=R-AAAA-MM-DD-NNNN»')
            fila = por_id.get(m.group(3))
            if fila is None:
                _overlay_rechazado('privatizar: una --privada nombra una fila que no está en el registro')
            if fila.get('unidad') or not RC.MARCADOR.search(str(fila.get('dato') or '')):
                cuenta['pedidas ya aplicadas'] += 1  # AC140: la fila ya lleva su clave o ya nombra sus extremos
                continue
            pedidas[(m.group(1), int(m.group(2)))] = m.group(3)
        declaraciones = {k: [dict(p) for p in v] for k, v in opacas.privadas.items()}

        # 1. Las reglas etiquetadas y las pedidas, a opacas: una por clave y capas.
        textos, claves_filas, nombrar_filas, opacas_puestas = dict(originales), {}, {}, {}
        for path in rutas[:-1]:
            ediciones = []
            for i, item, entrada in _deny_del_texto(textos[path], path.name):
                pedida = pedidas.pop((path.name, i), None)
                marca = next((m for m in _TAG.finditer(str((entrada or {}).get('reason') or ''))
                              if len(por_huella.get(m.group(1), ())) == 1), None)
                if es_opaca(entrada):
                    if pedida:
                        _overlay_rechazado(f'privatizar: --privada {path.name}:deny[{i}] es ya una regla opaca')
                    continue
                if not (pedida or marca):
                    continue
                explicita, = relations_loader._prepare_deny([entrada])
                if pedida:
                    vivos = vivos_por_par.get(frozenset((explicita['source_id'], explicita['target_id'])), set())
                    if vivos:
                        # D1: el grafo publica su par; la regla se queda y la fila nombra sus extremos (AC142).
                        nombrar_filas[pedida] = (explicita, sorted(vivos))
                        continue
                    clave = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
                    declaraciones[clave] = [_predicado_privado({**explicita, 'quote_contains': None})]
                    claves_filas[pedida] = clave
                    nueva = {'unidad': clave, 'kinds': explicita.get('kinds'), 'reason': MOTIVO_OPACO}
                    cuenta['clave privada nueva'] += 1
                else:
                    clave = por_huella[marca.group(1)][0]
                    censal = {**regla_de(opacas.arista(clave)), 'kinds': explicita.get('kinds')}
                    if _predicado(explicita) != _predicado(censal) \
                            and _predicado_privado(explicita) not in declaraciones.setdefault(clave, []):
                        declaraciones[clave].append(_predicado_privado(explicita))
                        cuenta['declaraciones privadas de un predicado explícito'] += 1
                    prefijo = str(entrada['reason'])[:marca.start()].strip()
                    if not _PREFIJO_LOTE.fullmatch(prefijo):
                        # Un motivo escrito a mano puede nombrar a quien la fila oculta: solo pasa la etiqueta del lote.
                        _overlay_rechazado(f'privatizar: {path.name}:deny[{i}] con un motivo que no empieza por '
                                           'la etiqueta de un lote')
                    nueva = {'unidad': clave, 'kinds': explicita.get('kinds'),
                             'reason': f'{prefijo} [{clave[:8]}]: {MOTIVO_OPACO}'.strip()}
                puesta = (nueva['unidad'], tuple(nueva.get('kinds') or ()))
                if puesta in opacas_puestas:
                    ediciones.append(_quitar(textos[path], item))  # la misma opaca dos veces: una basta
                    cuenta['reglas opacas repetidas quitadas'] += 1
                    continue
                opacas_puestas[puesta] = path
                ediciones.append((item.start_mark.index, _fin_contenido(item, textos[path]), _opaca_en_flujo(nueva)))
                cuenta['reglas opacas'] += 1
            textos[path] = _con_ediciones(textos[path], ediciones)
        if pedidas:
            _overlay_rechazado(f'privatizar: {len(pedidas)} --privada sin una regla deny en esa posición')
        for path in rutas[:-1]:
            # Las opacas que ya estaban (AC140): sus claves y capas cuentan como puestas.
            for _, _, entrada in _deny_del_texto(textos[path], path.name):
                if es_opaca(entrada):
                    opacas_puestas.setdefault((entrada['unidad'], tuple(entrada.get('kinds') or ())), path)

        # 2. El registro.
        reg_texto = textos[reg_path]
        nodo = _seccion(yaml.compose(reg_texto, Loader=yaml.SafeLoader), 'rectificaciones')
        faltan, editadas, ediciones = set(duplicadas) | set(claves_filas) | set(nombrar_filas), set(), []

        def dato_nuevo(fila, campos, dato, motivo):
            if fila.get('id') in editadas:
                _overlay_rechazado(f'privatizar: {fila.get("id")} recibiría dos datos distintos')
            editadas.add(fila.get('id'))
            ediciones.append((campos['dato'].start_mark.index, campos['dato'].end_mark.index, _escalar(dato)))
            cuenta[motivo] += 1

        for item, fila in zip(nodo.value if isinstance(nodo, yaml.SequenceNode) else [], filas):
            if not isinstance(fila, dict):
                continue
            campos = dict((k.value, v) for k, v in item.value)
            marcada = RC.MARCADOR.search(str(fila.get('dato') or ''))
            vigente = vigentes_[fila['unidad']] if fila.get('unidad') else None
            value = opacas.arista(fila['unidad']) if RC._forma(RC._UNIDAD, fila.get('unidad')) else None
            if marcada and fila.get('unidad') in asociadas:
                # D1 (con AC113 y AC139): el grafo publica su par y el marcador no protege nada; la fila nombra sus
                # extremos con lo que decía, y la «suprimido» vigente acaba con la fórmula de su caso.
                nombrado = _dato_nombrado(fila['dato'], value, fila.get('entidad'))
                if nombrado is None:
                    _overlay_rechazado(f'privatizar: {fila.get("id")} no tiene la forma de una fila de arista '
                                       'del censo')
                final = asociadas[fila['unidad']] if vigente[0] == fila.get('id') else None
                dato_nuevo(fila, campos, nombrado[1][:-len('arista retirada')] + final if final else nombrado[1],
                           'filas que nombran sus extremos: su par está publicado (D1)')
            elif marcada and vigente and vigente[1].get('decision') != 'suprimido':
                # Nombrar un extremo de un par que el grafo no publica sacaría a la luz a quien su nodo dejó fuera: la
                # fila se queda como está hasta que se decida su dato.
                sin_publicar.append(fila.get('id'))
            if fila.get('id') in duplicadas:
                faltan.discard(fila['id'])
                if str(fila.get('dato') or '').endswith(f': {RC.DUPLICADA}'):
                    cuenta['duplicadas ya aplicadas'] += 1  # AC140
                elif fila.get('decision') != 'suprimido' \
                        or not str(fila.get('dato') or '').endswith(': arista retirada'):
                    _overlay_rechazado(f'privatizar: {fila["id"]} no es una «suprimido» que acaba en «arista '
                                       'retirada»')
                else:
                    dato_nuevo(fila, campos, str(fila['dato'])[:-len('arista retirada')] + RC.DUPLICADA,
                               'filas con la fórmula de duplicada')
            if fila.get('id') in claves_filas:
                faltan.discard(fila['id'])
                if fila.get('unidad') != '' or fila.get('decision') != 'suprimido':
                    _overlay_rechazado(f'privatizar: {fila["id"]} no es una «suprimido» sin unidad')
                ediciones.append((campos['unidad'].start_mark.index, campos['unidad'].end_mark.index,
                                  _escalar(claves_filas[fila['id']])))
                cuenta['filas con una clave privada'] += 1
            if fila.get('id') in nombrar_filas:
                faltan.discard(fila['id'])
                explicita, vivos = nombrar_filas[fila['id']]
                regla = {'start': explicita['source_id'], 'end': explicita['target_id'], 'type': explicita['type']}
                nombrado = _dato_nombrado(fila.get('dato'), regla, fila.get('entidad'))
                if nombrado is None or fila.get('decision') != 'suprimido':
                    _overlay_rechazado(f'privatizar: {fila["id"]} no tiene la forma de la fila de su regla')
                dato_nuevo(fila, campos, f'{nombrado[1]}; {_sigue(explicita["type"], vivos)}',
                           'filas sin unidad que nombran sus extremos: su par está publicado (D1)')
                # Sin unidad ni marcador, el registro la comprueba con su regla pública (AC183): nada en privado.
        if faltan:
            _overlay_rechazado(f'privatizar: {len(faltan)} filas pedidas no están en el registro')
        sin_unidad = {f.get('id') for f in filas if isinstance(f, dict) and not f.get('unidad')
                      and f.get('id') not in claves_filas and f.get('id') not in nombrar_filas
                      and RC.MARCADOR.search(str(f.get('dato') or ''))}
        # Una declaración privada es de una fila sin unidad con marcador (AC134 y AC183): la «suprimido» que nombra sus
        # extremos (D1) se comprueba con su regla pública.
        declarables = filas_declarables(filas) - set(claves_filas)
        if set(filas_decl) - declarables:
            _overlay_rechazado('privatizar: declaraciones privadas de filas que no son filas sin unidad declarables')
        if sin_unidad - set(filas_decl):
            _overlay_rechazado(f'privatizar: {len(sin_unidad - set(filas_decl))} filas con marcador sin unidad ni '
                               'declaración privada (AC134)')
        if filas_decl:
            cuenta['filas sin unidad con su declaración privada'] = len(filas_decl)
        if reg_texto.count(CABECERA_UNIDAD) == 1:
            inicio = reg_texto.index(CABECERA_UNIDAD)
            ediciones.append((inicio, inicio + len(CABECERA_UNIDAD), CABECERA_CLAVE))
        elif reg_texto.count(CABECERA_CLAVE) != 1:
            _overlay_rechazado('privatizar: la cabecera del registro no describe `unidad` como se espera')
        textos[reg_path] = _con_ediciones(reg_texto, ediciones)

        # 3. AC124, sobre el registro y el overlay del paso 2, en una copia de los datos públicos.
        with tempfile.TemporaryDirectory(prefix='privatizar-') as tmp:
            espejo = Path(tmp)
            shutil.copytree(repo / 'pipeline/data', espejo / 'pipeline/data',
                            ignore=lambda d, nombres: [n for n in nombres if n == 'audit'])
            for path, texto in textos.items():
                (espejo / path.relative_to(repo)).write_text(texto, encoding='utf-8')
            nombres = defaultdict(set, {k: set(v) for k, v in RC.nombres_del_export(snapshot).items()})
            filas2 = (cargar_yaml(textos[reg_path], reg_path.name) or {}).get('rectificaciones') or []
            hallados = RC.hallazgos([f for f in filas2 if isinstance(f, dict)], valores, declaraciones,
                                    RC.referencias(espejo, nombres) + RC.entradas_del_export(snapshot, tras), tras,
                                    nombres, filas_decl)
        por_regla = defaultdict(set)
        for rid, clave, entrada, apartado in hallados:
            if entrada is None:
                residuo[f'filas ({apartado})'] += 1
                continue
            seccion = entrada.posicion.rsplit('[', 1)[0] if entrada.seccion else entrada.posicion
            if apartado in ('feed', 'editorial', 'cita', 'grafo'):
                continue  # avisos: los decide el dueño o los publica el grafo
            if Path(entrada.fichero).parts[0] == 'relations' and entrada.seccion in ('deny', 'fix'):
                por_regla[entrada.posicion].add((rid, clave, apartado))
            else:
                residuo[f'{seccion} ({apartado})'] += 1
        deny_final = [r for _, r in deny0]
        ediciones = defaultdict(list)
        for path in rutas[:-1]:
            texto = textos[path]
            nodo = yaml.compose(texto, Loader=yaml.SafeLoader)
            doc = cargar_yaml(texto, path.name) or {}
            for seccion in ('deny', 'fix'):
                items = _seccion(nodo, seccion)
                for i, (item, entrada) in enumerate(zip(items.value if isinstance(items, yaml.SequenceNode) else [],
                                                        doc.get(seccion) or [])):
                    hallado = por_regla.pop(f'relations/{path.name}:{seccion}[{i}]', None)
                    if not hallado:
                        continue
                    if seccion == 'fix':
                        # Remedio 1: una `fix` que no cambia nada en ningún grafo (`_muerta`, AC138) se borra; una que
                        # el loader descarta, también. Las demás se quedan.
                        preparada = relations_loader._prepare_fix([entrada])
                        otras = [r for o, r in fix0 if o != f'{path.name}:fix[{i}]']
                        if preparada and not _muerta(preparada[0], deny_final, otras):
                            muertas_vivas.append(f'{path.name}:fix[{i}]')
                            continue
                        ediciones[path].append(_quitar(texto, item))
                        cuenta['fix muertas borradas (remedio 1)'] += 1
                        continue
                    explicita, = relations_loader._prepare_deny([entrada])
                    # Una fila sin clave (la de una declaración privada en `filas:`, AC134) no da la suya a un predicado
                    # ajeno: su regla se queda y se lista (sin la clave, la opaca no tendría identidad, AC157).
                    claves = sorted({clave for _, clave, _ in hallado if clave}, key=lambda c: min(
                        r for r, k, _ in hallado if k == c))
                    if not claves:
                        residuo[f'relations/{path.name}:deny (fila sin clave)'] += 1
                        continue
                    cubren = [regla for (unidad, kinds) in opacas_puestas if unidad in claves
                              for regla in _predicados_de(unidad, list(kinds) or None, opacas, declaraciones)]
                    if any(_subsume(regla, explicita) for regla in cubren):
                        ediciones[path].append(_quitar(texto, item))
                        cuenta['AC124: deny redundantes borradas'] += 1
                        continue
                    clave = claves[0]
                    # Su predicado privado lleva sus capas (AC151): la opaca de la unidad, con otras, no se las cambia.
                    suyas = [k for u, k in opacas_puestas if u == clave]
                    ajena = {**_predicado_privado(explicita), 'ajena': True}
                    if ajena not in declaraciones.setdefault(clave, []):
                        declaraciones[clave].append(ajena)
                    if suyas:
                        ediciones[path].append(_quitar(texto, item))
                    else:
                        opacas_puestas[(clave, tuple(explicita.get('kinds') or ()))] = path
                        ediciones[path].append((item.start_mark.index, _fin_contenido(item, texto), _opaca_en_flujo(
                            {'unidad': clave, 'kinds': explicita.get('kinds'), 'reason': MOTIVO_OPACO})))
                    cuenta['AC124: deny a predicado privado'] += 1
            textos[path] = _con_ediciones(texto, ediciones[path])
        for posicion in por_regla:
            residuo[posicion.rsplit('[', 1)[0] + ' (sin regla en esa posición)'] += 1

        # 4. AC127: cada opaca dice cuántos predicados privados resuelve su clave.
        for path in rutas[:-1]:
            ediciones = []
            for _, item, entrada in _deny_del_texto(textos[path], path.name):
                if es_opaca(entrada) and declaraciones.get(entrada['unidad']):
                    nueva = _opaca_en_flujo({**entrada, 'declaraciones': len(declaraciones[entrada['unidad']])})
                    if textos[path][item.start_mark.index:_fin_contenido(item, textos[path])] != nueva:
                        ediciones.append((item.start_mark.index, _fin_contenido(item, textos[path]), nueva))
                    cuenta['reglas opacas con declaraciones privadas'] += 1
            textos[path] = _con_ediciones(textos[path], ediciones)
        filas_fin = (cargar_yaml(textos[reg_path], reg_path.name) or {}).get('rectificaciones') or []
        # Las que el loader y los checks contrastan con el registro (AC171): el fichero privado no lleva otra.
        sin_unidad_fin = filas_declarables(filas_fin)
        filas_decl = {rid: v for rid, v in filas_decl.items() if rid in sin_unidad_fin}
        # Las referencias privadas (AC197) de las claves que siguen siendo opacas: ninguna de una clave que no está.
        claves_fin = {entrada['unidad'] for path in rutas[:-1]
                      for _, _, entrada in _deny_del_texto(textos[path], path.name) if es_opaca(entrada)}
        referencias = {k: v for k, v in opacas.referencias.items() if k in claves_fin}

        # 5. AC138: lo que sobrevive al overlay, igual con el parche que sin él.
        antes = (deny_final, reglas_fix)
        despues = _reglas_de_textos({path: textos[path] for path in rutas[:-1]}, opacas, declaraciones)
        universo = _universo(index, valores, [*antes[0], *antes[1], *despues[0], *despues[1]])
        con, sin = (_supervivientes(RC.tras_el_overlay(universo, *reglas)) for reglas in (despues, antes))
        if con != sin:
            _overlay_rechazado(f'privatizar: el parche cambia lo que sobrevive al overlay '
                               f'({len(set(con) ^ set(sin))} aristas)')
    except (ReglaOpacaInvalida, YamlInvalido, RC.ExportInvalido) as exc:
        _overlay_rechazado(f'privatizar: {exc}')
    finally:
        relations_loader.log = previo
    parche = []
    for path in rutas:
        if textos[path] != originales[path]:
            rel = path.relative_to(repo).as_posix()
            parche += difflib.unified_diff(originales[path].splitlines(keepends=True),
                                           textos[path].splitlines(keepends=True),
                                           fromfile=f'a/{rel}', tofile=f'b/{rel}')
    privado = ('# Declaraciones PRIVADAS de las reglas deny opacas (spec 071 AC121) y de las filas con marcador sin '
               'unidad\n# (AC134): raíz de la auditoría, nunca el repo.\n'
               + yaml.safe_dump({**declaraciones, **({'filas': filas_decl} if filas_decl else {}),
                                 **({'referencias': referencias} if referencias else {})}, allow_unicode=True,
                                sort_keys=True))
    salidas = {'datos-071-e21.patch': ''.join(parche), 'reglas_opacas.yaml': privado}
    _fuera_del_repo(repo, [('--patch', salida / nombre) for nombre in salidas])
    _escribir_fuera(salida, salidas)
    print('privatizar:', json.dumps(dict(cuenta), ensure_ascii=False))
    if sin_publicar:
        print(f'privatizar: filas marcadas cuya arista no publica el export, sin tocar: {len(sin_publicar)} '
              f'({", ".join(sin_publicar)})')
    if residuo:
        print('privatizar: AC122-bis sin remedio de AC124:',
              json.dumps(dict(sorted(residuo.items())), ensure_ascii=False))
    if muertas_vivas:
        print(f'privatizar: fix marcadas que no son reglas muertas (pueden cambiar algo en otro grafo): '
              f'{len(muertas_vivas)} ({", ".join(muertas_vivas)})')
    print(f'privatizar: {salida / "datos-071-e21.patch"} y {salida / "reglas_opacas.yaml"}')
    return 0


def skip_applied(root, decisions):
    """Fuera las decisiones de packs ya `applied` en el censo: re-aplicarlas pisaría correcciones
    posteriores de su texto (063 tramo 6: la guarda de deriva lo cazó en 12 packs de tramos previos)."""
    conn = connect(root)
    try:
        applied = {r[0] for r in conn.execute("SELECT pack_id FROM esc_packs WHERE state='applied'")}
    finally:
        conn.close()
    return [d for d in decisions if (d.get('_meta') or {}).get('pack_id') not in applied]


def main(argv=None):
    parser = argparse.ArgumentParser(description='063 T10 — apply de overlays y censo')
    parser.add_argument('--root', required=True, help='raíz de la auditoría (contiene census.sqlite y esc/)')
    parser.add_argument('--repo', required=True, help='raíz del repo donde viven los overlays')
    parser.add_argument('--resolutions', default='', help='prefijos de _meta.resolution, separados por coma')
    parser.add_argument('--tag', default=None, help='etiqueta que firma cada fila de overlay')
    parser.add_argument('--upd', default=_today(), help='fecha YYYY-MM-DD de la tanda')
    parser.add_argument('--doc', default='', help='documento de referencia para los eventos del censo')
    parser.add_argument('--accept-drift', default='',
                        help='ficheros de decisión (coma) rehechos a mano sobre el texto vigente: sin guarda de deriva')
    parser.add_argument('--entities', default='',
                        help='JSON con la lista de entity_id del tramo: solo sus decisiones (063 tramo 6)')
    parser.add_argument('--snapshot', type=Path,
                        help='export del grafo (nodes.jsonl): prueba por la vía (a) la cita de una arista conservada '
                             '(spec 070) y es el de la puerta para `materializacion` (AC43) y `privatizar` (AC113)')
    parser.add_argument('--decisiones', nargs='+', default=[],
                        help='ficheros de esc/decisions que se procesan aunque su pack esté applied (AC45; tras el cmd)')
    parser.add_argument('--patch', help='con privatizar: carpeta fuera del repo donde dejar el parche y las '
                        'declaraciones privadas (spec 071 AC117), tal como se escribe: sin «.» ni «..» (AC177)')
    parser.add_argument('--privada', action='append', default=[],
                        help='con privatizar: «fichero:deny[n]=R-…», una regla sin etiqueta que pasa a opaca bajo una '
                             'clave privada nueva, que lleva la fila (AC121)')
    parser.add_argument('--duplicada', action='append', default=[],
                        help='con privatizar: fila «suprimido» que declara la copia duplicada de otra capa (AC119)')
    parser.add_argument('--nombrar', action='append', default=[],
                        help='con privatizar: fila marcada que nombra sus extremos por decisión del dueño (D1)')
    parser.add_argument('--filas-privadas', type=Path,
                        help='con privatizar: YAML privado {R-…: [predicado]} de las filas con marcador sin unidad, '
                             'fuera del repo (AC134)')
    parser.add_argument('cmd', choices=('check', 'dry-run', 'apply', 'record', 'materializacion', 'privatizar'))
    args = parser.parse_args(argv)
    # Ninguna excepción sale con su texto, que puede citar un valor de una decisión o del censo (AC52): un SystemExit
    # pasa con su código y cualquier otra da una línea con su clase y exit 2. Un apply interrumpido deja su marcador, y
    # el siguiente `check` dice qué restaurar.
    try:
        if args.cmd in ('materializacion', 'privatizar'):   # no escriben en el repo; el bloqueo es de los otros cuatro
            return _main(args, parser)
        with _bloqueo(args.repo):   # antes de mirar el marcador o leer nada: un solo apply/check/record por repo
            return _main(args, parser)
    except seudonimos.ErrorDeForma as err:   # AC114/AC115: un roster o un YAML que no se pueden leer, exit 2
        for motivo in err.motivos:
            print('  ❌', motivo)
        return 2
    except Exception as exc:
        print(f'qa_esc_apply: error interno ({type(exc).__name__})', file=sys.stderr)
        return 2


def _main(args, parser):
    interrumpido = _apply_interrumpido(args.repo)   # antes de leer nada: tampoco `record` con overlays a medias
    if interrumpido:
        print('  ❌', interrumpido)
        return 1
    tag = args.tag or f'063 auditoría escalonada {args.upd}'
    if args.cmd == 'privatizar':
        if not args.patch or not args.snapshot:
            parser.error('privatizar exige --patch y --snapshot')
        return privatizar(args.root, args.repo, args.patch, args.snapshot, args.privada, args.duplicada,
                          args.filas_privadas, args.nombrar)
    if args.cmd == 'materializacion':
        if not args.snapshot:
            parser.error('materializacion exige --snapshot')
        return materializacion(args.root, args.repo, args.snapshot)
    # Todas las decisiones del censo cuentan para la operación vigente (AC46 y AC54): una con una carga que el loader
    # descarta (AC78) o de un pack que no existe o que no contiene su unidad (AC106) no vale, la aplique o no este
    # mando.
    todas = load_decisions(args.root)
    validar_cargas(todas)
    validar_resoluciones(todas)
    conn = connect(args.root)
    try:
        validar_packs(conn, todas)
    finally:
        conn.close()
    if args.decisiones:
        # AC45: exactamente esos ficheros, aunque su pack esté `applied` (sin `skip_applied`), con los mismos gates.
        if args.cmd not in ('check', 'dry-run', 'apply'):
            parser.error('--decisiones solo con check, dry-run o apply')
        decisions = _decisiones_por_fichero(args.root, args.decisiones)
        _apply(args.root, args.repo, decisions, tag, args.upd, args.cmd == 'dry-run', args.cmd == 'check',
               [f.strip() for f in args.accept_drift.split(',') if f.strip()], None, args.snapshot, reparacion=True)
        return 0
    resolutions = [r for r in args.resolutions.split(',') if r.strip()]
    decisions = load_decisions(args.root, resolutions)
    if args.entities:
        wanted = set(json.loads(Path(args.entities).read_text(encoding='utf-8')))
        decisions = [d for d in decisions if (d.get('_meta') or {}).get('entity') in wanted]
    decisions = skip_applied(args.root, decisions)
    if not decisions:
        # `skip_applied` solo evita reescribir: la guarda (AC40, con la evidencia del censo, AC51) y el sentido inverso
        # (AC54, AC58) corren igual sobre las reglas ya materializadas.
        errors = []
        if args.cmd != 'record':
            contraste, avisos, _ = _contraste_reglas(args.root, args.repo)
            errors = _guarda_fix_deny({}, args.repo, _aristas_censo(args.root), args.root) + contraste
            for aviso in avisos:
                print('  aviso:', aviso)
        for error in errors:
            print('  ❌', error)
        if errors:
            return 1
        print('0 decisiones que apliquen a', resolutions or 'cualquier resolución')
        return 0
    if args.cmd == 'record':
        validar_cargas(decisions)  # una carga que el loader descarta: decisión que no vale, exit 2 (AC78)
        conn = connect(args.root)
        try:
            export = seudonimos.literal_del_export(args.snapshot) if args.snapshot else None
            errors, _ = gate(conn, decisions, args.root, export=export)
            for error in errors:
                print('  ❌', error)
            if errors:
                return 1
            record(conn, args.root, args.repo, decisions, '', args.doc)
        finally:
            conn.close()
        return 0
    _apply(args.root, args.repo, decisions, tag, args.upd, args.cmd == 'dry-run', args.cmd == 'check',
           [f.strip() for f in args.accept_drift.split(',') if f.strip()], None, args.snapshot)
    return 0


if __name__ == '__main__':
    sys.exit(main())
