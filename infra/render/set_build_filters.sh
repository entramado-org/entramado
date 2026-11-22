#!/bin/bash
# Apply to the Render services the buildFilter that render.yaml declares for each of them. The
# Blueprint is paused and never applied them, so without this every push to main redeploys all
# five services, sm-neo4j included.
#
# Usage: infra/render/set_build_filters.sh [--check-only] [--services caddy,web,api,mcp,neo4j]
#
#   --services    comma-separated services to check and update (default: all five)
#   --check-only  no PATCH: only compare the live filter of each service with render.yaml
#                 (exits 0 if all match, 2 if any differs)
#
# Services whose live filter already matches are left alone; the rest are PATCHed and re-read,
# and the script exits 1 if any re-read filter is not exactly the declared one (paths and
# ignoredPaths, order included). The PATCH deploys nothing. The Render API key is read from the
# macOS Keychain and never printed.
#
# PATCH body per https://api-docs.render.com/reference/update-service: buildFilter requires both
# paths and ignoredPaths, so ignoredPaths goes as [] when render.yaml declares none.
set -euo pipefail

API=https://api.render.com/v1
ALL=caddy,web,api,mcp,neo4j
ROOT=$(cd "$(dirname "$0")/../.." && pwd)

die() { echo "set_build_filters: $*" >&2; exit 1; }
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0" >&2; exit 2; }
need() { [ $# -ge 2 ] && [ -n "$2" ] && [ "${2#-}" = "$2" ] || die "$1 needs a value"; }

svc_id() {
  case "$1" in
    caddy) echo srv-d9fsh0rbc2fs73eefq60 ;;
    api)   echo srv-d9fsh0rbc2fs73eefq70 ;;
    mcp)   echo srv-d9fsh0rbc2fs73eefq6g ;;
    neo4j) echo srv-d9fsh0rbc2fs73eefq80 ;;
    web)   echo srv-d9fsh0rbc2fs73eefq90 ;;
    *) die "unknown service '$1' (expected one of $ALL)" ;;
  esac
}

# render METHOD PATH [JSON-BODY]. The key goes in through a header file so it never shows up
# in the process list.
render() {
  local data=()
  [ $# -lt 3 ] || data=(-H 'Content-Type: application/json' --data "$3")
  curl -fsS --max-time 30 -X "$1" "$API$2" -H 'Accept: application/json' \
       -H @<(printf 'Authorization: Bearer %s\n' "$KEY") ${data[@]+"${data[@]}"}
}

# Whether buildFilter JSONs $1 and $2 are equal; null and missing lists count as empty.
same() {
  jq -en --argjson a "$1" --argjson b "$2" \
     'def n: {paths: (.paths // []), ignoredPaths: (.ignoredPaths // [])}; ($a | n) == ($b | n)' > /dev/null
}

show() { if [ "$1" = null ]; then echo none; else echo "$1"; fi; }

SERVICES=$ALL CHECK_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --services)   need "$@"; SERVICES=$2; shift 2 ;;
    --check-only) CHECK_ONLY=1; shift ;;
    -h|--help)    usage ;;
    -*)           die "unknown option $1" ;;
    *)            die "unexpected argument $1" ;;
  esac
done

IFS=, read -r -a SVCS <<< "$SERVICES"
[ ${#SVCS[@]} -gt 0 ] || die "--services is empty"
for s in "${SVCS[@]}"; do svc_id "$s" > /dev/null; done
command -v jq > /dev/null || die "jq not found"
python3 -c 'import yaml' 2> /dev/null || die "python3 cannot import yaml (PyYAML)"

# Declared filter of each service in SVCS, same order.
YAML=$(python3 -c 'import json, sys, yaml; json.dump(yaml.safe_load(open(sys.argv[1])), sys.stdout, default=str)' \
         "$ROOT/render.yaml") || die "could not read $ROOT/render.yaml"
WANTS=()
for s in "${SVCS[@]}"; do
  w=$(jq -c --arg n "sm-$s" '.services[] | select(.name == $n) | .buildFilter | select(.paths | length > 0)
                             | {paths, ignoredPaths: (.ignoredPaths // [])}' <<< "$YAML") \
    || die "could not parse $ROOT/render.yaml"
  [ -n "$w" ] || die "render.yaml declares no buildFilter.paths for sm-$s"
  WANTS+=("$w")
done
KEY=$(security find-generic-password -a entramado -s render-api-key -w) \
  || die "no render-api-key for account entramado in the Keychain"

if [ "$CHECK_ONLY" = 1 ]; then echo "check only (no PATCH) against render.yaml"; else echo "applying the build filters of render.yaml"; fi
bad=0 i=0
for s in "${SVCS[@]}"; do
  id=$(svc_id "$s") want=${WANTS[i]}
  i=$((i + 1))
  info=$(render GET "/services/$id" | jq -r '"\(.name) \(.buildFilter | tojson)"') || die "GET $s ($id) failed"
  read -r name cur <<< "$info"
  [ "$name" = "sm-$s" ] || die "$s ($id) is named '$name' on Render, not sm-$s"
  note=''
  if same "$cur" "$want"; then
    mark=ok
  elif [ "$CHECK_ONLY" = 1 ]; then
    mark=MISMATCH note="live: $(show "$cur")  want: $want"
    bad=$((bad + 1))
  else
    render PATCH "/services/$id" "$(jq -nc --argjson f "$want" '{buildFilter: $f}')" > /dev/null \
      || die "PATCH $s ($id) failed"
    now=$(render GET "/services/$id" | jq -c '.buildFilter') || die "GET $s ($id) failed"
    if same "$now" "$want"; then
      mark='CHANGED→ok' note="was: $(show "$cur")"
    else
      mark=MISMATCH note="was: $(show "$cur")  now: $(show "$now")  want: $want"
      bad=$((bad + 1))
    fi
  fi
  printf '%-8s  %s  %s%s\n' "$name" "$(jq -r '"paths=\(.paths | length)  ignored=\(.ignoredPaths | length)"' <<< "$want")" \
         "$mark" "${note:+  $note}"
done
if [ "$bad" -gt 0 ] && [ "$CHECK_ONLY" = 1 ]; then echo "set_build_filters: $bad service(s) differ from render.yaml" >&2; exit 2; fi
[ "$bad" = 0 ] || die "$bad service(s) do not match render.yaml after the PATCH"
