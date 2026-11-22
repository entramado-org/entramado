import type { GraphData, GraphLink, GraphNode } from "./types";
import { edgeStatusRank, STATUS_META } from "./graphStyle";
import { isCaseClusterId } from "./clustering";

/** Computed "Claves" over the currently-visible (filtered) graph. Pure and
 *  deterministic, like clustering.ts — recomputed via useMemo on data change.
 *  O(N+E); trivial at the ≤300-node / ~3000-link overview. */
export interface Insights {
  hubs: { node: GraphNode; degree: number }[];
  money: { link: GraphLink; source: GraphNode; target: GraphNode; amount: number }[];
  accused: { node: GraphNode; topStatus: string; topRank: number; count: number }[];
  bridges: { node: GraphNode; cases: string[] }[];
  /** Top sub-tramas (Louvain communities, spec 012/013) present in the view:
   *  label (principal members), size, the principal (highest-degree) member id
   *  to focus on, and the loaded member ids. */
  communities: { community: number; label: string; size: number; principalId: string; memberIds: string[] }[];
}

const TOP = 5;

function endId(e: string | GraphNode): string {
  return typeof e === "string" ? e : e.id;
}

/** Spanish status label for a rank (used when the edge had no explicit status). */
function statusForRank(rank: number): string {
  let best = "investigado";
  let bestRank = -1;
  for (const [s, m] of Object.entries(STATUS_META)) {
    if (m.rank <= rank && m.rank > bestRank) {
      best = s;
      bestRank = m.rank;
    }
  }
  return best;
}

export function computeInsights(data: GraphData, clusterOf: Map<string, string>): Insights {
  const byId = new Map(data.nodes.map((n) => [n.id, n]));

  // ── hubs: top by server degree (stable under view filters) ──
  const hubs = [...data.nodes]
    .filter((n) => (n.degree ?? 0) > 0)
    .sort((a, b) => (b.degree ?? 0) - (a.degree ?? 0))
    .slice(0, TOP)
    .map((node) => ({ node, degree: node.degree ?? 0 }));

  // ── money: top amount-bearing links ──
  const money = data.links
    .filter((l) => l.amount != null && l.amount > 0)
    .map((l) => ({
      link: l,
      source: byId.get(endId(l.source)),
      target: byId.get(endId(l.target)),
      amount: l.amount as number,
    }))
    .filter((m): m is Insights["money"][number] => !!m.source && !!m.target)
    .sort((a, b) => b.amount - a.amount)
    .slice(0, TOP);

  // ── accused: entities by incident accusation edges (status or accusation type) ──
  const accMap = new Map<string, { count: number; topRank: number; topStatus: string }>();
  for (const l of data.links) {
    const rank = edgeStatusRank(l.type, l.status);
    if (rank <= 0) continue;
    // The accused party is the SOURCE of ACCUSED_OF/CONVICTED_OF/INVESTIGATED_BY
    // (person → crime / court); status-bearing edges of other types implicate
    // their source too.
    const id = endId(l.source);
    const status = l.status && STATUS_META[l.status] ? l.status : statusForRank(rank);
    const prev = accMap.get(id);
    if (!prev) accMap.set(id, { count: 1, topRank: rank, topStatus: status });
    else {
      prev.count += 1;
      if (rank > prev.topRank) {
        prev.topRank = rank;
        prev.topStatus = status;
      }
    }
  }
  const accused = [...accMap.entries()]
    .map(([id, v]) => ({ node: byId.get(id), ...v }))
    .filter((a): a is Insights["accused"][number] & { node: GraphNode } => !!a.node)
    .sort((a, b) => b.topRank - a.topRank || b.count - a.count)
    .slice(0, TOP)
    .map(({ node, topStatus, topRank, count }) => ({ node, topStatus, topRank, count }));

  // ── bridges: nodes whose neighbors span ≥2 NAMED case clusters ──
  const neighborCases = new Map<string, Set<string>>();
  for (const l of data.links) {
    const s = endId(l.source);
    const t = endId(l.target);
    const cs = clusterOf.get(s);
    const ct = clusterOf.get(t);
    if (ct && isCaseClusterId(ct)) {
      if (!neighborCases.has(s)) neighborCases.set(s, new Set());
      neighborCases.get(s)!.add(ct);
    }
    if (cs && isCaseClusterId(cs)) {
      if (!neighborCases.has(t)) neighborCases.set(t, new Set());
      neighborCases.get(t)!.add(cs);
    }
  }
  const bridges = [...neighborCases.entries()]
    .filter(([, cases]) => cases.size >= 2)
    .map(([id, cases]) => ({ node: byId.get(id), cases: [...cases] }))
    .filter((b): b is Insights["bridges"][number] & { node: GraphNode } => !!b.node)
    // Rank by real betweenness (spec 012/013) when present; fall back to the
    // cross-case heuristic + degree.
    .sort((a, b) =>
      (b.node.betweenness ?? 0) - (a.node.betweenness ?? 0) ||
      b.cases.length - a.cases.length ||
      (b.node.degree ?? 0) - (a.node.degree ?? 0))
    .slice(0, TOP)
    .map(({ node, cases }) => ({ node, cases }));

  // ── communities: top sub-tramas by size (Louvain, spec 012/013) ──
  const commAcc = new Map<number, { size: number; label: string; memberIds: string[] }>();
  for (const n of data.nodes) {
    if (n.community == null) continue;
    const e = commAcc.get(n.community);
    if (e) { e.size++; e.memberIds.push(n.id); }
    else commAcc.set(n.community, {
      size: 1,
      label: n.community_label ?? `Comunidad ${n.community}`,
      memberIds: [n.id],
    });
  }
  const communities = [...commAcc.entries()]
    .map(([community, v]) => {
      const principalId = v.memberIds.reduce(
        (best, id) => ((byId.get(id)?.degree ?? 0) > (byId.get(best)?.degree ?? 0) ? id : best),
        v.memberIds[0],
      );
      return { community, ...v, principalId };
    })
    .sort((a, b) => b.size - a.size)
    .slice(0, TOP);

  return { hubs, money, accused, bridges, communities };
}
