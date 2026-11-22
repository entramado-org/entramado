"""The vault exporter (pipeline) writes notes only for the entities the API serves. It cannot import the API, so it carries its
own copy of the rule; this test, which runs with the API suite on every change, keeps the copy equal to `_served`."""
import ast
import unittest
from pathlib import Path

from app import graph_queries

EXPORTER = Path(__file__).resolve().parents[2] / "pipeline" / "src" / "obsidian_export.py"


class VaultExportRuleTest(unittest.TestCase):
    def test_the_exporter_rule_is_the_api_rule(self):
        tree = ast.parse(EXPORTER.read_text(encoding="utf-8"))
        scope: dict = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id in ("_PROVENANCE_RELS", "SERVED"):
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(EXPORTER), "exec"), scope)  # noqa: S102
        self.assertIn("SERVED", scope, "el exportador ya no define SERVED")
        self.assertEqual(scope["SERVED"], graph_queries._served("n"))


if __name__ == "__main__":
    unittest.main()
