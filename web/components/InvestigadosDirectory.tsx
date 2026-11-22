"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ChevronDown, Network, Search, Users } from "lucide-react";
import type { AccusedCharge, AccusedEntity } from "@/lib/fichas";
import { SOURCE_KIND_META, STATUS_META, headlineStatus, shortRole } from "@/lib/graphStyle";
import { glosarioHref, slugifyTermino } from "@/lib/glosario";

/* ────────────────────────────────────────────────────────────────────────────
 * Directorio de investigados (spec 037) — isla client de /investigados.
 * Filtros por estado procesal y delito + búsqueda por nombre. Cada cargo lleva
 * su procedencia y su cita: el desglose completo vive en un <details> por fila.
 * Editorial: NUNCA penas teóricas ni agregados de años; el único recuento es
 * "cuántas personas" pasan el filtro. Estado inicial ("todos") deja la lista
 * completa en el HTML servido.
 * ──────────────────────────────────────────────────────────────────────────── */

/** Delitos únicos de una entidad, cada uno con su estado más avanzado. */
function uniqueCrimes(charges: AccusedCharge[]): { crime: string; article?: string | null; status: string }[] {
  const by = new Map<string, { crime: string; article?: string | null; status: string }>();
  for (const c of charges) {
    const prev = by.get(c.crime);
    const rank = STATUS_META[c.status]?.rank ?? 0;
    if (!prev || rank > (STATUS_META[prev.status]?.rank ?? 0)) {
      by.set(c.crime, { crime: c.crime, article: c.article ?? prev?.article, status: c.status });
    } else if (!prev.article && c.article) {
      prev.article = c.article;
    }
  }
  return [...by.values()];
}

function ChargeLine({ c }: { c: AccusedCharge }) {
  const kind = SOURCE_KIND_META[c.sourceKind];
  const status = STATUS_META[c.status];
  return (
    <li className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
      <p className="text-[13px] leading-snug text-slate-200">
        <a href={glosarioHref(c.crime)} className="font-medium hover:text-accent">
          {c.crime}
        </a>
        {c.article && <span className="ml-1.5 font-mono text-[11px] text-slate-500">{c.article}</span>}
      </p>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
        {status && (
          <span style={{ color: status.color }}>
            {status.label}
            {status.presunto ? " · presunto" : ""}
          </span>
        )}
        {kind && (
          <span style={{ color: kind.color }}>
            {kind.label}
            {(c.sourceKind === "prensa" || c.sourceKind === "ultima_hora") && c.outlet
              ? ` · según ${c.outlet}`
              : ""}
          </span>
        )}
        {c.doc && (
          <span className="font-mono">
            [{c.doc}
            {c.page != null ? `, p.${c.page}` : ""}]
          </span>
        )}
      </div>
      {c.quote && (
        <p className="mt-1.5 border-l-2 border-white/10 pl-2.5 text-[11.5px] italic leading-relaxed text-slate-500">
          «{c.quote.length > 200 ? c.quote.slice(0, 200).trimEnd() + "…" : c.quote}»
        </p>
      )}
    </li>
  );
}

function Row({ e }: { e: AccusedEntity }) {
  const status = headlineStatus(e.charges.map((c) => c.status));
  const meta = status ? STATUS_META[status] : null;
  const crimes = uniqueCrimes(e.charges);
  const nameEl = e.fichaSlug ? (
    <Link href={`/entidad/${e.fichaSlug}`} className="text-slate-100 hover:text-accent">
      {e.name}
    </Link>
  ) : (
    <Link href={`/explore?focus=${encodeURIComponent(e.id)}`} className="text-slate-100 hover:text-accent">
      {e.name}
    </Link>
  );
  return (
    <li className="rounded-xl border border-white/5 bg-white/[0.02] p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-[16px] font-semibold leading-snug">{nameEl}</span>
        {meta && status && (
          <Link
            href={glosarioHref(status)}
            title="¿Qué significa? Ver en el glosario"
            className="inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium transition hover:bg-white/5"
            style={{ color: meta.color, borderColor: `${meta.color}55` }}
          >
            {meta.label}
            {meta.presunto ? " · presunto" : ""}
          </Link>
        )}
      </div>
      {e.role && <p className="mt-1 text-[13px] leading-snug text-slate-500">{shortRole(e.role, "")}</p>}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {crimes.map((c) => (
          <a
            key={c.crime}
            href={glosarioHref(c.crime)}
            title={c.article ? `${c.crime} (${c.article}) — ver en el glosario` : `${c.crime} — ver en el glosario`}
            className="inline-flex items-center rounded-full glass px-2.5 py-0.5 text-[11.5px] text-slate-300 transition hover:border-accent/40 hover:text-accent"
          >
            {c.crime}
          </a>
        ))}
        {e.casos?.map((c) => (
          <Link
            key={c.slug}
            href={`/caso/${c.slug}`}
            className="inline-flex items-center rounded-full border border-accent/25 bg-accent/[0.06] px-2.5 py-0.5 text-[11.5px] text-accent/90 transition hover:bg-accent/15"
          >
            {c.titulo}
          </Link>
        ))}
      </div>
      <details className="group mt-3">
        <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[12px] text-slate-500 hover:text-accent [&::-webkit-details-marker]:hidden">
          <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
          Cargos y fuentes ({e.charges.length})
        </summary>
        <ul className="mt-2 space-y-2">
          {e.charges.map((c, i) => (
            <ChargeLine key={`${c.crime}-${c.status}-${c.doc ?? ""}-${i}`} c={c} />
          ))}
        </ul>
      </details>
    </li>
  );
}

