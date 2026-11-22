"""Orquestador y latido de la auditoría escalonada por entidad (spec 063).

El disco es la verdad: el estado del bucle en `<root>/esc-state.json`, los packs en
`esc/packs/`, los recibos en `esc/attempts/`, las decisiones cerradas en `esc/decisions/` y
el ledger de la 061 (`census.sqlite`) con las tablas aditivas `esc_packs`/`esc_readings`.
Cada subcomando es UNA etapa determinista —censar, clasificar, empaquetar, leer, enrutar,
escalar, arbitrar—; `step` decide cuál toca, la lanza como subproceso bajo el `runner.lock` compartido
con la 061 y termina en menos de diez segundos sin llamar a ningún modelo. Nada de esto abre
Neo4j ni Render: las correcciones salen como overlays que aplica `qa_esc_apply` (T10).

    cd pipeline && .venv/bin/python -m src.qa_esc --root <audit> --repo .. step
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import uuid

from .alcance import RANGO_CAPA
from .ontologia import ontologia
from .qa_dual import _alive, utcnow  # noqa: F401  (`_alive` lo parchean los tests)
from .qa_dual_packets import AMPLIADO_LIMITS, DEFAULT_LIMITS
from .qa_esc_opus import DEFAULT_EFFORTS, MODELS, effort_ok, floor_effort
from .qa_esc_packs import (ESC_DIR, SCOPES, WORKTREE, build_packs, ensure_esc_schema, entity_facts,
                           entity_queue, load_pack, render_pack, rule_flags, write_pack)
from .qa_esc_schema import changes as decision_changes, schema_for, validate_decision
from .qa_integral import canonical, connect, digest, now
from .qa_integral_runner import supervisor_lock
from . import qa_esc_opus, qa_esc_jev

REPO = Path(__file__).resolve().parents[2]
SPEC_DIR = 'specs/063-auditoria-escalonada-por-entidad'
BRIEF = 'BRIEF.md'
# El único prefijo que el lector puede ejecutar: la ruta real del buscador, no la de `--repo`
# (que en los tests es un árbol de juguete).
EV_PATH = Path(__file__).resolve().parent / 'qa_esc_ev.py'
STATE_FILE = 'esc-state.json'
SCHEMA_VERSION = 1

# Estados de pack (CONTRACTS.md §API de T9). `routed`/`dual-read`/`routed2` son transitorios:
# el router deja el pack ya en su estado final (`closed`, `dual-pending` o `tanda`). `arb-pending`
# (063-B): lo que el dual no cerró —o un lado sin lectura válida— espera al árbitro.
PACK_STATES = ('queued', 'classified', 'packed', 'opus-read', 'routed', 'dual-pending', 'dual-read',
               'arb-pending', 'closed', 'tanda', 'applied')
# Enrutado POR UNIDAD (dueño, 2026-10-01): el estado del pack es el de la máquina (dual, árbitro) sobre
# sus unidades `pending`; las del dueño esperan como `needs-human` aunque el pack siga en `dual-pending`
# o `arb-pending`, y la tanda las encuentra por la unidad (`owner_pack_ids`). Cuando a la máquina no le
# queda nada, el pack queda `closed` si todo está cerrado o `tanda` si aún espera al dueño (`settle`).
# La prosa de un bloque (resumen, rol) se decide entera, como en el árbitro.
PROSE_BLOCKS = {'summary': 'summary', 'attribute': 'role'}
# Estados en los que la máquina aún trabaja el pack.
MACHINE_STATES = ('dual-pending', 'dual-read', 'arb-pending')
# Lados vivos. `fable-esc` (piloto 063) queda en `esc_readings` como histórico y ningún paso lo lee.
SIDES = ('opus', 'opus-qc', 'astra-esc', 'opus-arb', 'astra-val', 'jev-class', 'jev-route')
READER_SIDES = ('opus', 'opus-qc')
DUAL_SIDES = ('astra-esc',)
# Familias de resolución que escribe este bucle (las de `humano-<fecha>` las escribe T9).
# `triple-cerrada` es histórica (piloto con Fable) y sigue contando como cerrada.
RESOLUTIONS = ('opus-cerrada', 'dual-cerrada', 'arbitraje-pendiente', 'arbitraje-<fecha>',
               'arbitraje-<fecha>-prudente', 'tanda-<fecha>-pendiente')
ARB_PENDING = 'arbitraje-pendiente'
ROUTE_ORDER = {'cerrar': 0, 'escalar': 1, 'dueno': 2}
ACCUSATORY = ontologia().procesales
EFFORTS = dict(DEFAULT_EFFORTS)
# Riesgo de mayor a menor: el `RISK_ORDER` de la 061 no conoce «critico», que sí sale del
# clasificador y de las reglas (familiar, seudónimo, menor).
ESC_RISK_ORDER = {'critico': 0, 'alto': 1, 'medio': 2, 'bajo': 3}
SOFT_CAP_CLAUDE = 6_500_000
# La cuota de Astra es desconocida (spec §Preguntas abiertas): tope provisional que el piloto fija.
SOFT_CAP_ASTRA = 2_000_000
RESET_SECONDS = 18000
PAUSE_RETRY_SECONDS = 3600
MAX_CONSEC_FAIL = 3
# Una lectura nula se reintenta UNA vez sobre el mismo pack; a la segunda el pack va a tanda.
MAX_READ_ATTEMPTS = 2
# Un pack leído que sigue sin enrutar pasadas dos horas no tiene dueño: parada explícita.
ORPHAN_SECONDS = 7200
PER_FIRE = {'classify': 20, 'pack': 6, 'opus': 3, 'escalate': 2, 'arbitrate': 2}
# Fallos de CALIDAD del lector (van a reintento/tanda): nunca alimentan el disyuntor, que es para la
# infraestructura (timeouts, procesos muertos, JSON ilegible).
# Esfuerzo de Astra en el dual: `high` siempre — en el piloto el lector ciego a xhigh gastaba 118 k tokens
# frescos y 30-38 k de salida por pack ($2,9) sin cambiar el veredicto; codex no acepta xhigh.
DUAL_EFFORT = 'high'
# Astra ciega por defecto: con herramienta gastaba ~740 k tokens por pack en el piloto (2026-09-21).
ASTRA_TOOLS_DEFAULT = 'never'
# Fuentes precargadas por pack: `ampliado` (061-C) o `normal`, ~5 veces menos texto; el lector
# busca lo que falte (piloto 24-sep: las fuentes eran el 78 % del pack y dos tercios del coste).
SOURCES = {'normal': DEFAULT_LIMITS, 'ampliado': AMPLIADO_LIMITS}
SOURCES_DEFAULT = 'ampliado'
READER_ERRORS = {'invalid-decision', 'unexpected-effort', 'unexpected-tool-use', 'permission-denied',
                 'tool-budget-exceeded', 'missing-structured-output', 'invalid-arbitration',
                 'invalid-validation'}
FIRE_ACTIONS = {'CLASSIFY': 'classify', 'PACK': 'pack', 'OPUS': 'opus', 'ROUTE': 'route',
                'ESCALATE': 'escalate', 'ARBITRATE': 'arbitrate'}
PROVISIONAL = 'Q-'
# Ámbito del cursor: `pending` (lo no auditado) o `verified-qc` (control de calidad de la 061).
QC_SCOPE = 'verified-qc'


# ── estado en disco ───────────────────────────────────────────────────────────

def state_path(root):
    return Path(root) / STATE_FILE


def initial_state(scope='pending'):
    return {'schemaVersion': SCHEMA_VERSION, 'startedAt': now(), 'updatedAt': now(), 'halted': False,
            'haltReason': None, 'consecutiveFailures': 0, 'firingCount': 0, 'lastError': None,
            'window': {'startedAt': now(), 'resetSeconds': RESET_SECONDS, 'usedClaude': 0,
                       'softCapClaude': SOFT_CAP_CLAUDE, 'cachedClaude': 0, 'usedAstra': 0,
                       'softCapAstra': SOFT_CAP_ASTRA, 'pausedUntil': None},
            'perFire': dict(PER_FIRE), 'efforts': dict(EFFORTS), 'sources': SOURCES_DEFAULT,
            # Jev no clasifica ni enruta hasta que `calibrate` + `adoption_gate` lo autoricen (AC5).
            'jevRouting': False, 'jevClass': False,
            'cursor': {'scope': scope}, 'batches': []}


def save_state(root, state):
    state['updatedAt'] = now()
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, path)
    return state


def load_state(root):
    path = state_path(root)
    if not path.exists():
        raise ValueError(f'no hay {STATE_FILE}; ejecuta `qa-esc-census` primero')
    return json.loads(path.read_text())


def _state_or_new(root, scope='pending'):
    try:
        return load_state(root)
    except ValueError:
        return save_state(root, initial_state(scope))


def _ok(root):
    """Una etapa que sale bien cierra el breaker."""
    state = load_state(root)
    state['consecutiveFailures'] = 0
    state['lastError'] = None
    return save_state(root, state)


_RESET_AT = re.compile(r'try again at ([A-Z][a-z]{2})[a-z]* (\d{1,2})(?:st|nd|rd|th)?, (\d{4}),? '
                       r'(\d{1,2}):(\d{2}) ?([AP]M)', re.IGNORECASE)


def quota_reset_at(root, receipt):
    """Instante (epoch) que el propio aviso de cuota da para reintentar, o None.

    Codex dice «try again at Sep 28th, 2026 1:47 PM» en hora local cuando lo que se agota es el
    límite semanal: pausar una hora a ciegas solo relanza el mismo pack cada hora hasta entonces.
    """
    attempt = (receipt or {}).get('attempt_id')
    if not attempt:
        return None
    folder = Path(root) / 'esc' / 'attempts' / attempt
    text = ''.join(f.read_text(errors='replace') for f in (folder / 'events.jsonl', folder / 'stderr.txt')
                   if f.exists())
    match = _RESET_AT.search(text)
    if not match:
        return None
    mon, day, year, hour, minute, half = match.groups()
    try:
        stamp = datetime.strptime(f'{mon[:3].title()} {day} {year} {hour}:{minute} {half.upper()}',
                                  '%b %d %Y %I:%M %p')
    except ValueError:
        return None
    return stamp.astimezone().timestamp()  # naive = hora local del Mac, como la escribe Codex


def _fail(root, ref, reason, receipt=None):
    """Semántica del latido 061: la cuota espera, la configuración para, lo demás cuenta.
    Si el aviso de cuota trae su hora de reinicio, la pausa llega hasta ella."""
    state = load_state(root)
    state['lastError'] = {'ref': ref, 'reason': reason, 'at': now()}
    if reason == 'quota':
        retry = utcnow().timestamp() + PAUSE_RETRY_SECONDS
        state['window']['pausedUntil'] = max(retry, quota_reset_at(root, receipt) or 0)
    elif str(reason).startswith('config'):
        state['halted'] = True
        state['haltReason'] = f'configuración: {reason}'
    else:
        state['consecutiveFailures'] += 1
        if state['consecutiveFailures'] >= MAX_CONSEC_FAIL:
            state['halted'] = True
            state['haltReason'] = f'{MAX_CONSEC_FAIL} fallos consecutivos no de cuota'
    return save_state(root, state)


def _count_claude(root, usage):
    """Tokens frescos de la suscripción: entrada + escritura de caché + salida + razonamiento.
    Las relecturas de caché se guardan aparte (`cachedClaude`): el piloto dirá si cuentan."""
    usage = usage or {}
    state = load_state(root)
    window = state['window']
    window['usedClaude'] += sum(int(usage.get(key) or 0)
                                for key in ('input', 'cache_write', 'output', 'reasoning'))
    window['cachedClaude'] = window.get('cachedClaude', 0) + int(usage.get('cached') or 0)
    return save_state(root, state)


def _count_astra(root, usage):
    usage = usage or {}
    state = load_state(root)
    state['window']['usedAstra'] += sum(int(value) for key, value in usage.items()
                                        if key != 'cached' and isinstance(value, (int, float)))
    return save_state(root, state)


def _roll_window(state):
    """True si la ventana ha vencido (el llamador la reinicia y guarda)."""
    window = state['window']
    started = _timestamp(window.get('startedAt'))
    return started is not None and utcnow().timestamp() >= started + window.get('resetSeconds',
                                                                               RESET_SECONDS)


def _timestamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


# ── utilidades del ledger ─────────────────────────────────────────────────────

def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _norm_risk(riesgo):
    riesgo = str(riesgo or '').strip().lower().replace('crítico', 'critico')
    return riesgo if riesgo in ESC_RISK_ORDER else 'medio'


def worst(*risks):
    """El peor riesgo de los que llegan (regla, Jev, unidad); ignora lo vacío."""
    known = [_norm_risk(r) for r in risks if str(r or '').strip()]
    return min(known, key=ESC_RISK_ORDER.get) if known else 'medio'


def _event(conn, unit_id, event, payload):
    seq = conn.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events').fetchone()[0]
    conn.execute('INSERT INTO events(seq,at,unit_id,event,payload) VALUES (?,?,?,?,?)',
                 (seq, now(), unit_id, event, canonical(payload)))


def _rejected(conn, pack_id, side):
    """Cuántas lecturas nulas lleva este pack por este lado (el reintento se cuenta en `events`)."""
    return conn.execute("SELECT count(*) FROM events WHERE event='reading-rejected' AND payload LIKE ?",
                        (f'%"pack_id":"{pack_id}","side":"{side}"%',)).fetchone()[0]


def _packs(conn, state, limit=None, entities=None):
    """Packs en un estado, lo peor primero (riesgo, luego el más grande, luego el id)."""
    rows = [dict(r) for r in conn.execute('SELECT * FROM esc_packs WHERE state=?', (state,))
            if entities is None or r['entity'] in entities]
    rows.sort(key=lambda r: (ESC_RISK_ORDER.get(_norm_risk(r['riesgo']), 9),
                             -len(json.loads(r['unit_ids'] or '[]')), r['pack_id']))
    return rows[:limit] if limit else rows


def entity_filter(value):
    """`--entities`: un JSON con la lista de `entity_id` o la lista separada por comas.

    Acota una etapa a un subconjunto de la cola (el piloto trabaja con 30 entidades, la mitad
    en replay con su propio `--overlay-ref`). Sin valor, `None`: la etapa ve la cola entera.
    """
    if not value:
        return None
    path = Path(value)
    if path.exists():
        names = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(names, list):
            raise ValueError(f'{path} no contiene una lista de entity_id')
    else:
        names = str(value).split(',')
    entities = {str(name).strip() for name in names if str(name).strip()}
    if not entities:
        raise ValueError(f'--entities sin ninguna entidad: {value!r}')
    return entities


def _count_states(conn):
    return {row['state']: row['n'] for row in
            conn.execute('SELECT state,count(*) n FROM esc_packs GROUP BY state')}


def _unit_rows(conn, unit_ids):
    """Las filas del censo que `baseline_route`/`route_state` esperan (con su tier)."""
    out = {}
    for uid in unit_ids:
        row = conn.execute('SELECT u.unit_id,u.kind,u.field,u.value,u.state,u.resolution,t.tier,o.type '
                           'FROM units u JOIN tiers t USING(unit_id) JOIN objects o USING(object_id) '
                           'WHERE u.unit_id=?', (uid,)).fetchone()
        if row is not None:
            out[uid] = dict(row)
    return out


def pack_side(conn, unit_ids):
    """`opus` mientras quede una unidad por auditar; `opus-qc` cuando el pack es solo control de
    calidad sobre unidades ya verificadas. Se deriva del ledger y no del cursor: los dos ámbitos
    pueden convivir en la cola sin que un lado pise las lecturas de la 061 (AC10)."""
    marks = ','.join('?' * len(unit_ids)) or "''"
    row = conn.execute(f"SELECT count(*) n FROM units WHERE state='pending' AND unit_id IN ({marks})",
                       tuple(unit_ids)).fetchone()
    return 'opus' if row['n'] else 'opus-qc'


def _attempt_dir(root, name=None):
    path = Path(root) / ESC_DIR / 'attempts' / (name or uuid.uuid4().hex)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _put_reading(conn, pack_id, side, result, receipt):
    """Una lectura viva por pack y lado: el reintento sustituye a la anterior."""
    conn.execute('DELETE FROM esc_readings WHERE pack_id=? AND side=?', (pack_id, side))
    conn.execute('INSERT INTO esc_readings(attempt_id,pack_id,side,result,receipt,at) VALUES (?,?,?,?,?,?)',
                 (receipt.get('attempt_id') or uuid.uuid4().hex, pack_id, side,
                  None if result is None else canonical(result), canonical(receipt), now()))


def _reading(conn, pack_id, sides):
    marks = ','.join('?' * len(sides))
    row = conn.execute(f'SELECT * FROM esc_readings WHERE pack_id=? AND side IN ({marks})',
                       (pack_id, *sides)).fetchone()
    return dict(row) if row else None


def _write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')
    os.replace(temporary, path)
    return path


_MARCADOR = re.compile(r'<!-- ontologia:([a-z_-]+) -->')


def render_brief(text):
    """Fill the BRIEF's vocabulary markers from the ontology (spec 069): `<!-- ontologia:SECCION -->`
    becomes that section's term cards, `<!-- ontologia:orden-capas -->` the trust order of the layers
    an overlay may write. The rendered text is what `brief_sha256` stamps."""
    ont = ontologia()

    def sub(m):
        key = m.group(1)
        if key == 'orden-capas':
            return ' > '.join(f'`{k}`' for k in sorted(RANGO_CAPA, key=RANGO_CAPA.get))
        if key in ont.raw and isinstance(ont.raw[key], dict):
            return ont.fichas_prompt(key)
        raise ValueError(f'BRIEF: marcador de ontología desconocido {m.group()!r}')
    return _MARCADOR.sub(sub, text)


def brief_text(repo):
    return render_brief((Path(repo) / SPEC_DIR / BRIEF).read_text(encoding='utf-8'))


# ── censo de la cola ──────────────────────────────────────────────────────────

def provisional_id(entry):
    """Id de la fila de cola, hasta que `pack` la sustituya por los `E-`/`D-` reales.
    No lleva `overlay_ref` (aún no se ha elegido) y por eso NUNCA es un `pack_id` de disco."""
    return PROVISIONAL + digest([entry['entity'], sorted(entry['unit_ids'])])[:12]


def census(root, repo, scope='pending', entities=None):
    """La cola de entidades entra en `esc_packs` como filas `queued`, una por entrada.

    `entities` acota el censo a esas entidades principales (los packs editoriales van con la
    suya); lo que no entra en la cola no existe para el resto del bucle.
    """
    if scope not in SCOPES:
        raise ValueError(f'ámbito desconocido {scope!r} (uno de {SCOPES})')
    state = _state_or_new(root, QC_SCOPE if scope == 'verified-T0' else 'pending')
    state['cursor']['scope'] = QC_SCOPE if scope == 'verified-T0' else 'pending'
    save_state(root, state)
    conn = connect(root)
    try:
        ensure_esc_schema(conn)
        # Unidades que ya viven en un pack real (o en una fila de cola que alguien clasificó):
        # volver a encolarlas duplicaría el trabajo y re-abriría lo cerrado.
        taken = set()
        for row in conn.execute("SELECT unit_ids FROM esc_packs WHERE state<>'queued'"):
            taken.update(json.loads(row['unit_ids'] or '[]'))
        counts = Counter()
        with conn:
            for entry in entity_queue(conn, scope):
                if entities is not None and entry['entity'] not in entities:
                    counts['fuera_del_filtro'] += 1
                    continue
                units = [uid for uid in entry['unit_ids'] if uid not in taken]
                if not units:
                    counts['ya_empaquetadas'] += 1
                    continue
                entry = {**entry, 'unit_ids': units}
                pack_id = provisional_id(entry)
                conn.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,pack_path,'
                             'pack_sha256,overlay_ref,riesgo,clase,route,state,batch,created_at) '
                             "VALUES (?,?,?,1,1,?,NULL,NULL,NULL,?,NULL,NULL,'queued',NULL,?) "
                             'ON CONFLICT(pack_id) DO UPDATE SET unit_ids=excluded.unit_ids,'
                             "riesgo=excluded.riesgo WHERE esc_packs.state='queued'",
                             (pack_id, entry['entity'], entry['kind'], canonical(sorted(units)),
                              _norm_risk(entry['riesgo']), now()))
                counts['queued'] += 1
                counts['unidades'] += len(units)
                counts[f"riesgo_{_norm_risk(entry['riesgo'])}"] += 1
        out = {'scope': scope, 'entidades_filtradas': sorted(entities) if entities else None,
               **dict(counts), 'packs': _count_states(conn)}
    finally:
        conn.close()
    return out


# ── clasificación (reglas siempre; Jev solo si pasó su puerta) ─────────────────

def rule_class(facts, flags):
    """Clase por regla, con la prioridad del gold: seudónimo > A5 > figures > estado/arista."""
    if flags['seudonimo']:
        return 'seudonimo'
    if flags['familiar_sin_acusacion']:
        return 'familiar_no_investigado'
    if flags['autoridad_judicial']:
        return 'autoridad_judicial_fiscal'
    if flags['figura_publica']:
        return 'figura_publica'
    if flags['investigado_encausado']:
        return 'investigado_encausado'
    if (facts.get('type') or '') != 'Person':
        return 'no_aplica'
    # Sin nada que la respalde, una persona es un particular: la clase que más escala.
    return 'particular_mencionado'


def rule_risk(flags):
    """Crítico por regla: familiar sin acusación y seudónimo (el menor no tiene flag)."""
    return 'critico' if (flags['seudonimo'] or flags['familiar_sin_acusacion']) else ''


def classification_for(reading, unit_id, pack_risk):
    """Los juicios que el router necesita para UNA unidad: clase de la entidad, riesgo peor
    de los tres (regla, entidad, unidad) y los nouls de la unidad."""
    reading = reading or {}
    entity = (reading.get('entidad') or {}).get('classification') or {}
    unit = ((reading.get('unidades') or {}).get(unit_id) or {}).get('classification') or {}
    out = {key: value for key, value in unit.items() if key not in ('clase', 'riesgo')}
    out.update({'clase': entity.get('clase') or '', 'source': reading.get('source') or 'reglas',
                'menor_o_victima': bool(entity.get('menor_o_victima') or unit.get('menor_o_victima')),
                'riesgo': worst(pack_risk, entity.get('riesgo'), unit.get('riesgo_unidad'))})
    return out


def _classify_pack(conn, root, repo, row, state, client):
    entity, unit_ids = row['entity'], json.loads(row['unit_ids'] or '[]')
    facts = entity_facts(conn, repo, entity)
    flags = rule_flags(facts)
    rule = {'clase': rule_class(facts, flags),
            'riesgo': worst(row['riesgo'], rule_risk(flags)), 'source': 'reglas'}
    attempt = _attempt_dir(root)
    if not state['jevClass']:
        result = {'source': 'reglas', 'entidad': {'answers': {}, 'classification': rule}, 'unidades': {}}
        receipt = {'attempt_id': attempt.name, 'side': 'jev-class', 'backend': 'reglas',
                   'flags': flags, 'items': 0, 'at': now()}
        return result, receipt, rule
    ent_state = qa_esc_jev.entity_state(conn, repo, entity)
    questions = qa_esc_jev.load_questions()
    items = qa_esc_jev.items_for('entidad', [{'id': entity, 'state': ent_state, 'meta': {'entity': entity}}])
    results, receipt = qa_esc_jev.classify(client, items, questions['entidad'], attempt / 'entidad')
    answers = (results[0] if results else {}).get('answers') or {}
    judgment = qa_esc_jev.classification_from(answers)
    judgment.update({'clase': judgment.get('clase') or rule['clase'], 'source': 'jev',
                     'riesgo': worst(rule['riesgo'], judgment.get('riesgo'))})
    unit_rows = _unit_rows(conn, unit_ids)
    unit_items = qa_esc_jev.items_for('unidad', [
        {'id': uid, 'state': qa_esc_jev.unit_state(unit_rows[uid], ent_state, judgment, conn),
         'meta': {'entity': entity, 'kind': unit_rows[uid]['kind']}}
        for uid in unit_ids if uid in unit_rows])
    unit_results, unit_receipt = qa_esc_jev.classify(client, unit_items, questions['unidad'],
                                                     attempt / 'unidad')
    unidades = {r['id']: {'answers': r['answers'],
                          'classification': qa_esc_jev.classification_from(r['answers'])}
                for r in unit_results}
    result = {'source': 'jev', 'entidad': {'answers': answers, 'classification': judgment},
              'unidades': unidades}
    receipt = {'attempt_id': attempt.name, 'side': 'jev-class', 'backend': 'jev', 'flags': flags,
               'entidad': receipt, 'unidad': unit_receipt}
    return result, receipt, judgment


def classify(root, repo, n=None, client=None):
    """Clase, riesgo y tipo de aserción de cada pack en cola. Sin Jev es solo reglas."""
    state = load_state(root)
    conn = connect(root)
    try:
        ensure_esc_schema(conn)
        rows = _packs(conn, 'queued', n or state['perFire']['classify'])
        if rows and state['jevClass'] and client is None:
            client = qa_esc_jev.make_client()
        counts = Counter()
        for row in rows:
            try:
                result, receipt, judgment = _classify_pack(conn, root, repo, row, state, client)
            except qa_esc_jev.ConfigError as exc:
                conn.commit()
                _fail(root, row['pack_id'], f'config: {exc}')
                counts['config'] += 1
                break
            with conn:
                _put_reading(conn, row['pack_id'], 'jev-class', result, receipt)
                conn.execute("UPDATE esc_packs SET clase=?,riesgo=?,state='classified' WHERE pack_id=?",
                             (judgment['clase'], judgment['riesgo'], row['pack_id']))
                _event(conn, None, 'esc-classified',
                       {'pack_id': row['pack_id'], 'entity': row['entity'], 'clase': judgment['clase'],
                        'riesgo': judgment['riesgo'], 'source': judgment['source']})
            counts['classified'] += 1
            counts[f"riesgo_{judgment['riesgo']}"] += 1
        out = {'classified': counts.pop('classified', 0), **dict(counts),
               'jev': bool(state['jevClass']), 'packs': _count_states(conn)}
    finally:
        conn.close()
    if out.get('classified'):
        _ok(root)
    return out


def calibrate(root, repo, run_decide=False):
    """Calibra a Jev contra el gold humano y aplica la puerta de adopción (AC5)."""
    out_dir = Path(root) / ESC_DIR / 'jev'
    summary = qa_esc_jev.calibrate(root, repo, out_dir, run_decide=run_decide)
    gate = qa_esc_jev.adoption_gate(summary)
    state = load_state(root)
    state['jevClass'] = bool(gate['jev_class'])
    state['jevRouting'] = bool(gate['jev_route'])
    save_state(root, state)
    out = {'jevClass': state['jevClass'], 'jevRouting': state['jevRouting'],
           'razones': gate['razones'], 'informe': str(out_dir / 'calibration.md'),
           'gold': summary.get('gold'), 'run_decide': bool(run_decide)}
    return out


# ── packs ─────────────────────────────────────────────────────────────────────

def pack(root, repo, n=None, overlay_ref=WORKTREE, fetch=True, entities=None, sources=None):
    """Congela los packs de las entidades clasificadas y jubila su fila provisional `Q-`."""
    state = load_state(root)
    limits = SOURCES[sources or state.get('sources', SOURCES_DEFAULT)]
    conn = connect(root)
    batch = f'esc-{now()[:19].replace(":", "")}-{uuid.uuid4().hex[:6]}'
    try:
        ensure_esc_schema(conn)
        rows = _packs(conn, 'classified', n or state['perFire']['pack'], entities)
        counts, created = Counter(), []
        for row in rows:
            reading = _reading(conn, row['pack_id'], ('jev-class',))
            classification = {'clase': row['clase'], 'riesgo': row['riesgo'],
                              'source': json.loads(reading['result'])['source'] if reading
                              and reading['result'] else 'reglas'}
            manifests = build_packs(conn, repo, row['entity'], json.loads(row['unit_ids']),
                                    kind=row['kind'], overlay_ref=overlay_ref, limits=limits,
                                    classification=classification, fetch=fetch)
            conn.commit()  # `write_pack` abre su propia conexión: no se le deja un lock encima
            ids = []
            for manifest in manifests:
                markdown = render_pack(manifest, tools=True, ev_path=str(EV_PATH))
                write_pack(root, manifest, markdown)
                ids.append(manifest['pack_id'])
            with conn:
                for pack_id in ids:
                    conn.execute('UPDATE esc_packs SET riesgo=?,clase=?,batch=?,overlay_ref=? '
                                 'WHERE pack_id=?',
                                 (row['riesgo'], row['clase'], batch, overlay_ref, pack_id))
                    if reading:
                        # La clasificación es de la entidad: cada sub-pack se queda una copia
                        # (la tabla es única por pack y lado) con su propio `attempt_id`.
                        receipt = {**json.loads(reading['receipt']), 'attempt_id': uuid.uuid4().hex}
                        _put_reading(conn, pack_id, 'jev-class',
                                     json.loads(reading['result']) if reading['result'] else None, receipt)
                    _event(conn, None, 'esc-packed', {'pack_id': pack_id, 'entity': row['entity'],
                                                      'from': row['pack_id'], 'batch': batch})
                if row['pack_id'].startswith(PROVISIONAL):
                    conn.execute('DELETE FROM esc_readings WHERE pack_id=?', (row['pack_id'],))
                    conn.execute('DELETE FROM esc_packs WHERE pack_id=?', (row['pack_id'],))
            created += ids
            counts['entidades'] += 1
            counts['packs'] += len(ids)
        out = {'batch': batch, 'entidades': counts['entidades'], 'packs_nuevos': counts['packs'],
               'ids': created[:12], 'packs': _count_states(conn)}
    finally:
        conn.close()
    if created:
        _ok(root)
    return out


# ── lectura de Opus ───────────────────────────────────────────────────────────

def _strand(conn, root, row, side, why, date=None):
    """Lado sin lectura válida: el pack va al árbitro, que lee desde cero lo que ese lado no leyó
    (063-B). El motivo viaja como `lado-sin-lectura-valida:<side>:<why>`."""
    reason = f'lado-sin-lectura-valida:{side}:{why}'
    unit_ids = json.loads(row['unit_ids'] or '[]')
    # Lo que ya espera al dueño no pasa por el árbitro.
    waiting = _units_in(conn, unit_ids, 'needs-human')
    unit_ids = [uid for uid in unit_ids if uid not in waiting]
    moved = to_arbitration(conn, root, row['pack_id'], unit_ids, {uid: reason for uid in unit_ids}, 'arbitraje')
    return {'resolution': ARB_PENDING, 'reason': reason, 'units': moved}


def arb_path(root, pack_id):
    """Las unidades que esperan al árbitro y sus motivos (`esc/arb/<pack_id>.json`)."""
    return Path(root) / ESC_DIR / 'arb' / f'{pack_id}.json'


def to_arbitration(conn, root, pack_id, unit_ids, reasons, route):
    """El pack pasa a `arb-pending`: sus unidades sin cerrar vuelven a `pending` con resolución
    `arbitraje-pendiente` (AC10: lo `verified` no se toca) y las unidades y los motivos del
    desacuerdo quedan en `esc/arb/<pack_id>.json`, que es lo que el árbitro lee."""
    moved, waiting = 0, []
    for uid in unit_ids:
        row = conn.execute('SELECT state, resolution FROM units WHERE unit_id=?', (uid,)).fetchone()
        if row is None or row['state'] == 'verified':
            continue
        waiting.append(uid)
        if (row['state'], row['resolution']) == ('pending', ARB_PENDING):
            continue
        conn.execute('UPDATE units SET state=?,resolution=? WHERE unit_id=?', ('pending', ARB_PENDING, uid))
        _event(conn, uid, 'esc-arbitraje', {'pack_id': pack_id, 'resolution': ARB_PENDING,
                                             'reason': reasons.get(uid) or '', 'from': row['resolution']})
        moved += 1
    path = arb_path(root, pack_id)
    prior = json.loads(path.read_text()) if path.exists() else {'units': [], 'reasons': {}}
    units = list(dict.fromkeys([*prior['units'], *waiting]))
    _write_json(path, {'pack_id': pack_id, 'units': units,
                       'reasons': {**prior['reasons'], **{uid: reasons.get(uid) or '' for uid in waiting}},
                       'at': now()})
    conn.execute("UPDATE esc_packs SET state='arb-pending',route=? WHERE pack_id=?", (route, pack_id))
    return moved


def _mark_units(conn, unit_ids, state, resolution, event, payload, verdicts=None):
    """Cambia SOLO las unidades `pending` (AC10: nada verificado se re-gatea)."""
    moved = 0
    for uid in unit_ids:
        row = conn.execute('SELECT state, resolution FROM units WHERE unit_id=?', (uid,)).fetchone()
        if row is None or row['state'] != 'pending':
            continue
        full = resolution + ('-corregido' if (verdicts or {}).get(uid) == 'corregido' else '')
        conn.execute('UPDATE units SET state=?,resolution=? WHERE unit_id=?', (state, full, uid))
        # `from` = la resolución que tenía mientras estaba `pending` (la que `reroute-owner` restaura).
        _event(conn, uid, event, {**payload, 'resolution': full, 'from': row['resolution']})
        moved += 1
    return moved


def _units_in(conn, unit_ids, state):
    """Las unidades de `unit_ids` que están en `state` en el censo."""
    ids = list(unit_ids)
    if not ids:
        return set()
    marks = ','.join('?' * len(ids))
    return {row['unit_id'] for row in conn.execute(
        f'SELECT unit_id FROM units WHERE state=? AND unit_id IN ({marks})', (state, *ids))}


def _scoped(manifest, unit_ids):
    """El manifest con `units` acotadas (el mismo objeto si no sobra ninguna)."""
    keep = set(unit_ids)
    if all(u['unit_id'] in keep for u in manifest['units']):
        return manifest
    return {**manifest, 'units': [u for u in manifest['units'] if u['unit_id'] in keep]}


def settle(conn, pack_id, route=None):
    """La máquina ha terminado con el pack: `closed` si no le queda nada abierto, `tanda` si quedan
    unidades del dueño. Con alguna unidad aún `pending` no cambia nada (devuelve None)."""
    row = conn.execute('SELECT unit_ids FROM esc_packs WHERE pack_id=?', (pack_id,)).fetchone()
    ids = json.loads(row['unit_ids'] or '[]') if row else []
    if _units_in(conn, ids, 'pending'):
        return None
    new = 'tanda' if _units_in(conn, ids, 'needs-human') else 'closed'
    conn.execute('UPDATE esc_packs SET state=?,route=COALESCE(?,route) WHERE pack_id=?', (new, route, pack_id))
    return new


def owner_pack_ids(conn):
    """Los packs con algo para el dueño: los `tanda` y los que la máquina aún trabaja (dual o árbitro)
    con unidades `needs-human` que el router le mandó por unidad."""
    ids = [row['pack_id'] for row in _packs(conn, 'tanda')]
    for pack_state in MACHINE_STATES:
        ids += [row['pack_id'] for row in _packs(conn, pack_state)
                if _units_in(conn, json.loads(row['unit_ids'] or '[]'), 'needs-human')]
    return ids


def opus(root, repo, n=None, effort=None, entities=None):
    """Lee con Opus 5 los packs congelados: una lectura por pack, recibo antes que ledger."""
    state = load_state(root)
    conn = connect(root)
    system = brief_text(repo)
    brief_sha = digest(system)
    try:
        ensure_esc_schema(conn)
        rows = _packs(conn, 'packed', n or state['perFire']['opus'], entities)
        counts = Counter()
        sides = Counter()
        for row in rows:
            manifest, _ = load_pack(root, row['pack_id'])
            side = pack_side(conn, json.loads(row['unit_ids'] or '[]'))
            sides[side] += 1
            riesgo = _norm_risk(row['riesgo'])
            chosen = floor_effort(effort or state['efforts'].get(riesgo, 'high'))
            attempt = _attempt_dir(root)
            result, receipt = qa_esc_opus.run_claude(
                attempt, side, MODELS['opus'], chosen, system, schema_for(manifest),
                render_pack(manifest, tools=True, ev_path=str(EV_PATH)),
                tools_prefix=f'python3 {EV_PATH}', budget_usd=None,
                extra_env={'QA_ESC_ROOT': str(Path(root).resolve())},
                stamp={'pack_sha256': row['pack_sha256'], 'brief_sha256': brief_sha,
                       'pack_id': row['pack_id']})
            why = receipt.get('error')
            if result is not None and not why:
                # El esperado es lo PEDIDO (estado del runner), no una tabla aparte: si no, se anulan lecturas buenas.
                if not effort_ok(receipt.get('effort'), chosen):
                    result, why = None, 'unexpected-effort'
                else:
                    errors = validate_decision(result, manifest)
                    if errors:
                        receipt['validation_errors'] = errors[:8]
                        result, why = None, 'invalid-decision'
            receipt['error'] = why
            with conn:
                _put_reading(conn, row['pack_id'], side, result, receipt)
            _count_claude(root, receipt.get('usage'))
            if result is not None:
                with conn:
                    conn.execute("UPDATE esc_packs SET state='opus-read' WHERE pack_id=?", (row['pack_id'],))
                    _event(conn, None, 'reading-accepted',
                           {'pack_id': row['pack_id'], 'side': side, 'effort': chosen,
                            'searches': len(receipt.get('searches') or [])})
                counts['leidos'] += 1
                _ok(root)
                continue
            with conn:
                _event(conn, None, 'reading-rejected',
                       {'pack_id': row['pack_id'], 'side': side, 'why': why})
                attempts = _rejected(conn, row['pack_id'], side)
                if attempts >= MAX_READ_ATTEMPTS and why not in ('quota',) \
                        and not str(why).startswith('config'):
                    _strand(conn, root, row, side, why)
                    counts['tanda'] += 1
            counts[f'nula_{why}'] += 1
            if why == 'quota':
                _fail(root, row['pack_id'], 'quota', receipt)
                break
            if str(why).startswith('config'):
                _fail(root, row['pack_id'], why)
                break
            if why in READER_ERRORS:
                # Calidad del lector, no de la infraestructura: va a reintento/tanda, no al disyuntor.
                continue
            _fail(root, row['pack_id'], 'other')
        out = {'sides': dict(sides), **dict(counts), 'packs': _count_states(conn),
               'window': load_state(root)['window']}
    finally:
        conn.close()
    return out


# ── enrutado (reglas fijas; Jev solo endurece) ────────────────────────────────

def _unit_of(decision, unit_id):
    return next((u for u in (decision.get('units') or []) if u.get('unit_id') == unit_id), {})


def _meta(manifest, resolution, route, sides, unit_ids=None, tanda_units=None):
    """`_meta` de la decisión final; `unit_ids` son las unidades que ESTA decisión cierra (en un
    cierre parcial, solo las acordadas: el resto sale en `tanda_units` y lo cierra el dueño)."""
    meta = {'pack_id': manifest['pack_id'], 'entity': manifest['entity']['id'], 'kind': manifest['kind'],
            'index': manifest['index'], 'total': manifest['total'], 'resolution': resolution,
            'route': route, 'decided_at': now(), 'sides': list(sides),
            'unit_ids': list(unit_ids) if unit_ids is not None else [u['unit_id'] for u in manifest['units']],
            'overlay_ref': manifest['overlay_ref']}
    if tanda_units:
        meta['tanda_units'] = list(tanda_units)
    return meta


def _decision_file(root, manifest, decision, resolution, route, sides, unit_ids=None, tanda_units=None):
    payload = {key: value for key, value in decision.items() if key != '_meta'}
    payload['_meta'] = _meta(manifest, resolution, route, sides, unit_ids, tanda_units)
    return _write_json(Path(root) / ESC_DIR / 'decisions' / f"{manifest['pack_id']}.json", payload)


def _routes_for(root, conn, manifest, decision, changes, class_reading, pack_risk, jev_routing, client,
                stored=None):
    """(rutas por unidad, lectura de Jev) — la regla decide y Jev solo puede endurecer. `stored`
    (`{unit_id: ruta}` de una lectura `jev-route` ya pagada) sustituye a la llamada a Jev."""
    unit_rows = _unit_rows(conn, [u['unit_id'] for u in manifest['units']])
    bases, states = {}, []
    for unit in manifest['units']:
        uid = unit['unit_id']
        row = unit_rows.get(uid)
        if row is None:
            continue
        judgments = classification_for(class_reading, uid, pack_risk)
        base = qa_esc_jev.baseline_route(row, manifest['entity'], decision, judgments)
        if changes.get('unanchored'):
            # Acción de entidad sin unidad de ese tipo en el pack (sub-pack de aristas, prosa ya
            # verificada): nunca se cierra con Opus solo (piloto 2026-09-21).
            base = qa_esc_jev.harden(base, 'escalar')
        bases[uid] = (base, judgments)
        if jev_routing:
            states.append({'id': uid, 'meta': {'pack_id': manifest['pack_id']},
                           'state': qa_esc_jev.route_state(row, manifest['entity'], _unit_of(decision, uid),
                                                           changes, judgments, bases[uid][0])})
    jev_reading = None
    answers = dict(stored or {})
    if stored is None and jev_routing and states:
        attempt = _attempt_dir(root)
        items = qa_esc_jev.items_for('ruta', states)
        results, receipt = qa_esc_jev.classify(client or qa_esc_jev.make_client(), items,
                                               qa_esc_jev.load_questions()['ruta'], attempt)
        answers = {r['id']: (r['answers'].get('ruta') or {}).get('choice') for r in results}
        jev_reading = ({'rutas': answers,
                        'respuestas': {r['id']: r['answers'] for r in results}}, receipt)
    routes = {uid: {'base': base, 'jev': answers.get(uid),
                    'final': qa_esc_jev.harden(base, answers.get(uid)) if answers.get(uid) else base}
              for uid, (base, _judgments) in bases.items()}
    return routes, jev_reading


def split_routes(manifest, routes):
    """(del dueño, el resto, ruta del resto) — las unidades `dueno` van al dueño con sus compañeras de
    bloque de prosa (`PROSE_BLOCKS`); el resto sigue la peor ruta entre ellas. Sin resto, `dueno`."""
    kinds = {u['unit_id']: u.get('kind') for u in manifest['units']}
    order = [u['unit_id'] for u in manifest['units'] if u['unit_id'] in routes]
    owner = {uid for uid in order if routes[uid]['final'] == 'dueno'}
    blocks = {PROSE_BLOCKS.get(kinds[uid]) for uid in owner} - {None}
    owner |= {uid for uid in order if PROSE_BLOCKS.get(kinds[uid]) in blocks}
    rest = [uid for uid in order if uid not in owner]
    final = max((routes[uid]['final'] for uid in rest), key=ROUTE_ORDER.get, default='dueno')
    return [uid for uid in order if uid in owner], rest, final


def _dispatch(conn, root, pack_id, manifest, decision, side, routes, date, counts):
    """Aplica las rutas por unidad de UN pack leído (dentro de la transacción del llamador) y devuelve
    la ruta con la que queda el pack: las unidades del dueño pasan a `needs-human` y el resto cierra
    (`opus-cerrada`) o escala al dual. El control de calidad (`opus-qc`) va entero por la peor ruta:
    sus unidades siguen verificadas y el hallazgo es del pack."""
    from . import qa_esc_escalate as X  # perezoso, como en el escalado
    unit_ids = [u['unit_id'] for u in manifest['units']]
    qc = side == 'opus-qc'
    verdicts = {u['unit_id']: u.get('verdict') for u in (decision.get('units') or [])}
    if qc:
        owner, rest = [], unit_ids
        final = max((r['final'] for r in routes.values()), key=ROUTE_ORDER.get, default='dueno')
    else:
        owner, rest, final = split_routes(manifest, routes)
        pulled = [uid for uid in owner if routes[uid]['final'] != 'dueno']
        counts['unidades_tanda'] += _mark_units(
            conn, [uid for uid in owner if uid not in pulled], 'needs-human', f'tanda-{date}-pendiente',
            'esc-tanda', {'pack_id': pack_id})
        counts['unidades_tanda'] += _mark_units(
            conn, pulled, 'needs-human', f'tanda-{date}-pendiente', 'esc-tanda',
            {'pack_id': pack_id, 'motivo': 'bloque de prosa con una unidad del dueño'})
        if owner and rest:
            counts['packs_divididos'] += 1
    closing = decision
    if final == 'cerrar' and owner:
        # Cierre parcial: la decisión se acota al resto (las acciones de entidad ancladas en unidades
        # del dueño quedan fuera). Si recortada no valida, el resto escala en vez de cerrar.
        closing = X.restrict_decision(decision, manifest, rest, partial=True)[0]
        errors = validate_decision(closing, X.restrict_pack(manifest, rest))
        if errors:
            _event(conn, None, 'esc-cierre-parcial-invalido', {'pack_id': pack_id, 'errors': errors[:4]})
            final = 'escalar'
    if final == 'cerrar':
        if qc:
            # Control de calidad: la unidad sigue verificada, solo queda el rastro.
            for uid in unit_ids:
                _event(conn, uid, 'esc-qc-ok', {'pack_id': pack_id})
            conn.execute("UPDATE esc_packs SET state='closed',route=? WHERE pack_id=?", (final, pack_id))
        else:
            _decision_file(root, manifest, closing, 'opus-cerrada', final, [side],
                           unit_ids=rest if owner else None, tanda_units=owner)
            counts['unidades_cerradas'] += _mark_units(conn, rest, 'verified', 'opus-cerrada', 'esc-cerrada',
                                                       {'pack_id': pack_id}, verdicts)
            settle(conn, pack_id, final)
    elif final == 'escalar':
        conn.execute("UPDATE esc_packs SET state='dual-pending',route=? WHERE pack_id=?", (final, pack_id))
    else:
        conn.execute("UPDATE esc_packs SET state='tanda',route=? WHERE pack_id=?", (final, pack_id))
    if qc and final != 'cerrar':
        _event(conn, None, 'esc-qc-hallazgo', {'pack_id': pack_id, 'route': final})
    return final


def route(root, repo, client=None):
    """Cierra, escala o manda al dueño cada pack leído, unidad por unidad (`_dispatch`)."""
    state = load_state(root)
    calibration = Path(root) / ESC_DIR / 'jev' / 'calibration.md'
    if state['jevRouting'] and not calibration.exists():
        _fail(root, 'route', f'config: jevRouting sin {calibration}')
        return {'action': 'HALT', 'note': f'falta {calibration}'}
    conn = connect(root)
    date = _today()
    try:
        ensure_esc_schema(conn)
        counts = Counter()
        for row in _packs(conn, 'opus-read'):
            reading = _reading(conn, row['pack_id'], READER_SIDES)
            if reading is None or not reading['result']:
                with conn:
                    conn.execute("UPDATE esc_packs SET state='packed' WHERE pack_id=?", (row['pack_id'],))
                counts['sin_lectura'] += 1
                continue
            manifest, _ = load_pack(root, row['pack_id'])
            decision = json.loads(reading['result'])
            receipt = json.loads(reading['receipt'])
            # `baseline_route` y `route_state` leen `searches` como un NÚMERO (int(...)).
            decision['searches'] = len(receipt.get('searches') or [])
            changes = decision_changes(decision, manifest)
            changes['searches'] = decision['searches']
            class_reading = _reading(conn, row['pack_id'], ('jev-class',))
            class_result = (json.loads(class_reading['result'])
                            if class_reading and class_reading['result'] else {})
            routes, jev_reading = _routes_for(root, conn, manifest, decision, changes, class_result,
                                              row['riesgo'], state['jevRouting'], client)
            with conn:
                if jev_reading:
                    _put_reading(conn, row['pack_id'], 'jev-route', jev_reading[0], jev_reading[1])
                for uid, trace in routes.items():
                    _event(conn, uid, 'esc-routed', {'pack_id': row['pack_id'], **trace})
                final = _dispatch(conn, root, row['pack_id'], manifest, decision, reading['side'], routes,
                                  date, counts)
            counts[final] += 1
        out = {**dict(counts), 'jevRouting': bool(state['jevRouting']), 'packs': _count_states(conn)}
    finally:
        conn.close()
    _ok(root)
    return out


def _pending_resolution(conn, unit_id):
    """La resolución que la unidad tenía en `pending` antes de su primera `esc-tanda` (el `from` que
    anota `_mark_units`). Los eventos anteriores a ese campo no lo traen: el router solo enrutaba lo
    que el censo tenía `pending`, que en el ledger de la 063 lleva resolución nula."""
    row = conn.execute("SELECT payload FROM events WHERE unit_id=? AND event='esc-tanda' ORDER BY seq LIMIT 1",
                       (unit_id,)).fetchone()
    return json.loads(row['payload'] or '{}').get('from') if row else None


def reroute_owner(root, repo, dry_run=False):
    """Una vez: re-enruta por unidad los packs que `route()` mandó ENTEROS al dueño (`tanda`/`dueno`).

    Recalcula las rutas con las mismas entradas que `route()` (lectura de Opus, manifest, `jev-class`
    y, si la hubo, la `jev-route` ya pagada: aquí no se llama a ningún modelo) y aplica `_dispatch`:
    lo del dueño sigue `needs-human`; el resto vuelve a `pending` con su resolución anterior y el pack
    escala al dual o cierra. Se salta los packs que releían unidades ya verificadas y los que no son
    un envío entero al dueño todavía sin responder. Con `dry_run`, solo el plan."""
    conn = connect(root)
    date = _today()
    counts, plan = Counter(), []
    try:
        ensure_esc_schema(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM esc_packs WHERE state='tanda' AND route='dueno' ORDER BY pack_id")]
        for row in rows:
            pack_id = row['pack_id']
            unit_ids = json.loads(row['unit_ids'] or '[]')
            reading = _reading(conn, pack_id, READER_SIDES)
            if reading is None or not reading['result']:
                counts['sin_lectura'] += 1
                continue
            units = _unit_rows(conn, unit_ids)
            if reading['side'] == 'opus-qc' or any(u['state'] == 'verified' for u in units.values()):
                counts['relectura_de_verificadas'] += 1
                continue
            if any(u['state'] != 'needs-human' or not re.fullmatch(r'tanda-.+-pendiente', u['resolution'] or '')
                   for u in units.values()):
                counts['no_es_tanda_entera'] += 1
                continue
            manifest, _ = load_pack(root, pack_id)
            decision = json.loads(reading['result'])
            receipt = json.loads(reading['receipt'])
            decision['searches'] = len(receipt.get('searches') or [])
            changes = decision_changes(decision, manifest)
            changes['searches'] = decision['searches']
            class_reading = _reading(conn, pack_id, ('jev-class',))
            class_result = json.loads(class_reading['result']) if class_reading and class_reading['result'] else {}
            jev_route = _reading(conn, pack_id, ('jev-route',))
            stored = (json.loads(jev_route['result']).get('rutas') or {}) if jev_route and jev_route['result'] else {}
            routes, _ = _routes_for(root, conn, manifest, decision, changes, class_result, row['riesgo'],
                                    False, None, stored=stored)
            owner, rest, final = split_routes(manifest, routes)
            if not rest:
                counts['todo_del_dueno'] += 1
                continue
            item = {'pack_id': pack_id, 'entity': row['entity'], 'dueno': len(owner), 'resto': len(rest),
                    'ruta_resto': final}
            if dry_run:
                plan.append(item)
                continue
            with conn:
                for uid in rest:
                    prior = _pending_resolution(conn, uid)
                    conn.execute("UPDATE units SET state='pending',resolution=? WHERE unit_id=?", (prior, uid))
                    _event(conn, uid, 'esc-reenrutada', {'pack_id': pack_id, 'from': units[uid]['resolution'],
                                                         'resolution': prior, **routes[uid]})
                item['ruta_pack'] = _dispatch(conn, root, pack_id, manifest, decision, reading['side'], routes,
                                              date, counts)
                item['estado'] = conn.execute('SELECT state FROM esc_packs WHERE pack_id=?',
                                              (pack_id,)).fetchone()['state']
                _event(conn, None, 'esc-reenrutado', {**item, 'owner': owner, 'rest': rest})
            plan.append(item)
        out = {'dry_run': bool(dry_run), 'reenrutados': len(plan),
               'unidades_al_dual_o_cierre': sum(p['resto'] for p in plan), **dict(counts), 'packs': plan,
               'estados': _count_states(conn)}
    finally:
        conn.close()
    return out


# ── escalado dual (T9) ────────────────────────────────────────────────────────

def escalate(root, repo, n=None, astra_tools=ASTRA_TOOLS_DEFAULT):
    """Astra lee a ciegas el pack escalado; lo que Opus y Astra acuerdan cierra (`dual-cerrada`) y
    el resto va al árbitro (`arb-pending`, 063-B), nunca directamente a la tanda."""
    from . import qa_esc_escalate  # T9: import perezoso, el bucle no depende de él para arrancar
    state = load_state(root)
    conn = connect(root)
    system = brief_text(repo)
    date = _today()
    try:
        ensure_esc_schema(conn)
        counts = Counter()
        for row in _packs(conn, 'dual-pending', n or state['perFire']['escalate']):
            manifest, _ = load_pack(root, row['pack_id'])
            opus_reading = _reading(conn, row['pack_id'], READER_SIDES)
            if opus_reading is None or not opus_reading['result']:
                with conn:
                    conn.execute("UPDATE esc_packs SET state='packed' WHERE pack_id=?", (row['pack_id'],))
                counts['sin_lectura'] += 1
                continue
            opus_decision = json.loads(opus_reading['result'])
            riesgo = _norm_risk(row['riesgo'])
            all_ids = [u['unit_id'] for u in manifest['units']]
            # El dual solo lee lo que la máquina aún debe: las unidades `pending` (las del dueño esperan
            # como `needs-human`, lo cerrado no se relee). El control de calidad relee el pack entero.
            qc = opus_reading['side'] == 'opus-qc'
            scope = manifest if qc else _scoped(manifest, _units_in(conn, all_ids, 'pending'))
            if not scope['units']:
                with conn:
                    settle(conn, row['pack_id'])
                counts['sin_unidades'] += 1
                continue
            owner = sorted(_units_in(conn, all_ids, 'needs-human'))
            # Lecturas válidas ya pagadas sobre este mismo trozo del pack: se reutilizan y solo se relee
            # el lado que falta.
            sha = digest(scope)
            reused = {}
            for reader in DUAL_SIDES:
                prior = _reading(conn, row['pack_id'], (reader,))
                if prior and prior['result']:
                    prior_receipt = json.loads(prior['receipt'])
                    if prior_receipt.get('pack_sha256') == sha and not prior_receipt.get('error'):
                        reused[reader] = (json.loads(prior['result']), prior_receipt)
            missing = [s for s in DUAL_SIDES if s not in reused]
            out_sides = dict(reused)
            if missing:
                out_sides.update(qa_esc_escalate.run_dual(
                    root, row['pack_id'], scope, render_pack(scope, tools=False),
                    render_pack(scope, tools=True, ev_path=str(EV_PATH)), schema_for(scope), system,
                    DUAL_EFFORT, astra_tools, opus_decision, sides=missing))
            decisions, nulls = {'opus': opus_decision}, []
            for reader in DUAL_SIDES:
                result, receipt = out_sides.get(reader) or (None, {'error': 'sin-lectura'})
                receipt = dict(receipt or {})
                why = receipt.get('error')
                if result is not None and not why:
                    errors = validate_decision(result, scope)
                    if errors:
                        receipt['validation_errors'] = errors[:8]
                        result, why = None, 'invalid-decision'
                receipt['error'] = why
                if reader in reused:
                    counts['reutilizadas'] += 1
                else:
                    with conn:
                        _put_reading(conn, row['pack_id'], reader, result, receipt)
                    _count_astra(root, receipt.get('usage'))
                if result is None:
                    nulls.append((reader, why or 'sin-lectura', receipt))
                    with conn:
                        _event(conn, None, 'reading-rejected',
                               {'pack_id': row['pack_id'], 'side': reader, 'why': why})
                else:
                    decisions[reader] = result
            if nulls:
                reader, why, failed = nulls[0]
                attempts = max(_rejected(conn, row['pack_id'], side) for side, _, _ in nulls)
                if why == 'quota':
                    _fail(root, row['pack_id'], 'quota', failed)
                    counts['cuota'] += 1
                    break
                if attempts >= MAX_READ_ATTEMPTS:
                    with conn:
                        _strand(conn, root, row, reader, why, date)
                    counts['arbitraje'] += 1
                if str(why).startswith('config'):
                    _fail(root, row['pack_id'], f'config: {why}')
                elif why not in READER_ERRORS:
                    _fail(root, row['pack_id'], 'other')
                continue
            # El acuerdo es POR UNIDAD (AC7): el pack cierra las acordadas y manda al árbitro el resto.
            agreement = qa_esc_escalate.dual_agreement(scope, decisions, full=manifest)
            mode = agreement.get('mode')
            unit_ids = [u['unit_id'] for u in scope['units']]
            acordadas = set(agreement.get('closed_units') or [])
            closed = [uid for uid in unit_ids if uid in acordadas]
            pending = [uid for uid in unit_ids if uid not in acordadas]
            reasons = {uid: (agreement.get('per_unit') or {}).get(uid, {}).get('reason') or ''
                       for uid in pending}
            with conn:
                if closed:
                    final = agreement['final']
                    verdicts = {u['unit_id']: u.get('verdict') for u in (final.get('units') or [])}
                    _decision_file(root, manifest, final, 'dual-cerrada', 'escalar',
                                   ['opus', *DUAL_SIDES], unit_ids=closed, tanda_units=[*pending, *owner])
                    counts['unidades_cerradas'] += _mark_units(
                        conn, closed, 'verified', 'dual-cerrada', 'esc-cerrada',
                        {'pack_id': row['pack_id'], 'mode': mode, 'closed_units': closed}, verdicts)
                if pending:
                    counts['unidades_arbitraje'] += to_arbitration(
                        conn, root, row['pack_id'], pending, reasons, 'parcial' if closed else 'arbitraje')
                    counts['arbitraje'] += 1
                else:
                    new = settle(conn, row['pack_id'])
                    counts[{'closed': 'cerrados', 'tanda': 'a_tanda'}.get(new, 'sin_asentar')] += 1
            _ok(root)
        out = {**dict(counts), 'astra_tools': astra_tools, 'packs': _count_states(conn),
               'window': load_state(root)['window']}
    finally:
        conn.close()
    return out


# ── árbitro (063-B) ───────────────────────────────────────────────────────────

def arbitrate(root, repo, n=None, dry_run=False, from_tanda=False):
    """Opus 5.5 arbitra lo que el dual no cerró y Astra valida sus citas; ver `qa_esc_arbitrate`."""
    from . import qa_esc_arbitrate  # import perezoso, como el escalado
    return qa_esc_arbitrate.arbitrate(root, repo, n=n, dry_run=dry_run, from_tanda=from_tanda)


# ── latido ────────────────────────────────────────────────────────────────────

def _orphans(root, conn, state):
    """Packs leídos que llevan demasiado sin enrutar: nadie los tiene y nadie los va a mover."""
    stale = []
    for pack_state in ('opus-read', 'dual-read'):
        for row in _packs(conn, pack_state):
            reading = _reading(conn, row['pack_id'], READER_SIDES + DUAL_SIDES)
            at = _timestamp(reading['at']) if reading else None
            # Un `resume` reinicia el reloj: si no, un pack leído al principio de un lote largo
            # vuelve a salir huérfano en cuanto se reencola (063, 30-09: lote de Opus de 2 h 30).
            requeued = conn.execute("SELECT MAX(at) FROM events WHERE event='esc-reencolado' AND payload LIKE ?",
                                    (f'%"{row["pack_id"]}"%',)).fetchone()[0]
            if at is not None and requeued:
                at = max(at, _timestamp(requeued))
            if at is not None and utcnow().timestamp() - at > ORPHAN_SECONDS:
                stale.append(row['pack_id'])
    return stale


def lock_free(root):
    """El `runner.lock` de la familia de auditorías (compartido con la 061)."""
    try:
        with supervisor_lock(root):
            return True
    except RuntimeError:
        return False


def decide(root, conn=None):
    """La única acción que toca ahora. Orden: HALT → RUNNING → huérfanos → WAIT → ESCALATE →
    ARBITRATE → ROUTE → OPUS → PACK → CLASSIFY → DONE."""
    state = load_state(root)
    if state['halted']:
        return {'action': 'HALT', 'note': state['haltReason']}
    for batch in state['batches']:
        if batch['status'] == 'running' and _alive(batch.get('pid')):
            return {'action': 'RUNNING', 'batch': batch['id'], 'note': batch['action']}
    close = conn is None
    conn = conn or connect(root)
    try:
        ensure_esc_schema(conn)
        stale = _orphans(root, conn, state)
        if stale:
            return {'action': 'HALT', 'note': f'{len(stale)} packs sin dueño ({stale[0]}…): '
                                              'qa-esc-status los lista; qa-esc-resume los reencola'}
        window = state['window']
        expired = _roll_window(state)
        paused = window.get('pausedUntil')
        if paused and utcnow().timestamp() < paused:
            return {'action': 'WAIT', 'note': 'pausa por cuota',
                    'pausedUntil': datetime.fromtimestamp(paused, timezone.utc).isoformat()}
        counts = _count_states(conn)
        claude_capped = not expired and window['usedClaude'] >= window['softCapClaude']
        astra_capped = not expired and window['usedAstra'] >= window['softCapAstra']
        until = datetime.fromtimestamp((_timestamp(window['startedAt']) or utcnow().timestamp())
                                       + window.get('resetSeconds', RESET_SECONDS),
                                       timezone.utc).isoformat()
        if claude_capped:
            return {'action': 'WAIT', 'note': 'tope blando de la ventana de Claude', 'pausedUntil': until}
        if counts.get('dual-pending'):
            if astra_capped:
                return {'action': 'WAIT', 'note': 'tope blando de la cuota de Astra', 'pausedUntil': until}
            return {'action': 'ESCALATE', 'note': f"{counts['dual-pending']} packs escalados"}
        if counts.get('arb-pending'):
            # El árbitro (Claude) y la validación (Astra) gastan de las dos ventanas.
            if astra_capped:
                return {'action': 'WAIT', 'note': 'tope blando de la cuota de Astra', 'pausedUntil': until}
            return {'action': 'ARBITRATE', 'note': f"{counts['arb-pending']} packs para el árbitro"}
        if counts.get('opus-read'):
            return {'action': 'ROUTE', 'note': f"{counts['opus-read']} packs leídos"}
        if counts.get('packed'):
            return {'action': 'OPUS', 'note': f"{counts['packed']} packs por leer"}
        if counts.get('classified'):
            return {'action': 'PACK', 'note': f"{counts['classified']} entidades por empaquetar"}
        if counts.get('queued'):
            return {'action': 'CLASSIFY', 'note': f"{counts['queued']} entidades en cola"}
        return {'action': 'DONE', 'note': 'sin packs en vuelo'}
    finally:
        if close:
            conn.close()


def _spawn(root, action, n):
    sub = FIRE_ACTIONS[action]
    cmd = [sys.executable, '-m', 'src.qa_esc', '--root', str(Path(root).resolve()),
           '--repo', str(REPO), sub]
    if sub in PER_FIRE:
        cmd += ['--n', str(n)]
    if sub == 'escalate':
        cmd += ['--astra-tools', load_state(root).get('astraTools') or ASTRA_TOOLS_DEFAULT]
    log = Path(root) / ESC_DIR / 'runs'
    log.mkdir(parents=True, exist_ok=True)
    with (log / f'{sub}.log').open('a') as out:
        proc = subprocess.Popen(cmd, cwd=str(REPO / 'pipeline'), stdout=out, stderr=out,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    return proc.pid


def _unit_counts(conn):
    return {row['state']: row['n'] for row in
            conn.execute('SELECT state,count(*) n FROM units GROUP BY state')}


def step(root, repo=REPO, spawn=None, dry_run=False):
    """UN latido: decide, lanza como mucho un subproceso y devuelve UNA línea JSON."""
    root = Path(root)
    spawn = spawn or _spawn
    state = load_state(root)
    dirty = False
    if _roll_window(state):
        state['window'].update(startedAt=now(), usedClaude=0, cachedClaude=0, usedAstra=0)
        dirty = True
    # Un lote que ya no vive dejó su rastro en `events` y en el ledger: fuera del estado.
    live = [batch for batch in state['batches'] if _alive(batch.get('pid'))]
    if len(live) != len(state['batches']):
        state['batches'] = live
        dirty = True
    if dirty:
        save_state(root, state)
    conn = connect(root)
    try:
        ensure_esc_schema(conn)
        if not lock_free(root):
            decision = {'action': 'RUNNING', 'note': 'runner.lock tomado (061 o una etapa en vuelo)'}
        else:
            decision = decide(root, conn)
        action = decision['action']
        if action == 'HALT' and not state['halted']:
            state = load_state(root)
            state.update(halted=True, haltReason=decision['note'])
            save_state(root, state)
        elif action in FIRE_ACTIONS and not dry_run:
            state = load_state(root)
            # Un estado anterior a la 063-B no trae `perFire.arbitrate`: vale el de por defecto.
            n = state['perFire'].get(FIRE_ACTIONS[action]) or PER_FIRE.get(FIRE_ACTIONS[action])
            pid = spawn(root, action, n)
            state['batches'].append({'id': f'{FIRE_ACTIONS[action]}-{uuid.uuid4().hex[:8]}',
                                     'action': FIRE_ACTIONS[action], 'n': n, 'pid': pid,
                                     'status': 'running', 'startedTs': now()})
            state['firingCount'] += 1
            save_state(root, state)
            decision['pid'] = pid
        elif action in FIRE_ACTIONS:
            decision['note'] = f"{decision.get('note') or ''} (dry-run: no se lanza nada)".strip()
        window = load_state(root)['window']
        out = {'action': action, 'packs': _count_states(conn), 'units': _unit_counts(conn),
               'window': {'usedClaude': window['usedClaude'], 'usedAstra': window['usedAstra'],
                          'pausedUntil': decision.get('pausedUntil') or window.get('pausedUntil')},
               'note': decision.get('note')}
    finally:
        conn.close()
    return out


def halt(root, reason):
    state = load_state(root)
    state.update(halted=True, haltReason=reason)
    save_state(root, state)
    out = {'halted': True, 'reason': reason}
    return out


def resume(root):
    """Reanuda tras una parada: ventana y breaker a cero, lotes muertos fuera y packs sin dueño
    de vuelta a la cola que les toca."""
    state = load_state(root)
    state.update(halted=False, haltReason=None, consecutiveFailures=0, lastError=None)
    state['window'].update(startedAt=now(), usedClaude=0, cachedClaude=0, usedAstra=0, pausedUntil=None)
    dead = [b['id'] for b in state['batches'] if b['status'] == 'running' and not _alive(b.get('pid'))]
    state['batches'] = [b for b in state['batches'] if b['id'] not in dead]
    save_state(root, state)
    conn = connect(root)
    try:
        ensure_esc_schema(conn)
        requeued = []
        with conn:
            for pack_id in _orphans(root, conn, state):
                row = conn.execute('SELECT state FROM esc_packs WHERE pack_id=?', (pack_id,)).fetchone()
                # Un pack leído vuelve a ROUTE; su lectura aceptada se conserva (no se relee).
                conn.execute("UPDATE esc_packs SET batch=NULL WHERE pack_id=?", (pack_id,))
                _event(conn, None, 'esc-reencolado', {'pack_id': pack_id, 'state': row['state']})
                requeued.append(pack_id)
        out = {'resumed_at': load_state(root)['updatedAt'], 'lotes_muertos': dead, 'reencolados': requeued}
    finally:
        conn.close()
    return out


# ── foto del bucle ────────────────────────────────────────────────────────────

def g7(conn):
    """AC6, determinista: 0 cierres indebidos y 0 cierres cuya única lectura sea de Jev (el árbitro
    `opus-arb` cuenta como lectura de Opus: lee desde cero lo que el lector no dejó válido; un pack
    que cerró el dueño (`route` dueno|humano) lo leyó una persona)."""
    owner = {}
    for row in conn.execute('SELECT pack_id,riesgo,unit_ids,state FROM esc_packs'):
        for uid in json.loads(row['unit_ids'] or '[]'):
            owner[uid] = dict(row)
    indebidos = []
    for row in conn.execute("SELECT u.unit_id,u.kind,o.type FROM units u JOIN objects o USING(object_id) "
                            "WHERE u.state='verified' AND u.resolution LIKE 'opus-cerrada%'"):
        pack = owner.get(row['unit_id']) or {}
        if (_norm_risk(pack.get('riesgo')) in ('alto', 'critico') or row['kind'] == 'identity'
                or (row['kind'] == 'edge' and row['type'] in ACCUSATORY)):
            indebidos.append(row['unit_id'])
    solo_jev = [row['pack_id'] for row in conn.execute(
        "SELECT pack_id FROM esc_packs WHERE state IN ('closed','applied') AND COALESCE(route,'') NOT IN ('dueno','humano') "
        "AND pack_id NOT IN "
        "(SELECT pack_id FROM esc_readings WHERE side IN ('opus','opus-qc','opus-arb') AND result IS NOT NULL)")]
    return {'cierres_indebidos': len(indebidos), 'ejemplos': [u[:12] for u in indebidos[:5]],
            'cierres_solo_jev': len(solo_jev), 'packs_solo_jev': solo_jev[:5]}


def status(root, packs=False, entities=None):
    """Foto del bucle. Con `packs`, una fila por pack (`--packs`) para seguir el piloto sin
    abrir sqlite; `entities` la acota a esas entidades."""
    state = load_state(root)
    conn = connect(root)
    try:
        ensure_esc_schema(conn)
        resolutions = {row['resolution']: row['n'] for row in conn.execute(
            'SELECT resolution,count(*) n FROM units WHERE resolution IS NOT NULL GROUP BY resolution')}
        out = {'root': str(root), 'halted': state['halted'], 'haltReason': state['haltReason'],
               'consecutiveFailures': state['consecutiveFailures'], 'firingCount': state['firingCount'],
               'cursor': state['cursor'], 'jevClass': state['jevClass'], 'jevRouting': state['jevRouting'],
               'window': state['window'], 'lastError': state['lastError'],
               'batches': [{k: b.get(k) for k in ('id', 'action', 'status', 'pid')}
                           for b in state['batches']],
               'packs': _count_states(conn), 'units': _unit_counts(conn), 'resoluciones': resolutions,
               'g7': g7(conn), 'orphans': _orphans(root, conn, state)}
        # Unidades del dueño en packs que la máquina aún trabaja (enrutado por unidad).
        in_flight = [row for state_ in MACHINE_STATES for row in _packs(conn, state_)]
        waiting = [_units_in(conn, json.loads(row['unit_ids'] or '[]'), 'needs-human') for row in in_flight]
        out['dueno_en_vuelo'] = {'packs': sum(1 for w in waiting if w), 'unidades': sum(len(w) for w in waiting)}
        if packs:
            rows = [dict(r) for r in conn.execute('SELECT * FROM esc_packs ORDER BY entity,pack_id')
                    if entities is None or r['entity'] in entities]
            out['packs_detalle'] = [{'pack_id': r['pack_id'], 'entity': r['entity'], 'kind': r['kind'],
                                     'idx': r['idx'], 'total': r['total'], 'riesgo': r['riesgo'],
                                     'clase': r['clase'], 'state': r['state'], 'route': r['route'],
                                     'unidades': len(json.loads(r['unit_ids'] or '[]'))} for r in rows]
    finally:
        conn.close()
    return out


def report(root, repo):
    """La foto de `status` en `esc/report/report.md` + la tanda pendiente del dueño (T9)."""
    from . import qa_esc_escalate
    snapshot = status(root)
    date = _today()
    lines = [f'# Auditoría escalonada 063 — {date}', '',
             f"Packs: {json.dumps(snapshot['packs'], ensure_ascii=False)}",
             f"Unidades: {json.dumps(snapshot['units'], ensure_ascii=False)}",
             f"Ventana: usedClaude {snapshot['window']['usedClaude']} · usedAstra "
             f"{snapshot['window']['usedAstra']} · pausa {snapshot['window']['pausedUntil']}",
             f"G7 (AC6): {json.dumps(snapshot['g7'], ensure_ascii=False)}", '', '## Resoluciones', '']
    lines += [f'- `{key}`: {value}' for key, value in sorted(snapshot['resoluciones'].items())] or ['- —']
    path = Path(root) / ESC_DIR / 'report' / 'report.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    conn = connect(root)
    try:
        packs = owner_pack_ids(conn)
        tanda = qa_esc_escalate.export_tanda(root, conn, packs, date) if packs else (None, None)
    finally:
        conn.close()
    out = {'report': str(path), 'tanda_md': str(tanda[0]) if tanda[0] else None,
           'tanda_json': str(tanda[1]) if tanda[1] else None, 'packs_en_tanda': len(packs)}
    return out


def tanda(root):
    from . import qa_esc_escalate
    conn = connect(root)
    try:
        ensure_esc_schema(conn)
        packs = owner_pack_ids(conn)
        md_path, json_path = qa_esc_escalate.export_tanda(root, conn, packs, _today()) \
            if packs else (None, None)
    finally:
        conn.close()
    out = {'packs': len(packs), 'md': str(md_path) if md_path else None,
           'json': str(json_path) if json_path else None}
    return out


def reopen(root, unit_ids, reason):
    from . import qa_esc_arbitrate
    conn = connect(root)
    try:
        ensure_esc_schema(conn)
        with supervisor_lock(root):
            done = qa_esc_arbitrate.reopen_to_owner(root, conn, unit_ids, reason, _today())
    finally:
        conn.close()
    return {'reopened': done, 'n': len(done)}


def tanda_apply(root, answers_path):
    from . import qa_esc_escalate
    conn = connect(root)
    try:
        ensure_esc_schema(conn)
        packs = qa_esc_escalate.merge_tanda_answers(root, conn, answers_path, _today())
    finally:
        conn.close()
    out = {'packs': list(packs), 'n': len(packs)}
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(prog='qa_esc', description='Auditoría escalonada por entidad (063)')
    parser.add_argument('--root', required=True, help='raíz de la auditoría (censo de la 061)')
    parser.add_argument('--repo', default=str(REPO))
    sub = parser.add_subparsers(dest='command', required=True)
    census_p = sub.add_parser('census', help='cola de entidades → filas `queued`')
    census_p.add_argument('--scope', default='pending', choices=SCOPES)
    census_p.add_argument('--entities', help='JSON con la lista de entity_id, o lista con comas')
    classify_p = sub.add_parser('classify', help='clase/riesgo por reglas (+ Jev si pasó la puerta)')
    classify_p.add_argument('--n', type=int)
    calibrate_p = sub.add_parser('calibrate', help='calibración de Jev + puerta de adopción')
    calibrate_p.add_argument('--run-decide', action='store_true', help='SACA datos del Mac')
    pack_p = sub.add_parser('pack', help='congela los packs de las entidades clasificadas')
    pack_p.add_argument('--n', type=int)
    pack_p.add_argument('--overlay-ref', default=WORKTREE)
    pack_p.add_argument('--no-fetch', action='store_true', help='no archivar URLs (sin red)')
    pack_p.add_argument('--entities', help='solo estas entidades (JSON o lista con comas)')
    pack_p.add_argument('--sources', choices=tuple(SOURCES), help='fuentes precargadas (por defecto, las del estado)')
    opus_p = sub.add_parser('opus', help='lectura de Opus 5 con búsqueda restringida')
    opus_p.add_argument('--n', type=int)
    opus_p.add_argument('--effort')
    opus_p.add_argument('--entities', help='solo estas entidades (JSON o lista con comas)')
    sub.add_parser('route', help='enruta lo leído: cerrar / escalar / dueño')
    reroute_p = sub.add_parser('reroute-owner', help='re-enruta por unidad los packs que route() mandó enteros al dueño')
    reroute_p.add_argument('--dry-run', action='store_true', help='lista lo que se movería, sin escribir')
    escalate_p = sub.add_parser('escalate', help='Astra ciega sobre lo escalado: acuerdo dual o árbitro')
    escalate_p.add_argument('--n', type=int)
    escalate_p.add_argument('--astra-tools', default=ASTRA_TOOLS_DEFAULT, choices=('auto', 'never', 'always'))
    arbitrate_p = sub.add_parser('arbitrate', help='árbitro Opus 5.5 + validación de Astra (063-B)')
    arbitrate_p.add_argument('--n', type=int)
    arbitrate_p.add_argument('--dry-run', action='store_true',
                             help='respuestas + informe sin fusionar ni cambiar estados')
    arbitrate_p.add_argument('--from-tanda', action='store_true',
                             help='toma también los packs en `tanda` (piloto) sin releer Opus/Astra')
    step_p = sub.add_parser('step', help='UN latido: una acción y una línea JSON')
    step_p.add_argument('--dry-run', action='store_true', help='decide sin lanzar el subproceso')
    status_p = sub.add_parser('status', help='foto del bucle + consulta G7')
    status_p.add_argument('--packs', action='store_true', help='añade una fila por pack')
    status_p.add_argument('--entities', help='acota el detalle a estas entidades')
    sub.add_parser('report', help='informe + tanda del dueño')
    sub.add_parser('tanda', help='exporta la tanda pendiente (JSON para AskUserQuestion)')
    apply_p = sub.add_parser('tanda-apply', help='fusiona las respuestas del dueño')
    apply_p.add_argument('--dec', required=True,
                         help='JSON [{pack_id, unit_id, choice_id, text?, evidence?, aliases?}]; retirar una '
                              'identidad exige aliases (la lista de los alias que se retiran)')
    reopen_p = sub.add_parser('reopen', help='devuelve al dueño unidades cerradas por el árbitro')
    reopen_p.add_argument('--units', required=True, help='unit_id completos separados por comas')
    reopen_p.add_argument('--reason', required=True)
    halt_p = sub.add_parser('halt', help='para el latido')
    halt_p.add_argument('--reason', default='manual')
    sub.add_parser('resume', help='reanuda tras una parada')
    args = parser.parse_args(argv)
    # Absolutas: los lectores corren en un tmpdir y una ruta relativa no llega a su recibo.
    root, repo = Path(args.root).resolve(), Path(args.repo).resolve()
    if args.command == 'census':
        out = census(root, repo, args.scope, entity_filter(args.entities))
    elif args.command == 'step':
        out = step(root, repo, dry_run=args.dry_run)
    elif args.command == 'status':
        out = status(root, args.packs, entity_filter(args.entities))
    elif args.command == 'report':
        out = report(root, repo)
    elif args.command == 'tanda':
        out = tanda(root)
    elif args.command == 'tanda-apply':
        out = tanda_apply(root, args.dec)
    elif args.command == 'reopen':
        out = reopen(root, [u.strip() for u in args.units.split(',') if u.strip()], args.reason)
    elif args.command == 'halt':
        out = halt(root, args.reason)
    elif args.command == 'resume':
        out = resume(root)
    elif args.command == 'calibrate':
        out = calibrate(root, repo, run_decide=args.run_decide)
    elif args.command == 'reroute-owner' and args.dry_run:
        out = reroute_owner(root, repo, dry_run=True)
    else:
        # Las etapas que llaman a modelos corren bajo el lock compartido con la 061.
        with supervisor_lock(root):
            if args.command == 'classify':
                out = classify(root, repo, args.n)
            elif args.command == 'pack':
                out = pack(root, repo, args.n, args.overlay_ref, fetch=not args.no_fetch,
                           entities=entity_filter(args.entities), sources=args.sources)
            elif args.command == 'opus':
                out = opus(root, repo, args.n, args.effort, entity_filter(args.entities))
            elif args.command == 'route':
                out = route(root, repo)
            elif args.command == 'reroute-owner':
                out = reroute_owner(root, repo)
            elif args.command == 'arbitrate':
                out = arbitrate(root, repo, args.n, dry_run=args.dry_run, from_tanda=args.from_tanda)
            else:
                out = escalate(root, repo, args.n, args.astra_tools)
    print(json.dumps(out, ensure_ascii=False, indent=1 if args.command == 'status' else None))
    return 0


__all__ = ['EFFORTS', 'MAX_CONSEC_FAIL', 'PACK_STATES', 'PAUSE_RETRY_SECONDS', 'PER_FIRE',
           'RESET_SECONDS', 'RESOLUTIONS', 'SIDES', 'SOFT_CAP_ASTRA', 'SOFT_CAP_CLAUDE', 'arbitrate', 'calibrate',
           'census', 'classify', 'decide', 'escalate', 'g7', 'halt', 'initial_state', 'load_state',
           'entity_filter', 'opus', 'owner_pack_ids', 'pack', 'report', 'reroute_owner', 'resume', 'route',
           'save_state', 'settle', 'split_routes', 'status',
           'step', 'tanda',
           'tanda_apply']


if __name__ == '__main__':
    raise SystemExit(main())
