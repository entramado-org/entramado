"""Load the curated BOE overlay (sección II.A appointments/ceses, spec 049).

Driven by `make boe`. Three passes over `data/boe/boe.yaml`:
  * Pass 1 (`trayectorias:` rows) — PROPS-ONLY: mark existing Person nodes with
    `is_boe` + the trajectory aggregate (`boe_n_cargos`, `boe_rango`, `boe_nota`,
    `boe_incidencias`). MATCH-only, CURATED-OVERWRITE on the `boe_*` namespace
    (the YAML is the source of truth for the layer — spec 050; a null clears the
    prop): never creates nodes, never touches name/summary/role/source_kind.
  * Pass 2 (`cargos:` per person) — EDGES: ONE aggregated `APPOINTED_TO` edge per
    (person, cargo, etapa), keyed {source_kind:'boe', boe_key:"<cargo-slug>|<año>"}.
    `boe_key` is IMMUTABLE once shipped (changing it duplicates the edge — a `|pre`
    etapa keeps its `|pre` suffix even after its nombramiento is filled in; the
    suffix is a uniquifier, not semantics). Edge props are CURATED-OVERWRITE too:
    `SET r += props` with the FULL prop map, so a null in the YAML (e.g. the removed
    «cese sin nombramiento» nota after a directed pass fills the etapa) DELETES the
    stale prop on reload. The person is MATCHed (never merge-created) — the ≤1-hop
    gate; body stubs come from `organismos:` and are merge-created onCreate-only
    (label from the id prefix: Organization/Court). The etapa carries
    `fecha_nombramiento`/`fecha_cese` (NEVER `date_norm`/`date_year`) and no
    `amount`, so the layer stays out of /flujos and the cronología BY CONSTRUCTION.
    `fecha_cese` absent = no cese in this layer (either a live office or a cese
    outside the recon seed) — surfaces must never word it as "vigente".
  * `incidencias:` (acuerdos CGPJ de servicios especiales/adscripción, excedencias,
    reingresos) NEVER create edges: they are folded into the node prop
    `boe_incidencias` by pass 1.

BOE data is official/public. An appointment or cese is a LEGAL administrative act and
is neither a judicial fact nor an accusation — the editorial signal is the trajectory
crossed with the case cast, never the office itself. Presunción de inocencia.
Idempotent + re-runnable. (matches.candidate.yaml has none of the loaded keys, so the
recon seed is never loaded.)

Usage:
    cd pipeline && uv run python -m src.boe_loader
    cd pipeline && uv run python -m src.boe_loader --dir ../some/dir
"""
from __future__ import annotations

import argparse
from pathlib import Path

import structlog
import yaml

from . import config, neo4j_loader
from .schema import RelationType

log = structlog.get_logger()

BOE_DIR = config.WORK_DIR / "boe"
SOURCE_KIND = "boe"
REL = RelationType.APPOINTED_TO.value

# Neutral, structural summary for body stubs. Being appointed to (or removed from) a
# public office is legal — presunción de inocencia.
_STUB_SUMMARY = (
    "Organismo público que consta como destino de nombramientos o ceses publicados en "
    "la sección II.A del BOE. Dato oficial público; un nombramiento o un cese es un "
    "acto administrativo legal y no es indicio ni prueba de delito. Presunción de "
    "inocencia."
)

# Edge props copied from each etapa. The curated `nota` lands on the edge as
# `note` (the shared per-edge note prop the /graph/relation projections expose).
_EDGE_PROPS = (
    "cargo", "fecha_nombramiento", "boe_id_nombramiento", "url_nombramiento",
    "titulo_nombramiento", "fecha_cese", "boe_id_cese", "url_cese", "titulo_cese",
)


def load_files(directory: Path) -> tuple[list[dict], list[dict]]:
    """Read every *.yaml/*.yml; collect `trayectorias:` and `organismos:`.
    (matches.candidate.yaml has neither key, so the recon seed is never loaded;
    `_*` files are scratch/crudos gitignored — p. ej. el borrador de curación
    `_boe_draft.yaml`, que SÍ tiene `trayectorias:` — and are never loaded.)"""
    trays: list[dict] = []
    orgs: list[dict] = []
    for f in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        if f.name.startswith("_"):
            continue
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        t = doc.get("trayectorias") or []
        o = doc.get("organismos") or []
        if t or o:
            log.info("boe_file", file=f.name, trayectorias=len(t), organismos=len(o))
        trays.extend(t)
        orgs.extend(o)
    return trays, orgs


