"""The MCP vault tools never serve what the graph does not serve today (the vault is an older export): neither the note of an
unserved entity nor the lines of other notes that link to it or name it. And they only serve the editorial folders (briefings and
pattern notes): the exported entity notes and timelines are an old copy of the graph that free-text filtering cannot keep clean."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import briefing_store, cases, graph_queries, mcp_server


def _note(entity_id, body):
    return f"---\nentity_id: {entity_id}\ntype: Person\n---\n# nota\n{body}\n"


class VaultEntityNotesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for d in ("People", "Organizations", "_Briefings", "Timeline"):
            (root / d).mkdir()
        (root / "People" / "Zz Visible.md").write_text(_note("Person:zz-visible", "zzpalabra testigo"), encoding="utf-8")
        (root / "People" / "Zz Oculta Perez.md").write_text(_note("Person:zz-oculta", "zzpalabra testigo"), encoding="utf-8")
        (root / "Organizations" / "Zz Org.md").write_text(
            "---\nentity_id: Organization:zz-org\n---\n## Conexiones\n- WORKS_FOR ← [[Zz Oculta Perez]] zzconexion\n"
            "- WORKS_FOR ← [[Zz Visible]] zzconexion\n", encoding="utf-8")
        (root / "Timeline" / "2020.md").write_text(
            "- **2020-01** — [[Zz Oculta Perez|la señora]] MET_WITH [[Zz Visible]] zzhecho\n  > cita de ese hecho\n"
            "- **2020-02** — [[Zz Visible]] zzhecho\n", encoding="utf-8")
        (root / "_Briefings" / "Zz Caso.md").write_text(
            "# Caso\nzzpalabra testigo editorial\nSegún el sumario, Zz Oculta Pérez acudió zzprosa.\nOtra línea zzprosa.\n", encoding="utf-8")
        mcp_server._ocultos_cache = None
        (root / "People" / "Zz Antigua Lopez.md").write_text(_note("Person:zz-id-viejo", "zzpalabra testigo"), encoding="utf-8")
        (root / "_Briefings" / "Zz Otro.md").write_text("# Otro\nZz Antigua López declaró zzotro.\n", encoding="utf-8")
        ids = lambda keep: (lambda got: {i for i in got if keep(i)})  # noqa: E731
        patches = (mock.patch.object(briefing_store, "VAULT_DIR", root),
                   mock.patch.object(graph_queries, "entities_with_public_note", side_effect=self._visible),
                   mock.patch.object(graph_queries, "unserved_person_names", side_effect=self._del_grafo),
                   mock.patch.object(graph_queries, "existing_entity_ids", side_effect=ids(lambda i: i != "Person:zz-id-viejo")),
                   mock.patch.object(graph_queries, "served_entity_ids",
                                     side_effect=ids(lambda i: i not in ("Person:zz-id-viejo", "Person:zz-oculta"))))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.fail_graph = False
        self.graph_names = []

    def _del_grafo(self):
        if self.fail_graph:
            raise RuntimeError("sin grafo")
        return list(self.graph_names)

    def _visible(self, ids):
        if self.fail_graph:
            raise RuntimeError("sin grafo")
        return {i for i in ids if i not in ("Person:zz-oculta", "Person:zz-id-viejo")}

    def test_frontmatter_entity_id(self):
        self.assertEqual(briefing_store.note_entity_id("People/Zz Oculta Perez.md"), "Person:zz-oculta")
        self.assertIsNone(briefing_store.note_entity_id("_Briefings/Zz Caso.md"))
        self.assertIsNone(briefing_store.note_entity_id("../fuera.md"))

    def test_search_drops_the_note_of_an_unserved_entity(self):
        out = mcp_server.buscar_vault("zzpalabra testigo")
        self.assertIn("_Briefings/Zz Caso.md", out)
        self.assertNotIn("Oculta", out)
        self.assertNotIn("People/", out)

    def test_search_by_the_hidden_name_finds_nothing(self):
        self.assertEqual(mcp_server.buscar_vault("Zz Oculta Perez"), "Sin notas que coincidan.")

    def test_other_notes_are_served_without_the_lines_that_link_to_or_name_it(self):
        (briefing_store.VAULT_DIR / "Patrones").mkdir()
        (briefing_store.VAULT_DIR / "Patrones" / "Zz Patron.md").write_text(
            "- **2020-01** — [[Zz Oculta Perez|la señora]] MET_WITH [[Zz Visible]] zzhecho\n  > cita de ese hecho\n"
            "- **2020-02** — [[Zz Visible]] zzhecho\n", encoding="utf-8")
        year = mcp_server.leer_nota("Patrones/Zz Patron.md")
        self.assertIn("2020-02", year)
        self.assertNotIn("2020-01", year)
        self.assertNotIn("cita de ese hecho", year)
        brief = mcp_server.leer_nota("_Briefings/Zz Caso.md")
        self.assertIn("Otra línea zzprosa.", brief)
        self.assertNotIn("Oculta", brief)
        self.assertNotIn("Oculta", mcp_server.buscar_vault("zzconexion"))
        self.assertNotIn("Oculta", mcp_server.buscar_vault("zzprosa"))

    def test_a_note_whose_id_no_longer_exists_is_not_served_but_its_name_is_not_scrubbed(self):
        """A stale id is usually a merge or a rename: the old ficha is not served, and the texts that name the entity stay."""
        self.assertNotIn("Antigua", mcp_server.buscar_vault("zzpalabra testigo"))
        self.assertNotIn("zzpalabra", mcp_server.leer_nota("People/Zz Antigua Lopez.md"))
        self.assertIn("Zz Antigua López declaró zzotro.", mcp_server.leer_nota("_Briefings/Zz Otro.md"))

    def test_the_names_to_withdraw_come_from_the_graph_even_with_no_entity_note_in_the_vault(self):
        import shutil
        root = briefing_store.VAULT_DIR
        for d in ("People", "Organizations", "Timeline"):
            shutil.rmtree(root / d)
        (root / "_Briefings" / "Zz Grafo.md").write_text(
            "# Grafo\nSegún el sumario, Zz Sinficha Ruiz acudió zzgrafo.\nEl señor Zzaliasgrafo Gil también zzgrafo.\nLimpia zzgrafo.\n",
            encoding="utf-8")
        mcp_server._ocultos_cache = None
        self.assertIn("Sinficha", mcp_server.leer_nota("_Briefings/Zz Grafo.md"))
        self.graph_names = ["Zz Sinficha Ruiz", "Zzaliasgrafo Gil"]
        mcp_server._ocultos_cache = None
        note = mcp_server.leer_nota("_Briefings/Zz Grafo.md")
        self.assertNotIn("Sinficha", note)
        self.assertNotIn("Zzaliasgrafo", note)
        self.assertIn("Limpia zzgrafo.", note)
        self.assertEqual(mcp_server.buscar_vault("Zz Sinficha Ruiz"), "Sin notas que coincidan.")
        # y sin ninguna ficha en el vault, sin grafo tampoco se sirve nada
        self.fail_graph = True
        mcp_server._ocultos_cache = None
        self.assertEqual(mcp_server.leer_nota("_Briefings/Zz Grafo.md"), mcp_server._VAULT_SIN_GRAFO)
        self.assertEqual(mcp_server.buscar_vault("zzgrafo"), mcp_server._VAULT_SIN_GRAFO)

    def test_only_the_editorial_folders_are_served(self):
        """Entity notes, timelines and anything else exported from the graph are neither found nor readable, served entity or not;
        a link from an editorial folder to one of them does not open it either."""
        root = briefing_store.VAULT_DIR
        (root / "_Briefings" / "Zz Enlace.md").symlink_to(root / "People" / "Zz Visible.md")
        (root / "Zz Raiz.md").write_text("zzsolo raiz\n", encoding="utf-8")
        for ruta in ("People/Zz Visible.md", "Organizations/Zz Org.md", "Timeline/2020.md", "Zz Raiz.md", "_Briefings/Zz Enlace.md",
                     "_Briefings/../People/Zz Visible.md"):
            with self.subTest(ruta=ruta):
                self.assertTrue(mcp_server.leer_nota(ruta).startswith("No existe la nota"))
        for consulta in ("zzconexion", "zzhecho", "zzsolo raiz", "Zz Visible"):
            with self.subTest(consulta=consulta):
                self.assertEqual(mcp_server.buscar_vault(consulta), "Sin notas que coincidan.")
        out = mcp_server.buscar_vault("zzpalabra testigo")
        self.assertNotIn("Enlace", out)
        self.assertIn("_Briefings/Zz Caso.md", out)

    def test_a_link_never_lends_another_folder_to_an_editorial_note_and_every_refusal_reads_the_same(self):
        root = briefing_store.VAULT_DIR
        otro = tempfile.TemporaryDirectory()
        self.addCleanup(otro.cleanup)
        fuera = Path(otro.name) / "zz-fuera-del-vault.md"
        fuera.write_text("zzfuera\n", encoding="utf-8")
        (root / "People" / "Zz Enlace Inverso.md").symlink_to(root / "_Briefings" / "Zz Caso.md")
        (root / "_Briefings" / "Zz Afuera.md").symlink_to(fuera)
        (root / "People" / "Zz Afuera.md").symlink_to(fuera)
        (root / "_Briefings" / "Zz Texto.txt").write_text("zzpalabra testigo\n", encoding="utf-8")
        ausente = mcp_server.leer_nota("People/Zz Nadie.md")
        # A `..` through another folder reads the same whether the component is a private link, a real folder or missing.
        (root / "People" / "zzdir").symlink_to(otro.name)
        for ruta in ("People/zzdir/../../_Briefings/Zz Caso.md", "People/zznada/../../_Briefings/Zz Caso.md",
                     "People/../_Briefings/Zz Caso.md", "./_Briefings/Zz Caso.md", "_Briefings//Zz Caso.md"):
            with self.subTest(ruta=ruta):
                self.assertEqual(mcp_server.leer_nota(ruta), ausente.replace("People/Zz Nadie.md", ruta))
        for ruta in ("People/Zz Enlace Inverso.md", "_Briefings/Zz Afuera.md", "People/Zz Afuera.md", "_Briefings/Zz Texto.txt",
                     f"../{Path(otro.name).name}/zz-fuera-del-vault.md", str(fuera), ".obsidian/x.md"):
            with self.subTest(ruta=ruta):
                self.assertEqual(mcp_server.leer_nota(ruta), ausente.replace("People/Zz Nadie.md", ruta))
        out = mcp_server.buscar_vault("zzpalabra testigo")
        self.assertNotIn("People/", out)
        self.assertNotIn("Afuera", out)
        self.assertEqual(mcp_server.buscar_vault("zzfuera"), "Sin notas que coincidan.")

    def test_search_and_read_share_one_rule_for_what_a_note_is(self):
        root = briefing_store.VAULT_DIR
        (root / "_Briefings" / "Zz Caso.md.bak").write_text("zzrespaldo secreto\n", encoding="utf-8")
        (root / "_Briefings" / "Zz Copia.md").symlink_to(root / "_Briefings" / "Zz Caso.md.bak")
        (root / "_Briefings" / "._Zz Recurso.md").write_text("zzrespaldo recurso\n", encoding="utf-8")
        self.assertEqual(mcp_server.buscar_vault("zzrespaldo"), "Sin notas que coincidan.")
        for ruta in ("_Briefings/Zz Copia.md", "_Briefings/Zz Caso.md.bak", "_Briefings/._Zz Recurso.md", "_Briefings/Zz\x00.md"):
            with self.subTest(ruta=ruta):
                self.assertTrue(mcp_server.leer_nota(ruta).startswith("No existe la nota"))

    def test_an_uppercase_extension_is_not_a_note_and_a_failed_read_is_a_missing_note(self):
        root = briefing_store.VAULT_DIR
        (root / "_Briefings" / "Zz Mayus.MD").write_text(_note("Person:zz-oculta", "zzmayus"), encoding="utf-8")
        self.assertTrue(mcp_server.leer_nota("_Briefings/Zz Mayus.MD").startswith("No existe la nota"))
        self.assertEqual(mcp_server.buscar_vault("zzmayus"), "Sin notas que coincidan.")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("/ruta/secreta")):
            out = mcp_server.leer_nota("_Briefings/Zz Caso.md")
        self.assertTrue(out.startswith("No existe la nota"))
        self.assertNotIn("secreta", out)

    def test_read_and_search_read_the_file_the_same_way(self):
        root = briefing_store.VAULT_DIR
        (root / "_Briefings" / "Zz Roto.md").write_bytes(b"zzroto \xff\xfe texto\n")
        relleno = "ñ" * 2500   # el id queda tras el byte 4096 y dentro de los primeros 4096 caracteres
        (root / "_Briefings" / "Zz Larga.md").write_text(
            f"---\nnota: {relleno}\nentity_id: Person:zz-oculta\n---\n# nota\nzzlarga\n", encoding="utf-8")
        mcp_server._ocultos_cache = None
        for consulta, ruta in (("zzroto", "_Briefings/Zz Roto.md"), ("zzlarga", "_Briefings/Zz Larga.md")):
            with self.subTest(ruta=ruta):
                self.assertEqual(mcp_server.buscar_vault(consulta), "Sin notas que coincidan.")
                self.assertTrue(mcp_server.leer_nota(ruta).startswith("No existe la nota"))

    def test_an_unresolvable_entity_note_closes_the_vault_without_naming_it(self):
        root = briefing_store.VAULT_DIR
        (root / "People" / "Zz Bucle Secreto.md").symlink_to(root / "People" / "Zz Bucle Secreto.md")
        mcp_server._ocultos_cache = None
        for out in (mcp_server.leer_nota("_Briefings/Zz Caso.md"), mcp_server.buscar_vault("zzpalabra testigo")):
            self.assertNotIn("Bucle", out)

    def test_a_relative_vault_dir_serves_the_same(self):
        import os
        root = briefing_store.VAULT_DIR
        here = os.getcwd()
        os.chdir(root.parent)
        self.addCleanup(os.chdir, here)
        with mock.patch.object(briefing_store, "VAULT_DIR", Path(root.name)):
            mcp_server._ocultos_cache = None
            self.assertIn("_Briefings/Zz Caso.md", mcp_server.buscar_vault("zzpalabra testigo"))
            self.assertIn("zzpalabra", mcp_server.leer_nota("_Briefings/Zz Caso.md"))
            self.assertTrue(mcp_server.leer_nota("People/Zz Visible.md").startswith("No existe la nota"))

    def test_an_entity_note_inside_an_editorial_folder_follows_the_graph(self):
        """The hidden-entity rule is still what decides inside the served folders."""
        root = briefing_store.VAULT_DIR
        (root / "_Briefings" / "Zz Ficha Oculta.md").write_text(_note("Person:zz-oculta", "zzdentro"), encoding="utf-8")
        (root / "_Briefings" / "Zz Ficha Visible.md").write_text(_note("Person:zz-visible", "zzdentro"), encoding="utf-8")
        mcp_server._ocultos_cache = None
        out = mcp_server.buscar_vault("zzdentro")
        self.assertIn("Zz Ficha Visible.md", out)
        self.assertNotIn("Ficha Oculta", out)
        self.assertTrue(mcp_server.leer_nota("_Briefings/Zz Ficha Oculta.md").startswith("No existe la nota"))
        self.assertIn("zzdentro", mcp_server.leer_nota("_Briefings/Zz Ficha Visible.md"))

    def test_a_vault_with_no_editorial_note_counts_as_not_deployed(self):
        for p in (briefing_store.VAULT_DIR / "_Briefings").iterdir():
            p.unlink()
        self.assertFalse(briefing_store.vault_available())
        self.assertIn("no está disponible", mcp_server.buscar_vault("zzpalabra"))

    def test_a_short_hidden_name_is_dropped_when_it_stands_alone(self):
        root = briefing_store.VAULT_DIR
        (root / "People" / "Zzromo.md").write_text(_note("Person:zz-oculta", "x"), encoding="utf-8")
        (root / "_Briefings" / "Zz Auto.md").write_text(
            "# Auto\nSegún el auto, don Zzromo aprobó zzauto.\nDeclaró Zzromo Pérez, otra persona, zzauto.\n\n"
            "> cita suelta con Zzromo, zzauto\n> — doc p.3\n\nLínea limpia zzauto.\n", encoding="utf-8")
        mcp_server._ocultos_cache = None
        auto = mcp_server.leer_nota("_Briefings/Zz Auto.md")
        self.assertNotIn("don Zzromo", auto)
        self.assertNotIn("cita suelta", auto)
        self.assertNotIn("doc p.3", auto)
        self.assertIn("Zzromo Pérez, otra persona", auto)
        self.assertIn("Línea limpia zzauto.", auto)
        self.assertNotIn("don Zzromo", mcp_server.buscar_vault("zzauto aprobó"))
        self.assertEqual(mcp_server.buscar_vault("Zzromo aprobó"), "Sin notas que coincidan.")

    def test_read_answers_as_if_the_note_did_not_exist(self):
        self.assertIn("zzpalabra", mcp_server.leer_nota("_Briefings/Zz Caso.md"))
        gone = mcp_server.leer_nota("People/Zz Oculta Perez.md")
        self.assertEqual(gone, mcp_server.leer_nota("People/Zz Nadie Perez.md").replace("Nadie", "Oculta"))
        self.assertNotIn("zzpalabra", gone)

    def test_without_the_graph_the_vault_serves_nothing_and_nothing_is_cached(self):
        self.fail_graph = True
        for out in (mcp_server.buscar_vault("zzpalabra testigo"), mcp_server.buscar_vault("Zz Oculta Perez"),
                    mcp_server.leer_nota("_Briefings/Zz Caso.md"), mcp_server.leer_nota("People/Zz Visible.md")):
            self.assertEqual(out, mcp_server._VAULT_SIN_GRAFO)
        self.fail_graph = False
        self.assertIn("zzpalabra", mcp_server.leer_nota("_Briefings/Zz Caso.md"))

    def test_a_withdrawn_entity_is_not_named_by_an_alias_either(self):
        root = briefing_store.VAULT_DIR
        (root / "People" / "Zz Oculta Perez.md").write_text(
            "---\nentity_id: Person:zz-oculta\naliases:\n- Zzalias\n- Zz Alias Largo\ndegree: 1\n---\n# nota\n", encoding="utf-8")
        (root / "_Briefings" / "Zz Alias.md").write_text(
            "# Alias\n- hecho uno zzal\n  > «el señor Zzalias lo aprobó»\n- hecho dos zzal\nSegún Zz Alias Largo, zzal.\nLimpia zzal.\n",
            encoding="utf-8")
        mcp_server._ocultos_cache = None
        note = mcp_server.leer_nota("_Briefings/Zz Alias.md")
        self.assertNotIn("hecho uno", note)
        self.assertNotIn("Zzalias", note)
        self.assertNotIn("Alias Largo", note)
        self.assertIn("hecho dos zzal", note)
        self.assertIn("Limpia zzal.", note)
        self.assertEqual(mcp_server.buscar_vault("Zzalias aprobó"), "Sin notas que coincidan.")
        self.assertNotIn("Zzalias", mcp_server.buscar_vault("zzal"))

    def test_a_single_word_name_never_swallows_its_homonyms_whatever_its_length(self):
        root = briefing_store.VAULT_DIR
        (root / "People" / "Zzmercedes.md").write_text(_note("Person:zz-oculta", "x"), encoding="utf-8")
        (root / "People" / "Zzmercedes Vaquera.md").write_text(_note("Person:zz-otra", "zzhom ficha de otra persona"), encoding="utf-8")
        (root / "_Briefings" / "Zz Hom.md").write_text("# Hom\nZzmercedes Vaquera declaró zzhom.\nDoña Zzmercedes, sola, zzhom.\n",
                                                        encoding="utf-8")
        mcp_server._ocultos_cache = None
        note = mcp_server.leer_nota("_Briefings/Zz Hom.md")
        self.assertIn("Zzmercedes Vaquera declaró zzhom.", note)
        self.assertNotIn("sola", note)

    def test_the_snippet_never_reaches_into_a_withdrawn_neighbour_block(self):
        (briefing_store.VAULT_DIR / "_Briefings" / "Zz Vecino.md").write_text(
            "# Vecino\nUna línea limpia con zzmarcador.\nZz Oculta Pérez hizo algo con cita «zzsecreto».\n", encoding="utf-8")
        out = mcp_server.buscar_vault("zzmarcador")
        self.assertIn("zzmarcador", out)
        self.assertNotIn("Oculta", out)
        self.assertNotIn("zzsecreto", out)


class CaseCastTest(unittest.TestCase):
    def test_the_case_only_counts_and_lists_the_served_cast(self):
        fake = [{"slug": "zz", "titulo": "Zz", "dek": "d", "estado": "", "estado_kind": "k", "entidades": [
            {"id": "Person:zz-oculta"}, {"id": "Person:zz-visible"}], "documentos": [], "ultima_hora_tags": [], "relacionados": []}]
        row = {"id": "Person:zz-visible", "degree": 1, "charges": []}
        with mock.patch.object(cases, "load_cases", return_value=fake), \
             mock.patch.object(graph_queries, "served_entity_ids", return_value={"Person:zz-visible"}), \
             mock.patch.object(graph_queries, "entities_with_charges", return_value=[dict(row)]), \
             mock.patch.object(graph_queries, "indexable_entities", return_value=[]), \
             mock.patch.object(graph_queries, "timeline", return_value=[]), \
             mock.patch.object(graph_queries, "money_flows", return_value=[]):
            self.assertEqual(cases.case_index()[0]["nImplicados"], 1)
            payload = cases.case_payload("zz")
        self.assertEqual(payload["entidadesIds"], ["Person:zz-visible"])
        self.assertEqual(payload["meta"]["nImplicados"], 1)


if __name__ == "__main__":
    unittest.main()
