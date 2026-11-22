import { ESTADOS, ESTADO_RANGO, type Estado, type TipoRelacion } from "./ontologia.generated";
import type { EntityType, SourceKind } from "./types";

export const TYPE_META: Record<EntityType, { color: string; label: string; glow: string }> = {
  Person: { color: "#38bdf8", glow: "#7dd3fc", label: "Personas" },
  Organization: { color: "#a78bfa", glow: "#c4b5fd", label: "Organizaciones" },
  Company: { color: "#fbbf24", glow: "#fcd34d", label: "Empresas" },
  Court: { color: "#34d399", glow: "#6ee7b7", label: "Tribunales" },
  LegalCase: { color: "#f472b6", glow: "#f9a8d4", label: "Causas" },
  Crime: { color: "#f87171", glow: "#fca5a5", label: "Delitos" },
  Event: { color: "#fb923c", glow: "#fdba74", label: "Eventos" },
  Location: { color: "#2dd4bf", glow: "#5eead4", label: "Lugares" },
  Contract: { color: "#818cf8", glow: "#a5b4fc", label: "Contratos" },
};

export const ALL_TYPES = Object.keys(TYPE_META) as EntityType[];

export function nodeColor(type: EntityType): string {
  return TYPE_META[type]?.color ?? "#94a3b8";
}

/** Derive a readable label + type color from a bare entity_id like
 *  "Person:koldo-garcia" (briefing anchors carry no display name). */
export function anchorLabel(id: string): { label: string; color: string } {
  const [type, ...rest] = id.split(":");
  const slug = rest.join(":") || type;
  const label = slug
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
  return { label, color: TYPE_META[type as EntityType]?.color ?? "#94a3b8" };
}

/** Log-scale radius. Tuned down from earlier passes — a degree-1 node renders
 *  at ~2.2px, a hub at degree ~3500 caps around 15px. Smaller-overall keeps
 *  the canvas from feeling clogged when the overview has 100+ entities. */
export function nodeRadius(degree = 0): number {
  return 1.8 + Math.log2(1 + Math.max(0, degree)) * 1.15;
}

/** Heuristic for "this node deserves a permanent label". True when the node is
 *  visually large enough to anchor reading regardless of zoom. */
export function isHubNode(degree = 0): boolean {
  return degree >= 80;
}

/** Color edges by aggregated relation type so type is legible at a glance —
 *  without forcing the user to hover or open the detail panel. Stays inside
 *  the existing ink/accent palette so it doesn't clash with node colors. */
const REL_COLORS: Record<string, string> = {
  PAID:           "#4ade80", // green — money out
  RECEIVED:       "#86efac", // green-light — money in
  OWNS:           "#fbbf24", // amber — ownership
  CONTROLS:       "#fcd34d",
  ADMINISTRA:     "#22d3ee", // cyan — registered administrator (BORME structure)
  RECEIVED_GRANT: "#a3e635", // lime — public grant/aid (BDNS; legal, not an accusation)
  MINOR_CONTRACTOR_OF: "#c084fc", // purple — aggregated minor contracts (PLACSP; legal, ≠ judicial 'contrato' indigo)
  CONTRACTOR_OF:  "#e879f9", // fuchsia — aggregated ampliada procurement (tenders + CCAA minors; same familia as menores purple, distinguishable)
  APPOINTED_TO:   "#2dd4bf", // teal — public-office tenure (BOE II.A; legal appointment, ≠ WORKS_FOR judicial sky)
  SUBSIDIARY_OF:  "#f59e0b", // deep amber — org hierarchy (filial→matriz)
  WORKS_FOR:      "#7dd3fc", // sky — employment
  MEMBER_OF:      "#a78bfa", // violet — affiliation
  ASSOCIATED_WITH:"#38e1c4", // accent teal — generic tie
  MET_WITH:       "#5eead4",
  ACCUSED_OF:     "#f87171", // red — accusation (alleged)
  CONVICTED_OF:   "#dc2626", // dark red — firm conviction
  INVESTIGATED_BY:"#fb7185",
  AWARDED_CONTRACT:   "#818cf8", // indigo — procurement (body→contract)
  CONTRACT_AWARDED_TO:"#a5b4fc", // light indigo — contract→awardee
  PARTY_TO:       "#f472b6", // pink — litigation
  OCCURRED_AT:    "#fdba74",
  PARTICIPATED_IN:"#fb923c",
  INVOLVES:       "#c4b5fd",
  FAMILY_OF:      "#e9d5ff", // soft lilac — kinship (curated context)
};

