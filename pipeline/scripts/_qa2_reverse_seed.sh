#!/usr/bin/env bash
# Reverse-seed tras aplicar los parches QA pase 2 (20260717): snapshot del store
# vivo de prod → seed del Mac ($REPO/neo4j/data), para que un deploy futuro no
# resucite las aristas parcheadas. Mismo patrón que el reverse-seed de los splits de julio de 2026,
# con sufijo -qa2 para no chocar con los backups -splits pendientes de purga.
# Corre DENTRO de sm-prod:
#   container machine run -n sm-prod --root -- bash <abs path>/_qa2_reverse_seed.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE=/var/lib/sm-prod-neo4j
LIVE=sm_neo4j
SBAK="$REPO/neo4j/data.bak-qa2"
SNAP="$STORE/_snap-qa2"
if [ -e "$SBAK" ]; then echo "❌ $SBAK already exists — remove it first" >&2; exit 1; fi
if [ -e "$SNAP" ]; then echo "❌ $SNAP already exists — remove it first" >&2; exit 1; fi
echo "stopping live neo4j for consistent snapshot…"; docker stop "$LIVE"
echo "snapshot: cp -a $STORE/data -> $SNAP (ext4->ext4, seconds)"; cp -a "$STORE/data" "$SNAP"
echo "restarting live neo4j (downtime ends)…"; docker start "$LIVE"
echo "backup seed: mv \$REPO/neo4j/data -> $SBAK"; mv "$REPO/neo4j/data" "$SBAK"
echo "reverse-seed: cp -a $SNAP -> \$REPO/neo4j/data (snapshot -> seed, neo4j UP)…"
mkdir -p "$REPO/neo4j/data"; cp -a "$SNAP/." "$REPO/neo4j/data/"
echo "cleanup snapshot: rm -rf $SNAP"; rm -rf "$SNAP"
echo "✅ reverse-seed done (seed==live tras parches qa2). Old seed kept at $SBAK"
