"""Jev (TypeSafe System One) as the CLASSIFIER and the ROUTER of the staged audit (spec 063).

Jev never closes a unit here. It answers three groups of typed questions over a short
structured state (`questions.esc.json`: entidad · unidad · ruta) and its answers can only
HARDEN the route that the fixed rules already decided (`baseline_route`, rules B1-B3 of the
plan — the single implementation of the fixed rules, imported by the orchestrator).

What leaves the Mac is only text already published or versioned: no packet sources, no chunks,
no `source_quote` of an instruction-file report (UCO/UDEF/Guardia Civil/AEAT/atestado), no web
bodies. `_scrub` enforces that on every state before it is handed to `items_for`/`classify`.

From `qa_dual_jev` this module only imports the client, the error classification and the
measured model id — a resolved model other than `JEV_MEASURED_MODEL` is a configuration HALT,
never a reading.

    cd pipeline && .venv/bin/python -m src.qa_esc_jev --root <audit> --repo .. gold --out <dir>
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import unicodedata
import warnings

from .qa_dual_jev import (JEV_MEASURED_MODEL, JEV_MODEL, ConfigError, JevClient, classify_exception,
                          raw_answers)
from .ontologia import ontologia
from .qa_integral import connect, now

REPO = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO / 'specs' / '063-auditoria-escalonada-por-entidad'
# Spec 069: rendered from `questions.esc.tmpl.json` by `make ontologia` (criteria from the ontology).
QUESTIONS_PATH = REPO / 'pipeline' / 'data' / 'jev' / 'questions.esc.json'
GROUPS = ('entidad', 'unidad', 'ruta')
# Where the T0e round recorded what the owner decided and with which label.
T0E_DIR = REPO / 'specs' / '061-auditoria-dual-fable-astra'
T0E_REVISION = T0E_DIR / 'REVISION-T0e-2026-09-17.md'
T0E_RESOLUTION = 'humano-2026-09-17e'
A5_FINDING = Path(os.environ.get('QA_A5_FINDING', REPO / 'specs' / '..' / 'findings' / 'auditoria-riesgo-legal-2026-09-05.md'))
DECIDE_DIR = Path(os.environ.get('DECIDE_DIR', REPO.parent / 'decide_local'))

MAX_TEXT_CHARS = 2000
MAX_EVIDENCE_CHARS = 400
MAX_FEED_ITEMS = 6
CHARS_PER_TOKEN = 3.5
MAX_STATE_TOKENS = 2500
# Probability thresholds: a noul is true at τ, the risk label is the most probable one but
# recall comes first — 30 % of mass on alto+critico already routes as `alto`.
TAU = 0.5
TAU_ALTO = 0.30
MAX_SEARCHES = 12

CERO_APARICIONES = re.compile(r'0 apariciones|0 hits|no aparece', re.I)
# Reports of an open investigation file: their quotes are not published text and never travel.
SUMARIO = re.compile(r'uco|udef|guardia|aeat|atestado', re.I)
FORBIDDEN_KEYS = ('sources', 'chunks', 'source_quote', 'fuentes', 'cuerpo', 'body')
URL_RE = re.compile(r'https?://\S+')
DOC_RE = re.compile(r'[\w./-]+\.pdf', re.I)
LABEL_RE = re.compile(r'\[(ok|cita|atrib|reescrita|retirada|alias|estado|arista)\]')
UNIT_PREFIX_RE = re.compile(r'\(([0-9a-f]{5,8})\)')
ENTITY_ROW_RE = re.compile(r'^\|\s*`([^`]+)`\s*\|')

ACCUSATORY = ontologia().procesales
T1_ACCUSATORY = set(ACCUSATORY)
ROUTES = ('cerrar', 'escalar', 'dueno')
ROUTE_ORDER = {'cerrar': 0, 'escalar': 1, 'dueno': 2}
# Classes the owner reserved for himself (spec AC8): they never close without his decision.
OWNER_CLASSES = {'familiar_no_investigado', 'seudonimo', 'menor_o_victima'}
CONDUCT_CLASSES = {'particular_mencionado', 'empresario_con_papel_propio', 'testigo'}
RISK_LEVELS = ('bajo', 'medio', 'alto', 'critico')
# Spec AC5, literal.
GATE = {'clase_exactitud': 0.90, 'clase_recall': 0.95, 'riesgo_auc': 0.75, 'riesgo_ece': 0.10,
        'conducta_recall': 0.90, 'falso_cierre': 0.035, 'recall_dueno': 1.0, 'cobertura_delta': 0.10}
GATE_RECALL_CLASSES = ('familiar_no_investigado', 'particular_mencionado', 'menor_o_victima')
# `seudonimo` is NOT here: it is not an option of `clase_persona` (identity is never
# asked of Jev), so it can be reported but never scored as a target.
RECALL_CLASSES = GATE_RECALL_CLASSES


# ── questions ─────────────────────────────────────────────────────────────────

def load_questions(path=QUESTIONS_PATH):
    """The three question groups of `questions.esc.json` (keys starting with `_` are docs)."""
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    groups = {name: value for name, value in raw.items() if not name.startswith('_')}
    missing = [g for g in GROUPS if g not in groups]
    if missing:
        raise ValueError(f'questions.esc.json sin los grupos {missing}')
    return groups


def questions_for(group, path=QUESTIONS_PATH):
    """One group as the flat map `decide run --questions` expects."""
    return load_questions(path)[group]


# ── rules: what Jev is never asked ────────────────────────────────────────────

def entity_facts(conn, repo, entity_id):
    """Lazy bridge to the pack builder (T5): importing it at call time keeps this module
    testable with a stub and free of the pack builder's own imports."""
    from .qa_esc_packs import entity_facts as facts
    return facts(conn, repo, entity_id)


def flags(conn, repo, entity_id, facts=None):
    """The truths a rule already knows: they travel inside the state and are never asked.

    The rules themselves live ONCE, in the pack builder (`qa_esc_packs.rule_flags`), so the
    pack and the state can never disagree about what a rule already decided; here they are
    only normalized to the types the state carries.
    """
    from .qa_esc_packs import rule_flags
    facts = facts if facts is not None else entity_facts(conn, repo, entity_id)
    out = dict(rule_flags(facts))
    # `a5_familiar` needs a FAMILY_OF edge, and the 061 census kept a 10 % sample of T4
    # (`t4_sample_pct: 10` → 6 FAMILY_OF edges in the whole ledger): the rule is structurally
    # blind to relatives. The nominal A5 list of the legal-risk finding decides instead.
    if str(entity_id).startswith('Person:') and _is_a5(facts.get('name') or '', _a5_groups(str(A5_FINDING))):
        out['familiar_sin_acusacion'] = True
    out['accused_status_null'] = int(out.get('accused_status_null') or 0)
    return {key: (value if key == 'accused_status_null' else bool(value)) for key, value in out.items()}


# ── states (what leaves the Mac) ──────────────────────────────────────────────

def _clip(text, limit=MAX_TEXT_CHARS):
    text = '' if text is None else str(text)
    return text.strip()[:limit]


def _scrub(state):
    """Drop every key that could carry a source body or a quote from a non-public report."""
    if isinstance(state, dict):
        return {k: _scrub(v) for k, v in state.items() if k not in FORBIDDEN_KEYS}
    if isinstance(state, list):
        return [_scrub(v) for v in state]
    return state


def state_tokens(state):
    """Rough token size of a state (~3,5 chars per token); over the soft ceiling it warns."""
    size = math.ceil(len(json.dumps(state, ensure_ascii=False)) / CHARS_PER_TOKEN)
    if size > MAX_STATE_TOKENS:
        warnings.warn(f'estado de {size} tokens > {MAX_STATE_TOKENS} (recorta texto o feed)', stacklevel=2)
    return size


