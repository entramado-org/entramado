#!/bin/bash
# Deploy de la REMEDIACIÓN de la auditoría mercantil (informe mercantil_validation_20260708.md)
# a PROD vía Path A (forward-seed). Delta = correcciones de props (rol/note/borme_id/hojas) en
# aristas/nodos existentes + 4 aristas ADMINISTRA both-exist nuevas (Guillermo Amaro, typo BORME
# "GUILEROMO") + props en_liquidacion (LIC). Todo se edita sobre la SEMILLA ($REPO/neo4j/data)
# vía un neo4j EFÍMERO publicado en :7688; al final un único forward-seed al store vivo.
# Solo `reseed` tiene downtime breve de neo4j (api/mcp/caddy siguen y reconectan).
#
# Corre DENTRO de sm-prod. Dispatch por PATH (args ASCII; brackets/quotes viven aquí):
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_mercantil_audit_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
BAK="$STORE/data.bak-audit"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

# Las aristas tocadas por la remediación (existentes corregidas + las 4 nuevas de Guillermo).
edges_q="MATCH (a)-[r:ADMINISTRA {source_kind:'mercantil'}]->(b)
WHERE (a.entity_id='Person:antonio-martin-jimenez' AND b.entity_id='Company:boreal-sinergias-sl')
   OR (a.entity_id='Person:guillermo-alfredo-amaro-chacon')
   OR (a.entity_id='Person:juan-carlos-barrabes' AND b.entity_id='Company:telefonica-open-innovation-sl')
   OR (a.entity_id='Person:patricia-ramos-garcia' AND b.entity_id='Company:deluxe-fortune-sl')
   OR (a.entity_id='Person:julio-martinez-martinez' AND b.entity_id='Company:bodegas-victorianas-sa')
   OR (a.entity_id='Person:julio-martinez-martinez' AND b.entity_id='Company:idella-consulenza-strategica')
RETURN a.entity_id AS src, b.entity_id AS dst, r.rol AS rol ORDER BY src, dst;"

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
  # Aplica un archivo .cypher del repo al efímero (los literales con acentos viven en el archivo).
  apply-edit)   docker exec -i "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain < "$REPO/${2:?usage: apply-edit <repo-relative .cypher>}" ;;
  # Solo-lectura contra el vivo desde archivo (el quoting de `container machine run` rompe queries inline).
  query-live)   docker exec -i "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain < "$REPO/${2:?usage: query-live <repo-relative .cypher>}" ;;
  bidir-edit)   cyq_edit "MATCH (a)-[r1]->(b)-[r2]->(a) WHERE type(r1)=type(r2) AND elementId(a)<elementId(b) RETURN count(*) AS bidir_pairs;" ;;

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

  *) echo "usage: $0 {counts-live|verify-live|edit-up|counts-edit|verify-edit|apply-edit <file>|bidir-edit|edit-down|reseed|drop-backup}" >&2; exit 2 ;;
esac
