import Link from "next/link";
import {
  ArrowLeft,
  ArrowRight,
  Code2,
  ExternalLink,
  FileSearch,
  Github,
  GitPullRequest,
  Mail,
  Scale,
  ShieldAlert,
} from "lucide-react";
import { REPO_PUBLIC, REPO_URL } from "@/lib/site";

export const metadata = {
  title: "Contribuye al proyecto",
  description:
    "El Entramado es código abierto: el código, las capas de datos editoriales y el censo del corpus son públicos en GitHub. Propón tramas nuevas y correcciones con fuente por correo, o mejora el código.",
  alternates: { canonical: "/contribuir" },
};

/* El repo es la fuente canónica de las reglas de contribución (CONTRIBUTING.md,
 * BACKLOG.md, data/SOURCES.md); esta página las destila en formato amigable y
 * enlaza — no las duplica. */

const CONTRIBUTING_URL = `${REPO_URL}/blob/main/CONTRIBUTING.md`;
const BACKLOG_URL = `${REPO_URL}/blob/main/BACKLOG.md`;
const SOURCES_URL = `${REPO_URL}/blob/main/data/SOURCES.md`;
const CODE_OF_CONDUCT_URL = `${REPO_URL}/blob/main/CODE_OF_CONDUCT.md`;

function Regla({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent">
        {n}
      </span>
      <span className="leading-relaxed text-slate-300">{children}</span>
    </li>
  );
}

function ExtLink({ href, children }: { href: string; children: React.ReactNode }) {
  if (!REPO_PUBLIC && href.startsWith(REPO_URL)) {
    // Mientras el repositorio esté privado el enlace daría 404: solo el nombre del fichero.
    return <span className="text-slate-500">{children}</span>;
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-slate-400 hover:text-accent"
    >
      {children} <ExternalLink className="h-3 w-3" />
    </a>
  );
}

