import type { Capa, TipoEntidad } from "./ontologia.generated";

/** Node types of the graph — derived from the controlled vocabulary (spec 069). */
export type EntityType = TipoEntidad;

export interface GraphNode {
  id: string;
  type: EntityType;
  name: string;
  role?: string | null;
  degree?: number;
  mentions?: number;
  betweenness?: number;       // intermediación (puente entre sub-tramas) — spec 012/013
  closeness?: number;         // cercanía (pasos al resto del grafo)
  community?: number | null;  // id de comunidad (Louvain); null si no calculada
  community_label?: string | null; // etiqueta tentativa de la comunidad (miembros principales)
  context?: boolean;          // true = curated public context, not a judicial doc fact
  press?: boolean;            // true = press-sourced entity (source_kind='prensa')
  ultima_hora?: boolean;      // true = última-hora-sourced entity (source_kind='ultima_hora')
  isPseudonym?: boolean;      // true = judicial-anonymisation pseudonym (real distinct person)
  pseudonymNote?: string | null; // why this name is a court pseudonym
  summary?: string | null;        // curated web-sourced summary (Síntesis tab)
  summarySources?: string[];      // URLs backing the curated summary
  summaryUpdated?: string | null; // ISO date the summary was last curated
  fichaSlug?: string | null;      // public /entidad/<slug> ficha (spec 035); null = not indexable
  chargeStatuses?: string[];  // procedural stages of this person's charges (accusations layer)
  // Estados que SOLO sostiene la prensa (status_fuente_tipo='prensa'): la ficha lo dice (063, revisión Codex).
  chargeStatusesPress?: { status: string; fecha?: string | null; fuente?: string | null }[];
  casos?: { slug: string; titulo: string }[]; // curated cases this entity belongs to (spec 038)
  mercantil?: {               // registro mercantil / BORME overlay (spec 026, ampliado 031); null if not enriched
    nif?: string | null;
    hoja?: string | null;
    capital?: number | null;
    cnae?: string | null;
    fecha?: string | null;
    provincia?: string | null;
    domicilio?: string | null;
    borme?: string | null;
    // Profundización (spec 031): situación mercantil, objeto e histórico de capital.
    objeto_social?: string | null;
    capital_historico?: string | null; // cadena pre-formateada "fecha: importe; …"
    en_concurso?: boolean | null;
    concurso_fecha?: string | null;
    concurso_juzgado?: string | null;
    disuelta?: boolean | null;
    disuelta_fecha?: string | null;
    en_liquidacion?: boolean | null;
    liquidacion_fecha?: string | null;
  } | null;
  clusterId?: string;         // runtime: case/community assignment (cluster layout)
  // runtime (react-force-graph) fields
  x?: number;
  y?: number;
}

/** Layout regimes for the graph canvas (mutually exclusive).
 *  - cluster: case regions on a ring
 *  - radial:  people (by case) in the centre, detail (by type) on an outer ring
 *  - chrono:  time axis (left→right by year)
 *  - free:    force-directed, no positional anchors */
export type LayoutMode = "cluster" | "radial" | "chrono" | "free";

/** A spatial cluster (a "case") the nodes are grouped into in cluster mode. */
export interface ClusterInfo {
  id: string;
  label: string;
  color: string;
  size: number;                 // node count
  anchor: { x: number; y: number };  // ring position, graph units
}

export interface GraphLink {
  source: string | GraphNode;
  target: string | GraphNode;
  type: string;
  count?: number;            // number of evidence chunks supporting this edge
  quote?: string | null;
  doc?: string | null;
  page?: number | null;
  amount?: number | null;
  currency?: string | null;
  date?: string | null;
  date_year?: number | null;
  context?: boolean;         // curated public-context edge (no judicial source)
  note?: string | null;     // short context/press note (shown instead of a quote)
  status?: string | null;   // procedural stage of a charge (imputado/acusado/condenado_no_firme/condenado…)
  // Procedencia del ESTADO procesal (spec 061): qué acto lo fija y de cuándo es.
  // 'prensa' ⇒ la fase solo la sostiene un medio y la ficha lo dice.
  status_fuente_tipo?: string | null;
  status_fecha?: string | null;
  status_fuente?: string | null; // doc o URL del acto/noticia que fija el estado
  source_kind?: string;     // a SourceKind: an ontology layer or 'sin_procedencia' (fail-closed)
  outlet?: string | null;   // press outlet name (source_kind='prensa')
  url?: string | null;      // press article URL (source_kind='prensa')
}

