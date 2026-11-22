"""Integral QA: frozen census, immutable batches and evidence-gated double readings.

Never executes graph mutations. The historical qa_audit ledger remains untouched.
Use `python -m src.qa_integral --help` from pipeline/.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import uuid

MODEL = 'gpt-6-astra'
EFFORT = 'high'
TECH_LABELS = {'Chunk', 'Document'}
TECH_RELS = {'MENTIONED_IN', 'PART_OF', 'ABOUT'}
# Explicit technical properties; unknown fields default to substantive review.
TECH_PROPS = {'embedding', 'degree', 'betweenness', 'closeness', 'pagerank', 'community',
              'chunk_index', 'chunk_id', 'doc_id', 'entity_id', 'claim_id', 'id',
              'created_at', 'updated_at', 'ingested_at', 'source_chunk_id'}
TEXT_PROPS = {'summary', 'description', 'text', 'note', 'role', 'boe_nota', 'boe_incidencias'}
HIGH = {'ACCUSED_OF', 'CONVICTED_OF', 'PAID', 'RECEIVED', 'ASSERTS', 'DISPUTES'}  # ontologia-ok: riesgo del censo 060


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def jsonl(path):
    with Path(path).open() as f:
        for line in f:
            yield json.loads(line)


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('x') as f:
            f.write(canonical(value) + '\n');f.flush();os.fsync(f.fileno())
        # Atomic publish, without rename's accidental overwrite behavior.
        os.link(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()


def connect(root):
    db = sqlite3.connect(Path(root) / 'census.sqlite')
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def segments(value):
    """Lossless spans, not an assertion that a sentence is semantically atomic.

    Readers must enumerate every atomic assertion inside each span. Decimal amounts
    and abbreviations may stay together; no text can fall outside the denominator.
    """
    if not isinstance(value, str) or not value:
        return [(0, 0, value)]
    starts = [0] + [m.end() for m in re.finditer(r'(?<=[.!?;])\s+(?=[A-ZÁÉÍÓÚÑ¿¡])|\n+', value)]
    return [(a, b, value[a:b]) for a, b in zip(starts, starts[1:] + [len(value)]) if b > a]


def leaves(value, path=''):
    if isinstance(value, dict) and value:
        for k in sorted(value):
            yield from leaves(value[k], path + '/' + k.replace('~', '~0').replace('/', '~1'))
    elif isinstance(value, list) and value:
        for i, x in enumerate(value):
            yield from leaves(x, path + '/' + str(i))
    else:
        yield path, value


def inventory(root, snapshot, seed, presentation=None, history=None):
    root, snapshot = Path(root), Path(snapshot)
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'manifest.json').exists():
        raise ValueError('census already exists; never overwrite a frozen universe')
    if (root/'census.sqlite').exists():
        # No published manifest means initialization did not complete. Preserve the
        # interrupted database for diagnosis, then retry from the immutable snapshot.
        (root/'census.sqlite').rename(root/f'census.interrupted-{uuid.uuid4().hex}.sqlite')
    if not re.fullmatch('[a-f0-9]{64}', seed.get('sha256', '')) or not seed.get('version'):
        raise ValueError('seed version and full SHA-256 required')
    nodes = list(jsonl(snapshot / 'nodes.jsonl'))
    rels = list(jsonl(snapshot / 'relationships.jsonl'))
    completion=json.loads((snapshot/'snapshot.json').read_text())
    for name,records in [('nodes',nodes),('relationships',rels)]:
        if completion[name]['count'] != len(records) or completion[name]['sha256'] != file_hash(snapshot/f'{name}.jsonl'):
            raise ValueError('incomplete or altered snapshot export')
    if not seed.get('artifact_path'):
        raise ValueError('seed artifact_path required for a new census')
    if file_hash(seed['artifact_path']) != seed['sha256']:
        raise ValueError('seed archive checksum mismatch')
    by_ref = {n['ref']: n for n in nodes}
    if len(by_ref) != len(nodes) or len({r['ref'] for r in rels}) != len(rels):
        raise ValueError('duplicate physical record locator; snapshot invalid')
    if any(r['start'] not in by_ref or r['end'] not in by_ref for r in rels):
        raise ValueError('snapshot has dangling relationship endpoints')
    presentation = presentation or {}
    old = defaultdict(list)
    for u in history or []:
        loc = u.get('locator', {})
        if u.get('unit_kind') == 'edge':
            old[(loc.get('source_id'), loc.get('rel_type'), loc.get('target_id'))].append(
                {k: u.get(k) for k in ('unit_id', 'status', 'defecto', 'audit_run')})
        elif u.get('unit_kind') == 'node-identity':
            old[(loc.get('entity_id'),)].append({k: u.get(k) for k in ('unit_id', 'status', 'defecto', 'audit_run')})
    schema = '''
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
    '''
    db = connect(root)
    db.executescript(schema)
    object_counts, prop_counts = Counter(), Counter()
    unit_ids = []
    def add_unit(oid, kind, field, value, technical, risk, prior):
        uid = digest([seed['sha256'], oid, kind, field, value])
        db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior) VALUES (?,?,?,?,?,?,?,?)',
                   (uid, oid, kind, field, canonical(value), technical, risk, canonical(prior)))
        unit_ids.append(uid)
    for objkind, records in [('node', nodes), ('relationship', rels)]:
        for obj in records:
            props = obj['props']
            labels = set(obj.get('labels', []))
            technical = bool(labels & TECH_LABELS) if objkind == 'node' else obj['type'] in TECH_RELS
            typ = '|'.join(sorted(labels)) if objkind == 'node' else obj['type']
            entity = props.get('entity_id') or props.get('claim_id') or props.get('doc_id') or props.get('chunk_id')
            provenance = props.get('source_kind') or 'unspecified'
            oid = digest([seed['sha256'], objkind, obj['ref']])
            payload = dict(obj)
            if objkind == 'relationship':
                payload['start_entity'] = by_ref[obj['start']]['props'].get('entity_id')
                payload['end_entity'] = by_ref[obj['end']]['props'].get('entity_id')
                payload['start_name'] = by_ref[obj['start']]['props'].get('name')
                payload['end_name'] = by_ref[obj['end']]['props'].get('name')
                prior = old[(payload['start_entity'], typ, payload['end_entity'])]
                content = [typ, digest(by_ref[obj['start']]), digest(by_ref[obj['end']]), props]
            else:
                prior = old[(entity,)]
                content = [sorted(labels), props]
            db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                       (oid, objkind, obj['ref'], entity, typ, provenance, technical, digest(content), canonical(payload)))
            object_counts[objkind] += 1
            risk = 100 if typ in HIGH else 80 if 'Person' in labels else 60
            kind = 'technical-link' if technical and objkind == 'relationship' else 'technical-node' if technical else 'edge' if objkind == 'relationship' else 'claim' if 'Claim' in labels else 'identity'
            identity = {k: props[k] for k in ('name', 'aliases', 'entity_id', 'claim_id', 'text', 'is_pseudonym', 'anonymized') if k in props}
            add_unit(oid, kind, '', {'type': typ, 'identity': identity} if objkind == 'node' else {'type': typ, 'start': payload['start_entity'], 'end': payload['end_entity'], 'props': props, 'public_label': presentation.get(typ)}, technical, risk, prior)
            for prop, value in sorted(props.items()):
                prop_counts[objkind] += 1
                techprop = technical or prop in TECH_PROPS
                parts = [('', value)] if techprop else list(leaves(value))
                for subpath, leaf in parts:
                    spans = segments(leaf) if prop in TEXT_PROPS and isinstance(leaf, str) else [(0, 0, leaf)]
                    for start, end, span in spans:
                        field = '/' + prop.replace('~','~0').replace('/','~1') + subpath + (f'@{start}:{end}' if end else '')
                        ukind = 'technical-property' if techprop else 'summary' if prop == 'summary' else 'attribute'
                        add_unit(oid, ukind, field, span, techprop, risk + (10 if prop in {'amount','status','firme','date','role'} else 0), prior)
    manifest = {'schema': 1, 'created_at': now(), 'seed': seed, 'snapshots': {k: file_hash(snapshot / f'{k}.jsonl') for k in ('nodes', 'relationships')},
                'snapshot_path': str(snapshot.resolve()), 'counts': dict(object_counts), 'properties': dict(prop_counts),
                'units': len(unit_ids), 'universe_sha256': digest(sorted(unit_ids)), 'model': MODEL, 'effort': EFFORT,
                'presentation': presentation, 'previous_results_used_as_evidence': False}
    db.commit()
    db.close()
    write_new(root / 'manifest.json', manifest)
    return manifest


def select_batch(root, batch_id, limit=24, pilot=False, requested_ids=None):
    if not re.fullmatch('[a-zA-Z0-9_-]+', batch_id) or limit < 1:
        raise ValueError('invalid batch id/limit')
    root = Path(root)
    path = root / 'batches' / f'{batch_id}.json'
    db = connect(root)
    with db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM batches WHERE batch_id=?', (batch_id,)).fetchone():
            raise ValueError('batch already exists; resume it, never replace it')
        if path.exists():
            # Recover the exact file written before an interrupted DB commit.
            payload=json.loads(path.read_text())
            manifest=json.loads((root/'manifest.json').read_text())
            if payload.get('batch_id') != batch_id or payload.get('universe_sha256') != manifest['universe_sha256'] or len(set(payload['units'])) != len(payload['units']):
                raise ValueError('invalid orphan batch')
            for uid in payload['units']:
                row=db.execute('SELECT state,technical FROM units WHERE unit_id=?',(uid,)).fetchone()
                if not row or row['state']!='pending' or row['technical']:
                    raise ValueError('orphan batch no longer matches pending units')
            db.execute('INSERT INTO batches VALUES (?,?,?)',(batch_id,file_hash(path),str(path.resolve())))
            db.executemany('INSERT INTO assignments VALUES (?,?)',[(uid,batch_id) for uid in payload['units']])
            return payload
        rows = list(db.execute('''SELECT u.*,o.provenance,o.type,o.entity FROM units u JOIN objects o USING(object_id)
          WHERE u.state='pending' AND u.technical=0 AND NOT EXISTS (SELECT 1 FROM assignments a WHERE a.unit_id=u.unit_id)
          ORDER BY u.risk DESC,u.unit_id'''))
        if requested_ids is not None:
            available={r['unit_id']:r for r in rows}
            if pilot or len(requested_ids)>limit or len(set(requested_ids))!=len(requested_ids) or any(uid not in available for uid in requested_ids):
                raise ValueError('explicit units must be unique, pending, unassigned and within limit')
            rows=[available[uid] for uid in requested_ids]
        chosen, seen = [], set()
        def pick(candidates):
            for r in candidates:
                if r['unit_id'] not in seen:
                    chosen.append(r); seen.add(r['unit_id']); return
        if pilot:
            if limit != 24:
                raise ValueError('pilot is exactly 24 units')
            # Greedy coverage, with explicit missing strata in the batch manifest.
            for kind in ('identity', 'edge', 'summary', 'attribute', 'claim'):
                pick(r for r in rows if r['kind'] == kind)
            for provenance in sorted({r['provenance'] for r in rows}):
                if len(chosen) < limit:
                    pick(r for r in rows if r['provenance'] == provenance and r['kind'] == 'edge')
            pick(r for r in rows if r['prior'] != '[]')
            pick(r for r in rows if r['type'] == 'MET_WITH')
        for r in rows:
            if len(chosen) == limit: break
            pick([r])
        payload = {'batch_id': batch_id, 'created_at': now(), 'universe_sha256': json.loads((root / 'manifest.json').read_text())['universe_sha256'],
                   'pilot': pilot, 'units': [r['unit_id'] for r in chosen],
                   'strata': {'kind': dict(Counter(r['kind'] for r in chosen)), 'provenance': dict(Counter(r['provenance'] for r in chosen)), 'prior_overlap': sum(r['prior'] != '[]' for r in chosen)},
                   'missing_kinds': sorted({'identity','edge','summary','attribute','claim'} - {r['kind'] for r in chosen}),
                   'missing_provenance': sorted({r['provenance'] for r in rows} - {r['provenance'] for r in chosen})}
        write_new(path, payload)
        db.execute('INSERT INTO batches VALUES (?,?,?)', (batch_id, file_hash(path), str(path.resolve())))
        db.executemany('INSERT INTO assignments VALUES (?,?)', [(r['unit_id'], batch_id) for r in chosen])
    db.close()
    return payload


def load_batch(root, batch_id):
    db = connect(root)
    row = db.execute('SELECT * FROM batches WHERE batch_id=?', (batch_id,)).fetchone()
    db.close()
    if not row or file_hash(row['path']) != row['checksum']:
        raise ValueError('unknown or altered batch')
    return json.loads(Path(row['path']).read_text())


def normalize(text):
    return ' '.join(str(text).split())


def verify_census(root):
    """Fail closed on altered source inventory or unit payloads before dispatch."""
    root = Path(root)
    manifest = json.loads((root/'manifest.json').read_text())
    db = connect(root)
    try:
        ids = []
        for u in db.execute('SELECT * FROM units'):
            expected = digest([manifest['seed']['sha256'], u['object_id'], u['kind'], u['field'], json.loads(u['value'])])
            if u['unit_id'] != expected:
                raise ValueError('altered census unit')
            ids.append(u['unit_id'])
        if digest(sorted(ids)) != manifest['universe_sha256']:
            raise ValueError('altered census denominator')
        for kind, name in [('node','nodes'),('relationship','relationships')]:
            snapshot = Path(manifest['snapshot_path']) / f'{name}.jsonl'
            if file_hash(snapshot) != manifest['snapshots'][name]:
                raise ValueError('altered source snapshot')
            stored = {r['ref']:json.loads(r['payload']) for r in db.execute('SELECT ref,payload FROM objects WHERE object_kind=?',(kind,))}
            count=0
            for original in jsonl(snapshot):
                obj=stored.get(original['ref'])
                if obj is None or any(obj.get(k) != v for k,v in original.items()):
                    raise ValueError('altered census object')
                count+=1
            if count != len(stored): raise ValueError('altered object denominator')
    finally: db.close()
    return True


def revalidate(root):
    """Recheck accepted results after a validator fix; never delete the attempt trail."""
    root=Path(root); db=connect(root); rejected=[]
    rows=list(db.execute('SELECT * FROM readings'))
    for r in rows:
        unit=db.execute('SELECT * FROM units WHERE unit_id=?',(r['unit_id'],)).fetchone()
        receipt=json.loads(r['receipt'])
        packet=json.loads(Path(receipt.get('packet_path',root/'packets'/f"{r['unit_id']}.json")).read_text())
        packet['image_locators']=packet.get('image_locators',[])[:12]
        ok,why=valid_result(unit,json.loads(r['result']),json.loads(r['receipt']),packet)
        if ok:
            receipt['visual_transcription_requires_triage']=why=='visual-transcription-requires-triage'
            with db:db.execute('UPDATE readings SET receipt=? WHERE unit_id=? AND side=?',(canonical(receipt),r['unit_id'],r['side']))
        if not ok:
            with db:
                db.execute('INSERT INTO events(at,unit_id,event,payload) VALUES (?,?,?,?)',
                           (now(),r['unit_id'],'reading-invalidated',canonical({**dict(r),'reason':why})))
                db.execute('DELETE FROM readings WHERE unit_id=? AND side=?',(r['unit_id'],r['side']))
            rejected.append({'unit_id':r['unit_id'],'side':r['side'],'reason':why})
    db.close()
    for uid in {r['unit_id'] for r in rows}: reconcile(root,uid)
    return rejected


def valid_result(unit, finding, receipt, packet):
    """Agreement alone is insufficient. Validate provenance, actual reads and coverage."""
    if receipt.get('model') != MODEL or receipt.get('effort') != EFFORT or not receipt.get('thread_id') or receipt.get('exit_code') != 0:
        return False, 'invalid-execution'
    if receipt.get('packet_sha256') != digest(packet) or receipt.get('finding_sha256') != digest(finding):
        return False, 'unbound-result'
    if finding.get('unit_id') != unit['unit_id'] or finding.get('verdict') not in {'ok','defect','uncertain'}:
        return False, 'invalid-finding'
    if not finding.get('reason') or not isinstance(finding.get('assertions'), list):
        return False, 'missing-reason-or-assertions'
    if finding['verdict'] == 'uncertain':
        if not finding.get('requested_sources'):
            return False, 'uncertainty-without-actionable-source-request'
        return True, 'documentary-uncertainty'
    if not finding['assertions'] or not finding.get('coverage_complete'):
        return False, 'incomplete-assertions'
    required = {'identity','predicate','direction','modality','event_vs_report_date','amount_currency','provenance','procedural_status','public_presentation','currentness'}
    checks = finding.get('checks', {})
    if not required <= checks.keys() or any(not isinstance(checks[k], str) or not checks[k].strip() for k in required):
        return False, 'incomplete-checklist'
    sources = {s['source_id']: s for s in packet['sources'] if s.get('status') in {'accessible','image-only'}}
    used_sources = set()
    visual_transcription=False
    for assertion in finding['assertions']:
        if not assertion.get('text') or assertion.get('verdict') not in {'ok','defect'} or not assertion.get('evidence'):
            return False, 'unsupported-assertion'
        for ev in assertion['evidence']:
            source = sources.get(ev.get('source_id'))
            used_sources.add(ev.get('source_id'))
            quote = normalize(ev.get('quote', ''))
            page = str(ev.get('page', ''))
            if not source or len(quote) < 12:
                return False, 'quote-not-in-original'
            visual = ev.get('original_page_required') and page in receipt.get('images', {}).get(source['source_id'], [])
            if quote not in normalize(source.get('pages', {}).get(page, '')):
                if not visual:return False, 'quote-not-in-original'
                visual_transcription=True
            if ev.get('original_page_required') and page not in receipt.get('images', {}).get(source['source_id'], []):
                return False, 'original-page-not-inspected'
    if finding['verdict'] == 'ok' and any(a['verdict'] != 'ok' for a in finding['assertions']):
        return False, 'inconsistent-verdict'
    if finding['verdict'] == 'defect' and (not finding.get('defect_code') or not finding.get('correction')):
        return False, 'incomplete-defect'
    if finding['verdict'] == 'defect' and not any(a['verdict']=='defect' for a in finding['assertions']):
        return False, 'defect-without-defective-assertion'
    if finding.get('current_claim') and not any(sources[s].get('consulted_at') and sources[s].get('url') for s in used_sources):
        return False, 'unverified-currentness'
    # A summary/attribute cannot claim complete coverage after omitting a sentence.
    target = json.loads(unit['value'])
    if isinstance(target, str):
        remaining = normalize(target)
        for assertion in sorted(finding['assertions'], key=lambda a: len(a['text']), reverse=True):
            remaining = remaining.replace(normalize(assertion['text']), '')
        if any(c.isalnum() for c in remaining):
            return False, 'uncovered-target-text'
    return True, 'visual-transcription-requires-triage' if visual_transcription else 'evidence-validated'


def reconcile(root, uid):
    db = connect(root)
    readings = {r['side']: (json.loads(r['result']), json.loads(r['receipt'])) for r in db.execute('SELECT * FROM readings WHERE unit_id=?', (uid,))}
    state, why = 'pending', 'missing-reader'
    if set(readings) == {'verifier','arbiter'}:
        (a, ar), (b, br) = readings['verifier'], readings['arbiter']
        if ar['thread_id'] == br['thread_id'] or ar['workspace'] == br['workspace'] or ar['packet_sha256'] != br['packet_sha256']:
            state, why = 'pending', 'independence-failed'
            with db:
                for row in db.execute('SELECT * FROM readings WHERE unit_id=?',(uid,)).fetchall():
                    db.execute('INSERT INTO events(at,unit_id,event,payload) VALUES (?,?,?,?)',
                               (now(),uid,'independence-invalidated',canonical(dict(row))))
                db.execute('DELETE FROM readings WHERE unit_id=?',(uid,))
        elif ar.get('visual_transcription_requires_triage') or br.get('visual_transcription_requires_triage'):
            state, why = 'needs-triage', 'visual-quotation-not-confirmed-by-extracted-text'
        elif {a['verdict'],b['verdict']} == {'uncertain','defect'}:
            state, why = 'disagreement', 'evidenced-defect-versus-documentary-insufficiency'
        elif 'uncertain' in (a['verdict'], b['verdict']):
            state, why = 'uncertainty', 'documentary-insufficiency'
        elif a['verdict'] == b['verdict'] == 'ok':
            # Different semantic decompositions stay open rather than assume agreement.
            if {normalize(x['text']) for x in a['assertions']} != {normalize(x['text']) for x in b['assertions']}:
                state, why = 'disagreement', 'assertion-decomposition-requires-triage'
            else:
                state, why = 'verified', 'double-evidence-gate'
        elif a['verdict'] == b['verdict'] == 'defect' and a.get('defect_code') == b.get('defect_code'):
            state, why = 'needs-triage', 'defect-category-agreed-corrections-require-triage-and-path-a'
        else:
            state, why = 'disagreement', 'independent-verdicts-disagree'
    with db:
        db.execute('UPDATE units SET state=?,resolution=? WHERE unit_id=?', (state, why, uid))
    db.close()
    return state


def archive_presentation_observation(root, uid, finding, receipt, reading_validation=None):
    """Keep out-of-scope observations for blind relation review, never as a gate verdict."""
    observation = finding.get('checks', {}).get('public_presentation', '')
    code = finding.get('defect_code', '')
    if 'TRIAJE_FUERA_DE_ALCANCE:' not in observation and code != 'PUBLIC_LABEL_PROCEDURAL_ESCALATION':
        return None
    db = connect(root)
    unit = db.execute('SELECT * FROM units WHERE unit_id=?', (uid,)).fetchone()
    relations = [r['unit_id'] for r in db.execute(
        "SELECT unit_id FROM units WHERE object_id=? AND kind='edge'", (unit['object_id'],))]
    db.close()
    candidate = {'status':'open', 'candidate_code':code or 'PUBLIC_PRESENTATION_OUT_OF_SCOPE',
                 'origin_unit_id':uid, 'relation_unit_ids':relations,
                 'attempt_id':receipt['attempt_id'], 'finding_sha256':digest(finding),
                 'packet_sha256':receipt['packet_sha256'], 'observation':observation,
                 'finding':finding, 'next_step':'Two blind readers of the relation unit; no global label change.',
                 'production_applied':False}
    path = Path(root)/'triage'/'presentation'/f"{receipt['attempt_id']}.json"
    if path.exists():
        if json.loads(path.read_text()) != candidate:
            raise ValueError('cannot overwrite presentation candidate')
    else:
        write_new(path,candidate)
    if reading_validation is not None:
        validation_path = Path(root)/'triage'/'presentation-validation'/f"{receipt['attempt_id']}.json"
        validation = {'attempt_id':receipt['attempt_id'], **reading_validation,
                      'note':'Candidate only; origin validation does not establish a presentation defect.'}
        if validation_path.exists():
            if json.loads(validation_path.read_text()) != validation:
                raise ValueError('cannot overwrite candidate origin validation')
        else:
            write_new(validation_path,validation)
    return candidate


def record(root, uid, side, finding, receipt, packet):
    if side not in {'verifier','arbiter'}:
        raise ValueError('invalid reader side')
    db = connect(root)
    unit = db.execute('SELECT * FROM units WHERE unit_id=?', (uid,)).fetchone()
    if unit is None:
        raise ValueError('unit outside census')
    ok, why = valid_result(unit, finding, receipt, packet)
    receipt={**receipt,'visual_transcription_requires_triage':why=='visual-transcription-requires-triage'}
    with db:
        db.execute('INSERT INTO events(at,unit_id,event,payload) VALUES (?,?,?,?)', (now(), uid, 'reading-accepted' if ok else 'reading-rejected', canonical({'side':side,'reason':why,'receipt':receipt})))
        if ok:
            prior = db.execute('SELECT result,receipt FROM readings WHERE unit_id=? AND side=?', (uid,side)).fetchone()
            if prior and (prior['result'] != canonical(finding) or prior['receipt'] != canonical(receipt)):
                raise ValueError('cannot overwrite accepted reading')
            db.execute('INSERT OR IGNORE INTO readings VALUES (?,?,?,?,?)', (uid,side,receipt['attempt_id'],canonical(finding),canonical(receipt)))
    db.close()
    archive_presentation_observation(root, uid, finding, receipt, {'reading_accepted':ok,'validation_reason':why})
    if ok: reconcile(root, uid)
    return ok, why


def technical_checks(root):
    """Structural checks are explicitly separate from the two-reader semantic gate."""
    db = connect(root)
    objects = {r['ref']: json.loads(r['payload']) for r in db.execute("SELECT * FROM objects WHERE object_kind='node'")}
    ids = Counter(n['props'].get('entity_id') for n in objects.values() if n['props'].get('entity_id'))
    results = []
    for row in db.execute('SELECT u.*,o.object_kind,o.payload FROM units u JOIN objects o USING(object_id) WHERE u.technical=1'):
        obj = json.loads(row['payload']); issues = []
        if row['kind'] == 'technical-link':
            a,b = objects[obj['start']],objects[obj['end']]
            expected = {'PART_OF': ('Chunk','Document'), 'MENTIONED_IN': (None,'Chunk'), 'ABOUT': ('Claim',None)}[obj['type']]
            if expected[0] and expected[0] not in a.get('labels',[]): issues.append('wrong-start-label')
            if expected[1] and expected[1] not in b.get('labels',[]): issues.append('wrong-end-label')
            if obj['type'] == 'PART_OF' and a['props'].get('doc_id') != b['props'].get('doc_id'): issues.append('document-mismatch')
            # Structural correctness of MENTIONED_IN does not prove attribution.
            if obj['type'] in {'MENTIONED_IN','ABOUT'}: issues.append('semantic-provenance-not-yet-reviewed')
        elif row['kind'] == 'technical-node':
            key = 'chunk_id' if 'Chunk' in obj.get('labels',[]) else 'doc_id'
            if not obj['props'].get(key): issues.append('missing-identifier')
        elif row['field'] == '/embedding':
            val=json.loads(row['value'])
            if not isinstance(val,list) or not val or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in val): issues.append('invalid-vector')
        elif row['field'] == '/entity_id' and ids[json.loads(row['value'])] > 1:
            issues.append('duplicate-identifier')
        # Every property remains accounted for; checks without a specific validator
        # explicitly stay uncovered rather than receiving a blanket green result.
        elif row['kind'] == 'technical-property': issues.append('no-specific-integrity-validator')
        state = 'technical-open' if issues else 'technical-checked'
        results.append((state,canonical(issues),row['unit_id']))
    with db: db.executemany('UPDATE units SET state=?,resolution=? WHERE unit_id=?', results)
    db.close()
    return dict(Counter(r[0] for r in results))


def report(root):
    root=Path(root); db=connect(root)
    db.execute('BEGIN')
    units=list(db.execute('SELECT u.*,o.entity,o.type,o.provenance,o.object_kind FROM units u JOIN objects o USING(object_id)'))
    rel_entities={}
    for r in db.execute("SELECT object_id,payload FROM objects WHERE object_kind='relationship'"):
        obj=json.loads(r['payload'])
        rel_entities[r['object_id']]={obj.get('start_entity') or f"(no-identifier:{obj['start']})",obj.get('end_entity') or f"(no-identifier:{obj['end']})"}
    by={key:defaultdict(Counter) for key in ('kind','type','provenance','entity')}
    for u in units:
        for key in by:
            names=rel_entities[u['object_id']] if key=='entity' and u['object_kind']=='relationship' else [u[key] or '(no-identifier)']
            for name in names:by[key][name][u['state']] += 1
    domain=[u for u in units if not u['technical']]
    states=Counter(u['state'] for u in domain)
    readings=[json.loads(r['receipt']) for r in db.execute('SELECT receipt FROM readings')]
    reported_defects_in_uncertainty = [r['attempt_id'] for r in db.execute('SELECT attempt_id,result FROM readings')
        if (f := json.loads(r['result'])).get('verdict') == 'uncertain'
        and any(a.get('verdict') == 'defect' for a in f.get('assertions', []))]
    attempts = [json.loads(p.read_text()) for p in (root/'attempts').glob('*/receipt.json')]
    all_usage = Counter()
    for attempt in attempts:
        all_usage.update({k:v for k,v in (attempt.get('usage') or {}).items() if isinstance(v,(int,float))})
    technical_states = Counter(u['state'] for u in units if u['technical'])
    presentation_candidates = [json.loads(p.read_text()) for p in (root/'triage'/'presentation').glob('*.json')]
    open_presentation = sum(c.get('status') == 'open' for c in presentation_candidates)
    consumed=Counter()
    for r in readings:
        consumed.update({k:v for k,v in (r.get('usage') or {}).items() if isinstance(v,(int,float))})
    out={'generated_at':now(),'manifest':json.loads((root/'manifest.json').read_text()),
         'production_attestation':json.loads((root/'production-attestation.json').read_text()) if (root/'production-attestation.json').exists() else None,
         'deployed_presentation':json.loads((root/'deployed-presentation.json').read_text()) if (root/'deployed-presentation.json').exists() else None,
         'domain_total':len(domain),'domain_states':dict(states),
         'technical_states':dict(Counter(u['state'] for u in units if u['technical'])),
         'open_presentation_candidates':open_presentation,
         'uncertainty_attempts_with_reported_defects':reported_defects_in_uncertainty,
         'reported_defects_note':'Assertions inside uncertain results are preserved allegations, not individually evidence-validated defects; triage is required.',
         'accepted_readers':len(readings),'accepted_reader_usage':dict(consumed),
         'accepted_reader_seconds_sum':sum(r.get('elapsed_seconds',0) for r in readings),
         'attempts_total':len(attempts),'all_attempt_usage':dict(all_usage),
         'attempts_without_usage':sum(a.get('usage') is None for a in attempts),
         'by':{k:{name:dict(counts) for name,counts in values.items()} for k,values in by.items()},
         'entity_counting':'Incidence: relationship units count for both endpoints; do not sum entity subtotals as a global denominator.',
         'previous_audit_disagreements':[u['unit_id'] for u in domain if u['prior']!='[]' and u['state'] in {'disagreement','needs-triage','uncertainty'}],
         'closure':{'double_reading_completed':not states.get('pending') and len(domain)>0,
                    'review_completed':not open_presentation and not states.get('pending') and not states.get('disagreement') and not states.get('needs-triage') and not technical_states.get('technical-open') and not technical_states.get('pending') and len(domain)>0,
                    'corrections_published':False,'uncertainties_pending':sum(states.get(s,0) for s in ('uncertainty','disagreement'))},
         'note':'Reviewed includes documentary uncertainties; a queue count does not guarantee truth. Technical-open remains uncovered. Usage of failed attempts is in attempts/*/receipt.json.'}
    db.close()
    # Derived report may be regenerated; immutable source data is never replaced.
    (root/'report.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    (root/'report.md').write_text(f"# Auditoría integral — {out['manifest']['seed']['version']}\n\n"
        f"Censo: {out['manifest']['counts']}; {len(units)} unidades, {len(domain)} de dominio.\n\n"
        f"Estados de dominio: {dict(states)}. Técnicos: {out['technical_states']}.\n\n"
        f"Lecturas aceptadas: {len(readings)}. Uso observado: {dict(consumed)}.\n\n"
        f"Cierre: {out['closure']}. Cola vacía no garantiza información verdadera.\n")
    return {k:v for k,v in out.items() if k not in {'by','manifest','previous_audit_disagreements'}}


def incremental(root, other, output):
    """Multiset comparison preserves indistinguishable duplicates and the initial census."""
    db1,db2=connect(root),connect(other)
    def counter(db):
        rows=list(db.execute('SELECT object_kind,ref,payload FROM objects'))
        # Physical locators differ after re-seeding. Portable hashes deliberately
        # exclude them while multiset counts retain identical twins.
        nodes={r['ref']:json.loads(r['payload']) for r in rows if r['object_kind']=='node'}
        node_hash={ref:digest([sorted(n['labels']),n['props']]) for ref,n in nodes.items()}
        result=Counter()
        for r in rows:
            obj=json.loads(r['payload'])
            h=node_hash[r['ref']] if r['object_kind']=='node' else digest([obj['type'],node_hash[obj['start']],node_hash[obj['end']],obj['props']])
            result[(r['object_kind'],h)]+=1
        return result
    a,b=counter(db1),counter(db2); db1.close();db2.close()
    out={'created_at':now(),'base_manifest_sha256':file_hash(Path(root)/'manifest.json'),'new_manifest_sha256':file_hash(Path(other)/'manifest.json'),
         'added':[{'kind':k[0],'hash':k[1],'count':v} for k,v in (b-a).items()],
         'removed':[{'kind':k[0],'hash':k[1],'count':v} for k,v in (a-b).items()],
         'note':'Removed objects stay in the original denominator. New census has its own pending units.'}
    write_new(output,out); return out


# ── Dual audit, spec 061: what its census and the staged audit (spec 063) still use ──
# The 061 readers and their gate (text/typed validators, `reconcile_dual`, patch rollback) were
# retired by spec 069 F5 — see git history. The 063 escalation still reads Astra under
# `DUAL_MODELS['astra']`.
DUAL_MODELS = {'jev': 'jev-latest', 'astra': 'gpt-6-astra'}
TIER_ORDER = ('T0', 'T1', 'T2', 'T4')

DUAL_SCHEMA = '''
CREATE TABLE IF NOT EXISTS tiers (unit_id TEXT PRIMARY KEY REFERENCES units,
  tier TEXT NOT NULL, rank REAL NOT NULL);
CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY, tier TEXT NOT NULL,
  group_key TEXT NOT NULL, kind TEXT NOT NULL, packet_path TEXT, packet_sha256 TEXT,
  batch_id TEXT, profile TEXT NOT NULL DEFAULT 'normal', UNIQUE(tier,group_key,group_id));
CREATE TABLE IF NOT EXISTS group_units (group_id TEXT NOT NULL REFERENCES groups,
  unit_id TEXT PRIMARY KEY REFERENCES units);
CREATE TABLE IF NOT EXISTS lint_checks (unit_id TEXT NOT NULL, check_id TEXT NOT NULL,
  detail TEXT NOT NULL, PRIMARY KEY(unit_id,check_id));
-- Evidence locators, never audited units: a bounded census stores no Chunk objects,
-- so the packet builder needs the chunk text and the mention index next to it.
CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, doc_id TEXT,
  page_start INTEGER, page_end INTEGER, chunk_index INTEGER, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mentions (entity_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
  PRIMARY KEY(entity_id,chunk_id));
CREATE INDEX IF NOT EXISTS tiers_by_rank ON tiers(tier,rank DESC);
CREATE INDEX IF NOT EXISTS groups_by_batch ON groups(batch_id);
'''


def ensure_dual_schema(conn):
    """Additive tables of the dual census. `readings.side` is free text already."""
    conn.executescript(DUAL_SCHEMA)
    conn.commit()
    return True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    s=p.add_subparsers(dest='cmd', required=True)
    c=s.add_parser('census');c.add_argument('--snapshot',type=Path,required=True);c.add_argument('--seed',type=Path,required=True);c.add_argument('--presentation',type=Path);c.add_argument('--history',type=Path)
    e=s.add_parser('export');e.add_argument('--batch',required=True);e.add_argument('--limit',type=int,default=24);e.add_argument('--pilot',action='store_true');e.add_argument('--units',type=Path)
    s.add_parser('report');s.add_parser('technical');s.add_parser('verify-census');s.add_parser('revalidate')
    i=s.add_parser('incremental');i.add_argument('--other',type=Path,required=True);i.add_argument('--output',type=Path,required=True)
    r=s.add_parser('reopen');r.add_argument('--unit',required=True);r.add_argument('--sources',type=Path,required=True)
    args=p.parse_args()
    if args.cmd=='census': result=inventory(args.root,args.snapshot,json.loads(args.seed.read_text()),json.loads(args.presentation.read_text()) if args.presentation else {},list(jsonl(args.history)) if args.history else [])
    elif args.cmd=='export': result=select_batch(args.root,args.batch,args.limit,args.pilot,json.loads(args.units.read_text()) if args.units else None)
    elif args.cmd=='technical':result=technical_checks(args.root)
    elif args.cmd=='verify-census':result=verify_census(args.root)
    elif args.cmd=='revalidate':result=revalidate(args.root)
    elif args.cmd=='reopen':
        from .qa_integral_sources import reopen_with_sources
        result=reopen_with_sources(args.root,args.unit,json.loads(args.sources.read_text()))
    elif args.cmd=='incremental':result=incremental(args.root,args.other,args.output)
    else:result=report(args.root)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
