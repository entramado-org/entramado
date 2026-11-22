import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ArrowLeft, ExternalLink, FileText, Network, ShieldAlert, Users } from "lucide-react";
import JsonLd from "@/components/seo/JsonLd";
import ProvenanceCitation from "@/components/ProvenanceCitation";
import { MercantilCard } from "@/components/entityDetail";
import { SITE_URL } from "@/lib/site";
import { getFicha, getIndexables, resolveSlug, FICHA_REVALIDATE } from "@/lib/fichas";
import type { FichaRelation } from "@/lib/fichas";
import { SOURCE_KIND_META, STATUS_META, accusationLabel, headlineStatus, relLabel, shortRole } from "@/lib/graphStyle";
import { glosarioHref } from "@/lib/glosario";
import { loadUltimaHora } from "@/lib/ultimaHora";

/* Ficha pública de entidad (spec 035): la página indexable de cada miembro del
 * reparto central (~300). ISR: se renderiza en el primer hit y se cachea 1 h —
 * nada de generateStaticParams (el API no es alcanzable en el build Docker).
 * Editorial: presunción de inocencia SIEMPRE — estados procesales con
 * "presunto" salvo condena, procedencia explícita en cada relación, y nada de
 * acusaciones en el JSON-LD (solo identidad). */

// Literal a propósito: Next 15+ lee la configuración de segmento en el build y rechaza
// identificadores importados. Debe coincidir con FICHA_REVALIDATE (lib/fichas.ts).
export const revalidate = 3600;
export const dynamicParams = true;

const TYPE_SINGULAR: Record<string, string> = {
  Person: "Persona",
  Company: "Empresa",
  Organization: "Organización",
};

/** jobTitle para el JSON-LD: SOLO identidad neutral. Los roles curados suelen
 *  añadir el estado procesal tras un ';' — eso nunca entra en el markup; si
 *  hasta la primera cláusula es procesal, mejor sin jobTitle. */
const PROCEDURAL_RE = /investigad|imputad|acusad|procesad|condenad|absuelt|querell/i;
function neutralJobTitle(role: string | null | undefined): string | undefined {
  const clause = role?.split(/[;.·]/)[0].trim();
  if (!clause || PROCEDURAL_RE.test(clause)) return undefined;
  return clause;
}

export async function generateMetadata(props: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const params = await props.params;
  const ent = await resolveSlug(params.slug);
  if (!ent) return { title: "Ficha no encontrada" };
  const desc =
    ent.teaser?.trim() ||
    `Ficha de ${ent.name} en el grafo de los casos de corrupción que rodean al Gobierno en España: conexiones documentadas con cita de fuente.`;
  return {
    title: `${ent.name} — ${shortRole(ent.role, TYPE_SINGULAR[ent.type] || ent.type)}`,
    description: desc.length > 155 ? desc.slice(0, 152).trimEnd() + "…" : desc,
    alternates: { canonical: `/entidad/${ent.slug}` },
    openGraph: {
      title: ent.name,
      description: desc,
      type: "profile",
      url: `/entidad/${ent.slug}`,
      // Un openGraph propio REEMPLAZA al del layout en el merging de Next →
      // sin esta línea la ficha perdería la imagen file-based del root.
      images: "/opengraph-image.png",
    },
  };
}

/** Frase completa de una relación, sin ambigüedad de dirección. */
function relationSentence(name: string, r: FichaRelation): { from: string; to: string } {
  return r.outgoing
    ? { from: name, to: r.neighbor.name }
    : { from: r.neighbor.name, to: name };
}

