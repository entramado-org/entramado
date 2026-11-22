import type { GraphData, ClusterInfo, EntityType } from "./types";
import { TYPE_META } from "./graphStyle";

// ---------------------------------------------------------------------------
// Client-side case clustering.
//
// There are no LegalCase nodes in the graph and no Neo4j GDS plugin, so we can't
// detect communities server-side. Pure graph topology also fails here: Koldo,
// Ábalos and Cerdán are mega-hubs connected to nearly everything, so label
// propagation collapses ~90% of nodes into one "Koldo" blob.
//
// The signal that actually separates the cases is WHICH DOCUMENT mentions an
// entity. Every aggregated link carries its source `doc` (filename), and the 25
// PDFs map cleanly onto cases (the UCO reports, the Begoña auto, the Zapatero
// auto, the parliamentary transcripts, the Supremo autos…). So each node is
// assigned to the case whose documents mention it most, with a small set of
// named protagonists hard-pinned to their case so they never drift.
//
// This is purely a *layout* heuristic — it never touches the data contract, the
// API, or what the detail panel reports.
// ---------------------------------------------------------------------------

// Source document (filename, as it appears in link.doc) → case id.
// Mirrors the corpus in pipeline/data + the briefing case structure.
const DOC_CASES: Record<string, string> = {
  // Caso Koldo · mascarillas / obra pública del MITMA
  "aldama-movil.pdf": "koldo",
  "ts-cita-abalos.pdf": "koldo",
  "Informe-InstruccionN3.pdf": "koldo",
  // Mascarillas — contratos de compra de mascarillas + auditoría del MITMA
  "2024-10-23-Exposicion-razonada-Abalos.pdf": "mascarillas",
  "20240819_informe_audit_ejec_om_adquis_mascarillas_an.pdf": "mascarillas",
  // David Sánchez · Badajoz (hermano de Pedro Sánchez) — enchufe en la Diputación
  "ESCRITO-DE-ACUSACION_fdo.pdf": "david",
  "2025-04-29-badajoz-1-pdf-2.pdf": "david",
  // Cerdán · informes UCO sobre obra pública + auto de prisión del TS
  "informe-uco.pdf": "cerdan",
  "informe-uco-cerdan-1-100.pdf": "cerdan",
  "informe-uco-cerdan-101-200.pdf": "cerdan",
  "informe-uco-cerdan-201-300.pdf": "cerdan",
  "informe-uco-cerdan-301-400.pdf": "cerdan",
  "informe-uco-cerdan-401-490.pdf": "cerdan",
  "auto-prision-cerdan-2025-06-30.pdf": "cerdan",
  "levanta-secreto-santos-cerdan.pdf": "cerdan",
  // Ferraz · PSOE — entrada y registro + informe de la Guardia Civil
  "autoentradaferraz.pdf": "ferraz",
  "informe_guardia_civil.pdf": "ferraz",
  // Begoña Gómez
  "AUTO-IMPUTACION-BEGONA-GOMEZ-11.04.2026.pdf": "begona",
  "auto-begona-incoacion-2024-04-16.pdf": "begona",
  "auto-begona-jurado-2025-09-23.pdf": "begona",
  // Auto Zapatero · Plus Ultra (DP 77/2024) — los informes UDEF son del mismo caso
  "auto-zapatero.pdf": "zapatero",
  "informe-udef-1907.pdf": "zapatero",
  "informe-udef-1908.pdf": "zapatero",
  // Comisión parlamentaria (Diario de Sesiones del Congreso)
  "DSCD-15-CI-8.pdf": "congreso",
  "DS_C_15_233.pdf": "congreso",
  "DS_C_15_103.pdf": "congreso",
};

// Unmapped docs (ATS_*, auto-causa-especial-aforado, recursos…) are Tribunal
// Supremo procedural autos.
const DEFAULT_CASE = "supremo";

