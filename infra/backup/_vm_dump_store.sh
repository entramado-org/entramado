#!/usr/bin/env bash
# Runs INSIDE the sm-prod Apple `container` machine (dispatched by backup-prod.sh
# via `container machine run -- bash <this>`). Tars the VM-local Neo4j store to a
# path on the home-mount (so the archive lands on the Mac). Neo4j MUST be stopped
# by the caller first — an offline copy of a stopped store is transactionally
# consistent and restorable by extracting it straight back to /var/lib/sm-prod-neo4j.
set -euo pipefail
DEST="${1:?usage: _vm_dump_store.sh <dest-dir> <timestamp>}"
TS="${2:?missing timestamp}"
STORE=/var/lib/sm-prod-neo4j
mkdir -p "$DEST"
tar czf "$DEST/sm-neo4j-store-$TS.tgz" -C "$STORE" data
echo "TAR_OK $(du -h "$DEST/sm-neo4j-store-$TS.tgz" | cut -f1)"
