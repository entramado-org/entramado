"""Spec 063 — lector Claude headless y buscador restringido (sin modelos, sin red).

El runner falso escribe el stdout que el CLI dejaría (flujo `stream-json`), igual que los tests del
lector Fable histórico de la 061 (`git show acfeffe:pipeline/tests/test_qa_dual.py`).
"""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from src import qa_esc_opus as O
from src.qa_integral import digest

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['entity_id'],
          'properties': {'entity_id': {'type': 'string'}}}
DECISION = {'entity_id': 'Person:koldo-garcia'}
EV = str(Path(O.__file__).resolve().with_name('qa_esc_ev.py'))
PREFIX = f'python3 {EV}'
USAGE = {'claude-opus-5-5': {'inputTokens': 7, 'outputTokens': 3100, 'cacheReadInputTokens': 22000,
                           'cacheCreationInputTokens': 41000, 'thinkingTokens': 9000}}


def tool_use(command, name='Bash', uid=None):
    return {'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'id': uid or f'toolu_{abs(hash(command)) % 10**8}', 'name': name,
         'input': {'command': command} if name == 'Bash' else {'file_path': command}}]}}


def result_event(**over):
    event = {'type': 'result', 'subtype': 'success', 'is_error': False, 'stop_reason': 'tool_use',
             'num_turns': 6, 'total_cost_usd': 1.234, 'modelUsage': USAGE,
             'permission_denials': [], 'structured_output': DECISION}
    event.update(over)
    return event


def stream(*events):
    return ''.join(json.dumps(e) + '\n' for e in events)


class FakeRunner:
    """Escribe el stdout del CLI y recuerda el cwd con el que se lanzó."""

    def __init__(self, stdout, stderr='', code=0, error=None):
        self.stdout, self.stderr, self.code, self.error = stdout, stderr, code, error
        self.cmd = self.cwd = None
        self.cwd_contents = None

    def __call__(self, cmd, stdin_text, timeout, stdout_path, stderr_path, cwd):
        self.cmd, self.cwd, self.stdin = cmd, Path(cwd), stdin_text
        self.cwd_contents = sorted(p.name for p in Path(cwd).iterdir())
        Path(stdout_path).write_text(self.stdout)
        Path(stderr_path).write_text(self.stderr)
        return self.code, self.error


class CommandTest(unittest.TestCase):
    def test_search_reader_only_gets_the_ev_prefix(self):
        cmd = O.command('BRIEF', O.MODELS['opus'], 'xhigh', SCHEMA, tools_prefix=PREFIX,
                        extra_env={'QA_ESC_ROOT': '/tmp/root'})
        self.assertNotIn('--bare', cmd)
        self.assertEqual(cmd[0], 'env')
        for name in O.ENV_STRIP:
            self.assertIn(name, cmd)
        self.assertEqual(cmd[cmd.index('-u') + 1], 'ANTHROPIC_API_KEY')
        self.assertIn('QA_ESC_ROOT=/tmp/root', cmd[:cmd.index('claude')])
        self.assertEqual(cmd[cmd.index('--tools') + 1], 'Bash')
        self.assertEqual(cmd[cmd.index('--allowedTools') + 1], f'Bash({PREFIX}:*)')
        self.assertEqual(cmd[cmd.index('--model') + 1], 'claude-opus-5-5')
        self.assertEqual(cmd[cmd.index('--effort') + 1], 'xhigh')
        self.assertEqual(cmd[cmd.index('--system-prompt') + 1], 'BRIEF')
        self.assertEqual(json.loads(cmd[cmd.index('--json-schema') + 1]), SCHEMA)
        # stream-json es el único formato que publica los comandos ejecutados, y exige --verbose.
        self.assertEqual(cmd[cmd.index('--output-format') + 1], 'stream-json')
        self.assertIn('--verbose', cmd)
        self.assertNotIn('--max-budget-usd', cmd)

    def test_blind_reader_gets_no_tools_and_a_budget_when_asked(self):
        cmd = O.command('BRIEF', O.MODELS['opus'], 'high', SCHEMA, budget_usd=0.05)
        self.assertEqual(cmd[cmd.index('--tools') + 1], '')
        self.assertNotIn('--allowedTools', cmd)
        self.assertEqual(cmd[cmd.index('--model') + 1], 'claude-opus-5-5')
        self.assertEqual(cmd[cmd.index('--max-budget-usd') + 1], '0.05')