def _feed_headlines(items):
    out = []
    for item in (items or [])[:MAX_FEED_ITEMS]:
        if isinstance(item, dict):
            head = item.get('headline') or item.get('titular') or item.get('text') or ''
            date = item.get('iso') or item.get('fecha') or item.get('date') or ''
            out.append({'titular': _clip(head, 240), 'fecha': str(date)[:10]})
        else:
            out.append({'titular': _clip(item, 240), 'fecha': ''})
    return out


def _node_prose(conn, entity_id):
    """Summary and role as the census froze them (the pack carries the vigente text)."""
    row = conn.execute("SELECT payload FROM objects WHERE object_kind='node' AND entity=? LIMIT 1",
                       (entity_id,)).fetchone()
    props = (json.loads(row['payload']).get('props') or {}) if row else {}
    return props.get('summary') or '', props.get('role') or ''


def entity_state(conn, repo, entity_id):
    """Short JSON about one entity: identity, rule flags, published prose, edge counts, registro, feed."""
    facts = entity_facts(conn, repo, entity_id)
    summary, role = _node_prose(conn, entity_id)
    state = {'entidad': {'id': entity_id, 'tipo': facts.get('type') or '', 'nombre': facts.get('name') or '',
                         'aliases': [str(a) for a in (facts.get('aliases') or [])][:12]},
             'flags': flags(conn, repo, entity_id, facts),
             'role': _clip(facts.get('context_role') or role),
             'summary': _clip(facts.get('summary') or summary),
             'aristas': facts.get('edge_counts') or {},
             'registro': _registro(facts.get('estado_row')),
             'feed': _feed_headlines(facts.get('feed_items'))}
    return _scrub(state)


def _unit_value(unit_row):
    value = unit_row.get('value')
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _edge_brief(value):
    """The edge as the classifier sees it: never the quote, never the document's body."""
    props = (value or {}).get('props') or {}
    return {'type': value.get('type') or '', 'status': props.get('status') or '',
            'source_kind': props.get('source_kind') or '', 'amount': props.get('amount'),
            'currency': props.get('currency') or '', 'date': props.get('date') or '',
            'public_label': value.get('public_label') or ''}


def _cited_sources(text):
    """URLs and document names the published text itself mentions (never their content)."""
    if not isinstance(text, str):
        return []
    out = [u.rstrip('.,;)') for u in URL_RE.findall(text)] + DOC_RE.findall(text)
    return sorted(dict.fromkeys(out))[:8]


def _lints(conn, unit_id):
    if conn is None:
        return []
    try:
        rows = conn.execute('SELECT check_id FROM lint_checks WHERE unit_id=? ORDER BY check_id', (unit_id,))
    except Exception:  # the table only exists once `qa-dual-lint` ran on this census
        return []
    return [r[0] for r in rows]


def _registro(row):
    """The `accusations.yaml` row compressed to its procedural record.

    `entity_facts` hands over the WHOLE row (with its `crimes[].quote`): only the status, its
    date and the document that sustains it travel — never the verbatim charges.
    """
    if not isinstance(row, dict) or not row:
        return None
    estado = row.get('estado') if isinstance(row.get('estado'), dict) else row
    fuente = estado.get('fuente') or {}
    return {'status': str(estado.get('status') or row.get('status') or ''),
            'fecha': str(estado.get('fecha') or '')[:10],
            'fuente': {'doc': str(fuente.get('doc') or ''), 'tipo': str(fuente.get('tipo') or '')},
            'nota': _clip(estado.get('nota') or '', 240)}


def _vigencia(entity_state_):
    registro = entity_state_.get('registro') or {}
    if registro:
        fuente = registro.get('fuente') or {}
        return {'ultimo_acto': str(registro.get('status') or fuente.get('tipo') or ''),
                'fecha': str(registro.get('fecha') or '')[:10]}
    feed = entity_state_.get('feed') or []
    if feed:
        return {'ultimo_acto': feed[0]['titular'], 'fecha': feed[0]['fecha']}
    return {'ultimo_acto': '', 'fecha': ''}


def _entity_brief(entity_state_, classification):
    classification = classification or {}
    return {'id': entity_state_['entidad']['id'], 'clase': classification.get('clase') or '',
            'riesgo': classification.get('riesgo') or '', 'flags': entity_state_['flags']}


def unit_state(unit_row, entity_state_, classification, conn=None):
    """Short JSON about one unit: its text (or its edge), what it cites, its lints and vigencia."""
    value = _unit_value(unit_row)
    kind = unit_row.get('kind') or ''
    unidad = {'tier': unit_row.get('tier') or '', 'kind': kind, 'campo': unit_row.get('field') or ''}
    if kind == 'edge' and isinstance(value, dict):
        unidad['arista'] = _edge_brief(value)
        texto = ''
    else:
        texto = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        unidad['texto'] = _clip(texto)
    state = {'entidad': _entity_brief(entity_state_, classification), 'unidad': unidad,
             'fuentes_citadas': _cited_sources(texto), 'lints': _lints(conn, unit_row.get('unit_id')),
             'registro': entity_state_.get('registro'), 'vigencia': _vigencia(entity_state_)}
    return _scrub(state)


def _evidence_kind(text):
    text = (text or '').strip()
    if not text:
        return 'ninguna'
    if URL_RE.search(text):
        return 'web'
    if re.search(r'feed|[uú]ltima hora', text, re.I):
        return 'feed'
    return 'corpus'


