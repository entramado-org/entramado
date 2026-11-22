"""Spec 069 — the API/MCP read the controlled vocabulary instead of holding their own lists."""
import typing
import unittest

from app import ontologia


class OntologiaApiTest(unittest.TestCase):
    def test_status_rank_keeps_the_public_order(self):
        r = ontologia.rango_estado()
        self.assertEqual(set(r), {"absuelto", "archivado", "investigado", "imputado", "procesado", "acusado",
                                  "condena_anulada", "condenado_no_firme", "condenado"})
        self.assertEqual(r["condena_anulada"], r["acusado"])  # decisión 07-oct: sin condena vigente
        self.assertLess(r["investigado"], r["imputado"])
        self.assertLess(r["condenado_no_firme"], r["condenado"])
        self.assertEqual(r["procesado"], r["acusado"])

    def test_layer_rank(self):
        self.assertEqual(ontologia.capas()[0], "judicial")
        self.assertEqual(ontologia.rango_capa()["boe"], 9)

    def test_graph_queries_use_it(self):
        from app import graph_queries
        self.assertEqual(graph_queries._STATUS_RANK, ontologia.rango_estado())
        self.assertEqual(graph_queries._KIND_RANK, ontologia.rango_capa())

    def test_mcp_vocabulary(self):
        from app import mcp_server
        self.assertIn("archivado", mcp_server.INSTRUCTIONS)
        self.assertIn("investigado|imputado|procesado|acusado|condena_anulada|condenado_no_firme|condenado|absuelto|archivado",
                      mcp_server.INSTRUCTIONS)
        self.assertEqual(set(typing.get_args(mcp_server.Procedencia)), set(ontologia.capas()))
        self.assertIn("MoneyAmount", typing.get_args(mcp_server.EntityLabel))


if __name__ == "__main__":
    unittest.main()
