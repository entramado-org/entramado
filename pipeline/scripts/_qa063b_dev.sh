#!/bin/bash
# Path A — spec 063 (2026-09-28): tramo 1 de la auditoría escalonada (50 entidades de más riesgo) sobre el seed vivo
# 20260924-qa063a: relations/context/accusations/ultima_hora/claims; feed, briefings y registro viajan fuera del grafo.
# Efímero sobre el tgz local del seed (md5==ETag R2), extraído a $REPO/neo4j/data.qa063b/data.
# Corre DENTRO de sm-dev con copia de trabajo VM-local (escritura pesada sobre virtiofs no persiste).
# Clon de _qa061g_dev.sh (base 20260924-qa063a). Sin migración manual: solo loaders.
#
# Secuencia:
#   extract → mkwork → edit-up → counts-edit → alledges-edit >pre.tsv →
#   [Mac, NEO4J_URI=bolt://<ip-sm-dev>:7689 NEO4J_AUTH none:
#      make contracts → make relations → make context → make accusations → make documents → make ultima-hora → make claims → make mercantil
#      → make contratacion → make contratacion-ampliada → make subvenciones   (063: relations deny/fix + context summaries; el resto se repite por el gate de idempotencia)
#      → GATE idempotencia: repetir TODOS los loaders y exigir alledges + accstatus idénticos
#      → gen_symmetric_collapse_migration → compute_degree → make centralidad] →
#   ⚠️ contracts ANTES de relations (revisión Codex 063, 29-sep): re-crea aristas de su YAML y relations tiene la
#   última palabra. `make prensa` está CONGELADO sobre el linaje (aborta si ya hay prensa): reescribía citas
#   auditadas (Gallardo MEMBER_OF PSOE) y resucitaba retiradas; las correcciones de prensa van a relations.yaml.
#   ⚠️ relations SIEMPRE antes que context: los splits deben crear el namesake antes de que
#   load_merges borre el duplicado y de que pseudonym_guard exija el nuevo seudónimo.
#   verify-edit → alledges-edit >post.tsv → [Mac: diff pre/post 100% mapeado a los overlays] →
#   edit-down → tar <ver> → bootverify (_qa061_bootverify.sh) → subir R2 (una vez) + bump (dueño)
#
# Despacho por PATH (el inline se rompe con {}()[] — gotcha conocido):
#   container machine run -n sm-dev --root -- bash <repo>/pipeline/scripts/_qa063b_dev.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TGZ="${BASE_TGZ:-$REPO/neo4j/neo4j-data-20260924-qa063a.tgz}"
SRC="${BASE_SRC:-$REPO/neo4j/data.qa063b/data}"
STORE=/var/lib/sm-qa063b
WORK="$STORE/work"
OUT="$REPO/neo4j/data.qa063b.out"
EDIT=sm_neo4j_editqa063b
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

