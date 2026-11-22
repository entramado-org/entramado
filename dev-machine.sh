#!/usr/bin/env bash
# Drive the entramado LOCAL-DEV stack inside an Apple `container` machine
# (apple/container 1.0.0+, macOS 26) instead of Docker Desktop. See the
# `migrate-to-container-machine` skill (private ops notes).
#
# The machine (`sm-dev`) is a persistent Linux VM that runs a normal Docker engine
# + docker-compose; the repo is visible inside it via the home-mount, so the dev
# compose files work unchanged. The dev Neo4j uses a VM-local store (NOT the shared
# ./neo4j/data, which the LIVE prod Neo4j holds open) seeded from a copy of the live
# graph. PROD (docker-compose.prod.yml + Tailscale Funnel) is never touched.
#
# Usage:
#   ./dev-machine.sh bootstrap   # one-time: build base image, create machine, install docker, seed neo4j
#   ./dev-machine.sh up          # start the stack (build if needed)
#   ./dev-machine.sh down        # stop the stack
#   ./dev-machine.sh logs [svc]  # follow logs
#   ./dev-machine.sh healthcheck # MCP 19-tool smoke test against the machine graph
#   ./dev-machine.sh status      # machine state + reachable URLs (resolves current IP)
#   ./dev-machine.sh seed        # re-copy live ./neo4j/data into the VM-local store
#   ./dev-machine.sh ip          # print the machine's current IP
set -euo pipefail

MACHINE=sm-dev
IMAGE=local/ubuntu-machine:latest
STORE=/var/lib/sm-neo4j
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # same abs path inside the machine (home-mount)
COMPOSE=(docker compose -f "$REPO/docker-compose.yml" -f "$REPO/docker-compose.machine.yml")
CTR="${CONTAINER_BIN:-/opt/homebrew/bin/container}"

# ---- helpers that run INSIDE the machine (dispatched via `container machine run`) ----
if [ "${1:-}" = "__in" ]; then
  shift
  case "${1:-}" in
    install-docker)
      if ! command -v docker >/dev/null 2>&1; then
        curl -fsSL https://get.docker.com -o /root/get-docker.sh && sh /root/get-docker.sh
      fi
      systemctl enable --now docker.service
      docker --version && docker compose version
      ;;
    seed)
      mkdir -p "$STORE"/{data,logs,plugins,import}
      if [ -d "$STORE/data/databases" ]; then
        echo "store already seeded ($(du -sh "$STORE/data" | cut -f1)); skipping"
      else
        echo "copying live store -> $STORE/data (fuzzy snapshot; Neo4j recovers on boot)"
        cp -a "$REPO/neo4j/data/." "$STORE/data/"
      fi
      chown -R 7474:7474 "$STORE"
      ;;
    compose) shift; cd "$REPO"; exec "${COMPOSE[@]}" "$@" ;;
    healthcheck)
      cd "$REPO"
      exec docker run --rm --network entramado_default \
        -v "$REPO/sm-vault:/vault:ro" \
        -e NEO4J_URI=bolt://neo4j:7687 -e NEO4J_USERNAME=neo4j \
        -e NEO4J_PASSWORD="${NEO4J_PASSWORD:-changeme}" -e VAULT_DIR=/vault \
        entramado-api python -m app.healthcheck ;;
    *) echo "unknown __in subcommand: ${1:-}" >&2; exit 2 ;;
  esac
  exit 0
fi

# ---- Mac-side driver ----
inmachine() { "$CTR" machine run -n "$MACHINE" --root -- bash "$REPO/dev-machine.sh" __in "$@"; }
machine_ip() { "$CTR" machine ls 2>/dev/null | awk -v m="$MACHINE" '$1==m{print $4}'; }

case "${1:-status}" in
  bootstrap)
    "$CTR" system status >/dev/null 2>&1 || "$CTR" system start
    if ! "$CTR" image ls 2>/dev/null | grep -q "ubuntu-machine"; then
      echo "▶ building $IMAGE"; ( cd "$REPO/infra/container-machine" && "$CTR" build -t "$IMAGE" . )
    fi
    if ! "$CTR" machine ls 2>/dev/null | awk '{print $1}' | grep -qx "$MACHINE"; then
      echo "▶ creating machine $MACHINE"; "$CTR" machine create "$IMAGE" --name "$MACHINE" --cpus 4 --memory 8G --set-default
    fi
    echo "▶ installing docker in $MACHINE"; inmachine install-docker
    echo "▶ seeding neo4j store";          inmachine seed
    echo "✅ bootstrap done — run: ./dev-machine.sh up"
    ;;
  up)          inmachine compose up -d --build; "$0" status ;;
  down)        inmachine compose down ;;
  logs)        shift; inmachine compose logs -f "$@" ;;
  healthcheck) inmachine healthcheck ;;
  seed)        inmachine seed ;;
  ip)          machine_ip ;;
  status)
    "$CTR" machine ls 2>&1 | sed -n '1p;/'"$MACHINE"'/p'
    IP="$(machine_ip)"
    if [ -n "$IP" ] && [ "$IP" != "-" ]; then
      echo
      echo "Reachable from this Mac (machine IP $IP — NOTE: it can change across machine restarts):"
      echo "  web     http://$IP:3000     (browser API calls need NEXT_PUBLIC_API_BASE=http://$IP:8000 — rebuild web, or front with Caddy)"
      echo "  api     http://$IP:8000/health"
      echo "  mcp     http://$IP:8008/mcp  (406 to a plain GET is expected)"
      echo "  neo4j   http://$IP:7474"
    fi
    ;;
  *) echo "usage: $0 {bootstrap|up|down|logs|healthcheck|status|seed|ip}" >&2; exit 2 ;;
esac
