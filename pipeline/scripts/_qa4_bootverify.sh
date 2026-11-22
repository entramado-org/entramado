#!/bin/bash
# Boot-verify del tar qa4a: extrae el tgz a un dir VM-local, arranca un Neo4j fresco
# sobre él y comprueba counts + guard.
# ⚠️ La extracción REPLICA el seed-entrypoint de prod (`tar xz --strip-components=1`,
# que espera wrapper `data/`): un tar sin wrapper pasa un boot-verify ingenuo y arranca
# VACÍO en prod (incidente 31-ago, seed qa4a → hotfix qa4a-r2). Despacho por PATH:
#   container machine run -n sm-dev --root -- bash <repo>/pipeline/scripts/_qa4_bootverify.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TGZ="$REPO/neo4j/neo4j-data-20260901-qa4a.tgz"
STORE=/var/lib/sm-qa4-bootverify
BV=sm_neo4j_bootverify_qa4

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
  "MATCH (a)-[r:MEMBER_OF]->(b {entity_id:'Company:banco-santander'}) RETURN a.entity_id, r.source_kind;"
docker rm -f "$BV" >/dev/null
rm -rf "$STORE"
echo "✅ boot-verify done (container y store temporales eliminados)"
