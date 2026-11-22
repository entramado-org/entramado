"use client";
import { useEffect, useState } from "react";
import type { GraphNode, NodeDetail } from "@/lib/types";
import type { UltimaHoraItem } from "@/lib/ultimaHora";
import { fetchUltimaHora } from "@/lib/api";
import { NEARIUS_URL } from "@/lib/attribution";
import ProvenanceCitation from "./ProvenanceCitation";

// Última-hora feed, fetched at runtime (spec 044 — no longer baked into the
// bundle). Module-level cache: the feed changes a few times a day at most, so
// one fetch per page load is plenty (the endpoint adds Cache-Control 300 s on
// top). API down → empty feed → the section just doesn't render.
let uhCache: UltimaHoraItem[] | null = null;
let uhPromise: Promise<UltimaHoraItem[]> | null = null;

function useUltimaHora(): UltimaHoraItem[] {
  const [items, setItems] = useState<UltimaHoraItem[]>(uhCache ?? []);
  useEffect(() => {
    if (uhCache) return;
    uhPromise ??= fetchUltimaHora().then(
      (j) => (uhCache = j.items ?? []),
      () => (uhCache = []),
    );
    let alive = true;
    uhPromise.then((v) => {
      if (alive) setItems(v);
    });
    return () => {
      alive = false;
    };
  }, []);
  return items;
}

// Shared bodies for the Síntesis and Fuentes tabs, used by BOTH the desktop
// NodeDetailPanel and the mobile MobileRelations surface so the two never drift.

/** Network-position metrics (spec 013) — relocated out of the panel's fixed
 *  header into the Síntesis body so the header stays compact on every surface.
 *  The verbose explanations sit behind a native <details> so the default view is
 *  just two numbers + the community label. Reads straight off the GraphNode, so
 *  both NodeDetailPanel and MobileRelations get it for free via SintesisBody. */
export function NetworkMetricsCard({ node }: { node: GraphNode }) {
  if (node.betweenness == null && node.closeness == null && !node.community_label) {
    return null;
  }
  return (
    <div className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2.5">
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-slate-500">Intermediación</span>
          <span className="font-mono tabular-nums text-slate-200">{(node.betweenness ?? 0).toFixed(3)}</span>
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-slate-500">Cercanía</span>
          <span className="font-mono tabular-nums text-slate-200">{(node.closeness ?? 0).toFixed(3)}</span>
        </div>
      </div>
      {node.community_label && (
        <p className="mt-1.5 border-t border-white/5 pt-1.5 text-[11px] text-slate-400">
          <span className="text-slate-500">Comunidad: </span>
          {node.community_label}
        </p>
      )}
      <details className="group mt-1.5">
        <summary className="cursor-pointer select-none text-[10px] text-slate-600 transition hover:text-slate-400">
          ¿Qué significan estas métricas?
        </summary>
        <p className="mt-1 text-[10px] leading-snug text-slate-600">
          Intermediación alta = hace de <span className="text-slate-400">puente</span> entre sub-tramas (no implica más que su posición en la red); cercanía = a cuántos pasos está del resto.
        </p>
        {node.community_label && (
          <p className="mt-1 text-[10px] italic leading-snug text-slate-600">
            Sub-trama por co-aparición en el grafo (agrupación estructural, NO una imputación).
          </p>
        )}
      </details>
    </div>
  );
}

/** Registro mercantil / BORME overlay (spec 026). Official public registry data
 *  (identity/structure), cited by borme_id — NOT a judicial fact nor an accusation.
 *  Rendered only when the node carries `is_mercantil`. */
