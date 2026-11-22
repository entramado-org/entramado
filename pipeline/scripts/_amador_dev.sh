#!/bin/bash
# Path A — pasada González Amador (2026-08-11): role+summary de contexto y entrada
# curada en accusations.yaml (poda los dups semánticos delito-contra-la-hacienda-publica
# y falsedad-en-documento-mercantil de sus ACCUSED_OF extraídos).
#
# Parte del store del SEED VIVO 20260810-ingesta20557 (8841/38219), presente en el Mac
# como $REPO/neo4j/data.ingesta20557.out (es el mismo store que se tarró y subió a R2).
# Corre DENTRO de sm-dev con copia de trabajo VM-local (escritura pesada sobre virtiofs
# no persiste — gotcha _dedup_prod.sh).
#
# Secuencia:
#   mkwork → edit-up → counts-edit (== 8841/38219) → alledges-edit >pre.tsv →
#   [Mac: NEO4J_URI=bolt://192.168.64.21:7688 make accusations] →
#   [Mac: NEO4J_URI=... make context] → [Mac: colapso simétrico si pairs>delta] →
#   [Mac: NEO4J_URI=... NEO4J_PASSWORD=... make centralidad] →
#   verify-edit → alledges-edit >post.tsv → [Mac: diff pre/post 100% mapeado] →
#   edit-down → tomac → [Mac: tar sin ._*] → subir R2 + bump SEED_VERSION (dueño)
#
# Despacho por PATH (el inline se rompe con {}()[] — gotcha conocido):
#   container machine run -n sm-dev --root -- bash <repo>/pipeline/scripts/_amador_dev.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO/neo4j/data.ingesta20557.out"
STORE=/var/lib/sm-amador
WORK="$STORE/work"
OUT="$REPO/neo4j/data.amador.out"
EDIT=sm_neo4j_editamador

# NEO4J_AUTH=none en el efímero (el seed lleva la password de PROD) — user/pass dummy.
cyq(){ docker exec "$EDIT" cypher-shell -u neo4j -p neo4j --format plain "$1"; }

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
# La ficha de GA tras la pasada: role+summary presentes.
ga_q="MATCH (n {entity_id:'Person:alberto-gonzalez-amador'}) RETURN n.role IS NOT NULL AS role_ok, n.summary IS NOT NULL AS summary_ok, n.summary_updated AS updated, COUNT { (n)--() } AS deg;"
# ACCUSED_OF de GA: exactamente 2 (defraudación tributaria + falsedad documental), con estado/artículo.
gacc_q="MATCH (n {entity_id:'Person:alberto-gonzalez-amador'})-[r:ACCUSED_OF]->(c) RETURN c.entity_id AS crime, r.source_kind AS kind, r.estado AS estado, r.articulo AS articulo ORDER BY crime;"
# Los dups podados NO deben conservar arista desde GA.
gadup_q="MATCH (n {entity_id:'Person:alberto-gonzalez-amador'})-[r:ACCUSED_OF]->(c) WHERE c.entity_id IN ['Crime:delito-contra-la-hacienda-publica','Crime:falsedad-en-documento-mercantil'] RETURN count(r) AS dups_deberia_ser_0;"
# Los nodos Crime dup siguen existiendo (otros acusados pueden usarlos) — solo informativo.
crimes_q="MATCH (c:Crime) WHERE c.entity_id IN ['Crime:defraudacion-tributaria','Crime:falsedad-documental','Crime:delito-contra-la-hacienda-publica','Crime:falsedad-en-documento-mercantil'] RETURN c.entity_id AS id, COUNT { (c)--() } AS deg ORDER BY id;"
bidir_q="MATCH (a)-[r1:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(b)-[r2:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(a) WHERE id(a) < id(b) AND type(r1)=type(r2) RETURN count(DISTINCT [id(a),id(b)]) AS bidir_pairs;"
# Guard del context loader (huérfanos con summary).
iso_q="MATCH (n) WHERE n.entity_id IS NOT NULL AND n.summary IS NOT NULL AND NOT EXISTS { MATCH (n)-[r]-(m) WHERE m.entity_id IS NOT NULL AND NOT type(r) IN ['MENTIONED_IN','PART_OF'] } RETURN count(n) AS context_isolated_nodes;"

alledges_q="MATCH (a)-[r]->(b)
WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
RETURN type(r) AS t, a.entity_id AS de, b.entity_id AS a_nodo,
       coalesce(r.source_doc,'') AS doc, left(coalesce(r.source_quote,''),40) AS quote
ORDER BY t, de, a_nodo, doc, quote;"

case "${1:-}" in
  mkwork)
    if [ -e "$WORK" ]; then echo "❌ $WORK already exists — rmwork first" >&2; exit 1; fi
    if [ ! -d "$SRC/databases" ]; then echo "❌ store del seed vivo no encontrado en $SRC" >&2; exit 1; fi
    echo "copying live-seed store ($SRC) -> $WORK (VM-local)…"
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
  verify-edit)
    cyq "$counts_q"; cyq "$ga_q"; cyq "$gacc_q"; cyq "$gadup_q"
    cyq "$crimes_q"; cyq "$bidir_q"; cyq "$iso_q" ;;
  bidir-list)
    cyq "MATCH (a)-[r1:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(b)-[r2:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(a) WHERE id(a) < id(b) AND type(r1)=type(r2) RETURN a.entity_id, b.entity_id, type(r1), r1.source_doc, r2.source_doc;" ;;

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

  *) echo "usage: $0 {mkwork|edit-up|counts-edit|alledges-edit|verify-edit|bidir-list|edit-down|tomac|rmwork}" >&2; exit 2 ;;
esac
