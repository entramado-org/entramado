#!/bin/bash
# Spec 046 — ephemeral Neo4j inside sm-dev for the contratacion-overlay verification.
# Own store (anonymous volume), bolt published on :7799, destroyed after. Clone of
# _spec042_ephemeral.sh (container machine run loses quoting/stdin → script in repo).
set -euo pipefail
case "${1:-}" in
  up)
    docker rm -f spec046-neo4j >/dev/null 2>&1 || true
    docker run -d --name spec046-neo4j -p 7799:7687 \
      -e NEO4J_AUTH=neo4j/spec046pass \
      -e NEO4J_PLUGINS='["apoc"]' \
      -e NEO4J_dbms_security_procedures_unrestricted=apoc.\* \
      neo4j:5.26
    echo "waiting for bolt..."
    for i in $(seq 1 60); do
      docker exec spec046-neo4j cypher-shell -u neo4j -p spec046pass "RETURN 1" >/dev/null 2>&1 && { echo "✅ up"; exit 0; }
      sleep 2
    done
    echo "❌ neo4j did not come up" >&2; exit 1 ;;
  down)
    docker rm -f spec046-neo4j; echo "✅ removed" ;;
  *) echo "usage: $0 {up|down}" >&2; exit 2 ;;
esac
