"""Load the curated SUBVENCIONES overlay (BDNS / SNPSAP public grants & guarantees).

Driven by `make subvenciones`. One `RECEIVED_GRANT` edge per curated concession:
(beneficiaria)-[:RECEIVED_GRANT]->(convocante), reading "recibió subvención/aval de".
The apoc merge identity is {source_kind:'subvencion', bdns_key} where bdns_key =
"<fecha>|<importe>|<instrumento>" — so several concessions between the same pair
coexist and re-runs are idempotent. The NIF is always an ATTRIBUTE, never a key.

Guard-rails (mirror of mercantil_loader's network pass, spec 042):
  * The beneficiary is MATCHed (never merge-created): a row whose beneficiary is not
    already a node is SKIPPED (`subvencion_skip_anchor_missing`) — the ≤1-hop gate.
  * Awarding-body stubs come from the `convocantes:` section and are merge-created
    with onCreate-only props (an existing node is never clobbered). A convocante id
    that is neither a stub nor an existing node skips the row with a warning.
  * Money view is SELECTIVE (owner decision 2026-07-11): a concession with
    `en_flujos: true` (the SEPI rescues only) gets `amount`+`currency`+`date_*` and
    therefore surfaces in /flujos & flujos_dinero; every other concession gets
    `importe` and stays out of the money view by construction.

BDNS data is official/public. Receiving a grant or a public guarantee is LEGAL and is
neither a judicial fact nor an accusation — the editorial signal is the crossing with
the case, never the aid itself. Presunción de inocencia. Idempotent + re-runnable.

Usage:
    cd pipeline && uv run python -m src.subvenciones_loader
    cd pipeline && uv run python -m src.subvenciones_loader --dir ../some/dir
"""
from __future__ import annotations

import argparse
from pathlib import Path

import structlog
import yaml

from . import config, neo4j_loader
from .schema import RelationType

log = structlog.get_logger()

SUBVENCIONES_DIR = config.WORK_DIR / "subvenciones"
SOURCE_KIND = "subvencion"
REL = RelationType.RECEIVED_GRANT.value

# Neutral, structural summary for awarding-body stubs. Receiving/granting public aid
# is legal — presunción de inocencia.
_STUB_SUMMARY = (
    "Organismo o entidad del sector público que consta como concedente de subvenciones, "
    "ayudas o avales en la Base de Datos Nacional de Subvenciones (BDNS/SNPSAP). Dato "
    "oficial público; conceder o recibir una ayuda es legal y no es indicio ni prueba de "
    "delito. Presunción de inocencia."
)


def load_files(directory: Path) -> tuple[list[dict], list[dict]]:
    """Read every *.yaml/*.yml; collect `subvenciones:` and `convocantes:` lists.
    (matches.candidate.yaml has neither key, so the recon seed is never loaded.)"""
    subs: list[dict] = []
    convs: list[dict] = []
    for f in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        s, c = doc.get("subvenciones") or [], doc.get("convocantes") or []
        if s or c:
            log.info("subvencion_file", file=f.name, beneficiarias=len(s), convocantes=len(c))
        subs.extend(s)
        convs.extend(c)
    return subs, convs


def _stub_defs(convs: list[dict]) -> dict[str, dict]:
    """Awarding-body stubs keyed by entity_id, as onCreate prop maps."""
    out: dict[str, dict] = {}
    for c in convs:
        cid = (c.get("id") or "").strip()
        name = (c.get("name") or "").strip()
        if not cid or not name:
            log.warning("subvencion_skip_bad_convocante", row=c)
            continue
        out[cid] = {
            "name": name,
            "canonical_name": name,
            "role": c.get("role"),
            "source_kind": SOURCE_KIND,
            "mentions": 0,
            "degree": 0,
            "summary": c.get("summary") or _STUB_SUMMARY,
        }
    return out


