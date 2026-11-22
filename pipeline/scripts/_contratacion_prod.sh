#!/bin/bash
# Deploy the CONTRATACION overlay (spec 046) to PROD: 15 MINOR_CONTRACTOR_OF aggregated
# edges + 5 organo stubs + props-only (is_contratacion en 17 adjudicatarias +
# placsp_sin_menores en 5 negativos). (Era 14/4/16 en el deploy original de PR #252; +1
# de cada por la reincorporación de Villafuel B01959998 — falso rechazo del recon 045.)
# Clone of _subvenciones_prod.sh (same HEAVY-WRITE
# Path A: VM-local work copy, promote, reverse-seed).
#
# Sequence (Mac side runs the loaders against bolt://192.168.64.2:7688):
#   counts-live → mkwork → edit-up → counts-edit (MUST equal counts-live) →
#   [Mac: make context relations contratacion NEO4J_URI=bolt://<vm-ip>:7688]
#     (context/relations = pasada estándar pre-overlay; podar jaime-far si resucita;
#      contratacion = el overlay nuevo)
#   [Mac: regen+apply symmetric collapse (gated) — MINOR_CONTRACTOR_OF es direccional,
#     pairs esperados 0 — y git checkout del manual_collapse_symmetric_20260619.cypher] →
#   [Mac: make centralidad NEO4J_URI=…] → verify-edit → edit-down →
#   [Mac: alledges diff — esperado: SOLO altas MINOR_CONTRACTOR_OF, 0 bajas] →
#   promote → reverse-seed → prod-machine.sh up (api+web+mcp)
#
# Runs INSIDE sm-prod. Dispatch by PATH:
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_contratacion_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
WORK="$STORE/data.contratacion"
BAK_LIVE="$STORE/data.bak-contratacion"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
# Overlay spot-checks: edge count, ZERO edges with amount/date_year (out of /flujos and
# cronología by construction), props-only coverage, stubs alive, placsp_key uniqueness.
contr_q="MATCH ()-[r:MINOR_CONTRACTOR_OF {source_kind:'contratacion'}]->() RETURN count(r) AS contr_edges, sum(CASE WHEN r.amount IS NOT NULL THEN 1 ELSE 0 END) AS con_amount_debe_ser_0, sum(CASE WHEN r.date_year IS NOT NULL THEN 1 ELSE 0 END) AS con_date_debe_ser_0;"
props_q="MATCH (n) WHERE coalesce(n.is_contratacion,false) WITH count(n) AS adjudicatarias_16 MATCH (m) WHERE coalesce(m.placsp_sin_menores,false) RETURN adjudicatarias_16, count(m) AS sin_menores_6;"
stubs_q="MATCH (n:Organization) WHERE n.source_kind='contratacion' RETURN n.entity_id AS stub, COUNT { (n)--() } AS deg ORDER BY stub;"
keys_q="MATCH (a)-[r:MINOR_CONTRACTOR_OF {source_kind:'contratacion'}]->(b) WITH a.entity_id AS de, r.placsp_key AS k, count(*) AS c WHERE c > 1 RETURN de, k, c;"
bidir_q="MATCH (a)-[r1:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(b)-[r2:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(a) WHERE id(a) < id(b) AND type(r1)=type(r2) RETURN count(DISTINCT [id(a),id(b)]) AS bidir_pairs;"
# Dump completo de aristas entidad↔entidad (diff live vs edit pre-promote). Incluye las
# claves de capa (bdns_key + placsp_key) para que el diff explique el overlay nuevo.
alledges_q="MATCH (a)-[r]->(b)
WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
RETURN type(r) AS t, a.entity_id AS de, b.entity_id AS a_nodo,
       coalesce(r.source_doc,'') AS doc, left(coalesce(r.source_quote,''),40) AS quote,
       coalesce(r.bdns_key,'') AS bdns, coalesce(r.placsp_key,'') AS placsp
ORDER BY t, de, a_nodo, doc, quote, bdns, placsp;"

case "${1:-}" in
  counts-live)  cyq_live "$counts_q" ;;
  verify-live)  cyq_live "$contr_q"; cyq_live "$props_q"; cyq_live "$stubs_q"; cyq_live "$keys_q"; cyq_live "$bidir_q" ;;

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

  counts-edit)  cyq_edit "$counts_q" ;;
  verify-edit)  cyq_edit "$contr_q"; cyq_edit "$props_q"; cyq_edit "$stubs_q"; cyq_edit "$keys_q"; cyq_edit "$bidir_q" ;;
  alledges-live) cyq_live "$alledges_q" ;;
  alledges-edit) cyq_edit "$alledges_q" ;;

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
    SBAK="$REPO/neo4j/data.bak-contratacion"
    SNAP="$STORE/_snap-contratacion"
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
    rm -rf "$BAK_LIVE" "$REPO/neo4j/data.bak-contratacion" && echo "removed VM + seed backups" ;;

  *) echo "usage: $0 {counts-live|verify-live|mkwork|edit-up|counts-edit|verify-edit|edit-down|promote|reverse-seed|drop-backups}" >&2; exit 2 ;;
esac