export default function Contribuir() {
  return (
    <main className="relative mx-auto max-w-3xl px-6 py-16">
      <Link
        href="/"
        className="mb-10 inline-flex items-center gap-2 text-sm text-slate-400 hover:text-accent"
      >
        <ArrowLeft className="h-4 w-4" /> Volver al inicio
      </Link>

      <p className="mb-4 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
        <Github className="h-3.5 w-3.5" /> Código abierto · AGPL-3.0 + CC BY 4.0
      </p>
      <h1 className="text-4xl font-bold tracking-tight text-slate-50">
        Contribuye al <span className="text-accent text-glow">entramado</span>
      </h1>
      <p className="mt-4 text-lg leading-relaxed text-slate-400">
        El código (pipeline, API, web y conector MCP), las capas de datos editoriales ya
        aprobadas y el censo del corpus documental se publican en GitHub
        {REPO_PUBLIC
          ? ""
          : " (el repositorio está temporalmente en privado mientras se reconstruye su historial)"}
        . Cada relación del
        grafo es trazable a su fuente, y cada corrección o trama nueva puede venir de
        cualquiera, siempre que traiga documentos oficiales detrás.
      </p>

      <div className="mt-8 rounded-2xl glass p-5">
        <p className="text-xs uppercase tracking-widest text-slate-500">El repositorio</p>
        {REPO_PUBLIC ? (
          <code className="mt-2 block select-all break-all rounded-lg bg-black/30 px-4 py-3 font-mono text-sm text-accent">
            github.com/entramado-org/entramado
          </code>
        ) : (
          <p className="mt-2 text-sm leading-relaxed text-slate-400">
            Temporalmente en privado: se está reconstruyendo su historial antes de volver a
            publicarlo. Mientras tanto, cualquier propuesta o corrección llega por correo.
          </p>
        )}
        <div className="mt-3 flex flex-wrap gap-3">
          {REPO_PUBLIC && (
            <a
              href={REPO_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-ink-950 transition hover:bg-accent-glow"
            >
              <Github className="h-4 w-4" /> Ver el repositorio
            </a>
          )}
          <a
            href="mailto:contacto@entramado.org"
            className="inline-flex items-center gap-2 rounded-xl glass px-4 py-2 text-sm text-slate-200 hover:bg-white/5"
          >
            <Mail className="h-4 w-4" /> contacto@entramado.org
          </a>
        </div>
      </div>

      {/* aviso: GitHub es público y permanente */}
      <section className="mt-8 rounded-2xl border border-amber-400/30 bg-amber-400/5 p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-300">
            <span className="font-medium text-slate-100">
              Lo que escribes en GitHub es público al instante y permanente.
            </span>{" "}
            Los issues y pull requests se publican antes de que nadie los revise y pueden
            copiarse; cerrar un pull request no elimina su contenido. Por eso todo lo que afecta a personas (hechos, estados
            procesales, identidades, casos nuevos) se envía por correo y no por GitHub: el
            mantenedor revisa las fuentes en privado y prepara él mismo el cambio.
          </p>
        </div>
      </section>

      {/* tres vías */}
      <section className="mt-12">
        <h2 className="mb-1 text-xl font-semibold text-slate-100">Tres maneras de contribuir</h2>
        <p className="mb-4 text-sm text-slate-500">
          Las rectificaciones van aparte y tienen prioridad (ver abajo). El detalle completo está en el{" "}
          <ExtLink href={CONTRIBUTING_URL}>CONTRIBUTING.md</ExtLink> del repositorio.
        </p>
        <div className="space-y-3">
          <div className="rounded-2xl glass p-5">
            <h3 className="flex items-center gap-2 text-base font-semibold text-slate-100">
              <FileSearch className="h-4 w-4 shrink-0 text-accent" /> Proponer una trama nueva
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">
              La contribución que más acelera es el <strong className="text-slate-200">trabajo
              de campo</strong>: reunir los documentos oficiales de una trama que aún no está en
              el grafo. Escribe a contacto@entramado.org con el censo de resoluciones e informes
              (con URL de origen y quién los publica) y qué poder fiscaliza, sin datos personales
              innecesarios. El alcance declarado son los casos que rodean al Gobierno; otras
              tramas de fiscalización del poder se valoran, sin garantía de que entren. Antes de
              nada, revisa el{" "}
              <ExtLink href={BACKLOG_URL}>BACKLOG</ExtLink> por si ya está en cola. La ingesta
              la ejecutan los mantenedores: es un proceso editorial con extracción verificada y
              gates de calidad, no un script sobre un PDF.
            </p>
          </div>
          <div className="rounded-2xl glass p-5">
            <h3 className="flex items-center gap-2 text-base font-semibold text-slate-100">
              <GitPullRequest className="h-4 w-4 shrink-0 text-accent" /> Corregir con fuente
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">
              Errores de hecho, estados procesales desactualizados, homónimos mal fusionados,
              atribuciones de medio: envíalos a contacto@entramado.org con la fuente (URL o
              documento y página). El mantenedor los comprueba y corrige las{" "}
              <strong className="text-slate-200">capas de datos curadas</strong> (los YAML del
              repositorio), nunca el grafo directamente: el grafo se regenera desde un seed
              versionado y las capas se re-aplican encima.
            </p>
          </div>
          <div className="rounded-2xl glass p-5">
            <h3 className="flex items-center gap-2 text-base font-semibold text-slate-100">
              <Code2 className="h-4 w-4 shrink-0 text-accent" /> Código
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">
              Pipeline de ingesta (Python), API (FastAPI), web (Next.js) y conector MCP.
              Mejoras, correcciones, documentación y errores técnicos (enlaces rotos, fallos de
              la web), por issue o pull request, sin datos personales ni acusaciones y conforme al{" "}
              <ExtLink href={CODE_OF_CONDUCT_URL}>código de conducta</ExtLink> del repositorio.
            </p>
          </div>
        </div>
      </section>

      {/* reglas */}
      <section className="mt-12">
        <h2 className="mb-1 text-xl font-semibold text-slate-100">
          Las reglas que lo hacen publicable
        </h2>
        <p className="mb-4 text-sm text-slate-500">
          Tres reglas resumen todas las demás. No son burocracia: son la diferencia entre
          documentar y difamar.
        </p>
        <ol className="space-y-3 text-sm">
          <Regla n={1}>
            <strong className="text-slate-200">Presunción de inocencia.</strong> El estado
            procesal se etiqueta con exactitud (investigado, acusado, condenado, absuelto…) y
            nunca se agrava; lo exculpatorio se incorpora con la misma diligencia que lo
            incriminatorio.
          </Regla>
          <Regla n={2}>
            <strong className="text-slate-200">Sin fuente no entra.</strong> Toda afirmación
            lleva documento y página, medio y URL, o identificador de registro oficial. Nada de
            análisis especulativos ni atribuciones de intención.
          </Regla>
          <Regla n={3}>
            <strong className="text-slate-200">Solo los datos personales pertinentes.</strong>{" "}
            Nada de DNI, teléfonos, domicilios particulares ni cuentas de personas físicas, ni
            siquiera citando documentos que los contengan.
          </Regla>
        </ol>
        <p className="mt-4 text-sm text-slate-500">
          El detalle — gate anti-homónimo, atribución a fuente primaria, mecánica y gates de un
          cambio de datos — está en el <ExtLink href={CONTRIBUTING_URL}>CONTRIBUTING.md</ExtLink>.
        </p>
      </section>

      {/* rectificación */}
      <section className="mt-12 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">
              ¿Algo publicado sobre ti o sobre un tercero es incorrecto?
            </span>{" "}
            No hace falta un pull request:{" "}
            <a
              href="mailto:contacto@entramado.org"
              className="underline decoration-white/20 underline-offset-2 hover:text-accent"
            >
              contacto@entramado.org
            </a>
            . Las solicitudes de rectificación o supresión se atienden con prioridad sobre
            cualquier otra contribución — ver el{" "}
            <Link
              href="/aviso-legal"
              className="underline decoration-white/20 underline-offset-2 hover:text-accent"
            >
              aviso legal
            </Link>
            .
          </p>
        </div>
      </section>

      {/* licencias */}
      <section className="mt-6 rounded-2xl glass p-5">
        <h2 className="mb-2 flex items-center gap-2 text-base font-semibold text-slate-100">
          <Scale className="h-4 w-4 text-accent" /> Licencias
        </h2>
        <p className="text-sm leading-relaxed text-slate-400">
          El <strong className="text-slate-200">código</strong> es AGPL-3.0, que obliga a
          ofrecer el código de las versiones modificadas que se ofrezcan como servicio en red.
          Las <strong className="text-slate-200">capas de datos editoriales</strong> son CC BY
          4.0: reutilización con atribución a entramado.org; la licencia no cubre material de
          terceros ni autoriza cualquier tratamiento de datos personales. Los{" "}
          <strong className="text-slate-200">PDF del corpus</strong> son documentos oficiales de
          terceros, reproducidos tal cual desde sus fuentes; su naturaleza y procedencia,
          documento a documento, está en el censo{" "}
          <ExtLink href={SOURCES_URL}>data/SOURCES.md</ExtLink>.
        </p>
      </section>

      <footer className="mt-12 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-white/5 pt-6 text-xs text-slate-600">
        <span className="inline-flex items-center gap-1.5">
          <Mail className="h-3.5 w-3.5" /> contacto@entramado.org
        </span>
        <ExtLink href={CONTRIBUTING_URL}>CONTRIBUTING.md</ExtLink>
        <ExtLink href={BACKLOG_URL}>BACKLOG.md</ExtLink>
        <ExtLink href={SOURCES_URL}>data/SOURCES.md</ExtLink>
        <ExtLink href={CODE_OF_CONDUCT_URL}>CODE_OF_CONDUCT.md</ExtLink>
        <Link href="/explore" className="ml-auto text-slate-400 hover:text-accent">
          Explorar el grafo →
        </Link>
      </footer>
    </main>
  );
}
