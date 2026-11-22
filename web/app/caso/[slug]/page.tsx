import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import {
  ArrowLeft,
  Coins,
  ExternalLink,
  FileText,
  FolderOpen,
  Network,
  ShieldAlert,
  Users,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import JsonLd from "@/components/seo/JsonLd";
import CaseTimeline from "@/components/CaseTimeline";
import { SITE_URL } from "@/lib/site";
import { getCaso, getCasoBriefing, getCasos, CASO_REVALIDATE } from "@/lib/casos";
import type { CasoImplicado } from "@/lib/casos";
import { CASE_STATUS_META, STATUS_META, headlineStatus, shortRole } from "@/lib/graphStyle";
import { glosarioHref } from "@/lib/glosario";
import { loadUltimaHora } from "@/lib/ultimaHora";

/* Ficha pública de caso (spec 038): la vista agregada de una causa — implicados
 * con su estado procesal EN VIVO (del grafo, citado), cronología [doc, p.N],
 * documentos y flujos. Mismo régimen que las fichas de entidad (spec 035):
 * ISR sin generateStaticParams (el API no es alcanzable en el build Docker).
 * Editorial: presunción de inocencia; JSON-LD solo neutral (título del caso,
 * breadcrumb) — nunca personas con estados ni delitos en el markup. */

// Literal a propósito: Next 15+ lee la configuración de segmento en el build y rechaza
// identificadores importados. Debe coincidir con CASO_REVALIDATE (lib/casos.ts).
export const revalidate = 3600;
export const dynamicParams = true;

const MAX_CRONO = 120;

export async function generateMetadata(props: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const params = await props.params;
  const casos = await getCasos();
  const c = casos.find((x) => x.slug === params.slug);
  if (!c) return { title: "Caso no encontrado" };
  const desc = c.dek.length > 155 ? c.dek.slice(0, 152).trimEnd() + "…" : c.dek;
  return {
    title: c.titulo,
    description: desc,
    alternates: { canonical: `/caso/${c.slug}` },
    openGraph: {
      title: c.titulo,
      description: desc,
      type: "article",
      url: `/caso/${c.slug}`,
      // Un openGraph propio REEMPLAZA al del layout → declarar la imagen.
      images: "/opengraph-image.png",
    },
  };
}

function ImplicadoCard({ imp }: { imp: CasoImplicado }) {
  const status = headlineStatus(imp.charges.map((c) => c.status));
  const meta = status ? STATUS_META[status] : null;
  const crimes = [...new Set(imp.charges.map((c) => c.crime))];
  const nameEl = imp.fichaSlug ? (
    <Link href={`/entidad/${imp.fichaSlug}`} className="text-slate-100 hover:text-accent">
      {imp.name}
    </Link>
  ) : (
    <Link href={`/explore?focus=${encodeURIComponent(imp.id)}`} className="text-slate-100 hover:text-accent">
      {imp.name}
    </Link>
  );
  return (
    <li className="rounded-xl border border-white/5 bg-white/[0.02] p-4">
      <p className="text-[15px] font-semibold leading-snug">{nameEl}</p>
      <p className="mt-0.5 text-[12.5px] leading-snug text-slate-500">
        {imp.papel || shortRole(imp.role, "")}
      </p>
      {(meta || crimes.length > 0) && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {meta && status && (
            <Link
              href={glosarioHref(status)}
              title="¿Qué significa? Ver en el glosario"
              className="inline-flex items-center rounded-full border px-2 py-0.5 text-[10.5px] font-medium transition hover:bg-white/5"
              style={{ color: meta.color, borderColor: `${meta.color}55` }}
            >
              {meta.label}
              {meta.presunto ? " · presunto" : ""}
            </Link>
          )}
          {crimes.map((crime) => (
            <a
              key={crime}
              href={glosarioHref(crime)}
              className="inline-flex items-center rounded-full glass px-2 py-0.5 text-[10.5px] text-slate-400 transition hover:border-accent/40 hover:text-accent"
            >
              {crime}
            </a>
          ))}
        </div>
      )}
    </li>
  );
}

