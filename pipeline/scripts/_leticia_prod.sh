#!/bin/bash
# Apply the Leticia de la Hoz alta (PR #226) to PROD via Path A light (forward-seed).
# Delta = +1 node (Crime:obstruccion-a-la-justicia) +1 edge (ACCUSED_OF status=imputado
# Leticia de la Hoz Calvo -> obstrucción a la justicia, source_kind='ultima_hora')
# + role prop on Person:leticia-de-la-hoz-calvo (context layer). No merges.
# The loaders+collapse+centralidad run from the MAC (uv) against the EPHEMERAL neo4j
# published here on :7688 over the SEED ($REPO/neo4j/data, home-mounted). Then the whole
# seed is forward-seeded into the VM-local live store. Live stays up during the edit
# phase; only `reseed` has a brief neo4j downtime (api/mcp/caddy stay up, reconnect).
#
# Distinct backup name (data.bak-leticia) so it never collides with other backups.
#
# Runs INSIDE sm-prod. Dispatch by PATH (args stay ASCII; brackets/quotes live here):
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_leticia_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
BAK="$STORE/data.bak-leticia"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

# Presence check: Crime node + Leticia's ACCUSED_OF edge (status imputado) + role prop.
verify_q="MATCH (c:Crime)
WHERE c.entity_id='Crime:obstruccion-a-la-justicia' OR c.name='obstrucción a la justicia'
OPTIONAL MATCH (p:Person {entity_id:'Person:leticia-de-la-hoz-calvo'})
OPTIONAL MATCH (p)-[r:ACCUSED_OF {source_kind:'ultima_hora'}]->(c)
RETURN c.entity_id AS crime, c.source_kind AS crime_kind,
       p.entity_id AS leticia, r.status AS edge_status,
       p.role IS NOT NULL AS has_role;"

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
