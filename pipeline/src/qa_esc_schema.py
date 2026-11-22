"""Decision contract of spec 063 (auditoría escalonada por entidad): ONE flat JSON schema for
the readers (Opus 5.5 with search, Astra blind) and the validator that turns a reader's JSON into
an accepted decision or a list of reasons to reject it. Addendum 063-B adds two more contracts at
the end of the module: the ARBITER's (`arbitration_schema_for`/`validate_arbitration`, one closed
choice per unit with cited evidence) and Astra's VALIDATION (`validation_schema_for`/
`validate_validation`, valida | no_valida per unit).

The schema is deliberately flat — no `oneOf/anyOf/allOf`, no union or null types, no numeric
or array-length bounds — because both strict structured-output modes refuse those keywords
(pilot 2026-09-09: codex answers HTTP 400 to `oneOf`). Unused fields travel as "" or []. Everything the
schema cannot express (coverage, coherence verdict⇔label⇔action, procedural vocabulary,
pseudonym rules, sub-pack rules, edge ops) is a validator rule here.

Vocabularies are imported from the loaders that will apply the decisions, never copied:
`accusations_loader` for statuses/tipos, `alcance` for `set:` props and kinds (the preparation
`relations_loader` itself uses, which also vets every edge op's load: spec 071 AC78) and
`relations_loader` for relationship types. If a loader tightens its rules, decisions tighten with it.
"""
from __future__ import annotations

import re

from .accusations_loader import (_FECHA_RE, _FUENTE_TIPOS, _STATUSES, _TIPO_PRENSA_STATUSES,
                                 _TIPOS_EXIGIDOS)
from . import seudonimos
from .alcance import PROPS_SET, RANGO_CAPA, CargaInvalida, carga, nombre_de_prop
from .ontologia import ontologia
from .relations_loader import _PROV, _REL_TYPES

VERDICTS = ('verificado', 'corregido', 'incierto')
LABELS = ontologia().etiquetas_defecto
CONFIDENCE = ('alta', 'media', 'baja')
ENTITY_ACTIONS = ('none', 'replace', 'remove')
ESTADO_ACTIONS = ('none', 'set')
EDGE_ACTIONS = ('keep', 'deny', 'retype', 'invert', 'set_prop', 'set_status')
EDITORIAL_ACTIONS = ('keep', 'replace', 'remove')
# Label classes used by the dual-agreement rule (spec AC7): two readers "agree" on a unit
# when their labels fall in the same class, not when the strings match.
LABEL_CLASS = ontologia().clase_etiqueta
# Labels that only make sense on one unit kind.
LABEL_KIND = {'alias': {'identity'}, 'arista': {'edge'}}
PROSE_KINDS = {'summary', 'attribute'}
# `status` has its own op (`set_status`) so the validator can check the edge type; the rest of
# the loader's `set:` allowlist goes through `set_prop`.
SET_PROPS = tuple(sorted(PROPS_SET - {'status'}))
STATUS_EDGE_TYPES = set(ontologia().procesales)
STATUSES = tuple(sorted(_STATUSES))
FUENTE_TIPOS = tuple(sorted(_FUENTE_TIPOS))
REL_TYPES = tuple(sorted(t for t in _REL_TYPES if t not in _PROV))
VAGUE = re.compile(r'seg[uú]n (?:la|los|las) (?:prensa|medios|informaciones|fuentes period)', re.I)
MAX_SEARCHES = 12

_S = {'type': 'string'}


def _enum(values):
    return {'type': 'string', 'enum': list(values)}


def _arr(item):
    return {'type': 'array', 'items': item}


def _obj(props):
    return {'type': 'object', 'additionalProperties': False, 'required': list(props), 'properties': props}


def unit_ids(pack, kind=None):
    return [u['unit_id'] for u in pack['units'] if kind is None or u.get('kind') == kind]


def ref_unidad(uid, conocidas) -> str:
    """Una unidad en un mensaje: los 8 primeros caracteres de su id si es de las `conocidas` (ids del censo); si no, es
    texto de la decisión y va su huella (spec 070, AC58): estos errores acaban en la terminal y en los PRs."""
    try:
        conocida = uid in conocidas
    except TypeError:
        conocida = False
    return str(uid)[:8] if conocida else f'huella {seudonimos.sha16(uid)}'


