"""Semantic PII gate (spec 067): one unit per entry, schema 2 identity, the path and OUT guards, the selection
policy, reviewed units keyed by sha, exit codes and the config. No Jev calls: the predictions are synthetic, the
units talk about invented people and places, and the guards run against throwaway git repositories."""
import contextlib
import io
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from pipeline.scripts import pii_semantic_export as export
from pipeline.scripts import pii_semantic_select as gate

QUESTIONS = export.REPO / "pipeline" / "scripts" / "pii_semantic_questions.json"
# The maintainer's reviewed list lives outside the public repo, reached through the gitignored `specs/` link.
REVISADOS = export.REPO / "specs" / "067-pii-semantica-jev" / "revisados.yaml"
GIT_ENV = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
UNITS = {
    "casa.yaml": "- nombre: Persona Inventada\n  nota: vive en una casa junto al río Ficticio\n",
    "hermana.yaml": "- nombre: Persona Inventada\n  nota: su hermana Figurante Imaginaria lleva una panadería\n",
    "seudonimo.yaml": "- seudonimo: Ramiro\n  nota: el auto lo nombra así y lo sitúa en la consejería de Villaficticia\n",
    "cuenta.yaml": "- nombre: Banco Inventado\n  nota: aparece la cuenta de una clienta, Figurante Imaginaria\n",
    "cargo.yaml": "- nombre: Cargo Inventado\n  rol: alcalde de Villaficticia\n",
}
# file -> (class, confidence, exceso, probabilities): one selected, two gray (unsure grave class; grave runner-up),
# one selected but reviewed, one clean.
ANSWERS = {
    "casa.yaml": ("domicilio_particular", 0.5, 0.3, {"domicilio_particular": 0.55, "ninguno": 0.45}),
    "hermana.yaml": ("familiar_o_menor", 0.8, 0.4, {"familiar_o_menor": 0.85, "ninguno": 0.15}),
    "seudonimo.yaml": ("reidentifica_seudonimo", 0.7, 0.5, {"reidentifica_seudonimo": 0.75, "ninguno": 0.25}),
    "cuenta.yaml": ("ninguno", 0.51, 0.48, {"ninguno": 0.57, "identificador": 0.27, "bien_personal": 0.16}),
    "cargo.yaml": ("ninguno", 0.95, 0.1, {"ninguno": 0.96, "familiar_o_menor": 0.04}),
}
REVIEWABLE = ("casa.yaml", "hermana.yaml", "seudonimo.yaml", "cuenta.yaml")


def answers(choice, conf, exceso, probabilities):
    return {"answers": {"clase": {"type": "choice", "choice": choice, "probabilities": probabilities,
                                  "confidence": conf},
                        "exceso": {"type": "noul", "noul": exceso}}}


def quiet(fn, *args):
    """Run fn(*args) and return (exit code, stdout + stderr): what it returns, or the code it exits with."""
    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(out):
        try:
            code = fn(*args)
        except SystemExit as exit_:
            code = exit_.code
    return code or 0, out.getvalue()


def git(repo: Path, *args: str) -> str:
    config = ("-c", "user.name=Prueba", "-c", "user.email=prueba@example.invalid", "-c", "commit.gpgsign=false",
              "-c", "core.hooksPath=/dev/null")
    return subprocess.run(["git", "-C", str(repo), *config, *args], check=True, capture_output=True, text=True,
                          env=GIT_ENV).stdout


