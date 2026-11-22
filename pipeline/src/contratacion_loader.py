"""Load the curated CONTRATACION overlay (PLACSP minor public contracts, spec 046).

Driven by `make contratacion`. Three passes over `data/contratacion/contratacion.yaml`:
  * Pass 1 (`adjudicatarias:` rows) — PROPS-ONLY: mark existing awardee nodes with
    `is_contratacion` + the global aggregate (`placsp_n_contratos`, `placsp_total_eur`,
    `placsp_rango`, `placsp_organos_top`, `placsp_nota`). MATCH-only, fill-only on the
    `placsp_*` namespace: never creates nodes, never touches name/summary/role/source_kind.
  * Pass 2 (`organos_destacados:` per awardee) — EDGES: ONE aggregated
    `MINOR_CONTRACTOR_OF` edge per (awardee, highlighted contracting body), keyed
    {source_kind:'contratacion', placsp_key:"agg|<organo_entity_id>"}. The awardee is
    MATCHed (never merge-created) — the ≤1-hop gate; body stubs come from `organos:` and
    are merge-created onCreate-only. The aggregate carries `total_eur` (NEVER `amount`)
    and `rango` (NEVER date_norm/date_year), so the layer stays out of /flujos and the
    cronología BY CONSTRUCTION. The NIF is always an ATTRIBUTE, never a key.
  * Pass 3 (`sin_menores:` rows) — NEGATIVES: mark existing nodes with
    `placsp_sin_menores` + `placsp_nota` (an informative absence: 0 minor contracts in
    the sweep window). Props-only, 0 edges.

Distinct from the judicial reified contracts layer (contracts_loader,
source_kind='contrato', Contract nodes): both layers coexist on the same pair without
collision thanks to the edge type + source_kind key.

PLACSP data is official/public. Being awarded a minor contract is LEGAL and is neither
a judicial fact nor an accusation — the editorial signal is the crossing with the case
(and the consequence), never the contract itself. Presunción de inocencia.
Idempotent + re-runnable. (matches.candidate.yaml has none of the three keys, so the
recon seed is never loaded.)

Usage:
    cd pipeline && uv run python -m src.contratacion_loader
    cd pipeline && uv run python -m src.contratacion_loader --dir ../some/dir
"""
from __future__ import annotations

import argparse
from pathlib import Path

import structlog
import yaml

from . import config, neo4j_loader
from .schema import RelationType

log = structlog.get_logger()

CONTRATACION_DIR = config.WORK_DIR / "contratacion"
SOURCE_KIND = "contratacion"
REL = RelationType.MINOR_CONTRACTOR_OF.value

# Neutral, structural summary for contracting-body stubs. Awarding/receiving a minor
# contract is legal — presunción de inocencia.
_STUB_SUMMARY = (
    "Órgano de contratación del sector público que consta como adjudicador de contratos "
    "menores en la Plataforma de Contratación del Sector Público (PLACSP). Dato oficial "
    "público; adjudicar o recibir un contrato menor es legal y no es indicio ni prueba "
    "de delito. Presunción de inocencia."
)

# Node props written by the props-only passes (fill-only, own namespace).
_NODE_PROPS = {
    "placsp_n_contratos": "n_contratos",
    "placsp_total_eur": "total_eur",
    "placsp_rango": "rango",
    "placsp_organos_top": "organos_top",
    "placsp_nota": "nota",
    "placsp_denominacion": "denominacion_placsp",
    "placsp_nif": "nif",
}


