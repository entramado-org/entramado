"""Idempotent loading of entities, chunks, relationships and embeddings into Neo4j.

Uses APOC for dynamic labels/relationship types and UNWIN-batched MERGEs.
"""
from __future__ import annotations

import structlog
from neo4j import GraphDatabase

from . import config
from .ontologia import ontologia
from .schema import EntityType, RelationType

# Symmetric relations describe an UNDIRECTED fact; the extractor emits them in whatever
# order the sentence used, so "A casado con B" and "B casado con A" (usually from different
# chunks) land as two opposite edges that double-count degree (#4 in the Pedro Sánchez ego
# audit, run 20260619: ASSOCIATED_WITH 177 bidirectional pairs, MET_WITH 75, FAMILY_OF 16).
# We canonicalise the direction (lower entity_id → higher) at load time so a symmetric fact
# is always stored ONE way; the cross-chunk dedup in load_facts then collapses true repeats.
# Endpoint props are symmetric (no amount/date on these), so the swap is lossless.
_SYMMETRIC = set(ontologia().simetricas)

log = structlog.get_logger()

ENTITY_LABELS = [t.value for t in EntityType]
_BATCH = 500


def get_driver():
    return GraphDatabase.driver(
        config.NEO4J_URI, auth=(config.NEO4J_USERNAME, config.NEO4J_PASSWORD)
    )


def setup_schema(driver):
    stmts = [
        f"CREATE CONSTRAINT {lbl.lower()}_id IF NOT EXISTS "
        f"FOR (n:{lbl}) REQUIRE n.entity_id IS UNIQUE"
        for lbl in ENTITY_LABELS
    ]
    stmts += [
        "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.doc_id IS UNIQUE",
        "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",
        "CREATE FULLTEXT INDEX chunk_fulltext IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text]",
        "CREATE FULLTEXT INDEX entityNames IF NOT EXISTS "
        f"FOR (n:{ '|'.join(ENTITY_LABELS) }) ON EACH [n.name, n.aliases]",
        "CREATE VECTOR INDEX chunk_embeddings IF NOT EXISTS "
        "FOR (c:Chunk) ON (c.embedding) "
        "OPTIONS {indexConfig: {`vector.dimensions`: "
        f"{config.EMBEDDING_DIM}, `vector.similarity_function`: 'cosine'}}}}",
    ]
    with driver.session() as s:
        for st in stmts:
            s.run(st)
    log.info("neo4j_schema_ready", labels=len(ENTITY_LABELS))


def _chunked(seq, n=_BATCH):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def load_documents(driver, docs: list[dict]):
    # `ingested_at` is sealed on FIRST load (coalesce keeps the original), so a
    # re-run doesn't reset it — this is the timestamp a future "what was added
    # since X" changelog reads. Only documents ingested after this change carry it.
    q = """
    UNWIND $rows AS row
    MERGE (d:Document {doc_id: row.doc_id})
      SET d.title = row.title, d.page_count = row.page_count,
          d.low_text_pages = row.low_text_pages,
          d.ingested_at = coalesce(d.ingested_at, datetime())
    """
    with driver.session() as s:
        s.run(q, rows=docs)


def load_entities(driver, registry: dict):
    rows = list(registry.values())
    q = """
    UNWIND $rows AS row
    CALL apoc.merge.node([row.type], {entity_id: row.entity_id},
      {name: row.canonical_name, canonical_name: row.canonical_name,
       aliases: row.aliases, mentions: row.mentions},
      {aliases: row.aliases, mentions: row.mentions}) YIELD node
    RETURN count(node)
    """
    with driver.session() as s:
        for batch in _chunked(rows):
            s.run(q, rows=batch)
    log.info("loaded_entities", n=len(rows))


def load_chunks(driver, chunks: list[dict]):
    q = """
    UNWIND $rows AS row
    MERGE (c:Chunk {chunk_id: row.chunk_id})
      SET c.text = row.text, c.doc_id = row.doc_id, c.doc_title = row.doc_title,
          c.page_start = row.page_start, c.page_end = row.page_end,
          c.chunk_index = row.chunk_index
    MERGE (d:Document {doc_id: row.doc_id})
    MERGE (c)-[:PART_OF]->(d)
    """
    with driver.session() as s:
        for batch in _chunked(chunks):
            s.run(q, rows=batch)
    log.info("loaded_chunks", n=len(chunks))


