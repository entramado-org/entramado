#!/bin/bash
# Loopback forwarder wrapper for the prod cutover — TCC-safe, IP-drift-aware.
#
# Run by launchd agent com.entramado.prod-forwarder. This file is INSTALLED to
# ~/Library/Application Support/entramado/ (NOT run from the repo under
# ~/Documents, which macOS TCC blocks for launchd-spawned processes). It touches
# only /opt/homebrew (container + socat) and never the repo, so it needs no Full
# Disk Access.
#
# It bridges 127.0.0.1:8080 → <sm-prod-ip>:80 and RE-RESOLVES the IP on every
# (re)start. Critically, it also runs a WATCHDOG: `socat TCP-LISTEN,fork` keeps
# its listener alive even when the backend IP goes dead (only the per-connection
# children fail), so socat alone never exits on an IP drift and launchd's
# KeepAlive would never re-resolve. The watchdog polls the machine IP and, when
# it changes (or socat dies), kills socat and exits so launchd restarts this
# wrapper against the fresh IP. Verified by a `container system stop` + launchd
# cold-start: machine came back on a new vmnet IP and the site self-healed.
set -euo pipefail
CTR=/opt/homebrew/bin/container
SOCAT=/opt/homebrew/bin/socat
POLL=5   # seconds between IP-drift checks

resolve() { "$CTR" machine ls 2>/dev/null | awk '$1=="sm-prod"{print $4}'; }

IP="$(resolve)"
[ -n "${IP:-}" ] && [ "$IP" != "-" ] || { echo "sm-prod has no IP yet — exiting so launchd retries"; exit 1; }
echo "socat 127.0.0.1:8080 → $IP:80"
"$SOCAT" TCP-LISTEN:8080,fork,reuseaddr,bind=127.0.0.1 "TCP:$IP:80" &
SPID=$!

# Stop socat if this wrapper is signalled (launchd restart/stop).
trap 'kill "$SPID" 2>/dev/null || true' TERM INT

while kill -0 "$SPID" 2>/dev/null; do
  sleep "$POLL"
  NOW="$(resolve)"
  if [ -n "${NOW:-}" ] && [ "$NOW" != "-" ] && [ "$NOW" != "$IP" ]; then
    echo "sm-prod IP drifted $IP → $NOW — restarting forwarder to re-resolve"
    kill "$SPID" 2>/dev/null || true
    exit 0   # launchd KeepAlive restarts us; we re-resolve at the top
  fi
done
echo "socat exited — letting launchd restart this wrapper"
exit 1
