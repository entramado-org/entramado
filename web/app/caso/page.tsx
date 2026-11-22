import Link from "next/link";
import { ArrowLeft, FolderOpen, Network, Users } from "lucide-react";
import { getCasos } from "@/lib/casos";
import { CASE_STATUS_META } from "@/lib/graphStyle";

/* Índice de casos curados (spec 038). force-dynamic (como el índice /entidad):
 * en el build Docker la API no responde y el prerender congelaría el índice
 * vacío; el fetch de getCasos() sigue cacheado 1 h. */

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Los casos",
  description:
    "Los casos de corrupción del corpus, uno a uno: implicados con su estado procesal, cronología citada, documentos judiciales y flujos de dinero.",
  alternates: { canonical: "/caso" },
};

export default async function CasosIndexPage() {
  const casos = await getCasos();

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      {/* nav */}
      <div className="mb-8 flex items-center justify-between">
        <Link href="/" className="inline-flex items-center gap-2 text-sm text-slate-400 hover:text-accent">
          <ArrowLeft className="h-4 w-4" /> Inicio
        </Link>
        <Link
          href="/explore"
          className="inline-flex items-center gap-2 rounded-lg glass px-3 py-1.5 text-sm text-slate-200 hover:bg-white/5"
        >
          <Network className="h-4 w-4" /> Abrir el grafo
        </Link>
      </div>

      {/* header */}
      <header className="mb-10">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
          <FolderOpen className="h-3.5 w-3.5" /> Casos
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">
          Los casos, uno a uno
        </h1>
        <p className="mt-4 text-lg leading-relaxed text-slate-400">
          Cada ficha reúne lo que el grafo sabe de un caso: quién está implicado y en qué fase
          procesal, la cronología con sus citas, los documentos judiciales y el rastro del dinero.
        </p>
      </header>

      <ul className="grid gap-4 sm:grid-cols-2">
        {casos.map((c) => {
          const st = CASE_STATUS_META[c.estadoKind];
          return (
            <li key={c.slug}>
              <Link
                href={`/caso/${c.slug}`}
                className="group flex h-full flex-col rounded-2xl glass p-5 transition hover:border-accent/40 hover:bg-white/5"
              >
                <div className="flex items-start justify-between gap-3">
                  <h2 className="text-[16px] font-semibold leading-snug text-slate-100 group-hover:text-accent">
                    {c.titulo}
                  </h2>
                  {st && (
                    <span
                      className="shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium"
                      style={{ color: st.color, borderColor: `${st.color}55` }}
                    >
                      {st.label}
                    </span>
                  )}
                </div>
                <p className="mt-2 flex-1 text-[13px] leading-relaxed text-slate-400">{c.dek}</p>
                <div className="mt-3 flex items-center justify-between border-t border-white/5 pt-2.5 text-[11px] text-slate-600">
                  <span className="inline-flex items-center gap-1">
                    <Users className="h-3 w-3" /> {c.nImplicados} implicados
                  </span>
                  {c.updated && <span className="tabular-nums">Actualizado {c.updated}</span>}
                </div>
              </Link>
            </li>
          );
        })}
      </ul>

      <p className="mt-10 text-xs leading-relaxed text-slate-600">
        La selección de qué entidades y documentos componen cada caso es editorial; los estados
        procesales y las citas salen del grafo en vivo. Rige la presunción de inocencia.{" "}
        <Link href="/aviso-legal" className="hover:text-accent">
          Aviso legal
        </Link>
        .
      </p>
    </main>
  );
}