def ref_valor(valor, vocabulario) -> str:
    """Un valor de vocabulario cerrado en un mensaje: él mismo si es del vocabulario; si no, su huella (AC58)."""
    try:
        valido = valor in vocabulario
    except TypeError:
        valido = False
    return str(valor) if valido else f'huella {seudonimos.sha16(valor)}'


def aliases_to_retire(unit, asked):
    """(aliases, problem): the aliases of the `identity` unit that `asked` names, stripped, or why not.
    The unit is the whole block the census stores (`{'type', 'identity': {'name', 'aliases',
    'entity_id', …}}`, qa_dual): what gets denied are aliases of it, named one by one. A unit without
    that block is an error, never a fallback; the problem never repeats the aliases."""
    value = unit.get('value')
    if not (isinstance(value, dict) and isinstance(value.get('identity'), dict)):
        return [], 'la unidad de identidad no trae el bloque `identity`'
    known = {str(a).strip() for a in value['identity'].get('aliases') or []}
    if not (isinstance(asked, list) and asked and all(isinstance(a, str) and a.strip() for a in asked)):
        return [], (f'retirar una identidad exige `aliases: [...]` con los alias a retirar '
                    f'(la identidad tiene {len(known)} alias)')
    asked = [a.strip() for a in asked]
    if any(a not in known for a in asked):
        return [], 'un alias pedido no está en la identidad'
    return asked, None


def schema_for(pack):
    """The JSON schema for one pack: `unit_id` enums closed over the pack's units."""
    ids = unit_ids(pack)
    edge_ids = unit_ids(pack, 'edge')
    editorial_ids = unit_ids(pack, 'editorial')
    unit = _obj({'unit_id': _enum(ids), 'verdict': _enum(VERDICTS), 'label': _enum(LABELS),
                 'evidence': _S, 'confidence': _enum(CONFIDENCE), 'requested_sources': _arr(_S)})
    summary = _obj({'action': _enum(ENTITY_ACTIONS), 'resumen': _S, 'sources': _arr(_S), 'reason': _S})
    role = _obj({'action': _enum(ENTITY_ACTIONS), 'role': _S, 'reason': _S})
    estado = _obj({'action': _enum(ESTADO_ACTIONS), 'status': _enum((*STATUSES, '')), 'fecha': _S, 'doc': _S,
                   'url': _S, 'tipo': _enum((*FUENTE_TIPOS, '')), 'nota': _S})
    edge = _obj({'unit_id': _enum(edge_ids) if edge_ids else _S, 'action': _enum(EDGE_ACTIONS), 'new_type': _enum((*REL_TYPES, '')),
                 'prop': _enum((*SET_PROPS, '')), 'value': _S, 'status': _enum((*STATUSES, '')),
                 'evidence': _S, 'reason': _S})
    editorial = _obj({'unit_id': _enum(editorial_ids) if editorial_ids else _S, 'action': _enum(EDITORIAL_ACTIONS),
                      'text': _S, 'reason': _S})
    root = _obj({'entity_id': _enum([pack['entity']['id']]), 'units': _arr(unit), 'summary': summary, 'role': role,
                 'alias_deny': _arr(_S), 'estado': estado, 'edges': _arr(edge), 'editorial': _arr(editorial),
                 'notes': _S})
    root['$schema'] = 'http://json-schema.org/draft-07/schema#'
    return root


FORBIDDEN_KEYWORDS = ('oneOf', 'anyOf', 'allOf', 'minItems', 'maxItems', 'minimum', 'maximum', 'null')


def schema_is_flat(schema):
    """True when no keyword a strict mode refuses appears anywhere (types included)."""
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key in FORBIDDEN_KEYWORDS:
                    return False
                if key == 'type' and (isinstance(value, list) or value == 'null'):
                    return False
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return True


def _blank(decision, key, default):
    value = decision.get(key)
    return default if value is None else value


