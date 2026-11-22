import Link from "next/link";
import { ArrowLeft, Network, Scale, ShieldAlert } from "lucide-react";
import { NEARIUS_URL } from "@/lib/attribution";
import { REPO_PUBLIC, REPO_URL } from "@/lib/site";

export const metadata = {
  title: "Aviso legal",
  description:
    "Titularidad, naturaleza del proyecto, tratamiento de datos, derecho de rectificación y exención de responsabilidad de El Entramado.",
  alternates: { canonical: "/aviso-legal" },
};

/* ────────────────────────────────────────────────────────────────────────────
 * Aviso legal del sitio. Página estática, sin datos del grafo. Pensada como
 * primera línea de defensa al difundir el proyecto: da una vía de contacto del
 * responsable (sin nombre personal, por decisión del titular), deja
 * por escrito la naturaleza (organización de fuentes públicas, no medio ni
 * resolución judicial), la base del tratamiento de datos (interés público /
 * libertad de información, art. 85 RGPD) y, sobre todo, una vía de
 * RECTIFICACIÓN/SUPRESIÓN clara. Si cambia el email o la titularidad, editar
 * aquí. Enlazada desde el footer de la home.
 * ──────────────────────────────────────────────────────────────────────────── */

const CONTACTO = "contacto@entramado.org";
const REGISTRO_URL = `${REPO_URL}/blob/main/pipeline/data/rectificaciones/registro.yaml`;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <h2 className="mb-2 text-lg font-semibold text-slate-100">{title}</h2>
      <div className="space-y-3 text-sm leading-relaxed text-slate-400">{children}</div>
    </section>
  );
}