export function relColor(type: string): string {
  return REL_COLORS[type] ?? "#94a3b8";
}

/** Human-readable Spanish label for relation types — used in tooltips and the
 *  connections list. Falls back to the raw type if unknown. */
// Exhaustive over the ontology's relation types (spec 069): a new type without a label breaks the build.
const REL_LABELS_BY_TYPE: Record<TipoRelacion, string> = {
  PAID: "Pagó a",
  RECEIVED: "Recibió de",
  OWNS: "Posee",
  CONTROLS: "Controla",
  ADMINISTRA: "Administra",
  RECEIVED_GRANT: "Subvención pública",
  MINOR_CONTRACTOR_OF: "Contratos menores de (PLACSP)",
  CONTRACTOR_OF: "Contratación pública de (agregado)",
  APPOINTED_TO: "Cargo público en (BOE)",
  SUBSIDIARY_OF: "Filial de",
  WORKS_FOR: "Trabaja para",
  MEMBER_OF: "Miembro de",
  ASSOCIATED_WITH: "Asociado con",
  MET_WITH: "Se reunió con",
  ACCUSED_OF: "Acusado de",
  CONVICTED_OF: "Condenado por",
  INVESTIGATED_BY: "Investigado por",
  AWARDED_CONTRACT: "Adjudicó",
  CONTRACT_AWARDED_TO: "Adjudicado a",
  PARTY_TO: "Parte en",
  OCCURRED_AT: "Ocurrió en",
  PARTICIPATED_IN: "Participó en",
  INVOLVES: "Implica a",
  FAMILY_OF: "Familiar de",
};
const REL_LABELS: Record<string, string> = REL_LABELS_BY_TYPE;

/** Etiqueta de una arista de acusación SEGÚN SU ESTADO PROCESAL (spec 061), o
 *  null si el tipo no es acusatorio. La etiqueta genérica "Acusado de" mentía
 *  sobre las 220 ACCUSED_OF sin `status`: sin estado registrado, lo único que
 *  sostiene una acusación es «investigado». Sin firmeza declarada, una condena es
 *  «no firme» (spec 059). */
export function accusationLabel(type: string, status?: string | null): string | null {
  if (type !== "ACCUSED_OF" && type !== "CONVICTED_OF") return null;
  switch (status) {
    case "investigado":
    case "imputado":
      return "Investigado por";
    case "procesado":
    case "acusado":
      return "Acusado de";
    case "condena_anulada":
      return "Condena anulada (TC) por";
    case "condenado_no_firme":
      return "Condenado (no firme) por";
    case "condenado":
      return "Condenado por";
    case "absuelto":
      return "Absuelto de";
    case "archivado":
      return "Causa archivada por";
    default:
      return type === "CONVICTED_OF" ? "Condenado (no firme) por" : "Investigado por";
  }
}

/** Etiqueta de una relación. Con `status` (o sin él) una arista de acusación pasa
 *  por `accusationLabel`, así que ninguna vista puede rotular «Acusado de» una
 *  arista sin estado registrado. */
export function relLabel(type: string, status?: string | null): string {
  return accusationLabel(type, status) ?? REL_LABELS[type] ?? type.replace(/_/g, " ").toLowerCase();
}

/** Legend rows for relation types — derived from the same color + label maps the
 *  canvas uses, so the rail legend and on-canvas edges never drift apart. La
 *  leyenda nombra TIPOS, no aristas concretas: ahí "Acusado de" sigue siendo el
 *  nombre del tipo ACCUSED_OF (la escalera de estados vive en EVIDENCE_LEGEND). */
export const REL_LEGEND: { type: string; color: string; label: string }[] = Object.keys(
  REL_COLORS,
).map((type) => ({ type, color: REL_COLORS[type], label: REL_LABELS[type] ?? relLabel(type) }));

