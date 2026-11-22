"""Review queue of spec 069: what no machine may decide alone.

Every decision point sends here (never to the graph) an LLM output that falls outside the
controlled vocabulary, and every doubtful accusatory decision. Items are JSONL rows under
`pipeline/data/reports/revision/` (git-ignored: they carry names and quotes):

    {id, ts, punto, motivo, detalle, termino, payload, ontologia_version, prompt_sha256}

A person resolves each item. `aprobar` on an extraction item writes its relation into the facts
cache of its chunk (so the normal load picks it up); `descartar` just records the decision.
Resolutions are append-only in `resoluciones.jsonl`.

    python -m src.revision status            # pending items by decision point and reason
    python -m src.revision listar [--punto P] [--motivo M]
    python -m src.revision aprobar ID [ID…] --nota "…"
    python -m src.revision descartar ID [ID…] --nota "…"
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .ontologia import ontologia

REVISION_DIR = config.REPORTS_DIR / "revision"
RESOLUCIONES = "resoluciones.jsonl"
DECISIONES = ("aprobada", "descartada")
# Decision points whose payload is an extracted relation of one chunk (`aprobar` writes it back).
PUNTOS_EXTRACCION = {"extraccion_mlx", "extraccion_enjambre", "verify_ingest_guardas"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def item_id(punto: str, motivo: str, payload: dict) -> str:
    """Stable id: the same rejection of the same output is ONE item, however often it is re-sent."""
    blob = json.dumps([punto, motivo, payload], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def encolar(punto: str, motivo: str, payload: dict, *, detalle: str = "", termino: str | None = None,
            prompt_sha256: str | None = None, directory: Path | None = None) -> str:
    """Append one item (idempotent by id) and return its id."""
    d = Path(directory or REVISION_DIR)
    d.mkdir(parents=True, exist_ok=True)
    iid = item_id(punto, motivo, payload)
    path = d / f"{punto}.jsonl"
    if path.exists() and any(json.loads(l)["id"] == iid for l in path.read_text(encoding="utf-8").splitlines() if l):
        return iid
    row = {"id": iid, "ts": _now(), "punto": punto, "motivo": motivo, "detalle": detalle,
           "termino": termino, "payload": payload, "ontologia_version": ontologia().version,
           "prompt_sha256": prompt_sha256}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return iid


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def items(directory: Path | None = None) -> list[dict]:
    d = Path(directory or REVISION_DIR)
    return [r for p in sorted(d.glob("*.jsonl")) if p.name != RESOLUCIONES for r in _rows(p)]


def resoluciones(directory: Path | None = None) -> dict[str, dict]:
    return {r["id"]: r for r in _rows(Path(directory or REVISION_DIR) / RESOLUCIONES)}


def pendientes(directory: Path | None = None) -> list[dict]:
    hechas = resoluciones(directory)
    return [r for r in items(directory) if r["id"] not in hechas]


def resumen(directory: Path | None = None) -> Counter:
    return Counter((r["punto"], r["motivo"]) for r in pendientes(directory))


def _escribir_en_cache(item: dict, facts_dir: Path) -> None:
    """Add an approved extracted relation to its chunk's record in the facts cache."""
    p = item["payload"]
    path = facts_dir / f"{p['doc_id']}.jsonl"
    rows = _rows(path)
    for rec in rows:
        if rec["chunk_id"] == p["chunk_id"]:
            break
    else:
        raise SystemExit(f"revision: el fragmento {p['chunk_id']} no está en la caché de {p['doc_id']}")
    rel = {**p["relation"], "revision": item["id"]}
    if rel not in rec["relations"]:
        rec["relations"].append(rel)
    have = {e["name"] for e in rec["entities"]}
    rec["entities"].extend(e for e in p.get("entities") or [] if e["name"] not in have)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def resolver(ids: list[str], decision: str, nota: str, *, directory: Path | None = None,
             facts_dir: Path | None = None) -> list[dict]:
    if decision not in DECISIONES:
        raise ValueError(decision)
    if not nota.strip():
        raise SystemExit("revision: cada resolución lleva una nota (quién y por qué)")
    d = Path(directory or REVISION_DIR)
    todos = {r["id"]: r for r in items(d)}
    hechas = resoluciones(d)
    out = []
    for iid in ids:
        if iid not in todos:
            raise SystemExit(f"revision: no existe el ítem {iid}")
        if iid in hechas:
            raise SystemExit(f"revision: {iid} ya está resuelto ({hechas[iid]['decision']})")
        item = todos[iid]
        if decision == "aprobada" and item["punto"] in PUNTOS_EXTRACCION:
            if "relation" not in item["payload"]:
                # A rejected entity is a crime or entity type outside the vocabulary: it enters by
                # extending the ontology (reviewed PR) and re-extracting, never by approving it here.
                raise SystemExit(f"revision: {iid} es una entidad fuera de vocabulario "
                                 f"({item.get('termino')!r}); solo puede rechazarse — un término nuevo "
                                 "entra ampliando la ontología y volviendo a extraer")
            _escribir_en_cache(item, Path(facts_dir or config.FACTS_DIR))
        res = {"id": iid, "ts": _now(), "decision": decision, "nota": nota.strip(), "punto": item["punto"]}
        with (d / RESOLUCIONES).open("a", encoding="utf-8") as f:
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
        out.append(res)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Cola de revisión (spec 069)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    ls = sub.add_parser("listar")
    ls.add_argument("--punto")
    ls.add_argument("--motivo")
    for cmd in ("aprobar", "descartar"):
        c = sub.add_parser(cmd)
        c.add_argument("ids", nargs="+")
        c.add_argument("--nota", required=True)
    args = ap.parse_args()
    if args.cmd == "status":
        r = resumen()
        print(f"cola de revisión: {sum(r.values())} pendientes (ontología v{ontologia().version})")
        for (punto, motivo), n in sorted(r.items()):
            print(f"  {n:6d}  {punto:24s} {motivo}")
    elif args.cmd == "listar":
        for it in pendientes():
            if (args.punto and it["punto"] != args.punto) or (args.motivo and it["motivo"] != args.motivo):
                continue
            print(json.dumps({k: it[k] for k in ("id", "punto", "motivo", "detalle", "termino")}, ensure_ascii=False))
    else:
        for res in resolver(args.ids, "aprobada" if args.cmd == "aprobar" else "descartada", args.nota):
            print(f"{res['id']}: {res['decision']}")


if __name__ == "__main__":
    main()