/** Aggregation of every visible parallel edge between an undirected node pair,
 *  used to collapse the canvas to a single line per pair (the rest are drawn
 *  invisible) and to list the pair's connections in the hover tooltip. */
export interface PairAgg {
  repLink: GraphLink;        // the one link drawn for the pair
  count: number;             // Σ evidence counts across the pair's visible edges
  amount: number;            // Σ amounts
  mixed: boolean;            // more than one distinct relation type
  types: {
    type: string;
    count: number;
    source_kind?: string;
    status?: string | null;
    amount?: number | null;
  }[];                       // one entry per visible edge, count desc
}

/** One de-aggregated evidence row behind a display edge (GET /graph/relation). */
export interface EdgeEvidence {
  quote?: string | null;
  doc?: string | null;       // document title (falls back to the file id)
  file?: string | null;      // doc_id / filename
  page?: number | null;
  date?: string | null;
  date_year?: number | null;
  amount?: number | null;
  currency?: string | null;
  status?: string | null;
  source_kind: string;
  outlet?: string | null;
  url?: string | null;
  note?: string | null;
  // Concesión BDNS (source_kind 'subvencion'): props propias fuera de la vista dinero.
  fecha?: string | null;
  importe?: number | null;
  instrumento?: string | null;
  convocante_detalle?: string | null;
  // Agregado PLACSP (source_kind 'contratacion'): fuera de la vista dinero y cronología.
  n_contratos?: number | null;
  total_eur?: number | null;
  rango?: string | null;
  organo_detalle?: string | null;
  via_ute?: string | null;
  ejemplo_link?: string | null;
  ejemplo_titulo?: string | null;
  /** Tipo de la relación de esta evidencia (p. ej. CONTRACTOR_OF) — discriminador
   *  explícito del sub-overlay en el panel; el panel no debe adivinar por props. */
  rel_type?: string | null;
  // Agregado ampliada (spec 055, CONTRACTOR_OF): señales de licitación, misma capa.
  ambito?: string | null;
  fuente?: string | null;
  n_emergencia?: number | null;
  n_negociado_sp?: number | null;
  presupuesto_total_eur?: number | null;
  // Etapa BOE (source_kind 'boe'): cargo público agregado, fuera de dinero y cronología.
  cargo?: string | null;
  fecha_nombramiento?: string | null;
  boe_id_nombramiento?: string | null;
  url_nombramiento?: string | null;
  fecha_cese?: string | null;
  boe_id_cese?: string | null;
  url_cese?: string | null;
}

/** /graph/relation payload: every parallel relation of one type between two
 *  entities. meta.count is the year-window count (matches the edge's ×N);
 *  meta.total is unfiltered so the UI can disclose "N de M". */
export interface EdgeDetail {
  type: string;
  source: { id: string; name: string; type: EntityType };
  target: { id: string; name: string; type: EntityType };
  evidence: EdgeEvidence[];
  meta: { count: number; total: number };
}

/** Edge selection normalized at click time — string ids only (react-force-graph
 *  mutates link.source/target into node objects, and refetches replace those
 *  objects, so the selection must never hold link references).
 *  `type` is OMITTED when a collapsed per-pair line is clicked (the canvas now
 *  draws one line per node pair) → the panel fetches ALL relations of the pair. */
export interface SelectedEdge {
  sourceId: string;
  sourceName?: string;
  targetId: string;
  targetName?: string;
  type?: string;
  /** When true, opening this edge isolates the pair on the canvas (dims the rest
   *  and frames the two endpoints). Set when the pair is opened from a node's
   *  Conexiones list; edges clicked straight on the canvas leave it unset. */
  isolate?: boolean;
}

/** One (direction, type) group between a pair — GET /graph/relation without
 *  `type`. `count` is the year-window count, `total` is unfiltered. */
export interface RelationGroup {
  fromId: string;
  fromName: string;
  fromType: EntityType;
  toId: string;
  toName: string;
  toType: EntityType;
  type: string;
  count: number;
  total: number;
  evidence: EdgeEvidence[];
}

/** /graph/relation payload (no `type`): every relation between two entities,
 *  one group per (direction, type), so the collapsed per-pair line can show the
 *  full list of connections. */
export interface RelationPair {
  endpoints: { id: string; name?: string; type?: EntityType }[];
  groups: RelationGroup[];
  meta: { types: number; count: number; total: number };
}

