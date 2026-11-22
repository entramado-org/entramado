#!/bin/bash
# Deploy the BOE overlay to PROD. Spec 049 shipped the base layer (196 edges/84 personas,
# 2026-07-16); spec 050 re-runs the same Path A with the DIRECTED-PASS delta: filled-in
# `|pre` etapas (RD colectivos jun-2018 via XML), promoted por_verificar trayectorias,
# Armengol (alias-driven) — mostly PROP-UPDATES on existing edges, which the alledges
# diff (keyed) CANNOT see → use the boeprops-live/boeprops-edit dump diff for those.
# Clone of _contratacion_prod.sh (same HEAVY-WRITE Path A: VM-local work copy, promote,
# reverse-seed). Work/backup paths suffixed -boe050 so the (possibly un-purged) 049
# backups data.bak-boe never collide.
#
# Sequence (Mac side runs the loaders against bolt://192.168.64.2:7688):
#   counts-live → mkwork → edit-up → counts-edit (MUST equal counts-live) →
#   [Mac: make context relations boe NEO4J_URI=bolt://<vm-ip>:7688]
#     (context = aplica los 6 merges nuevos ANTES del overlay; relations = pasada
#      estándar; podar jaime-far si resucita; boe = el overlay nuevo)
#   [Mac: regen+apply symmetric collapse (gated) — los merges RESUCITAN pares bidir
#     (gotcha conocido); APPOINTED_TO es direccional, pairs esperados 0 tras colapso —
#     y git checkout del manual_collapse_symmetric_20260619.cypher] →
#   [Mac: make centralidad NEO4J_URI=… (exportar NEO4J_PASSWORD — no lee .env)] →
#   verify-edit → edit-down →
#   [Mac: alledges diff — esperado: SOLO altas APPOINTED_TO + re-points 1:1 de los 6
#     merges (mismas aristas cambiando de entity_id duplicado → canónico), 0 bajas
#     inexplicadas] →
#   promote → reverse-seed → prod-machine.sh up (api+web+mcp; MCP tiene build propio)
#
# Runs INSIDE sm-prod. Dispatch by PATH:
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_boe_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
WORK="$STORE/data.boe050"
BAK_LIVE="$STORE/data.bak-boe050"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
# Overlay spot-checks: edge count, ZERO edges with amount/date_year (out of /flujos and
# cronología by construction), props-only coverage, stubs alive, boe_key uniqueness.
boe_q="MATCH ()-[r:APPOINTED_TO {source_kind:'boe'}]->() RETURN count(r) AS boe_edges, sum(CASE WHEN r.amount IS NOT NULL THEN 1 ELSE 0 END) AS con_amount_debe_ser_0, sum(CASE WHEN r.date_year IS NOT NULL THEN 1 ELSE 0 END) AS con_date_debe_ser_0;"
props_q="MATCH (n) WHERE coalesce(n.is_boe,false) RETURN count(n) AS personas_boe;"
# Etapas |pre (spec 050): las rellenadas por la pasada dirigida llevan fecha_nombramiento
# con la MISMA boe_key |pre (inmutable); las que quedan null deben conservar su nota
# («anterior a la ventana…») — una |pre null SIN nota es curación a medias.
pre_q="MATCH ()-[r:APPOINTED_TO {source_kind:'boe'}]->() WHERE r.boe_key ENDS WITH '|pre' RETURN sum(CASE WHEN r.fecha_nombramiento IS NOT NULL THEN 1 ELSE 0 END) AS pre_rellenadas, sum(CASE WHEN r.fecha_nombramiento IS NULL THEN 1 ELSE 0 END) AS pre_sin_nombramiento, sum(CASE WHEN r.fecha_nombramiento IS NULL AND r.note IS NULL THEN 1 ELSE 0 END) AS pre_null_sin_nota_debe_ser_0;"
# Dump de props de las aristas de la capa (spec 050): el alledges diff va por claves
# (src|type|dst|boe_key) y NO ve prop-updates — este dump los hace diffeables 1:1.
boeprops_q="MATCH (a)-[r:APPOINTED_TO {source_kind:'boe'}]->(b)
RETURN a.entity_id AS de, b.entity_id AS org, r.boe_key AS k,
       coalesce(r.fecha_nombramiento,'') AS f_nom, coalesce(r.boe_id_nombramiento,'') AS id_nom,
       coalesce(r.fecha_cese,'') AS f_cese, coalesce(r.boe_id_cese,'') AS id_cese,
       coalesce(r.note,'') AS note
ORDER BY de, k, org;"
stubs_q="MATCH (n) WHERE n.source_kind='boe' AND (n:Organization OR n:Court) RETURN n.entity_id AS stub, COUNT { (n)--() } AS deg ORDER BY stub;"
keys_q="MATCH (a)-[r:APPOINTED_TO {source_kind:'boe'}]->(b) WITH a.entity_id AS de, b.entity_id AS org, r.boe_key AS k, count(*) AS c WHERE c > 1 RETURN de, org, k, c;"
merges_q="UNWIND ['Person:carlos-moreno-medina','Person:nadia-calvino','Person:isaias-taboas','Person:felix-bolanos','Person:sanchez-melgar','Person:antonio-del-moral'] AS id OPTIONAL MATCH (n {entity_id:id}) RETURN id, n IS NULL AS fusionado_debe_ser_true;"
bidir_q="MATCH (a)-[r1:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(b)-[r2:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(a) WHERE id(a) < id(b) AND type(r1)=type(r2) RETURN count(DISTINCT [id(a),id(b)]) AS bidir_pairs;"
# Dump completo de aristas entidad↔entidad (diff live vs edit pre-promote). Incluye las
# claves de capa (bdns_key + placsp_key + boe_key) para que el diff explique el overlay.
alledges_q="MATCH (a)-[r]->(b)
WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
RETURN type(r) AS t, a.entity_id AS de, b.entity_id AS a_nodo,
       coalesce(r.source_doc,'') AS doc, left(coalesce(r.source_quote,''),40) AS quote,
       coalesce(r.bdns_key,'') AS bdns, coalesce(r.placsp_key,'') AS placsp,
       coalesce(r.boe_key,'') AS boe
ORDER BY t, de, a_nodo, doc, quote, bdns, placsp, boe;"

case "${1:-}" in
  counts-live)  cyq_live "$counts_q" ;;
  verify-live)  cyq_live "$boe_q"; cyq_live "$props_q"; cyq_live "$pre_q"; cyq_live "$stubs_q"; cyq_live "$keys_q"; cyq_live "$merges_q"; cyq_live "$bidir_q" ;;
  boeprops-live) cyq_live "$boeprops_q" ;;
  boeprops-edit) cyq_edit "$boeprops_q" ;;

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
  verify-edit)  cyq_edit "$boe_q"; cyq_edit "$props_q"; cyq_edit "$pre_q"; cyq_edit "$stubs_q"; cyq_edit "$keys_q"; cyq_edit "$merges_q"; cyq_edit "$bidir_q" ;;
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
    SBAK="$REPO/neo4j/data.bak-boe050"
    SNAP="$STORE/_snap-boe050"
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
    rm -rf "$BAK_LIVE" "$REPO/neo4j/data.bak-boe050" && echo "removed VM + seed backups (boe050)" ;;

  *) echo "usage: $0 {counts-live|verify-live|boeprops-live|boeprops-edit|mkwork|edit-up|counts-edit|verify-edit|edit-down|promote|reverse-seed|drop-backups}" >&2; exit 2 ;;
esac