// Named protagonists hard-pinned to their case regardless of which document
// happens to mention them most (Ábalos appears everywhere, but he's "Koldo").
const ANCHOR_CASE: Record<string, string> = {
  "Person:koldo-garcia": "koldo",
  "Person:jose-luis-abalos": "koldo",
  "Person:victor-de-aldama": "koldo",
  "Company:soluciones-de-gestion": "koldo",
  "Person:david-sanchez": "david",
  "Person:santos-cerdan": "cerdan",
  "Person:antxon": "cerdan",
  "Organization:acciona": "cerdan",
  "Person:begona-gomez": "begona",
  "Person:juan-carlos-barrabes": "begona",
  // "Segismundo" is the CENDOJ anonymisation pseudonym in the TS autos; by the
  // ATS 10788/2025 context it is Ábalos, so pin it beside him (Koldo) instead of
  // letting the ATS_* docs drop it into the generic "supremo" group.
  "Person:segismundo": "koldo",
};

// Type-based clusters that OVERRIDE the doc-based case assignment: crimes and
// legal cases get their own regions regardless of which document mentions them
// most (otherwise they'd all sink into the UCO/Cerdán case).
const CRIME_ID = "delitos";
const CASE_NODE_ID = "causas";
const ORG_ID = "organismos";

// Only Organization/Court nodes whose NAME identifies a genuine public body go
// to the "organismos" group. An allowlist (not a blocklist) because the graph
// mislabels many private firms (Globalia, Air Europa, banks…), parties (PSOE,
// Vox) and even concepts ("Código Penal") as Organization — those fall through
// to their case/document cluster instead.
const PUBLIC_BODY_KEYWORDS = [
  "ministerio", "guardia civil", "unidad central operativa", "direccion general",
  "puertos del estado", "diputacion", "ayuntamiento", "gobierno", "parlamento",
  "congreso", "senado", "sepi", "adif", "administrador de infraestructuras",
  "ib-salut", "servicio de salud", "fiscalia", "audiencia", "tribunal", "juzgado",
  "corte suprema", "ssts", "jci", "agencia tributaria", "universidad", "conservatorio",
  "intervencion general", "anticorrupcion", "policia judicial", "udef", "onif",
  "sepblac", "administracion de justicia", "abogacia", "confederacion hidrografica",
  "unidad militar de emergencias", "estado mayor", "ejercito", "consell consultiu",
  "aena", "renfe", "correos", "boe", "sector publico", "instituto nacional",
  "departamento de investigacion economica", "delincuencia economica", "brigada central",
  "dgc", "otri", "hacienda", "seguridad social", "tribunal de cuentas",
  "policia", "consejeria", "fuerzas y cuerpos", "union europea", "red.es",
  "guardia urbana", "mossos", "ertzaintza", "centro nacional", "comisaria",
];
function stripAccents(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "");
}
function isPublicBody(name: string): boolean {
  const n = stripAccents((name || "").toLowerCase());
  return PUBLIC_BODY_KEYWORDS.some((k) => n.includes(k));
}

// Declared order → stable palette indices + ring placement. "otros" always last.
const CASE_ORDER = ["koldo", "mascarillas", "david", "cerdan", "ferraz", "begona", "zapatero", "congreso", "supremo", CRIME_ID, CASE_NODE_ID, ORG_ID];
const CASE_LABELS: Record<string, string> = {
  koldo: "Caso Koldo · Ábalos",
  mascarillas: "Mascarillas",
  david: "David Sánchez · Badajoz",
  cerdan: "Cerdán · UCO",
  ferraz: "Ferraz · PSOE",
  begona: "Begoña Gómez",
  zapatero: "Auto Zapatero",
  congreso: "Comisión parlamentaria",
  supremo: "Tribunal Supremo",
  [CRIME_ID]: "Delitos",
  [CASE_NODE_ID]: "Causas",
  [ORG_ID]: "Organismos públicos",
};

