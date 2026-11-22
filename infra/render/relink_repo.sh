#!/bin/bash
# Point the Render services at the public GitHub repo (e.g. after it is recreated) and,
# optionally, deploy one of them and wait for the result.
#
# Usage: infra/render/relink_repo.sh <repo-url> [--services caddy,web,api,mcp,neo4j]
#                                    [--deploy <svc>] [--commit <sha>] [--check-only]
#
#   <repo-url>      e.g. https://github.com/entramado-org/entramado (the branch is always main)
#   --services      comma-separated services to relink and show (default: all five)
#   --deploy <svc>  then deploy <svc> (one of --services) and poll it every 10 s until it is
#                   live or has failed, for 10 min at most
#   --commit <sha>  deploy that commit instead of the head of main (needs --deploy)
#   --check-only    no PATCH and no deploy: only show name, repo, branch and autoDeploy of each
#                   service (exits non-zero if any is not on <repo-url>@main)
#   After a relink (not with --check-only) the build filters declared in render.yaml are re-applied,
#   because Render resets them when the repo of a service changes.
#
# Every service is re-read after the PATCH; if any is not on <repo-url>@main the script exits
# non-zero without deploying. Relinking alone deploys nothing. The Render API key is read from
# the macOS Keychain and never printed.
set -euo pipefail

API=https://api.render.com/v1
ALL=caddy,web,api,mcp,neo4j

die() { echo "relink_repo: $*" >&2; exit 1; }
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

# "id status commit" of the newest deploy of service id $1 ("-" for missing fields).
latest_deploy() {
  render GET "/services/$1/deploys?limit=1" \
    | jq -r '.[0].deploy // {} | [.id, .status, .commit.id]
             | map(if . == null or . == "" then "-" else tostring end) | join(" ")'
}

# Comparable form of a repo URL: lower case, without trailing slash or .git.
norm() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -e 's#/*$##' -e 's#\.git$##'; }

REPO='' SERVICES=$ALL DEPLOY='' COMMIT='' CHECK_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --services)   need "$@"; SERVICES=$2; shift 2 ;;
    --deploy)     need "$@"; DEPLOY=$2; shift 2 ;;
    --commit)     need "$@"; COMMIT=$2; shift 2 ;;
    --check-only) CHECK_ONLY=1; shift ;;
    -h|--help)    usage ;;
    -*)           die "unknown option $1" ;;
    *)            [ -z "$REPO" ] || die "unexpected argument $1"; REPO=$1; shift ;;
  esac
done

[ -n "$REPO" ] || usage
case "$REPO" in https://*/*/*) ;; *) die "repo URL must look like https://github.com/<org>/<repo>" ;; esac
REPO=$(printf '%s' "$REPO" | sed -e 's#/*$##' -e 's#\.git$##')
IFS=, read -r -a SVCS <<< "$SERVICES"
[ ${#SVCS[@]} -gt 0 ] || die "--services is empty"
for s in "${SVCS[@]}"; do svc_id "$s" > /dev/null; done
if [ -n "$DEPLOY" ]; then
  [ "$CHECK_ONLY" = 0 ] || die "--check-only does not deploy; drop one of --check-only/--deploy"
  case ",$SERVICES," in *",$DEPLOY,"*) ;; *) die "--deploy $DEPLOY is not in --services $SERVICES" ;; esac
fi
if [ -n "$COMMIT" ]; then
  [ -n "$DEPLOY" ] || die "--commit needs --deploy"
  [[ $COMMIT =~ ^[0-9a-f]{7,40}$ ]] || die "--commit must be a git sha"
fi
command -v jq > /dev/null || die "jq not found"
KEY=$(security find-generic-password -a entramado -s render-api-key -w) \
  || die "no render-api-key for account entramado in the Keychain"

if [ "$CHECK_ONLY" = 1 ]; then echo "check only (no PATCH) against $REPO@main"; else echo "relinking to $REPO@main"; fi
want=$(norm "$REPO")
bad=0
for s in "${SVCS[@]}"; do
  id=$(svc_id "$s")
  if [ "$CHECK_ONLY" = 0 ]; then
    render PATCH "/services/$id" "$(jq -nc --arg repo "$REPO" '{repo: $repo, branch: "main"}')" > /dev/null \
      || die "PATCH $s ($id) failed"
  fi
  info=$(render GET "/services/$id" \
           | jq -r '[.name, .repo, .branch, .autoDeploy, .autoDeployTrigger]
                    | map(if . == null or . == "" then "-" else tostring end) | join(" ")') \
    || die "GET $s ($id) failed"
  read -r name repo branch auto trigger <<< "$info"
  if [ "$(norm "$repo")" = "$want" ] && [ "$branch" = main ]; then mark=ok; else mark=MISMATCH; bad=$((bad + 1)); fi
  printf '%-6s %-10s %-46s %-6s autoDeploy=%s trigger=%s  %s\n' "$s" "$name" "$repo" "$branch" "$auto" "$trigger" "$mark"
done
[ "$bad" = 0 ] || die "$bad service(s) not on $REPO@main${DEPLOY:+, not deploying $DEPLOY}"
# Render drops the buildFilter of a service when its repo changes (2026-10-06: the first merge after a
# relink redeployed all five services), so put the filters declared in render.yaml back.
if [ "$CHECK_ONLY" = 0 ]; then
  bash "$(dirname "$0")/set_build_filters.sh" --services "$SERVICES" \
    || die "build filters not restored; run infra/render/set_build_filters.sh"
fi

[ -n "$DEPLOY" ] || exit 0

# Poll the newest deploy, but skip the one that was newest before the POST: right after it the
# list can still show the previous (live) deploy, which must not count as this one.
id=$(svc_id "$DEPLOY")
before=$(latest_deploy "$id") || die "could not list the deploys of $DEPLOY"
prev=${before%% *}
body=$(jq -nc --arg c "$COMMIT" '{clearCache: "do_not_clear"} + (if $c == "" then {} else {commitId: $c} end)')
resp=$(render POST "/services/$id/deploys" "$body") || die "POST deploy of $DEPLOY failed"
dep=$(jq -r '.id // empty' <<< "$resp" 2> /dev/null || true)
echo "deploy of $DEPLOY: ${dep:-queued behind a deploy in progress}"
st='not listed'
for i in $(seq 60); do
  sleep 10
  line=$(latest_deploy "$id") || line='- ? -'
  read -r did status sha <<< "$line"
  if [ "$did" = "$prev" ]; then echo "  $((i * 10))s: new deploy not listed yet"; continue; fi
  st=$status
  echo "  $((i * 10))s: $st  commit $sha  ($did)"
  case "$st" in
    live) echo "$DEPLOY is live on commit $sha"; exit 0 ;;
    build_failed|update_failed|pre_deploy_failed|canceled|deactivated) die "deploy $did of $DEPLOY ended $st" ;;
  esac
done
die "deploy of $DEPLOY not live after 10 min (last status: $st), check the Render dashboard"
