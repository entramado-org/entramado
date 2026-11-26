import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

export const dynamic = "force-static";

/* Served at /sitemap.xml (spec 035): solo las páginas que existen (spec 077). */
const STATIC_PATHS = ["", "/ultima-hora", "/documentos", "/contribuir", "/aviso-legal"];

export default function sitemap(): MetadataRoute.Sitemap {
  return STATIC_PATHS.map((p) => ({
    url: `${SITE_URL}${p || "/"}`,
    changeFrequency: p === "/ultima-hora" || p === "" ? "daily" : "weekly",
    priority: p === "" ? 1 : 0.7,
  }));
}
