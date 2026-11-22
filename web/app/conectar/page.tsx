import Link from "next/link";
import { headers } from "next/headers";
import {
  ArrowLeft,
  Bot,
  MessageSquare,
  Monitor,
  Terminal,
  ShieldAlert,
  Network,
  Lock,
} from "lucide-react";

export const metadata = {
  title: "Conéctalo a Claude",
  description:
    "Añade El Entramado como conector MCP en claude.ai, Claude Desktop o Claude Code y pregúntale al grafo de los casos de corrupción desde tu propia cuenta de Claude.",
  alternates: { canonical: "/conectar" },
};

export const dynamic = "force-dynamic";

/* El conector MCP es público (sin Basic Auth — claude.ai se conecta directo),
 * de SOLO LECTURA, y corre en /mcp detrás de Caddy. El modelo lo pone el
 * usuario: chatea en su claude.ai/Desktop/Code con su suscripción; aquí solo
 * servimos las herramientas de consulta (grafo Neo4j + vault de notas). */

const EJEMPLOS = [
  "¿Quién es Víctor de Aldama y de qué ha sido condenado en la sentencia del Supremo?",
  "¿Qué relación hay entre Santos Cerdán y Acciona? Cítame los documentos.",
  "Sigue el rastro del dinero: ¿qué pagos y comisiones constan por encima de 100.000 €?",
  "¿Qué sub-tramas o comunidades detecta el grafo y quién las conecta?",
  "¿Qué dicen los informes de la UCO sobre los pagos a Koldo García?",
  "Resúmeme el briefing del caso Koldo.",
];

async function mcpUrl(): Promise<string> {
  const h = await headers();
  const host = h.get("host") ?? "TU-DOMINIO";
  // Local dev: the web app runs on :3000 but the MCP service listens on :8008
  // (no Caddy in front). In prod the public host routes /mcp via Caddy.
  if (host.startsWith("localhost") || host.startsWith("127.0.0.1")) {
    return "http://localhost:8008/mcp";
  }
  const proto = h.get("x-forwarded-proto") ?? "https";
  return `${proto}://${host}/mcp`;
}

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

