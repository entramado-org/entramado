"""Caducidad de las acusaciones de última hora (spec 061).

Sin grafo: `_prepare` decide qué relaciones entran según la fecha, el tipo y el
registro de estado de `accusations.yaml`.
"""
from datetime import date
from pathlib import Path
import unittest

from pipeline.src.ultima_hora_loader import _is_stale, _prepare, registered_defendants

AS_OF = date(2026, 9, 9)
FRESH = "2026-08-01"   # 39 días
STALE = "2026-01-15"   # 238 días
ACCUSATIONS_DIR = Path(__file__).resolve().parents[1] / "data" / "accusations"

ENTITIES = [
    {"name": "Fulana de Tal", "type": "Person"},
    {"name": "Koldo García", "type": "Person"},     # sí está en el registro
    {"name": "cohecho", "type": "Crime"},
    {"name": "Empresa Ficticia SL", "type": "Company"},
]


def _rel(**extra):
    r = {"type": "ACCUSED_OF", "source": "Fulana de Tal", "target": "cohecho",
         "outlet": "El Medio", "url": "https://elmedio.es/x", "date": STALE}
    r.update(extra)
    return r


def _prep(rels, registered=frozenset()):
    return _prepare(ENTITIES, rels, as_of=AS_OF, registered=set(registered))[1]


class StaleDetectionTest(unittest.TestCase):
    def test_umbral_de_90_dias(self):
        self.assertFalse(_is_stale({"date": "2026-06-11"}, AS_OF))  # exactamente 90
        self.assertTrue(_is_stale({"date": "2026-06-10"}, AS_OF))   # 91
        self.assertFalse(_is_stale({"date": FRESH}, AS_OF))

    def test_sin_fecha_o_fecha_invalida_no_caduca(self):
        # La retirada aprobada es la de las aristas CON `date` > 90 días.
        self.assertFalse(_is_stale({}, AS_OF))
        self.assertFalse(_is_stale({"date": "2026"}, AS_OF))
        self.assertFalse(_is_stale({"date": "sin fecha"}, AS_OF))


class StalePrepareTest(unittest.TestCase):
    def test_caducada_sin_registro_no_se_carga(self):
        self.assertEqual(_prep([_rel()]), [])

    def test_caducada_con_registro_se_carga(self):
        edges = _prep([_rel(source="Koldo García")], registered={"Person:koldo-garcia"})
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["source_id"], "Person:koldo-garcia")

    def test_reciente_se_carga_aunque_no_haya_registro(self):
        edges = _prep([_rel(date=FRESH)])
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["date"], FRESH)

    def test_relacion_no_acusatoria_caducada_se_carga(self):
        edges = _prep([_rel(type="WORKS_FOR", target="Empresa Ficticia SL"),
                       _rel(type="MET_WITH", target="Koldo García")])
        self.assertEqual([e["type"] for e in edges], ["WORKS_FOR", "MET_WITH"])

    def test_los_tres_tipos_acusatorios_caducan(self):
        rels = [_rel(type="ACCUSED_OF"),
                _rel(type="INVESTIGATED_BY", target="Empresa Ficticia SL"),
                _rel(type="CONVICTED_OF")]
        self.assertEqual(_prep(rels), [])

    def test_el_registro_vale_en_cualquiera_de_los_extremos(self):
        edges = _prep([_rel(type="INVESTIGATED_BY", source="Empresa Ficticia SL",
                            target="Koldo García")], registered={"Person:koldo-garcia"})
        self.assertEqual(len(edges), 1)


class RegisteredDefendantsTest(unittest.TestCase):
    def test_lee_el_registro_real(self):
        ids = registered_defendants(ACCUSATIONS_DIR)
        # Todas las entradas curadas declaran `estado` o `status:` + `doc:`.
        self.assertIn("Person:koldo-garcia", ids)
        self.assertIn("Person:begona-gomez", ids)
        self.assertNotIn("Person:fulana-de-tal", ids)

    def test_directorio_inexistente_no_revienta(self):
        self.assertEqual(registered_defendants(Path("/no/existe/aqui")), set())


if __name__ == "__main__":
    unittest.main()


class StatusSoloEnAcusacionesTest(unittest.TestCase):
    def test_el_estado_no_viaja_en_relaciones_que_no_son_acusaciones(self):
        # 063 (2026-09-29): 40 WORKS_FOR/ASSOCIATED_WITH de prensa se publicaban con «Imputado».
        edges = _prep([_rel(type="WORKS_FOR", target="Empresa Ficticia SL", status="imputado", date=FRESH),
                       _rel(status="imputado", date=FRESH)])
        by_type = {e["type"]: e for e in edges}
        self.assertIsNone(by_type["WORKS_FOR"]["status"])
        self.assertEqual(by_type["ACCUSED_OF"]["status"], "imputado")


class StatusVocabularyTest(unittest.TestCase):
    """Spec 069 T4.5 — the feed's status is ontology vocabulary; a bad one goes to review."""

    def test_bad_status_goes_to_review_and_good_one_is_canonical(self):
        import tempfile
        from unittest import mock
        from pipeline.src import revision
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(revision, "REVISION_DIR", Path(tmp)):
            edges = _prep([_rel(date=FRESH, status="sospechoso"), _rel(date=FRESH, status="Imputado",
                                                                       target="cohecho", source="Koldo García")])
            self.assertEqual([e["status"] for e in edges], ["imputado"])
            (item,) = revision.pendientes()
            self.assertEqual((item["punto"], item["motivo"], item["termino"]),
                             ("ultima_hora", "estado_fuera_de_vocabulario", "sospechoso"))
