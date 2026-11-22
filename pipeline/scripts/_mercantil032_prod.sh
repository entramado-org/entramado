#!/bin/bash
# Deploy spec 032 (mercantil dimensión 3: participaciones/cadena de control) to PROD.
# Delta = 4 both-exist ADMINISTRA edges (dim3) + 1 node MERGE (Deluxe Fortune s-l → sl).
#
# ⚠️ HEAVY-WRITE path (NOT Path A forward-seed): a node merge (apoc.refactor) may NOT persist through
# the ephemeral-over-virtiofs seed edit (documented gotcha: Carmen Pano/Villafuel). So we edit a
# **VM-LOCAL working copy** of the store (ext4, merges persist), PROMOTE it to live, then reverse-seed
# the finished store back to $REPO/neo4j/data. The Mac runs the loaders against the ephemeral on :7688.
#
# seed==live is verified operationally: WORK is copied from the seed ($REPO/neo4j/data), and `counts-edit`
# BEFORE any Mac edit MUST equal `counts-live` — else the seed is stale → abort before promote.
#
# Runs INSIDE sm-prod. Dispatch by PATH (args stay ASCII; inline cypher would break the dispatch):
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_mercantil032_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
WORK="$STORE/data.dim3"
BAK_LIVE="$STORE/data.bak-dim3"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
merc_q="MATCH (n) WHERE n.is_mercantil RETURN count(n) AS is_mercantil;"
deluxe_q="MATCH (n) WHERE n.entity_id IN ['Company:deluxe-fortune-sl','Company:deluxe-fortune-s-l'] RETURN n.entity_id AS id, n.name AS nm ORDER BY id;"
dim3_q="MATCH (a)-[e:ADMINISTRA {source_kind:'mercantil'}]->(b) WHERE a.entity_id IN ['Company:snip-aviation-sl','Company:flyspain-s-l','Company:llitarrada-innova-sl'] AND b.entity_id IN ['Company:plus-ultra-lineas-aereas','Company:formiguero-barrabes-sl','Company:step-one'] RETURN a.entity_id AS src, b.entity_id AS tgt, e.borme_id AS borme ORDER BY src, tgt;"

case "${1:-}" in
  counts-live)  cyq_live "$counts_q"; cyq_live "$merc_q"; cyq_live "$deluxe_q" ;;
  dim3-live)    cyq_live "$dim3_q" ;;

  mkwork)
    if [ -e "$WORK" ]; then echo "❌ $WORK already exists — remove it first" >&2; exit 1; fi
    echo "copying SEED \$REPO/neo4j/data -> $WORK (VM-local working copy)…"
    mkdir -p "$WORK"; cp -a "$REPO/neo4j/data/." "$WORK/"
    chown -R 7474:7474 "$WORK"
    echo "✅ work copy ready ($(du -sh "$WORK" | cut -f1))" ;;

  edit-up)
    docker rm -f "$EDIT" >/dev/null 2>&1 || true
    docker run -d --name "$EDIT" -p 0.0.0.0:7688:7687 \
      -v "$WORK:/data" \
      -e NEO4J_PLUGINS='["apoc"]' \
      -e NEO4J_dbms_security_procedures_unrestricted='apoc.*' \
      neo4j:5.26 >/dev/null
    echo "waiting for $EDIT to accept bolt…"
    for i in $(seq 1 90); do
      if docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" "RETURN 1;" >/dev/null 2>&1; then
        echo "✅ $EDIT ready on :7688 (over $WORK)"; exit 0
      fi
      sleep 2
    done
    echo "❌ $EDIT did not become ready" >&2; docker logs --tail 30 "$EDIT" >&2; exit 1 ;;

  counts-edit)  cyq_edit "$counts_q"; cyq_edit "$merc_q"; cyq_edit "$deluxe_q" ;;
  dim3-edit)    cyq_edit "$dim3_q" ;;

  edit-down)
    docker stop -t 40 "$EDIT" && docker rm "$EDIT"
    echo "✅ $EDIT stopped cleanly (consistent VM-local store at $WORK)" ;;

  promote)
    if [ -e "$BAK_LIVE" ]; then echo "❌ $BAK_LIVE already exists — remove it first" >&2; exit 1; fi
    if [ ! -e "$WORK/databases" ]; then echo "❌ $WORK/databases missing — did the edit persist?" >&2; exit 1; fi
    echo "stopping live neo4j ($LIVE)…"; docker stop "$LIVE"
    echo "backup: mv $STORE/data -> $BAK_LIVE (instant, same fs)"; mv "$STORE/data" "$BAK_LIVE"
    echo "promote: mv $WORK -> $STORE/data"; mv "$WORK" "$STORE/data"
    chown -R 7474:7474 "$STORE/data"
    echo "starting live neo4j ($LIVE)…"; docker start "$LIVE"
    echo "✅ promote done (live backup kept at $BAK_LIVE)" ;;

  reverse-seed)
    # Safe reverse-seed (session-71 pattern): a hot cp of a running neo4j store is inconsistent →
    # brief-downtime snapshot while stopped, restart, then copy the snapshot to the Mac (no downtime).
    SBAK="$REPO/neo4j/data.bak-dim3"
    SNAP="$STORE/_snap-dim3"
    if [ -e "$SBAK" ]; then echo "❌ $SBAK already exists — remove it first" >&2; exit 1; fi
    if [ -e "$SNAP" ]; then echo "❌ $SNAP already exists — remove it first" >&2; exit 1; fi
    echo "stopping live neo4j for consistent snapshot…"; docker stop "$LIVE"
    echo "snapshot: cp -a $STORE/data -> $SNAP (ext4->ext4, seconds)"; cp -a "$STORE/data" "$SNAP"
    echo "restarting live neo4j (downtime ends)…"; docker start "$LIVE"
    echo "backup seed: mv \$REPO/neo4j/data -> $SBAK"; mv "$REPO/neo4j/data" "$SBAK"
    echo "reverse-seed: cp -a $SNAP -> \$REPO/neo4j/data (snapshot -> seed; ~566M over home-mount, neo4j UP)…"
    mkdir -p "$REPO/neo4j/data"; cp -a "$SNAP/." "$REPO/neo4j/data/"
    echo "cleanup snapshot: rm -rf $SNAP"; rm -rf "$SNAP"
    echo "✅ reverse-seed done (seed==live). Old seed kept at $SBAK" ;;

  drop-backups)
    rm -rf "$BAK_LIVE" "$REPO/neo4j/data.bak-dim3" && echo "removed VM + seed backups" ;;

  *) echo "usage: $0 {counts-live|dim3-live|mkwork|edit-up|counts-edit|dim3-edit|edit-down|promote|reverse-seed|drop-backups}" >&2; exit 2 ;;
esac