def _stub_defs(orgs: list[dict]) -> dict[str, dict]:
    """Body stubs keyed by entity_id, as onCreate prop maps (+ label from the prefix)."""
    out: dict[str, dict] = {}
    for o in orgs:
        oid = (o.get("id") or "").strip()
        name = (o.get("name") or "").strip()
        if not oid or not name:
            log.warning("boe_skip_bad_organismo", row=o)
            continue
        label = o.get("label") or oid.split(":", 1)[0]
        if label not in ("Organization", "Court"):
            log.warning("boe_skip_bad_label", entity_id=oid, label=label)
            continue
        out[oid] = {
            "label": label,
            "props": {
                "name": name,
                "canonical_name": name,
                "role": o.get("role"),
                "source_kind": SOURCE_KIND,
                "mentions": 0,
                "degree": 0,
                "summary": o.get("summary") or _STUB_SUMMARY,
            },
        }
    return out


def _fold_incidencias(incs: list[dict]) -> str | None:
    """Compact one-line-per-incidencia text for the node prop (no dates as props)."""
    lines = [f"{i.get('fecha')}: {i.get('nota')} ({i.get('boe_id')})"
             for i in incs if i.get("nota")]
    return "\n".join(lines) or None


def _prepare_nodes(trays: list[dict]) -> list[dict]:
    """Props-only rows for the persons (trajectory aggregate)."""
    rows: list[dict] = []
    for t in trays:
        t_id = (t.get("entity_id") or "").strip()
        if not t_id:
            log.warning("boe_skip_no_entity", row=t)
            continue
        rows.append({
            "entity_id": t_id,
            "n_cargos": t.get("boe_n_cargos"),
            "rango": t.get("boe_rango"),
            "nota": t.get("nota"),
            "incidencias": _fold_incidencias(t.get("incidencias") or []),
        })
    return rows


def _prepare_edges(trays: list[dict], stubs: dict[str, dict]) -> list[dict]:
    """Flatten persons × cargos into aggregated edge rows. Forbidden money/chronology
    props (`amount`, `en_flujos`, `date_norm`, `date_year`) are rejected: the layer
    stays out of /flujos and cronología by construction."""
    rows: list[dict] = []
    for t in trays:
        t_id = (t.get("entity_id") or "").strip()
        if not t_id:
            continue
        for c in t.get("cargos") or []:
            org_id = (c.get("organismo") or "").strip()
            key = (c.get("boe_key") or "").strip()
            if not org_id or not key or not c.get("cargo"):
                log.warning("boe_skip_bad_cargo_row", persona=t_id, row=c)
                continue
            forbidden = {"amount", "en_flujos", "date_norm", "date_year"} & set(c)
            if forbidden:
                log.warning("boe_skip_forbidden_props", persona=t_id, organismo=org_id,
                            props=sorted(forbidden))
                continue
            props = {k: c.get(k) for k in _EDGE_PROPS}
            props["note"] = c.get("nota")
            # Full map, nulls INCLUDED: `SET r += props` deletes a prop set to
            # null, so un-setting a curated field (e.g. the nota of a filled-in
            # `|pre` etapa) propagates on reload (curated-overwrite, spec 050).
            rows.append({
                "p_id": t_id,
                "org_id": org_id,
                "org_is_stub": org_id in stubs,
                "org_label": stubs.get(org_id, {}).get("label"),
                "org_props": stubs.get(org_id, {}).get("props"),
                "key": key,
                "props": props,
            })
    return rows


def _report_missing(driver, ids: set[str]) -> set[str]:
    """Log anchor ids that are NOT nodes. A missing person is the ≤1-hop gate doing
    its job; a missing non-stub organismo is a curation error."""
    q = "UNWIND $ids AS id OPTIONAL MATCH (n {entity_id: id}) RETURN id, n IS NOT NULL AS found"
    with driver.session() as s:
        found = {rec["id"]: rec["found"] for rec in s.run(q, ids=list(ids))}
    missing = {i for i, ok in found.items() if not ok}
    for i in sorted(missing):
        log.warning("boe_skip_anchor_missing", entity_id=i)
    return missing