def normalize_decision(decision, pack):
    """Resolve the one documented ambiguity IN PLACE before validating: a reader that keeps an edge
    (`edges[].action == keep` or no entry) may label the unit `arista` as a *kind* marker while saying
    `verificado`; the contract wants `ok` there (`arista` = something changes). Astra did this on
    every kept edge in the pilot (2026-09-21). Returns the same dict."""
    if not isinstance(decision, dict):
        return decision
    kinds = {u['unit_id']: u.get('kind') for u in pack.get('units', [])}
    changed = {e.get('unit_id') for e in (decision.get('edges') or []) if e.get('action') not in (None, '', 'keep')}
    for u in decision.get('units') or []:
        if (kinds.get(u.get('unit_id')) == 'edge' and u.get('verdict') == 'verificado'
                and u.get('label') == 'arista' and u.get('unit_id') not in changed):
            u['label'] = 'ok'
    return decision


def validate_decision(decision, pack):
    """Every reason the decision cannot be accepted for this pack; [] means accepted.
    Normalizes the decision in place first (see `normalize_decision`), so what callers store is
    exactly what passed. The reasons end up in reports and logs: a value outside the closed
    vocabulary is never repeated, only its field and, in a list, its position (spec 071 AC87)."""
    normalize_decision(decision, pack)
    errors = []
    entity = pack['entity']
    eid = entity['id']
    if decision.get('entity_id') != eid:
        errors.append('entity_id distinto del de la entidad del pack')
    by_id = {u['unit_id']: u for u in pack['units']}
    seen = {}
    alias_deny = [a for a in _blank(decision, 'alias_deny', []) if str(a).strip()]
    units = _blank(decision, 'units', [])
    for n, u in enumerate(units, 1):
        uid = u.get('unit_id')
        if uid in seen:
            errors.append(f'units[{n}]: unidad repetida')
        seen[uid] = u
        if uid not in by_id:
            errors.append(f'units[{n}]: la unidad no pertenece al pack')
            continue
        kind = by_id[uid].get('kind')
        verdict, label = u.get('verdict'), u.get('label')
        if verdict not in VERDICTS or label not in LABELS:
            fuera = [campo for campo, valor, vocabulario in (('verdict', verdict, VERDICTS), ('label', label, LABELS))
                     if valor not in vocabulario]
            errors.append(f'unidad {uid[:8]}: {", ".join(fuera)} fuera de vocabulario')
            continue
        if not (u.get('evidence') or '').strip():
            errors.append(f'unidad {uid[:8]}: sin evidence')
        if u.get('confidence') not in CONFIDENCE:
            errors.append(f'unidad {uid[:8]}: confidence fuera de vocabulario')
        if verdict == 'verificado' and label != 'ok':
            errors.append(f'unidad {uid[:8]}: verificado exige label ok (tiene {label})')
        if verdict == 'incierto':
            if label != 'ok':
                errors.append(f'unidad {uid[:8]}: incierto no cambia nada (label {label})')
            if not [s for s in _blank(u, 'requested_sources', []) if str(s).strip()]:
                errors.append(f'unidad {uid[:8]}: incierto exige requested_sources')
        if verdict == 'corregido' and label == 'ok':
            errors.append(f'unidad {uid[:8]}: corregido exige una etiqueta de cambio')
        if label in LABEL_KIND and kind not in LABEL_KIND[label]:
            errors.append(f'unidad {uid[:8]}: label {label} solo cabe en unidades {sorted(LABEL_KIND[label])}, es {kind}')
        if label == 'retirada' and kind == 'edge':
            errors.append(f'unidad {uid[:8]}: las aristas se retiran con label arista + action deny')
        if kind == 'identity' and (label == 'retirada' or verdict == 'corregido') and not alias_deny:
            errors.append(f'unidad {uid[:8]}: las identidades se retiran con label alias + alias_deny')
    missing = [uid for uid in by_id if uid not in seen]
    if missing:
        errors.append(f'{len(missing)} unidades del pack sin decisión: ' + ', '.join(m[:8] for m in missing[:10]))
    corrected = {uid: u for uid, u in seen.items() if uid in by_id and u.get('verdict') == 'corregido'}
    # Kinds the pack actually carries. An entity-level action whose kind has NO unit in this pack
    # (a sub-pack of edges, prose already verified) cannot hang on a corrected unit: it is allowed
    # with a `reason` and flagged as «sin ancla» — the router never closes it alone (piloto 2026-09-21).
    kinds = {u.get('kind') for u in pack['units']}

    def corrected_of(kind=None, label=None):
        return [uid for uid, u in corrected.items()
                if (kind is None or by_id[uid].get('kind') == kind) and (label is None or u.get('label') == label)]

    summary = _blank(decision, 'summary', {})
    role = _blank(decision, 'role', {})
    estado = _blank(decision, 'estado', {})
    edges = _blank(decision, 'edges', [])
    editorial = _blank(decision, 'editorial', [])
    s_action, r_action, e_action = summary.get('action', 'none'), role.get('action', 'none'), estado.get('action', 'none')
    first = int(pack.get('index') or 1) == 1
    if not first and (s_action != 'none' or r_action != 'none' or alias_deny or e_action != 'none'):
        errors.append('sub-pack que no es el primero: summary/role/alias_deny/estado deben ir vacíos (usa notes)')
    if entity.get('is_pseudonym'):
        if s_action == 'replace' or r_action == 'replace':
            errors.append('seudónimo judicial: la prosa se retira (remove), nunca se reescribe')
        if e_action == 'set' and (estado.get('status') or '') != 'investigado':
            errors.append('seudónimo judicial: la fila de estado solo puede ser investigado (el suelo)')
    # --- prose actions ⇔ corrected prose units
    if s_action not in ENTITY_ACTIONS:
        errors.append('summary.action: valor fuera del vocabulario')
    elif s_action == 'replace':
        text = (summary.get('resumen') or '').strip()
        if not text:
            errors.append('summary replace sin resumen')
        if VAGUE.search(text):
            errors.append('resumen con «según la prensa/los medios» sin medio nominal')
        if not (summary.get('reason') or '').strip():
            errors.append('summary replace sin reason')
    elif s_action == 'remove':
        if (summary.get('resumen') or '').strip():
            errors.append('summary remove con resumen')
        if not (summary.get('reason') or '').strip():
            errors.append('summary remove sin reason')
    if s_action != 'none' and first and 'summary' in kinds and not corrected_of('summary'):
        errors.append('summary cambia pero ninguna unidad summary es corregido')
    if r_action not in ENTITY_ACTIONS:
        errors.append('role.action: valor fuera del vocabulario')
    elif r_action == 'replace':
        text = (role.get('role') or '').strip()
        if not text:
            errors.append('role replace sin role')
        if VAGUE.search(text):
            errors.append('role con «según la prensa» sin medio nominal')
    elif r_action == 'remove' and (role.get('role') or '').strip():
        errors.append('role remove con texto')
    if r_action != 'none' and first and 'attribute' in kinds and not corrected_of('attribute'):
        errors.append('role cambia pero ninguna unidad attribute (rol) es corregido')
    for uid in corrected_of('summary'):
        if corrected[uid].get('label') in ('cita', 'atrib', 'reescrita', 'retirada') and s_action == 'none' and first:
            errors.append(f'unidad {uid[:8]} corregida ({corrected[uid]["label"]}) sin acción sobre summary')
    for uid in corrected_of('attribute'):
        if corrected[uid].get('label') in ('cita', 'atrib', 'reescrita', 'retirada') and r_action == 'none' and first:
            errors.append(f'unidad {uid[:8]} corregida ({corrected[uid]["label"]}) sin acción sobre role')
    # --- aliases
    if alias_deny and first and 'identity' in kinds and not corrected_of('identity', 'alias'):
        errors.append('alias_deny sin unidad identity corregida con label alias')
    if corrected_of(None, 'alias') and not alias_deny:
        errors.append('label alias sin alias_deny')
    civil = {str(entity.get('name') or '').strip().casefold()}
    # La posición y no el alias: estos errores acaban en informes y logs.
    for pos, alias in enumerate(_blank(decision, 'alias_deny', []), 1):
        if str(alias).strip() and str(alias).strip().casefold() in civil:
            errors.append(f'alias_deny[{pos}] es el nombre civil de la entidad')
    # --- estado
    if e_action not in ESTADO_ACTIONS:
        errors.append('estado.action: valor fuera del vocabulario')
    elif e_action == 'set':
        status, tipo = estado.get('status') or '', estado.get('tipo') or ''
        if status not in _STATUSES:
            errors.append('estado.status fuera de vocabulario')
        if tipo not in _FUENTE_TIPOS:
            errors.append('estado.tipo fuera de vocabulario')
        exige = ontologia().fuente_exigida.get(status)
        if exige in _TIPOS_EXIGIDOS and tipo not in _TIPOS_EXIGIDOS[exige]:
            nombre = 'un acto acusatorio' if exige == 'acto_acusatorio' else f'una {exige}'
            errors.append(f'estado {status} exige {nombre} (tipo ∈ {sorted(_TIPOS_EXIGIDOS[exige])})')
        if tipo == 'prensa' and status not in _TIPO_PRENSA_STATUSES:
            errors.append(f'la prensa solo sostiene {sorted(_TIPO_PRENSA_STATUSES)}'
                          + (f', no {status}' if status in _STATUSES else ''))
        if not _FECHA_RE.match(estado.get('fecha') or ''):
            errors.append('estado.fecha debe ser YYYY-MM-DD')
        if not ((estado.get('doc') or '').strip() or (estado.get('url') or '').strip()):
            errors.append('estado set exige doc o url')
        anchored = corrected_of(None, 'estado') or any(e.get('action') == 'set_status' for e in edges)
        if not anchored and not (estado.get('nota') or '').strip():
            errors.append('estado set sin unidad con label estado, sin set_status de arista y sin nota que lo justifique')
    if corrected_of(None, 'estado') and e_action != 'set':
        errors.append('label estado sin estado.action = set')
    # --- edges
    edge_units = {uid for uid, u in by_id.items() if u.get('kind') == 'edge'}
    edge_seen = {}
    for n, e in enumerate(edges, 1):
        uid = e.get('unit_id')
        if uid not in edge_units:
            errors.append(f'edges[{n}]: no es una arista del pack')
            continue
        if uid in edge_seen:
            errors.append(f'edges: {uid[:8]} repetida')
        edge_seen[uid] = e
        action = e.get('action')
        current = by_id[uid].get('edge') or {}
        if action not in EDGE_ACTIONS:
            errors.append(f'edges {uid[:8]}: action: valor fuera del vocabulario')
            continue
        pares = []
        if action == 'retype':
            new_type = e.get('new_type') or ''
            if new_type not in REL_TYPES:
                errors.append(f'edges {uid[:8]}: new_type fuera del vocabulario')
            elif new_type == current.get('type'):
                errors.append(f'edges {uid[:8]}: retype al mismo tipo')
        if action in ('retype', 'invert') and e.get('set') is not None:
            # Un retype o un invert con las props que su regla escribe en la misma fila del overlay (`set`, spec 071
            # AC71): una operación, varias fases. Sin el mapa, la regla solo retipa o invierte.
            mapa = e['set']
            if isinstance(mapa, dict) and mapa:
                pares = list(mapa.items())
            else:
                errors.append(f'edges {uid[:8]}: {action} con un set vacío o que no es un mapa')
        elif action == 'set_prop':
            # Un prop (`prop` + `value`) o varios en una sola operación (`set`: {prop: valor}, spec 071 enmienda 11):
            # la carga de una regla que corrige a la vez la nota y la página.
            mapa = e.get('set')
            if mapa is not None and (e.get('prop') or str(e.get('value') or '').strip()):
                errors.append(f'edges {uid[:8]}: set_prop con prop/value y set a la vez')
            if mapa is None:
                pares = [(e.get('prop') or '', e.get('value'))]
            elif isinstance(mapa, dict) and mapa:
                pares = list(mapa.items())
            else:
                errors.append(f'edges {uid[:8]}: set_prop con un set vacío o que no es un mapa')
        elif action == 'set_status':
            if (e.get('status') or '') not in _STATUSES:
                errors.append(f'edges {uid[:8]}: status fuera de vocabulario')
            if current.get('type') not in STATUS_EDGE_TYPES:
                errors.append(f'edges {uid[:8]}: set_status solo en {sorted(STATUS_EDGE_TYPES)}')
        if e.get('set') is not None and action not in ('set_prop', 'retype', 'invert'):
            errors.append(f'edges {uid[:8]}: {action} con set: solo un set_prop, un retype o un invert cambian props')
        for n, (prop, value) in enumerate(pares, 1):
            value = str(value if value is not None else '').strip()
            if prop not in SET_PROPS:
                # Spec 071 AC82: la clave, solo si es del vocabulario cerrado; si no, su posición (puede ser un nombre).
                nombre = nombre_de_prop(prop, n)
                nombre = f'prop {nombre!r}' if nombre == prop else nombre
                errors.append(f'edges {uid[:8]}: {nombre} fuera de la lista cerrada {SET_PROPS}')
            if not value:
                errors.append(f'edges {uid[:8]}: {action} sin value')
            if prop == 'source_kind':
                old = current.get('source_kind')
                if value not in RANGO_CAPA:
                    errors.append(f'edges {uid[:8]}: source_kind desconocido')
                elif old in RANGO_CAPA and RANGO_CAPA[value] < RANGO_CAPA[old]:
                    errors.append(f'edges {uid[:8]}: la procedencia solo puede bajar ({old} → {value})')
        try:
            # La carga que escribiría el loader (spec 071 AC78): una que descarta no es «sin set». El motivo nombra el
            # prop, nunca el valor.
            carga(e)
        except CargaInvalida as exc:
            errors.append(f'edges {uid[:8]}: operación con carga inválida ({exc})')
        if action != 'keep':
            u = seen.get(uid) or {}
            if not (u.get('verdict') == 'corregido' and u.get('label') == 'arista'):
                errors.append(f'edges {uid[:8]}: acción {action} exige unidad corregida con label arista')
            if not (e.get('reason') or '').strip():
                errors.append(f'edges {uid[:8]}: acción {action} sin reason')
    for uid in corrected_of('edge', 'arista'):
        if (edge_seen.get(uid) or {}).get('action', 'keep') == 'keep':
            errors.append(f'unidad {uid[:8]} corregida (arista) sin acción ≠ keep en edges')
    # --- editorial
    editorial_units = {uid for uid, u in by_id.items() if u.get('kind') == 'editorial'}
    ed_seen = {}
    for n, e in enumerate(editorial, 1):
        uid = e.get('unit_id')
        if uid not in editorial_units:
            errors.append(f'editorial[{n}]: no es un ítem editorial del pack')
            continue
        if uid in ed_seen:
            errors.append(f'editorial: {uid[:8]} repetido')
        ed_seen[uid] = e
        action = e.get('action')
        text = (e.get('text') or '').strip()
        if action not in EDITORIAL_ACTIONS:
            errors.append(f'editorial {uid[:8]}: action: valor fuera del vocabulario')
            continue
        if action == 'replace' and not text:
            errors.append(f'editorial {uid[:8]}: replace sin text')
        elif action == 'remove' and text:
            errors.append(f'editorial {uid[:8]}: remove con text')
        if text and VAGUE.search(text):
            errors.append(f'editorial {uid[:8]}: «según la prensa» sin medio nominal')
        u = seen.get(uid) or {}
        if action != 'keep' and u.get('verdict') != 'corregido':
            errors.append(f'editorial {uid[:8]}: acción {action} exige unidad corregida')
    for uid in corrected_of('editorial'):
        if (ed_seen.get(uid) or {}).get('action', 'keep') == 'keep':
            errors.append(f'unidad editorial {uid[:8]} corregida sin acción en editorial')
    return errors


