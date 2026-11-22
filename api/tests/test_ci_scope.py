"""Spec 073: `.github/scripts/ci_scope.py` (when the long pipeline step may be skipped). Synthetic data only."""
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

_PATH = pathlib.Path(__file__).resolve().parents[2] / '.github/scripts/ci_scope.py'
_SPEC = importlib.util.spec_from_file_location('ci_scope', _PATH)
ci_scope = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ci_scope)

FEED = 'pipeline/data/ultima_hora/feed.yaml'
PR_TO_MAIN = {'pull_request': {'base': {'ref': 'main'}}}
REPO = 'example-org/example'
A, B = 'a' * 40, 'b' * 40


def never():
    raise AssertionError('this check must not be consulted here')


def yes():
    return True


def no():
    return False


class PathsTest(unittest.TestCase):
    def test_unrelated_paths(self):
        for path in (FEED, 'web/app/page.tsx', 'api/app/graph_queries.py', 'api/tests/test_redact.py',
                     'api/app/data/cases.yaml', 'api/tests/test_ci_scope.py'):
            with self.subTest(path=path):
                self.assertTrue(ci_scope.is_unrelated(path))

    def test_related_paths(self):
        for path in ('pipeline/src/seudonimos.py', 'pipeline/tests/test_seudonimos.py', 'Makefile',
                     '.github/workflows/checks.yml', '.github/scripts/ci_scope.py', 'api/pyproject.toml',
                     'api/uv.lock', 'pipeline/data/ultima_hora/ultima_hora.yaml', 'pipeline/data/relations/relations.yaml',
                     'pipeline/data/ultima_hora/feed.yaml.bak', 'web', 'webx/a', 'api/apps/x.py', 'README.md', '',
                     '/web/a', 'web/../pipeline/src/seudonimos.py', 'api/app/../../Makefile',
                     'api/tests/__init__.py', 'api/tests/conftest.py', 'api/tests/sub/test_a.py', 'api/tests/test_a.txt',
                     'api/__init__.py'):
            with self.subTest(path=path):
                self.assertFalse(ci_scope.is_unrelated(path))

    def test_a_list_needs_the_long_step_unless_every_path_is_unrelated(self):
        self.assertFalse(ci_scope.paths_need_long_step([FEED]))
        self.assertFalse(ci_scope.paths_need_long_step([FEED, 'web/a.ts', 'api/app/main.py']))
        self.assertTrue(ci_scope.paths_need_long_step([FEED, 'Makefile']))
        self.assertTrue(ci_scope.paths_need_long_step([]))      # nothing changed: not a reason to skip
        self.assertTrue(ci_scope.paths_need_long_step(None))


