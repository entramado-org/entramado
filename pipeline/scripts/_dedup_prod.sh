#!/bin/bash
# Deploy the DEDUP 2026-07-11 to PROD: merge pilot-real-state-sl→pilot-real-estate +
# merge inigo-rotaecho→inigo-rotaeche-lachiondo (context) + deny OCCURRED_AT desayuno
# (relations) + gated delete of Event:desayuno. Clone of _subvenciones_prod.sh (same
# HEAVY-WRITE path: merges don't persist through ephemeral-over-virtiofs → VM-local
# work copy, promote, reverse-seed).
#
# Sequence (Mac side runs the loaders against bolt://<vm-ip>:7688):
#   counts-live → alledges-live → mkwork → edit-up → counts-edit (MUST equal counts-live) →
#   [Mac: make context relations NEO4J_URI=bolt://<vm-ip>:7688]
#     (context = 2 merges; relations = deny desayuno + re-deny post-merge; podar
#      jaime-far si resucita → prune-jaimefar)
#   apply-desayuno-patch →
#   [Mac: regen+apply symmetric collapse (gated) — merges resucitan inversas —
#     y git checkout del manual_collapse_symmetric_20260619.cypher pisado] →
#   [Mac: make centralidad NEO4J_URI=…] → verify-edit → edit-down →
#   [Mac: alledges diff] → promote → reverse-seed → prod-machine.sh up
#
# Runs INSIDE sm-prod. Dispatch by PATH:
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_dedup_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
WORK="$STORE/data.dedup"
BAK_LIVE="$STORE/data.bak-dedup"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
# Post-merge only the canonicals must remain; Event:desayuno must be gone post-patch.
merges_q="MATCH (n) WHERE n.entity_id IN ['Company:pilot-real-estate','Company:pilot-real-state-sl','Person:inigo-rotaeche-lachiondo','Person:inigo-rotaecho','Event:desayuno','Person:jaime-far'] RETURN n.entity_id AS id, n.name AS nm, COUNT { (n)--() } AS deg ORDER BY id;"
# Canonical pilot must inherit the sentencia-TS alias + keep OWNS/CONTROLS/ADMINISTRA.
alias_q="MATCH (n {entity_id:'Company:pilot-real-estate'}) RETURN [a IN n.aliases WHERE a CONTAINS 'STATE'] AS alias_state, n.summary IS NOT NULL AS has_summary;"
pilot_edges_q="MATCH (n {entity_id:'Company:pilot-real-estate'})-[r]-(o) WHERE o.entity_id IS NOT NULL RETURN type(r) AS t, o.entity_id AS otro, coalesce(r.source_kind,'extraccion') AS kind ORDER BY t, otro;"
bidir_q="MATCH (a)-[r1:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(b)-[r2:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(a) WHERE id(a) < id(b) AND type(r1)=type(r2) RETURN count(DISTINCT [id(a),id(b)]) AS bidir_pairs;"
# Dump completo de aristas entidad↔entidad (diff live vs edit pre-promote).
alledges_q="MATCH (a)-[r]->(b)
WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
RETURN type(r) AS t, a.entity_id AS de, b.entity_id AS a_nodo,
       coalesce(r.source_doc,'') AS doc, left(coalesce(r.source_quote,''),40) AS quote
ORDER BY t, de, a_nodo, doc, quote;"

case "${1:-}" in
  counts-live)  cyq_live "$counts_q"; cyq_live "$merges_q" ;;
  verify-live)  cyq_live "$merges_q"; cyq_live "$alias_q"; cyq_live "$pilot_edges_q"; cyq_live "$bidir_q" ;;

  rmwork)
    docker rm -f "$EDIT" >/dev/null 2>&1 || true
    rm -rf "$WORK"; echo "✅ removed $WORK (and $EDIT container if present)" ;;

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
  verify-edit)  cyq_edit "$merges_q"; cyq_edit "$alias_q"; cyq_edit "$pilot_edges_q"; cyq_edit "$bidir_q" ;;
  alledges-live) cyq_live "$alledges_q" ;;
  alledges-edit) cyq_edit "$alledges_q" ;;

  apply-desayuno-patch)
    # Gated delete via cypher-shell (patch reviewed in-repo). Idempotent.
    docker exec -i "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain \
      < "$REPO/pipeline/data/audit/patches/manual_delete_event_desayuno_20260711.cypher"
    echo "✅ desayuno delete applied to $EDIT"
    cyq_edit "$merges_q" ;;

  prune-jaimefar)
    # Gotcha: the durable by_doc split resurrects Person:jaime-far EMPTY on every
    # full `make relations` run (4th time). Prune only if truly edgeless.
    cyq_edit "MATCH (n {entity_id:'Person:jaime-far'}) WHERE COUNT { (n)--() } = 0 DETACH DELETE n;"
    cyq_edit "OPTIONAL MATCH (n {entity_id:'Person:jaime-far'}) RETURN n.entity_id AS jaime_far_debe_ser_null;" ;;

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
    SBAK="$REPO/neo4j/data.bak-dedup"
    SNAP="$STORE/_snap-dedup"
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
    rm -rf "$BAK_LIVE" "$REPO/neo4j/data.bak-dedup" && echo "removed VM + seed backups" ;;

  *) echo "usage: $0 {counts-live|verify-live|mkwork|edit-up|counts-edit|verify-edit|alledges-live|alledges-edit|apply-desayuno-patch|prune-jaimefar|edit-down|promote|reverse-seed|drop-backups}" >&2; exit 2 ;;
esac
