"""Tests for the 061 removals script with a recording fake driver: no Neo4j, no writes.

Covers the three properties that make the script safe to hand to a human in Path A:
the dry-run writes nothing, --apply journals exactly what it deletes, and --rollback
replays that journal back into the graph.
"""
import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from pipeline.scripts import qa061_retiradas as R
from pipeline.src.qa_integral_adif_date import AFTER as ADIF_AFTER, BEFORE as ADIF_BEFORE

WRITES = ('DELETE', 'CREATE', 'MERGE', ' SET ')

NODES = [
    {'ref': 'huerfana', 'labels': ['Person'], 'props': {'entity_id': 'Person:huerfana-sola'}},
    {'ref': 'beto', 'labels': ['Person'], 'props': {'entity_id': 'Person:beto-malo'}},
    {'ref': 'ana', 'labels': ['Person'], 'props': {'entity_id': 'Person:ana-buena'}},
    {'ref': 'cohecho', 'labels': ['Crime'], 'props': {'entity_id': 'Crime:cohecho'}},
    {'ref': 'org', 'labels': ['Organization'], 'props': {'entity_id': 'Organization:ayuntamiento'}},
]
RELS = [
    {'ref': 'r_uh_old', 'start': 'beto', 'end': 'cohecho', 'type': 'ACCUSED_OF', 'props': {
        'source_kind': 'ultima_hora', 'date': '2025-01-05', 'url': 'https://prensa.example/viejo'}},
    # Registered defendant: expired, but the registry row keeps it (never planned for removal).
    {'ref': 'r_uh_reg', 'start': 'ana', 'end': 'cohecho', 'type': 'ACCUSED_OF', 'props': {
        'source_kind': 'ultima_hora', 'date': '2025-02-05', 'url': 'https://prensa.example/ana'}},
    {'ref': 'r_ctx', 'start': 'ana', 'end': 'org', 'type': 'WORKS_FOR', 'props': {
        'source_kind': 'contexto', 'note': 'alcaldesa'}},
]
ORPHAN_EDGES = [{'type': 'MENTIONED_IN', 'props': {}, 'outgoing': True,
                 'other_key': 'chunk_id', 'other_value': 'c1'}]


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def single(self):
        return self.rows[0] if self.rows else None


class FakeTx:
    """Answers the script's queries from a tiny in-memory graph, recording every call."""

    def __init__(self, calls, counts, state):
        self.calls, self.counts, self.state = calls, counts, state

    def run(self, query, **params):
        self.calls.append((' '.join(query.split()), params))
        text = query
        if 'COUNT { (n)-[r]-() WHERE NOT type(r) IN $tech } = 0' in text and 'RETURN count(n)' in text:
            return FakeResult([{'n': self.counts['orphans']}])
        if "r.source_kind = 'ultima_hora' RETURN count(r)" in text:
            return FakeResult([{'n': self.counts['ultima_hora']}])
        if 'r.source_url IS NULL RETURN count(r)' in text:
            return FakeResult([{'n': self.counts['contexto']}])
        if 'MATCH (n:Person {entity_id: $entity_id})' in text and 'RETURN labels(n)' in text:
            return FakeResult([{'labels': ['Person'], 'props': {'entity_id': params['entity_id'],
                                                                'name': 'Huérfana Sola'},
                                'edges': [*ORPHAN_EDGES, None]}])
        if 'DETACH DELETE n' in text:
            return FakeResult([{'n': 1}])
        if 'RETURN properties(r) AS props' in text and 'ADIF' not in text:
            if 'r.source_kind' in text or 'type(r) = $type' in text:
                return FakeResult([{'props': {'source_kind': 'ultima_hora', 'date': '2025-01-05'}}])
        if 'DELETE r RETURN count(*)' in text:
            return FakeResult([{'n': 1}])
        if 'r.source_chunk_id=$chunk' in text and 'RETURN properties(r) AS props' in text:
            return FakeResult([{'props': dict(ADIF_BEFORE if self.state['adif'] == 'before' else ADIF_AFTER)}])
        if 'SET r=$desired' in text:
            self.state['adif'] = 'after' if self.state['adif'] == 'before' else 'before'
            return FakeResult([{'n': 1}])
        if 'CREATE (a)-[r:' in text:
            return FakeResult([{'n': 1}])
        return FakeResult([{'n': 0}])


class FakeSession:
    def __init__(self, calls, counts, state):
        self.tx = FakeTx(calls, counts, state)

    def run(self, query, **params):
        return self.tx.run(query, **params)

    def execute_write(self, fn, *a, **kw):
        return fn(self.tx, *a, **kw)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeDriver:
    def __init__(self, counts):
        self.calls, self.counts, self.state = [], counts, {'adif': 'before'}

    def session(self, **kw):
        return FakeSession(self.calls, self.counts, self.state)

    def close(self):
        pass

    def writes(self):
        return [q for q, _ in self.calls if any(w in q for w in WRITES)]


class RetiradasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.snapshot = self.base / 'snapshot'
        self.snapshot.mkdir()
        for name, rows in (('nodes', NODES), ('relationships', RELS)):
            (self.snapshot / f'{name}.jsonl').write_text(
                ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        self.lint = self.base / 'lint'
        self.lint.mkdir()
        self.write_lint()
        self.driver = FakeDriver({'orphans': 1, 'ultima_hora': 2, 'contexto': 1})
        patched = mock.patch('neo4j.GraphDatabase.driver', return_value=self.driver)
        patched.start()
        self.addCleanup(patched.stop)
        registry = mock.patch.object(R, 'registered_defendants', return_value={'Person:ana-buena'})
        registry.start()
        self.addCleanup(registry.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def write_lint(self):
        def rows(name, values):
            (self.lint / name).write_text(''.join(json.dumps(v, ensure_ascii=False) + '\n'
                                                  for v in values))
        rows('L18.jsonl', [{'entity_id': 'Person:huerfana-sola', 'ref': 'huerfana',
                            'detail': 'solo con menciones'}])
        rows('L11.jsonl', [{'ref': 'r_uh_old', 'entity_id': 'Person:beto-malo', 'detail': 'caducada'},
                           {'ref': 'r_uh_reg', 'entity_id': 'Person:ana-buena', 'detail': 'caducada'}])
        rows('L17.jsonl', [{'ref': 'r_ctx', 'entity_id': 'Person:ana-buena', 'detail': 'sin fuente'}])
        rows('L06.jsonl', [{'entity_id': 'Person:david', 'field': 'summary'}])
        rows('L07.jsonl', [{'entity_id': 'Person:beto-malo', 'field': 'summary'}])
        (self.lint / 'manifest.json').write_text(json.dumps(
            {'snapshot': str(self.snapshot), 'as_of': '2026-09-09'}))
        (self.lint / 'summary.json').write_text(json.dumps(
            {'L17': {'count': 1}, 'L18': {'count': 1}}))

    def invoke(self, *args):
        argv = ['qa061_retiradas.py', '--neo4j-uri', 'bolt://localhost:7689', *args]
        with mock.patch.object(sys, 'argv', argv), contextlib.redirect_stdout(None):
            return R.main()

    def test_dry_run_writes_nothing(self):
        self.assertEqual(self.invoke('--lint', str(self.lint)), 0)
        self.assertEqual(self.driver.writes(), [])
        self.assertEqual(list(self.lint.glob('retiradas-*.json')), [])

    def test_plan_respects_the_registry(self):
        snap = R.Snapshot(self.snapshot)
        plan = R.plan_ultima_hora(self.lint, snap, {'Person:ana-buena'}, R.date(2026, 9, 9))
        self.assertEqual([(p['start'], p['expected']) for p in plan], [('Person:beto-malo', 1)])

    def test_apply_journals_exactly_what_it_deletes(self):
        self.assertEqual(self.invoke('--lint', str(self.lint), '--apply'), 0)
        journals = list(self.lint.glob('retiradas-*.json'))
        self.assertEqual(len(journals), 1)
        journal = json.loads(journals[0].read_text())
        self.assertEqual(journal['status'], 'applied')
        self.assertEqual(journal['applied'], {'orphans': 1, 'ultima_hora': 1, 'contexto': 1,
                                              'adif': 1})
        self.assertEqual([n['entity_id'] for n in journal['orphans']], ['Person:huerfana-sola'])
        self.assertEqual(journal['orphans'][0]['edges'], ORPHAN_EDGES)
        self.assertEqual([e['start'] for e in journal['ultima_hora']], ['Person:beto-malo'])
        self.assertEqual(journal['contexto'][0]['props'], [{'source_kind': 'ultima_hora',
                                                            'date': '2025-01-05'}])
        deletes = [q for q in self.driver.writes() if 'DELETE' in q]
        self.assertEqual(len(deletes), 3)
        self.assertTrue(any('DETACH DELETE n' in q for q in deletes))

    def test_apply_aborts_when_the_layer_drifted(self):
        self.driver.counts['contexto'] = 7   # a loader moved the layer after the lint
        with self.assertRaises(ValueError) as caught:
            self.invoke('--lint', str(self.lint), '--apply')
        self.assertIn('contexto', str(caught.exception))

    def test_rollback_replays_the_journal(self):
        self.invoke('--lint', str(self.lint), '--apply')
        journal = next(iter(self.lint.glob('retiradas-*.json')))
        self.driver.calls.clear()
        self.assertEqual(self.invoke('--rollback', str(journal), '--apply'), 0)
        creates = [q for q in self.driver.writes() if 'CREATE (a)-[r:' in q]
        self.assertEqual(len(creates), 3)          # 1 arista técnica + ultima_hora + contexto
        self.assertTrue(any('MERGE (n:`Person`' in q for q in self.driver.writes()))
        self.assertTrue(any('SET r=$desired' in q for q in self.driver.writes()))

    def test_rollback_dry_run_writes_nothing(self):
        self.invoke('--lint', str(self.lint), '--apply')
        journal = next(iter(self.lint.glob('retiradas-*.json')))
        self.driver.calls.clear()
        self.assertEqual(self.invoke('--rollback', str(journal)), 0)
        self.assertEqual(self.driver.writes(), [])

    def test_public_host_is_refused(self):
        with self.assertRaises(SystemExit):
            argv = ['qa061_retiradas.py', '--lint', str(self.lint), '--apply',
                    '--neo4j-uri', 'bolt://entramado.org:7687']
            with mock.patch.object(sys, 'argv', argv), contextlib.redirect_stdout(None):
                R.main()


if __name__ == '__main__':
    unittest.main()
