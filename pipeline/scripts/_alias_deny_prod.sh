#!/bin/bash
# Deploy spec 025 alias_deny to the LIVE prod store (sm_neo4j) — surgical.
# covid/Fiscalía are already at the loader's fixpoint live (19/7), so the only real
# delta is dropping the bare "LANDSIDE" alias from Company:landside-dubai-fzco. The
# SEED (./neo4j/data) gets the full `make relations` alias_deny separately (idempotent).
# Runs INSIDE sm-prod. Dispatch (args stay ASCII, cypher/brackets live in this file):
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_alias_deny_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
NEO=sm_neo4j
cyq(){ docker exec "$NEO" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

case "${1:-}" in
  counts)
    cyq "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;" ;;

  verify)   # alias counts for the 3 targets + whether the bare LANDSIDE alias is present
    cyq "MATCH (n {entity_id:'Company:landside-dubai-fzco'}) RETURN 'landside-dubai' AS node, size(coalesce(n.aliases,[])) AS aliases, size([a IN coalesce(n.aliases,[]) WHERE a='LANDSIDE']) AS bare_landside;"
    cyq "MATCH (n {entity_id:'Event:comision-de-investigacion-sobre-los-contratos-publicos-duran'}) RETURN 'covid' AS node, size(coalesce(n.aliases,[])) AS aliases;"
    cyq "MATCH (n {entity_id:'Organization:fiscalia'}) RETURN 'fiscalia' AS node, size(coalesce(n.aliases,[])) AS aliases;" ;;

  apply)    # drop only the bare "LANDSIDE" alias (idempotent); keeps the full forms
    cyq "MATCH (n {entity_id:'Company:landside-dubai-fzco'}) WITH n, size(coalesce(n.aliases,[])) AS before SET n.aliases=[a IN coalesce(n.aliases,[]) WHERE a<>'LANDSIDE'] RETURN before AS before_aliases, size(n.aliases) AS after_aliases;" ;;

  *) echo "usage: $0 {counts|verify|apply}" >&2; exit 2 ;;
esac
