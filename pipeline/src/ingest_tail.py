"""No-MLX, doc-SCOPED ingest tail: resolve → load → embed → degree.

Finishes ingestion for an explicit list of doc_ids whose fact cache
(data/facts/{doc_id}.jsonl) was already produced by the Claude extraction swarm
(scripts/_ingest_apply.py). Reuses the exact resolution/loader/embedding code paths
but WITHOUT LangGraph and WITHOUT the MLX health gate in run_ingest.

Crucially it loads ONLY the given docs: load_facts MERGEs relationships keyed by
{type, source_chunk_id}, and every new edge carries a new-doc source_chunk_id, so
old docs' edges are never re-MERGEd — this is what prevents resurrecting the ~10.7k
hallucination edges that were deleted live-DB-only in earlier sessions.

Run from pipeline/:
    uv run python -m src.ingest_tail --docs ATS_10789_2025.pdf informe-udef-1907.pdf ...
"""
from __future__ import annotations

import argparse
import json
import os

import structlog

from . import config, embeddings, entity_resolution, neo4j_loader
from .chunking import chunk_to_dict
from .graph import _read_chunks, _read_facts  # reuse the exact disk-IO helpers

log = structlog.get_logger()


def run(doc_ids: list[str]) -> None:
    doc_set = set(doc_ids)

    # 1) RESOLVE — over ONLY the new docs' facts (host BGE-m3; no MLX).
    all_records: list[dict] = []
    for d in doc_ids:
        all_records.extend(_read_facts(d))
    if not all_records:
        log.error("no_facts", hint="run scripts._ingest_apply first")
        raise SystemExit(1)
    registry, resolved = entity_resolution.resolve(all_records)  # writes registry.json (scoped)
    with (config.ENTITIES_DIR / "resolved.jsonl").open("w", encoding="utf-8") as f:
        for r in resolved:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    log.info("resolved", records=len(all_records), entities=len(registry))

    # 2) LOAD GRAPH — only these docs' documents/entities/chunks/edges (all MERGE-based).
    driver = neo4j_loader.get_driver()
    try:
        neo4j_loader.setup_schema(driver)
        docs_meta = json.loads((config.WORK_DIR / "docs_meta.json").read_text(encoding="utf-8"))
        docs_meta = [m for m in docs_meta if m["doc_id"] in doc_set]
        neo4j_loader.load_documents(driver, docs_meta)
        neo4j_loader.load_entities(driver, registry)

        all_chunks = []
        for d in doc_ids:
            all_chunks.extend(chunk_to_dict(c) for c in _read_chunks(d))
        neo4j_loader.load_chunks(driver, all_chunks)

        resolved_disk = [
            json.loads(l)
            for l in (config.ENTITIES_DIR / "resolved.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        neo4j_loader.load_facts(driver, resolved_disk)
    finally:
        driver.close()

    # 3) EMBED new chunks (host BGE-m3) + recompute degree.
    cache_path = config.ENTITIES_DIR / "embeddings.jsonl"
    done: dict[str, list[float]] = {}
    if cache_path.exists():
        for l in cache_path.read_text(encoding="utf-8").splitlines():
            if l.strip():
                rec = json.loads(l)
                done[rec["chunk_id"]] = rec["embedding"]

    new_chunks = [c for d in doc_ids for c in _read_chunks(d)]
    new_ids = {c.chunk_id for c in new_chunks}
    todo = [c for c in new_chunks if c.chunk_id not in done]
    if todo:
        vecs = embeddings.embed_texts([c.text for c in todo])
        with cache_path.open("a", encoding="utf-8") as f:
            for c, v in zip(todo, vecs):
                done[c.chunk_id] = v
                f.write(json.dumps({"chunk_id": c.chunk_id, "embedding": v}) + "\n")

    driver = neo4j_loader.get_driver()
    try:
        # push only the new docs' vectors (don't rewrite every chunk in the graph)
        neo4j_loader.load_embeddings(driver, {k: v for k, v in done.items() if k in new_ids})
        neo4j_loader.compute_degree(driver)
    finally:
        driver.close()

    log.info("ingest_tail_done", docs=len(doc_ids), entities=len(registry),
             chunks=len(all_chunks), embedded=len(todo))
    print(f"\n✔ tail: {len(doc_ids)} docs · {len(registry)} entities · {len(all_chunks)} chunks"
          f" · {len(todo)} newly embedded. Run `make context relations accusations` next.")


def main():
    ap = argparse.ArgumentParser(description="No-MLX scoped ingest tail")
    ap.add_argument("--docs", nargs="+", required=True, help="doc_ids WITH .pdf")
    run(ap.parse_args().docs)
    # torch/sentence-transformers leave non-daemon threads; work is durable, exit hard.
    os._exit(0)


if __name__ == "__main__":
    main()