def route_state(unit_row, pack_entity, decision_unit, decision_changes, classification, baseline):
    """Short JSON about ONE decision of the reader, for the `ruta` question."""
    decision_unit = decision_unit or {}
    changes = decision_changes or {}
    evidence = decision_unit.get('evidence') or ''
    value = _unit_value(unit_row)
    kind = unit_row.get('kind') or ''
    unidad = {'tier': unit_row.get('tier') or '', 'kind': kind, 'campo': unit_row.get('field') or ''}
    if kind == 'edge' and isinstance(value, dict):
        unidad['arista'] = _edge_brief(value)
    else:
        unidad['texto'] = _clip(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    entidad = {'id': (pack_entity or {}).get('id') or '', 'clase': (classification or {}).get('clase') or '',
               'riesgo': (classification or {}).get('riesgo') or '',
               'flags': (pack_entity or {}).get('flags') or {}}
    state = {'entidad': entidad, 'unidad': unidad,
             'opus': {'verdict': decision_unit.get('verdict') or '', 'label': decision_unit.get('label') or '',
                      'confianza': decision_unit.get('confidence') or '',
                      'accion': _unit_action(unit_row, changes),
                      'busquedas': int(changes.get('searches') or 0),
                      'evidencia': {'tipo': _evidence_kind(evidence),
                                    'texto': _clip(evidence, MAX_EVIDENCE_CHARS)},
                      'cero_apariciones': bool(CERO_APARICIONES.search(evidence)),
                      'notes': bool(changes.get('notes'))},  # no-llm-bool: presencia de texto
             'regla': {'ruta_baseline': baseline}}
    return _scrub(state)


def _unit_action(unit_row, changes):
    """The entity or edge action that touches this unit, as one word for the state."""
    kind = unit_row.get('kind') or ''
    if kind == 'edge':
        actions = sorted(a for a in (changes.get('edge_actions') or set()) if a)
        return actions[0] if actions else 'keep'
    if kind == 'summary':
        return changes.get('summary') or 'none'
    if kind == 'attribute':
        return changes.get('role') or 'none'
    if kind == 'identity':
        return 'alias_deny' if changes.get('alias_deny') else 'none'
    return changes.get('estado') or 'none'


# ── items and the typed call ──────────────────────────────────────────────────

def _questions_sha256(path=QUESTIONS_PATH):
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def items_for(group, states):
    """`decide` items: one `{id, state, meta}` per element, scrubbed and sized."""
    if group not in GROUPS:
        raise ValueError(f'grupo desconocido {group!r} (no está en {GROUPS})')
    items = []
    for row in states:
        ident, state = row['id'], _scrub(row['state'])
        meta = dict(row.get('meta') or {})
        meta['grupo'] = group
        meta['state_tokens'] = state_tokens(state)
        # Decision record (spec 069): model, prompt (questions file) hash and ontology version.
        meta['modelo'] = JEV_MODEL
        meta['preguntas_sha256'] = _questions_sha256()
        meta['ontologia_version'] = ontologia().version
        items.append({'id': ident, 'state': state, 'meta': meta})
    return items


def to_sdk(questions):
    """`questions.esc.json` shape → SDK objects: choice, noul and score
    (decide_local `backends/jev.py:100-119`)."""
    from typesafe_sdk import Choice, Noul, NoulCriteria, Score
    out = {}
    for name, question in questions.items():
        criteria = question.get('criteria')
        if question['type'] == 'noul':
            if isinstance(criteria, dict):
                criteria = NoulCriteria(true=criteria.get('true'), false=criteria.get('false'))
            out[name] = Noul(instructions=question['instructions'], criteria=criteria)
        elif question['type'] == 'choice':
            out[name] = Choice(instructions=question['instructions'], criteria=criteria)
        elif question['type'] == 'score':
            out[name] = Score(instructions=question['instructions'], criteria=criteria)
        else:
            raise ValueError(f'unsupported question type {question["type"]!r}')
    return out


class EscJevClient(JevClient):
    """`JevClient` plus the score-aware call: the 063 questions include two rubrics."""

    def system_one(self, state, questions):
        return self._client.system_one(state=state, questions=to_sdk(questions))


def make_client(model=JEV_MODEL):
    return EscJevClient(model)


def _score_answers(response, questions):
    """Score answers with the rubric level's own text as the probability key (decide's shape)."""
    out = {}
    for name, answer in getattr(response, 'scores', {}).items():
        levels = (questions.get(name) or {}).get('criteria') or []
        labels = [str(level) for level in levels] if isinstance(levels, list) else []
        probabilities = {}
        for index, probability in answer.probabilities.items():
            index = int(index)
            probabilities[labels[index] if 0 <= index < len(labels) else str(index)] = float(probability)
        out[name] = {'type': 'score', 'score': int(round(answer.score)), 'probabilities': probabilities,
                     'confidence': float(answer.confidence)}
    return out


def classify(client, items, questions, attempt_dir):
    """One typed call per item. Returns `(results, receipt)`; the ledger rows are the caller's.

    `results` are `decide`-shaped rows (so `decide bench` can score them straight away) and every
    call is journaled to `calls.jsonl` BEFORE the caller sees it. A resolved model other than the
    measured one is a `ConfigError`: the calibration belongs to `JEV_MEASURED_MODEL`.
    """
    attempt_dir = Path(attempt_dir)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    receipt = {'attempt_id': attempt_dir.name, 'side': 'jev', 'model': JEV_MODEL,
               'measured_model': JEV_MEASURED_MODEL, 'model_resolved': None, 'items': len(items),
               'questions': sorted(questions), 'started_at': now(),
               'usage': {'input': 0, 'output': 0}, 'error': None}
    results, calls = [], []
    start = time.monotonic()
    try:
        for item in items:
            started = time.perf_counter()
            response = client.system_one(state=item['state'], questions=questions)
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            usage = getattr(response, 'usage', None)
            answers = {**raw_answers(response), **_score_answers(response, questions)}
            row = {'id': item['id'], 'backend': 'jev', 'model': response.model, 'answers': answers,
                   'usage': {'prefill_tokens': getattr(usage, 'input_tokens', None) or 0, 'cached': False,
                             'question_tokens': getattr(usage, 'output_tokens', None) or 0},
                   'latency_ms': latency_ms, 'meta': item.get('meta') or {}}
            calls.append(row)
            results.append(row)
            receipt['model_resolved'] = response.model
            receipt['usage']['input'] += row['usage']['prefill_tokens']
            receipt['usage']['output'] += row['usage']['question_tokens']
            if response.model != JEV_MEASURED_MODEL:
                raise ConfigError(f'modelo resuelto {response.model!r} ≠ medido {JEV_MEASURED_MODEL!r}')
    except ConfigError:
        receipt['error'] = f'config: modelo resuelto {receipt["model_resolved"]!r}'
        raise
    except Exception as exc:
        receipt['error'] = classify_exception(exc)
        raise
    finally:
        with (attempt_dir / 'calls.jsonl').open('w', encoding='utf-8') as handle:
            for call in calls:
                handle.write(json.dumps(call, ensure_ascii=False) + '\n')
        receipt['finished_at'] = now()
        receipt['elapsed_seconds'] = round(time.monotonic() - start, 3)
        (attempt_dir / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=1))
    return results, receipt


def p_answer(answers, name, option=None):
    """P(yes) of a `noul`, or P(option) of a `choice` (spec 069 split compound questions into
    exclusive choices; the judgments the router reads keep their names)."""
    answer = (answers or {}).get(name) or {}
    if option is None:
        return float(answer.get('noul', 0.0))
    probs = answer.get('probabilities') or {}
    if probs:
        return float(probs.get(option, 0.0))
    return 1.0 if answer.get('choice') == option else 0.0


def classification_from(answers):
    """Typed answers → the thresholded judgments the router uses (τ = 0,5; riesgo by mass)."""
    answers = answers or {}

    def noul(name):
        return p_answer(answers, name) >= TAU

    def choice(name):
        answer = answers.get(name) or {}
        return answer.get('choice') or ''

    riesgo_entidad = _risk_label(answers.get('riesgo_entidad'))
    return {'clase': choice('clase_persona'), 'riesgo': riesgo_entidad,
            'menor_o_victima': noul('menor') or noul('victima'),
            'afirma_conducta': p_answer(answers, 'conducta', 'conducta_afirmada') >= TAU,
            'mas_alla_del_registro': noul('mas_alla_del_registro'),
            'dato_biografico': noul('dato_biografico'),
            'valoracion_o_etiqueta': p_answer(answers, 'valoracion', 'valoracion_propia') >= TAU,
            'nombra_tercero': noul('nombra_tercero'),
            'apoyo_en_prensa': p_answer(answers, 'apoyo', 'solo_prensa_o_sin_fuente') >= TAU,
            'riesgo_unidad': _risk_label(answers.get('riesgo_unidad'))}


def _risk_label(answer):
    """The most probable rubric level, raised to `alto` when alto+critico carry τ_alto of the mass."""
    if not answer:
        return ''
    probabilities = {_level_label(k): float(v) for k, v in (answer.get('probabilities') or {}).items()}
    if not probabilities:
        return ''
    label = max(probabilities, key=lambda k: probabilities[k])
    tail = probabilities.get('alto', 0.0) + probabilities.get('critico', 0.0)
    if label in ('bajo', 'medio') and tail >= TAU_ALTO:
        return 'alto'
    return label


def _level_label(level):
    """`"alto: conducta atribuida sin registro…"` → `alto`."""
    return _strip_accents(str(level).split(':')[0].strip().lower())


