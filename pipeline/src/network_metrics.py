"""Offline network metrics: betweenness, closeness and community detection.

Materializes graph-analysis scores as node properties so the MCP tools can read
them cheaply (never compute in-flight). Idempotent: each run recomputes from
scratch and overwrites `n.betweenness`, `n.closeness`, `n.community` and
`n.community_label` on every entity node. The same graph always yields the same
communities (fixed Louvain seed, input sorted by entity_id), so a re-run only
moves a node when the graph changed.

Why offline igraph and not Neo4j GDS: the deployed image is neo4j:5.26 Community
with only APOC and a 1G heap cap — installing GDS would mean recreating the live
DB with a heavy plugin. igraph computes the same metrics in C in <1s at this
scale and writes plain properties back over Bolt, exactly like `compute_degree`.
See specs/012-centralidad-comunidades/plan.md.

Run: `make centralidad` (after an ingest, like the other overlays).
"""
from __future__ import annotations

import argparse
import math
import os
import random

import igraph as ig
import structlog
from neo4j import GraphDatabase

log = structlog.get_logger()


def get_driver():
    """Bolt driver from env (same defaults as pipeline.config / neo4j_loader).
    Read directly from env so this job stays decoupled from the heavy pipeline
    package (schema/pydantic/mlx) — it only needs neo4j + igraph + structlog."""
    return GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"),
              os.getenv("NEO4J_PASSWORD", "changeme")),
    )

# Structural edges that are not entity-entity relationships (mirror of
# api/app/config.PROVENANCE_RELS — kept inline so the pipeline has no dep on the
# api package).
_EXCLUDE = ["MENTIONED_IN", "PART_OF"]
_BATCH = 1000
_LABEL_TOP = 3  # members named in a community's tentative label
_SEED = 0  # Louvain draws from Python's `random` (igraph's default RNG): fixed, the partition is the same on every run


def _finite(x: float) -> float:
    """0.0 for NaN/inf — keeps the written property JSON-serializable (the API
    500s on NaN). igraph closeness yields NaN for isolated/disconnected nodes."""
    return x if x is not None and math.isfinite(x) else 0.0


def read_graph(driver) -> tuple[list[dict], list[tuple[str, str, int]]]:
    """Return (nodes, edges). Nodes = entity nodes with id/name/degree. Edges =
    (a_id, b_id, weight) where weight is the number of parallel entity-entity
    relationships between the pair (structural rels excluded)."""
    node_q = """
    MATCH (n) WHERE n.entity_id IS NOT NULL
    RETURN n.entity_id AS id, coalesce(n.name, n.entity_id) AS name,
           coalesce(n.degree, 0) AS degree
    """
    # Undirected, de-duplicated pair weights: id(a) < id(b) collapses the two
    # directions so each unordered pair appears once with its parallel-edge count.
    edge_q = f"""
    MATCH (a)-[r]-(b)
    WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
      AND NOT type(r) IN {_EXCLUDE}
      AND id(a) < id(b)
    RETURN a.entity_id AS a, b.entity_id AS b, count(r) AS weight
    """
    with driver.session() as s:
        nodes = s.run(node_q).data()
        edges = [(e["a"], e["b"], e["weight"]) for e in s.run(edge_q).data()]
    return nodes, edges