def load_node_props(driver, rows: list[dict]) -> int:
    """Pass 1: MATCH person, SET is_boe + boe_* curated-overwrite (the YAML is the
    source of truth for the layer namespace; a null clears the prop). 0 creation;
    never touches name/role/summary/source_kind."""
    if not rows:
        return 0
    q = """
    UNWIND $rows AS row
    MATCH (n {entity_id: row.entity_id})
    SET n.is_boe = true,
        n.boe_n_cargos = row.n_cargos,
        n.boe_rango = row.rango,
        n.boe_nota = row.nota,
        n.boe_incidencias = row.incidencias
    RETURN count(n) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("boe_nodes", rows=len(rows), enriched=n)
    return n


def load_edges_existing(driver, rows: list[dict]) -> int:
    """Pass 2a: edges whose organismo already exists (both endpoints MATCHed; 0
    creation). Plain MERGE (the rel type is a constant) + `SET r +=` with the full
    prop map = curated-overwrite: props update on match and a null deletes."""
    if not rows:
        return 0
    q = f"""
    UNWIND $rows AS row
    MATCH (a {{entity_id: row.p_id}}), (b {{entity_id: row.org_id}})
    MERGE (a)-[r:{REL} {{source_kind: '{SOURCE_KIND}', boe_key: row.key}}]->(b)
    SET r += row.props
    RETURN count(r) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("boe_edges_existing", rows=len(rows), merged=n)
    return n


def load_edges_stub(driver, rows: list[dict]) -> int:
    """Pass 2b: create the organismo stub hung off an existing person, then the edge.
    MATCH on the person is the ≤1-hop gate; the stub stays onCreate-only (onMatch {}
    never clobbers; label from the curated id prefix Organization/Court) while the
    edge itself is curated-overwrite like pass 2a."""
    if not rows:
        return 0
    q = f"""
    UNWIND $rows AS row
    MATCH (a {{entity_id: row.p_id}})
    CALL apoc.merge.node([row.org_label], {{entity_id: row.org_id}}, row.org_props, {{}}) YIELD node AS b
    MERGE (a)-[r:{REL} {{source_kind: '{SOURCE_KIND}', boe_key: row.key}}]->(b)
    SET r += row.props
    RETURN count(r) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("boe_edges_stub", rows=len(rows), merged=n)
    return n


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", default=str(BOE_DIR))
    args = p.parse_args()

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("boe_dir_missing", dir=str(directory))
        return
    trays, orgs = load_files(directory)
    stubs = _stub_defs(orgs)
    node_rows = _prepare_nodes(trays)
    edge_rows = _prepare_edges(trays, stubs)
    if not node_rows and not edge_rows:
        log.warning("boe_empty", dir=str(directory))
        return

    driver = neo4j_loader.get_driver()
    try:
        anchor_ids = ({r["entity_id"] for r in node_rows}
                      | {r["p_id"] for r in edge_rows}
                      | {r["org_id"] for r in edge_rows if not r["org_is_stub"]})
        missing = _report_missing(driver, anchor_ids)
        node_rows = [r for r in node_rows if r["entity_id"] not in missing]
        edge_rows = [r for r in edge_rows
                     if r["p_id"] not in missing and r["org_id"] not in missing]
        n_nodes = load_node_props(driver, node_rows)
        n_exist = load_edges_existing(driver, [r for r in edge_rows if not r["org_is_stub"]])
        n_stub = load_edges_stub(driver, [r for r in edge_rows if r["org_is_stub"]])
        neo4j_loader.compute_degree(driver)
    finally:
        driver.close()
    log.info(
        "boe_done",
        nodes_enriched=n_nodes,
        edges_total=n_exist + n_stub,
        edges_existing=n_exist,
        edges_via_stub=n_stub,
        skipped_missing=len(missing),
    )


if __name__ == "__main__":
    main()
