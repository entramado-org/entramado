"""Spec 069 F4 — review queue and the shared extraction guards (T4.1-T4.3, T4.10)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import extraction_guards as G
from src import revision as R


def ent(name, type_):
    return {"name": name, "type": type_, "aliases": [], "role": None}


def rel(s, t, type_, **kw):
    return {"source": s, "target": t, "type": type_, "source_quote": "cita", **kw}


TEXT = "Persona Uno pagó a Empresa Dos. Persona Uno es socia de Empresa Dos."


class GuardsTest(unittest.TestCase):
    def test_out_of_vocabulary_goes_to_review_not_to_output(self):
        data = {"entities": [ent("Persona Uno", "Person"), ent("Empresa Dos", "Company")],
                "relations": [rel("Persona Uno", "Empresa Dos", "COLLUDED_WITH")]}
        ex, rech = G.validar_extraccion(data, TEXT)
        self.assertEqual(ex.relations, [])
        self.assertEqual([r["motivo"] for r in rech], ["tipo_fuera_de_vocabulario"])
        self.assertEqual(len(rech[0]["entidades"]), 2)

    def test_loader_only_type_and_unknown_entity_type(self):
        data = {"entities": [ent("Persona Uno", "Person"), ent("Ayto", "Ayuntamiento")],
                "relations": [rel("Persona Uno", "Ayto", "CONTRACTOR_OF")]}
        ex, rech = G.validar_extraccion(data, TEXT)
        self.assertEqual(sorted(r["motivo"] for r in rech),
                         ["tipo_entidad_fuera_de_vocabulario", "tipo_solo_loader"])

    def test_accusatory_without_verifier_goes_to_review(self):
        data = {"entities": [ent("Persona Uno", "Person"), ent("Empresa Dos", "Company")],
                "relations": [rel("Persona Uno", "Empresa Dos", "PAID", amount=100),
                              rel("Persona Uno", "Empresa Dos", "OWNS")]}
        ex, rech = G.validar_extraccion(data, TEXT, acusatorias_a_revision=True)
        self.assertEqual([r.type.value for r in ex.relations], ["OWNS"])
        self.assertEqual([r["motivo"] for r in rech], ["acusatoria_sin_verificador"])
        ex2, rech2 = G.validar_extraccion(data, TEXT, acusatorias_a_revision=False)
        self.assertEqual(len(ex2.relations), 2)
        self.assertEqual(rech2, [])

    def test_per_type_doubt_exceptions_without_verifier(self):
        # Revisión Codex #15: CONTROLS y FAMILY_OF no son acusatorias, pero su política de duda es
        # `revision`; en una vía sin verificador no entran solas.
        data = {"entities": [ent("Persona Uno", "Person"), ent("Persona Tres", "Person"),
                             ent("Empresa Dos", "Company")],
                "relations": [rel("Persona Uno", "Empresa Dos", "CONTROLS"),
                              rel("Persona Uno", "Persona Tres", "FAMILY_OF"),
                              rel("Persona Uno", "Empresa Dos", "OWNS")]}
        ex, rech = G.validar_extraccion(data, TEXT, acusatorias_a_revision=True)
        self.assertEqual([r.type.value for r in ex.relations], ["OWNS"])
        self.assertEqual([(r["termino"], r["motivo"]) for r in rech],
                         [("CONTROLS", "revision_sin_verificador"), ("FAMILY_OF", "revision_sin_verificador")])

    def test_endpoints_outside_the_card(self):
        data = {"entities": [ent("Juzgado Tres", "Court"), ent("Persona Uno", "Person")],
                "relations": [rel("Juzgado Tres", "Persona Uno", "INVESTIGATED_BY")]}
        ex, rech = G.validar_extraccion(data, TEXT)
        self.assertEqual(ex.relations, [])
        self.assertEqual(rech[0]["motivo"], "extremos_fuera_de_ficha")

    def test_crimes_canonicalised_or_reviewed(self):
        data = {"entities": [ent("Persona Uno", "Person"), ent("Soborno", "Crime"), ent("Piratería", "Crime")],
                "relations": [rel("Persona Uno", "Soborno", "ACCUSED_OF"),
                              rel("Persona Uno", "Piratería", "ACCUSED_OF")]}
        ex, rech = G.validar_extraccion(data, TEXT)
        self.assertIn("cohecho", [e.name for e in ex.entities])
        self.assertEqual([(r.target, r.type.value) for r in ex.relations], [("cohecho", "ACCUSED_OF")])
        self.assertEqual(sorted(r["motivo"] for r in rech), ["delito_no_canonico", "delito_no_canonico"])

    def test_both_paths_share_the_guards(self):
        # The swarm applier no longer has its own (weaker) validator.
        import inspect
        from scripts import _ingest_apply
        src = inspect.getsource(_ingest_apply)
        self.assertIn("validar_extraccion", src)
        self.assertNotIn("def _lenient", src)


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "revision"
        self.facts = Path(self.tmp.name) / "facts"
        self.facts.mkdir()
        (self.facts / "doc.pdf.jsonl").write_text(json.dumps(
            {"chunk_id": "c1", "doc_id": "doc.pdf", "entities": [ent("Persona Uno", "Person")],
             "relations": []}) + "\n", encoding="utf-8")
        self.p = mock.patch.object(R, "REVISION_DIR", self.dir)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_encolar_is_idempotent_and_versioned(self):
        meta = {"doc_id": "doc.pdf", "chunk_id": "c1", "page_start": 3}
        data = {"entities": [ent("Persona Uno", "Person"), ent("Empresa Dos", "Company")],
                "relations": [rel("Persona Uno", "Empresa Dos", "PAID")]}
        _, rech = G.validar_extraccion(data, TEXT, acusatorias_a_revision=True)
        G.encolar_rechazos("extraccion_mlx", meta, rech, prompt_sha256="x")
        G.encolar_rechazos("extraccion_mlx", meta, rech, prompt_sha256="x")
        items = R.pendientes()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["ontologia_version"], R.ontologia().version)
        self.assertEqual(R.resumen()[("extraccion_mlx", "acusatoria_sin_verificador")], 1)

    def test_aprobar_writes_into_the_facts_cache_and_descartar_does_not(self):
        meta = {"doc_id": "doc.pdf", "chunk_id": "c1", "page_start": 3}
        data = {"entities": [ent("Persona Uno", "Person"), ent("Empresa Dos", "Company")],
                "relations": [rel("Persona Uno", "Empresa Dos", "PAID"), rel("Persona Uno", "Empresa Dos", "RECEIVED")]}
        _, rech = G.validar_extraccion(data, TEXT, acusatorias_a_revision=True)
        G.encolar_rechazos("extraccion_mlx", meta, rech)
        a, b = [i["id"] for i in R.pendientes()]
        with self.assertRaises(SystemExit):
            R.resolver([a], "aprobada", "  ", facts_dir=self.facts)  # nota obligatoria
        R.resolver([a], "aprobada", "revisado con el auto, p.3", facts_dir=self.facts)
        R.resolver([b], "descartada", "no consta el pago", facts_dir=self.facts)
        rec = json.loads((self.facts / "doc.pdf.jsonl").read_text(encoding="utf-8"))
        self.assertEqual([r["type"] for r in rec["relations"]], ["PAID"])
        self.assertEqual(rec["relations"][0]["revision"], a)
        self.assertIn("Empresa Dos", [e["name"] for e in rec["entities"]])
        self.assertEqual(R.pendientes(), [])
        with self.assertRaises(SystemExit):
            R.resolver([a], "descartada", "otra vez", facts_dir=self.facts)


if __name__ == "__main__":
    unittest.main()


class EntityRejectionTest(unittest.TestCase):
    def test_an_entity_rejection_can_only_be_discarded(self):
        # Revisión Codex #15: aprobar un delito fuera de vocabulario no puede escribir nada.
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(R, "REVISION_DIR", Path(tmp)):
            data = {"entities": [ent("Persona Uno", "Person"), ent("Piratería", "Crime")], "relations": []}
            _, rech = G.validar_extraccion(data, TEXT)
            G.encolar_rechazos("extraccion_mlx", {"doc_id": "doc.pdf", "chunk_id": "c1"}, rech)
            (iid,) = [i["id"] for i in R.pendientes()]
            with self.assertRaises(SystemExit):
                R.resolver([iid], "aprobada", "parece un delito", facts_dir=Path(tmp))
            R.resolver([iid], "descartada", "no es un delito del Código Penal", facts_dir=Path(tmp))
            self.assertEqual(R.pendientes(), [])


class VerifiedIngestGuardsTest(unittest.TestCase):
    """Revisión Codex #15: el OK del verificador no exime al registro final de la ontología."""

    def test_verified_records_pass_the_guards(self):
        import inspect
        from scripts import _apply_verified as AV
        self.assertIn("validar_extraccion(", inspect.getsource(AV.main))
        self.assertIn(AV.PUNTO_GUARDAS, R.PUNTOS_EXTRACCION)
        # What the gate's output looks like after an OK verdict: an invented crime, a company as
        # the object of ACCUSED_OF and a loader-only type. None reaches the cache.
        data = {"entities": [ent("Persona Uno", "Person"), ent("Empresa Dos", "Company"),
                             ent("Piratería", "Crime")],
                "relations": [rel("Persona Uno", "Piratería", "ACCUSED_OF"),
                              rel("Persona Uno", "Empresa Dos", "ACCUSED_OF"),
                              rel("Empresa Dos", "Persona Uno", "CONTRACTOR_OF")]}
        ex, rech = G.validar_extraccion(data)
        self.assertEqual(ex.relations, [])
        self.assertEqual(sorted(r["motivo"] for r in rech),
                         ["delito_no_canonico", "delito_no_canonico", "extremos_fuera_de_ficha", "tipo_solo_loader"])


