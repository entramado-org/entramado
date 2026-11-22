"""Generate the gated, reversible migration that removes the NODES left orphaned
by the transcript-edge retirement (Option D, PR #157 / manual_fix_transcript_edges_20260702).

That migration deleted the 679 entity-entity edges sourced from the Diarios de
Sesiones but kept the nodes: endpoints whose ONLY support was a transcript (e.g.
Event:operacion-delorme, boilerplate from the committee title) are now dangling
with zero entity-entity edges — invisible in the graph explorer yet still hits in
buscar_entidades, with a `degree` that counts only MENTIONED_IN. Owner decision
(2026-07-09): full sweep of those orphans, plus two explicit adds:
  - Event:trama            generic noun (session-42 pattern); its single surviving
                           judicial edge (Zapatero INVOLVES trama, auto-zapatero
                           p.34) is a co-occurrence with a non-referent — the quote
                           stays readable in the document. Also added to
                           GENERIC_EVENT_NAMES in src/cleanup.py (root fix).
  - Event:delorme          fragment/dup of operacion-delorme.

Candidates = the entity_ids that appear as endpoints in the #157 rollback file
(the exact set of nodes that could have been orphaned by it) + the explicit adds.
Orphan = zero entity-entity edges in the graph queried (MENTIONED_IN/PART_OF and
edges to non-entity nodes excluded; do NOT trust `n.degree`, which counts mentions).

DETACH DELETE removes each node's remaining relationships too (its MENTIONED_IN →
:Chunk and, for trama, the judicial edge); :Chunk/:Document stay intact, so the
transcripts remain consultable. The rollback recreates every node with its props
+ ALL its relationships → 100% reversible.

Usage (against the ephemeral edit copy on ./neo4j/data, Path A; NOT against prod):
    cd pipeline && NEO4J_URI=bolt://<machine-ip>:7688 NEO4J_PASSWORD=... \
        uv run python -m scripts.gen_transcript_orphan_cleanup 20260709
Apply GATED:  make qa-migrate FILE=<the .cypher> CONFIRM=1
Then:  make centralidad && make healthcheck
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0].parent))
from src.neo4j_loader import get_driver  # noqa: E402

PATCHES = Path(__file__).resolve().parents[1] / "data" / "audit" / "patches"
EDGES_ROLLBACK = PATCHES / "manual_fix_transcript_edges_20260702.rollback.cypher"

# Owner-confirmed deletions even if not strict orphans (trama keeps 1 judicial
# co-occurrence edge; see module docstring).
EXPLICIT = ["Event:trama", "Event:delorme", "Event:operacion-delorme"]

_EXCL = ["MENTIONED_IN", "PART_OF"]

DEG_Q = """
MATCH (n {entity_id:$e})
RETURN labels(n) AS labels, properties(n) AS props,
       COUNT { MATCH (n)-[r]-(m) WHERE m.entity_id IS NOT NULL
               AND NOT type(r) IN $excl } AS deg,
       COUNT { (n)-[:MENTIONED_IN]->(:Chunk) } AS mentions
"""

RELS_Q = """
MATCH (n {entity_id:$e})-[r]-(o)
RETURN type(r) AS rt,
       startNode(r).entity_id = $e AS outgoing,
       coalesce(o.entity_id, o.chunk_id) AS okey,
       (o.entity_id IS NOT NULL) AS is_entity,
       properties(r) AS rp
