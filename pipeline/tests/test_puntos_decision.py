"""Spec 069 T4.10 — every active decision point: an out-of-vocabulary (or doubtful accusatory) LLM
output reaches the review queue or blocks the batch, never the graph or the facts cache.

Covered elsewhere: última hora (test_ultima_hora_stale.StatusVocabularyTest), verify-ingest
(test_revision.VerifyIngestDoubtTest), 063 (test_qa_esc_schema: new_type fuera del vocabulario;
test_qa_esc_jev: incierto acusatorio → dueño), graph cleanup (test_revision.CleanupCrimesTest).
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config, revision
from src.chunking import Chunk

OUT = {"entities": [{"name": "Persona Uno", "type": "Person"}, {"name": "Empresa Dos", "type": "Company"}],
       "relations": [{"source": "Persona Uno", "target": "Empresa Dos", "type": "COLLUDED_WITH", "source_quote": "x"},
                     {"source": "Persona Uno", "target": "Empresa Dos", "type": "PAID", "source_quote": "x"},
                     {"source": "Persona Uno", "target": "Empresa Dos", "type": "WORKS_FOR", "source_quote": "x"}]}
TEXT = "Persona Uno trabaja para Empresa Dos y le pagó."


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [mock.patch.object(revision, "REVISION_DIR", root / "revision"),
                        mock.patch.object(config, "REPORTS_DIR", root / "reports"),
                        mock.patch.object(config, "FACTS_DIR", root / "facts")]
        for p in self.patches:
            p.start()
        (root / "facts").mkdir()
        (root / "reports" / "extractions").mkdir(parents=True)
        self.root = root

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def assertQueued(self, punto):
        motivos = sorted(i["motivo"] for i in revision.pendientes() if i["punto"] == punto)
        self.assertEqual(motivos, ["acusatoria_sin_verificador", "tipo_fuera_de_vocabulario"])


class MlxTest(Base):
    def test_extract_one(self):
        from src import extract_facts as E

        async def fake(system, user, json_mode=False):
            return json.dumps(OUT)
        chunk = Chunk("c1", "doc.pdf", "doc", 3, 3, 0, 0, TEXT)
        with mock.patch.object(E.llm_client, "complete", fake):
            rec = asyncio.run(E._extract_one(chunk))
        self.assertEqual([r["type"] for r in rec["relations"]], ["WORKS_FOR"])
        self.assertQueued("extraccion_mlx")
        # T7.2: the facts line is a decision record.
        self.assertEqual((rec["modelo"], rec["prompt_sha256"], rec["ontologia_version"]),
                         (config.MLX_MODEL, E.PROMPT_SHA256, E._ONT.version))


class SwarmTest(Base):
    def test_ingest_apply(self):
        from scripts import _ingest_apply as A
        (self.root / "reports" / "ingest_chunks.json").write_text(json.dumps({
            "meta": {"n_chunks": 1, "prompt_sha256": "p"},
            "batches": [{"batch_id": 0, "chunks": [{"chunk_id": "c1", "doc_id": "doc.pdf", "doc_title": "doc",
                                                    "page_start": 3, "page_end": 3, "text": TEXT}]}]}))
        (self.root / "reports" / "extractions" / "batch_0.json").write_text(json.dumps([{"chunk_id": "c1", **OUT}]))
        with mock.patch.object(A, "EXTR_DIR", self.root / "reports" / "extractions"):
            A.main()
        rec = json.loads((self.root / "facts" / "doc.pdf.jsonl").read_text())
        self.assertEqual([r["type"] for r in rec["relations"]], ["WORKS_FOR"])
        self.assertQueued("extraccion_enjambre")
        self.assertEqual(rec["prompt_sha256"], "p")


class CendojTest(Base):
    def test_out_of_vocabulary_blocks_the_batch(self):
        from scripts import _cendoj_guard as G
        (self.root / "reports" / "ingest_chunks.json").write_text(json.dumps({
            "batches": [{"chunks": [{"chunk_id": "c1", "text": "Empresa Dos cohecho"}]}]}))
        item = {"chunk_id": "c1", "entities": [{"name": "Empresa Dos", "type": "Company"}],
                "relations": [{"source": "Empresa Dos", "target": "Empresa Dos", "type": "COLLUDED_WITH",
                               "source_quote": "Empresa Dos"}]}
        (self.root / "reports" / "extractions" / "batch_0.json").write_text(json.dumps([item]))
        with mock.patch.object(G, "EXTR_DIR", self.root / "reports" / "extractions"):
            self.assertNotEqual(G.main(), 0)


class ProvenanceTest(unittest.TestCase):
    """T7.2 / T7.5 — 063 attempts and Jev items carry model, prompt hash and ontology version;
    the active Claude workflows pin their model."""

    def test_063_attempt_and_jev_items(self):
        from src import qa_esc_jev, qa_esc_opus
        from src.ontologia import ontologia
        with tempfile.TemporaryDirectory() as tmp:
            att = qa_esc_opus._attempt(Path(tmp) / "a1", "opus", "claude-opus-5-5", ["claude"], "in", "high", {})
        self.assertEqual(att["ontologia_version"], ontologia().version)
        self.assertIn("brief_sha256", att)
        (item,) = qa_esc_jev.items_for("entidad", [{"id": "e1", "state": "estado", "meta": {}}])
        self.assertEqual(item["meta"]["modelo"], qa_esc_jev.JEV_MODEL)
        self.assertEqual(item["meta"]["ontologia_version"], ontologia().version)
        self.assertIn("preguntas_sha256", item["meta"])

    def test_workflows_pin_their_model(self):
        root = Path(__file__).resolve().parents[1] / "scripts"
        for wf in ("ingest_swarm", "verify_ingest", "enrich_entities"):
            src = (root / f"{wf}.workflow.js").read_text(encoding="utf-8")
            self.assertIn("const MODEL = 'opus'", src, wf)
            self.assertEqual(src.count("agent("), src.count("model: MODEL") + src.count("// NO es trabajo de LLM: antes esto era un agent()"), wf)


if __name__ == "__main__":
    unittest.main()
