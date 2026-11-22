"""Ingesta dirigida autos CENDOJ 20775/2020 (2026-09): convierte el fichero de hechos redactado
inline (`data/reports/cendoj_facts.json`, una lista de {doc_id, entities:{name:type}, relations:[...]})
en `data/reports/extractions/batch_N.json` con el formato que espera `_ingest_apply`
({chunk_id, entities, relations} por chunk). Localiza cada cita verbatim en los chunks del
documento (tolerando las ligaduras ﬁ/ﬂ del PDF) y la asigna al chunk que la contiene; las
entidades de cada chunk son los extremos de sus relaciones. Falla si una cita no aparece.

    cd pipeline && uv run python -m scripts._cendoj_facts_to_batches
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

from src import config

FACTS = config.REPORTS_DIR / "cendoj_facts.json"
EXTR = config.REPORTS_DIR / "extractions"


def _variants(q: str) -> list[str]:
    ws = " ".join(q.split())
    return [ws, ws.replace("fi", "ﬁ").replace("fl", "ﬂ"), ws.replace("ﬁ", "fi").replace("ﬂ", "fl")]


def _find_chunk(quote: str, chunks: list[dict]) -> tuple[dict, str] | None:
    for ch in chunks:
        text = " ".join(ch["text"].split())
        for v in _variants(quote):
            if v in text:
                return ch, v
    return None


def main() -> int:
    dump = json.loads((config.REPORTS_DIR / "ingest_chunks.json").read_text(encoding="utf-8"))
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for b in dump["batches"]:
        for c in b["chunks"]:
            by_doc[c["doc_id"]].append(c)
    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    EXTR.mkdir(parents=True, exist_ok=True)
    for f in EXTR.glob("batch_*.json"):
        f.unlink()
    problems: list[str] = []
    out_items: list[dict] = []
    n_rel = 0
    for doc in facts:
        did = doc["doc_id"]
        chunks = sorted(by_doc.get(did, []), key=lambda c: (c["page_start"], c["chunk_id"]))
        if not chunks:
            problems.append(f"{did}: sin chunks en ingest_chunks.json"); continue
        etypes = doc["entities"]
        per_chunk: dict[str, dict] = {}
        for r in doc["relations"]:
            hit = _find_chunk(r["quote"], chunks)
            if not hit:
                problems.append(f"{did}: cita no encontrada: «{r['quote']}»"); continue
            ch, exact = hit
            item = per_chunk.setdefault(ch["chunk_id"], {"chunk_id": ch["chunk_id"], "entities": {}, "relations": []})
            for name in (r["source"], r["target"]):
                if name not in etypes:
                    problems.append(f"{did}: entidad sin tipo: «{name}»"); continue
                item["entities"][name] = {"name": name, "type": etypes[name], "aliases": [], "role": None}
            rel = {"source": r["source"], "target": r["target"], "type": r["type"], "source_quote": exact}
            for k in ("date", "amount", "currency"):
                if r.get(k) is not None:
                    rel[k] = r[k]
            item["relations"].append(rel); n_rel += 1
        for item in per_chunk.values():
            item["entities"] = list(item["entities"].values())
            out_items.append(item)
    # one batch file per 10 chunk-items
    for i in range(0, len(out_items), 10):
        (EXTR / f"batch_{i // 10}.json").write_text(json.dumps(out_items[i:i + 10], ensure_ascii=False, indent=1), encoding="utf-8")
    for p in problems:
        print("❌", p)
    print(f"facts→batches: {len(facts)} docs · {len(out_items)} chunks con hechos · {n_rel} relaciones · {len(problems)} problema(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
