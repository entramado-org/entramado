#!/bin/bash
# Deploy the badge-sweep (PR #214) to PROD: 3-node Jésica merge + Aurora/Emilia merges +
# ~107 new ACCUSED_OF edges (ultima_hora + accusations) + FAMILY_OF deny + gated
# badge-status patch + roles/synthesis. Clone of _serrano_prod.sh (same HEAVY-WRITE path:
# merges don't persist through ephemeral-over-virtiofs → VM-local work copy, promote,
# reverse-seed).
#
# Sequence (Mac side runs the loaders against bolt://192.168.64.3:7688):
#   counts-live → mkwork → edit-up → counts-edit (MUST equal counts-live) →
#   [Mac: make context accusations ultima-hora relations NEO4J_URI=bolt://192.168.64.3:7688] →
#   [Mac: regen+apply symmetric collapse (gated), git checkout the overwritten .cypher] →
#   apply-badge-patch → [Mac: make centralidad] → verify-edit → edit-down →
#   [Mac: alledges diff] → promote → reverse-seed → prod-machine.sh up (api+web+mcp)
#
# Runs INSIDE sm-prod. Dispatch by PATH:
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_barrido_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
WORK="$STORE/data.barrido"
BAK_LIVE="$STORE/data.bak-barrido"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
# The 3 Jésica ids + the 2 other merge pairs: post-merge only canonicals must remain.
merges_q="MATCH (n) WHERE n.entity_id IN ['Person:jesica-rodriguez-garcia','Person:jessica-rodriguez-garcia','Person:jessica-rodriguez','Person:aurora-lopez-lopez','Person:maria-aurora-lopez-lopez','Person:emilia-parejo-gala','Person:emilio-pareja-gala'] RETURN n.entity_id AS id, n.name AS nm ORDER BY id;"
# Badge spot-checks: hijas + Jésica + one SEPI + status-patch beneficiaries.
badges_q="MATCH (n:Person)-[r:ACCUSED_OF]->(c:Crime) WHERE n.entity_id IN ['Person:laura-rodriguez-espinosa','Person:alba-rodriguez-espinosa','Person:jesica-rodriguez-garcia','Person:rosario-martin-manzanedo','Person:maria-gertrudis-alcazar-jimenez','Person:julio-martinez-martinez','Person:leire-diez','Person:ruben-villalba-carnero'] RETURN n.entity_id AS id, c.entity_id AS delito, r.status AS st, r.source_kind AS kind ORDER BY id, delito;"
family_q="MATCH (n {entity_id:'Person:jesica-rodriguez-garcia'})-[r:FAMILY_OF]-(o) RETURN count(r) AS family_edges_should_be_0;"
bidir_q="MATCH (a)-[r1:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(b)-[r2:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(a) WHERE id(a) < id(b) AND type(r1)=type(r2) RETURN count(DISTINCT [id(a),id(b)]) AS bidir_pairs;"

case "${1:-}" in
  counts-live)  cyq_live "$counts_q"; cyq_live "$merges_q" ;;
  verify-live)  cyq_live "$merges_q"; cyq_live "$badges_q"; cyq_live "$family_q"; cyq_live "$bidir_q" ;;

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

  counts-edit)  cyq_edit "$counts_q"; cyq_edit "$merges_q" ;;
  verify-edit)  cyq_edit "$merges_q"; cyq_edit "$badges_q"; cyq_edit "$family_q"; cyq_edit "$bidir_q" ;;

  apply-badge-patch)
    # Gated status patch via cypher-shell (NOT qa-migrate: text literals). Idempotent.
    docker exec -i "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain \
      < "$REPO/pipeline/data/audit/patches/manual_fix_badge_status_20260710.cypher"
    echo "✅ badge-status patch applied to $EDIT"
    cyq_edit "$badges_q" ;;

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
    SBAK="$REPO/neo4j/data.bak-barrido"
    SNAP="$STORE/_snap-barrido"
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
    rm -rf "$BAK_LIVE" "$REPO/neo4j/data.bak-barrido" && echo "removed VM + seed backups" ;;

  *) echo "usage: $0 {counts-live|verify-live|mkwork|edit-up|counts-edit|verify-edit|apply-badge-patch|edit-down|promote|reverse-seed|drop-backups}" >&2; exit 2 ;;
esac
