#!/usr/bin/env bash
# Corte y vuelta atrás (spec 077, F2). La zona tiene una sola ruta, `entramado.org/*`, asignada a un Worker.
#   sitio   → la ruta pasa a `entramado-sitio` (este sitio estático). Es el corte.
#   render  → la ruta vuelve a `entramado-maintenance` (pasa el tráfico al origen del DNS). Es la vuelta atrás.
#   ver     → solo muestra a qué Worker apunta la ruta.
# No toca el DNS, no despliega y no borra nada.
#
#   CLOUDFLARE_API_TOKEN=… scripts/corte.sh ver|sitio|render
set -euo pipefail
: "${CLOUDFLARE_API_TOKEN:?falta CLOUDFLARE_API_TOKEN}"
API=https://api.cloudflare.com/client/v4
PATRON='entramado.org/*'
case "${1:-}" in
  ver) destino='' ;;
  sitio) destino=entramado-sitio ;;
  render) destino=entramado-maintenance ;;
  *) echo "uso: scripts/corte.sh ver|sitio|render" >&2; exit 2 ;;
esac
muere() { echo "corte: $*" >&2; exit 1; }
cf() {
  local datos=()
  [ $# -lt 3 ] || datos=(-H 'Content-Type: application/json' --data "$3")
  curl -fsS --max-time 30 -X "$1" "$API$2" -H @<(printf 'Authorization: Bearer %s\n' "$CLOUDFLARE_API_TOKEN") \
    ${datos[@]+"${datos[@]}"}
}
zona=$(cf GET '/zones?name=entramado.org' | jq -r '.result[0].id // empty')
[ -n "$zona" ] || muere "no encuentro la zona"
rutas=$(cf GET "/zones/$zona/workers/routes")
[ "$(echo "$rutas" | jq '.result | length')" = 1 ] || muere "esperaba una sola ruta en la zona: $(echo "$rutas" | jq -c '[.result[] | {pattern, script}]')"
id=$(echo "$rutas" | jq -r --arg p "$PATRON" '.result[0] | select(.pattern == $p) | .id // empty')
[ -n "$id" ] || muere "la ruta de la zona no es $PATRON"
actual=$(echo "$rutas" | jq -r '.result[0].script // "-"')
echo "$PATRON → $actual"
[ -n "$destino" ] || exit 0
[ "$actual" != "$destino" ] || { echo "ya apunta a $destino; nada que hacer"; exit 0; }
cuenta=$(cf GET "/zones/$zona" | jq -r '.result.account.id')
cf GET "/accounts/$cuenta/workers/scripts/$destino/settings" > /dev/null || muere "el Worker $destino no existe: no se cambia nada"
cf PUT "/zones/$zona/workers/routes/$id" "$(jq -nc --arg p "$PATRON" --arg s "$destino" '{pattern: $p, script: $s}')" > /dev/null
echo "$PATRON → $(cf GET "/zones/$zona/workers/routes/$id" | jq -r '.result.script')"
