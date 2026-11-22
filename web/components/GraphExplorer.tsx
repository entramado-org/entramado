"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, ChevronLeft, ChevronRight, Clock, HelpCircle, Landmark, Loader2, Newspaper, SlidersHorizontal, WifiOff, X } from "lucide-react";
import { useGraphData } from "@/lib/useGraphData";
import { ALL_TYPES, TYPE_META } from "@/lib/graphStyle";
import { computeClusters, layoutClusterAnchors, computeRadialClusters, layoutRadialAnchors } from "@/lib/clustering";
import { computeInsights } from "@/lib/insights";
import type { ColorMode, EdgeFilters, EntityType, GraphData, GraphFilters, GraphNode, LayoutMode, PathResult, SearchHit, SelectedEdge } from "@/lib/types";
import { fetchNodeDetail, fetchPath } from "@/lib/api";
import { isLinkHidden } from "@/lib/edgeVisibility";
import type { SectionId } from "./explorer/ControlSection";
import type { PickedEntity } from "./explorer/EntityPicker";
import GraphCanvas, { type GraphCanvasHandle, type PathHighlight } from "./GraphCanvas";
import SearchBar from "./SearchBar";
import ExplorerControls from "./ExplorerControls";
import NodeDetailPanel from "./NodeDetailPanel";
import EdgeDetailPanel from "./EdgeDetailPanel";
import DocumentReader from "./DocumentReader";
import MobileRelations from "./MobileRelations";
import { coverFrac, type SheetSnap } from "./MobileSheet";
import TimelineStrip from "./TimelineStrip";
import Breadcrumbs from "./Breadcrumbs";
import OnboardingHint from "./OnboardingHint";
import HelpOverlay from "./HelpOverlay";
import EvidenceLegend from "./EvidenceLegend";
import { Kbd } from "./Kbd";

const TRAIL_MAX = 6;
const PATH_MAX_LEN = 4;
// Deepest neighbourhood the "Expandir red" button will pull, one ring per click.
// Matches the existing depth-2 ceiling (the API caps at 500; beyond 2 is a hairball).
const MAX_HOPS = 2;

const DEFAULT_SECTIONS: Record<SectionId, boolean> = {
  vista: true,
  filtros: true,
  explorar: false,
  claves: true,
};

const DEFAULT_EDGE_FILTERS: EdgeFilters = {
  hiddenRels: new Set(),
  hiddenKinds: new Set(),
  minStatusRank: 0,
  emphasis: "none",
};

interface PathState {
  a: PickedEntity | null;
  b: PickedEntity | null;
  result: PathResult | null;
  activeIdx: number | null; // null = highlight the union of all paths
  loading: boolean;
  notFound: boolean;
}

const EMPTY_PATH: PathState = { a: null, b: null, result: null, activeIdx: null, loading: false, notFound: false };