class VerifyIngestDoubtTest(unittest.TestCase):
    """T4.4 — the four cases of the gated verify-ingest verdicts."""

    def test_four_cases(self):
        from scripts import _apply_verified as A
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            pay = lambda t: {"key": f"c1#{t}", "doc_id": "doc.pdf", "chunk_id": "c1", "relation": {"type": t}}
            self.assertEqual(A.decidir("OK", "PAID", pay("PAID"), directory=d), "entra")
            self.assertEqual(A.decidir("FUERA", "WORKS_FOR", pay("WORKS_FOR"), directory=d), "fuera")
            self.assertEqual(A.decidir("DUDA", "WORKS_FOR", pay("WORKS_FOR"), directory=d), "entra_baja")
            self.assertEqual(A.decidir("DUDA", "PAID", pay("PAID"), directory=d), "revision")
            self.assertEqual(A.decidir("DUDA", "FAMILY_OF", pay("FAMILY_OF"), directory=d), "revision")
            (item,) = [i for i in R.pendientes(d) if i["termino"] == "PAID"]
            R.resolver([item["id"]], "aprobada", "consta en el auto", directory=d)
            self.assertEqual(A.decidir("DUDA", "PAID", pay("PAID"), directory=d), "entra")


class CleanupCrimesTest(unittest.TestCase):
    """T4.8 — graph cleanup keeps canonical crimes and queues every other name (never deletes)."""

    def test_topic_crimes(self):
        from src import cleanup

        class Sess:
            def __init__(self, rows, deleted): self.rows, self.deleted = rows, deleted
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def run(self, q, **kw):
                if "DETACH DELETE" in q:
                    self.deleted.extend(kw["ids"])
                return self.rows

        class Drv:
            def __init__(self, rows): self.rows, self.deleted = rows, []
            def session(self): return Sess(self.rows, self.deleted)

        rows = [{"id": "Crime:a", "name": "cohecho"}, {"id": "Crime:b", "name": "defraudación tributaria"},
                {"id": "Crime:c", "name": "delito de cohecho continuado"}, {"id": "Crime:d", "name": "corrupción"}]
        rows += [{"id": "Crime:e", "name": "revelación de secretos"}, {"id": "Crime:f", "name": "abuso de poder"}]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(R, "REVISION_DIR", Path(tmp)):
            drv = Drv(rows)
            # Revisión Codex #15: cleanup never deletes a Crime (an ambiguous or unknown name may be
            # a real charge with accusatory edges); everything non-canonical goes to review.
            self.assertEqual(cleanup._queue_noncanonical_crimes(drv), 5)
            self.assertEqual(drv.deleted, [])
            self.assertEqual(sorted((i["motivo"], i["termino"]) for i in R.pendientes()),
                             [("delito_desconocido", "abuso de poder"),
                              ("delito_desconocido", "corrupción"),
                              ("delito_desconocido", "revelación de secretos"),
                              ("delito_no_canonico", "delito de cohecho continuado"),
                              ("delito_sin_canonizar", "defraudación tributaria")])


