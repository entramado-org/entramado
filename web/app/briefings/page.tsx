import Link from "next/link";
import type { Metadata } from "next";
import { ArrowLeft, ArrowUpRight, ScrollText } from "lucide-react";

export const metadata: Metadata = {
  title: "Briefings",
  description:
    "Síntesis periodísticas generadas a partir de los documentos judiciales del expediente, con cada afirmación enlazada a su documento y página.",
  alternates: { canonical: "/briefings" },
};
import AnchorChips from "@/components/AnchorChips";
import type { BriefingMeta } from "@/lib/api";

const API_BASE = process.env.API_INTERNAL_BASE || process.env.NEXT_PUBLIC_API_BASE || "http://api:8000";

async function load(): Promise<BriefingMeta[]> {
  try {
    const r = await fetch(`${API_BASE}/briefings`, { cache: "no-store" });
    if (!r.ok) return [];
    const j = await r.json();
    return j.briefings ?? [];
  } catch {
    return [];
  }
}

export const dynamic = "force-dynamic";

export default async function BriefingsIndex() {
  const briefings = await load();
  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <Link href="/" className="mb-8 inline-flex items-center gap-2 text-sm text-slate-400 hover:text-accent">
        <ArrowLeft className="h-4 w-4" /> Volver
      </Link>
      <p className="mb-3 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
        <ScrollText className="h-3.5 w-3.5" /> Briefings · síntesis del corpus
      </p>
      <h1 className="mb-3 text-4xl font-bold leading-tight text-slate-50">Lo que dicen los papeles</h1>
      <p className="mb-10 max-w-2xl text-base leading-relaxed text-slate-400">
        Resúmenes periodísticos generados a partir de los documentos del expediente. Cada uno
        distingue hechos de indicios y enlaza cada afirmación a su documento y página.
      </p>
      {briefings.length === 0 ? (
        <p className="text-sm text-slate-500">
          Aún no hay briefings. Genéralos con: <code>make briefings</code>.
        </p>
      ) : (
        <ul className="space-y-3">
          {briefings.map((b) => (
            <li key={b.slug}>
              <Link
                href={`/briefings/${b.slug}`}
                className="group block rounded-2xl glass p-5 transition hover:border-accent/40 hover:bg-white/5"
              >
                <div className="flex items-start justify-between gap-3">
                  <span className="text-lg font-medium text-slate-100 group-hover:text-accent">
                    {b.title}
                  </span>
                  <ArrowUpRight className="mt-0.5 h-5 w-5 shrink-0 text-slate-600 transition group-hover:text-accent" />
                </div>
                {b.dek && <p className="mt-1.5 text-sm leading-snug text-slate-400">{b.dek}</p>}
                <AnchorChips ids={b.anchors} max={5} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
