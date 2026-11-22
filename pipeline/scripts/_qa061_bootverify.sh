#!/bin/bash
# Boot-verify del tar 061 (Fase 1: retiradas aprobadas + registro de estado + overlays). Extrae el tgz a un
# dir VM-local REPLICANDO el seed-entrypoint de prod (`tar xz --strip-components=1`, espera wrapper `data/`),
# arranca un Neo4j fresco y comprueba counts + spot-checks. Despacho por PATH:
#   container machine run -n sm-dev --root -- env PY=<python con PyYAML> TGZ=<repo>/neo4j/neo4j-data-<ver>.tgz EXP_NODES=N EXP_RELS=M \
#     bash <repo>/pipeline/scripts/_qa061_bootverify.sh
set -euo pipefail
: "${TGZ:?TGZ=<ruta al tgz>}"; : "${EXP_NODES:?EXP_NODES}"; : "${EXP_RELS:?EXP_RELS}"
STORE=/var/lib/sm-qa061-bootverify
BV=sm_neo4j_bootverify_qa061

docker rm -f "$BV" >/dev/null 2>&1 || true
rm -rf "$STORE"; mkdir -p "$STORE/data"
tar -xzf "$TGZ" -C "$STORE/data" --strip-components=1
[ -d "$STORE/data/databases" ] || { echo "❌ layout del tar incorrecto (falta wrapper data/)"; exit 1; }
if tar -tzf "$TGZ" | grep -q '/\._'; then echo "❌ el tar arrastra AppleDouble ._*"; exit 1; fi
chown -R 7474:7474 "$STORE/data"
docker run -d --name "$BV" -v "$STORE/data:/data" -e NEO4J_AUTH=none neo4j:5.26 >/dev/null
echo "waiting for $BV bolt…"
for i in $(seq 1 90); do
  if docker exec "$BV" cypher-shell -u neo4j -p neo4j "RETURN 1;" >/dev/null 2>&1; then break; fi
  sleep 2
done
cy(){ docker exec "$BV" cypher-shell -u neo4j -p neo4j --format plain "$1"; }
echo "# counts (esperado $EXP_NODES / $EXP_RELS)"
got=$(cy "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels;" | tail -1 | tr -d ' "')
[ "$got" = "$EXP_NODES,$EXP_RELS" ] || { echo "❌ counts $got"; docker rm -f "$BV" >/dev/null; exit 1; }
echo "✅ counts $got"
# Las comprobaciones con valor esperado FALLAN si no lo cumplen (antes solo se imprimían y el guion acababa en ✅).
# Un valor distinto de 0 conocido y aceptado se declara al lanzar: EXP_SEUD=a,b EXP_HUERFANAS=n EXP_CTX_SIN_FUENTE=n.
fallos=0
esperado(){ # esperado <etiqueta> <valor esperado> <consulta de una fila>
  local v; v=$(cy "$3" | tail -1 | tr -d ' "')
  if [ "$v" = "$2" ]; then echo "✅ $1: $v"; else echo "❌ $1: $v (esperado $2)"; fallos=$((fallos+1)); fi
}
esperado "is_pseudonym con summary, con role narrativo" "${EXP_SEUD:-0,0}" "MATCH (p:Person) WHERE p.is_pseudonym = true RETURN count(CASE WHEN p.summary IS NOT NULL THEN 1 END) AS con_summary, count(CASE WHEN p.role IS NOT NULL AND p.role <> '' AND NOT p.role STARTS WITH 'Seudónimo de anonimización judicial' THEN 1 END) AS con_role_narrativo;"
cy "MATCH (p:Person) WHERE p.is_pseudonym = true AND ((p.role IS NOT NULL AND p.role <> '' AND NOT p.role STARTS WITH 'Seudónimo de anonimización judicial') OR p.summary IS NOT NULL) RETURN p.entity_id AS id, left(p.role,90) AS role, p.source_kind AS kind, p.is_press AS press, p.is_context AS ctx;"
# «Con relación» es el mismo predicado con que el API decide si sirve a una persona (api/app/graph_queries.py, _served):
# un vecino DISTINTO de ella con entity_id o un Claim, por una arista que no sea de procedencia.
CON_RELACION="m <> p AND (m.entity_id IS NOT NULL OR m:Claim) AND NOT type(r) IN ['MENTIONED_IN', 'PART_OF']"
# Todo seudónimo declarado en context/pseudonyms.yaml ha de existir y llevar is_pseudonym = true, tenga o no relaciones:
# de esa marca depende que el API no sirva sus fragmentos.
# La lista la da el mismo lector que usa el cargador (seudonimos.ids_seudonimos): PY ha de ser un Python con PyYAML
# (el venv del pipeline). Si no se puede leer, la comprobación falla; no hay lectura alternativa.
PIPE="$(cd "$(dirname "$0")/.." && pwd)"
DECL="$(cd "$PIPE" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "${PY:-python3}" -c 'import os, sys
import src.seudonimos as m
yml = os.path.join(os.getcwd(), "data", "context", "pseudonyms.yaml")
if os.path.realpath(m.__file__) != os.path.realpath(os.path.join(os.getcwd(), "src", "seudonimos.py")) or not os.path.isfile(yml):
    sys.exit("el lector o el YAML no son los de este checkout")
