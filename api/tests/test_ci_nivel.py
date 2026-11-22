"""Spec 076: `.github/scripts/ci_nivel.py` (what the long pipeline step does) and the marks of the slow tier."""
import ast
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PATH = _ROOT / '.github/scripts/ci_nivel.py'
_SPEC = importlib.util.spec_from_file_location('ci_nivel', _PATH)
ci_nivel = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ci_nivel)

MARK = '''def lento(prueba):
    if os.environ.get('SM_NIVEL', '') == 'rapido':
        omitir = unittest.skip('nivel lento (spec 076)')
        return omitir(prueba)
    return prueba
'''


class TierTest(unittest.TestCase):
    def test_the_tier_goes_by_the_exact_words_of_the_scope_step(self):
        self.assertEqual(ci_nivel.tier_from_scope('success', 'true', 'false'), 'rapido')
        self.assertEqual(ci_nivel.tier_from_scope('success', 'false', 'false'), 'omitir')
        for scope in (('success', 'true', 'true'), ('success', None, 'false'), ('success', '', 'false'),
                      ('success', 'true', 'False'), ('success', 'True', 'false'), ('success', 'False', 'false'),
                      ('success', 'false', None), ('success', 'false', ''), ('success', 'false', 'true'),
                      ('success', 'false', 'False'), ('failure', 'false', 'false'), ('skipped', 'false', 'false'),
                      ('failure', 'true', 'false'), ('Success', 'true', 'false'), (None, 'true', 'false'),
                      ('success', 'true', None), ('success', 'true', ''), ('success', 'true', ' false'),
                      ('success', 'FALSE', 'false'), (None, None, None)):
            with self.subTest(scope=scope):
                self.assertEqual(ci_nivel.tier_from_scope(*scope), 'completo')

    def test_the_step_writes_its_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / 'out'
            for scope, word in (({'ALCANCE': 'success', 'PESADO': 'false', 'LENTO': 'false'}, 'omitir'),
                                ({'ALCANCE': 'success', 'PESADO': 'true', 'LENTO': 'false'}, 'rapido'),
                                ({'ALCANCE': 'success', 'PESADO': 'False', 'LENTO': 'false'}, 'completo'),
                                ({'ALCANCE': 'success', 'PESADO': 'false'}, 'completo'), ({}, 'completo')):
                with self.subTest(scope=scope):
                    out.write_text('')
                    done = subprocess.run([sys.executable, '-B', str(_PATH)], env={'GITHUB_OUTPUT': str(out), **scope},
                                          capture_output=True, text=True)
                    self.assertEqual((done.returncode, out.read_text()), (0, f'nivel={word}\n'))
            done = subprocess.run([sys.executable, '-B', str(_PATH)], env={}, capture_output=True, text=True)
            self.assertNotEqual(done.returncode, 0)   # no output file: the step fails and the long step runs whole


class MarksTest(unittest.TestCase):
    """The slow tier is a mark on the test itself. It may only depend on the exact word `rapido` in `SM_NIVEL`, and
    only test methods carry it: a mark on a class or a fixture would take more than the listed tests out."""
    MODULES = ('test_seudonimos.py', 'test_qa_esc_apply.py')

    def test_the_mark_is_the_one_line_and_only_test_methods_carry_it(self):
        total = 0
        for name in self.MODULES:
            with self.subTest(module=name):
                source = (_ROOT / 'pipeline/tests' / name).read_text(encoding='utf-8')
                tree = ast.parse(source)
                defined = [node for node in ast.walk(tree)
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == 'lento']
                stored = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == 'lento'
                          and not isinstance(node.ctx, ast.Load)]
                self.assertEqual((len(defined), stored), (1, []))   # one definition, never rebound
                self.assertIn(defined[0], tree.body)   # at module level
                self.assertEqual(ast.dump(defined[0]), ast.dump(ast.parse(MARK).body[0]))
                words = [node for node in ast.walk(tree) if isinstance(node, ast.Constant) and node.value == 'SM_NIVEL']
                self.assertEqual(len(words), 1)   # nothing else in the module reads or sets the word
                # Only a method written directly in a class of the module: where unittest finds tests.
                methods = [node for cls in tree.body if isinstance(cls, ast.ClassDef) for node in cls.body]
                marked = [node for node in ast.walk(tree)
                          if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                          and any(isinstance(d, ast.Name) and d.id == 'lento' for d in node.decorator_list)]
                for node in marked:
                    self.assertTrue(any(node is m for m in methods), getattr(node, 'name', node))
                uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == 'lento']
                self.assertEqual(len(uses), len(marked))   # each use is a decorator
                for node in marked:
                    self.assertIsInstance(node, ast.FunctionDef)
                    self.assertTrue(node.name.startswith('test_'), node.name)
                    self.assertEqual(node.decorator_list[0].id, 'lento')   # outermost
                total += len(marked)
        self.assertEqual(total, 45)

    def test_the_workflow_sets_the_word_only_in_the_long_step(self):
        import yaml
        flow = yaml.safe_load((_ROOT / '.github/workflows/checks.yml').read_text(encoding='utf-8'))
        steps = flow['jobs']['data-and-tests']['steps']
        with_word = [s for s in steps if 'SM_NIVEL' in str(s)]
        self.assertEqual([s['name'] for s in with_word], ['Tests del pipeline (seudónimos y apply 063)'])
        self.assertEqual(with_word[0]['env']['SM_NIVEL'],
                         "${{ steps.nivel.outcome == 'success' && steps.nivel.outputs.nivel || 'completo' }}")
        self.assertNotIn('SM_NIVEL', str({k: v for k, v in flow.items() if k != 'jobs'}))
        self.assertNotIn('SM_NIVEL', str(flow['jobs']['web-build']))


if __name__ == '__main__':
    unittest.main()
