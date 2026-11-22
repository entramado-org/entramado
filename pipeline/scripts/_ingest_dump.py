"""Dump chunks for the Claude extraction swarm (replaces the MLX extract_facts step).

For the 10 new docs: PDF -> text (+OCR for scans) -> page-aware chunks on disk
(data/chunks/{doc_id}.jsonl, the SAME files run_ingest writes), then batch the
chunks into data/reports/ingest_chunks.json for `ingest_swarm.workflow.js`.

The batch file also carries the EXACT extract_facts.SYSTEM_PROMPT (entity/relation
vocab interpolated) so the swarm honours an identical extraction contract — no drift.

No MLX. Run from pipeline/ — pass the new doc_ids (WITH .pdf) as CLI args:
    uv run python -m scripts._ingest_dump auto-peinado-juicio-oral-begona-2026-06-20.pdf
With no args it falls back to DEFAULT_DOCS (the last ingested doc, for reference).
"""
from __future__ import annotations

import json
import sys

from src import config
from src.chunking import chunk_document, chunk_to_dict
from src.pdf_extract import extract_document
from src.extract_facts import PROMPT_SHA256, SYSTEM_PROMPT
from src.ontologia import ontologia

# The extraction swarm's model, pinned in scripts/ingest_swarm.workflow.js (spec 069 T7.5).
SWARM_MODEL = "opus"

# Fallback when no doc_ids are passed on the CLI (doc_id == filename WITH .pdf,
# matching extract_document). Prefer passing the docs as args; this is only a default.
DEFAULT_DOCS = [
    "auto-zapatero-joyas-2026-06-12.pdf",  # auto Calama: pieza separada Plus Ultra (joyas) — contrabando + delito fiscal
]

BATCH = 10  # chunks per swarm agent (~9k tokens of text per batch)


def _chunks_path(doc_id: str):
    return config.CHUNKS_DIR / f"{doc_id}.jsonl"


def _write_chunks(doc_id: str, chunks):
    with _chunks_path(doc_id).open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(chunk_to_dict(c), ensure_ascii=False) + "\n")


def _read_chunk_dicts(doc_id: str) -> list[dict]:
    path = _chunks_path(doc_id)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    docs = sys.argv[1:] or DEFAULT_DOCS
    docs_meta = []
    all_items: list[dict] = []

    for doc_id in docs:
        if not _chunks_path(doc_id).exists():
            doc = extract_document(config.DATA_DIR / doc_id)  # OCR fires for scanned pages
            chunks = chunk_document(doc)
            _write_chunks(doc_id, chunks)
            docs_meta.append({
                "doc_id": doc.doc_id, "title": doc.title,
                "page_count": doc.page_count, "low_text_pages": doc.low_text_pages,
            })
            print(f"  extracted {doc_id}: {doc.page_count}pp -> {len(chunks)} chunks"
                  f" (low_text={len(doc.low_text_pages)})")
        else:
            docs_meta.append({
                "doc_id": doc_id, "title": doc_id.rsplit(".", 1)[0],
                "page_count": None, "low_text_pages": [],
            })
            print(f"  cached    {doc_id}: {len(_read_chunk_dicts(doc_id))} chunks")

        for c in _read_chunk_dicts(doc_id):
            all_items.append({
                "chunk_id": c["chunk_id"], "doc_id": c["doc_id"], "doc_title": c["doc_title"],
                "page_start": c["page_start"], "page_end": c["page_end"], "text": c["text"],
            })

    # Merge docs_meta with the existing file so the tail load_documents keeps all docs.
    meta_path = config.WORK_DIR / "docs_meta.json"
    existing = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else []
    merged = {m["doc_id"]: m for m in existing}
    merged.update({m["doc_id"]: m for m in docs_meta})
    meta_path.write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=2),
                         encoding="utf-8")

    # Batch chunks for the swarm.
    batches = [{"batch_id": i // BATCH, "chunks": all_items[i:i + BATCH]}
               for i in range(0, len(all_items), BATCH)]
    out = config.REPORTS_DIR / "ingest_chunks.json"
    out.write_text(json.dumps({
        "meta": {
            "n_chunks": len(all_items), "n_batches": len(batches), "docs": docs,
            "system_prompt": SYSTEM_PROMPT,  # identical extraction contract for the swarm
            "prompt_sha256": PROMPT_SHA256, "ontologia_version": ontologia().version,
            "modelo": SWARM_MODEL,
        },
        "batches": batches,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✔ {len(all_items)} chunks → {len(batches)} batches → {out}")
    print(f"  next: run scripts/ingest_swarm.workflow.js (the extraction swarm)")


if __name__ == "__main__":
    main()
