"""Census of the dual audit (spec 061): the frozen ledger the staged audit (spec 063) runs on.

`census` freezes the bounded universe of units from a seed export into `census.sqlite`;
`lint` joins the 0-token checks of `qa_lint` onto it. The 061 reading loop (two blind readers,
batches, gate, overlay patches) was retired by spec 069 F5 — see git history. Nothing here
touches a graph.

    cd pipeline && .venv/bin/python -m src.qa_dual --root data/audit/dual/<run> lint
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import uuid

from .qa_integral import (TIER_ORDER, canonical, connect, digest, ensure_dual_schema, file_hash, jsonl,
                          leaves, now, segments, write_new)

REPO = Path(__file__).resolve().parents[2]
AUDIT_PARENT = REPO / 'pipeline' / 'data' / 'audit' / 'dual'
# `dual-state.json` keeps the shape the 061 loop wrote: it marks a census root (Makefile `DUAL`).
RESET_SECONDS = 18000
BATCH_GROUPS = 6
SCHEMA_VERSION = 2
DEFAULT_PROFILE = 'normal'

# ── universe (spec 061 §2) ────────────────────────────────────────────────────
PROSE_LABELS = {'Person'}
CONTEXT_LABELS = {'Organization', 'Company', 'LegalCase'}
PROSE_PROPS = ('summary', 'role', 'description', 'note')
# The tiers are a frozen policy of the 061 universe, not a copy of the vocabulary: the census
# of a seed must keep the same units (and ids) as the ledger the 063 already runs on.
T1_RELS = {'ACCUSED_OF', 'CONVICTED_OF', 'INVESTIGATED_BY', 'PARTY_TO', 'ASSERTS', 'DISPUTES'}  # ontologia-ok: tier 061
T2_RELS = {'PAID', 'RECEIVED', 'AWARDED_CONTRACT', 'CONTRACT_AWARDED_TO', 'OWNS', 'CONTROLS'}  # ontologia-ok: tier 061
T4_RELS = {'ASSOCIATED_WITH', 'MET_WITH', 'WORKS_FOR', 'MEMBER_OF', 'PARTICIPATED_IN',  # ontologia-ok: tier 061
           'INVOLVES', 'OCCURRED_AT', 'FAMILY_OF'}  # ontologia-ok: tier 061
TIER_WEIGHT = {'T0': 4.0, 'T1': 3.0, 'T2': 2.0, 'T4': 1.0}
# Rank of an edge inside its tier, inherited from the queue of the 052 audit: reputational and
# monetary types first; a type not listed weighs 0.5.
RISK_WEIGHT = {
    'ACCUSED_OF': 1.0, 'CONVICTED_OF': 1.0,   # presunción de inocencia — highest stakes
    'PARTY_TO': 0.85,                          # parte en causa/organización → cuasi-acusatorio
    'PAID': 0.9, 'RECEIVED': 0.9,
    'OWNS': 0.8, 'CONTROLS': 0.8,
    'AWARDED_CONTRACT': 0.8, 'CONTRACT_AWARDED_TO': 0.8,
    'INVESTIGATED_BY': 0.7,
    # Pase 4 (2026-08-28): tier estructural — tipos sustantivos nunca auditados en
    # los pases 1-3.
    'WORKS_FOR': 0.6, 'MEMBER_OF': 0.6, 'APPOINTED_TO': 0.55,
    'PARTICIPATED_IN': 0.55, 'INVOLVES': 0.55,
    'FAMILY_OF': 0.5, 'SUBSIDIARY_OF': 0.5,
    # Pase 5 (2026-08-31): tier de co-menciones, adjudicado bajo la doctrina de
    # co-menciones del WORKFLOW-qa §Capa 2 (co-aparición ≠ defecto 2).
    'ASSOCIATED_WITH': 0.45, 'MET_WITH': 0.45, 'OCCURRED_AT': 0.4,
}
MIN_NAME_CHARS = 6
# Editorial safeguards, not claims about anybody: they say what the graph does NOT mean.
# Asking a reader to source «su elevado grado refleja centralidad documental» produced
# 26 needs-human in pilot 2 (2026-09-09) over sentences that assert nothing checkable.
DISCLAIMER = re.compile(
    r'presunci[oó]n de inocencia|grado en el grafo|centralidad documental'
    r'|no (?:implica|equivale|supone)[^.;]*(?:responsabilidad|culpabilidad|imputaci[oó]n)'
    r'|rigen? (?:plenamente )?la presunci', re.IGNORECASE)
DISCLAIMER_RESOLUTION = 'salvaguarda-editorial'
# Legal exposure of a sentence, for the order of the human queue.
RISK_VOCAB = re.compile(
    r'condena|condenad|acusad|imputad|procesad|investigad|prisi[oó]n|blanqueo|cohecho'
    r'|malversaci|comisi[oó]n|pag[oó]|cobr|€|euros|millones', re.IGNORECASE)
RISK_ORDER = {'alto': 0, 'medio': 1, 'bajo': 2}
EDITORIAL_FILES = {'feed': 'pipeline/data/ultima_hora/feed.yaml',
                   'cases': 'api/app/data/cases.yaml',
                   'patrones': 'pipeline/data/patrones/patrones.yaml',
                   'briefings': 'sm-vault/_Briefings'}


def utcnow():
    return datetime.now(timezone.utc)


def save_state(root, state):
    state['updatedAt'] = now()
    path = Path(root) / 'dual-state.json'
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, path)
    return state


def initial_state(tiers):
    return {'schemaVersion': SCHEMA_VERSION, 'startedAt': now(), 'updatedAt': now(), 'halted': False,
            'haltReason': None, 'consecutiveFailures': 0, 'firingCount': 0, 'lastError': None,
            'window': {'startedAt': now(), 'resetSeconds': RESET_SECONDS, 'usedJev': 0, 'usedAstra': 0,
                       'pausedUntil': None},
            'tierOrder': tiers, 'cursorTier': tiers[0] if tiers else None,
            'batchGroups': BATCH_GROUPS, 'pairs': 1, 'packetProfile': DEFAULT_PROFILE, 'batches': []}


# ── census ────────────────────────────────────────────────────────────────────

def _object_id(seed_sha, kind, ref):
    return digest([seed_sha, kind, ref])


def _unit_id(seed_sha, oid, kind, field, value):
    return digest([seed_sha, oid, kind, field, value])


def _person_index(nodes):
    """Names long enough to be worth matching inside another entity's prose."""
    names = {}
    for n in nodes:
        if 'Person' not in n.get('labels', []):
            continue
        eid = n['props'].get('entity_id')
        for name in [n['props'].get('name'), *(n['props'].get('aliases') or [])]:
            if isinstance(name, str) and len(name.strip()) >= MIN_NAME_CHARS:
                names[name.strip().casefold()] = eid
    return names


