#!/usr/bin/env bash
# Corte y vuelta atrás (spec 077, F2). La zona tiene una sola ruta, `entramado.org/*`, asignada a un Worker.
#   sitio   → la ruta pasa a `entramado-sitio` (este sitio estático). Es el corte.
#   render  → la ruta vuelve a `entramado-maintenance` (pasa el tráfico al origen del DNS). Es la vuelta atrás.
#   ver     → solo muestra a qué Worker apunta la ruta.
# No toca el DNS, no despliega y no borra nada.
#
# Termina con código 0 solo si la ruta queda, leída de nuevo, en el destino pedido.
#
#   CLOUDFLARE_API_TOKEN=… CLOUDFLARE_ACCOUNT_ID=… scripts/corte.sh ver|sitio|render
set -euo pipefail
: "${CLOUDFLARE_API_TOKEN:?falta CLOUDFLARE_API_TOKEN}" "${CLOUDFLARE_ACCOUNT_ID:?falta CLOUDFLARE_ACCOUNT_ID}"
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
# Exactamente una zona con ese nombre, y en la cuenta esperada.
zonas=$(cf GET "/zones?name=entramado.org&account.id=$CLOUDFLARE_ACCOUNT_ID")
zona=$(echo "$zonas" | jq -er --arg c "$CLOUDFLARE_ACCOUNT_ID" \
  'select(.success and (.result | length) == 1) | .result[0] | select(.name == "entramado.org" and .account.id == $c) | .id') \
  || muere "no hay exactamente una zona entramado.org en la cuenta indicada"
rutas=$(cf GET "/zones/$zona/workers/routes")
[ "$(echo "$rutas" | jq '.result | length')" = 1 ] || muere "esperaba una sola ruta en la zona: $(echo "$rutas" | jq -c '[.result[] | {pattern, script}]')"
id=$(echo "$rutas" | jq -r --arg p "$PATRON" '.result[0] | select(.pattern == $p) | .id // empty')
[ -n "$id" ] || muere "la ruta de la zona no es $PATRON"
actual=$(echo "$rutas" | jq -r '.result[0].script // "-"')
echo "$PATRON → $actual"
[ -n "$destino" ] || exit 0
cf GET "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts/$destino/settings" | jq -e '.success == true' > /dev/null \
  || muere "el Worker $destino no existe en la cuenta: no se cambia nada"
if [ "$actual" = "$destino" ]; then
  echo "ya apunta a $destino; no se cambia, se confirma"
else
  cf PUT "/zones/$zona/workers/routes/$id" "$(jq -nc --arg p "$PATRON" --arg s "$destino" '{pattern: $p, script: $s}')" > /dev/null \
    || muere "el cambio de ruta falló; comprobar con: scripts/corte.sh ver"
fi
final=$(cf GET "/zones/$zona/workers/routes/$id") || muere "SIN CONFIRMAR: no se pudo leer la ruta tras el cambio"
echo "$final" | jq -e --arg p "$PATRON" --arg s "$destino" '.success and .result.pattern == $p and .result.script == $s' > /dev/null \
  || muere "SIN CONFIRMAR: la ruta no quedó en $destino: $(echo "$final" | jq -c '.result | {pattern, script}')"
echo "$PATRON → $destino (confirmado)"
