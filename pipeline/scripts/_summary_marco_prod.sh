#!/bin/bash
# Apply the curated summary for Person:jose-antonio-marco-sanjuan to a neo4j
# container (props-only SET generated from pipeline/data/context/summaries.yaml
# by the session of 2026-07-09 — no reseed needed). Runs INSIDE sm-prod;
# dispatch by PATH so the unicode text stays in the repo file, never in argv:
#   container machine run -n sm-prod --root -- bash .../pipeline/scripts/_summary_marco_prod.sh {live|edit|verify-live|verify-edit}
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
Q="$REPO/pipeline/data/audit/patches/summary_marco_sanjuan.cypher"
VQ="MATCH (n {entity_id:'Person:jose-antonio-marco-sanjuan'}) RETURN n.summary_updated AS updated, size(n.summary) AS summary_len, size(n.summary_sources) AS sources;"

case "${1:-}" in
  live)        docker exec -i sm_neo4j      cypher-shell -u neo4j -p "$PW" --format plain < "$Q" ;;
  edit)        docker exec -i sm_neo4j_edit cypher-shell -u neo4j -p "$PW" --format plain < "$Q" ;;
  verify-live) docker exec sm_neo4j      cypher-shell -u neo4j -p "$PW" --format plain "$VQ" ;;
  verify-edit) docker exec sm_neo4j_edit cypher-shell -u neo4j -p "$PW" --format plain "$VQ" ;;
  *) echo "usage: $0 {live|edit|verify-live|verify-edit}" >&2; exit 2 ;;
esac