def _names_a_person(text, index):
    low = text.casefold()
    return next((eid for name, eid in index.items() if name in low), None)


def _editorial_units(repo):
    """Editorial layers live in files, not in the graph; each item is one unit."""
    import yaml
    repo = Path(repo)
    out = []

    def add(kind, path, item_id, field, value, props):
        if isinstance(value, str) and value.strip():
            out.append({'layer': kind, 'file': str(Path(path).relative_to(repo)), 'item_id': item_id,
                        'field': field, 'value': value.strip(), 'props': props})

    feed = repo / EDITORIAL_FILES['feed']
    if feed.exists():
        for item in (yaml.safe_load(feed.read_text()) or {}).get('items') or []:
            ident = f"{item.get('iso')}-{digest([item.get('headline')])[:8]}"
            add('feed', feed, ident, '/item', f"{item.get('headline') or ''}\n{item.get('dek') or ''}",
                {'url': item.get('url'), 'outlet': item.get('outlet'), 'entities': item.get('entities')})
    cases = repo / EDITORIAL_FILES['cases']
    if cases.exists():
        for case in (yaml.safe_load(cases.read_text()) or {}).get('cases') or []:
            add('cases', cases, case.get('slug'), '/dek+estado',
                f"{case.get('dek') or ''}\n{case.get('estado') or ''}",
                {'documentos': case.get('documentos'), 'causa': case.get('causa')})
    patrones = repo / EDITORIAL_FILES['patrones']
    if patrones.exists():
        for pattern in (yaml.safe_load(patrones.read_text()) or {}).get('patrones') or []:
            add('patrones', patrones, pattern.get('slug'), '/tesis', pattern.get('tesis') or '',
                {'fuentes': pattern.get('fuentes')})
    briefings = repo / EDITORIAL_FILES['briefings']
    for note in sorted(briefings.glob('*.md')) if briefings.exists() else []:
        body = note.read_text()
        for i, section in enumerate(re.split(r'\n(?=## )', body)):
            if section.strip():
                add('briefings', note, f'{note.stem}#{i}', f'/seccion@{i}', section, {})
    return out


