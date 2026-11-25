import Link from "next/link";
import { ArrowRight, FileText, Landmark, Mail, Newspaper, ShieldAlert } from "lucide-react";
import ItemList from "@/components/ItemList";
import { ITEMS } from "@/lib/datos";

export const metadata = {
  alternates: { canonical: "/" },
};

const ULTIMOS = 10;

export default function Landing() {
  return (
    <main className="mx-auto max-w-5xl px-6 py-14">
      {/* qué es */}
      <section className="animate-fade-in">
        <p className="mb-4 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
          <Newspaper className="h-3.5 w-3.5" /> Agregador de prensa y documentos oficiales
        </p>
        <h1 className="max-w-3xl text-5xl font-bold leading-[1.05] tracking-tight text-slate-50 sm:text-6xl">
          El <span className="text-accent text-glow">entramado</span>, en titulares enlazados.
        </h1>
        <p className="mt-5 max-w-2xl text-lg leading-relaxed text-slate-400">
          El Entramado republica los titulares que los medios han publicado sobre causas judiciales de
          relevancia política en España y enlaza los documentos oficiales en la web del organismo que los emitió.{" "}
          <span className="text-slate-300">No redacta ni afirma nada por su cuenta, salvo en la sección
          institucional, que es análisis y opinión propios.</span> Cada titular es el de la pieza enlazada,
          con su medio y su fecha. Rige la presunción de inocencia.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link
            href="/ultima-hora"
            className="inline-flex items-center gap-2 rounded-xl bg-accent px-5 py-3 font-medium text-ink-950 transition hover:bg-accent-glow"
          >
            Toda la cronología <ArrowRight className="h-4 w-4" />
          </Link>
          <Link
            href="/documentos"
            className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
          >
            <FileText className="h-4 w-4" /> Documentos oficiales
          </Link>
          <Link
            href="/institucional"
            className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
          >
            <Landmark className="h-4 w-4" /> Contexto institucional
          </Link>
          <Link
            href="/contribuir"
            className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
          >
            <Mail className="h-4 w-4" /> Contacto y rectificaciones
          </Link>
        </div>
      </section>

      {/* últimos ítems */}
      <section className="mt-20">
        <div className="mb-6 flex items-end justify-between">
          <div>
            <p className="mb-1 inline-flex items-center gap-1.5 text-xs uppercase tracking-widest text-[#fdba74]">
              <Newspaper className="h-3.5 w-3.5" /> Última hora
            </p>
            <h2 className="text-2xl font-semibold text-slate-100">Lo último que se ha publicado</h2>
          </div>
          <Link href="/ultima-hora" className="text-sm text-slate-400 hover:text-accent">
            Ver todo →
          </Link>
        </div>
        <ItemList items={ITEMS.slice(0, ULTIMOS)} />
      </section>

      {/* recordatorio */}
      <section className="mt-16 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">Presunción de inocencia.</span> Los titulares son los
            de cada medio, no una afirmación de este sitio: que alguien aparezca en uno no significa que sea
            culpable, y rige hasta que exista sentencia firme. Si algo es inexacto o ha quedado desactualizado, o quieres que se retire un enlace,{" "}
            <Link href="/contribuir" className="underline decoration-white/20 underline-offset-2 hover:text-accent">
              escríbenos
            </Link>
            .
          </p>
        </div>
      </section>
    </main>
  );
}
