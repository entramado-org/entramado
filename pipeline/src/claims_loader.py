"""Load the curated CLAIMS-IN-DISPUTE overlay into Neo4j (spec 011, P5).

Many statements in the cases have an accusation AND a denial (e.g. in a
parliamentary appearance: "acudía a Diego de León" vs "yo no estuve nunca
allí"). The source-anchored graph models edges as facts/indicios with ONE
provenance; it can't represent a posture and its opposite together with their
sources. This overlay reifies each contested statement as a `Claim` node and
attaches the postures as typed, cited edges:

    (:Entity)-[:ASSERTS  {source_kind, source_doc, source_page, source_quote, date}]->(:Claim)
    (:Entity)-[:DISPUTES {…}]->(:Claim)
    (:Claim)-[:ABOUT]->(:Entity)         # optional anchor to the case/entity

A `Claim` carries `{claim_id, text, topic}` and — crucially — has NO
`entity_id`, so it never appears in the normal graph/overview (which match
`entity_id IS NOT NULL`); claims are a separate, consultable layer surfaced by
the `claims_entidad` / `evidencia_claim` MCP tools and the web "En disputa"
panel. Idempotent: MERGE on `claim_id` and on (stance, source_kind) per edge.

GROUND RULE (presunción de inocencia): every posture cites its source and is
attributed to whoever holds it; an accusation is never presented as proven, and
a denial is shown alongside it, not as the last word.

Usage:
    cd pipeline && uv run python -m src.claims_loader
    cd pipeline && uv run python -m src.claims_loader --dir ../some/dir
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

import structlog
import yaml

from . import config, neo4j_loader
from .entity_resolution import entity_id

log = structlog.get_logger()

CLAIMS_DIR = config.WORK_DIR / "claims"
_STANCES = {"asserts": "ASSERTS", "disputes": "DISPUTES"}


def _slug(text: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60]


def load_files(directory: Path) -> list[dict]:
    out: list[dict] = []
    for f in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        items = doc.get("claims") or []
        out.extend(items)
        log.info("claims_file", file=f.name, claims=len(items))
    return out


def declared_empty(directory: Path, claims: list[dict]) -> bool:
    """True only for an overlay that EXPLICITLY lists no Claims: at least one YAML file, every file
    carries a `claims:` key, and no entry was read (so nothing was dropped as invalid)."""
    files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    if not files or claims:
        return False
    for f in files:
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or "claims" not in doc or doc["claims"] not in (None, []):
            return False
    return True


# Labels a posture holder may carry (the id prefix picks the node label in `load`).
_HOLDER_LABELS = {"Person", "Organization", "Company", "Court"}


def _resolve_person(p: dict) -> tuple[str, str]:
    """(entity_id, display_name) for a posture's holder."""
    name = (p.get("person") or "").strip()
    pid = (p.get("entity_id") or "").strip() or (entity_id("Person", name) if name else "")
    return pid, name


def _prepare(claims: list[dict]) -> tuple[list[dict], list[dict]]:
    """Flatten to Claim rows + posture-edge rows. Resolves entity_ids the same
    way the pipeline does, so postures attach to the canonical person nodes."""
    claim_rows: list[dict] = []
    edge_rows: list[dict] = []
    for c in claims:
        text = (c.get("text") or "").strip()
        if not text:
            log.warning("claim_skip_no_text", entry=c)
            continue
        cid = (c.get("claim_id") or "").strip() or _slug(c.get("topic") or text)
        claim_rows.append({
            "claim_id": cid,
            "text": text,
            "topic": (c.get("topic") or "").strip() or None,
            "about": [a.strip() for a in (c.get("about") or []) if a and a.strip()],
        })
        for stance_key, rel in _STANCES.items():
            for p in c.get(stance_key) or []:
                pid, name = _resolve_person(p)
                if not pid:
                    log.warning("claim_posture_skip_no_person", claim=cid, entry=p)
                    continue
                # The label is derived from the id prefix at load time: only known labels, never empty.
                label, _, suffix = pid.partition(":")
                if label not in _HOLDER_LABELS or not suffix:
                    log.warning("claim_posture_skip_bad_entity_id", claim=cid, entity_id=pid)
                    continue
                edge_rows.append({
                    "claim_id": cid,
                    "rel_type": rel,
                    "person_id": pid,
                    "person_name": name,
                    "source_kind": (p.get("source_kind") or "judicial").strip().lower(),
                    "source_doc": p.get("doc"),
                    "source_page": p.get("page"),
                    "source_quote": (p.get("quote") or "").strip()[:600] or None,
                    "date": p.get("date"),
                    "outlet": p.get("outlet"),
                    "url": p.get("url"),
                })
    return claim_rows, edge_rows


