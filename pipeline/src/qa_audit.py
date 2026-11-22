"""GATED human apply of a reviewable Cypher migration (`make qa-migrate FILE=… CONFIRM=1`).

What is left of the spec 016/052 graph-audit loop: its enqueue/export/apply/report commands and
the verifier/arbiter swarm were retired by spec 069 F5 (superseded by the 063 audit). Path A
and the `gen_*_migration` scripts still emit `.cypher` files that a human applies here, in ONE
transaction. The statement splitter is a naive split on `;` — a migration whose string literals
contain `;` goes through cypher-shell instead (see `_parra_prod.sh apply`).

The historical ledger (`data/audit/ledger.jsonl`) stays as data: applying (or rolling back) a
`qa_migration_<run>.cypher` of that loop still flips its units, so re-reading it stays consistent.

Usage:
    cd pipeline && uv run python -m src.qa_audit migrate --file data/audit/patches/<file>.cypher [--confirm]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import config, neo4j_loader

# ── paths (under data/audit/, which `make clean` does NOT wipe) ─────────────────
AUDIT_DIR = config.WORK_DIR / "audit"
LEDGER_PATH = AUDIT_DIR / "ledger.jsonl"


# ── ledger I/O (JSONL, upsert by compaction) ────────────────────────────────────

def _read_ledger() -> dict[str, dict]:
    if not LEDGER_PATH.exists():
        return {}
    out: dict[str, dict] = {}
    for line in LEDGER_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        out[row["unit_id"]] = row
    return out


def _write_ledger(rows: dict[str, dict]) -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows.values(), key=lambda r: r.get("priority", 0.0), reverse=True)
    LEDGER_PATH.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in ordered) + "\n")


# ── migrate (GATED human apply) ─────────────────────────────────────────────────

def migrate(file: Path, confirm: bool) -> None:
    if not file.exists():
        print(f"✗ No existe {file}"); return
    statements = [s.strip() for s in _strip_comments(file.read_text()).split(";") if s.strip()]
    if not confirm:
        print(f"DRY-RUN — {len(statements)} sentencia(s) en {file}. NADA se aplica sin CONFIRM=1.\n")
        for s in statements:
            print(s + ";\n")
        return
    driver = neo4j_loader.get_driver()
    try:
        with driver.session() as sess:
            with sess.begin_transaction() as tx:   # one transaction; all-or-nothing
                for s in statements:
                    tx.run(s)
                tx.commit()
        print(f"\n✔ Migración aplicada en UNA transacción ({len(statements)} sentencias).")
        n_led = _mark_migrated(file)
        if n_led:
            print(f"  Ledger actualizado: {n_led} unidad(es) → "
                  f"{'patch-propuesto (rollback)' if file.name.endswith('.rollback.cypher') else 'aplicado'}.")
        rbk = file.with_suffix(".rollback.cypher") if not file.name.endswith(".rollback.cypher") \
            else file
        if rbk.exists():
            print(f"  Rollback disponible: make qa-migrate FILE={rbk} CONFIRM=1")
    finally:
        driver.close()


def _mark_migrated(file: Path) -> int:
    """Reflect a just-applied migration in the ledger so re-runs stay idempotent.
    `qa_migration_<run>.cypher` marks that run's patch-propuesto units as `aplicado`;
    the matching `.rollback.cypher` reverts them to `patch-propuesto`. Matched by
    `audit_run` parsed from the filename — no Neo4j round-trip needed. El run-id es fecha +
    sufijo alfanumérico: sirve tanto al `<fecha>-<HHMMSS>` del reloj como al `<fecha>-<L03>`
    que fija el bucle de cobertura (la regex vieja exigía 6 dígitos, así que las migraciones
    de las rondas del bucle no flipeaban el ledger)."""
    m = re.search(r"qa_migration_(\d{8}-[A-Za-z0-9]+?)(?:\.rollback)?\.cypher$", file.name)
    if not m:
        return 0
    run = m.group(1)
    rolling_back = file.name.endswith(".rollback.cypher")
    src_status = "aplicado" if rolling_back else "patch-propuesto"
    dst_status = "patch-propuesto" if rolling_back else "aplicado"
    ledger = _read_ledger()
    n = 0
    for row in ledger.values():
        if row.get("audit_run") == run and row.get("status") == src_status:
            row["status"] = dst_status
            if dst_status == "aplicado":
                row["applied_migration"] = file.name
            n += 1
    if n:
        _write_ledger(ledger)
    return n


def _strip_comments(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))


# ── CLI ──────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="GATED human apply of a reviewable Cypher migration.")
    sub = p.add_subparsers(dest="cmd", required=True)
    mg = sub.add_parser("migrate", help="GATED: apply a migration file in one transaction")
    mg.add_argument("--file", required=True)
    mg.add_argument("--confirm", action="store_true", help="actually execute (default: dry-run)")
    a = p.parse_args()
    migrate(Path(a.file), a.confirm)


if __name__ == "__main__":
    main()
