"""Assemble the Claude extraction swarm's output into the per-doc fact cache.

Reads data/reports/extractions/batch_*.json (one entry per chunk:
{chunk_id, entities[], relations[]}), recovers doc/page metadata from
ingest_chunks.json, runs the SAME guards as the MLX path (src.extraction_guards.validar_extraccion, spec 069):
item by item, never the whole chunk; what falls outside the controlled vocabulary and every
accusatory relation (no verifier on this raw path) go to the review queue, not the cache. Writes
data/facts/{doc_id}.jsonl in the EXACT extract_facts._to_record format. Populating
this cache is what makes the downstream resolve/load steps skip MLX entirely.

Run from pipeline/:
    uv run python -m scripts._ingest_apply
"""
from __future__ import annotations

import json

from src import config
from src.extraction_guards import encolar_rechazos, validar_extraccion
from src.schema import ChunkExtraction

EXTR_DIR = config.REPORTS_DIR / "extractions"


def _record(meta: dict, ex: ChunkExtraction, prov: dict | None = None) -> dict:
    """EXACT key set of extract_facts._to_record, provenance included (spec 069)."""
    prov = prov or {}
    return {
        "modelo": prov.get("modelo"),
        "prompt_sha256": prov.get("prompt_sha256"),
        "ontologia_version": prov.get("ontologia_version"),
        "chunk_id": meta["chunk_id"],
        "doc_id": meta["doc_id"],
        "doc_title": meta["doc_title"],
        "page_start": meta["page_start"],
        "page_end": meta.get("page_end", meta["page_start"]),
        "entities": [e.model_dump(mode="json") for e in ex.entities],
        "relations": [r.model_dump(mode="json") for r in ex.relations],
    }


def main():
    dump = json.loads((config.REPORTS_DIR / "ingest_chunks.json").read_text(encoding="utf-8"))
    cmeta = {c["chunk_id"]: c for b in dump["batches"] for c in b["chunks"]}

    if not EXTR_DIR.exists():
        raise SystemExit(f"missing {EXTR_DIR} — run the extraction swarm first")

    by_doc: dict[str, dict[str, dict]] = {}
    n_files = n_chunks = dropped = n_revision = 0
    for f in sorted(EXTR_DIR.glob("batch_*.json")):
        n_files += 1
        payload = json.loads(f.read_text(encoding="utf-8"))
        entries = payload if isinstance(payload, list) else \
            (payload.get("chunks") or payload.get("results") or [])
        for entry in entries:
            cid = entry.get("chunk_id")
            m = cmeta.get(cid)
            if not m:
                dropped += 1
                continue
            ex, rechazos = validar_extraccion(entry, m.get("text", ""), acusatorias_a_revision=True)
            rec = _record(m, ex, dump["meta"])
            n_revision += encolar_rechazos("extraccion_enjambre", rec, rechazos,
                                           prompt_sha256=dump["meta"].get("prompt_sha256"))
            by_doc.setdefault(m["doc_id"], {})[cid] = rec  # dedup by chunk_id (last wins)
            n_chunks += 1

    for doc_id, recs in sorted(by_doc.items()):
        path = config.FACTS_DIR / f"{doc_id}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for r in recs.values():
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        ne = sum(len(r["entities"]) for r in recs.values())
        nr = sum(len(r["relations"]) for r in recs.values())
        print(f"  ✔ {doc_id}: {len(recs)} chunks, {ne} entities, {nr} relations → {path.name}")

    # Coverage check: chunks dumped vs chunks with a fact record.
    covered = sum(len(v) for v in by_doc.values())
    print(f"\n✔ {n_files} batch files · {covered}/{dump['meta']['n_chunks']} chunks covered"
          f" · {dropped} entries with unknown chunk_id dropped · {n_revision} items → cola de revisión"
          " (python -m src.revision status)")
    if covered < dump["meta"]["n_chunks"]:
        print(f"  ⚠ {dump['meta']['n_chunks'] - covered} chunks MISSING a fact record"
              f" — re-run those batches before the tail.")


if __name__ == "__main__":
    main()
