#!/bin/bash
# Path A — ingesta autos CENDOJ 20775/2020 (31 ATS/AATS) + spec 059 salvaguardas legales
# (2026-09-05). Efímero sobre el seed vivo 20260902-qa5 (md5 c3702583… == ETag R2), extraído
# en $REPO/neo4j/data.autos20775/data. Corre DENTRO de sm-dev con copia de trabajo VM-local
# (escritura pesada sobre virtiofs no persiste).
#
# Secuencia:
#   mkwork → edit-up → counts-edit → alledges-edit >pre.tsv → [Mac: auditoría Fase 0 (Cypher)] →
#   [Mac: ingest_tail --docs <31> + cleanup + migración source_kind + context/relations/accusations
#    + colapso + documents (ÚLTIMO) + centralidad, NEO4J_URI=bolt://<ip>:7688] →
#   verify-edit → alledges-edit >post.tsv → [Mac: diff pre/post 100% mapeado] →
#   edit-down → tomac → [Mac: tar -C <stage> data] → boot-verify del tar → subir R2 + bump (dueño)
#
# Despacho por PATH (el inline se rompe con {}()[] — gotcha conocido):
#   container machine run -n sm-dev --root -- bash <repo>/pipeline/scripts/_autos20775_dev.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO/neo4j/data.autos20775/data"
STORE=/var/lib/sm-autos20775
WORK="$STORE/work"
OUT="$REPO/neo4j/data.autos20775.out"
EDIT=sm_neo4j_editautos
# Los 31 doc_ids nuevos (para el guard «0 Person nuevos» de verify-edit).
NEWDOCS="['AATS_13839_2024.pdf','AATS_6465_2026.pdf','AATS_7235_2026.pdf','AATS_7236_2026.pdf','ATS_11327_2025.pdf','ATS_11788_2025.pdf','ATS_13325_2024.pdf','ATS_1640_2026.pdf','ATS_207_2026.pdf','ATS_246_2026.pdf','ATS_248_2026.pdf','ATS_253_2026.pdf','ATS_2566_2026.pdf','ATS_2606_2025.pdf','ATS_2608_2025.pdf','ATS_280_2026.pdf','ATS_287_2026.pdf','ATS_288_2026.pdf','ATS_3155_2021.pdf','ATS_3218_2026.pdf','ATS_3257_2026.pdf','ATS_3258_2026.pdf','ATS_4262_2026.pdf','ATS_4512_2026.pdf','ATS_5540_2025.pdf','ATS_5556_2026.pdf','ATS_5934_2025.pdf','ATS_6254_2021.pdf','ATS_7081_2025.pdf','ATS_7107_2025.pdf','ATS_9744_2025.pdf']"

# NEO4J_AUTH=none en el efímero (el seed lleva la password de PROD) — user/pass dummy.
cyq(){ docker exec "$EDIT" cypher-shell -u neo4j -p neo4j --format plain "$1"; }

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
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
  verify-edit)
    cyq "$counts_q"; cyq "$bidir_q"; cyq "$iso_q"
    echo "# spec 059 — source_kind null (esperado 0)"
    cyq "MATCH ()-[r]->() WHERE r.source_kind IS NULL AND NOT type(r) IN ['MENTIONED_IN','PART_OF','ABOUT'] RETURN count(r) AS sin_procedencia;"
    echo "# spec 059 — CONVICTED_OF sin firmeza declarada (esperado 0)"
    cyq "MATCH ()-[r:CONVICTED_OF]->() WHERE r.firme IS NULL RETURN count(r) AS convicted_sin_firme;"
    echo "# spec 059 — Badajoz servida como firme (esperado 0)"
    cyq "MATCH ()-[r:CONVICTED_OF]->() WHERE r.source_doc STARTS WITH 'sentencia-ap-badajoz' AND r.status = 'condenado' RETURN count(r) AS badajoz_firme;"
    echo "# lote CENDOJ — Person nuevos mencionados SOLO en los 31 autos (esperado 0)"
    cyq "MATCH (p:Person)-[:MENTIONED_IN]->(:Chunk)-[:PART_OF]->(d:Document) WITH p, collect(DISTINCT d.doc_id) AS docs WHERE all(x IN docs WHERE x IN $NEWDOCS) RETURN count(p) AS person_solo_autos_nuevos;" ;;

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

  *) echo "usage: $0 {mkwork|edit-up|counts-edit|alledges-edit|verify-edit|edit-down|tomac|rmwork}" >&2; exit 2 ;;
esac
