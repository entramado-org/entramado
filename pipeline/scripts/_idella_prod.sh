#!/bin/bash
# Apply the Plus Ultra ↔ Idella overassertion fix to PROD via Path A (ephemeral over
# seed + forward-seed). Delta = -2 edges, both denied durably in relations.yaml:
#   (Company:plus-ultra-lineas-aereas)-[:PAID]->(Company:idella-consulenza-strategica)
#     — auto-zapatero p.55 documents the SIGNING of the 1% contract; p.33: AEAT feb-2026
#       says no payment was ever executed. Contract itself stays modeled (PARTY_TO +
#       Contrato Idella-Plus Ultra 19/01/2021 node).
#   (Company:idella-consulenza-strategica)-[:RECEIVED]->(Company:plus-ultra-lineas-aereas)
#     — informe-udef-1908 p.137 "percibiría" (pure conditional), direction inverted too.
#
# Unlike _flowfix_prod.sh there is NO cypher patch: the edit is `make relations`
# (the durable overlay loader) run FROM THE MAC against the ephemeral on :7688
# (NEO4J_URI=bolt://<vm-ip>:7688), which applies the two new deny entries and
# recomputes degree. Then `make centralidad` from the Mac, then forward-seed.
#
# Runs INSIDE sm-prod. Dispatch by PATH (args stay ASCII):
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_idella_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
BAK="$STORE/data.bak-idella"
DIRTY="$REPO/neo4j/data.dirty-idella"
REHEARSAL="$REPO/neo4j/data.rehearsal"
PATCH="$REPO/pipeline/data/audit/patches/manual_fix_idella_plusultra_20260709.cypher"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

# Presence check: the two overasserted edges (1+1 before, 0+0 after).
verify_q="MATCH (pu {entity_id:'Company:plus-ultra-lineas-aereas'})-[r:PAID]->(id {entity_id:'Company:idella-consulenza-strategica'})
WITH count(r) AS paid_edge
MATCH (id2 {entity_id:'Company:idella-consulenza-strategica'})
OPTIONAL MATCH (id2)-[r2:RECEIVED]->(pu2 {entity_id:'Company:plus-ultra-lineas-aereas'})
RETURN paid_edge, count(r2) AS received_edge;"

# The contract modeling that must SURVIVE the deny (PARTY_TO both ways + contract node).
party_q="MATCH (a {entity_id:'Company:idella-consulenza-strategica'})-[r:PARTY_TO]-(b)
RETURN startNode(r).entity_id AS de, endNode(r).entity_id AS a_nodo ORDER BY de, a_nodo;"

# Diagnostic: per-type edge counts (diff live vs edit to localize unexpected deltas).
types_q="MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS n ORDER BY t;"

# Diagnostic: every SUBSIDIARY_OF edge with endpoints+doc (diff live vs edit).
subs_q="MATCH (a)-[r:SUBSIDIARY_OF]->(b)
RETURN a.entity_id AS de, b.entity_id AS a_nodo, coalesce(r.source_doc,'') AS doc
ORDER BY de, a_nodo, doc;"

# Diagnostic: full entity-edge dump (diff live vs edit to enumerate every change).
alledges_q="MATCH (a)-[r]->(b)
WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
RETURN type(r) AS t, a.entity_id AS de, b.entity_id AS a_nodo,
       coalesce(r.source_doc,'') AS doc, left(coalesce(r.source_quote,''),40) AS quote
ORDER BY t, de, a_nodo, doc, quote;"

case "${1:-}" in
  counts-live)  cyq_live "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;" ;;
  verify-live)  cyq_live "$verify_q" ;;
  party-live)   cyq_live "$party_q" ;;
  types-live)   cyq_live "$types_q" ;;
  types-edit)   cyq_edit "$types_q" ;;
  subs-live)    cyq_live "$subs_q" ;;
  subs-edit)    cyq_edit "$subs_q" ;;
  alledges-live) cyq_live "$alledges_q" ;;
  alledges-edit) cyq_edit "$alledges_q" ;;

  # Full props of the two edges (for the rollback file).
  props-live)
    cyq_live "MATCH (a {entity_id:'Company:plus-ultra-lineas-aereas'})-[r:PAID]->(b {entity_id:'Company:idella-consulenza-strategica'}) RETURN properties(r) AS props;"
    cyq_live "MATCH (a {entity_id:'Company:idella-consulenza-strategica'})-[r:RECEIVED]->(b {entity_id:'Company:plus-ultra-lineas-aereas'}) RETURN properties(r) AS props;" ;;

  apply-edit)   docker exec -i "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain < "$PATCH" ;;
  degree-edit)  cyq_edit "MATCH (n) WHERE n.entity_id IS NOT NULL SET n.degree = COUNT { (n)--() };" ;;

  # Discard a dirty seed and restore it from the LIVE store (reverse-seed).
  # The dirty seed is parked at $DIRTY (home-mount) for inspection; purge later.
  restore-seed)
    if docker ps --format '{{.Names}}' | grep -q "^$EDIT$"; then
      echo "ERROR ephemeral $EDIT still running — edit-down first" >&2; exit 1
    fi
    if [ -e "$DIRTY" ]; then echo "ERROR $DIRTY already exists — remove it first" >&2; exit 1; fi
    echo "stopping live neo4j ($LIVE)…"; docker stop -t 60 "$LIVE"
    echo "parking dirty seed: mv \$REPO/neo4j/data -> $DIRTY"; mv "$REPO/neo4j/data" "$DIRTY"
    echo "reverse-seed: cp -a $STORE/data -> \$REPO/neo4j/data"; mkdir -p "$REPO/neo4j/data"; cp -a "$STORE/data/." "$REPO/neo4j/data/"
    echo "starting live neo4j ($LIVE)…"; docker start "$LIVE"
    echo "OK seed restored from live (dirty seed parked at $DIRTY)" ;;

  drop-dirty)   rm -rf "$DIRTY" && echo "removed $DIRTY" ;;

  # Dress-rehearsal of `make relations` on a disposable COPY of the seed: same
  # ephemeral container name, so every *-edit subcommand works against it too.
  # Copy the seed first (from the Mac: cp -a neo4j/data neo4j/data.rehearsal).
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

  counts-edit)  cyq_edit "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;" ;;
  verify-edit)  cyq_edit "$verify_q" ;;
  party-edit)   cyq_edit "$party_q" ;;

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

  *) echo "usage: $0 {counts-live|verify-live|party-live|edit-up|counts-edit|verify-edit|party-edit|edit-down|reseed|drop-backup}" >&2; exit 2 ;;
esac
