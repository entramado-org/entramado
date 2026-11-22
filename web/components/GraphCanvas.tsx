"use client";
import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from "react";
import { forceCollide, forceX, forceY } from "d3-force";
import type { ClusterInfo, ColorMode, EdgeFilters, GraphData, GraphLink, GraphNode, LayoutMode, PairAgg, SelectedEdge } from "@/lib/types";
import { drawNodeShape, EDGE_NEUTRAL, isHubNode, nodeColor, nodeRadius, relColor, relLabel } from "@/lib/graphStyle";
import { isLinkHidden } from "@/lib/edgeVisibility";
import { CLUSTER_PALETTE, OTHER_ID } from "@/lib/clustering";
import EdgeTooltip from "./EdgeTooltip";

// NOTE: react-force-graph-2d is loaded via a client-only dynamic import held in
// state (below), NOT via next/dynamic. next/dynamic does not forward refs, which
// left fgRef null and silently disabled every imperative call (d3Force,
// centerAt, zoomToFit). Importing it ourselves and rendering it directly lets
// the ref attach to the real ForceGraph instance.

export interface GraphCanvasHandle {
  /** Smoothly center the camera on a node id, if it exists in the data. */
  centerOn: (id: string, zoom?: number) => void;
  /** Frame the camera around a specific node-id set (e.g. a found path). */
  fitNodes: (ids: Set<string>) => void;
}

/** Path-mode highlight: node ids + UNDIRECTED endpoint-pair keys
 *  (`min|max`) of the active evidence chains. Overrides hover/selection
 *  dimming while present. */
export interface PathHighlight {
  nodeIds: Set<string>;
  pairKeys: Set<string>;
}

interface Props {
  data: GraphData;
  selectedId: string | null;
  onSelect: (node: GraphNode) => void;
  /** Click/tap on an edge — opens the relation detail panel. Endpoint ids are
   *  normalized to strings here so the consumer never holds link object refs. */
  onLinkSelect?: (edge: SelectedEdge) => void;
  /** Tap/click on empty canvas — deselects (closes the detail panel). Lets
   *  touch users "go back" with a tap on the background, the natural gesture. */
  onDeselect?: () => void;
  /** Layout regime: cluster (default) / chrono / free. */
  mode?: LayoutMode;
  /** Node colour scheme — 'type' (default) or 'community' (spec 013). Orthogonal to mode. */
  colorBy?: ColorMode;
  /** node id → clusterId (cluster mode). */
  clusterOf?: Map<string, string>;
  /** cluster metadata incl. ring anchors + colour (cluster mode). */
  clusterInfo?: ClusterInfo[];
  /** Multiplier applied to every node radius (node-size control). */
  sizeScale?: number;
  /** Render-level edge filters (relation types / source layers / status / €). */
  edgeFilters?: EdgeFilters;
  /** Focus ring depth: 2 = also spotlight the relations of relations. */
  focusHops?: 1 | 2;
  /** Active "ruta entre entidades" highlight; null when no path is shown. */
  pathHighlight?: PathHighlight | null;
  /** Fraction (0..0.85) of the viewport height the mobile detail sheet covers
   *  from the bottom — drives how far the focused node is nudged up so it stays
   *  visible above the sheet. 0 = sheet collapsed/none; 0.5 = half (default). */
  sheetCoverFrac?: number;
}

const PULSE_PERIOD_MS = 1800;
// Horizontal spacing of the chronological layout, in graph units per year.
const PX_PER_YEAR = 170;
// When a node is focused, cap how much text the canvas draws. A 493-degree hub
// would otherwise label all 493 neighbours + edges into an unreadable wall.
const FOCUS_NEIGHBOR_LABELS = 16; // top neighbours (by degree) that get a label
const FOCUS_EDGE_LABELS = 20; // top incident edges (by evidence) that get relation text
const MONEY_EDGE_LABELS = 14; // top €-flows (by amount) labelled under money emphasis
const MONEY_GREEN = "#4ade80";
const COMMUNITY_LEGEND = 8;   // top-N communities named + coloured (rest = grey)
const COMMUNITY_GREY = "#5a6172"; // micro-clusters / unnamed communities
const BRIDGE_NODE_LABELS = 12; // top betweenness nodes labelled under bridge emphasis

// Compact es-ES money for edge labels ("450.000 €" → "450 mil €", "2,9 M€"…).
const EUR_FMT = new Intl.NumberFormat("es-ES", {
  style: "currency", currency: "EUR", notation: "compact", maximumFractionDigits: 1,
});

/** 0..1 alpha → 2-digit hex suffix for #rrggbb + aa colours. */
function a2(alpha: number): string {
  const v = Math.max(0, Math.min(255, Math.round(alpha * 255)));
  return v.toString(16).padStart(2, "0");
}