def changes(decision, pack=None):
    """What a decision changes, for the router: label set, destructive flag, entity actions."""
    labels = {u.get('label') for u in _blank(decision, 'units', [])}
    verdicts = {u.get('verdict') for u in _blank(decision, 'units', [])}
    summary = _blank(decision, 'summary', {}).get('action', 'none')
    role = _blank(decision, 'role', {}).get('action', 'none')
    estado = _blank(decision, 'estado', {}).get('action', 'none')
    edge_actions = {e.get('action') for e in _blank(decision, 'edges', [])} - {'keep'}
    destructive = bool(labels & {'retirada', 'estado', 'alias'} or summary == 'remove' or role == 'remove'
                       or edge_actions or estado == 'set')
    kinds = {u.get('kind') for u in (pack or {}).get('units', [])} if pack else None
    unanchored = bool(kinds is not None and ((summary != 'none' and 'summary' not in kinds)
                                            or (role != 'none' and 'attribute' not in kinds)
                                            or (_blank(decision, 'alias_deny', []) and 'identity' not in kinds)
                                            or (estado == 'set' and not labels & {'estado'})))
    return {'labels': labels, 'verdicts': verdicts, 'summary': summary, 'role': role, 'estado': estado, 'unanchored': unanchored,
            'edge_actions': edge_actions, 'alias_deny': bool(_blank(decision, 'alias_deny', [])),
            'destructive': destructive, 'notes': bool((decision.get('notes') or '').strip()),
            'incierto': 'incierto' in verdicts}


