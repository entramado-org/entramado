"""Deterministic lint L1-L26 (specs 061, 063 y 070) over a frozen snapshot export (0 tokens, 0 graph).

Reads `neo4j/<snapshot>/{nodes,relationships}.jsonl` plus the versioned overlays and
writes one JSONL per check. It is a REPORT: exit code is 0 even with hits, unless
`--fail-on` names checks that must stay empty (the L1/L2/L12/L13/L15/L26 guards).

Use `python -m src.qa_lint --help` from pipeline/.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, namedtuple
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlparse

import yaml

from .ontologia import ontologia
from .qa_integral import digest, now, segments
from .seudonimos import URL_INVALIDA

_ONT = ontologia()

REPO = Path(__file__).resolve().parents[2]
TECH_RELS = {'MENTIONED_IN', 'PART_OF', 'ABOUT'}
ACCUSATION_RELS = {'ACCUSED_OF', 'CONVICTED_OF'}
# Procedural stages and layers come from the ontology (spec 069), like the API's.
STATUSES = set(_ONT.estados)
OPEN_STATUSES = set(_ONT.estados_abiertos)
CLOSED_STATUSES = set(_ONT.estados_cerrados)
# A null source_kind is the extracted judicial layer.
SOURCE_KINDS = set(_ONT.capas)
STALE_DAYS = 90
MIN_QUOTE_FRAGMENT = 4

QUOTE_CHARS = str.maketrans({'«': '"', '»': '"', '“': '"', '”': '"', '„': '"', '‘': "'",
                             '’': "'", '‹': '"', '›': '"', '–': '-', '—': '-',
                             ' ': ' ', '​': ''})
ELLIPSIS = re.compile(r'\s*(?:\.\.\.|…)\s*')
# Editorial meta-text: prose that talks about the GRAPH instead of about the person.
META_TEXT = re.compile(
    r'(?:\b(?:el|este|ese|dicho|los|estos|un)\s+nodos?\b|\bnodo\s+(?:agrega|agrupa|conflaci|hub|puente)'
    r'|grado\s+en\s+el\s+grafo|centralidad\s+en\s+el\s+grafo|\bdeben?\s+separarse\b'
    r'|agrega\s+referencias|conflaci[oó]n|desambigu\w+|entity_id|source_kind'
    r'|\bel\s+grafo\s+(?:refleja|muestra|recoge))', re.I)
CONVICTION = re.compile(r'\bcondenad[oa]s?\b|\bconden[oó]\b|\bcondena\s+(?:firme|a\s)', re.I)
FIRMEZA = re.compile(r'\bfirme(?:za)?\b|\brecurrid\w+|\brecurrib\w+|pendiente de recurso'
                     r'|cabe recurso|casaci[oó]n', re.I)
# The clause before the verb turns it into something other than an assertion: a negation
# («no consta condenada»), a hypothesis («si son condenadas») or a petition («pide que
# sean condenados»). Only assertions of an actual conviction are a firmeza defect.
NOT_ASSERTED = re.compile(
    r'\b(?:no|ni|nunca|jam[aá]s|sin|tampoco|ninguna?|ning[uú]n|si|sea|sean|fuera|fuese|fueran'
    r'|fuesen|resulte|resulten|ser[íi]a|ser[íi]an|pide|piden|solicita|solicitan|reclama|reclaman'
    r'|pedir|solicitar|interesa|para que)\b[^.;:]{0,70}$', re.I)
PROCEDURAL = re.compile(r'\binvestigad\w+|\bimputad\w+|\bprocesad\w+|\bacusad\w+|\bcondenad\w+'
                        r'|\babsuelt\w+|\barchivad\w+|prisi[oó]n provisional|juicio oral', re.I)

Prose = namedtuple('Prose', 'object_kind ref entity_id type field text owners')
Row = namedtuple('Row', 'check severity object_kind ref entity_id type field detail proposed_action')


def dominio(url) -> str | None:
    """The lowercase network location of `url`, or None if the parser cannot read it: its error carries the URL
    (spec 070, AC193), reported instead with the tree's fixed reason (`seudonimos.URL_INVALIDA`)."""
    try:
        return urlparse(str(url)).netloc.lower()
    except ValueError:
        return None


CHECKS: dict[str, dict] = {}


def check(cid, label, severity):
    def wrap(fn):
        # A reused id would silently replace the earlier check (and drop it from `--fail-on`).
        if cid in CHECKS:
            raise ValueError(f'check {cid} duplicado: {CHECKS[cid]["fn"].__name__} y {fn.__name__}')
        CHECKS[cid] = {'label': label, 'severity': severity, 'fn': fn}
        return fn
    return wrap


def norm(text):
    """Whitespace/accent/quote-insensitive form for substring and name matching."""
    text = (text or '').translate(QUOTE_CHARS)
    text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', text).strip().lower()


def contains_quote(haystack, quote):
    """Elided quotes («A ... B») count as present when their fragments appear in order."""
    needle = norm(quote)
    if needle and needle in haystack:
        return True
    parts = [p for p in ELLIPSIS.split(needle) if len(p) >= MIN_QUOTE_FRAGMENT]
    if len(parts) < 2:
        return False
    at = 0
    for part in parts:
        found = haystack.find(part, at)
        if found < 0:
            return False
        at = found + len(part)
    return True


def as_date(value):
    text = str(value or '')[:10]
    return date.fromisoformat(text) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text) else None


def as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def load_yaml(path, key):
    path = Path(path)
    if not path.exists():
        return []
    return (yaml.safe_load(path.read_text(encoding='utf-8')) or {}).get(key) or []


class Snapshot:
    """Streamed read of the frozen export; embeddings are dropped, never held."""

    def __init__(self, path):
        path = Path(path)
        self.path = path
        self.nodes, self.by_id, self.chunks, self.docs = {}, {}, {}, {}
        self.chunks_by_doc = defaultdict(list)
        with (path / 'nodes.jsonl').open() as f:
            for line in f:
                record = json.loads(line)
                props = record['props']
                props.pop('embedding', None)
                self.nodes[record['ref']] = record
                if props.get('entity_id'):
                    self.by_id.setdefault(props['entity_id'], record)
                if 'Chunk' in record['labels']:
                    self.chunks[props['chunk_id']] = props
                    self.chunks_by_doc[props.get('doc_id')].append(props)
                elif 'Document' in record['labels']:
                    self.docs[props['doc_id']] = props
        self.rels = []
        self.domain_degree = Counter()
        self.pairs = defaultdict(set)
        with (path / 'relationships.jsonl').open() as f:
            for line in f:
                record = json.loads(line)
                self.rels.append(record)
                if record['type'] in TECH_RELS:
                    continue
                self.domain_degree[record['start']] += 1
                self.domain_degree[record['end']] += 1
                start, end = self.eid(record['start']), self.eid(record['end'])
                if start and end:
                    self.pairs[start].add(end)
                    self.pairs[end].add(start)
        self._doc_text = {}

    def eid(self, ref):
        return self.nodes[ref]['props'].get('entity_id') if ref in self.nodes else None

    def labels(self, ref):
        return self.nodes[ref]['labels'] if ref in self.nodes else []

    def endpoints(self, rel):
        return self.eid(rel['start']), self.eid(rel['end'])

    def doc_text(self, doc_id):
        if doc_id not in self._doc_text:
            self._doc_text[doc_id] = norm(' '.join(c.get('text') or ''
                                                   for c in self.chunks_by_doc.get(doc_id, [])))
        return self._doc_text[doc_id]

    def persons(self):
        return [n for n in self.nodes.values() if 'Person' in n['labels']]


