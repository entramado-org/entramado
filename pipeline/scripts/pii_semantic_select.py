"""Apply the spec 067 selection policy to a `decide` run of the semantic PII sweep (`make pii-semantico`).

Reads OUT/items.jsonl (`pii_semantic_export.py`) and OUT/pred.jsonl (`decide run`) and writes, only in OUT,
seleccion.md (tables, then the text of every unit to review by hand) and seleccion.json (their ids).

Policy (pilot of 2026-10-07: synthetic controls got the right class at 0.65-1.00, hence 0.6 and not 0.8): a
unit is selected when Jev's class is personal data (not `ninguno` / `sede_oficial_o_empresa`) with confidence
>= 0.6, or when `exceso` >= 0.6. A separate gray list takes the grave classes (home, identifier, pseudonym
re-identification): chosen with confidence 0.4-0.6, or holding probability >= 0.25 whether chosen or not
(the pilot missed an identifier that came out `ninguno` 0.51 with `identificador` at 0.27). A unit whose
`meta.sha` is in the `--revisados` list with `decision: mantener` was reviewed and kept: it stays out until its
exact text changes. That list is the maintainer's and lives outside the public repo, because even without text
it would point at where personal-looking data sits; without it every flagged unit is listed. Schema 1 shas
(12 hex, line windows) are refused in items, predictions and that list alike: they do not name the same text.

Exit 0: nothing new to review. Exit 1: selected or gray units to review (the gate stops the operator).
Exit 2: the run or the list is missing, incomplete, stale or of schema 1.

    cd pipeline && uv run python -m scripts.pii_semantic_select --out DIR --revisados FILE
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

from .pii_semantic_export import out_dir

BENIGN = {"ninguno", "sede_oficial_o_empresa"}
GRAVE = {"domicilio_particular", "identificador", "reidentifica_seudonimo"}
SELECT_FROM = 0.6
GRAY_FROM = 0.4
GRAVE_PROBABILITY_FROM = 0.25
SHA = re.compile(r"[0-9a-f]{16}")
SHA_V1 = re.compile(r"[0-9a-f]{12}")


def grave_peak(clase: dict) -> tuple[str, float]:
    """The grave class Jev finds most probable, chosen or not, with its probability."""
    top = max(sorted(GRAVE), key=lambda c: clase["probabilities"].get(c, 0.0))
    return top, clase["probabilities"].get(top, 0.0)


def verdict(pred: dict) -> str | None:
    clase, exceso = pred["answers"]["clase"], pred["answers"]["exceso"]["noul"]
    if (clase["choice"] not in BENIGN and clase["confidence"] >= SELECT_FROM) or exceso >= SELECT_FROM:
        return "seleccionada"
    unsure_grave = clase["choice"] in GRAVE and clase["confidence"] >= GRAY_FROM
    if unsure_grave or grave_peak(clase)[1] >= GRAVE_PROBABILITY_FROM:
        return "gris"
    return None


def load_revisados(path: Path | None) -> set[str]:
    """Shas of the units reviewed and kept. Quoted in the YAML: unquoted, an all-digit sha loads as a number."""
    if path is None or not path.is_file():
        print(f"pii-semantico: sin lista de revisadas ({path or 'falta --revisados'}): no se excluye ninguna unidad",
              file=sys.stderr)
        return set()
    rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    for row in rows:
        sha = row.get("sha")
        if isinstance(sha, str) and SHA_V1.fullmatch(sha):
            raise ValueError(f"{path} es del esquema 1 (sha de 12 hex de ventanas por líneas, como la fila de "
                             f"{row.get('fichero')}): reconstrúyela con los sha de 16 hex del esquema 2, los "
                             "`meta.sha` de items.jsonl")
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise ValueError(f"{path}: sha inválido en la fila de {row.get('fichero')}: {sha!r}")
    return {row["sha"] for row in rows if row.get("decision") == "mantener"}


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _fence(text: str) -> str:
    return "`" * max([3] + [len(run) + 1 for run in re.findall(r"`{3,}", text)])


def _clase(pred: dict) -> str:
    """Chosen class, plus the grave class behind a gray verdict when another class won."""
    clase = pred["answers"]["clase"]
    top, probability = grave_peak(clase)
    if top != clase["choice"] and probability >= GRAVE_PROBABILITY_FROM:
        return f"{clase['choice']} ({top} {probability:.2f})"
    return clase["choice"]


def _lineas(item: dict) -> str:
    meta = item["meta"]
    return f"{meta['l1']}-{meta['l2']}" if meta.get("l1") else "—"


def _report(found: dict[str, list[tuple[dict, dict]]], summary: str, revisados: Path | None) -> str:
    where = f"`{revisados}`" if revisados else "la lista de revisadas (`--revisados`)"
    lines = ["# Barrido semántico de datos personales (spec 067): unidades a revisar", "", summary, "",
             "Seleccionada: clase de dato personal con confianza ≥ 0,6, o exceso ≥ 0,6. Zona gris: domicilio, "
             "identificador o seudónimo elegido con confianza 0,4–0,6, o con probabilidad ≥ 0,25 aunque gane otra "
             f"clase (entre paréntesis). Lo que se revisa y se mantiene se anota con su sha en {where}, fuera del repo."]
    for kind, title in (("seleccionada", "Seleccionadas"), ("gris", "Zona gris")):
        lines += ["", f"## {title} ({len(found[kind])})", ""]
        if found[kind]:
            lines += ["| unidad | líneas | clase | conf | exceso | sha |", "|---|---|---|---|---|---|"]
        for item, pred in found[kind]:
            lines.append(f"| `{item['id']}` | {_lineas(item)} | {_clase(pred)} | "
                         f"{pred['answers']['clase']['confidence']:.2f} | {pred['answers']['exceso']['noul']:.2f} | "
                         f"`{item['meta']['sha']}` |")
    lines += ["", "## Texto de las unidades"]
    for kind in ("seleccionada", "gris"):
        for item, pred in found[kind]:
            text = item["state"]["texto"].rstrip("\n")
            suffix = Path(item["meta"]["fichero"]).suffix
            language = {".yaml": "yaml", ".yml": "yaml", ".json": "json"}.get(suffix, "")
            lines += ["", f"### `{item['id']}` ({kind}, {_clase(pred)}, líneas {_lineas(item)}, "
                          f"sha `{item['meta']['sha']}`)", "", _fence(text) + language, text, _fence(text)]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="run directory with items.jsonl and pred.jsonl; outputs go here too")
    ap.add_argument("--revisados", type=Path, help="the maintainer's private list of units reviewed and kept")
    args = ap.parse_args(argv)
    out = out_dir(args.out)
    if not (out / "items.jsonl").exists():
        print(f"pii-semantico: no hay {out / 'items.jsonl'}: ejecuta antes `make pii-semantico OUT={out}`")
        return 2
    items = _read_jsonl(out / "items.jsonl")
    if not all(SHA.fullmatch(str(item["meta"].get("sha"))) for item in items):
        print(f"pii-semantico: {out / 'items.jsonl'} no es del esquema 2 (meta.sha de 16 hex por unidad; los de "
              "12 son ventanas por líneas del esquema 1): re-exporta con pii_semantic_export.py en un OUT nuevo")
        return 2
    preds = {pred["id"]: pred for pred in _read_jsonl(out / "pred.jsonl")} if (out / "pred.jsonl").exists() else {}
    if any(SHA_V1.fullmatch(str((pred.get("meta") or {}).get("sha"))) for pred in preds.values()):
        print(f"pii-semantico: {out / 'pred.jsonl'} es del esquema 1 (sha de 12 hex de ventanas por líneas): "
              "relanza `make pii-semantico` en un OUT nuevo")
        return 2
    missing = [item["id"] for item in items if item["id"] not in preds]
    stale = [item["id"] for item in items
             if item["id"] in preds and (preds[item["id"]].get("meta") or {}).get("sha") != item["meta"]["sha"]]
    if missing or stale:
        print(f"pii-semantico: {len(items)} unidades, {len(missing)} sin predicción, {len(stale)} predichas sobre otro texto")
        if missing:
            print(f"  sin predicción: relanza `make pii-semantico OUT={out}` (reintenta {out / 'pred.errors.jsonl'})")
        if stale:
            print(f"  predichas sobre otro texto (--resume salta por id): usa un OUT nuevo o borra {out / 'pred.jsonl'}")
        return 2
    try:
        kept = load_revisados(args.revisados)
    except ValueError as error:
        print(f"pii-semantico: {error}")
        return 2
    found: dict[str, list[tuple[dict, dict]]] = {"seleccionada": [], "gris": []}
    excluded = 0
    for item in items:
        kind = verdict(preds[item["id"]])
        if kind is None:
            continue
        if item["meta"]["sha"] in kept:
            excluded += 1
        else:
            found[kind].append((item, preds[item["id"]]))
    summary = (f"{len(items)} unidades, {len(found['seleccionada'])} seleccionadas, {len(found['gris'])} en zona gris, "
               f"{excluded} ya revisadas")
    (out / "seleccion.md").write_text(_report(found, summary, args.revisados), encoding="utf-8")
    ids = {"seleccionadas": [item["id"] for item, _ in found["seleccionada"]],
           "grises": [item["id"] for item, _ in found["gris"]]}
    (out / "seleccion.json").write_text(json.dumps(ids, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"pii-semantico: {summary} -> {out / 'seleccion.md'}")
    return 1 if found["seleccionada"] or found["gris"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