def _strip_accents(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')


# ── the fixed router (B1-B3) ──────────────────────────────────────────────────

def _decision_unit(decision, unit_id):
    for unit in (decision or {}).get('units') or []:
        if unit.get('unit_id') == unit_id:
            return unit
    return {}


def baseline_route(unit_row, pack_entity, decision, classification):
    """The fixed rules of the plan (B1 dueño · B2 escalar · B3 cerrar). Jev can only harden this.

    `decision` is the reader's whole decision (so entity actions count) plus, when the caller
    stamps it, `searches`: over `MAX_SEARCHES` the reading is void anyway, but a route that
    survives it escalates.
    """
    from .qa_esc_schema import changes as decision_changes
    entity_flags = (pack_entity or {}).get('flags') or {}
    judgments = classification or {}
    unit = _decision_unit(decision, unit_row.get('unit_id'))
    changes = decision_changes(decision or {})
    kind, tier = unit_row.get('kind') or '', unit_row.get('tier') or ''
    label, verdict = unit.get('label') or '', unit.get('verdict') or ''
    clase, riesgo = judgments.get('clase') or '', judgments.get('riesgo') or ''
    edge_type = (_unit_value(unit_row) or {}).get('type') if kind == 'edge' else None
    # Spec 069 doubt policy: an uncertain verdict on an accusatory edge (or CONTROLS/FAMILY_OF) is
    # never settled by another model — it goes straight to the owner.
    if verdict == 'incierto' and edge_type and ontologia().ante_duda(edge_type) == 'revision':
        return 'dueno'
    # B1 — the owner's desk.
    if (clase in OWNER_CLASSES or judgments.get('menor_o_victima') or riesgo == 'critico'
            or entity_flags.get('seudonimo') or entity_flags.get('anonimizado')
            or entity_flags.get('familiar_sin_acusacion') or kind == 'identity'
            or label in ('alias', 'estado')
            # `notes` NO manda al dueño: Opus deja notas en el 100 % de los packs (piloto 2026-09-21) y la
            # regla anulaba el nivel dual; las notas viajan al informe y a la tanda como contexto.
            or (clase == 'particular_mencionado' and judgments.get('valoracion_o_etiqueta'))):
        return 'dueno'
    # B2 — blind dual reading.
    if (riesgo == 'alto'
            or (tier == 'T1' and edge_type in T1_ACCUSATORY)
            or (clase in CONDUCT_CLASSES and judgments.get('afirma_conducta'))
            or label in ('reescrita', 'retirada')
            or verdict == 'incierto'
            or int((decision or {}).get('searches') or 0) > MAX_SEARCHES
            or judgments.get('nombra_tercero')
            or (kind == 'edge' and changes['edge_actions'])
            or (kind == 'summary' and changes['summary'] == 'remove')
            or (kind == 'attribute' and changes['role'] == 'remove')):
        return 'escalar'
    # B3 — close, or escalate when the closing conditions are not all met.
    if (label in ('ok', 'cita', 'atrib') and unit.get('confidence') == 'alta'
            and _evidence_kind(unit.get('evidence')) != 'ninguna'):
        return 'cerrar'
    return 'escalar'


def harden(base, jev):
    """Jev may only move cerrar → escalar → dueño; anything else keeps the rule's route."""
    if jev not in ROUTE_ORDER:
        return base
    return max((base, jev), key=lambda route: ROUTE_ORDER[route])


# ── gold (the 925 units the owner already resolved) ───────────────────────────

def _load_yaml(path):
    import yaml
    path = Path(path)
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding='utf-8')) or {}


def _pseudonym_ids(repo):
    rows = _load_yaml(Path(repo) / 'pipeline' / 'data' / 'context' / 'pseudonyms.yaml').get('pseudonyms') or []
    return {row.get('id') for row in rows if isinstance(row, dict) and row.get('id')}


def _figure_names(repo):
    """Names of the curated context layer that carry a public role (`entities:`, never `relations:`)."""
    rows = _load_yaml(Path(repo) / 'pipeline' / 'data' / 'context' / 'figures.yaml').get('entities') or []
    return {_norm(row.get('name')) for row in rows
            if isinstance(row, dict) and row.get('name') and str(row.get('role') or '').strip()}


def _estado_rows(repo):
    rows = _load_yaml(Path(repo) / 'pipeline' / 'data' / 'accusations' / 'accusations.yaml').get('accusations') or []
    return {row.get('entity_id'): row.get('estado') for row in rows
            if isinstance(row, dict) and row.get('entity_id') and row.get('estado')}


def _norm(name):
    return _strip_accents(str(name or '')).lower().strip()


def a5_names(path=A5_FINDING):
    """The nominal A5 list (relatives with `FAMILY_OF` and no charge) from the legal-risk finding.

    The finding is a markdown table: the A5 row names them inside one parenthesis, with
    `Nombre1/Nombre2 Apellido` for siblings. Returns the name sets used for matching.
    """
    path = Path(path)
    if not path.exists():
        return []
    row = next((line for line in path.read_text(encoding='utf-8').splitlines()
                if line.startswith('| A5 ')), '')
    inner = max(re.findall(r'\(([^()]{20,})\)', row), key=len, default='')
    names = []
    for token in inner.split(','):
        token = token.strip()
        if not token or len(token) < 3:
            continue
        head, _, tail = token.partition('/')
        if tail:
            rest = tail.split(' ', 1)[1] if ' ' in tail else ''
            names += [f'{head} {rest}'.strip(), tail.strip()]
        else:
            names.append(token)
    return [frozenset(w for w in _norm(n).split() if len(w) >= 4) for n in names if n]


@lru_cache(maxsize=4)
def _a5_groups(path):
    """The A5 name sets, read once per path (`flags` asks for them entity by entity)."""
    return tuple(a5_names(Path(path)))


def _is_a5(name, a5):
    words = {w for w in _norm(name).split() if len(w) >= 4}
    return any(group and group <= words for group in a5)


def _t0e_owner_units(path=T0E_REVISION):
    """Unit-id prefixes of the entities the owner touched in the tandas (`✎` in the round's report)."""
    path = Path(path)
    if not path.exists():
        return set()
    prefixes = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        if '✎' in line and ENTITY_ROW_RE.match(line):
            prefixes.update(UNIT_PREFIX_RE.findall(line))
    return prefixes


def _causa(evidence):
    if re.search(r'0 apariciones|0 hits|sin fuente', evidence, re.I):
        return 'sin_fuente'
    if re.search(r'alias', evidence, re.I):
        return 'alias'
    if re.search(r'estado|imputad|investigad|acusad|condenad', evidence, re.I):
        return 'estado'
    if re.search(r'seg[uú]n', evidence, re.I):
        return 'atribucion'
    return ''


def gold(conn, repo):
    """One gold row per unit the owner resolved (`humano-%`), in `decide bench` shape."""
    repo = Path(repo)
    pseudonyms, figures, estados = _pseudonym_ids(repo), _figure_names(repo), _estado_rows(repo)
    a5 = a5_names(A5_FINDING)
    owner_prefixes = _t0e_owner_units(T0E_REVISION)
    names, accused = {}, set()
    for row in conn.execute("SELECT entity, type, payload FROM objects WHERE object_kind='node'"):
        names[row['entity']] = (json.loads(row['payload']).get('props') or {}).get('name') or ''
    for row in conn.execute('SELECT DISTINCT entity, type FROM objects '
                            "WHERE object_kind='relationship'"):
        if row['type'] in T1_ACCUSATORY:
            accused.add(row['entity'])
    evidence_by_unit = {}
    for row in conn.execute("SELECT unit_id, payload FROM events WHERE event IN "
                            "('corregido-humano','verificado-humano') ORDER BY seq"):
        evidence_by_unit[row['unit_id']] = (json.loads(row['payload']) or {}).get('evidence') or ''
    rows = conn.execute('SELECT u.unit_id, u.kind, u.field, u.value, u.resolution, t.tier, o.entity, o.type '
                        'FROM units u JOIN tiers t USING(unit_id) JOIN objects o USING(object_id) '
                        "WHERE u.resolution LIKE 'humano-%' ORDER BY u.unit_id")
    out = []
    for row in rows:
        uid, entity = row['unit_id'], row['entity']
        resolution = row['resolution'] or ''
        corregido = resolution.endswith('-corregido')
        evidence = evidence_by_unit.get(uid, '')
        found = LABEL_RE.search(evidence)
        etiqueta = found.group(1) if found else None
        clases = _gold_classes(entity, names.get(entity, ''), pseudonyms, figures, estados, accused, a5)
        riesgo = _gold_risk(etiqueta, entity, row, pseudonyms, accused, a5, names)
        ruta = _gold_route(uid, resolution, etiqueta, corregido, owner_prefixes)
        out.append({'id': uid, 'entity': entity, 'kind': row['kind'], 'tier': row['tier'],
                    'resolution': resolution,
                    'gold': {'clase': clases[0] if clases else None, 'clases_validas': clases,
                             'riesgo': riesgo, 'ruta': ruta, 'etiqueta': etiqueta,
                             'causa': _causa(evidence) if etiqueta else None,
                             'corregido': 'no-ok' if corregido else 'ok'}})
    return out


