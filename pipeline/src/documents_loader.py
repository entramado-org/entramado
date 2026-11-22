"""Annotate Document nodes with a curated `doc_type` (evidence-weight metadata).

The corpus mixes judicial autos, UCO/police reports, parliamentary transcripts,
prosecutorial briefs and audits. Their evidentiary weight differs a lot — a fact
asserted in a Tribunal Supremo auto is not a debate phrase from a Diario de
Sesiones. This loader promotes that distinction (today only implicit in
`cleanup.py:TRANSCRIPT_DOCS`) to an explicit, queryable `Document.doc_type`
property, so the chat/RAG can flag transcript-sourced chunks as debate-not-proof
and the UI can show the source type per citation.

Pure metadata: it only `SET`s `d.doc_type` (and optional `court`/`date`) on the
existing Document nodes; it never creates nodes or touches entity-entity edges.
Idempotent and re-runnable.

doc_type vocabulary:
  auto_judicial | informe_uco | transcripcion_parlamentaria |
  escrito_acusacion | auditoria | otros

Usage:
    cd pipeline && uv run python -m src.documents_loader
    cd pipeline && uv run python -m src.documents_loader --dir ../some/dir
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import structlog
import yaml

from . import config, neo4j_loader

log = structlog.get_logger()

DOCUMENTS_DIR = config.WORK_DIR / "documents"

_DOC_TYPES = {
    "auto_judicial", "informe_uco", "transcripcion_parlamentaria",
    "escrito_acusacion", "auditoria", "otros",
}

# Many doc_ids embed the document's date in the filename. We extract only FULL
# dates (never bare years — those would sort imprecisely as "recientes"), and
# normalize to ISO YYYY-MM-DD. An explicit `date:` in the YAML always wins.
_DATE_PATTERNS = [
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), ("y", "m", "d")),   # 2025-06-30
    (re.compile(r"(?<!\d)(\d{2})[.\-/](\d{2})[.\-/](\d{4})(?!\d)"), ("d", "m", "y")),  # 11.04.2026
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"), ("y", "m", "d")),     # 20240819
]


def _date_from_doc_id(doc_id: str) -> str | None:
    """Best-effort ISO date pulled from the filename; None if none/invalid."""
    for rx, order in _DATE_PATTERNS:
        m = rx.search(doc_id)
        if not m:
            continue
        parts = dict(zip(order, m.groups()))
        y, mo, da = int(parts["y"]), int(parts["m"]), int(parts["d"])
        if 1990 <= y <= 2099 and 1 <= mo <= 12 and 1 <= da <= 31:
            return f"{y:04d}-{mo:02d}-{da:02d}"
    return None


def sentencia_firme_map(directory: Path | None = None) -> dict[str, bool]:
    """doc_id → firmeza declarada (`sentencia_firme: true|false`) para las SENTENCIAS
    del corpus (spec 059). Única fuente de verdad de la firmeza: la usan este loader
    (aristas CONVICTED_OF extraídas) y accusations_loader (capa curada)."""
    directory = directory or DOCUMENTS_DIR
    out: dict[str, bool] = {}
    for d in load_files(directory):
        did = (d.get("doc_id") or "").strip()
        if did and "sentencia_firme" in d:
            # `bool("false")` es True: una firmeza citada como texto publicaría como
            # firme una condena recurrida. Fail-closed.
            if not isinstance(d["sentencia_firme"], bool):
                raise SystemExit(
                    f"documents[{did}]: sentencia_firme debe ser un booleano YAML (true/false), "
                    f"no {d['sentencia_firme']!r} — spec 059")
            out[did] = bool(d["sentencia_firme"])
    return out


def load_files(directory: Path) -> list[dict]:
    """Read every *.yaml/*.yml; collect the top-level `documents:` list."""
    out: list[dict] = []
    for f in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        items = doc.get("documents") or []
        out.extend(items)
        log.info("documents_file", file=f.name, docs=len(items))
    return out


def transcript_doc_ids(directory: Path | None = None, include_curated: bool = False) -> set[str]:
    """doc_ids whose curated doc_type is a parliamentary transcript (Diario de
    Sesiones). Source of truth = documents/*.yaml, so this can never drift from the
    doc_type annotation the way the old hardcoded `cleanup.TRANSCRIPT_DOCS` did
    (it listed only 3 of 5 — see findings/aristas-transcripciones-parlamentarias).

    Entries flagged `curated_edges: true` are EXCLUDED by default: their edges did
    not come from the old bulk extraction that Option D retired, but from a
    human-gated review (extract → REVIEW-CURATED → owner verdict → scoped apply,
    e.g. DS_C_15_727/744/745, 2026-07-14) that keeps only first-person admissions.
    cleanup's transcript-edge purge and orphan sweep must not undo that curation.
    Pass include_curated=True to get every transcript regardless."""
    directory = directory or DOCUMENTS_DIR
    return {
        did
        for d in load_files(directory)
        if (d.get("doc_type") or "").strip() == "transcripcion_parlamentaria"
        and (include_curated or not d.get("curated_edges"))
        and (did := (d.get("doc_id") or "").strip())
    }


def _prepare(items: list[dict]) -> list[dict]:
    rows: list[dict] = []
    derived = 0
    for d in items:
        doc_id = (d.get("doc_id") or "").strip()
        dtype = (d.get("doc_type") or "").strip()
        if not doc_id:
            log.warning("document_skip_no_id", entry=d)
            continue
        if dtype not in _DOC_TYPES:
            log.warning("document_skip_bad_type", doc_id=doc_id, doc_type=dtype)
            continue
        # Explicit YAML date wins; otherwise try to read it off the filename.
        date = d.get("date") or _date_from_doc_id(doc_id)
        if not d.get("date") and date:
            derived += 1
        rows.append({
            "doc_id": doc_id, "doc_type": dtype,
            "court": d.get("court"), "date": date,
        })
    if derived:
        log.info("documents_dates_from_filename", derived=derived)
    return rows


def load(driver, rows: list[dict]) -> int:
    # MATCH-only: never create a Document, only annotate an existing one (so a
    # typo'd doc_id is a no-op rather than a phantom node).
    q = """
    UNWIND $rows AS row
    MATCH (d:Document {doc_id: row.doc_id})
    SET d.doc_type = row.doc_type,
        d.court = coalesce(row.court, d.court),
        d.doc_date = coalesce(row.date, d.doc_date)
    RETURN count(d) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("documents_annotated", requested=len(rows), matched=n)
    return n


def load_firmeza(driver, firme: dict[str, bool]) -> int:
    """Spec 059 (fail-closed): every CONVICTED_OF edge sourced from a sentencing
    document carries `firme` and a `status` that spells the difference —
    'condenado' (firme) vs 'condenado_no_firme' (recurrida / recurrible). Edges of
    documents WITHOUT a declared `sentencia_firme` are left untouched: the API then
    renders them as no firme (coalesce → 'condenado_no_firme'), never as firme."""
    rows = [{"doc_id": d, "firme": f, "status": "condenado" if f else "condenado_no_firme"}
            for d, f in firme.items()]
    q = """
    UNWIND $rows AS row
    MATCH ()-[r:CONVICTED_OF]->()
    WHERE r.source_doc = row.doc_id
    SET r.firme = row.firme, r.status = row.status
    RETURN count(r) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("documents_firmeza", docs=len(rows), edges=n)
    return n


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default=str(DOCUMENTS_DIR))
    args = p.parse_args()

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("documents_dir_missing", dir=str(directory))
        return
    rows = _prepare(load_files(directory))
    if not rows:
        log.warning("documents_empty", dir=str(directory))
        return

    driver = neo4j_loader.get_driver()
    try:
        load(driver, rows)
        load_firmeza(driver, sentencia_firme_map(directory))
    finally:
        driver.close()
    log.info("documents_done")


if __name__ == "__main__":
    main()
