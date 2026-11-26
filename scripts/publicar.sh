#!/usr/bin/env bash
# Publica el sitio estático (spec 077): valida → escanea → construye → prueba → despliega.
# Nunca despliega un `web/out` que no acabe de construirse a partir de los datos actuales.
#
#   CLOUDFLARE_API_TOKEN=… CLOUDFLARE_ACCOUNT_ID=… scripts/publicar.sh [--sin-desplegar]
# Antes: `cd web && npm ci`.
#
# PYTHON apunta a un Python con PyYAML (por defecto, python3).
# SOLO_PUNTA_DE=<owner/repo> (en el CI): justo antes de desplegar se relee la punta de main de ese repositorio y
# solo se despliega si es GITHUB_SHA; si no se puede leer, falla. Así una ejecución superada no publica.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHON="${PYTHON:-python3}"
# Lo que se construye y se prueba es lo que se valida y se escanea: los datos de este checkout y el `web/out`
# recién construido, herede lo que herede el entorno.
export DATOS_DIR="$PWD/datos"
unset E2E_BASE_URL

# Los datos son ficheros regulares de este checkout: ningún enlace puede dar a cada lector un contenido distinto.
for dato in datos datos/feed.yaml datos/documentos.yaml datos/medios.yaml; do
  [ ! -L "$dato" ] || { echo "publicar: $dato es un enlace simbólico" >&2; exit 1; }
done
[ -d datos ] && [ -f datos/feed.yaml ] && [ -f datos/documentos.yaml ] && [ -f datos/medios.yaml ] \
  || { echo "publicar: faltan los ficheros de datos" >&2; exit 1; }
"$PYTHON" scripts/validar_datos.py datos/feed.yaml datos/documentos.yaml
# Ningún identificador personal en el árbol, titulares incluidos (solo biblioteca estándar).
"$PYTHON" pipeline/scripts/pii_tree_scan.py
# …ni en el texto que la web muestra una vez cargado el YAML (un titular partido en dos líneas se une al mostrarlo).
texto=$(mktemp -d)
trap 'rm -rf "$texto"' EXIT
"$PYTHON" scripts/texto_publicado.py datos/feed.yaml datos/documentos.yaml > "$texto/texto_publicado.txt"
"$PYTHON" pipeline/scripts/pii_tree_scan.py "$texto/texto_publicado.txt"
(cd scripts && "$PYTHON" -W error -m unittest discover tests)
(
  # Se construye y se prueba sin credenciales y con la URL del sitio fija: nada del entorno llega al HTML.
  unset CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID GH_TOKEN GITHUB_TOKEN
  export SITE_URL=https://entramado.org
  cd web
  rm -rf .next out
  npm run build
  npx tsc --noEmit
  # Lo que se sube es una lista cerrada de ficheros: nada suelto en web/public ni en web/out.
  "$PYTHON" ../scripts/salida_cerrada.py public out
  npx playwright test
)
if [ "${1:-}" = "--sin-desplegar" ]; then
  echo "construido y probado; no se despliega"
  exit 0
fi
: "${CLOUDFLARE_API_TOKEN:?falta CLOUDFLARE_API_TOKEN}" "${CLOUDFLARE_ACCOUNT_ID:?falta CLOUDFLARE_ACCOUNT_ID}"
if [ -n "${SOLO_PUNTA_DE:-}" ]; then
  : "${GITHUB_SHA:?SOLO_PUNTA_DE necesita GITHUB_SHA}"
  punta=$(gh api "repos/$SOLO_PUNTA_DE/commits/main" --jq .sha)
  [ -n "$punta" ] || { echo "publicar: no se pudo leer la punta de main" >&2; exit 1; }
  if [ "$punta" != "$GITHUB_SHA" ]; then
    echo "main va por $punta: no se despliega $GITHUB_SHA"
    exit 0
  fi
fi
# Se despliega desde una carpeta vacía, con una copia del `web/out` comprobado arriba y de la configuración: nada
# de la raíz del checkout (un `.env`, un `.npmrc`, otro `wrangler.*`) llega a Wrangler. El ejecutable es el del
# lockfile de `web/` (versión e integridad fijadas), instalado antes y sin credenciales.
raiz=$PWD
limpio=$(mktemp -d)
trap 'rm -rf "$texto" "$limpio"' EXIT
mkdir "$limpio/web"
cp -R web/out "$limpio/web/out"
cp wrangler.jsonc "$limpio/wrangler.jsonc"
cd "$limpio"
"$raiz/web/node_modules/.bin/wrangler" deploy --config ./wrangler.jsonc --assets ./web/out