# ── 063-B: arbiter and validator contracts ────────────────────────────────────

ARB_CHOICES = ('mantener-opus', 'adoptar-astra', 'retirar', 'verificar', 'otra')
# Which reader each «keep/adopt» choice starts from (the arbiter can only pick a LIVE reading).
ARB_SOURCE = {'mantener-opus': 'opus', 'adoptar-astra': 'astra-esc'}
# `otra` has nowhere to go on an edge (the brief forbids it); on an alias it goes to the owner.
OTRA_FORBIDDEN_KINDS = {'edge'}
VAL_VERDICTS = ('valida', 'no_valida')


def arbitration_schema_for(pack):
    """The arbiter's schema for one (restricted) pack: one row per unit, citations flat (`{doc,
    pagina, url}` with "" for the unused half: strict modes refuse unions). `aliases` names the
    aliases a `retirar` withdraws from an identity unit ([] on every other row)."""
    cita = _obj({'doc': _S, 'pagina': _S, 'url': _S})
    unit = _obj({'unit_id': _enum(unit_ids(pack)), 'choice_id': _enum(ARB_CHOICES), 'text': _S,
                 'sostenido': {'type': 'boolean'}, 'evidence': _S, 'citas': _arr(cita),
                 'al_dueno': {'type': 'boolean'}, 'motivo': _S, 'aliases': _arr(_S)})
    root = _obj({'units': _arr(unit)})
    root['$schema'] = 'http://json-schema.org/draft-07/schema#'
    return root


