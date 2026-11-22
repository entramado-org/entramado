#!/bin/bash
# Deploy the Juanma Serrano update (PR #203) to PROD: node MERGE (juanma-serrano →
# juan-manuel-serrano-quintana, + correos-y-telegrafos → correos) + context role/edge
# + 2 última-hora edges (→ Leire Díez, → Vicente Fernández).
#
# ⚠️ HEAVY-WRITE path (NOT plain Path A forward-seed): node merges (apoc.refactor) may NOT
# persist through the ephemeral-over-virtiofs seed edit (documented gotcha: Carmen Pano/
# Villafuel). So we edit a **VM-LOCAL working copy** of the store (ext4, merges persist),
# PROMOTE it to live, then reverse-seed the finished store back to $REPO/neo4j/data.
# The Mac runs the loaders against the ephemeral on :7688 (bolt://192.168.64.3:7688).
#
# seed==live is verified operationally: WORK is copied from the seed ($REPO/neo4j/data), and
# `counts-edit` BEFORE any Mac edit MUST equal `counts-live` — else the seed is stale → abort.
#
# Runs INSIDE sm-prod. Dispatch by PATH (args stay ASCII; inline cypher would break dispatch):
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_serrano_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
WORK="$STORE/data.serrano"
BAK_LIVE="$STORE/data.bak-serrano"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
# Both Serrano ids + both Correos ids: post-merge only the canonicals must remain.
serrano_q="MATCH (n) WHERE n.entity_id IN ['Person:juan-manuel-serrano-quintana','Person:juanma-serrano','Organization:correos','Organization:correos-y-telegrafos'] RETURN n.entity_id AS id, n.name AS nm, n.aliases AS aliases ORDER BY id;"
edges_q="MATCH (s {entity_id:'Person:juan-manuel-serrano-quintana'})-[r]-(o) RETURN type(r) AS t, r.source_kind AS kind, CASE WHEN startNode(r)=s THEN 'out' ELSE 'in' END AS dir, o.entity_id AS other ORDER BY t, other;"
bidir_q="MATCH (a)-[r1:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(b)-[r2:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(a) WHERE id(a) < id(b) AND type(r1)=type(r2) RETURN count(DISTINCT [id(a),id(b)]) AS bidir_pairs;"

case "${1:-}" in
  counts-live)  cyq_live "$counts_q"; cyq_live "$serrano_q" ;;
  verify-live)  cyq_live "$serrano_q"; cyq_live "$edges_q"; cyq_live "$bidir_q" ;;

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

  counts-edit)  cyq_edit "$counts_q"; cyq_edit "$serrano_q" ;;
  verify-edit)  cyq_edit "$serrano_q"; cyq_edit "$edges_q"; cyq_edit "$bidir_q" ;;

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
    SBAK="$REPO/neo4j/data.bak-serrano"
    SNAP="$STORE/_snap-serrano"
    if [ -e "$SBAK" ]; then echo "❌ $SBAK already exists — remove it first" >&2; exit 1; fi
    if [ -e "$SNAP" ]; then echo "❌ $SNAP already exists — remove it first" >&2; exit 1; fi
    echo "stopping live neo4j for consistent snapshot…"; docker stop "$LIVE"
    echo "snapshot: cp -a $STORE/data -> $SNAP (ext4->ext4, seconds)"; cp -a "$STORE/data" "$SNAP"
    echo "restarting live neo4j (downtime ends)…"; docker start "$LIVE"
    echo "backup seed: mv \$REPO/neo4j/data -> $SBAK"; mv "$REPO/neo4j/data" "$SBAK"
    echo "reverse-seed: cp -a $SNAP -> \$REPO/neo4j/data (snapshot -> seed, neo4j UP)…"
    mkdir -p "$REPO/neo4j/data"; cp -a "$SNAP/." "$REPO/neo4j/data/"
    echo "cleanup snapshot: rm -rf $SNAP"; rm -rf "$SNAP"
    echo "✅ reverse-seed done (seed==live). Old seed kept at $SBAK" ;;

  drop-backups)
    rm -rf "$BAK_LIVE" "$REPO/neo4j/data.bak-serrano" && echo "removed VM + seed backups" ;;

  *) echo "usage: $0 {counts-live|verify-live|mkwork|edit-up|counts-edit|verify-edit|edit-down|promote|reverse-seed|drop-backups}" >&2; exit 2 ;;
esac