export default async function Conectar() {
  const url = await mcpUrl();
  return (
    <main className="relative mx-auto max-w-3xl px-6 py-16">
      <Link
        href="/"
        className="mb-10 inline-flex items-center gap-2 text-sm text-slate-400 hover:text-accent"
      >
        <ArrowLeft className="h-4 w-4" /> Volver al inicio
      </Link>

      <p className="mb-4 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
        <Bot className="h-3.5 w-3.5" /> Conector MCP · solo lectura
      </p>
      <h1 className="text-4xl font-bold tracking-tight text-slate-50">
        Pregúntale al <span className="text-accent text-glow">entramado</span> desde tu Claude
      </h1>
      <p className="mt-4 text-lg leading-relaxed text-slate-400">
        Añade este sitio como <em>conector</em> en tu cuenta de Claude y podrás hacerle
        preguntas al grafo en lenguaje natural: quién es quién, qué los conecta, qué dicen
        los documentos. El modelo corre en <strong className="text-slate-200">tu</strong>{" "}
        cuenta (claude.ai, Claude Desktop o Claude Code); este sitio solo aporta las
        herramientas de consulta sobre el grafo y las notas.
      </p>

      <div className="mt-8 rounded-2xl glass p-5">
        <p className="text-xs uppercase tracking-widest text-slate-500">URL del conector</p>
        <code className="mt-2 block select-all break-all rounded-lg bg-black/30 px-4 py-3 font-mono text-sm text-accent">
          {url}
        </code>
        <p className="mt-2 text-xs text-slate-500">
          Sin clave ni autenticación: el conector es público y de solo lectura.
        </p>
      </div>

      {/* claude.ai / Desktop */}
      <section className="mt-12">
        <h2 className="mb-1 flex items-center gap-2 text-xl font-semibold text-slate-100">
          <MessageSquare className="h-5 w-5 text-accent" /> En claude.ai (web) y Claude Desktop
        </h2>
        <p className="mb-4 text-sm text-slate-500">
          Requiere un plan de pago de Claude (Pro o Max). Los conectores personalizados se
          configuran una sola vez y quedan disponibles en todos tus chats.
        </p>
        <ol className="space-y-3 text-sm">
          <Paso n={1}>
            Abre <span className="text-slate-100">Ajustes → Conectores</span> (Settings →
            Connectors).
          </Paso>
          <Paso n={2}>
            Pulsa <span className="text-slate-100">«Añadir conector personalizado»</span> (Add
            custom connector).
          </Paso>
          <Paso n={3}>
            Pega la URL de arriba y guarda. No pide autenticación.
          </Paso>
          <Paso n={4}>
            En un chat nuevo, activa el conector «El Entramado» en el menú de herramientas y
            pregunta.
          </Paso>
        </ol>
      </section>

      {/* Claude Code */}
      <section className="mt-12">
        <h2 className="mb-1 flex items-center gap-2 text-xl font-semibold text-slate-100">
          <Terminal className="h-5 w-5 text-accent" /> En Claude Code (terminal)
        </h2>
        <p className="mb-4 text-sm text-slate-500">Un comando y listo:</p>
        <code className="block select-all break-all rounded-lg bg-black/30 px-4 py-3 font-mono text-sm text-slate-200">
          claude mcp add --transport http entramado {url}
        </code>
      </section>

      {/* ejemplos */}
      <section className="mt-12">
        <h2 className="mb-1 flex items-center gap-2 text-xl font-semibold text-slate-100">
          <Network className="h-5 w-5 text-accent" /> Preguntas que sabe responder
        </h2>
        <p className="mb-4 text-sm text-slate-500">
          Las respuestas citan documento y página: pídeselo si no lo hace.
        </p>
        <ul className="space-y-2">
          {EJEMPLOS.map((e) => (
            <li
              key={e}
              className="rounded-xl glass px-4 py-3 text-sm leading-relaxed text-slate-300"
            >
              «{e}»
            </li>
          ))}
        </ul>
      </section>

      {/* qué expone */}
      <section className="mt-12 rounded-2xl glass p-5">
        <h2 className="mb-2 flex items-center gap-2 text-base font-semibold text-slate-100">
          <Lock className="h-4 w-4 text-accent" /> Qué expone (y qué no)
        </h2>
        <p className="text-sm leading-relaxed text-slate-400">
          Veinticinco herramientas de <strong className="text-slate-200">solo lectura</strong>:
          sobre el grafo (buscar entidades, fichas, relaciones, caminos y evidencias), el
          rastro del dinero (flujos con importe), el análisis de red (centralidad,
          intermediación y detección de comunidades), las afirmaciones en disputa, la
          cronología, los documentos y notas del corpus (buscar, leer y novedades), los casos
          curados, las capas de registros oficiales (estructura societaria BORME, ayudas BDNS,
          contratos menores PLACSP, contratación pública ampliada —licitaciones, plataformas
          agregadas y menores autonómicos— y trayectorias de cargos del BOE) y los patrones
          llamativos de la capa editorial. No puede escribir ni modificar nada, y el servidor no ve tu
          conversación: Claude solo le envía las consultas concretas de cada herramienta.
        </p>
      </section>

      {/* disclaimer */}
      <section className="mt-6 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">Presunción de inocencia.</span> Las
            herramientas devuelven lo que afirman documentos públicos y prensa citada
            —indicios, imputaciones, acusaciones o condenas firmes— con su estado procesal;
            distinguir una imputación de una condena es responsabilidad de cada respuesta. El
            conector instruye al modelo para
            citar fuente y respetar ese estado, pero la conversación ocurre en tu cuenta de
            Claude.
          </p>
        </div>
      </section>

      <footer className="mt-12 flex items-center justify-between border-t border-white/5 pt-6 text-xs text-slate-600">
        <span className="inline-flex items-center gap-1.5">
          <Monitor className="h-3.5 w-3.5" /> Protocolo MCP (Model Context Protocol), transporte
          Streamable HTTP.
        </span>
        <span className="flex items-center gap-4">
          <Link href="/contribuir" className="text-slate-400 hover:text-accent">
            Contribuir al proyecto →
          </Link>
          <Link href="/explore" className="text-slate-400 hover:text-accent">
            Explorar el grafo →
          </Link>
        </span>
      </footer>
    </main>
  );
}