def _prepare(subs: list[dict], stubs: dict[str, dict]) -> list[dict]:
    """Flatten beneficiaries × concessions into edge rows. Validates the money-view
    invariant: `en_flujos: true` ⇒ `amount` (+date_*), else ⇒ `importe`, never both."""
    rows: list[dict] = []
    for b in subs:
        b_id = (b.get("beneficiario") or "").strip()
        if not b_id:
            log.warning("subvencion_skip_no_beneficiario", row=b)
            continue
        for c in b.get("concesiones") or []:
            fecha = str(c.get("fecha") or "").strip()
            importe = c.get("importe")
            instrumento = (c.get("instrumento") or "").strip()
            conv_id = (c.get("convocante") or "").strip()
            if not fecha or not conv_id:
                log.warning("subvencion_skip_bad_row", beneficiario=b_id, row=c)
                continue
            try:
                importe = float(importe) if importe is not None else None
            except (TypeError, ValueError):
                log.warning("subvencion_bad_importe", beneficiario=b_id, importe=importe)
                importe = None
            props = {
                "fecha": fecha,
                "instrumento": instrumento or None,
                "convocante_detalle": c.get("convocante_detalle"),
                "bdns_endpoint": c.get("bdns_endpoint"),
                "tercero_id": b.get("tercero_id"),
                "nif": b.get("nif"),
                "denominacion_bdns": b.get("denominacion_bdns"),
                "note": c.get("note") or b.get("nota"),
            }
            if c.get("en_flujos"):
                if importe is None:
                    log.warning("subvencion_skip_flujos_sin_importe", beneficiario=b_id, row=c)
                    continue
                props.update({
                    "amount": importe,
                    "currency": "EUR",
                    "date_norm": fecha,
                    "date_year": int(fecha[:4]),
                })
            else:
                props["importe"] = importe
            rows.append({
                "b_id": b_id,
                "conv_id": conv_id,
                "conv_is_stub": conv_id in stubs,
                "conv_props": stubs.get(conv_id),
                "key": f"{fecha}|{importe}|{instrumento}",
                "props": {k: v for k, v in props.items() if v is not None},
            })
    return rows


def _report_missing(driver, rows: list[dict]) -> list[str]:
    """Log beneficiary/convocante ids that are NOT nodes. A missing beneficiary is the
    ≤1-hop gate doing its job; a missing non-stub convocante is a curation error."""
    ids = {r["b_id"] for r in rows} | {r["conv_id"] for r in rows if not r["conv_is_stub"]}
    q = "UNWIND $ids AS id OPTIONAL MATCH (n {entity_id: id}) RETURN id, n IS NOT NULL AS found"
    with driver.session() as s:
        found = {rec["id"]: rec["found"] for rec in s.run(q, ids=list(ids))}
    missing = [i for i, ok in found.items() if not ok]
    for i in missing:
        log.warning("subvencion_skip_anchor_missing", entity_id=i)
    return missing


def load_existing(driver, rows: list[dict]) -> int:
    """Edges whose convocante already exists (both endpoints MATCHed; 0 creation)."""
    if not rows:
        return 0
    q = f"""
    UNWIND $rows AS row
    MATCH (a {{entity_id: row.b_id}}), (b {{entity_id: row.conv_id}})
    CALL apoc.merge.relationship(a, '{REL}',
        {{source_kind: '{SOURCE_KIND}', bdns_key: row.key}}, row.props, b, {{}}) YIELD rel
    RETURN count(rel) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("subvencion_edges_existing", rows=len(rows), merged=n)
    return n


def load_stub(driver, rows: list[dict]) -> int:
    """Create the convocante stub hung off an existing beneficiary, then the edge.
    MATCH on the beneficiary is the ≤1-hop gate; onMatch {} never clobbers a stub
    that already exists (idempotent)."""
    if not rows:
        return 0
    q = f"""
    UNWIND $rows AS row
    MATCH (a {{entity_id: row.b_id}})
    CALL apoc.merge.node(['Organization'], {{entity_id: row.conv_id}}, row.conv_props, {{}}) YIELD node AS b
    CALL apoc.merge.relationship(a, '{REL}',
        {{source_kind: '{SOURCE_KIND}', bdns_key: row.key}}, row.props, b, {{}}) YIELD rel
    RETURN count(rel) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("subvencion_edges_stub", rows=len(rows), merged=n)
    return n


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default=str(SUBVENCIONES_DIR))
    args = p.parse_args()

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("subvencion_dir_missing", dir=str(directory))
        return
    subs, convs = load_files(directory)
    stubs = _stub_defs(convs)
    rows = _prepare(subs, stubs)
    if not rows:
        log.warning("subvencion_empty", dir=str(directory))
        return

    driver = neo4j_loader.get_driver()
    try:
        missing = _report_missing(driver, rows)
        rows = [r for r in rows if r["b_id"] not in missing and r["conv_id"] not in missing]
        n_exist = load_existing(driver, [r for r in rows if not r["conv_is_stub"]])
        n_stub = load_stub(driver, [r for r in rows if r["conv_is_stub"]])
        neo4j_loader.compute_degree(driver)
    finally:
        driver.close()
    log.info(
        "subvencion_done",
        edges_total=n_exist + n_stub,
        edges_existing=n_exist,
        edges_via_stub=n_stub,
        skipped_missing=len(missing),
    )


if __name__ == "__main__":
    main()
