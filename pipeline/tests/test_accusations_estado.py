"""Registro de estado procesal en la capa de acusaciones (spec 061, T1.1).

Sin grafo: se valida el parseo/validación de `estado:` y los parámetros de la
propagación con un driver falso que registra las consultas.
"""
from pathlib import Path
import tempfile
import unittest

import yaml

from pipeline.src.accusations_loader import (_ESTADO_KEPT_CLOSED_Q, _ESTADO_Q, _prepare,
                                             _prepare_estados, _parse_estado, apply_estados,
                                             load, load_files, prune)
from pipeline.src.documents_loader import sentencia_firme_map

ACCUSATIONS_DIR = Path(__file__).resolve().parents[1] / "data" / "accusations"


class FakeResult:
    def __init__(self, rows): self._rows = rows

    def single(self): return self._rows[0] if self._rows else None

    def data(self): return self._rows

    def __iter__(self): return iter(self._rows)


class FakeSession:
    """Registra (consulta, parámetros) de cada run y devuelve filas prefijadas."""

    def __init__(self, rows=None, kept=None):
        self.calls = []
        self._rows = rows if rows is not None else [{"person_id": "Person:x", "n": 3}]
        self._kept = kept if kept is not None else [{"person_id": "Person:x", "n": 0}]

    def run(self, query, **params):
        self.calls.append((str(query), params))
        # La 1ª consulta es el diagnóstico de disposiciones cerradas (solo lectura).
        return FakeResult(self._kept if len(self.calls) % 2 == 1 else self._rows)

    def __enter__(self): return self

    def __exit__(self, *exc): return False


class FakeDriver:
    def __init__(self, session): self._session = session

    def session(self): return self._session


def _entry(**estado):
    base = {"status": "investigado", "fecha": "2026-05-18", "revisado": "2026-09-09",
            "fuente": {"doc": "auto.pdf", "tipo": "auto"}}
    base.update(estado)
    return {"person": "Fulana de Tal", "entity_id": "Person:fulana", "estado": base,
            "crimes": [{"name": "cohecho"}]}


class EstadoValidationTest(unittest.TestCase):
    def test_bloque_completo(self):
        est = _parse_estado(_entry())
        self.assertEqual(est["status"], "investigado")
        self.assertEqual(est["fecha"], "2026-05-18")
        self.assertEqual(est["fuente"], "auto.pdf")
        self.assertEqual(est["fuente_tipo"], "auto")
        self.assertEqual(est["revisado"], "2026-09-09")

    def test_sin_bloque_no_es_error(self):
        self.assertIsNone(_parse_estado({"person": "Fulana", "status": "acusado"}))

    def test_campos_obligatorios(self):
        for falta in ("fecha", "revisado", "fuente"):
            entry = _entry()
            del entry["estado"][falta]
            with self.assertRaises(SystemExit, msg=falta):
                _parse_estado(entry)

    def test_fecha_mal_formada(self):
        with self.assertRaises(SystemExit):
            _parse_estado(_entry(fecha="18-05-2026"))

    def test_status_fuera_del_enum(self):
        with self.assertRaises(SystemExit):
            _parse_estado(_entry(status="sospechoso"))

    def test_fuente_exige_exactamente_doc_o_url(self):
        with self.assertRaises(SystemExit):
            _parse_estado(_entry(fuente={"tipo": "auto"}))
        with self.assertRaises(SystemExit):
            _parse_estado(_entry(fuente={"doc": "a.pdf", "url": "https://x.es", "tipo": "auto"}))

    def test_url_debe_ser_https(self):
        with self.assertRaises(SystemExit):
            _parse_estado(_entry(fuente={"url": "http://x.es", "tipo": "prensa"}))

    def test_tipo_fuera_del_enum(self):
        with self.assertRaises(SystemExit):
            _parse_estado(_entry(fuente={"doc": "a.pdf", "tipo": "recorte"}))

    def test_acusado_exige_acto_acusatorio(self):
        for tipo in ("auto", "providencia", "sentencia", "prensa"):
            with self.assertRaises(SystemExit, msg=tipo):
                _parse_estado(_entry(status="acusado", fuente={"doc": "a.pdf", "tipo": tipo}))
        for tipo in ("escrito_acusacion", "auto_apertura_juicio_oral", "auto_procesamiento"):
            est = _parse_estado(_entry(status="acusado", fuente={"doc": "a.pdf", "tipo": tipo}))
            self.assertEqual(est["fuente_tipo"], tipo)

    def test_procesado_exige_acto_acusatorio(self):
        with self.assertRaises(SystemExit):
            _parse_estado(_entry(status="procesado", fuente={"doc": "a.pdf", "tipo": "auto"}))

    def test_prensa_solo_sostiene_fases_abiertas(self):
        est = _parse_estado(_entry(status="investigado",
                                   fuente={"url": "https://medio.es/x", "tipo": "prensa"}))
        self.assertEqual(est["fuente_tipo"], "prensa")
        for status in ("condenado", "absuelto", "archivado"):
            with self.assertRaises(SystemExit, msg=status):
                _parse_estado(_entry(status=status,
                                     fuente={"url": "https://medio.es/x", "tipo": "prensa"}))