# Spec 070: los seudónimos declarados, como lista Cypher (ids [A-Za-z0-9:-], seguros entre comillas simples).
seudonimos_cypher(){
  local file="$REPO/pipeline/data/context/pseudonyms.yaml" lista
  lista=$(grep -oE '^- id: "?Person:[a-z0-9-]+' "$file" | sed -E "s/^- id: \"?(.*)/'\1'/" | paste -sd, -)
  [ -n "$lista" ] || { echo "❌ sin seudónimos en $file" >&2; return 1; }
  printf '[%s]' "$lista"
}
# El valor de una consulta de una sola columna (cypher-shell --format plain: cabecera + valor).
cyv(){ cyq "$1" | tail -1; }

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
    for _ in $(seq 1 90); do
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

  # Spec 070 — fixture de la política de seudónimos (en la VM): por cada seudónimo declarado, una arista
  # judicial a una sociedad sintética, otra a un organismo sintético SIN entity_id (la política lo reconoce
  # por etiqueta) y un alias sintético. El primer `make relations` tiene que borrarlos.
  fixture-up)
    lista=$(seudonimos_cypher)
    cyq "MERGE (c:Company {entity_id:'Company:__fixture_070__'}) SET c.name = '__fixture_070__'
         MERGE (s:Organization {name:'__fixture_070_sin_id__'})
         WITH c, s UNWIND $lista AS sid MATCH (p {entity_id: sid})
         MERGE (p)-[r:ASSOCIATED_WITH {source_doc:'FIXTURE_070'}]->(c) SET r.source_kind = 'judicial'
         MERGE (p)-[r2:WORKS_FOR {source_doc:'FIXTURE_070'}]->(s) SET r2.source_kind = 'judicial'
         SET p.aliases = CASE WHEN '__alias_fixture_070__' IN coalesce(p.aliases, []) THEN p.aliases
                              ELSE coalesce(p.aliases, []) + '__alias_fixture_070__' END
         RETURN count(DISTINCT p) AS seudonimos_con_fixture;" ;;

  # Tras el ciclo 1: 0 aristas y 0 alias fixture, los dos nodos fixture (ya sin aristas) borrados, y tantas
  # aristas seudónimo↔sociedad/organismo (por etiqueta, como la política) como espera seudonimos_permitidos.yaml:
  # una por entrada, o una por documento si la entrada acota `docs` (<n> = seudonimos.aristas_esperadas, lo
  # calcula el Mac).
  fixture-check)
    n="${2:?aristas esperadas de seudonimos_permitidos.yaml}"
    lista=$(seudonimos_cypher)
    aristas=$(cyv "MATCH ()-[r]->() WHERE r.source_doc = 'FIXTURE_070' RETURN count(r) AS n;")
    alias=$(cyv "MATCH (x) WHERE '__alias_fixture_070__' IN coalesce(x.aliases, []) RETURN count(x) AS n;")
    cyq "MATCH (c {entity_id:'Company:__fixture_070__'}) WHERE NOT (c)--() DELETE c;" >/dev/null
    cyq "MATCH (s:Organization {name:'__fixture_070_sin_id__'}) WHERE s.entity_id IS NULL AND NOT (s)--() DELETE s;" >/dev/null
    nodo=$(cyv "MATCH (c {entity_id:'Company:__fixture_070__'}) RETURN count(c) AS n;")
    sin_id=$(cyv "MATCH (s:Organization {name:'__fixture_070_sin_id__'}) RETURN count(s) AS n;")
    sensibles=$(cyv "UNWIND $lista AS sid MATCH (p {entity_id: sid})-[r]-(o)
                     WHERE o:Company OR o:Organization
                     RETURN count(r) AS n;")
    echo "fixture 070: aristas=$aristas alias=$alias nodo=$nodo sin_id=$sin_id · seudónimo↔sociedad/organismo=$sensibles (lista: $n)"
    if [ "$aristas" != 0 ] || [ "$alias" != 0 ] || [ "$nodo" != 0 ] || [ "$sin_id" != 0 ] || [ "$sensibles" != "$n" ]; then
      echo "❌ la política de seudónimos no dejó el grafo como la lista" >&2; exit 1
    fi
    echo "✅ fixture 070 retirado; aristas seudónimo↔sociedad/organismo = lista" ;;

  # En el MAC (bolt :7689, como los loaders), no en la VM: export completo + canonico.tsv (spec 070).
  snap-edit)
    mkdir -p "${2:?outdir}"; out="$(cd "$2" && pwd)"
    (cd "$REPO/pipeline" && uv run -q python scripts/_qa063b_export_snap.py "$out") ;;

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
    tar -tzf "$TGZ_OUT" | sed -n '1,3p'   # sed lee el listado entero: head cortaría con SIGPIPE (141) bajo pipefail
    echo "✅ $TGZ_OUT ($(du -h "$TGZ_OUT" | cut -f1))" ;;

  rmwork)
    docker rm -f "$EDIT" >/dev/null 2>&1 || true
    rm -rf "$STORE" && echo "removed $STORE" ;;

  *) echo "usage: $0 {extract|mkwork|edit-up|counts-edit|alledges-edit|accstatus-edit|verify-edit|fixture-up|fixture-check <n>|snap-edit <dir> (Mac)|edit-down|tar <ver>|tomac|rmwork}" >&2; exit 2 ;;
esac
