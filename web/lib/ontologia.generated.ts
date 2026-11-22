// GENERADO por pipeline/scripts/ontologia_gen.py desde pipeline/data/ontologia/ontologia.yaml.
// No lo edites: cambia la ontología y regenera (`make ontologia`). CI falla si está desfasado.
export const ONTOLOGIA_VERSION = 1;

// Tipos de nodo que existen en el grafo (MoneyAmount nunca es nodo).
export const TIPOS_ENTIDAD = ["Person", "Organization", "Company", "Court", "LegalCase", "Crime", "Event", "Location", "Contract"] as const;
export type TipoEntidad = (typeof TIPOS_ENTIDAD)[number];

export const TIPOS_RELACION = ["WORKS_FOR", "MEMBER_OF", "ASSOCIATED_WITH", "MET_WITH", "PAID", "RECEIVED", "ACCUSED_OF", "CONVICTED_OF", "INVESTIGATED_BY", "PARTY_TO", "OWNS", "CONTROLS", "ADMINISTRA", "RECEIVED_GRANT", "MINOR_CONTRACTOR_OF", "CONTRACTOR_OF", "APPOINTED_TO", "SUBSIDIARY_OF", "OCCURRED_AT", "PARTICIPATED_IN", "INVOLVES", "FAMILY_OF", "AWARDED_CONTRACT", "CONTRACT_AWARDED_TO"] as const;
export type TipoRelacion = (typeof TIPOS_RELACION)[number];

// Orden = escalera procesal del fichero; ESTADO_RANGO = gravedad para elegir el estado titular.
export const ESTADOS = ["absuelto", "archivado", "investigado", "imputado", "procesado", "acusado", "condena_anulada", "condenado_no_firme", "condenado"] as const;
export type Estado = (typeof ESTADOS)[number];
export const ESTADO_RANGO: Record<Estado, number> = {"absuelto": 1, "archivado": 1, "investigado": 2, "imputado": 3, "procesado": 4, "acusado": 4, "condena_anulada": 4, "condenado_no_firme": 5, "condenado": 6};

// Capas de procedencia (source_kind), de más a menos autoritativa.
export const CAPAS = ["judicial", "contrato", "acusacion", "prensa", "ultima_hora", "contexto", "mercantil", "subvencion", "contratacion", "boe"] as const;
export type Capa = (typeof CAPAS)[number];
export const CAPA_RANGO: Record<Capa, number> = {"judicial": 0, "contrato": 1, "acusacion": 2, "prensa": 3, "ultima_hora": 4, "contexto": 5, "mercantil": 6, "subvencion": 7, "contratacion": 8, "boe": 9};