/** Neutral ink for unfocused edges in cluster mode. The 15 relation colours only
 *  ever appear for the focused subgraph the user is actively reading — keeping
 *  the default frame calm. */
export const EDGE_NEUTRAL = "#7c8aa0";

/* ── Grado epistémico ──────────────────────────────────────────────────────
 * The single most important honesty cue in the UI: how strong is the claim?
 * We DON'T invent data — we surface two fields the graph already carries:
 *   · edge.status      → procedural stage of a charge (accusations layer)
 *   · edge.source_kind → which layer the edge comes from (judicial/contexto/…)
 * Colours/labels live here so tooltip, connections list and the legend never
 * drift. Rendered with inline hex (not Tailwind classes) so lib/ stays out of
 * the purge path. `rank` orders statuses from least to most advanced so a node
 * with several charges shows its highest stage; `presunto` flags that the
 * presumption of innocence still applies (everything short of a firm sentence).
 */
type StatusMeta = { label: string; color: string; rank: number; presunto: boolean };
// Labels and colours are editorial; the set of statuses and their `rank` come from the ontology
// (spec 069), so a new status without a label breaks the build.
const STATUS_LABELS: Record<Estado, Omit<StatusMeta, "rank">> = {
  investigado: { label: "Investigado", color: "#fca5a5", presunto: true },
  imputado:    { label: "Imputado",    color: "#f87171", presunto: true },
  procesado:   { label: "Procesado",   color: "#ef4444", presunto: true },
  acusado:     { label: "Acusado",     color: "#ef4444", presunto: true },
  // Spec 059: una condena RECURRIDA o recurrible no es firme — la presunción de inocencia
  // sigue vigente. La firmeza la declara documents.yaml (sentencia_firme); sin ella, la API
  // sirve 'condenado_no_firme' (fail-closed), nunca 'condenado'.
  // Decisión 07-oct (spec 069): el TC anuló la condena; no hay condena vigente.
  condena_anulada: { label: "Condena anulada (TC)", color: "#cbd5e1", presunto: true },
  condenado_no_firme: { label: "Condenado (no firme)", color: "#dc2626", presunto: true },
  condenado:   { label: "Condenado",   color: "#dc2626", presunto: false },
  absuelto:    { label: "Absuelto",    color: "#4ade80", presunto: false },
  archivado:   { label: "Archivado",   color: "#94a3b8", presunto: false },
};
export const STATUS_META: Record<string, StatusMeta> = Object.fromEntries(
  ESTADOS.map((s) => [s, { ...STATUS_LABELS[s], rank: ESTADO_RANGO[s] }]),
);

/** Rol corto para titles y filas de listado: primera cláusula del rol curado,
 *  acotada — los roles del grafo suelen ser una frase entera. */
export function shortRole(role: string | null | undefined, fallback: string): string {
  if (!role) return fallback;
  const clause = role.split(/[;.·]/)[0].trim();
  if (!clause) return fallback;
  return clause.length > 65 ? clause.slice(0, 62).trimEnd() + "…" : clause;
}

export function statusMeta(status?: string | null) {
  return status ? STATUS_META[status] ?? null : null;
}

/** Given a person's charge statuses, return the most advanced one to headline
 *  in the detail panel (or null if there are none). */
export function headlineStatus(statuses?: string[] | null): string | null {
  if (!statuses || statuses.length === 0) return null;
  let best: string | null = null;
  let bestRank = -1;
  for (const s of statuses) {
    const r = STATUS_META[s]?.rank ?? 0;
    if (r > bestRank) {
      bestRank = r;
      best = s;
    }
  }
  return best;
}

/** Default procedural rank for accusation-shaped relation types when the edge
 *  carries no explicit `status`. Shared by the canvas status filter and the
 *  "Claves → Más señalados" insight so the two never disagree. */
export const ACCUSATION_REL_RANK: Record<string, number> = {
  INVESTIGATED_BY: 2, // = investigado
  // Spec 061: sin status explícito no se presume una fase avanzada — una acusación
  // sin estado registrado vale como «investigado» (antes 4 = acusado), igual que
  // el default de la API (coalesce(acc.status,'investigado')).
  ACCUSED_OF: 2,      // = investigado
  CONVICTED_OF: 5,    // = condenado_no_firme (sin status explícito no se presume firmeza)
};

