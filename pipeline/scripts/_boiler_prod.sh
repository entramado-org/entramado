#!/bin/bash
# Ephemeral edit-neo4j + reseed helpers for the boilerplate-:LegalCase remediation (spec 024).
# Runs INSIDE sm-prod. Dispatch:
#   /opt/homebrew/bin/container machine run -n sm-prod --root -- bash <this-path> <cmd>
# Mirrors findings/post-cutover-graph-edit: edit ./neo4j/data via an ephemeral neo4j on :7688,
# then reseed the VM-local store from ./neo4j/data (with a timestamped backup = rollback).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # ... (repo home-mounted)
STORE=/var/lib/sm-prod-neo4j
EDIT=sm_neo4j_edit
COMPOSE=(docker compose -f "$REPO/docker-compose.prod.yml" -f "$REPO/docker-compose.prod-machine.yml")

case "${1:-}" in
  edit-start)
    # The store is a copy of the LIVE store, so its password is the real .env one (neo4j 5.x
    # keeps creds in the `system` DB → NEO4J_AUTH is ignored on an initialized store). Probing
    # with the wrong password trips Neo4j's AuthenticationRateLimit → read the real one from .env.
    PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
    docker rm -f "$EDIT" >/dev/null 2>&1 || true
    docker run -d --name "$EDIT" -p 0.0.0.0:7688:7687 \
      -v "$REPO/neo4j/data:/data" \
      -e NEO4J_PLUGINS='["apoc"]' \
      -e NEO4J_dbms_security_procedures_unrestricted='apoc.*' \
      neo4j:5.26 >/dev/null
    for i in $(seq 1 90); do
      if docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" "RETURN 1;" >/dev/null 2>&1; then
        echo "edit-ready"; exit 0
      fi
      sleep 2
    done
    echo "edit-timeout" >&2; docker logs --tail 20 "$EDIT" >&2 || true; exit 1 ;;

  count)   # read-only sanity count against the ephemeral
    docker exec "$EDIT" cypher-shell -u neo4j -p changeme --format plain \
      "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;" ;;

  edit-stop)
    docker stop -t 40 "$EDIT" >/dev/null && docker rm "$EDIT" >/dev/null && echo "edit-stopped" ;;

  reseed)
    BAK="$STORE/data.bak-boiler-$(date +%Y%m%d-%H%M%S)"
    cd "$REPO"
    "${COMPOSE[@]}" stop neo4j
    mv "$STORE/data" "$BAK"
    mkdir -p "$STORE/data"
    cp -a "$REPO/neo4j/data/." "$STORE/data/"
    chown -R 7474:7474 "$STORE/data"
    "${COMPOSE[@]}" up -d neo4j
    echo "reseeded; vm-store backup = $BAK" ;;

  *) echo "usage: $0 {edit-start|count|edit-stop|reseed}" >&2; exit 2 ;;
esac