def load_facts(driver, resolved_records: list[dict]):
    """Load entity->chunk MENTIONED_IN edges and entity-entity relationships."""
    mentions, rels = [], []
    for rec in resolved_records:
        for e in rec["entities"]:
            mentions.append({"entity_id": e["entity_id"], "chunk_id": rec["chunk_id"]})
        for r in rec["relations"]:
            rels.append(
                {
                    "source_id": r["source_id"],
                    "target_id": r["target_id"],
                    "type": r["type"],
                    "chunk_id": rec["chunk_id"],
                    "doc_id": rec["doc_id"],
                    "page": rec["page_start"],
                    "quote": r.get("source_quote") or "",
                    "amount": r.get("amount"),
                    "currency": r.get("currency"),
                    "date": r.get("date"),
                }
            )

    # Ingest guard (2026-07-02, Option D): parliamentary transcripts (Diario de
    # Sesiones) are consultable documents, NOT a source of entity-entity edges —
    # their extractions are overwhelmingly page-header boilerplate, Q&A co-mention
    # and rhetoric (findings/aristas-transcripciones-parlamentarias). We still load
    # their Document + Chunks + MENTIONED_IN (mentions are untouched below), but drop
    # the entity-entity relations so a (re-)ingest can't reintroduce the 679 defects
    # this replaced. Mirrors the cleanup.py rule so live and re-ingest agree.
    from .documents_loader import transcript_doc_ids  # lazy: avoid import cycle

    _tdocs = transcript_doc_ids()
    if _tdocs:
        _before = len(rels)
        rels = [r for r in rels if r["doc_id"] not in _tdocs]
        if len(rels) != _before:
            log.info("skipped_transcript_relations", before=_before,
                     after=len(rels), docs=sorted(_tdocs))

    # Canonicalise symmetric-relation direction (lower entity_id → higher) so "A↔B" and
    # "B↔A" don't become two opposite edges. Done BEFORE the dedup below so true repeats
    # collapse. Only affects this load — the live prod graph is untouched (never re-ingests).
    for r in rels:
        if r["type"] in _SYMMETRIC and (r["source_id"] or "") > (r["target_id"] or ""):
            r["source_id"], r["target_id"] = r["target_id"], r["source_id"]

    # Collapse identical relations grounded in different (overlapping) chunks:
    # same (source, target, type, doc, page, normalized-quote) is one fact, not
    # N parallel edges. MERGE already dedupes within a chunk (keyed on
    # source_chunk_id); this dedupes ACROSS chunks before the write so a fact
    # repeated by chunk overlap doesn't become a duplicate edge. Only affects
    # this load — the live prod graph is untouched (it never re-ingests).
    seen: set = set()
    deduped: list[dict] = []
    for r in rels:
        key = (r["source_id"], r["target_id"], r["type"], r["doc_id"],
               r["page"], " ".join((r["quote"] or "").split()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    if len(deduped) != len(rels):
        log.info("dedup_relations", before=len(rels), after=len(deduped))
    rels = deduped

    mention_q = """
    UNWIND $rows AS row
    MATCH (e {entity_id: row.entity_id})
    MATCH (c:Chunk {chunk_id: row.chunk_id})
    MERGE (e)-[:MENTIONED_IN]->(c)
    """
    rel_q = """
    UNWIND $rows AS row
    MATCH (a {entity_id: row.source_id})
    MATCH (b {entity_id: row.target_id})
    CALL apoc.merge.relationship(a, row.type,
      {source_chunk_id: row.chunk_id},
      {source_doc: row.doc_id, source_page: row.page, source_quote: row.quote,
       amount: row.amount, currency: row.currency, date: row.date},
      b, {}) YIELD rel
    // Spec 059 (procedencia fail-closed): an extracted edge is ALWAYS labelled with
    // its provenance tier; readers no longer assume 'judicial' for a missing kind.
    SET rel.source_kind = coalesce(rel.source_kind, 'judicial')
    RETURN count(rel)
    """
    with driver.session() as s:
        for batch in _chunked(mentions):
            s.run(mention_q, rows=batch)
        for batch in _chunked(rels):
            s.run(rel_q, rows=batch)
    log.info("loaded_facts", mentions=len(mentions), relations=len(rels))


def load_embeddings(driver, vectors: dict[str, list[float]]):
    rows = [{"chunk_id": k, "embedding": v} for k, v in vectors.items()]
    q = """
    UNWIND $rows AS row
    MATCH (c:Chunk {chunk_id: row.chunk_id})
    CALL db.create.setNodeVectorProperty(c, 'embedding', row.embedding)
    """
    with driver.session() as s:
        for batch in _chunked(rows, 200):
            s.run(q, rows=batch)
    log.info("loaded_embeddings", n=len(rows))


def compute_degree(driver):
    """Materialize degree on entity nodes for fast hub ranking / overview."""
    q = """
    MATCH (n) WHERE n.entity_id IS NOT NULL
    SET n.degree = COUNT { (n)--() }
    """
    with driver.session() as s:
        s.run(q)
    log.info("computed_degree")
