import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

/* Served at /robots.txt (spec 035). /api and /mcp are machine endpoints —
 * crawling them wastes budget and could hammer Neo4j. Everything else is
 * public content. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: ["/api/", "/mcp"] }],
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
