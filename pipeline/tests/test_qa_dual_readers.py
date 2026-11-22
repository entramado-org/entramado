"""Deterministic tests of the reader plumbing the staged audit (spec 063) still shares with the
retired 061 loop: `reader_guard`/`_spawn` (smoke_opus, escalation) and `run_astra`. Kept from the
061 suite when spec 069 F5 retired its loop (Codex review of PR #16). No model call, no network."""
import json
import os
from pathlib import Path
import signal as signalmod
import subprocess
import sys
import tempfile
import time
import unittest

from pipeline.src.qa_dual_readers import run_astra


class SharedReadersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def astra_runner(events, finding, stderr=''):
        def runner(cmd, stdin_text, timeout, stdout_path, stderr_path, cwd):
            Path(stdout_path).write_text(''.join(json.dumps(e) + '\n' for e in events))
            Path(stderr_path).write_text(stderr)
            if finding is not None:
                Path(cmd[cmd.index('--output-last-message') + 1]).write_text(json.dumps(finding))
            return 0, None
        return runner

    def test_a_real_reader_process_group_dies_with_its_supervisor(self):
        """Codex review 2026-09-09: a SIGTERM to the supervisor used to release the lock
        while its readers kept running and burning quota."""
        pidfile = self.base / 'child.pid'
        program = f"""
import sys, threading
sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
from src import qa_dual_readers as readers
from pathlib import Path
with readers.reader_guard():
    t = threading.Thread(target=readers._spawn, args=(
        ['/bin/sh', '-c', 'sleep 60'], '', 60, {str(self.base / 'o.txt')!r},
        {str(self.base / 'e.txt')!r}, {str(self.base)!r}))
    t.start()
    while not readers._ACTIVE:
        pass
    Path({str(pidfile)!r}).write_text(str(next(iter(readers._ACTIVE)).pid))
    t.join()
"""
        supervisor = subprocess.Popen([sys.executable, '-c', program])
        try:
            for _ in range(200):
                if pidfile.exists():
                    break
                time.sleep(0.05)
            child = int(pidfile.read_text())
            os.kill(child, 0)                      # the reader is alive
            supervisor.send_signal(signalmod.SIGTERM)
            # KeyboardInterrupt out of the guard: the supervisor never exits clean on a signal.
            self.assertNotEqual(supervisor.wait(timeout=30), 0)
            for _ in range(100):                   # ... and dies with its supervisor
                try:
                    os.kill(child, 0)
                    time.sleep(0.05)
                except ProcessLookupError:
                    break
            with self.assertRaises(ProcessLookupError):
                os.kill(child, signalmod.SIGTERM)
        finally:
            if supervisor.poll() is None:
                supervisor.kill()
                supervisor.wait()

    def test_astra_reads_events_and_rejects_tool_use(self):
        events = [{'type': 'turn.completed', 'usage': {'input_tokens': 16000, 'output_tokens': 700,
                                                       'cached_input_tokens': 0, 'reasoning_output_tokens': 120}},
                  {'type': 'item.completed', 'item': {'type': 'command_execution'}}]
        result, receipt = run_astra(self.base / 'a', {'packet_id': 'p'}, 'SYS', {'type': 'object'}, 'entrada', 'medium', 30,
                                    self.astra_runner(events, {'packet_id': 'p', 'findings': []}))
        self.assertIsNone(result)
        self.assertEqual(receipt['error'], 'unexpected-tool-use')
        self.assertEqual(receipt['usage']['input'], 16000)
        self.assertEqual(receipt['effort'], 'medium')


if __name__ == '__main__':
    unittest.main()
