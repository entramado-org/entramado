#!/bin/bash
# Bump del seed del grafo a 20260911-qa061c (spec 061 Fase 4: overlays de la tercera ronda humana T0c + poda de 3 nodos aislados) — lo lanza el DUEÑO: `! bash neo4j/bump_qa061c.sh`
# Gate: md5 local == ETag en R2. Luego SEED_URL + SEED_VERSION en sm-neo4j (Render REST) + deploy manual
# (el buildFilter de sm-neo4j no salta solo) y vigilancia del deploy. Rollback = volver a 20260911-qa061b.
set -euo pipefail
VER=20260911-qa061c
TGZ="$(dirname "$0")/neo4j-data-$VER.tgz"
URL="https://pub-409b470f34834cd2ab20ed98ea667170.r2.dev/neo4j-data-$VER.tgz"
SVC=srv-d9fsh0rbc2fs73eefq80   # sm-neo4j
KEY="$(security find-generic-password -a entramado -s render-api-key -w)"
API=https://api.render.com/v1

local_md5=$(md5 -q "$TGZ")
etag=$(curl -sI "$URL" | tr -d '\r' | awk 'tolower($1)=="etag:"{gsub(/"/,"",$2);print $2}')
echo "md5 local=$local_md5 · ETag R2=$etag"
[ "$local_md5" = "$etag" ] || { echo "❌ md5 != ETag — NO se hace el bump"; exit 1; }

put(){ curl -s -o /dev/null -w "%{http_code}" -X PUT "$API/services/$SVC/env-vars/$1" \
        -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d "{\"value\":\"$2\"}"; }
echo "SEED_URL     → $(put SEED_URL "$URL")"; sleep 2
echo "SEED_VERSION → $(put SEED_VERSION "$VER")"; sleep 2
dep=$(curl -s -X POST "$API/services/$SVC/deploys" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{"clearCache":"do_not_clear"}')
echo "deploy: $(echo "$dep" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("id"), d.get("status"))' 2>/dev/null || echo "$dep" | head -c 200)"
echo "vigilando (Ctrl-C para salir)…"
for i in $(seq 1 40); do
  sleep 15
  st=$(curl -s "$API/services/$SVC/deploys?limit=1" -H "Authorization: Bearer $KEY" | python3 -c 'import sys,json; d=json.load(sys.stdin); d=d[0]["deploy"] if isinstance(d,list) else d; print(d.get("status"))' 2>/dev/null || echo "?")
  echo "  $((i*15))s: $st"
  case "$st" in live) echo "✅ live — comprobar log: [seed] restoring store version '$VER' from R2 / restore done"; exit 0;; build_failed|update_failed|canceled|deactivated) echo "❌ $st"; exit 1;; esac
done
echo "⏳ sigue en curso; mirar el dashboard"
