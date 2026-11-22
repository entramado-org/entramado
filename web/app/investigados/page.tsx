import Link from "next/link";
import { ArrowLeft, Gavel, Network, ShieldAlert } from "lucide-react";
import InvestigadosDirectory from "@/components/InvestigadosDirectory";
import { getAccused } from "@/lib/fichas";

/* Directorio de investigados (spec 037): toda entidad con cargos en el grafo,
 * con su estado procesal exacto y cada cargo citado a su fuente. Editorial:
 * nada de penas teóricas ni agregados sensacionalistas — la distancia
 * investigado↔condenado ES el contenido. Sin JSON-LD con personas.
 * force-dynamic (como el índice /entidad): en el build Docker la API no es
 * alcanzable y un prerender estático congelaría la página VACÍA; los datos
 * siguen cacheados 1 h a nivel de fetch (apiJson). */

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Investigados y acusados — estado procesal, con fuentes",
  description:
    "Quién tiene cargos en los casos de corrupción que rodean al Gobierno en España y en qué fase exacta está cada uno: investigado, imputado, procesado, acusado o condenado. Cada cargo, con su documento y página.", // ontologia-ok: texto editorial (SEO), no es una lista de vocabulario
  alternates: { canonical: "/investigados" },
};

export default async function InvestigadosPage() {
  const entities = await getAccused();

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
          <Gavel className="h-3.5 w-3.5" /> Investigados
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">
          Quién tiene cargos, y en qué fase exacta
        </h1>
        <p className="mt-4 text-lg leading-relaxed text-slate-400">
          Todas las personas y entidades con cargos en el grafo, con su estado procesal y cada
          delito citado a su documento de origen. Filtra por estado o delito; los términos enlazan
          al <Link href="/glosario" className="text-accent hover:underline">glosario</Link>.
        </p>
      </header>

      {/* disclaimer — antes de la lista, no después */}
      <section className="mb-8 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">Una imputación no es una condena.</span>{" "}
            Hasta la sentencia firme rige la presunción de inocencia: figurar aquí significa que un
            procedimiento en curso atribuye cargos a esa persona, no que sea culpable. El estado de
            cada cargo (
            <Link href="/glosario#investigado" className="text-slate-300 underline decoration-white/20 underline-offset-2 hover:text-accent">
              investigado
            </Link>
            ,{" "}
            <Link href="/glosario#imputado" className="text-slate-300 underline decoration-white/20 underline-offset-2 hover:text-accent">
              imputado
            </Link>
            ,{" "}
            <Link href="/glosario#acusado" className="text-slate-300 underline decoration-white/20 underline-offset-2 hover:text-accent">
              acusado
            </Link>
            …) es parte esencial del dato. Aquí no se suman penas ni se especula con años de cárcel.
          </p>
        </div>
      </section>

      <InvestigadosDirectory entities={entities} />

      <footer className="mt-10 border-t border-white/5 pt-6 text-xs text-slate-600">
        Fuentes: documentos judiciales del corpus (citados por documento y página), escritos de
        acusación curados y, cuando se indica, prensa atribuida a su medio.{" "}
        <Link href="/aviso-legal" className="hover:text-accent">
          Aviso legal y vía de rectificación
        </Link>
        .
      </footer>
    </main>
  );
}
