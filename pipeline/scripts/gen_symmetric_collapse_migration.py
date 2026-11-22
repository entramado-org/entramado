"""Generate a reviewable migration that collapses BIDIRECTIONAL symmetric edges graph-wide
(the live counterpart of the `_SYMMETRIC` direction-canonicalisation added to
neo4j_loader.py in the Pedro Sánchez ego audit — the loader only fixes FUTURE ingests; prod
never re-ingests, so the accumulated live graph still holds A↔B + B↔A as two opposite edges).

Symmetric types: FAMILY_OF / ASSOCIATED_WITH / MET_WITH. A pair is BIDIRECTIONAL when both
A→B and B→A exist. Direction carries no meaning for these types, so the two opposite edges
inflate the pair's degree and make `relaciones_entidad` show the same relation twice.

Collapse rule (per the follow-up note `findings/colapso-simetrico-live.md`), applied WITHIN
each provenance layer (source_kind) — judicial / prensa / contexto / ultima_hora edges are
NEVER merged across kinds:

  * Keep the canonical direction (lower entity_id → higher).
  * For each edge stored in the inverse direction of a bidirectional pair, keyed by its
    evidence (source_kind + source_chunk_id, or source_kind + doc|quote when it has no chunk):
      - if a canonical edge with the SAME evidence already exists → pure duplicate → DELETE
        the inverse (this is the degree reduction).
      - else → distinct evidence → CREATE a canonical edge carrying the inverse's full props
        (preserve the citation as an extra evidence layer) and DELETE the inverse.

Unilateral inverse edges (a pair that is NOT bidirectional) are left untouched — they create
no duplicate pair and flipping them would be churn with no user-visible effect.

Read-only against Neo4j — only emits files. Apply GATED:
    make qa-migrate FILE=… CONFIRM=1
Generates a faithful per-statement rollback that restores the exact original edges.

    cd pipeline && uv run python -m scripts.gen_symmetric_collapse_migration
"""
from __future__ import annotations

from collections import defaultdict

from src import config, neo4j_loader
from src.ontologia import ontologia

SYMMETRIC = sorted(ontologia().simetricas)
PROP_KEYS = ["source_kind", "source_doc", "source_page", "source_chunk_id",
             "source_quote", "amount", "currency", "date"]

OUT_DIR = config.WORK_DIR / "audit" / "patches"
STAMP = "20260619"  # date-stamped; no Date.now() so the run stays reproducible
MIG = OUT_DIR / f"manual_collapse_symmetric_{STAMP}.cypher"
RBK = OUT_DIR / f"manual_collapse_symmetric_{STAMP}.rollback.cypher"


