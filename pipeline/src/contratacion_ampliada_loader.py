"""Load the curated CONTRATACION AMPLIADA overlay (spec 055).

Sister layer of the 046 minor-contracts overlay, same source_kind='contratacion',
covering PLACSP tenders (sindicación 643), aggregated CCAA/EELL platforms (1044) and
the CCAA minor-contract sweeps (Andalucía/Euskadi/Galicia, spec 054). Driven by
`make contratacion-ampliada`. Three passes over `data/contratacion/ampliada/*.yaml`:
  * Pass 1 (`ampliada_adjudicatarias:` rows) — PROPS-ONLY: mark existing awardee
    nodes with `is_contratacion_amp` + per-ámbito aggregates in the `amp_*`
    namespace (`amp_<ambito>_n`, `amp_<ambito>_total_eur`, `amp_<ambito>_rango`,
    `amp_<ambito>_nota`) + `amp_nota`/`amp_nif`/`amp_denominacion`. MATCH-only and
    CURATED-OVERWRITE on `amp_*` (the YAML rules its own namespace, BOE-050 lesson:
    a curated correction MUST propagate on re-run; removing a key needs a manual
    strip): never creates nodes, never touches name/summary/role/source_kind,
    never touches the 046 `placsp_*` namespace.
  * Pass 2 (`organos_destacados:` per awardee) — EDGES: ONE aggregated `CONTRACTOR_OF`
    edge per (awardee, ámbito, highlighted contracting body), onMatch=props
    (curated-overwrite, see Pass 1), keyed
    {source_kind:'contratacion', placsp_key:"<ambito>|<organo_entity_id>"} with
    ambito ∈ {lic, agr, ccaa_andalucia, ccaa_euskadi, ccaa_galicia}. Tender signals
    ride as props (n_emergencia, n_negociado_sp, via_ute, presupuesto_total_eur,
    fuente). The aggregate carries `total_eur` (NEVER `amount`) and `rango` (NEVER
    date_norm/date_year), so the layer stays out of /flujos and the cronología BY
    CONSTRUCTION. The NIF is always an ATTRIBUTE, never a key. The awardee is MATCHed
    (never merge-created) — the ≤1-hop gate; body stubs come from `organos:` and are
    merge-created onCreate-only (label from the entity_id prefix).
  * Pass 3 (`ampliada_sin_adjudicaciones:` rows) — NEGATIVES: mark existing nodes with
    `amp_sin_adjudicaciones` + `amp_nota` (an informative absence: 0 DIRECT awards in
    the whole ampliada sweep). Props-only, 0 edges. The 046 `placsp_sin_menores`
    negatives are untouched.

Coexists without collision with the 046 layer (MINOR_CONTRACTOR_OF, placsp_key
"agg|…") and with the judicial reified-contracts layer (source_kind='contrato'):
edge type + source_kind + own key. Surgical removal = delete CONTRACTOR_OF edges +
strip amp_* props; the minors layer stays.

Being awarded a public tender or minor contract is LEGAL and is neither a judicial
fact nor an accusation — the editorial signal is the crossing with the case and the
procurement signals (emergency processing, negotiated without publicity, via-UTE,
top-capping), never the award itself. Presunción de inocencia.
Idempotent + re-runnable (same YAML → 0 net changes; edited YAML → the edit lands).
Key-namespace separation: this loader reads ONLY `ampliada_*` top-level keys, the 046
loader reads ONLY its own (`adjudicatarias:`/`organos:`/`sin_menores:`) — so neither
loader can ever parse the other's file, wherever it sits. The `ampliada/` subdir +
parent-dir refusal remain as belt and braces. Recon seeds are never loaded.

Usage:
    cd pipeline && uv run python -m src.contratacion_ampliada_loader
    cd pipeline && uv run python -m src.contratacion_ampliada_loader --dir ../some/dir/ampliada
"""
from __future__ import annotations

import argparse
from pathlib import Path

import structlog
import yaml

from . import config, neo4j_loader
from .schema import RelationType

log = structlog.get_logger()

AMPLIADA_DIR = config.WORK_DIR / "contratacion" / "ampliada"
SOURCE_KIND = "contratacion"
REL = RelationType.CONTRACTOR_OF.value
AMBITOS = ("lic", "agr", "ccaa_andalucia", "ccaa_euskadi", "ccaa_galicia")

# Neutral, structural summary for contracting-body stubs. Awarding/receiving public
# contracts is legal — presunción de inocencia.
_STUB_SUMMARY = (
    "Órgano o ente del sector público que consta como adjudicador de contratación "
    "pública en los registros oficiales (PLACSP, plataformas agregadas o plataformas "
    "autonómicas). Dato oficial público; adjudicar o recibir un contrato es legal y no "
    "es indicio ni prueba de delito. Presunción de inocencia."
)

