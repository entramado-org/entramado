import type { Claim, DocSearchHit, DocumentRead, EdgeDetail, GraphData, GraphFilters, MoneyFlow, NodeDetail, PathResult, RecentDoc, RelationPair, SearchHit, SelectedEdge, TimelineEvent } from "./types";

// Resolve the API base so the app works from any device on the LAN with no
// hardcoded IP: an explicit non-localhost NEXT_PUBLIC_API_BASE wins; otherwise
// the browser talks to the API on the SAME host it loaded the page from, port
// 8000 (localhost on the host machine, the LAN IP/hostname on other devices).
function resolveApiBase(): string {
  const env = process.env.NEXT_PUBLIC_API_BASE;
  if (env && !/localhost|127\.0\.0\.1/.test(env)) return env;
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return env || "http://localhost:8000";
}

const BASE = resolveApiBase();

async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal });
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
  return res.json();
}

function filterQS(f?: GraphFilters): string {
  if (!f) return "";
  const parts: string[] = [];
  if (f.minDegree && f.minDegree > 1) parts.push(`min_degree=${f.minDegree}`);
  if (f.fromYear != null) parts.push(`from_year=${f.fromYear}`);
  if (f.toYear != null) parts.push(`to_year=${f.toYear}`);
  return parts.length ? "&" + parts.join("&") : "";
}

export async function searchEntities(
  q: string,
  signal?: AbortSignal,
  types?: string[],
): Promise<{ results: SearchHit[] }> {
  const t = types?.length ? `&types=${types.join(",")}` : "";
  const j = await getJSON<{ results?: SearchHit[] }>(`/search?q=${encodeURIComponent(q)}${t}`, signal);
  return { results: j.results ?? [] };
}

export function fetchOverview(types?: string[], limit = 250, filters?: GraphFilters): Promise<GraphData> {
  const t = types?.length ? `&types=${types.join(",")}` : "";
  return getJSON(`/graph/overview?limit=${limit}${t}${filterQS(filters)}`);
}

export function fetchNeighborhood(id: string, hops = 1, limit = 120, filters?: GraphFilters): Promise<GraphData> {
  return getJSON(
    `/node/${encodeURIComponent(id)}/neighborhood?hops=${hops}&limit=${limit}${filterQS(filters)}`,
  );
}

/** Shortest evidence chains between two entities. Deliberately NOT
 *  year-filtered (the chronology filter could visually break a valid chain). */
export function fetchPath(fromId: string, toId: string, maxLen = 4, maxPaths = 5): Promise<PathResult> {
  return getJSON(
    `/graph/path?from=${encodeURIComponent(fromId)}&to=${encodeURIComponent(toId)}&max_len=${maxLen}&max_paths=${maxPaths}`,
  );
}

export interface BriefingMeta { slug: string; title: string; dek?: string; anchors?: string[]; updated?: string | null }
export interface Briefing extends BriefingMeta { content: string }

export function fetchUltimaHora(): Promise<{ items: import("./ultimaHora").UltimaHoraItem[] }> {
  return getJSON(`/ultima-hora`);
}

export function fetchBriefings(): Promise<{ briefings: BriefingMeta[] }> {
  return getJSON(`/briefings`);
}

export function fetchBriefing(slug: string): Promise<Briefing> {
  return getJSON(`/briefing/${encodeURIComponent(slug)}`);
}

export function fetchTimeline(
  entity?: string | null,
  filters?: GraphFilters,
  limit = 500,
): Promise<{ events: TimelineEvent[] }> {
  const e = entity ? `&entity=${encodeURIComponent(entity)}` : "";
  return getJSON(`/timeline?limit=${limit}${e}${filterQS(filters)}`);
}

export function fetchNodeDetail(id: string): Promise<NodeDetail> {
  return getJSON(`/node/${encodeURIComponent(id)}`);
}

/** Full evidence behind one aggregated display edge. Passes the active year
 *  window so the panel count matches the ×N drawn on the canvas. */
export function fetchEdgeDetail(edge: SelectedEdge, filters?: GraphFilters): Promise<EdgeDetail> {
  const qs =
    `from=${encodeURIComponent(edge.sourceId)}&to=${encodeURIComponent(edge.targetId)}` +
    `&type=${encodeURIComponent(edge.type!)}` +
    (filters?.fromYear != null ? `&from_year=${filters.fromYear}` : "") +
    (filters?.toYear != null ? `&to_year=${filters.toYear}` : "");
  return getJSON(`/graph/relation?${qs}`);
}

/** All relations between a pair (no `type`) — backs the collapsed per-pair line. */
export function fetchRelationPair(edge: SelectedEdge, filters?: GraphFilters): Promise<RelationPair> {
  const qs =
    `from=${encodeURIComponent(edge.sourceId)}&to=${encodeURIComponent(edge.targetId)}` +
    (filters?.fromYear != null ? `&from_year=${filters.fromYear}` : "") +
    (filters?.toYear != null ? `&to_year=${filters.toYear}` : "");
  return getJSON(`/graph/relation?${qs}`);
}

/** Resolve a citation to its passage text (web twin of leer_documento). */
export function fetchDocument(docId: string, page?: number, window = 0): Promise<DocumentRead> {
  const qs = page != null ? `?page=${page}&window=${window}` : "";
  return getJSON(`/document/${encodeURIComponent(docId)}${qs}`);
}

export function fetchRecentDocuments(limit = 12, tipo?: string): Promise<{ documents: RecentDoc[] }> {
  const t = tipo ? `&tipo=${encodeURIComponent(tipo)}` : "";
  return getJSON(`/documents/recent?limit=${limit}${t}`);
}

export function fetchDocumentsSearch(
  q: string,
  opts?: { tipo?: string; desde?: string; hasta?: string; limit?: number; mentions?: string },
  signal?: AbortSignal,
): Promise<{ results: DocSearchHit[] }> {
  const p: string[] = [`q=${encodeURIComponent(q)}`, `limit=${opts?.limit ?? 12}`];
  if (opts?.tipo) p.push(`tipo=${encodeURIComponent(opts.tipo)}`);
  if (opts?.desde) p.push(`desde=${opts.desde}`);
  if (opts?.hasta) p.push(`hasta=${opts.hasta}`);
  if (opts?.mentions) p.push(`mentions=${encodeURIComponent(opts.mentions)}`);
  return getJSON(`/documents/search?${p.join("&")}`, signal);
}

export function fetchMoneyFlows(
  opts?: { entity?: string; min?: number; fromYear?: number; toYear?: number; limit?: number },
): Promise<{ flows: MoneyFlow[] }> {
  const p: string[] = [];
  if (opts?.entity) p.push(`entity=${encodeURIComponent(opts.entity)}`);
  if (opts?.min != null) p.push(`min=${opts.min}`);
  if (opts?.fromYear != null) p.push(`from_year=${opts.fromYear}`);
  if (opts?.toYear != null) p.push(`to_year=${opts.toYear}`);
  if (opts?.limit != null) p.push(`limit=${opts.limit}`);
  return getJSON(`/money-flows${p.length ? "?" + p.join("&") : ""}`);
}

export function fetchEntityClaims(id: string): Promise<{ claims: Claim[] }> {
  return getJSON(`/node/${encodeURIComponent(id)}/claims`);
}
