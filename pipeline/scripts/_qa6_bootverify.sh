#!/bin/bash
# Boot-verify del tar 20260902-qa5 (QA pase 5, co-menciones): extrae el tgz a un dir VM-local
# REPLICANDO el seed-entrypoint de prod (`tar xz --strip-components=1`, espera wrapper `data/`),
# arranca un Neo4j fresco y comprueba counts + spot-checks del pase. Despacho por PATH:
#   container machine run -n sm-dev --root -- bash <repo>/pipeline/scripts/_qa6_bootverify.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TGZ="$REPO/neo4j/neo4j-data-20260902-qa5.tgz"
STORE=/var/lib/sm-qa6-bootverify
BV=sm_neo4j_bootverify_qa6

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
cy(){ docker exec "$BV" cypher-shell -u neo4j -p neo4j --format plain "$1"; }
echo "# counts (esperado 8600 / 36941)"
cy "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
echo "# bidir_pairs (esperado 0)"
cy "MATCH (a)-[r1:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(b)-[r2:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(a) WHERE id(a) < id(b) AND type(r1)=type(r2) RETURN count(DISTINCT [id(a),id(b)]) AS bidir_pairs;"
echo "# ALVA (esperado role no nulo, grado 4)"
cy "MATCH (n {entity_id:'Person:jose-luis-alvarez-trujillo'}) RETURN n.role IS NOT NULL AS has_role, size([(n)-[r]-(m) WHERE m.entity_id IS NOT NULL AND NOT type(r) IN ['MENTIONED_IN','PART_OF'] | 1]) AS deg;"
echo "# Hermanos Fernández Menéndez (esperado el nombre nuevo)"
cy "MATCH (n {entity_id:'Organization:fernandez-menendez'}) RETURN n.name;"
echo "# UCO OCCURRED_AT → Madrid (esperado 0)"
cy "MATCH (u {entity_id:'Organization:unidad-central-operativa'})-[r:OCCURRED_AT]->(l) WHERE l.entity_id CONTAINS 'madrid' RETURN count(r);"
echo "# nodos-alias fusionados + stubs podados triage5d (esperado 0)"
cy "MATCH (n) WHERE n.entity_id IN ['Person:fernando-codobes','Person:santos-jefe-psoe','Person:daniel206','Person:cueto','Person:cristian','Person:pepito','Person:aforado','Event:factura','Organization:secretario-de-organizacion'] RETURN count(n);"
echo "# inverts Cátedra TSC (esperado 2 Event→Cátedra, 0 al revés)"
cy "MATCH (e)-[r:OCCURRED_AT]->(c {entity_id:'Organization:catedra-extraordinaria-de-transformacion-social-competitiva'}) WHERE r.source_chunk_id IN ['e99fbf210f632776','fd4213b0d7c04577'] RETURN count(r) AS ok; MATCH (c {entity_id:'Organization:catedra-extraordinaria-de-transformacion-social-competitiva'})-[r:OCCURRED_AT]->(e) WHERE r.source_chunk_id IN ['e99fbf210f632776','fd4213b0d7c04577'] RETURN count(r) AS bad;"
docker rm -f "$BV" >/dev/null
rm -rf "$STORE"
echo "✅ boot-verify done (container y store temporales eliminados)"
