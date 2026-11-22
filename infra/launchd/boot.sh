#!/bin/bash
# Prod boot wrapper for the cutover — TCC-safe.
#
# Run by launchd agent com.entramado.prod at login / Mac reboot. INSTALLED to
# ~/Library/Application Support/entramado/ (NOT run from the repo under
# ~/Documents, which macOS TCC blocks for launchd-spawned processes). It only
# starts the container system and boots the sm-prod machine — it does NOT run
# `docker compose` from the host, because the stack self-recovers inside the
# machine (docker.service is `systemctl enable`d and every service is
# `restart: unless-stopped`; verified by a machine stop/start in the cutover
# session). It then keeps the Mac awake (cloudflared/the stack need it on).
#
# PUBLIC INGRESS = Cloudflare Tunnel only (entramado.org), via the separate
# com.entramado.cloudflared agent. The Tailscale Funnel was CLOSED in the
# 2026-06-27 security hardening (it duplicated public exposure under the ts.net
# hostname without Cloudflare's WAF). Do NOT re-add `tailscale funnel` here. To
# reach the stack privately from your own tailnet, use the Mac's tailscale IP
# directly — that needs no Funnel.
#
# NOTE (verify after the first real Mac reboot): the container apiserver must be
# able to virtiofs-mount the home dir (incl. the repo's ./vault, mounted :ro by
# api/mcp). If a cold reboot leaves the stack unable to read ./vault, grant the
# container helper Full Disk Access once in System Settings → Privacy, or run
# ../entramado/serve-mac.sh manually from a Terminal (TCC-granted) once.
set -euo pipefail
CTR=/opt/homebrew/bin/container
TS=/opt/homebrew/bin/tailscale
REPO=$HOME/Documents/projects/entramado   # home-mount path INSIDE the machine
"$CTR" system status >/dev/null 2>&1 || "$CTR" system start
"$CTR" machine run -n sm-prod --root -- true     # boots the machine; stack self-recovers
# (The Grafana home-mount self-heal that lived here was removed with the
# observability overlay — spec 057: metrics moved to Grafana Cloud.)
# Tailscale Funnel intentionally NOT enabled here (closed 2026-06-27 hardening —
# public ingress is Cloudflare-only). See the header note above.
#
# Keep the Mac awake so the stack + cloudflared stay up — ON BATTERY TOO.
# `-i` prevents idle system sleep off-AC; `-m` keeps the disk from idling. The
# display may still sleep (no `-d`). NOT `-s`: that assertion only holds on AC
# power, so prod could sleep/drop the tunnel once unplugged (2026-07-10 fix).
exec caffeinate -i -m
