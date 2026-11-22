#!/bin/bash
# Apply the ÚLTIMA-HORA overlay (make ultima-hora) to PROD via Path A (forward-seed).
# Delta = the vigía 8-jul-2026 edit: +1 node (Person:joaquin-parra-paez) +1 edge
# (ASSOCIATED_WITH Joaquín Parra Páez -> Leire Díez, source_kind='ultima_hora').
# The loader+collapse+centralidad run from the MAC (uv) against the EPHEMERAL neo4j
# published here on :7688 over the SEED ($REPO/neo4j/data, home-mounted). Then the whole
# seed is forward-seeded into the VM-local live store. Live stays up during the edit
# phase; only `reseed` has a brief neo4j downtime (api/mcp/caddy stay up, reconnect).
#
# Distinct backup name (data.bak-ultimahora) so it never collides with the mercantil
# spec030 rollback (data.bak-spec030) already present in the store.
#
# Runs INSIDE sm-prod. Dispatch by PATH (args stay ASCII; brackets/quotes live here):
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_ultimahora_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
BAK="$STORE/data.bak-ultimahora"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

# Presence check: Parra node + his última-hora edge to Leire Díez (by entity_id OR name).
verify_q="MATCH (p:Person)
WHERE p.entity_id='Person:joaquin-parra-paez' OR p.name='Joaquín Parra Páez'
OPTIONAL MATCH (p)-[r:ASSOCIATED_WITH {source_kind:'ultima_hora'}]->(l:Person)
WHERE l.entity_id='Person:leire-diez' OR l.name='Leire Díez'
RETURN p.entity_id AS parra, p.source_kind AS kind, count(r) AS edge_to_leire;"

case "${1:-}" in
  counts-live)  cyq_live "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;" ;;
  verify-live)  cyq_live "$verify_q" ;;

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
  verify-edit)  cyq_edit "$verify_q" ;;

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
