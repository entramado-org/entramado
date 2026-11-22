"""Load the curated MERCANTIL overlay (registro mercantil / BORME).

Two passes, both driven by `make mercantil`:
  * Tanda 1 (`companies:` key) — PROPS-ONLY: enrich existing Company/Organization nodes with
    registry properties (see below). Never creates nodes, never writes edges.
  * Tanda 2 (`network:` key) — NETWORK: create new Company/Person nodes + ADMINISTRA/OWNS edges
    from BORME structure, strictly ≤1 hop from an existing node (see the NETWORK section below).


Our graph is extracted from judicial PDFs: rich in case relations, poor in structured
company data. There is no `nif`/`hoja`/`capital` property today — a NIF only lives as free
text inside `role` prose for ~9 orgs in context/organizaciones.yaml. The Nearius recon
(findings/nearius-mercantil-recon.md) verified the BORME hoja registral for much of the cast.

This loader enriches `Company`/`Organization` nodes that **already exist** with first-class
registry properties (nif, hoja_registral, capital_social, cnae, fecha_constitucion, provincia,
domicilio, borme_id) + an `is_mercantil` marker. It is deliberately conservative:

  * It uses MATCH (not merge-create): a row whose entity_id is not already a node is SKIPPED
    (logged `mercantil_skip_missing`) — it never creates nodes, and it writes no edges.
  * It is FILL-ONLY: `coalesce(n.prop, row.prop)` never overwrites an existing non-null value,
    and it never touches name/canonical_name/summary/role/source_kind — a judicial or context
    node keeps its provenance intact.

BORME data is official/public identity+structure, cited by borme_id — NOT a judicial fact and
NOT an accusation. Structure ≠ guilt; respect presunción de inocencia. Idempotent + re-runnable.

Usage:
    cd pipeline && uv run python -m src.mercantil_loader
    cd pipeline && uv run python -m src.mercantil_loader --dir ../some/dir
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

MERCANTIL_DIR = config.WORK_DIR / "mercantil"
SOURCE_KIND = "mercantil"

_ENTITY_TYPES = {t.value for t in EntityType}
_REL_TYPES = {t.value for t in RelationType}

# Neutral, structural summaries for nodes created by the network pass (Tanda 2). BORME data is
# public identity/structure, NOT a judicial fact nor an accusation — presunción de inocencia.
_DEFAULT_SUMMARY = {
    "Company": ("Sociedad mercantil inscrita en el Registro Mercantil (BORME). Dato de estructura "
                "societaria público; no es indicio ni prueba de uso delictivo. Presunción de inocencia."),
    "Organization": ("Entidad inscrita en el Registro Mercantil (BORME). Dato de estructura pública; "
                     "no es indicio ni prueba de uso delictivo. Presunción de inocencia."),
    "Person": ("Administrador registral inscrito en el Registro Mercantil (BORME). Dato de estructura "
               "societaria público; el BORME capta administradores, no la titularidad real ni conducta "
               "delictiva alguna. Presunción de inocencia."),
}

# Registry props carried onto the node (all fill-only). Keys = node prop, values = row key.
# Ampliado spec 031: objeto social, histórico de capital (STRING pre-formateada — Neo4j no
# admite lista de mapas como prop de nodo) y situación mercantil (concurso / disolución).
_PROPS = {
    "nif": "nif",
    "hoja_registral": "hoja",
    "capital_social": "capital_social",
    "cnae": "cnae",
    "fecha_constitucion": "fecha_constitucion",
    "provincia": "provincia",
    "domicilio": "domicilio",
    "borme_id": "borme_id",
    "objeto_social": "objeto_social",
    "capital_historico": "capital_historico",
    "en_concurso": "en_concurso",
    "concurso_fecha": "concurso_fecha",
    "concurso_juzgado": "concurso_juzgado",
    "disuelta": "disuelta",
    "disuelta_fecha": "disuelta_fecha",
    # Situación "en liquidación" (≠ concurso, ≠ disuelta) — añadido en la remediación de la
    # auditoría 2026-07-08 (caso LIC, BORME-A-2025-140-46). Solo dato en nodo; web/MCP aún no lo renderizan.
    "en_liquidacion": "en_liquidacion",
    "liquidacion_fecha": "liquidacion_fecha",
}


def load_files(directory: Path) -> list[dict]:
    """Read every *.yaml/*.yml; collect the top-level `companies:` list."""
    out: list[dict] = []
    for f in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        items = doc.get("companies") or []
        out.extend(items)
        log.info("mercantil_file", file=f.name, companies=len(items))
    return out


def _prepare(items: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for c in items:
        name = (c.get("name") or "").strip()
        etype = (c.get("type") or "Company").strip()
        eid = (c.get("entity_id") or "").strip()
        if etype not in _ENTITY_TYPES:
            log.warning("mercantil_skip_bad_type", entity_id=eid, name=name, type=etype)
            continue
        if not eid:
            if not name:
                log.warning("mercantil_skip_no_id_no_name", row=c)
                continue
            eid = entity_id(etype, name)
        cap = c.get("capital_social")
        try:
            cap = float(cap) if cap is not None else None
        except (TypeError, ValueError):
            log.warning("mercantil_bad_capital", entity_id=eid, capital=cap)
            cap = None
        rows.append({
            "entity_id": eid,
            "nif": c.get("nif"),
            "hoja": c.get("hoja"),
            "capital_social": cap,
            "cnae": c.get("cnae"),
            "fecha_constitucion": c.get("fecha_constitucion"),
            "provincia": c.get("provincia"),
            "domicilio": c.get("domicilio"),
            "borme_id": c.get("borme_id"),
            "objeto_social": c.get("objeto_social"),
            "capital_historico": c.get("capital_historico"),
            # Situación mercantil (concurso / disolución) — señal pública, no imputación.
            "en_concurso": c.get("en_concurso"),
            "concurso_fecha": c.get("concurso_fecha"),
            "concurso_juzgado": c.get("concurso_juzgado"),
            "disuelta": c.get("disuelta"),
            "disuelta_fecha": c.get("disuelta_fecha"),
            "en_liquidacion": c.get("en_liquidacion"),
            "liquidacion_fecha": c.get("liquidacion_fecha"),
        })
    return rows


def _report_missing(driver, rows: list[dict]) -> list[str]:
    """Log entity_ids that are NOT already nodes (props-only never creates them)."""
    ids = [r["entity_id"] for r in rows]
    q = "UNWIND $ids AS id MATCH (n {entity_id: id}) RETURN collect(DISTINCT n.entity_id) AS found"
    with driver.session() as s:
        found = set(s.run(q, ids=ids).single()["found"])
    missing = [i for i in ids if i not in found]
    for i in missing:
        log.warning("mercantil_skip_missing", entity_id=i)
    return missing


def load(driver, rows: list[dict]) -> int:
    # MATCH existing node by entity_id, SET registry props fill-only + is_mercantil marker.
    # Never MERGE/CREATE (0 new nodes), never write edges, never clobber name/summary/role/source_kind.
    sets = ",\n        ".join(
        f"n.{prop} = coalesce(n.{prop}, row.{key})" for prop, key in _PROPS.items()
    )
    q = f"""
    UNWIND $rows AS row
    MATCH (n {{entity_id: row.entity_id}})
    SET n.is_mercantil = true,
        {sets}
    RETURN count(n) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("mercantil_loaded", rows=len(rows), enriched=n)
    return n


# ---------------------------------------------------------------------------
# Tanda 2 — NETWORK pass: create new Company/Person nodes + ADMINISTRA/OWNS edges
# from BORME structure, strictly ≤1 hop from an existing node (owner guard-rail).
#
# The ≤1-hop gate is materialised in the loader shape: every NEW node is created
# ONLY as the far endpoint of an edge to an `anchor` that already exists. The
# anchor is MATCHed (never merge-created); if it is absent the whole row drops
# out (Cypher MATCH yields no rows) so no orphan is ever created. Edges are keyed
# `source_kind='mercantil'` so they coexist with judicial/contexto edges of the
# same triple and are protected from the relations-overlay deny (source_kind
# null/'judicial' only). Idempotent + re-runnable.
# ---------------------------------------------------------------------------


def load_network_files(directory: Path) -> list[dict]:
    """Read every *.yaml/*.yml; collect the top-level `network:` list."""
    out: list[dict] = []
    for f in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        items = doc.get("network") or []
        if items:
            log.info("mercantil_network_file", file=f.name, edges=len(items))
        out.extend(items)
    return out


def _norm_endpoint(spec: dict | None) -> dict | None:
    """Normalise one edge endpoint. `exists: true` → an anchor (MATCH-only, never
    created/clobbered). Otherwise it is a NEW node with onCreate props."""
    if not spec:
        return None
    etype = (spec.get("type") or "Company").strip()
    if etype not in _ENTITY_TYPES:
        log.warning("mercantil_skip_bad_type_ep", type=etype, spec=spec)
        return None
    eid = (spec.get("id") or spec.get("entity_id") or "").strip()
    name = (spec.get("name") or "").strip()
    if not eid:
        if not name:
            log.warning("mercantil_skip_ep_no_id_no_name", spec=spec)
            return None
        eid = entity_id(etype, name)
    exists = bool(spec.get("exists"))
    oncreate = None
    if not exists:
        cap = spec.get("capital_social")
        try:
            cap = float(cap) if cap is not None else None
        except (TypeError, ValueError):
            log.warning("mercantil_bad_capital", entity_id=eid, capital=cap)
            cap = None
        label = name or eid.split(":", 1)[-1]
        oncreate = {
            "name": label,
            "canonical_name": label,
            "source_kind": SOURCE_KIND,
            "is_mercantil": True,
            "mentions": 0,
            "degree": 0,
            "nif": spec.get("nif"),
            "hoja_registral": spec.get("hoja"),
            "capital_social": cap,
            "cnae": spec.get("cnae"),
            "fecha_constitucion": spec.get("fecha_constitucion"),
            "provincia": spec.get("provincia"),
            "domicilio": spec.get("domicilio"),
            "borme_id": spec.get("borme_id"),
            "summary": spec.get("summary") or _DEFAULT_SUMMARY.get(etype, _DEFAULT_SUMMARY["Company"]),
        }
    return {"entity_id": eid, "type": etype, "exists": exists, "oncreate": oncreate}


def _prepare_network(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split rows into `both_exist` (edge between two existing nodes, 0 creation)
    and `one_new` (exactly one endpoint created, hung off an existing anchor).
    A row with no existing anchor is skipped (would create an orphan > 1 hop)."""
    both: list[dict] = []
    new: list[dict] = []
    for it in items:
        rel = (it.get("rel") or "ADMINISTRA").strip().upper().replace(" ", "_")
        if rel not in _REL_TYPES:
            log.warning("mercantil_skip_rel_type", rel=rel, row=it)
            continue
        s = _norm_endpoint(it.get("source"))
        t = _norm_endpoint(it.get("target"))
        if s is None or t is None:
            continue
        edge = it.get("edge") or {}
        eprops = {
            "borme_id": edge.get("borme_id"),
            "rol": edge.get("rol"),
            "note": edge.get("note"),
            "desde": edge.get("desde"),
        }
        if s["exists"] and t["exists"]:
            both.append({"a_id": s["entity_id"], "b_id": t["entity_id"], "rel": rel, "props": eprops})
        elif s["exists"] ^ t["exists"]:
            if s["exists"]:  # target is the new node; edge source→target keeps its direction
                anchor, newn, new_is_source = s, t, False
            else:            # source is the new node
                anchor, newn, new_is_source = t, s, True
            new.append({
                "anchor_id": anchor["entity_id"],
                "new_id": newn["entity_id"],
                "new_type": newn["type"],
                "new_props": newn["oncreate"],
                "new_is_source": new_is_source,
                "rel": rel,
                "props": eprops,
            })
        else:  # neither endpoint exists → would be an orphan cluster > 1 hop
            log.warning("mercantil_skip_no_anchor", row=it)
    return both, new


def _report_missing_anchors(driver, both: list[dict], new: list[dict]) -> list[str]:
    """Log anchor entity_ids that are NOT present (their rows are silently dropped
    by the MATCH → the ≤1-hop gate). Ids created in THIS run's `new` pass are not
    flagged (a both-exist edge may legitimately reference one, e.g. Boreal→Avintia)."""
    created = {r["new_id"] for r in new}
    ids = set()
    for r in both:
        ids.update((r["a_id"], r["b_id"]))
    for r in new:
        ids.add(r["anchor_id"])
    ids -= created
    if not ids:
        return []
    q = "UNWIND $ids AS id OPTIONAL MATCH (n {entity_id: id}) RETURN id, n IS NOT NULL AS found"
    with driver.session() as s:
        found = {rec["id"]: rec["found"] for rec in s.run(q, ids=list(ids))}
    missing = [i for i, ok in found.items() if not ok]
    for i in missing:
        log.warning("mercantil_skip_anchor_missing", entity_id=i)
    return missing


def load_network_both(driver, rows: list[dict]) -> int:
    """Edges between two already-existing nodes (both MATCHed; 0 node creation)."""
    if not rows:
        return 0
    q = """
    UNWIND $rows AS row
    MATCH (a {entity_id: row.a_id}), (b {entity_id: row.b_id})
    CALL apoc.merge.relationship(a, row.rel, {source_kind: 'mercantil'}, row.props, b, {}) YIELD rel
    RETURN count(rel) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("mercantil_network_edges_existing", rows=len(rows), created=n)
    return n


def load_network_new(driver, rows: list[dict]) -> int:
    """Create the far (new) node hung off an existing anchor, then the edge.
    MATCH on the anchor is the ≤1-hop gate: a missing anchor drops the whole row."""
    if not rows:
        return 0
    q = """
    UNWIND $rows AS row
    MATCH (anchor {entity_id: row.anchor_id})
    CALL apoc.merge.node([row.new_type], {entity_id: row.new_id}, row.new_props, {}) YIELD node AS nn
    WITH row, anchor, nn,
         CASE WHEN row.new_is_source THEN nn ELSE anchor END AS s,
         CASE WHEN row.new_is_source THEN anchor ELSE nn END AS t
    CALL apoc.merge.relationship(s, row.rel, {source_kind: 'mercantil'}, row.props, t, {}) YIELD rel
    RETURN count(DISTINCT nn) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("mercantil_network_nodes_new", rows=len(rows), nodes=n)
    return n


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default=str(MERCANTIL_DIR))
    args = p.parse_args()

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("mercantil_dir_missing", dir=str(directory))
        return
    rows = _prepare(load_files(directory))
    both, new = _prepare_network(load_network_files(directory))
    if not rows and not both and not new:
        log.warning("mercantil_empty", dir=str(directory))
        return

    driver = neo4j_loader.get_driver()
    try:
        # Pass 1 — props-only enrichment (Tanda 1): fill registry props on existing nodes.
        missing = _report_missing(driver, rows) if rows else []
        if rows:
            load(driver, rows)
        # Pass 2 — network (Tanda 2): create new nodes/edges, ≤1-hop gated.
        # New nodes FIRST so a subsequent both-exist edge can reference a node
        # created in this same run (e.g. Boreal Sinergias → Avintia succession).
        missing_anchors = _report_missing_anchors(driver, both, new)
        n_new = load_network_new(driver, new)
        n_edges = load_network_both(driver, both)
        neo4j_loader.compute_degree(driver)  # network edges/nodes count toward degree
    finally:
        driver.close()
    log.info(
        "mercantil_done",
        props_total=len(rows),
        props_skipped_missing=len(missing),
        network_edges_existing=n_edges,
        network_nodes_new=n_new,
        network_skipped_anchors=len(missing_anchors),
    )


if __name__ == "__main__":
    main()
