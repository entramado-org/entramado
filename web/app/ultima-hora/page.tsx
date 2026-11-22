import Link from "next/link";
import { ArrowLeft, Network, Newspaper, ShieldAlert, ExternalLink, FileText, Users } from "lucide-react";
import { caseColor, loadUltimaHora } from "@/lib/ultimaHora";
import { getIndexables } from "@/lib/fichas";

// force-dynamic: el mapa id→ficha viene del API (data cache ISR); sin esto la
// página se congelaría en el build Docker (API inaccesible) sin los enlaces.
export const dynamic = "force-dynamic";

export const metadata = {
  title: "Última hora",
  description:
    "Desarrollos recientes de los casos que, de momento, solo constan en prensa: peticiones de pena, nuevos informes de la UCO, imputaciones. Cada dato citado a su medio. Rige la presunción de inocencia.",
  alternates: { canonical: "/ultima-hora" },
};

/* ────────────────────────────────────────────────────────────────────────────
 * Esta página recoge ÚLTIMA HORA: hechos que aún no tienen documento judicial
 * descargable y que, por tanto, NO entran en el grafo source-anchored. Se
 * muestran como prensa, fechados y atribuidos, con presunción de inocencia.
 * Fuente de datos: GET /ultima-hora (pipeline/data/ultima_hora/feed.yaml servido
 * en runtime, spec 044 — espejo editorial del overlay de grafo ultima_hora.yaml).
 * ──────────────────────────────────────────────────────────────────────────── */

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export default async function UltimaHoraPage() {
  const [indexables, items] = await Promise.all([getIndexables(), loadUltimaHora()]);
  const bySlug = new Map(indexables.map((e) => [e.id, e]));
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
      <header className="mb-8">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
          <Newspaper className="h-3.5 w-3.5" /> Última hora
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">
          Desarrollos recientes
        </h1>
        <p className="mt-4 text-lg leading-relaxed text-slate-400">
          Lo que avanza en los casos y todavía <span className="text-slate-300">no tiene un documento
          judicial público</span>: peticiones de pena, nuevos informes de la UCO, imputaciones. No entra
          en el grafo —que solo recoge lo documentado—, sino que se muestra aquí <span className="text-slate-300">
          como prensa, fechado y con enlace al medio</span>. Rige la presunción de inocencia.
        </p>
      </header>

      {/* feed */}
      <ol className="relative border-l border-white/10 pl-6">
        {items.map((it) => (
          <li key={`${it.iso}-${it.headline}`} className="mb-8">
            <span
              className="absolute -left-[7px] mt-1 h-3 w-3 rounded-full"
              style={{ background: "#fb923c", boxShadow: "0 0 10px #fb923c" }}
            />
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-xs font-medium uppercase tracking-widest text-[#fdba74]">{it.date}</p>
              {it.caseTag && (
                <span
                  className="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium"
                  style={{ color: caseColor(it.caseTag), borderColor: `${caseColor(it.caseTag)}66` }}
                >
                  {it.caseTag}
                </span>
              )}
              {it.status && (
                <span className="rounded-full bg-orange-500/15 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-orange-300">
                  {it.status} · presunto
                </span>
              )}
            </div>
            <h2 className="mt-1.5 text-lg font-semibold leading-snug text-slate-100">{it.headline}</h2>
            <p className="mt-1 leading-relaxed text-slate-300">{it.dek}</p>
            {it.quote && (
              <p className="mt-2 border-l-2 border-orange-400/30 pl-3 text-sm italic leading-snug text-slate-500">
                &ldquo;{it.quote}&rdquo;
              </p>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
              <a
                href={it.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-[#fdba74] hover:underline"
              >
                <ExternalLink className="h-3.5 w-3.5" /> {it.outlet}
              </a>
              {it.briefingSlug && (
                <Link
                  href={`/briefings/${it.briefingSlug}`}
                  className="inline-flex items-center gap-1 text-slate-400 hover:text-accent"
                >
                  <FileText className="h-3.5 w-3.5" /> Leer el briefing
                </Link>
              )}
              {it.focus && (
                <Link
                  href={`/explore?focus=${encodeURIComponent(it.focus)}`}
                  className="inline-flex items-center gap-1 text-slate-400 hover:text-accent"
                >
                  <Network className="h-3.5 w-3.5" /> Ver en el grafo
                </Link>
              )}
              {(it.entities ?? [])
                .map((id) => bySlug.get(id))
                .filter((e): e is NonNullable<typeof e> => Boolean(e))
                .slice(0, 3)
                .map((e) => (
                  <Link
                    key={e.id}
                    href={`/entidad/${e.slug}`}
                    className="inline-flex items-center gap-1 text-slate-400 hover:text-accent"
                  >
                    <Users className="h-3.5 w-3.5" /> Ficha: {e.name}
                  </Link>
                ))}
            </div>
          </li>
        ))}
      </ol>

      {/* disclaimer */}
      <section className="mt-10 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">Prensa de procedimientos en curso.</span> Estos
            desarrollos proceden de medios y no de documentos judiciales: no están confirmados como hechos
            probados y no constituyen una declaración de culpabilidad. Cada uno cita su medio y enlaza al
            artículo. Cuando exista el documento oficial, el dato pasará al grafo con su fuente. Rige la
            presunción de inocencia.
          </p>
        </div>
      </section>
    </main>
  );
}
