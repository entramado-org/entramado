"""Spec 069: every procedural status of the ontology has a public explanation (glossary entry and
legend label). A new status without them must fail CI, not reach the site unexplained."""
import re
import unittest
from pathlib import Path

from src.ontologia import ontologia

WEB = Path(__file__).resolve().parents[2] / "web" / "lib"


class StatusesExplainedTest(unittest.TestCase):
    def test_every_status_has_a_glossary_entry(self):
        ids = set(re.findall(r'^\s+id: "([^"]+)",\s*\n\s+termino: .*\n\s+categoria: "estados"',
                             (WEB / "glosario.ts").read_text(encoding="utf-8"), re.M))
        missing = [e for e in ontologia().estados if e.replace("_", "-") not in ids]
        self.assertEqual(missing, [], "estados sin entrada en web/lib/glosario.ts (categoria: estados)")

    def test_every_status_has_a_legend_label(self):
        src = (WEB / "graphStyle.ts").read_text(encoding="utf-8")
        block = src[src.index("const STATUS_LABELS"):src.index("export const STATUS_META")]
        missing = [e for e in ontologia().estados if not re.search(rf"^\s+{e}:\s*\{{ label:", block, re.M)]
        self.assertEqual(missing, [], "estados sin etiqueta en STATUS_LABELS (web/lib/graphStyle.ts)")
