// 067 (2026-10-07) — anonimiza el nodo de la pareja de Ramón Alzórriz (particular no investigada; decisión del dueño,
// familiares_revisados.yaml). Localiza el nodo por su arista del Diario de Sesiones del Senado, sin nombrarla.
// Idempotente: si el id nuevo ya existe no hace nada. Se aplica dentro del Path A:
//   PRE_MIGRATION=pipeline/scripts/_067_anonymize_familiar_alzorriz.cypher bash pipeline/scripts/_qa063b_gate.sh <out> <base>
MATCH (n:Person)-[r:ASSOCIATED_WITH]-(a:Person {entity_id: 'Person:ramon-alzorriz-goni'})
WHERE n.entity_id <> a.entity_id AND NOT n.entity_id STARTS WITH 'Person:familiar-de-'
  AND r.source_quote CONTAINS 'para decir que su pareja'
  AND NOT EXISTS { MATCH (m {entity_id: 'Person:familiar-de-ramon-alzorriz'}) }
SET n.entity_id = 'Person:familiar-de-ramon-alzorriz',
    n.name = 'Familiar de Ramón Alzórriz (no investigada)',
    n.aliases = []
REMOVE n.summary, n.summary_sources, n.summary_updated, n.role, n.role_source, n.role_sources
RETURN count(n) AS anonimizados;
