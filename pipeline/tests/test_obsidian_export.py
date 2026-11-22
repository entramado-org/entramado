"""The vault is a copy of what the graph serves (spec 075): served entities only, the node's own summary, no passages for
pseudonyms or anonymised relatives, and nothing left over from a previous export."""
import re
import tempfile
import unittest
from pathlib import Path

from src import obsidian_export as ox

REPO = Path(__file__).resolve().parents[2]


class _Session:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, cypher, **params):
        flat = " ".join(cypher.split())
        self.driver.calls.append((flat, params))
        for needle, rows in self.driver.answers:
            if needle in flat:
                return rows(params) if callable(rows) else rows
        return []


class _Driver:
    def __init__(self, answers=()):
        self.calls = []
        self.answers = list(answers)

    def session(self):
        return _Session(self)


def _entity(eid, name, **kw):
    return {"id": eid, "type": "Person", "name": name, "role": None, "aliases": [], "degree": 3, "mentions": 2, "context": False,
            "summary": None, "pseudonym": False, **kw}


class ServedRuleTest(unittest.TestCase):
    def test_the_exporter_uses_the_same_rule_as_the_api(self):
        api = (REPO / "api" / "app" / "graph_queries.py").read_text(encoding="utf-8")
        m = re.search(r'return \(f"(\(NOT \{v\}:Person OR EXISTS .*?)"\s*f"(.*?)"\)', api, re.S)
        self.assertIsNotNone(m, "no encuentro _served en el API")
        api_rule = (m.group(1) + m.group(2)).replace("{{", "{").replace("}}", "}").replace("{v}", "n")
        api_rule = api_rule.replace("{_EXCLUDE}", str(["MENTIONED_IN", "PART_OF"]))
        self.assertEqual(ox.SERVED, api_rule)
        cfg = (REPO / "api" / "app" / "config.py").read_text(encoding="utf-8")
        self.assertIn('PROVENANCE_RELS = ["MENTIONED_IN", "PART_OF"]', cfg)

    def test_only_served_entities_are_fetched(self):
        d = _Driver()
        ox.fetch_entities(d, 2)
        self.assertIn(" ".join(ox.SERVED.split()), d.calls[0][0])
        self.assertIn("n.summary AS summary", d.calls[0][0])


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "_Briefings").mkdir()
        self.chunk = [{"doc": "Auto", "doc_id": "auto.pdf", "page": 3, "chunk_id": "c1",
                       "text": "zzpasaje del auto, " + "relleno " * 24 + "con DNI 12345678Z y domicilio en calle Mayor 12, 3º B"}]
        self.docs = [{"id": "auto.pdf", "title": "Auto", "pages": 4, "low_text": [], "chunks": 9}]

    def _run(self, entities):
        d = _Driver([("RETURN n.entity_id AS id, labels(n)[0] AS type", entities), ("MENTIONED_IN]->(ch:Chunk)", self.chunk),
                     ("MATCH (d:Document)", self.docs)])
        return d, ox.export(d, self.root, 2)

    def _read(self, rel):
        return (self.root / rel).read_text(encoding="utf-8")

    def test_the_summary_is_the_one_on_the_node_and_nothing_is_read_on_the_side(self):
        self._run([_entity("Person:zz-a", "Zz A", summary="Resumen corregido zzgrafo."), _entity("Person:zz-b", "Zz B")])
        self.assertIn("## Resumen\n\nResumen corregido zzgrafo.", self._read("People/Zz A.md"))
        self.assertNotIn("## Resumen", self._read("People/Zz B.md"))
        src = (REPO / "pipeline" / "src" / "obsidian_export.py").read_text(encoding="utf-8")
        for banned in ("summaries.yaml", "llm_client", "import asyncio"):
            self.assertNotIn(banned, src)

    def test_a_pseudonym_gets_no_passages_and_is_flagged(self):
        d, _ = self._run([_entity("Person:zz-seud", "Zzseud", pseudonym=True), _entity("Person:zz-a", "Zz A")])
        seud = self._read("People/Zzseud.md")
        self.assertNotIn("zzpasaje", seud)
        self.assertIn("pseudonym: true", seud)
        self.assertIn("Seudónimo judicial", seud)
        self.assertIn("zzpasaje", self._read("People/Zz A.md"))
        asked = [p.get("id") for c, p in d.calls if "(ch:Chunk)" in c]
        self.assertEqual(asked, ["Person:zz-a"])

    def test_an_anonymised_relative_gets_no_passages(self):
        self._run([_entity("Person:familiar-de-zz", "Familiar de Zz (no investigada)")])
        self.assertNotIn("zzpasaje", self._read("People/Familiar de Zz (no investigada).md"))

    def test_the_notes_carry_the_mask_of_the_api(self):
        """Masked before the passage is cut (the identifier straddles the cut) and before the header is serialised."""
        import yaml
        self._run([_entity("Person:zz-a", "Zz A", summary="Vive en calle Mayor 12, 3º B; DNI 12345678Z.",
                           aliases=["Zz A, DNI 12345678Z", "calle Mayor 12: Zz"])])
        note = self._read("People/Zz A.md")
        self.assertIn("zzpasaje", note)
        for raw in ("12345678", "1234567", "Mayor 12"):
            self.assertNotIn(raw, note)
        head = yaml.safe_load(note.split("---")[1])
        self.assertEqual(head["entity_id"], "Person:zz-a")
        self.assertTrue(all(isinstance(a, str) for a in head["aliases"]))

    def test_each_export_rewrites_its_own_folders_and_touches_nothing_else(self):
        for d in ("Patrones", "Inbox"):
            (self.root / d).mkdir()
        (self.root / "_Briefings" / "Zz Caso.md").write_text("---\ntype: Briefing\nentity_id: Person:zz-a\n---\n# Caso\n", encoding="utf-8")
        (self.root / "Patrones" / "Zz Patron.md").write_text("---\ntype: Document\n---\n# Patrón\n", encoding="utf-8")
        (self.root / "Notas mías.md").write_text("---\nentity_id: Person:zz-a\n---\n", encoding="utf-8")
        self._run([_entity("Person:zz-a", "Zz A"), _entity("Person:zz-retirada", "Zz Retirada"),
                   _entity("Event:zz-ev", "Zz Evento", type="Event")])
        self.assertTrue((self.root / "Events" / "sin-fecha" / "Zz Evento.md").exists())
        self._run([_entity("Person:zz-a", "Zz a")])   # renamed in case only: the note survives with the new spelling
        self.assertEqual([p.name for p in (self.root / "People").iterdir()], ["Zz a.md"])
        self.assertFalse((self.root / "Events").exists())
        for kept in ("_Briefings/Zz Caso.md", "Patrones/Zz Patron.md", "Notas mías.md", "Inbox", "_MOC/Entramado.md", "README.md"):
            self.assertTrue((self.root / kept).exists(), kept)

    def test_a_folder_that_is_not_a_vault_is_refused(self):
        ajena = tempfile.TemporaryDirectory()
        self.addCleanup(ajena.cleanup)
        (Path(ajena.name) / "Documents").mkdir()
        (Path(ajena.name) / "Documents" / "tesis.md").write_text("mía\n", encoding="utf-8")
        d = _Driver([("RETURN n.entity_id AS id, labels(n)[0] AS type", [_entity("Person:zz-a", "Zz A")]), ("MATCH (d:Document)", self.docs)])
        with self.assertRaises(RuntimeError):
            ox.export(d, Path(ajena.name), 2)
        self.assertEqual((Path(ajena.name) / "Documents" / "tesis.md").read_text(encoding="utf-8"), "mía\n")

    def test_a_linked_readme_is_not_written_through(self):
        fuera = tempfile.TemporaryDirectory()
        self.addCleanup(fuera.cleanup)
        (Path(fuera.name) / "nota.md").write_text("ajena\n", encoding="utf-8")
        (self.root / "README.md").symlink_to(Path(fuera.name) / "nota.md")
        with self.assertRaises(RuntimeError):
            self._run([_entity("Person:zz-a", "Zz A")])
        self.assertEqual((Path(fuera.name) / "nota.md").read_text(encoding="utf-8"), "ajena\n")

    def test_a_failure_halfway_leaves_the_previous_export_intact(self):
        self._run([_entity("Person:zz-a", "Zz A", summary="zzanterior")])

        def boom(params):
            raise RuntimeError("se cayó Neo4j")
        d = _Driver([("RETURN n.entity_id AS id, labels(n)[0] AS type", [_entity("Person:zz-a", "Zz A", summary="zznuevo")]),
                     ("MATCH (d:Document)", self.docs), ("MENTIONED_IN]->(ch:Chunk)", boom)])
        with self.assertRaises(RuntimeError):
            ox.export(d, self.root, 2)
        self.assertIn("zzanterior", self._read("People/Zz A.md"))
        self._run([_entity("Person:zz-a", "Zz A", summary="zznuevo")])   # and the next export is not blocked by the leftovers
        self.assertIn("zznuevo", self._read("People/Zz A.md"))
        self.assertFalse((self.root / ".exportando").exists())

    def test_an_empty_graph_or_a_linked_folder_stops_the_export_before_deleting(self):
        self._run([_entity("Person:zz-a", "Zz A")])
        with self.assertRaises(RuntimeError):
            self._run([])
        self.assertTrue((self.root / "People" / "Zz A.md").exists())
        fuera = tempfile.TemporaryDirectory()
        self.addCleanup(fuera.cleanup)
        (Path(fuera.name) / "ajena.md").write_text("---\nentity_id: x\n---\n", encoding="utf-8")
        (self.root / "Timeline").symlink_to(fuera.name)
        with self.assertRaises(RuntimeError):
            self._run([_entity("Person:zz-a", "Zz A")])
        self.assertTrue((Path(fuera.name) / "ajena.md").exists())
        self.assertTrue((self.root / "People" / "Zz A.md").exists())

    def test_two_entities_with_the_same_title_keep_a_note_each(self):
        self._run([_entity("Person:zz-civil", "Zzdavid", summary="zzcivil"), _entity("Person:zz-seud", "ZZDAVID", pseudonym=True)])
        notes = sorted(p.name for p in (self.root / "People").iterdir())
        self.assertEqual(len(notes), 2)
        texts = [self._read(f"People/{n}") for n in notes]
        self.assertEqual(sum("zzcivil" in t for t in texts), 1)
        self.assertEqual(sum("pseudonym: true" in t for t in texts), 1)

    def test_a_link_goes_to_the_note_of_that_entity_not_to_a_homonym(self):
        rels = [{"id": "Person:zz-seud", "name": "ZZDAVID", "type": "Person", "rt": "MET_WITH", "dir": "out", "cnt": 1, "context": False}]
        ents = [_entity("Person:zz-civil", "Zzdavid"), _entity("Person:zz-seud", "ZZDAVID", pseudonym=True),
                _entity("Person:zz-otro", "Zz Otro")]
        d = _Driver([("RETURN n.entity_id AS id, labels(n)[0] AS type", ents), ("MATCH (d:Document)", self.docs),
                     ("WITH c, m, type(r) AS rt", lambda p: rels if p["id"] == "Person:zz-otro" else [])])
        ox.export(d, self.root, 2)
        target = re.search(r"\[\[([^\]|]+)\|ZZDAVID\]\]", self._read("People/Zz Otro.md")).group(1)
        self.assertIn("entity_id: Person:zz-seud", self._read(f"People/{target}.md"))

    def test_an_entity_without_a_note_is_named_not_linked(self):
        rels = [{"id": "Person:zz-sin-ficha", "name": "Zz A", "type": "Person", "rt": "MET_WITH", "dir": "out", "cnt": 1, "context": False}]
        d = _Driver([("RETURN n.entity_id AS id, labels(n)[0] AS type", [_entity("Person:zz-a", "Zz A"), _entity("Person:zz-otro", "Zz Otro")]),
                     ("MATCH (d:Document)", self.docs), ("WITH c, m, type(r) AS rt", lambda p: rels if p["id"] == "Person:zz-otro" else [])])
        ox.export(d, self.root, 2)
        self.assertIn("- → Zz A · 1 evidencias", self._read("People/Zz Otro.md"))

    def test_a_document_or_a_case_map_that_shares_a_title_is_still_the_target_of_its_links(self):
        self.docs = [{"id": "Zz Auto.pdf", "title": "Auto", "pages": 4, "low_text": [], "chunks": 9}]
        self.chunk = [{"doc": "Auto", "doc_id": "Zz Auto.pdf", "page": 3, "chunk_id": "c1", "text": "zzpasaje"}]
        self._run([_entity("LegalCase:zz-auto", "Zz Auto", type="LegalCase"), _entity("LegalCase:caso-koldo", "Caso Koldo", type="LegalCase"),
                   _entity("Person:zz-a", "Zz A")])
        doc = re.search(r"— \[\[([^\]|]+)\|Zz Auto\]\] p\.3", self._read("People/Zz A.md")).group(1)
        self.assertIn("type: Document", self._read(f"Documents/{doc}.md"))
        moc = re.search(r"- \[\[([^\]|]+)\|Caso Koldo\]\]", self._read("_MOC/Entramado.md")).group(1)
        self.assertIn("type: CaseMOC", self._read(f"_MOC/{moc}.md"))

    def test_a_first_export_that_fails_can_be_retried_and_a_linked_briefings_folder_does_not_make_a_vault(self):
        nueva = tempfile.TemporaryDirectory()
        self.addCleanup(nueva.cleanup)
        root = Path(nueva.name)

        def boom(params):
            raise RuntimeError("se cayó Neo4j")
        ents = [_entity("Person:zz-a", "Zz A")]
        d = _Driver([("RETURN n.entity_id AS id, labels(n)[0] AS type", ents), ("MATCH (d:Document)", self.docs), ("MENTIONED_IN]->(ch:Chunk)", boom)])
        with self.assertRaises(RuntimeError):
            ox.export(d, root, 2)
        ox.export(_Driver([("RETURN n.entity_id AS id, labels(n)[0] AS type", ents), ("MATCH (d:Document)", self.docs)]), root, 2)
        self.assertTrue((root / "People" / "Zz A.md").exists())
        ajena = tempfile.TemporaryDirectory()
        self.addCleanup(ajena.cleanup)
        (Path(ajena.name) / "Documents").mkdir()
        (Path(ajena.name) / "Documents" / "tesis.md").write_text("mía\n", encoding="utf-8")
        (Path(ajena.name) / "_Briefings").symlink_to(self.root / "_Briefings")
        with self.assertRaises(RuntimeError):
            ox.export(_Driver([("RETURN n.entity_id AS id, labels(n)[0] AS type", ents), ("MATCH (d:Document)", self.docs)]), Path(ajena.name), 2)
        self.assertTrue((Path(ajena.name) / "Documents" / "tesis.md").exists())

    def test_a_failed_swap_puts_the_previous_export_back(self):
        from unittest import mock
        self._run([_entity("Person:zz-a", "Zz A", summary="zzanterior")])
        real = Path.rename

        def flaky(self_, target):
            if self_.name == "People" and ".exportando" in self_.parts and ".anterior" not in self_.parts:
                raise OSError("disco lleno")
            return real(self_, target)
        with mock.patch.object(Path, "rename", flaky), self.assertRaises(OSError):
            self._run([_entity("Person:zz-a", "Zz A", summary="zznuevo")])
        self.assertIn("zzanterior", self._read("People/Zz A.md"))
        self.assertTrue((self.root / "Documents" / "auto.md").exists())
        self._run([_entity("Person:zz-a", "Zz A", summary="zznuevo")])
        self.assertIn("zznuevo", self._read("People/Zz A.md"))

    def test_a_case_map_only_lists_what_has_a_note(self):
        self._run([_entity("Person:koldo-garcia", "Koldo García")])
        moc = self._read("_MOC/Caso Koldo.md")
        self.assertIn("[[Koldo García]]", moc)
        self.assertNotIn("[[José Luis Ábalos]]", moc)


class AnonymisedRelativeVaultTest(unittest.TestCase):
    def test_no_chunks_are_fetched_for_a_relative(self):
        driver = _Driver()
        self.assertEqual(ox.fetch_chunks_for(driver, "Person:familiar-de-zz"), [])
        self.assertEqual(driver.calls, [])
        ox.fetch_chunks_for(driver, "Person:zz-a")
        self.assertEqual(len(driver.calls), 1)

    def test_the_year_timeline_drops_the_quote_of_a_relation_with_a_relative(self):
        driver = _Driver()
        ox.fetch_year_events(driver, 2020)
        cypher, params = driver.calls[0]
        self.assertIn("CASE WHEN a.entity_id STARTS WITH $anon OR b.entity_id STARTS WITH $anon THEN null ELSE r.source_quote END AS quote",
                      cypher)
        self.assertNotIn("r.source_quote AS quote", cypher)
        self.assertEqual(params["anon"], "Person:familiar-de-")


if __name__ == "__main__":
    unittest.main()