class Overlays:
    """Versioned curation the lint contrasts the graph against."""

    def __init__(self, repo=REPO):
        self.repo = Path(repo)
        data = self.repo / 'pipeline' / 'data'
        self.documents = load_yaml(data / 'documents' / 'documents.yaml', 'documents')
        self.accusations = load_yaml(data / 'accusations' / 'accusations.yaml', 'accusations')
        self.summaries = load_yaml(data / 'context' / 'summaries.yaml', 'summaries')
        self.pseudonyms = load_yaml(data / 'context' / 'pseudonyms.yaml', 'pseudonyms')
        self.merges = load_yaml(data / 'context' / 'merges.yaml', 'merges')
        self.ultima_hora = load_yaml(data / 'ultima_hora' / 'ultima_hora.yaml', 'relations')
        self.feed = load_yaml(data / 'ultima_hora' / 'feed.yaml', 'items')
        # figures.yaml tiene DOS secciones (`entities:` y `relations:`): solo la primera
        # declara figuras públicas con `role` (cf. finding figures-yaml-two-sections-gotcha).
        self.figures = load_yaml(data / 'context' / 'figures.yaml', 'entities')
        self.familiares_revisados = load_yaml(data / 'context' / 'familiares_revisados.yaml',
                                              'revisados')
        self.patrones = load_yaml(data / 'patrones' / 'patrones.yaml', 'patrones')
        self.cases = load_yaml(self.repo / 'api' / 'app' / 'data' / 'cases.yaml', 'cases')
        self.briefings = sorted((self.repo / 'sm-vault' / '_Briefings').glob('*.md'))
        self.firmeza = {d.get('doc_id'): d.get('sentencia_firme') for d in self.documents}
        # A defendant counts as REGISTERED with an `estado` block (its `fuente` is a doc or a url,
        # spec 061 plan §127) or a legacy `status` citing a `doc`: the ultima_hora loader's rule.
        self.registered = {a.get('entity_id') for a in self.accusations
                           if a.get('estado') or (a.get('status') and a.get('doc'))}
        self.source_domains = {d for entry in self.summaries for u in (entry.get('sources') or [])
                               if (d := dominio(u)) is not None}

    def estado(self, entity_id):
        for row in self.accusations:
            if row.get('entity_id') == entity_id:
                return row.get('estado') or ({'status': row['status']} if row.get('status') else None)
        return None


def prose_items(snap, over):
    """Every prose span L5/L7/L8/L10 read: node summary/role plus the editorial files.

    `owners` = entity_ids the prose legitimately talks about (the node itself, or the
    ids an editorial item declares), so L8 can tell a stray name from a declared one.
    """
    items = []
    for ref, node in snap.nodes.items():
        props = node['props']
        entity_id = props.get('entity_id')
        if not entity_id:
            continue
        for field in ('summary', 'role'):
            if isinstance(props.get(field), str) and props[field].strip():
                items.append(Prose('node', ref, entity_id, node['labels'][0], field,
                                   props[field], {entity_id}))
    for index, item in enumerate(over.feed):
        ref = f"pipeline/data/ultima_hora/feed.yaml#{item.get('iso', '?')}#{index}"
        owners = set(item.get('entities') or [])
        for field in ('headline', 'dek'):
            if item.get(field):
                items.append(Prose('feed_item', ref, None, 'feed', field, item[field], owners))
    for case in over.cases:
        ref = f"api/app/data/cases.yaml#{case.get('slug')}"
        owners = {e.get('id') for e in (case.get('entidades') or [])}
        for field in ('dek', 'estado'):
            if case.get(field):
                items.append(Prose('case', ref, case.get('slug'), 'case', field, case[field], owners))
        for entity in case.get('entidades') or []:
            if entity.get('papel'):
                items.append(Prose('case', ref, entity.get('id'), 'case', 'papel',
                                   entity['papel'], owners))
    for patron in over.patrones:
        ref = f"pipeline/data/patrones/patrones.yaml#{patron.get('slug')}"
        owners = set(patron.get('entities') or [])
        for field in ('tesis', 'estado'):
            if patron.get(field):
                items.append(Prose('patron', ref, patron.get('slug'), 'patron', field,
                                   patron[field], owners))
        for index, hito in enumerate(patron.get('hitos') or []):
            if hito.get('hecho'):
                items.append(Prose('patron', ref, patron.get('slug'), 'patron',
                                   f'hitos[{index}].hecho', hito['hecho'], owners))
    for path in over.briefings:
        text = path.read_text(encoding='utf-8')
        front = re.match(r'^---\n(.*?)\n---\n', text, re.S)
        owners = set()
        if front:
            owners = set((yaml.safe_load(front.group(1)) or {}).get('anchors') or [])
            text = text[front.end():]
        for index, para in enumerate(p.strip() for p in text.split('\n\n')):
            if para and not para.startswith('#'):
                items.append(Prose('briefing', f"sm-vault/_Briefings/{path.name}#p{index}",
                                   None, 'briefing', f'p{index}', para, owners))
    return items


def person_matcher(snap):
    """Whole-word index of every ≥2-token Person name/alias, on normalised text."""
    surfaces = defaultdict(set)
    for node in snap.persons():
        props = node['props']
        entity_id = props.get('entity_id')
        if not entity_id:
            continue
        for surface in [props.get('name'), props.get('canonical_name'), *(props.get('aliases') or [])]:
            key = norm(surface)
            if key and len(key.split()) >= 2:
                surfaces[key].add(entity_id)
    if not surfaces:
        return surfaces, None
    pattern = '|'.join(re.escape(s) for s in sorted(surfaces, key=len, reverse=True))
    return surfaces, re.compile(rf'(?<!\w)(?:{pattern})(?!\w)')


# --- L1-L2 · firmeza and procedural coherence ---------------------------------

@check('L1', 'CONVICTED_OF sin `sentencia_firme` declarado en documents.yaml', 'error')
def l1(ctx):
    for rel in ctx.snap.rels:
        if rel['type'] != 'CONVICTED_OF':
            continue
        doc = rel['props'].get('source_doc')
        if ctx.over.firmeza.get(doc) is None:
            yield Row('L1', 'error', 'relationship', rel['ref'], ctx.snap.eid(rel['start']),
                      rel['type'], 'source_doc', f'documento «{doc}» sin sentencia_firme declarada',
                      'declarar sentencia_firme en documents.yaml')


