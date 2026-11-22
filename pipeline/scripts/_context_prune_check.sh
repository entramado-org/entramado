#!/bin/bash
# Verification queries for the context-YAML orphan-prune dress-rehearsal (2026-07-09).
# Runs INSIDE sm-prod against the EPHEMERAL sm_neo4j_edit (:7688 over neo4j/data.rehearsal,
# started with `_idella_prod.sh rehearse-up`). Dispatch by PATH (args stay ASCII):
#   container machine run -n sm-prod --root -- bash .../_context_prune_check.sh <cmd>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PW="$(grep -E '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
EDIT=sm_neo4j_edit

cyq(){ docker exec "$EDIT" cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

case "${1:-}" in
  # nodes / rels / isolated entity nodes — the three headline numbers.
  counts)
    cyq "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() WITH nodes, count(r) AS rels
         OPTIONAL MATCH (m) WHERE m.entity_id IS NOT NULL AND NOT (m)--()
         RETURN nodes, rels, count(m) AS isolated;" ;;

  # list isolated entity nodes (expect none).
  isolated)
    cyq "MATCH (n) WHERE n.entity_id IS NOT NULL AND NOT (n)--()
         RETURN n.entity_id AS id ORDER BY id;" ;;

  # the 29 swept orphans must NOT exist (expect 0 rows).
  orphans)
    cyq "MATCH (n) WHERE n.entity_id IN [
      'Organization:abogacia-de-la-comunidad-autonoma-de-las-illes-balears',
      'Organization:centro-nacional-de-medios-de-proteccion',
      'Organization:comision-de-investigacion','Organization:consell-consultiu',
      'Organization:fiscalia-especial-contra-la-corrupcion-y-la-criminalidad-org',
      'Organization:gobierno-balear','Organization:gobierno-de-las-illes-balears',
      'Organization:grupo-parlamentario-popular',
      'Organization:intervencion-general-de-la-administracion-del-estado',
      'Organization:senado','Organization:upn','Person:caballero-martinez',
      'Person:campos-asensi','Person:de-foronda-vaquero','Person:estarellas-torrens',
      'Person:far','Person:gomez-enriquez','Person:gordillo-moreno','Person:mari-boso',
      'Person:martin-molina','Person:mascaro','Person:michaux-miranda',
      'Person:miguel-tellado','Person:mogo-zaro','Person:raquel-sanchez',
      'Person:sanchez-fuentefria','Person:sanchez-roca','Person:santamaria-ruiz',
      'Person:suarez-lamata']
    RETURN n.entity_id;" ;;

  # the 2 rescued entries must now carry their curated role (expect role text, degree>0).
  rescued)
    cyq "MATCH (n) WHERE n.entity_id IN
           ['Court:juzgado-numero-15-de-madrid','Organization:agencia-estatal-de-seguridad-aerea']
         RETURN n.entity_id, n.degree, left(coalesce(n.role,'<NULL>'),60) AS role;" ;;

  # the 5 dead entries must NOT have been created (expect 0 rows).
  dead)
    cyq "MATCH (n) WHERE n.entity_id IN [
      'Company:martin-molina-abogados-y-economistas','Organization:comision-europea',
      'Organization:grupo-parlamentario-socialista','Company:duro-felguera','Organization:igae']
    RETURN n.entity_id;" ;;

  # guard end-to-end test: plant a dummy isolated context node / remove it again.
  plant-dummy)
    cyq "CREATE (:Person {entity_id:'Person:dummy-guard-test', name:'Dummy Guard Test',
         source_kind:'contexto', is_context:true, mentions:0, degree:0});" ;;
  drop-dummy)
    cyq "MATCH (n {entity_id:'Person:dummy-guard-test'}) DELETE n;" ;;

  *) echo "usage: $0 counts|isolated|orphans|rescued|dead|plant-dummy|drop-dummy" >&2; exit 1 ;;
esac