// Named CASES only — excludes the type-based pseudo-clusters (delitos/causas/
// organismos/otros), which connect to everything and would yield false "bridge"
// signals in the Claves insights.
const NAMED_CASE_IDS = new Set(CASE_ORDER.filter((id) => ![CRIME_ID, CASE_NODE_ID, ORG_ID].includes(id)));

export function isCaseClusterId(id: string): boolean {
  return NAMED_CASE_IDS.has(id);
}

export function caseLabel(id: string): string {
  return CASE_LABELS[id] ?? id;
}

export const OTHER_ID = "otros";
const OTHER_LABEL = "Otros";
const OTHER_COLOR = "#7e8696"; // warm grey — the un-anchored remainder

/** Muted, low-chroma categorical palette. Distinguishable but calm, so a region
 *  reads as one coherent colour family rather than competing with its neighbours.
 *  (The 9 saturated TYPE_META colours are still used in free / chrono modes.) */
export const CLUSTER_PALETTE = [
  "#5b94a6", // dusty teal
  "#7c8fc4", // slate-blue
  "#c9a85f", // muted amber
  "#c486a0", // dusty rose
  "#8fae86", // sage
  "#c08d6f", // clay
  "#9a8fd4", // periwinkle
  "#8aa0b4", // steel
  "#a88fb0", // mauve
  "#6f9c8f", // muted green-teal
  "#b8856a", // terracotta
  "#7fa0a8", // muted cyan-grey
];

function linkEndId(end: GraphData["links"][number]["source"]): string {
  return typeof end === "string" ? end : end.id;
}

export type ClusterBy = "case" | "community";

export interface ClusterResult {
  byNode: Map<string, string>;
  clusters: ClusterInfo[];
}

/** Assign every node a clusterId by dominant source-document case, with named
 *  anchors overriding. Pure & deterministic (stable tie-breaks) so it memoises
 *  cleanly. `community` is a documented seam — not shipped, falls back to case. */
export function computeClusters(data: GraphData, _opts: { clusterBy?: ClusterBy } = {}): ClusterResult {
  const nodeIds = new Set(data.nodes.map((n) => n.id));

  // Per-node, per-case evidence weight accumulated from incident links.
  const score = new Map<string, Map<string, number>>();
  const bump = (id: string, c: string, w: number) => {
    if (!nodeIds.has(id)) return;
    let m = score.get(id);
    if (!m) score.set(id, (m = new Map()));
    m.set(c, (m.get(c) ?? 0) + w);
  };
  for (const l of data.links) {
    if (!l.doc) continue;
    const c = DOC_CASES[l.doc] ?? DEFAULT_CASE;
    const w = l.count ?? 1;
    bump(linkEndId(l.source), c, w);
    bump(linkEndId(l.target), c, w);
  }

  const byNode = new Map<string, string>();
  const sizeOf = new Map<string, number>();
  for (const n of data.nodes) {
    let cid: string;
    // Type wins: crimes and legal cases get their own regions.
    if (n.type === "Crime") {
      cid = CRIME_ID;
    } else if (n.type === "LegalCase") {
      cid = CASE_NODE_ID;
    } else if ((n.type === "Organization" || n.type === "Court") && isPublicBody(n.name)) {
      cid = ORG_ID;
    } else {
      cid = ANCHOR_CASE[n.id];
      if (!cid) {
        const m = score.get(n.id);
        if (m && m.size) {
          let best: string | null = null;
          let bestW = -Infinity;
          // tie-break deterministically by case id sort
          for (const [c, w] of [...m.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
            if (w > bestW) { bestW = w; best = c; }
          }
          cid = best ?? OTHER_ID;
        } else {
          cid = OTHER_ID; // no doc-bearing links touch this node
        }
      }
    }
    byNode.set(n.id, cid);
    sizeOf.set(cid, (sizeOf.get(cid) ?? 0) + 1);
  }

  // Build ClusterInfo only for clusters that actually have members. Cases keep
  // their declared order (stable palette indices); "otros" always last.
  const present = [...CASE_ORDER, OTHER_ID].filter((id) => (sizeOf.get(id) ?? 0) > 0);
  const clusters: ClusterInfo[] = present.map((id, i) => ({
    id,
    label: id === OTHER_ID ? OTHER_LABEL : CASE_LABELS[id] ?? id,
    color: id === OTHER_ID ? OTHER_COLOR : CLUSTER_PALETTE[i % CLUSTER_PALETTE.length],
    size: sizeOf.get(id) ?? 0,
    anchor: { x: 0, y: 0 },
  }));

  return { byNode, clusters };
}

/** Place clusters on a ring so regions are maximally separated. The "otros"
 *  bucket sinks to the origin so the named cases ring the noisy remainder.
 *  Mutates + returns the clusters (anchors filled in). Deterministic. */
export function layoutClusterAnchors(
  clusters: ClusterInfo[],
  opts: { baseR?: number; k?: number; sinkOtros?: boolean } = {},
): ClusterInfo[] {
  const baseR = opts.baseR ?? 380;
  const k = opts.k ?? 26;
  const sinkOtros = opts.sinkOtros ?? true;

  const total = clusters.reduce((s, c) => s + c.size, 0);
  const R = Math.max(baseR, k * Math.sqrt(Math.max(1, total)));

  const ring = clusters.filter((c) => !(sinkOtros && c.id === OTHER_ID));
  // Biggest cases at the cardinal points → stable, balanced layout.
  const ringSorted = [...ring].sort((a, b) => b.size - a.size || (a.id < b.id ? -1 : 1));
  const N = ringSorted.length;
  ringSorted.forEach((c, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, N);
    c.anchor = { x: R * Math.cos(angle), y: R * Math.sin(angle) };
  });
  for (const c of clusters) if (sinkOtros && c.id === OTHER_ID) c.anchor = { x: 0, y: 0 };

  return clusters;
}

