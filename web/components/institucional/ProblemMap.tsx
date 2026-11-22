"use client";

import { useEffect, useState } from "react";
import { ArrowDown } from "lucide-react";
import { CATEGORIAS, PROBLEMAS, type CategoriaId, type Problema } from "@/lib/institucional";
import { hasBoeEvidence } from "@/lib/boeNombramientos";

/* ────────────────────────────────────────────────────────────────────────────
 * Mapa de problemas — isla client del dashboard de /institucional.
 * Grid de tarjetas-resumen (una por problema) con filtro por categoría y un
 * modo "¿Cómo me afecta?" que cambia la explicación llana por la cotidiana.
 * Cada tarjeta es un <a href="#seccion"> → la navegación funciona sin JS y el
 * estado inicial ("todas", modo normal) deja las 27 tarjetas en el HTML SSG.
 * ──────────────────────────────────────────────────────────────────────────── */

const MODO_KEY = "sm:institucional.modo.v1";

const CAT_BADGE: Record<CategoriaId, string> = {
  "poder-justicia": "border-violet-400/30 bg-violet-400/10 text-violet-300",
  "reglas-juego": "border-sky-400/30 bg-sky-400/10 text-sky-300",
  "estado-gasto": "border-amber-400/30 bg-amber-400/10 text-amber-300",
  bolsillo: "border-rose-400/30 bg-rose-400/10 text-rose-300",
  europa: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
};

const STAT_TONE: Record<NonNullable<Problema["stat"]["tone"]> | "default", string> = {
  accent: "text-accent text-glow",
  amber: "text-amber-400",
  rose: "text-rose-400",
  default: "text-accent text-glow",
};

function ProblemCard({ p, cotidiano }: { p: Problema; cotidiano: boolean }) {
  const cat = CATEGORIAS.find((c) => c.id === p.categoria);
  return (
    <a
      href={`#${p.id}`}
      aria-label={`${p.titulo}: ${p.stat.value}${p.stat.unit ?? ""} — ir a la sección completa`}
      className="group flex flex-col rounded-xl border border-white/5 bg-ink-900/40 p-4 transition-colors hover:border-accent/30 hover:bg-ink-900/70 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent/60 animate-fade-in"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-baseline gap-1">
          <span className={`text-2xl font-bold tracking-tight tabular-nums ${STAT_TONE[p.stat.tone ?? "default"]}`}>
            {p.stat.value}
          </span>
          {p.stat.unit && <span className="text-xs font-medium text-slate-400">{p.stat.unit}</span>}
        </div>
        <span className="flex shrink-0 items-center gap-1">
          {hasBoeEvidence(p.id) && (
            <span
              className="rounded-full border border-accent/30 bg-accent/10 px-2 py-0.5 font-mono text-[8px] uppercase tracking-[0.14em] text-accent"
              title="Sección con evidencia de nombramientos del BOE"
            >
              BOE
            </span>
          )}
          <span
            className={`rounded-full border px-2 py-0.5 font-mono text-[8px] uppercase tracking-[0.14em] ${CAT_BADGE[p.categoria]}`}
          >
            {cat?.label}
          </span>
        </span>
      </div>
      <p className="mt-0.5 text-[11px] leading-snug text-slate-500">{p.stat.label}</p>
      <h3 className="mt-2.5 text-[14px] font-semibold leading-snug text-slate-100">{p.titulo}</h3>
      <p className="mt-1.5 flex-1 text-[12.5px] leading-relaxed text-slate-400">
        {cotidiano ? (
          <>
            <span className="font-medium text-accent/80">En tu vida: </span>
            {p.cotidiano}
          </>
        ) : (
          p.llano
        )}
      </p>
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-white/5 pt-2.5">
        <span className="truncate text-[10px] text-slate-600">{p.fuente.ref}</span>
        <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-accent/80 group-hover:text-accent">
          Sección completa <ArrowDown className="h-3 w-3" />
        </span>
      </div>
    </a>
  );
}

export default function ProblemMap() {
  const [cat, setCat] = useState<CategoriaId | "todas">("todas");
  const [cotidiano, setCotidiano] = useState(false);

  useEffect(() => {
    try {
      if (localStorage.getItem(MODO_KEY) === "1") setCotidiano(true);
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(MODO_KEY, cotidiano ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [cotidiano]);

  const visibles = cat === "todas" ? PROBLEMAS : PROBLEMAS.filter((p) => p.categoria === cat);
  const activa = cat === "todas" ? null : CATEGORIAS.find((c) => c.id === cat);

  return (
    <section aria-label="Mapa de problemas" className="mt-10">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.28em] text-accent/70">
            mapa de problemas
          </p>
          <h2 className="text-xl font-bold tracking-tight text-slate-100">
            {PROBLEMAS.length} problemas, explicados sin jerga
          </h2>
          <p className="mt-1 text-[13px] text-slate-400">
            Toca cualquier tarjeta para ir a su sección completa, con los datos y sus fuentes.
          </p>
        </div>
        <button
          type="button"
          aria-pressed={cotidiano}
          onClick={() => setCotidiano((v) => !v)}
          className={`inline-flex min-h-[44px] items-center gap-2 rounded-lg border px-3 py-2 text-[13px] transition-colors ${
            cotidiano
              ? "border-accent/40 bg-accent/15 text-accent"
              : "glass text-slate-300 hover:bg-white/5"
          }`}
        >
          <span
            aria-hidden
            className={`inline-block h-3.5 w-3.5 rounded-full border ${
              cotidiano ? "border-accent bg-accent" : "border-slate-500"
            }`}
          />
          ¿Cómo me afecta?
        </button>
      </div>

      <div
        role="group"
        aria-label="Filtrar problemas por categoría"
        className="-mx-1 mb-1 flex gap-2 overflow-x-auto whitespace-nowrap px-1 pb-2"
      >
        <button
          type="button"
          aria-pressed={cat === "todas"}
          onClick={() => setCat("todas")}
          className={`min-h-[44px] shrink-0 rounded-lg border px-3 py-2 text-[13px] transition-colors ${
            cat === "todas"
              ? "border-accent/40 bg-accent/15 text-accent"
              : "glass text-slate-300 hover:bg-white/5"
          }`}
        >
          Todos · {PROBLEMAS.length}
        </button>
        {CATEGORIAS.map((c) => {
          const n = PROBLEMAS.filter((p) => p.categoria === c.id).length;
          const on = cat === c.id;
          return (
            <button
              key={c.id}
              type="button"
              aria-pressed={on}
              onClick={() => setCat(on ? "todas" : c.id)}
              className={`min-h-[44px] shrink-0 rounded-lg border px-3 py-2 text-[13px] transition-colors ${
                on ? "border-accent/40 bg-accent/15 text-accent" : "glass text-slate-300 hover:bg-white/5"
              }`}
            >
              {c.label} · {n}
            </button>
          );
        })}
      </div>

      <p aria-live="polite" className="mb-3 text-[12px] text-slate-500">
        {visibles.length} problema{visibles.length === 1 ? "" : "s"}
        {activa ? <> · {activa.descripcion}</> : null}
      </p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {visibles.map((p) => (
          <ProblemCard key={p.id} p={p} cotidiano={cotidiano} />
        ))}
      </div>
    </section>
  );
}