def _gold_classes(entity, name, pseudonyms, figures, estados, accused, a5):
    """EVERY deterministic class that fits the entity, most specific first.

    They are not exclusive: a public figure under investigation is both, and scoring a
    classifier against only one of them would count a correct answer as an error. `clase`
    (the first) is what the tables report; `clases_validas` is what the scoring accepts.
    """
    classes = []
    if entity in pseudonyms:
        classes.append('seudonimo')
    if entity.startswith('Person:') and _is_a5(name, a5):
        classes.append('familiar_no_investigado')
    if _norm(name) in figures:
        classes.append('figura_publica')
    if entity in estados or entity in accused:
        classes.append('investigado_encausado')
    return classes


def _gold_risk(etiqueta, entity, row, pseudonyms, accused, a5, names):
    accusatory_unit = row['tier'] == 'T1' and (row['type'] or '') in T1_ACCUSATORY
    if (etiqueta in ('retirada', 'estado') or entity in pseudonyms
            or _is_a5(names.get(entity, ''), a5) or accusatory_unit):
        return 'alto'
    if etiqueta in ('ok', 'cita'):
        return 'bajo'
    return 'medio'


def _gold_route(unit_id, resolution, etiqueta, corregido, owner_prefixes):
    """Only the T0e units have a route gold: they are the ones read by three readers + owner."""
    if not resolution.startswith(T0E_RESOLUTION) or etiqueta is None:
        return None
    if any(unit_id.startswith(prefix) for prefix in owner_prefixes):
        return 'dueno'
    if etiqueta in ('reescrita', 'retirada'):
        return 'escalar'
    if etiqueta in ('ok', 'cita', 'atrib') and corregido == (etiqueta != 'ok'):
        return 'cerrar'
    return None


# ── calibration ───────────────────────────────────────────────────────────────

def calibrate(root, repo, out_dir, run_decide=False, rescore=False):
    """Export gold + items (+ pred/bench when asked) and write `calibration.md`.

    Without `run_decide` nothing leaves the Mac: the files are written and the report says
    which groups are ready. `items.ruta.jsonl` only exists when there are Opus decisions in
    `esc_readings` for the gold units. `rescore` re-reads the `pred.*.jsonl` already on disk
    and recomputes the report WITHOUT calling anything: the export is deterministic, so a
    measurement bug is fixed by re-scoring, never by paying for the answers again.
    """
    root, repo, out_dir = Path(root), Path(repo), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(root)
    reuse = bool(rescore and (out_dir / 'items.entidad.jsonl').exists())
    try:
        gold_rows = gold(conn, repo)
        _write_jsonl(out_dir / 'gold.jsonl', gold_rows)
        if reuse:
            # The item files ARE the record of what was sent to the API: a re-score fixes the
            # measurement, never the evidence. Only the gold and the report are rewritten.
            entity_items, unit_items = (_read_pred(out_dir / f'items.{g}.jsonl') for g in ('entidad', 'unidad'))
            route_items = _read_pred(out_dir / 'items.ruta.jsonl')
            return _finish_calibration(out_dir, gold_rows, entity_items, unit_items, route_items,
                                       run_decide, rescore)
        entity_ids = sorted({row['entity'] for row in gold_rows if row['entity']})
        units = _gold_units(conn, [row['id'] for row in gold_rows])
        states = {}
        for entity in entity_ids:
            try:
                states[entity] = entity_state(conn, repo, entity)
            except Exception as exc:  # an entity the pack builder cannot describe is not calibrated
                states[entity] = None
                warnings.warn(f'{entity}: sin estado ({exc})', stacklevel=2)
        entity_items = items_for('entidad', [{'id': e, 'state': s, 'meta': {'entity': e}}
                                             for e, s in states.items() if s])
        unit_items = items_for('unidad', [
            {'id': row['id'], 'state': unit_state(units[row['id']], states[row['entity']], None, conn),
             'meta': {'entity': row['entity'], 'kind': row['kind'], 'tier': row['tier']}}
            for row in gold_rows if states.get(row['entity']) and row['id'] in units])
        _write_jsonl(out_dir / 'items.entidad.jsonl', entity_items)
        _write_jsonl(out_dir / 'items.unidad.jsonl', unit_items)
        route_items = _route_items(conn, gold_rows, units, states)
        if route_items:
            _write_jsonl(out_dir / 'items.ruta.jsonl', route_items)
        questions = load_questions()
        for group in GROUPS:
            (out_dir / f'questions.{group}.json').write_text(
                json.dumps(questions[group], ensure_ascii=False, indent=1), encoding='utf-8')
    finally:
        conn.close()
    return _finish_calibration(out_dir, gold_rows, entity_items, unit_items, route_items,
                               run_decide, rescore)


