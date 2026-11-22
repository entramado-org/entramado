"""Retiradas y fuentes del overlay de contexto (spec 061, T1.3).

Sin grafo: `summaries remove: true`, `role: remove`, `source_url`/`source_date` en
relaciones y el orden de carga de los ficheros `*_qa061.yaml`.
"""
from pathlib import Path
import tempfile
import unittest

from pipeline.src.context_loader import (_ordered, _prepare, _split_summaries, load, load_pseudonyms,
                                         load_summaries)


class FakeResult:
    def __init__(self, row): self._row = row

    def single(self): return self._row


class FakeSession:
    def __init__(self, counts=None):
        self.counts = list(counts or [])
        self.calls = []

    def run(self, query, **params):
        self.calls.append((str(query), params))
        return FakeResult({"n": self.counts.pop(0) if self.counts else 0,
                           "c": 0})

    def __enter__(self): return self

    def __exit__(self, *exc): return False


class FakeDriver:
    def __init__(self, session): self._session = session

    def session(self): return self._session


class SummariesRemoveTest(unittest.TestCase):
    def test_separa_escrituras_y_retiradas(self):
        rows, removes = _split_summaries([
            {"entity_id": "Person:a", "resumen": "Texto curado.", "sources": ["https://x.es"]},
            {"entity_id": "Person:b", "remove": True},
        ])
        self.assertEqual([r["entity_id"] for r in rows], ["Person:a"])
        self.assertEqual(removes, [{"entity_id": "Person:b"}])

    def test_una_fila_incompleta_no_se_ignora(self):
        # Spec 070 (AC192): un resumen vacío o sin `entity_id` ya no se ignora en silencio: el árbol y
        # `load_files` lo paran antes del driver, y aquí aborta con el motivo del árbol.
        for fila, motivo in (({"entity_id": "Person:c", "resumen": "   "},
                              "un resumen sin `resumen` ni retirada (spec 061)"),
                             ({"resumen": "sin id"}, "un resumen sin `entity_id` (spec 061)")):
            with self.subTest(motivo=motivo), self.assertRaises(SystemExit) as err:
                _split_summaries([fila])
            self.assertEqual(str(err.exception), f"context: {motivo}")

    def test_remove_con_resumen_aborta(self):
        with self.assertRaises(SystemExit):
            _split_summaries([{"entity_id": "Person:a", "remove": True, "resumen": "algo"}])

    def test_la_retirada_borra_las_tres_props_y_va_despues(self):
        session = FakeSession()
        load_summaries(FakeDriver(session), [
            {"entity_id": "Person:a", "resumen": "Texto."},
            {"entity_id": "Person:b", "remove": True},
        ])
        self.assertEqual(len(session.calls), 2)
        write_q, remove_q = session.calls[0][0], session.calls[1][0]
        self.assertIn("SET n.summary", write_q)
        self.assertIn("REMOVE n.summary, n.summary_sources, n.summary_updated", remove_q)
        self.assertEqual(session.calls[1][1]["rows"], [{"entity_id": "Person:b"}])

    def test_sin_retiradas_no_lanza_la_segunda_consulta(self):
        session = FakeSession()
        load_summaries(FakeDriver(session), [{"entity_id": "Person:a", "resumen": "Texto."}])
        self.assertEqual(len(session.calls), 1)


