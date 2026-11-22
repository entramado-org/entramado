#!/bin/bash
# READ-ONLY: export the PLACSP recon target list from the LIVE graph (spec 045, Fase 1).
# Companies + Organizations (all, with nif when present) and central Persons (degree >= 10).
# Zero writes. Runs INSIDE sm-prod (dispatch by path, heredoc-free — quoting lives here):
#   container machine run -n sm-prod --root -- bash \
#     <repo>/pipeline/scripts/_placsp_target_list.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
OUTDIR="$REPO/pipeline/data/contratacion"
mkdir -p "$OUTDIR"

cyq(){ docker exec sm_neo4j cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

cyq "MATCH (n) WHERE n:Company OR n:Organization
     RETURN labels(n)[0] AS label, n.entity_id AS id, n.name AS name,
            coalesce(n.nif,'') AS nif,
            coalesce(n.degree,0) AS degree, coalesce(n.is_mercantil,false) AS mercantil
     ORDER BY degree DESC;" > "$OUTDIR/_target_companies.tsv"

cyq "MATCH (p:Person) WHERE coalesce(p.degree,0) >= 10
     RETURN p.entity_id AS id, p.name AS name, coalesce(p.degree,0) AS degree
     ORDER BY degree DESC;" > "$OUTDIR/_target_persons.tsv"

wc -l "$OUTDIR/_target_companies.tsv" "$OUTDIR/_target_persons.tsv"
