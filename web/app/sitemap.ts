import type { MetadataRoute } from "next";
import { CASOS } from "@/lib/datos";
import { SITE_URL } from "@/lib/site";

export const dynamic = "force-static";

/* Served at /sitemap.xml (spec 035): solo las páginas que existen (spec 077). */
const STATIC_PATHS = ["", "/ultima-hora", "/documentos", "/institucional", "/contribuir", "/aviso-legal"];

export default function sitemap(): MetadataRoute.Sitemap {
  const entries: MetadataRoute.Sitemap = STATIC_PATHS.map((p) => ({
    url: `${SITE_URL}${p || "/"}`,
    changeFrequency: p === "/ultima-hora" || p === "" ? "daily" : "weekly",
    priority: p === "" ? 1 : 0.7,
  }));
  for (const c of CASOS) {
    entries.push({ url: `${SITE_URL}/caso/${c.slug}`, changeFrequency: "daily", priority: 0.8 });
  }
  return entries;
}
