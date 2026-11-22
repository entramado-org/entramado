import { edgeStatusRank } from "./graphStyle";
import type { EdgeFilters, GraphLink, SourceKind } from "./types";

/**
 * Single source of truth for "is this edge hidden by the active render-level
 * filters?" — relation type (`hiddenRels`), source layer (`hiddenKinds`) or
 * procedural status (`minStatusRank`). Used both by the canvas (to draw hidden
 * edges transparent) and by the explorer (to detect orphan nodes — those left
 * with no visible edge once these filters apply). spec 019.
 *
 * Note: per-pair collapse (one representative line per pair) is a *visual* dedup,
 * not a hide — it deliberately lives in the canvas and is NOT considered here, so
 * a node connected only by collapsed-duplicate edges is never treated as orphan.
 */
export function isLinkHidden(l: GraphLink, edgeFilters?: EdgeFilters): boolean {
  if (!edgeFilters) return false;
  const { hiddenRels, hiddenKinds, minStatusRank } = edgeFilters;
  if (hiddenRels.size === 0 && hiddenKinds.size === 0 && minStatusRank === 0) return false;
  const kind = (l.source_kind ?? "sin_procedencia") as SourceKind;
  if (hiddenRels.has(l.type) || hiddenKinds.has(kind)) return true;
  if (minStatusRank > 0) {
    // Only accusation-bearing edges are status-filterable; rank 0 means "not an
    // accusation edge" and is never hidden by this filter.
    const rank = edgeStatusRank(l.type, l.status);
    return rank > 0 && rank < minStatusRank;
  }
  return false;
}