def _finish_calibration(out_dir, gold_rows, entity_items, unit_items, route_items, run_decide, rescore):
    _write_jsonl(out_dir / 'gold.entidad.jsonl', _entity_gold(gold_rows))
    summary = {'gold': len(gold_rows), 'items': {'entidad': len(entity_items), 'unidad': len(unit_items),
                                                'ruta': len(route_items)},
               'distribucion': _distribution(gold_rows), 'run_decide': bool(run_decide),
               'rescore': bool(rescore)}
    if run_decide:
        summary['decide'] = _run_decide(out_dir)
    if run_decide or rescore:
        summary.update(_score(out_dir, gold_rows))
    (out_dir / 'calibration.md').write_text(_calibration_md(summary, out_dir), encoding='utf-8')
    (out_dir / 'calibration.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                              encoding='utf-8')
    return summary


def _entity_gold(gold_rows):
    """The same gold keyed by ENTITY, for `decide bench` on the `entidad` group.

    `bench` matches pred ids against gold ids, and the `entidad` predictions are keyed by
    entity while the audit's gold is keyed by unit: without this file every entity id is a
    miss («ningún id del gold aparece en las predicciones», first real run).
    """
    out = {}
    for row in gold_rows:
        classes = _valid_classes(row['gold'])
        if classes and row['entity'] not in out:
            out[row['entity']] = {'id': row['entity'], 'entity': row['entity'], 'kind': 'entidad',
                                  'gold': {'clase': classes[0], 'clases_validas': classes}}
    return list(out.values())


def _gold_units(conn, unit_ids):
    rows = conn.execute('SELECT u.unit_id, u.kind, u.field, u.value, t.tier, o.entity, o.type '
                        'FROM units u JOIN tiers t USING(unit_id) JOIN objects o USING(object_id)')
    wanted = set(unit_ids)
    return {r['unit_id']: dict(r) for r in rows if r['unit_id'] in wanted}


def _route_items(conn, gold_rows, units, states):
    """`ruta` items exist only where a reader decision is already recorded for the unit."""
    try:
        readings = list(conn.execute("SELECT pack_id, result FROM esc_readings WHERE side='opus'"))
    except Exception:
        return []
    decisions = {}
    for reading in readings:
        decision = json.loads(reading['result'] or '{}')
        for unit in decision.get('units') or []:
            decisions[unit.get('unit_id')] = decision
    rows = []
    for row in gold_rows:
        decision = decisions.get(row['id'])
        unit = units.get(row['id'])
        if not decision or not unit or not states.get(row['entity']):
            continue
        from .qa_esc_schema import changes as decision_changes
        entity = {'id': row['entity'], 'flags': states[row['entity']]['flags']}
        base = baseline_route(unit, entity, decision, None)
        rows.append({'id': row['id'],
                     'state': route_state(unit, entity, _decision_unit(decision, row['id']),
                                          decision_changes(decision), None, base),
                     'meta': {'entity': row['entity'], 'baseline': base}})
    return items_for('ruta', rows)


def _write_jsonl(path, rows):
    with Path(path).open('w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')


def _distribution(gold_rows):
    out = defaultdict(Counter)
    for row in gold_rows:
        for key, value in row['gold'].items():
            for item in (value if isinstance(value, list) else [value]):
                out[key][str(item)] += 1
    return {key: dict(counter) for key, counter in out.items()}


def _run_decide(out_dir):
    """`decide run` + `decide bench` from decide_local, with its own `.env` (the key never
    reaches this process). `cerrar` is exported as `ok` so `_as_choice` scores P(ok)."""
    done = {}
    # `decide` runs with cwd=DECIDE_DIR: every path it receives must be absolute.
    out_dir = Path(out_dir).resolve()
    for group in GROUPS:
        items = out_dir / f'items.{group}.jsonl'
        if not items.exists():
            continue
        pred = out_dir / f'pred.{group}.jsonl'
        command = (f'set -a; . ./.env; set +a; uv run --no-sync decide run --backend jev '
                   f'--model {JEV_MEASURED_MODEL} --questions {out_dir / f"questions.{group}.json"} '
                   f'--items {items} --out {pred} --resume')
        run = subprocess.run(['zsh', '-lc', command], cwd=DECIDE_DIR, capture_output=True, text=True)
        done[group] = {'returncode': run.returncode, 'stderr': run.stderr[-2000:]}
        if run.returncode != 0 or not pred.exists():
            continue
        if group == 'ruta':
            _rename_choice(pred, 'ruta', {'cerrar': 'ok'})
        question = {'entidad': 'clase_persona', 'unidad': 'conducta', 'ruta': 'ruta'}[group]
        gold_file = out_dir / ('gold.entidad.jsonl' if group == 'entidad' else 'gold.jsonl')
        bench = (f'set -a; . ./.env; set +a; uv run --no-sync decide bench --pred {pred} '
                 f'--gold {gold_file} --report {out_dir / f"bench.{group}.md"} '
                 f'--summary {out_dir / f"bench.{group}.json"} --question {question} '
                 f'--gold-keys clase,riesgo,ruta --group-by kind')
        run = subprocess.run(['zsh', '-lc', bench], cwd=DECIDE_DIR, capture_output=True, text=True)
        done[group]['bench'] = {'returncode': run.returncode, 'stderr': run.stderr[-2000:]}
    return done


def _rename_choice(pred_path, question, mapping):
    rows = [json.loads(line) for line in Path(pred_path).read_text(encoding='utf-8').splitlines() if line.strip()]
    for row in rows:
        answer = (row.get('answers') or {}).get(question)
        if not answer:
            continue
        answer['choice'] = mapping.get(answer.get('choice'), answer.get('choice'))
        answer['probabilities'] = {mapping.get(k, k): v for k, v in (answer.get('probabilities') or {}).items()}
    _write_jsonl(pred_path, rows)


# ── metrics and the adoption gate ─────────────────────────────────────────────

def auc(positive, negative):
    """Pair-counting AUC (ties count a half), as in `tools-T0e/jev_t0e.py`."""
    if not positive or not negative:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in positive for n in negative)
    return round(wins / (len(positive) * len(negative)), 4)


def ece(probabilities, correct, bins=10):
    """Expected calibration error over `bins` equal-width buckets."""
    if not probabilities:
        return None
    total, error = len(probabilities), 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        chosen = [(p, c) for p, c in zip(probabilities, correct) if (low < p <= high or (index == 0 and p == 0))]
        if not chosen:
            continue
        mean_p = sum(p for p, _ in chosen) / len(chosen)
        accuracy = sum(1 for _, c in chosen if c) / len(chosen)
        error += (len(chosen) / total) * abs(mean_p - accuracy)
    return round(error, 4)


def _score(out_dir, gold_rows):
    """The summary `adoption_gate` reads, computed from the preds this run produced.

    Two measurement rules, learned from the first real run (2026-09-21):
    `clase_persona` is only scored on PEOPLE — for a company, an organization or a case
    `no_aplica` is the correct answer by construction, and counting those as hits (or as
    misses) says nothing about the classifier — and `seudonimo` is not one of its options
    (identity is never asked of Jev), so it cannot be a target either.
    """
    by_id = {row['id']: row['gold'] for row in gold_rows}
    summary = {}
    preds = _read_pred(out_dir / 'pred.entidad.jsonl')
    types = {row['id']: ((row.get('state') or {}).get('entidad') or {}).get('tipo') or ''
             for row in _read_pred(out_dir / 'items.entidad.jsonl')}
    roles = {row['id']: ((row.get('state') or {}).get('role') or '')
             for row in _read_pred(out_dir / 'items.entidad.jsonl')}
    seen_familiar = {row['id']: bool(((row.get('state') or {}).get('flags') or {}).get('familiar_sin_acusacion'))
                     for row in _read_pred(out_dir / 'items.entidad.jsonl')}
    clase_pairs, role_pairs = [], []
    riesgo = {'probabilities': [], 'correct': [], 'positive': [], 'negative': []}
    entity_classes = {}
    for row in gold_rows:
        classes = _valid_classes(row['gold'])
        if classes:
            entity_classes.setdefault(row['entity'], classes)
    not_person, no_aplica, person_no_aplica, pseudonym_answers = 0, 0, [], Counter()
    for pred in preds:
        answer = (pred.get('answers') or {}).get('clase_persona') or {}
        choice = answer.get('choice')
        is_person = types.get(pred['id']) == 'Person'
        not_person += not is_person
        no_aplica += choice == 'no_aplica'
        if choice == 'no_aplica' and is_person:
            person_no_aplica.append(pred['id'])
        truth = entity_classes.get(pred['id']) or []
        if 'seudonimo' in truth:
            pseudonym_answers[choice or '(sin respuesta)'] += 1
        valid = [c for c in truth if c != 'seudonimo']
        if valid and choice and is_person:
            clase_pairs.append((choice, tuple(valid)))
            role_pairs.append((choice, tuple(_with_business_role(valid, roles.get(pred['id'], '')))))
    for pred in _read_pred(out_dir / 'pred.unidad.jsonl'):
        truth = by_id.get(pred['id']) or {}
        answer = (pred.get('answers') or {}).get('riesgo_unidad') or {}
        if not truth.get('riesgo') or not answer:
            continue
        probabilities = {_level_label(k): float(v) for k, v in (answer.get('probabilities') or {}).items()}
        mass = probabilities.get('alto', 0.0) + probabilities.get('critico', 0.0)
        is_high = truth['riesgo'] in ('alto', 'critico')
        (riesgo['positive'] if is_high else riesgo['negative']).append(mass)
        riesgo['probabilities'].append(mass)
        riesgo['correct'].append(is_high)
    summary['clase'] = {'n': len(clase_pairs),
                        # A hit is any class the rules consider valid for the entity, not just
                        # the one the report prints first.
                        'exactitud': round(sum(p in g for p, g in clase_pairs) / len(clase_pairs), 4)
                        if clase_pairs else None,
                        # Informative: figures.yaml gives a curated role to businessmen too, so
                        # the rule calls «figura pública» people Jev calls «empresario».
                        'exactitud_rol_empresario':
                            round(sum(p in g for p, g in role_pairs) / len(role_pairs), 4)
                            if role_pairs else None,
                        'recall': {label: _recall(clase_pairs, label) for label in RECALL_CLASSES},
                        'no_person': {'n': not_person, 'no_aplica_total': no_aplica,
                                      'no_aplica_pct': round(no_aplica / len(preds), 4) if preds else None,
                                      'personas_con_no_aplica': person_no_aplica},
                        'seudonimos': {'n': sum(pseudonym_answers.values()),
                                       'respuestas': dict(pseudonym_answers)},
                        # Did the state Jev read even carry the flag it is being scored on?
                        'familiar_flag': {'n': sum(1 for e, c in entity_classes.items()
                                                   if 'familiar_no_investigado' in c and e in seen_familiar),
                                          'con_flag': sum(1 for e, c in entity_classes.items()
                                                          if 'familiar_no_investigado' in c
                                                          and seen_familiar.get(e))}}
    summary['riesgo'] = {'n': len(riesgo['probabilities']),
                         'auc': auc(riesgo['positive'], riesgo['negative']),
                         'ece': ece(riesgo['probabilities'], riesgo['correct'])}
    summary['conducta'] = _conduct_recall(out_dir, by_id)
    summary['ruta'] = _route_scores(out_dir, by_id)
    return summary


