"""Generate the gated, reversible migration for the SECOND orphan sweep (owner
decision 2026-07-09, follow-up to manual_fix_transcript_orphans_20260709 / PR #201):

  SET A — mention-only transcript orphans: entity nodes with ZERO entity-entity
          edges whose EVERY MENTIONED_IN points into a parliamentary transcript.
          These were never endpoints of the 679 edges removed by Option D (#157)
          — they were mention-only from ingestion — so the first sweep (which
          derived its candidates from the #157 rollback endpoints) missed them.
          They are exactly the population the durable rule
          cleanup._delete_transcript_orphans targets; this migration is the
          one-shot application of that predicate to the live lineage.
  SET B — absolute isolates: entity nodes with NO relationships at all (no
          entity edges, no MENTIONED_IN — leftovers of earlier cleanups that
          removed their last edge). Unreachable from explorer, documents and
          evidence alike; pure dead weight in buscar_entidades.

Deliberately NOT touched: mention-only nodes from judicial docs (informe_uco,
auto_judicial, auditoria, escrito_acusacion…) — they serve as a searchable index
of who/what appears in the documents (owner decision: keep).

DETACH DELETE removes set A's MENTIONED_IN edges too; :Chunk/:Document stay
intact. The rollback recreates every node with its props + all its relationships
→ 100% reversible.

Usage (against the ephemeral edit copy on ./neo4j/data, Path A; NOT against prod):
    cd pipeline && NEO4J_URI=bolt://<machine-ip>:7688 NEO4J_PASSWORD=... \
        uv run python -m scripts.gen_orphan_sweep_cleanup 20260709
Apply GATED:  make qa-migrate FILE=<the .cypher> CONFIRM=1   (or _orphan_sweep_prod.sh)
Then:  make centralidad && make healthcheck
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0].parent))
from src.documents_loader import transcript_doc_ids  # noqa: E402
from src.neo4j_loader import get_driver  # noqa: E402

PATCHES = Path(__file__).resolve().parents[1] / "data" / "audit" / "patches"

_EXCL = ["MENTIONED_IN", "PART_OF"]

# Same predicate as cleanup._delete_transcript_orphans (keep in sync).
SET_A_Q = """
MATCH (n) WHERE n.entity_id IS NOT NULL
AND NOT EXISTS { MATCH (n)-[r]-(m) WHERE m.entity_id IS NOT NULL
                 AND NOT type(r) IN $excl }
AND EXISTS { (n)-[:MENTIONED_IN]->(:Chunk) }
AND NOT EXISTS { MATCH (n)-[:MENTIONED_IN]->(c:Chunk)
                 WHERE NOT c.doc_id IN $docs }
RETURN n.entity_id AS eid ORDER BY eid
"""

SET_B_Q = """
MATCH (n) WHERE n.entity_id IS NOT NULL AND COUNT { (n)--() } = 0
RETURN n.entity_id AS eid ORDER BY eid
"""

NODE_Q = "MATCH (n {entity_id:$e}) RETURN labels(n) AS labels, properties(n) AS props"

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


def main() -> None:
    stamp = sys.argv[1] if len(sys.argv) > 1 else "manual"
    docs = sorted(transcript_doc_ids())
    driver = get_driver()

    with driver.session() as s:
        set_a = [r["eid"] for r in s.run(SET_A_Q, docs=docs, excl=_EXCL)]
        set_b = [r["eid"] for r in s.run(SET_B_Q)]

    nodes = []    # (eid, labels, props, bucket)
    all_rels = {}
    with driver.session() as s:
        for eid, bucket in [(e, "A:transcript") for e in set_a] + [(e, "B:isolate") for e in set_b]:
            row = s.run(NODE_Q, e=eid).single()
            if row is None:
                continue
            props = dict(row["props"])
            props.pop("embedding", None)
            nodes.append((eid, list(row["labels"]), props, bucket))
            all_rels[eid] = [dict(x) for x in s.run(RELS_Q, e=eid)]
    driver.close()

    n = len(nodes)
    tot_men = sum(1 for rels in all_rels.values() for r in rels if not r["is_entity"])
    tot_sem = sum(1 for rels in all_rels.values() for r in rels if r["is_entity"])
    assert tot_sem == 0, f"set inesperado: {tot_sem} aristas entidad-entidad en candidatos"

    print(f"set A (mención-only transcripciones): {len(set_a)}")
    print(f"set B (aislados absolutos): {len(set_b)}")
    print(f"total nodos: {n}  MENTIONED_IN: {tot_men}")
    by_label: dict[str, int] = {}
    for _, labels, _, _ in nodes:
        by_label[labels[0]] = by_label.get(labels[0], 0) + 1
    for lbl, c in sorted(by_label.items(), key=lambda x: -x[1]):
        print(f"  {lbl}: {c}")

    mig = [
        "// GENERATED by gen_orphan_sweep_cleanup.py — 2º barrido de huérfanos (dueño 2026-07-09):",
        f"// SET A = {len(set_a)} nodos mención-only cuyas menciones son TODAS de diarios de sesión",
        "//   (nunca tuvieron aristas: no eran extremos del rollback de #157; mismo predicado que",
        "//   cleanup._delete_transcript_orphans, aplicado one-shot al linaje vivo).",
        f"// SET B = {len(set_b)} nodos aislados absolutos (0 relaciones de cualquier tipo).",
        "// NO tocados: mención-only de docs judiciales (índice de búsqueda — decisión del dueño).",
        f"// DETACH DELETE quita sus {tot_men} MENTIONED_IN; :Chunk/:Document intactos. REVERSIBLE.",
        "// Apply GATED: make qa-migrate FILE=… CONFIRM=1 (o _orphan_sweep_prod.sh).",
        "// Tras aplicar: make centralidad && make healthcheck.",
        "",
    ]
    for eid, _, _, _ in nodes:
        mig.append(f"MATCH (c {{entity_id:{cy_val(eid)}}}) DETACH DELETE c;")
    mig.append("")
    mig_path = PATCHES / f"manual_fix_orphan_sweep_{stamp}.cypher"
    mig_path.write_text("\n".join(mig))

    rb = [
        f"// ROLLBACK de manual_fix_orphan_sweep_{stamp}.cypher — recrea los {n} nodos",
        f"// + sus {tot_men} MENTIONED_IN. (degree/centralidad: `make centralidad`).",
        "",
    ]
    for eid, labels, props, _ in nodes:
        rb.append(f"CREATE (c:{':'.join(labels)} {cy_map(props)});")
    rb.append("")
    for eid, _, _, _ in nodes:
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
    rbk_path = PATCHES / f"manual_fix_orphan_sweep_{stamp}.rollback.cypher"
    rbk_path.write_text("\n".join(rb))

    print(f"wrote {mig_path}")
    print(f"wrote {rbk_path}")


if __name__ == "__main__":
    main()
