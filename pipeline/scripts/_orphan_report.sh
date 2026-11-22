#!/bin/bash
# READ-ONLY diagnostic: entity nodes with ZERO entity-entity edges in the LIVE graph,
# bucketed by label and by the doc_type of the documents that mention them.
# Dispatch by PATH inside sm-prod:
#   container machine run -n sm-prod --root -- bash .../pipeline/scripts/_orphan_report.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
cyq(){ docker exec sm_neo4j cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

orphan_where="n.entity_id IS NOT NULL
AND NOT EXISTS { MATCH (n)-[r]-(m) WHERE m.entity_id IS NOT NULL
                 AND NOT type(r) IN ['MENTIONED_IN','PART_OF'] }"

case "${1:-}" in
  by-label)
    cyq "MATCH (n) WHERE $orphan_where
         RETURN labels(n)[0] AS tipo, count(n) AS huerfanos ORDER BY huerfanos DESC;" ;;
  by-doctype)
    cyq "MATCH (n) WHERE $orphan_where
         OPTIONAL MATCH (n)-[:MENTIONED_IN]->(:Chunk)-[:PART_OF]->(d:Document)
         WITH n, collect(DISTINCT d.doc_type) AS dts
         RETURN CASE WHEN size(dts)=0 THEN ['sin_menciones'] ELSE dts END AS origen,
                count(n) AS huerfanos ORDER BY huerfanos DESC;" ;;
  list)
    cyq "MATCH (n) WHERE $orphan_where
         OPTIONAL MATCH (n)-[:MENTIONED_IN]->(c:Chunk)-[:PART_OF]->(d:Document)
         WITH n, count(c) AS menciones, collect(DISTINCT d.doc_type) AS dts
         RETURN n.entity_id AS id, menciones, dts ORDER BY menciones DESC LIMIT 200;" ;;
  *) echo "usage: $0 {by-label|by-doctype|list}" >&2; exit 2 ;;
esac