def _valid_classes(gold_row):
    classes = gold_row.get('clases_validas')
    if classes:
        return list(classes)
    return [gold_row['clase']] if gold_row.get('clase') else []


def _recall(pairs, label):
    """Over the entities the rules place in `label`: how often the classifier says so."""
    truth = [(p, g) for p, g in pairs if label in g]
    return round(sum(p == label for p, _ in truth) / len(truth), 4) if truth else None


BUSINESS_ROLE = re.compile(r'empresari|fundador|administrador|apoderad|consejer[oa]|socio|'
                           r'director(?:a)? general|presidente del (?:grupo|consejo)', re.I)


def _with_business_role(classes, role):
    """`empresario_con_papel_propio` is deterministic too when the CURATED role says so."""
    if BUSINESS_ROLE.search(role or '') and 'empresario_con_papel_propio' not in classes:
        return list(classes) + ['empresario_con_papel_propio']
    return list(classes)


def _conduct_recall(out_dir, by_id):
    """Two measures of «does Jev see a conduct claim», both against weak gold proxies.

    `recall` uses the audit's label (`reescrita`/`retirada`): the owner corrected the unit, but
    often for a date, an amount or a missing attribution, not for a conduct claim — so a low
    value here is as much a fact about the proxy as about Jev. `mas_alla_del_registro` is the
    tighter one: units whose recorded cause is the procedural status (`causa == 'estado'`).
    """
    labelled = beyond = hits = beyond_hits = 0
    for pred in _read_pred(out_dir / 'pred.unidad.jsonl'):
        truth = by_id.get(pred['id']) or {}
        answers = pred.get('answers') or {}
        if truth.get('etiqueta') in ('reescrita', 'retirada'):
            labelled += 1
            hits += p_answer(answers, 'conducta', 'conducta_afirmada') >= TAU
        if truth.get('causa') == 'estado':
            beyond += 1
            beyond_hits += float((answers.get('mas_alla_del_registro') or {}).get('noul', 0.0)) >= TAU
    return {'recall': round(hits / labelled, 4) if labelled else None, 'n': labelled,
            'proxy': 'etiqueta reescrita|retirada (débil: la corrección puede ser de fecha o importe)',
            'mas_alla_del_registro': {'recall': round(beyond_hits / beyond, 4) if beyond else None,
                                      'n': beyond, 'proxy': "causa == 'estado'"}}


def _route_scores(out_dir, by_id):
    rows = _read_pred(out_dir / 'pred.ruta.jsonl')
    if not rows:
        return {'n': 0, 'falso_cierre': None, 'falso_cierre_baseline': None, 'recall_dueno': None,
                'cobertura': None, 'cobertura_baseline': None}
    closed = wrong = owner = owner_hit = 0
    base_closed = base_wrong = 0
    for pred in rows:
        truth = (by_id.get(pred['id']) or {}).get('ruta')
        if not truth:
            continue
        answer = (pred.get('answers') or {}).get('ruta') or {}
        # `cerrar` travels as `ok` in the pred so `decide bench` scores P(ok).
        route = harden((pred.get('meta') or {}).get('baseline') or 'escalar',
                       {'ok': 'cerrar'}.get(answer.get('choice'), answer.get('choice')))
        base = (pred.get('meta') or {}).get('baseline') or 'escalar'
        closed += route == 'cerrar'
        wrong += route == 'cerrar' and truth != 'cerrar'
        base_closed += base == 'cerrar'
        base_wrong += base == 'cerrar' and truth != 'cerrar'
        owner += truth == 'dueno'
        owner_hit += truth == 'dueno' and route == 'dueno'
    total = sum(1 for pred in rows if (by_id.get(pred['id']) or {}).get('ruta'))
    return {'n': total,
            'falso_cierre': round(wrong / closed, 4) if closed else None,
            'falso_cierre_baseline': round(base_wrong / base_closed, 4) if base_closed else None,
            'recall_dueno': round(owner_hit / owner, 4) if owner else None,
            'cobertura': round(closed / total, 4) if total else None,
            'cobertura_baseline': round(base_closed / total, 4) if total else None}


