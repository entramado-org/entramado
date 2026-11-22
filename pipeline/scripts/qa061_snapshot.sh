#!/usr/bin/env bash
# Run by PATH inside sm-dev; only creates a new seed-only, read-only Neo4j.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SEED="$REPO/neo4j/neo4j-data-20260909-qa061.tgz"
EXPECTED=ddacd74f56c770f2b4f5ed6b38cc49ff4764274dab6b33e41b093e532e50b61d
STORE=/var/lib/sm-integral-20260909
NAME=sm_neo4j_integral_20260909
case "${1:-}" in
  up)
    test ! -e "$STORE"
    test "$(sha256sum "$SEED" | cut -d' ' -f1)" = "$EXPECTED"
    mkdir -p "$STORE/data"
    tar -xzf "$SEED" -C "$STORE/data" --strip-components=1
    test -d "$STORE/data/databases"
    chown -R 7474:7474 "$STORE/data"
    # No host ports. Queries run inside the isolated container.
    docker run -d --name "$NAME" -v "$STORE/data:/data" \
      -e NEO4J_AUTH=none -e NEO4J_server_databases_default__to__read__only=true \
      neo4j:5.26
    ;;
  counts)
    docker exec "$NAME" cypher-shell --format plain \
      'MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes,count(r) AS relationships;'
    docker exec "$NAME" cypher-shell --format plain \
      'MATCH (n) RETURN sum(size(keys(n))) AS node_properties;'
    docker exec "$NAME" cypher-shell --format plain \
      'MATCH ()-[r]->() RETURN sum(size(keys(r))) AS relationship_properties;'
    docker exec "$NAME" cypher-shell --format plain \
      "SHOW DATABASES YIELD name, access WHERE name='neo4j' RETURN name, access;"
    ;;
  export)
    # Export complete JSONL via the Python driver; no APOC required.
    OUT="$REPO/neo4j/integral-20260909"
    mkdir -p "$OUT"
    docker exec "$NAME" cypher-shell --format plain \
      'MATCH (n) RETURN elementId(n) AS ref, labels(n) AS labels, properties(n) AS props ORDER BY ref;' > "$OUT/nodes.cypher.txt"
    # Use the Python driver bundled in our existing API image, in this network namespace.
    docker run --rm --network "container:$NAME" \
      -v "$REPO:/repo:ro" -v "$OUT:/out" --entrypoint python \
      "${API_IMAGE:-entramado-api:latest}" /repo/pipeline/scripts/qa_integral_snapshot.py
    ;;
  stop)
    docker stop -t 40 "$NAME"
    ;;
  *) echo 'usage: qa_integral_seed.sh up|counts|export|stop' >&2; exit 2 ;;
esac