ids = sorted(m.ids_seudonimos(yml))
if len(ids) < 11 or any(c in i for i in ids for c in " \x27\x22\\\n,"):
    sys.exit("lista de seudónimos inesperada")
print("\n".join(ids))')" || { echo "❌ no puedo leer los seudónimos declarados con ${PY:-python3} (PY=<python con PyYAML>)"; fallos=$((fallos+1)); DECL=""; }
[ -n "$DECL" ] || { [ "$fallos" -gt 0 ] || { echo "❌ lista de seudónimos declarados vacía"; fallos=$((fallos+1)); }; }
LISTA="$(printf '%s\n' "$DECL" | sed "s/.*/'&'/" | paste -sd, -)"
esperado "seudónimos declarados sin nodo o sin marca is_pseudonym = true" "0" "UNWIND [$LISTA] AS id OPTIONAL MATCH (p:Person {entity_id: id}) WITH id, p WHERE p IS NULL OR coalesce(p.is_pseudonym, false) <> true RETURN count(id) AS declarados_sin_marca;"
# Una marca is_pseudonym que no sea booleana escaparía a las dos comprobaciones siguientes.
esperado "Person con is_pseudonym no booleano" "0" "MATCH (p:Person) WHERE p.is_pseudonym IS NOT :: BOOLEAN RETURN count(p) AS marca_no_booleana;"
# El API aún cuenta una autorrelación como relación; aquí se exige que no haya ninguna en una persona.
esperado "Person con una relación consigo misma (de cualquier tipo)" "0" "MATCH (p:Person)-[r]->(p) RETURN count(r) AS autorrelaciones;"
esperado "Person huérfanas (sin los seudónimos judiciales, que la guarda 059 exige presentes)" "${EXP_HUERFANAS:-0}" "MATCH (p:Person) WHERE coalesce(p.is_pseudonym, false) = false AND NOT EXISTS { MATCH (p)-[r]-(m) WHERE $CON_RELACION } RETURN count(p) AS huerfanas;"
# Los seudónimos judiciales sin relaciones se quedan en el grafo (la guarda de contexto de la spec 059 falla si falta uno
# declarado) y han de ser exactamente los aprobados (spec 074): sus identificadores, que ya son públicos en
# context/pseudonyms.yaml, comparados en Cypher como lista ordenada (un nodo sin entity_id cuenta como '' y hace fallar la comparación). Cambiar el conjunto exige decidirlo y actualizar esta lista.
esperado "seudónimos judiciales sin relaciones: exactamente los 11 aprobados" "TRUE" "MATCH (p:Person) WHERE p.is_pseudonym = true AND NOT EXISTS { MATCH (p)-[r]-(m) WHERE $CON_RELACION } WITH coalesce(p.entity_id, '') AS id ORDER BY id WITH collect(id) AS ids RETURN size(ids) = 11 AND ids = ['Person:adrian', 'Person:alfonso', 'Person:apolonia', 'Person:claudio-seudonimo-ts', 'Person:elias', 'Person:gonzalo', 'Person:justa', 'Person:justiniano', 'Person:mercedes', 'Person:pedro-antonio-seudonimo-ts', 'Person:tomas'] AS exactos;"
esperado "contexto sin fuente (ni source_url ni documento con página)" "${EXP_CTX_SIN_FUENTE:-0}" "MATCH ()-[r]->() WHERE r.source_kind = 'contexto' AND r.source_url IS NULL AND (r.source_doc IS NULL OR r.source_page IS NULL) RETURN count(r) AS contexto_sin_fuente;"
echo "# ultima_hora ACCUSED_OF caducadas (informativo)"
cy "MATCH ()-[r:ACCUSED_OF]->() WHERE r.source_kind = 'ultima_hora' RETURN count(r) AS uh_accused;"
echo "# spec 059 guards"
cy "MATCH ()-[r]->() WHERE r.source_kind IS NULL AND NOT type(r) IN ['MENTIONED_IN','PART_OF','ABOUT'] RETURN count(r) AS sin_procedencia;"
cy "MATCH ()-[r:CONVICTED_OF]->() WHERE r.firme IS NULL RETURN count(r) AS convicted_sin_firme;"
echo "# registro de estado propagado"
cy "MATCH ()-[r:ACCUSED_OF]->() WHERE r.status_fecha IS NOT NULL RETURN count(r) AS con_registro;"
echo "# ADIF (RECEIVED sin date '26 de marzo de 2020')"
cy "MATCH ()-[r:RECEIVED]->() WHERE r.date = '26 de marzo de 2020' RETURN count(r) AS adif_date_restante;"
docker rm -f "$BV" >/dev/null; rm -rf "$STORE"
[ "$fallos" -eq 0 ] || { echo "❌ bootverify: $fallos comprobación(es) fuera de lo esperado"; exit 1; }
echo "✅ bootverify done"
