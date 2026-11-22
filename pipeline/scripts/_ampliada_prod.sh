#!/bin/bash
# Deploy the CONTRATACION AMPLIADA overlay (spec 055) to PROD: 34 CONTRACTOR_OF
# aggregated edges (lic + agr + menores CCAA, señales de procedimiento) + 9 organo
# stubs + props-only (is_contratacion_amp en 29 adjudicatarias, amp_<ambito>_*) +
# 4 negativos amp_sin_adjudicaciones + alias SEKAI en Company:globalia (vía context:
# organizaciones.yaml [aliases + role] + roles_enriched.yaml [role, carga la última y
# manda] + summaries.yaml — NO figures.yaml). Clone of _contratacion_prod.sh (same HEAVY-WRITE
# Path A: VM-local work copy, promote, reverse-seed). Backups suffixed -amp055.
#
# Sequence (Mac side runs the loaders against bolt://192.168.64.2:7688):
#   counts-live → mkwork → edit-up → counts-edit (MUST equal counts-live) →
#   [Mac: make context relations contratacion contratacion-ampliada NEO4J_URI=bolt://<vm-ip>:7688]
#     (context/relations = pasada estándar pre-overlay, aplica el alias SEKAI
#      [organizaciones/roles_enriched] y el summary de Globalia; podar jaime-far si resucita; contratacion = re-pasada 046
#      idempotente [fill-only, no clobber]; contratacion-ampliada = el overlay nuevo)
#   [Mac: regen+apply symmetric collapse (gated) — CONTRACTOR_OF es direccional,
#     pairs esperados 0 — y git checkout del manual_collapse_symmetric_20260619.cypher
#     DESPUÉS del apply (gotcha 049: jamás re-aplicar un patch viejo)] →
#   [Mac: make centralidad NEO4J_URI=… (exportar NEO4J_PASSWORD)] → verify-edit →
#   edit-down → [Mac: alledges diff — esperado: SOLO altas CONTRACTOR_OF (34) +
#   re-points de merges si los hubiera, 0 bajas; los placsp_key del diff explican
#   cada alta por <ambito>|<organo>] → promote → reverse-seed →
#   prod-machine.sh up (api+web+mcp — tool nueva → el MCP tiene build PROPIO)
#
# Runs INSIDE sm-prod. Dispatch by PATH:
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_ampliada_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
WORK="$STORE/data.amp055"
BAK_LIVE="$STORE/data.bak-amp055"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
# Overlay spot-checks: edge count por ámbito, ZERO edges with amount/date_year (out of
# /flujos and cronología by construction), props-only coverage, negativos, stubs alive,
# placsp_key uniqueness per pair, alias SEKAI presente, capa 046 intacta.
# Esperado A FECHA DEL PR 055 (los conteos VIVEN en ampliada.yaml y cambian con la
# curación — comparar contra el YAML del árbol, no contra este comentario a ciegas):
# 34 aristas (lic 25 / agr 4 / ccaa_andalucia 2 / ccaa_euskadi 1 / ccaa_galicia 2),
# 29 adjudicatarias, 4 negativos, 9 stubs, menores 046 = 15.
amp_q="MATCH ()-[r:CONTRACTOR_OF {source_kind:'contratacion'}]->() RETURN count(r) AS amp_edges, sum(CASE WHEN r.amount IS NOT NULL THEN 1 ELSE 0 END) AS con_amount_debe_ser_0, sum(CASE WHEN r.date_year IS NOT NULL THEN 1 ELSE 0 END) AS con_date_debe_ser_0, sum(CASE WHEN r.ambito IS NULL THEN 1 ELSE 0 END) AS sin_ambito_debe_ser_0;"
ambitos_q="MATCH ()-[r:CONTRACTOR_OF {source_kind:'contratacion'}]->() RETURN r.ambito AS ambito, count(*) AS n ORDER BY ambito;"
props_q="MATCH (n) WHERE coalesce(n.is_contratacion_amp,false) WITH count(n) AS adjudicatarias MATCH (m) WHERE coalesce(m.amp_sin_adjudicaciones,false) RETURN adjudicatarias, count(m) AS sin_adjudicaciones;"
stubs_q="MATCH (n) WHERE n.source_kind='contratacion' AND n.entity_id IN ['Organization:ayuntamiento-de-vigo','Organization:autoridad-portuaria-de-vigo','Organization:diputacion-de-granada','Organization:junta-de-contratacion-centralizada','Organization:fundacion-eoi','Organization:nasuvinsa','Organization:xunta-de-galicia','Organization:axencia-turismo-de-galicia','Organization:servicio-andaluz-de-salud'] RETURN n.entity_id AS stub, COUNT { (n)--() } AS deg ORDER BY stub;"
keys_q="MATCH (a)-[r:CONTRACTOR_OF {source_kind:'contratacion'}]->(b) WITH a.entity_id AS de, b.entity_id AS a_nodo, r.placsp_key AS k, count(*) AS c WHERE c > 1 RETURN de, a_nodo, k, c;"
# s86 (2026-08-27): el alias/summary SEKAI vive en el nodo FILIAL (split matriz/filial).
sekai_q="MATCH (n {entity_id:'Company:globalia-corporate-travel'}) RETURN any(a IN coalesce(n.aliases,[]) WHERE toLower(a) CONTAINS 'sekai') AS alias_sekai_true, n.summary CONTAINS 'Sekai' AS summary_sekai_true;"
c046_q="MATCH ()-[r:MINOR_CONTRACTOR_OF {source_kind:'contratacion'}]->() RETURN count(r) AS menores_046, sum(CASE WHEN r.amount IS NOT NULL THEN 1 ELSE 0 END) AS m_amount_debe_ser_0;"
bidir_q="MATCH (a)-[r1:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(b)-[r2:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(a) WHERE id(a) < id(b) AND type(r1)=type(r2) RETURN count(DISTINCT [id(a),id(b)]) AS bidir_pairs;"
# Dump completo de aristas entidad↔entidad (diff live vs edit pre-promote). Incluye las
# claves de capa (bdns_key + placsp_key) para que el diff explique el overlay nuevo
# (las altas CONTRACTOR_OF llevan placsp_key "<ambito>|<organo>").
alledges_q="MATCH (a)-[r]->(b)
WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
RETURN type(r) AS t, a.entity_id AS de, b.entity_id AS a_nodo,
       coalesce(r.source_doc,'') AS doc, left(coalesce(r.source_quote,''),40) AS quote,
       coalesce(r.bdns_key,'') AS bdns, coalesce(r.placsp_key,'') AS placsp