def _lit(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _evid_key(props: dict) -> tuple:
    """Identity of a fact within a pair: provenance layer + the specific evidence."""
    kind = props.get("source_kind") or ""
    chunk = props.get("source_chunk_id")
    if chunk:
        return (kind, "chunk", chunk)
    quote = " ".join((props.get("source_quote") or "").split())
    return (kind, "noq", (props.get("source_doc") or "") + "|" + quote)


def _propstr(props: dict) -> str:
    return ",".join(f"{k}:{_lit(props.get(k))}" for k in PROP_KEYS if props.get(k) is not None)


def _locator(props: dict) -> str:
    """A WHERE clause (on bound `r`) that uniquely identifies one stored edge."""
    chunk = props.get("source_chunk_id")
    if chunk:
        return f"r.source_chunk_id={_lit(chunk)}"
    quote = props.get("source_quote") or ""
    return (f"r.source_chunk_id IS NULL AND coalesce(r.source_kind,'')={_lit(props.get('source_kind') or '')} "
            f"AND coalesce(r.source_quote,'')={_lit(quote)}")


def main() -> None:
    driver = neo4j_loader.get_driver()
    mig, rbk = [], []
    n_del = n_mig = n_pairs = 0
    try:
        with driver.session() as s:
            for t in SYMMETRIC:
                q = (f"MATCH (a)-[r:{t}]->(b) "
                     "WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL "
                     "AND a.entity_id <> b.entity_id "
                     "RETURN a.entity_id AS sa, b.entity_id AS sb, properties(r) AS p, "
                     "a.name AS na, b.name AS nb")
                # group every edge of this type under its canonical (lo,hi) pair
                pairs: dict = defaultdict(lambda: {"canon": [], "inv": []})
                for rec in s.run(q):
                    sa, sb, p = rec["sa"], rec["sb"], rec["p"]
                    lo, hi = (sa, sb) if sa < sb else (sb, sa)
                    edge = {"sa": sa, "sb": sb, "p": p, "na": rec["na"], "nb": rec["nb"], "lo": lo, "hi": hi}
                    (pairs[(lo, hi)]["canon"] if sa == lo else pairs[(lo, hi)]["inv"]).append(edge)

                for (lo, hi), grp in pairs.items():
                    if not grp["canon"] or not grp["inv"]:
                        continue  # not bidirectional → leave untouched
                    n_pairs += 1
                    canon_keys = {_evid_key(e["p"]) for e in grp["canon"]}
                    for e in grp["inv"]:
                        p = e["p"]
                        k = _evid_key(p)
                        head = f"// [{t}] {e.get('na')} (inv {e['sa']}→{e['sb']}) canon {lo}→{hi}"
                        del_stmt = (f"MATCH (a {{entity_id:{_lit(hi)}}})-[r:{t}]->(b {{entity_id:{_lit(lo)}}}) "
                                    f"WHERE {_locator(p)} DELETE r;")
                        if k in canon_keys:
                            # pure duplicate of an existing canonical edge → just drop the inverse
                            mig.append(f"{head} — DUP, delete inverse\n{del_stmt}\n")
                            rbk.append(f"{head} — restore inverse\n"
                                       f"MATCH (a {{entity_id:{_lit(hi)}}}),(b {{entity_id:{_lit(lo)}}}) "
                                       f"CREATE (a)-[:{t} {{{_propstr(p)}}}]->(b);\n")
                            n_del += 1
                        else:
                            # distinct evidence → migrate citation onto a new canonical edge
                            canon_keys.add(k)
                            mig.append(
                                f"{head} — MIGRATE evidence to canonical, delete inverse\n"
                                f"MATCH (lo {{entity_id:{_lit(lo)}}}),(hi {{entity_id:{_lit(hi)}}}) "
                                f"CREATE (lo)-[:{t} {{{_propstr(p)}}}]->(hi);\n"
                                f"{del_stmt}\n")
                            rbk.append(
                                f"{head} — drop migrated canonical, restore inverse\n"
                                f"MATCH (lo {{entity_id:{_lit(lo)}}})-[r:{t}]->(hi {{entity_id:{_lit(hi)}}}) "
                                f"WHERE {_locator(p)} DELETE r;\n"
                                f"MATCH (a {{entity_id:{_lit(hi)}}}),(b {{entity_id:{_lit(lo)}}}) "
                                f"CREATE (a)-[:{t} {{{_propstr(p)}}}]->(b);\n")
                            n_mig += 1
    finally:
        driver.close()

    banner = (f"// GENERATED by gen_symmetric_collapse_migration.py — {n_pairs} bidirectional pair(s), "
              f"{n_del} duplicate delete(s) + {n_mig} evidence-migrate(s). REVISABLE Y REVERSIBLE.\n"
              "// Collapse A↔B + B↔A symmetric edges to the canonical direction WITHIN each source_kind\n"
              "// (FAMILY_OF/ASSOCIATED_WITH/MET_WITH). Root guarded in neo4j_loader._SYMMETRIC for future\n"
              "// ingests. Apply GATED: make qa-migrate FILE=… CONFIRM=1.\n\n")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MIG.write_text(banner + "\n".join(mig))
    RBK.write_text(banner.replace("REVISABLE Y REVERSIBLE", "ROLLBACK") + "\n".join(reversed(rbk)))
    print(f"✔ pairs={n_pairs} deletes={n_del} migrates={n_mig}")
    print(f"  migration → {MIG}")
    print(f"  rollback  → {RBK}")


if __name__ == "__main__":
    main()