// ───────────────────────────────────────────────────────────────────────────
// Radial layout ("Personas"): people at the CENTRE grouped by case, every other
// entity pushed to an OUTER ring grouped by TYPE (Organizaciones, Empresas,
// Tribunales, Causas, Delitos, Eventos, Lugares). A separate, additive
// layout mode — the case-cluster functions above are untouched.
//   • people → their dominant case (same doc/anchor logic as computeClusters)
//   • everything else → a per-TYPE cluster, regardless of case
// ───────────────────────────────────────────────────────────────────────────

// People-only case order (the 9 doc-cases + "otros"); the crime/legalcase/orgs
// type-overrides don't apply here since non-people are grouped by type instead.
const PERSON_CASE_ORDER = ["koldo", "mascarillas", "david", "cerdan", "ferraz", "begona", "zapatero", "congreso", "supremo"];
// Outer ring order. Person is excluded — people live in the centre. Ordered into
// semantic neighbours so the procurement triangle reads across adjacent sectors:
// Organization (awarding body) → Contract → Company (awardee), then the legal
// substance (Causas/Delitos/Tribunales) and context (Eventos/Lugares).
const DETAIL_TYPE_ORDER: EntityType[] = ["Organization", "Contract", "Company", "Court", "LegalCase", "Crime", "Event", "Location"];
const TYPE_PREFIX = "tipo:";
const typeClusterId = (t: EntityType): string => `${TYPE_PREFIX}${t}`;
const isTypeClusterId = (id: string): boolean => id.startsWith(TYPE_PREFIX);

/** Resolve a person's case the same way computeClusters does (anchor override →
 *  dominant source-doc case → "otros"). */
