#!/bin/bash
# Boot-verify del tar 20260902-splits: extrae el tgz a un dir VM-local REPLICANDO el
# seed-entrypoint de prod (`tar xz --strip-components=1`, espera wrapper `data/`),
# arranca un Neo4j fresco y comprueba counts + splits del pase s91. Despacho por PATH:
#   container machine run -n sm-dev --root -- bash <repo>/pipeline/scripts/_qa5_bootverify.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TGZ="$REPO/neo4j/neo4j-data-20260902-splits.tgz"
STORE=/var/lib/sm-qa5-bootverify
BV=sm_neo4j_bootverify_qa5

docker rm -f "$BV" >/dev/null 2>&1 || true
rm -rf "$STORE"; mkdir -p "$STORE/data"
tar -xzf "$TGZ" -C "$STORE/data" --strip-components=1
[ -d "$STORE/data/databases" ] || { echo "❌ layout del tar incorrecto (falta wrapper data/)"; exit 1; }
chown -R 7474:7474 "$STORE/data"
docker run -d --name "$BV" -v "$STORE/data:/data" -e NEO4J_AUTH=none neo4j:5.26 >/dev/null
echo "waiting for $BV bolt…"
for i in $(seq 1 90); do
  if docker exec "$BV" cypher-shell -u neo4j -p neo4j "RETURN 1;" >/dev/null 2>&1; then break; fi
  sleep 2
done
docker exec "$BV" cypher-shell -u neo4j -p neo4j --format plain \
  "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
docker exec "$BV" cypher-shell -u neo4j -p neo4j --format plain \
  "MATCH (n {entity_id:'Contract:expediente-3-20-27507-0207'})-[r]-(m) WHERE m.entity_id IS NOT NULL RETURN n.name, count(r);"
docker exec "$BV" cypher-shell -u neo4j -p neo4j --format plain \
  "MATCH (n {entity_id:'Person:maria-del-rosario-cornejo-arribas'}) RETURN n.name; MATCH (x {entity_id:'Person:maria-rosario'}) RETURN x.is_pseudonym;"
docker exec "$BV" cypher-shell -u neo4j -p neo4j --format plain \
  "MATCH (a {entity_id:'Person:jose-luis-abalos'})-[r:WORKS_FOR]->(b {entity_id:'Organization:ministerio-de-transportes-movilidad-y-agenda-urbana'}) RETURN count(r);"
docker rm -f "$BV" >/dev/null
rm -rf "$STORE"
echo "✅ boot-verify done (container y store temporales eliminados)"
