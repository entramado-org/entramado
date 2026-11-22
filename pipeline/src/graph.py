"""LangGraph ingestion DAG: discover -> extract_text -> extract_facts ->
resolve -> load_graph -> embed -> report.

State is intentionally light (doc ids, flags, stats); all heavy data lives on
disk (chunks/, facts/, entities/) so the checkpoint stays small and a killed run
resumes without re-doing expensive LLM extraction.
"""
from __future__ import annotations

import json
import time
from typing import TypedDict

import structlog
from langgraph.graph import END, START, StateGraph

from . import config, embeddings, entity_resolution, neo4j_loader
from .chunking import Chunk, chunk_document, chunk_to_dict
from .extract_facts import extract_doc
from .pdf_extract import extract_document

log = structlog.get_logger()


class IngestState(TypedDict, total=False):
    subset: bool
    run_id: str
    doc_ids: list[str]
    stats: dict


# ---------- disk IO helpers ----------

def _chunks_path(doc_id):
    return config.CHUNKS_DIR / f"{doc_id}.jsonl"


def _write_chunks(doc_id, chunks: list[Chunk]):
    with _chunks_path(doc_id).open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(chunk_to_dict(c), ensure_ascii=False) + "\n")


def _read_chunks(doc_id) -> list[Chunk]:
    path = _chunks_path(doc_id)
    if not path.exists():
        return []
    return [Chunk(**json.loads(l)) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _read_facts(doc_id) -> list[dict]:
    path = config.FACTS_DIR / f"{doc_id}.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------- nodes ----------

async def n_discover(state: IngestState) -> dict:
    pdfs = sorted(config.DATA_DIR.glob("*.pdf"))
    if state.get("subset"):
        names = set(config.SUBSET_DOCS)
        pdfs = [p for p in pdfs if p.name in names]
    doc_ids = [p.name for p in pdfs]
    log.info("discover", n=len(doc_ids), subset=state.get("subset"))
    return {"doc_ids": doc_ids, "stats": {"docs": len(doc_ids)}}


async def n_extract_text(state: IngestState) -> dict:
    docs_meta = []
    total_chunks = 0
    for doc_id in state["doc_ids"]:
        path = config.DATA_DIR / doc_id
        if not _chunks_path(doc_id).exists():
            doc = extract_document(path)
            chunks = chunk_document(doc)
            _write_chunks(doc_id, chunks)
            docs_meta.append(
                {
                    "doc_id": doc.doc_id,
                    "title": doc.title,
                    "page_count": doc.page_count,
                    "low_text_pages": doc.low_text_pages,
                }
            )
            log.info("extracted", doc=doc_id, pages=doc.page_count,
                     chunks=len(chunks), low_text=len(doc.low_text_pages))
        else:
            docs_meta.append({"doc_id": doc_id, "title": doc_id.rsplit(".", 1)[0],
                              "page_count": None, "low_text_pages": []})
        total_chunks += len(_read_chunks(doc_id))
    # persist docs metadata for the load step
    (config.WORK_DIR / "docs_meta.json").write_text(
        json.dumps(docs_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"stats": {**state["stats"], "chunks": total_chunks}}


async def n_extract_facts(state: IngestState) -> dict:
    extracted = 0
    for doc_id in state["doc_ids"]:
        chunks = _read_chunks(doc_id)
        records = await extract_doc(doc_id, chunks)
        extracted += sum(len(r["entities"]) for r in records)
    return {"stats": {**state["stats"], "entity_mentions": extracted}}


async def n_resolve(state: IngestState) -> dict:
    all_records = []
    for doc_id in state["doc_ids"]:
        all_records.extend(_read_facts(doc_id))
    registry, resolved = entity_resolution.resolve(all_records)
    with (config.ENTITIES_DIR / "resolved.jsonl").open("w", encoding="utf-8") as f:
        for r in resolved:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"stats": {**state["stats"], "entities": len(registry)}}


async def n_load_graph(state: IngestState) -> dict:
    driver = neo4j_loader.get_driver()
    try:
        neo4j_loader.setup_schema(driver)
        docs_meta = json.loads((config.WORK_DIR / "docs_meta.json").read_text(encoding="utf-8"))
        neo4j_loader.load_documents(driver, docs_meta)

        registry = json.loads(config.REGISTRY_PATH.read_text(encoding="utf-8"))
        neo4j_loader.load_entities(driver, registry)

        all_chunks = []
        for doc_id in state["doc_ids"]:
            all_chunks.extend(chunk_to_dict(c) for c in _read_chunks(doc_id))
        neo4j_loader.load_chunks(driver, all_chunks)

        resolved = [
            json.loads(l)
            for l in (config.ENTITIES_DIR / "resolved.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        neo4j_loader.load_facts(driver, resolved)
    finally:
        driver.close()
    return {}


async def n_embed(state: IngestState) -> dict:
    cache_path = config.ENTITIES_DIR / "embeddings.jsonl"
    done: dict[str, list[float]] = {}
    if cache_path.exists():
        for l in cache_path.read_text(encoding="utf-8").splitlines():
            if l.strip():
                rec = json.loads(l)
                done[rec["chunk_id"]] = rec["embedding"]

    all_chunks = []
    for doc_id in state["doc_ids"]:
        all_chunks.extend(_read_chunks(doc_id))
    todo = [c for c in all_chunks if c.chunk_id not in done]

    if todo:
        vecs = embeddings.embed_texts([c.text for c in todo])
        with cache_path.open("a", encoding="utf-8") as f:
            for c, v in zip(todo, vecs):
                done[c.chunk_id] = v
                f.write(json.dumps({"chunk_id": c.chunk_id, "embedding": v}) + "\n")

    driver = neo4j_loader.get_driver()
    try:
        neo4j_loader.load_embeddings(driver, done)
        neo4j_loader.compute_degree(driver)
    finally:
        driver.close()
    return {"stats": {**state["stats"], "embedded": len(done)}}


async def n_report(state: IngestState) -> dict:
    report = {
        "run_id": state["run_id"],
        "subset": state.get("subset", False),
        "doc_ids": state["doc_ids"],
        "stats": state["stats"],
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    out = config.REPORTS_DIR / f"run_{state['run_id']}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("ingest_complete", **report["stats"])
    return {}


def build_graph():
    g = StateGraph(IngestState)
    g.add_node("discover", n_discover)
    g.add_node("extract_text", n_extract_text)
    g.add_node("extract_facts", n_extract_facts)
    g.add_node("resolve", n_resolve)
    g.add_node("load_graph", n_load_graph)
    g.add_node("embed", n_embed)
    g.add_node("report", n_report)

    g.add_edge(START, "discover")
    g.add_edge("discover", "extract_text")
    g.add_edge("extract_text", "extract_facts")
    g.add_edge("extract_facts", "resolve")
    g.add_edge("resolve", "load_graph")
    g.add_edge("load_graph", "embed")
    g.add_edge("embed", "report")
    g.add_edge("report", END)
    return g
