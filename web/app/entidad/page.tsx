import Link from "next/link";
import type { Metadata } from "next";
import { ArrowLeft, ArrowUpRight, Network, Users } from "lucide-react";
import { getIndexables } from "@/lib/fichas";
import type { IndexableEntity } from "@/lib/fichas";

/* Índice de fichas públicas (spec 035): el hub de crawl de /entidad/[slug] y
 * una portada útil del reparto central. force-dynamic (patrón del repo) para
 * no congelar la lista vacía del build Docker; los datos vienen del data cache
 * ISR de getIndexables(), así que el coste por hit es ~0. */

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Protagonistas de los casos de corrupción",
  description:
    "Las personas, empresas y organismos centrales del caso Koldo, el caso Cerdán, Ábalos, Aldama, Begoña Gómez, Plus Ultra y el caso Leire: una ficha por protagonista con sus conexiones documentadas y cita de fuente.",
  alternates: { canonical: "/entidad" },
};

const GROUPS: { type: IndexableEntity["type"]; title: string }[] = [
  { type: "Person", title: "Personas" },
  { type: "Company", title: "Empresas" },
  { type: "Organization", title: "Organismos y organizaciones" },
];

export default async function EntidadIndex() {
  const all = await getIndexables();
  return (
    <main className="mx-auto max-w-4xl px-6 py-12">
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

      <header className="mb-10">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
          <Users className="h-3.5 w-3.5" /> Protagonistas
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">
          Quién es quién en el entramado
        </h1>
        <p className="mt-4 max-w-2xl text-lg leading-relaxed text-slate-400">
          El reparto central de los casos: cada ficha resume el papel de la persona, empresa u
          organismo y sus conexiones documentadas, con cita de fuente y estado procesal. Rige la
          presunción de inocencia.
        </p>
      </header>

      {all.length === 0 ? (
        <p className="text-sm text-slate-500">Las fichas no están disponibles en este momento.</p>
      ) : (
        GROUPS.map(({ type, title }) => {
          const group = all.filter((e) => e.type === type);
          if (group.length === 0) return null;
          return (
            <section key={type} className="mb-12">
              <h2 className="mb-4 text-xl font-semibold text-slate-100">
                {title} <span className="text-sm font-normal text-slate-500">({group.length})</span>
              </h2>
              <ul className="grid gap-3 sm:grid-cols-2">
                {group.map((e) => (
                  <li key={e.id}>
                    <Link
                      href={`/entidad/${e.slug}`}
                      className="group flex h-full flex-col rounded-2xl glass p-4 transition hover:border-accent/40 hover:bg-white/5"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <span className="text-sm font-medium leading-snug text-slate-100 group-hover:text-accent">
                          {e.name}
                        </span>
                        <ArrowUpRight className="mt-0.5 h-4 w-4 shrink-0 text-slate-600 transition group-hover:text-accent" />
                      </div>
                      {e.role && <p className="mt-1 text-xs leading-snug text-slate-400">{e.role}</p>}
                      {e.teaser && (
                        <p className="mt-1.5 line-clamp-2 text-xs leading-snug text-slate-500">{e.teaser}</p>
                      )}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          );
        })
      )}
    </main>
  );
}