@check('L2', 'status/firme incoherentes en aristas de acusación o condena', 'error')
def l2(ctx):
    for rel in ctx.snap.rels:
        if rel['type'] not in ACCUSATION_RELS:
            continue
        props, person = rel['props'], ctx.snap.eid(rel['start'])
        status, firme = props.get('status'), props.get('firme')
        problems = []
        if status and status not in STATUSES:
            problems.append(f'status «{status}» fuera del enum')
        if rel['type'] == 'CONVICTED_OF':
            declared = ctx.over.firmeza.get(props.get('source_doc'))
            if firme is None:
                problems.append('condena sin prop `firme`')
            elif bool(firme) and status != 'condenado':
                problems.append(f'firme=true con status «{status}»')
            elif not firme and status != 'condenado_no_firme':
                problems.append(f'firme=false con status «{status}»')
            if declared is not None and firme is not None and bool(declared) != bool(firme):
                problems.append(f'firme={firme} contra documents.yaml sentencia_firme={declared}')
        elif status in {'condenado', 'condenado_no_firme'}:
            # El registro `estado` (spec 061) propaga la condena a las ACCUSED_OF de la
            # persona con su firmeza; solo es un defecto si la firmeza no acompaña.
            propagado = (props.get('status_fuente_tipo') == 'sentencia'
                         and ((status == 'condenado_no_firme' and firme is False)
                              or (status == 'condenado' and firme is True)))
            if not propagado:
                problems.append(f'ACCUSED_OF con status de condena «{status}»')
        for problem in problems:
            yield Row('L2', 'error', 'relationship', rel['ref'], person, rel['type'], 'status',
                      problem, 'corregir la arista por overlay relations fix.set')


# --- L3-L4 · registro de estado procesal --------------------------------------

@check('L3', 'ACCUSED_OF con status null', 'warn')
def l3(ctx):
    for rel in ctx.snap.rels:
        if rel['type'] != 'ACCUSED_OF' or rel['props'].get('status'):
            continue
        person, crime = ctx.snap.endpoints(rel)
        kind = rel['props'].get('source_kind') or 'judicial'
        yield Row('L3', 'warn', 'relationship', rel['ref'], person, rel['type'], 'status',
                  f'{kind} · {crime} · sin estado procesal (la UI lo rellenaría por defecto)',
                  'fila `estado` en accusations.yaml para esa persona')


@check('L4', 'persona con estado cerrado y estado abierto a la vez, sin fila de registro', 'warn')
def l4(ctx):
    by_person = defaultdict(set)
    for rel in ctx.snap.rels:
        if rel['type'] not in ACCUSATION_RELS:
            continue
        default = 'condenado_no_firme' if rel['type'] == 'CONVICTED_OF' else None
        by_person[ctx.snap.eid(rel['start'])].add(rel['props'].get('status') or default)
    for person, states in sorted(by_person.items()):
        if not person or person in ctx.over.registered:
            continue
        if (states & CLOSED_STATUSES) and (states & (OPEN_STATUSES | {None})):
            yield Row('L4', 'warn', 'node', ctx.snap.by_id[person]['ref'], person, 'Person', 'status',
                      'estados simultáneos: ' + ', '.join(sorted(str(s) for s in states)),
                      'fila `estado` en accusations.yaml que arbitre la fase vigente')


# --- L5-L8, L10 · prosa --------------------------------------------------------

@check('L5', 'prosa que afirma una condena sin declarar firmeza', 'warn')
def l5(ctx):
    for item in ctx.prose:
        if FIRMEZA.search(item.text):
            continue
        for _, _, sentence in segments(item.text):
            hit = next((m for m in CONVICTION.finditer(sentence)
                        if not NOT_ASSERTED.search(sentence[:m.start()])), None)
            if hit:
                yield Row('L5', 'warn', item.object_kind, item.ref, item.entity_id, item.type,
                          item.field, sentence[max(0, hit.start() - 140):hit.end() + 140].strip(),
                          'añadir firmeza («firme» / «no firme, recurrible») o retirar la frase')
                break


@check('L6', 'prosa curada en nodos con seudónimo judicial', 'warn')
def l6(ctx):
    flagged = {p['id'].strip() for p in ctx.over.pseudonyms if (p.get('id') or '').strip()}
    for node in ctx.snap.nodes.values():
        props = node['props']
        entity_id = props.get('entity_id')
        if not (props.get('is_pseudonym') or entity_id in flagged):
            continue
        for field in ('summary', 'role'):
            if isinstance(props.get(field), str) and props[field].strip():
                spans = len(segments(props[field]))
                target = 'summaries_qa061.yaml remove: true' if field == 'summary' else 'roles_qa061.yaml role: remove'
                yield Row('L6', 'warn', 'node', node['ref'], entity_id, node['labels'][0], field,
                          f'{spans} frases sobre un seudónimo judicial (spec 059)', target)


@check('L7', 'meta-texto editorial en la prosa publicada', 'warn')
def l7(ctx):
    for item in ctx.prose:
        found = META_TEXT.search(item.text)
        if found:
            yield Row('L7', 'warn', item.object_kind, item.ref, item.entity_id, item.type,
                      item.field, f'«{found.group(0)}» — la prosa habla del grafo, no de la entidad',
                      'reescribir sin meta-texto o retirar la frase por overlay')


@check('L8', 'frase que nombra a una Person sin arista (ni declaración) que lo respalde', 'info')
def l8(ctx):
    surfaces, matcher = ctx.persons
    if matcher is None:
        return
    for item in ctx.prose:
        linked = set(item.owners)
        for owner in item.owners:
            linked |= ctx.snap.pairs.get(owner, set())
        seen = set()
        for _, _, sentence in segments(item.text):
            for match in matcher.finditer(norm(sentence)):
                for person in sorted(surfaces[match.group(0)] - linked - seen):
                    seen.add(person)
                    detail = ('nombrada en la prosa sin arista de dominio con el nodo'
                              if item.object_kind == 'node' else
                              'nombrada en la prosa sin figurar en `entities` del ítem')
                    yield Row('L8', 'info', item.object_kind, item.ref, item.entity_id, item.type,
                              item.field, f'{person}: {detail} — «{sentence.strip()[:180]}»',
                              'declarar la relación con fuente, o retirar el nombre de la prosa')


@check('L10', 'summary con estado procesal y `summary_updated` caducado', 'info')
def l10(ctx):
    horizon = ctx.as_of - timedelta(days=STALE_DAYS)
    for node in ctx.snap.nodes.values():
        props = node['props']
        summary = props.get('summary')
        if not (isinstance(summary, str) and PROCEDURAL.search(summary)):
            continue
        updated = as_date(props.get('summary_updated'))
        if updated and updated >= horizon:
            continue
        age = 'sin summary_updated' if not updated else f'actualizado {updated} ({(ctx.as_of - updated).days} días)'
        yield Row('L10', 'info', 'node', node['ref'], props.get('entity_id'), node['labels'][0],
                  'summary_updated', f'{age}; el texto afirma un estado procesal',
                  'refrescar el resumen o retirar la frase de estado')


# --- L9 · fuentes de los resúmenes --------------------------------------------

