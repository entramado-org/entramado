#!/bin/bash
# Deploy spec 031 Fase 2 (mercantil profundización: concurso/disolución + objeto + histórico
# de capital) to PROD via Path A (forward-seed). Delta = PROPS-ONLY on 4 existing nodes
# (OPR, Have Got Time [is_mercantil false->true], Boreal, IntProsp) → 0 new nodes, 0 new edges.
# `make mercantil` runs from the MAC (uv) against the EPHEMERAL neo4j published here on :7688 over
# the SEED ($REPO/neo4j/data, home-mounted). SET of scalar props persists on the ephemeral-over-seed
# (confirmed gotcha: only *merges* don't persist). Then the whole seed is forward-seeded into the
# VM-local live store. Props-only ⇒ NO `make centralidad` (0 edges/degree change).
#
# Runs INSIDE sm-prod. Dispatch by PATH (args stay ASCII):
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_mercantil031_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
BAK="$STORE/data.bak-spec031"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

props_q="MATCH (n) WHERE n.entity_id IN ['Company:obras-publicas-y-regadios','Company:have-got-time','Company:boreal-sinergias-sl','Company:inteligencia-prospectiva']
RETURN n.entity_id AS id, coalesce(n.is_mercantil,false) AS im, n.en_concurso AS ec, n.disuelta AS di, (n.capital_historico IS NOT NULL) AS ch, (n.objeto_social IS NOT NULL) AS obj ORDER BY id;"
counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
merc_q="MATCH (n) WHERE n.is_mercantil RETURN count(n) AS is_mercantil;"

case "${1:-}" in
  counts-live)  cyq_live "$counts_q"; cyq_live "$merc_q" ;;
  props-live)   cyq_live "$props_q" ;;

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

  counts-edit)  cyq_edit "$counts_q"; cyq_edit "$merc_q" ;;
  props-edit)   cyq_edit "$props_q" ;;

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

  *) echo "usage: $0 {counts-live|props-live|edit-up|counts-edit|props-edit|edit-down|reseed|drop-backup}" >&2; exit 2 ;;
esac
