"""Packs por entidad de la auditoría escalonada (spec 063) y su ledger aditivo.

Una entidad entera —su texto vigente, su identidad, sus aristas y sus ítems editoriales—
viaja en UN pack (o en sub-packs de ≤12 unidades) con las fuentes que la prueban. El
manifest JSON es lo que se hashea (`pack_sha256`); el markdown es lo único que ve el
lector. Nada de esto toca el grafo: los hechos de la entidad salen del ledger de la 061
(`objects.payload`) y de los YAML versionados, nunca de Neo4j.

    python -m src.qa_esc_packs --root <audit> --repo . queue --scope pending --limit 5
    python -m src.qa_esc_packs --root <audit> --repo . pack --entity Person:koldo-garcia
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import uuid

from .ontologia import ontologia
from .qa_dual import RISK_ORDER, risk_class
from .qa_dual_packets import (ACCUSATIONS_YAML, AMPLIADO_LIMITS, FEED_YAML, TextPacketBuilder,
                              _vigencia_text, anchor_terms, group_key, load_vigencia)
from .qa_integral import (TIER_ORDER, canonical, connect, digest, file_hash, jsonl, now, write_new)

REPO = Path(__file__).resolve().parents[2]
ESC_DIR = 'esc'
WORKTREE = 'worktree'
SCOPES = ('pending', 'verified-T0')
# Un pack por entidad; los hubs (Ábalos, 136 unidades) se trocean y solo el primero
# devuelve las acciones de entidad (validator de `qa_esc_schema`).
MAX_PACK_UNITS = 12
MAX_EDITORIAL_UNITS = 6
OVERLAY_SUMMARIES = 'pipeline/data/context/summaries_qa061.yaml'
OVERLAY_ROLES = 'pipeline/data/context/roles_qa061.yaml'
FIGURES_YAML = 'pipeline/data/context/figures.yaml'
PSEUDONYMS_YAML = 'pipeline/data/context/pseudonyms.yaml'
ACCUSATORY_RELS = set(ontologia().procesales) | {'PARTY_TO'}
# Structural facts (family, edge counts, statuses) come from the frozen seed export, never
# from `objects`: the 061 census kept a 10 % sample of T4 (`t4_sample_pct`), so the ledger
# holds 6 of the graph's 60 FAMILY_OF edges and the A5 rule read from it is blind.
GRAPH_INDEX = 'graph-edges.sqlite'
INDEX_SKIP_RELS = {'MENTIONED_IN', 'PART_OF'}
EDGE_PROPS = ('source_kind', 'status', 'firme', 'amount', 'currency', 'date', 'source_doc', 'source_page')
GRAPH_SCHEMA = '''
CREATE TABLE IF NOT EXISTS graph_edges (ref TEXT PRIMARY KEY, type TEXT NOT NULL, start_entity TEXT,
  end_entity TEXT, start_name TEXT, end_name TEXT, source_kind TEXT, status TEXT, firme TEXT,
  amount TEXT, currency TEXT, date TEXT, source_doc TEXT, source_page TEXT);
CREATE TABLE IF NOT EXISTS graph_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS graph_edges_by_start ON graph_edges(start_entity);
CREATE INDEX IF NOT EXISTS graph_edges_by_end ON graph_edges(end_entity);
'''
ASSOCIATIVE_RELS = {'MET_WITH', 'ASSOCIATED_WITH', 'WORKS_FOR'}  # ontologia-ok: política de packs 063
PROSE_KINDS = {'summary', 'attribute', 'identity', 'claim'}
JUDICIAL_ROLE = re.compile(r'juez|jueza|magistrad|fiscal|instructor|letrad[oa] de la administraci', re.I)
W_PSEUDONYM = 'SEUDÓNIMO JUDICIAL (D5)'
W_A5 = 'FAMILIAR SIN ACUSACIÓN (A5)'
W_ANONYMIZED = 'ANONIMIZADO'
TOOL_LINE = ("Herramienta: python3 {ev} [-n 6] [-w 300] [--doc SUBSTR] [--nosrc] [--nocorpus] 'regex' ['regex' …]"
             ' — busca en el corpus y en las fuentes archivadas. Máximo 8 búsquedas por pack;'
             ' más de 12, o cualquier otro comando, anula tu lectura.')
BLIND_LINE = ('Lector ciego: sin herramienta. La ausencia de prueba en una fuente INCOMPLETA no es prueba de'
              ' ausencia: responde `incierto` y nombra en `requested_sources` qué buscar y dónde.')

ESC_SCHEMA = '''
CREATE TABLE IF NOT EXISTS esc_packs (pack_id TEXT PRIMARY KEY, entity TEXT NOT NULL, kind TEXT NOT NULL,
  idx INTEGER NOT NULL, total INTEGER NOT NULL, unit_ids TEXT NOT NULL, pack_path TEXT,
  pack_sha256 TEXT, overlay_ref TEXT, riesgo TEXT, clase TEXT, route TEXT, state TEXT NOT NULL,
  batch TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS esc_readings (attempt_id TEXT PRIMARY KEY, pack_id TEXT NOT NULL REFERENCES esc_packs,
  side TEXT NOT NULL, result TEXT, receipt TEXT NOT NULL, at TEXT NOT NULL, UNIQUE(pack_id, side));
CREATE INDEX IF NOT EXISTS esc_packs_by_entity ON esc_packs(entity, kind);
CREATE INDEX IF NOT EXISTS esc_packs_by_state ON esc_packs(state);
'''


def ensure_esc_schema(conn):
    """Additive tables for the escalonada loop; the 061 ledger is untouched."""
    conn.executescript(ESC_SCHEMA)
    conn.commit()
    return True


def root_of(conn):
    """The audit root of an open census connection (the packs live next to it)."""
    for _, name, filename in conn.execute('PRAGMA database_list'):
        if name == 'main' and filename:
            return Path(filename).parent
    raise ValueError('census connection without a file on disk')


# ── YAML sources (worktree or a frozen git ref, for the T0e replay) ───────────
_CACHE = {}


def _stamp(path):
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def read_yaml(repo, relpath, ref=WORKTREE):
    """The YAML as of `ref` (`git show <ref>:<relpath>`) or as it sits in the worktree."""
    import yaml
    if ref and ref != WORKTREE:
        key = ('git', str(repo), ref, relpath)
        if key not in _CACHE:
            out = subprocess.run(['git', '-C', str(repo), 'show', f'{ref}:{relpath}'],
                                 capture_output=True, text=True)
            _CACHE[key] = (yaml.safe_load(out.stdout) or {}) if out.returncode == 0 else {}
        return _CACHE[key]
    path = Path(repo) / relpath
    key = ('file', str(path), _stamp(path))
    if key[2] is None:
        return {}
    if key not in _CACHE:
        _CACHE[key] = yaml.safe_load(path.read_text()) or {}
    return _CACHE[key]


def load_overlays(repo, overlay_ref=WORKTREE):
    """Rows of `summaries_qa061` (by entity_id) and `roles_qa061` (by entity_id and by name)."""
    summaries, roles = {}, {}
    for row in (read_yaml(repo, OVERLAY_SUMMARIES, overlay_ref).get('summaries') or []):
        if isinstance(row, dict) and row.get('entity_id'):
            summaries.setdefault(row['entity_id'], row)
    for row in (read_yaml(repo, OVERLAY_ROLES, overlay_ref).get('entities') or []):
        if isinstance(row, dict):
            for key in (row.get('entity_id'), row.get('name')):
                if key:
                    roles.setdefault(key, row)
    return summaries, roles


def load_context(repo):
    """Curated context: `figures.yaml` roles (by entity_id and name) and the pseudonym roster."""
    figures, pseudonyms = {}, {}
    data = read_yaml(repo, FIGURES_YAML)
    for row in ((data.get('entities') if isinstance(data, dict) else None) or []):
        if isinstance(row, dict):
            for key in (row.get('entity_id'), row.get('name')):
                if key:
                    figures.setdefault(key, row)
    for row in (read_yaml(repo, PSEUDONYMS_YAML).get('pseudonyms') or []):
        if isinstance(row, dict) and row.get('id'):
            pseudonyms[row['id']] = row
    return figures, pseudonyms


def vigencia_of(repo):
    """`load_vigencia` with the same mtime cache as the rest of the YAML."""
    key = ('vigencia', str(repo), _stamp(Path(repo) / ACCUSATIONS_YAML), _stamp(Path(repo) / FEED_YAML))
    if key not in _CACHE:
        _CACHE[key] = load_vigencia(repo)
    return _CACHE[key]


# ── structural facts: the entity's edges, from the frozen seed export ─────────

def snapshot_of(root):
    """The export the census froze (`manifest.snapshot_path`) and its stamp, or `(None, None)`."""
    manifest = Path(root) / 'manifest.json'
    key = ('snapshot', str(manifest), _stamp(manifest))
    if key[2] is None:
        return None, None
    if key not in _CACHE:
        data = json.loads(manifest.read_text())
        path = Path(data.get('snapshot_path') or '')
        usable = all((path / f'{name}.jsonl').exists() for name in ('nodes', 'relationships'))
        _CACHE[key] = ((path, digest([data.get('snapshots') or {}, str(path)])) if usable else (None, None))
    return _CACHE[key]


def _node_entities(path):
    """ref → (entity_id, name). A Chunk node carries the embedding and no `entity_id`: skipping
    it before parsing is what keeps 128 MB of nodes under a few seconds."""
    index = {}
    with Path(path).open(encoding='utf-8') as handle:
        for line in handle:
            if '"entity_id"' not in line:
                continue
            node = json.loads(line)
            props = node.get('props') or {}
            if props.get('entity_id') and node.get('ref'):
                index[node['ref']] = (props['entity_id'], props.get('name') or props['entity_id'])
    return index


def build_graph_index(root, snapshot, stamp):
    """Index every domain edge of the export in `esc/graph-edges.sqlite` (props travel as text).

    `None` when the export no longer hashes to what the census froze: an altered snapshot is
    never trusted, and the (sampled) ledger answers instead.
    """
    hashes = (json.loads((Path(root) / 'manifest.json').read_text()).get('snapshots') or {})
    for name in ('nodes', 'relationships'):
        if hashes.get(name) and hashes[name] != file_hash(Path(snapshot) / f'{name}.jsonl'):
            return None
    nodes = _node_entities(Path(snapshot) / 'nodes.jsonl')
    rows = []
    for rel in jsonl(Path(snapshot) / 'relationships.jsonl'):
        if rel.get('type') in INDEX_SKIP_RELS:
            continue
        start, end = nodes.get(rel.get('start')), nodes.get(rel.get('end'))
        if not start and not end:
            continue
        props = rel.get('props') or {}
        rows.append((rel.get('ref'), rel.get('type'), start[0] if start else None, end[0] if end else None,
                     start[1] if start else '', end[1] if end else '',
                     *(None if props.get(key) is None else str(props[key]) for key in EDGE_PROPS)))
    path = Path(root) / ESC_DIR / GRAPH_INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    db = sqlite3.connect(temporary)
    try:
        db.executescript(GRAPH_SCHEMA)
        db.executemany('INSERT OR REPLACE INTO graph_edges VALUES (%s)' % ','.join('?' * 14), rows)
        db.executemany('INSERT OR REPLACE INTO graph_meta VALUES (?,?)',
                       (('stamp', stamp), ('edges', str(len(rows))), ('built_at', now())))
        db.commit()
    finally:
        db.close()
    os.replace(temporary, path)
    return path


def graph_index(root, rebuild=False):
    """Path of this audit's edge index, built on first use and reused while the export's
    hashes hold; `None` when there is no usable export (then the ledger answers)."""
    snapshot, stamp = snapshot_of(root)
    if not snapshot:
        return None
    path = Path(root) / ESC_DIR / GRAPH_INDEX
    if path.exists() and not rebuild:
        db = sqlite3.connect(path)
        try:
            row = db.execute("SELECT value FROM graph_meta WHERE key='stamp'").fetchone()
        except sqlite3.DatabaseError:
            row = None
        finally:
            db.close()
        if row and row[0] == stamp:
            return path
    return build_graph_index(root, snapshot, stamp)


def _entity_edges(conn, entity_id):
    """Every domain edge with this entity at either end, from the export's index.

    NOT from `objects`: the census sampled T4 (`t4_sample_pct` 10 %), so the ledger holds 6 of
    the graph's 60 FAMILY_OF edges and every rule read from it (A5, `solo_mencionado`,
    `edge_counts`) would be structurally blind. The ledger is the documented fallback when the
    export is unreachable — a SAMPLED subset, and the caller must know it.
    """
    index = graph_index(root_of(conn))
    if index:
        db = sqlite3.connect(index)
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute('SELECT * FROM graph_edges WHERE start_entity=? OR end_entity=?',
                              (entity_id, entity_id)).fetchall()
        finally:
            db.close()
        return [{'type': row['type'], 'start_entity': row['start_entity'], 'end_entity': row['end_entity'],
                 'start_name': row['start_name'], 'end_name': row['end_name'],
                 'props': {key: row[key] for key in EDGE_PROPS if row[key] is not None}}
                for row in rows]
    rows = conn.execute("SELECT payload FROM objects WHERE object_kind='relationship' "
                        'AND (entity=? OR payload LIKE ?)',
                        (entity_id, f'%"end_entity":"{entity_id}"%'))
    return [json.loads(r['payload']) for r in rows]


def entity_facts(conn, repo, entity_id):
    """What the rules know about an entity before anybody reads it.

    Imported by `qa_esc_jev.flags`: the keys are contract (CONTRACTS.md §Jev). The edges come from the
    frozen export (`graph_index`), everything else from the ledger and the YAML. `estado_row`
    is the WHOLE `accusations.yaml` row (it carries `estado` inside), as `_vigencia_text`
    takes it.
    """
    node = conn.execute("SELECT type,payload FROM objects WHERE object_kind='node' AND entity=?",
                        (entity_id,)).fetchone()
    props = (json.loads(node['payload']).get('props') or {}) if node else {}
    figures, pseudonyms = load_context(repo)
    registry, feed = vigencia_of(repo)
    name = props.get('name') or entity_id
    figure = figures.get(entity_id) or figures.get(name) or {}
    pseudonym = pseudonyms.get(entity_id) or {}
    counts, family, types = defaultdict(lambda: defaultdict(Counter)), [], set()
    accused_null = 0
    for rel in _entity_edges(conn, entity_id):
        rel_props = rel.get('props') or {}
        typ = rel.get('type') or ''
        types.add(typ)
        counts[typ][rel_props.get('source_kind') or 'unspecified'][str(rel_props.get('status') or '')] += 1
        if typ == 'FAMILY_OF':
            other = rel.get('end_entity') if rel.get('start_entity') == entity_id else rel.get('start_entity')
            if other and other != entity_id and other not in family:
                family.append(other)
        if typ == 'ACCUSED_OF' and not str(rel_props.get('status') or '').strip():
            accused_null += 1
    estado_row = registry.get(entity_id)
    return {'name': name,
            'type': (node['type'] if node else entity_id.split(':')[0]),
            'aliases': [a for a in (props.get('aliases') or []) if isinstance(a, str)],
            'is_pseudonym': bool(props.get('is_pseudonym') or pseudonym),
            'pseudonym_note': props.get('pseudonym_note') or pseudonym.get('note') or '',
            'anonymized': bool(props.get('anonymized')),
            'degree': int(props.get('degree') or 0),
            'context_role': figure.get('role') or '',
            'is_boe': bool(props.get('is_boe')),
            'family_of': family,
            'estado_row': estado_row,
            'feed_items': [{'iso': str(it.get('iso') or it.get('date') or ''),
                            'headline': str(it.get('headline') or ''),
                            'outlet': str(it.get('outlet') or ''),
                            'url': str(it.get('url') or '')}
                           for it in (feed.get(entity_id) or [])],
            'accused_status_null': accused_null,
            'edge_counts': {typ: {kind: dict(status) for kind, status in sorted(by_kind.items())}
                            for typ, by_kind in sorted(counts.items())},
            'only_associative': not (types - ASSOCIATIVE_RELS),
            # A5 (auditoría de riesgo legal 2026-09-05): familiar sin acusación NI cargo público. El
            # cargo se mide por la trayectoria BOE (Pedro Sánchez, is_boe) y NO por tener fila en
            # figures.yaml: los familiares están ahí precisamente para identificarlos («esposa de…»,
            # María Sonsoles Espinosa), y excluirlos por eso los dejaba fuera del A5. Incluir de más
            # es seguro: A5 solo manda la decisión a la tanda del dueño.
            'a5_familiar': bool('FAMILY_OF' in types and not (types & ACCUSATORY_RELS)
                                and not estado_row and not props.get('is_boe'))}


def rule_flags(facts):
    """The flags a rule already knows, so Jev is never asked about them (spec AC4)."""
    types = set(facts['edge_counts'])
    return {'figura_publica': bool(facts['context_role'] or facts['is_boe']),
            'investigado_encausado': bool(facts['estado_row'] or (types & ACCUSATORY_RELS)),
            'seudonimo': facts['is_pseudonym'],
            'anonimizado': facts['anonymized'],
            'familiar_sin_acusacion': facts['a5_familiar'],
            'solo_mencionado': facts['only_associative'],
            'autoridad_judicial': bool(JUDICIAL_ROLE.search(facts['context_role'] or '')),
            'accused_status_null': facts['accused_status_null']}


# ── queue ─────────────────────────────────────────────────────────────────────

_SELECT = '''SELECT u.unit_id,u.kind,u.field,u.value,u.state,u.resolution,u.technical,
    t.tier,t.rank,o.object_id,o.object_kind,o.entity,o.type,o.provenance,o.payload
    FROM units u JOIN tiers t USING(unit_id) JOIN objects o USING(object_id) WHERE {} ORDER BY u.unit_id'''
_SCOPE_WHERE = {'pending': ("u.state='pending' AND t.tier IN ('T0','T1','T2','T4')", ()),
                'verified-T0': ("u.state='verified' AND u.technical=0 AND t.tier='T0'", ())}


def _rows(conn, where, params=()):
    out = []
    for row in conn.execute(_SELECT.format(where), params):
        row = dict(row)
        row['_payload'] = json.loads(row['payload'])
        row['_value'] = json.loads(row['value'])
        row['riesgo'] = risk_class(row)
        out.append(row)
    return out


def pack_entity(row, payload):
    """Which pack a unit belongs to: (entity key, kind).

    An edge belongs to its Person endpoint (the one a wrong edge would harm); an editorial
    item to its principal entity (feed: the first `entities`; briefing: its file; cases and
    patrones: their slug).
    """
    if row['object_kind'] == 'editorial':
        props = payload.get('props') or {}
        if row['type'] == 'feed':
            entities = props.get('entities')
            first = next((e for e in entities if isinstance(e, str) and e.strip()), None) \
                if isinstance(entities, list) else None
            return (first or payload.get('item_id') or row['entity'], 'editorial')
        if row['type'] == 'briefings':
            return (payload.get('file') or row['entity'], 'editorial')
        return (payload.get('item_id') or row['entity'], 'editorial')
    if row['object_kind'] == 'relationship':
        start, end = payload.get('start_entity'), payload.get('end_entity')
        if isinstance(start, str) and start.startswith('Person:'):
            return (start, 'entity')
        if isinstance(end, str) and end.startswith('Person:'):
            return (end, 'entity')
        return (start or end or row['entity'], 'entity')
    return (row['entity'], 'entity')


def _unit_order(row):
    """T0 → T1 → T2 → T4, and inside a tier the edges of one `doc:página` together."""
    tier = row['tier'] if row['tier'] in TIER_ORDER else 'T4'
    return (TIER_ORDER.index(tier), group_key(row, row['_payload']), row['kind'],
            row['field'] or '', row['unit_id'])


def entity_queue(conn, scope='pending'):
    """Entities to read, worst first: risk alto→medio→bajo, then the biggest, then the id."""
    if scope not in _SCOPE_WHERE:
        raise ValueError(f'unknown scope {scope!r}')
    where, params = _SCOPE_WHERE[scope]
    buckets = defaultdict(list)
    for row in _rows(conn, where, params):
        buckets[pack_entity(row, row['_payload'])].append(row)
    queue = []
    for (entity, kind), rows in buckets.items():
        rows.sort(key=_unit_order)
        queue.append({'entity': entity, 'kind': kind, 'unit_ids': [r['unit_id'] for r in rows],
                      'riesgo': min((r['riesgo'] for r in rows), key=RISK_ORDER.get),
                      'n_units': len(rows)})
    queue.sort(key=lambda e: (RISK_ORDER[e['riesgo']], -e['n_units'], e['entity']))
    return queue


# ── pack ──────────────────────────────────────────────────────────────────────

class EntityPackBuilder(TextPacketBuilder):
    """`TextPacketBuilder` without the frozen-group path: sources for one entity pack."""

    def build_group(self, group):
        raise NotImplementedError('spec 063 packs are per entity: use sources_for')

    def sources_for(self, rows, entity, limits=AMPLIADO_LIMITS):
        """Corpus pages anchored by these units, their cited URLs and the vigencia ones."""
        hints, urls, chunk_ids, entities, values = defaultdict(set), [], [], [], []
        for row in rows:
            payload = row['_payload']
            self._locators(row, payload, payload.get('props') or {}, hints, urls, chunk_ids)
            if row['kind'] in PROSE_KINDS and row['entity']:
                entities.append(row['entity'])
            values.append(row['_value'])
        # What a previous reading said it was missing localises the evidence too.
        values += self._requested_sources([r['unit_id'] for r in rows])
        terms = anchor_terms(values)
        vigencia = self.vigencia_source(entity)
        extra = [u for u in (vigencia.pop('urls') if vigencia else []) if u not in urls]
        # Prose and identity assert facts the corpus can prove; an edge already carries its
        # own document and page, which travel as hints.
        corpus = self.corpus_sources(dict.fromkeys(entities), terms, limits=limits) if entities else []
        return self._sources(hints, urls, chunk_ids, terms, corpus, limits=limits, extra_urls=extra)


def _flat_sources(sources):
    """One entry per page: `{source_id, doc, url, page, status, complete, omitted_chars, text}`."""
    flat = []
    for source in sources:
        base = {'source_id': source.get('source_id') or '', 'doc': source.get('doc') or '',
                'url': source.get('url') or '', 'status': source.get('status') or '',
                'complete': bool(source.get('complete')), 'omitted_chars': int(source.get('omitted_chars') or 0),
                'note': source.get('note') or ''}
        pages = source.get('pages') or {}
        if not pages:
            flat.append({**base, 'page': str(source.get('page') or ''), 'text': ''})
            continue
        for page, text in pages.items():
            flat.append({**base, 'page': str(page), 'text': text})
    return flat


def _edge_block(payload, value):
    props = payload.get('props') or {}

    def prop(key):
        raw = props.get(key)
        return '' if raw is None else raw
    return {'type': payload.get('type') or '', 'start_entity': payload.get('start_entity') or '',
            'start_name': payload.get('start_name') or '', 'end_entity': payload.get('end_entity') or '',
            'end_name': payload.get('end_name') or '', 'source_kind': prop('source_kind'),
            'source_doc': prop('source_doc'), 'source_page': prop('source_page'),
            'source_quote': prop('source_quote'), 'status': prop('status'), 'firme': prop('firme'),
            'status_fuente_tipo': prop('status_fuente_tipo'), 'amount': prop('amount'),
            'currency': prop('currency'), 'date': prop('date'), 'note': prop('note'),
            'public_label': (value.get('public_label') or '') if isinstance(value, dict) else ''}


def current_text(conn, repo, entity, overlay_ref=WORKTREE):
    """The text PUBLISHED today: the qa061 overlay row when there is one, else the node."""
    summaries, roles = load_overlays(repo, overlay_ref)
    node = conn.execute("SELECT payload FROM objects WHERE object_kind='node' AND entity=?",
                        (entity,)).fetchone()
    props = (json.loads(node['payload']).get('props') or {}) if node else {}
    name = props.get('name') or entity
    summary, role = None, None
    row = summaries.get(entity)
    if row is not None:
        summary = ({'removed': True, 'origin': 'overlay'} if row.get('remove') else
                   {'text': str(row.get('resumen') or '').strip(),
                    'sources': [s for s in (row.get('sources') or []) if isinstance(s, str)],
                    'origin': 'overlay', 'updated': str(row.get('updated') or '')})
    elif str(props.get('summary') or '').strip():
        summary = {'text': str(props['summary']).strip(),
                   'sources': [s for s in (props.get('summary_sources') or []) if isinstance(s, str)],
                   'origin': 'node', 'updated': str(props.get('summary_updated') or '')}
    row = roles.get(entity) or roles.get(name)
    if row is not None:
        role = ({'removed': True, 'origin': 'overlay'} if str(row.get('role') or '').strip() == 'remove'
                else {'text': str(row.get('role') or '').strip(), 'origin': 'overlay'})
    elif str(props.get('role') or '').strip():
        role = {'text': str(props['role']).strip(), 'origin': 'node'}
    return {'summary': summary, 'role': role}


def build_packs(conn, repo, entity, unit_ids, kind='entity', overlay_ref=WORKTREE,
                limits=AMPLIADO_LIMITS, classification=None, fetch=True):
    """The manifests of one entity: one, or the sub-packs of a hub, in reading order."""
    if not unit_ids:
        raise ValueError('a pack holds at least one unit')
    rows = {r['unit_id']: r for r in _rows(conn, 'u.unit_id IN (%s)' % ','.join('?' * len(unit_ids)),
                                           tuple(unit_ids))}
    missing = [uid for uid in unit_ids if uid not in rows]
    if missing:
        raise ValueError(f'{len(missing)} unidades no están en el censo: {missing[0][:12]}…')
    ordered = sorted(rows.values(), key=_unit_order)
    facts = entity_facts(conn, repo, entity)
    current = current_text(conn, repo, entity, overlay_ref)
    registry, feed = vigencia_of(repo)
    items = feed.get(entity) or []
    vigencia = (_vigencia_text(entity, facts['name'], registry.get(entity), items)
                if registry.get(entity) or items else '')
    warnings = []
    if facts['is_pseudonym']:
        warnings.append(W_PSEUDONYM)
    if facts['a5_familiar']:
        warnings.append(W_A5)
    if facts['anonymized']:
        warnings.append(W_ANONYMIZED)
    block = {'id': entity, 'type': facts['type'], 'name': facts['name'], 'aliases': facts['aliases'],
             'is_pseudonym': facts['is_pseudonym'], 'pseudonym_note': facts['pseudonym_note'],
             'anonymized': facts['anonymized'], 'a5_familiar': facts['a5_familiar'],
             'degree': facts['degree'], 'context_role': facts['context_role'],
             'family_of': facts['family_of'], 'estado_row': facts['estado_row'],
             'feed_items': facts['feed_items'],
             'tiers_pending': [t for t in TIER_ORDER if any(r['tier'] == t for r in ordered)],
             'flags': rule_flags(facts), 'classification': classification}
    prefix = 'D-' if kind == 'editorial' else 'E-'
    base = prefix + digest([entity, sorted(unit_ids), overlay_ref])[:12]
    size = MAX_EDITORIAL_UNITS if kind == 'editorial' else MAX_PACK_UNITS
    chunks = [ordered[start:start + size] for start in range(0, len(ordered), size)]
    builder = EntityPackBuilder(root_of(conn), repo, fetch=fetch)
    try:
        manifests = []
        for index, chunk in enumerate(chunks, 1):
            pack_id = base if index == 1 else f'{base}-{index}'
            units = [{'unit_id': row['unit_id'], 'tier': row['tier'], 'kind': row['kind'],
                      'field': row['field'], 'riesgo': row['riesgo'], 'value': row['_value'],
                      'subject': builder._subject(row, row['_payload']),
                      'edge': _edge_block(row['_payload'], row['_value']) if row['kind'] == 'edge' else None}
                     for row in chunk]
            manifests.append({'pack_id': pack_id, 'kind': kind, 'index': index, 'total': len(chunks),
                              'overlay_ref': overlay_ref, 'entity': block, 'current': current,
                              'units': units, 'vigencia': vigencia,
                              'sources': _flat_sources(builder.sources_for(chunk, entity, limits)),
                              'warnings': warnings + ([f'SUB-PACK {index} de {len(chunks)}']
                                                      if len(chunks) > 1 else [])})
    finally:
        builder.db.close()
    return manifests


# ── markdown (the only thing the reader sees) ────────────────────────────────

def _current_lines(current):
    out = ['## Texto VIGENTE (lo que se publica hoy; corrige SOBRE esto, no sobre el valor del censo)', '']
    summary = current.get('summary')
    if not summary:
        out.append('**summary**: (sin resumen publicado)')
    elif summary.get('removed'):
        out.append('**summary**: RETIRADO por overlay `summaries_qa061` (no se publica).')
    else:
        origin = 'overlay summaries_qa061' if summary['origin'] == 'overlay' else 'nodo del grafo'
        out += [f"**summary ({origin}, updated {summary.get('updated') or '—'})**:", '', summary['text'], '',
                '**sources vigentes**: ' + ('; '.join(summary.get('sources') or []) or '(ninguna)')]
    role = current.get('role')
    out.append('')
    if not role:
        out.append('**role**: (sin rol publicado)')
    elif role.get('removed'):
        out.append('**role**: RETIRADO por overlay `roles_qa061`.')
    else:
        out.append(f"**role ({'overlay roles_qa061' if role['origin'] == 'overlay' else 'nodo del grafo'})**: "
                   f"{role['text']}")
    return out


def _unit_lines(unit):
    value = unit['value'] if isinstance(unit['value'], str) else canonical(unit['value'])
    out = [f"### {unit['unit_id']}",
           f"- tier: {unit['tier']} · tipo: {unit['kind']} · riesgo: {unit['riesgo']}"
           f" · campo: {unit['field'] or '(objeto completo)'}",
           f"- sujeto: {unit['subject']}",
           f"- valor: {value}"]
    edge = unit.get('edge')
    if edge:
        out += [f"- arista: ({edge['start_name']} · {edge['start_entity']}) -[{edge['type']}]->"
                f" ({edge['end_name']} · {edge['end_entity']})",
                f"- cita guardada: «{edge['source_quote'] or '(ninguna)'}»",
                f"- documento: {edge['source_doc'] or '—'} p.{edge['source_page'] or '—'}"
                f" · procedencia: {edge['source_kind'] or '(no registrada)'}",
                f"- estado: {edge['status'] or '(sin estado)'} · firmeza: {edge['firme'] if edge['firme'] != '' else '—'}"
                f" · acto: {edge['status_fuente_tipo'] or '—'}",
                f"- importe: {edge['amount'] if edge['amount'] != '' else '—'} {edge['currency']}"
                f" · fecha: {edge['date'] or '—'} · nota: {edge['note'] or '—'}",
                f"- etiqueta pública: {edge['public_label'] or '(la del tipo)'}"]
    out.append('')
    return out


def _source_lines(sources):
    out = ['## Fuentes', '']
    corpus = [s for s in sources if not s['url']]
    web = [s for s in sources if s['url']]
    if not sources:
        out += ['(el pack no lleva fuentes: usa la herramienta o responde `incierto`)', '']
    for header, group in (('FUENTES DEL CORPUS (documentos ingeridos)', corpus),
                          ('FUENTES WEB (texto archivado)', web)):
        if not group:
            continue
        out += [f'### {header}', '']
        for source in group:
            head = source['doc'] or source['url'] or source['source_id']
            page = f" p.{source['page']}" if source['page'] else ''
            out.append(f"[fuente {source['source_id']}] {head}{page} · estado: {source['status']}"
                       f" · {'COMPLETA' if source['complete'] else 'INCOMPLETA'}")
            if not source['complete']:
                out.append(f"  ⚠ FUENTE INCOMPLETA: se han omitido {source['omitted_chars']} caracteres."
                           ' Los cortes van marcados «[…omitido…]». Ausencia de prueba en una fuente'
                           ' incompleta NO es prueba de ausencia: busca o responde `incierto`.')
            if source['note']:
                out.append(f"  nota: {source['note']}")
            out += [f"  --- p.{source['page'] or '1'} ---", source['text'], '']
    return out


def render_pack(manifest, tools=False, ev_path=None):
    """The pack as the reader gets it: header, warnings, current text, units, sources, feed."""
    if tools and not ev_path:
        raise ValueError('con herramienta hace falta la ruta de qa_esc_ev.py')
    entity, out = manifest['entity'], []
    out.append(f"# PACK {manifest['pack_id']} — {entity['id']} · {entity['name']}")
    out += ['', f"- tipo: {entity['type']} · grado: {entity['degree']} · unidades: {len(manifest['units'])}"
                f" · sub-pack {manifest['index']} de {manifest['total']}"
                f" · tiers: {', '.join(entity['tiers_pending']) or '—'}"]
    if entity['aliases']:
        out.append(f"- aliases publicados: {json.dumps(entity['aliases'], ensure_ascii=False)}")
    if entity['context_role']:
        out.append(f"- cargo (capa de contexto curada): {entity['context_role']}")
    if entity['family_of']:
        out.append(f"- parentesco registrado con: {', '.join(entity['family_of'])}")
    classification = entity.get('classification') or {}
    if classification:
        out.append(f"- clase: {classification.get('clase') or '—'} · riesgo: {classification.get('riesgo') or '—'}"
                   f" ({classification.get('source') or '—'})")
    for warning in manifest['warnings']:
        out.append(f'- ⚠️ **{warning}**')
    if entity['is_pseudonym'] and entity['pseudonym_note']:
        out.append(f"- nota de seudónimo: {entity['pseudonym_note']}")
    if manifest['total'] > 1 and manifest['index'] > 1:
        out.append('- En este sub-pack `summary`, `role`, `alias_deny` y `estado` van vacíos:'
                   ' lo que descubras sobre ellos va a `notes`.')
    out += ['', TOOL_LINE.format(ev=ev_path) if tools else BLIND_LINE, '']
    out += _current_lines(manifest['current'])
    out += ['', f"## Unidades ({len(manifest['units'])})", '']
    for unit in manifest['units']:
        out += _unit_lines(unit)
    out += ['## Vigencia', '',
            manifest['vigencia'] or '(sin fila de registro ni ítems de última hora para esta entidad)', '']
    out += _source_lines(manifest['sources'])
    items = entity['feed_items']
    out += [f'## Feed de última hora de la entidad ({len(items)} ítems)', '']
    for item in items:
        out.append(f"- {item['iso'] or '—'} · **{item['outlet'] or '—'}** · {item['headline']} <{item['url']}>")
    if not items:
        out.append('- (ningún ítem del feed nombra a esta entidad)')
    out += ['', f"Devuelve EXCLUSIVAMENTE el JSON del esquema con entity_id={entity['id']} y una entrada por"
                f" cada una de las {len(manifest['units'])} unidades de este pack."]
    return '\n'.join(out) + '\n'


# ── disk + ledger ─────────────────────────────────────────────────────────────

def pack_paths(root, pack_id):
    base = Path(root) / ESC_DIR / 'packs'
    return base / f'{pack_id}.json', base / f'{pack_id}.md'


def pack_sha256(manifest):
    return digest(manifest)


def write_pack(root, manifest, markdown):
    """Freeze the pack on disk and register it (`state='packed'`); idempotent."""
    json_path, md_path = pack_paths(root, manifest['pack_id'])
    sha = pack_sha256(manifest)
    if json_path.exists():
        if pack_sha256(json.loads(json_path.read_text())) != sha:
            raise ValueError(f"pack {manifest['pack_id']} ya congelado con otro contenido; "
                             'investiga antes de seguir')
    else:
        write_new(json_path, manifest)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown)
    riesgo = min((u['riesgo'] for u in manifest['units']), key=RISK_ORDER.get)
    clase = (manifest['entity'].get('classification') or {}).get('clase')
    conn = connect(root)
    try:
        ensure_esc_schema(conn)
        with conn:
            conn.execute('INSERT OR IGNORE INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,pack_path,'
                         'pack_sha256,overlay_ref,riesgo,clase,route,state,batch,created_at)'
                         ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (manifest['pack_id'], manifest['entity']['id'], manifest['kind'], manifest['index'],
                          manifest['total'], canonical([u['unit_id'] for u in manifest['units']]),
                          str(json_path), sha, manifest['overlay_ref'], riesgo, clase, None, 'packed',
                          None, now()))
    finally:
        conn.close()
    return {'pack_id': manifest['pack_id'], 'pack_sha256': sha, 'units': len(manifest['units']),
            'sources': len(manifest['sources']), 'chars': len(markdown),
            'path': str(json_path), 'markdown_path': str(md_path)}


def load_pack(root, pack_id):
    """The frozen manifest and its markdown."""
    json_path, md_path = pack_paths(root, pack_id)
    if not json_path.exists():
        raise ValueError(f'pack {pack_id} no está en {json_path.parent}')
    return json.loads(json_path.read_text()), (md_path.read_text() if md_path.exists() else '')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='qa_esc_packs', description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--repo', default=str(REPO))
    sub = parser.add_subparsers(dest='command', required=True)
    queue = sub.add_parser('queue', help='cola de entidades en JSON')
    queue.add_argument('--scope', default='pending', choices=SCOPES)
    queue.add_argument('--limit', type=int)
    pack = sub.add_parser('pack', help='empaqueta una entidad')
    pack.add_argument('--entity', required=True)
    pack.add_argument('--scope', default='pending', choices=SCOPES)
    pack.add_argument('--overlay-ref', default=WORKTREE)
    pack.add_argument('--tools', action='store_true')
    pack.add_argument('--ev-path', default=str(REPO / 'pipeline/src/qa_esc_ev.py'))
    pack.add_argument('--no-fetch', action='store_true', help='no archivar URLs (sin red)')
    args = parser.parse_args(argv)
    conn = connect(args.root)
    try:
        entries = entity_queue(conn, args.scope)
        if args.command == 'queue':
            print(json.dumps(entries[:args.limit] if args.limit else entries, ensure_ascii=False, indent=1))
            return 0
        entry = next((e for e in entries if e['entity'] == args.entity), None)
        if not entry:
            raise SystemExit(f'sin unidades «{args.scope}» para {args.entity}')
        manifests = build_packs(conn, args.repo, entry['entity'], entry['unit_ids'], kind=entry['kind'],
                                overlay_ref=args.overlay_ref, fetch=not args.no_fetch)
        for manifest in manifests:
            markdown = render_pack(manifest, tools=args.tools, ev_path=args.ev_path if args.tools else None)
            print(json.dumps(write_pack(args.root, manifest, markdown), ensure_ascii=False))
    finally:
        conn.close()
    return 0


__all__ = ['EntityPackBuilder', 'MAX_EDITORIAL_UNITS', 'MAX_PACK_UNITS', 'WORKTREE', 'build_graph_index',
           'build_packs', 'current_text', 'ensure_esc_schema', 'entity_facts', 'entity_queue',
           'graph_index', 'load_overlays', 'load_pack', 'pack_entity', 'pack_paths', 'pack_sha256',
           'read_yaml', 'render_pack', 'root_of', 'rule_flags', 'snapshot_of', 'write_pack']


if __name__ == '__main__':
    raise SystemExit(main())