@check('L9', 'summary sin `summary_sources`, con dominio fuera de allowlist o URL muerta', 'info')
def l9(ctx):
    session = None
    if ctx.network:
        import requests  # lazy: only the opt-in --network path pays for it
        session = requests.Session()
        session.headers['User-Agent'] = 'entramado-qa-lint/061'
    for node in ctx.snap.nodes.values():
        props = node['props']
        if not props.get('summary'):
            continue
        entity_id, label = props.get('entity_id'), node['labels'][0]
        sources = props.get('summary_sources') or []
        if not sources:
            yield Row('L9', 'info', 'node', node['ref'], entity_id, label, 'summary_sources',
                      'resumen publicado sin ninguna fuente', 'añadir fuentes o retirar el resumen')
            continue
        for url in sources:
            domain = dominio(url)
            if domain is None:   # spec 070 (AC187/AC193): its error carries the URL; a fixed reason instead
                yield Row('L9', 'info', 'node', node['ref'], entity_id, label, 'summary_sources',
                          URL_INVALIDA, 'revisar la fuente')
                continue
            ctx.extra['L9_domains'][domain] += 1
            if domain not in ctx.over.source_domains:
                yield Row('L9', 'info', 'node', node['ref'], entity_id, label, 'summary_sources',
                          f'dominio «{domain}» fuera de la allowlist de summaries.yaml', 'revisar la fuente')
            if session is not None:
                try:
                    status = session.head(url, timeout=15, allow_redirects=True).status_code
                    if status >= 400:
                        status = session.get(url, timeout=20, stream=True).status_code
                except Exception as error:  # noqa: BLE001 — liveness is best-effort
                    status = type(error).__name__
                if status != 200:
                    ctx.extra['L9_dead'] += 1
                    yield Row('L9', 'info', 'node', node['ref'], entity_id, label, 'summary_sources',
                              f'URL no viva ({status}): {url}', 'sustituir por archive.org o retirar')


# --- L11 · caducidad de la capa última hora ------------------------------------

@check('L11', 'ACCUSED_OF de última hora con fecha caducada y sin registro', 'warn')
def l11(ctx):
    horizon = ctx.as_of - timedelta(days=STALE_DAYS)
    by_url = {(r.get('source'), r.get('target'), r.get('url')): r for r in ctx.over.ultima_hora}
    for rel in ctx.snap.rels:
        props = rel['props']
        if rel['type'] != 'ACCUSED_OF' or props.get('source_kind') != 'ultima_hora':
            continue
        person, crime = ctx.snap.endpoints(rel)
        when = as_date(props.get('date'))
        if when is None:
            item = by_url.get((ctx.snap.nodes[rel['start']]['props'].get('name'),
                               ctx.snap.nodes[rel['end']]['props'].get('name'), props.get('url')))
            when = as_date((item or {}).get('date'))
        if when is None or when >= horizon:
            continue
        registered = person in ctx.over.registered
        yield Row('L11', 'warn', 'relationship', rel['ref'], person, rel['type'], 'date',
                  f'{crime} · {when} ({(ctx.as_of - when).days} días) · '
                  f'{"con" if registered else "sin"} fila de registro',
                  'mantener si hay registro; si no, retirar en qa061_retiradas.py')


# --- L12-L13 · guardas heredadas ----------------------------------------------

@check('L12', 'pseudonym_guard: un seudónimo judicial adquiriendo identidad civil', 'error')
def l12(ctx):
    from .context_loader import _norm_tokens
    merged = set()
    for merge in ctx.over.merges:
        canonical = merge.get('canonical') or {}
        names = [canonical.get('name') if isinstance(canonical, dict) else canonical]
        names += [d.get('name') if isinstance(d, dict) else d for d in merge.get('duplicates') or []]
        merged |= {norm(n) for n in names if n}
    for entry in ctx.over.pseudonyms:
        entity_id = (entry.get('id') or '').strip()
        if not entity_id:
            continue
        node = ctx.snap.by_id.get(entity_id)
        if node is None:
            yield Row('L12', 'error', 'node', None, entity_id, 'Person', 'entity_id',
                      'el nodo del seudónimo ya no existe (¿fusionado?)',
                      'revisar merges.yaml — un seudónimo nunca se fusiona')
            continue
        props = node['props']
        slug = set(entity_id.split(':', 1)[1].split('-'))
        name_tokens = _norm_tokens(props.get('name') or '')
        if name_tokens - slug:
            yield Row('L12', 'error', 'node', node['ref'], entity_id, 'Person', 'name',
                      f'nombre civil «{props.get("name")}» sobre un seudónimo', 'revertir el nombre')
        for alias in props.get('aliases') or []:
            if _norm_tokens(alias) - slug - name_tokens:
                yield Row('L12', 'error', 'node', node['ref'], entity_id, 'Person', 'aliases',
                          f'alias civil «{alias}» sobre un seudónimo', 'retirar el alias')
        if norm(props.get('name') or '') in merged:
            yield Row('L12', 'error', 'node', node['ref'], entity_id, 'Person', 'merges',
                      'el seudónimo figura en merges.yaml', 'retirar la fila de merges.yaml')


@check('L13', 'PII en las capas versionadas o en nombres/alias del grafo', 'error')
def l13(ctx):
    spec = importlib.util.spec_from_file_location(
        'qa_lint_pii_check', ctx.over.repo / 'pipeline' / 'scripts' / 'pii_check.py')
    pii = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pii)
    allow = pii._allow()
    for kind, match, where in pii.scan_files():
        if not pii._allowed(match, where, allow):
            yield Row('L13', 'error', 'overlay_row', where, None, kind, 'file', match,
                      'retirar el dato o justificarlo en pii_allowlist.yaml')
    # scan_graph() needs a live driver; the same patterns run over the frozen export.
    for node in ctx.snap.nodes.values():
        props = node['props']
        entity_id = props.get('entity_id')
        if not entity_id:
            continue
        for text in [props.get('name') or '', *(props.get('aliases') or [])]:
            for kind, pattern in pii.PATTERNS.items():
                for match in pattern.finditer(text):
                    if not pii._allowed(match.group(0), f'graph:{entity_id}', allow):
                        yield Row('L13', 'error', 'node', node['ref'], entity_id,
                                  node['labels'][0], 'name/aliases', f'{kind}: {match.group(0)}',
                                  'renombrar el nodo o justificarlo en pii_allowlist.yaml')


# --- L14-L17 · procedencia de las aristas --------------------------------------

@check('L14', '`source_quote` que no aparece en su chunk/documento', 'warn')
def l14(ctx):
    for rel in ctx.snap.rels:
        props = rel['props']
        quote = props.get('source_quote')
        if not quote:
            continue
        chunk = ctx.snap.chunks.get(props.get('source_chunk_id'))
        person = ctx.snap.eid(rel['start'])
        if chunk is not None:
            page = as_int(props.get('source_page'))
            start, end = as_int(chunk.get('page_start')), as_int(chunk.get('page_end'))
            if page is not None and start is not None and end is not None and not start <= page <= end:
                yield Row('L14', 'warn', 'relationship', rel['ref'], person, rel['type'],
                          'source_page', f'página {page} fuera del chunk ({start}-{end})',
                          'corregir source_page por overlay relations fix.set')
            if not contains_quote(norm(chunk.get('text') or ''), quote):
                yield Row('L14', 'warn', 'relationship', rel['ref'], person, rel['type'],
                          'source_quote', f'no literal en el chunk: «{quote[:160]}»',
                          'reanclar la cita o retirar la arista')
            continue
        doc = props.get('source_doc')
        if doc in ctx.snap.chunks_by_doc and not contains_quote(ctx.snap.doc_text(doc), quote):
            yield Row('L14', 'warn', 'relationship', rel['ref'], person, rel['type'], 'source_quote',
                      f'no literal en «{doc}»: «{quote[:160]}»', 'reanclar la cita o retirar la arista')


