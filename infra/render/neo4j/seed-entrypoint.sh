#!/bin/bash
# Seed/re-seed the store from R2 BEFORE neo4j starts. Gated by SEED_VERSION:
# the version is written to a marker on the persistent disk after a successful
# restore, so restarts/redeploys with the same version boot straight from disk.
# Tarball layout: `tar czf neo4j-data.tgz -C neo4j data` on the Mac (top dir
# `data/`), stripped into /data. Fail HARD on a bad restore — better a down
# neo4j than silently serving an empty/stale graph.
set -euo pipefail

MARKER=/data/.seed-version

if [ -n "${SEED_URL:-}" ] && [ "$(cat "$MARKER" 2>/dev/null || true)" != "${SEED_VERSION:-}" ]; then
    echo "[seed] restoring store version '${SEED_VERSION:-}' from R2"
    rm -rf /data/databases /data/transactions /data/dbms
    curl -fsSL "$SEED_URL" | tar xz -C /data --strip-components=1
    echo "${SEED_VERSION:-}" > "$MARKER"
    echo "[seed] restore done"
else
    echo "[seed] store up to date (version '$(cat "$MARKER" 2>/dev/null || echo none)')"
fi

exec /startup/docker-entrypoint.sh "$@"
