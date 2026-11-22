"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ArrowRight, ArrowLeftRight, Coins, Network } from "lucide-react";
import { fetchMoneyFlows } from "@/lib/api";
import type { MoneyFlow } from "@/lib/types";
import { relLabel } from "@/lib/graphStyle";

const KIND_LABEL: Record<string, string> = {
  judicial: "judicial",
  contrato: "contrato",
  acusacion: "acusación",
  prensa: "prensa",
  ultima_hora: "última hora",
  contexto: "contexto",
  mercantil: "registro mercantil",
  subvencion: "subvención (BDNS)",
  contratacion: "contratación pública (PLACSP/plataformas oficiales)", // defensivo: la capa nunca lleva `amount`
  boe: "cargo público (BOE)", // defensivo: la capa nunca lleva `amount`
};

const MINS: { value: number; label: string }[] = [
  { value: 0, label: "Cualquier importe" },
  { value: 100_000, label: "≥ 100 mil €" },
  { value: 1_000_000, label: "≥ 1 M€" },
  { value: 10_000_000, label: "≥ 10 M€" },
];

function fmt(n: number, currency: string): string {
  return new Intl.NumberFormat("es-ES", { notation: "compact", maximumFractionDigits: 1 }).format(n) + " " + (currency || "EUR");
}

function NodeLink({ id, name }: { id?: string; name: string }) {
  if (!id) return <span className="text-slate-300">{name}</span>;
  return (
    <Link href={`/explore?focus=${encodeURIComponent(id)}`} className="text-slate-200 underline-offset-2 hover:text-accent hover:underline">
      {name}
    </Link>
  );
}

export default function MoneyFlowsView() {
  const [flows, setFlows] = useState<MoneyFlow[]>([]);
  const [min, setMin] = useState(0);
  const [limit, setLimit] = useState(80);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchMoneyFlows({ min: min || undefined, limit })
      .then((r) => setFlows(r.flows))
      .catch(() => setFlows([]))
      .finally(() => setLoading(false));
  }, [min, limit]);

  const maxLog = useMemo(() => {
    const m = Math.max(0, ...flows.map((f) => Math.log10(1 + f.amount)));
    return m || 1;
  }, [flows]);

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <div className="mb-8 flex items-center justify-between">
        <Link href="/" className="inline-flex items-center gap-2 text-sm text-slate-400 hover:text-accent">
          <ArrowLeft className="h-4 w-4" /> Inicio
        </Link>
        <Link href="/explore" className="inline-flex items-center gap-2 rounded-lg glass px-3 py-1.5 text-sm text-slate-200 hover:bg-white/5">
          <Network className="h-4 w-4" /> Abrir el grafo
        </Link>
      </div>

      <header className="mb-7">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
          <Coins className="h-3.5 w-3.5" /> Flujos de dinero
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">El rastro del dinero</h1>
        <p className="mt-4 text-lg leading-relaxed text-slate-400">
          Los pagos, cobros y contratos del entramado que llevan un <span className="text-slate-300">importe
          documentado</span>, ordenados de mayor a menor. Cada flujo conserva su cita y su procedencia.
        </p>
      </header>

      <div className="mb-5 flex flex-wrap items-center gap-2 text-xs">
        {MINS.map((m) => (
          <button
            key={m.value}
            onClick={() => setMin(m.value)}
            aria-pressed={min === m.value}
            className={`rounded-full px-3 py-1.5 transition ${
              min === m.value ? "bg-accent text-ink-950" : "glass text-slate-300 hover:bg-white/5"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="py-4 text-sm text-slate-500">Cargando…</p>
      ) : flows.length === 0 ? (
        <p className="py-4 text-sm text-slate-500">No hay flujos con ese importe.</p>
      ) : (
        <ul className="space-y-2">
          {flows.map((f, i) => {
            const w = Math.max(4, Math.round((Math.log10(1 + f.amount) / maxLog) * 100));
            return (
              <li key={i} className="rounded-xl glass px-4 py-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[13px]">
                    <NodeLink id={f.sourceId} name={f.source} />
                    {f.directed === false ? (
                      <ArrowLeftRight className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-label="dirección no determinada" />
                    ) : (
                      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-emerald-400/70" />
                    )}
                    <NodeLink id={f.targetId} name={f.target} />
                    <span className="ml-1 text-[11px] text-slate-600">· {relLabel(f.type)}</span>
                    {f.label && <span className="text-[11px] text-slate-500">· {f.label}</span>}
                    {f.directed === false && (
                      <span className="rounded-full bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-500">
                        dirección no determinada
                      </span>
                    )}
                  </div>
                  <span className="inline-flex items-center gap-1.5 font-mono text-sm font-medium tabular-nums text-emerald-400">
                    {fmt(f.amount, f.currency)}
                    {f.currency && f.currency !== "EUR" && (
                      <span className="rounded-full bg-amber-500/10 px-1.5 py-0.5 font-sans text-[10px] font-normal text-amber-400">
                        en {f.currency}, sin convertir
                      </span>
                    )}
                  </span>
                </div>
                <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/5">
                  <div className="h-full rounded-full bg-emerald-500/50" style={{ width: `${w}%` }} />
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-slate-500">
                  {f.date && <span className="tabular-nums">{f.date}</span>}
                  <span>· {KIND_LABEL[f.source_kind] ?? f.source_kind}</span>
                  {f.outlet && <span>· {f.outlet}</span>}
                  {f.doc && <span className="truncate font-mono">· {f.doc}{f.page != null ? ` p.${f.page}` : ""}</span>}
                  {f.sources != null && f.sources > 1 && <span>· {f.sources} fuentes</span>}
                </div>
                {f.quote && <p className="mt-1 line-clamp-2 text-[11px] italic leading-snug text-slate-500">"{f.quote}"</p>}
              </li>
            );
          })}
        </ul>
      )}

      {!loading && flows.length > 0 && (
        <div className="mt-5 flex items-center justify-between text-xs text-slate-500">
          <span>
            Mostrando {flows.length} {flows.length === 1 ? "flujo" : "flujos"}
          </span>
          {flows.length >= limit && limit < 200 && (
            <button
              onClick={() => setLimit(200)}
              className="rounded-full glass px-3 py-1.5 text-slate-300 transition hover:bg-white/5"
            >
              Ver todos
            </button>
          )}
        </div>
      )}
    </main>
  );
}
