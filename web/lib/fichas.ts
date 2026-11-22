/* Server-side data access for the public entity fichas (/entidad, spec 035).
 *
 * Runs ONLY in server components: it talks to the API over the compose network
 * (same pattern as app/page.tsx). Every fetch uses ISR (`next.revalidate`) —
 * NOT `no-store` — so the ficha pages stay static-ish and one crawler sweep
 * costs Neo4j at most one query set per page per hour. */
import type { GraphData, GraphLink, GraphNode, NodeDetail } from "./types";

const API_BASE =
  process.env.API_INTERNAL_BASE || process.env.NEXT_PUBLIC_API_BASE || "http://api:8000";

export const FICHA_REVALIDATE = 3600;

export interface IndexableEntity {
  id: string;
  slug: string;
  type: "Person" | "Company" | "Organization";
  name: string;
  role?: string | null;
  degree: number;
  hasSummary: boolean;
  teaser?: string | null;
  summaryUpdated?: string | null;
  isMercantil: boolean;
}

async function apiJson<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`${API_BASE}${path}`, { next: { revalidate: FICHA_REVALIDATE } });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null; // API down → callers degrade (404 / empty section), never throw
  }
}

/* --- Directorio de investigados (spec 037) -------------------------------- */

export interface AccusedCharge {
  crime: string;
  article?: string | null;
  status: string;
  sourceKind: string;
  doc?: string | null;
  page?: number | null;
  quote?: string | null;
  outlet?: string | null;
  // Registro de estado procesal (spec 061): acto que lo fija, su fecha y su tipo.
  statusFecha?: string | null;
  statusFuente?: string | null;
  statusFuenteTipo?: string | null;
}

export interface AccusedEntity {
  id: string;
  type: string;
  name: string;
  role?: string | null;
  degree: number;
  charges: AccusedCharge[];
  fichaSlug?: string | null;
  /** Casos curados a los que pertenece (se puebla con spec 038). */
  casos?: { slug: string; titulo: string }[];
}

/** Every entity with charges, ordered by procedural rank (backend). */
export async function getAccused(): Promise<AccusedEntity[]> {
  const j = await apiJson<{ entities: AccusedEntity[] }>("/entities/accused");
  return j?.entities ?? [];
}

/** The central cast (~300): the only entities with a public ficha. */
export async function getIndexables(): Promise<IndexableEntity[]> {
  const j = await apiJson<{ entities: IndexableEntity[] }>("/entities/indexable");
  return j?.entities ?? [];
}

export async function resolveSlug(slug: string): Promise<IndexableEntity | null> {
  const all = await getIndexables();
  return all.find((e) => e.slug === slug) ?? null;
}

/** One aggregated relation row for the ficha, derived from the 1-hop
 *  neighborhood: the neighbor + the strongest display edge joining it. */
export interface FichaRelation {
  neighbor: GraphNode;
  link: GraphLink;
  outgoing: boolean; // entity → neighbor (relation label reads left-to-right)
}

export interface Ficha {
  detail: NodeDetail;
  relations: FichaRelation[];
}

/** Full ficha payload: node detail + top relations, fetched in parallel. */
export async function getFicha(id: string, topN = 12): Promise<Ficha | null> {
  const [detail, hood] = await Promise.all([
    apiJson<NodeDetail>(`/node/${encodeURIComponent(id)}`),
    apiJson<GraphData>(`/node/${encodeURIComponent(id)}/neighborhood?hops=1&limit=40`),
  ]);
  if (!detail) return null;
  const byId = new Map((hood?.nodes ?? []).map((n) => [n.id, n]));
  // One row per neighbor: keep its best-evidenced edge (count desc), then rank
  // neighbors the same way so the top of the page carries the strongest ties.
  const best = new Map<string, FichaRelation>();
  for (const l of hood?.links ?? []) {
    const src = typeof l.source === "string" ? l.source : l.source.id;
    const tgt = typeof l.target === "string" ? l.target : l.target.id;
    if (src !== id && tgt !== id) continue;
    const otherId = src === id ? tgt : src;
    const neighbor = byId.get(otherId);
    if (!neighbor) continue;
    const prev = best.get(otherId);
    if (!prev || (l.count ?? 0) > (prev.link.count ?? 0)) {
      best.set(otherId, { neighbor, link: l, outgoing: src === id });
    }
  }
  const relations = [...best.values()]
    .sort((a, b) => (b.link.count ?? 0) - (a.link.count ?? 0))
    .slice(0, topN);
  return { detail, relations };
}
