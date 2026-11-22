"""Dump the NEW central cast for the enrichment swarm — entities introduced by the
10 newly-ingested docs that are not yet enriched (no curated summary, not a context
node) and central enough to be worth a web-sourced role+resumen.

Writes /tmp/cast_batch_NN.json (6 entities/batch, the paths enrich_entities.workflow.js
reads) and prints the batch count to pass as args.nBatches.

Run from pipeline/ — pass the target doc_ids (WITH .pdf) via --docs:
    uv run python -m scripts._enrich_dump_newdocs [person_min] [org_min] --docs <doc1.pdf> ...
With no --docs it falls back to DEFAULT_DOCS (the last enriched batch, for reference).
"""
import argparse
import collections
import json
import os

from neo4j import GraphDatabase

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USERNAME", "neo4j")
PWD = os.getenv("NEO4J_PASSWORD", "changeme")

DEFAULT_DOCS = [
    "DS_C_15_659.pdf",  # Senado, comisión SEPI, 15-abr-2026
    "DS_C_15_717.pdf",  # Senado, comisión SEPI, 28-may-2026
]
NOTABLE = ["Person", "Organization", "Company", "Court", "LegalCase", "Contract"]

_ap = argparse.ArgumentParser()
_ap.add_argument("person_min", nargs="?", type=int, default=8)
_ap.add_argument("org_min", nargs="?", type=int, default=8)
_ap.add_argument("--docs", nargs="+", default=DEFAULT_DOCS, help="doc_ids WITH .pdf")
_args = _ap.parse_args()
person_min, org_min, NEW_DOCS = _args.person_min, _args.org_min, _args.docs

q = """
MATCH (n) WHERE n.entity_id IS NOT NULL AND labels(n)[0] IN $notable
  AND n.summary IS NULL                       // not already enriched
  AND NOT coalesce(n.is_context, false)        // not a curated context-only node
  AND EXISTS { MATCH (n)-[:MENTIONED_IN]->(c:Chunk) WHERE c.doc_id IN $docs }
WITH n, labels(n)[0] AS t, coalesce(n.degree,0) AS deg
WHERE (t IN ['LegalCase','Court','Contract'] AND deg >= 3)
   OR (t = 'Person' AND deg >= $person_min)
   OR (t IN ['Organization','Company'] AND deg >= $org_min)
RETURN n.entity_id AS id, t AS type, n.name AS name,
       n.role AS role, coalesce(n.aliases,[]) AS aliases, deg AS degree
ORDER BY deg DESC, name
"""

driver = GraphDatabase.driver(URI, auth=(USER, PWD))
with driver.session() as s:
    rows = [dict(r) for r in s.run(q, notable=NOTABLE, docs=NEW_DOCS,
                                   person_min=person_min, org_min=org_min)]
driver.close()

# Shape each row like the workflow expects: {name,type,entity_id,current_role,aliases,degree}
cast = [{
    "name": r["name"], "type": r["type"], "entity_id": r["id"],
    "current_role": r.get("role") or "", "aliases": r.get("aliases") or [],
    "degree": r["degree"],
} for r in rows]

# Clear any stale batch files, then write 6/batch.
import glob
for f in glob.glob("/tmp/cast_batch_*.json"):
    os.remove(f)
PER = 6
n_batches = 0
for i in range(0, len(cast), PER):
    fn = f"/tmp/cast_batch_{str(i // PER).zfill(2)}.json"
    json.dump(cast[i:i + PER], open(fn, "w"), ensure_ascii=False, indent=1)
    n_batches += 1
os.makedirs("/tmp/enrich_out", exist_ok=True)

bylabel = collections.Counter(r["type"] for r in cast)
print(f"NEW cast selected: {len(cast)} -> {dict(bylabel)}  (person_min={person_min}, org_min={org_min})")
print(f"n_batches={n_batches}  (pass as args.nBatches to enrich_entities.workflow.js)")
for r in cast[:25]:
    print(f"  deg{r['degree']:>4}  {r['type']:<13} {r['name']}")
