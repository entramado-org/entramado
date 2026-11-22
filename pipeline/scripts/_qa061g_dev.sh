#!/bin/bash
# Path A — spec 061, Fase 4 T0e (2026-09-18/19): overlays de la ronda humana T0e a tres lectores (60 summaries, 30 roles, 21 alias_deny; PR org #63)
# (summaries_qa061 / roles_qa061 / relations.yaml alias_deny) sobre el seed vivo 20260916-qa061f .
# Efímero sobre el tgz local del seed (md5==ETag R2), extraído a $REPO/neo4j/data.qa061g/data.
# Corre DENTRO de sm-dev con copia de trabajo VM-local (escritura pesada sobre virtiofs no persiste).
# Clon de _qa061f_dev.sh (qa061f → qa061g; base 20260916-qa061f). Sin migración manual: solo loaders.
#
# Secuencia:
#   extract → mkwork → edit-up → counts-edit → alledges-edit >pre.tsv →
#   [Mac, NEO4J_URI=bolt://<ip-sm-dev>:7689 NEO4J_AUTH none:
#      make relations → make context → make accusations → make documents → make ultima-hora → make claims → make mercantil
#      → make contratacion → make contratacion-ampliada → make subvenciones   (T0e solo toca relations alias_deny + context summaries/roles;
#      el resto del ciclo se repite por el gate de idempotencia — esperado: 8757/37041, diff alledges/accstatus = 0)
#      → GATE idempotencia: repetir TODOS los loaders y exigir alledges + accstatus idénticos
#      → gen_symmetric_collapse_migration → compute_degree → make centralidad] →
#   ⚠️ relations SIEMPRE antes que context: los splits deben crear el namesake antes de que
#   load_merges borre el duplicado y de que pseudonym_guard exija el nuevo seudónimo.
#   verify-edit → alledges-edit >post.tsv → [Mac: diff pre/post 100% mapeado a los overlays] →
#   edit-down → tar <ver> → bootverify (_qa061_bootverify.sh) → subir R2 (una vez) + bump (dueño)
#
# Despacho por PATH (el inline se rompe con {}()[] — gotcha conocido):
#   container machine run -n sm-dev --root -- bash <repo>/pipeline/scripts/_qa061g_dev.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TGZ="$REPO/neo4j/neo4j-data-20260916-qa061f.tgz"
SRC="$REPO/neo4j/data.qa061g/data"
STORE=/var/lib/sm-qa061g
WORK="$STORE/work"
OUT="$REPO/neo4j/data.qa061g.out"
EDIT=sm_neo4j_editqa061g
PORT=7689

# NEO4J_AUTH=none en el efímero (el seed lleva la password de PROD) — user/pass dummy.
cyq(){ docker exec "$EDIT" cypher-shell -u neo4j -p neo4j --format plain "$1"; }

counts_q="MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;"
bidir_q="MATCH (a)-[r1:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(b)-[r2:ASSOCIATED_WITH|MET_WITH|FAMILY_OF]->(a) WHERE id(a) < id(b) AND type(r1)=type(r2) RETURN count(DISTINCT [id(a),id(b)]) AS bidir_pairs;"
iso_q="MATCH (n) WHERE n.entity_id IS NOT NULL AND n.summary IS NOT NULL AND NOT EXISTS { MATCH (n)-[r]-(m) WHERE m.entity_id IS NOT NULL AND NOT type(r) IN ['MENTIONED_IN','PART_OF'] } RETURN count(n) AS context_isolated_nodes;"

alledges_q="MATCH (a)-[r]->(b)
WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
RETURN type(r) AS t, a.entity_id AS de, b.entity_id AS a_nodo,
       coalesce(r.source_doc,'') AS doc, left(coalesce(r.source_quote,''),40) AS quote
ORDER BY t, de, a_nodo, doc, quote;"

