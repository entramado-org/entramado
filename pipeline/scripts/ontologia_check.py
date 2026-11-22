"""CI gate of the controlled vocabulary (spec 069). Exit 1 on any finding.

1. Derived artifacts are up to date (`ontologia_gen.py --check`).
2. No tracked code file holds a literal COPY of the vocabulary: three or more DISTINCT relation
   types, procedural statuses or provenance layers as quoted literals in a row (list, set, dict
   keys, TS union), or five or more in a prose enumeration (the shape of a prompt's type list).
   Comments that name two or three terms are fine. Tests may pin values. Derive, don't copy.
   A deliberate local policy subset (e.g. «the layers this loader recreates») carries
   `ontologia-ok: <why>` on the same line.
3. No LLM decision point coerces a model output with `bool(...)`: `bool("false")` is True. Use
   `ontologia.parse_bool`. A line that is NOT an LLM output may carry `# no-llm-bool`.

    python3 pipeline/scripts/ontologia_check.py

Stdlib + PyYAML only (runs in the API job of CI and in the pipeline venv).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pipeline"))
sys.path.insert(0, str(REPO / "pipeline" / "scripts"))

import ontologia_gen  # noqa: E402
from src.ontologia import ontologia  # noqa: E402

CODE = (".py", ".js", ".mjs", ".ts", ".tsx", ".json")
# Files allowed to spell the vocabulary out: the source, its generated mirror, and tests that pin
# legacy values on purpose.
PERMITIDOS = {
    "pipeline/data/ontologia/ontologia.yaml",
    "web/lib/ontologia.generated.ts",
    "pipeline/tests/test_ontologia.py",
    "api/tests/test_ontologia.py",
}
# Files whose job is to parse an LLM / Jev output (spec 069 «puntos de decisión»).
PUNTOS_LLM = [
    "pipeline/src/extract_facts.py",
    "pipeline/src/extraction_guards.py",
    "pipeline/src/qa_esc_opus.py",
    "pipeline/src/qa_esc_arbitrate.py",
    "pipeline/src/qa_esc_escalate.py",
    "pipeline/src/qa_esc_jev.py",
    "pipeline/src/qa_esc_schema.py",
    "pipeline/src/briefings.py",
    "pipeline/scripts/_ingest_apply.py",
    "pipeline/scripts/_apply_verified.py",
    "pipeline/scripts/_cendoj_guard.py",
    "pipeline/scripts/pii_semantic_select.py",
]
SEP = r"""[\s,|/:\[\]{}()0-9=]*"""
PROSA_MIN = 5
BOOL_RE = re.compile(r"\bbool\(\s*[\w.\[\]'\"]+\.get\(")


def _alt(terms) -> str:
    return "|".join(sorted((re.escape(t) for t in terms), key=len, reverse=True))


def _citada(terms) -> re.Pattern:
    """Three or more quoted terms separated only by punctuation/whitespace."""
    term = rf"""(["'])(?:{_alt(terms)})\1"""
    return re.compile(rf"{term}(?:{SEP}{term}){{2,}}")


def _prosa(terms) -> re.Pattern:
    """PROSA_MIN or more bare terms in an enumeration (prompt type lists)."""
    term = rf"\b(?:{_alt(terms)})\b"
    sep = r"(?:[\s,|/()]+|[\s,]+(?:y|o|e)\s+)"  # «a, b, c y d» is an enumeration too
    return re.compile(rf"{term}(?:{sep}(?:no firme\)[\s,]*)?{term}){{{PROSA_MIN - 1},}}")


def copias(files, root: Path = REPO) -> list[str]:
    o = ontologia()
    patrones = {}
    for nombre, terms in (("tipos de relación", o.tipos_relacion), ("estados procesales", o.estados),
                          ("capas", o.capas)):
        patrones[nombre] = _citada(terms)
        patrones[nombre + " (prosa)"] = _prosa(terms)
    out = []
    for rel in files:
        if rel in PERMITIDOS or not rel.endswith(CODE) or "/tests/" in rel:
            continue
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        # Template markers ({{definiciones:capas:a,b,c}}) name terms the renderer fills FROM the
        # ontology: they are references, not copies. Blank them, keeping line numbers.
        text = re.sub(r"\{\{[^}\n]*\}\}", lambda m: " " * len(m.group()), text)
        # A backslash line continuation («investigado, \\⏎imputado») does not break an enumeration.
        text = text.replace("\\\n", " \n")
        for nombre, pat in patrones.items():
            for m in pat.finditer(text):
                # A real copy names three DISTINCT terms (not one term repeated in a Cypher pattern).
                minimo = PROSA_MIN if nombre.endswith("(prosa)") else 3
                if len(set(re.findall(r"[A-Za-z_]+", m.group()))) < minimo:
                    continue
                line = text.count("\n", 0, m.start()) + 1
                # A deliberate local POLICY subset (not a copy of a vocabulary) says so in place.
                if "ontologia-ok:" in text.splitlines()[line - 1]:
                    continue
                out.append(f"{rel}:{line}: copia de {nombre}: {m.group()[:90]!r}")
    return out


def bools(files, root: Path = REPO) -> list[str]:
    out = []
    for rel in files:
        if rel not in PUNTOS_LLM:
            continue
        for i, line in enumerate((root / rel).read_text(encoding="utf-8").splitlines(), 1):
            if BOOL_RE.search(line) and "# no-llm-bool" not in line:
                out.append(f"{rel}:{i}: bool() sobre una salida de LLM — usa ontologia.parse_bool")
    return out


def tracked() -> list[str]:
    res = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True)
    return [f for f in res.stdout.splitlines() if (REPO / f).is_file()]


def main() -> int:
    files = tracked()
    problemas = []
    stale = [str(p.relative_to(REPO)) for p, render in ontologia_gen.ARTIFACTS.items()
             if not p.exists() or p.read_text(encoding="utf-8") != render()]
    problemas += [f"{s}: artefacto desfasado (ejecuta `make ontologia`)" for s in stale]
    problemas += copias(files)
    problemas += bools(files)
    for p in problemas:
        print(p)
    print(f"ontologia-check: {len(files)} ficheros, {len(problemas)} problemas (ontología v{ontologia().version})")
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