/** Effective procedural rank of an edge: explicit status wins, else the
 *  relation type's default, else 0 (not an accusation edge). */
export function edgeStatusRank(type: string, status?: string | null): number {
  if (status && STATUS_META[status]) return STATUS_META[status].rank;
  return ACCUSATION_REL_RANK[type] ?? 0;
}

/** Estado de una CAUSA curada (spec 038) — no confundir con el estado procesal
 *  de una persona (STATUS_META): describe en qué fase está el caso entero.
 *  Tonos coherentes con la escalera de STATUS_META. */
export const CASE_STATUS_META: Record<string, { label: string; color: string }> = {
  instruccion: { label: "En instrucción", color: "#fca5a5" },
  juicio:      { label: "En fase de juicio", color: "#ef4444" },
  sentencia:   { label: "Con sentencia", color: "#dc2626" },
  archivado:   { label: "Archivado", color: "#94a3b8" },
};

/** Provenance layer of an edge — what kind of source backs it. Used for the
 *  badge shown when an edge carries no procedural status. */
// Exhaustive over the ontology's layers + the API's fail-closed marker (spec 069).
const SOURCE_KIND_META_BY_KIND: Record<SourceKind, { label: string; desc: string; color: string }> = {
  judicial:  { label: "Documentado", color: "#7dd3fc", desc: "Afirmado en un documento judicial o policial citado. No equivale a hecho probado en juicio." },
  contexto:  { label: "Contexto",    color: "#94a3b8", desc: "Dato público de contexto; no procede de los documentos judiciales." },
  prensa:    { label: "Prensa",      color: "#fbbf24", desc: "Procede de prensa; sin fuente judicial." },
  acusacion: { label: "Acusación",   color: "#ef4444", desc: "Cargo del registro curado de acusaciones: cita el auto o escrito judicial que lo formula (imputación, procesamiento o escrito de acusación). No es condena." },
  contrato:  { label: "Contrato",    color: "#818cf8", desc: "Adjudicación de contratación pública." },
  ultima_hora: { label: "Última hora", color: "#fb923c", desc: "Desarrollo reciente que de momento solo consta en prensa; sin documento judicial. Rige la presunción de inocencia." },
  mercantil: { label: "Mercantil", color: "#22d3ee", desc: "Estructura societaria del Registro Mercantil (BORME), vía Nearius: administradores↔empresas. Dato oficial público (se cita el borme_id), no fuente judicial. Estructura ≠ culpabilidad." },
  subvencion: { label: "Subvenciones", color: "#a3e635", desc: "Subvención, ayuda o aval público registrado en la BDNS (SNPSAP): beneficiaria↔organismo concedente. Dato oficial público, no fuente judicial. Recibir una ayuda es legal; la señal es el cruce con la trama." },
  contratacion: { label: "Contratación (PLACSP y plataformas oficiales)", color: "#c084fc", desc: "Agregados oficiales de contratación pública: contratos menores de PLACSP (morado) y capa ampliada de licitaciones, plataformas agregadas y menores autonómicos con señales de procedimiento — emergencia, negociado sin publicidad, vía UTE (fucsia). Adjudicataria↔órgano con nº de contratos, total y rango. Dato oficial público, no fuente judicial; distinto de la capa «Contrato» (adjudicaciones citadas en documentos judiciales). Contratar con la administración es legal; la señal es el cruce con la trama y el patrón de procedimiento." },
  sin_procedencia: { label: "Procedencia no registrada", color: "#64748b", desc: "Relación sin marcador de capa: se muestra con el menor peso hasta verificar su origen." },
  boe: { label: "Cargos (BOE)", color: "#2dd4bf", desc: "Trayectoria de cargos públicos de la sección II.A del BOE: persona↔organismo, con la disposición citable (BOE-A-…) de nombramiento y, si consta, de cese. Dato oficial público, no fuente judicial. Un nombramiento es un acto administrativo legal; la señal es la trayectoria cruzada con el caso (quién dirigía qué organismo cuándo), nunca el cargo en sí." },
};
export const SOURCE_KIND_META: Record<string, { label: string; desc: string; color: string }> =
  SOURCE_KIND_META_BY_KIND;