class RoleRemoveTest(unittest.TestCase):
    def test_prepare_marca_el_centinela(self):
        nodes, _ = _prepare([
            {"type": "Person", "name": "Fulana de Tal", "role": "remove"},
            {"type": "Person", "name": "Mengana de Cual", "role": "Ministra"},
        ], [])
        by_name = {n["name"]: n for n in nodes}
        self.assertTrue(by_name["Fulana de Tal"]["role_remove"])
        self.assertIsNone(by_name["Fulana de Tal"]["role"])
        self.assertFalse(by_name["Mengana de Cual"]["role_remove"])
        self.assertEqual(by_name["Mengana de Cual"]["role"], "Ministra")

    def test_una_retirada_no_crea_el_nodo(self):
        session = FakeSession()
        nodes, edges = _prepare([{"type": "Person", "name": "Fulana de Tal", "role": "remove"},
                                 {"type": "Person", "name": "Mengana de Cual", "role": "Ministra"}], [])
        load(FakeDriver(session), nodes, edges)
        merge_q, merge_params = session.calls[0]
        self.assertIn("apoc.merge.node", merge_q)
        self.assertEqual([r["name"] for r in merge_params["rows"]], ["Mengana de Cual"])
        remove_q, remove_params = session.calls[-1]
        self.assertIn("REMOVE n.role", remove_q)
        self.assertIn("MATCH (n {entity_id: row.entity_id})", remove_q)
        self.assertEqual(remove_params["rows"], [{"entity_id": "Person:fulana-de-tal"}])


class RelationSourceTest(unittest.TestCase):
    def _edges(self, rel):
        # `source`/`target` son los EXTREMOS de la relación; la fuente va en `source_url`.
        entities = [{"type": "Person", "name": "Fulana de Tal"},
                    {"type": "Organization", "name": "Partido Ficticio"}]
        _, edges = _prepare(entities, [dict(rel, type="MEMBER_OF", source="Fulana de Tal",
                                            target="Partido Ficticio")])
        return edges

    def test_source_y_source_date_viajan_a_la_arista(self):
        edges = self._edges({"source_url": "https://boe.es/x", "source_date": "2024-01-02"})
        self.assertEqual(edges[0]["source_url"], "https://boe.es/x")
        self.assertEqual(edges[0]["source_date"], "2024-01-02")

    def test_url_no_https_no_se_carga(self):
        self.assertEqual(self._edges({"source_url": "boe.es/x"}), [])

    def test_estado_canonico_y_fuera_de_vocabulario_aborta(self):
        # Spec 069 (revisión Codex #15): el estado de un overlay curado es vocabulario de la
        # ontología; uno inventado aborta la carga, no se escribe ni se descarta en silencio.
        url = {"source_url": "https://boe.es/x"}
        self.assertEqual(self._edges(dict(url, status="Imputado"))[0]["status"], "imputado")
        self.assertEqual(self._edges(dict(url, status="condena_anulada"))[0]["status"], "condena_anulada")
        with self.assertRaises(SystemExit):
            self._edges(dict(url, status="culpable"))

    def test_condena_anulada_cita_la_sentencia(self):
        # `exige_fuente: sentencia`: la fila cita la sentencia oficial, no la prensa que la cuenta.
        with self.assertRaises(SystemExit):
            self._edges({"source_url": "https://www.infobae.com/x", "status": "condena_anulada"})
        ok = self._edges({"source_url": "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2024-17480",
                          "status": "condena_anulada"})
        self.assertEqual(ok[0]["status"], "condena_anulada")

    def test_los_overlays_versionados_cargan(self):
        # Todos los estados de pipeline/data/context son vocabulario (si no, `make context` aborta).
        from pipeline.src.context_loader import CONTEXT_DIR, load_files
        entities, relations = load_files(CONTEXT_DIR)[:2]
        _prepare(entities, relations)

    def test_sin_source_url_no_se_carga(self):
        # Fail-closed: sin este gate, el siguiente `make context` resucitaba las
        # aristas sin fuente que la migración de retiradas había borrado.
        self.assertEqual(self._edges({}), [])

    def test_las_filas_con_fuente_conviven_con_las_que_no(self):
        entities = [{"type": "Person", "name": "Fulana de Tal"},
                    {"type": "Organization", "name": "Partido Ficticio"},
                    {"type": "Organization", "name": "Otro Partido"}]
        _, edges = _prepare(entities, [
            {"type": "MEMBER_OF", "source": "Fulana de Tal", "target": "Partido Ficticio"},
            {"type": "MEMBER_OF", "source": "Fulana de Tal", "target": "Otro Partido",
             "source_url": "https://boe.es/x"},
        ])
        self.assertEqual([e["target_id"] for e in edges], ["Organization:otro-partido"])

    def test_la_consulta_de_relaciones_escribe_source_url(self):
        session = FakeSession()
        nodes, edges = _prepare(
            [{"type": "Person", "name": "Fulana de Tal"},
             {"type": "Organization", "name": "Partido Ficticio"}],
            [{"type": "MEMBER_OF", "source": "Fulana de Tal", "target": "Partido Ficticio",
              "source_url": "https://boe.es/x"}])
        load(FakeDriver(session), nodes, edges)
        rel_q = session.calls[1][0]
        self.assertIn("source_url: row.source_url", rel_q)
        self.assertIn("source_date: row.source_date", rel_q)


