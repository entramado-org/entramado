#!/bin/bash
# Apply the "Joaquín Parra desde el origen" overlay to PROD via Path A (forward-seed).
# Delta = +1 MENTIONED_IN edge (Parra -> chunk p.41 de autoentradaferraz.pdf) + role +
# summary + degree en Person:joaquin-parra-paez (aplicado con la migración cypher
# parra_ingest_ferraz_20260708.cypher vía qa-migrate contra el efímero :7688).
# Backup propio data.bak-parra (no colisiona con data.bak-spec030 ni data.bak-ultimahora).
#
# Runs INSIDE sm-prod. Dispatch by PATH:
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_parra_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
BAK="$STORE/data.bak-parra"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

verify_q="MATCH (p:Person {entity_id:'Person:joaquin-parra-paez'})
OPTIONAL MATCH (p)-[m:MENTIONED_IN]->(c:Chunk)
RETURN p.degree AS degree,
       (p.role IS NOT NULL AND p.role <> '') AS has_role,
       (p.summary IS NOT NULL AND p.summary <> '') AS has_summary,
       count(m) AS menciones,
       collect(DISTINCT c.doc_id)[..3] AS docs;"

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

  # Apply the migration via cypher-shell (its parser respects string literals with
  # semicolons — unlike qa_audit's naive ';' splitter). Runs against the EPHEMERAL.
  apply)
    echo "aplicando migración Parra al efímero ($EDIT)…"
    docker exec -i "$EDIT" cypher-shell -u neo4j -p "$PW" \
      < "$REPO/pipeline/data/audit/patches/parra_ingest_ferraz_20260708.cypher"
    echo "✅ migración Parra aplicada" ;;

  *) echo "usage: $0 {counts-live|verify-live|edit-up|counts-edit|verify-edit|edit-down|reseed|drop-backup|apply}" >&2; exit 2 ;;
esac
