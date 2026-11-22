#!/bin/bash
# Deploy de la limpieza de summaries dormidos + relabel Duro Felguera a PROD via Path A
# (efímero sobre seed + forward-seed). Delta en vivo:
#   - nodo Duro Felguera: :LegalCase→:Company + entity_id nuevo
#     (patch gateado manual_fix_duro_felguera_relabel_20260710.cypher, ANTES de make context)
#   - summaries re-keyed que ahora cargan: Organization:inac (summary nuevo),
#     Organization:bar-franky (texto retocado), Company:duro-felguera (summary nuevo),
#     Company:caleton-consultores (no-op, texto idéntico al vivo)
#   - role de bar-franky sin "(mal tipado como Persona)" (roles_enriched.yaml)
# Conteos SIN cambio (0 nodos / 0 aristas nuevos) → sin `make centralidad` (props/label only).
# `make context` se corre DESDE EL MAC contra el efímero: NEO4J_URI=bolt://192.168.64.3:7688.
#
# Corre DENTRO de sm-prod. Dispatch por PATH (args ASCII; brackets/quotes viven aquí):
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_duro_summaries_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
BAK="$STORE/data.bak-summaries"
REHEARSAL="$REPO/neo4j/data.rehearsal"
PATCH="$REPO/pipeline/data/audit/patches/manual_fix_duro_felguera_relabel_20260710.cypher"
ROLLBACK="$REPO/pipeline/data/audit/patches/manual_fix_duro_felguera_relabel_20260710.rollback.cypher"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

# El delta esperado en los 4 nodos tocados (labels + summary_updated + role de bar-franky).
verify_q="MATCH (n) WHERE n.entity_id IN
  ['Company:duro-felguera','LegalCase:duro-felguera','Organization:inac',
   'Organization:bar-franky','Company:caleton-consultores']
RETURN n.entity_id AS id, labels(n) AS labels, n.degree AS degree,
       coalesce(n.summary_updated,'<sin summary>') AS sum_upd,
       left(coalesce(n.role,'<sin role>'),55) AS role
ORDER BY id;"

# Dump completo de aristas entidad↔entidad (diff live vs edit — delta esperado: VACÍO;
# la única arista de duro es MENTIONED_IN a chunk, fuera de este dump).
alledges_q="MATCH (a)-[r]->(b)
WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
RETURN type(r) AS t, a.entity_id AS de, b.entity_id AS a_nodo,
       coalesce(r.source_doc,'') AS doc, left(coalesce(r.source_quote,''),40) AS quote
ORDER BY t, de, a_nodo, doc, quote;"

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() WITH nodes, count(r) AS rels
OPTIONAL MATCH (m) WHERE m.entity_id IS NOT NULL AND NOT (m)--()
RETURN nodes, rels, count(m) AS isolated;"

bidir_q="MATCH (a)-[r1]->(b)-[r2]->(a) WHERE type(r1)=type(r2) AND elementId(a)<elementId(b)
RETURN count(*) AS bidir_pairs;"

case "${1:-}" in
  counts-live)   cyq_live "$counts_q" ;;
  counts-edit)   cyq_edit "$counts_q" ;;
  verify-live)   cyq_live "$verify_q" ;;
  verify-edit)   cyq_edit "$verify_q" ;;
  bidir-edit)    cyq_edit "$bidir_q" ;;
  bidir-live)    cyq_live "$bidir_q" ;;
  alledges-live) cyq_live "$alledges_q" ;;
  alledges-edit) cyq_edit "$alledges_q" ;;

  # Efímero sobre la COPIA de la semilla (dress-rehearsal). Copiar antes desde el Mac:
  #   cp -a neo4j/data neo4j/data.rehearsal
  rehearse-up)
    if [ ! -d "$REHEARSAL" ]; then echo "ERROR $REHEARSAL missing — copy the seed first" >&2; exit 1; fi
    docker rm -f "$EDIT" >/dev/null 2>&1 || true
    docker run -d --name "$EDIT" -p 0.0.0.0:7688:7687 \
      -v "$REHEARSAL:/data" \
      -e NEO4J_AUTH=neo4j/"$PW" \
      -e NEO4J_PLUGINS='["apoc"]' \
      -e NEO4J_dbms_security_procedures_unrestricted='apoc.*' \
      neo4j:5.26 >/dev/null
    echo "waiting for $EDIT (rehearsal) to accept bolt…"
    for i in $(seq 1 60); do
      if docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" "RETURN 1;" >/dev/null 2>&1; then
        echo "OK $EDIT ready on :7688 over $REHEARSAL"; exit 0
      fi
      sleep 2
    done
    echo "ERROR $EDIT did not become ready" >&2; docker logs --tail 30 "$EDIT" >&2; exit 1 ;;

  drop-rehearsal) rm -rf "$REHEARSAL" && echo "removed $REHEARSAL" ;;

  # Efímero sobre la SEMILLA REAL (Path A de verdad).
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
        echo "OK $EDIT ready on :7688 over \$REPO/neo4j/data"; exit 0
      fi
      sleep 2
    done
    echo "ERROR $EDIT did not become ready" >&2; docker logs --tail 30 "$EDIT" >&2; exit 1 ;;

  apply-edit)    docker exec -i "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain < "$PATCH" ;;
  rollback-edit) docker exec -i "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain < "$ROLLBACK" ;;

  edit-down)
    docker stop -t 40 "$EDIT" && docker rm "$EDIT"
    echo "OK $EDIT stopped cleanly (consistent store)" ;;

  # Forward-seed: la semilla editada pasa a ser el store vivo (backup instantáneo en la VM).
  reseed)
    if [ -e "$BAK" ]; then echo "ERROR $BAK already exists — remove it first" >&2; exit 1; fi
    if docker ps --format '{{.Names}}' | grep -q "^$EDIT$"; then
      echo "ERROR ephemeral $EDIT still running — edit-down first" >&2; exit 1
    fi
    echo "stopping live neo4j ($LIVE)…"; docker stop -t 60 "$LIVE"
    echo "backup: mv $STORE/data -> $BAK (instant, same fs)"; mv "$STORE/data" "$BAK"
    echo "forward-seed: cp -a \$REPO/neo4j/data -> $STORE/data"; mkdir -p "$STORE/data"; cp -a "$REPO/neo4j/data/." "$STORE/data/"
    chown -R 7474:7474 "$STORE/data"
    echo "starting live neo4j ($LIVE)…"; docker start "$LIVE"
    echo "OK reseed done (backup kept at $BAK)" ;;

  drop-backup)   rm -rf "$BAK" && echo "removed $BAK" ;;

  *) echo "usage: $0 counts-live|counts-edit|verify-live|verify-edit|bidir-edit|alledges-live|alledges-edit|rehearse-up|drop-rehearsal|edit-up|apply-edit|rollback-edit|edit-down|reseed|drop-backup" >&2; exit 1 ;;
esac
