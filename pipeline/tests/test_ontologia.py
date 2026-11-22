"""Spec 069 — the controlled vocabulary: invariants of the YAML and the helpers (no models, no graph)."""
import copy
import unittest

import yaml

from src import ontologia as O
from src.schema import EntityType, RelationType

ONT = O.ontologia()


def raw():
    return copy.deepcopy(ONT.raw)


class InvariantesTest(unittest.TestCase):
    def test_real_file_is_valid_and_versioned(self):
        self.assertIsInstance(ONT.version, int)
        self.assertEqual(len(ONT.sha256), 64)

    def assertRompe(self, r, fragmento):
        with self.assertRaises(O.OntologiaError) as cm:
            O.validar_ontologia(r)
        self.assertTrue(any(fragmento in p for p in cm.exception.problemas), cm.exception.problemas)

    def test_version_must_be_int(self):
        r = raw(); r["version"] = "1"
        self.assertRompe(r, "version")

    def test_every_section_requires_its_full_card(self):
        for sec, campos in O.CAMPOS.items():
            term = next(iter(ONT.raw[sec]))
            for campo in campos:
                if campo == "articulos":
                    continue
                r = raw(); del r[sec][term][campo]
                self.assertRompe(r, f"{sec}.{term}: falta `{campo}`")

    def test_synonym_belongs_to_one_term(self):
        r = raw(); r["tipos_relacion"]["MET_WITH"]["sinonimos"].append("ASOCIADO_CON")
        self.assertRompe(r, "pertenece a la vez")
        r = raw(); r["delitos"]["estafa"]["sinonimos"].append("Soborno")
        self.assertRompe(r, "pertenece a la vez")

    def test_symmetry_matches_direction(self):
        r = raw(); r["tipos_relacion"]["PAID"]["simetrica"] = True
        self.assertRompe(r, "simétrica exige")
        r = raw(); r["tipos_relacion"]["FAMILY_OF"]["simetrica"] = False
        self.assertRompe(r, "no simétrica exige")

    def test_endpoint_types_exist(self):
        r = raw(); r["tipos_relacion"]["OWNS"]["objeto"].append("Account")
        self.assertRompe(r, "tipos de entidad desconocidos")

    def test_doubt_policy_closed(self):
        r = raw(); r["politica_duda"]["acusatoria"]["accion"] = "quizá"
        self.assertRompe(r, "politica_duda.acusatoria")

    def test_accusatory_doubt_cannot_be_relaxed(self):
        r = raw(); r["politica_duda"]["acusatoria"]["accion"] = "mantener"
        self.assertRompe(r, "por decisión del dueño")

    def test_relation_doubt_override_cannot_relax(self):
        r = raw(); r["tipos_relacion"]["PAID"]["duda"] = "mantener"
        self.assertRompe(r, "no puede relajar")

    def test_booleans_are_strict(self):
        # `simetrica: "false"` is truthy: it would make PAID symmetric (revisión Codex 07-oct).
        r = raw(); r["tipos_relacion"]["PAID"]["simetrica"] = "false"
        self.assertRompe(r, "`simetrica` debe ser un booleano")
        r = raw(); r["estados_procesales"]["acusado"]["admite_prensa"] = "no"
        self.assertRompe(r, "`admite_prensa` debe ser un booleano")

    def test_endpoints_not_empty_and_pairs_valid(self):
        r = raw(); r["tipos_relacion"]["OWNS"]["sujeto"] = []
        self.assertRompe(r, "al menos un tipo")
        r = raw(); r["tipos_relacion"]["INVOLVES"]["pares_extra"] = [["LegalCase", "Delito"]]
        self.assertRompe(r, "no es un par")

    def test_crime_family_and_ambiguous_names(self):
        r = raw(); r["delitos"]["cohecho pasivo"]["familia"] = "corrupción"
        self.assertRompe(r, "no es un delito canónico")
        r = raw(); r["delitos_ambiguos"] = {"soborno": ["cohecho"]}
        self.assertRompe(r, "es también un término o sinónimo")
        r = raw(); r["delitos_ambiguos"] = {"secretos": ["inventado"]}
        self.assertRompe(r, "candidatos deben ser delitos canónicos")

    def test_examples_are_synthetic(self):
        # The file is public: examples use «Persona A», «Empresa B»… never a real name. A cheap
        # guard: no example quotes a seed alias of the corpus.
        from src import config
        reales = {O.norm(a) for a in config.SEED_ALIASES.values()}
        texto = O.norm(yaml.safe_dump(ONT.raw, allow_unicode=True))
        self.assertEqual([a for a in reales if a and a in texto], [])


