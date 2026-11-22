import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

/* Served at /sitemap.xml (spec 035): static pages + every briefing + every
 * indexable entity ficha.
 *
 * GOTCHA (Next 14 metadata routes): without an uncached fetch this route is
 * evaluated once at `next build` — where the API container is unreachable —
 * and the sitemap would freeze as statics-only forever. `cache: "no-store"`
 * keeps it dynamic; the API answers from Neo4j props, so per-request cost is
 * one cheap query and crawlers fetch sitemaps rarely. */
const API_BASE =
  process.env.API_INTERNAL_BASE || process.env.NEXT_PUBLIC_API_BASE || "http://api:8000";

const STATIC_PATHS = [
  "",
  "/explore",
  "/entidad",
  "/documentos",
  "/flujos",
  "/institucional",
  "/patrones",
  "/ultima-hora",
  "/briefings",
  "/conectar",
  "/contribuir",
  "/caso",
  "/investigados",
  "/glosario",
  "/aviso-legal",
];

type BriefingRow = { slug: string; updated?: string | null };
type EntityRow = { slug: string; summaryUpdated?: string | null };
type CaseRow = { slug: string; updated?: string | null };

async function fetchJson<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null; // API down → statics-only sitemap beats a 500
  }
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const entries: MetadataRoute.Sitemap = STATIC_PATHS.map((p) => ({
    url: `${SITE_URL}${p || "/"}`,
    changeFrequency: p === "/ultima-hora" || p === "" ? "daily" : "weekly",
    priority: p === "" ? 1 : 0.7,
  }));

  const briefings = await fetchJson<{ briefings: BriefingRow[] }>("/briefings");
  for (const b of briefings?.briefings ?? []) {
    entries.push({
      url: `${SITE_URL}/briefings/${b.slug}`,
      lastModified: b.updated ?? undefined,
      changeFrequency: "weekly",
      priority: 0.8,
    });
  }

  const casos = await fetchJson<{ cases: CaseRow[] }>("/cases");
  for (const c of casos?.cases ?? []) {
    entries.push({
      url: `${SITE_URL}/caso/${c.slug}`,
      lastModified: c.updated ?? undefined,
      changeFrequency: "weekly",
      priority: 0.8,
    });
  }

  const fichas = await fetchJson<{ entities: EntityRow[] }>("/entities/indexable");
  for (const e of fichas?.entities ?? []) {
    entries.push({
      url: `${SITE_URL}/entidad/${e.slug}`,
      lastModified: e.summaryUpdated ?? undefined,
      changeFrequency: "weekly",
      priority: 0.6,
    });
  }

  return entries;
}