@check('L15', '`source_kind` fuera del enum de capas', 'error')
def l15(ctx):
    for rel in ctx.snap.rels:
        kind = rel['props'].get('source_kind')
        if kind and kind not in SOURCE_KINDS:
            yield Row('L15', 'error', 'relationship', rel['ref'], ctx.snap.eid(rel['start']),
                      rel['type'], 'source_kind', f'capa desconocida «{kind}»',
                      'normalizar a una capa existente o declarar la nueva')


@check('L16', 'amount sin currency, o date posterior al documento que la sostiene', 'warn')
def l16(ctx):
    for rel in ctx.snap.rels:
        props = rel['props']
        person = ctx.snap.eid(rel['start'])
        if props.get('amount') is not None and not props.get('currency'):
            yield Row('L16', 'warn', 'relationship', rel['ref'], person, rel['type'], 'currency',
                      f'amount={props["amount"]} sin moneda', 'fijar currency por overlay fix.set')
        when = as_date(props.get('date'))
        doc_date = as_date((ctx.snap.docs.get(props.get('source_doc')) or {}).get('doc_date'))
        if when and doc_date and when > doc_date:
            yield Row('L16', 'warn', 'relationship', rel['ref'], person, rel['type'], 'date',
                      f'fecha {when} posterior a «{props.get("source_doc")}» ({doc_date})',
                      'corregir la fecha del hecho o retirarla (cf. qa_integral_adif_date)')


@check('L17', 'arista de contexto sin `source_url`', 'warn')
def l17(ctx):
    for rel in ctx.snap.rels:
        props = rel['props']
        if props.get('source_kind') != 'contexto' or props.get('source_url'):
            continue
        start, end = ctx.snap.endpoints(rel)
        other = ', '.join(k for k in ('url', 'source_doc', 'source_quote') if props.get(k)) or 'ninguna'
        yield Row('L17', 'warn', 'relationship', rel['ref'], start, rel['type'], 'source_url',
                  f'{start} → {end} · otra procedencia presente: {other}',
                  'añadir `source` en el YAML de contexto o retirar en qa061_retiradas.py')


# --- L18-L20 · nodos huérfanos, registro y capas editoriales --------------------

@check('L18', 'Person sin ninguna arista de dominio', 'warn')
def l18(ctx):
    for ref, node in ctx.snap.nodes.items():
        if 'Person' not in node['labels'] or ctx.snap.domain_degree[ref]:
            continue
        props = node['props']
        yield Row('L18', 'warn', 'node', ref, props.get('entity_id'), 'Person', 'degree',
                  f'«{props.get("name")}» solo con menciones ({props.get("mentions")} menciones)',
                  'DETACH DELETE en qa061_retiradas.py')


@check('L19', 'persona con acusación en el grafo y sin fila de registro documentada', 'warn')
def l19(ctx):
    by_person = defaultdict(Counter)
    for rel in ctx.snap.rels:
        if rel['type'] in ACCUSATION_RELS and 'Person' in ctx.snap.labels(rel['start']):
            by_person[ctx.snap.eid(rel['start'])][rel['type']] += 1
    for person, types in sorted(by_person.items()):
        if not person or person in ctx.over.registered:
            continue
        node = ctx.snap.by_id.get(person)
        yield Row('L19', 'warn', 'node', node['ref'] if node else None, person, 'Person', 'estado',
                  ' · '.join(f'{t}×{n}' for t, n in sorted(types.items())) + ' sin fila `estado` con doc',
                  'añadir fila `estado` (status, fecha, fuente) en accusations.yaml')


@check('L20', 'id declarado en feed/cases/patrones que no resuelve a nodo', 'warn')
def l20(ctx):
    declared = []
    for index, item in enumerate(ctx.over.feed):
        ref = f"pipeline/data/ultima_hora/feed.yaml#{item.get('iso', '?')}#{index}"
        declared += [('feed_item', ref, 'entities', e) for e in item.get('entities') or []]
    for case in ctx.over.cases:
        ref = f"api/app/data/cases.yaml#{case.get('slug')}"
        declared += [('case', ref, 'entidades', e.get('id')) for e in case.get('entidades') or []]
        declared += [('case', ref, 'documentos', d) for d in case.get('documentos') or []]
    for patron in ctx.over.patrones:
        ref = f"pipeline/data/patrones/patrones.yaml#{patron.get('slug')}"
        declared += [('patron', ref, 'entities', e) for e in patron.get('entities') or []]
    for kind, ref, field, value in declared:
        known = ctx.snap.docs if field == 'documentos' else ctx.snap.by_id
        if value and value not in known:
            yield Row('L20', 'warn', kind, ref, value, field, field,
                      f'«{value}» no existe en el grafo',
                      'corregir el id o retirar la referencia')


# --- L21 · aliases (061-C) -------------------------------------------------------

ALIAS_LABELS = {'Person', 'Organization', 'Company'}
# Honorifics and titles that, alone, name nobody; and the noise a dangling alias ends in.
HONORIFICS = {'don', 'dona', 'sr', 'sra', 'srta', 'd', 'dna', 'da', 'senor', 'senora', 'excmo',
              'excma', 'ilmo', 'ilma', 'mr', 'mrs', 'ms'}
# A word-connector left hanging at the end («Asesor de»); single letters stay out because
# «Aena S.A.» / «Aerovip C.A.» tokenise to a trailing `a` (Claude review 2026-09-19).
DANGLING = re.compile(r'\s(?:de|del|y|con|por|para|la|el|los|las)\s*$', re.I)
GENERIC_LEAD = re.compile(r'^(?:el|la|los|las|un|una|unos|unas)\s', re.I)


def alias_findings(name, aliases, pseudonym=False):
    """The owner's alias rules of the T0e round (REVISION-T0e §3.6), decidable without a model.

    (a) a lone first name («Miguel» on «Miguel Asesor De Fran»); a surname-first node
        («Gordillo Moreno») fires too — the owner tells them apart in the tanda;
    (b) an extraction artefact: line break, digit, no letters, under three characters, a bare
        honorific, or a dangling connector at the end («Asesor de»; «De Foronda» is a surname);
    (c) a generic description: leads with an article («el asesor de…»).
    A real OCR variant («lon Fernando Antolín») is left alone — that rule needs the corpus.
    """
    name_tokens = [t for t in re.split(r'[^a-z0-9]+', norm(name)) if t]
    first = name_tokens[0] if name_tokens else None
    for alias in aliases or []:
        if not isinstance(alias, str):
            continue
        raw = alias
        flat = norm(alias)
        tokens = [t for t in re.split(r'[^a-z0-9]+', flat) if t]
        if '\n' in raw or re.search(r'\d', flat) or not re.search(r'[a-z]', flat) or len(flat) < 3:
            yield raw, 'artefacto de extracción (salto de línea, dígitos, sin letras o <3 caracteres)'
        elif tokens and all(t in HONORIFICS for t in tokens):
            yield raw, 'solo honorífico o tratamiento'
        elif GENERIC_LEAD.search(flat):
            yield raw, 'descripción genérica (empieza por artículo)'
        elif DANGLING.search(flat):      # «De Foronda» is a surname; «Asesor de» is not
            yield raw, 'fragmento: termina en conector'
        elif (not pseudonym and len(name_tokens) >= 2 and len(tokens) == 1
              and tokens[0] == first and tokens[0] not in HONORIFICS):
            yield raw, 'nombre de pila suelto'


