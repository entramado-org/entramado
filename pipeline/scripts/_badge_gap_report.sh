#!/bin/bash
# READ-ONLY diagnostic: procedural-status gaps in the LIVE graph — people the text layer
# (edge status props, curated role/summary) marks as investigated/charged but who have NO
# ACCUSED_OF|CONVICTED_OF edge to a :Crime (so no badge and absent from /investigados),
# plus ACCUSED_OF edges missing their status prop and likely duplicate Person nodes.
# Dispatch by PATH inside sm-prod:
#   container machine run -n sm-prod --root -- bash .../pipeline/scripts/_badge_gap_report.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
cyq(){ docker exec sm_neo4j cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

no_charge="NOT EXISTS { MATCH (n)-[:ACCUSED_OF|CONVICTED_OF]->(:Crime) }"
status_re="(?i).*\\\\b(investigad|imputad|procesad|acusad|condenad).*"

case "${1:-}" in
  edge-status)
    # Persons carrying a procedural status on some OTHER edge type but no charge edge
    cyq "MATCH (n:Person)-[r]-(m) WHERE r.status IS NOT NULL AND $no_charge
         WITH n, collect(DISTINCT type(r)+':'+r.status) AS señales
         OPTIONAL MATCH (n)-[e]-(x) WHERE x.entity_id IS NOT NULL
         WITH n, señales, count(e) AS deg
         RETURN n.entity_id AS id, señales, deg,
                n.role IS NOT NULL AS rol, n.summary IS NOT NULL AS sintesis
         ORDER BY deg DESC;" ;;
  text-status)
    # Persons whose curated role/summary claims a procedural status but no charge edge
    cyq "MATCH (n:Person) WHERE $no_charge
           AND (n.role =~ '$status_re' OR n.summary =~ '$status_re')
         OPTIONAL MATCH (n)-[e]-(x) WHERE x.entity_id IS NOT NULL
         WITH n, count(e) AS deg
         RETURN n.entity_id AS id, deg,
                n.role IS NOT NULL AS rol, n.summary IS NOT NULL AS sintesis,
                left(coalesce(n.role, n.summary),160) AS texto
         ORDER BY deg DESC;" ;;
  missing-status)
    # Charge edges with no status prop (rendered as default 'acusado')
    cyq "MATCH (n)-[r:ACCUSED_OF|CONVICTED_OF]->(c:Crime) WHERE r.status IS NULL
         RETURN n.entity_id AS id, type(r) AS tipo, c.entity_id AS delito,
                r.source_kind AS fuente, r.source_doc AS doc
         ORDER BY id;" ;;
  dupes)
    # Likely duplicate Persons: same first+last token of the name
    cyq "MATCH (n:Person) WHERE n.entity_id IS NOT NULL
         WITH n, split(toLower(coalesce(n.name,'')),' ') AS t
         WHERE size(t) >= 2
         WITH t[0]+' '+t[-1] AS clave, collect(n.entity_id) AS ids
         WHERE size(ids) > 1
         RETURN clave, ids ORDER BY size(ids) DESC;" ;;
  q)
    # Ad-hoc READ query, read from a file path (survives container-machine arg re-quoting)
    cyq "$(cat "$2")" ;;
  *) echo "usage: $0 {edge-status|text-status|missing-status|dupes|q <cypher>}" >&2; exit 2 ;;
esac
