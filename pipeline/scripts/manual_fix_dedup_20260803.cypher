// Dedup 2026-08-03 — fixes manuales previos a los loaders (ver PR de overlays).
// 1) Alfonso: retirar la trayectoria BOE falsa (Alfonso Jiménez Palacios, ajeno al caso)
MATCH (a:Person {entity_id:'Person:alfonso'})-[r:APPOINTED_TO]->(o:Organization {entity_id:'Organization:ministerio-de-sanidad'})
WHERE r.source_kind = 'boe' DELETE r;
MATCH (a:Person {entity_id:'Person:alfonso'}) REMOVE a.boe_n_cargos, a.boe_rango, a.is_boe;
MATCH (a:Person {entity_id:'Person:alfonso'})
SET a.role = '«ALFONSO» de los audios de la UCO — referente sin identificar (informe-uco p.347; informe-uco-cerdan-301-400 p.53). No atribuir nombre civil.';
// 2) Fiscalía: retirar los 8 nombramientos BOE del nodo genérico; make boe los
// re-crea sobre los organismos correctos (boe.yaml re-apuntado)
MATCH (:Person)-[r:APPOINTED_TO]->(f:Organization {entity_id:'Organization:fiscalia'})
WHERE r.source_kind = 'boe' DELETE r;
