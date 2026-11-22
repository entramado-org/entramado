#!/bin/bash
# Path A — QA PASE 3 (2026-08-15): auditoría de datos legalmente sensibles.
# Efímero sobre el seed vivo 20260811-amador (8840/38218), bajado de R2 vía wrangler y
# extraído en $REPO/neo4j/data.qa3/data. Corre DENTRO de sm-dev con copia de trabajo
# VM-local (escritura pesada sobre virtiofs no persiste — gotcha _dedup_prod.sh).
#
# Secuencia:
#   mkwork → edit-up → counts-edit (== 8840/38218) → alledges-edit >pre.tsv →
#   [Mac: bucle de auditoría 052 (rondas 1-N) con NEO4J_URI=bolt://<ip sm-dev>:7688;
#    retirado en la spec 069 F5] →
#   [Mac: make qa-migrate FILE=… CONFIRM=1 contra el efímero, gate del dueño] →
#   [colapso simétrico si pairs>657 base] → verify-edit → alledges-edit >post.tsv →
#   [Mac: diff pre/post 100% mapeado] → edit-down → tomac →
#   [Mac: tar sin ._*] → subir R2 + bump SEED_VERSION (dueño)
#
# Despacho por PATH (el inline se rompe con {}()[] — gotcha conocido):
#   container machine run -n sm-dev --root -- bash <repo>/pipeline/scripts/_qa3_dev.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO/neo4j/data.qa3/data"
STORE=/var/lib/sm-qa3
WORK="$STORE/work"
OUT="$REPO/neo4j/data.qa3.out"
EDIT=sm_neo4j_editqa3

# NEO4J_AUTH=none en el efímero (el seed lleva la password de PROD) — user/pass dummy.
cyq(){ docker exec "$EDIT" cypher-shell -u neo4j -p neo4j --format plain "$1"; }

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
bidir_q="MATCH (a)-[r1:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(b)-[r2:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(a) WHERE id(a) < id(b) AND type(r1)=type(r2) RETURN count(DISTINCT [id(a),id(b)]) AS bidir_pairs;"
# Guard del context loader (huérfanos con summary).
iso_q="MATCH (n) WHERE n.entity_id IS NOT NULL AND n.summary IS NOT NULL AND NOT EXISTS { MATCH (n)-[r]-(m) WHERE m.entity_id IS NOT NULL AND NOT type(r) IN ['MENTIONED_IN','PART_OF'] } RETURN count(n) AS context_isolated_nodes;"
# Censo del universo sensible del pase 3 (aristas judiciales/null de los 6 tipos).
sens_q="MATCH (a)-[r]->(b) WHERE type(r) IN ['ACCUSED_OF','CONVICTED_OF','PAID','RECEIVED','PARTY_TO','INVESTIGATED_BY'] AND (r.source_kind IS NULL OR r.source_kind = 'judicial') RETURN type(r) AS t, count(r) AS n ORDER BY n DESC;"

alledges_q="MATCH (a)-[r]->(b)
WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
RETURN type(r) AS t, a.entity_id AS de, b.entity_id AS a_nodo,
       coalesce(r.source_doc,'') AS doc, left(coalesce(r.source_quote,''),40) AS quote
ORDER BY t, de, a_nodo, doc, quote;"

case "${1:-}" in
  mkwork)
    if [ -e "$WORK" ]; then echo "❌ $WORK already exists — rmwork first" >&2; exit 1; fi
    if [ ! -d "$SRC/databases" ]; then echo "❌ store del seed no encontrado en $SRC" >&2; exit 1; fi
    echo "copying seed store ($SRC) -> $WORK (VM-local)…"
    mkdir -p "$WORK"; cp -a "$SRC/." "$WORK/"
    chown -R 7474:7474 "$WORK"
    echo "✅ work copy ready ($(du -sh "$WORK" | cut -f1))" ;;

  edit-up)
    docker rm -f "$EDIT" >/dev/null 2>&1 || true
    docker run -d --name "$EDIT" -p 0.0.0.0:7688:7687 \
      -v "$WORK:/data" \
      -e NEO4J_AUTH=none \
      -e NEO4J_PLUGINS='["apoc"]' \
      -e NEO4J_dbms_security_procedures_unrestricted='apoc.*' \
      neo4j:5.26 >/dev/null
    echo "waiting for $EDIT bolt…"
    for i in $(seq 1 90); do
      if docker exec "$EDIT" cypher-shell -u neo4j -p neo4j "RETURN 1;" >/dev/null 2>&1; then
        echo "✅ $EDIT ready on :7688 (over $WORK)"; exit 0
      fi
      sleep 2
    done
    echo "❌ $EDIT not ready" >&2; docker logs --tail 30 "$EDIT" >&2; exit 1 ;;

  counts-edit)   cyq "$counts_q" ;;
  alledges-edit) cyq "$alledges_q" ;;
  sens-census)   cyq "$sens_q" ;;
  verify-edit)
    cyq "$counts_q"; cyq "$bidir_q"; cyq "$iso_q" ;;

  edit-down)
    docker stop -t 40 "$EDIT" && docker rm "$EDIT"
    echo "✅ $EDIT stopped cleanly" ;;

  tomac)
    if [ -e "$OUT" ]; then echo "❌ $OUT already exists — remove it first" >&2; exit 1; fi
    if docker ps --format '{{.Names}}' | grep -q "^$EDIT$"; then echo "❌ $EDIT still running — edit-down first" >&2; exit 1; fi
    echo "copying $WORK -> $OUT (VM -> Mac via home-mount)…"
    mkdir -p "$OUT"; cp -a "$WORK/." "$OUT/"
    echo "✅ store copied to Mac ($(du -sh "$OUT" | cut -f1))" ;;

  rmwork)
    docker rm -f "$EDIT" >/dev/null 2>&1 || true
    rm -rf "$STORE" && echo "removed $STORE" ;;

  *) echo "usage: $0 {mkwork|edit-up|counts-edit|alledges-edit|sens-census|verify-edit|edit-down|tomac|rmwork}" >&2; exit 2 ;;
esac