export default function AvisoLegalPage() {
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
          <Scale className="h-3.5 w-3.5" /> Aviso legal
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">
          Aviso legal y condiciones de uso
        </h1>
        <p className="mt-4 text-lg leading-relaxed text-slate-400">
          Quién está detrás de este sitio, qué es y qué no es, cómo se tratan los datos personales y cómo
          ejercer tu derecho de rectificación o supresión.
        </p>
      </header>

      <Section title="1. Titularidad">
        <p>
          El responsable mantiene este sitio a título personal. Para cualquier comunicación, incluido
          el ejercicio de los derechos que se describen más abajo, el contacto es{" "}
          <a href={`mailto:${CONTACTO}`} className="text-accent hover:underline">
            {CONTACTO}
          </a>
          .
        </p>
      </Section>

      <Section title="2. Naturaleza del proyecto">
        <p>
          <span className="text-slate-200">El Entramado</span> es una herramienta de consulta que organiza
          y relaciona información procedente de <span className="text-slate-300">documentos judiciales y
          policiales públicos</span> y de <span className="text-slate-300">medios de comunicación</span>{" "}
          sobre los casos de corrupción que rodean al Gobierno en España. No es un medio de comunicación, ni un registro oficial, ni
          una resolución judicial: es un trabajo de sistematización de fuentes ya públicas, realizado en
          ejercicio de las libertades de expresión e información (art. 20 de la Constitución Española).
        </p>
        <p>
          Las afirmaciones derivadas de documentos enlazan a su fuente y página de origen. Las que solo
          constan en prensa se muestran como tales, fechadas y atribuidas a su medio. Los datos de{" "}
          <span className="text-slate-300">registros y bases de datos oficiales</span> — estructura
          societaria del Registro Mercantil (BORME), subvenciones y ayudas de la BDNS, contratación
          pública de la PLACSP y de las plataformas oficiales autonómicas (contratos menores,
          licitaciones y plataformas agregadas; datos abiertos de la Junta de Andalucía,
          api.euskadi.eus y contratosdegalicia.gal) y nombramientos y ceses de la sección II.A
          del BOE — son información oficial pública, no un hecho ni un indicio del caso.
          La sección institucional contiene, además,{" "}
          <span className="text-slate-300">análisis y opinión</span>, y como tal debe leerse.
        </p>
      </Section>

      <Section title="3. Presunción de inocencia">
        <p>
          Muchas de las personas mencionadas están investigadas, imputadas, procesadas o acusadas en
          procedimientos <span className="text-slate-300">en curso</span>. Que alguien aparezca en estos
          casos <span className="text-slate-200">no significa que sea culpable</span>: rige plenamente la
          presunción de inocencia hasta que exista sentencia firme. El sitio distingue, en lo posible,
          entre hechos probados, indicios, acusaciones y condenas, y respeta el estado procesal de cada
          persona. Una imputación o una acusación no son una condena.
        </p>
      </Section>

      <Section title="4. Tratamiento de datos personales">
        <p>
          El sitio trata datos personales (nombres, cargos y vínculos) que ya constan en fuentes públicas.
          La base que legitima este tratamiento es el <span className="text-slate-300">interés público</span>{" "}
          y el ejercicio de la libertad de información, conforme al artículo 85 del Reglamento (UE)
          2016/679 (RGPD) y a la normativa española de protección de datos, que prevén la conciliación de
          esos derechos con la protección de datos cuando el tratamiento se realiza con fines de expresión
          e información.
        </p>
        <p>
          La navegación genera registros técnicos (dirección IP, agente de usuario, página de procedencia)
          que se usan exclusivamente para medir el tráfico, garantizar la seguridad del servicio y
          prevenir abusos. No se emplean para identificar a personas concretas ni se ceden a terceros con
          fines comerciales.
        </p>
      </Section>

      <Section title="5. Derecho de rectificación y supresión">
        <p>
          Si crees que un dato sobre ti es <span className="text-slate-200">inexacto, está
          desactualizado</span> (por ejemplo, un sobreseimiento o una absolución posteriores) o no debería
          figurar, puedes solicitar su rectificación o supresión escribiendo a{" "}
          <a href={`mailto:${CONTACTO}`} className="text-accent hover:underline">
            {CONTACTO}
          </a>
          , indicando el dato concreto y aportando la referencia que lo acredite. Plazos a los que se
          compromete el titular: <span className="text-slate-200">acuse de recibo en un día hábil</span>;
          las rectificaciones y supresiones sobre personas se resuelven, o se explica por qué no
          proceden, <span className="text-slate-200">en tres días hábiles como objetivo</span>; cualquier
          otra petición, en un mes como máximo. También tienes derecho a presentar una reclamación ante
          la Agencia Española de Protección de Datos.
        </p>
        <p>
          Cada corrección queda anotada, sin datos de quien la pidió ni el texto retirado, en un{" "}
          {REPO_PUBLIC ? (
            <a
              href={REGISTRO_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline"
            >
              registro público de rectificaciones
            </a>
          ) : (
            <span className="text-slate-200">registro público de rectificaciones</span>
          )}
          : qué se corrigió, con qué fuente y cuándo.
          {REPO_PUBLIC
            ? ""
            : " El fichero vive en el repositorio del proyecto, temporalmente en privado mientras se reconstruye su historial; hasta entonces se facilita por correo a quien lo pida."}
        </p>
        <p>
          El compromiso no se limita a los datos propios:{" "}
          <span className="text-slate-200">cualquier error señalado con su fuente se rectifica</span>,
          lo señale o no la persona afectada, por el mismo canal.
        </p>
      </Section>

      <Section title="6. Exactitud y exención de responsabilidad">
        <p>
          La información se ofrece de buena fe y a partir de las fuentes citadas, pero puede contener
          errores o quedar desactualizada conforme avanzan los procedimientos. No se garantiza su
          exhaustividad ni su vigencia permanente, y su uso es responsabilidad de quien la consulta. Los
          enlaces a sitios externos (medios, documentos) no implican respaldo de sus contenidos.
        </p>
      </Section>

      <Section title="7. Propiedad intelectual">
        <p>
          Los documentos judiciales y policiales citados pertenecen a sus titulares y se enlazan a su
          fuente. La organización, los textos propios y el diseño de este sitio son obra de su titular.
          Puedes citar y enlazar libremente el proyecto indicando la fuente.
        </p>
      </Section>

      <Section title="8. Fuentes de datos">
        <p>
          El sitio combina <span className="text-slate-300">documentos judiciales y policiales
          públicos</span> (la fuente principal, siempre citada por documento y página),{" "}
          <span className="text-slate-300">prensa</span> (fechada y atribuida a su medio),{" "}
          <span className="text-slate-300">datos de contexto público</span> (cargos, partidos,
          parentesco) y cinco capas de registros oficiales: <span className="text-slate-300">estructura
          societaria del Registro Mercantil (BORME)</span>, <span className="text-slate-300">subvenciones,
          ayudas y avales públicos de la Base de Datos Nacional de Subvenciones (BDNS)</span>,{" "}
          <span className="text-slate-300">contratos menores publicados en la Plataforma de Contratación
          del Sector Público (PLACSP)</span>, <span className="text-slate-300">contratación pública
          ampliada — licitaciones de PLACSP y de las plataformas agregadas, y contratos menores de las
          plataformas oficiales autonómicas (datos abiertos de la Junta de Andalucía, api.euskadi.eus y
          contratosdegalicia.gal)</span> y <span className="text-slate-300">nombramientos y ceses de la
          sección II.A del BOE</span>, agregados por adjudicataria y órgano de contratación (o por cargo
          y organismo). Los
          datos mercantiles se obtienen a través de{" "}
          <a href={NEARIUS_URL} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
            Nearius
          </a>
          , que ha cedido el acceso a su corpus mercantil, y se muestran como estructura oficial pública
          (se cita el identificador BORME), nunca como hecho ni indicio del caso. El Registro Mercantil
          recoge a los administradores registrales, no necesariamente a la titularidad real: su presencia
          no imputa y su ausencia no exculpa.
        </p>
        <p>
          Lo mismo rige para las demás capas administrativas: recibir una subvención o un aval, o
          contratar con la administración, es <span className="text-slate-200">legal</span>. Estos datos
          se muestran como capas separadas, con su procedencia marcada, y no constituyen hechos ni
          indicios de ningún caso: su interés es permitir el cruce con las entidades que ya aparecen en
          las fuentes judiciales.
        </p>
      </Section>

      <Section title="9. Legislación aplicable">
        <p>
          Este aviso se rige por la legislación española. Para cualquier cuestión relativa al sitio, el
          contacto es{" "}
          <a href={`mailto:${CONTACTO}`} className="text-accent hover:underline">
            {CONTACTO}
          </a>
          .
        </p>
      </Section>

      {/* recordatorio */}
      <section className="mt-10 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">En una línea.</span> Esto organiza información ya
            pública sobre casos abiertos; rige la presunción de inocencia, y si algo sobre ti es inexacto o
            ha quedado desactualizado, escribe a{" "}
            <a href={`mailto:${CONTACTO}`} className="text-accent hover:underline">
              {CONTACTO}
            </a>{" "}
            y se corrige.
          </p>
        </div>
      </section>
    </main>
  );
}
