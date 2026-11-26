#!/usr/bin/env bash
# Publica el sitio estático (spec 077): valida → escanea → construye → prueba → despliega.
# Nunca despliega un `web/out` que no acabe de construirse a partir de los datos actuales.
#
#   CLOUDFLARE_API_TOKEN=… CLOUDFLARE_ACCOUNT_ID=… scripts/publicar.sh [--sin-desplegar]
#
# PYTHON apunta a un Python con PyYAML (por defecto, python3).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHON="${PYTHON:-python3}"

"$PYTHON" scripts/validar_datos.py datos/feed.yaml datos/documentos.yaml
# Ningún identificador personal en el árbol, titulares incluidos (solo biblioteca estándar).
"$PYTHON" pipeline/scripts/pii_tree_scan.py
(cd scripts && "$PYTHON" -W error -m unittest discover tests)
(
  cd web
  rm -rf .next out
  npm run build
  npx tsc --noEmit
  npx playwright test
)
if [ "${1:-}" = "--sin-desplegar" ]; then
  echo "construido y probado; no se despliega"
  exit 0
fi
: "${CLOUDFLARE_API_TOKEN:?falta CLOUDFLARE_API_TOKEN}" "${CLOUDFLARE_ACCOUNT_ID:?falta CLOUDFLARE_ACCOUNT_ID}"
npx --yes wrangler@4.129.0 deploy