export default async function CasoPage(props: { params: Promise<{ slug: string }> }) {
  const params = await props.params;
  const caso = await getCaso(params.slug);
  if (!caso) notFound();
  const { meta } = caso;
  const briefing = meta.briefingSlug ? await getCasoBriefing(meta.briefingSlug) : null;
  // El briefing arranca con su propio "# título" — fuera, aquí manda el header.
  const briefingBody = briefing?.replace(/^#\s[^\n]+\n+/, "") ?? null;
  const st = CASE_STATUS_META[meta.estadoKind];
  const entidadesSet = new Set(caso.entidadesIds);
  const ultimaHora = await loadUltimaHora(CASO_REVALIDATE);
  const uh = ultimaHora.filter(
    (i) =>
      (i.caseTag && caso.ultimaHoraTags.includes(i.caseTag)) ||
      i.entities.some((id) => entidadesSet.has(id)),
  ).slice(0, 5);
  const crono = caso.cronologia.slice(-MAX_CRONO);

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "WebPage",
          name: meta.titulo,
          description: meta.dek,
          url: `${SITE_URL}/caso/${meta.slug}`,
          inLanguage: "es",
          dateModified: meta.updated || undefined,
          publisher: { "@type": "Organization", name: "El Entramado", url: SITE_URL },
        }}
      />
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "BreadcrumbList",
          itemListElement: [
            { "@type": "ListItem", position: 1, name: "Inicio", item: SITE_URL },
            { "@type": "ListItem", position: 2, name: "Casos", item: `${SITE_URL}/caso` },
            { "@type": "ListItem", position: 3, name: meta.titulo },
          ],
        }}
      />

      {/* nav */}
      <div className="mb-8 flex items-center justify-between">
        <Link href="/caso" className="inline-flex items-center gap-2 text-sm text-slate-400 hover:text-accent">
          <ArrowLeft className="h-4 w-4" /> Casos
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
          <FolderOpen className="h-3.5 w-3.5" /> Caso
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">{meta.titulo}</h1>
        <p className="mt-3 text-lg leading-relaxed text-slate-400">{meta.dek}</p>
        <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-[12px]">
          {st && (
            <span
              className="inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium"
              style={{ color: st.color, borderColor: `${st.color}55` }}
            >
              {st.label}
            </span>
          )}
          {meta.causa && <span className="text-slate-500">{meta.causa}</span>}
          {meta.updated && <span className="tabular-nums text-slate-600">Actualizado {meta.updated}</span>}
        </div>
        {meta.estado && <p className="mt-3 text-sm leading-relaxed text-slate-400">{meta.estado}</p>}
      </header>

      {/* implicados */}
      {caso.implicados.length > 0 && (
        <section className="mb-10">
          <h2 className="mb-1 flex items-center gap-2 text-xl font-semibold text-slate-100">
            <Users className="h-4 w-4 text-accent" /> Implicados
          </h2>
          <p className="mb-4 text-sm text-slate-500">
            El estado procesal de cada uno sale del grafo en vivo, citado a su fuente; los términos
            enlazan al glosario. Rige la presunción de inocencia.
          </p>
          <ul className="grid gap-2.5 sm:grid-cols-2">
            {caso.implicados.map((imp) => (
              <ImplicadoCard key={imp.id} imp={imp} />
            ))}
          </ul>
        </section>
      )}

      {/* resumen (briefing) */}
      {briefingBody ? (
        <section className="mb-10 rounded-2xl border border-accent/20 bg-accent/[0.04] p-6">
          <p className="mb-3 font-mono text-[10px] uppercase tracking-[0.22em] text-accent/70">
            el caso, contado
          </p>
          <article
            className="prose prose-invert max-w-none
                       prose-headings:font-semibold prose-headings:tracking-tight prose-headings:text-slate-100
                       prose-h2:mt-8 prose-h2:text-accent prose-h2:text-xs prose-h2:uppercase prose-h2:tracking-widest
                       prose-p:text-slate-300 prose-p:leading-relaxed prose-p:text-[14.5px]
                       prose-strong:text-slate-100 prose-li:text-slate-300
                       prose-blockquote:border-accent/40 prose-blockquote:text-slate-400
                       prose-em:text-slate-400"
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{briefingBody}</ReactMarkdown>
          </article>
        </section>
      ) : (
        meta.briefingSlug && (
          <section className="mb-10">
            <Link
              href={`/briefings/${meta.briefingSlug}`}
              className="inline-flex items-center gap-2 rounded-xl glass px-4 py-2.5 text-sm text-slate-200 hover:bg-white/5"
            >
              <FileText className="h-4 w-4" /> Leer el briefing del caso
            </Link>
          </section>
        )
      )}

      {/* última hora del caso */}
      {uh.length > 0 && (
        <section className="mb-10 rounded-2xl border border-[#fb923c]/25 bg-[#fb923c]/[0.05] p-6">
          <p className="mb-3 font-mono text-[10px] uppercase tracking-[0.22em] text-[#fdba74]">
            última hora del caso
          </p>
          <ul className="space-y-3">
            {uh.map((it, i) => (
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

      {/* cronología */}
      {crono.length > 0 && (
        <section className="mb-10">
          <h2 className="mb-1 text-xl font-semibold text-slate-100">Cronología documentada</h2>
          <p className="mb-4 text-sm text-slate-500">
            Hitos fechados extraídos de los documentos del caso, cada uno con su cita
            [documento, página].
            {caso.cronologia.length > MAX_CRONO
              ? ` Se muestran los ${MAX_CRONO} más recientes de ${caso.cronologia.length}.`
              : ""}
          </p>
          <CaseTimeline events={crono} />
        </section>
      )}

      {/* flujos */}
      {caso.flujos.length > 0 && (
        <section className="mb-10">
          <h2 className="mb-1 flex items-center gap-2 text-xl font-semibold text-slate-100">
            <Coins className="h-4 w-4 text-accent" /> El rastro del dinero
          </h2>
          <p className="mb-4 text-sm text-slate-500">
            Transferencias y adjudicaciones con importe que tocan a los implicados del caso.{" "}
            <Link href="/flujos" className="text-accent hover:underline">
              Ver todos los flujos →
            </Link>
          </p>
          <ul className="space-y-2">
            {caso.flujos.slice(0, 12).map((f, i) => (
              <li key={i} className="rounded-xl border border-white/5 bg-white/[0.02] p-3.5 text-sm">
                <p className="leading-snug text-slate-200">
                  <span className="text-slate-300">{f.source}</span>{" "}
                  <span className="text-accent">→</span>{" "}
                  <span className="text-slate-300">{f.target}</span>
                  <span className="ml-2 font-mono text-[13px] tabular-nums text-emerald-300">
                    {f.amount.toLocaleString("es-ES")} {f.currency === "EUR" ? "€" : f.currency}
                  </span>
                </p>
                {f.doc && (
                  <p className="mt-1 font-mono text-[11px] text-slate-600">
                    [{f.doc}
                    {f.page != null ? `, p.${f.page}` : ""}]
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* documentos */}
      {caso.documentos.length > 0 && (
        <section className="mb-10">
          <h2 className="mb-1 flex items-center gap-2 text-xl font-semibold text-slate-100">
            <FileText className="h-4 w-4 text-accent" /> Los documentos del caso
          </h2>
          <p className="mb-4 text-sm text-slate-500">
            El corpus judicial y oficial del que sale esta ficha. Puedes buscar en su texto íntegro
            en{" "}
            <Link href="/documentos" className="text-accent hover:underline">
              Documentos
            </Link>
            .
          </p>
          <ul className="space-y-1.5">
            {caso.documentos.map((d) => (
              <li
                key={d.file}
                className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 rounded-lg border border-white/5 bg-white/[0.02] px-3.5 py-2.5"
              >
                <span className="font-mono text-[12.5px] text-slate-300">{d.file}</span>
                <span className="text-[11px] tabular-nums text-slate-600">
                  {d.date ?? ""}
                  {d.pageCount ? ` · ${d.pageCount} págs` : ""}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* relacionados */}
      {caso.relacionados.length > 0 && (
        <section className="mb-10">
          <span className="mr-2 text-[11px] uppercase tracking-widest text-slate-600">
            Casos relacionados
          </span>
          {caso.relacionados.map((r) => (
            <Link
              key={r.slug}
              href={`/caso/${r.slug}`}
              className="mr-2 inline-flex items-center rounded-full glass px-3 py-1 text-sm text-slate-200 transition hover:border-accent/40 hover:text-accent"
            >
              {r.titulo}
            </Link>
          ))}
        </section>
      )}

      {/* disclaimer — requisito editorial fijo */}
      <section className="mb-10 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">Presunción de inocencia.</span> Esta ficha
            agrega información de documentos públicos sobre un procedimiento{" "}
            {meta.estadoKind === "sentencia" ? "con sentencia y piezas aún en curso" : "en curso"}.
            La selección de implicados y documentos es editorial; los estados procesales y las citas
            salen del grafo y de sus fuentes. Una imputación o una acusación no son una condena.{" "}
            <Link href="/aviso-legal" className="text-slate-300 underline decoration-white/20 underline-offset-2 hover:text-accent">
              Aviso legal y vía de rectificación
            </Link>
            .
          </p>
        </div>
      </section>

      <footer className="flex flex-wrap gap-3">
        <Link
          href={`/explore?focus=${encodeURIComponent(caso.entidadesIds[0] ?? "")}`}
          className="inline-flex items-center gap-2 rounded-xl bg-accent px-5 py-3 font-medium text-ink-950 transition hover:bg-accent-glow"
        >
          <Network className="h-4 w-4" /> Ver la red del caso en el grafo
        </Link>
        <Link
          href="/investigados"
          className="inline-flex items-center gap-2 rounded-xl glass px-5 py-3 text-slate-200 hover:bg-white/5"
        >
          <Users className="h-4 w-4" /> Directorio de investigados
        </Link>
      </footer>
    </main>
  );
}