class DecideTest(unittest.TestCase):
    def test_pull_request(self):   # AC1, AC2, AC7
        self.assertEqual(ci_scope.decide('pull_request', False, [FEED], yes)[0], False)
        self.assertEqual(ci_scope.decide('pull_request', True, [FEED], yes)[0], False)
        self.assertEqual(ci_scope.decide('pull_request', False, [FEED], no)[0], True)
        self.assertEqual(ci_scope.decide('pull_request', True, [FEED], no)[0], True)
        self.assertEqual(ci_scope.decide('pull_request', False, [FEED, 'pipeline/src/a.py'], never)[0], True)
        self.assertEqual(ci_scope.decide('pull_request', False, [], never)[0], True)
        self.assertEqual(ci_scope.decide('pull_request', False, None, never)[0], True)

    def test_a_pull_request_into_another_branch_runs_the_long_step(self):   # AC2
        for ref in ('release', 'Main', '', None, 'refs/heads/main'):
            with self.subTest(ref=ref):
                self.assertEqual(ci_scope.decide('pull_request', False, [FEED], never, ref)[0], True)
        self.assertEqual(ci_scope.decide('pull_request', False, [FEED], yes, 'main')[0], False)

    def test_push_with_unrelated_paths_needs_a_green_previous_commit(self):   # AC3
        self.assertEqual(ci_scope.decide('push', False, ['web/a.ts'], yes)[0], False)
        self.assertEqual(ci_scope.decide('push', False, ['web/a.ts'], no)[0], True)

    def test_push_with_related_unknown_or_empty_paths_runs_the_long_step(self):   # AC4, AC7
        self.assertEqual(ci_scope.decide('push', False, ['Makefile'], never)[0], True)
        self.assertEqual(ci_scope.decide('push', False, ['web/a.ts', 'Makefile'], never)[0], True)
        self.assertEqual(ci_scope.decide('push', False, None, never)[0], True)
        self.assertEqual(ci_scope.decide('push', False, [], never)[0], True)

    def test_a_failed_check_runs_the_long_step(self):   # AC7
        def broken():
            raise RuntimeError('boom')
        for event in ('push', 'pull_request'):
            with self.subTest(event=event):
                needed, reason = ci_scope.decide(event, False, [FEED], broken)
                self.assertTrue(needed)
                self.assertIn('RuntimeError', reason)
                self.assertNotIn('boom', reason)

    def test_only_a_declared_mirror_skips_a_push_outright(self):   # AC6
        self.assertEqual(ci_scope.decide('push', True, ['Makefile'], never)[0], False)
        self.assertEqual(ci_scope.decide('push', True, None, never)[0], False)
        for mirror in (False, None, 'true', 1, 'false', ''):   # anything but the boolean True is not a mirror
            with self.subTest(mirror=mirror):
                self.assertEqual(ci_scope.decide('push', mirror, ['Makefile'], yes)[0], True)

    def test_any_other_event_runs_the_long_step(self):   # AC7
        for event in ('workflow_dispatch', 'pull_request_target', 'merge_group', '', 'PUSH'):
            with self.subTest(event=event):
                self.assertEqual(ci_scope.decide(event, True, [FEED], yes)[0], True)
        self.assertEqual(ci_scope.decide('schedule', False, [FEED], yes)[0], True)   # spec 076: the weekly run


class SlowTierTest(unittest.TestCase):   # spec 076
    DATA = ['pipeline/data/jev/questions.esc.tmpl.json', 'pipeline/data/jev/questions.esc.json',
            'pipeline/data/context/figures.yaml', 'pipeline/uv.lock', 'neo4j/bump.sh', 'pipeline/scripts/_one_off.sh',
            FEED, 'web/app/page.tsx']

    def test_paths_unrelated_to_the_slow_tier(self):
        for path in self.DATA:
            with self.subTest(path=path):
                self.assertTrue(ci_scope.is_unrelated_to_slow_tier(path))

    def test_paths_the_slow_tier_watches(self):
        for path in ('pipeline/src/seudonimos.py', 'pipeline/tests/test_seudonimos.py', 'pipeline/tests/_vigilancia.py',
                     'pipeline/scripts/seudonimos_check.py', 'pipeline/scripts/_qa063b_gate.sh',
                     'pipeline/scripts/_migracion_070_citas.py', 'pipeline/scripts/_dir/x.sh', 'pipeline/pyproject.toml',
                     'pipeline/scripts/__init__.py', 'pipeline/scripts/_reposicion_071.py', 'pipeline/scripts/_new.py',
                     'pipeline/scripts/_qa063b_dev.sh', 'pipeline/scripts/run.sh', 'pipeline/data/conftest.py',
                     'neo4j/sitecustomize.py', 'pipeline/scripts/_x.sh.py', 'neo4j/__init__.abi3.so',
                     'neo4j/__init__.pyc', 'pipeline/data/x.pth', 'pipeline/data/mod.so', 'pipeline/data/noext',
                     'pipeline/data/a.py.yaml', 'pipeline/data/.yaml',
                     'pipeline/data/a.so.json', 'pipeline/data/x.pth.md', 'neo4j/x.cpython-312-darwin.so', 'pipeline/data/x.YAML',
                     '.github/scripts/ci_nivel.py', '.github/workflows/checks.yml', 'Makefile', 'api/uv.lock',
                     'api/pyproject.toml', 'README.md', 'new_top_level.py', 'pipeline/data/../src/x.py',
                     '/pipeline/data/x.yaml', '', 'pipeline/datax.yaml', 'neo4j'):
            with self.subTest(path=path):
                self.assertFalse(ci_scope.is_unrelated_to_slow_tier(path))

    def test_it_is_left_out_only_for_data_changes_on_a_green_base(self):
        for event in ('pull_request', 'push'):
            with self.subTest(event=event):
                self.assertEqual(ci_scope.decide_slow(event, self.DATA, yes)[0], False)
                self.assertEqual(ci_scope.decide_slow(event, self.DATA, no)[0], True)
                self.assertEqual(ci_scope.decide_slow(event, self.DATA + ['pipeline/src/schema.py'], never)[0], True)
                self.assertEqual(ci_scope.decide_slow(event, None, never)[0], True)
                self.assertEqual(ci_scope.decide_slow(event, [], never)[0], True)

    def test_a_pull_request_into_another_branch_includes_it(self):
        self.assertEqual(ci_scope.decide_slow('pull_request', self.DATA, never, 'release')[0], True)

    def test_a_failed_check_includes_it(self):
        def boom():
            raise RuntimeError('boom')
        self.assertEqual(ci_scope.decide_slow('push', self.DATA, boom)[0], True)

    def test_the_weekly_the_manual_and_any_other_event_include_it(self):
        for event in ('schedule', 'workflow_dispatch', 'merge_group', '', None):
            with self.subTest(event=event):
                self.assertEqual(ci_scope.decide_slow(event, self.DATA, never)[0], True)

    def test_a_declared_mirror_skips_its_weekly_run(self):
        self.assertEqual(ci_scope.decide('schedule', True, None, never)[0], False)
        self.assertEqual(ci_scope.decide('schedule', False, None, never)[0], True)
        self.assertEqual(ci_scope.decide('workflow_dispatch', True, None, never)[0], True)