def load_files(directory: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """Read every *.yaml/*.yml; collect `adjudicatarias:`, `organos:` and `sin_menores:`.
    (matches.candidate.yaml has none of these keys, so the recon seed is never loaded.)"""
    adjs: list[dict] = []
    orgs: list[dict] = []
    negs: list[dict] = []
    for f in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        a = doc.get("adjudicatarias") or []
        o = doc.get("organos") or []
        n = doc.get("sin_menores") or []
        if a or o or n:
            log.info("contratacion_file", file=f.name, adjudicatarias=len(a),
                     organos=len(o), sin_menores=len(n))
        adjs.extend(a)
        orgs.extend(o)
        negs.extend(n)
    return adjs, orgs, negs


def _stub_defs(orgs: list[dict]) -> dict[str, dict]:
    """Contracting-body stubs keyed by entity_id, as onCreate prop maps."""
    out: dict[str, dict] = {}
    for o in orgs:
        oid = (o.get("id") or "").strip()
        name = (o.get("name") or "").strip()
        if not oid or not name:
            log.warning("contratacion_skip_bad_organo", row=o)
            continue
        out[oid] = {
            "name": name,
            "canonical_name": name,
            "role": o.get("role"),
            "source_kind": SOURCE_KIND,
            "mentions": 0,
            "degree": 0,
            "summary": o.get("summary") or _STUB_SUMMARY,
        }
    return out


def _num(value, *, kind: str, ctx: str):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        log.warning(f"contratacion_bad_{kind}", ctx=ctx, value=value)
        return None


def _prepare_nodes(adjs: list[dict]) -> list[dict]:
    """Props-only rows for the awardees (global aggregate)."""
    rows: list[dict] = []
    for a in adjs:
        a_id = (a.get("adjudicataria") or "").strip()
        if not a_id:
            log.warning("contratacion_skip_no_adjudicataria", row=a)
            continue
        rows.append({
            "entity_id": a_id,
            "n_contratos": a.get("n_contratos"),
            "total_eur": _num(a.get("total_eur"), kind="total", ctx=a_id),
            "rango": a.get("rango"),
            "organos_top": a.get("organos_top"),
            "nota": a.get("nota"),
            "denominacion_placsp": a.get("denominacion_placsp"),
            "nif": a.get("nif"),
        })
    return rows


def _prepare_edges(adjs: list[dict], stubs: dict[str, dict]) -> list[dict]:
    """Flatten awardees × organos_destacados into aggregated edge rows. Forbidden money/
    chronology props (`amount`, `en_flujos`, `date_norm`, `date_year`) are rejected: the
    layer stays out of /flujos and cronología by construction."""
    rows: list[dict] = []
    for a in adjs:
        a_id = (a.get("adjudicataria") or "").strip()
        if not a_id:
            continue
        for od in a.get("organos_destacados") or []:
            org_id = (od.get("organo") or "").strip()
            if not org_id:
                log.warning("contratacion_skip_bad_organo_row", adjudicataria=a_id, row=od)
                continue
            forbidden = {"amount", "en_flujos", "date_norm", "date_year"} & set(od)
            if forbidden:
                log.warning("contratacion_skip_forbidden_props", adjudicataria=a_id,
                            organo=org_id, props=sorted(forbidden))
                continue
            props = {
                "n_contratos": od.get("n_contratos"),
                "total_eur": _num(od.get("total_eur"), kind="total", ctx=f"{a_id}->{org_id}"),
                "rango": od.get("rango"),
                "organo_detalle": od.get("organo_detalle"),
                "via_ute": od.get("via_ute"),
                "ejemplo_link": od.get("ejemplo_link"),
                "ejemplo_titulo": od.get("ejemplo_titulo"),
                "nif": a.get("nif"),
                "denominacion_placsp": a.get("denominacion_placsp"),
                "note": od.get("note"),
            }
            rows.append({
                "a_id": a_id,
                "org_id": org_id,
                "org_is_stub": org_id in stubs,
                "org_props": stubs.get(org_id),
                "key": f"agg|{org_id}",
                "props": {k: v for k, v in props.items() if v is not None},
            })
    return rows


def _prepare_negatives(negs: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for n in negs:
        n_id = (n.get("entity_id") or "").strip()
        if not n_id:
            log.warning("contratacion_skip_bad_negativo", row=n)
            continue
        rows.append({"entity_id": n_id, "nota": n.get("nota")})
    return rows


def _report_missing(driver, ids: set[str]) -> set[str]:
    """Log anchor ids that are NOT nodes. A missing awardee/negative is the ≤1-hop gate
    doing its job; a missing non-stub organo is a curation error."""
    q = "UNWIND $ids AS id OPTIONAL MATCH (n {entity_id: id}) RETURN id, n IS NOT NULL AS found"
    with driver.session() as s:
        found = {rec["id"]: rec["found"] for rec in s.run(q, ids=list(ids))}
    missing = {i for i, ok in found.items() if not ok}
    for i in sorted(missing):
        log.warning("contratacion_skip_anchor_missing", entity_id=i)
    return missing


def load_node_props(driver, rows: list[dict]) -> int:
    """Pass 1: MATCH awardee, SET is_contratacion + placsp_* fill-only. 0 creation."""
    if not rows:
        return 0
    sets = ",\n        ".join(
        f"n.{prop} = coalesce(n.{prop}, row.{key})" for prop, key in _NODE_PROPS.items()
    )
    q = f"""
    UNWIND $rows AS row
    MATCH (n {{entity_id: row.entity_id}})
    SET n.is_contratacion = true,
        {sets}
    RETURN count(n) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("contratacion_nodes", rows=len(rows), enriched=n)
    return n


def load_edges_existing(driver, rows: list[dict]) -> int:
    """Pass 2a: edges whose organo already exists (both endpoints MATCHed; 0 creation)."""
    if not rows:
        return 0
    q = f"""
    UNWIND $rows AS row
    MATCH (a {{entity_id: row.a_id}}), (b {{entity_id: row.org_id}})
    CALL apoc.merge.relationship(a, '{REL}',
        {{source_kind: '{SOURCE_KIND}', placsp_key: row.key}}, row.props, b, {{}}) YIELD rel
    RETURN count(rel) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("contratacion_edges_existing", rows=len(rows), merged=n)
    return n


def load_edges_stub(driver, rows: list[dict]) -> int:
    """Pass 2b: create the organo stub hung off an existing awardee, then the edge.
    MATCH on the awardee is the ≤1-hop gate; onMatch {} never clobbers."""
    if not rows:
        return 0
    q = f"""
    UNWIND $rows AS row
    MATCH (a {{entity_id: row.a_id}})
    CALL apoc.merge.node(['Organization'], {{entity_id: row.org_id}}, row.org_props, {{}}) YIELD node AS b
    CALL apoc.merge.relationship(a, '{REL}',
        {{source_kind: '{SOURCE_KIND}', placsp_key: row.key}}, row.props, b, {{}}) YIELD rel
    RETURN count(rel) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("contratacion_edges_stub", rows=len(rows), merged=n)
    return n


def load_negatives(driver, rows: list[dict]) -> int:
    """Pass 3: MATCH node, SET placsp_sin_menores + nota fill-only. 0 creation, 0 edges."""
    if not rows:
        return 0
    q = """
    UNWIND $rows AS row
    MATCH (n {entity_id: row.entity_id})
    SET n.placsp_sin_menores = true,
        n.placsp_nota = coalesce(n.placsp_nota, row.nota)
    RETURN count(n) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("contratacion_negatives", rows=len(rows), marked=n)
    return n


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", default=str(CONTRATACION_DIR))
    args = p.parse_args()

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("contratacion_dir_missing", dir=str(directory))
        return
    adjs, orgs, negs = load_files(directory)
    stubs = _stub_defs(orgs)
    node_rows = _prepare_nodes(adjs)
    edge_rows = _prepare_edges(adjs, stubs)
    neg_rows = _prepare_negatives(negs)
    if not node_rows and not edge_rows and not neg_rows:
        log.warning("contratacion_empty", dir=str(directory))
        return

    driver = neo4j_loader.get_driver()
    try:
        anchor_ids = ({r["entity_id"] for r in node_rows}
                      | {r["a_id"] for r in edge_rows}
                      | {r["org_id"] for r in edge_rows if not r["org_is_stub"]}
                      | {r["entity_id"] for r in neg_rows})
        missing = _report_missing(driver, anchor_ids)
        node_rows = [r for r in node_rows if r["entity_id"] not in missing]
        neg_rows = [r for r in neg_rows if r["entity_id"] not in missing]
        edge_rows = [r for r in edge_rows
                     if r["a_id"] not in missing and r["org_id"] not in missing]
        n_nodes = load_node_props(driver, node_rows)
        n_exist = load_edges_existing(driver, [r for r in edge_rows if not r["org_is_stub"]])
        n_stub = load_edges_stub(driver, [r for r in edge_rows if r["org_is_stub"]])
        n_negs = load_negatives(driver, neg_rows)
        neo4j_loader.compute_degree(driver)
    finally:
        driver.close()
    log.info(
        "contratacion_done",
        nodes_enriched=n_nodes,
        edges_total=n_exist + n_stub,
        edges_existing=n_exist,
        edges_via_stub=n_stub,
        negatives=n_negs,
        skipped_missing=len(missing),
    )


if __name__ == "__main__":
    main()
