#!/usr/bin/env bash
# Helper spec 043 — run the MCP healthcheck INSIDE sm-dev with the real .env
# password (container machine run does not propagate Mac-side env vars, and the
# dev store is a copy of the live one, whose auth lives in the system db).
#   /opt/homebrew/bin/container machine run -n sm-dev --root -- bash <repo>/_hc043_dev.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
set -a; source .env; set +a
exec docker run --rm --network entramado_default \
  -v "$PWD/sm-vault:/vault:ro" \
  -e NEO4J_URI=bolt://neo4j:7687 -e NEO4J_USERNAME=neo4j \
  -e NEO4J_PASSWORD="${NEO4J_PASSWORD:-changeme}" -e VAULT_DIR=/vault \
  entramado-api python -m app.healthcheck