class LoadOrderTest(unittest.TestCase):
    def test_los_qa061_se_cargan_los_ultimos(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name in ("summaries.yaml", "roles_enriched.yaml", "zeta.yaml",
                         "roles_qa061.yaml", "summaries_qa061.yaml"):
                (d / name).write_text("entities: []\n", encoding="utf-8")
            self.assertEqual([f.name for f in _ordered(d)],
                             ["roles_enriched.yaml", "summaries.yaml", "zeta.yaml",
                              "roles_qa061.yaml", "summaries_qa061.yaml"])


if __name__ == "__main__":
    unittest.main()


class EntityIdOverrideTest(unittest.TestCase):
    def test_explicit_entity_id_wins_over_name_derivation(self):
        from src import context_loader as cl
        nodes, _ = cl._prepare([{"type": "Person", "name": "Daniel (seudónimo TS)", "entity_id": "Person:daniel-seudonimo-ts", "role": "remove"}], [])
        self.assertEqual(nodes[0]["entity_id"], "Person:daniel-seudonimo-ts")
        self.assertTrue(nodes[0]["role_remove"])

    def test_without_entity_id_the_name_derives_it(self):
        from src import context_loader as cl
        nodes, _ = cl._prepare([{"type": "Person", "name": "Koldo García", "role": "x"}], [])
        self.assertEqual(nodes[0]["entity_id"], "Person:koldo-garcia")


class PseudonymRenameTest(unittest.TestCase):
    def test_rename_from_rekeys_before_flagging(self):
        # 063 pendientes (2026-10-04): id/nombre del seudónimo daban la empresa; el loader los cambia.
        session = FakeSession()
        load_pseudonyms(FakeDriver(session), [{"id": "Person:x-ats-1", "rename_from": "Person:x-empresa",
                                               "name": "X (ATS 1)", "note": "n"}])
        (rename_q, params), (flag_q, _) = session.calls
        self.assertIn("SET n.entity_id = row.new", rename_q)
        self.assertIn("NOT EXISTS { MATCH (m {entity_id: row.new}) }", rename_q)
        self.assertEqual(params["rows"], [{"old": "Person:x-empresa", "new": "Person:x-ats-1", "name": "X (ATS 1)"}])
        self.assertIn("SET n.is_pseudonym = true", flag_q)

    def test_without_rename_from_only_flags(self):
        session = FakeSession()
        load_pseudonyms(FakeDriver(session), [{"id": "Person:y", "note": "n"}])
        self.assertEqual(len(session.calls), 1)

    def test_the_flag_sets_the_roster_sample_name(self):
        # Spec 070 (AC83): name and canonical_name of every roster node become the roster's sample form, in every
        # Path A: the row's `name` if it has one, else the capitalised first name; never the census' or the graph's.
        session = FakeSession()
        load_pseudonyms(FakeDriver(session), [{"id": "Person:zz-ats-1234-2025", "note": "n"},
                                              {"id": "Person:yy-xx", "name": "Yy Xx (seudónimo TS)", "note": "n"}])
        (flag_q, params), = session.calls
        self.assertIn("n.name = row.name, n.canonical_name = row.name", flag_q)
        self.assertEqual([(r["entity_id"], r["name"]) for r in params["rows"]],
                         [("Person:zz-ats-1234-2025", "Zz"), ("Person:yy-xx", "Yy Xx (seudónimo TS)")])
