#!/bin/bash
# READ-ONLY: counts del grafo vivo para el parity gate del recon BOE (spec 048).
#   container machine run -n sm-prod --root -- bash \
#     <repo>/pipeline/scripts/_boe_parity_counts.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
docker exec sm_neo4j cypher-shell -u neo4j -p "$PW" --format plain \
  "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
