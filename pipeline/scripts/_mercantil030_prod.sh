#!/bin/bash
# Deploy spec 030 (mercantil tanda 5) to PROD via Path A (forward-seed).
# Delta = 3 ADMINISTRA both-exist edges (source_kind='mercantil'), 0 new nodes.
# The loader+centralidad run from the MAC (uv) against the EPHEMERAL neo4j published
# here on :7688 over the SEED ($REPO/neo4j/data, home-mounted). Then the whole seed is
# forward-seeded into the VM-local live store. Live stays up during the edit phase;
# only the `reseed` step has a brief neo4j downtime (api/mcp/caddy stay up, reconnect).
#
# Runs INSIDE sm-prod. Dispatch by PATH (args stay ASCII; brackets/quotes live here):
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_mercantil030_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
BAK="$STORE/data.bak-spec030"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

edges_q="MATCH (a)-[r:ADMINISTRA {source_kind:'mercantil'}]->(b)
WHERE (a.entity_id='Person:antonio-martin-jimenez' AND b.entity_id='Company:boreal-sinergias-sl')
   OR (a.entity_id='Company:deluxe-fortune-sl' AND b.entity_id='Company:bocata-food-sl')
   OR (a.entity_id='Company:businesscity-sl' AND b.entity_id='Company:future-factory-sl')
RETURN a.entity_id AS src, b.entity_id AS dst;"

case "${1:-}" in
  counts-live)  cyq_live "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;" ;;
  verify-live)  cyq_live "$edges_q" ;;

  edit-up)
    docker rm -f "$EDIT" >/dev/null 2>&1 || true
    docker run -d --name "$EDIT" -p 0.0.0.0:7688:7687 \
      -v "$REPO/neo4j/data:/data" \
      -e NEO4J_AUTH=neo4j/"$PW" \
      -e NEO4J_PLUGINS='["apoc"]' \
      -e NEO4J_dbms_security_procedures_unrestricted='apoc.*' \
      neo4j:5.26 >/dev/null
    echo "waiting for $EDIT to accept bolt…"
    for i in $(seq 1 60); do
      if docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" "RETURN 1;" >/dev/null 2>&1; then
        echo "✅ $EDIT ready on :7688"; exit 0
      fi
      sleep 2
    done
    echo "❌ $EDIT did not become ready" >&2; docker logs --tail 30 "$EDIT" >&2; exit 1 ;;

  counts-edit)  cyq_edit "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;" ;;
  verify-edit)  cyq_edit "$edges_q" ;;

  edit-down)
    docker stop -t 40 "$EDIT" && docker rm "$EDIT"
    echo "✅ $EDIT stopped cleanly (consistent store)" ;;

  reseed)
    if [ -e "$BAK" ]; then echo "❌ $BAK already exists — remove it first" >&2; exit 1; fi
    echo "stopping live neo4j ($LIVE)…"; docker stop "$LIVE"
    echo "backup: mv $STORE/data -> $BAK (instant, same fs)"; mv "$STORE/data" "$BAK"
    echo "forward-seed: cp -a \$REPO/neo4j/data -> $STORE/data"; mkdir -p "$STORE/data"; cp -a "$REPO/neo4j/data/." "$STORE/data/"
    chown -R 7474:7474 "$STORE/data"
    echo "starting live neo4j ($LIVE)…"; docker start "$LIVE"
    echo "✅ reseed done (backup kept at $BAK)" ;;

  drop-backup)  rm -rf "$BAK" && echo "removed $BAK" ;;

  *) echo "usage: $0 {counts-live|verify-live|edit-up|counts-edit|verify-edit|edit-down|reseed|drop-backup}" >&2; exit 2 ;;
esac
