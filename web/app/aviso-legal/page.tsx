import { Scale, ShieldAlert } from "lucide-react";
import { CONTACTO } from "@/lib/site";

export const metadata = {
  title: "Aviso legal",
  description:
    "Titularidad, naturaleza del sitio, presunción de inocencia, tratamiento de datos, derecho de rectificación y exención de responsabilidad de El Entramado.",
  alternates: { canonical: "/aviso-legal" },
};

/* ────────────────────────────────────────────────────────────────────────────
 * Aviso legal del sitio. Página estática. Da una vía de contacto del responsable (sin nombre
 * personal, por decisión del titular), deja por escrito la naturaleza (agregador: republica
 * titulares de medios y enlaza documentos oficiales; no es medio ni resolución judicial), la base
 * del tratamiento de datos (interés público / libertad de información, art. 85 RGPD) y, sobre
 * todo, una vía de RECTIFICACIÓN/RETIRADA clara. Si cambia el email o la titularidad, editar aquí.
 * ──────────────────────────────────────────────────────────────────────────── */

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

      <Section title="2. Naturaleza del sitio">
        <p>
          <span className="text-slate-200">El Entramado</span> es un agregador: republica los{" "}
          <span className="text-slate-300">titulares publicados por medios de comunicación</span> sobre
          causas judiciales de relevancia política en España y enlaza los{" "}
          <span className="text-slate-300">documentos oficiales</span> en la web del organismo que los emitió.
          No es un medio de comunicación, ni un registro oficial, ni una resolución judicial.{" "}
          <span className="text-slate-300">No redacta ni afirma nada por su cuenta.</span> Cada ítem muestra el titular que tiene la
          pieza enlazada, su medio y su fecha, y un enlace a ella.
        </p>
        <p>
          El sitio no aloja copia del contenido de ninguna pieza ni de ningún documento: solo el titular y el enlace.
        </p>
      </Section>

      <Section title="3. Presunción de inocencia">
        <p>
          Los titulares enlazados informan, según cada medio y en su fecha, de investigaciones, imputaciones y
          acusaciones, y también de sentencias ya dictadas, que pueden no ser firmes. La situación de una
          persona puede haber cambiado desde que se publicó la pieza. Que alguien aparezca en
          un titular <span className="text-slate-200">no significa que sea culpable</span>: rige plenamente la
          presunción de inocencia hasta que exista sentencia firme. Una imputación o una acusación no son una
          condena, y el titular de un medio no es una declaración de este sitio.
        </p>
      </Section>

      <Section title="4. Titulares, enlaces y propiedad intelectual">
        <p>
          Los titulares y las piezas enlazadas pertenecen a sus medios, y los documentos oficiales a los
          organismos que los emiten. Cada enlace lleva a la pieza cuyo titular se muestra; cuando la pieza es de una agencia y se leyó en otro
          medio, se enlaza la web donde se leyó y se indica junto al medio, por ejemplo «Europa Press (vía
          Infobae)». El titular mostrado es el que tenía
          la pieza al incorporarla al sitio y puede haber cambiado desde entonces; los enlaces a sitios externos
          no implican respaldo de sus contenidos.
        </p>
        <p>
          La organización y el diseño de este sitio son obra de su titular. Puedes citar y enlazar libremente el
          sitio indicando la fuente.
        </p>
      </Section>

      <Section title="5. Tratamiento de datos personales">
        <p>
          El sitio trata datos personales (nombres, cargos) que ya constan en los titulares de medios y en
          documentos oficiales públicos. La base que legitima este tratamiento es el{" "}
          <span className="text-slate-300">interés público</span> y el ejercicio de la libertad de
          información, conforme al artículo 85 del Reglamento (UE) 2016/679 (RGPD) y a la normativa española de
          protección de datos, que prevén la conciliación de esos derechos con la protección de datos cuando el
          tratamiento se realiza con fines de expresión e información.
        </p>
        <p>
          La navegación genera registros técnicos (dirección IP, agente de usuario, página de procedencia)
          que se usan exclusivamente para medir el tráfico, garantizar la seguridad del servicio y
          prevenir abusos. No se emplean para identificar a personas concretas ni se ceden a terceros con
          fines comerciales.
        </p>
      </Section>

      <Section title="6. Derecho de rectificación, retirada y supresión">
        <p>
          Si crees que un titular o un enlace sobre ti es <span className="text-slate-200">inexacto, está
          desactualizado</span> (por ejemplo, un sobreseimiento o una absolución posteriores) o no debería
          figurar, puedes solicitar su rectificación, su retirada o su supresión escribiendo a{" "}
          <a href={`mailto:${CONTACTO}`} className="text-accent hover:underline">
            {CONTACTO}
          </a>
          , indicando el ítem concreto y aportando la referencia que lo acredite. Plazos a los que se
          compromete el titular: <span className="text-slate-200">acuse de recibo en tres días hábiles</span>;
          las rectificaciones y retiradas que afectan a personas se resuelven, o se explica por qué no
          proceden, <span className="text-slate-200">en tres días hábiles como objetivo</span>; cualquier
          otra petición, en un mes como máximo. Un ítem retirado deja de aparecer en el sitio. También tienes derecho a presentar una reclamación ante la Agencia Española de Protección
          de Datos.
        </p>
        <p>
          Si lo que es inexacto es el contenido de la pieza, quien puede rectificarla es el medio que la
          publicó; aquí se retira el enlace o se actualiza el titular cuando el medio ya lo ha corregido.
        </p>
        <p>
          El compromiso no se limita a los datos propios:{" "}
          <span className="text-slate-200">cualquier error señalado con su fuente se corrige o el ítem se
          retira</span>, lo señale o no la persona afectada, por el mismo canal.
        </p>
      </Section>

      <Section title="7. Exactitud y exención de responsabilidad">
        <p>
          La información se ofrece de buena fe y a partir de las fuentes enlazadas, pero puede contener
          errores o quedar desactualizada conforme avanzan los procedimientos. No se garantiza su
          exhaustividad ni su vigencia permanente, y su uso es responsabilidad de quien la consulta. Los
          enlaces a sitios externos (medios, organismos) no implican respaldo de sus contenidos.
        </p>
      </Section>

      <Section title="8. Legislación aplicable">
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
            <span className="font-medium text-slate-200">En una línea.</span> Esto republica titulares de medios
            y enlaza documentos oficiales sobre procedimientos judiciales, abiertos o ya sentenciados; rige la presunción de inocencia, y si algo sobre
            ti es inexacto o ha quedado desactualizado, escribe a{" "}
            <a href={`mailto:${CONTACTO}`} className="text-accent hover:underline">
              {CONTACTO}
            </a>{" "}
            y se corrige o se retira.
          </p>
        </div>
      </section>
    </main>
  );
}
