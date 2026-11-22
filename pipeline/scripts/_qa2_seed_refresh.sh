#!/usr/bin/env bash
# Refresca el seed del Mac con el store vivo SIN tocar el backup data.bak-qa2
# (que conserva el seed pre-QA-pase2). Para re-sync tras una segunda migración
# en la misma sesión. Corre DENTRO de sm-prod.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
LIVE=sm_neo4j
SNAP="$STORE/_snap-qa2r"
if [ -e "$SNAP" ]; then echo "❌ $SNAP already exists — remove it first" >&2; exit 1; fi
echo "stopping live neo4j for consistent snapshot…"; docker stop "$LIVE"
echo "snapshot: cp -a $STORE/data -> $SNAP"; cp -a "$STORE/data" "$SNAP"
echo "restarting live neo4j (downtime ends)…"; docker start "$LIVE"
echo "refresh seed: rm -rf + cp -a (neo4j UP)…"
rm -rf "$REPO/neo4j/data"; mkdir -p "$REPO/neo4j/data"; cp -a "$SNAP/." "$REPO/neo4j/data/"
rm -rf "$SNAP"
echo "✅ seed refresh done (seed==live tras familia C; backup pre-pase2 intacto en data.bak-qa2)"
