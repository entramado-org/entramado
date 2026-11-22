#!/bin/bash
# Bump del seed del grafo a 20261004-qa064a («Anibal» declarado seudónimo judicial sin resumen; un alias de seudónimo denegado; antes 20261004-qa063z, vigía BOE: jubilaciones de Peinado y Serrano Molera; rol y resumen de Peinado en pasado con el auto de 21-09-2026; antes 20261004-qa063y, cierre de la auditoría: L23 — 22 citados de la pieza Leire con estado investigado registrado, Ana Esther Sánchez García no investigada; restos 061 — Molano, Vox con fuente, Zapatero investigado; L21 — 136 alias genéricos denegados (El Plural conservado); «corpus» fuera del texto publicado; antes 20261004-qa063w, 063 pendientes + revisión Codex: renombrado versionado, nota Evaristo→Benito neutralizada, Cristina Álvarez acusada solo por el software; seudónimo Benito renombrado, notas y citas con cargo de seudónimos retiradas, jerga técnica fuera del texto publicado, Begoña Gómez y Cristina Álvarez «acusadas»; antes 20261003-qa063u, spec 063 tramo 6 + revisión Codex: textos servidos sin domicilios/catastro/matrículas/teléfonos, 19 seudónimos sin cargos identificativos, favorables repuestos; 187 entidades de riesgo bajo — tanda decidida, retiradas de vínculos sobredimensionados, Avintia y Beyol ADMINISTRA, domicilios particulares, vehículos y teléfonos retirados; antes 20261003-qa063s, spec 063 tramo 5 + revisión Codex: fichas de seudónimos sin fragmentos, Hidalgo→Air Europa vinculado, fuentes de Rubén Eladio; 271 entidades de riesgo medio — tanda decidida, 32 alias artefacto denegados, seudónimos Carlos Francisco/Luis Miguel, cargos identificativos de Elias/Plácido/Segismundo/Justa retirados, Teliz borrado, una testigo sin INVOLVES y con lo favorable, retipados a ASSOCIATED_WITH; antes 20261002-qa063q, spec 063 tramo 4 + dos revisiones Codex: KPMG con la conclusión favorable de la UCO, José Ignacio sin INVESTIGATED_BY, favorables repuestos; 85 entidades de riesgo alto — retiradas prudentes corregidas en origen, pagos sin respaldo retirados, Zaldívar sin malversación, seudónimos Jesús Luis y Pablo Jesús; antes 20261002-qa063n, spec 063 tramo 3 + dos revisiones Codex: Gualda sin atribución individual de delitos, INDJ con la comparecencia de Goñi, COPASA e INDJ con lo favorable, Gualda y Plus Ultra coherentes, 3 acusaciones de prensa caducadas retiradas; 50 entidades de riesgo alto — tanda decidida, FAMILY_OF que des-anonimizaban retiradas, desmentidos repuestos; antes 20261001-qa063k, tramo 2: 50 entidades de riesgo alto — tanda decidida, caso Leire sin «Imputado (presunto)»/«testaferro», 3 grafías corregidas, artículos no sostenidos fuera; antes 20260930-qa063h: L7/L25: prosa sin meta-texto y atribuciones con medio y fecha; causa Plus Ultra en la Plaza nº 2; antes 20260930-qa063g: auto de apertura de 21-09-2026 de Begoña Gómez ingerido; L22: resúmenes de Begoña y González Amador; revisión Codex: estados «acusado» sin auto en corpus revertidos; estado procesal solo en acusaciones de última hora; spec 063: tramo 1 + fixes Codex + lote L23/L24 del 29-sep — estados de Begoña Gómez/Cristina Álvarez/Ruz/Martínez Sola/González Amador, roles del caso Leire, seudónimo «Rubén» del ATS 9905/2025 escindido) — lo lanza el DUEÑO: `! bash neo4j/bump_qa063b.sh`
# Gate: md5 local == ETag en R2. Luego SEED_URL + SEED_VERSION en sm-neo4j (Render REST) + deploy manual
# (el buildFilter de sm-neo4j no salta solo) y vigilancia del deploy. Rollback = volver a 20261004-qa063z.
set -euo pipefail
VER=20261007-qa067b
TGZ="$(dirname "$0")/neo4j-data-$VER.tgz"
URL="https://pub-409b470f34834cd2ab20ed98ea667170.r2.dev/neo4j-data-$VER.tgz"
SVC=srv-d9fsh0rbc2fs73eefq80   # sm-neo4j
KEY="$(security find-generic-password -a entramado -s render-api-key -w)"
API=https://api.render.com/v1

local_md5=$(md5 -q "$TGZ")
etag=$(curl -sI "$URL" | tr -d '\r' | awk 'tolower($1)=="etag:"{gsub(/"/,"",$2);print $2}')
echo "md5 local=$local_md5 · ETag R2=$etag"
[ "$local_md5" = "$etag" ] || { echo "❌ md5 != ETag — NO se hace el bump"; exit 1; }

# Cada PUT debe devolver 2xx; si no, se aborta ANTES de tocar la segunda variable o el deploy
# (revisión Codex T1: URL vieja + versión nueva haría que el entrypoint borrase el store y
# restaurase el seed anterior con la etiqueta nueva).
put(){ code=$(curl -s -o /dev/null -w "%{http_code}" -X PUT "$API/services/$SVC/env-vars/$1" \
        -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d "{\"value\":\"$2\"}")
       echo "$1 → HTTP $code"; case "$code" in 2*) ;; *) echo "❌ PUT $1 falló ($code) — NO se despliega"; exit 1;; esac; }
get(){ curl -s "$API/services/$SVC/env-vars/$1" -H "Authorization: Bearer $KEY" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("value",""))' 2>/dev/null; }
put SEED_URL "$URL"; sleep 2
put SEED_VERSION "$VER"; sleep 2
[ "$(get SEED_URL)" = "$URL" ] && [ "$(get SEED_VERSION)" = "$VER" ] || { echo "❌ relectura de env vars ≠ esperado — NO se despliega"; exit 1; }
echo "✅ env vars verificadas por relectura"
dep=$(curl -s -X POST "$API/services/$SVC/deploys" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{"clearCache":"do_not_clear"}')
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