def cita_ok(cita):
    """A citation points at something extractable: document + page, or an archived URL."""
    if not isinstance(cita, dict):
        return False
    doc, page, url = (str(cita.get(k) or '').strip() for k in ('doc', 'pagina', 'url'))
    return bool(url) or bool(doc and re.search(r'\d', page))


def _reader_row(decision, unit_id):
    return next((u for u in (decision or {}).get('units') or [] if u.get('unit_id') == unit_id), None)


def validate_arbitration(result, pack, readers):
    """Every reason the arbiter's JSON cannot be accepted for this pack; [] means accepted.

    `readers` = `{'opus': decision|None, 'astra-esc': decision|None}` — the LIVE readings the arbiter
    saw. Rules: every unit exactly once; `otra` needs `text` and never on an edge; keeping/adopting a
    reader needs that reader's valid, non-`incierto` row for the unit; `citas` may only be empty on
    `retirar`; `sostenido=false` only on `retirar`; `retirar` on an identity names, in `aliases`,
    aliases of that identity (the unit is the whole block: name, aliases, id).
    """
    if not isinstance(result, dict) or not isinstance(result.get('units'), list):
        return ['sin units']
    errors = []
    by_id = {u['unit_id']: u for u in pack['units']}
    seen = set()
    for n, row in enumerate(result['units'], 1):
        if not isinstance(row, dict):
            errors.append('fila de unidad que no es un objeto')
            continue
        uid = row.get('unit_id')
        if uid in seen:
            errors.append(f'units[{n}]: unidad repetida')
        seen.add(uid)
        if uid not in by_id:
            errors.append(f'units[{n}]: la unidad no pertenece al pack')
            continue
        short = uid[:8]
        kind = by_id[uid].get('kind')
        choice = row.get('choice_id')
        if choice not in ARB_CHOICES:
            errors.append(f'unidad {short}: choice_id: valor fuera del vocabulario')
            continue
        text = str(row.get('text') or '').strip()
        if choice == 'otra':
            if kind in OTRA_FORBIDDEN_KINDS:
                errors.append(f'unidad {short}: «otra» no cabe en una unidad {kind}')
            if not text:
                errors.append(f'unidad {short}: «otra» exige text')
            elif VAGUE.search(text):
                errors.append(f'unidad {short}: «según la prensa» sin medio nominal')
        if choice == 'retirar' and kind == 'identity':
            problem = aliases_to_retire(by_id[uid], row.get('aliases'))[1]
            if problem:
                errors.append(f'unidad {short}: {problem}')
        if choice in ARB_SOURCE:
            source = ARB_SOURCE[choice]
            reader = _reader_row(readers.get(source), uid)
            if reader is None:
                errors.append(f'unidad {short}: {choice} sin lectura válida de {source}')
            elif reader.get('verdict') == 'incierto':
                errors.append(f'unidad {short}: {choice} sobre una lectura incierta de {source}')
        if not isinstance(row.get('sostenido'), bool) or not isinstance(row.get('al_dueno'), bool):
            errors.append(f'unidad {short}: sostenido/al_dueno deben ser booleanos')
        elif not row['sostenido'] and choice != 'retirar':
            errors.append(f'unidad {short}: no sostenido con {choice} (solo cabe retirar)')
        if not str(row.get('evidence') or '').strip():
            errors.append(f'unidad {short}: sin evidence')
        citas = row.get('citas') if isinstance(row.get('citas'), list) else None
        if citas is None:
            errors.append(f'unidad {short}: citas debe ser una lista')
        elif choice != 'retirar' and not citas:
            errors.append(f'unidad {short}: {choice} sin citas')
        elif any(not cita_ok(c) for c in citas):
            errors.append(f'unidad {short}: cita sin doc+pagina ni url')
    missing = [uid for uid in by_id if uid not in seen]
    if missing:
        errors.append(f'{len(missing)} unidades del pack sin decisión del árbitro: '
                      + ', '.join(m[:8] for m in missing[:10]))
    return errors