function RelationRow({
  name,
  r,
  slugById,
}: {
  name: string;
  r: FichaRelation;
  slugById: Map<string, string>;
}) {
  const { from, to } = relationSentence(name, r);
  const kind = SOURCE_KIND_META[r.link.source_kind ?? "sin_procedencia"];
  const status = r.link.status ? STATUS_META[r.link.status] : null;
  // Spec 061: una acusación se rotula por su ESTADO registrado («Investigado por»
  // mientras no conste un acto acusatorio), y si ese estado solo lo sostiene la
  // prensa, la frase lo dice.
  const relText =
    (accusationLabel(r.link.type, r.link.status) ?? relLabel(r.link.type)).toLowerCase() +
    (r.link.status_fuente_tipo === "prensa" ? " (según prensa)" : "");
  const neighborSlug = slugById.get(r.neighbor.id);
  const neighborEl = neighborSlug ? (
    <Link href={`/entidad/${neighborSlug}`} className="text-slate-100 underline decoration-white/20 underline-offset-2 hover:text-accent">
      {r.neighbor.name}
    </Link>
  ) : (
    <Link
      href={`/explore?focus=${encodeURIComponent(r.neighbor.id)}`}
      className="text-slate-100 hover:text-accent"
    >
      {r.neighbor.name}
    </Link>
  );
  return (
    <li className="rounded-xl border border-white/5 bg-white/[0.02] p-4">
      <p className="text-[15px] leading-snug text-slate-200">
        {r.outgoing ? (
          <>
            <span className="text-slate-400">{from}</span>{" "}
            <span className="text-accent">{relText}</span> {neighborEl}
          </>
        ) : (
          <>
            {neighborEl} <span className="text-accent">{relText}</span>{" "}
            <span className="text-slate-400">{to}</span>
          </>
        )}
        {r.link.amount != null && (
          <span className="ml-2 font-mono text-[13px] tabular-nums text-emerald-300">
            {r.link.amount.toLocaleString("es-ES")} {r.link.currency ?? "€"}
          </span>
        )}
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
        {kind && (
          <span style={{ color: kind.color }}>
            {kind.label}
            {(r.link.source_kind === "prensa" || r.link.source_kind === "ultima_hora") && r.link.outlet
              ? ` · según ${r.link.outlet}`
              : ""}
          </span>
        )}
        {status && (
          <span style={{ color: status.color }}>
            {status.label}
            {status.presunto ? " · presunto" : ""}
          </span>
        )}
        {r.link.status && r.link.status_fuente && r.link.status_fuente_tipo !== "prensa" &&
          r.link.status_fuente !== r.link.doc && (
          <span>
            estado fijado por {(r.link.status_fuente_tipo ?? "resolución").replace(/_/g, " ")}
            {r.link.status_fecha ? ` de ${r.link.status_fecha}` : ""} [{r.link.status_fuente}]
          </span>
        )}
        {r.link.status_fuente_tipo === "prensa" && r.link.status && (
          <span>
            estado según{" "}
            {r.link.status_fuente?.startsWith("http") ? (
              <a href={r.link.status_fuente} target="_blank" rel="noopener noreferrer"
                 className="underline decoration-white/20 underline-offset-2 hover:text-accent">
                prensa
              </a>
            ) : (
              "prensa"
            )}
            {r.link.status_fecha ? ` (${r.link.status_fecha})` : ""}, no la cita judicial
          </span>
        )}
        {r.link.doc && r.link.source_kind !== "contexto" && (
          <span className="font-mono">
            [{r.link.doc}
            {r.link.page != null ? `, p.${r.link.page}` : ""}]
          </span>
        )}
      </div>
      {r.link.quote && r.link.source_kind !== "contexto" && (
        <p className="mt-2 border-l-2 border-white/10 pl-3 text-[12px] italic leading-relaxed text-slate-500">
          «{r.link.quote.length > 220 ? r.link.quote.slice(0, 220).trimEnd() + "…" : r.link.quote}»
        </p>
      )}
    </li>
  );
}

