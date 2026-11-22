#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["certifi", "pyyaml"]   # via import de _placsp_recon_sweep
# ///
"""READ-ONLY: itemiza contratos menores de _index/ por NIF/denominación (spec 053, Fase A).

Filtra los shards JSONL del recon 045 (pipeline/data/contratacion/_index/, 3,5M
adjudicaciones 2018–2026) para una lista de NIFs y/o denominaciones, y vuelca la tabla
itemizada (fecha, objeto, importe, órgano, expediente, deeplink) que alimenta los dosieres
de sm-vault/Patrones/ y el YAML publicado. Cero escrituras fuera de --out.

  uv run pipeline/scripts/_patrones_itemize.py --nif B04783338 \
      --name "AQUATERRA SERVICIOS INFRAESTRUCTURAS" --out /tmp/aquaterra

NB importes: el indexador prefirió PayableAmount (c/IVA) y cayó a TaxExclusiveAmount si no
existía — verificar una muestra de deeplinks antes de publicar cifras.
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _placsp_recon_sweep import UTE_RE, norm, norm_company, norm_nif  # noqa: E402
from _placsp_recon_sweep import INDEX as _INDEX_DEFAULT  # noqa: E402

# Los shards viven en el checkout principal (gitignored, no viajan a worktrees):
# override con PLACSP_INDEX cuando se corre desde ../sm-wt-053.
INDEX = Path(os.environ["PLACSP_INDEX"]) if os.environ.get("PLACSP_INDEX") else _INDEX_DEFAULT

COLS = ("award_date", "updated", "title", "amount", "organo", "folder_id",
        "winner_name", "winner_nif", "match", "link")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nif", action="append", default=[], help="NIF exacto (repetible)")
    ap.add_argument("--name", action="append", default=[],
                    help="denominación; match por contención normalizada (repetible)")
    ap.add_argument("--organo", help="filtro extra: subcadena normalizada del órgano")
    ap.add_argument("--out", help="prefijo de salida (.tsv + .md); por defecto solo stdout")
    args = ap.parse_args()
    if not args.nif and not args.name:
        ap.error("hace falta al menos un --nif o --name")

    nifs = {norm_nif(n) for n in args.nif}
    names = [norm_company(n) for n in args.name]
    organo_f = norm(args.organo) if args.organo else None

    shards = sorted(INDEX.glob("*.jsonl"))
    if not shards:
        sys.exit("no hay shards en _index/ — corre _placsp_recon_sweep.py index primero")

    rows: list[dict] = []
    seen: set[tuple] = set()
    for shard in shards:
        with shard.open() as f:
            for line in f:
                if not _fast_prefilter(line, nifs, names):
                    continue
                rec = json.loads(line)
                wnif = norm_nif(rec.get("winner_nif") or "")
                wnorm = norm_company(rec.get("winner_name") or "")
                why = None
                if wnif and wnif in nifs:
                    why = f"nif:{wnif}"
                else:
                    for n in names:
                        if n and n in wnorm:
                            why = ("ute:" if UTE_RE.search(norm(rec.get("winner_name") or ""))
                                   else "nombre:") + n
                            break
                if not why:
                    continue
                if organo_f and organo_f not in norm(rec.get("organo") or ""):
                    continue
                key = (rec.get("id"), wnif or wnorm)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({**rec, "match": why})

    rows.sort(key=lambda r: (r.get("award_date") or r.get("updated") or ""))
    _report(rows, args.out)


def _fast_prefilter(line: str, nifs: set[str], names: list[str]) -> bool:
    """Cheap gate before json.loads (3,5M líneas). Compara en el MISMO espacio
    normalizado que el matcher real: los shards van con ensure_ascii=False
    (tildes literales) y un prefilter sobre la línea cruda descartaría
    «PÉREZ…» al buscar «PEREZ…» — falso negativo silencioso, no solo coste."""
    up = norm(line)
    return any(n in up.replace(" ", "") for n in nifs) or \
        any(n and n.split()[0] in up for n in names)


def _report(rows: list[dict], out_prefix: str | None) -> None:
    total = sum(float(r["amount"]) for r in rows if r.get("amount"))
    by_year = Counter((r.get("award_date") or r.get("updated") or "????")[:4] for r in rows)
    by_organo = Counter(r.get("organo") or "?" for r in rows)
    def _cell(v) -> str:  # títulos PLACSP traen tabs/saltos embebidos que parten filas
        return " ".join(str(v or "").split())

    tsv = ["\t".join(COLS)] + [
        "\t".join(_cell(r.get(c)) for c in COLS) for r in rows]
    md = ["| fecha adj. | objeto | importe € | órgano | expediente |", "|---|---|---|---|---|"] + [
        "| {} | {} | {} | {} | {} |".format(
            r.get("award_date") or r.get("updated") or "?",
            (r.get("title") or "?").replace("|", "/"),
            r.get("amount") or "?",
            (r.get("organo") or "?").replace("|", "/"),
            r.get("folder_id") or "?")
        for r in rows]
    summary = (f"\n{len(rows)} contratos · {total:,.0f} € (importes según índice, ver NB IVA)\n"
               f"por año: {dict(sorted(by_year.items()))}\n"
               f"órganos: {dict(by_organo.most_common())}")
    print("\n".join(tsv))
    print(summary)
    if out_prefix:
        Path(f"{out_prefix}.tsv").write_text("\n".join(tsv) + "\n")
        Path(f"{out_prefix}.md").write_text("\n".join(md) + summary + "\n")
        print(f"\nescrito: {out_prefix}.tsv / .md")


if __name__ == "__main__":
    main()
