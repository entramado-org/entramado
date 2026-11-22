import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ArrowLeft, Network } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Briefing } from "@/lib/api";
import JsonLd from "@/components/seo/JsonLd";
import { SITE_URL } from "@/lib/site";
import { getIndexables } from "@/lib/fichas";
import { anchorLabel } from "@/lib/graphStyle";

const API_BASE = process.env.API_INTERNAL_BASE || process.env.NEXT_PUBLIC_API_BASE || "http://api:8000";

async function load(slug: string): Promise<Briefing | null> {
  try {
    const r = await fetch(`${API_BASE}/briefing/${encodeURIComponent(slug)}`, { cache: "no-store" });
    if (!r.ok) return null;
    return r.json();
  } catch {
    return null;
  }
}

export const dynamic = "force-dynamic";

export async function generateMetadata(props: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const params = await props.params;
  const b = await load(params.slug);
  if (!b) return { title: "Briefing no encontrado" };
  return {
    title: b.title,
    description: b.dek || undefined,
    alternates: { canonical: `/briefings/${b.slug}` },
    openGraph: {
      title: b.title,
      description: b.dek || undefined,
      type: "article",
      url: `/briefings/${b.slug}`,
      // Un openGraph propio reemplaza al del layout → declarar la imagen.
      images: "/opengraph-image.png",
    },
  };
}

export default async function BriefingPage(props: { params: Promise<{ slug: string }> }) {
  const params = await props.params;
  const b = await load(params.slug);
  if (!b) notFound();
  // Protagonistas del briefing: los anchors con ficha pública enlazan a ella
  // (interlinking spec 035); el resto, al explorador con foco.
  const indexables = await getIndexables();
  const slugById = new Map(indexables.map((e) => [e.id, e.slug]));
  const nameById = new Map(indexables.map((e) => [e.id, e.name]));
  const anchors = (b.anchors ?? []).slice(0, 8);
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      {/* Article (no NewsArticle): síntesis editorial, no medio registrado.
          Solo identidad y fechas — el matiz editorial vive en la prosa. */}
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "Article",
          headline: b.title,
          description: b.dek || undefined,
          dateModified: b.updated || undefined,
          inLanguage: "es",
          mainEntityOfPage: `${SITE_URL}/briefings/${b.slug}`,
          author: { "@type": "Organization", name: "El Entramado", url: SITE_URL },
          publisher: { "@type": "Organization", name: "El Entramado", url: SITE_URL },
        }}
      />
      <div className="mb-8 flex items-center justify-between">
        <Link href="/briefings" className="inline-flex items-center gap-2 text-sm text-slate-400 hover:text-accent">
          <ArrowLeft className="h-4 w-4" /> Briefings
        </Link>
        <Link
          href="/explore"
          className="inline-flex items-center gap-2 rounded-lg glass px-3 py-1.5 text-sm text-slate-200 hover:bg-white/5"
        >
          <Network className="h-4 w-4" /> Abrir el grafo
        </Link>
      </div>
      {anchors.length > 0 && (
        <div className="mb-6 flex flex-wrap items-center gap-2">
          <span className="text-[11px] uppercase tracking-widest text-slate-600">Protagonistas</span>
          {anchors.map((id) => {
            const slug = slugById.get(id);
            const label = nameById.get(id) ?? anchorLabel(id).label;
            return slug ? (
              <Link
                key={id}
                href={`/entidad/${slug}`}
                className="rounded-full glass px-2.5 py-1 text-xs text-slate-200 transition hover:border-accent/40 hover:text-accent"
              >
                {label}
              </Link>
            ) : (
              <Link
                key={id}
                href={`/explore?focus=${encodeURIComponent(id)}`}
                className="rounded-full glass px-2.5 py-1 text-xs text-slate-400 transition hover:text-accent"
              >
                {label}
              </Link>
            );
          })}
        </div>
      )}
      <article
        className="prose prose-invert max-w-none
                   prose-headings:font-semibold prose-headings:tracking-tight prose-headings:text-slate-100
                   prose-h1:text-4xl prose-h1:mb-4 prose-h1:leading-tight
                   prose-h2:mt-10 prose-h2:text-accent prose-h2:text-xs prose-h2:uppercase prose-h2:tracking-widest
                   prose-p:text-slate-300 prose-p:leading-relaxed
                   prose-strong:text-slate-100
                   prose-li:text-slate-300
                   prose-blockquote:border-accent/40 prose-blockquote:text-slate-400
                   prose-em:text-slate-400"
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{b.content}</ReactMarkdown>
      </article>
    </main>
  );
}
