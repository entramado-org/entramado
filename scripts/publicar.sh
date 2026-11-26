#!/usr/bin/env bash
# Publica el sitio estático (spec 077): valida → escanea → construye → prueba → despliega.
# Nunca despliega un `web/out` que no acabe de construirse a partir de los datos actuales.
#
#   CLOUDFLARE_API_TOKEN=… CLOUDFLARE_ACCOUNT_ID=… scripts/publicar.sh [--sin-desplegar]
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
npx --yes wrangler@4.129.0 deploy
