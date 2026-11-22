"""Alcance de las reglas del overlay de relaciones (`relations*.yaml`): una sola definición para el apply, que las
escribe (`qa_esc_apply`), y para las comprobaciones de lo que dejan (`registro_check`, spec 071). Solo stdlib y la
ontología (PyYAML): el CI la importa con el entorno de la API, como `src.ontologia`.

El apply escribe la regla de una arista decidida con su par, su tipo, su documento y `quote_contains`, el principio
literal de su cita (QUOTE_CHARS caracteres), que el loader busca sin mayúsculas en la cita de cada arista del par.
Aquí el trozo y la cita se comparan normalizados (`cita_normalizada`: NFKC, mayúsculas y espacios): nunca se deja
fuera un caso que el loader case. Una sola preparación del bloque `set:` de una regla (`preparar_set`), la que usa el
propio loader (`relations_loader._prepare_set` la envuelve) y con la que las comprobaciones leen la carga de una
decisión (`carga`, spec 071 AC77). Una sola precedencia entre decisiones (`vigentes`): la que usan el apply, el
registro y la puerta. Un solo lector de los YAML del overlay y del registro (`leer_yaml`), que rechaza una clave
repetida (spec 071 AC108). Y una sola resolución de las reglas deny opacas (`Opacas`, spec 071 AC114 y AC121), la del
loader y la de los dos checks.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata

import yaml

from .entity_ids import entity_id
from .ontologia import ontologia

QUOTE_CHARS = 60
NUMERIC_PROPS = {'amount'}
SIMETRICAS = frozenset(ontologia().simetricas)
# Las posturas sobre una afirmación (`claims.yaml`): la arista de un titular hacia un Claim, sin entity_id, con su
# clave en el YAML.
POSTURAS = {'ASSERTS': 'asserts', 'DISPUTES': 'disputes'}
# Props que un `fix: {set: …}` puede reescribir (spec 061). Lista cerrada: nada de tocar la procedencia documental
# (source_doc/source_quote) ni claves de registro — corregir un dato ≠ reescribir su evidencia. `source_page` sí (063,
# 2026-09-29): es el localizador de la cita, y una página equivocada abre el pasaje de OTRA persona (Koldo CONVICTED_OF
# cohecho apuntaba a la p.219, fallo de Ábalos; la cita está en la p.220).
PROPS_SET = frozenset({'amount', 'currency', 'date', 'status', 'note', 'source_kind', 'source_page'})
# Lo único que un diagnóstico de carga nombra (spec 071 AC82): las props de la lista cerrada, las de la firma documental
# de una arista, que ningún `set` reescribe, y los campos de una operación de arista. Cualquier otra clave puede ser un
# nombre o un identificador puesto por error: se señala por su posición en el bloque (`nombre_de_prop`).
CLAVES_CONOCIDAS = PROPS_SET | {'source_doc', 'source_quote', 'unit_id', 'action', 'new_type', 'prop', 'value', 'set',
                                'evidence', 'reason'}
# Orden de confianza de las capas (menor = más autoritativa). Espejo de api/app/graph_queries._KIND_RANK para las capas
# que el overlay puede escribir. Ontología (spec 069): las capas hasta `contexto`; las de registros oficiales las
# recrea su propio loader y el overlay no las toca.
RANGO_CAPA = {k: r for k, r in ontologia().rango_capa.items() if r <= ontologia().rango_capa['contexto']}
# La forma de una unidad del censo (un sha256 en hex) y la de un pack (de entidad o editorial): los únicos `unit_id` y
# `pack_id` que un diagnóstico puede citar (spec 071 AC95 y AC106).
_UNIDAD = re.compile(r'[0-9a-f]{64}')
_PACK = re.compile(r'[DE]-[0-9a-f]{12}(-\d+)?')


def norm_prose(text):
    """Espacios colapsados y NFC: el YAML del overlay y el seed pueden diferir en saltos de línea
    o en la composición de tildes sin que cambie una sola palabra publicada."""
    return unicodedata.normalize('NFC', re.sub(r'\s+', ' ', str(text or '')).strip())


def cita_normalizada(text):
    """Una cita para compararla con otra o con un trozo (spec 071 AC107): la equivalencia canónica y de compatibilidad
    (NFKC, otra vez tras el casefold, que puede descomponer), sin mayúsculas y con los espacios colapsados. Dos citas
    que solo difieren en la composición de una tilde (NFC y NFD), en una ligadura o en un espacio duro son la misma."""
    texto = unicodedata.normalize('NFKC', unicodedata.normalize('NFKC', str(text or '')).casefold())
    return ' '.join(texto.split())


def trozo(quote):
    """Prefijo literal de la cita para acotar el match del loader (nunca normalizado: el
    Cypher hace CONTAINS sobre `r.source_quote`, así que solo vale una subcadena real)."""
    return (str(quote or '')[:QUOTE_CHARS]).strip() or None


def cast_value(prop, value):
    """El esquema transporta todo valor como texto; el loader espera el tipo real.
    Las mismas reglas que tenía el bucle 061 (ambos cargan por el mismo loader)."""
    text = str(value if value is not None else '').strip()
    if prop in NUMERIC_PROPS:
        return float(text.replace('.', '').replace(',', '.') if ',' in text else text)
    if prop == 'source_page':  # el loader exige entero ≥ 1 (063 tramo 6: «81» se descartaba)
        return int(text)
    return text


def props_del_set(op):
    """{prop: valor} que cambia una operación de arista: su mapa `set` (varias props en una sola operación: un
    `set_prop`, o un `retype` o un `invert` cuya regla también las escribe, spec 071 AC71) o el par `prop`/`value` de
    un `set_prop`. Los valores van como texto, como los transporta el esquema; los convierte `cast_value`."""
    mapa = op.get('set')
    if isinstance(mapa, dict) and mapa:
        return dict(mapa)
    return {op['prop']: op.get('value')} if op.get('action') == 'set_prop' and op.get('prop') else {}


_SPANISH = {'languages': ['es'],
            'settings': {'PREFER_DAY_OF_MONTH': 'first', 'STRICT_PARSING': False, 'RELATIVE_BASE': None}}
_YEAR_RE = re.compile(r'(19|20)\d{2}')


def normalize_date(raw):
    """(fecha ISO o año, año) de una fecha escrita de cualquier forma, también en español; (None, None) si no hay
    ninguna. La usan la limpieza del grafo (`cleanup`, que la reexporta) y la carga de un `set:` (`preparar_set`). Una
    fecha que no es ISO ni un año la interpreta `dateparser`, que solo está en el entorno del pipeline: se importa aquí,
    y sin él es un ImportError (el CI corre con el de la API)."""
    if not raw:
        return None, None
    raw = raw.strip()
    # Fast path: already ISO YYYY-MM-DD
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
        return raw, int(raw[:4])
    if re.fullmatch(r'\d{4}', raw):
        return raw, int(raw)
    import dateparser
    try:
        dt = dateparser.parse(raw, **_SPANISH)
    except Exception:  # noqa: BLE001
        dt = None
    if dt:
        return dt.date().isoformat(), dt.year
    # Last-ditch: just pull a 4-digit year if present
    m = _YEAR_RE.search(raw)
    if m:
        y = int(m.group(0))
        return str(y), y
    return None, None


class CargaInvalida(ValueError):
    """Un bloque `set:` que el loader descarta (spec 071 AC78). Lleva el prop como lo nombra `nombre_de_prop` (AC82) y
    un motivo neutro, nunca el valor: su texto acaba en informes y logs. `evento` es el aviso con que el loader registra
    el descarte."""

    def __init__(self, prop, motivo, evento=None):
        super().__init__(f'{prop}: {motivo}' if prop else motivo)
        self.prop, self.motivo, self.evento = prop, motivo, evento


def nombre_de_prop(clave, posicion):
    """Cómo nombra un diagnóstico la clave de un bloque en su `posicion` (desde 1): por su nombre solo si es del
    vocabulario cerrado (CLAVES_CONOCIDAS); si no, «prop desconocido (posición n)» (spec 071 AC82: una clave con forma
    de identificador puede ser el nombre de una persona)."""
    if isinstance(clave, str) and clave in CLAVES_CONOCIDAS:
        return clave
    return f'prop desconocido (posición {posicion})'


def preparar_set(props):
    """El bloque `set:` de una regla `fix` tal como lo escribe el loader en la arista (spec 061): la lista cerrada de
    props (PROPS_SET), `source_kind` solo a la BAJA (las filas solo tocan aristas extraídas: reetiquetar una prensa
    como judicial sería fabricar respaldo documental), el estado procesal en su forma canónica de la ontología (spec
    069), la página entera ≥ 1 y, con `date`, `date_norm`/`date_year`, que la API publica antes que `date`
    (`graph_queries`: corregir o retirar solo `date` dejaba visible la fecha errónea, revisión Codex 063; None borra la
    prop). Un bloque que no vale es CargaInvalida, con el prop y el motivo, nunca el valor; el loader lo envuelve, avisa
    y descarta la fila (`relations_loader._prepare_set`). Una clave fuera de la lista se nombra solo si es del
    vocabulario cerrado; si no, por su posición en el bloque (AC82)."""
    if not isinstance(props, dict) or not props:
        raise CargaInvalida(None, 'el bloque `set` está vacío o no es un mapa', 'fix_skip_set_shape')
    desconocidos = [nombre_de_prop(clave, n) for n, clave in enumerate(props, 1) if clave not in PROPS_SET]
    if desconocidos:
        raise CargaInvalida(', '.join(desconocidos), 'fuera de la lista cerrada del loader', 'fix_skip_set_props')
    kind = props.get('source_kind')
    if kind is not None and (kind not in RANGO_CAPA or RANGO_CAPA[kind] <= RANGO_CAPA['judicial']):
        raise CargaInvalida('source_kind', 'solo puede degradar la capa de una arista judicial', 'fix_skip_set_kind')
    if 'status' in props:
        res = ontologia().validar('estados_procesales', props['status'])
        if not res.ok:
            raise CargaInvalida('status', 'fuera de los estados procesales de la ontología', 'fix_skip_set_status')
        props = {**props, 'status': res.valor}
    page = props.get('source_page', 1)
    # Un nulo tampoco: borrar la página quitaría el [doc, p.N] de una cita publicada (spec 070 AC154, enmienda 89).
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise CargaInvalida('source_page', 'tiene que ser un entero ≥ 1', 'fix_skip_set_page')
    props = dict(props)
    if 'date' in props:
        try:
            iso, year = normalize_date(str(props['date'])) if props['date'] else (None, None)
        except ImportError:
            raise CargaInvalida('date', 'una fecha que no es ISO ni un año solo la interpreta dateparser, que este '
                                'entorno no tiene', 'fix_skip_set_date') from None
        props['date_norm'], props['date_year'] = iso, year
    return props


def carga(op):
    """Los props que deja en la arista la operación de arista `op`, preparados como los escribe el loader
    (`preparar_set`, con sus props derivadas: spec 071 AC77): los de un set_prop (un prop o un mapa), el estado de un
    set_status o el mapa `set` de un retype o un invert (AC71), con los valores que escribe el apply (`cast_value`).
    None si no cambia props (un deny, un keep, un retype o un invert sin mapa); CargaInvalida si el loader no la
    escribiría (AC78): un mapa vacío, un set_prop sin props, un valor que no tiene el tipo de su prop o un bloque que
    `preparar_set` descarta."""
    accion = op.get('action')
    if accion == 'set_status':
        return preparar_set({'status': str(op.get('status') or '').strip()})
    if accion not in ('set_prop', 'retype', 'invert') or (accion != 'set_prop' and op.get('set') is None):
        return None
    if op.get('set') is not None and not (isinstance(op['set'], dict) and op['set']):
        raise CargaInvalida(None, 'el bloque `set` está vacío o no es un mapa', 'fix_skip_set_shape')
    crudo = props_del_set(op)
    if not crudo:
        raise CargaInvalida(None, 'un set_prop sin props que cambiar')
    valores = {}
    for n, (prop, valor) in enumerate(crudo.items(), 1):
        try:
            valores[prop] = cast_value(prop, valor)
        except ValueError:
            raise CargaInvalida(nombre_de_prop(prop, n),
                                'no es un número' if prop in NUMERIC_PROPS else 'no es un entero') from None
    return preparar_set(valores)


def regla_de(value):
    """La regla que el apply escribe para la arista `value` del censo (`{type, start, end, props}`): par, tipo,
    documento, trozo de cita y, si su capa no es judicial, `kinds` con ella."""
    props = value.get('props') or {}
    kind = props.get('source_kind')
    return {'source_id': value.get('start'), 'target_id': value.get('end'), 'type': value.get('type'),
            'doc': props.get('source_doc') or None, 'quote_contains': trozo(props.get('source_quote')),
            'kinds': [kind] if kind and kind != 'judicial' else None}


def _sin_mayusculas(text):
    return cita_normalizada(text)


def casa(regla, value, exacta=False, dirigida=False):
    """Si la regla (`{source_id, target_id, type, doc, quote_contains, kinds}`) casa la arista `value`: el mismo
    tipo, el par (sin dirección salvo `dirigida`), el documento de la regla o ninguno, y su trozo de cita dentro de la
    cita de la arista (sin trozo, cualquiera). `exacta` añade el filtro de capas del loader (sin `kinds`, solo
    judicial o nula). Compara citas normalizadas (`cita_normalizada`), así que es un superconjunto de lo que hace el
    loader: sirve para no dejar fuera ningún conflicto (AC40) ni ninguna copia en la tolerancia, nunca para afirmar que
    una regla materializa una decisión; eso es `alcanza_loader`."""
    if regla.get('type') != value.get('type'):
        return False
    suya, de_la_arista = (regla.get('source_id'), regla.get('target_id')), (value.get('start'), value.get('end'))
    if suya != de_la_arista if dirigida else set(suya) != set(de_la_arista):
        return False
    props = value.get('props') or {}
    if exacta:
        kind = props.get('source_kind')
        if not (kind is None or kind == 'judicial' or kind in (regla.get('kinds') or [])):
            return False
    doc = regla.get('doc') or None
    if doc is not None and doc != (props.get('source_doc') or None):
        return False
    return not regla.get('quote_contains') or \
        _sin_mayusculas(regla['quote_contains']) in _sin_mayusculas(props.get('source_quote'))


def alcanza_loader(regla, value, accion):
    """Si la regla preparada actúa sobre la arista `value` (tal como está en el grafo) con la acción `accion`, con el
    predicado exacto del Cypher de `relations_loader` (spec 071 AC48): el mismo tipo (en un invert o un set, el
    corregido si la fila también retipa: `_SET_Q` y `_SWAP_Q` corren tras el retype); el par sin dirección en un deny
    (`apply_deny`: `(a)-[r]-(b)`), con ella en un retype o un invert (`_RETYPE_Q`/`_SWAP_Q`: `(a)-[r]->(b)`), también
    en las simétricas, y en un set con ella salvo en las simétricas (`_SET_Q`); sin `kinds`, solo capa judicial o
    nula; el documento de la regla o ninguno, y su trozo de cita como subcadena literal de la cita, las dos en
    minúsculas (`toLower(…) CONTAINS toLower(…)`: sin colapsar espacios ni componer tildes)."""
    tipo = (regla.get('corrected_type') or regla.get('type')) if accion in ('invert', 'set') else regla.get('type')
    if tipo != value.get('type'):
        return False
    suya, de_la_arista = (regla.get('source_id'), regla.get('target_id')), (value.get('start'), value.get('end'))
    sin_direccion = accion == 'deny' or (accion == 'set' and value.get('type') in SIMETRICAS)
    if (set(suya) != set(de_la_arista)) if sin_direccion else (suya != de_la_arista):
        return False
    props = value.get('props') or {}
    kind = props.get('source_kind')
    if not (kind is None or kind == 'judicial' or kind in (regla.get('kinds') or [])):
        return False
    if regla.get('doc') is not None and props.get('source_doc') != regla['doc']:
        return False
    trozo = regla.get('quote_contains')
    return trozo is None or str(trozo).lower() in str(props.get('source_quote') or '').lower()


def solapan(a, b, aristas=()):
    """Si dos reglas pueden casar una misma arista: el mismo tipo y par (sin dirección), documentos compatibles
    (iguales o alguno sin documento) y, o un trozo de cita dentro del otro (o alguno sin trozo), o alguna de `aristas`
    (las del censo y las del export, cuando las hay) que casan las dos (spec 071 AC51: dos trozos distintos de una
    misma cita no se contienen y casan la misma arista). Las capas no las separan: sin `kinds` una regla casa judicial
    o nula, y con `kinds`, además, esas."""
    if a.get('type') != b.get('type') or \
            {a.get('source_id'), a.get('target_id')} != {b.get('source_id'), b.get('target_id')}:
        return False
    if a.get('doc') and b.get('doc') and a['doc'] != b['doc']:
        return False
    x, y = (_sin_mayusculas(r.get('quote_contains')) for r in (a, b))
    return not x or not y or x in y or y in x or any(casa(a, v) and casa(b, v) for v in aristas)


# --- precedencia entre decisiones (spec 071 AC46, AC54 y AC55) -----------------------------------------------------

def _para(motivo):
    """Una decisión que no se puede ordenar para todo con exit 2; el motivo nombra el fichero o la unidad, nunca un
    valor."""
    print(motivo, file=sys.stderr)
    raise SystemExit(2) from None


def precedencia(decision):
    """(familia, instante) de una decisión: manda la resolución humana, luego el arbitraje y luego el resto (las
    cerradas por la dual, la triple u Opus); dentro de una familia, la más reciente. `decided_at` es un instante con
    zona y se compara en UTC: como texto, «…T10:30+02:00» quedaba por delante de «…T09:00+00:00», que es posterior."""
    meta = decision.get('_meta') or {}
    resolucion = str(meta.get('resolution') or '')
    familia = 2 if resolucion.startswith('humano-') else 1 if resolucion.startswith('arbitraje-') else 0
    try:
        instante = datetime.datetime.fromisoformat(str(meta.get('decided_at')))
    except ValueError:
        instante = None
    if instante is None or instante.tzinfo is None:
        _para(f'decisión con decided_at inválido ({decision.get("_file") or "?"})')
    return familia, instante.astimezone(datetime.timezone.utc)


def _firma_operacion(op):
    props = tuple(sorted((str(k), str(v if v is not None else '').strip()) for k, v in props_del_set(op).items()))
    return (op.get('action') or 'keep', str(op.get('new_type') or ''), props, str(op.get('status') or '').strip())


def huella(uid, posicion):
    """Los 8 primeros hex de `uid` si tiene el formato exacto de una unidad del censo (64 hex) y, si no, su `posicion`
    (spec 071 AC95 y AC106): un valor escrito por error en ese campo, un nombre o un identificador, nunca sale."""
    return uid[:8] if isinstance(uid, str) and _UNIDAD.fullmatch(uid) else posicion


def nombre_de_unidad(uid, posicion):
    """Cómo nombra un diagnóstico la unidad de una entrada de un fichero (spec 071 AC95): «unidad» y su `huella`, o su
    `posicion` («D-1.json, operación 3») si no tiene la forma de una unidad."""
    return f'unidad {uid[:8]}' if isinstance(uid, str) and _UNIDAD.fullmatch(uid) else posicion


def nombre_de_pack(pack, posicion):
    """Cómo nombra un diagnóstico el pack de una decisión (spec 071 AC106): su id si tiene la forma de un pack del censo
    (`E-` o `D-` y 12 hex, con su índice) y, si no, su `posicion` (el fichero de la decisión)."""
    return pack if isinstance(pack, str) and _PACK.fullmatch(pack) else posicion


def validar_cargas(decisions):
    """Cada operación de arista de `decisions` que cambia props los cambia con una carga que el loader escribiría
    (`carga`, spec 071 AC78). Una que descarta no es una fase ausente sino una decisión que no vale: para todo con exit
    2, como un decided_at inválido; el motivo nombra el fichero, la unidad (por su posición si su `unit_id` no tiene la
    forma de una unidad: AC95) y el prop (solo uno del vocabulario cerrado; cualquier otro, por su posición: AC82),
    nunca el valor."""
    for decision in decisions:
        for n, op in enumerate(decision.get('edges') or [], 1):
            try:
                carga(op)
            except CargaInvalida as exc:
                _para(f'operación con carga inválida ({decision.get("_file") or "?"}, '
                      f'{nombre_de_unidad(op.get("unit_id"), f"operación {n}")}: {exc})')


def vigentes(decisions):
    """{unit_id: (decisión, operación)}: la operación de arista vigente de cada unidad, la de la decisión de más
    `precedencia` que la toca. Una unidad que una decisión cubre sin operación queda en `keep`, como la deja el
    esquema. Dos operaciones distintas con la misma precedencia no se pueden ordenar: exit 2 (spec 071 AC55), con la
    unidad por su hash o, si su `unit_id` no tiene la forma de una unidad, por el fichero y la posición (AC95); una
    operación cuya carga el loader descarta, tampoco vale (`validar_cargas`, AC78)."""
    validar_cargas(decisions)
    candidatas = {}
    for decision in decisions:
        p, fichero = precedencia(decision), decision.get('_file') or '?'
        ops = {}
        for n, op in enumerate(decision.get('edges') or [], 1):
            if op.get('unit_id'):
                ops[op['unit_id']] = (op, f'{fichero}, operación {n}')
        cubiertas = [(u.get('unit_id'), ({'unit_id': u.get('unit_id'), 'action': 'keep'}, f'{fichero}, unidad {n}'))
                     for n, u in enumerate(decision.get('units') or [], 1) if u.get('unit_id') not in ops]
        for uid, (op, posicion) in [*ops.items(), *cubiertas]:
            if uid:
                candidatas.setdefault(uid, []).append((p, decision, op, posicion))
    out = {}
    for uid, lista in candidatas.items():
        mejor = max(p for p, *_ in lista)
        empatadas = [(d, op, posicion) for p, d, op, posicion in lista if p == mejor]
        if len({_firma_operacion(op) for _, op, _ in empatadas}) > 1:
            _para(f'precedencia irresoluble ({nombre_de_unidad(uid, empatadas[0][2])})')
        out[uid] = empatadas[0][:2]
    return out


# --- lectura de los YAML del overlay y del registro (spec 071 AC108) --------------------------------------------------

class YamlInvalido(ValueError):
    """Un YAML que no carga: con un error de sintaxis, un valor que su etiqueta no admite (`!!int "…"`) o una clave
    repetida en cualquier nivel. Su texto es el fichero y la posición, nunca el de la excepción de PyYAML, que cita la
    línea rota o el valor."""


class _SinRepetidas(yaml.SafeLoader):
    """`yaml.SafeLoader` que no deja pasar una clave repetida: PyYAML se queda con la última en silencio, y un bloque
    `rectificaciones:` o un `deny:` duplicado (o una `decision` dentro de una fila) esconde lo que dice el primero. Ni
    una clave de fusión `<<` (spec 071 AC128): mezcla en una regla o una fila lo que dice otra, sin que se vea."""

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            vistas = set()
            for clave, _ in node.value:
                if clave.tag == 'tag:yaml.org,2002:merge':
                    raise yaml.constructor.ConstructorError(None, None, 'clave de fusión', clave.start_mark)
                valor = self.construct_object(clave, deep=True)
                try:
                    repetida = valor in vistas
                except TypeError:
                    continue  # una clave que no es hashable: la rechaza la construcción de PyYAML
                if repetida:
                    raise yaml.constructor.ConstructorError(None, None, 'clave repetida', clave.start_mark)
                vistas.add(valor)
        return super().construct_mapping(node, deep=deep)


def cargar_yaml(texto, nombre):
    """El contenido del YAML `texto` (el fichero `nombre`), con `_SinRepetidas`; si no carga, YamlInvalido."""
    try:
        return yaml.load(texto, Loader=_SinRepetidas)  # un SafeLoader: ninguna etiqueta de Python
    except Exception as exc:
        mark = getattr(exc, 'problem_mark', None) or getattr(exc, 'context_mark', None)
        donde = f'línea {mark.line + 1}, columna {mark.column + 1}' if mark is not None else 'posición desconocida'
        raise YamlInvalido(f'{nombre}: YAML inválido ({donde})') from None


def leer_yaml(path):
    """El contenido del YAML de `path` (`cargar_yaml`)."""
    return cargar_yaml(Path(path).read_text(encoding='utf-8'), Path(path).name)


def cargar_documentos(texto, nombre):
    """[documento]: cada documento del YAML `texto` (el fichero `nombre`), construido aparte con `_SinRepetidas` (los
    vacíos, None), en su orden; si alguno no se construye (una etiqueta desconocida, un `!!binary` que no es base64,
    una clave repetida), YamlInvalido (spec 071 AC185)."""
    try:
        return list(yaml.load_all(texto, Loader=_SinRepetidas))
    except Exception as exc:
        mark = getattr(exc, 'problem_mark', None) or getattr(exc, 'context_mark', None)
        donde = f'línea {mark.line + 1}, columna {mark.column + 1}' if mark is not None else 'posición desconocida'
        raise YamlInvalido(f'{nombre}: YAML inválido ({donde})') from None


# --- reglas deny opacas (spec 071 AC114 y AC121) ----------------------------------------------------------------------

# Las declaraciones privadas de las reglas opacas, en la raíz de la auditoría (`QA_CENSO_ROOT`), nunca en el repo: por
# clave, el predicado de un deny que la unidad del censo no da (el par actual de una arista re-identificada, o el
# predicado completo de una retirada sin unidad del censo). Uno `ajena: true` es el de una regla sin etiqueta de otro
# hecho que se hizo opaca bajo esa clave (spec 071 AC124): se resuelve sin la etiqueta de la unidad, como la regla
# explícita que sustituye, que no se contrastaba con ninguna decisión.
REGLAS_OPACAS = 'esc/reglas_opacas.yaml'
# Lo único que lleva una regla opaca del overlay público: su clave, cuántos predicados privados resuelve (AC127; sin el
# campo, ninguno), sus capas y su motivo; y sus campos estructurales, que no dicen nada del hecho.
CAMPOS_OPACA = frozenset({'unidad', 'declaraciones', 'kinds', 'reason'})
ESTRUCTURALES_OPACA = frozenset({'unidad', 'declaraciones'})
# Las claves de una regla deny explícita (spec 071 AC159). Una entrada deny con otra (una variante de `unidad`, un campo
# mal escrito) no es ni opaca ni explícita: no se clasifica ni se descarta, aborta.
CLAVES_DENY = frozenset({'source', 'source_id', 'source_type', 'target', 'target_id', 'target_type', 'type', 'doc',
                         'quote_contains', 'kinds', 'reason'})
_PREDICADO = frozenset({'source_id', 'target_id', 'type', 'doc', 'quote_contains', 'kinds', 'ajena'})
# El motivo neutro de una regla opaca, y con el que se resuelve el predicado privado de una regla ajena.
MOTIVO_AJENA = 'retirada; motivo en la decisión interna de la auditoría'
# La etiqueta de lote con que empieza el motivo de una regla del apply («063 auditoría escalonada 2026-09-26», «063
# tramo 6 (2026-10-03)»), y la fórmula cerrada del motivo de una regla opaca (spec 071 AC130): la etiqueta de su lote,
# si la lleva, la de su unidad (`[hash8]`, de su propia clave) y el motivo neutro; o solo el motivo neutro (una clave
# privada). Nada más: un motivo libre puede nombrar a quien la fila oculta.
LOTE = r'0\d\d (?:auditoría escalonada|tramo \d+) \(?\d{4}-\d{2}-\d{2}\)?'
_MOTIVO_OPACA = re.compile(rf'(?:(?:{LOTE}) )?\[(?P<h8>[0-9a-f]{{8}})\]: {re.escape(MOTIVO_AJENA)}'
                           rf'|{re.escape(MOTIVO_AJENA)}')


def motivo_opaco_valido(regla):
    """Si el motivo de la regla opaca `regla` es su fórmula cerrada (AC130), con la etiqueta de su propia clave."""
    m = _MOTIVO_OPACA.fullmatch(str(regla.get('reason') or ''))
    return m is not None and (m.group('h8') is None or str(regla.get('unidad') or '').startswith(m.group('h8')))


def declaraciones_validas(regla):
    """Si el campo `declaraciones` de la regla opaca `regla` es un entero ≥ 0, o falta (spec 071 AC127)."""
    n = regla.get('declaraciones', 0)
    return isinstance(n, int) and not isinstance(n, bool) and n >= 0


class ReglaOpacaInvalida(ValueError):
    """Una regla opaca que no se puede resolver: sin la forma de una regla opaca, sin unidad del censo ni declaración
    privada, sin el censo, o una declaración privada sin la forma de un deny. Su texto no lleva ningún valor."""


def es_opaca(regla):
    """Si una regla deny del overlay es opaca (spec 071 AC114): nombra su clave (`unidad`) y no su par, su tipo, su
    documento ni su cita."""
    return isinstance(regla, dict) and 'unidad' in regla


# Los tipos de procedencia nunca se recablean ni se borran como aristas entre entidades.
PROVENANCIA = ('MENTIONED_IN', 'PART_OF')


def resolver(item, side):
    """El entity_id del extremo `side` ('source' o 'target') de una regla del overlay, como lo resuelve el loader:
    `<side>_id` sin blancos, o `<side>` (un nombre) con su `<side>_type` sin blancos (Person si no lo dice), con el
    `entity_id` de `entity_ids` (solo biblioteca estándar); None si no resuelve. Vive aquí, y no en el loader, para que
    `registro_check --tree` (el CI, en el entorno de la API) lo use sin importarlo (enmienda 16 y spec 071 AC176)."""
    eid = (item.get(f'{side}_id') or '').strip()
    if eid:
        return eid
    name = (item.get(side) or '').strip()
    etype = (item.get(f'{side}_type') or 'Person').strip()
    if not name or etype not in ontologia().tipos_entidad:
        return None
    return entity_id(etype, name)


def forma_deny(d):
    """Por qué la entrada deny `d` no tiene ninguna de las dos formas que aplica el loader (spec 071 AC159), o None: la
    opaca EXACTA (`{unidad, declaraciones?, kinds?, reason}`: esas claves y ninguna otra; su resolución comprueba lo
    demás) o la explícita completa (claves de `CLAVES_DENY`, campos de texto, un tipo de relación válido, los dos
    extremos resueltos con `resolver`, capas conocidas). Cualquier otra —una variante `Unidad` o ` unidad`, una clave
    mal escrita, sin tipo, un extremo sin resolver— ni se clasifica ni se salta: el loader aborta y `registro_check
    --tree` la da como problema (AC176). El motivo no lleva ningún valor."""
    if not isinstance(d, dict):
        return 'no es un mapa'
    if es_opaca(d):
        return None if set(d) <= CAMPOS_OPACA else 'regla opaca con otra clave que las de su forma'
    if set(d) - CLAVES_DENY:
        return 'una clave que no es de una regla deny'
    if any(d.get(k) is not None and not isinstance(d[k], str) for k in CLAVES_DENY - {'kinds'}):
        return 'un campo que no es un texto'
    rtype = (d.get('type') or '').strip()
    if rtype not in ontologia().tipos_relacion or rtype in PROVENANCIA:
        return 'sin un tipo de relación válido'
    if not resolver(d, 'source') or not resolver(d, 'target'):
        return 'un extremo sin resolver'
    kinds = d.get('kinds')
    if kinds and not (isinstance(kinds, list) and all(isinstance(k, str) and k.strip() in RANGO_CAPA for k in kinds)):
        return 'capas desconocidas'
    return None


def _kinds_validos(kinds):
    return kinds is None or (isinstance(kinds, list) and kinds and all(k in RANGO_CAPA for k in kinds))


_FILA = re.compile(r'R-\d{4}-\d{2}-\d{2}-\d{4}')
# El vocabulario cerrado de los marcadores de anonimato del `dato` (el que escribe el apply y los que puso el dueño):
# una fila con uno oculta un extremo, y nada público puede nombrarlo (spec 071 AC113–AC115 y AC122).
MARCADOR = re.compile(r'\[(?:tercero|dato personal|bien personal|sociedad|organismo|domicilio particular)\]')


def filas_declarables(rows):
    """Los ids de las filas del registro `rows` que llevan una declaración privada en `filas:` (spec 071 AC134 y AC183):
    sin unidad y con un marcador en su dato, las únicas cuyo predicado no puede ser público. Una «suprimido» sin unidad
    ni marcador se comprueba por la forma de su dato, con las reglas públicas
    (`registro_check.retiradas_sin_unidad`)."""
    return {f.get('id') for f in rows if isinstance(f, dict) and not f.get('unidad')
            and MARCADOR.search(str(f.get('dato') or ''))}


def _predicados(nombre, n, valor, ajena):
    """La lista de predicados de una entrada del fichero privado, con su forma comprobada: una lista no vacía de
    predicados con la forma de un deny (`ajena: true` solo bajo una clave, `ajena`). Un documento, un trozo de cita o
    unas capas vacíos o solo de blancos no son un comodín (el loader no los lee así): un comodín es un campo ausente
    (spec 071 AC151)."""
    if not (isinstance(valor, list) and valor):
        raise ReglaOpacaInvalida(f'{nombre}: entrada {n} sin una lista de predicados')
    for m, p in enumerate(valor, 1):
        if not (isinstance(p, dict) and set(p) <= _PREDICADO
                and all(isinstance(p.get(k), str) and p[k].strip() for k in ('source_id', 'target_id', 'type'))
                and all(k not in p or (isinstance(p[k], str) and p[k].strip()) for k in ('doc', 'quote_contains'))
                and ('kinds' not in p or (p['kinds'] is not None and _kinds_validos(p['kinds'])))
                and p.get('ajena') in ((None, True) if ajena else (None,))):
            raise ReglaOpacaInvalida(f'{nombre}: entrada {n}, predicado {m} sin la forma de un deny')
    return [dict(p) for p in valor]


def _declaraciones(path):
    """`declaraciones_de` el fichero privado `REGLAS_OPACAS` de `path`, o None si no está."""
    if not Path(path).is_file():
        return None
    try:
        doc = leer_yaml(path)
    except YamlInvalido as exc:
        raise ReglaOpacaInvalida(str(exc)) from None
    return declaraciones_de(doc, Path(path).name)


def declaraciones_de(doc, nombre):
    """({clave: [predicado]}, {fila: [predicado]}, {clave: [palabra]}) del contenido `doc` del fichero privado
    `nombre`, con su forma comprobada (si no la tiene, ReglaOpacaInvalida): por clave (64 hex), los predicados privados
    de sus reglas opacas; en `filas:`, por id del registro, los de una fila con marcador sin unidad, con los que se
    comprueba su anonimato (spec 071 AC134); y en `referencias:`, por clave, las palabras sueltas (un nombre de pila, un
    apellido, un apodo) que nombran en su contexto al extremo que oculta su fila: el barrido del registro las lee como
    un nombre suyo (AC197, enmienda 24 bis). No son declaraciones: no cuentan para las de su regla (AC127)."""
    if doc is None:
        return {}, {}, {}
    if not isinstance(doc, dict):
        raise ReglaOpacaInvalida(f'{nombre}: no es un mapa de claves')
    claves, filas, referencias = {}, {}, {}
    for n, (clave, valor) in enumerate(doc.items(), 1):
        if clave == 'referencias':
            if not isinstance(valor, dict):
                raise ReglaOpacaInvalida(f'{nombre}: `referencias` no es un mapa de claves')
            for m, (suya, palabras) in enumerate(valor.items(), 1):
                if not (isinstance(suya, str) and _UNIDAD.fullmatch(suya) and isinstance(palabras, list) and palabras
                        and all(isinstance(w, str) and len(w.split()) == 1 for w in palabras)):
                    raise ReglaOpacaInvalida(f'{nombre}: referencias {m} sin una clave y una lista de palabras sueltas')
                referencias[suya] = [w.strip() for w in palabras]
        elif clave == 'filas':
            if not isinstance(valor, dict):
                raise ReglaOpacaInvalida(f'{nombre}: `filas` no es un mapa de filas')
            for m, (fila, predicados) in enumerate(valor.items(), 1):
                if not (isinstance(fila, str) and _FILA.fullmatch(fila)):
                    raise ReglaOpacaInvalida(f'{nombre}: fila {m} sin un id del registro')
                filas[fila] = _predicados(nombre, f'filas {m}', predicados, ajena=False)
        elif isinstance(clave, str) and _UNIDAD.fullmatch(clave):
            claves[clave] = _predicados(nombre, n, valor, ajena=True)
        else:
            raise ReglaOpacaInvalida(f'{nombre}: entrada {n} sin una clave de 64 hex')
    return claves, filas, referencias


def identidad_propia(arista, predicados):
    """Si una clave opaca dice por sí misma qué retira (spec 071 AC157): su arista del censo (`arista`, o None) o, entre
    sus predicados privados, alguno que no sea `ajena`. Los `ajena` son de otros hechos que se hicieron opacos bajo
    ella (AC124): una clave con solo esos no retira nada suyo, y ninguna comprobación de su fila tendría qué mirar."""
    return arista is not None or any(not p.get('ajena') for p in predicados)


class Opacas:
    """Las reglas deny opacas de una auditoría (spec 071 AC114 y AC121): la unidad de cada clave en su censo
    (`census.sqlite`) y las declaraciones privadas de `REGLAS_OPACAS`, leídas una vez. Sin el censo no se resuelve
    ninguna, ni sin el fichero privado (AC127: perderlo reduciría la retirada en silencio), ni con una clave sin
    identidad propia (AC157): ReglaOpacaInvalida, y el loader aborta."""

    def __init__(self, raiz):
        self.raiz = Path(raiz)
        censo = self.raiz / 'census.sqlite'
        if not censo.is_file():
            raise ReglaOpacaInvalida('sin el censo de la auditoría (QA_CENSO_ROOT)')
        self.conn = sqlite3.connect(censo.resolve().as_uri() + '?mode=ro', uri=True)
        declaradas = _declaraciones(self.raiz / REGLAS_OPACAS)
        self.con_fichero = declaradas is not None
        self.privadas, self.filas, self.referencias = declaradas if declaradas is not None else ({}, {}, {})
        if not all(identidad_propia(self.arista(clave), predicados) for clave, predicados in self.privadas.items()):
            raise ReglaOpacaInvalida('una clave privada sin identidad propia: ni arista del censo ni un predicado suyo')

    def verificar(self, claves, registro, cobertura=True):
        """Que el fichero privado diga exactamente lo que el overlay y el registro `registro` necesitan: ninguna clave
        que ninguna regla opaca del overlay (`claves`) resuelve (AC127: una de más es una retirada que no se aplica); y
        en `filas:`, cobertura en las dos direcciones con las filas del registro que admiten una declaración
        (`filas_declarables`): ninguna declaración que no sea de una de ellas (AC171) y ninguna de ellas sin la suya
        (AC183: sin ella, su retirada no se comprueba), también con `filas:` vacío o sin fichero privado. Un registro
        que no está no tiene ninguna; uno que no se lee no se da por bueno. Sin `cobertura`, solo la primera dirección:
        la de quien escribe las declaraciones (`privatizar`, el apply), que comprueba lo que deja."""
        if set(self.privadas) - set(claves):
            raise ReglaOpacaInvalida('declaraciones privadas de una clave que ninguna regla opaca resuelve')
        if set(self.referencias) - set(claves):
            raise ReglaOpacaInvalida('referencias privadas de una clave que ninguna regla opaca resuelve')
        if not cobertura and not self.filas:
            return
        try:
            doc = leer_yaml(registro) if registro is not None and Path(registro).is_file() else {}
        except YamlInvalido:
            raise ReglaOpacaInvalida('el registro no se lee: no se puede contrastar `filas:`') from None
        filas = doc.get('rectificaciones') if isinstance(doc, dict) else None
        declarables = filas_declarables(filas if isinstance(filas, list) else [])
        if set(self.filas) - declarables:
            raise ReglaOpacaInvalida('declaraciones privadas de una fila que ninguna fila del registro resuelve')
        if cobertura and declarables - set(self.filas):
            raise ReglaOpacaInvalida('filas del registro que admiten una declaración privada sin la suya')

    def arista(self, clave):
        """La arista del censo de la unidad `clave`, o None si no es una."""
        fila = self.conn.execute('SELECT kind, value FROM units WHERE unit_id=?', (clave,)).fetchone()
        return json.loads(fila[1]) if fila is not None and fila[0] == 'edge' else None

    def resolver(self, regla):
        """[regla explícita] de la regla opaca `regla` (`{unidad, declaraciones?, kinds?, reason}`): la de su unidad del
        censo, como la escribe el apply (`regla_de`), y la de cada predicado que su clave declara en privado, que SE
        SUMA (el par actual de una arista re-identificada, o el predicado completo de una retirada sin unidad del censo:
        AC121), tal como se declaró, capas incluidas (sin ellas, judicial o nula: lo prepara el loader como una deny
        explícita, AC151); la de la unidad, con las capas de la opaca. Cada una lleva la clave y el motivo de la opaca.
        Una regla sin la forma de una opaca, una clave sin unidad del censo ni declaración privada o una sin identidad
        propia (solo predicados `ajena`, AC157): ReglaOpacaInvalida."""
        clave = regla.get('unidad')
        if not (set(regla) <= CAMPOS_OPACA and isinstance(clave, str) and _UNIDAD.fullmatch(clave)
                and _kinds_validos(regla.get('kinds') or None) and declaraciones_validas(regla)):
            raise ReglaOpacaInvalida('regla opaca sin la forma {unidad: <64 hex>, declaraciones?, kinds?, reason}')
        if not self.con_fichero:
            raise ReglaOpacaInvalida('regla opaca sin el fichero privado de la auditoría')
        if len(self.privadas.get(clave, ())) != regla.get('declaraciones', 0):
            # AC127: el fichero privado tiene que dar exactamente los predicados que la regla pública dice.
            raise ReglaOpacaInvalida('regla opaca con otras declaraciones privadas que las que dice')
        kinds = regla.get('kinds') or None
        out = []
        value = self.arista(clave)
        if value is not None:
            out.append({**regla_de(value), 'kinds': kinds})
        for p in self.privadas.get(clave, ()):
            out.append({'source_id': p['source_id'], 'target_id': p['target_id'], 'type': p['type'],
                        'doc': p.get('doc'), 'quote_contains': p.get('quote_contains'),
                        'kinds': p.get('kinds'), 'ajena': bool(p.get('ajena'))})
        if not out:
            raise ReglaOpacaInvalida('regla opaca sin unidad del censo ni declaración privada')
        if not identidad_propia(value, self.privadas.get(clave, ())):
            raise ReglaOpacaInvalida('una clave privada sin identidad propia: ni arista del censo ni un predicado suyo')
        return [{**{k: v for k, v in r.items() if k != 'ajena'}, 'unidad': clave,
                 'reason': MOTIVO_AJENA if r.get('ajena') else regla.get('reason')} for r in out]
