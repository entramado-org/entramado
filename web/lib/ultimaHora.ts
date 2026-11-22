// «Última hora» feed access (spec 044). The items themselves live in
// `pipeline/data/ultima_hora/feed.yaml` (versioned, served at runtime by
// GET /ultima-hora with an mtime cache) so a merged feed PR is visible
// WITHOUT rebuilding the web image. This module keeps the item type, the
// caseTag→color mapping and the server-side loader; client components fetch
// via `fetchUltimaHora()` in lib/api.ts instead.

export interface UltimaHoraItem {
  iso: string; // YYYY-MM-DD, for sorting (most recent first)
  date: string; // display label, e.g. "12 jun 2026"
  headline: string;
  dek: string; // neutral one/two-line summary, presumption-of-innocence tone
  outlet: string; // attributing media outlet
  url: string; // link to the article
  quote?: string; // optional verbatim snippet
  status?: string; // optional procedural stage (investigado/imputado/acusado…)
  caseTag?: string;
  entities: string[]; // graph entity_ids this item is about
  focus?: string; // -> /explore?focus={entity_id}
  briefingSlug?: string; // -> /briefings/{slug}
}

// Palette echoes web/lib/timeline.ts so the chips don't introduce new colors.
// The feed data carries only `caseTag`; the color is derived here (an unknown
// tag from a future vigía item degrades to a neutral slate, never breaks).
const C = {
  obra: "#818cf8", // indigo — obra pública
  judicial: "#f87171", // red — fase judicial
  politica: "#a78bfa", // violet — político/institucional
  dinero: "#4ade80", // green — rastro del dinero
};

export const CASE_TAG_COLORS: Record<string, string> = {
  "Begoña Gómez": C.politica,
  "Caso Leire": C.politica,
  "David Sánchez": C.politica,
  "Financiación PSOE": C.politica,
  "Plus Ultra": C.politica,
  "Político": C.politica,
  "Judicial": C.judicial,
  "Obra pública": C.obra,
  "Dinero": C.dinero,
};

const NEUTRAL = "#94a3b8"; // slate-400 — unknown/new caseTag

export function caseColor(tag?: string): string {
  return (tag && CASE_TAG_COLORS[tag]) || NEUTRAL;
}

const API_BASE =
  process.env.API_INTERNAL_BASE || process.env.NEXT_PUBLIC_API_BASE || "http://api:8000";

/** Server-side loader (RSC). No `revalidate` → no-store (force-dynamic pages);
 *  with `revalidate` → ISR fetch cache (case/entity fichas). Degrades to []
 *  when the API is down, never throws. */
export async function loadUltimaHora(revalidate?: number): Promise<UltimaHoraItem[]> {
  try {
    const r = await fetch(
      `${API_BASE}/ultima-hora`,
      revalidate === undefined ? { cache: "no-store" } : { next: { revalidate } },
    );
    if (!r.ok) return [];
    const j = await r.json();
    return j.items ?? [];
  } catch {
    return [];
  }
}
