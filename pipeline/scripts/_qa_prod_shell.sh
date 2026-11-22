#!/usr/bin/env bash
# Run a .cypher file against the prod Neo4j from INSIDE the sm-prod VM.
# Invoked via: container machine run -n sm-prod --root -- bash <this abs path> <cypher file abs path>
# (the repo is home-mounted at the same absolute path inside the VM; quoting does
# not survive `container machine run`, hence this wrapper).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FILE="${1:?usage: _qa_prod_shell.sh <file.cypher>}"
PASS="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
docker exec -i sm_neo4j cypher-shell -u neo4j -p "$PASS" --format plain < "$FILE"