export function MercantilCard({ m }: { m: NonNullable<NodeDetail["node"]["mercantil"]> }) {
  const rows: [string, string][] = [];
  if (m.nif) rows.push(["NIF/CIF", m.nif]);
  if (m.hoja) rows.push(["Hoja registral", m.hoja]);
  if (m.capital != null) rows.push(["Capital social", `${m.capital.toLocaleString("es-ES")} €`]);
  if (m.capital_historico) rows.push(["Histórico de capital", m.capital_historico]);
  if (m.cnae) rows.push(["CNAE", m.cnae]);
  if (m.objeto_social) rows.push(["Objeto social", m.objeto_social]);
  if (m.fecha) rows.push(["Constitución", m.fecha]);
  if (m.provincia) rows.push(["Provincia", m.provincia]);
  if (m.domicilio) rows.push(["Domicilio", m.domicilio]);
  // Situación mercantil (concurso / disolución) — señal pública, NO imputación.
  const badges: string[] = [];
  if (m.en_concurso) {
    const det = [m.concurso_fecha, m.concurso_juzgado].filter(Boolean).join(" · ");
    badges.push("En concurso de acreedores" + (det ? ` (${det})` : ""));
  }
  if (m.en_liquidacion) badges.push("En liquidación" + (m.liquidacion_fecha ? ` (${m.liquidacion_fecha})` : ""));
  if (m.disuelta) badges.push("Disuelta/extinguida" + (m.disuelta_fecha ? ` (${m.disuelta_fecha})` : ""));
  if (rows.length === 0 && badges.length === 0) return null;
  return (
    <div className="rounded-xl border border-sky-500/20 bg-sky-500/[0.04] p-4">
      <p className="mb-2 font-mono text-[9px] uppercase tracking-[0.22em] text-sky-300/80">
        datos registrales · BORME
      </p>
      {badges.length > 0 && (
        <div className="mb-2.5 flex flex-wrap gap-1.5">
          {badges.map((b) => (
            <span
              key={b}
              className="rounded-full border border-amber-500/30 bg-amber-500/[0.08] px-2 py-0.5 text-[10px] font-medium text-amber-300"
            >
              {b}
            </span>
          ))}
        </div>
      )}
      {rows.length > 0 && (
        <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-[12px]">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-slate-500">{k}</dt>
              <dd className="text-right font-mono tabular-nums text-slate-200">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="mt-2.5 border-t border-white/5 pt-2 text-[10px] leading-snug text-slate-500">
        Registro Mercantil (público){m.borme ? ` · ${m.borme}` : ""} · vía{" "}
        <a href={NEARIUS_URL} target="_blank" rel="noopener noreferrer" className="text-sky-300/80 hover:underline">
          Nearius
        </a>
        . Datos de identidad y estructura societaria — no son hechos ni indicios del caso. Rige la
        presunción de inocencia.
      </p>
    </div>
  );
}

export function SintesisBody({
  node,
  detail,
  loading,
}: {
  node: GraphNode;
  detail: NodeDetail | null;
  loading: boolean;
}) {
  const ultimaHora = useUltimaHora();
  // Longest source quote → hero pull-quote.
  const heroQuote = detail?.sources
    .slice()
    .sort((a, b) => (b.quote?.length ?? 0) - (a.quote?.length ?? 0))[0];

  return (
    <div className="space-y-4 p-4">
      {loading && <p className="text-sm text-slate-500">Cargando…</p>}
      <NetworkMetricsCard node={node} />
      {!loading && detail?.node.summary && (
        <div className="rounded-xl border border-accent/20 bg-accent/[0.04] p-4">
          <p className="mb-2 font-mono text-[9px] uppercase tracking-[0.22em] text-accent/70">
            resumen
          </p>
          <p className="whitespace-pre-line text-[13px] leading-relaxed text-slate-200">
            {detail.node.summary}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-white/5 pt-2 text-[10px] text-slate-500">
            {detail.node.summaryUpdated && (
              <span className="tabular-nums">Actualizado: {detail.node.summaryUpdated}</span>
            )}
            {detail.node.summarySources && detail.node.summarySources.length > 0 && (
              <span className="flex flex-wrap items-center gap-1.5">
                <span className="uppercase tracking-[0.16em] text-slate-600">fuentes</span>
                {detail.node.summarySources.map((url, i) => {
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
        </div>
      )}
      {!loading && detail?.node.mercantil && <MercantilCard m={detail.node.mercantil} />}
      {node && (() => {
        const uh = ultimaHora.filter((i) => i.entities.includes(node.id));
        if (uh.length === 0) return null;
        return (
          <div className="rounded-xl border border-[#fb923c]/25 bg-[#fb923c]/[0.05] p-4">
            <p className="mb-2 font-mono text-[9px] uppercase tracking-[0.22em] text-[#fdba74]">
              última hora
            </p>
            <ul className="space-y-2.5">
              {uh.map((it, i) => (
                <li key={i} className="text-[12px] leading-snug">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="shrink-0 font-mono text-[10px] uppercase tracking-wider text-[#fdba74]">
                      {it.date}
                    </span>
                    {it.status && (
                      <span className="rounded-full bg-orange-500/15 px-1.5 font-mono text-[9px] uppercase tracking-wider text-orange-300">
                        {it.status} · presunto
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-slate-200">{it.headline}</p>
                  <a
                    href={it.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-0.5 inline-block text-[11px] text-[#fdba74] hover:underline"
                  >
                    {it.outlet}
                  </a>
                </li>
              ))}
            </ul>
            <p className="mt-2.5 border-t border-white/5 pt-2 text-[10px] leading-snug text-slate-500">
              Prensa; sin documento judicial. Rige la presunción de inocencia.
            </p>
          </div>
        );
      })()}
      {!loading && heroQuote && (
        <figure className="relative rounded-xl border border-white/5 bg-ink-800/30 p-4">
          <span
            aria-hidden
            className="absolute -top-3 left-3 font-serif text-4xl leading-none text-accent/30"
          >
            &ldquo;
          </span>
          <blockquote className="text-[13px] leading-relaxed text-slate-300">
            {(heroQuote.quote || "").slice(0, 360).trim()}
            {(heroQuote.quote || "").length > 360 ? "…" : ""}
          </blockquote>
          <figcaption className="mt-3 flex items-center justify-between text-[10px] text-slate-500">
            <span className="font-mono uppercase tracking-[0.16em] text-accent/80">
              {heroQuote.doc}
            </span>
            <span className="tabular-nums">página {heroQuote.page}</span>
          </figcaption>
        </figure>
      )}
      {detail?.node.aliases && detail.node.aliases.length > 0 && (
        <div>
          <p className="mb-1 font-mono text-[9px] uppercase tracking-[0.22em] text-slate-600">
            también conocida como
          </p>
          <p className="text-xs leading-relaxed text-slate-400">
            {detail.node.aliases.slice(0, 12).join(" · ")}
          </p>
        </div>
      )}
    </div>
  );
}

export function FuentesBody({
  detail,
  loading,
}: {
  detail: NodeDetail | null;
  loading: boolean;
}) {
  return (
    <div className="space-y-2 p-4">
      {loading && <p className="text-sm text-slate-500">Cargando…</p>}
      {!loading && detail && detail.sources.length === 0 && (
        <p className="text-sm text-slate-500">Sin citas registradas.</p>
      )}
      {detail?.sources.map((p) => <ProvenanceCitation key={p.chunkId} p={p} />)}
    </div>
  );
}
