#!/bin/bash
# Apply the gated transcript-orphan cleanup (manual_fix_transcript_orphans_20260709.cypher)
# to PROD via Path A (ephemeral over seed + forward-seed). Removes the nodes left
# orphaned by the transcript-edge retirement (Option D, PR #157) + Event:trama
# (owner decision 2026-07-09; see gen_transcript_orphan_cleanup.py docstring).
#
# The cypher runs against the EPHEMERAL neo4j published on :7688 over the SEED
# ($REPO/neo4j/data, home-mounted); degree is recomputed in-place; centralidad runs
# from the MAC against :7688; then the whole seed is forward-seeded into the VM-local
# live store. Distinct backup name (data.bak-transcript-orphans).
#
# Runs INSIDE sm-prod. Dispatch by PATH (args stay ASCII):
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_transcript_orphans_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
BAK="$STORE/data.bak-transcript-orphans"
PATCH="$REPO/pipeline/data/audit/patches/manual_fix_transcript_orphans_20260709.cypher"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

# Presence check: the two explicit residuals (should each be 1 before, 0 after).
verify_q="OPTIONAL MATCH (t {entity_id:'Event:trama'})
OPTIONAL MATCH (d {entity_id:'Event:operacion-delorme'})
RETURN count(t) AS trama, count(d) AS operacion_delorme;"

# Degree recompute (same cypher as neo4j_loader.compute_degree).
degree_q="MATCH (n) WHERE n.entity_id IS NOT NULL SET n.degree = COUNT { (n)--() };"

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
        echo "OK $EDIT ready on :7688"; exit 0
      fi
      sleep 2
    done
    echo "ERROR $EDIT did not become ready" >&2; docker logs --tail 30 "$EDIT" >&2; exit 1 ;;

  apply-edit)   docker exec -i "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain < "$PATCH" ;;
  degree-edit)  cyq_edit "$degree_q" ;;
  counts-edit)  cyq_edit "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;" ;;
  verify-edit)  cyq_edit "$verify_q" ;;

  edit-down)
    docker stop -t 40 "$EDIT" && docker rm "$EDIT"
    echo "OK $EDIT stopped cleanly (consistent store)" ;;

  reseed)
    if [ -e "$BAK" ]; then echo "ERROR $BAK already exists — remove it first" >&2; exit 1; fi
    echo "stopping live neo4j ($LIVE)…"; docker stop "$LIVE"
    echo "backup: mv $STORE/data -> $BAK (instant, same fs)"; mv "$STORE/data" "$BAK"
    echo "forward-seed: cp -a \$REPO/neo4j/data -> $STORE/data"; mkdir -p "$STORE/data"; cp -a "$REPO/neo4j/data/." "$STORE/data/"
    chown -R 7474:7474 "$STORE/data"
    echo "starting live neo4j ($LIVE)…"; docker start "$LIVE"
    echo "OK reseed done (backup kept at $BAK)" ;;

  drop-backup)  rm -rf "$BAK" && echo "removed $BAK" ;;

  *) echo "usage: $0 {counts-live|verify-live|edit-up|apply-edit|degree-edit|counts-edit|verify-edit|edit-down|drop-backup|reseed}" >&2; exit 2 ;;
esac