# Per-ámbito node props (curated-overwrite, own amp_* namespace).
_AMBITO_PROPS = ("n", "total_eur", "rango", "nota")
# Edge props copied verbatim from each organos_destacados row when present.
_EDGE_PROPS = ("n_contratos", "total_eur", "rango", "n_emergencia", "n_negociado_sp",
               "presupuesto_total_eur", "via_ute", "organo_detalle", "fuente",
               "ejemplo_link", "ejemplo_titulo", "note")
_FORBIDDEN = {"amount", "en_flujos", "date_norm", "date_year"}


def load_files(directory: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """Read every *.yaml/*.yml in the ampliada subdir; collect the `ampliada_*`
    top-level keys (namespace propio — ni este loader puede parsear un fichero 046
    ni el 046 puede parsear este). Refuses the parent contratacion/ dir as belt."""
    if directory.name != "ampliada":
        raise SystemExit(
            f"contratacion_ampliada_loader: --dir must point at an 'ampliada/' subdir "
            f"(got {directory}) — the parent contratacion/ dir belongs to the 046 loader.")
    adjs: list[dict] = []
    orgs: list[dict] = []
    negs: list[dict] = []
    for f in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        a = doc.get("ampliada_adjudicatarias") or []
        o = doc.get("ampliada_organos") or []
        n = doc.get("ampliada_sin_adjudicaciones") or []
        if a or o or n:
            log.info("ampliada_file", file=f.name, adjudicatarias=len(a),
                     organos=len(o), sin_adjudicaciones=len(n))
        adjs.extend(a)
        orgs.extend(o)
        negs.extend(n)
    return adjs, orgs, negs


def _label_for(entity_id: str) -> str | None:
    """Stub label from the entity_id prefix (Organization:/Company:/Court:…).
    Unknown prefix → None (caller warns and SKIPS: a typo'd prefix must fail loud,
    never silently mint an Organization with a mis-keyed entity_id)."""
    prefix = entity_id.split(":", 1)[0]
    return prefix if prefix in ("Organization", "Company", "Court") else None


def _stub_defs(orgs: list[dict]) -> dict[str, dict]:
    """Contracting-body stubs keyed by entity_id, as onCreate prop maps."""
    out: dict[str, dict] = {}
    for o in orgs:
        oid = (o.get("id") or "").strip()
        name = (o.get("name") or "").strip()
        if not oid or not name:
            log.warning("ampliada_skip_bad_organo", row=o)
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
        log.warning(f"ampliada_bad_{kind}", ctx=ctx, value=value)
        return None


def _prepare_nodes(adjs: list[dict]) -> list[dict]:
    """Props-only rows: flatten `ambitos:` into flat amp_<ambito>_* prop maps."""
    rows: list[dict] = []
    for a in adjs:
        a_id = (a.get("adjudicataria") or "").strip()
        if not a_id:
            log.warning("ampliada_skip_no_adjudicataria", row=a)
            continue
        props: dict = {
            "amp_nota": a.get("nota"),
            "amp_nif": a.get("nif"),
            "amp_denominacion": a.get("denominacion"),
        }
        for ambito, vals in (a.get("ambitos") or {}).items():
            if ambito not in AMBITOS:
                log.warning("ampliada_skip_bad_ambito", adjudicataria=a_id, ambito=ambito)
                continue
            if not isinstance(vals, dict):
                log.warning("ampliada_skip_bad_ambito_vals", adjudicataria=a_id, ambito=ambito)
                continue
            for key in _AMBITO_PROPS:
                if vals.get(key) is None:
                    continue
                v = vals[key]
                if key == "total_eur":
                    v = _num(v, kind="total", ctx=f"{a_id}.{ambito}")
                props[f"amp_{ambito}_{key}"] = v
        rows.append({"entity_id": a_id,
                     "props": {k: v for k, v in props.items() if v is not None}})
    return rows


def _prepare_edges(adjs: list[dict], stubs: dict[str, dict]) -> list[dict]:
    """Flatten awardees × organos_destacados into aggregated edge rows. Forbidden
    money/chronology props are rejected: the layer stays out of /flujos and the
    cronología by construction."""
    rows: list[dict] = []
    for a in adjs:
        a_id = (a.get("adjudicataria") or "").strip()
        if not a_id:
            continue
        for od in a.get("organos_destacados") or []:
            org_id = (od.get("organo") or "").strip()
            ambito = (od.get("ambito") or "").strip()
            if not org_id or ambito not in AMBITOS:
                log.warning("ampliada_skip_bad_organo_row", adjudicataria=a_id, row=od)
                continue
            forbidden = _FORBIDDEN & set(od)
            if forbidden:
                log.warning("ampliada_skip_forbidden_props", adjudicataria=a_id,
                            organo=org_id, props=sorted(forbidden))
                continue
            props = {k: od.get(k) for k in _EDGE_PROPS}
            props["total_eur"] = _num(od.get("total_eur"), kind="total",
                                      ctx=f"{a_id}->{org_id}")
            props["presupuesto_total_eur"] = _num(od.get("presupuesto_total_eur"),
                                                  kind="presupuesto",
                                                  ctx=f"{a_id}->{org_id}")
            props["ambito"] = ambito
            props["nif"] = a.get("nif")
            props["denominacion"] = a.get("denominacion")
            org_label = _label_for(org_id)
            if org_label is None:
                log.warning("ampliada_skip_bad_organo_prefix", adjudicataria=a_id,
                            organo=org_id)
                continue
            rows.append({
                "a_id": a_id,
                "org_id": org_id,
                "org_label": org_label,
                "org_is_stub": org_id in stubs,
                "org_props": stubs.get(org_id),
                "key": f"{ambito}|{org_id}",
                "props": {k: v for k, v in props.items() if v is not None},
            })
    return rows


def _prepare_negatives(negs: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for n in negs:
        n_id = (n.get("entity_id") or "").strip()
        if not n_id:
            log.warning("ampliada_skip_bad_negativo", row=n)
            continue
        rows.append({"entity_id": n_id, "nota": n.get("nota")})
    return rows


def _report_missing(driver, ids: set[str]) -> set[str]:
    """Log anchor ids that are NOT nodes. A missing awardee/negative is the ≤1-hop
    gate doing its job; a missing non-stub organo is a curation error."""
    q = "UNWIND $ids AS id OPTIONAL MATCH (n {entity_id: id}) RETURN id, n IS NOT NULL AS found"
    with driver.session() as s:
        found = {rec["id"]: rec["found"] for rec in s.run(q, ids=list(ids))}
    missing = {i for i, ok in found.items() if not ok}
    for i in sorted(missing):
        log.warning("ampliada_skip_anchor_missing", entity_id=i)
    return missing


def load_node_props(driver, rows: list[dict]) -> int:
    """Pass 1: MATCH awardee, SET is_contratacion_amp + amp_* curated-overwrite
    (`n += props` — the YAML rules its own namespace; rows carry ONLY amp_* keys by
    construction of _prepare_nodes). 0 creation."""
    if not rows:
        return 0
    q = """
    UNWIND $rows AS row
    MATCH (n {entity_id: row.entity_id})
    SET n.is_contratacion_amp = true,
        n += row.props
    RETURN count(n) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("ampliada_nodes", rows=len(rows), enriched=n)
    return n


def load_edges_existing(driver, rows: list[dict]) -> int:
    """Pass 2a: edges whose organo already exists (both endpoints MATCHed; 0 creation)."""
    if not rows:
        return 0
    q = f"""
    UNWIND $rows AS row
    MATCH (a {{entity_id: row.a_id}}), (b {{entity_id: row.org_id}})
    CALL apoc.merge.relationship(a, '{REL}',
        {{source_kind: '{SOURCE_KIND}', placsp_key: row.key}}, row.props, b, row.props) YIELD rel
    RETURN count(rel) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("ampliada_edges_existing", rows=len(rows), merged=n)
    return n


def load_edges_stub(driver, rows: list[dict]) -> int:
    """Pass 2b: create the organo stub hung off an existing awardee, then the edge.
    MATCH on the awardee is the ≤1-hop gate; onMatch {} never clobbers."""
    if not rows:
        return 0
    q = f"""
    UNWIND $rows AS row
    MATCH (a {{entity_id: row.a_id}})
    CALL apoc.merge.node([row.org_label], {{entity_id: row.org_id}}, row.org_props, {{}}) YIELD node AS b
    CALL apoc.merge.relationship(a, '{REL}',
        {{source_kind: '{SOURCE_KIND}', placsp_key: row.key}}, row.props, b, row.props) YIELD rel
    RETURN count(rel) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("ampliada_edges_stub", rows=len(rows), merged=n)
    return n


def load_negatives(driver, rows: list[dict]) -> int:
    """Pass 3: MATCH node, SET amp_sin_adjudicaciones + amp_nota (curated-overwrite:
    the YAML nota wins). 0 creation, 0 edges; 046 placsp_sin_menores untouched."""
    if not rows:
        return 0
    q = """
    UNWIND $rows AS row
    MATCH (n {entity_id: row.entity_id})
    SET n.amp_sin_adjudicaciones = true,
        n.amp_nota = coalesce(row.nota, n.amp_nota)
    RETURN count(n) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("ampliada_negatives", rows=len(rows), marked=n)
    return n


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", default=str(AMPLIADA_DIR))
    args = p.parse_args()

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("ampliada_dir_missing", dir=str(directory))
        raise SystemExit(1)
    adjs, orgs, negs = load_files(directory)
    stubs = _stub_defs(orgs)
    node_rows = _prepare_nodes(adjs)
    edge_rows = _prepare_edges(adjs, stubs)
    neg_rows = _prepare_negatives(negs)
    if not node_rows and not edge_rows and not neg_rows:
        log.error("ampliada_empty", dir=str(directory))
        raise SystemExit(1)

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
        "ampliada_done",
        nodes_enriched=n_nodes,
        edges_total=n_exist + n_stub,
        edges_existing=n_exist,
        edges_via_stub=n_stub,
        negatives=n_negs,
        skipped_missing=len(missing),
    )


if __name__ == "__main__":
    main()