const GraphCanvas = forwardRef<GraphCanvasHandle, Props>(function GraphCanvas(
  { data, selectedId, onSelect, onLinkSelect, onDeselect, mode = "cluster", colorBy = "type", clusterOf, clusterInfo, sizeScale = 1, edgeFilters, focusHops = 1, pathHighlight = null, sheetCoverFrac = 0.5 },
  ref,
) {
  const chrono = mode === "chrono";
  const free = mode === "free";
  // Both "cluster" (cases) and "radial" (Personas) are anchor-pulled cluster
  // layouts — they share the same physics + halo rendering, differing only in
  // the clusterOf/clusterInfo passed in by GraphExplorer.
  const cluster = (mode === "cluster" || mode === "radial") && !!clusterOf && !!clusterInfo;
  const wrapRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
  const [FG, setFG] = useState<any>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });

  // Client-only import so the canvas component isn't server-rendered, while the
  // ref still attaches normally (unlike next/dynamic).
  useEffect(() => {
    let mounted = true;
    import("react-force-graph-2d").then((mod) => {
      if (mounted) setFG(() => mod.default);
    });
    return () => { mounted = false; };
  }, []);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [hoverLink, setHoverLink] = useState<GraphLink | null>(null);
  const [mouse, setMouse] = useState({ x: 0, y: 0 });
  const [pulseAnchor, setPulseAnchor] = useState<{ x: number; y: number; r: number } | null>(null);
  // Coarse pointer (touch) → larger node hit area so taps land reliably.
  const [coarse, setCoarse] = useState(false);
  useEffect(() => {
    setCoarse(typeof window !== "undefined" && !!window.matchMedia?.("(pointer: coarse)").matches);
  }, []);

  // --- size observer ---
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // --- representative year per node: the median year across all dated edges
  // that touch it. ~95% of nodes have at least one dated edge, so this anchors
  // almost the whole graph on the time axis; the rest float to the centre. ---
  const { nodeYear, yearDomain } = useMemo(() => {
    const acc = new Map<string, number[]>();
    const push = (id: string, y: number) => {
      const arr = acc.get(id);
      if (arr) arr.push(y);
      else acc.set(id, [y]);
    };
    for (const l of data.links) {
      const y = l.date_year;
      if (!y) continue;
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      push(s, y);
      push(t, y);
    }
    const ny = new Map<string, number>();
    let lo = Infinity, hi = -Infinity;
    for (const [id, ys] of acc) {
      ys.sort((a, b) => a - b);
      const med = ys[Math.floor(ys.length / 2)];
      ny.set(id, med);
      if (med < lo) lo = med;
      if (med > hi) hi = med;
    }
    if (!Number.isFinite(lo)) { lo = 2015; hi = 2026; }
    if (lo === hi) hi = lo + 1;
    return { nodeYear: ny, yearDomain: [lo, hi] as [number, number] };
  }, [data.links]);

  const yearToX = useCallback(
    (year: number) => (year - (yearDomain[0] + yearDomain[1]) / 2) * PX_PER_YEAR,
    [yearDomain],
  );

  // --- cluster lookups (cluster mode) ---
  const clusterById = useMemo(() => {
    const m = new Map<string, ClusterInfo>();
    for (const c of clusterInfo ?? []) m.set(c.id, c);
    return m;
  }, [clusterInfo]);

  const anchorOf = useCallback(
    (node: { id: string }) => clusterById.get(clusterOf?.get(node.id) ?? "")?.anchor ?? { x: 0, y: 0 },
    [clusterOf, clusterById],
  );

  const clusterColorOf = useCallback(
    (id: string) => clusterById.get(clusterOf?.get(id) ?? "")?.color ?? "#7e8696",
    [clusterOf, clusterById],
  );

  // --- per-node incident-link count, parallels included (free-mode springs).
  // Replicates d3-force's DEFAULT link strength 1/min(count(s), count(t)),
  // which the cluster/chrono modes clobber with flat .strength() values:
  // leaves get a full-strength spring and sit tight by their hub (Obsidian's
  // dandelion fans) while hub–hub springs go weak, letting repulsion pull the
  // big names apart. A flat strength did the opposite — every leaf drifted to
  // a fuzzy ring where its weak spring balanced the core's repulsion. ---
  const linkCountOf = useMemo(() => {
    const m = new Map<string, number>();
    for (const l of data.links) {
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      m.set(s, (m.get(s) ?? 0) + 1);
      m.set(t, (m.get(t) ?? 0) + 1);
    }
    return m;
  }, [data.links]);

  // --- physics. Three regimes:
  //   • cluster (default): each node is pulled to its case's ring anchor by a
  //     DOMINANT forceX/forceY while link strength is crushed to ~nothing, so the
  //     ~3000 inter-case links can't smear the regions back into a hairball.
  //     Positions are seeded to the anchor on first sight so the first paint is
  //     already clustered. Charge + collision spread members *within* a region.
  //   • chronological: each node's X is HARD-PINNED (fx) to its year column; Y
  //     free. Pinning beats a soft forceX because 3000 links overpower it.
  //   • free-form: aggressive charge + collision so hubs breathe.
  //   Re-runs on data change (re-seed merged nodes) AND when the regime flips.
  const applyForces = useCallback(() => {
    const fg = fgRef.current;
    if (!fg || typeof fg.d3Force !== "function") return false;
    fg.d3Force("collide", forceCollide((n: any) => nodeRadius(n.degree) * sizeScale + 6).iterations(2));
    if (cluster) {
      let i = 0;
      for (const n of data.nodes as any[]) {
        n.fx = undefined;
        n.fy = undefined;
        if (!Number.isFinite(n.x) || !Number.isFinite(n.y)) {
          // deterministic golden-angle jitter so members don't stack on the anchor
          const a = anchorOf(n);
          const ang = i * 2.399963;
          const rad = 16 + (i % 9) * 6;
          n.x = a.x + Math.cos(ang) * rad;
          n.y = a.y + Math.sin(ang) * rad;
        }
        i++;
      }
      fg.d3Force("x", forceX((n: any) => anchorOf(n).x).strength(0.9));
      fg.d3Force("y", forceY((n: any) => anchorOf(n).y).strength(0.9));
      fg.d3Force("link")?.distance(34).strength(0.02);
      fg.d3Force("charge")?.strength((n: any) => -24 - nodeRadius(n.degree) * 4).distanceMax(120);
      fg.d3Force("center")?.strength(0);
      fg.d3VelocityDecay?.(0.45);
    } else if (chrono) {
      fg.d3Force("x", null);
      fg.d3Force("y", null);
      // Pin X to the year column AND set node.x directly, so the position
      // jumps immediately even if the cooled engine doesn't re-tick.
      for (const n of data.nodes as any[]) {
        if (nodeYear.has(n.id)) {
          const x = yearToX(nodeYear.get(n.id)!);
          n.fx = x;
          n.x = x;
        } else {
          n.fx = undefined;
        }
        n.fy = undefined;
      }
      fg.d3Force("link")?.distance(46).strength(0.08);
      fg.d3Force("charge")?.strength((n: any) => -70 - nodeRadius(n.degree) * 8).distanceMax(260);
      fg.d3Force("center")?.strength(0);
      fg.d3VelocityDecay?.(0.32);
    } else {
      // Free mode, tuned to feel like Obsidian's graph: degree-weighted springs
      // (d3's default — see linkCountOf), moderate repulsion, and a weak central
      // gravity so the constellation stays framed.
      for (const n of data.nodes as any[]) { n.fx = undefined; n.fy = undefined; }
      fg.d3Force("x", forceX(0).strength(0.05));
      fg.d3Force("y", forceY(0).strength(0.05));
      fg.d3Force("link")?.distance(36).strength((l: any) => {
        const s = typeof l.source === "object" ? l.source.id : l.source;
        const t = typeof l.target === "object" ? l.target.id : l.target;
        return 1 / Math.max(1, Math.min(linkCountOf.get(s) ?? 1, linkCountOf.get(t) ?? 1));
      });
      fg.d3Force("charge")?.strength((n: any) => -90 - nodeRadius(n.degree) * 10).distanceMax(700);
      fg.d3Force("center")?.strength(0);
      fg.d3VelocityDecay?.(0.28);
    }
    fg.d3ReheatSimulation?.();
    return true;
  }, [data.nodes, cluster, chrono, nodeYear, yearToX, sizeScale, anchorOf, linkCountOf]);

  // The ForceGraph2D is a dynamic (ssr:false) import that mounts AFTER this
  // component, so fgRef.current is often still null on the first data-load run —
  // which is why the forces (collision, year-pinning) never took and the graph
  // stayed a clump. Retry on rAF until the instance exists, then apply.
  useEffect(() => {
    if (applyForces()) return;
    let raf = 0;
    let tries = 0;
    const tick = () => {
      if (applyForces() || tries++ > 180) return;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [applyForces]);

  // --- reframe the camera when the layout regime flips, so the change is
  // immediately visible (the whole timeline / hairball comes into view). ---
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    const id = setTimeout(() => fg.zoomToFit?.(600, 70), 500);
    return () => clearTimeout(id);
  }, [mode]);

  // --- adjacency (used for focus dimming) ---
  const adjacency = useMemo(() => {
    const m = new Map<string, Set<string>>();
    for (const l of data.links) {
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      if (!m.has(s)) m.set(s, new Set());
      if (!m.has(t)) m.set(t, new Set());
      m.get(s)!.add(t);
      m.get(t)!.add(s);
    }
    return m;
  }, [data.links]);

  // --- per-link curvature: fan out multiple edges between the same node pair
  // so different relation types (e.g. PAID + ASSOCIATED_WITH) don't draw on top
  // of one another. A lone edge stays straight (0). ---
  const curvature = useMemo(() => {
    const groups = new Map<string, GraphLink[]>();
    for (const l of data.links) {
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      const key = s < t ? `${s}|${t}` : `${t}|${s}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(l);
    }
    const m = new Map<GraphLink, number>();
    for (const group of groups.values()) {
      const n = group.length;
      group.forEach((l, i) => m.set(l, n === 1 ? 0 : (i - (n - 1) / 2) * 0.28));
    }
    return m;
  }, [data.links]);

  const focusId = hoverId ?? selectedId;

  // --- hop distance from the focused node (only when depth 2 is active). A
  // bounded BFS over the adjacency map, recomputed per focus/data change —
  // NEVER per frame. Null at depth 1, so every depth-1 code path below is
  // byte-identical to the original behaviour (regression criterion A3). ---
  const hopOf = useMemo(() => {
    if (!focusId || focusHops < 2) return null;
    const m = new Map<string, number>([[focusId, 0]]);
    let frontier = [focusId];
    for (let d = 1; d <= focusHops; d++) {
      const next: string[] = [];
      for (const id of frontier) {
        adjacency.get(id)?.forEach((nb) => {
          if (!m.has(nb)) {
            m.set(nb, d);
            next.push(nb);
          }
        });
      }
      frontier = next;
    }
    return m;
  }, [focusId, focusHops, adjacency]);

  const highlighted = useMemo(() => {
    if (!focusId) return null;
    if (hopOf) return new Set(hopOf.keys()); // depth 2: include the second ring
    const set = new Set<string>([focusId]);
    adjacency.get(focusId)?.forEach((id) => set.add(id));
    return set;
  }, [focusId, adjacency, hopOf]);

  // --- render-level edge filters: one O(links) pass per filter/data change,
  // then O(1) Map lookups inside the per-frame link callbacks. ---
  const linkHidden = useMemo(() => {
    const m = new Map<GraphLink, boolean>();
    if (!edgeFilters) return m;
    const { hiddenRels, hiddenKinds, minStatusRank } = edgeFilters;
    if (hiddenRels.size === 0 && hiddenKinds.size === 0 && minStatusRank === 0) return m;
    // isLinkHidden is the shared predicate (also drives orphan hiding, spec 019).
    for (const l of data.links) if (isLinkHidden(l, edgeFilters)) m.set(l, true);
    return m;
  }, [data.links, edgeFilters]);

  // Single active emphasis (mutually exclusive) — spec 013. `moneyEmphasis` is kept
  // as a derived boolean so the €-rendering paths below stay byte-identical.
  const emphasis = edgeFilters?.emphasis ?? "none";
  const moneyEmphasis = emphasis === "money";
  const bridgeEmphasis = emphasis === "bridges";

  // Money-emphasis width scale: normalize by the largest visible amount.
  const moneyMaxLog = useMemo(() => {
    if (!moneyEmphasis) return 1;
    let max = 0;
    for (const l of data.links) {
      if (l.amount != null && l.amount > 0) max = Math.max(max, Math.log10(1 + l.amount));
    }
    return max || 1;
  }, [data.links, moneyEmphasis]);

  // Top money flows get an € label (precomputed Set, bounded — never a per-frame
  // scan over all edges). The rest stay unlabelled lines; hover has the detail.
  const moneyTopLabels = useMemo(() => {
    if (!moneyEmphasis) return null;
    const withAmount = data.links.filter((l) => l.amount != null && l.amount > 0 && !linkHidden.get(l));
    withAmount.sort((a, b) => (b.amount ?? 0) - (a.amount ?? 0));
    return new Set(withAmount.slice(0, MONEY_EDGE_LABELS));
  }, [moneyEmphasis, data.links, linkHidden]);

  // --- Community colouring (spec 013): top-N communities present in the visible
  // nodes get a stable palette colour + a legend entry; the long tail of
  // micro-clusters (mostly degree-1 leaves) stays neutral grey. Derived from the
  // already-loaded payload — no extra request. `community_label` (top members,
  // spec 012) is the legend text.
  const communityInfo = useMemo(() => {
    if (colorBy !== "community") return null;
    const acc = new Map<number, { size: number; label: string }>();
    for (const n of data.nodes) {
      if (n.community == null) continue;
      const e = acc.get(n.community);
      if (e) e.size++;
      else acc.set(n.community, { size: 1, label: n.community_label ?? `Comunidad ${n.community}` });
    }
    const top = [...acc.entries()].sort((a, b) => b[1].size - a[1].size).slice(0, COMMUNITY_LEGEND);
    const colorOf = new Map<number, string>();
    const legend = top.map(([cid, info], i) => {
      const color = CLUSTER_PALETTE[i % CLUSTER_PALETTE.length];
      colorOf.set(cid, color);
      return { community: cid, label: info.label, color, size: info.size };
    });
    return { legend, colorOf };
  }, [colorBy, data.nodes]);
  const communityColor = (c: number | null | undefined) =>
    c != null ? communityInfo?.colorOf.get(c) ?? COMMUNITY_GREY : COMMUNITY_GREY;

  // --- Bridge emphasis (spec 013): the top-K nodes by betweenness get the
  // spotlight (label + full size/alpha); the rest are attenuated. RANK, not a
  // magnitude ramp (lesson from money emphasis). Bounded Set, memoized — never a
  // per-frame scan.
  const bridgeTop = useMemo(() => {
    if (!bridgeEmphasis) return null;
    const withBet = data.nodes.filter((n) => (n.betweenness ?? 0) > 0);
    withBet.sort((a, b) => (b.betweenness ?? 0) - (a.betweenness ?? 0));
    return new Set(withBet.slice(0, BRIDGE_NODE_LABELS).map((n) => n.id));
  }, [bridgeEmphasis, data.nodes]);

  // --- Per-pair edge collapse: at rest, draw ONE line per undirected node pair
  // and make the other parallel edges invisible, aggregating the pair's
  // relations onto a representative (for the hover tooltip-list + a thicker
  // line). Computed over VISIBLE links only, so a hidden layer drops out of the
  // aggregate. Only consulted in the resting overview (not focus/path/money) —
  // there the fanned, colour-coded per-type edges stay informative. ---
  const pairOf = useMemo(() => {
    const groups = new Map<string, GraphLink[]>();
    for (const l of data.links) {
      if (linkHidden.get(l)) continue;
      const s = typeof l.source === "string" ? l.source : (l.source as GraphNode).id;
      const t = typeof l.target === "string" ? l.target : (l.target as GraphNode).id;
      const key = s < t ? `${s}|${t}` : `${t}|${s}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(l);
    }
    const m = new Map<GraphLink, PairAgg>();
    for (const links of groups.values()) {
      if (links.length < 2) continue; // a lone edge needs no collapse
      const rep = links.reduce((a, b) => ((b.count ?? 1) > (a.count ?? 1) ? b : a), links[0]);
      const types = links
        .map((l) => ({ type: l.type, count: l.count ?? 1, source_kind: l.source_kind, status: l.status ?? null, amount: l.amount ?? null }))
        .sort((a, b) => b.count - a.count);
      const agg: PairAgg = {
        repLink: rep,
        count: links.reduce((s, l) => s + (l.count ?? 1), 0),
        amount: links.reduce((s, l) => s + (l.amount ?? 0), 0),
        mixed: new Set(links.map((l) => l.type)).size > 1,
        types,
      };
      for (const l of links) m.set(l, agg);
    }
    return m;
  }, [data.links, linkHidden]);

  // --- path-mode link set: links whose endpoints form a consecutive pair in
  // the active path(s). Matched by undirected pair so every parallel relation
  // between two hop endpoints lights up (mirrors the evidence list). ---
  const pathLinks = useMemo(() => {
    const set = new Set<GraphLink>();
    if (!pathHighlight) return set;
    for (const l of data.links) {
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      const k = s < t ? `${s}|${t}` : `${t}|${s}`;
      if (pathHighlight.pairKeys.has(k)) set.add(l);
    }
    return set;
  }, [data.links, pathHighlight]);

  const nodeDeg = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of data.nodes) m.set(n.id, n.degree ?? 0);
    return m;
  }, [data.nodes]);

  // Focusing a high-degree node must NOT label every neighbour/edge — that's the
  // "wall of text" explosion. Cap to the strongest few: top neighbours by degree
  // get node labels, top incident edges by evidence count get relation text. The
  // rest still draw as coloured lines/dots, just unlabelled. (The full list lives
  // in the detail panel's Conexiones tab.)
  const focusLabelNodes = useMemo(() => {
    if (!focusId) return null;
    const nbrs = [...(adjacency.get(focusId) ?? [])];
    nbrs.sort((a, b) => (nodeDeg.get(b) ?? 0) - (nodeDeg.get(a) ?? 0));
    const set = new Set<string>(nbrs.slice(0, FOCUS_NEIGHBOR_LABELS));
    set.add(focusId);
    return set;
  }, [focusId, adjacency, nodeDeg]);

  const focusEdgeLabels = useMemo(() => {
    if (!focusId) return null;
    const inc: GraphLink[] = [];
    for (const l of data.links) {
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      if (s === focusId || t === focusId) inc.push(l);
    }
    inc.sort((a, b) => (b.count ?? 1) - (a.count ?? 1));
    return new Set(inc.slice(0, FOCUS_EDGE_LABELS));
  }, [focusId, data.links]);

  // --- persistent-label set. In cluster mode: top-K by degree PER cluster, so
  // every region keeps a few named anchors regardless of absolute degree, plus a
  // global floor of the very top hubs. Otherwise: the classic global hub set. ---
  const labelIds = useMemo(() => {
    const byDeg = [...data.nodes].sort((a, b) => (b.degree ?? 0) - (a.degree ?? 0));
    const set = new Set<string>();
    if (cluster && clusterOf) {
      const K = 3;
      const perCluster = new Map<string, number>();
      for (const n of byDeg) {
        if (set.size >= 24) break;
        const cid = clusterOf.get(n.id) ?? "";
        const c = perCluster.get(cid) ?? 0;
        if (c < K) { set.add(n.id); perCluster.set(cid, c + 1); }
      }
      for (const n of byDeg.slice(0, 8)) set.add(n.id); // global floor
    } else {
      for (const n of byDeg.slice(0, 10)) if (isHubNode(n.degree)) set.add(n.id);
    }
    return set;
  }, [data.nodes, cluster, clusterOf]);

  // --- edge gating (cluster mode): which links stay within one cluster. Built
  // once per data/cluster change (NOT per frame) so the link callbacks stay O(1)
  // lookups. Inter-cluster edges are hidden in the unfocused overview — the
  // single biggest hairball-killer; they reappear when a node is focused. ---
  const isIntra = useMemo(() => {
    const m = new Map<GraphLink, boolean>();
    if (!clusterOf) return m;
    for (const l of data.links) {
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      m.set(l, clusterOf.get(s) === clusterOf.get(t));
    }
    return m;
  }, [data.links, clusterOf]);

  // Center the camera on a node. On mobile the detail bottom-sheet covers the
  // lower part of the screen, so nudge the node into the still-visible upper
  // area (its relations would otherwise sit hidden behind the panel). The nudge
  // tracks the LIVE sheet coverage (peek≈0 → half≈0.5 → full): at half it
  // reproduces the previous fixed 0.22·h offset.
  const focusCamera = useCallback(
    (node: any, zoom: number) => {
      const fg = fgRef.current;
      if (!fg || !Number.isFinite(node.x) || !Number.isFinite(node.y)) return;
      const mobile = typeof window !== "undefined" && window.innerWidth < 768;
      const cover = Math.max(0, Math.min(0.85, sheetCoverFrac));
      const offY = mobile && zoom > 0 ? (cover * 0.44 * size.h) / zoom : 0;
      fg.centerAt(node.x, node.y + offY, 700);
      fg.zoom(zoom, 700);
    },
    [size.h, sheetCoverFrac],
  );

  // --- camera control: smoothly center on selection changes ---
  useEffect(() => {
    if (!selectedId || !fgRef.current) return;
    const node = data.nodes.find((n) => n.id === selectedId) as any;
    if (!node || !Number.isFinite(node.x) || !Number.isFinite(node.y)) return;
    focusCamera(node, Math.max(fgRef.current.zoom?.() ?? 1.6, 1.6));
  }, [selectedId, data.nodes, focusCamera]);

  useImperativeHandle(ref, () => ({
    centerOn(id, zoom = 1.8) {
      const node = data.nodes.find((n) => n.id === id) as any;
      if (!node || !fgRef.current) return;
      focusCamera(node, zoom);
    },
    fitNodes(ids) {
      // Frame the camera around a node subset (e.g. a found path).
      fgRef.current?.zoomToFit?.(600, 80, (n: any) => ids.has(n.id));
    },
  }));

  // --- pulse-ring overlay: track the selected node's screen position via RAF ---
  useEffect(() => {
    if (!selectedId) {
      setPulseAnchor(null);
      return;
    }
    let raf = 0;
    const tick = () => {
      const node = data.nodes.find((n) => n.id === selectedId) as any;
      if (node && fgRef.current && Number.isFinite(node.x) && Number.isFinite(node.y)) {
        const p = fgRef.current.graph2ScreenCoords(node.x, node.y);
        if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) {
          setPulseAnchor({
            x: p.x,
            y: p.y,
            r: nodeRadius(node.degree) * sizeScale * (fgRef.current.zoom?.() ?? 1),
          });
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [selectedId, data.nodes]);

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    setMouse({ x: e.clientX - rect.left, y: e.clientY - rect.top });
  }, []);

  // Tap/click selects (parent handler). A second tap on the SAME node within
  // 300ms is a double-tap → zoom the camera in on it. Single-tap behaviour is
  // unchanged (the first tap of a double still selects); works for mouse + touch.
  const lastTap = useRef<{ id: string; t: number }>({ id: "", t: 0 });
  const handleNodeClick = useCallback(
    (n: any) => {
      const now = Date.now();
      if (lastTap.current.id === n.id && now - lastTap.current.t < 300) {
        const z = fgRef.current?.zoom?.() ?? 1.6;
        focusCamera(n, Math.min(z * 1.8, 8));
      }
      lastTap.current = { id: n.id, t: now };
      onSelect(n);
    },
    [onSelect, focusCamera],
  );

  return (
    <div ref={wrapRef} onMouseMove={onMouseMove} className="relative h-full w-full grid-bg touch-none-canvas">
      {/* radial spotlight when something is selected — adds spatial calm */}
      {selectedId && pulseAnchor && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            background: `radial-gradient(620px circle at ${pulseAnchor.x}px ${pulseAnchor.y}px,
                          transparent 0%, transparent 40%, rgba(7,10,18,0.55) 100%)`,
            transition: "background 280ms ease-out",
          }}
        />
      )}

      {FG && (
      /* @ts-ignore dynamic component typing */
      <FG
        ref={fgRef}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        nodeId="id"
        cooldownTicks={160}
        warmupTicks={20}
        d3VelocityDecay={0.32}
        // On touch the node hit area is floored at ~22px so taps land reliably,
        // but with node-drag enabled that same generous radius means a one-finger
        // pan or a pinch-zoom almost always grabs a node and flings it instead of
        // moving the camera ("salta a los otros nodos"). Disabling drag on coarse
        // pointers makes every gesture over the canvas pan/zoom; taps still select.
        enableNodeDrag={!coarse}
        // A click/tap on empty canvas deselects (closes the detail panel) — the
        // natural "go back" gesture on touch, where there's no Escape key.
        onBackgroundClick={() => {
          setHoverLink(null);
          onDeselect?.();
        }}
        // ---------- backdrops (drawn behind nodes/links) ----------
        // Cluster mode: faint case "regions" + labels, computed from the live
        // node positions. O(clusters)≈6, never per-edge. Chrono mode: year axis.
        onRenderFramePre={(ctx: CanvasRenderingContext2D, globalScale: number) => {
          const fg = fgRef.current;
          if (!fg) return;

          if (cluster && clusterInfo && clusterOf) {
            // One O(nodes) pass for centroids, a second for radial spread.
            const acc = new Map<string, { sx: number; sy: number; n: number }>();
            for (const node of data.nodes as any[]) {
              if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) continue;
              const cid = clusterOf.get(node.id);
              if (!cid) continue;
              let a = acc.get(cid);
              if (!a) acc.set(cid, (a = { sx: 0, sy: 0, n: 0 }));
              a.sx += node.x; a.sy += node.y; a.n++;
            }
            const centroid = new Map<string, { cx: number; cy: number }>();
            for (const [cid, a] of acc) centroid.set(cid, { cx: a.sx / a.n, cy: a.sy / a.n });
            const spread = new Map<string, number>();
            for (const node of data.nodes as any[]) {
              if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) continue;
              const cid = clusterOf.get(node.id);
              const c = cid ? centroid.get(cid) : undefined;
              if (!c) continue;
              const d = Math.hypot(node.x - c.cx, node.y - c.cy);
              spread.set(cid!, Math.max(spread.get(cid!) ?? 0, d));
            }
            ctx.save();
            // Working list of visible clusters with a finite centroid.
            const blobs: { info: ClusterInfo; cx: number; cy: number; rad: number }[] = [];
            for (const info of clusterInfo) {
              if (info.id === OTHER_ID) continue; // the remainder gets no backdrop
              const c = centroid.get(info.id);
              if (!c || !Number.isFinite(c.cx) || !Number.isFinite(c.cy)) continue;
              const rad = (spread.get(info.id) ?? 0) + 46;
              if (!Number.isFinite(rad) || rad <= 0) continue;
              blobs.push({ info, cx: c.cx, cy: c.cy, rad });
            }

            // Pass 1 — paint every cluster halo first, so no halo can later cover a title.
            // Quiet washes (~9%): with ~19 groups in the radial view, stronger
            // halos compete with the data instead of grounding it.
            for (const b of blobs) {
              const grad = ctx.createRadialGradient(b.cx, b.cy, 0, b.cx, b.cy, b.rad);
              grad.addColorStop(0, b.info.color + "17");
              grad.addColorStop(0.72, b.info.color + "0c");
              grad.addColorStop(1, b.info.color + "00");
              ctx.fillStyle = grad;
              ctx.beginPath();
              ctx.arc(b.cx, b.cy, b.rad, 0, 2 * Math.PI);
              ctx.fill();
            }

            // Pass 2 — place every title radially OUTWARD from the graph centre
            // (into empty space outside the ring), then nudge apart any that still
            // collide, then draw. O(clusters^2) ≈ 36, never per-edge.
            const fontSize = 12 / globalScale;
            ctx.font = `700 ${fontSize}px ui-sans-serif, system-ui`;
            const padY = fontSize * 0.45;
            const lineH = fontSize + padY * 2;
            type Title = { text: string; x: number; y: number; halfW: number; color: string };
            const titles: Title[] = [];
            for (const b of blobs) {
              const len = Math.hypot(b.cx, b.cy);
              const dirX = len > 1 ? b.cx / len : 0;
              const dirY = len > 1 ? b.cy / len : -1;
              const pad = b.rad + 10 / globalScale;
              const text = b.info.label.toUpperCase();
              const halfW = ctx.measureText(text).width / 2 + fontSize * 0.5;
              titles.push({ text, x: b.cx + dirX * pad, y: b.cy + dirY * pad, halfW, color: b.info.color });
            }
            // Greedy de-overlap: process top-to-bottom, push later boxes down off earlier ones.
            titles.sort((a, b) => a.y - b.y);
            for (let i = 0; i < titles.length; i++) {
              for (let j = 0; j < i; j++) {
                const a = titles[j], t = titles[i];
                if (Math.abs(t.y - a.y) < lineH && Math.abs(t.x - a.x) < a.halfW + t.halfW) {
                  t.y = a.y + lineH; // shift below the already-placed neighbour
                }
              }
            }
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            for (const t of titles) {
              const w = t.halfW * 2;
              const r = lineH / 2;
              const left = t.x - t.halfW, top = t.y - lineH / 2;
              ctx.fillStyle = "rgba(8,11,20,0.55)";
              ctx.beginPath();
              (ctx as any).roundRect?.(left, top, w, lineH, r);
              if (!(ctx as any).roundRect) ctx.rect(left, top, w, lineH);
              ctx.fill();
              ctx.fillStyle = "rgba(226,232,240,0.66)";
              ctx.fillText(t.text, t.x, t.y);
            }
            ctx.restore();
            return;
          }

          if (!chrono) return;
          const tl = fg.screen2GraphCoords(0, 0);
          const br = fg.screen2GraphCoords(size.w, size.h);
          if (!tl || !br || ![tl.x, tl.y, br.x, br.y].every(Number.isFinite)) return;
          ctx.save();
          ctx.lineWidth = 1 / globalScale;
          ctx.font = `600 ${13 / globalScale}px ui-sans-serif, system-ui`;
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
          for (let y = yearDomain[0]; y <= yearDomain[1]; y++) {
            const x = yearToX(y);
            if (x < tl.x - 40 || x > br.x + 40) continue;
            ctx.strokeStyle = "rgba(148,163,184,0.07)";
            ctx.beginPath();
            ctx.moveTo(x, tl.y);
            ctx.lineTo(x, br.y);
            ctx.stroke();
            ctx.fillStyle = "rgba(148,163,184,0.40)";
            ctx.fillText(String(y), x, br.y - 8 / globalScale);
          }
          ctx.restore();
        }}
        // ---------- links ----------
        // Color edges by relation type so the GRAPH itself communicates the
        // nature of each connection — green for money, red for accusations,
        // teal for plain ties, etc. Without this the canvas reads as
        // monochrome lines and you can't tell PAID from ASSOCIATED_WITH.
        // Context edges (curated public background, not judicial facts) render
        // as a muted neutral grey + dashed (see linkLineDash) so they're clearly
        // distinct from document-sourced relations.
        linkColor={(l: any) => {
          const s = typeof l.source === "object" ? l.source.id : l.source;
          const t = typeof l.target === "object" ? l.target.id : l.target;
          // Render-level filters: hidden edges cost nothing to "draw".
          if (linkHidden.get(l)) return "#00000000";
          // Per-pair collapse (resting overview only): every parallel edge of a
          // pair except the representative is drawn invisible — one line per pair.
          if (!highlighted && !pathHighlight && !moneyEmphasis) {
            const agg = pairOf.get(l);
            if (agg && agg.repLink !== l) return "#00000000";
          }
          // Path mode overrides everything: the chain pops, the rest recedes.
          if (pathHighlight) {
            if (pathLinks.has(l)) return relColor(l.type) + "e6";
            if (cluster && !isIntra.get(l)) return EDGE_NEUTRAL + "00";
            return EDGE_NEUTRAL + "0a";
          }
          // Money emphasis: €-edges in green with intensity ∝ log-importe (the
          // big flows pop, the small ones whisper) — over the NORMAL skeleton,
          // just dimmed. It used to fade everything else to ~8%, so every
          // visible line was green and the graph structure disappeared.
          if (moneyEmphasis) {
            if (l.amount != null && l.amount > 0) {
              if (highlighted) return highlighted.has(s) && highlighted.has(t) ? MONEY_GREEN + "e6" : MONEY_GREEN + "33";
              // Rank-based hierarchy: the labelled top flows go solid; the other
              // ~300 documented amounts stay a faint web. (A magnitude ramp
              // doesn't work here — the log scale compresses 700 € vs 5 M€ into
              // too narrow a band and everything reads equally green.)
              if (moneyTopLabels?.has(l)) return MONEY_GREEN + "e0";
              const k = Math.log10(1 + l.amount) / moneyMaxLog; // 0..1
              return MONEY_GREEN + a2(0.08 + 0.22 * k * k);
            }
            if (cluster && !highlighted) {
              if (!isIntra.get(l)) return EDGE_NEUTRAL + "00";
              return EDGE_NEUTRAL + a2(Math.min(0.3, 0.08 + Math.log2(1 + (l.count ?? 1)) * 0.04));
            }
            return EDGE_NEUTRAL + "26";
          }
          if (!highlighted) {
            // Cluster mode: hide inter-cluster edges entirely (the hairball is
            // mostly cross-case ties), draw intra-cluster edges in calm neutral
            // ink with opacity scaled by evidence weight.
            if (cluster) {
              if (!isIntra.get(l)) return EDGE_NEUTRAL + "00";
              return EDGE_NEUTRAL + a2(Math.min(0.5, 0.12 + Math.log2(1 + (l.count ?? 1)) * 0.06));
            }
            // Free mode at rest: uniform faint threads, Obsidian-style — the
            // structure reads from the layout, not from 3000 coloured lines.
            // Relation colours still light up on focus (below).
            if (free) return EDGE_NEUTRAL + a2(Math.min(0.22, 0.1 + Math.log2(1 + (l.count ?? 1)) * 0.025));
            // Other modes: relation-coloured baseline, bright enough to read.
            // A collapsed multi-type pair reads neutral (no single type "wins").
            const aggC = pairOf.get(l);
            const base = aggC?.mixed
              ? EDGE_NEUTRAL
              : l.source_kind === "ultima_hora" ? "#fb923c" : l.context ? "#94a3b8" : l.source_kind === "prensa" ? "#bfa06a" : relColor(l.type);
            return base + "59"; // ~35% opacity hex suffix
          }
          // Focused subgraph: relation colours light up (incl. cross-cluster
          // edges of the focused node — how you discover inter-case links). The
          // strongest ties pop; the rest of a hub's edges recede so the view
          // stays legible even at degree 493.
          const base = l.source_kind === "ultima_hora" ? "#fb923c" : l.context ? "#94a3b8" : l.source_kind === "prensa" ? "#bfa06a" : relColor(l.type);
          const onFocus = highlighted.has(s) && highlighted.has(t);
          if (!onFocus) return base + "0a"; // ~4%
          // Depth 2: second-ring edges stay dimmer so the direct ring dominates.
          if (hopOf && Math.max(hopOf.get(s) ?? 9, hopOf.get(t) ?? 9) >= 2) return base + "30";
          return focusEdgeLabels?.has(l) ? base + "e6" : base + "55"; // 90% vs ~33%
        }}
        linkLineDash={(l: any) =>
          l.source_kind === "prensa" ? [1.5, 3] : l.source_kind === "ultima_hora" ? [5, 3] : l.context ? [4, 3] : null
        }
        linkWidth={(l: any) => {
          if (linkHidden.get(l)) return 0;
          // Per-pair collapse (resting overview): hide non-representatives, and
          // size the representative by the pair's TOTAL evidence count.
          const collapse = !highlighted && !pathHighlight && !moneyEmphasis;
          const agg = collapse ? pairOf.get(l) : undefined;
          if (agg && agg.repLink !== l) return 0;
          const cnt = agg ? agg.count : (l.count ?? 1);
          const base = 0.9 + Math.log2(1 + cnt) * 0.6;
          if (pathHighlight) return pathLinks.has(l) ? base + 1.2 : 0.3;
          if (moneyEmphasis) {
            if (l.amount != null && l.amount > 0) {
              const k = Math.log10(1 + l.amount) / moneyMaxLog; // 0..1
              return 0.7 + 4 * k * k;
            }
            if (cluster && !highlighted && !isIntra.get(l)) return 0;
            return 0.6;
          }
          if (!highlighted) {
            if (cluster && !isIntra.get(l)) return 0; // hidden inter-cluster
            if (free) return Math.min(1.3, 0.5 + Math.log2(1 + cnt) * 0.2); // thin Obsidian threads
            return base;
          }
          const s = typeof l.source === "object" ? l.source.id : l.source;
          const t = typeof l.target === "object" ? l.target.id : l.target;
          if (!(highlighted.has(s) && highlighted.has(t))) return 0.3;
          if (hopOf && Math.max(hopOf.get(s) ?? 9, hopOf.get(t) ?? 9) >= 2) return base * 0.5;
          return focusEdgeLabels?.has(l) ? base + 1.2 : base * 0.7;
        }}
        // PERF: only curve / arrow / label edges when a node is focused. At the
        // 3000-link overview, bezier curves + per-link arrows + a per-link canvas
        // callback every frame is what made it crawl. With nothing focused we
        // draw plain straight colored lines (cheap); the richness kicks in only
        // for the small focused subgraph you're actually looking at.
        linkCurvature={(l: any) => {
          if (pathHighlight) return pathLinks.has(l) ? curvature.get(l) ?? 0 : 0;
          if (!highlighted) return 0;
          const s = typeof l.source === "object" ? l.source.id : l.source;
          const t = typeof l.target === "object" ? l.target.id : l.target;
          return highlighted.has(s) && highlighted.has(t) ? curvature.get(l) ?? 0 : 0;
        }}
        linkDirectionalArrowLength={(l: any) => {
          if (pathHighlight) return pathLinks.has(l) ? 5 : 0;
          // Only the labelled top-edges get arrowheads — 493 arrows is noise.
          if (!highlighted) return 0;
          return focusEdgeLabels?.has(l) ? 5 : 0;
        }}
        linkDirectionalArrowRelPos={0.92}
        linkDirectionalArrowColor={(l: any) => relColor(l.type) + "cc"}
        onLinkHover={(l: any) => setHoverLink(l ?? null)}
        // Click/tap opens the relation detail panel (the hover chip stays as the
        // lightweight desktop preview). This is also the only edge affordance on
        // touch, where hover doesn't exist (B5).
        onLinkClick={(l: any) => {
          setHoverLink(null);
          if (!l || !onLinkSelect) return;
          const src = l.source, tgt = l.target;
          // At rest a clicked line is a collapsed PAIR → omit `type` so the panel
          // lists every relation between the two. Focused/path/money clicks keep
          // the single relation type (the fanned, colour-coded edges).
          const collapsed = !highlighted && !pathHighlight && !moneyEmphasis && pairOf.has(l);
          onLinkSelect({
            sourceId: typeof src === "object" ? src.id : src,
            sourceName: typeof src === "object" ? src.name : undefined,
            targetId: typeof tgt === "object" ? tgt.id : tgt,
            targetName: typeof tgt === "object" ? tgt.name : undefined,
            type: collapsed ? undefined : l.type,
          });
        }}
        // Relation-name labels: only drawn when a node is focused OR a path is
        // highlighted, and only for those edges. When neither is active these
        // two props are undefined, so react-force-graph skips the custom
        // link-render path for all 3000 edges entirely — the single biggest
        // perf win.
        linkCanvasObjectMode={highlighted || pathHighlight || moneyTopLabels ? () => "after" : undefined}
        linkCanvasObject={
          highlighted || pathHighlight || moneyTopLabels
            ? (l: any, ctx: CanvasRenderingContext2D, scale: number) => {
                let money = false;
                if (pathHighlight) {
                  // Path mode: label every chain edge (bounded by path size,
                  // ≤ ~a dozen links); single Set lookup bails the rest.
                  if (!pathLinks.has(l)) return;
                } else if (highlighted) {
                  const s = typeof l.source === "object" ? l.source?.id : l.source;
                  const t = typeof l.target === "object" ? l.target?.id : l.target;
                  if (!(highlighted!.has(s) && highlighted!.has(t))) return;
                  // Cap relation-text to the focused node's strongest edges only.
                  if (focusEdgeLabels && !focusEdgeLabels.has(l)) return;
                } else {
                  // Money emphasis, nothing focused: € amounts on the top flows
                  // only (bounded precomputed Set; single lookup bails the rest).
                  if (!moneyTopLabels!.has(l)) return;
                  money = true;
                }

                const src = l.source, tgt = l.target;
                if (!src || !tgt) return;
                const sx = src.x, sy = src.y, tx = tgt.x, ty = tgt.y;
                if (![sx, sy, tx, ty].every(Number.isFinite)) return;

                const dx = tx - sx, dy = ty - sy;
                const len = Math.hypot(dx, dy);
                if (len < 1) return;

                const curve = curvature.get(l) ?? 0;
                const mx = (sx + tx) / 2 + (-dy / len) * (curve * len * 0.5);
                const my = (sy + ty) / 2 + (dx / len) * (curve * len * 0.5);

                let angle = Math.atan2(dy, dx);
                if (angle > Math.PI / 2) angle -= Math.PI;
                if (angle < -Math.PI / 2) angle += Math.PI;

                const text = money ? EUR_FMT.format(l.amount) : relLabel(l.type, l.status);
                const fontSize = (money ? 10 : 8.5) / scale;
                ctx.save();
                ctx.translate(mx, my);
                ctx.rotate(angle);
                ctx.font = `${money ? 700 : 500} ${fontSize}px ui-sans-serif, system-ui`;
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                const w = ctx.measureText(text).width;
                const pad = 2.5 / scale;
                ctx.fillStyle = "rgba(7,10,18,0.72)";
                ctx.fillRect(-w / 2 - pad, -fontSize / 2 - pad, w + pad * 2, fontSize + pad * 2);
                ctx.fillStyle = money ? MONEY_GREEN : relColor(l.type);
                ctx.fillText(text, 0, 0);
                ctx.restore();
              }
            : undefined
        }
        // ---------- nodes ----------
        onNodeClick={handleNodeClick}
        onNodeHover={(n: any) => setHoverId(n ? n.id : null)}
        nodeCanvasObject={(node: any, ctx: CanvasRenderingContext2D, scale: number) => {
          if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) return;

          // Bridge emphasis: the top-K betweenness nodes get a bigger radius +
          // full alpha + a label; everyone else is attenuated (spec 013).
          const isBridge = bridgeEmphasis && !!bridgeTop?.has(node.id);
          const r = nodeRadius(node.degree) * sizeScale * (isBridge ? 1.8 : 1);
          // Colour: community (spec 013) overrides everything when active; else
          // cluster mode colours by case (type → shape cue), other modes by type.
          const color = colorBy === "community"
            ? communityColor(node.community)
            : cluster ? clusterColorOf(node.id) : nodeColor(node.type);
          const isSel = node.id === selectedId;
          const onPath = pathHighlight?.nodeIds.has(node.id) ?? false;
          const inFocus = pathHighlight ? onPath : highlighted ? highlighted.has(node.id) : true;
          const isDust = (node.degree ?? 0) < 2;
          // Depth-2 ring: present in the focus set but visually subdued (no
          // halo, reduced alpha) so the direct ring keeps the spotlight.
          const hop = !pathHighlight && hopOf ? hopOf.get(node.id) : undefined;

          // Aggressive dim: non-neighbors fade to ~8% on EVERYTHING. The eye
          // only sees the active subgraph; the rest reads as faint dust. With
          // nothing focused, low-degree dust is further muted so the periphery
          // reads as texture inside its region, not competing dots.
          let baseAlpha = inFocus ? 1 : 0.08;
          if (hop === 2) baseAlpha = 0.45;
          if (!pathHighlight && !highlighted && isDust && !isSel) baseAlpha = 0.5;
          // Bridge emphasis (when nothing else focused): bridges full, rest dim.
          if (bridgeEmphasis && !highlighted && !pathHighlight) baseAlpha = isBridge ? 1 : 0.15;
          ctx.globalAlpha = baseAlpha;

          // Glow halo. We DON'T draw it for every node anymore — only for the
          // per-cluster label anchors and the in-focus subgraph. Painting a 2.7r
          // radial gradient for every dust-mote node was the single biggest
          // contributor to the "blurry mass" feeling at the overview level.
          const wantsHalo = pathHighlight
            ? onPath
            : isSel || (!isDust && hop !== 2 && (labelIds.has(node.id) || (inFocus && !!highlighted)));
          if (wantsHalo) {
            const haloR = r * (isSel ? 2.4 : 2.0);
            const grad = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, haloR);
            grad.addColorStop(0, color + "99");
            grad.addColorStop(1, color + "00");
            ctx.fillStyle = grad;
            ctx.beginPath();
            ctx.arc(node.x, node.y, haloR, 0, 2 * Math.PI);
            ctx.fill();
          }

          // core fill — shape encodes entity type in cluster mode, plain circle
          // elsewhere (where colour already encodes type).
          if (cluster) drawNodeShape(ctx, node.type, node.x, node.y, r);
          else { ctx.beginPath(); ctx.arc(node.x, node.y, r, 0, 2 * Math.PI); }
          ctx.fillStyle = color;
          ctx.fill();

          if (isSel) {
            // Hard outer ring drawn in canvas (the soft pulse is the DOM overlay)
            ctx.lineWidth = 1.5 / scale;
            ctx.strokeStyle = "#ffffff";
            ctx.stroke();
          }

          // Labels — always for the per-cluster anchors and the selected node +
          // its neighbors (and every path node in path mode); otherwise only
          // when zoomed quite deep.
          const showLabel =
            onPath ||
            isSel ||
            isBridge ||
            (focusLabelNodes && focusLabelNodes.has(node.id)) ||
            labelIds.has(node.id) ||
            scale > 2.4;
          if (showLabel) {
            const fontSize = isSel || isBridge || labelIds.has(node.id) ? 11 / scale : 9.5 / scale;
            ctx.font = `${isSel ? 600 : 500} ${fontSize}px ui-sans-serif, system-ui`;
            ctx.textAlign = "center";
            ctx.textBaseline = "top";

            // Slight dark wash behind for legibility on a dense canvas
            const text = node.name as string;
            const metrics = ctx.measureText(text);
            const pad = 3 / scale;
            const ly = node.y + r + 2 / scale;
            ctx.fillStyle = "rgba(7,10,18,0.65)";
            ctx.fillRect(
              node.x - metrics.width / 2 - pad,
              ly - 1 / scale,
              metrics.width + pad * 2,
              fontSize + 2 / scale,
            );
            ctx.fillStyle = isSel ? "#5ef2d8" : inFocus ? "#e2e8f0" : "rgba(148,163,184,0.5)";
            ctx.fillText(text, node.x, ly);
          }

          ctx.globalAlpha = 1;
        }}
        nodePointerAreaPaint={(node: any, color: string, ctx: CanvasRenderingContext2D) => {
          if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) return;
          const r = nodeRadius(node.degree) * sizeScale;
          // Coarse (touch): floor the hit radius at ~22px (≥44px diameter) so
          // even degree-0 dust nodes (r≈2.2) land reliably under a fingertip;
          // hubs keep a generous margin. Fine pointers stay tight at r+4.
          const hit = coarse ? Math.max(22, r + 12) : r + 4;
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(node.x, node.y, hit, 0, 2 * Math.PI);
          ctx.fill();
        }}
      />
      )}

      {/* pulse-ring overlay at the selected node's screen position */}
      {pulseAnchor && (
        <svg
          aria-hidden
          className="pointer-events-none absolute left-0 top-0 h-full w-full overflow-visible"
        >
          <circle
            cx={pulseAnchor.x}
            cy={pulseAnchor.y}
            r={pulseAnchor.r + 8}
            fill="none"
            stroke="#5ef2d8"
            strokeWidth={1}
            strokeOpacity={0.7}
            style={{
              transformOrigin: `${pulseAnchor.x}px ${pulseAnchor.y}px`,
              animation: `sm-pulse ${PULSE_PERIOD_MS}ms ease-out infinite`,
            }}
          />
          <circle
            cx={pulseAnchor.x}
            cy={pulseAnchor.y}
            r={pulseAnchor.r + 3}
            fill="none"
            stroke="#5ef2d8"
            strokeWidth={1.2}
            strokeOpacity={0.9}
          />
        </svg>
      )}

      {/* edge hover tooltip — at rest, a collapsed pair shows its full list.
          Disabled on coarse (touch) pointers: there's no hover, and a tap opens
          the richer EdgeDetailPanel instead. Desktop preview is unchanged. */}
      <EdgeTooltip
        link={coarse ? null : hoverLink}
        pair={!coarse && !highlighted && !pathHighlight && !moneyEmphasis && hoverLink ? pairOf.get(hoverLink) ?? null : null}
        x={mouse.x}
        y={mouse.y}
        boundsW={size.w}
        boundsH={size.h}
      />

      {/* Community legend (spec 013): names the top sub-tramas shown, with their
          palette colour. Bounded to COMMUNITY_LEGEND entries — never per-node. */}
      {communityInfo && communityInfo.legend.length > 0 && (
        <div className={`pointer-events-none absolute bottom-3 left-3 z-10 max-w-[15rem] rounded-xl border border-white/10 glass px-3 py-2 text-[11px] leading-tight ${coarse && sheetCoverFrac > 0.15 ? "hidden md:block" : ""}`}>
          <p className="mb-1 font-medium text-slate-300">Comunidades (sub-tramas)</p>
          <ul className="space-y-0.5">
            {communityInfo.legend.map((c) => (
              <li key={c.community} className="flex items-center gap-1.5 text-slate-400">
                <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: c.color }} />
                <span className="truncate">{c.label}</span>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-[10px] italic text-slate-600">Co-aparición estructural, no imputación.</p>
        </div>
      )}

      {/* keyframes for the pulse — kept local to this island */}
      <style jsx global>{`
        @keyframes sm-pulse {
          0%   { transform: scale(1);   opacity: 0.8; }
          80%  { transform: scale(2.4); opacity: 0;   }
          100% { transform: scale(2.4); opacity: 0;   }
        }
      `}</style>
    </div>
  );
});

export default GraphCanvas;
