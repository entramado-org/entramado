#!/usr/bin/env bash
# Serve "El Entramado" from this Mac, via Tailscale Funnel.
#
#   ./serve-mac.sh
#
# Since the 2026-06-24 cutover the prod stack runs inside the Apple `container`
# machine `sm-prod` (NOT Docker Desktop) — see prod-machine.sh and
# the private ops notes. This script is the
# single "go live" entrypoint and the target of the launchd agent
# com.entramado.prod. It:
#   1. ensures the container system + the sm-prod machine are running,
#   2. ensures the prod stack is up inside the machine,
#   3. ensures the Tailscale Funnel points at the loopback forwarder (:8080),
#   4. keeps the Mac awake (the Funnel requires it).
# The socat forwarder itself (127.0.0.1:8080 → <machine-ip>:80) is run+kept
# alive by the SEPARATE launchd agent com.entramado.prod-forwarder, which
# re-resolves the machine IP whenever the machine restarts.
#
# Stable public URLs (do NOT change between restarts):
#   https://entramado.org                      (canonical, via Cloudflare Tunnel)
#   https://<tailnet-host>.ts.net        (Funnel, kept as a fallback)
# The site is PUBLIC since 2026-06-25 (no login); abuse is bounded by a per-IP
# rate limit in Caddy. To password-protect it again, re-add the basicauth block
# in the Caddyfile (BASIC_AUTH_* in .env are still wired).
#
# Rollback to Docker Desktop (the old setup, kept stopped-but-intact):
#   tailscale funnel --bg 80
#   docker compose -f docker-compose.prod.yml up -d
set -euo pipefail
cd "$(dirname "$0")"

TS=/opt/homebrew/bin/tailscale
CTR=/opt/homebrew/bin/container
URL="https://<tailnet-host>.ts.net"
FWD_PORT=8080

echo "▶ Ensuring the container system + sm-prod machine are up…"
"$CTR" system status >/dev/null 2>&1 || "$CTR" system start
"$CTR" machine run -n sm-prod --root -- true   # boots the machine on-demand if stopped

echo "▶ Ensuring the prod stack is up inside sm-prod…"
"$CTR" machine run -n sm-prod --root -- bash "$PWD/prod-machine.sh" __in compose up -d

echo "▶ Ensuring the Tailscale Funnel points at the loopback forwarder (:$FWD_PORT)…"
"$TS" funnel --bg "localhost:$FWD_PORT"

echo
echo "✅ Live at:  https://entramado.org  (and $URL)   — public, no login"
echo "   Forwarder (socat :$FWD_PORT → machine :80) is owned by launchd agent com.entramado.prod-forwarder."
echo
echo "▶ Keeping this Mac awake while serving (battery too). Ctrl-C stops caffeinate only"
echo "  (the Funnel + machine stack keep running regardless)."
# -i -m holds off-AC too (was -s = AC-only); -w $$ ties it to this shell.
exec caffeinate -i -m -w $$
