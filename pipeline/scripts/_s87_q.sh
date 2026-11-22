#!/bin/bash
# Ejecuta pipeline/data/audit/_s87_query.cypher contra el efímero sm_neo4j_editqa3.
# Despacho por PATH (quoting inline se rompe): container machine run -n sm-dev --root -- bash <este script>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
Q="$REPO/pipeline/data/audit/_s87_query.cypher"
docker cp "$Q" sm_neo4j_editqa3:/tmp/q.cypher >/dev/null
docker exec sm_neo4j_editqa3 cypher-shell -u neo4j -p neo4j --format plain -f /tmp/q.cypher
