#!/bin/bash
# Cloudflare Tunnel wrapper for entramado.org — TCC-safe. Spec 020.
#
# Run by launchd agent com.entramado.cloudflared. This file is INSTALLED to
# ~/Library/Application Support/entramado/ (NOT run from the repo under
# ~/Documents, which macOS TCC blocks for launchd-spawned processes). It touches
# only /opt/homebrew (cloudflared), $SUP (its config) and ~/.cloudflared (creds) —
# none under Documents — so it needs no Full Disk Access.
#
# cloudflared opens an OUTBOUND tunnel to Cloudflare's edge and serves
# entramado.org → http://localhost:8080 (the same socat forwarder the Funnel uses
# → sm-prod:80 Caddy). It reconnects to the edge on its own and re-resolves nothing
# locally (localhost:8080 is stable; socat absorbs the VM IP drift), so unlike the
# forwarder this wrapper has no watchdog: it just execs cloudflared and lets
# launchd's KeepAlive restart it if it ever dies.
set -euo pipefail
CFD=/opt/homebrew/bin/cloudflared
SUP="$HOME/Library/Application Support/entramado"
exec "$CFD" tunnel --config "$SUP/cloudflared-config.yml" run