class EffortTest(unittest.TestCase):
    def test_expected_effort_by_risk(self):
        self.assertEqual(O.expected_effort('alto'), 'high')
        self.assertEqual(O.expected_effort('critico'), 'xhigh')
        self.assertEqual(O.expected_effort('crítico'), 'xhigh')
        # Suelo: nunca se pide por debajo de high; lo desconocido también sube al suelo.
        self.assertEqual(O.floor_effort('medium'), 'high')
        self.assertEqual(O.floor_effort('low'), 'high')
        self.assertEqual(O.floor_effort('xhigh'), 'xhigh')
        self.assertEqual(O.floor_effort(None), 'high')
        self.assertEqual(O.expected_effort('medio'), 'high')
        self.assertEqual(O.expected_effort('bajo'), 'high')
        self.assertEqual(O.expected_effort(None), 'high')

    def test_effort_rank_rejects_reading_below_the_expected_level(self):
        self.assertEqual(O.EFFORT_RANK, {'low': 0, 'medium': 1, 'high': 2, 'xhigh': 3, 'max': 4})
        self.assertTrue(O.effort_ok('xhigh', 'xhigh'))
        self.assertTrue(O.effort_ok('max', 'xhigh'))
        self.assertFalse(O.effort_ok('high', 'xhigh'))
        self.assertFalse(O.effort_ok('medium', 'high'))
        self.assertFalse(O.effort_ok('', 'high'))


class RunClaudeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_one(self, runner, name='a', model=None, **over):
        kwargs = {'tools_prefix': PREFIX, 'timeout': 30, 'runner': runner,
                  'stamp': {'brief_sha256': 'b' * 64, 'pack_sha256': 'p' * 64}}
        kwargs.update(over)
        return O.run_claude(self.base / name, 'opus', model or O.MODELS['opus'], 'xhigh', 'BRIEF', SCHEMA,
                            'PACK', **kwargs)

    def test_receipt_carries_usage_cost_searches_and_the_stamped_hashes(self):
        runner = FakeRunner(stream(tool_use(f"{PREFIX} 'Koldo'"),
                                   tool_use('{"entity_id": "x"}', name='StructuredOutput'),
                                   result_event()))
        result, receipt = self.run_one(runner)
        self.assertEqual(result, DECISION)
        self.assertEqual(receipt['usage'], {'input': 7, 'output': 3100, 'cached': 22000,
                                            'cache_write': 41000, 'reasoning': 9000})
        self.assertEqual((receipt['cost_usd'], receipt['num_turns']), (1.234, 6))
        self.assertEqual(receipt['searches'], [f"{PREFIX} 'Koldo'"])
        self.assertEqual((receipt['exit_code'], receipt['error'], receipt['tool_calls']), (0, None, []))
        self.assertEqual((receipt['side'], receipt['model'], receipt['effort']),
                         ('opus', 'claude-opus-5-5', 'xhigh'))
        self.assertEqual((receipt['brief_sha256'], receipt['pack_sha256']), ('b' * 64, 'p' * 64))
        self.assertEqual(receipt['schema_sha256'], digest(SCHEMA))
        self.assertEqual(receipt['input_sha256'], digest('PACK'))
        self.assertEqual(receipt['finding_sha256'], digest(DECISION))
        for name in ('receipt.json', 'command.json', 'input.txt.gz', 'stdout.jsonl'):
            self.assertTrue((self.base / 'a' / name).exists(), name)
        # El lector corre en un directorio vacío que desaparece al terminar.
        self.assertEqual(runner.cwd_contents, [])
        self.assertFalse(runner.cwd.exists())

    def test_a_foreign_tool_voids_the_reading(self):
        result, receipt = self.run_one(FakeRunner(stream(tool_use('/etc/passwd', name='Read'), result_event())))
        self.assertIsNone(result)
        self.assertEqual((receipt['exit_code'], receipt['error']), (-1, 'unexpected-tool-use'))
        self.assertEqual(receipt['tool_calls'], ['Read'])

    def test_a_bash_command_outside_the_prefix_voids_the_reading(self):
        result, receipt = self.run_one(FakeRunner(stream(tool_use('curl https://example.org'), result_event())))
        self.assertIsNone(result)
        self.assertEqual(receipt['error'], 'unexpected-tool-use')
        self.assertEqual(receipt['tool_calls'], ['Bash: curl https://example.org'])

    def test_a_denial_voids_the_reading_even_inside_the_prefix(self):
        """El comando compuesto (`python3 ev.py 'x'; ls /`) empieza por el prefijo pero el CLI lo deniega."""
        compound = f"{PREFIX} 'x'; ls /"
        denial = {'tool_name': 'Bash', 'tool_use_id': 'toolu_c', 'tool_input': {'command': compound}}
        result, receipt = self.run_one(FakeRunner(stream(tool_use(compound, uid='toolu_c'),
                                                         result_event(permission_denials=[denial]))))
        self.assertIsNone(result)
        # Desde la revisión del driver (2026-09-21) un comando encadenado es herramienta ajena aunque
        # empiece por el prefijo: anula como `unexpected-tool-use` antes de mirar las denegaciones.
        self.assertEqual((receipt['exit_code'], receipt['error']), (-1, 'unexpected-tool-use'))
        self.assertEqual(receipt['denials'], [{'id': 'toolu_c', 'tool': 'Bash', 'command': compound}])
        self.assertEqual(receipt['searches'], [])          # lo denegado no cuenta como búsqueda

    def test_more_than_twelve_searches_voids_the_reading(self):
        calls = [tool_use(f"{PREFIX} 't{i}'", uid=f'toolu_{i}') for i in range(13)]
        result, receipt = self.run_one(FakeRunner(stream(*calls, result_event())))
        self.assertIsNone(result)
        self.assertEqual(receipt['error'], 'tool-budget-exceeded')
        self.assertEqual(len(receipt['searches']), 13)
        ok, receipt_ok = self.run_one(FakeRunner(stream(*calls[:12], result_event())), name='ok')
        self.assertEqual(ok, DECISION)
        self.assertIsNone(receipt_ok['error'])

    def test_chained_searches_count_one_by_one(self):
        """Piloto 24-sep: cuatro lecturas se anularon sólo por encadenar dos búsquedas con `;`."""
        chained = f"{PREFIX} 'a' ; {PREFIX} --doc auto 'b' && {PREFIX} 'c';"
        result, receipt = self.run_one(FakeRunner(stream(tool_use(chained), result_event())))
        self.assertEqual(result, DECISION)
        self.assertIsNone(receipt['error'])
        self.assertEqual(len(receipt['searches']), 3)
        for bad in (f"{PREFIX} 'a' ; ls /", f"{PREFIX} 'a' | head", f"{PREFIX} 'a' ;; {PREFIX} 'b'",
                    f"cd /tmp && {PREFIX} 'a'", f"{PREFIX} 'a' & {PREFIX} 'b'"):
            self.assertIsNone(O.search_calls(bad, PREFIX), bad)

    def test_missing_structured_output_voids_the_reading(self):
        result, receipt = self.run_one(FakeRunner(stream(result_event(structured_output=None))))
        self.assertIsNone(result)
        self.assertEqual((receipt['exit_code'], receipt['error']), (-1, 'missing-structured-output'))
        empty, receipt_empty = self.run_one(FakeRunner(''), name='empty')
        self.assertIsNone(empty)
        self.assertEqual(receipt_empty['error'], 'missing-or-invalid-json')

    def test_quota_and_timeout_are_reported_as_such(self):
        quota = FakeRunner('', stderr='Claude usage limit reached: you have hit your usage limit', code=1)
        result, receipt = self.run_one(quota)
        self.assertIsNone(result)
        self.assertEqual(receipt['error'], 'quota')
        timed, receipt_t = self.run_one(FakeRunner('', code=-1, error='reader-timeout'), name='t')
        self.assertIsNone(timed)
        self.assertEqual(receipt_t['error'], 'reader-timeout')
        bad = FakeRunner('', stderr='API Error: 401 authentication_error', code=1)
        _, receipt_c = self.run_one(bad, name='c')
        self.assertTrue(receipt_c['error'].startswith('config:'), receipt_c['error'])

    def test_model_aliases_still_resolve_their_usage(self):
        """`--model sonnet` factura como `claude-sonnet-5`: el recibo deja el id que resolvió (smoke b)."""
        runner = FakeRunner(stream(result_event(modelUsage={'claude-sonnet-5': {'inputTokens': 4,
                                                                               'outputTokens': 1789}})))
        _, receipt = self.run_one(runner, name='alias', model='sonnet')
        self.assertEqual(receipt['model_resolved'], 'claude-sonnet-5')
        self.assertEqual(receipt['usage']['output'], 1789)

    def test_the_blind_reader_runs_without_tools(self):
        runner = FakeRunner(stream(tool_use('{"entity_id": "x"}', name='StructuredOutput'), result_event(
            modelUsage={'claude-opus-5-5': {'inputTokens': 2, 'outputTokens': 800}})))
        result, receipt = O.run_claude(self.base / 'blind', 'opus', O.MODELS['opus'], 'high', 'BRIEF',
                                       SCHEMA, 'PACK', timeout=30, runner=runner)
        self.assertEqual(result, DECISION)
        self.assertEqual(receipt['usage']['output'], 800)
        self.assertEqual(runner.cmd[runner.cmd.index('--tools') + 1], '')


