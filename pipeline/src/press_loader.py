"""Load the curated PRESS-enrichment layer into Neo4j.

The graph is built from judicial/police documents; the `contexto` layer adds
common-knowledge public facts with no source. This loader adds a third tier:
NEUTRAL biographical / structural facts reported by named press outlets, each
carrying its provenance (outlet + URL + date + verbatim quote). Tagged
`source_kind="prensa"` so the UI, the vault and the chat keep it clearly separate
from both judicial facts and unsourced context.

GROUND RULE (same presumption-of-innocence model): press enrichment is for
NEUTRAL, verifiable facts only — offices held, appointment dates, company
foundations, family / professional ties reported as fact. NEVER alleged
wrongdoing; that lives in the judicial documents with their own provenance.

It is idempotent: entities MERGE by the same deterministic `entity_id` the
pipeline uses; press relationships are keyed by `source_kind` so re-running never
duplicates them and never overwrites a document-sourced edge of the same type.
On an existing (document-sourced) node it only *fills* a missing role — it never
clobbers judicial data nor flags that node as press.

Usage:
    cd pipeline && uv run python -m src.press_loader
    cd pipeline && uv run python -m src.press_loader --dir ../some/other/dir
"""
from __future__ import annotations

import argparse
from pathlib import Path

import structlog
import yaml

from . import config, neo4j_loader
from .entity_resolution import entity_id
from .schema import EntityType, RelationType

log = structlog.get_logger()

PRESS_DIR = config.WORK_DIR / "press"

_ENTITY_TYPES = {t.value for t in EntityType}
_REL_TYPES = {t.value for t in RelationType}


def load_files(directory: Path) -> tuple[list[dict], list[dict]]:
    """Read and merge every *.yaml / *.yml file in the press directory."""
    files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    entities: list[dict] = []
    relations: list[dict] = []
    for f in files:
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        entities.extend(doc.get("entities") or [])
        relations.extend(doc.get("relations") or [])
        log.info("press_file", file=f.name,
                 entities=len(doc.get("entities") or []),
                 relations=len(doc.get("relations") or []))
    return entities, relations


def _year_of(rel: dict) -> int | None:
    """A press relation may carry an explicit `year`, else derive it from a
    leading 4-digit year in `date` (e.g. '2024-11-15' → 2024). Structural facts
    with no inherent date simply have no year (and are kept under any filter)."""
    y = rel.get("year")
    if isinstance(y, int):
        return y
    date = str(rel.get("date") or "")
    if len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def _prepare(entities: list[dict], relations: list[dict]):
    """Resolve names to entity_ids; validate types. Returns (nodes, edges)."""
    nodes: list[dict] = []
    name_to_id: dict[str, str] = {}
    for e in entities:
        etype, name = e.get("type"), (e.get("name") or "").strip()
        if etype not in _ENTITY_TYPES or not name:
            log.warning("press_skip_entity", name=name, type=etype)
            continue
        eid = entity_id(etype, name)
        name_to_id[name] = eid
        nodes.append({"entity_id": eid, "type": etype, "name": name, "role": e.get("role")})

    edges: list[dict] = []
    for r in relations:
        rtype = r.get("type")
        src, tgt = (r.get("source") or "").strip(), (r.get("target") or "").strip()
        if rtype not in _REL_TYPES:
            log.warning("press_skip_rel_type", type=rtype, source=src, target=tgt)
            continue
        sid = name_to_id.get(src) or _id_from_loose(src, entities)
        tid = name_to_id.get(tgt) or _id_from_loose(tgt, entities)
        if not sid or not tid:
            log.warning("press_skip_rel_unresolved", source=src, target=tgt)
            continue
        outlet, url = (r.get("outlet") or "").strip(), (r.get("url") or "").strip()
        if not outlet or not url:
            # Press enrichment without attribution defeats the whole point.
            log.warning("press_skip_rel_unattributed", source=src, target=tgt, type=rtype)
            continue
        edges.append({
            "source_id": sid, "target_id": tid, "type": rtype,
            "outlet": outlet, "url": url,
            "date": r.get("date"), "year": _year_of(r),
            "quote": (r.get("quote") or "").strip()[:300] or None,
            "note": r.get("note"),
        })
    return nodes, edges


def _id_from_loose(name: str, entities: list[dict]) -> str | None:
    """Allow a relation endpoint that wasn't listed under `entities` as long as
    we can find its declared type (so endpoints must appear in `entities`)."""
    for e in entities:
        if (e.get("name") or "").strip() == name and e.get("type") in _ENTITY_TYPES:
            return entity_id(e["type"], name)
    return None


def load(driver, nodes: list[dict], edges: list[dict]):
    # Entities: onCreate sets identity + flags the new node as press-sourced. The
    # role is only ever *filled* (coalesce), never overwritten — so a real
    # document figure keeps its provenance and we never clobber judicial data.
    node_q = """
    UNWIND $rows AS row
    CALL apoc.merge.node([row.type], {entity_id: row.entity_id},
      {name: row.name, canonical_name: row.name,
       source_kind: 'prensa', is_press: true, mentions: 0, degree: 0},
      {}) YIELD node
    SET node.role = coalesce(node.role, row.role)
    RETURN count(node)
    """
    # Relations keyed by source_kind so a press edge never collides with (or
    # overwrites) a judicial / contexto edge of the same (source, target, type).
    rel_q = """
    UNWIND $rows AS row
    MATCH (a {entity_id: row.source_id})
    MATCH (b {entity_id: row.target_id})
    CALL apoc.merge.relationship(a, row.type,
      {source_kind: 'prensa'},
      {outlet: row.outlet, url: row.url, date: row.date, date_year: row.year,
       source_quote: row.quote, note: row.note},
      b,
      {outlet: row.outlet, url: row.url, date: row.date, date_year: row.year,
       source_quote: row.quote, note: row.note}) YIELD rel
    RETURN count(rel)
    """
    with driver.session() as s:
        s.run(node_q, rows=nodes)
        s.run(rel_q, rows=edges)
    log.info("press_loaded", nodes=len(nodes), edges=len(edges))


def lineage_has_press(driver) -> bool:
    with driver.session() as s:
        return s.run("MATCH ()-[r {source_kind: 'prensa'}]->() RETURN count(r) > 0 AS has").single()["has"]


def main():
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default=str(PRESS_DIR))
    p.add_argument("--allow-lineage", action="store_true",
                   help="cargar aunque el grafo ya tenga aristas de prensa (NO sobre el seed vivo)")
    args = p.parse_args()

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("press_dir_missing", dir=str(directory))
        return
    entities, relations = load_files(directory)
    nodes, edges = _prepare(entities, relations)
    if not nodes and not edges:
        log.warning("press_empty", dir=str(directory))
        return

    driver = neo4j_loader.get_driver()
    try:
        if not args.allow_lineage and lineage_has_press(driver):
            # Capa CONGELADA (063, revisión Codex 29-sep): sobre el linaje (seed R2) el MERGE reescribe
            # cita/url/nota de aristas ya auditadas —incluidas las rebajadas a `prensa` por relations.yaml,
            # que conservan su source_doc— y resucita las retiradas. Correcciones → relations.yaml.
            log.error("press_frozen_on_lineage",
                      note="el grafo ya tiene aristas de prensa: corrige con relations.yaml, no re-cargando")
            raise SystemExit(1)
        load(driver, nodes, edges)
        neo4j_loader.compute_degree(driver)  # press edges now count toward degree
    finally:
        driver.close()
    log.info("press_done")


if __name__ == "__main__":
    main()
