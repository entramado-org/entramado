import Link from "next/link";
import { Mail, ShieldAlert } from "lucide-react";
import { CONTACTO } from "@/lib/site";

export const metadata = {
  title: "Contacto y rectificaciones",
  description:
    "Cómo pedir la rectificación o la retirada de un titular, cómo avisar de un error y cómo proponer una pieza o un documento oficial. Escribe a contacto@entramado.org.",
  alternates: { canonical: "/contribuir" },
};

function Paso({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent">
        {n}
      </span>
      <span className="leading-relaxed text-slate-300">{children}</span>
    </li>
  );
}

export default function Contribuir() {
  return (
    <main className="relative mx-auto max-w-3xl px-6 py-12">
      <p className="mb-4 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
        <Mail className="h-3.5 w-3.5" /> Contacto
      </p>
      <h1 className="text-4xl font-bold tracking-tight text-slate-50">
        Contacto y <span className="text-accent text-glow">rectificaciones</span>
      </h1>
      <p className="mt-4 text-lg leading-relaxed text-slate-400">
        Este sitio republica titulares de medios y enlaza documentos oficiales; no redacta ni afirma nada por su
        cuenta, salvo en la sección institucional, que es análisis y opinión propios. Si un enlace es inexacto,
        está desactualizado o no debería figurar, se corrige o se retira escribiendo a{" "}
        <a href={`mailto:${CONTACTO}`} className="text-accent hover:underline">
          {CONTACTO}
        </a>
        .
      </p>

      {/* rectificación / retirada */}
      <section className="mt-10">
        <h2 className="mb-1 text-xl font-semibold text-slate-100">Pedir la rectificación o la retirada de un ítem</h2>
        <p className="mb-4 text-sm text-slate-500">
          Las peticiones se atienden con prioridad sobre cualquier otra; los plazos están en el{" "}
          <Link href="/aviso-legal" className="underline decoration-white/20 underline-offset-2 hover:text-accent">
            aviso legal
          </Link>
          .
        </p>
        <ol className="space-y-3 text-sm">
          <Paso n={1}>
            <strong className="text-slate-200">Escribe a {CONTACTO}</strong> indicando el ítem (el enlace de la
            pieza o su titular).
          </Paso>
          <Paso n={2}>
            <strong className="text-slate-200">Explica qué falla</strong>: que el titular es inexacto, que ha
            quedado desactualizado (por ejemplo, por un sobreseimiento o una absolución posteriores) o que, por
            otro motivo, no debería figurar. Aporta la referencia que lo acredite.
          </Paso>
          <Paso n={3}>
            <strong className="text-slate-200">Se revisa y se responde.</strong> Un ítem retirado deja de aparecer
            en el sitio.
          </Paso>
        </ol>
        <p className="mt-4 text-sm leading-relaxed text-slate-500">
          El titular es el de la pieza que publicó el medio. Si lo que es inexacto es el contenido de esa pieza,
          quien puede rectificarla es el medio; aquí se retira el enlace o se actualiza el titular cuando el medio
          ya lo ha corregido.
        </p>
      </section>

      {/* proponer */}
      <section className="mt-10">
        <h2 className="mb-2 text-xl font-semibold text-slate-100">Avisar de un error o proponer una pieza</h2>
        <p className="text-sm leading-relaxed text-slate-400">
          También puedes avisar de un enlace roto o de un titular que ha cambiado, o proponer una pieza de prensa
          o un documento oficial: envía el enlace. Un ítem nuevo entra en el sitio solo después de comprobar que
          la pieza existe y que el titular es el que muestra su medio. Solo entran titulares cuyas personas
          nombradas son parte del procedimiento o cargos públicos, y sin motes.
        </p>
      </section>

      {/* presunción de inocencia */}
      <section className="mt-12 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">Presunción de inocencia.</span> Los titulares son de los
            medios enlazados y no una afirmación de este sitio. Que una persona aparezca en un titular no significa
            que sea culpable: rige la presunción de inocencia hasta que exista sentencia firme.
          </p>
        </div>
      </section>
    </main>
  );
}