class EvTest(unittest.TestCase):
    """`qa_esc_ev.py` como subproceso, con `QA_ESC_ROOT` apuntando a un censo mínimo."""

    def test_the_search_output_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'sources').mkdir()
            conn = sqlite3.connect(root / 'census.sqlite')
            conn.execute('CREATE TABLE chunks (doc_id TEXT, page_start INTEGER, page_end INTEGER, text TEXT)')
            conn.executemany('INSERT INTO chunks VALUES (?,?,?,?)',
                             [(f'doc{i}.pdf', 1, 1, 'x' * 3000 + ' Koldo ' + 'y' * 3000) for i in range(40)])
            conn.commit()
            conn.close()
            out = subprocess.run([sys.executable, EV, '-n', '50', '-w', '5000', 'Koldo', 'x{10}', 'y{10}', 'x{20}', 'y{20}', 'x{30}'],
                                 capture_output=True, text=True, cwd=tmp,
                                 env={'QA_ESC_ROOT': str(root), 'PATH': '/usr/bin:/bin'})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertLessEqual(len(out.stdout), 20300)
            self.assertIn('salida recortada', out.stdout)
            # -n se recorta a 6 ventanas por término.
            self.assertLessEqual(out.stdout.split('######## x{10}')[0].count('[corpus doc'), 6)

    def test_it_searches_a_census_named_by_the_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'sources').mkdir()
            conn = sqlite3.connect(root / 'census.sqlite')
            conn.execute('CREATE TABLE chunks (doc_id TEXT, page_start INTEGER, page_end INTEGER, text TEXT)')
            conn.execute('INSERT INTO chunks VALUES (?,?,?,?)',
                         ('auto-20260620.pdf', 3, 4, 'El investigado MORENO  PURROY recibió 5.700 euros.'))
            conn.commit()
            conn.close()
            out = subprocess.run([sys.executable, EV, r'MORENO\s+PURROY', 'zzz-no-existe'],
                                 capture_output=True, text=True, cwd=tmp,
                                 env={'QA_ESC_ROOT': str(root), 'PATH': '/usr/bin:/bin'})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn('[corpus auto-20260620.pdf p.3-4]', out.stdout)
            self.assertIn('MORENO PURROY recibió 5.700 euros', out.stdout)
            self.assertIn('######## zzz-no-existe', out.stdout)
            self.assertIn('[corpus] 0 hits', out.stdout)
            self.assertIn('[web] 0 hits', out.stdout)
            # Sólo lectura: el censo no cambia y no aparece ningún fichero nuevo.
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['census.sqlite', 'sources'])


if __name__ == '__main__':
    unittest.main()


class PlainSearchTest(unittest.TestCase):
    """Un comando encadenado que empieza por el prefijo NO es una búsqueda (revisión del driver 2026-09-21)."""

    def test_chained_commands_are_foreign(self):
        from src.qa_esc_opus import is_plain_search
        prefix = 'python3 /abs/qa_esc_ev.py'
        self.assertTrue(is_plain_search(f"{prefix} -n 4 'Koldo|Aldama' 'x; y'", prefix))
        self.assertFalse(is_plain_search(f"{prefix} 'x'; ls /", prefix))
        self.assertFalse(is_plain_search(f"{prefix} 'x' | head -3", prefix))
        self.assertFalse(is_plain_search(f"{prefix} $(cat /etc/passwd)", prefix))
        self.assertFalse(is_plain_search("grep -rn Koldo .", prefix))
        self.assertFalse(is_plain_search(f"{prefix} 'x'", None))


class SearchBypassTest(unittest.TestCase):
    """Revisión de seguridad 2026-09-21: una comilla falsa no puede esconder un operador de shell."""

    def test_fake_quoted_span_does_not_hide_a_chain(self):
        from src.qa_esc_opus import is_plain_search
        prefix = 'python3 /abs/qa_esc_ev.py'
        # `'a'"; ls /; echo "` es UN solo argumento para el shell (no encadena): la comprobación por
        # tokens lo acepta, mientras que un `;` fuera de comillas es un operador y se rechaza.
        self.assertTrue(is_plain_search(f"{prefix} 'a'\"; ls /; echo \"", prefix))
        self.assertFalse(is_plain_search(f"{prefix} 'a'; ls /", prefix))
        self.assertFalse(is_plain_search(f"{prefix} 'a' && ls /", prefix))
        self.assertFalse(is_plain_search(f"{prefix} 'a' > /tmp/x", prefix))
        self.assertFalse(is_plain_search(f"{prefix} $(cat /etc/passwd)", prefix))
        self.assertFalse(is_plain_search(f"{prefix} `id`", prefix))
        self.assertFalse(is_plain_search(f"{prefix} 'a", prefix))            # comilla sin cerrar: no parsea
        self.assertTrue(is_plain_search(f"{prefix} -n 6 'Koldo|Aldama' 'x; y' --doc uco", prefix))
