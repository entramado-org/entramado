"""Build the per-doc fact cache from the VERIFIED ingest verdicts (gated apply).

Unlike scripts/_ingest_apply.py (which assembles the raw extraction swarm output),
this script materialises ONLY the relations the human-gated verification accepted:

  reports/verify_dump.json      → relation data, keyed "{chunk_id}#{idx}"
  reports/verify_verdicts/final_state.json
      .relations[key].status    → OK / OK_FIXED enter; FUERA_* dropped
      .relations[key].fix       → {type?, swap_direction?, date?, amount?} overrides
      .entity_map.accept        → surface-name → canonical-name chain
      .entity_map.type_force    → canonical-name → EntityType (live-graph collisions)
  reports/extractions/batch_*.json → entity records (type/aliases/role) per chunk

Entities are filtered to the census of names actually appearing in accepted
relations (canonicalised), so no orphan nodes are created. Output is
data/facts/{doc_id}.jsonl in the EXACT extract_facts._to_record format, strictly
validated against schema.ChunkExtraction.

Run from pipeline/ (after the human gate, see verify_verdicts/REVIEW-PASADA4.md):
    uv run python -m scripts._apply_verified
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from src import config, revision
from src.extraction_guards import encolar_rechazos, validar_extraccion
from src.ontologia import ontologia
from src.schema import ChunkExtraction

VV = config.REPORTS_DIR / "verify_verdicts"
EXTR_DIR = config.REPORTS_DIR / "extractions"

ACCEPT_STATUSES = {"OK", "OK_FIXED"}
DOUBT_STATUS = "DUDA"
PUNTO = "verify_ingest"
PUNTO_GUARDAS = "verify_ingest_guardas"  # the final record failed an ontology guard


def decidir(status: str, rel_type: str, payload: dict, *, directory=None) -> str:
    """What a gated verdict does (spec 069 doubt policy): `entra`, `entra_baja` (kept, marked low
    confidence), `revision` (queued until a person approves it: then `entra`) or `fuera`."""
    if status in ACCEPT_STATUSES:
        return "entra"
    if status != DOUBT_STATUS:
        return "fuera"
    if ontologia().ante_duda(rel_type) == "mantener":
        return "entra_baja"
    motivo = "duda_acusatoria" if rel_type in ontologia().acusatorias else "duda_a_revision"
    iid = revision.encolar(PUNTO, motivo, payload, termino=rel_type, directory=directory)
    res = revision.resoluciones(directory).get(iid)
    return "entra" if res and res["decision"] == "aprobada" else "revision"


def main():
    fs = json.loads((VV / "final_state.json").read_text(encoding="utf-8"))
    dump = json.loads((config.REPORTS_DIR / "verify_dump.json").read_text(encoding="utf-8"))
    chunks_meta = json.loads((config.REPORTS_DIR / "ingest_chunks.json").read_text(encoding="utf-8"))
    cmeta = {c["chunk_id"]: c for b in chunks_meta["batches"] for c in b["chunks"]}

    accept_map: dict[str, str] = fs["entity_map"].get("accept", {})
    type_force: dict[str, str] = fs["entity_map"].get("type_force", {})

    def canon(name: str) -> str:
        seen: set[str] = set()
        while name in accept_map and name not in seen:
            seen.add(name)
            name = accept_map[name]
        return name

    # --- accepted relations per chunk, with fixes applied -------------------
    verdicts = fs["relations"]
    rel_by_chunk: dict[str, list[dict]] = defaultdict(list)
    verified: set[str] = set()  # every chunk with a verdict: its cache record is always rewritten
    n_acc = 0
    n_dec = Counter()
    for batch in dump["batches"]:
        for ch in batch:
            for r in ch.get("relations", []):
                v = verdicts.get(r["key"])
                if not v:
                    continue
                verified.add(ch["chunk_id"])
                fix = v.get("fix") or {}
                payload = {"key": r["key"], "doc_id": ch.get("doc_id"), "chunk_id": ch["chunk_id"],
                           "page_start": ch.get("page"), "relation": r}
                dec = decidir(v["status"], fix.get("type") or r["type"], payload)
                n_dec[dec] += 1
                if dec not in ("entra", "entra_baja"):
                    continue
                src, tgt = canon(r["source"]), canon(r["target"])
                if fix.get("swap_direction") is True:  # booleans only — never strings
                    src, tgt = tgt, src
                rel_by_chunk[ch["chunk_id"]].append({
                    "source": src,
                    "target": tgt,
                    "type": fix.get("type") or r["type"],
                    "source_quote": r.get("quote") or "",
                    "amount": fix["amount"] if "amount" in fix else r.get("amount"),
                    "currency": r.get("currency"),
                    "date": fix["date"] if "date" in fix else r.get("date"),
                    **({"confianza": "baja"} if dec == "entra_baja" else {}),
                })
                n_acc += 1

    census = {canon(n) for rels in rel_by_chunk.values() for r in rels for n in (r["source"], r["target"])}
    print(f"verdicts: {dict(n_dec)} (revision → python -m src.revision status)")
    print(f"accepted relations: {n_acc} · census entities: {len(census)} · chunks with facts: {len(rel_by_chunk)}")

    # --- global entity catalog from the swarm extractions -------------------
    types: dict[str, Counter] = defaultdict(Counter)
    aliases: dict[str, set] = defaultdict(set)
    roles: dict[str, str] = {}
    for f in sorted(EXTR_DIR.glob("batch_*.json")):
        payload = json.loads(f.read_text(encoding="utf-8"))
        entries = payload if isinstance(payload, list) else (payload.get("chunks") or payload.get("results") or [])
        for entry in entries:
            for e in entry.get("entities") or []:
                name = " ".join((e.get("name") or "").split())
                if not name:
                    continue
                c = canon(name)
                if c not in census:
                    continue
                if e.get("type"):
                    types[c][e["type"]] += 1
                if name != c:
                    aliases[c].add(name)
                for a in e.get("aliases") or []:
                    if a and a != c:
                        aliases[c].add(a)
                if e.get("role") and c not in roles:
                    roles[c] = e["role"]

    missing = sorted(n for n in census if not types[n] and n not in type_force)
    if missing:
        raise SystemExit(f"census names with no extraction entity record and no type_force: {missing}")

    def ent_record(name: str) -> dict:
        etype = type_force.get(name) or types[name].most_common(1)[0][0]
        return {"name": name, "type": etype,
                "aliases": sorted(aliases.get(name, set()))[:8],
                "role": roles.get(name)}

    # --- emit per-doc facts through the ontology guards ----------------------
    # Spec 069: the verifier's OK does not exempt the FINAL record (after fixes and name
    # resolution) from the vocabulary: canonical crimes, admitted endpoints, loader-only types.
    # What fails goes to the review queue; the cache holds only what passed.
    by_doc: dict[str, list[dict]] = defaultdict(list)
    n_cola = 0
    for cid in sorted(verified):
        m = cmeta[cid]
        rels = rel_by_chunk.get(cid, [])
        # Validate the low-confidence relations as their own group so the `confianza: baja` mark (a
        # field the schema does not model) stays bound to them through the guards' normalisation.
        ok_rels, ok_ents = [], {}
        for baja in (False, True):
            grupo = [r for r in rels if (r.get("confianza") == "baja") == baja]
            if not grupo:
                continue
            names = sorted({n for r in grupo for n in (r["source"], r["target"])})
            ok, rechazos = validar_extraccion({"entities": [ent_record(n) for n in names],
                                               "relations": [{k: v for k, v in r.items() if k != "confianza"}
                                                             for r in grupo]})
            n_cola += encolar_rechazos(PUNTO_GUARDAS, {"doc_id": m["doc_id"], "chunk_id": cid,
                                                       "page_start": m["page_start"]}, rechazos)
            ok_rels += [{**r.model_dump(mode="json"), **({"confianza": "baja"} if baja else {})}
                        for r in ok.relations]
            ok_ents.update({e.name: e for e in ok.entities})
        used = {n for r in ok_rels for n in (r["source"], r["target"])}
        # A chunk whose relations were all rejected is still written (empty): otherwise the previous
        # cache record, with the relations now under review, would survive and be loaded.
        rec = {
            "chunk_id": cid,
            "doc_id": m["doc_id"],
            "doc_title": m["doc_title"],
            "page_start": m["page_start"],
            "page_end": m.get("page_end", m["page_start"]),
            "entities": [e.model_dump(mode="json") for n, e in sorted(ok_ents.items()) if n in used],
            "relations": ok_rels,
        }
        ChunkExtraction.model_validate({"entities": rec["entities"], "relations": rec["relations"]})
        by_doc[m["doc_id"]].append(rec)

    total_r = 0
    for doc_id, recs in sorted(by_doc.items()):
        path = config.FACTS_DIR / f"{doc_id}.jsonl"
        # Replace only the verified chunks: chunks of the same doc outside this verification run keep
        # their cache record (Codex 3rd review of #15).
        mine = {r["chunk_id"] for r in recs}
        if path.exists():
            recs += [r for r in (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip())
                     if r.get("chunk_id") not in mine]
        recs.sort(key=lambda r: (r.get("page_start") or 0, r["chunk_id"]))  # legacy lines may lack pages
        with path.open("w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        nr = sum(len(r["relations"]) for r in recs)
        total_r += nr
        print(f"  ✔ {doc_id}: {len(recs)} chunks, {nr} relations → {path.name}")

    print(f"\n✔ facts cache written for {len(by_doc)} docs · {total_r} of {n_acc} gate-accepted relations · "
          f"{n_cola} items to review ({PUNTO_GUARDAS}) · the rest dropped by the extraction guards")


if __name__ == "__main__":
    main()