export default function InvestigadosDirectory({ entities }: { entities: AccusedEntity[] }) {
  const [estado, setEstado] = useState<string>("todos");
  const [delito, setDelito] = useState<string>("todos");
  const [q, setQ] = useState("");

  const estados = useMemo(() => {
    const set = new Set<string>();
    for (const e of entities) for (const c of e.charges) set.add(c.status);
    return [...set].sort((a, b) => (STATUS_META[b]?.rank ?? 0) - (STATUS_META[a]?.rank ?? 0));
  }, [entities]);

  const delitos = useMemo(() => {
    const count = new Map<string, number>();
    for (const e of entities)
      for (const crime of new Set(e.charges.map((c) => c.crime)))
        count.set(crime, (count.get(crime) ?? 0) + 1);
    return [...count.entries()].sort((a, b) => b[1] - a[1]);
  }, [entities]);

  const visibles = entities.filter((e) => {
    if (estado !== "todos" && !e.charges.some((c) => c.status === estado)) return false;
    if (delito !== "todos" && !e.charges.some((c) => c.crime === delito)) return false;
    if (q.trim()) {
      const needle = slugifyTermino(q);
      if (!slugifyTermino(e.name).includes(needle)) return false;
    }
    return true;
  });

  const chip = (on: boolean) =>
    `min-h-[38px] shrink-0 rounded-lg border px-3 py-1.5 text-[12.5px] transition-colors ${
      on ? "border-accent/40 bg-accent/15 text-accent" : "glass text-slate-300 hover:bg-white/5"
    }`;

  return (
    <section aria-label="Directorio de investigados">
      {/* búsqueda */}
      <div className="mb-3 flex items-center gap-2 rounded-xl glass px-3 py-2">
        <Search className="h-4 w-4 shrink-0 text-slate-500" />
        <input
          type="search"
          value={q}
          onChange={(ev) => setQ(ev.target.value)}
          placeholder="Buscar por nombre…"
          aria-label="Buscar por nombre"
          className="w-full bg-transparent text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none"
        />
      </div>

      {/* filtro por estado */}
      <div role="group" aria-label="Filtrar por estado procesal" className="-mx-1 mb-2 flex gap-2 overflow-x-auto whitespace-nowrap px-1 pb-1">
        <button type="button" aria-pressed={estado === "todos"} onClick={() => setEstado("todos")} className={chip(estado === "todos")}>
          Todos · {entities.length}
        </button>
        {estados.map((s) => {
          const meta = STATUS_META[s];
          const n = entities.filter((e) => e.charges.some((c) => c.status === s)).length;
          const on = estado === s;
          return (
            <button key={s} type="button" aria-pressed={on} onClick={() => setEstado(on ? "todos" : s)} className={chip(on)}>
              <span style={on ? undefined : { color: meta?.color }}>{meta?.label ?? s}</span> · {n}
            </button>
          );
        })}
      </div>

      {/* filtro por delito */}
      <div role="group" aria-label="Filtrar por delito" className="-mx-1 mb-3 flex gap-2 overflow-x-auto whitespace-nowrap px-1 pb-1">
        <button type="button" aria-pressed={delito === "todos"} onClick={() => setDelito("todos")} className={chip(delito === "todos")}>
          Cualquier delito
        </button>
        {delitos.map(([crime, n]) => {
          const on = delito === crime;
          return (
            <button key={crime} type="button" aria-pressed={on} onClick={() => setDelito(on ? "todos" : crime)} className={chip(on)}>
              {crime} · {n}
            </button>
          );
        })}
      </div>

      <p aria-live="polite" className="mb-4 text-[12px] text-slate-500">
        {visibles.length} {visibles.length === 1 ? "entidad" : "entidades"} con cargos
        {estado !== "todos" && STATUS_META[estado] ? ` en estado «${STATUS_META[estado].label.toLowerCase()}»` : ""}
        {delito !== "todos" ? ` por ${delito}` : ""}.
      </p>

      {visibles.length > 0 ? (
        <ul className="space-y-3">
          {visibles.map((e) => (
            <Row key={e.id} e={e} />
          ))}
        </ul>
      ) : (
        <div className="rounded-xl glass p-8 text-center text-sm text-slate-500">
          <Users className="mx-auto mb-2 h-5 w-5 text-slate-600" />
          Nadie cumple ese filtro. Prueba a quitar alguno.
        </div>
      )}

      <p className="mt-6 flex items-center gap-2 text-[12px] text-slate-600">
        <Network className="h-3.5 w-3.5" /> Cada nombre enlaza a su ficha o a su red en el grafo.
      </p>
    </section>
  );
}
