import Link from "next/link";
import { ArrowLeft, BookOpenText, ExternalLink, Network, ShieldAlert } from "lucide-react";
import JsonLd from "@/components/seo/JsonLd";
import { SITE_URL } from "@/lib/site";
import { CATEGORIAS_GLOSARIO, GLOSARIO, terminosDe } from "@/lib/glosario";

/* Glosario judicial (spec 036). Página estática pura (molde: aviso-legal) —
 * todo el contenido vive en lib/glosario.ts. Es el destino de los badges de
 * estado procesal y de los chips de delito del resto del sitio, así que las
 * anclas (#investigado, #malversacion…) son un contrato estable. */

export const metadata = {
  title: "Glosario judicial",
  description:
    "Qué significan imputado, procesado, cohecho, malversación, diligencias previas o sentencia firme — en lenguaje llano y con su artículo legal.",
  alternates: { canonical: "/glosario" },
};

export default function GlosarioPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      {/* JSON-LD: conceptos jurídicos neutrales — nunca personas ni casos. */}
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "DefinedTermSet",
          name: "Glosario judicial de El Entramado",
          url: `${SITE_URL}/glosario`,
          inLanguage: "es",
          hasDefinedTerm: GLOSARIO.map((t) => ({
            "@type": "DefinedTerm",
            "@id": `${SITE_URL}/glosario#${t.id}`,
            name: t.termino,
            description: t.corta,
          })),
        }}
      />

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
          <BookOpenText className="h-3.5 w-3.5" /> Glosario
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">
          La jerga judicial, en llano
        </h1>
        <p className="mt-4 text-lg leading-relaxed text-slate-400">
          Imputado no es culpable, un auto no es una sentencia y «piden 6 años» no es una condena.
          Este glosario explica los términos que aparecen en las fichas, los documentos y las
          noticias del sitio — con su fuente legal cuando la hay.
        </p>
      </header>

      {/* índice */}
      <nav className="mb-12 space-y-3" aria-label="Índice del glosario">
        {CATEGORIAS_GLOSARIO.map((cat) => (
          <div key={cat.id} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <a href={`#cat-${cat.id}`} className="text-sm font-medium text-slate-200 hover:text-accent">
              {cat.titulo}
            </a>
            <span className="flex flex-wrap gap-1.5">
              {terminosDe(cat.id).map((t) => (
                <a
                  key={t.id}
                  href={`#${t.id}`}
                  className="rounded-full glass px-2.5 py-0.5 text-xs text-slate-400 transition hover:border-accent/40 hover:text-accent"
                >
                  {t.termino.split(" — ")[0]}
                </a>
              ))}
            </span>
          </div>
        ))}
      </nav>

      {/* categorías */}
      {CATEGORIAS_GLOSARIO.map((cat) => (
        <section key={cat.id} id={`cat-${cat.id}`} className="mb-12 scroll-mt-24">
          <h2 className="text-2xl font-semibold text-slate-100">{cat.titulo}</h2>
          <p className="mt-1 mb-6 text-sm leading-relaxed text-slate-500">{cat.intro}</p>
          <div className="space-y-4">
            {terminosDe(cat.id).map((t) => (
              <article key={t.id} id={t.id} className="scroll-mt-24 rounded-2xl border border-white/5 bg-white/[0.02] p-5">
                <h3 className="text-base font-semibold text-slate-100">
                  <a href={`#${t.id}`} className="hover:text-accent">
                    {t.termino}
                  </a>
                </h3>
                <p className="mt-1 text-sm font-medium leading-snug text-accent/90">{t.corta}</p>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">{t.larga}</p>
                <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px]">
                  {t.fuente && (
                    <a
                      href={t.fuente.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 font-mono text-slate-500 hover:text-accent"
                    >
                      <ExternalLink className="h-3 w-3" /> {t.fuente.ref}
                    </a>
                  )}
                  {t.relacionados && t.relacionados.length > 0 && (
                    <span className="flex flex-wrap items-center gap-1.5 text-slate-600">
                      <span className="uppercase tracking-[0.16em]">ver</span>
                      {t.relacionados.map((rid) => {
                        const rel = GLOSARIO.find((x) => x.id === rid);
                        if (!rel) return null;
                        return (
                          <a key={rid} href={`#${rid}`} className="text-slate-500 underline decoration-white/15 underline-offset-2 hover:text-accent">
                            {rel.termino.split(" — ")[0]}
                          </a>
                        );
                      })}
                    </span>
                  )}
                </div>
              </article>
            ))}
          </div>
        </section>
      ))}

      {/* recordatorio editorial */}
      <section className="mt-4 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">Por qué importa.</span> La distancia entre
            «investigado» y «condenado» es la presunción de inocencia. Este sitio etiqueta cada
            afirmación con su estado procesal y su fuente precisamente para que esa distancia nunca
            se pierda.{" "}
            <Link href="/aviso-legal" className="text-slate-300 underline decoration-white/20 underline-offset-2 hover:text-accent">
              Aviso legal
            </Link>
            .
          </p>
        </div>
      </section>
    </main>
  );
}
