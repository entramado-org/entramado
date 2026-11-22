import Link from "next/link";
import { ArrowRight, Bot, Coins, Crosshair, FolderOpen, Gavel, Github, Layers, Network, Radar, ScrollText, ShieldAlert, FileText, Landmark, Newspaper, ExternalLink, Users } from "lucide-react";
import KeyFigures from "@/components/story/KeyFigures";
import HeroTeaser from "@/components/HeroTeaser";
import AnchorChips from "@/components/AnchorChips";
import { TIMELINE } from "@/lib/timeline";
import { caseColor, loadUltimaHora } from "@/lib/ultimaHora";
import type { BriefingMeta } from "@/lib/api";
import { NEARIUS_URL } from "@/lib/attribution";
import { REPO_PUBLIC, REPO_URL } from "@/lib/site";
import { getIndexables } from "@/lib/fichas";
import { SOURCE_KIND_META } from "@/lib/graphStyle";

/** Las 7 capas de cara al usuario, agrupadas en 4 familias editoriales: prensa y
 *  última hora comparten fuente material (medios, atribuidos) y los tres registros
 *  oficiales comparten naturaleza (estructura, no indicios); contexto va aparte porque
 *  identifica sin atribución. Color y label de cada capa vienen de SOURCE_KIND_META;
 *  título y desc de familia son propios de la landing. Acusación/contrato son
 *  sub-tiers de la judicial y no se listan. */
const FAMILIAS_LANDING: { title: string; kinds: string[]; desc: string; href: string }[] = [
  { title: "Documentos judiciales", kinds: ["judicial"], desc: "Sumarios, autos, informes de la UCO y sentencias: el núcleo, citado por documento y página.", href: "/documentos" },
  { title: "Contexto público", kinds: ["contexto"], desc: "Cargos, partidos y parentescos públicos: la capa que dice quién es quién, sin ser hecho ni indicio del caso.", href: "/entidad" },
  { title: "Prensa", kinds: ["prensa", "ultima_hora"], desc: "Lo que solo consta en medios, fechado y atribuido; la última hora — aún sin documento judicial — viaja en su propia capa.", href: "/ultima-hora" },
  { title: "Registros oficiales", kinds: ["mercantil", "subvencion", "contratacion"], desc: "Quién administra qué empresa (BORME, vía Nearius), qué ayudas recibió (BDNS) y qué contratos menores acumula (PLACSP).", href: "/explore" }, // ontologia-ok: agrupación editorial
];

const API_BASE = process.env.API_INTERNAL_BASE || process.env.NEXT_PUBLIC_API_BASE || "http://api:8000";

async function loadBriefings(): Promise<BriefingMeta[]> {
  try {
    const r = await fetch(`${API_BASE}/briefings`, { cache: "no-store" });
    if (!r.ok) return [];
    const j = await r.json();
    return j.briefings ?? [];
  } catch {
    return [];
  }
}

export const dynamic = "force-dynamic";

export const metadata = {
  alternates: { canonical: "/" },
};