def validation_schema_for(unit_ids_):
    """Astra's validation schema: `{units: [{unit_id, veredicto, motivo}]}` closed over the units."""
    unit = _obj({'unit_id': _enum(unit_ids_), 'veredicto': _enum(VAL_VERDICTS), 'motivo': _S})
    root = _obj({'units': _arr(unit)})
    root['$schema'] = 'http://json-schema.org/draft-07/schema#'
    return root


def validate_validation(result, unit_ids_):
    """Every reason Astra's validation cannot be accepted; [] means accepted."""
    if not isinstance(result, dict) or not isinstance(result.get('units'), list):
        return ['sin units']
    errors, seen, wanted = [], set(), set(unit_ids_)
    for n, row in enumerate(result['units'], 1):
        uid = (row or {}).get('unit_id') if isinstance(row, dict) else None
        if uid in seen:
            errors.append(f'units[{n}]: unidad repetida')
        seen.add(uid)
        if uid not in wanted:
            errors.append(f'units[{n}]: la unidad no pertenece a la validación')
        elif row.get('veredicto') not in VAL_VERDICTS:
            errors.append(f'unidad {str(uid)[:8]}: veredicto fuera de vocabulario')
        elif row['veredicto'] == 'no_valida' and not str(row.get('motivo') or '').strip():
            errors.append(f'unidad {str(uid)[:8]}: no_valida sin motivo')
    missing = [uid for uid in unit_ids_ if uid not in seen]
    if missing:
        errors.append(f'{len(missing)} unidades sin veredicto: ' + ', '.join(m[:8] for m in missing[:10]))
    return errors