export default function GraphExplorer() {
  const { data, loading, error, loadOverview, expand, focusOnly, addData, depthOf } = useGraphData();
  const [selected, setSelected] = useState<GraphNode | null>(null);
  // Clicked edge → relation detail panel. Mutually exclusive with `selected`
  // (one right-rail slot); holds normalized string ids, never link objects.
  const [selectedEdge, setSelectedEdge] = useState<SelectedEdge | null>(null);
  // Document reader (web twin of leer_documento), opened from a citation.
  const [reader, setReader] = useState<{ docId: string; page?: number | null; quote?: string | null } | null>(null);
  const [active, setActive] = useState<Set<EntityType>>(new Set(ALL_TYPES));
  const [controlsOpen, setControlsOpen] = useState(false);
  // Desktop-only: collapse the left controls rail to reclaim canvas width.
  const [railOpen, setRailOpen] = useState(true);
  const [mode, setMode] = useState<LayoutMode>("free");
  // Node colour scheme — orthogonal to layout (spec 013). 'type' = current behaviour.
  const [colorBy, setColorBy] = useState<ColorMode>("type");
  const [sizeScale, setSizeScale] = useState(1);
  const [timelineOpen, setTimelineOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  // Render-level edge filters (relation types / source layers / procedural
  // status / money emphasis). Generalizes the old single última-hora toggle.
  const [edgeFilters, setEdgeFilters] = useState<EdgeFilters>(DEFAULT_EDGE_FILTERS);
  // spec 019 — hide "orphan" nodes (no visible edge under the active filters),
  // like Obsidian's Orphans toggle. Defaults to ON so the entry view is clean.
  const [hideOrphans, setHideOrphans] = useState(true);
  // Expansion depth for select/expand actions: 1 = direct ties (as always),
  // 2 = also load the relations of relations. Deliberately not persisted.
  const [hopDepth, setHopDepth] = useState<1 | 2>(1);
  // "Ruta entre entidades" tool state (Explorar section).
  const [path, setPath] = useState<PathState>(EMPTY_PATH);
  // Dismissible truncation notice for hop-2 mega-hub neighborhoods.
  const [truncDismissed, setTruncDismissed] = useState(false);
  // Collapsible rail sections; persisted so the panel opens how you left it.
  const [sections, setSections] = useState<Record<SectionId, boolean>>(DEFAULT_SECTIONS);
  // Mobile-only: which surface is the hero — the structured relations view or the
  // full force-graph canvas. Defaults to the legible relations view on phones.
  const [mobileView, setMobileView] = useState<"relations" | "graph">("relations");
  // Mobile detail bottom-sheet height. Lifted here (not local to the panels) so
  // the canvas can nudge the focused node above the live sheet coverage.
  const [sheetSnap, setSheetSnap] = useState<SheetSnap>("half");
  // Default minDegree=3 hides the periphery of singletons / very weak ties.
  // The chronology slider, type filters, and search all still work as escape
  // hatches — this just gives the entry view actual signal.
  const [filters, setFilters] = useState<GraphFilters>({ minDegree: 3, fromYear: null, toYear: null });
  const [trail, setTrail] = useState<GraphNode[]>([]);
  const canvasRef = useRef<GraphCanvasHandle>(null);
  const params = useSearchParams();

  // Libre mode loads the WHOLE constellation, Obsidian-style: the degree-1/2
  // leaves (one-case people, single payments, dated events) are what give that
  // graph its dandelion halo — at min_degree 1 / limit 1000 the API returns
  // ~1000 nodes but only ~3700 links (leaves barely add edges), measured 156 ms.
  // The anchored layouts keep the curated 250/min-degree-3 core.
  const dataFilters = useMemo<GraphFilters>(
    () => (mode === "free" ? { ...filters, minDegree: 1 } : filters),
    [mode, filters],
  );
  const overviewLimit = mode === "free" ? 1000 : 250;

  // --- rail-section open/closed state: persisted to localStorage. Merge stored
  // over defaults so sections added later start with their default state.
  useEffect(() => {
    try {
      const raw = localStorage.getItem("sm:panelSections.v1");
      if (raw) setSections({ ...DEFAULT_SECTIONS, ...JSON.parse(raw) });
    } catch { /* ignore */ }
  }, []);
  const toggleSection = useCallback((id: SectionId) => {
    setSections((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      try {
        localStorage.setItem("sm:panelSections.v1", JSON.stringify(next));
      } catch { /* ignore */ }
      return next;
    });
  }, []);

  // --- initial load ---
  useEffect(() => {
    const focus = params.get("focus");
    // Deep-link a path: /explore?from=Person:x&to=Person:y (names backfilled
    // from the payload once the route resolves).
    const pFrom = params.get("from");
    const pTo = params.get("to");
    if (pFrom && pTo && pFrom !== pTo) {
      setSections((prev) => ({ ...prev, explorar: true })); // surface the results list
      loadOverview(undefined, filters).then(() =>
        runPathFor({ id: pFrom, name: pFrom }, { id: pTo, name: pTo }),
      );
    } else if (focus) {
      // ?hops=2 deep-links the "relations of relations" view of the focus node.
      const deep = params.get("hops") === "2";
      if (deep) setHopDepth(2);
      focusOnly(focus, filters).then(() => {
        if (deep) expand(focus, 2, filters, 250);
        fetchNodeDetail(focus).then((d) => selectNode(d.node)).catch(() => {});
      });
    } else {
      loadOverview(undefined, filters);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- refetch on filter OR layout-mode change, preserving focus (mode swaps
  // the effective fetch params: Libre = full constellation, anchored = core) ---
  useEffect(() => {
    if (selected) focusOnly(selected.id, dataFilters);
    else loadOverview(undefined, dataFilters, overviewLimit);
    // The refetch resets the merged store, so any merged path subgraph is gone —
    // keep the A/B picks but drop the (now stale) highlight/result.
    setPath((p) => (p.result || p.notFound ? { ...p, result: null, activeIdx: null, notFound: false } : p));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.minDegree, filters.fromYear, filters.toYear, mode]);

  // --- selection helper: also push onto trail ---
  const selectNode = useCallback((node: GraphNode | null) => {
    setSelected(node);
    setSelectedEdge(null);
    setSheetSnap("half"); // a freshly selected node opens the sheet at half height
    if (!node) return;
    setTrail((prev) => {
      const trimmed = prev.filter((n) => n.id !== node.id);
      const next = [...trimmed, node];
      return next.length > TRAIL_MAX ? next.slice(next.length - TRAIL_MAX) : next;
    });
    // Center the canvas camera on this node once it's known to the layout.
    requestAnimationFrame(() => canvasRef.current?.centerOn(node.id));
  }, []);

  // Canvas node clicks select AND load the node's neighbourhood at the current
  // depth, so focus mode has the complete relation set even when the node was
  // clicked straight from the overview.
  // expand() is depth-aware-deduped, so re-clicking is cheap. Depth 2 raises
  // the limit (the API caps at 500) and that bigger net is what surfaces the
  // "relations of relations" ring.
  const expandAtDepth = useCallback((id: string) => {
    expand(id, hopDepth, dataFilters, hopDepth === 2 ? 250 : 120);
    setTruncDismissed(false);
  }, [expand, hopDepth, dataFilters]);

  // "Expandir red" button: pull the NEXT ring for this node (depth already
  // fetched + 1, capped at MAX_HOPS), independent of the global hopDepth toggle.
  // Selecting a node already auto-expands to hopDepth, so this is what lets the
  // user reveal one more layer on demand instead of the button being a no-op.
  const expandDeeper = useCallback((id: string) => {
    const next = Math.min(depthOf(id) + 1, MAX_HOPS);
    expand(id, next, dataFilters, next >= 2 ? 250 : 120);
    setTruncDismissed(false);
  }, [expand, depthOf, dataFilters]);

  const selectFromCanvas = useCallback((node: GraphNode) => {
    selectNode(node);
    expandAtDepth(node.id);
  }, [selectNode, expandAtDepth]);

  const selectEdgeFromCanvas = useCallback((edge: SelectedEdge) => {
    setSelected(null);
    setSelectedEdge(edge);
    setSheetSnap("half");
  }, []);

  // Conexiones list → open the pair A↔B and ISOLATE it on the canvas (dim the
  // rest, frame the two nodes). Keeps `selected` (A) so closing the pair panel
  // returns to A's card. On mobile, jump to the graph surface so the isolation
  // and the pair sheet are actually visible.
  const openPairFromList = useCallback((otherId: string, otherName?: string) => {
    if (!selected) return;
    setSelectedEdge({
      sourceId: selected.id,
      sourceName: selected.name,
      targetId: otherId,
      targetName: otherName,
      isolate: true,
    });
    setSheetSnap("half");
    setMobileView("graph");
    requestAnimationFrame(() => canvasRef.current?.fitNodes(new Set([selected.id, otherId])));
  }, [selected]);

  // Mobile: jump from the structured relations view to the spatial graph of the
  // CURRENT selection (preserved). Re-center once the now-visible canvas has a size.
  const openInGraph = useCallback(() => {
    setMobileView("graph");
    if (selected) requestAnimationFrame(() => canvasRef.current?.centerOn(selected.id));
  }, [selected]);

  // Top entities by degree → entry chips for the mobile "Grafo" empty state
  // (search-first instead of dropping the user into the 3000-edge hairball).
  const keyEntities = useMemo(
    () => [...data.nodes].sort((a, b) => (b.degree ?? 0) - (a.degree ?? 0)).slice(0, 12),
    [data.nodes],
  );

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of data.nodes) c[n.type] = (c[n.type] ?? 0) + 1;
    return c;
  }, [data.nodes]);

  const filtered: GraphData = useMemo(() => {
    const nodes = data.nodes.filter((n) => active.has(n.type));
    const ids = new Set(nodes.map((n) => n.id));
    const links = data.links.filter((l) => {
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      return ids.has(s) && ids.has(t);
    });
    return { nodes, links, meta: data.meta };
  }, [data, active]);

  const presentRels = useMemo(() => {
    const s = new Set<string>();
    for (const l of filtered.links) if (l.type) s.add(l.type);
    return s;
  }, [filtered.links]);

  // spec 019 — Obsidian-style orphan hiding. Over the type-filtered set, drop any
  // node left with no VISIBLE edge (visibility = the shared isLinkHidden predicate,
  // covering source-layer / relation-type / status filters). The currently selected
  // node is always kept so the open detail panel never points at a vanished node.
  // Edges incident to dropped nodes were all hidden anyway, so removing them is safe.
  const displayed: GraphData = useMemo(() => {
    if (!hideOrphans) return filtered;
    const connected = new Set<string>();
    for (const l of filtered.links) {
      if (isLinkHidden(l, edgeFilters)) continue;
      connected.add(typeof l.source === "string" ? l.source : l.source.id);
      connected.add(typeof l.target === "string" ? l.target : l.target.id);
    }
    if (selected) connected.add(selected.id);
    const nodes = filtered.nodes.filter((n) => connected.has(n.id));
    if (nodes.length === filtered.nodes.length) return filtered;
    const ids = new Set(nodes.map((n) => n.id));
    const links = filtered.links.filter((l) => {
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      return ids.has(s) && ids.has(t);
    });
    return { nodes, links, meta: filtered.meta };
  }, [filtered, hideOrphans, edgeFilters, selected]);

  // Case clusters for the cluster layout. Computed over the FULL data (not the
  // type-filtered subset) so toggling a type filter doesn't reshuffle regions —
  // the canvas just skips hidden nodes. Purely client-side, no API change.
  const clusters = useMemo(() => {
    const c = computeClusters(data, { clusterBy: "case" });
    layoutClusterAnchors(c.clusters);
    return c;
  }, [data]);

  // "Personas" layout: people at the centre grouped by case, detail on an outer
  // ring grouped by type. Computed independently of the case clustering above.
  const radialClusters = useMemo(() => {
    const c = computeRadialClusters(data);
    layoutRadialAnchors(c.clusters);
    return c;
  }, [data]);

  // The clustering fed to the canvas depends on which anchor-based mode is active.
  const activeClusters = mode === "radial" ? radialClusters : clusters;

  // "Claves" insights over the visible (type-filtered) view. O(N+E), trivial
  // at overview scale; always against the CASE clustering (bridge detection).
  const insights = useMemo(() => computeInsights(filtered, clusters.byNode), [filtered, clusters]);

  function toggle(t: EntityType) {
    setActive((prev) => {
      const next = new Set(prev);
      next.has(t) ? next.delete(t) : next.add(t);
      return next;
    });
  }

  function pick(hit: SearchHit) {
    selectNode({ id: hit.id, type: hit.type, name: hit.name, degree: hit.degree });
    expandAtDepth(hit.id);
  }

  function pickById(id: string) {
    expandAtDepth(id);
    fetchNodeDetail(id).then((d) => selectNode(d.node)).catch(() => {});
  }

  function resetView() {
    setSelected(null);
    setSelectedEdge(null);
    setTrail([]);
    setPath((p) => ({ ...p, result: null, activeIdx: null, notFound: false }));
    loadOverview(undefined, dataFilters, overviewLimit);
  }

  // --- "Ruta entre entidades": fetch, merge into the live graph, frame it ---
  const runPathFor = useCallback(async (a: PickedEntity, b: PickedEntity) => {
    if (a.id === b.id) return;
    setPath((p) => ({ ...p, a, b, loading: true, notFound: false, result: null, activeIdx: null }));
    try {
      const res = await fetchPath(a.id, b.id, PATH_MAX_LEN);
      if (!res.meta?.found || res.paths.length === 0) {
        setPath((p) => ({ ...p, loading: false, notFound: true }));
        return;
      }
      addData(res);
      // Backfill display names from the payload (the deep-link below only has ids).
      const byId = new Map(res.nodes.map((n) => [n.id, n.name]));
      setPath((p) => ({
        ...p,
        a: p.a && byId.has(p.a.id) ? { ...p.a, name: byId.get(p.a.id)! } : p.a,
        b: p.b && byId.has(p.b.id) ? { ...p.b, name: byId.get(p.b.id)! } : p.b,
        loading: false,
        result: res,
      }));
      setControlsOpen(false); // mobile: close the sheet so the highlight is visible
      const ids = new Set(res.paths.flat());
      requestAnimationFrame(() => canvasRef.current?.fitNodes(ids));
    } catch {
      setPath((p) => ({ ...p, loading: false, notFound: true }));
    }
  }, [addData]);

  const runPath = useCallback(() => {
    if (path.a && path.b) runPathFor(path.a, path.b);
  }, [path.a, path.b, runPathFor]);

  const clearPath = useCallback(() => {
    setPath((p) => ({ ...p, result: null, activeIdx: null, notFound: false }));
  }, []);

  // Highlight payload for the canvas: node ids + UNDIRECTED pair keys of the
  // active path(s) — pair keys (not directed triples) so every parallel
  // relation between two hop endpoints lights up, matching the evidence list.
  const pathHighlight: PathHighlight | null = useMemo(() => {
    if (!path.result) return null;
    const seqs = path.activeIdx == null ? path.result.paths : [path.result.paths[path.activeIdx]];
    const nodeIds = new Set<string>();
    const pairKeys = new Set<string>();
    for (const seq of seqs) {
      if (!seq) continue;
      for (const id of seq) nodeIds.add(id);
      for (let i = 0; i < seq.length - 1; i++) {
        const a = seq[i];
        const b = seq[i + 1];
        pairKeys.add(a < b ? `${a}|${b}` : `${b}|${a}`);
      }
    }
    return { nodeIds, pairKeys };
  }, [path.result, path.activeIdx]);

  // Isolation highlight for a pair opened from the Conexiones list: same payload
  // shape as a path highlight (two nodes + their pair key), so the canvas dims
  // everything else. Takes precedence over the path-tool highlight while active.
  const pairHighlight: PathHighlight | null = useMemo(() => {
    if (!selectedEdge?.isolate) return null;
    const a = selectedEdge.sourceId;
    const b = selectedEdge.targetId;
    return {
      nodeIds: new Set([a, b]),
      pairKeys: new Set([a < b ? `${a}|${b}` : `${b}|${a}`]),
    };
  }, [selectedEdge]);
  const canvasHighlight = pairHighlight ?? pathHighlight;

  // --- keyboard shortcuts ---
  useEffect(() => {
    let lastG = 0;
    function onKey(e: KeyboardEvent) {
      // Don't hijack typing inside inputs / textareas.
      const t = e.target as HTMLElement | null;
      const inField = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);

      if (!inField && (e.key === "/" || e.key === "?")) {
        e.preventDefault();
        if (e.key === "?") return setHelpOpen(true);
        const inp = document.querySelector<HTMLInputElement>("[data-search-input]");
        inp?.focus();
        return;
      }
      if (e.key === "Escape") {
        // Priority: reader → help overlay → path highlight → edge selection → node.
        if (reader) return setReader(null);
        if (helpOpen) return setHelpOpen(false);
        if (path.result) return clearPath();
        if (selectedEdge) return setSelectedEdge(null);
        if (selected) return setSelected(null);
        return;
      }
      if (!inField && e.key.toLowerCase() === "g") {
        const now = Date.now();
        if (now - lastG < 450) resetView();
        lastG = now;
        return;
      }
      if (!inField && e.key.toLowerCase() === "t") {
        e.preventDefault();
        setTimelineOpen((v) => !v);
        return;
      }
      if (!inField && (e.key === "ArrowLeft" || e.key === "ArrowRight") && trail.length > 0) {
        const idx = selected ? trail.findIndex((n) => n.id === selected.id) : trail.length - 1;
        const next = e.key === "ArrowLeft" ? Math.max(0, idx - 1) : Math.min(trail.length - 1, idx + 1);
        if (next !== idx) {
          const node = trail[next];
          setSelected(node);
          requestAnimationFrame(() => canvasRef.current?.centerOn(node.id));
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [helpOpen, selected, selectedEdge, reader, trail, path.result, clearPath]);

  // Both ExplorerControls instances (desktop rail + mobile sheet) share these.
  const controlProps = {
    sections,
    onToggleSection: toggleSection,
    vista: {
      mode, onModeChange: setMode,
      colorBy, onColorByChange: setColorBy,
      sizeScale, onSizeChange: setSizeScale,
    },
    filtros: {
      active, counts, onToggle: toggle,
      filters, onFiltersChange: setFilters,
      presentRels, edgeFilters, onEdgeFiltersChange: setEdgeFilters,
      hideOrphans, onHideOrphansChange: setHideOrphans,
    },
    explorar: {
      hopDepth, onHopDepthChange: setHopDepth,
      pathA: path.a, pathB: path.b,
      onPathAChange: (v: PickedEntity | null) => setPath((p) => ({ ...p, a: v })),
      onPathBChange: (v: PickedEntity | null) => setPath((p) => ({ ...p, b: v })),
      selectedEntity: selected ? { id: selected.id, name: selected.name } : null,
      onRunPath: runPath,
      pathLoading: path.loading,
      pathNotFound: path.notFound,
      pathResult: path.result,
      activePathIdx: path.activeIdx,
      onSelectPath: (idx: number | null) => setPath((p) => ({ ...p, activeIdx: idx })),
      onClearPath: clearPath,
      onPickNode: pickById,
      maxLen: PATH_MAX_LEN,
    },
    claves: {
      insights,
      onPick: pickById,
      onFocusCommunity: (principalId: string) => { setColorBy("community"); pickById(principalId); },
    },
  };

  return (
    <div className="flex h-[100dvh] flex-col">
      {/* top bar */}
      <header className="z-20 flex items-center gap-2 border-b border-white/5 px-3 py-3 pt-safe glass sm:gap-3 sm:px-4">
        <Link href="/" className="flex shrink-0 items-center gap-2 text-slate-400 hover:text-accent">
          <ArrowLeft className="h-4 w-4" />
          <span className="hidden text-sm font-medium sm:inline">El Entramado</span>
        </Link>
        <div className="min-w-0 flex-1">
          <SearchBar onPick={pick} />
        </div>
        {loading && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-accent" />}
        <Link
          href="/ultima-hora"
          className="flex shrink-0 items-center gap-1.5 rounded-lg bg-white/5 p-2 text-sm text-slate-300 transition hover:bg-white/10 sm:px-3 sm:py-1.5"
          title="Última hora"
        >
          <Newspaper className="h-4 w-4" /> <span className="hidden sm:inline">Última hora</span>
        </Link>
        <Link
          href="/institucional"
          className="flex shrink-0 items-center gap-1.5 rounded-lg bg-white/5 p-2 text-sm text-slate-300 transition hover:bg-white/10 sm:px-3 sm:py-1.5"
          title="Contexto institucional"
        >
          <Landmark className="h-4 w-4" /> <span className="hidden sm:inline">Contexto</span>
        </Link>
        {/* mobile-only: open the controls bottom-sheet (the desktop rail is hidden) */}
        <button
          onClick={() => setControlsOpen(true)}
          className="flex shrink-0 items-center justify-center rounded-lg bg-white/5 p-2 text-slate-300 hover:bg-white/10 md:hidden"
          aria-label="Controles"
        >
          <SlidersHorizontal className="h-4 w-4" />
        </button>
        <button
          onClick={() => setTimelineOpen((v) => !v)}
          className={`flex shrink-0 items-center gap-1.5 rounded-lg p-2 text-sm transition sm:px-3 sm:py-1.5 ${
            timelineOpen ? "bg-accent/20 text-accent" : "bg-white/5 text-slate-300 hover:bg-white/10"
          }`}
          aria-label="Cronología"
        >
          <Clock className="h-4 w-4" /> <span className="hidden sm:inline">Cronología</span>
        </button>
      </header>

      <div className="relative flex min-h-0 flex-1 flex-col">
        {/* mobile surface switch: structured relations vs full graph (B1) */}
        <div className="flex shrink-0 items-center justify-center gap-1 border-b border-white/5 p-1.5 md:hidden">
          {(["relations", "graph"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setMobileView(v)}
              className={`rounded-lg px-4 py-1.5 text-sm font-medium transition ${
                mobileView === v ? "bg-accent/20 text-accent" : "bg-white/5 text-slate-400 active:bg-white/10"
              }`}
            >
              {v === "relations" ? "Relaciones" : "Grafo"}
            </button>
          ))}
        </div>

        <div className={`min-h-0 flex-1 ${mobileView === "relations" ? "hidden md:flex" : "flex"}`}>
          {/* filter rail (desktop) — collapsible */}
          {railOpen ? (
            <nav className="relative hidden w-56 shrink-0 overflow-auto border-r border-white/5 p-3 md:block">
              <button
                onClick={() => setRailOpen(false)}
                className="absolute right-2 top-2 z-10 rounded p-1 text-slate-500 hover:bg-white/5 hover:text-slate-200"
                aria-label="Ocultar panel de controles"
                title="Ocultar"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <ExplorerControls {...controlProps} onReset={resetView} />
            </nav>
          ) : (
            <button
              onClick={() => setRailOpen(true)}
              className="hidden shrink-0 flex-col items-center gap-2 border-r border-white/5 px-1.5 py-3 text-slate-500 transition hover:bg-white/5 hover:text-slate-200 md:flex"
              aria-label="Mostrar panel de controles"
              title="Mostrar controles"
            >
              <ChevronRight className="h-4 w-4" />
              <SlidersHorizontal className="h-4 w-4" />
            </button>
          )}

          {/* canvas + overlays */}
          <main className="relative min-w-0 flex-1">
            <Breadcrumbs
              trail={trail}
              currentId={selected?.id ?? null}
              onPick={(id) => {
                const node = trail.find((n) => n.id === id) ?? null;
                if (node) {
                  setSelected(node);
                  requestAnimationFrame(() => canvasRef.current?.centerOn(node.id));
                }
              }}
              onReset={resetView}
            />
            <GraphCanvas
              ref={canvasRef}
              data={displayed}
              selectedId={selected?.id ?? null}
              onSelect={selectFromCanvas}
              onLinkSelect={selectEdgeFromCanvas}
              onDeselect={() => {
                // Tap empty canvas to go back: edge first, then node (mirrors Escape).
                if (selectedEdge) return setSelectedEdge(null);
                if (selected) return setSelected(null);
              }}
              mode={mode}
              colorBy={colorBy}
              clusterOf={activeClusters.byNode}
              clusterInfo={activeClusters.clusters}
              sizeScale={sizeScale}
              edgeFilters={edgeFilters}
              focusHops={hopDepth}
              pathHighlight={canvasHighlight}
              sheetCoverFrac={selected || selectedEdge ? coverFrac[sheetSnap] : 0}
            />
            {data.meta?.truncated && !truncDismissed && (
              <div className="absolute inset-x-0 top-14 z-20 flex justify-center px-4">
                <div className="flex items-center gap-2 rounded-xl border border-amber-400/20 glass px-3 py-1.5 text-xs text-amber-200/90">
                  <span>Vecindario truncado — afina los filtros para ver más conexiones.</span>
                  <button
                    onClick={() => setTruncDismissed(true)}
                    className="rounded p-0.5 text-amber-200/60 hover:bg-white/10 hover:text-amber-100"
                    aria-label="Cerrar aviso"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              </div>
            )}
            {!selected && <OnboardingHint />}
            {/* mobile "Grafo" empty state: search-first over a faint overview,
                instead of an unreadable 3000-edge hairball (B1/B3). */}
            {!selected && !selectedEdge && mobileView === "graph" && (
              <div className="absolute inset-0 z-20 flex flex-col gap-4 overflow-auto bg-ink-950/80 px-4 py-6 backdrop-blur-sm md:hidden">
                <div>
                  <h2 className="text-lg font-semibold tracking-tight text-slate-100">Explora el grafo</h2>
                  <p className="mt-1 text-sm leading-snug text-slate-400">
                    Busca o toca una figura para ver su red de conexiones.
                  </p>
                </div>
                <SearchBar onPick={pick} />
                {keyEntities.length > 0 && (
                  <div>
                    <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">
                      Figuras y entidades clave
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {keyEntities.map((n) => (
                        <button
                          key={n.id}
                          onClick={() => pickById(n.id)}
                          className="flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-200 transition active:bg-white/10"
                        >
                          <span
                            className="h-2 w-2 shrink-0 rounded-full"
                            style={{ background: TYPE_META[n.type]?.color ?? "#94a3b8" }}
                          />
                          <span className="max-w-[60vw] truncate">{n.name}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
            {error && (
              <div className="absolute inset-x-0 top-3 z-30 flex justify-center px-4">
                <div className="flex items-center gap-3 rounded-xl border border-red-400/30 glass px-4 py-2 text-sm text-red-200">
                  <WifiOff className="h-4 w-4 shrink-0 text-red-300" aria-hidden />
                  <span>{error}</span>
                  <button
                    onClick={() => (selected ? focusOnly(selected.id, dataFilters) : loadOverview(undefined, dataFilters, overviewLimit))}
                    className="rounded-lg bg-white/10 px-2.5 py-1 text-xs font-medium text-slate-100 transition hover:bg-white/20"
                  >
                    Reintentar
                  </button>
                </div>
              </div>
            )}
            {data.nodes.length === 0 && !loading && !error && (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                <p className="text-sm text-slate-500">Sin datos. ¿Has ejecutado la ingesta?</p>
              </div>
            )}
            {/* tiny help chip, bottom-left */}
            <button
              onClick={() => setHelpOpen(true)}
              className="pointer-events-auto absolute bottom-4 left-4 z-20 hidden items-center gap-1.5 rounded-full glass px-2.5 py-1 text-[11px] text-slate-400 transition hover:text-accent sm:flex"
            >
              <HelpCircle className="h-3 w-3" /> Atajos <Kbd>?</Kbd>
            </button>
            <EvidenceLegend />
          </main>

          {/* detail panel — one rail slot: the clicked edge wins over the node */}
          {selectedEdge ? (
            <EdgeDetailPanel
              edge={selectedEdge}
              filters={filters}
              snap={sheetSnap}
              onSnapChange={setSheetSnap}
              onClose={() => setSelectedEdge(null)}
              onPickNode={(id) => {
                setSelectedEdge(null);
                pickById(id);
              }}
              onOpenDocument={(docId, page, quote) => setReader({ docId, page, quote })}
            />
          ) : (
            <NodeDetailPanel
              node={selected}
              filters={filters}
              snap={sheetSnap}
              onSnapChange={setSheetSnap}
              onClose={() => setSelected(null)}
              onExpand={expandDeeper}
              canExpand={selected ? depthOf(selected.id) < MAX_HOPS : true}
              onPick={pickById}
              onOpenPair={openPairFromList}
            />
          )}
        </div>

        {/* mobile relations surface (hero on phones; hidden on desktop) */}
        {mobileView === "relations" && (
          <div className="flex min-h-0 flex-1 md:hidden">
            <MobileRelations
              selected={selected}
              filters={filters}
              nodes={data.nodes}
              hiddenKinds={edgeFilters.hiddenKinds}
              onSearchPick={pick}
              onPick={pickById}
              onOpenPair={openPairFromList}
              onClose={() => setSelected(null)}
              onOpenInGraph={openInGraph}
            />
          </div>
        )}

        {/* controls bottom-sheet (mobile only — the desktop rail is hidden) */}
        {controlsOpen && (
          <div className="md:hidden">
            <div
              onClick={() => setControlsOpen(false)}
              className="fixed inset-0 z-40 bg-ink-900/50 backdrop-blur-[2px] animate-fade-in"
              aria-hidden
            />
            <div className="fixed inset-x-0 bottom-0 z-50 max-h-[82dvh] overflow-auto rounded-t-2xl border-t border-white/10 glass p-3 pb-safe animate-fade-in">
              <div className="mb-3 flex items-center justify-between">
                <span className="text-xs uppercase tracking-widest text-slate-500">Controles</span>
                <button
                  onClick={() => setControlsOpen(false)}
                  className="-mr-1.5 flex h-11 w-11 items-center justify-center rounded-lg text-slate-400 hover:bg-white/5 hover:text-slate-200"
                  aria-label="Cerrar"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>
              <ExplorerControls {...controlProps} onReset={() => { resetView(); setControlsOpen(false); }} />
            </div>
          </div>
        )}

        {/* timeline */}
        {timelineOpen && (
          <TimelineStrip
            scopeEntity={selected?.id ?? null}
            filters={filters}
            onPick={pickById}
            onClose={() => setTimelineOpen(false)}
          />
        )}
      </div>

      <HelpOverlay open={helpOpen} onClose={() => setHelpOpen(false)} />

      {reader && (
        <DocumentReader
          docId={reader.docId}
          initialPage={reader.page}
          highlightQuote={reader.quote}
          onClose={() => setReader(null)}
        />
      )}
    </div>
  );
}
