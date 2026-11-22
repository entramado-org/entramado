"""Spec 069 T2.6 — the CI gate catches a planted copy of the vocabulary and an unsafe bool()."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ontologia_check as C  # noqa: E402


class CheckTest(unittest.TestCase):
    def run_on(self, rel, text, fn):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            return fn([rel], root=Path(tmp))

    def test_quoted_copy_is_caught(self):
        out = self.run_on("pipeline/src/x.py", 'SYM = {"FAMILY_OF", "ASSOCIATED_WITH", "MET_WITH"}\n', C.copias)
        self.assertEqual(len(out), 1)
        self.assertIn("tipos de relación", out[0])

    def test_status_union_is_caught(self):
        out = self.run_on("web/lib/x.ts", 'type S = "investigado" | "imputado" | "acusado";\n', C.copias)
        self.assertTrue(any("estados procesales" in o for o in out))

    def test_prompt_enumeration_is_caught_but_a_comment_is_not(self):
        prompt = "Tipos: WORKS_FOR, MEMBER_OF, ASSOCIATED_WITH, MET_WITH, PAID.\n"
        self.assertTrue(self.run_on("pipeline/scripts/w.workflow.js", prompt, C.copias))
        comment = "# ACCUSED_OF / INVESTIGATED_BY / CONVICTED_OF carry a status\n"
        self.assertEqual(self.run_on("pipeline/src/y.py", comment, C.copias), [])

    def test_policy_subset_with_marker_passes(self):
        line = "K = {'acusacion', 'ultima_hora', 'contexto'}  # ontologia-ok: capas que recrea su loader\n"
        self.assertEqual(self.run_on("pipeline/src/z.py", line, C.copias), [])

    def test_template_markers_are_references(self):
        line = '{"i": "Capas: {{definiciones:capas:judicial,acusacion,prensa,ultima_hora,contexto}}"}\n'
        self.assertEqual(self.run_on("pipeline/data/jev/q.tmpl.json", line, C.copias), [])

    def test_bool_on_llm_output(self):
        rel = C.PUNTOS_LLM[0]
        self.assertTrue(self.run_on(rel, 'ok = bool(obj.get("sustentado", True))\n', C.bools))
        self.assertEqual(self.run_on(rel, 'ok = parse_bool(obj.get("sustentado"))\n', C.bools), [])

    def test_tree_is_clean(self):
        self.assertEqual(C.main(), 0)


if __name__ == "__main__":
    unittest.main()
