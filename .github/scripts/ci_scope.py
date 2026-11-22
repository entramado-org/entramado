"""Decide whether the long pipeline test step of `data-and-tests` has to run (spec 073).

Prints the decision and its reason, and writes `pesado=true|false` to `$GITHUB_OUTPUT`. The workflow runs the long
step unless the value is exactly `false`, and every doubt here resolves to `true` (fail closed): an unknown event, an
empty or unreadable list of changed paths, a failed query.

Spec 076: it also writes `lento=true|false`, whether the long step includes its slow tier (the tests marked
`@lento`). The workflow leaves the slow tier out only on the exact value `false`. The slow tier is left out
when the change is confined to data and one-off scripts, which those tests either do not read or read through the same
functions the remaining tests exercise; the weekly and the manual run always include it.

The long step ("Tests del pipeline (seudónimos y apply 063)") reads `pipeline/**`, `.github/**` and `Makefile` from the
checkout and builds its environment from `api/pyproject.toml` and `api/uv.lock`. A change confined to paths it never
reads cannot change its result. Stdlib only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

WORKFLOW = 'checks.yml'
# Paths the long step never reads. Anything else (pipeline code and data, the workflow, the Makefile, the API
# environment files, a new top-level file) keeps the long step on.
# `feed.yaml`: the long step audits the real file with the same function the short step "Seudónimos judiciales" runs on
# every change, and that step also fails on a field outside its inventory. `api/tests/`: only its test modules, since
# an `__init__.py` there would turn it into a package named `tests`, the name the pipeline suite imports its own by.
UNRELATED_PREFIXES = ('web/', 'api/app/')
UNRELATED_FILES = ('pipeline/data/ultima_hora/feed.yaml',)


# Spec 076. Paths a change may touch without the slow tier having to run, on top of the unrelated ones: data, the
# pipeline lockfile (the long step builds its environment from `api/`) and the one-off shell scripts (`_` prefix,
# `.sh`: Python cannot import them) other than the ones the suite reads. Every Python file under `pipeline/`, the
# remaining scripts, `.github/`, the Makefile and the API environment files keep the slow tier on, and so does any path
# not listed here.
SLOW_UNRELATED_PREFIXES = ('pipeline/data/', 'neo4j/')
SLOW_UNRELATED_FILES = ('pipeline/uv.lock',)
ONE_OFF_SCRIPTS = 'pipeline/scripts/_'
# Nothing Python can import (`.py`, `.pyc`, `.so`, `.pth`...) is data, wherever it sits: the suffixes are a closed list.
DATA_SUFFIXES = ('.yaml', '.yml', '.json', '.md', '.sh', '.lock')
CODE_WORDS = frozenset(('py', 'pyc', 'pyo', 'pyd', 'pyw', 'pyi', 'pth', 'so', 'dylib', 'dll'))   # in no part of the name
ONE_OFF_SCRIPTS_THE_SUITE_READS = ('pipeline/scripts/_qa063b_',)


def is_unrelated(path: str) -> bool:
    """`path` (repo-relative, as git prints it) is one the long step never reads."""
    if not path or path.startswith('/') or '..' in path.split('/'):
        return False
    if path.startswith('api/tests/'):
        name = path[len('api/tests/'):]
        return '/' not in name and name.startswith('test_') and name.endswith('.py')
    return path in UNRELATED_FILES or path.startswith(UNRELATED_PREFIXES)


def paths_need_long_step(paths) -> bool:
    """True unless `paths` is a non-empty list made only of unrelated paths."""
    paths = list(paths) if paths is not None else []
    return not paths or not all(is_unrelated(p) for p in paths)


def is_unrelated_to_slow_tier(path: str) -> bool:
    """`path` is one whose change does not ask for the slow tier of the long step (spec 076)."""
    if is_unrelated(path):
        return True
    if not path or path.startswith('/') or '..' in path.split('/'):
        return False
    name = path.rsplit('/', 1)[-1]
    parts = name.split('.')
    if not name.endswith(DATA_SUFFIXES) or not parts[0] or CODE_WORDS.intersection(parts[1:]):
        return False   # only plain data files, by a closed list
    if path.startswith(ONE_OFF_SCRIPTS):
        return '/' not in path[len(ONE_OFF_SCRIPTS):] and path.endswith('.sh') \
            and not path.startswith(ONE_OFF_SCRIPTS_THE_SUITE_READS)
    return path in SLOW_UNRELATED_FILES or path.startswith(SLOW_UNRELATED_PREFIXES)


def decide_slow(event: str, paths, base_is_green=lambda: False, base_ref: str = 'main') -> tuple[bool, str]:
    """`(slow tier needed, reason)` for a run whose long step runs (spec 076). Left out only for a pull request into
    `main` or a push, with known changed paths that are all unrelated to the slow tier, starting from a commit whose
    required check succeeded. Every other event (the weekly run, a manual run) and every doubt include it."""
    try:
        if event in ('pull_request', 'push'):
            if not paths:
                return True, 'changed paths unknown or empty'
            if not all(is_unrelated_to_slow_tier(p) for p in paths):
                return True, 'the change touches paths the slow tier watches'
            if event == 'pull_request' and base_ref != 'main':
                return True, 'the pull request does not target main'
            if not base_is_green():
                return True, 'the commit the change starts from has no successful required check'
            return False, 'the change only touches data and one-off scripts'
    except Exception as error:   # fail closed
        return True, f'a check could not be completed ({type(error).__name__})'
    return True, f'event {event!r} always runs the slow tier'


def decide(event: str, mirror: bool, paths, base_is_green=lambda: False, base_ref: str = 'main') -> tuple[bool, str]:
    """`(long step needed, reason)`. `paths` is None when the changed paths could not be computed. `mirror` is the
    repository's own declaration that its `main` only receives commits the canonical repository already tested.
    Skipping on unrelated paths also needs a green base on `main`: the long step must have passed (or been rightly
    skipped) on the commit the change starts from, so a red `main` is not papered over by the next unrelated change.
    A pull request into any other branch always runs the long step: the green of that branch's tip may have been
    earned against a different base."""
    try:
        if event in ('push', 'schedule') and mirror is True:   # the weekly run belongs to the canonical repository
            return False, f'{event} in a declared mirror of the canonical repository'
        if event in ('pull_request', 'push'):
            if not paths:
                return True, 'changed paths unknown or empty'
            if paths_need_long_step(paths):
                return True, 'the change touches paths the long step reads'
            if event == 'pull_request' and base_ref != 'main':
                return True, 'the pull request does not target main'
            if not base_is_green():
                return True, 'the commit the change starts from has no successful required check'
            return False, 'the change only touches paths the long step never reads'
    except Exception as error:   # fail closed: any doubt runs the long step
        return True, f'a check could not be completed ({type(error).__name__})'
    return True, f'unhandled event {event!r}'


def _run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120).stdout


def _gh_json(path: str):
    return json.loads(_run('gh', 'api', path))


def _is_sha(value) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(c in '0123456789abcdef' for c in value) \
        and set(value) != {'0'}


def _changed(base: str, head: str) -> list[str]:
    """Paths that differ between two commits; a rename counts as both its paths."""
    return [p for p in _run('git', 'diff', '--name-only', '--no-renames', '-z', base, head).split('\0') if p]


def base_commit(event: str, payload: dict):
    """The commit this run's change starts from, if it is in the checkout (`fetch-depth: 2`): the base parent of a
    pull request's merge commit, or the previous tip of a push. None otherwise; nothing is fetched."""
    try:
        if event == 'pull_request':   # HEAD is the merge commit of the pull request into its base
            parents = _run('git', 'rev-list', '--parents', '-n', '1', 'HEAD').split()[1:]
            return parents[0] if len(parents) == 2 and _is_sha(parents[0]) else None
        if event == 'push':
            before = payload.get('before')
            if not _is_sha(before):
                return None
            _run('git', 'cat-file', '-e', f'{before}^{{commit}}')
            return before
    except Exception:
        return None
    return None


