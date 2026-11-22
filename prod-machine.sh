#!/usr/bin/env bash
# Drive the entramado PRODUCTION stack inside an Apple `container` machine
# (`sm-prod`) instead of Docker Desktop. Sibling of dev-machine.sh. See
# the private ops notes and the
# `migrate-to-container-machine` skill.
#
# The public hostname https://<tailnet-host>.ts.net is served by the
# Tailscale Funnel (node `entramado`, Homebrew tailscaled on THIS Mac — never
# touched). Because a container machine cannot publish on the Mac loopback and
# its vmnet IP drifts across restarts, a `socat` forwarder on the Mac bridges
#   Funnel → 127.0.0.1:8080 → <machine-ip>:80 (sm_caddy inside sm-prod)
# re-resolving the IP on every start. The compose files + Caddyfile + .env are
# byte-identical to the Docker Desktop setup; prod Neo4j uses a VM-local store
# (docker-compose.prod-machine.yml) seeded once from ./neo4j/data.
#
# Usage:
#   ./prod-machine.sh bootstrap   # one-time: build base image, create machine, install docker
#   ./prod-machine.sh seed        # copy ./neo4j/data -> VM-local store (DD prod neo4j MUST be stopped first)
#   ./prod-machine.sh up          # start the prod stack inside the machine (build if needed)
#   ./prod-machine.sh down        # stop the prod stack
#   ./prod-machine.sh logs [svc]  # follow logs
#   ./prod-machine.sh healthcheck # MCP 19-tool smoke test against the machine graph
#   ./prod-machine.sh top         # lazydocker TUI over the machine's docker (interactive)
#   ./prod-machine.sh forwarder   # run the socat bridge :8080 -> <machine-ip>:80 (foreground; launchd target)
#   ./prod-machine.sh funnel-on   # point the Tailscale Funnel at localhost:8080
#   ./prod-machine.sh status      # machine state + reachable URLs (resolves current IP)
#   ./prod-machine.sh ip          # print the machine's current IP
set -euo pipefail

MACHINE=sm-prod
IMAGE=local/ubuntu-machine:latest
STORE=/var/lib/sm-prod-neo4j
FWD_PORT=8080
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # same abs path inside the machine (home-mount)
# The observability overlay (spec 017) was removed with the Render cutover
# (spec 057: metrics live in Grafana Cloud now; this whole machine is a rollback
# guard slated for retirement).
COMPOSE=(docker compose -f "$REPO/docker-compose.prod.yml" -f "$REPO/docker-compose.prod-machine.yml")
CTR="${CONTAINER_BIN:-/opt/homebrew/bin/container}"
TS="${TAILSCALE_BIN:-/opt/homebrew/bin/tailscale}"
SOCAT="${SOCAT_BIN:-socat}"

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
        echo "store already seeded ($(du -sh "$STORE/data" | cut -f1)); refusing to overwrite."
        echo "to re-seed, first: rm -rf $STORE/data/* inside the machine, then run seed again."
      else
        echo "copying live store -> $STORE/data (DD prod neo4j must be stopped for a consistent copy)"
        cp -a "$REPO/neo4j/data/." "$STORE/data/"
      fi
      chown -R 7474:7474 "$STORE"
      ;;
    compose) shift; cd "$REPO"; exec "${COMPOSE[@]}" "$@" ;;
    healthcheck)
      cd "$REPO"
      # The store's password is the real NEO4J_PASSWORD from .env; compose injects
      # it into api/mcp, but this throwaway `docker run` doesn't read .env, so
      # without it we'd auth with the placeholder default → every neo4j-backed
      # tool reports "Neo4j no disponible" (a FALSE all-red auth failure, not a
      # service outage). Pull just that key from .env when not already in the env
      # (grep, not `source`, so the doubled `$$` in BASIC_AUTH_HASH isn't eaten).
      : "${NEO4J_PASSWORD:=$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" 2>/dev/null | cut -d= -f2-)}"
      exec docker run --rm --network entramado_default \
        -v "$REPO/sm-vault:/vault:ro" \
        -v "$REPO/pipeline/data/patrones:/data/patrones:ro" \
        -e NEO4J_URI=bolt://neo4j:7687 -e NEO4J_USERNAME=neo4j \
        -e NEO4J_PASSWORD="${NEO4J_PASSWORD:-changeme}" -e VAULT_DIR=/vault \
        entramado-api python -m app.healthcheck ;;
    *) echo "unknown __in subcommand: ${1:-}" >&2; exit 2 ;;
  esac
  exit 0