class EstadoPrecedenceTest(unittest.TestCase):
    def test_estado_gana_al_status_legacy(self):
        entry = _entry(status="investigado")
        entry["status"] = "acusado"  # campo legacy
        edges = _prepare([entry], firme_by_doc={})
        self.assertEqual([e["status"] for e in edges], ["investigado"])

    def test_status_por_delito_sigue_ganando(self):
        entry = _entry(status="investigado")
        entry["crimes"] = [{"name": "cohecho"}, {"name": "prevaricación", "status": "absuelto"}]
        edges = _prepare([entry], firme_by_doc={})
        self.assertEqual([e["status"] for e in edges], ["investigado", "absuelto"])
        # …y queda MARCADO para que la propagación posterior no lo pise.
        self.assertEqual([e["status_explicit"] for e in edges], [None, True])

    def test_status_explicit_se_borra_si_el_yaml_deja_de_declararlo(self):
        entry = _entry(status="investigado")
        entry["crimes"] = [{"name": "cohecho"}]
        # None (no False) → APOC borra la marca de la arista en la re-carga.
        self.assertIsNone(_prepare([entry], firme_by_doc={})[0]["status_explicit"])

    def test_sin_estado_se_conserva_el_comportamiento_legacy(self):
        entry = {"person": "Fulana", "entity_id": "Person:fulana", "status": "acusado",
                 "crimes": [{"name": "cohecho"}]}
        self.assertEqual(_prepare([entry], firme_by_doc={})[0]["status"], "acusado")

    def test_sin_estado_no_publica_acusado(self):
        # Revisión Codex: sin acto acusatorio declarado nadie es «acusado»; el default
        # es la fase más leve, y un status inventado ABORTA en vez de caer al default.
        entry = {"person": "Fulana", "entity_id": "Person:fulana",
                 "crimes": [{"name": "cohecho"}]}
        self.assertEqual(_prepare([entry], firme_by_doc={})[0]["status"], "investigado")
        entry["status"] = "sospechoso"
        with self.assertRaises(SystemExit):
            _prepare([entry], firme_by_doc={})
        entry["status"] = "investigado"
        entry["crimes"] = [{"name": "cohecho", "status": "reo"}]
        with self.assertRaises(SystemExit):
            _prepare([entry], firme_by_doc={})

    def test_tipo_convicted_no_puede_anular_absolucion(self):
        # Una absolución nunca se viste de condena, ni escribiendo `type:` a mano.
        entry = {"person": "Fulana", "entity_id": "Person:fulana", "doc": "sentencia.pdf",
                 "crimes": [{"name": "cohecho", "status": "absuelto", "type": "CONVICTED_OF"}]}
        with self.assertRaises(SystemExit):
            _prepare([entry], firme_by_doc={"sentencia.pdf": True})
        entry["crimes"][0]["status"] = "archivado"
        with self.assertRaises(SystemExit):
            _prepare([entry], firme_by_doc={"sentencia.pdf": True})
        # Sin el `type:` forzado, la absolución sigue siendo una ACCUSED_OF normal.
        del entry["crimes"][0]["type"]
        edges = _prepare([entry], firme_by_doc={"sentencia.pdf": True})
        self.assertEqual((edges[0]["rel_type"], edges[0]["status"]), ("ACCUSED_OF", "archivado"))

    def test_documentos_de_estado_y_condena_incompatibles_abortan(self):
        # La firmeza se calcularía dos veces con documentos distintos: fail-closed.
        sent = {"doc": "sentencia.pdf", "tipo": "sentencia"}
        entry = _entry(status="condenado", fuente=sent)
        entry["crimes"] = [{"name": "cohecho", "doc": "otra-sentencia.pdf"}]
        with self.assertRaises(SystemExit):
            _prepare([entry], firme_by_doc={"sentencia.pdf": True, "otra-sentencia.pdf": False})
        # Con el mismo doc en los dos sitios, la condena carga con su firmeza.
        entry["crimes"] = [{"name": "cohecho", "doc": "sentencia.pdf"}]
        edges = _prepare([entry], firme_by_doc={"sentencia.pdf": True})
        self.assertEqual((edges[0]["rel_type"], edges[0]["status"], edges[0]["firme"]),
                         ("CONVICTED_OF", "condenado", True))

    def test_condenado_no_firme_es_termino_propio(self):
        # Spec 069: el registro puede decir `condenado_no_firme`; la firmeza la sigue
        # fijando documents.yaml, y decir «no firme» de una sentencia firme aborta.
        sent = {"doc": "sentencia.pdf", "tipo": "sentencia"}
        entry = _entry(status="condenado_no_firme", fuente=sent)
        entry["crimes"] = [{"name": "cohecho", "doc": "sentencia.pdf"}]
        edges = _prepare([entry], firme_by_doc={"sentencia.pdf": False})
        self.assertEqual((edges[0]["rel_type"], edges[0]["status"], edges[0]["firme"]),
                         ("CONVICTED_OF", "condenado_no_firme", False))
        rows = _prepare_estados([entry], firme_by_doc={"sentencia.pdf": False})
        self.assertEqual(rows[0]["status"], "condenado_no_firme")
        with self.assertRaises(SystemExit):
            _prepare([entry], firme_by_doc={"sentencia.pdf": True})
        with self.assertRaises(SystemExit):
            _prepare_estados([entry], firme_by_doc={"sentencia.pdf": True})

    def test_sentencia_firme_rechaza_booleano_como_texto(self):
        # `bool("false")` es True: una firmeza citada como texto publicaría como firme
        # una condena recurrida (spec 059).
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "documents.yaml"
            path.write_text(yaml.safe_dump(
                {"documents": [{"doc_id": "sentencia.pdf", "sentencia_firme": "false"}]}),
                encoding="utf-8")
            with self.assertRaises(SystemExit):
                sentencia_firme_map(Path(tmp))
            path.write_text(yaml.safe_dump(
                {"documents": [{"doc_id": "sentencia.pdf", "sentencia_firme": False}]}),
                encoding="utf-8")
            self.assertEqual(sentencia_firme_map(Path(tmp)), {"sentencia.pdf": False})

    def test_prepare_estados_resuelve_entity_id(self):
        rows = _prepare_estados([_entry(), {"person": "Mengana", "crimes": []}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["person_id"], "Person:fulana")
        self.assertEqual(rows[0]["person_name"], "Fulana de Tal")
        self.assertEqual(rows[0]["closed"], [])

    def test_condenado_resuelve_firmeza_fail_closed(self):
        # T0d: `estado: condenado` propaga 'condenado' solo con firmeza declarada true;
        # con false propaga 'condenado_no_firme'; sin declaración (o sin sentencia) aborta.
        sent = {"doc": "sentencia.pdf", "tipo": "sentencia"}
        rows = _prepare_estados([_entry(status="condenado", fuente=sent)],
                                firme_by_doc={"sentencia.pdf": False})
        self.assertEqual(rows[0]["status"], "condenado_no_firme")
        rows = _prepare_estados([_entry(status="condenado", fuente=sent)],
                                firme_by_doc={"sentencia.pdf": True})
        self.assertEqual(rows[0]["status"], "condenado")
        with self.assertRaises(SystemExit):
            _prepare_estados([_entry(status="condenado", fuente=sent)], firme_by_doc={})
        with self.assertRaises(SystemExit):
            _prepare_estados([_entry(status="condenado", fuente={"doc": "sentencia.pdf", "tipo": "auto"})],
                             firme_by_doc={"sentencia.pdf": False})
        # …y la arista curada del delito sigue siendo CONVICTED_OF no firme (spec 059).
        entry = _entry(status="condenado", fuente=sent); entry["doc"] = "sentencia.pdf"
        edges = _prepare([entry], firme_by_doc={"sentencia.pdf": False})
        self.assertEqual((edges[0]["rel_type"], edges[0]["status"]), ("CONVICTED_OF", "condenado_no_firme"))

    def test_delito_cerrado_viaja_con_la_fila(self):
        entry = _entry(status="condenado", fuente={"doc": "sentencia.pdf", "tipo": "sentencia"})
        entry["crimes"] = [{"name": "prevaricación"},
                           {"name": "tráfico de influencias", "status": "absuelto", "doc": "sentencia.pdf"}]
        rows = _prepare_estados([entry], firme_by_doc={"sentencia.pdf": False})
        self.assertEqual(rows[0]["closed"], [{"crime": "Crime:trafico-de-influencias",
                                              "status": "absuelto", "doc": "sentencia.pdf"}])
        # Un status ABIERTO por delito junto a `estado` sigue prohibido (contradicción).
        entry["crimes"][1]["status"] = "acusado"
        with self.assertRaises(SystemExit):
            _prepare_estados([entry], firme_by_doc={"sentencia.pdf": False})


class EstadoPropagationTest(unittest.TestCase):
    def test_parametros_y_alcance_de_la_propagacion(self):
        session = FakeSession()
        rows = _prepare_estados([_entry()])
        n = apply_estados(FakeDriver(session), rows)
        self.assertEqual(n, 3)
        self.assertEqual(len(session.calls), 2)  # diagnóstico + propagación
        query, params = session.calls[1]
        self.assertEqual(params["rows"], rows)
        self.assertEqual(params["excluded_kinds"], ["ultima_hora", "prensa"])
        self.assertEqual(params["closed_statuses"], ["absuelto", "archivado"])
        self.assertEqual(params["open_statuses"],
                         ["investigado", "imputado", "procesado", "acusado", "condena_anulada"])
        # Solo ACCUSED_OF: la firmeza de una condena la declara documents.yaml.
        self.assertIn(":ACCUSED_OF", query)
        self.assertNotIn("CONVICTED_OF", query)
        for prop in ("r.status", "r.status_fecha", "r.status_fuente",
                     "r.status_fuente_tipo", "r.status_revisado"):
            self.assertIn(prop, query)

    def test_no_pisa_el_status_declarado_por_delito(self):
        # Regresión (revisión Codex): sin esta guarda, quien está investigado por un
        # cargo y absuelto de otro se publicaba como investigado en los dos.
        self.assertIn("coalesce(r.status_explicit, false) <> true", _ESTADO_Q)
        self.assertIn("coalesce(r.status_explicit, false) <> true", _ESTADO_KEPT_CLOSED_Q)

    def test_no_toca_una_disposicion_cerrada(self):
        # T0d: una arista absuelta/archivada no se reabre NI se pisa (antes solo se
        # protegía frente a estados abiertos; un `condenado_no_firme` la habría pisado).
        self.assertIn("NOT coalesce(r.status,'') IN $closed_statuses", _ESTADO_Q)
        self.assertNotIn("$open_statuses", _ESTADO_Q)

    def test_el_delito_cerrado_manda_sobre_el_estado_general(self):
        # T0d: quien está condenado por un cargo y absuelto de otro — la arista
        # extraída del cargo absuelto recibe `absuelto` (+ su doc), no la condena.
        self.assertIn("head([x IN row.closed WHERE x.crime = c.entity_id | x.status])", _ESTADO_Q)
        self.assertIn("head([x IN row.closed WHERE x.crime = c.entity_id | x.doc])", _ESTADO_Q)

    def test_diagnostico_de_cerradas_no_escribe(self):
        self.assertNotIn("SET ", _ESTADO_KEPT_CLOSED_Q)
        session = FakeSession(kept=[{"person_id": "Person:fulana", "n": 2}])
        apply_estados(FakeDriver(session), _prepare_estados([_entry()]))
        self.assertEqual(session.calls[0][1]["closed_statuses"], ["absuelto", "archivado"])

    def test_idempotente_misma_llamada(self):
        session = FakeSession()
        rows = _prepare_estados([_entry()])
        driver = FakeDriver(session)
        apply_estados(driver, rows)
        apply_estados(driver, rows)
        self.assertEqual(session.calls[0], session.calls[2])  # diagnóstico
        self.assertEqual(session.calls[1], session.calls[3])  # propagación

    def test_propagacion_setea_firme_coherente(self):
        # Revisión Codex: propagar 'condenado_no_firme' sin `firme` dejaba la arista
        # incoherente (el lint L2 la cantaba). `st` se calcula una vez y manda en las dos.
        self.assertIn("coalesce(head([x IN row.closed WHERE x.crime = c.entity_id | x.status]), "
                      "row.status) AS st", _ESTADO_Q)
        self.assertIn("r.firme = CASE st WHEN 'condenado' THEN true "
                      "WHEN 'condenado_no_firme' THEN false ELSE null END", _ESTADO_Q)
        self.assertIn("r.status = st", _ESTADO_Q)
        self.assertIn("RETURN row.person_id AS person_id, count(r) AS n", _ESTADO_Q)
        session = FakeSession()
        rows = _prepare_estados([_entry(status="condenado",
                                        fuente={"doc": "sentencia.pdf", "tipo": "sentencia"})],
                                firme_by_doc={"sentencia.pdf": False})
        apply_estados(FakeDriver(session), rows)
        _, params = session.calls[1]
        self.assertEqual(params["rows"][0]["status"], "condenado_no_firme")
        self.assertEqual(params["excluded_kinds"], ["ultima_hora", "prensa"])

    def test_sin_filas_no_toca_el_grafo(self):
        session = FakeSession()
        self.assertEqual(apply_estados(FakeDriver(session), []), 0)
        self.assertEqual(session.calls, [])


class CuratedMergeTest(unittest.TestCase):
    def test_la_arista_curada_lleva_status_explicit(self):
        entry = _entry(status="investigado")
        entry["crimes"] = [{"name": "cohecho"}, {"name": "prevaricación", "status": "absuelto"}]
        edges = _prepare([entry], firme_by_doc={})
        session = FakeSession()
        load(FakeDriver(session), edges)
        query, params = session.calls[0]
        self.assertEqual(query.count("status_explicit: row.status_explicit"), 2)  # onCreate + onMatch
        self.assertEqual([r["status_explicit"] for r in params["rows"]], [None, True])

    def test_la_pagina_del_delito_se_publica_como_source_page(self):
        # 071 (R-2026-09-24-0103): la arista curada citaba el auto sin página.
        entry = _entry(status="investigado")
        entry["crimes"] = [{"name": "cohecho", "page": 29}, {"name": "prevaricación"}]
        edges = _prepare([entry], firme_by_doc={})
        self.assertEqual([e["page"] for e in edges], [29, None])
        session = FakeSession()
        load(FakeDriver(session), edges)
        query, params = session.calls[0]
        self.assertEqual(query.count("source_page: row.page"), 2)  # onCreate + onMatch: None la borra
        self.assertEqual([r["page"] for r in params["rows"]], [29, None])


class PruneTest(unittest.TestCase):
    def test_prune_borra_curadas_fuera_del_yaml(self):
        # Idempotencia en los DOS sentidos: si el YAML pasa de condena a absolución del
        # mismo cargo, la vieja CONVICTED_OF curada no puede sobrevivir a su lado.
        entry = {"person": "Fulana", "entity_id": "Person:fulana", "doc": "sentencia.pdf",
                 "crimes": [{"name": "cohecho", "status": "absuelto"}]}
        edges = _prepare([entry], firme_by_doc={"sentencia.pdf": True})
        session = FakeSession()
        prune(FakeDriver(session), edges)
        self.assertEqual(len(session.calls), 2)  # extraídas + curadas
        query, params = session.calls[1]
        self.assertIn("r.source_kind = 'acusacion'", query)
        self.assertIn("type(r) IN ['ACCUSED_OF','CONVICTED_OF']", query)
        self.assertIn("NOT (c.entity_id + '|' + type(r)) IN row.keep_typed", query)
        self.assertEqual(params["rows"][0]["keep_typed"], ["Crime:cohecho|ACCUSED_OF"])
        self.assertEqual(params["rows"][0]["keep"], ["Crime:cohecho"])
        # La consulta de las extraídas sigue siendo la de antes (solo por delito).
        self.assertIn("(r.source_kind IS NULL OR r.source_kind = 'judicial')", session.calls[0][0])


class CuratedFileTest(unittest.TestCase):
    """El fichero curado del repo tiene que pasar el validador (gate de contenido)."""

    def test_accusations_yaml_valida(self):
        rows = _prepare_estados(load_files(ACCUSATIONS_DIR))
        self.assertGreaterEqual(len(rows), 8)
        for r in rows:
            self.assertTrue(r["person_id"].startswith("Person:"), r)

    def test_una_persona_no_declara_dos_estados(self):
        rows = _prepare_estados(load_files(ACCUSATIONS_DIR))
        ids = [r["person_id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)), f"estado duplicado: {ids}")

    def test_con_estado_solo_hay_status_por_delito_cerrados(self):
        """Con `estado`, un status por delito solo puede ser una disposición cerrada
        (absuelto/archivado): es lo que la propagación aplica a la arista de ese cargo.
        Un status abierto por delito junto a `estado` es una contradicción y aborta."""
        accusations = load_files(ACCUSATIONS_DIR)
        mixtos = {(a.get("entity_id") or "").strip(): [(c["name"], c["status"]) for c in a.get("crimes") or []
                                                       if (c.get("status") or "").strip().lower()
                                                       not in ("", "absuelto", "archivado")]
                  for a in accusations if a.get("estado")}
        self.assertEqual({k: v for k, v in mixtos.items() if v}, {})

    def test_el_yaml_carga_como_yaml(self):
        for f in sorted(ACCUSATIONS_DIR.glob("*.yaml")):
            self.assertIsInstance(yaml.safe_load(f.read_text(encoding="utf-8")), dict, f.name)


if __name__ == "__main__":
    unittest.main()


class FuenteExigidaTest(unittest.TestCase):
    """Segunda revisión Codex #15: la fuente que exige un estado sale de su ficha, no de su fase."""

    def _estado(self, status, tipo):
        from pipeline.src.accusations_loader import _parse_estado
        return _parse_estado({"person": "Persona Uno", "estado": {
            "status": status, "fecha": "2024-07-01", "revisado": "2026-10-07",
            "fuente": {"url": "https://example.org/stc", "tipo": tipo}}})

    def test_condena_anulada_needs_the_annulling_sentence(self):
        self.assertEqual(self._estado("condena_anulada", "sentencia")["status"], "condena_anulada")
        with self.assertRaises(SystemExit):
            self._estado("condena_anulada", "auto_procesamiento")

    def test_acusado_still_needs_an_accusatory_act(self):
        self.assertEqual(self._estado("acusado", "escrito_acusacion")["status"], "acusado")
        with self.assertRaises(SystemExit):
            self._estado("acusado", "sentencia")