def compute(nodes: list[dict], edges: list[tuple[str, str, int]]) -> list[dict]:
    """Build the igraph graph and compute the four scores per node.

    Betweenness/closeness use the graph UNWEIGHTED (each relationship = one hop:
    the natural reading of "shortest path" / "bridge" centrality here). Community
    detection (Louvain) uses the parallel-edge count as a similarity weight so
    heavily-evidenced pairs cohere. Betweenness is normalized to [0,1] by the
    theoretical max for an undirected graph; closeness uses igraph's normalized
    form (per connected component)."""
    # Neo4j returns the nodes in storage order: sort them so the vertex numbering, and with it the partition, depends on
    # entity_ids only (the order and orientation of the pairs does not change igraph's partition).
    nodes = sorted(nodes, key=lambda n: n["id"])
    ids = [n["id"] for n in nodes]
    idx = {nid: i for i, nid in enumerate(ids)}
    names = {n["id"]: n["name"] for n in nodes}
    degree = {n["id"]: n["degree"] for n in nodes}

    g = ig.Graph(n=len(ids), directed=False)
    g.add_edges([(idx[a], idx[b]) for a, b, _ in edges])
    weights = [w for _, _, w in edges]

    bet = g.betweenness(directed=False)  # unweighted
    n = len(ids)
    norm = ((n - 1) * (n - 2) / 2.0) if n > 2 else 1.0
    bet = [_finite(b / norm) if norm else 0.0 for b in bet]
    # igraph closeness returns NaN (not None) for isolated/disconnected vertices;
    # NaN is not JSON-compliant and 500s the API serializer, so coerce to 0.0.
    clo = [_finite(c) for c in g.closeness(normalized=True)]
    state = random.getstate()
    random.seed(_SEED)
    try:
        comm = g.community_multilevel(weights=weights if weights else None)
    finally:
        random.setstate(state)  # no side effect on the caller's RNG
    membership = comm.membership

    # Tentative label per community = its top members by graph degree.
    by_comm: dict[int, list[str]] = {}
    for nid, c in zip(ids, membership):
        by_comm.setdefault(c, []).append(nid)
    labels: dict[int, str] = {}
    for c, members in by_comm.items():
        top = sorted(members, key=lambda x: degree.get(x, 0), reverse=True)[:_LABEL_TOP]
        labels[c] = " · ".join(names.get(m, m) for m in top)

    out = []
    for nid in ids:
        i = idx[nid]
        c = membership[i]
        out.append({
            "id": nid,
            "betweenness": round(bet[i], 6),
            "closeness": round(clo[i], 6),
            "community": int(c),
            "community_label": labels[c],
        })
    return out


def write(driver, rows: list[dict]) -> None:
    """Overwrite the four metric properties on each node, in UNWIND batches."""
    q = """
    UNWIND $rows AS row
    MATCH (n {entity_id: row.id})
    SET n.betweenness = row.betweenness,
        n.closeness = row.closeness,
        n.community = row.community,
        n.community_label = row.community_label
    """
    with driver.session() as s:
        for i in range(0, len(rows), _BATCH):
            s.run(q, rows=rows[i:i + _BATCH])
    log.info("network_metrics_written", nodes=len(rows))


def _preview(nodes: list[dict], rows: list[dict]) -> None:
    """Print top-betweenness and the largest communities — for read-only
    verification (AC3/AC5) without touching the graph."""
    name = {n["id"]: n["name"] for n in nodes}
    by_id = {r["id"]: r for r in rows}
    top = sorted(rows, key=lambda r: r["betweenness"], reverse=True)[:15]
    print("\n== Top intermediación (betweenness) ==")
    for r in top:
        print(f"  {r['betweenness']:.4f}  {name.get(r['id'], r['id'])}  [grado en props no aquí]")
    comm: dict[int, list[str]] = {}
    for r in rows:
        comm.setdefault(r["community"], []).append(r["id"])
    print(f"\n== Comunidades ({len(comm)}) — las 8 mayores ==")
    for c, members in sorted(comm.items(), key=lambda kv: len(kv[1]), reverse=True)[:8]:
        label = by_id[members[0]]["community_label"]
        print(f"  c{c} ({len(members)} miembros): {label}")


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and preview top-betweenness + communities WITHOUT writing.")
    args = ap.parse_args()

    driver = get_driver()
    try:
        nodes, edges = read_graph(driver)
        if not nodes:
            log.warning("network_metrics_empty_graph")
            return
        rows = compute(nodes, edges)
        n_comm = len({r["community"] for r in rows})
        if args.dry_run:
            _preview(nodes, rows)
            log.info("network_metrics_dry_run", nodes=len(nodes), edges=len(edges),
                     communities=n_comm)
            return
        write(driver, rows)
        log.info("network_metrics_done", nodes=len(nodes), edges=len(edges),
                 communities=n_comm)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
