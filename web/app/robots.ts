import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

export const dynamic = "force-static";

/* Served at /robots.txt (spec 035). Sin `disallow`: las rutas retiradas (spec 077) deben poder
 * rastrearse para que el buscador vea el 404 y las desindexe. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/" }],
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
