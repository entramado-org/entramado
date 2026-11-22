#!/bin/bash
# READ-ONLY: export the BOE recon target list from the LIVE graph (spec 048, Fase 1).
# Persons only (BOE seccion II.A nombramientos/ceses son de personas físicas), with
# degree + role/aliases when present (señales del gate anti-homónimo — el BOE no trae NIF).
# Zero writes. Runs INSIDE sm-prod (dispatch by path, heredoc-free — quoting lives here):
#   container machine run -n sm-prod --root -- bash \
#     <repo>/pipeline/scripts/_boe_target_list.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
OUTDIR="$REPO/pipeline/data/boe"
mkdir -p "$OUTDIR"

cyq(){ docker exec sm_neo4j cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

# aliases van unidos con ' ~ ' (una lista literal rompe el CSV del formato plain)
cyq "MATCH (p:Person)
     RETURN p.entity_id AS id, p.name AS name,
            coalesce(p.degree,0) AS degree,
            coalesce(p.role,'') AS role,
            reduce(s='', a IN coalesce(p.aliases,[]) | s + a + ' ~ ') AS aliases
     ORDER BY degree DESC;" > "$OUTDIR/_target_persons.tsv"

wc -l "$OUTDIR/_target_persons.tsv"
