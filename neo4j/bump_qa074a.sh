#!/bin/bash
# Bump del seed del grafo al del Path A de limpieza (spec 074): borrado de 18 nodos Person sin relaciones o sin papel en los casos,
# cita sin nombre en dos relaciones de un familiar anonimizado y contexto corregido. El seed es una RECONSTRUCCIÓN lógica (AC341 de la
# 070), como el anterior: no lleva historia de transacciones ni la base `system`, así que la contraseña de Neo4j la da NEO4J_AUTH de
# sm-neo4j. Este guion comprueba ANTES de tocar nada, sin imprimir ningún valor, que NEO4J_AUTH == «neo4j/» + NEO4J_PASSWORD de sm-api
# y de sm-mcp; si no puede leerlo o no cuadra, aborta.
# Lo lanza el DUEÑO con `!`: `! bash neo4j/bump_qa074a.sh`. Rollback = `! bash neo4j/bump_qa071b.sh` (el seed anterior sigue en R2).
# Gates: md5 local == md5 aceptado en el ensayo == ETag en R2. Luego SEED_URL + SEED_VERSION en sm-neo4j (Render REST) + deploy manual y vigilancia.
# Idempotente: si SEED_URL y SEED_VERSION ya valen lo pedido NO crea otro deploy (FORCE_DEPLOY=1 lo fuerza); la clave de Render sale del Keychain y nunca se imprime.
set -euo pipefail
VER="${VER:-20261120-qa074a}"
MD5_ACEPTADO="${MD5_ACEPTADO:-50c0aa9cde133aaf1b31cff05c5a36f3}"   # tar del ensayo e10 sobre 54d839b
case "$VER" in *AAAA*) echo "❌ VER sin fijar (queda el marcador AAAAMMDD)"; exit 1;; esac
TGZ="${TGZ:-$(dirname "$0")/neo4j-data-$VER.tgz}"
URL="https://pub-409b470f34834cd2ab20ed98ea667170.r2.dev/neo4j-data-$VER.tgz"
SVC=srv-d9fsh0rbc2fs73eefq80   # sm-neo4j
KEY="$(security find-generic-password -a entramado -s render-api-key -w)"
API=https://api.render.com/v1

local_md5=$(md5 -q "$TGZ")
[ "$local_md5" = "$MD5_ACEPTADO" ] || { echo "❌ el tar local no es el aceptado en el ensayo (md5 $local_md5) — NO se hace el bump"; exit 1; }
code=$(curl -s -I -m 20 -o /dev/null -w '%{http_code}' "$URL" || true)
[ "$code" = 000 ] && { echo "❌ r2.dev inaccesible desde aquí (¿bloqueo de LaLiga a Cloudflare? mira hayahora.futbol) — NO se hace el bump"; exit 1; }
etag=$(curl -sI -m 20 "$URL" | tr -d '\r' | awk 'tolower($1)=="etag:"{gsub(/"/,"",$2);print $2}')
echo "md5 local=$local_md5 · ETag R2=$etag"
[ "$local_md5" = "$etag" ] || { echo "❌ md5 != ETag — NO se hace el bump"; exit 1; }

# Cada PUT debe devolver 2xx; si no, se aborta ANTES de tocar la segunda variable o el deploy
# (revisión Codex T1: URL vieja + versión nueva haría que el entrypoint borrase el store y
# restaurase el seed anterior con la etiqueta nueva).
put(){ code=$(curl -s -o /dev/null -w "%{http_code}" -X PUT "$API/services/$SVC/env-vars/$1" \
        -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d "{\"value\":\"$2\"}")
       echo "$1 → HTTP $code"; case "$code" in 2*) ;; *) echo "❌ PUT $1 falló ($code) — NO se despliega"; exit 1;; esac; }
