"""Validador de la extracción inline de autos CENDOJ anonimizados (ingesta 2026-09).

Reglas del lote (decisión del dueño 2026-09-05, spec 059): de un documento anonimizado NO se
extraen personas — ni seudónimos ni operadores jurídicos —, solo entidades públicas
(Court/Organization/Company/LegalCase/Crime/Event/Location) y relaciones entre ellas.
Además: cita verbatim ⊂ chunk, endpoints ∈ entities, Crime ∈ CRIME_ALLOWLIST, Event con fecha
en el nombre, y ningún tipo acusatorio/de pago (sin Person no tienen sujeto legítimo).

    cd pipeline && uv run python -m scripts._cendoj_guard        # valida extractions/batch_*.json
"""
from __future__ import annotations

import json
import re
import sys

from src import config
from src.cleanup import _crime_is_real
from src.schema import ExtractedEntity, ExtractedRelation

EXTR_DIR = config.REPORTS_DIR / "extractions"
FORBIDDEN_TYPES = {"ACCUSED_OF", "CONVICTED_OF", "PAID", "RECEIVED", "OWNS", "CONTROLS", "FAMILY_OF", "MET_WITH"}  # ontologia-ok: política CENDOJ (autos seudonimizados)
ALLOWED_ENTITY_TYPES = {"Court", "Organization", "Company", "LegalCase", "Crime", "Event", "Location", "Contract"}
EXISTING_EVENTS_OK = {"Autovía A-68"}  # Event vivo del grafo reutilizado (sin fecha en el nombre)
DATE_IN_NAME = re.compile(r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{4}-\d{2}-\d{2}|\b(19|20)\d{2}\b)")


def _norm(t: str) -> str:
    return " ".join((t or "").replace("ﬁ", "fi").replace("ﬂ", "fl").split()).lower()


def main() -> int:
    dump = json.loads((config.REPORTS_DIR / "ingest_chunks.json").read_text(encoding="utf-8"))
    chunks = {c["chunk_id"]: c for b in dump["batches"] for c in b["chunks"]}
    problems: list[str] = []
    n_items = n_ents = n_rels = 0
    for f in sorted(EXTR_DIR.glob("batch_*.json")):
        for item in json.loads(f.read_text(encoding="utf-8")):
            cid = item.get("chunk_id")
            ch = chunks.get(cid)
            if not ch:
                problems.append(f"{f.name}: chunk desconocido {cid}")
                continue
            n_items += 1
            names: set[str] = set()
            for e in item.get("entities") or []:
                try:
                    ent = ExtractedEntity.model_validate(e)
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"{cid}: entidad inválida {e} ({exc})"); continue
                n_ents += 1
                names.add(ent.name)
                et = ent.type.value if hasattr(ent.type, "value") else str(ent.type)
                if et == "Person":
                    problems.append(f"{cid}: PERSON prohibido en lote CENDOJ: «{ent.name}»")
                elif et not in ALLOWED_ENTITY_TYPES:
                    problems.append(f"{cid}: tipo no permitido {et}: «{ent.name}»")
                if et == "Crime" and not _crime_is_real(ent.name):
                    problems.append(f"{cid}: Crime fuera de allowlist: «{ent.name}»")
                if et == "Event" and ent.name not in EXISTING_EVENTS_OK and not DATE_IN_NAME.search(ent.name):
                    problems.append(f"{cid}: Event sin fecha en el nombre: «{ent.name}»")
            text = _norm(ch["text"])
            for r in item.get("relations") or []:
                try:
                    rel = ExtractedRelation.model_validate(r)
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"{cid}: relación inválida {r} ({exc})"); continue
                n_rels += 1
                rt = rel.type.value if hasattr(rel.type, "value") else str(rel.type)
                if rt in FORBIDDEN_TYPES:
                    problems.append(f"{cid}: tipo prohibido {rt}: {rel.source} → {rel.target}")
                if rel.source not in names or rel.target not in names:
                    problems.append(f"{cid}: endpoint fuera de entities: {rel.source} -[{rt}]-> {rel.target}")
                if not rel.source_quote or _norm(rel.source_quote) not in text:
                    problems.append(f"{cid}: cita NO verbatim: «{rel.source_quote}»")
    for p in problems:
        print("❌", p)
    print(f"cendoj-guard: {n_items} chunks · {n_ents} entidades · {n_rels} relaciones · "
          f"{len(problems)} problema(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