class CrimeMigrationTest(unittest.TestCase):
    """T4.9 — the migration merges synonyms, leaves ambiguous names to the owner unless mapped."""

    def test_classify(self):
        from scripts import gen_crime_canonical_migration as M
        node = lambda n: {"labels": ["Crime"], "props": {"entity_id": f"Crime:{n}", "name": n}}
        nodes = [node("cohecho"), node("defraudación tributaria"), node("revelación de secretos"),
                 {"labels": ["Person"], "props": {"entity_id": "Person:x", "name": "x"}}]
        plan = M.classify(nodes)
        self.assertEqual(plan["ok"], 1)
        self.assertEqual([m[3] for m in plan["merge"]], ["delito contra la Hacienda Pública"])
        self.assertEqual([o[1] for o in plan["owner"]], ["revelación de secretos"])
        plan = M.classify(nodes, {"revelación de secretos": "revelación de datos reservados"})
        self.assertEqual(plan["owner"], [])
        self.assertIn('"delito contra la Hacienda Pública"', M.cypher(plan))
        with self.assertRaises(SystemExit):
            M.classify(nodes, {"revelación de secretos": "secretos"})

    def test_dry_run_counts_edges_to_repoint(self):
        from scripts import gen_crime_canonical_migration as M
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "nodes.jsonl").write_text("\n".join(json.dumps(n) for n in [
                {"ref": "n1", "labels": ["Crime"], "props": {"entity_id": "Crime:x", "name": "defraudación tributaria"}},
                {"ref": "n2", "labels": ["Person"], "props": {"entity_id": "Person:a", "name": "a"}}]))
            (d / "relationships.jsonl").write_text("\n".join(json.dumps(r) for r in [
                {"start": "n2", "end": "n1", "type": "ACCUSED_OF"}, {"start": "n2", "end": "n2", "type": "X"}]))
            plan = M.classify([json.loads(l) for l in (d / "nodes.jsonl").read_text().splitlines()])
            self.assertEqual(M.aristas_a_repuntar(d, plan), {"Crime:x": 1})


