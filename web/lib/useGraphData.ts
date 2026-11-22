"use client";
import { useCallback, useRef, useState } from "react";
import type { GraphData, GraphFilters, GraphLink, GraphNode } from "./types";
import { fetchNeighborhood, fetchOverview } from "./api";

function linkKey(l: GraphLink): string {
  const s = typeof l.source === "string" ? l.source : l.source.id;
  const t = typeof l.target === "string" ? l.target : l.target.id;
  // No `page` component: the API emits exactly one link per directed
  // (source,type,target), but the representative evidence (and so the page)
  // can differ between payloads — keying on it duplicated edges on merge.
  return `${s}|${l.type}|${t}`;
}

/** Holds the live graph and merges incremental expansions (dedupe by id/key). */
export function useGraphData() {
  const [data, setData] = useState<GraphData>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(false);
  // Network/API failure flag so the UI can tell "API down" apart from a
  // genuinely empty graph. Cleared on any successful load.
  const [error, setError] = useState<string | null>(null);
  const nodeIds = useRef<Set<string>>(new Set());
  const linkKeys = useRef<Set<string>>(new Set());
  // id → deepest hops already fetched, so switching to depth 2 re-expands a
  // node that was only fetched at depth 1 (a plain Set silently blocked that).
  const expanded = useRef<Map<string, number>>(new Map());

  const merge = useCallback((incoming: GraphData) => {
    setData((prev) => {
      const nodes = [...prev.nodes];
      const links = [...prev.links];
      for (const n of incoming.nodes) {
        if (!nodeIds.current.has(n.id)) {
          nodeIds.current.add(n.id);
          nodes.push(n);
        }
      }
      for (const l of incoming.links) {
        const k = linkKey(l);
        if (!linkKeys.current.has(k)) {
          linkKeys.current.add(k);
          links.push(l);
        }
      }
      return { nodes, links, meta: incoming.meta };
    });
  }, []);

  const reset = useCallback(() => {
    nodeIds.current = new Set();
    linkKeys.current = new Set();
    expanded.current = new Map();
    setData({ nodes: [], links: [] });
  }, []);

  const loadOverview = useCallback(
    // Default 250 nodes: the cluster layout hides inter-cluster edges and lays
    // nodes out by case, so it stays readable well past the old ~150 hairball
    // ceiling. The higher cap is what lets the smaller cases (David Sánchez/
    // Badajoz, Begoña, Zapatero) show their mid-degree members by default
    // instead of only on expand. Libre mode passes a bigger limit (the whole
    // constellation) — see GraphExplorer.
    async (types?: string[], filters?: GraphFilters, limit = 250) => {
      setLoading(true);
      try {
        reset();
        merge(await fetchOverview(types, limit, filters));
        setError(null);
      } catch {
        setError("No se pudo conectar con la API.");
      } finally {
        setLoading(false);
      }
    },
    [merge, reset],
  );

  const expand = useCallback(
    async (id: string, hops = 1, filters?: GraphFilters, limit = 120) => {
      const prevDepth = expanded.current.get(id) ?? 0;
      if (prevDepth >= hops) return;
      expanded.current.set(id, hops);
      setLoading(true);
      try {
        merge(await fetchNeighborhood(id, hops, limit, filters));
        setError(null);
      } catch {
        // allow retry after a failed expand (restore the previous depth)
        if (prevDepth > 0) expanded.current.set(id, prevDepth);
        else expanded.current.delete(id);
        setError("No se pudo conectar con la API.");
      } finally {
        setLoading(false);
      }
    },
    [merge],
  );

  const focusOnly = useCallback(
    async (id: string, filters?: GraphFilters) => {
      setLoading(true);
      try {
        reset();
        expanded.current.set(id, 1);
        merge(await fetchNeighborhood(id, 1, 120, filters));
        setError(null);
      } catch {
        expanded.current.delete(id);
        setError("No se pudo conectar con la API.");
      } finally {
        setLoading(false);
      }
    },
    [merge, reset],
  );

  // Merge an externally-fetched payload (e.g. /graph/path) into the live graph.
  const addData = merge;

  return {
    data,
    loading,
    error,
    loadOverview,
    expand,
    focusOnly,
    addData,
    isExpanded: (id: string) => expanded.current.has(id),
    // Deepest hops already fetched for a node (0 = not expanded). Lets the UI
    // offer progressive "expand the next ring" and disable it at the cap.
    depthOf: (id: string) => expanded.current.get(id) ?? 0,
  };
}

export type { GraphNode };