export default async function Landing() {
  const [briefings, indexables, ultimaHora] = await Promise.all([
    loadBriefings(),
    getIndexables(),
    loadUltimaHora(),
  ]);
  const protagonistas = indexables.slice(0, 18); // ya viene ordenado por degree
  const slugById = new Map(indexables.map((e) => [e.id, e.slug]));
  return (
    <main className="relative overflow-hidden">
      {/* ambient network */}
      <svg className="pointer-events-none absolute inset-0 h-full w-full opacity-[0.15]" aria-hidden>
        <defs>
          <radialGradient id="n" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#38e1c4" />
            <stop offset="100%" stopColor="#38e1c4" stopOpacity="0" />
          </radialGradient>
        </defs>
        {[
          [20, 30], [40, 20], [65, 35], [80, 25], [30, 60], [55, 70], [75, 60], [50, 45],
        ].map(([x, y], i) => (
          <circle key={i} cx={`${x}%`} cy={`${y}%`} r="3" fill="url(#n)" className="animate-pulse-glow" />
        ))}
        <line x1="20%" y1="30%" x2="50%" y2="45%" stroke="#38e1c4" strokeWidth="0.5" />
        <line x1="40%" y1="20%" x2="50%" y2="45%" stroke="#38e1c4" strokeWidth="0.5" />
        <line x1="65%" y1="35%" x2="50%" y2="45%" stroke="#38e1c4" strokeWidth="0.5" />
        <line x1="50%" y1="45%" x2="55%" y2="70%" stroke="#38e1c4" strokeWidth="0.5" />
        <line x1="55%" y1="70%" x2="30%" y2="60%" stroke="#38e1c4" strokeWidth="0.5" />
        <line x1="65%" y1="35%" x2="80%" y2="25%" stroke="#38e1c4" strokeWidth="0.5" />
        <line x1="75%" y1="60%" x2="55%" y2="70%" stroke="#38e1c4" strokeWidth="0.5" />
      </svg>

      <div className="relative mx-auto max-w-5xl px-6 py-20">
        {/* hero */}
        <section className="animate-fade-in grid items-center gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
          <div>
          <p className="mb-4 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
            <Network className="h-3.5 w-3.5" /> Grafo de conocimiento · siete capas de fuentes públicas
          </p>
          <h1 className="max-w-3xl text-5xl font-bold leading-[1.05] tracking-tight text-slate-50 sm:text-6xl">
            El <span className="text-accent text-glow">entramado</span> de la corrupción, conectado.
          </h1>
          <p className="mt-5 max-w-2xl text-lg leading-relaxed text-slate-400">
            Un mapa navegable de personas, empresas, organismos y delitos extraídos de sumarios,
            autos judiciales, informes de la UCO y las sentencias ya dictadas — la del Supremo
            sobre Ábalos, Koldo y Aldama y la de la AP de Badajoz sobre David Sánchez (no firme).
            Sigue el hilo entre el caso Koldo y las mascarillas, Cerdán y la obra pública, Begoña
            Gómez, el rescate de Plus Ultra, la trama de Leire Díez y la contratación del hermano
            del presidente en la Diputación de Badajoz. Y ya no solo papeles judiciales: cada
            figura se cruza con los registros oficiales — quién administra qué empresa (BORME),
            qué ayudas recibió (BDNS), qué contratos menores acumula (PLACSP) — y con la prensa,
            citada a su medio.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/explore"
              className="inline-flex items-center gap-2 rounded-xl bg-accent px-5 py-3 font-medium text-ink-950 transition hover:bg-accent-glow"
            >
              Explorar el grafo <ArrowRight className="h-4 w-4" />
            </Link>
            <a
              href="#figuras"
              className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
            >
              <ScrollText className="h-4 w-4" /> Las figuras clave
            </a>
            <Link
              href="/ultima-hora"
              className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
            >
              <Newspaper className="h-4 w-4" /> Última hora
            </Link>
            <Link
              href="/patrones"
              className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
            >
              <Radar className="h-4 w-4" /> Patrones llamativos
            </Link>
            <Link
              href="/documentos"
              className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
            >
              <FileText className="h-4 w-4" /> Documentos
            </Link>
            <Link
              href="/flujos"
              className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
            >
              <Coins className="h-4 w-4" /> Flujos de dinero
            </Link>
            <Link
              href="/caso"
              className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
            >
              <FolderOpen className="h-4 w-4" /> Casos
            </Link>
            <Link
              href="/investigados"
              className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
            >
              <Gavel className="h-4 w-4" /> Investigados
            </Link>
            <Link
              href="/institucional"
              className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
            >
              <Landmark className="h-4 w-4" /> Contexto institucional
            </Link>
            <Link
              href="/conectar"
              className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
            >
              <Bot className="h-4 w-4" /> Pregúntale desde tu Claude
            </Link>
            <Link
              href="/contribuir"
              className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
            >
              <Github className="h-4 w-4" /> Contribuye
            </Link>
          </div>
          </div>
          <div className="lg:pl-1">
            <HeroTeaser />
          </div>
        </section>

        {/* alcance declarado — el universo que se cubre, censo y no muestra */}
        <section className="mt-16 rounded-2xl glass p-5">
          <div className="flex gap-3">
            <Crosshair className="h-5 w-5 shrink-0 text-accent" />
            <p className="text-sm leading-relaxed text-slate-400">
              <span className="font-medium text-slate-200">Alcance declarado.</span> Este grafo
              documenta las causas judiciales abiertas que rodean al Gobierno en ejercicio y sus
              derivadas, cubiertas de forma exhaustiva: un censo de ese universo, no una muestra
              de la corrupción en España. Se fiscaliza el poder, no unas siglas: cuando gobierne
              otro partido rodeado de causas equivalentes, tendrá su sección o su grafo.{" "}
              <Link href="/institucional" className="underline decoration-white/20 underline-offset-2 hover:text-accent">
                La crítica institucional
              </Link>{" "}
              es la única capa de opinión y está declarada como tal.
            </p>
          </div>
        </section>

        {/* fuentes — the seven provenance layers, and why crossing them matters */}
        <section id="fuentes" className="mt-24">
          <div className="mb-6">
            <p className="mb-1 inline-flex items-center gap-1.5 text-xs uppercase tracking-widest text-accent">
              <Layers className="h-3.5 w-3.5" /> Fuentes
            </p>
            <h2 className="text-2xl font-semibold text-slate-100">Cuatro familias de fuentes, siete capas</h2>
            <p className="mt-1 max-w-2xl text-sm text-slate-500">
              El documento judicial es el núcleo. Sobre él se superponen, siempre distinguibles,
              el contexto público, la prensa y los registros oficiales — cada capa con su color y
              su peso probatorio.
            </p>
          </div>
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {FAMILIAS_LANDING.map(({ title, kinds, desc, href }) => (
              <li key={title}>
                <Link
                  href={href}
                  className="flex h-full flex-col rounded-2xl glass p-4 transition hover:bg-white/5"
                >
                  <span className="text-sm font-medium text-slate-100">{title}</span>
                  <span className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
                    {kinds.map((k) => {
                      const meta = SOURCE_KIND_META[k];
                      if (!meta) return null;
                      return (
                        <span
                          key={k}
                          className="inline-flex items-center gap-1.5 text-[11px] font-medium"
                          style={{ color: meta.color }}
                        >
                          <span
                            className="h-2 w-2 shrink-0 rounded-full"
                            style={{ backgroundColor: meta.color, boxShadow: `0 0 8px ${meta.color}66` }}
                          />
                          {meta.label}
                        </span>
                      );
                    })}
                  </span>
                  <p className="mt-2 text-xs leading-snug text-slate-400">{desc}</p>
                </Link>
              </li>
            ))}
            <li className="sm:col-span-2 lg:col-span-4">
              <div className="flex h-full flex-col gap-3 rounded-2xl border border-accent/30 bg-accent/5 p-4 sm:flex-row sm:items-center">
                <div className="flex-1">
                  <span className="text-sm font-medium text-accent">¿Por qué cruzarlas?</span>
                  <p className="mt-1 text-xs leading-snug text-slate-300">
                    Porque la señal está en la superposición: una empresa citada en un sumario que
                    además comparte administrador, recibió ayudas y acumula contratos menores.
                  </p>
                </div>
                <div className="flex shrink-0 flex-col items-start gap-1.5">
                  <Link
                    href="/explore"
                    className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:text-accent-glow"
                  >
                    Ver las capas en el grafo <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
                  <Link
                    href="/patrones"
                    className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:text-accent-glow"
                  >
                    <Radar className="h-3.5 w-3.5" /> Patrones llamativos documentados
                  </Link>
                </div>
              </div>
            </li>
          </ul>
          <p className="mt-3 text-xs text-slate-600">
            Los datos administrativos (BORME, BDNS, PLACSP) son estructura, no indicios: la
            presencia en un registro no imputa y la ausencia no exculpa.
          </p>
        </section>

        {/* briefings — the narrative entry point */}
        {briefings.length > 0 && (
          <section id="briefings" className="mt-24">
            <div className="mb-6 flex items-end justify-between">
              <div>
                <p className="mb-1 inline-flex items-center gap-1.5 text-xs uppercase tracking-widest text-accent">
                  <FileText className="h-3.5 w-3.5" /> Briefings
                </p>
                <h2 className="text-2xl font-semibold text-slate-100">Lo que dicen los papeles</h2>
                <p className="mt-1 text-sm text-slate-500">Síntesis periodísticas con cita de fuente.</p>
              </div>
              <Link href="/briefings" className="text-sm text-slate-400 hover:text-accent">
                Ver todos →
              </Link>
            </div>
            <ul className="grid gap-3 sm:grid-cols-2">
              {briefings.slice(0, 6).map((b) => (
                <li key={b.slug}>
                  <Link
                    href={`/briefings/${b.slug}`}
                    className="group flex h-full flex-col rounded-2xl glass p-5 transition hover:border-accent/40 hover:bg-white/5"
                  >
                    <div className="flex items-start gap-3">
                      <FileText className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
                      <span className="text-sm font-medium leading-snug text-slate-100 group-hover:text-accent">
                        {b.title}
                      </span>
                    </div>
                    {b.dek && (
                      <p className="mt-2 line-clamp-2 text-xs leading-snug text-slate-400">{b.dek}</p>
                    )}
                    <AnchorChips ids={b.anchors} />
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* última hora — recent developments, press-attributed */}
        <section className="mt-24">
          <div className="mb-6 flex items-end justify-between">
            <div>
              <p className="mb-1 inline-flex items-center gap-1.5 text-xs uppercase tracking-widest text-[#fdba74]">
                <Newspaper className="h-3.5 w-3.5" /> Última hora
              </p>
              <h2 className="text-2xl font-semibold text-slate-100">Lo que avanza estos días</h2>
              <p className="mt-1 text-sm text-slate-500">
                Desarrollos recientes, aún sin documento judicial. Citados a su medio; presunción de inocencia.
              </p>
            </div>
            <Link href="/ultima-hora" className="text-sm text-slate-400 hover:text-accent">
              Ver todo →
            </Link>
          </div>
          <ul className="grid gap-3 sm:grid-cols-3">
            {ultimaHora.slice(0, 3).map((it) => (
              <li key={`${it.iso}-${it.headline}`}>
                <a
                  href={it.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group flex h-full flex-col rounded-2xl glass p-5 transition hover:border-[#fb923c]/40 hover:bg-white/5"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] font-medium uppercase tracking-widest text-[#fdba74]">
                      {it.date}
                    </span>
                    {it.caseTag && (
                      <span
                        className="inline-flex items-center rounded-full border px-1.5 py-0.5 text-[9px] font-medium"
                        style={{ color: caseColor(it.caseTag), borderColor: `${caseColor(it.caseTag)}66` }}
                      >
                        {it.caseTag}
                      </span>
                    )}
                  </div>
                  <span className="mt-2 text-sm font-medium leading-snug text-slate-100 group-hover:text-accent">
                    {it.headline}
                  </span>
                  <p className="mt-2 line-clamp-3 text-xs leading-snug text-slate-400">{it.dek}</p>
                  <span className="mt-3 inline-flex items-center gap-1 text-[11px] text-[#fdba74]">
                    <ExternalLink className="h-3 w-3" /> {it.outlet}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </section>

        {/* key figures */}
        <section id="figuras" className="mt-24">
          <h2 className="mb-1 text-2xl font-semibold text-slate-100">Las figuras clave</h2>
          <p className="mb-6 text-sm text-slate-500">
            Pulsa una para entrar directamente en su red de conexiones.
          </p>
          <KeyFigures />
        </section>

        {/* protagonistas — fichas públicas indexables (spec 035) */}
        {protagonistas.length > 0 && (
          <section className="mt-24">
            <div className="mb-6 flex items-end justify-between">
              <div>
                <p className="mb-1 inline-flex items-center gap-1.5 text-xs uppercase tracking-widest text-accent">
                  <Users className="h-3.5 w-3.5" /> Protagonistas
                </p>
                <h2 className="text-2xl font-semibold text-slate-100">Quién es quién</h2>
                <p className="mt-1 text-sm text-slate-500">
                  Una ficha por protagonista: papel, conexiones documentadas y estado procesal.
                </p>
              </div>
              <Link href="/entidad" className="text-sm text-slate-400 hover:text-accent">
                Ver todos →
              </Link>
            </div>
            <ul className="flex flex-wrap gap-2">
              {protagonistas.map((e) => (
                <li key={e.id}>
                  <Link
                    href={`/entidad/${e.slug}`}
                    className="inline-flex items-center gap-1.5 rounded-full glass px-3 py-1.5 text-sm text-slate-200 transition hover:border-accent/40 hover:text-accent"
                  >
                    {e.name}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* timeline */}
        <section className="mt-24">
          <h2 className="mb-1 text-2xl font-semibold text-slate-100">Una cronología</h2>
          <p className="mb-6 text-sm text-slate-500">
            Hitos del entramado, 2017–2026. Cada uno enlaza a su briefing o a la red implicada.
          </p>
          <ol className="relative border-l border-white/10 pl-6">
            {TIMELINE.map((t) => (
              <li key={`${t.date}-${t.text}`} className="mb-7">
                <span className="absolute -left-[7px] mt-1 h-3 w-3 rounded-full bg-accent shadow-[0_0_10px_#5ef2d8]" />
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-xs font-medium uppercase tracking-widest text-accent">{t.date}</p>
                  {t.caseTag && (
                    <span
                      className="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium"
                      style={{ color: t.caseColor, borderColor: `${t.caseColor}66` }}
                    >
                      {t.caseTag}
                    </span>
                  )}
                </div>
                <p className="mt-1.5 leading-relaxed text-slate-300">{t.text}</p>
                <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                  {t.briefingSlug && (
                    <Link
                      href={`/briefings/${t.briefingSlug}`}
                      className="inline-flex items-center gap-1 text-slate-400 hover:text-accent"
                    >
                      <FileText className="h-3.5 w-3.5" /> Leer el briefing
                    </Link>
                  )}
                  {t.focus && (
                    <Link
                      href={`/explore?focus=${encodeURIComponent(t.focus)}`}
                      className="inline-flex items-center gap-1 text-slate-400 hover:text-accent"
                    >
                      <Network className="h-3.5 w-3.5" /> Ver en el grafo
                    </Link>
                  )}
                  {t.focus && slugById.has(t.focus) && (
                    <Link
                      href={`/entidad/${slugById.get(t.focus)}`}
                      className="inline-flex items-center gap-1 text-slate-400 hover:text-accent"
                    >
                      <Users className="h-3.5 w-3.5" /> Ficha
                    </Link>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* código abierto — banner hacia el repo público y /contribuir */}
        <section className="mt-24 flex flex-col gap-4 rounded-2xl border border-accent/30 bg-accent/5 p-5 sm:flex-row sm:items-center">
          <div className="flex flex-1 gap-3">
            <Github className="h-5 w-5 shrink-0 text-accent" />
            <div>
              <span className="text-sm font-medium text-accent">Proyecto de código abierto</span>
              <p className="mt-1 text-sm leading-relaxed text-slate-300">
                El código, las capas de datos editoriales y el censo del corpus se publican en
                GitHub (AGPL-3.0 + CC BY 4.0)
                {REPO_PUBLIC
                  ? ""
                  : "; el repositorio está temporalmente en privado mientras se reconstruye su historial"}
                . Puedes proponer tramas nuevas con documentos oficiales, corregir con fuente lo ya
                publicado o mejorar el código.
              </p>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-3 sm:flex-col sm:items-stretch">
            <Link
              href="/contribuir"
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-ink-950 transition hover:bg-accent-glow"
            >
              Cómo contribuir <ArrowRight className="h-4 w-4" />
            </Link>
            {REPO_PUBLIC && (
              <a
                href={REPO_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center justify-center gap-2 rounded-xl glass px-4 py-2 text-sm text-slate-200 hover:bg-white/5"
              >
                <Github className="h-4 w-4" /> Ver en GitHub
              </a>
            )}
          </div>
        </section>

        {/* disclaimer */}
        <section className="mt-8 rounded-2xl glass p-5">
          <div className="flex gap-3">
            <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
            <p className="text-sm leading-relaxed text-slate-400">
              <span className="font-medium text-slate-200">Presunción de inocencia.</span> Esta
              herramienta organiza información de documentos públicos y procedimientos en curso. Las
              relaciones reflejan lo que afirman las fuentes citadas —indicios, imputaciones,
              acusaciones o, cuando ya las hay, condenas firmes— respetando en cada caso su estado
              procesal. Toda afirmación enlaza a su documento y página de origen.{" "}
              <span className="font-medium text-slate-200">Cualquier error señalado con su fuente
              se rectifica</span>
              :{" "}
              <a href="mailto:contacto@entramado.org" className="underline decoration-white/20 underline-offset-2 hover:text-accent">
                contacto@entramado.org
              </a>
              .
            </p>
          </div>
        </section>

        <footer className="mt-16 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-white/5 pt-6 text-xs text-slate-600">
          <span>Grafo Neo4j · fuentes judiciales y policiales, registros oficiales (BORME · BDNS · PLACSP) y prensa atribuida.</span>
          <span aria-hidden>·</span>
          <span>
            Datos societarios:{" "}
            <a href={NEARIUS_URL} target="_blank" rel="noopener noreferrer" className="hover:text-accent">
              Nearius
            </a>{" "}
            (BORME)
          </span>
          <span aria-hidden>·</span>
          <Link href="/glosario" className="hover:text-accent">
            Glosario
          </Link>
          <span aria-hidden>·</span>
          <Link href="/aviso-legal" className="hover:text-accent">
            Aviso legal
          </Link>
          {REPO_PUBLIC && (
            <>
              <span aria-hidden>·</span>
              <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className="hover:text-accent">
                GitHub
              </a>
            </>
          )}
        </footer>
      </div>
    </main>
  );
}