class ContenidoTest(unittest.TestCase):
    """Doctrine fixed by the owner on 2026-10-07 (spec 069 §Decisiones)."""

    # Frozen copy of the pre-069 enums and synonym map (schema.py @ c45a207): the derived ones must
    # keep every name and value, and every old synonym must still land on the same type.
    LEGACY_ENTITIES = {"PERSON": "Person", "ORGANIZATION": "Organization", "COMPANY": "Company",
                       "COURT": "Court", "LEGAL_CASE": "LegalCase", "CRIME": "Crime",
                       "MONEY_AMOUNT": "MoneyAmount", "EVENT": "Event", "LOCATION": "Location",
                       "CONTRACT": "Contract"}
    LEGACY_SYNONYMS = {
        "TRABAJA_PARA": "WORKS_FOR", "MIEMBRO_DE": "MEMBER_OF", "ASOCIADO_CON": "ASSOCIATED_WITH",
        "SE_REUNIO_CON": "MET_WITH", "REUNION_CON": "MET_WITH", "PAGO": "PAID", "PAGO_A": "PAID",
        "RECIBIO": "RECEIVED", "ACUSADO_DE": "ACCUSED_OF", "CONDENADO_POR": "CONVICTED_OF",
        "INVESTIGADO_POR": "INVESTIGATED_BY", "PARTE_EN": "PARTY_TO", "POSEE": "OWNS", "CONTROLA": "CONTROLS",
        "ADMINISTRA": "ADMINISTRA", "ADMINISTRADOR_DE": "ADMINISTRA", "ADMINISTRA_DE": "ADMINISTRA",
        "RECIBIO_SUBVENCION": "RECEIVED_GRANT", "SUBVENCION": "RECEIVED_GRANT", "RECIBIO_AYUDA": "RECEIVED_GRANT",
        "CONTRATOS_MENORES": "MINOR_CONTRACTOR_OF", "ADJUDICATARIA_DE_MENORES": "MINOR_CONTRACTOR_OF",
        "CONTRATISTA_MENOR_DE": "MINOR_CONTRACTOR_OF", "NOMBRADO_PARA": "APPOINTED_TO",
        "NOMBRADO_EN": "APPOINTED_TO", "CARGO_EN": "APPOINTED_TO", "FILIAL_DE": "SUBSIDIARY_OF",
        "ADJUDICO": "AWARDED_CONTRACT", "ADJUDICA_A": "AWARDED_CONTRACT", "ADJUDICADO_A": "CONTRACT_AWARDED_TO",
        "OCURRIO_EN": "OCCURRED_AT", "PARTICIPO_EN": "PARTICIPATED_IN", "INVOLUCRA": "INVOLVES",
        "FAMILIAR_DE": "FAMILY_OF", "ESPOSA_DE": "FAMILY_OF", "ESPOSO_DE": "FAMILY_OF", "CONYUGE_DE": "FAMILY_OF",
        "PAREJA_DE": "FAMILY_OF", "HIJO_DE": "FAMILY_OF", "HIJA_DE": "FAMILY_OF", "HERMANO_DE": "FAMILY_OF",
        "HERMANA_DE": "FAMILY_OF", "PADRE_DE": "FAMILY_OF", "MADRE_DE": "FAMILY_OF"}

    def test_parity_with_legacy_enums(self):
        self.assertEqual({m.name: m.value for m in EntityType}, self.LEGACY_ENTITIES)
        self.assertEqual(len(RelationType), 24)
        for m in RelationType:
            self.assertEqual(m.name, m.value)
        self.assertEqual(RelationType.MET_WITH, "MET_WITH")  # str-enum semantics kept

    def test_synonyms_cover_legacy_map(self):
        for k, v in self.LEGACY_SYNONYMS.items():
            self.assertEqual(ONT.canon_relacion(k), v, k)

    def test_pydantic_coercion(self):
        from src.schema import ExtractedRelation
        r = ExtractedRelation(source="A", target="B", type="se reunió con")
        self.assertIs(r.type, RelationType.MET_WITH)
        with self.assertRaises(Exception):
            ExtractedRelation(source="A", target="B", type="COLLUDED_WITH")

    def test_symmetric_set(self):
        self.assertEqual(ONT.simetricas, {"FAMILY_OF", "ASSOCIATED_WITH", "MET_WITH"})

    def test_accusatory_set(self):
        self.assertEqual(ONT.acusatorias, {"ACCUSED_OF", "CONVICTED_OF", "INVESTIGATED_BY", "PAID", "RECEIVED"})

    def test_condenado_no_firme_is_a_term(self):
        self.assertIn("condenado_no_firme", ONT.estados)
        self.assertEqual(len(ONT.estados), 9)  # + condena_anulada (decisión 07-oct)
        self.assertLess(ONT.rango_estado["condenado_no_firme"], ONT.rango_estado["condenado"])

    def test_met_with_excludes_calls(self):
        f = ONT.ficha("tipos_relacion", "MET_WITH")
        self.assertTrue(any("llamada" in x for x in f["excluye"]))

    def test_associated_with_excludes_comention(self):
        f = ONT.ficha("tipos_relacion", "ASSOCIATED_WITH")
        self.assertTrue(any("co-mención" in x for x in f["excluye"]))
        self.assertFalse(any("co-mención" in x or "co-aparición" in x for x in f["incluye"]))

    def test_investigated_by_direction(self):
        f = ONT.ficha("tipos_relacion", "INVESTIGATED_BY")
        self.assertTrue(f["direccion"].startswith("persona o sociedad investigada →"))
        self.assertNotIn("Crime", f["objeto"])

    def test_status_phases_and_ladder(self):
        self.assertEqual(ONT.estados_cerrados, {"absuelto", "archivado"})
        self.assertEqual(ONT.estados_abiertos, {"investigado", "imputado", "procesado", "acusado", "condena_anulada"})
        self.assertEqual(ONT.estados_condena, {"condenado_no_firme", "condenado"})
        self.assertEqual(ONT.estados_prensa, {"investigado", "imputado"})
        self.assertEqual(ONT.escalera, ("investigado", "imputado", "procesado", "acusado",
                                        "condena_anulada", "condenado_no_firme", "condenado"))

    def test_layers_order(self):
        self.assertEqual(ONT.capas[:6], ("judicial", "contrato", "acusacion", "prensa", "ultima_hora", "contexto"))


