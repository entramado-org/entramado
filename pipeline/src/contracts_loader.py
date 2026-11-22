"""Load the curated CONTRACTS overlay (reified public-procurement awards).

A flat `PAID`/`PARTICIPATED_IN` edge cannot express "awarding body X adjudicated
contract C to company Y for amount A, object O, on date D" — yet that triangle is
the spine of these cases (mascarillas, obras ferroviarias Acciona/Adif). This
loader reifies each award as a `Contract` node tying the three roles together:

    (awarding_body) -[:AWARDED_CONTRACT]-> (Contract) -[:CONTRACT_AWARDED_TO]-> (awardee)

The Contract node carries amount/currency/object/date + provenance. Modelled on
accusations_loader: MERGE by deterministic entity_id, endpoints only ensured (never
clobbered), edges keyed `source_kind='contrato'` so they coincide cleanly with
extracted edges and are protected from the relations-overlay deny/fix (whose guard
only touches `source_kind` null/'judicial'). Idempotent + re-runnable.

The award itself is a documented fact; any alleged irregularity (amaño,
sobreprecio) is a separate accusation and is NOT asserted here — keep `note`/
`object` descriptive, cite doc + quote, respect presunción de inocencia.

NOTE — one-time live-DB migration for the new `Contract` label (no re-ingest):
this loader ensures the `contract_id` uniqueness constraint itself; to also make
contracts fuzzy-searchable, recreate the `entityNames` fulltext index to include
the Contract label (see the project memory / Makefile note).

Usage:
    cd pipeline && uv run python -m src.contracts_loader
    cd pipeline && uv run python -m src.contracts_loader --dir ../some/dir
"""
from __future__ import annotations

import argparse
from pathlib import Path

import structlog
import yaml

from . import config, neo4j_loader
from .entity_resolution import entity_id
from .schema import EntityType

log = structlog.get_logger()

CONTRACTS_DIR = config.WORK_DIR / "contracts"
SOURCE_KIND = "contrato"

_ENTITY_TYPES = {t.value for t in EntityType}


def load_files(directory: Path) -> list[dict]:
    """Read every *.yaml/*.yml; collect the top-level `contracts:` list."""
    out: list[dict] = []
    for f in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        items = doc.get("contracts") or []
        out.extend(items)
        log.info("contracts_file", file=f.name, contracts=len(items))
    return out


def _prepare(items: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for c in items:
        cname = (c.get("contract") or "").strip()
        body = (c.get("awarding_body") or "").strip()
        awardee = (c.get("awardee") or "").strip()
        body_type = (c.get("body_type") or "Organization").strip()
        awardee_type = (c.get("awardee_type") or "Company").strip()
        if not cname or not body or not awardee:
            log.warning("contract_skip_incomplete", contract=cname, body=body, awardee=awardee)
            continue
        if body_type not in _ENTITY_TYPES or awardee_type not in _ENTITY_TYPES:
            log.warning("contract_skip_bad_type", contract=cname, body_type=body_type, awardee_type=awardee_type)
            continue
        amount = c.get("amount")
        rows.append({
            "contract_id": entity_id("Contract", cname),
            "contract_name": cname,
            "object": c.get("object"),
            "amount": float(amount) if amount is not None else None,
            "currency": (c.get("currency") or "EUR") if amount is not None else None,
            "date": c.get("date"),
            "body_id": c.get("body_id") or entity_id(body_type, body),
            "body_type": body_type,
            "body_name": body,
            "awardee_id": c.get("awardee_id") or entity_id(awardee_type, awardee),
            "awardee_type": awardee_type,
            "awardee_name": awardee,
            "doc": c.get("doc"),
            "page": c.get("page"),
            "quote": (c.get("quote") or "").strip()[:300] or None,
            "note": c.get("note"),
        })
    return rows


def ensure_constraint(driver) -> None:
    """One-time idempotent migration: uniqueness constraint for the Contract label
    (setup_schema only runs during a full ingest, which we don't re-run)."""
    with driver.session() as s:
        s.run("CREATE CONSTRAINT contract_id IF NOT EXISTS "
              "FOR (n:Contract) REQUIRE n.entity_id IS UNIQUE")
    log.info("contract_constraint_ready")


def load(driver, rows: list[dict]) -> int:
    # Contract node: MERGE on entity_id, set descriptive + provenance props.
    # Body/awardee: only ensure existence (never clobber a real document figure).
    # Two edges keyed source_kind='contrato' so they're idempotent and protected.
    q = """
    UNWIND $rows AS row
    CALL apoc.merge.node(['Contract'], {entity_id: row.contract_id},
      {name: row.contract_name, canonical_name: row.contract_name, mentions: 0, degree: 0,
       source_kind: 'contrato', object: row.object, amount: row.amount, currency: row.currency,
       date: row.date, source_doc: row.doc, source_page: row.page, source_quote: row.quote},
      {object: row.object, amount: row.amount, currency: row.currency, date: row.date,
       source_doc: row.doc, source_quote: row.quote}) YIELD node AS k
    CALL apoc.merge.node([row.body_type], {entity_id: row.body_id},
      {name: row.body_name, canonical_name: row.body_name, mentions: 0, degree: 0}, {}) YIELD node AS body
    CALL apoc.merge.node([row.awardee_type], {entity_id: row.awardee_id},
      {name: row.awardee_name, canonical_name: row.awardee_name, mentions: 0, degree: 0}, {}) YIELD node AS aw
    CALL apoc.merge.relationship(body, 'AWARDED_CONTRACT', {source_kind: 'contrato'},
      {source_doc: row.doc, source_page: row.page, source_quote: row.quote,
       amount: row.amount, currency: row.currency, date: row.date, note: row.note},
      k, {}) YIELD rel AS r1
    CALL apoc.merge.relationship(k, 'CONTRACT_AWARDED_TO', {source_kind: 'contrato'},
      {source_doc: row.doc, source_page: row.page, source_quote: row.quote,
       amount: row.amount, currency: row.currency, date: row.date},
      aw, {}) YIELD rel AS r2
    RETURN count(k) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("contracts_loaded", contracts=len(rows), merged=n)
    return n


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default=str(CONTRACTS_DIR))
    args = p.parse_args()

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("contracts_dir_missing", dir=str(directory))
        return
    rows = _prepare(load_files(directory))
    if not rows:
        log.warning("contracts_empty", dir=str(directory))
        return

    driver = neo4j_loader.get_driver()
    try:
        ensure_constraint(driver)
        load(driver, rows)
        neo4j_loader.compute_degree(driver)  # contract edges count toward degree
    finally:
        driver.close()
    log.info("contracts_done")


if __name__ == "__main__":
    main()