/** A chunk hit from GET /documents/search. */
export interface DocSearchHit {
  chunkId: string;
  text: string;
  page: number;
  doc: string;        // document title
  file: string;       // doc_id
  docType?: string | null;
  score: number;
}

/** A document from GET /documents/recent. */
export interface RecentDoc {
  file: string;
  doc: string;
  docType?: string | null;
  date?: string | null;
  pageCount?: number;
}

/** GET /document/{id} payloads. */
export interface DocPageChunk { page: number; idx: number; text: string }
export interface DocumentMeta { doc_id: string; title: string; docType?: string | null; date?: string | null; pageCount?: number }
export interface DocumentRead {
  doc?: DocumentMeta;
  pages?: number[];                 // when no page requested: the page index
  page?: number;
  window?: number;
  chunks?: DocPageChunk[];          // when a page requested: that page's text ±window
  candidates?: DocumentMeta[];      // ambiguous title → caller disambiguates
}

/** A money-trail row from GET /money-flows. */
export interface MoneyFlow {
  source: string;
  sourceId?: string;
  type: string;
  target: string;
  targetId?: string;
  amount: number;
  currency: string;
  date?: string | null;
  doc?: string | null;
  page?: number | null;
  quote?: string | null;
  source_kind: string;
  outlet?: string | null;
  directed?: boolean; // false when sources disagree on direction → render undirected
  sources?: number; // number of documents corroborating this collapsed flow
  label?: string | null; // contract name when this flow came from a reified Contract node
}

/** A disputed claim with its for/against postures (GET /node/{id}/claims, /claim/{id}). */
export interface ClaimPosture {
  entityId: string;
  entityName: string;
  stance: "asserts" | "disputes";
  source_kind: string;
  quote?: string | null;
  doc?: string | null;
  file?: string | null;
  page?: number | null;
  date?: string | null;
  outlet?: string | null;
  url?: string | null;
}
export interface Claim {
  claimId: string;
  text: string;
  topic?: string | null;
  postures: ClaimPosture[];
}

export interface TimelineEvent {
  date: string;              // YYYY-MM-DD or "YYYY"
  year: number;
  type: string;              // relation type
  source: { id: string; name: string; type: EntityType };
  target: { id: string; name: string; type: EntityType };
  doc?: string;
  page?: number;
  quote?: string;
  amount?: number | null;
  currency?: string | null;
}

/** Casos curados a los que pertenece una entidad (spec 038, GET /node/{id}). */
export interface CaseRef {
  slug: string;
  titulo: string;
}

export interface GraphFilters {
  minDegree?: number;
  fromYear?: number | null;
  toYear?: number | null;
}

/** Provenance tiers an edge can come from (see SOURCE_KIND_META): the ontology's layers (spec 069)
 *  plus the API's fail-closed marker for an edge without one. */
export type SourceKind = Capa | "sin_procedencia";

/** Render-level edge filters (hide, don't relayout — same mechanism as the old
 *  única última-hora toggle). "hidden" sets rather than "active" sets so that
 *  unknown/future relation types stay visible by default. */
/** Single active emphasis (mutually exclusive): money scales €-edges; bridges
 *  highlights the top betweenness nodes. spec 013. */
export type EmphasisMode = "none" | "money" | "bridges";
/** How node colour is chosen — orthogonal to the layout mode. spec 013. */
export type ColorMode = "type" | "community";

export interface EdgeFilters {
  hiddenRels: Set<string>;       // relation types switched off in the legend
  hiddenKinds: Set<SourceKind>;  // source layers switched off
  minStatusRank: number;         // 0 = todos; only affects accusation-bearing edges
  emphasis: EmphasisMode;        // none | money (€-edges by amount) | bridges (top-betweenness nodes)
}

export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
  meta?: { truncated?: boolean; hiddenCount?: number };
}

/** /graph/path payload: the union subgraph of all found paths (standard
 *  nodes/links, mergeable like any other payload) plus the ordered entity-id
 *  sequence of each individual path so the UI can highlight/list chains. */
export interface PathResult extends GraphData {
  paths: string[][];
  meta?: GraphData["meta"] & { found?: boolean; pathCount?: number; maxLen?: number };
}

export interface SearchHit {
  id: string;
  type: EntityType;
  name: string;
  degree: number;
  score: number;
}

export interface Provenance {
  doc: string;
  file: string;
  page: number;
  quote: string;
  chunkId: string;
}

export interface NodeDetail {
  node: GraphNode & { aliases?: string[] };
  sources: Provenance[];
}