class HelpersTest(unittest.TestCase):
    def test_canon_relacion(self):
        self.assertEqual(ONT.canon_relacion("se reunió con"), "MET_WITH")
        self.assertEqual(ONT.canon_relacion("hermana_de"), "FAMILY_OF")
        self.assertIsNone(ONT.canon_relacion("COLLUDED_WITH"))

    def test_validar_extraccion_rejects_loader_only(self):
        self.assertFalse(ONT.validar("tipos_relacion", "CONTRACTOR_OF", extraccion=True).ok)
        self.assertTrue(ONT.validar("tipos_relacion", "CONTRACTOR_OF").ok)

    def test_canon_delito_exact_not_substring(self):
        self.assertEqual(ONT.canon_delito("Cohecho Pasivo"), "cohecho pasivo")
        self.assertEqual(ONT.familia_delito["cohecho pasivo"], "cohecho")
        self.assertEqual(ONT.canon_delito("soborno"), "cohecho")
        self.assertEqual(ONT.canon_delito("delito fiscal"), "delito contra la Hacienda Pública")
        self.assertIsNone(ONT.canon_delito("cohecho y otras cosas"))
        r = ONT.validar("delitos", "piratería")
        self.assertFalse(r.ok)
        self.assertIn("sin forma canónica", r.motivo)

    def test_crimes_after_review(self):
        # Revisión Codex 07-oct: distinct crimes never merge; no penal name, no node.
        self.assertEqual(ONT.canon_delito("grupo criminal"), "grupo criminal")
        self.assertNotEqual(ONT.canon_delito("grupo criminal"), ONT.canon_delito("organización criminal"))
        r = ONT.validar("delitos", "Revelación de secretos")
        self.assertFalse(r.ok)
        self.assertIn("ambiguo", r.motivo)
        # …but the cited article resolves it (registro: «revelación de secretos», art. 417 CP).
        self.assertEqual(ONT.validar("delitos", "revelación de secretos", articulo="art. 417 CP").valor,
                         "revelación de datos reservados")
        self.assertEqual(ONT.canon_delito("revelación de secretos", "art. 197.3"), "descubrimiento y revelación de secretos")
        self.assertIsNone(ONT.canon_delito("revelación de secretos", "art. 300"))
        self.assertTrue(ONT.parece_delito("delito de cohecho"))
        self.assertFalse(ONT.parece_delito("la corrupción"))
        for coloquial in ("abuso de poder", "amaño", "contratación pública fraudulenta"):
            self.assertFalse(ONT.validar("delitos", coloquial).ok, coloquial)
        self.assertEqual(ONT.canon_delito("falsedad en documento mercantil"), "falsedad en documento mercantil")

    def test_validar_never_raises_on_bad_json(self):
        for sec in ("tipos_relacion", "estados_procesales", "delitos", "capas", "tipos_entidad", "etiquetas_defecto"):
            for v in ([], {}, 3, True):
                self.assertFalse(ONT.validar(sec, v).ok, (sec, v))
        self.assertFalse(ONT.validar("tipos_entidad", "MoneyAmount", extraccion=True).ok)
        self.assertTrue(ONT.validar("tipos_entidad", "MoneyAmount").ok)

    def test_extremos(self):
        self.assertTrue(ONT.extremos_ok("ACCUSED_OF", "Person", "Crime"))
        self.assertFalse(ONT.extremos_ok("ACCUSED_OF", "Organization", "Crime"))
        self.assertFalse(ONT.extremos_ok("INVESTIGATED_BY", "Court", "Person"))
        self.assertTrue(ONT.extremos_ok("INVOLVES", "LegalCase", "Crime"))
        self.assertFalse(ONT.extremos_ok("INVOLVES", "Event", "Crime"))
        self.assertTrue(ONT.extremos_ok("CONTRACTOR_OF", "Company", "Company"))

    def test_estados_and_capas(self):
        self.assertFalse(ONT.validar("estados_procesales", "encausado").ok)  # ambiguo: sin sinónimo
        self.assertEqual(ONT.validar("estados_procesales", "Condena no firme").valor, "condenado_no_firme")
        self.assertFalse(ONT.validar("estados_procesales", "sospechoso").ok)
        self.assertEqual(ONT.canon_capa(None), "judicial")
        self.assertIsNone(ONT.canon_capa("wikipedia"))

    def test_ante_duda(self):
        self.assertEqual(ONT.ante_duda("ACCUSED_OF"), "revision")
        self.assertEqual(ONT.ante_duda("PAID"), "revision")
        self.assertEqual(ONT.ante_duda("acusado"), "revision")
        self.assertEqual(ONT.ante_duda("WORKS_FOR"), "mantener")
        # T1.6 (07-oct): no acusatorias, pero a revisión ante la duda.
        self.assertEqual(ONT.ante_duda("CONTROLS"), "revision")
        self.assertEqual(ONT.ante_duda("FAMILY_OF"), "revision")
        self.assertEqual(ONT.ante_duda("ASSOCIATED_WITH"), "mantener")
        self.assertEqual(ONT.ante_duda("INVENTADA"), "revision")

    def test_parse_bool_strict(self):
        self.assertIs(O.parse_bool("false"), False)
        self.assertIs(O.parse_bool("Sí"), True)
        self.assertIs(O.parse_bool(False), False)
        for v in ("quizá", "", None, 1):
            with self.assertRaises(ValueError):
                O.parse_bool(v)

    def test_fichas_prompt(self):
        txt = ONT.fichas_prompt("tipos_relacion", ["MET_WITH"])
        self.assertTrue(txt.startswith("- MET_WITH (A ↔ B): "))
        self.assertIn("Excluye:", txt)
        self.assertIn("No:", txt)


