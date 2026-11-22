#!/bin/bash
# Ciclo de loaders del Path A 063 contra el efímero (bolt :7689). Orden fijo: contracts antes de relations
# (las reglas deny/fix tienen que ver las aristas de contrato); prensa congelada (press_loader aborta sobre el linaje).
set -uo pipefail
cd "$(dirname "$0")/../.."
export NEO4J_URI=${NEO4J_URI:-bolt://192.168.64.3:7689} NEO4J_USER=neo4j NEO4J_PASSWORD=neo4j
for t in contracts relations context accusations documents ultima-hora claims mercantil contratacion contratacion-ampliada subvenciones boe; do
  echo "=== make $t"; make -s "$t" || { echo "=== FALLO make $t"; exit 1; }
done
echo "=== CICLO OK"