class GitTest(unittest.TestCase):
    """`base_commit` and `changed_paths` on a throwaway repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)
        os.chdir(self.dir)
        self.git('init', '-q', '-b', 'main')
        self.write('web/a.ts', 'a')
        self.write('Makefile', 'm')
        self.first = self.commit('first')

    def git(self, *args):
        env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid',
               'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid', 'GIT_CONFIG_GLOBAL': os.devnull,
               'GIT_CONFIG_NOSYSTEM': '1'}
        return subprocess.run(('git', *args), check=True, capture_output=True, text=True, env=env).stdout.strip()

    def write(self, path, text):
        target = self.dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def test_push(self):
        self.write('web/a.ts', 'b')
        self.commit('second')
        base = ci_scope.base_commit('push', {'before': self.first})
        self.assertEqual(base, self.first)
        self.assertEqual(ci_scope.changed_paths(base), ['web/a.ts'])

    def test_a_rename_counts_as_both_paths(self):   # AC8
        self.git('mv', 'Makefile', 'web/Makefile')
        self.commit('rename')
        self.assertEqual(sorted(ci_scope.changed_paths(self.first)), ['Makefile', 'web/Makefile'])
        self.assertTrue(ci_scope.paths_need_long_step(ci_scope.changed_paths(self.first)))

    def test_a_previous_commit_that_is_not_in_the_checkout_gives_no_paths(self):   # AC7
        for before in ('0' * 40, 'e' * 40, '', None, self.first[:12], self.first.upper(), 'HEAD', '--all'):
            with self.subTest(before=before):
                base = ci_scope.base_commit('push', {'before': before})
                self.assertIsNone(base)
                self.assertIsNone(ci_scope.changed_paths(base))
        self.assertIsNone(ci_scope.base_commit('push', {}))

    def test_pull_request_uses_the_base_parent_of_the_merge_commit(self):
        self.git('checkout', '-q', '-b', 'topic')
        self.write(FEED, 'items: []')
        self.commit('topic')
        self.git('checkout', '-q', 'main')
        self.write('web/b.ts', 'b')
        main_tip = self.commit('main moves')
        self.git('merge', '-q', '--no-ff', '-m', 'merge', 'topic')
        base = ci_scope.base_commit('pull_request', {})
        self.assertEqual(base, main_tip)
        self.assertEqual(ci_scope.changed_paths(base), [FEED])

    def test_pull_request_on_a_commit_that_is_not_a_merge_gives_no_base(self):   # AC7
        self.assertIsNone(ci_scope.base_commit('pull_request', {}))
        self.assertIsNone(ci_scope.base_commit('workflow_dispatch', {'before': self.first}))


GREEN = {'path': '.github/workflows/checks.yml@main', 'head_sha': B, 'event': 'push', 'head_branch': 'main',
         'status': 'completed', 'conclusion': 'success'}


class CheckTest(unittest.TestCase):
    def green(self, answer, sha=B):
        def fake(path):
            self.assertEqual(path, f'repos/{REPO}/actions/workflows/checks.yml/runs?head_sha={B}&branch=main&per_page=100')
            return answer
        with mock.patch.object(ci_scope, '_gh_json', fake):
            return ci_scope.check_is_green(REPO, sha)

    def test_green(self):
        self.assertTrue(self.green({'total_count': 1, 'workflow_runs': [GREEN]}))
        self.assertTrue(self.green({'total_count': 2, 'workflow_runs': [GREEN, {**GREEN, 'path': 'x'}]}))
        for run in ({'event': 'pull_request'}, {'event': 'pull_request', 'conclusion': 'failure'},
                    {'event': 'pull_request_target', 'status': 'in_progress', 'conclusion': None}):
            with self.subTest(run=run):   # a run of a pull request with the same head neither earns nor removes it
                self.assertTrue(self.green({'total_count': 2, 'workflow_runs': [GREEN, {**GREEN, **run}]}))
        for event in ('schedule', 'workflow_dispatch'):   # spec 076: the runs that always include the slow tier
            with self.subTest(event=event):
                self.assertTrue(self.green({'total_count': 2, 'workflow_runs': [GREEN, {**GREEN, 'event': event}]}))

    def test_not_green(self):
        cases = {
            'no run': {'total_count': 0, 'workflow_runs': []},
            'green push, red weekly run': {'total_count': 2, 'workflow_runs': [
                GREEN, {**GREEN, 'event': 'schedule', 'conclusion': 'failure'}]},
            'green push, red manual run': {'total_count': 2, 'workflow_runs': [
                GREEN, {**GREEN, 'event': 'workflow_dispatch', 'conclusion': 'failure'}]},
            'green push, weekly run in progress': {'total_count': 2, 'workflow_runs': [
                GREEN, {**GREEN, 'event': 'schedule', 'status': 'in_progress', 'conclusion': None}]},
            'weekly run alone': {'total_count': 1, 'workflow_runs': [{**GREEN, 'event': 'schedule'}]},
            'red push and a green run of a pull request': {'total_count': 2, 'workflow_runs': [
                {**GREEN, 'conclusion': 'failure'}, {**GREEN, 'event': 'pull_request'}]},
            'green push and a run of an unknown event': {'total_count': 2, 'workflow_runs': [
                GREEN, {**GREEN, 'event': 'merge_group'}]},
            'green push and something that is not a run': {'total_count': 2, 'workflow_runs': [GREEN, None]},
            'red run under another spelling of the path': {'total_count': 2, 'workflow_runs': [
                GREEN, {**GREEN, 'path': '.github/workflows/checks.yml', 'conclusion': 'failure'}]},
            'not a run': {'total_count': 1, 'workflow_runs': [None]},
            'failed': {'total_count': 1, 'workflow_runs': [{**GREEN, 'conclusion': 'failure'}]},
            'running': {'total_count': 1, 'workflow_runs': [{**GREEN, 'status': 'in_progress', 'conclusion': None}]},
            'one of two cancelled': {'total_count': 2, 'workflow_runs': [GREEN, {**GREEN, 'conclusion': 'cancelled'}]},
            'run of a pull request': {'total_count': 1, 'workflow_runs': [{**GREEN, 'event': 'pull_request'}]},
            'another branch': {'total_count': 1, 'workflow_runs': [{**GREEN, 'head_branch': 'release'}]},
            'another commit': {'total_count': 1, 'workflow_runs': [{**GREEN, 'head_sha': A}]},
            'partial page': {'total_count': 101, 'workflow_runs': [GREEN] * 100},
            'no count': {'workflow_runs': [GREEN]},
            'no list': {'total_count': 1},
            'empty answer': {},
        }
        for name, answer in cases.items():
            with self.subTest(case=name):
                self.assertFalse(self.green(answer))

    def test_without_a_commit_nothing_is_queried(self):
        with mock.patch.object(ci_scope, '_gh_json', lambda path: never()):
            for sha in (None, '', '0' * 40, 'HEAD', B[:12]):
                with self.subTest(sha=sha):
                    self.assertFalse(ci_scope.check_is_green(REPO, sha))


class MainTest(unittest.TestCase):
    def run_main(self, env, payload=PR_TO_MAIN, **patches):
        with tempfile.TemporaryDirectory() as tmp:
            out, event = pathlib.Path(tmp) / 'out', pathlib.Path(tmp) / 'event.json'
            event.write_text(json.dumps(payload))
            full = {'GITHUB_OUTPUT': str(out), 'GITHUB_EVENT_PATH': str(event), 'GITHUB_REPOSITORY': REPO, **env}
            with mock.patch.dict(os.environ, full, clear=True), mock.patch.multiple(ci_scope, **patches), \
                    mock.patch('builtins.print'):
                self.assertEqual(ci_scope.main(), 0)
            return out.read_text()

    def test_output(self):   # AC7, AC10
        skip = dict(base_commit=lambda e, p: A, changed_paths=lambda b: [FEED], check_is_green=lambda r, s: True)
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'pull_request'}, **skip), 'pesado=false\nlento=false\n')
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'workflow_dispatch'}, **skip), 'pesado=true\nlento=true\n')
        self.assertEqual(self.run_main({}, **skip), 'pesado=true\nlento=true\n')
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_EVENT_PATH': '/nonexistent'}, **skip),
                         'pesado=true\nlento=true\n')
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'pull_request'}, payload={}, **skip), 'pesado=true\nlento=true\n')
        other = {'pull_request': {'base': {'ref': 'release'}}}
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'pull_request'}, payload=other, **skip), 'pesado=true\nlento=true\n')
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'push'}, payload={}, **skip), 'pesado=false\nlento=false\n')
        broken = dict(skip, base_commit=mock.Mock(side_effect=RuntimeError('boom')))
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'pull_request'}, **broken), 'pesado=true\nlento=true\n')

    def test_output_of_the_slow_tier(self):   # spec 076
        data = dict(base_commit=lambda e, p: A, changed_paths=lambda b: ['pipeline/data/context/figures.yaml'])
        calls = []

        def green(repo, sha):
            calls.append(sha)
            return True
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'pull_request'}, check_is_green=green, **data),
                         'pesado=true\nlento=false\n')
        self.assertEqual(calls, [A])   # one query serves both decisions
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'pull_request'}, check_is_green=lambda r, s: False, **data),
                         'pesado=true\nlento=true\n')
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'schedule'}, check_is_green=green, **data),
                         'pesado=true\nlento=true\n')
        calls.clear()

        def timeout_then_green(repo, sha):
            calls.append(sha)
            if len(calls) == 1:
                raise TimeoutError
            return True
        web = dict(data, changed_paths=lambda b: ['web/app/page.tsx'])
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'pull_request'}, check_is_green=timeout_then_green, **web),
                         'pesado=true\nlento=true\n')
        self.assertEqual(calls, [A])   # the failed query is the answer for both decisions
        code = dict(data, changed_paths=lambda b: ['pipeline/src/seudonimos.py'])
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'push'}, payload={}, check_is_green=green, **code),
                         'pesado=true\nlento=true\n')

    def test_the_mirror_flag_is_the_exact_word_true(self):   # AC6
        patches = dict(base_commit=lambda e, p: None, changed_paths=lambda b: None,
                       check_is_green=lambda r, s: False)
        self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'push', 'CI_MIRROR': 'true'}, **patches), 'pesado=false\nlento=false\n')
        for value in ('True', '1', 'yes', '', ' true'):
            with self.subTest(value=value):
                self.assertEqual(self.run_main({'GITHUB_EVENT_NAME': 'push', 'CI_MIRROR': value}, **patches),
                                 'pesado=true\nlento=true\n')


if __name__ == '__main__':
    unittest.main()