@check('L21', 'alias = nombre de pila suelto, artefacto de extracción o descripción genérica', 'warn')
def l21(ctx):
    for node in ctx.snap.nodes.values():
        labels = set(node['labels'])
        if not labels & ALIAS_LABELS:
            continue
        props = node['props']
        # A pseudonym's aliases («don Casimiro», «el Sr. Casimiro») are the auto's own words
        # and L12 already guards them; the owner's rules are for civil names.
        if not props.get('entity_id') or not props.get('aliases') or props.get('is_pseudonym'):
            continue
        hits = list(alias_findings(props.get('name') or '', props['aliases']))
        if hits:
            label = next(iter(labels & ALIAS_LABELS))
            yield Row('L21', 'warn', 'node', node['ref'], props['entity_id'], label, 'aliases',
                      json.dumps([{'alias': a, 'motivo': m} for a, m in hits], ensure_ascii=False),
                      'alias_deny en relations.yaml tras revisión del dueño (nunca automático)')


# --- L22-L25 · criterios legales por entidad (spec 063) --------------------------

# La prosa nunca puede nombrar una fase más avanzada que la registrada. Sin fila
# `estado` la API sirve la más leve (accusations_loader.py:61), así que ese es el techo.
STATUS_LADDER = _ONT.escalera
DEFAULT_STATUS = 'investigado'
PROCEDURAL_RELS = set(_ONT.procesales)
CASE_RELS = PROCEDURAL_RELS | {'PARTY_TO'}
PRESS_KINDS = {'prensa', 'ultima_hora'}

# Superficie de cada fase. Solo participios y adjetivos: los sustantivos («la acusación
# particular», «la investigación», «el procesamiento») nombran a una parte o a un
# trámite, no atribuyen la fase a la persona.
PROCEDURAL_SURFACES = (
    ('investigado', re.compile(r'\binvestigad[oa]s?\b|\bencausad[oa]s?\b', re.I)),
    ('imputado', re.compile(r'\bimputad[oa]s?\b', re.I)),
    ('procesado', re.compile(r'\bprocesad[oa]s?\b|auto de procesamiento', re.I)),
    ('acusado', re.compile(r'\bacusad[oa]s?\b|\bbanquillo\b|juicio oral', re.I)),
    # El activo («la causa que condenó al fiscal general») tiene por sujeto al tribunal y
    # habla de OTRO: solo cuenta con clítico («lo condenó») o en participio.
    ('condenado', re.compile(r'\bcondenad[oa]s?\b|\bl[eoa]s?\s+conden[oó]\b|\bculpable\b', re.I)),
    ('absuelto', re.compile(r'\babsuelt[oa]s?\b|\bl[eoa]s?\s+absolvi[oó]\b|absoluci[oó]n', re.I)),
    ('archivado', re.compile(r'\barchivad[oa]s?\b|\bsobrese\w+|archivo de la causa', re.I)),
)
# Una condena recurrida o recurrible es `condenado_no_firme`: un peldaño por debajo.
NO_FIRME = re.compile(r'no\s+firme|recurrid\w+|recurrib\w+|pendiente de recurso|cabe recurso'
                      r'|casaci[oó]n', re.I)
# Conducta delictiva afirmada como hecho consumado. Se exige la TILDE: «pagó» afirma,
# «el pago» es un sustantivo neutro y sin tilde son homógrafos.
CONDUCT = re.compile(
    r'\b(?:cobr|pag|amañ|blanque|ocult|desvi|apropi|embols|lucr)(?:ó|aron|aban|aba)\b'
    r'|\b(?:malvers|soborn|defraud|falsific|amañ)(?:ó|aron)\b'
    r'|\ba cambio de\b|\bmordidas?\b|comisiones ilegales|\bcaja b\b|dinero negro'
    r'|(?:cobro|pago|reparto|amaño) de (?:comisiones|mordidas|contratos)'
    r'|recib(?:ió|ieron)(?:\s+\w+){0,2}\s+comisiones'
    r'|repart(?:ió|ieron)\s+(?:comisiones|mordidas|dinero)', re.I)
# Lo que convierte la conducta en imputación modalizada o atribuida DENTRO de la frase:
# adverbio de presunción, condicional, cita entrecomillada («…» → `"` vía QUOTE_CHARS),
# atribución a una fuente, o marco procesal explícito.
MODALIZED = re.compile(
    r'presunt\w+|supuest\w+|al parecer|seg[uú]n\b|de acuerdo con|"'
    r'|habr[íi]an?\b|hubiera|hubiese|ser[íi]an?\b|podr[íi]an?\b'
    r'|investigad[oa]s? por|imputad[oa]s? por|procesad[oa]s? por|acusad[oa]s? (?:de|por)'
    r'|condenad[oa]s? por|se le atribuy\w+|sostien\w+|afirma\w*|declara\w*\s+probad\w+'
    r'|hechos? probados?|la (?:UCO|UDEF|Fiscal[ií]a|Guardia Civil|polic[ií]a)'
    r'|el (?:auto|informe|atestado|escrito de acusaci[oó]n|sumario)'
    r'|la (?:sentencia|querella|denuncia|acusaci[oó]n)', re.I)
# L25: la atribución nombra al medio y a la fecha, nunca al gremio. Copiada verbatim de
# `specs/061-auditoria-dual-fable-astra/tools-T0e/apply_t0e.py:31`.
VAGUE = re.compile(r'seg[uú]n (?:la|los|las) (?:prensa|medios|informaciones|fuentes period)', re.I)


def _procedural_hits(text):
    """(fase, match) de cada término procesal del texto, con su posición."""
    text = text or ''
    for stage, pattern in PROCEDURAL_SURFACES:
        for match in pattern.finditer(text):
            yield ('condenado_no_firme' if stage == 'condenado' and NO_FIRME.search(text)
                   else stage), match


def procedural_terms(text):
    """Fases procesales que el texto afirma, como peldaños de `STATUS_LADDER` (+ cerrados).

    Pura y reutilizable: L22 la llama por frase y L23 sobre el summary/role entero.
    """
    return {stage for stage, _ in _procedural_hits(text)}


def asserted_procedural_terms(text):
    """Como `procedural_terms`, pero frase a frase y sin las fases negadas o hipotéticas
    («no consta investigada», «no fue acusado»): lo mismo que L22 descarta con NOT_ASSERTED."""
    terms = set()
    for _, _, sentence in segments(text or ''):
        flat = sentence.translate(QUOTE_CHARS)
        terms |= {stage for stage, match in _procedural_hits(flat)
                  if not NOT_ASSERTED.search(flat[:match.start()])}
    return terms


def exceeds(term, registered):
    """¿La fase `term` va más allá del estado registrado?

    Sin registro el techo es `investigado`. Con un estado CERRADO (`absuelto`,
    `archivado`) cualquier fase abierta sobra; al revés nunca agrava.
    """
    registered = registered or DEFAULT_STATUS
    if registered in CLOSED_STATUSES:
        return term not in CLOSED_STATUSES
    if term not in STATUS_LADDER or registered not in STATUS_LADDER:
        return False
    return STATUS_LADDER.index(term) > STATUS_LADDER.index(registered)