export default async function FichaPage(props: { params: Promise<{ slug: string }> }) {
  const params = await props.params;
  const ent = await resolveSlug(params.slug);
  if (!ent) notFound();
  const [ficha, indexables] = await Promise.all([getFicha(ent.id), getIndexables()]);
  // Slug resolved but detail unavailable ⇒ transient API failure: fail the
  // request (5xx, uncached) rather than cache a wrong 404 for an hour.
  if (!ficha) throw new Error(`ficha unavailable for ${ent.id}`);
  const { detail, relations } = ficha;
  const node = detail.node;
  const slugById = new Map(indexables.map((e) => [e.id, e.slug]));
  const status = headlineStatus(node.chargeStatuses);
  const statusMeta = status ? STATUS_META[status] : null;
  const uh = (await loadUltimaHora(FICHA_REVALIDATE)).filter((i) => i.entities.includes(node.id));
  const typeEs = TYPE_SINGULAR[node.type] ?? node.type;

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "WebPage",
          name: node.name,
          url: `${SITE_URL}/entidad/${ent.slug}`,
          inLanguage: "es",
          dateModified: node.summaryUpdated || undefined,
          // Solo identidad neutral — nunca acusaciones ni delitos en el markup.
          about: {
            "@type": node.type === "Person" ? "Person" : "Organization",
            name: node.name,
            ...(node.type === "Person" && neutralJobTitle(node.role)
              ? { jobTitle: neutralJobTitle(node.role) }
              : {}),
          },
          publisher: { "@type": "Organization", name: "El Entramado", url: SITE_URL },
        }}
      />
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "BreadcrumbList",
          itemListElement: [
            { "@type": "ListItem", position: 1, name: "Inicio", item: SITE_URL },
            { "@type": "ListItem", position: 2, name: "Protagonistas", item: `${SITE_URL}/entidad` },
            { "@type": "ListItem", position: 3, name: node.name },
          ],
        }}
      />

      {/* nav */}
      <div className="mb-8 flex items-center justify-between">
        <Link href="/entidad" className="inline-flex items-center gap-2 text-sm text-slate-400 hover:text-accent">
          <ArrowLeft className="h-4 w-4" /> Protagonistas
        </Link>
        <Link
          href={`/explore?focus=${encodeURIComponent(node.id)}`}
          className="inline-flex items-center gap-2 rounded-lg glass px-3 py-1.5 text-sm text-slate-200 hover:bg-white/5"
        >
          <Network className="h-4 w-4" /> Ver en el grafo
        </Link>
      </div>

      {/* header */}
      <header className="mb-8">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
          <Users className="h-3.5 w-3.5" /> {typeEs}
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">{node.name}</h1>
        {node.role && <p className="mt-2 text-lg leading-snug text-slate-400">{node.role}</p>}
        {node.casos && node.casos.length > 0 && (
          <p className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-[11px] uppercase tracking-widest text-slate-600">Casos</span>
            {node.casos.map((c) => (
              <Link
                key={c.slug}
                href={`/caso/${c.slug}`}
                className="inline-flex items-center rounded-full glass px-2.5 py-1 text-xs text-slate-200 transition hover:border-accent/40 hover:text-accent"
              >
                {c.titulo}
              </Link>
            ))}
          </p>
        )}
        {status && statusMeta && (
          <p className="mt-3">
            <Link
              href={glosarioHref(status)}
              title="¿Qué significa? Ver en el glosario"
              className="inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium transition hover:bg-white/5"
              style={{ color: statusMeta.color, borderColor: `${statusMeta.color}55` }}
            >
              {statusMeta.label}
              {statusMeta.presunto ? " · presunto" : ""}
              {(node.chargeStatusesPress ?? []).some((p) => p.status === status) ? " · según prensa" : ""}
            </Link>
          </p>
        )}
      </header>

      {/* resumen curado */}
      {node.summary && (
        <section className="mb-8 rounded-2xl border border-accent/20 bg-accent/[0.04] p-6">
          <p className="mb-3 font-mono text-[10px] uppercase tracking-[0.22em] text-accent/70">resumen</p>
          <p className="whitespace-pre-line text-[15px] leading-relaxed text-slate-200">{node.summary}</p>
          <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-white/5 pt-3 text-[11px] text-slate-500">
            {node.summaryUpdated && <span className="tabular-nums">Actualizado: {node.summaryUpdated}</span>}
            {node.summarySources && node.summarySources.length > 0 && (
              <span className="flex flex-wrap items-center gap-1.5">
                <span className="uppercase tracking-[0.16em] text-slate-600">fuentes</span>
                {node.summarySources.map((url, i) => {
                  let host = url;
                  try {
                    host = new URL(url).hostname.replace(/^www\./, "");
                  } catch {
                    /* keep raw */
                  }
                  return (
                    <a
                      key={url}
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={url}
                      className="rounded bg-ink-800/60 px-1.5 py-0.5 font-mono text-accent/80 hover:text-accent hover:underline"
                    >
                      {i + 1}. {host}
                    </a>
                  );
                })}
              </span>
            )}
          </div>
        </section>
      )}

      {/* registro mercantil */}
      {node.mercantil && (
        <section className="mb-8">
          <MercantilCard m={node.mercantil} />
        </section>
      )}

      {/* última hora */}
      {uh.length > 0 && (
        <section className="mb-8 rounded-2xl border border-[#fb923c]/25 bg-[#fb923c]/[0.05] p-6">
          <p className="mb-3 font-mono text-[10px] uppercase tracking-[0.22em] text-[#fdba74]">última hora</p>
          <ul className="space-y-3">
            {uh.slice(0, 4).map((it, i) => (
              <li key={i} className="text-sm leading-snug">
                <span className="font-mono text-[11px] uppercase tracking-wider text-[#fdba74]">{it.date}</span>
                <p className="mt-0.5 text-slate-200">{it.headline}</p>
                <a
                  href={it.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-0.5 inline-flex items-center gap-1 text-[12px] text-[#fdba74] hover:underline"
                >
                  <ExternalLink className="h-3 w-3" /> {it.outlet}
                </a>
              </li>
            ))}
          </ul>
          <p className="mt-3 border-t border-white/5 pt-2 text-[11px] leading-snug text-slate-500">
            Prensa; sin documento judicial. Rige la presunción de inocencia.
          </p>
        </section>
      )}

      {/* relaciones principales */}
      {relations.length > 0 && (
        <section className="mb-8">
          <h2 className="mb-1 text-xl font-semibold text-slate-100">Conexiones documentadas</h2>
          <p className="mb-4 text-sm text-slate-500">
            Las relaciones con más respaldo documental. Cada una indica su procedencia y, cuando existe,
            su cita [documento, página].
          </p>
          <ul className="space-y-2.5">
            {relations.map((r) => (
              <RelationRow key={r.neighbor.id + r.link.type} name={node.name} r={r} slugById={slugById} />
            ))}
          </ul>
        </section>
      )}

      {/* fuentes */}
      {detail.sources.length > 0 && (
        <section className="mb-8">
          <h2 className="mb-1 text-xl font-semibold text-slate-100">En los documentos</h2>
          <p className="mb-4 text-sm text-slate-500">Pasajes del corpus judicial donde aparece.</p>
          <div className="space-y-2">
            {detail.sources.slice(0, 6).map((p) => (
              <ProvenanceCitation key={p.chunkId} p={p} />
            ))}
          </div>
        </section>
      )}

      {/* disclaimer — requisito editorial fijo de cada ficha */}
      <section className="mb-10 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">Presunción de inocencia.</span> Esta ficha
            organiza información de documentos públicos y procedimientos en curso. Las relaciones
            reflejan lo que afirman las fuentes citadas —indicios, imputaciones, acusaciones o, cuando
            ya las hay, condenas firmes— respetando en cada caso su estado procesal. Toda afirmación
            enlaza a su documento y página de origen.{" "}
            <Link href="/aviso-legal" className="text-slate-300 underline decoration-white/20 underline-offset-2 hover:text-accent">
              Aviso legal y vía de rectificación
            </Link>
            .
          </p>
        </div>
      </section>

      <footer className="flex flex-wrap gap-3">
        <Link
          href={`/explore?focus=${encodeURIComponent(node.id)}`}
          className="inline-flex items-center gap-2 rounded-xl bg-accent px-5 py-3 font-medium text-ink-950 transition hover:bg-accent-glow"
        >
          <Network className="h-4 w-4" /> Ver su red en el grafo
        </Link>
        <Link
          href="/documentos"
          className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
        >
          <FileText className="h-4 w-4" /> Buscar en los documentos
        </Link>
      </footer>
    </main>
  );
}