def changed_paths(base):
    """The changed paths between `base` and HEAD, or None if they cannot be computed."""
    if not base:
        return None
    try:
        return _changed(base, 'HEAD')
    except Exception:
        return None


MAIN_EVENTS = ('push', 'schedule', 'workflow_dispatch')
PULL_REQUEST_EVENTS = ('pull_request', 'pull_request_target')


def check_is_green(repo: str, sha) -> bool:
    """This workflow ran for a push of `sha` to `main`, the answer holds every run of `sha` on `main`, and each one
    completed with `success` (a run reports its latest attempt). The weekly and the manual runs count (spec 076): they
    are the ones that always run the slow tier, so a red one on the commit a change starts from keeps everything on.
    A run of the same commit as the head of a pull request does not count either way: it tested the merge with
    whatever base the pull request had, so it earns no green and takes none away."""
    if not _is_sha(sha):
        return False
    answer = _gh_json(f'repos/{repo}/actions/workflows/{WORKFLOW}/runs?head_sha={sha}&branch=main&per_page=100')
    runs = answer.get('workflow_runs')
    if not isinstance(runs, list) or not runs or answer.get('total_count') != len(runs):   # a partial page proves nothing
        return False
    if not all(isinstance(r, dict) for r in runs):
        return False
    runs = [r for r in runs if r.get('event') not in PULL_REQUEST_EVENTS]
    return all(r.get('head_sha') == sha and r.get('event') in MAIN_EVENTS and r.get('head_branch') == 'main'
               and r.get('status') == 'completed' and r.get('conclusion') == 'success' for r in runs) \
        and any(r.get('event') == 'push' for r in runs)


def main() -> int:
    needed, reason = True, 'the scope could not be computed'
    slow, slow_reason = True, 'the scope could not be computed'
    try:
        event = os.environ.get('GITHUB_EVENT_NAME', '')
        repo = os.environ.get('GITHUB_REPOSITORY', '')
        mirror = os.environ.get('CI_MIRROR', '') == 'true'
        with open(os.environ['GITHUB_EVENT_PATH'], encoding='utf-8') as handle:
            payload = json.load(handle)
        base = base_commit(event, payload)
        paths = changed_paths(base)
        base_ref = ((payload.get('pull_request') or {}).get('base') or {}).get('ref')
        target = base_ref if event == 'pull_request' else 'main'
        green = []   # one query at most, shared by both decisions; a query that fails counts as not green for both

        def base_is_green() -> bool:
            if not green:
                green.append(False)
                green[0] = check_is_green(repo, base) is True
            return green[0]

        needed, reason = decide(event, mirror, paths, base_is_green, target)
        slow, slow_reason = decide_slow(event, paths, base_is_green, target)
        print(f'changed paths: {"unknown" if paths is None else len(paths)}')
    except Exception as error:   # fail closed
        needed, reason = True, f'the scope could not be computed ({type(error).__name__})'
        slow, slow_reason = True, reason
    print(f'long step: {"run" if needed else "skip"} ({reason})')
    if not needed:
        slow = False   # no long step, no tier of it
    else:
        print(f'slow tier: {"run" if slow else "skip"} ({slow_reason})')
    output = os.environ.get('GITHUB_OUTPUT')
    if output:   # if this write fails the step fails, and the workflow runs the long step on any outcome but success
        with open(output, 'a', encoding='utf-8') as handle:
            handle.write(f'pesado={"true" if needed else "false"}\n')
            handle.write(f'lento={"true" if slow else "false"}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