get(){ curl -s "$API/services/$SVC/env-vars/$1" -H "Authorization: Bearer $KEY" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("value",""))' 2>/dev/null; }
# Puerta de la contraseña (seed sin `system`): nunca se imprime ningún valor, solo el veredicto.
SVC_API=srv-d9fsh0rbc2fs73eefq70; SVC_MCP=srv-d9fsh0rbc2fs73eefq6g
getde(){ curl -s -m 20 "$API/services/$1/env-vars/$2" -H "Authorization: Bearer $KEY" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("value",""))' 2>/dev/null || true; }
if [ "${AUTH_COMPROBADA_A_MANO:-0}" = 1 ]; then
  echo "⚠️ puerta de la contraseña saltada (AUTH_COMPROBADA_A_MANO=1): el dueño la comprobó en el panel de Render"
else
  a="$(getde "$SVC" NEO4J_AUTH)"; p1="$(getde "$SVC_API" NEO4J_PASSWORD)"; p2="$(getde "$SVC_MCP" NEO4J_PASSWORD)"
  [ -n "$a" ] && [ -n "$p1" ] && [ -n "$p2" ] || { echo "❌ no puedo leer NEO4J_AUTH / NEO4J_PASSWORD por la API de Render — compruébalo en el panel y relanza con AUTH_COMPROBADA_A_MANO=1"; exit 1; }
  [ "$p1" = "$p2" ] || { echo "❌ NEO4J_PASSWORD de sm-api y sm-mcp no coinciden — NO se hace el bump"; exit 1; }
  [ "$a" = "neo4j/$p1" ] || { echo "❌ NEO4J_AUTH de sm-neo4j no es «neo4j/» + la clave que usa la API — corrige NEO4J_AUTH (no la de la API) y relanza. NO se hace el bump"; exit 1; }
  [ "${#p1}" -ge 8 ] || { echo "❌ la clave tiene menos de 8 caracteres: Neo4j no arrancaría — NO se hace el bump"; exit 1; }
  unset a p1 p2
  echo "✅ NEO4J_AUTH coincide con la clave de sm-api y sm-mcp"
fi
# Si las dos variables ya valen lo esperado (un intento anterior las guardó) NO se da el bump por hecho: el deploy pudo no
# crearse o fallar. Se salta solo la escritura y se pide el deploy igualmente; repetirlo con la misma versión es inocuo
# (el entrypoint no restaura dos veces la misma versión). No hay bandera para saltarse el deploy: el éxito solo lo da un deploy visto «live».
if [ "$(get SEED_URL)" = "$URL" ] && [ "$(get SEED_VERSION)" = "$VER" ]; then
  echo "ℹ️ SEED_URL y SEED_VERSION ya valen $VER: no se reescriben"
else
  put SEED_URL "$URL"; sleep 2
  put SEED_VERSION "$VER"; sleep 2
fi
[ "$(get SEED_URL)" = "$URL" ] && [ "$(get SEED_VERSION)" = "$VER" ] || { echo "❌ relectura de env vars ≠ esperado — NO se despliega"; exit 1; }
echo "✅ env vars verificadas por relectura"
dep=$(curl -s -X POST "$API/services/$SVC/deploys" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{"clearCache":"clear"}')   # a propósito SIN caché de capas: neo4j:5.26 es un tag flotante y el rebuild recoge el ca-certificates corregido (eMudhra, oct-2026)
dep_id=$(echo "$dep" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("id",""))' 2>/dev/null)
[ -n "$dep_id" ] || { echo "❌ deploy no creado: $(echo "$dep" | head -c 300)"; exit 1; }
echo "deploy: $dep_id"
echo "vigilando $dep_id (Ctrl-C para salir)…"
for i in $(seq 1 40); do
  sleep 15
  st=$(curl -s -m 20 "$API/services/$SVC/deploys/$dep_id" -H "Authorization: Bearer $KEY" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("status"))' 2>/dev/null || echo "?")
  echo "  $((i*15))s: $st"
  case "$st" in live) echo "✅ live — comprobar log: [seed] restoring store version '$VER' from R2 / restore done"; exit 0;; build_failed|update_failed|canceled|deactivated) echo "❌ $st"; exit 1;; esac
done
echo "⏳ el deploy $dep_id sigue en curso tras 10 min: NO está confirmado. Mirar el dashboard hasta «live» (salida 2)"; exit 2