def registered_status(over, entity_id):
    """El status de la fila `estado` de accusations.yaml, o None si no hay fila."""
    estado = over.estado(entity_id) or {}
    return (estado.get('status') or '').strip().lower() or None


def feed_by_entity(over):
    """entity_id → [(ref, campo, texto)] de los ítems de última hora que lo declaran."""
    index = defaultdict(list)
    for position, item in enumerate(over.feed):
        ref = f"pipeline/data/ultima_hora/feed.yaml#{item.get('iso', '?')}#{position}"
        for field in ('headline', 'dek'):
            if item.get(field):
                for entity_id in item.get('entities') or []:
                    index[entity_id].append((ref, field, item[field]))
    return index


def sentence_problems(sentence, registered):
    """Los defectos L22 de UNA frase: fase por encima del registro y conducta cruda."""
    flat = (sentence or '').translate(QUOTE_CHARS)
    problems, seen = [], set()
    for stage, match in _procedural_hits(flat):
        # «no consta imputado», «si fuera condenado»: la frase no lo afirma (cf. L5).
        if stage in seen or NOT_ASSERTED.search(flat[:match.start()]):
            continue
        if exceeds(stage, registered):
            seen.add(stage)
            problems.append(f'término «{match.group(0)}» = fase «{stage}» por encima '
                            f'del registro «{registered}»')
    conduct = next((m for m in CONDUCT.finditer(flat)
                    if not NOT_ASSERTED.search(flat[:m.start()])), None)
    if conduct and not MODALIZED.search(flat):
        problems.append(f'conducta afirmada sin modalizar ni atribuir: «{conduct.group(0)}»')
    return problems


@check('L22', 'prosa con fase procesal por encima del registro, o conducta sin modalizar', 'warn')
def l22(ctx):
    """Conducta ≤ registro (AC14 de la 063), sobre summary, role y feed de cada persona
    con causa (fila `estado` o arista ACCUSED_OF/CONVICTED_OF/INVESTIGATED_BY).

    Un hallazgo por frase: `field` dice de dónde sale, `detail` el término y el registro.
    """
    feed = feed_by_entity(ctx.over)
    subjects = {row.get('entity_id') for row in ctx.over.accusations if row.get('estado')}
    for rel in ctx.snap.rels:
        if rel['type'] not in PROCEDURAL_RELS:
            continue
        subjects |= {ctx.snap.eid(r) for r in (rel['start'], rel['end'])
                     if 'Person' in ctx.snap.labels(r)}
    # 063 (2026-09-29): una CONVICTED_OF documentada también fija el techo (Koldo/Ábalos/Aldama: STS 418/2026 firme,
    # fila de registro `acusado` por sus otras piezas) — el máximo de ambos, en la escalera.
    convicted = {}
    for rel in ctx.snap.rels:
        if rel['type'] == 'CONVICTED_OF' and 'Person' in ctx.snap.labels(rel['start']):
            stage = rel['props'].get('status') or 'condenado_no_firme'
            eid = ctx.snap.eid(rel['start'])
            if stage in STATUS_LADDER and (eid not in convicted or
                                           STATUS_LADDER.index(stage) > STATUS_LADDER.index(convicted[eid])):
                convicted[eid] = stage
    for entity_id in sorted(s for s in subjects if s):
        node = ctx.snap.by_id.get(entity_id)
        registered = registered_status(ctx.over, entity_id) or DEFAULT_STATUS
        if entity_id in convicted and registered in STATUS_LADDER and \
                STATUS_LADDER.index(convicted[entity_id]) > STATUS_LADDER.index(registered):
            registered = convicted[entity_id]
        texts = []
        if node is not None:
            texts += [('node', node['ref'], field, node['props'][field])
                      for field in ('summary', 'role')
                      if isinstance(node['props'].get(field), str) and node['props'][field].strip()]
        texts += [('feed_item', ref, field, text) for ref, field, text in feed.get(entity_id, ())]
        for kind, ref, field, text in texts:
            for _, _, sentence in segments(text):
                problems = sentence_problems(sentence, registered)
                if problems:
                    yield Row('L22', 'warn', kind, ref, entity_id, 'Person', field,
                              '; '.join(problems) + f' — «{sentence.strip()[:180]}»',
                              'modalizar/atribuir la frase, o actualizar la fila `estado`')


@check('L23', 'particular sin cargo público con acusación de prensa o prosa procesal', 'warn')
def l23(ctx):
    """El hueco de la 063: una persona que no es figura pública ni consta en el registro,
    pero a la que el grafo o la prosa atribuyen una causa.

    Figura pública = entrada con `role` en `figures.yaml` `entities:` o nodo `is_boe`.
    Los seudónimos judiciales (L6/L12) y los anonimizados (spec 059) quedan fuera.
    """
    figures = {norm(entry.get('name')) for entry in ctx.over.figures
               if isinstance(entry, dict) and entry.get('role') and entry.get('name')}
    registered = {row.get('entity_id') for row in ctx.over.accusations if row.get('estado')}
    causes = defaultdict(set)
    for rel in ctx.snap.rels:
        if rel['type'] not in {'ACCUSED_OF', 'INVESTIGATED_BY'}:
            continue
        kind = rel['props'].get('source_kind')
        if kind not in PRESS_KINDS:
            continue
        for ref in (rel['start'], rel['end']):
            if 'Person' in ctx.snap.labels(ref):
                causes[ctx.snap.eid(ref)].add(f'{rel["type"]} sostenida solo por «{kind}»')
    for node in ctx.snap.persons():
        props = node['props']
        entity_id = props.get('entity_id')
        if not entity_id or entity_id in registered:
            continue
        if props.get('is_pseudonym') or props.get('anonymized') or props.get('is_boe'):
            continue
        if figures & {norm(props.get('name')), norm(props.get('canonical_name')),
                      *(norm(a) for a in props.get('aliases') or [])}:
            continue
        reasons = sorted(causes.get(entity_id, ()))
        terms = sorted(set().union(*(asserted_procedural_terms(props.get(f))
                                     for f in ('summary', 'role'))))
        if terms:
            reasons.append('prosa con fase procesal: ' + ', '.join(terms))
        if reasons:
            yield Row('L23', 'warn', 'node', node['ref'], entity_id, 'Person', 'estado',
                      f'«{props.get("name")}» sin cargo público ni fila `estado` · '
                      + ' · '.join(reasons),
                      'decisión del dueño: documentar con fuente judicial, anonimizar o retirar')