def census(root, snapshot, seed, repo=REPO, presentation=None, editorial=False, t4_sample=0):
    """Bounded, frozen universe. Unit ids match the 060 census for the same seed."""
    root, snapshot, repo = Path(root), Path(snapshot), Path(repo)
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'manifest.json').exists():
        raise ValueError('census already exists; never overwrite a frozen universe')
    if not re.fullmatch('[a-f0-9]{64}', seed.get('sha256', '')) or not seed.get('version'):
        raise ValueError('seed version and full SHA-256 required')
    completion = json.loads((snapshot / 'snapshot.json').read_text())
    for name in ('nodes', 'relationships'):
        if completion[name]['sha256'] != file_hash(snapshot / f'{name}.jsonl'):
            raise ValueError('altered snapshot export')
    presentation = presentation or {}
    seed_sha = seed['sha256']
    nodes = list(jsonl(snapshot / 'nodes.jsonl'))
    rels = list(jsonl(snapshot / 'relationships.jsonl'))
    if completion['nodes']['count'] != len(nodes) or completion['relationships']['count'] != len(rels):
        raise ValueError('incomplete snapshot export')
    by_ref = {n['ref']: n for n in nodes}
    if any(r['start'] not in by_ref or r['end'] not in by_ref for r in rels):
        raise ValueError('snapshot has dangling relationship endpoints')
    persons = _person_index(nodes)
    db = connect(root)
    db.executescript('''
    CREATE TABLE objects (object_id TEXT PRIMARY KEY, object_kind TEXT NOT NULL,
      ref TEXT NOT NULL, entity TEXT, type TEXT NOT NULL, provenance TEXT NOT NULL,
      technical INTEGER NOT NULL, content_hash TEXT NOT NULL, payload TEXT NOT NULL,
      UNIQUE(object_kind, ref));
    CREATE TABLE units (unit_id TEXT PRIMARY KEY, object_id TEXT NOT NULL REFERENCES objects,
      kind TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL, technical INTEGER NOT NULL,
      risk INTEGER NOT NULL, prior TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
      resolution TEXT, UNIQUE(object_id,kind,field));
    CREATE TABLE batches (batch_id TEXT PRIMARY KEY, checksum TEXT NOT NULL, path TEXT NOT NULL);
    CREATE TABLE assignments (unit_id TEXT NOT NULL REFERENCES units, batch_id TEXT NOT NULL REFERENCES batches,
      PRIMARY KEY(unit_id));
    CREATE TABLE readings (unit_id TEXT NOT NULL REFERENCES units, side TEXT NOT NULL,
      attempt_id TEXT NOT NULL UNIQUE, result TEXT NOT NULL, receipt TEXT NOT NULL,
      PRIMARY KEY(unit_id,side));
    CREATE TABLE events (seq INTEGER PRIMARY KEY, at TEXT NOT NULL, unit_id TEXT, event TEXT NOT NULL, payload TEXT NOT NULL);
    ''')
    ensure_dual_schema(db)
    unit_ids, counts = [], Counter()

    def add_object(kind, ref, entity, typ, provenance, payload, content):
        oid = _object_id(seed_sha, kind, ref)
        db.execute('INSERT OR IGNORE INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   (oid, kind, ref, entity, typ, provenance, 0, digest(content), canonical(payload)))
        return oid

    def add_unit(oid, kind, field, value, tier, risk, rank, technical=0, state='pending', resolution=None):
        uid = _unit_id(seed_sha, oid, kind, field, value)
        db.execute('INSERT OR IGNORE INTO units(unit_id,object_id,kind,field,value,technical,risk,prior,'
                   'state,resolution) VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (uid, oid, kind, field, canonical(value), technical, risk, '[]', state, resolution))
        db.execute('INSERT OR IGNORE INTO tiers VALUES (?,?,?)', (uid, tier, round(rank, 4)))
        unit_ids.append(uid)
        counts[tier] += 1
        return uid

    def prose_units(node, oid, labels, degree, context):
        for prop in PROSE_PROPS:
            value = node['props'].get(prop)
            if not isinstance(value, str) or not value.strip():
                continue
            for subpath, leaf in leaves(value):
                for start, end, span in segments(leaf):
                    if context and not _names_a_person(span, persons):
                        continue
                    kind = 'summary' if prop == 'summary' else 'attribute'
                    field = '/' + prop + subpath + (f'@{start}:{end}' if end else '')
                    weight = 1.0 if prop == 'summary' else 0.9
                    safeguard = bool(DISCLAIMER.search(span))
                    add_unit(oid, kind, field, span, 'T0', 100,
                             TIER_WEIGHT['T0'] * weight * math.log1p(degree),
                             technical=int(safeguard), state='verified' if safeguard else 'pending',
                             resolution=DISCLAIMER_RESOLUTION if safeguard else None)

    for node in nodes:
        labels = set(node.get('labels', []))
        props = node['props']
        typ = '|'.join(sorted(labels))
        entity = props.get('entity_id') or props.get('claim_id')
        if not (labels & (PROSE_LABELS | CONTEXT_LABELS | {'Claim'})):
            continue
        degree = props.get('degree') or 0
        payload = dict(node)
        payload['props'] = {k: v for k, v in props.items() if k != 'embedding'}
        oid = add_object('node', node['ref'], entity, typ, props.get('source_kind') or 'unspecified',
                         payload, [sorted(labels), props])
        identity = {k: props[k] for k in ('name', 'aliases', 'entity_id', 'claim_id', 'text',
                                          'is_pseudonym', 'anonymized') if k in props}
        if 'Claim' in labels:
            add_unit(oid, 'claim', '', {'type': typ, 'identity': identity}, 'T1', 100,
                     TIER_WEIGHT['T1'] * math.log1p(degree))
            continue
        if labels & PROSE_LABELS:
            add_unit(oid, 'identity', '', {'type': typ, 'identity': identity}, 'T0', 100,
                     TIER_WEIGHT['T0'] * math.log1p(degree))
        prose_units(node, oid, labels, degree, context=not (labels & PROSE_LABELS))

    for rel in rels:
        typ = rel['type']
        tier = ('T1' if typ in T1_RELS else 'T2' if typ in T2_RELS else 'T4' if typ in T4_RELS else None)
        if tier is None:
            continue
        start, end = by_ref[rel['start']], by_ref[rel['end']]
        person_endpoint = 'Person' in start.get('labels', []) or 'Person' in end.get('labels', [])
        if tier == 'T4' and not person_endpoint:
            continue
        props = rel['props']
        payload = dict(rel)
        payload.update({'start_entity': start['props'].get('entity_id'), 'end_entity': end['props'].get('entity_id'),
                        'start_name': start['props'].get('name'), 'end_name': end['props'].get('name')})
        oid = _object_id(seed_sha, 'relationship', rel['ref'])
        value = {'type': typ, 'start': payload['start_entity'], 'end': payload['end_entity'],
                 'props': props, 'public_label': presentation.get(typ)}
        uid = _unit_id(seed_sha, oid, 'edge', '', value)
        # Deterministic 10% sample of the associative tier already closed in seven passes.
        if tier == 'T4' and int(digest([uid])[:8], 16) % 100 >= t4_sample:
            continue
        add_object('relationship', rel['ref'], payload['start_entity'], typ,
                   props.get('source_kind') or 'unspecified', payload, [typ, digest(start), digest(end), props])
        degree = max(start['props'].get('degree') or 0, end['props'].get('degree') or 0)
        add_unit(oid, 'edge', '', value, tier, 100,
                 TIER_WEIGHT[tier] * RISK_WEIGHT.get(typ, 0.5) * math.log1p(degree))

    editorial_rows = _editorial_units(repo) if editorial else []
    for item in editorial_rows:
        ref = f"{item['file']}#{item['item_id']}"
        oid = add_object('editorial', ref, item['item_id'], item['layer'], 'editorial',
                         {'file': item['file'], 'item_id': item['item_id'], 'props': item['props']},
                         [item['layer'], item['file'], item['item_id'], item['value']])
        add_unit(oid, 'editorial', item['field'], item['value'], 'T0', 100, TIER_WEIGHT['T0'] * 1.0)

    # Evidence locators for the packet builder; never audited units.
    for node in nodes:
        props = node['props']
        if 'Chunk' in node.get('labels', []) and props.get('chunk_id'):
            db.execute('INSERT OR IGNORE INTO chunks VALUES (?,?,?,?,?,?)',
                       (props['chunk_id'], props.get('doc_id'), props.get('page_start'),
                        props.get('page_end'), props.get('chunk_index'), props.get('text') or ''))
    for rel in rels:
        if rel['type'] == 'MENTIONED_IN':
            eid = by_ref[rel['start']]['props'].get('entity_id')
            cid = by_ref[rel['end']]['props'].get('chunk_id')
            if eid and cid:
                db.execute('INSERT OR IGNORE INTO mentions VALUES (?,?)', (eid, cid))
    db.commit()
    db.close()
    present = [t for t in TIER_ORDER if counts.get(t)]
    manifest = {'schema': SCHEMA_VERSION, 'spec': '061', 'created_at': now(), 'seed': seed,
                'snapshot_path': str(snapshot.resolve()),
                'snapshots': {k: file_hash(snapshot / f'{k}.jsonl') for k in ('nodes', 'relationships')},
                'presentation': presentation, 'payload_excludes': ['embedding'],
                'editorial': sorted({item['file'] for item in editorial_rows}),
                'editorial_hashes': {item['file']: file_hash(repo / item['file'])
                                     for item in editorial_rows},
                't4_sample_pct': t4_sample, 'tiers': dict(counts), 'units': len(unit_ids),
                'universe_sha256': digest(sorted(unit_ids)),
                'note': 'Bounded universe: only units that can harm an identifiable person. '
                        'Chunk/Document, embeddings and derived properties are out of scope by design.'}
    write_new(root / 'manifest.json', manifest)
    save_state(root, initial_state(present))
    return manifest


def reclassify_disclaimers(root):
    """Close editorial safeguards in place, idempotently.

    NOTE: `kind` is part of the unit id (`digest([seed, oid, kind, field, value])`), so a
    literal `kind='disclaimer'` would rename the unit and break `verify_census` on a live
    census. The marking therefore travels where it costs nothing: technical=1 (never
    exported), state `verified`, resolution `salvaguarda-editorial`.
    """
    root = Path(root)
    db = connect(root)
    counts = Counter()
    try:
        rows = list(db.execute("SELECT unit_id,value,state FROM units WHERE kind IN ('summary','attribute')"))
        pending, already = [], 0
        for row in rows:
            value = json.loads(row['value'])
            if not isinstance(value, str) or not DISCLAIMER.search(value):
                continue
            counts['matched'] += 1
            if row['state'] == 'verified':
                already += 1
            elif row['state'] == 'pending':
                pending.append(row['unit_id'])
            else:
                # Something already judged it; a safeguard rule never overrides a verdict.
                counts['skipped-already-judged'] += 1
        with db:
            db.executemany("UPDATE units SET technical=1,state='verified',resolution=? WHERE unit_id=?",
                           [(DISCLAIMER_RESOLUTION, uid) for uid in pending])
            db.execute('INSERT INTO events(at,unit_id,event,payload) VALUES (?,?,?,?)',
                       (now(), None, 'disclaimers-reclassified',
                        canonical({'closed': len(pending), 'already': already})))
        counts['closed'] = len(pending)
        counts['already-closed'] = already
    finally:
        db.close()
    return {'root': str(root), 'resolution': DISCLAIMER_RESOLUTION, 'counts': dict(counts)}


def verify_census(root):
    """Bounded twin of qa_integral.verify_census: ids and denominator, not the full export."""
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    db = connect(root)
    try:
        ids = []
        for u in db.execute('SELECT * FROM units'):
            expected = _unit_id(manifest['seed']['sha256'], u['object_id'], u['kind'], u['field'],
                                json.loads(u['value']))
            if u['unit_id'] != expected:
                raise ValueError('altered census unit')
            ids.append(u['unit_id'])
        if digest(sorted(ids)) != manifest['universe_sha256']:
            raise ValueError('altered census denominator')
        for name in ('nodes', 'relationships'):
            if file_hash(Path(manifest['snapshot_path']) / f'{name}.jsonl') != manifest['snapshots'][name]:
                raise ValueError('altered source snapshot')
    finally:
        db.close()
    return True


# ── lint ──────────────────────────────────────────────────────────────────────

def lint(root, snapshot=None, repo=REPO, network=False):
    """L1-L21 live in qa_lint (0 tokens); this joins its rows back onto the census."""
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    snapshot = Path(snapshot or manifest['snapshot_path'])
    out = root / 'lint'
    try:
        from . import qa_lint
    except ImportError:
        return {'status': 'unavailable', 'run': f'make qa-lint SNAPSHOT={snapshot} OUT={out}',
                'note': 'src.qa_lint is not importable yet; the checks are 0-token and independent.'}
    summary = qa_lint.run(snapshot, out, utcnow().date(), network=network, repo=Path(repo))
    seed_sha = manifest['seed']['sha256']
    db = connect(root)
    marked = Counter()
    try:
        with db:
            for path in sorted(out.glob('L*.jsonl')):
                for line in path.read_text().splitlines():
                    row = json.loads(line)
                    # Lint speaks physical locators; the census speaks unit ids.
                    oid = _object_id(seed_sha, row['object_kind'], row['ref'])
                    units = [r['unit_id'] for r in db.execute(
                        'SELECT unit_id,field FROM units WHERE object_id=?', (oid,))
                        if not row.get('field') or r['field'] in ('', '/' + row['field'])
                        or r['field'].startswith('/' + row['field'] + '@')]
                    for uid in units:
                        db.execute('INSERT OR REPLACE INTO lint_checks VALUES (?,?,?)',
                                   (uid, row['check'], canonical(row)))
                        marked[row['check']] += 1
    finally:
        db.close()
    return {'lint': out and str(out), 'checks': {k: v['count'] for k, v in summary.items()},
            'units_marked': dict(marked)}


# ── shared with the staged audit (spec 063) ───────────────────────────────────

def risk_class(unit):
    """What a wrong sentence would cost. Order of the human queue, nothing else."""
    value = json.loads(unit['value'])
    text = value if isinstance(value, str) else canonical(value)
    if RISK_VOCAB.search(text):
        return 'alto'
    if 'Person' in (unit['type'] or '') and unit['kind'] in {'summary', 'attribute', 'identity'}:
        return 'medio'
    return 'bajo'


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError) as exc:
        return isinstance(exc, PermissionError)
    return True


# ── CLI ───────────────────────────────────────────────────────────────────────

def default_root():
    # dual-state.json, not manifest.json: the lint output also writes a manifest.
    dirs = sorted((d for d in AUDIT_PARENT.glob('*') if (d / 'dual-state.json').exists()), reverse=True)
    if not dirs:
        raise ValueError(f'no dual audit under {AUDIT_PARENT}; run census first')
    return dirs[0]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path)
    p.add_argument('--repo', type=Path, default=REPO)
    s = p.add_subparsers(dest='cmd', required=True)
    c = s.add_parser('census')
    c.add_argument('--snapshot', type=Path, required=True)
    c.add_argument('--seed', type=Path, required=True)
    c.add_argument('--presentation', type=Path)
    c.add_argument('--editorial', action='store_true')
    c.add_argument('--t4-sample', type=int, default=0)
    li = s.add_parser('lint')
    li.add_argument('--snapshot', type=Path)
    li.add_argument('--network', action='store_true')
    for name in ('verify-census', 'reclassify-disclaimers'):
        s.add_parser(name)
    a = p.parse_args()
    root = a.root or (None if a.cmd == 'census' else default_root())
    if a.cmd == 'census':
        result = census(a.root or AUDIT_PARENT / f"{utcnow():%Y%m%d}-{json.loads(a.seed.read_text())['version']}",
                        a.snapshot, json.loads(a.seed.read_text()), a.repo,
                        json.loads(a.presentation.read_text()) if a.presentation else {},
                        a.editorial, a.t4_sample)
    elif a.cmd == 'lint':
        result = lint(root, a.snapshot, a.repo, a.network)
    elif a.cmd == 'verify-census':
        result = verify_census(root)
    else:
        result = reclassify_disclaimers(root)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