class RepoTestCase(unittest.TestCase):
    """A throwaway git repository stands in for the checkout (export.REPO); OUT is a private folder outside it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()  # macOS: /var is /private/var, and the guards compare resolved paths
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        patch = mock.patch.object(export, "REPO", self.repo)
        patch.start()
        self.addCleanup(patch.stop)
        self.out = self.tmp / "privado" / "run"

    def track(self, rel: str, text: str) -> str:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        git(self.repo, "add", "--", rel)
        return rel

    def export(self, *rels: str) -> dict[str, dict]:
        code, output = quiet(export.main, ["--out", str(self.out), *rels])
        self.assertEqual(code, 0, output)
        rows = [json.loads(line) for line in (self.out / "items.jsonl").read_text(encoding="utf-8").splitlines()]
        return {row["id"]: row for row in rows}


class PolicyTest(unittest.TestCase):
    def test_thresholds(self):
        cases = [
            (("familiar_o_menor", 0.6, 0.1, {"familiar_o_menor": 0.6, "ninguno": 0.4}), "seleccionada"),
            # Not a grave class: no gray band.
            (("familiar_o_menor", 0.59, 0.1, {"familiar_o_menor": 0.59, "ninguno": 0.41}), None),
            (("bien_personal", 0.5, 0.1, {"bien_personal": 0.5, "ninguno": 0.5}), None),
            (("dato_sensible", 0.55, 0.1, {"dato_sensible": 0.55, "ninguno": 0.45}), None),
            (("ninguno", 0.9, 0.6, {"ninguno": 0.9, "familiar_o_menor": 0.1}), "seleccionada"),  # exceso alone
            (("sede_oficial_o_empresa", 0.95, 0.59, {"sede_oficial_o_empresa": 0.96, "ninguno": 0.04}), None),
            (("domicilio_particular", 0.6, 0.1, {"domicilio_particular": 0.6, "ninguno": 0.4}), "seleccionada"),
            (("domicilio_particular", 0.59, 0.1, {"domicilio_particular": 0.59, "ninguno": 0.41}), "gris"),
            (("domicilio_particular", 0.4, 0.1, {"domicilio_particular": 0.4, "ninguno": 0.6}), "gris"),
            (("domicilio_particular", 0.39, 0.1, {"domicilio_particular": 0.24, "ninguno": 0.76}), None),
            (("domicilio_particular", 0.5, 0.7, {"domicilio_particular": 0.5, "ninguno": 0.5}), "seleccionada"),
            (("identificador", 0.5, 0.1, {"identificador": 0.5, "ninguno": 0.5}), "gris"),
            (("reidentifica_seudonimo", 0.45, 0.1, {"reidentifica_seudonimo": 0.45, "ninguno": 0.55}), "gris"),
            # A grave class that does not win still counts from 0.25 (the identifier the pilot missed).
            (("ninguno", 0.51, 0.48, {"ninguno": 0.57, "identificador": 0.27, "bien_personal": 0.16}), "gris"),
            (("ninguno", 0.7, 0.1, {"ninguno": 0.75, "reidentifica_seudonimo": 0.25}), "gris"),
            (("familiar_o_menor", 0.5, 0.1, {"familiar_o_menor": 0.5, "domicilio_particular": 0.3}), "gris"),
            (("ninguno", 0.7, 0.1, {"ninguno": 0.76, "domicilio_particular": 0.24}), None),
            (("ninguno", 0.6, 0.1, {"ninguno": 0.6, "familiar_o_menor": 0.4}), None),  # non-grave runner-up
        ]
        for (choice, conf, exceso, probabilities), want in cases:
            with self.subTest(choice=choice, conf=conf, exceso=exceso, probabilities=probabilities):
                self.assertEqual(gate.verdict(answers(choice, conf, exceso, probabilities)), want)


class ShaTest(unittest.TestCase):
    def test_the_sha_is_of_the_exact_text(self):
        text = "- nombre: Persona Inventada\n  nota: vive junto al río Ficticio\n"
        self.assertRegex(export.unit_sha(text), r"^[0-9a-f]{16}$")
        for changed in (text.replace("  nota", "    nota"), text.replace("Ficticio", "Imaginario"),
                        text + "  otra: nota\n"):
            with self.subTest(changed=changed):
                self.assertNotEqual(export.unit_sha(text), export.unit_sha(changed))


class UnitTest(RepoTestCase):
    def test_reindenting_changes_the_sha(self):
        # Codex's case: the same words, but the address moves from the body to the home of its representative.
        organismo = ("organismos:\n"
                     "  - id: Organization:organismo-inventado\n"
                     "    representante:\n"
                     "      name: Persona Inventada\n"
                     "    domicilio: la casa junto al río Ficticio\n")
        representante = organismo.replace("    domicilio", "      domicilio")
        self.assertEqual(organismo.split(), representante.split())  # schema 1 hashed these words: same sha
        self.assertIn("domicilio", yaml.safe_load(representante)["organismos"][0]["representante"])
        rel = "pipeline/data/boe/organismos.yaml"
        shas = []
        for text in (organismo, representante):
            self.track(rel, text)
            shas.append(self.export(rel)[f"{rel}#organismos[id=Organization:organismo-inventado]"]["meta"]["sha"])
        self.assertNotEqual(*shas)

    def test_a_new_entry_leaves_the_others_alone(self):
        text = ("# Cabecera de prueba\n"
                "\n"
                "entities:\n"
                "  - entity_id: Person:persona-uno\n"
                "    role: alcaldesa de Villaficticia\n"
                "\n"
                "  # La segunda, con su comentario\n"
                "  - entity_id: Person:persona-dos\n"
                "    role: concejal de Villaficticia\n"
                "relations:\n"
                "  - source: Person:persona-uno\n"
                "    target: Person:persona-dos\n"
                "    type: ASSOCIATED_WITH\n")
        rel = self.track("pipeline/data/context/prueba.yaml", text)
        before = self.export(rel)
        self.track(rel, text.replace("entities:\n", "entities:\n  - entity_id: Person:persona-nueva\n"
                                                    "    role: tesorera de Villaficticia\n\n"))
        after = self.export(rel)
        self.assertEqual(set(after) - set(before), {f"{rel}#entities[entity_id=Person:persona-nueva]"})
        for unit, item in before.items():
            self.assertEqual(after[unit]["meta"]["sha"], item["meta"]["sha"], unit)
        segunda = after[f"{rel}#entities[entity_id=Person:persona-dos]"]
        self.assertEqual(segunda["state"]["texto"], "  # La segunda, con su comentario\n"
                                                    "  - entity_id: Person:persona-dos\n"
                                                    "    role: concejal de Villaficticia\n")
        self.assertEqual(segunda["meta"]["l1"], before[segunda["id"]]["meta"]["l1"] + 3)
        lines = (self.repo / rel).read_text(encoding="utf-8").splitlines(keepends=True)
        for item in after.values():
            self.assertEqual("".join(lines[item["meta"]["l1"] - 1:item["meta"]["l2"]]), item["state"]["texto"])
            self.assertEqual((item["meta"]["sha"], item["meta"]["schema"]), (export.unit_sha(item["state"]["texto"]), 2))

    def test_a_long_unit_goes_out_in_overlapping_subwindows(self):
        notas = "".join(f"      - nota número {i}\n" for i in range(1, 100))
        rel = self.track("pipeline/data/context/larga.yaml",
                         "summaries:\n  - entity_id: Person:persona-larga\n    notas:\n" + notas)
        unit = f"{rel}#summaries[entity_id=Person:persona-larga]"
        parts = [item for item in self.export(rel).values() if item["meta"]["unidad"] == unit]
        self.assertEqual([part["id"] for part in parts], [f"{unit}~{i}/3" for i in (1, 2, 3)])
        self.assertEqual([part["meta"]["parte"] for part in parts], ["1/3", "2/3", "3/3"])
        bodies = []
        for part in parts:
            head, *body = part["state"]["texto"].splitlines()
            self.assertEqual(head, f"# {unit} · - entity_id: Person:persona-larga")
            self.assertLessEqual(len(body), export.MAX_LINES)
            self.assertEqual(part["meta"]["sha"], export.unit_sha(part["state"]["texto"]))
            bodies.append(body)
        for previous, following in zip(bodies, bodies[1:]):
            self.assertEqual(previous[-export.OVERLAP:], following[:export.OVERLAP])
        self.assertEqual((bodies[0][0], bodies[-1][-1]), ("  - entity_id: Person:persona-larga", "      - nota número 99"))
        self.assertEqual((parts[0]["meta"]["l1"], parts[-1]["meta"]["l2"]), (2, 102))

    def test_a_unit_of_few_long_lines_is_cut_by_words(self):
        prosa = " ".join(["palabra"] * 200)  # 600 words on three lines: over LONG_WORDS, so cut by MAX_WORDS
        rel = self.track("pipeline/data/context/prosa.yaml", "summaries:\n  - entity_id: Person:persona-prolija\n"
                                                             f"    summary: >-\n      {prosa}\n      {prosa}\n      {prosa}\n")
        parts = [item for item in self.export(rel).values() if item["meta"].get("parte")]
        self.assertEqual(len(parts), 3)
        spans = [(part["meta"]["l1"], part["meta"]["l2"]) for part in parts]
        self.assertEqual((spans[0][0], spans[-1][1]), (2, 6))  # the whole entry
        self.assertLess(spans[1][0], spans[0][1] + 1)  # they overlap ...
        self.assertGreater(spans[1][1], spans[0][1])  # ... and the second one goes further

    def test_json_lists_of_objects_go_out_one_element_per_unit(self):
        personas = [{"id": "Person:persona-uno", "name": "Persona Uno", "keys": ["persona uno"]},
                    {"id": "Person:persona-dos", "name": "Persona Dos", "keys": ["persona dos"]}]
        lista = self.track("pipeline/data/boe/personas.json", json.dumps(personas, indent=2))
        objeto = self.track("pipeline/data/boe/objetivos.json", json.dumps({"generado_desde": "prueba", "targets": personas}))
        items = self.export(lista, objeto)
        self.assertEqual(sorted(items), sorted([
            f"{lista}#[id=Person:persona-uno]", f"{lista}#[id=Person:persona-dos]", f"{objeto}#generado_desde",
            f"{objeto}#targets[id=Person:persona-uno]", f"{objeto}#targets[id=Person:persona-dos]"]))
        uno = items[f"{lista}#[id=Person:persona-uno]"]
        self.assertEqual(uno["state"]["texto"], json.dumps(personas[0], ensure_ascii=False, indent=1))
        self.assertEqual(items[f"{objeto}#generado_desde"]["state"]["texto"], '{\n "generado_desde": "prueba"\n}')
        self.assertIsNone(uno["meta"]["l1"])

    def test_repeated_or_missing_discriminators(self):
        rel = self.track("pipeline/data/relations/prueba.yaml", "deny:\n"
                                                                "  - source: Person:persona-uno\n    target: A\n"
                                                                "  - source: Person:persona-uno\n    target: B\n"
                                                                "  - source_id: Person:persona-dos\n")
        self.assertEqual(sorted(self.export(rel)), sorted([
            f"{rel}#deny", f"{rel}#deny[source=Person:persona-uno,0]", f"{rel}#deny[source=Person:persona-uno,1]",
            f"{rel}#deny[2]"]))


class GuardTest(RepoTestCase):
    def refused(self, *names: str) -> tuple[int, str]:
        code, output = quiet(export.main, ["--out", str(self.out), *names])
        self.assertFalse((self.out / "items.jsonl").exists())
        return code, output

    def test_a_tracked_symlink_is_refused(self):
        (self.repo / ".env").write_text("TYPESAFE_API_KEY=clave-inventada\n", encoding="utf-8")
        link = self.repo / "pipeline" / "data" / "context" / "extra.yaml"
        link.parent.mkdir(parents=True)
        link.symlink_to("../../../.env")
        git(self.repo, "add", "--", "pipeline/data/context/extra.yaml")
        self.assertIn("120000", git(self.repo, "ls-files", "-s"))  # git versions the link itself
        (self.tmp / "fuera").mkdir()
        (self.tmp / "fuera" / "datos.yaml").write_text("entities: []\n", encoding="utf-8")
        (self.repo / "pipeline" / "data" / "enlace").symlink_to(self.tmp / "fuera")
        for name in ("pipeline/data/context/extra.yaml", "pipeline/data/enlace/datos.yaml"):
            with self.subTest(name=name):
                code, output = self.refused(name)
                self.assertEqual(code, 2)
                self.assertIn(f"{name}: pasa por un enlace simbólico", output)
                self.assertNotIn("clave-inventada", output)

    def test_paths_outside_the_repo_or_untracked_are_refused(self):
        self.track("pipeline/data/bueno.yaml", "entities: []\n")
        (self.tmp / "fuera.yaml").write_text("entities: []\n", encoding="utf-8")
        (self.repo / "pipeline" / "data" / "suelto.yaml").write_text("entities: []\n", encoding="utf-8")
        for name, why in (("../fuera.yaml", "fuera del repo"), (str(self.tmp / "fuera.yaml"), "fuera del repo"),
                          ("pipeline/data/suelto.yaml", "no está versionado")):
            with self.subTest(name=name):
                code, output = self.refused("pipeline/data/bueno.yaml", name)
                self.assertEqual(code, 2)
                self.assertIn(f"{name}: {why}", output)
                self.assertNotIn("bueno.yaml", output)

    def test_every_extension_is_exported_skipped_or_stops_the_export(self):
        self.track("pipeline/data/bueno.yaml", "entities: []\n")
        self.track("pipeline/data/fuente.pdf", "%PDF-1.4 inventado\n")
        code, output = quiet(export.main, ["--out", str(self.out), "pipeline/data/bueno.yaml", "pipeline/data/fuente.pdf"])
        self.assertEqual(code, 0, output)
        self.assertIn("2 ficheros: 1 yaml, 0 json, 0 de texto, 1 saltados", output)
        self.assertIn("saltado (documento fuente", output)
        (self.out / "items.jsonl").unlink()
        self.track("pipeline/data/tabla.csv", "nombre;domicilio\nPersona Inventada;junto al río Ficticio\n")
        code, output = self.refused("pipeline/data/bueno.yaml", "pipeline/data/tabla.csv")
        self.assertEqual(code, 3)
        self.assertIn("pipeline/data/tabla.csv", output)

    def test_out_in_any_work_tree_must_be_ignored_there(self):
        self.track(".gitignore", "pipeline/data/audit/\n")
        self.track("pipeline/data/bueno.yaml", "entities: []\n")
        git(self.repo, "commit", "-q", "-m", "prueba")
        segundo = self.tmp / "segundo"
        git(self.repo, "worktree", "add", "-q", str(segundo))
        cases = [(segundo / "pipeline" / "semantic-review", 2),  # Codex's case: another checkout of the same repo
                 (self.repo / "pipeline", 2),
                 (segundo / "pipeline" / "data" / "audit" / "pii", 0),  # ignored there
                 (self.tmp / "privado" / "pii" / "run", 0)]  # outside any repo
        for out, want in cases:
            with self.subTest(out=out):
                code, output = quiet(export.main, ["--out", str(out), "pipeline/data/bueno.yaml"])
                self.assertEqual(code, want, output)
                self.assertEqual((out / "items.jsonl").is_file(), want == 0)
        self.assertEqual(quiet(gate.main, ["--out", str(segundo / "pipeline" / "semantic-review")])[0], 2)
        for made in (self.tmp / "privado", self.tmp / "privado" / "pii", self.tmp / "privado" / "pii" / "run"):
            self.assertEqual(stat.S_IMODE(made.stat().st_mode), 0o700, made)


class GateTest(RepoTestCase):
    def setUp(self):
        super().setUp()
        for name, text in UNITS.items():
            self.track(name, text)
        self.items = self.export_units()
        self.predict()

    def export_units(self):
        return {item["meta"]["fichero"]: item for item in self.export(*UNITS).values()}

    def predict(self, skip=()):
        # `decide run` copies each item's meta (sha included) into its prediction.
        lines = [json.dumps({"id": item["id"], **answers(*ANSWERS[name]), "meta": item["meta"]})
                 for name, item in self.items.items() if name not in skip]
        (self.out / "pred.jsonl").write_text("\n".join(lines) + "\n")

    def revisados(self, **decisions):
        rows = [{"sha": self.items[name]["meta"]["sha"], "fichero": name, "clase": ANSWERS[name][0],
                 "decision": decision, "motivo": "prueba", "fecha": "2026-10-07"}
                for name, decision in decisions.items()]
        path = self.tmp / "revisados.yaml"
        path.write_text(yaml.safe_dump(rows, allow_unicode=True), encoding="utf-8")
        return path

    def run_gate(self, revisados):
        code, stdout = quiet(gate.main, ["--out", str(self.out), "--revisados", str(revisados)])
        listed = json.loads((self.out / "seleccion.json").read_text()) if code in (0, 1) else None
        return code, stdout, listed

    def test_new_units_stop_the_gate_and_reviewed_ones_stay_out(self):
        code, stdout, listed = self.run_gate(self.revisados(**{"seudonimo.yaml": "mantener"}))
        self.assertEqual(code, 1)
        self.assertEqual(listed, {"seleccionadas": [self.items["hermana.yaml"]["id"]],
                                  "grises": [self.items["casa.yaml"]["id"], self.items["cuenta.yaml"]["id"]]})
        self.assertIn("5 unidades, 1 seleccionadas, 2 en zona gris, 1 ya revisadas", stdout)
        report = (self.out / "seleccion.md").read_text()
        for name in ("hermana.yaml", "casa.yaml", "cuenta.yaml"):
            self.assertIn(UNITS[name].strip(), report)
        self.assertIn("| 1-2 | ninguno (identificador 0.27) | 0.51 |", report)  # why a `ninguno` unit is gray
        self.assertNotIn("Ramiro", report)
        self.assertNotIn("Cargo Inventado", report)

    def test_everything_reviewed_clears_the_gate(self):
        path = self.revisados(**{name: "mantener" for name in REVIEWABLE})
        code, stdout, listed = self.run_gate(path)
        self.assertEqual((code, listed), (0, {"seleccionadas": [], "grises": []}))
        self.assertIn("4 ya revisadas", stdout)

    def test_without_the_reviewed_list_everything_flagged_is_listed(self):
        for argv in (["--revisados", str(self.tmp / "no-existe.yaml")], []):
            with self.subTest(argv=argv):
                code, output = quiet(gate.main, ["--out", str(self.out), *argv])
                listed = json.loads((self.out / "seleccion.json").read_text())
                self.assertEqual(code, 1)
                self.assertIn("sin lista de revisadas", output)
                self.assertIn("0 ya revisadas", output)
                self.assertEqual(sorted(listed["seleccionadas"] + listed["grises"]),
                                 sorted(self.items[name]["id"] for name in REVIEWABLE))

    def test_only_mantener_excludes(self):
        path = self.revisados(**{**{name: "mantener" for name in REVIEWABLE}, "hermana.yaml": "corregir"})
        code, _, listed = self.run_gate(path)
        self.assertEqual((code, listed["seleccionadas"]), (1, [self.items["hermana.yaml"]["id"]]))

    def test_a_reviewed_unit_comes_back_when_its_text_changes(self):
        path = self.revisados(**{name: "mantener" for name in REVIEWABLE})
        (self.repo / "seudonimo.yaml").write_text(UNITS["seudonimo.yaml"] + "  extra: y nombra a su jefe\n")
        self.items = self.export_units()
        self.predict()
        code, _, listed = self.run_gate(path)
        self.assertEqual((code, listed["seleccionadas"]), (1, [self.items["seudonimo.yaml"]["id"]]))

    def test_stale_or_missing_predictions_are_an_error(self):
        path = self.revisados()
        (self.repo / "hermana.yaml").write_text(UNITS["hermana.yaml"].replace("panadería", "frutería"))
        self.export_units()  # same ids, new text: `decide --resume` would skip them and keep the old predictions
        code, stdout, _ = self.run_gate(path)
        self.assertEqual(code, 2)
        self.assertIn("1 predichas sobre otro texto", stdout)
        self.assertFalse((self.out / "seleccion.md").exists())
        self.items = self.export_units()
        self.predict(skip=("cargo.yaml",))
        code, stdout, _ = self.run_gate(path)
        self.assertEqual(code, 2)
        self.assertIn("1 sin predicción", stdout)

    def test_schema_1_shas_are_refused(self):
        v1 = "0123456789ab"
        row = {"sha": v1, "fichero": "casa.yaml", "clase": "domicilio_particular", "decision": "mantener",
               "motivo": "prueba", "fecha": "2026-10-07"}
        (self.tmp / "revisados-v1.yaml").write_text(yaml.safe_dump([row]), encoding="utf-8")
        code, stdout, _ = self.run_gate(self.tmp / "revisados-v1.yaml")
        self.assertEqual(code, 2)
        self.assertIn("del esquema 1", stdout)
        self.assertIn("reconstrúyela", stdout)
        lines = [json.dumps({"id": item["id"], **answers(*ANSWERS[name]), "meta": {**item["meta"], "sha": v1}})
                 for name, item in self.items.items()]
        (self.out / "pred.jsonl").write_text("\n".join(lines) + "\n")
        code, stdout, _ = self.run_gate(self.revisados())
        self.assertEqual(code, 2)
        self.assertIn("pred.jsonl es del esquema 1", stdout)
        items = [{**item, "meta": {**item["meta"], "sha": v1}} for item in self.items.values()]
        (self.out / "items.jsonl").write_text("".join(json.dumps(item) + "\n" for item in items))
        code, stdout, _ = self.run_gate(self.revisados())
        self.assertEqual(code, 2)
        self.assertIn("no es del esquema 2", stdout)
        self.assertFalse((self.out / "seleccion.md").exists())


class RealDataTest(unittest.TestCase):
    """The units over the data layer as versioned in this checkout."""

    def tracked(self) -> list[str]:
        return subprocess.run(["git", "-C", str(export.REPO), "ls-files", "pipeline/data", "api/app/data"], check=True,
                              capture_output=True, text=True, env=GIT_ENV).stdout.split()

    def test_units_recompose_every_tracked_yaml_and_cut_between_entries(self):
        yamls = [rel for rel in self.tracked() if export.kind_of(rel) == "yaml"]
        self.assertGreater(len(yamls), 40)
        for rel in yamls:
            with self.subTest(rel=rel):
                text = (export.REPO / rel).read_text(encoding="utf-8")
                spans = export.yaml_spans(rel, text)
                self.assertEqual("".join(raw for _, _, raw in spans), text)
                self.assertEqual(len({name for name, _, _ in spans}), len(spans))
                data = yaml.safe_load(text)
                for key, value in data.items() if isinstance(data, dict) else ():
                    entries = [raw for name, _, raw in spans if name.startswith(f"{rel}#{key}[")]
                    if not entries:
                        continue
                    # Alone under its key, each item's lines load back as exactly that item: nothing of the
                    # next entry leaks in and nothing of its own is left out.
                    self.assertEqual([yaml.safe_load(f"{key}:\n{raw}")[key] for raw in entries], [[item] for item in value])
                    for raw in entries:
                        last = raw.rstrip("\n").rsplit("\n", 1)[-1].strip()
                        self.assertTrue(last and not last.startswith("#"), raw)

    def test_the_whole_data_layer_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp).resolve() / "run"
            code, output = quiet(export.main, ["--out", str(out), *self.tracked()])
            self.assertEqual(code, 0, output)
            items = [json.loads(line) for line in (out / "items.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len({item["id"] for item in items}), len(items))
        self.assertTrue(any(item["id"].startswith("pipeline/data/boe/vigia_targets.json#targets[") for item in items))
        self.assertTrue(all(item["meta"]["schema"] == 2 and re.fullmatch(r"[0-9a-f]{16}", item["meta"]["sha"])
                            for item in items))


class ConfigTest(unittest.TestCase):
    def test_questions_cover_the_policy_classes(self):
        questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
        self.assertEqual(questions["clase"]["type"], "choice")
        self.assertEqual(questions["exceso"]["type"], "noul")
        self.assertLessEqual(gate.BENIGN | gate.GRAVE, set(questions["clase"]["criteria"]))

    def test_private_reviewed_list_has_valid_rows(self):
        if not REVISADOS.is_file():
            self.skipTest(f"{REVISADOS.relative_to(export.REPO)}: lista privada del mantenedor, no está en este checkout")
        rows = yaml.safe_load(REVISADOS.read_text(encoding="utf-8"))
        if all(isinstance(row.get("sha"), str) and gate.SHA_V1.fullmatch(row["sha"]) for row in rows):
            self.skipTest(f"{REVISADOS.relative_to(export.REPO)}: aún en el esquema 1 (sha de 12 hex); el gate la "
                          "rechaza hasta reconstruirla con el esquema 2")
        classes = set(json.loads(QUESTIONS.read_text(encoding="utf-8"))["clase"]["criteria"])
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(sha=row.get("sha")):
                self.assertEqual(set(row), {"sha", "fichero", "clase", "decision", "motivo", "fecha"})
                self.assertIsInstance(row["sha"], str)
                self.assertRegex(row["sha"], r"^[0-9a-f]{16}$")
                self.assertIn(row["clase"], classes)
                self.assertEqual(row["decision"], "mantener")
                self.assertTrue(row["motivo"].strip())
                self.assertTrue(re.match(r"(pipeline/data|api/app/data)/", row["fichero"]))
        self.assertEqual(len(gate.load_revisados(REVISADOS)), len(rows))  # no duplicate sha


if __name__ == "__main__":
    unittest.main()


class EdgeCaseTest(RepoTestCase):
    """Codex review of 2026-10-07: content must never be lost or mis-measured in silence."""

    def test_repeated_json_keys_are_refused_not_collapsed(self):
        rel = self.track("pipeline/data/boe/objetivos.json",
                         '{"targets": [{"id": "Person:uno", "nota": "vive en la calle Inventada 1", "nota": "cargo público"}]}')
        code, output = quiet(export.main, ["--out", str(self.out), rel])
        self.assertNotEqual(code, 0)
        self.assertIn("clave JSON repetida (nota)", f"{code} {output}")
        self.assertFalse((self.out / "items.jsonl").exists())

    def test_yaml_aliases_are_refused_not_mismeasured(self):
        rel = self.track("pipeline/data/context/alias.yaml",
                         "plantilla:\n  - &p\n    entity_id: Person:uno\n    nota: algo\nlista:\n  - *p\n"
                         "  - entity_id: Person:dos\n    nota: otra\n")
        code, output = quiet(export.main, ["--out", str(self.out), rel])
        self.assertNotEqual(code, 0)
        self.assertIn("alias/ancla YAML", f"{code} {output}")
        self.assertFalse((self.out / "items.jsonl").exists())