def load(driver, claim_rows: list[dict], edge_rows: list[dict]) -> None:
    # Claim node: MERGE on claim_id (NOT entity_id → stays out of the graph).
    qc = """
    UNWIND $rows AS row
    CALL apoc.merge.node(['Claim'], {claim_id: row.claim_id},
      {text: row.text, topic: row.topic}, {text: row.text, topic: row.topic}) YIELD node AS cl
    WITH cl, row
    UNWIND (CASE WHEN size(row.about) = 0 THEN [null] ELSE row.about END) AS aid
    OPTIONAL MATCH (e {entity_id: aid})
    FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END |
      MERGE (cl)-[:ABOUT]->(e))
    RETURN count(DISTINCT cl) AS n
    """
    # Posture edge: holder -[ASSERTS|DISPUTES {source_kind}]-> Claim, idempotent. The holder's label
    # comes from the entity_id prefix (Person by default; an explicit `entity_id: Organization:…`
    # attaches to the existing organisation instead of minting a Person stub — 061 T1, Manos Limpias).
    qe = """
    UNWIND $rows AS row
    MATCH (cl:Claim {claim_id: row.claim_id})
    CALL apoc.merge.node([split(row.person_id, ':')[0]], {entity_id: row.person_id},
      {name: coalesce(row.person_name, row.person_id), canonical_name: coalesce(row.person_name, row.person_id),
       mentions: 0, degree: 0}, {}) YIELD node AS p
    CALL apoc.merge.relationship(p, row.rel_type, {source_kind: row.source_kind},
      {source_doc: row.source_doc, source_page: row.source_page, source_quote: row.source_quote,
       date: row.date, date_norm: row.date, outlet: row.outlet, url: row.url},
      cl,
      {source_doc: row.source_doc, source_page: row.source_page, source_quote: row.source_quote,
       date: row.date, date_norm: row.date, outlet: row.outlet, url: row.url}) YIELD rel
    RETURN count(rel) AS n
    """
    with driver.session() as s:
        nc = s.run(qc, rows=claim_rows).single()["n"]
        ne = s.run(qe, rows=edge_rows).single()["n"] if edge_rows else 0
    log.info("claims_loaded", claims=nc, postures=ne)


def prune(driver, claim_rows: list[dict], edge_rows: list[dict]) -> None:
    """claims.yaml is the source of truth: drop the Claims and postures it no longer lists.

    `load` only MERGEs, so a posture retired in the YAML (an audit found no source for it) would
    otherwise stay published forever. Postures are keyed like the MERGE: (holder, stance,
    source_kind, claim). A Claim that leaves the YAML goes with its ABOUT anchors and postures.
    """
    keep = [{"claim_id": r["claim_id"], "rel_type": r["rel_type"], "person_id": r["person_id"],
             "source_kind": r["source_kind"]} for r in edge_rows]
    qp = """
    MATCH (p)-[r:ASSERTS|DISPUTES]->(cl:Claim)
    WHERE NOT {claim_id: cl.claim_id, rel_type: type(r), person_id: p.entity_id,
               source_kind: r.source_kind} IN $keep
    DELETE r
    RETURN count(r) AS n
    """
    qc = """
    MATCH (cl:Claim) WHERE NOT cl.claim_id IN $ids
    DETACH DELETE cl
    RETURN count(cl) AS n
    """
    with driver.session() as s:
        npost = s.run(qp, keep=keep).single()["n"]
        nclaim = s.run(qc, ids=[r["claim_id"] for r in claim_rows]).single()["n"]
    log.info("claims_pruned", claims=nclaim, postures=npost)


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default=str(CLAIMS_DIR))
    args = p.parse_args()

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("claims_dir_missing", dir=str(directory))
        return
    claims = load_files(directory)
    claim_rows, edge_rows = _prepare(claims)
    if not claim_rows and not declared_empty(directory, claims):
        # Nothing valid to load and no explicit `claims: []`: a missing or broken overlay must
        # never wipe the published Claims.
        log.warning("claims_empty", dir=str(directory))
        return

    driver = neo4j_loader.get_driver()
    try:
        if claim_rows:
            load(driver, claim_rows, edge_rows)
        # An explicit empty overlay still prunes: retiring the LAST Claim must unpublish it.
        prune(driver, claim_rows, edge_rows)
    finally:
        driver.close()
    log.info("claims_done")


if __name__ == "__main__":
    main()