class VerifiedIngestCacheTest(unittest.TestCase):
    """Segunda revisión Codex #15: `main()` de extremo a extremo con E/S en un tmpdir."""

    def _run(self, tmp: Path, relations: list[dict], verdicts: dict) -> dict:
        from scripts import _apply_verified as AV
        rep, facts = tmp / "reports", tmp / "facts"
        (rep / "verify_verdicts").mkdir(parents=True)
        (rep / "extractions").mkdir()
        facts.mkdir(exist_ok=True)
        (rep / "verify_verdicts" / "final_state.json").write_text(json.dumps(
            {"relations": verdicts, "entity_map": {"accept": {}, "type_force": {}}}))
        (rep / "verify_dump.json").write_text(json.dumps(
            {"batches": [[{"chunk_id": "c1", "doc_id": "doc", "page": 1, "relations": relations}]]}))
        (rep / "ingest_chunks.json").write_text(json.dumps(
            {"batches": [{"chunks": [{"chunk_id": "c1", "doc_id": "doc", "doc_title": "Doc", "page_start": 1}]}]}))
        ents = {"Persona Uno": "Person", "Empresa Dos": "Company", "Empresa Tres": "Company",
                "Piratería": "Crime", "cohecho": "Crime"}
        (rep / "extractions" / "batch_1.json").write_text(json.dumps(
            [{"entities": [ent(n, t) for n, t in ents.items()]}], ensure_ascii=False))
        with mock.patch.object(AV.config, "REPORTS_DIR", rep), mock.patch.object(AV.config, "FACTS_DIR", facts), \
                mock.patch.object(AV, "VV", rep / "verify_verdicts"), mock.patch.object(AV, "EXTR_DIR", rep / "extractions"), \
                mock.patch.object(R, "REVISION_DIR", tmp / "revision"), mock.patch("builtins.print"):
            AV.main()
        return json.loads((facts / "doc.jsonl").read_text(encoding="utf-8").splitlines()[0])

    def test_all_rejected_chunk_overwrites_the_old_cache(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "facts").mkdir()
            old = {"chunk_id": "c1", "doc_id": "doc", "relations": [rel("Persona Uno", "Piratería", "ACCUSED_OF")]}
            (tmp / "facts" / "doc.jsonl").write_text(json.dumps(old, ensure_ascii=False) + "\n")
            r = {"key": "c1#0", "source": "Persona Uno", "target": "Piratería", "type": "ACCUSED_OF", "quote": "cita"}
            rec = self._run(tmp, [r], {"c1#0": {"status": "OK"}})
            self.assertEqual(rec["relations"], [])  # the rejected accusation is not left in the cache
            # Both the invented crime (entity) and the accusation (relation) wait for a person.
            self.assertEqual(len(R.pendientes(tmp / "revision")), 2)

    def test_low_confidence_mark_survives_normalisation_and_shared_quote(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            q = " cita compartida " + "x" * 320
            rels = [{"key": "c1#0", "source": "Persona Uno", "target": "Empresa Dos", "type": "WORKS_FOR", "quote": q},
                    {"key": "c1#1", "source": "Persona Uno", "target": "Empresa Tres", "type": "WORKS_FOR", "quote": q}]
            rec = self._run(tmp, rels, {"c1#0": {"status": "DUDA"}, "c1#1": {"status": "OK"}})
            marks = {r["target"]: r.get("confianza") for r in rec["relations"]}
            self.assertEqual(marks, {"Empresa Dos": "baja", "Empresa Tres": None})

    def test_unverified_chunks_of_the_same_doc_are_kept(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "facts").mkdir()
            other = {"chunk_id": "c2", "doc_id": "doc",  # legacy line: no page_start
                     "relations": [rel("Persona Uno", "Empresa Dos", "WORKS_FOR")]}
            (tmp / "facts" / "doc.jsonl").write_text("\n" + json.dumps(other, ensure_ascii=False) + "\n  \n")
            r = {"key": "c1#0", "source": "Persona Uno", "target": "Piratería", "type": "ACCUSED_OF", "quote": "cita"}
            self._run(tmp, [r], {"c1#0": {"status": "OK"}})
            recs = [json.loads(x) for x in (tmp / "facts" / "doc.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(x["chunk_id"], len(x["relations"])) for x in recs], [("c2", 1), ("c1", 0)])