@check('L24', 'familiar sin acusación y sin decisión registrada del dueño', 'warn')
def l24(ctx):
    """Clase A5 del finding `auditoria-riesgo-legal-2026-09-05`: quien solo existe en el
    grafo por el parentesco lo decide el dueño, uno a uno — nunca QA.

    La marca de revisión es la prop de nodo `owner_reviewed: true` o una fila en
    `pipeline/data/context/familiares_revisados.yaml`:

        revisados:
          - {entity_id: Person:fulana-de-tal, decision: conservar-con-papel,
             fecha: '2026-09-21', reason: 'administradora receptora de pagos (UCO f. 120)'}

    `decision` ∈ conservar-con-papel | anonimizar | podar. El fichero puede no existir
    todavía: entonces salen todos como hallazgo.
    """
    reviewed = {row.get('entity_id') for row in ctx.over.familiares_revisados
                if isinstance(row, dict)}
    registered = {row.get('entity_id') for row in ctx.over.accusations if row.get('estado')}
    family, charged = Counter(), set()
    for rel in ctx.snap.rels:
        if rel['type'] != 'FAMILY_OF' and rel['type'] not in CASE_RELS:
            continue
        for ref in (rel['start'], rel['end']):
            if 'Person' not in ctx.snap.labels(ref):
                continue
            if rel['type'] == 'FAMILY_OF':
                family[ctx.snap.eid(ref)] += 1
            else:
                charged.add(ctx.snap.eid(ref))
    for entity_id, ties in sorted(family.items()):
        if not entity_id or entity_id in charged or entity_id in registered or entity_id in reviewed:
            continue
        node = ctx.snap.by_id.get(entity_id)
        props = node['props'] if node else {}
        if props.get('owner_reviewed'):
            continue
        yield Row('L24', 'warn', 'node', node['ref'] if node else None, entity_id, 'Person',
                  'owner_reviewed',
                  f'«{props.get("name")}» solo por parentesco (FAMILY_OF×{ties}), sin decisión',
                  'fila en familiares_revisados.yaml (conservar-con-papel | anonimizar | podar)')


@check('L25', 'atribución vaga («según la prensa», «según los medios»)', 'warn')
def l25(ctx):
    """La atribución nombra al medio y a la fecha: «según Infobae (16-07-2026)»."""
    fix = 'atribuir al medio con fecha: «según <medio> (DD-MM-AAAA)»'
    for node in ctx.snap.nodes.values():
        props = node['props']
        for field in ('summary', 'role', 'description'):
            found = VAGUE.search(props.get(field) or '') if isinstance(props.get(field), str) else None
            if found:
                yield Row('L25', 'warn', 'node', node['ref'], props.get('entity_id'),
                          node['labels'][0], field, f'«{found.group(0)}»', fix)
    for position, item in enumerate(ctx.over.feed):
        ref = f"pipeline/data/ultima_hora/feed.yaml#{item.get('iso', '?')}#{position}"
        for field in ('headline', 'dek'):
            found = VAGUE.search(item.get(field) or '')
            if found:
                yield Row('L25', 'warn', 'feed_item', ref, None, 'feed', field,
                          f'«{found.group(0)}»', fix)


# --- L26 · seudónimos judiciales (spec 070) -------------------------------------------

@check('L26', 'regla pública o fila del registro que empareja un seudónimo con sociedad/organismo o alias', 'error')
def l26(ctx):
    """Denegación por defecto: la retirada es la política del loader, así que ninguna regla
    pública ni fila del registro la repite (`seudonimos.auditar`, lo mismo que el CI).
    Los splits seudónimo→nombre civil quedan fuera hasta la decisión Q2 de la spec."""
    from .seudonimos import auditar
    for hit in auditar(ctx.over.repo):
        yield Row('L26', 'error', 'overlay_row', f'{hit.fichero}:{hit.linea}', hit.seudonimo, hit.clase,
                  hit.clase, hit.motivo,
                  'retirar la regla o neutralizar la fila: la política vive en seudonimos_permitidos.yaml')


class Context:
    def __init__(self, snap, over, as_of, network):
        self.snap, self.over, self.as_of, self.network = snap, over, as_of, network
        self.prose = prose_items(snap, over)
        self.persons = person_matcher(snap)
        self.extra = {'L9_domains': Counter(), 'L9_dead': 0}


def normalise_ids(value):
    return [f'L{int(part.strip().lstrip("Ll")):d}' for part in (value or '').split(',') if part.strip()]


def run(snapshot, out, as_of, only=None, network=False, repo=REPO):
    snap, over = Snapshot(snapshot), Overlays(repo)
    ctx = Context(snap, over, as_of, network)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for cid, spec in CHECKS.items():
        if only and cid not in only:
            continue
        rows = list(spec['fn'](ctx))
        path = out / f'L{int(cid[1:]):02d}.jsonl'
        with path.open('w') as f:
            for row in rows:
                f.write(json.dumps(row._asdict(), ensure_ascii=False, sort_keys=True) + '\n')
        summary[cid] = {'count': len(rows), 'label': spec['label'], 'severity': spec['severity'],
                        'by': dict(Counter(r.type for r in rows).most_common(8))}
    if 'L9' in summary:
        summary['L9']['domains'] = dict(ctx.extra['L9_domains'].most_common())
        summary['L9']['dead_urls'] = ctx.extra['L9_dead'] if network else None
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    manifest = {'generated_at': now(), 'snapshot': str(Path(snapshot).resolve()),
                'snapshot_sha256': digest(json.loads((Path(snapshot) / 'snapshot.json').read_text())),
                'as_of': as_of.isoformat(), 'network': network,
                'nodes': len(snap.nodes), 'relationships': len(snap.rels),
                'prose_items': len(ctx.prose), 'checks': sorted(summary)}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    (out / 'summary.md').write_text(render(summary, manifest))
    return summary


def render(summary, manifest):
    width = max((len(v['label']) for v in summary.values()), default=10)
    lines = [f"# qa-lint 061 — {manifest['as_of']}", '',
             f"Snapshot `{Path(manifest['snapshot']).name}`: {manifest['nodes']} nodos / "
             f"{manifest['relationships']} aristas; {manifest['prose_items']} unidades de prosa.",
             f"Red: {'sí' if manifest['network'] else 'no (solo comprobaciones deterministas)'}.", '',
             f"| check | sev | hits | {'qué mira'.ljust(width)} |",
             f"|---|---|---:|{'-' * (width + 2)}|"]
    for cid in sorted(summary, key=lambda c: int(c[1:])):
        entry = summary[cid]
        lines.append(f"| {cid} | {entry['severity']} | {entry['count']} | {entry['label'].ljust(width)} |")
    total = sum(e['count'] for e in summary.values())
    errors = sum(e['count'] for e in summary.values() if e['severity'] == 'error')
    lines += ['', f'Total {total} hallazgos; {errors} de severidad `error` (guardas).', '']
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot', type=Path, required=True, help='neo4j/<export>/ con nodes+relationships.jsonl')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--as-of', default=date.today().isoformat())
    p.add_argument('--checks', default='', help='subconjunto, p. ej. L3,L6')
    p.add_argument('--fail-on', default='', help='devuelve 1 si esas comprobaciones tienen hits')
    p.add_argument('--repo', type=Path, default=REPO)
    network = p.add_mutually_exclusive_group()
    network.add_argument('--network', action='store_true', help='comprueba también que las URLs viven (L9)')
    network.add_argument('--no-network', action='store_true', help='por defecto: cero peticiones')
    args = p.parse_args()
    summary = run(args.snapshot, args.out, date.fromisoformat(args.as_of),
                  only=set(normalise_ids(args.checks)) or None, network=args.network, repo=args.repo)
    print(json.dumps({k: v['count'] for k, v in summary.items()}, ensure_ascii=False, indent=2))
    guarded = [c for c in normalise_ids(args.fail_on) if summary.get(c, {}).get('count')]
    if guarded:
        print('guardas con hallazgos: ' + ', '.join(guarded))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