case "${1:-}" in
  extract)
    if [ -d "$SRC/databases" ]; then echo "✅ seed ya extraído en $SRC"; exit 0; fi
    [ -f "$TGZ" ] || { echo "❌ falta $TGZ" >&2; exit 1; }
    mkdir -p "$SRC"
    # el entrypoint de prod hace strip-components=1 sobre el wrapper data/
    tar -xzf "$TGZ" -C "$SRC" --strip-components=1
    [ -d "$SRC/databases" ] || { echo "❌ layout del tar incorrecto (falta wrapper data/)" >&2; exit 1; }
    echo "✅ seed extraído en $SRC" ;;

  mkwork)
    if [ -e "$WORK" ]; then echo "❌ $WORK already exists — rmwork first" >&2; exit 1; fi
    if [ ! -d "$SRC/databases" ]; then echo "❌ store del seed no encontrado en $SRC (extract primero)" >&2; exit 1; fi
    echo "copying seed store ($SRC) -> $WORK (VM-local)…"
    mkdir -p "$WORK"; cp -a "$SRC/." "$WORK/"
    chown -R 7474:7474 "$WORK"
    echo "✅ work copy ready ($(du -sh "$WORK" | cut -f1))" ;;

  edit-up)
    docker rm -f "$EDIT" >/dev/null 2>&1 || true
    docker run -d --name "$EDIT" -p 0.0.0.0:$PORT:7687 \
      -v "$WORK:/data" \
      -e NEO4J_AUTH=none \
      -e NEO4J_PLUGINS='["apoc"]' \
      -e NEO4J_dbms_security_procedures_unrestricted='apoc.*' \
      neo4j:5.26 >/dev/null
    echo "waiting for $EDIT bolt…"
    for i in $(seq 1 90); do
      if docker exec "$EDIT" cypher-shell -u neo4j -p neo4j "RETURN 1;" >/dev/null 2>&1; then
        echo "✅ $EDIT ready on :$PORT (over $WORK)"; exit 0
      fi
      sleep 2
    done
    echo "❌ $EDIT not ready" >&2; docker logs --tail 30 "$EDIT" >&2; exit 1 ;;

  counts-edit)   cyq "$counts_q" ;;
  alledges-edit) cyq "$alledges_q" ;;
  # T0d: foto del estado procesal por arista (el diff alledges no ve cambios de status).
  accstatus-edit) cyq "MATCH (a)-[r:ACCUSED_OF|CONVICTED_OF]->(b) RETURN type(r) AS t, a.entity_id AS de, b.entity_id AS a_nodo, coalesce(r.source_kind,'') AS kind, coalesce(r.status,'') AS status, coalesce(r.status_fuente,'') AS fuente, coalesce(r.status_fuente_tipo,'') AS tipo, coalesce(r.firme,'') AS firme ORDER BY de, a_nodo, t, kind, status;" ;;
  verify-edit)
    cyq "$counts_q"; cyq "$bidir_q"; cyq "$iso_q"
    echo "# spec 059 — source_kind null (esperado 0)"
    cyq "MATCH ()-[r]->() WHERE r.source_kind IS NULL AND NOT type(r) IN ['MENTIONED_IN','PART_OF','ABOUT'] RETURN count(r) AS sin_procedencia;"
    echo "# spec 059 — CONVICTED_OF sin firmeza declarada (esperado 0)"
    cyq "MATCH ()-[r:CONVICTED_OF]->() WHERE r.firme IS NULL RETURN count(r) AS convicted_sin_firme;"
    echo "# 061 D5 — Person is_pseudonym con summary (esperado 0)"
    cyq "MATCH (p:Person) WHERE p.is_pseudonym = true AND p.summary IS NOT NULL RETURN count(p) AS pseudonimos_con_summary;"
    echo "# 061 D5 — Person sin aristas de dominio (esperado 0)"
    cyq "MATCH (p:Person) WHERE NOT EXISTS { MATCH (p)-[r]-(m) WHERE NOT m:Chunk AND NOT m:Document } RETURN count(p) AS person_huerfanas;"
    echo "# 061 D5 — aristas contexto sin source_url (esperado 0)"
    cyq "MATCH ()-[r]->() WHERE r.source_kind = 'contexto' AND r.source_url IS NULL RETURN count(r) AS contexto_sin_fuente;"
    echo "# 061 D7 — ACCUSED_OF judiciales con status null (informativo; el default de API es investigado)"
    cyq "MATCH ()-[r:ACCUSED_OF]->() WHERE r.status IS NULL RETURN coalesce(r.source_kind,'null') AS kind, count(r) AS n ORDER BY n DESC;"
    echo "# 061 D7 — aristas con status_fecha propagado (informativo)"
    cyq "MATCH ()-[r:ACCUSED_OF]->() WHERE r.status_fecha IS NOT NULL RETURN count(r) AS con_registro;" ;;

  edit-down)
    docker stop -t 40 "$EDIT" && docker rm "$EDIT"
    echo "✅ $EDIT stopped cleanly" ;;

  tomac)
    if [ -e "$OUT" ]; then echo "❌ $OUT already exists — remove it first" >&2; exit 1; fi
    if docker ps --format '{{.Names}}' | grep -q "^$EDIT$"; then echo "❌ $EDIT still running — edit-down first" >&2; exit 1; fi
    echo "copying $WORK -> $OUT (VM -> Mac via home-mount)…"
    mkdir -p "$OUT"; cp -a "$WORK/." "$OUT/"
    echo "✅ store copied to Mac ($(du -sh "$OUT" | cut -f1))" ;;

  tar)
    # Tar del store de trabajo DENTRO de Linux (sin AppleDouble), wrapper data/ como espera el entrypoint.
    VER="${2:?version, p.ej. 20260909-qa061}"; TGZ_OUT="$REPO/neo4j/neo4j-data-$VER.tgz"
    if docker ps --format '{{.Names}}' | grep -q "^$EDIT$"; then echo "❌ $EDIT still running — edit-down first" >&2; exit 1; fi
    [ -e "$TGZ_OUT" ] && { echo "❌ $TGZ_OUT already exists" >&2; exit 1; }
    tar czf "$TGZ_OUT" --exclude '._*' --transform 's,^work,data,' -C "$STORE" work
    tar -tzf "$TGZ_OUT" | head -3
    echo "✅ $TGZ_OUT ($(du -h "$TGZ_OUT" | cut -f1))" ;;

  rmwork)
    docker rm -f "$EDIT" >/dev/null 2>&1 || true
    rm -rf "$STORE" && echo "removed $STORE" ;;

  *) echo "usage: $0 {extract|mkwork|edit-up|counts-edit|alledges-edit|accstatus-edit|verify-edit|edit-down|tar <ver>|tomac|rmwork}" >&2; exit 2 ;;
esac