ORDER BY t, de, a_nodo, doc, quote, bdns, placsp;"

case "${1:-}" in
  counts-live)  cyq_live "$counts_q" ;;
  verify-live)  cyq_live "$amp_q"; cyq_live "$ambitos_q"; cyq_live "$props_q"; cyq_live "$stubs_q"; cyq_live "$keys_q"; cyq_live "$sekai_q"; cyq_live "$c046_q"; cyq_live "$bidir_q" ;;

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
  verify-edit)  cyq_edit "$amp_q"; cyq_edit "$ambitos_q"; cyq_edit "$props_q"; cyq_edit "$stubs_q"; cyq_edit "$keys_q"; cyq_edit "$sekai_q"; cyq_edit "$c046_q"; cyq_edit "$bidir_q" ;;
  alledges-live) cyq_live "$alledges_q" ;;
  alledges-edit) cyq_edit "$alledges_q" ;;

  edit-down)
    docker stop -t 40 "$EDIT" && docker rm "$EDIT"
    echo "✅ $EDIT stopped cleanly (consistent VM-local store at $WORK)" ;;

  promote)
    if [ -e "$BAK_LIVE" ]; then echo "❌ $BAK_LIVE already exists — remove it first" >&2; exit 1; fi
    if [ ! -e "$WORK/databases" ]; then echo "❌ $WORK/databases missing — did the edit persist?" >&2; exit 1; fi
    echo "stopping live neo4j ($LIVE)…"; docker stop -t 60 "$LIVE"
    echo "backup: mv $STORE/data -> $BAK_LIVE (instant, same fs)"; mv "$STORE/data" "$BAK_LIVE"
    echo "promote: mv $WORK -> $STORE/data"; mv "$WORK" "$STORE/data"
    chown -R 7474:7474 "$STORE/data"
    echo "starting live neo4j ($LIVE)…"; docker start "$LIVE"
    echo "✅ promote done (live backup kept at $BAK_LIVE)" ;;

  reverse-seed)
    SBAK="$REPO/neo4j/data.bak-amp055"
    SNAP="$STORE/_snap-amp055"
    if [ -e "$SBAK" ]; then echo "❌ $SBAK already exists — remove it first" >&2; exit 1; fi
    if [ -e "$SNAP" ]; then echo "❌ $SNAP already exists — remove it first" >&2; exit 1; fi
    echo "stopping live neo4j for consistent snapshot…"; docker stop -t 60 "$LIVE"
    echo "snapshot: cp -a $STORE/data -> $SNAP (ext4->ext4, seconds)"; cp -a "$STORE/data" "$SNAP"
    echo "restarting live neo4j (downtime ends)…"; docker start "$LIVE"
    echo "backup seed: mv \$REPO/neo4j/data -> $SBAK"; mv "$REPO/neo4j/data" "$SBAK"
    echo "reverse-seed: cp -a $SNAP -> \$REPO/neo4j/data (snapshot -> seed, neo4j UP)…"
    mkdir -p "$REPO/neo4j/data"; cp -a "$SNAP/." "$REPO/neo4j/data/"
    echo "cleanup snapshot: rm -rf $SNAP"; rm -rf "$SNAP"
    echo "✅ reverse-seed done (seed==live). Old seed kept at $SBAK" ;;

  drop-backups)
    rm -rf "$BAK_LIVE" "$REPO/neo4j/data.bak-amp055" && echo "removed VM + seed backups" ;;

  *) echo "usage: $0 {counts-live|verify-live|mkwork|edit-up|counts-edit|verify-edit|edit-down|promote|reverse-seed|drop-backups}" >&2; exit 2 ;;
esac
