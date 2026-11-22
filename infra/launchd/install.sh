#!/usr/bin/env bash
# Install the prod-cutover launchd agents for El Entramado.
#
# Copies the TCC-safe wrappers (boot.sh, forwarder.sh) out of the repo (which
# lives under ~/Documents, blocked for launchd-spawned processes) into
# ~/Library/Application Support/entramado/, installs the two LaunchAgents, and
# (re)bootstraps them. Idempotent — re-run after editing any of these files.
#
#   ./infra/launchd/install.sh           # install + load
#   ./infra/launchd/install.sh uninstall # bootout + remove
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUP="$HOME/Library/Application Support/entramado"
LA="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"
AGENTS=(com.entramado.prod-forwarder com.entramado.prod com.entramado.cloudflared)

if [ "${1:-install}" = "uninstall" ]; then
  for L in "${AGENTS[@]}"; do
    launchctl bootout "gui/$UID_N/$L" 2>/dev/null || true
    rm -f "$LA/$L.plist"
  done
  echo "✅ uninstalled. (left $SUP in place; rm -rf it to fully clean)"
  exit 0
fi

mkdir -p "$SUP" "$LA"
install -m 0755 "$HERE/boot.sh" "$HERE/forwarder.sh" "$HERE/cloudflared.sh" "$SUP/"
# Cloudflare Tunnel config (spec 020): versioned source is infra/cloudflared/config.yml;
# the launchd-spawned cloudflared reads this Support-dir copy (TCC-safe). Credentials
# stay in ~/.cloudflared, never copied here.
install -m 0644 "$HERE/../cloudflared/config.yml" "$SUP/cloudflared-config.yml"
cp "$HERE"/com.entramado.prod.plist "$HERE"/com.entramado.prod-forwarder.plist \
   "$HERE"/com.entramado.cloudflared.plist "$LA/"

for L in "${AGENTS[@]}"; do
  launchctl bootout "gui/$UID_N/$L" 2>/dev/null || true
  # bootout is ASYNC: a bootstrap fired immediately races it and can silently leave
  # the agent unloaded (observed leaving prod-forwarder + cloudflared down → :8080
  # dead → 502 on both the Funnel and the Cloudflare tunnel). Wait for the label to
  # actually disappear (poll ≤5s), then bootstrap with one retry.
  for _ in $(seq 1 25); do
    launchctl print "gui/$UID_N/$L" >/dev/null 2>&1 || break
    sleep 0.2
  done
  launchctl bootstrap "gui/$UID_N" "$LA/$L.plist" 2>/dev/null \
    || { sleep 1; launchctl bootstrap "gui/$UID_N" "$LA/$L.plist"; }
  echo "▶ bootstrapped $L"
done
echo "✅ installed. Logs: /tmp/sm_prod.launchd.log /tmp/sm_prod_forwarder.launchd.log /tmp/sm_cloudflared.launchd.log"