/** The epistemic-tier legend shown in the explorer. Ordered strongest→weakest
 *  so a reader sees at a glance that "documentado" ≠ "condenado". */
export const EVIDENCE_LEGEND: { label: string; color: string; desc: string }[] = [
  { label: "Condenado", color: "#dc2626", desc: "Condena por sentencia firme (ya no cabe recurso)." },
  { label: "Condenado (no firme)", color: "#dc2626", desc: "Sentencia recurrida o recurrible: rige la presunción de inocencia." },
  { label: "Procesado / acusado", color: "#ef4444", desc: "La causa avanza hacia juicio. Presunto." },
  { label: "Imputado / investigado", color: "#f87171", desc: "Bajo investigación. Rige la presunción de inocencia." },
  { label: "Documentado en fuente", color: "#7dd3fc", desc: "El documento judicial lo afirma; no es una condena." },
  { label: "Contexto público", color: "#94a3b8", desc: "Dato público de contexto, no judicial." },
  { label: "Prensa", color: "#fbbf24", desc: "Dato de prensa, sin fuente judicial." },
  { label: "Última hora", color: "#fb923c", desc: "Desarrollo reciente, de momento solo en prensa; presunción de inocencia." },
  { label: "Mercantil (BORME)", color: "#22d3ee", desc: "Estructura societaria oficial del registro mercantil (vía Nearius), no judicial." },
  { label: "Subvenciones (BDNS)", color: "#a3e635", desc: "Ayuda o aval público oficial de la BDNS; legal, no judicial." },
  { label: "Contratación — menores (PLACSP)", color: "#c084fc", desc: "Agregado oficial de contratos menores de PLACSP; legal, no judicial." },
  { label: "Contratación — ampliada (licitaciones y CCAA)", color: "#e879f9", desc: "Agregado oficial de licitaciones (PLACSP y plataformas agregadas) y menores autonómicos, con señales de procedimiento; legal, no judicial." },
];

type NodeShape = "circle" | "square" | "triangle" | "diamond";

/** In cluster mode nodes are coloured by case, so entity type moves to a SHAPE
 *  cue. Kept coarse (4 shapes) — cluster colour carries the primary grouping and
 *  the detail panel always states the exact type. */
function nodeShape(type: EntityType): NodeShape {
  switch (type) {
    case "Organization":
    case "Company":
    case "Court":
      return "square";
    case "LegalCase":
    case "Crime":
      return "triangle";
    case "Contract":
      return "diamond";
    default: // Person, Event, Location
      return "circle";
  }
}

function traceRoundRect(ctx: CanvasRenderingContext2D, x: number, y: number, s: number, rad: number) {
  const r = Math.min(rad, s / 2);
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + s, y, x + s, y + s, r);
  ctx.arcTo(x + s, y + s, x, y + s, r);
  ctx.arcTo(x, y + s, x, y, r);
  ctx.arcTo(x, y, x + s, y, r);
  ctx.closePath();
}

/** Trace the type-shape path centred at (x,y) sized to radius r. Leaves the path
 *  open so the caller can both fill and stroke it (no fill/stroke done here). */
export function drawNodeShape(ctx: CanvasRenderingContext2D, type: EntityType, x: number, y: number, r: number) {
  ctx.beginPath();
  switch (nodeShape(type)) {
    case "square": {
      const s = r * 1.7;
      traceRoundRect(ctx, x - s / 2, y - s / 2, s, s * 0.28);
      break;
    }
    case "triangle": {
      const a = r * 1.45;
      ctx.moveTo(x, y - a);
      ctx.lineTo(x + a * 0.92, y + a * 0.72);
      ctx.lineTo(x - a * 0.92, y + a * 0.72);
      ctx.closePath();
      break;
    }
    case "diamond": {
      const a = r * 1.4;
      ctx.moveTo(x, y - a);
      ctx.lineTo(x + a, y);
      ctx.lineTo(x, y + a);
      ctx.lineTo(x - a, y);
      ctx.closePath();
      break;
    }
    default:
      ctx.arc(x, y, r, 0, 2 * Math.PI);
  }
}
