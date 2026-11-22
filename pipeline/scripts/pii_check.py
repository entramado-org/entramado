"""Gate PII (spec 059, regla 5 de CONTRIBUTING): ningún dato personal identificador
(IBAN, DNI/NIE, teléfono, e-mail personal, JID de WhatsApp, matrícula) puede viajar
en las capas versionadas ni en nombres/alias de nodos del grafo.

Usage (from pipeline/):
    uv run python -m scripts.pii_check                 # capas YAML/TS del repo
    uv run python -m scripts.pii_check --graph         # + nombres/alias del grafo (NEO4J_URI)
Exit 1 si hay hallazgos fuera de data/pii_allowlist.yaml (cada excepción, justificada).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PIPE = REPO / "pipeline"
ALLOWLIST = PIPE / "data" / "pii_allowlist.yaml"

SCAN_GLOBS = [
    "pipeline/data/context/*.yaml", "pipeline/data/relations/*.yaml",
    "pipeline/data/accusations/*.yaml", "pipeline/data/ultima_hora/*.yaml",
    "pipeline/data/patrones/*.yaml", "pipeline/data/prensa/*.yaml",
    "pipeline/data/documents/*.yaml", "pipeline/data/contracts/*.yaml",
    "pipeline/data/claims/*.yaml", "pipeline/data/mercantil/*.yaml",
    "pipeline/data/bdns/*.yaml", "pipeline/data/placsp/*.yaml", "pipeline/data/boe/*.yaml",
    "api/app/data/*.yaml", "web/lib/*.ts",
]

PATTERNS = {
    "iban": re.compile(r"\bES\d{2}(?:[ -]?\d{4}){5}\b"),
    "dni": re.compile(r"(?<![\w/.])(?!0000)(?:\d{8}|\d{1,2}\.\d{3}\.\d{3})[ -]?[A-HJ-NP-TV-Z]\b"),  # also dotted 12.345.678-A  # no expedientes 2021/EA26/00001152E
    "nie": re.compile(r"\b[XYZ][ -]?\d{7}[ -]?[A-HJ-NP-TV-Z]\b"),
    "whatsapp_jid": re.compile(r"\b\d{9,15}@s\.whatsapp\.net\b"),
    "telefono": re.compile(r"(?<![\w/.-])(?:\+34|0034)?[ ]?[6-9]\d{2}[ .-]?\d{3}[ .-]?\d{3}(?![\w/-])"),
    "telefono_intl": re.compile(r"(?<![\w/])(?:\+?34|0034)[6-9]\d{8}(?![\w/-])"),  # 34612345678 without the plus sign
    "matricula": re.compile(r"\b\d{4}[ -]?[BCDFGHJKLMNPRSTVWXYZ]{3}\b"),
}
# Emails: personal mailboxes are PII; institutional/press addresses are not. We flag ALL
# emails and let the allowlist keep the institutional ones (contacto@, redacciones…).
PATTERNS["email"] = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def _allow() -> list[dict]:
    if not ALLOWLIST.exists():
        return []
    return (yaml.safe_load(ALLOWLIST.read_text(encoding="utf-8")) or {}).get("allow") or []


def _allowed(match: str, where: str, allow: list[dict]) -> bool:
    for a in allow:
        if a.get("match") == match and (not a.get("where") or a["where"] in where):
            return True
    return False


def scan_files() -> list[tuple[str, str, str]]:
    hits = []
    for g in SCAN_GLOBS:
        for f in REPO.glob(g):
            rel = str(f.relative_to(REPO))
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for kind, rx in PATTERNS.items():
                    for m in rx.finditer(line):
                        hits.append((kind, m.group(0), f"{rel}:{i}"))
    return hits


def scan_graph() -> list[tuple[str, str, str]]:
    from src import neo4j_loader  # lazy: only with --graph
    drv = neo4j_loader.get_driver()
    hits = []
    q = "MATCH (n) WHERE n.entity_id IS NOT NULL RETURN n.entity_id AS id, n.name AS name, coalesce(n.aliases,[]) AS aliases"
    try:
        with drv.session() as s:
            for r in s.run(q):
                for txt in [r["name"] or "", *r["aliases"]]:
                    for kind, rx in PATTERNS.items():
                        for m in rx.finditer(txt):
                            hits.append((kind, m.group(0), f"graph:{r['id']}"))
    finally:
        drv.close()
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", action="store_true", help="also scan node names/aliases (NEO4J_URI)")
    args = ap.parse_args()
    allow = _allow()
    hits = scan_files() + (scan_graph() if args.graph else [])
    bad = [(k, m, w) for k, m, w in hits if not _allowed(m, w, allow)]
    for k, m, w in bad:
        print(f"❌ {k:12} {m:40} {w}")
    n_ok = len(hits) - len(bad)
    print(f"pii-check: {len(bad)} hallazgos fuera de allowlist ({n_ok} permitidos) en "
          f"{'repo+grafo' if args.graph else 'repo'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