fi

# ---- Mac-side driver ----
inmachine() { "$CTR" machine run -n "$MACHINE" --root -- bash "$REPO/prod-machine.sh" __in "$@"; }
machine_ip() { "$CTR" machine ls 2>/dev/null | awk -v m="$MACHINE" '$1==m{print $4}'; }

case "${1:-status}" in
  bootstrap)
    "$CTR" system status >/dev/null 2>&1 || "$CTR" system start
    if ! "$CTR" image ls 2>/dev/null | grep -q "ubuntu-machine"; then
      echo "▶ building $IMAGE"; ( cd "$REPO/infra/container-machine" && "$CTR" build -t "$IMAGE" . )
    fi
    if ! "$CTR" machine ls 2>/dev/null | awk '{print $1}' | grep -qx "$MACHINE"; then
      echo "▶ creating machine $MACHINE (4 CPU / 6G — sized for the 24/7 Neo4j)"
      "$CTR" machine create "$IMAGE" --name "$MACHINE" --cpus 4 --memory 6G
    fi
    echo "▶ installing docker in $MACHINE"; inmachine install-docker
    echo "✅ bootstrap done. Next (cutover window): stop DD prod neo4j → ./prod-machine.sh seed → up → forwarder → funnel-on"
    ;;
  up)          inmachine compose up -d --build; "$0" status ;;
  down)        inmachine compose down ;;
  logs)        shift; inmachine compose logs -f "$@" ;;
  healthcheck) inmachine healthcheck ;;
  seed)        inmachine seed ;;
  ip)          machine_ip ;;
  top)         exec "$CTR" machine run -n "$MACHINE" --root -it -- lazydocker ;;
  forwarder)
    IP="$(machine_ip)"
    [ -n "$IP" ] && [ "$IP" != "-" ] || { echo "machine $MACHINE has no IP (is it running?)" >&2; exit 1; }
    echo "▶ socat bridge: 127.0.0.1:$FWD_PORT → $IP:80  (Ctrl-C to stop)"
    exec "$SOCAT" TCP-LISTEN:$FWD_PORT,fork,reuseaddr,bind=127.0.0.1 "TCP:$IP:80"
    ;;
  funnel-on)
    echo "▶ pointing Tailscale Funnel at localhost:$FWD_PORT"
    "$TS" funnel --bg "localhost:$FWD_PORT"
    "$TS" funnel status
    ;;
  status)
    "$CTR" machine ls 2>&1 | sed -n '1p;/'"$MACHINE"'/p'
    IP="$(machine_ip)"
    if [ -n "$IP" ] && [ "$IP" != "-" ]; then
      echo
      echo "Machine IP $IP (NOTE: can change across machine restarts — forwarder re-resolves it):"
      echo "  caddy   http://$IP:80      (Basic-Auth site; the Funnel + socat forwarder front this)"
      echo "  forwarder  127.0.0.1:$FWD_PORT → $IP:80   (run: ./prod-machine.sh forwarder)"
      echo "  public  https://<tailnet-host>.ts.net  (Funnel → localhost:$FWD_PORT)"
    fi
    ;;
  *) echo "usage: $0 {bootstrap|seed|up|down|logs|healthcheck|top|forwarder|funnel-on|status|ip}" >&2; exit 2 ;;
esac
