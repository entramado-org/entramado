#!/bin/bash
# Deploy de las fichas de los 11 condenados del caso Badajoz (PR #249, squash a88494f):
# capa contexto PROPS-ONLY — roles (figures.yaml + roles_enriched.yaml) y summaries
# (summaries.yaml) a «condenado (NO firme) sentencia 152/2026» + altas de Cristina Núñez
# y Félix González Márquez (deben ENRIQUECER nodos vivos, no crear huérfanos). Clon de
# _badajoz_prod.sh (mismo camino Path A: copia de trabajo VM-local, promote, reverse-seed).
#
# Secuencia (el lado Mac corre pipeline contra bolt://192.168.64.2:7688):
#   counts-live → alledges-live → mkwork → edit-up → counts-edit (== counts-live) →
#   [Mac: NEO4J_URI=bolt://192.168.64.2:7688 make context (exportar NEO4J_PASSWORD)] →
#   [Mac: gen_symmetric_collapse (la inversa Aldama→Ábalos resucita con el loader) →
#    qa-migrate CONFIRM=1 + git checkout del .cypher pisado] → verify-edit →
#   [Mac: alledges diff live vs edit — 100% explicado o NO se promueve] → edit-down →
#   promote → reverse-seed → prod-machine.sh up (rebuild api+web+mcp por cases.yaml)
#
# Corre DENTRO de sm-prod. Despacho por PATH:
#   container machine run -n sm-prod --root -- bash <repo>/pipeline/scripts/_fichas_prod.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit
LIVE=sm_neo4j
WORK="$STORE/data.fichas"
BAK_LIVE="$STORE/data.bak-fichas"

cyq_live(){ docker exec "$LIVE" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }
cyq_edit(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
# Los 11 condenados: role Y summary deben citar la sentencia 152/2026 (props del context_loader).
IDS="['Person:david-sanchez','Person:miguel-angel-gallardo','Person:luis-carrero','Person:elisa-moriano-morales','Person:cristina-nunez-fernandez','Person:felix-gonzalez-marquez','Person:juana-cinta-calderon-zazo','Person:emilia-parejo-gala','Person:francisco-martos-ortiz','Person:ricardo-cabezas-martin','Person:manuel-candalija-valle']"
roles_q="MATCH (n:Person) WHERE n.entity_id IN $IDS RETURN n.entity_id AS id, n.role CONTAINS '152/2026' AS role_ok, n.summary CONTAINS '152/2026' AS summary_ok, n.summary_updated AS sum_upd ORDER BY id;"
roles_count_q="MATCH (n:Person) WHERE n.entity_id IN $IDS AND n.role CONTAINS '152/2026' AND n.summary CONTAINS '152/2026' RETURN count(n) AS fichas_ok_deberia_ser_11;"
# Las 2 altas NO deben haber creado nodos nuevos (1 nodo por nombre, el vivo).
dup_q="MATCH (n:Person) WHERE n.name IN ['Cristina Núñez Fernández','Félix González Márquez'] RETURN n.name AS nm, count(n) AS nodos_deberia_ser_1, collect(n.entity_id) AS ids;"
# Guards de siempre.
grant_q="MATCH ()-[r:RECEIVED_GRANT]->() RETURN count(r) AS received_grant_deberia_ser_34;"
jaimefar_q="MATCH (n {entity_id:'Person:jaime-far'}) RETURN count(n) AS jaime_far_deberia_ser_0;"
bidir_q="MATCH (a)-[r1:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(b)-[r2:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(a) WHERE id(a) < id(b) AND type(r1)=type(r2) RETURN count(DISTINCT [id(a),id(b)]) AS bidir_pairs;"
iso_q="MATCH (n) WHERE n.entity_id IS NOT NULL AND n.summary IS NOT NULL AND NOT EXISTS { MATCH (n)-[r]-(m) WHERE m.entity_id IS NOT NULL AND NOT type(r) IN ['MENTIONED_IN','PART_OF'] } RETURN count(n) AS context_isolated_nodes;"

# Dump completo de aristas entidad↔entidad (diff live vs edit pre-promote).
alledges_q="MATCH (a)-[r]->(b)
WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
RETURN type(r) AS t, a.entity_id AS de, b.entity_id AS a_nodo,
       coalesce(r.source_doc,'') AS doc, left(coalesce(r.source_quote,''),40) AS quote
ORDER BY t, de, a_nodo, doc, quote;"

case "${1:-}" in
  counts-live)  cyq_live "$counts_q" ;;
  verify-live)  cyq_live "$counts_q"; cyq_live "$roles_count_q"; cyq_live "$roles_q"; cyq_live "$dup_q"; cyq_live "$grant_q"; cyq_live "$bidir_q"; cyq_live "$iso_q" ;;
  alledges-live) cyq_live "$alledges_q" ;;
  alledges-edit) cyq_edit "$alledges_q" ;;

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
  verify-edit)
    cyq_edit "$counts_q"; cyq_edit "$roles_count_q"; cyq_edit "$roles_q"; cyq_edit "$dup_q"
    cyq_edit "$grant_q"; cyq_edit "$jaimefar_q"; cyq_edit "$bidir_q"; cyq_edit "$iso_q" ;;

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
    SBAK="$REPO/neo4j/data.bak-fichas"
    SNAP="$STORE/_snap-fichas"
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

  rmwork)
    docker rm -f "$EDIT" >/dev/null 2>&1 || true
    rm -rf "$WORK" && echo "removed $WORK (aborted edit)" ;;

  drop-backups)
    rm -rf "$BAK_LIVE" "$REPO/neo4j/data.bak-fichas" && echo "removed VM + seed backups" ;;

  *) echo "usage: $0 {counts-live|verify-live|alledges-live|alledges-edit|mkwork|edit-up|counts-edit|verify-edit|edit-down|promote|reverse-seed|rmwork|drop-backups}" >&2; exit 2 ;;
esac