"""


def cy_val(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(cy_val(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return "'" + s + "'"


def cy_map(props):
    return "{" + ", ".join(f"{k}: {cy_val(v)}" for k, v in props.items()) + "}"


def candidates() -> list[str]:
    text = EDGES_ROLLBACK.read_text()
    eids = set(re.findall(r"entity_id:'((?:[^'\\]|\\.)+)'", text))
    eids = {e.replace("\\'", "'").replace("\\\\", "\\") for e in eids}
    return sorted(eids | set(EXPLICIT))


def main() -> None:
    stamp = sys.argv[1] if len(sys.argv) > 1 else "manual"
    cands = candidates()
    driver = get_driver()

    nodes = []    # (eid, labels, props, deg, mentions)
    all_rels = {}  # eid -> [rel dicts]
    with driver.session() as s:
        for eid in cands:
            row = s.run(DEG_Q, e=eid, excl=_EXCL).single()
            if row is None:
                continue  # merged/renamed/deleted since #157 — nothing to clean
            if row["deg"] > 0 and eid not in EXPLICIT:
                continue  # still supported by non-transcript edges — keep
            props = dict(row["props"])
            props.pop("embedding", None)
            nodes.append((eid, list(row["labels"]), props, row["deg"], row["mentions"]))
            all_rels[eid] = [dict(x) for x in s.run(RELS_Q, e=eid)]
    driver.close()

    n = len(nodes)
    tot_sem = sum(1 for rels in all_rels.values() for r in rels if r["is_entity"])
    tot_men = sum(1 for rels in all_rels.values() for r in rels if not r["is_entity"])

    print(f"candidatos (extremos #157 + explícitos): {len(cands)}")
    print(f"a borrar: {n} nodos  ({tot_sem} aristas entidad-entidad, {tot_men} MENTIONED_IN)")
    for eid, labels, _, deg, mentions in nodes:
        flag = "  [EXPLÍCITO]" if eid in EXPLICIT else ""
        print(f"  {eid}  ({':'.join(labels)})  deg={deg} mentions={mentions}{flag}")

    # ---------- migration ----------
    mig = [
        "// GENERATED by gen_transcript_orphan_cleanup.py — borra los nodos huérfanos",
        "// dejados por la retirada de aristas de transcripciones (Option D, PR #157):",
        f"// {n} nodos con 0 aristas entidad-entidad (+ Event:trama, decisión del dueño",
        "// 2026-07-09: sustantivo genérico; su única arista judicial es co-ocurrencia).",
        f"// DETACH DELETE quita sus {tot_men} MENTIONED_IN + {tot_sem} aristas restantes;",
        "// los :Chunk/:Document quedan intactos (docs siguen consultables). REVERSIBLE.",
        "// Apply GATED: make qa-migrate FILE=… CONFIRM=1. Tras aplicar: make centralidad && make healthcheck.",
        "",
    ]
    for eid, _, _, _, _ in nodes:
        mig.append(f"MATCH (c {{entity_id:{cy_val(eid)}}}) DETACH DELETE c;")
    mig.append("")
    mig_path = PATCHES / f"manual_fix_transcript_orphans_{stamp}.cypher"
    mig_path.write_text("\n".join(mig))

    # ---------- rollback ----------
    rb = [
        f"// ROLLBACK de manual_fix_transcript_orphans_{stamp}.cypher — recrea los {n} nodos",
        f"// + todas sus relaciones ({tot_sem} entidad-entidad, {tot_men} MENTIONED_IN).",
        "// (degree/centralidad se recalculan con `make centralidad`).",
        "",
    ]
    for eid, labels, props, _, _ in nodes:
        rb.append(f"CREATE (c:{':'.join(labels)} {cy_map(props)});")
    rb.append("")
    for eid, _, _, _, _ in nodes:
        for r in all_rels[eid]:
            if r["is_entity"]:
                match_o = f"(o {{entity_id:{cy_val(r['okey'])}}})"
            else:
                match_o = f"(o:Chunk {{chunk_id:{cy_val(r['okey'])}}})"
            rp = cy_map(r["rp"]) if r["rp"] else ""
            relpat = f"[:{r['rt']}{(' ' + rp) if rp else ''}]"
            pat = f"(c)-{relpat}->(o)" if r["outgoing"] else f"(c)<-{relpat}-(o)"
            rb.append(f"MATCH (c {{entity_id:{cy_val(eid)}}}), {match_o} CREATE {pat};")
    rb.append("")
    rbk_path = PATCHES / f"manual_fix_transcript_orphans_{stamp}.rollback.cypher"
    rbk_path.write_text("\n".join(rb))

    print(f"wrote {mig_path}")
    print(f"wrote {rbk_path}")


if __name__ == "__main__":
    main()