if __name__ == "__main__":
    unittest.main()


class PromptExtraccionTest(unittest.TestCase):
    """T3.1 — the extraction prompt carries the cards, not a bare list of names."""

    def test_prompt_inserts_every_extractable_card(self):
        from src.extract_facts import SYSTEM_PROMPT
        for t in ONT.tipos_extraibles:
            self.assertIn(f"- {t} (", SYSTEM_PROMPT)
            self.assertIn(ONT.ficha("tipos_relacion", t)["definicion"], SYSTEM_PROMPT)
        for t in set(ONT.tipos_relacion) - set(ONT.tipos_extraibles):
            self.assertNotIn(t, SYSTEM_PROMPT)
        self.assertIn(f"versión {ONT.version}", SYSTEM_PROMPT)
        self.assertNotIn("MoneyAmount:", SYSTEM_PROMPT)
        self.assertNotIn("u otro delito", SYSTEM_PROMPT)

    def test_swarm_dump_reuses_the_same_prompt(self):
        import inspect
        from scripts import _ingest_dump
        self.assertIn("SYSTEM_PROMPT", inspect.getsource(_ingest_dump))


class BriefTest(unittest.TestCase):
    """T3.2 — BRIEF-063 takes its vocabulary from the ontology."""

    def test_markers_render(self):
        from src.qa_esc import render_brief
        out = render_brief("orden: <!-- ontologia:orden-capas -->.\n<!-- ontologia:estados_procesales -->\n")
        self.assertIn("`judicial` > `contrato` > `acusacion` > `prensa` > `ultima_hora` > `contexto`.", out)
        self.assertIn("- condenado_no_firme: ", out)
        self.assertNotIn("<!--", out)
        with self.assertRaises(ValueError):
            render_brief("<!-- ontologia:inventada -->")

    def test_real_brief_has_no_raw_vocabulary_left(self):
        from pathlib import Path
        from src import qa_esc
        repo = Path(__file__).resolve().parents[2]
        if not (repo / qa_esc.SPEC_DIR / qa_esc.BRIEF).exists():
            self.skipTest("specs/ (vault) no está enlazado en este checkout")
        text = qa_esc.brief_text(repo)
        self.assertNotIn("<!-- ontologia:", text)
        self.assertIn("- MET_WITH (A ↔ B)", text)
        self.assertNotIn("son\n     `estado: condenado`", text)
        self.assertIn("`condenado_no_firme`", text)


class StatusWritesTest(unittest.TestCase):
    """T4.6 — every procedural status an audit writes is ontology vocabulary."""

    def test_set_prop_cannot_touch_status(self):
        from src import qa_esc_schema as S
        self.assertNotIn("status", S.SET_PROPS)
        self.assertEqual(set(S.STATUSES), set(ONT.estados))

    def test_relations_set_status_is_validated(self):
        from src.relations_loader import _prepare_set
        from src.seudonimos import ErrorDeForma
        with self.assertRaises(ErrorDeForma) as fin:   # spec 070 (AC144): para el loader, no se descarta
            _prepare_set({"status": "sospechoso"}, "t")
        self.assertEqual(fin.exception.motivos, ["t: corrección con un `set` cuyo estado no es de la ontología"])
        self.assertEqual(_prepare_set({"status": "Acusado"}, "t")["status"], "acusado")