def _read_pred(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def adoption_gate(summary):
    """Spec AC5, literally: what Jev is allowed to do, and why not when it is not."""
    reasons = []

    def check(value, ok, text):
        if value is None:
            reasons.append(f'{text}: sin métrica')
            return False
        if not ok:
            reasons.append(text)
        return ok

    clase = (summary or {}).get('clase') or {}
    riesgo = (summary or {}).get('riesgo') or {}
    conducta = (summary or {}).get('conducta') or {}
    ruta = (summary or {}).get('ruta') or {}
    exactitud = clase.get('exactitud')
    ok_class = check(exactitud, (exactitud or 0) >= GATE['clase_exactitud'],
                     f'exactitud de clase {exactitud} < {GATE["clase_exactitud"]}')
    for label in GATE_RECALL_CLASSES:
        value = (clase.get('recall') or {}).get(label)
        ok_class &= check(value, (value or 0) >= GATE['clase_recall'],
                          f'recall de {label} {value} < {GATE["clase_recall"]}')
    ok_class &= check(riesgo.get('auc'), (riesgo.get('auc') or 0) >= GATE['riesgo_auc'],
                      f'AUC de riesgo {riesgo.get("auc")} < {GATE["riesgo_auc"]}')
    ece_value = riesgo.get('ece')
    ok_class &= check(ece_value, ece_value is not None and ece_value <= GATE['riesgo_ece'],
                      f'ECE de riesgo {ece_value} > {GATE["riesgo_ece"]}')
    ok_class &= check(conducta.get('recall'), (conducta.get('recall') or 0) >= GATE['conducta_recall'],
                      f'recall de conducta {conducta.get("recall")} < {GATE["conducta_recall"]}')
    if not ruta.get('n'):
        # No Opus decisions yet: the router was never piloted, which is not the same as a
        # metric that could not be computed.
        reasons.append('ruta: sin pilotar (no hay decisiones de Opus sobre las que enrutar)')
        return {'jev_class': bool(ok_class), 'jev_route': False, 'razones': reasons}
    false_close, baseline = ruta.get('falso_cierre'), ruta.get('falso_cierre_baseline')
    ok_route = check(false_close, false_close is not None and false_close <= GATE['falso_cierre'],
                     f'falso cierre {false_close} > {GATE["falso_cierre"]}')
    ok_route &= check(baseline, false_close is not None and baseline is not None and false_close <= baseline,
                      f'falso cierre {false_close} peor que el baseline {baseline}')
    ok_route &= check(ruta.get('recall_dueno'), ruta.get('recall_dueno') == GATE['recall_dueno'],
                      f'recall de dueño {ruta.get("recall_dueno")} ≠ {GATE["recall_dueno"]}')
    coverage, coverage_base = ruta.get('cobertura'), ruta.get('cobertura_baseline')
    ok_route &= check(coverage, coverage is not None and coverage_base is not None
                      and coverage >= coverage_base + GATE['cobertura_delta'],
                      f'cobertura {coverage} < baseline {coverage_base} + {GATE["cobertura_delta"]}')
    return {'jev_class': bool(ok_class), 'jev_route': bool(ok_class and ok_route), 'razones': reasons}


def _decide_mode(summary):
    if summary.get('run_decide'):
        return 'sí'
    return 'no (re-puntuación de los pred en disco)' if summary.get('rescore') else 'no (solo exportación)'


def _calibration_md(summary, out_dir):
    gate = adoption_gate(summary)
    lines = ['# Calibración de Jev — spec 063 (AC5)', '',
             f'Gold: {summary["gold"]} unidades resueltas por el dueño · ítems: '
             + ' · '.join(f'{group} {count}' for group, count in summary['items'].items())
             + f' · `decide`: {_decide_mode(summary)}',
             f'Ficheros en `{out_dir}`.', '', '## Distribución del gold', '',
             '| columna | valores |', '|---|---|']
    for column, counts in (summary.get('distribucion') or {}).items():
        lines.append(f'| {column} | ' + ' · '.join(f'{k}: {v}' for k, v in sorted(counts.items())) + ' |')
    clase, riesgo = summary.get('clase') or {}, summary.get('riesgo') or {}
    conducta, ruta = summary.get('conducta') or {}, summary.get('ruta') or {}
    lines += ['', '## Métricas', '', '| métrica | valor | umbral |', '|---|---:|---:|',
              f'| exactitud de clase (n={clase.get("n")}) | {clase.get("exactitud")} | ≥ {GATE["clase_exactitud"]} |']
    for label in RECALL_CLASSES:
        lines.append(f'| recall {label} | {(clase.get("recall") or {}).get(label)} | ≥ {GATE["clase_recall"]} |')
    lines += [f'| AUC riesgo (n={riesgo.get("n")}) | {riesgo.get("auc")} | ≥ {GATE["riesgo_auc"]} |',
              f'| ECE riesgo | {riesgo.get("ece")} | ≤ {GATE["riesgo_ece"]} |',
              f'| recall conducta (n={conducta.get("n")}) | {conducta.get("recall")} | '
              f'≥ {GATE["conducta_recall"]} |',
              f'| falso cierre de ruta (n={ruta.get("n")}) | {ruta.get("falso_cierre")} | '
              f'≤ {GATE["falso_cierre"]} y ≤ baseline {ruta.get("falso_cierre_baseline")} |',
              f'| recall dueño | {ruta.get("recall_dueno")} | = {GATE["recall_dueno"]} |',
              f'| cobertura de cierre | {ruta.get("cobertura")} | ≥ baseline '
              f'{ruta.get("cobertura_baseline")} + {GATE["cobertura_delta"]} |', '',
              '## Lo que estas métricas NO miden', '']
    no_person = clase.get('no_person') or {}
    beyond = conducta.get('mas_alla_del_registro') or {}
    pseudonyms = clase.get('seudonimos') or {}
    lines += [f'- **No-personas fuera de la exactitud**: {no_person.get("n")} de las entidades no son '
              f'`Person` (empresa, organismo, causa) y para ellas `no_aplica` es la respuesta correcta '
              f'por construcción; Jev respondió `no_aplica` {no_person.get("no_aplica_total")} veces '
              f'({no_person.get("no_aplica_pct")} del total). Personas con `no_aplica` (fallo real): '
              + (', '.join(f'`{e}`' for e in no_person.get('personas_con_no_aplica') or []) or 'ninguna') + '.',
              f'- **Familiares**: de las {(clase.get("familiar_flag") or {}).get("n")} entidades A5 del gold, '
              f'el estado que leyó Jev llevaba `flags.familiar_sin_acusacion` en '
              f'{(clase.get("familiar_flag") or {}).get("con_flag")}. El censo 061 muestrea T4 al 10 %, '
              'así que casi no hay aristas `FAMILY_OF` en el ledger y la regla era ciega; `flags()` usa '
              'ahora la lista nominal A5 del finding, pero medir ese recall exige un `decide run` nuevo.',
              f'- **Seudónimos fuera del marcador**: `seudonimo` no es opción de `clase_persona` '
              f'(la identidad nunca se pregunta). De las {pseudonyms.get("n")} entidades seudónimo del gold, '
              f'Jev dijo: {pseudonyms.get("respuestas")}.',
              f'- **Exactitud con «empresario» deducido del rol curado**: '
              f'{clase.get("exactitud_rol_empresario")} — figures.yaml da rol curado también a '
              f'empresarios, así que la regla los llama «figura pública» donde Jev dice «empresario '
              f'con papel propio»; es un desacuerdo de definición, no un error del clasificador.',
              f'- **Proxy de conducta**: {conducta.get("proxy")}; métrica alternativa '
              f'`mas_alla_del_registro` sobre unidades con causa `estado`: '
              f'{beyond.get("recall")} (n={beyond.get("n")}).',
              '- **Riesgo**: el gold de riesgo es una regla (por defecto `medio`), así que AUC y ECE '
              'miden el acuerdo con esa regla, no con el juicio humano de riesgo.', '',
              '## Puerta de adopción', '',
              f'- Jev como **clasificador**: {"PASA" if gate["jev_class"] else "NO pasa"}',
              f'- Jev como **enrutador**: {"PASA" if gate["jev_route"] else "NO pasa"} '
              '(si no pasa, enruta el baseline B y Jev solo endurece)', '']
    lines += [f'- {reason}' for reason in gate['razones']] or ['- sin objeciones']
    return '\n'.join(lines) + '\n'


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description='Jev clasificador/enrutador de la auditoría 063')
    parser.add_argument('--root', required=True, help='raíz de la auditoría (census.sqlite)')
    parser.add_argument('--repo', default=str(REPO))
    parser.add_argument('command', choices=('gold', 'items', 'calibrate'))
    parser.add_argument('--out', help='directorio de salida (por defecto <root>/esc/jev)')
    parser.add_argument('--run-decide', action='store_true', help='lanza decide run/bench (SACA datos del Mac)')
    parser.add_argument('--rescore', action='store_true',
                        help='recalcula el informe con los pred.*.jsonl ya en disco (no llama a nadie)')
    args = parser.parse_args(argv)
    root, repo = Path(args.root), Path(args.repo)
    out_dir = Path(args.out) if args.out else root / 'esc' / 'jev'
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.command == 'gold':
        conn = connect(root)
        try:
            rows = gold(conn, repo)
        finally:
            conn.close()
        _write_jsonl(out_dir / 'gold.jsonl', rows)
        out = {'gold': len(rows), 'out': str(out_dir / 'gold.jsonl'), 'distribucion': _distribution(rows)}
    else:
        out = calibrate(root, repo, out_dir, run_decide=args.run_decide and args.command == 'calibrate',
                        rescore=args.rescore and args.command == 'calibrate')
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
