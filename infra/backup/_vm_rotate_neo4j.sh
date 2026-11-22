#!/usr/bin/env bash
# Runs INSIDE the sm-prod Apple `container` machine (dispatched by the rotation
# command via `container machine run -- bash <this> OLD NEW`). Changes the neo4j
# password OLD -> NEW on the LIVE database. OLD/NEW must be space-free (hex) so they
# survive the machine-run argv boundary. The ALTER is written to a file in the
# VM-local import dir (mounted into the neo4j container at /var/lib/neo4j/import) and
# fed to cypher-shell -f — passing the statement as an arg gets split on spaces by
# `container machine run`, hence the file. The file (it holds the new password) is
# shredded on exit. After this, update .env's NEO4J_PASSWORD and recreate neo4j+api+mcp.
set -euo pipefail
OLD="${1:?usage: _vm_rotate_neo4j.sh <old> <new>}"
NEW="${2:?missing new password}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMPORT=/var/lib/sm-prod-neo4j/import
mkdir -p "$IMPORT"
F="$IMPORT/_rotate_$$.cypher"
printf "ALTER CURRENT USER SET PASSWORD FROM '%s' TO '%s';\n" "$OLD" "$NEW" > "$F"
trap 'rm -f "$F"' EXIT
bash "$REPO/prod-machine.sh" __in compose exec -T neo4j \
  cypher-shell -u neo4j -p "$OLD" -f "/var/lib/neo4j/import/$(basename "$F")"
echo "ALTER_OK"
