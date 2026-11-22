#!/bin/bash
# Bump del seed del grafo al de Path A1 del #22 (spec 071, «registro fiel»): ajustes de relations/context/accusations/claims de la 071 —retiradas
# de aristas que el registro público declaraba suprimidas, dos aristas repuestas, el referente «Ocariz» sin identificar— sobre el seed 20261007-qa067b.
# Lo lanza el DUEÑO con `!`: `! VER=<AAAAMMDD>-qa071a bash neo4j/bump_qa071a.sh`. Rollback = volver a 20261007-qa067b (VER=20261007-qa067b).
# Gate: md5 local == ETag en R2. Luego SEED_URL + SEED_VERSION en sm-neo4j (Render REST) + deploy manual y vigilancia.
# Idempotente: si SEED_URL y SEED_VERSION ya valen lo pedido NO crea otro deploy (FORCE_DEPLOY=1 lo fuerza); la clave de Render sale del Keychain y nunca se imprime.
set -euo pipefail
VER="${VER:-20261108-qa071a}"
case "$VER" in *AAAA*) echo "❌ VER sin fijar (queda el marcador AAAAMMDD)"; exit 1;; esac
TGZ="${TGZ:-$(dirname "$0")/neo4j-data-$VER.tgz}"
URL="https://pub-409b470f34834cd2ab20ed98ea667170.r2.dev/neo4j-data-$VER.tgz"
SVC=srv-d9fsh0rbc2fs73eefq80   # sm-neo4j
KEY="$(security find-generic-password -a entramado -s render-api-key -w)"
API=https://api.render.com/v1

local_md5=$(md5 -q "$TGZ")
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
if [ "${FORCE_DEPLOY:-0}" != 1 ] && [ "$(get SEED_URL)" = "$URL" ] && [ "$(get SEED_VERSION)" = "$VER" ]; then
  echo "✅ SEED_URL y SEED_VERSION ya valen $VER: no se crea otro deploy (FORCE_DEPLOY=1 para forzarlo)"; exit 0
fi
put SEED_URL "$URL"; sleep 2
put SEED_VERSION "$VER"; sleep 2
[ "$(get SEED_URL)" = "$URL" ] && [ "$(get SEED_VERSION)" = "$VER" ] || { echo "❌ relectura de env vars ≠ esperado — NO se despliega"; exit 1; }
echo "✅ env vars verificadas por relectura"
dep=$(curl -s -X POST "$API/services/$SVC/deploys" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{"clearCache":"clear"}')   # a propósito SIN caché de capas: neo4j:5.26 es un tag flotante y el rebuild recoge el ca-certificates corregido (eMudhra, oct-2026)
dep_id=$(echo "$dep" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("id",""))' 2>/dev/null)
[ -n "$dep_id" ] || { echo "❌ deploy no creado: $(echo "$dep" | head -c 300)"; exit 1; }
echo "deploy: $dep_id"
echo "vigilando $dep_id (Ctrl-C para salir)…"
for i in $(seq 1 40); do
  sleep 15
  st=$(curl -s "$API/services/$SVC/deploys/$dep_id" -H "Authorization: Bearer $KEY" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("status"))' 2>/dev/null || echo "?")
  echo "  $((i*15))s: $st"
  case "$st" in live) echo "✅ live — comprobar log: [seed] restoring store version '$VER' from R2 / restore done"; exit 0;; build_failed|update_failed|canceled|deactivated) echo "❌ $st"; exit 1;; esac
done
echo "⏳ sigue en curso; mirar el dashboard"