function dominantCase(id: string, score: Map<string, Map<string, number>>): string {
  const anchor = ANCHOR_CASE[id];
  if (anchor) return anchor;
  const m = score.get(id);
  if (m && m.size) {
    let best: string | null = null;
    let bestW = -Infinity;
    for (const [c, w] of [...m.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
      if (w > bestW) { bestW = w; best = c; }
    }
    return best ?? OTHER_ID;
  }
  return OTHER_ID;
}

/** People grouped by case (centre), everything else grouped by entity type
 *  (outer). Pure & deterministic, like computeClusters. */
export function computeRadialClusters(data: GraphData): ClusterResult {
  const nodeIds = new Set(data.nodes.map((n) => n.id));

  // Same per-node, per-case evidence weighting computeClusters uses for people.
  const score = new Map<string, Map<string, number>>();
  const bump = (id: string, c: string, w: number) => {
    if (!nodeIds.has(id)) return;
    let m = score.get(id);
    if (!m) score.set(id, (m = new Map()));
    m.set(c, (m.get(c) ?? 0) + w);
  };
  for (const l of data.links) {
    if (!l.doc) continue;
    const c = DOC_CASES[l.doc] ?? DEFAULT_CASE;
    const w = l.count ?? 1;
    bump(linkEndId(l.source), c, w);
    bump(linkEndId(l.target), c, w);
  }

  const byNode = new Map<string, string>();
  const sizeOf = new Map<string, number>();
  for (const n of data.nodes) {
    const cid = n.type === "Person" ? dominantCase(n.id, score) : typeClusterId(n.type);
    byNode.set(n.id, cid);
    sizeOf.set(cid, (sizeOf.get(cid) ?? 0) + 1);
  }

  // Centre: case clusters (colour pinned to the case's CASE_ORDER index so it
  // matches the "Casos" mode exactly; koldo=teal in both).
  const caseClusters: ClusterInfo[] = [...PERSON_CASE_ORDER, OTHER_ID]
    .filter((id) => (sizeOf.get(id) ?? 0) > 0)
    .map((id) => ({
      id,
      label: id === OTHER_ID ? OTHER_LABEL : CASE_LABELS[id] ?? id,
      color: id === OTHER_ID ? OTHER_COLOR : CLUSTER_PALETTE[Math.max(0, CASE_ORDER.indexOf(id)) % CLUSTER_PALETTE.length],
      size: sizeOf.get(id) ?? 0,
      anchor: { x: 0, y: 0 },
    }));
  // Outer: one cluster per entity type, coloured by the type palette + labelled
  // like the FilterRail (Organizaciones, Empresas, …).
  const typeClusters: ClusterInfo[] = DETAIL_TYPE_ORDER
    .filter((t) => (sizeOf.get(typeClusterId(t)) ?? 0) > 0)
    .map((t) => ({
      id: typeClusterId(t),
      label: TYPE_META[t].label,
      color: TYPE_META[t].color,
      size: sizeOf.get(typeClusterId(t)) ?? 0,
      anchor: { x: 0, y: 0 },
    }));

  return { byNode, clusters: [...caseClusters, ...typeClusters] };
}

/** Two concentric rings: case (people) clusters on the inner ring, type (detail)
 *  clusters on the outer ring. Order is preserved from computeRadialClusters
 *  (semantic case order inside; FilterRail type order outside). Deterministic. */
export function layoutRadialAnchors(
  clusters: ClusterInfo[],
  opts: { innerBaseR?: number; outerFactor?: number } = {},
): ClusterInfo[] {
  const cases = clusters.filter((c) => !isTypeClusterId(c.id));
  const types = clusters.filter((c) => isTypeClusterId(c.id));
  const peopleTotal = cases.reduce((s, c) => s + c.size, 0);

  // Wider rings than the maths strictly needs: the case groups breathe instead
  // of fusing into one central blob, and the type ring reads as a distinct band.
  const Ri = Math.max(opts.innerBaseR ?? 340, 32 * Math.sqrt(Math.max(1, peopleTotal)));
  const Ro = Ri * (opts.outerFactor ?? 2.6);

  const place = (arr: ClusterInfo[], R: number) => {
    const N = arr.length;
    arr.forEach((c, i) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, N);
      c.anchor = { x: R * Math.cos(angle), y: R * Math.sin(angle) };
    });
  };
  place(cases, Ri);
  place(types, Ro);

  return clusters;
}
