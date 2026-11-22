"""Blind reader processes (spec 061), reused by the staged audit (spec 063).

GPT-6-Astra through `codex exec`, toolless, fed the text on stdin and constrained by a JSON
schema; the launcher, the process-group guard and the receipt are shared with the Opus and
Astra readers of `qa_esc_*`. The 061 dual loop (Jev beside Astra, `pair`, the gate) was retired
by spec 069 F5 — see git history.
"""
from contextlib import contextmanager
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from .qa_integral import DUAL_MODELS, canonical, digest, now, write_new
from .qa_integral_runner import command as astra_command

QUOTA = re.compile(r'/usage limit|rate limit|429|overloaded|529|hit your|quota', re.IGNORECASE)
# Readers run in their own sessions, so a SIGTERM to the supervisor would release the
# lock while they keep burning quota and the next step launches replacements
# (Codex review 2026-09-09). Track every group and drain it before letting go.
_PROCESS_LOCK = threading.Lock()
_ACTIVE = set()
_STOPPING = threading.Event()


def terminate_readers(grace=10):
    """Drain every owned process group; SIGKILL what does not go quietly."""
    _STOPPING.set()
    with _PROCESS_LOCK:
        active = list(_ACTIVE)
    for proc in active:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    for proc in active:
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()
    return len(active)


@contextmanager
def reader_guard():
    """No reader outlives its supervisor — on a signal or on the way out."""
    def stop(signum, frame):
        terminate_readers()
        raise KeyboardInterrupt(f'supervisor interrupted by signal {signum}')
    try:
        previous = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    except ValueError:
        # Only the main thread can install handlers; the drain on exit still applies.
        previous = {}
    try:
        yield
    finally:
        terminate_readers()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        _STOPPING.clear()


def _spawn(cmd, stdin_text, timeout, stdout_path, stderr_path, cwd):
    with open(stdout_path, 'w') as out, open(stderr_path, 'w') as err:
        with _PROCESS_LOCK:
            if _STOPPING.is_set():
                raise RuntimeError('supervisor stopping; reader not launched')
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=err, text=True,
                                    cwd=str(cwd), start_new_session=True)
            _ACTIVE.add(proc)
        try:
            proc.communicate(stdin_text, timeout=timeout)
            return proc.returncode, None
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            return proc.returncode if proc.returncode is not None else -1, 'reader-timeout'
        finally:
            with _PROCESS_LOCK:
                _ACTIVE.discard(proc)


def _attempt(attempt_dir, side, cmd, stdin_text, effort, schema):
    attempt_dir = Path(attempt_dir)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    write_new(attempt_dir / 'command.json', cmd)
    with gzip.open(attempt_dir / 'input.txt.gz', 'wt') as f:
        f.write(stdin_text)
    # `input_sha256` is the hash of exactly what the model saw on stdin.
    return {'attempt_id': attempt_dir.name, 'side': side, 'model': DUAL_MODELS[side], 'effort': effort,
            'schema_sha256': digest(schema), 'input_sha256': digest(stdin_text), 'started_at': now()}


def _finish(attempt_dir, receipt, result, start, exit_code, error, usage, tool_calls):
    receipt.update({'exit_code': exit_code, 'error': error, 'usage': usage, 'tool_calls': tool_calls,
                    'finding_sha256': digest(result) if result is not None else None,
                    'elapsed_seconds': round(time.monotonic() - start, 3), 'finished_at': now()})
    # Nothing in this contract needs a tool. Any tool use means the reader could have
    # reached another reader, the ledger or the network: the reading is void.
    if tool_calls:
        receipt['exit_code'] = -1
        receipt['error'] = 'unexpected-tool-use'
    write_new(Path(attempt_dir) / 'receipt.json', receipt)
    return (result if receipt['exit_code'] == 0 and error is None else None), receipt


def run_astra(attempt_dir, packet, system, schema, input_text, effort='high', timeout=900, transport=None):
    """`packet` is unused on purpose: this reader only ever sees the rendering (`input_text`)."""
    runner = transport or _spawn
    attempt_dir = Path(attempt_dir)
    work = Path(tempfile.mkdtemp(prefix='dual-astra-'))
    os.chmod(work, 0o700)
    schema_path, output = work / 'schema.json', attempt_dir / 'result.json'
    attempt_dir.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(canonical(schema))
    cmd = astra_command(work, schema_path, output, effort=effort)
    stdin_text = system + '\n\n' + input_text
    receipt = _attempt(attempt_dir, 'astra', cmd, stdin_text, effort, schema)
    events_path, stderr = attempt_dir / 'events.jsonl', attempt_dir / 'stderr.txt'
    start = time.monotonic()
    try:
        code, error = runner(cmd, stdin_text, timeout, events_path, stderr, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    usage, tool_calls = None, []
    for line in events_path.read_text().splitlines() if events_path.exists() else []:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get('type') == 'turn.completed':
            used = event.get('usage') or {}
            usage = {'input': used.get('input_tokens'), 'output': used.get('output_tokens'),
                     'cached': used.get('cached_input_tokens'), 'cache_write': used.get('cache_write_input_tokens'),
                     'reasoning': used.get('reasoning_output_tokens')}
        if event.get('type') == 'item.completed' and event.get('item', {}).get('type') not in {'agent_message', 'reasoning', 'error'}:
            tool_calls.append(event['item'].get('type'))
        if event.get('type') == 'error':
            error = error or event.get('message', 'runtime-error')
    try:
        result = json.loads(output.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        result, error = None, error or 'missing-or-invalid-json'
    return _finish(attempt_dir, receipt, result, start, code, error, usage, tool_calls)
