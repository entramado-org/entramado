"use client";
import { useEffect, useState } from "react";
import { Clock, X } from "lucide-react";
import { fetchTimeline } from "@/lib/api";
import type { GraphFilters, TimelineEvent } from "@/lib/types";
import { TYPE_META } from "@/lib/graphStyle";

interface Props {
  scopeEntity: string | null;       // entity_id to scope the timeline to, or null for global
  filters: GraphFilters;
  onPick: (id: string) => void;     // click an event endpoint -> select that node
  onClose: () => void;
}

function fmtDate(d: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(d)) {
    return new Date(d).toLocaleDateString("es-ES", { day: "2-digit", month: "short", year: "numeric" });
  }
  return d;
}

export default function TimelineStrip({ scopeEntity, filters, onPick, onClose }: Props) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetchTimeline(scopeEntity, filters, 400)
      .then((d) => setEvents(d.events))
      .catch(() => setEvents([]))
      .finally(() => setLoading(false));
  }, [scopeEntity, filters.fromYear, filters.toYear]); // eslint-disable-line react-hooks/exhaustive-deps

  // group by year
  const byYear = new Map<number, TimelineEvent[]>();
  for (const e of events) {
    const arr = byYear.get(e.year) || [];
    arr.push(e);
    byYear.set(e.year, arr);
  }
  const years = [...byYear.keys()].sort();

  return (
    <div className="flex h-56 shrink-0 flex-col border-t border-white/5 glass">
      <div className="flex items-center justify-between border-b border-white/5 px-4 py-2">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
          <Clock className="h-4 w-4 text-accent" />
          Línea de tiempo
          {scopeEntity && (
            <span className="text-xs text-slate-500">
              · centrada en <span className="text-accent">{scopeEntity.split(":")[1]?.replace(/-/g, " ")}</span>
            </span>
          )}
          <span className="text-xs text-slate-600">· {events.length} hechos fechados</span>
        </div>
        <button onClick={onClose} aria-label="Cerrar cronología" className="rounded p-1 text-slate-500 hover:bg-white/5 hover:text-slate-200">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-x-auto overflow-y-hidden">
        {loading && (
          <div className="flex h-full items-center justify-center text-sm text-slate-500">cargando…</div>
        )}
        {!loading && events.length === 0 && (
          <div className="flex h-full items-center justify-center text-sm text-slate-500">
            No hay hechos fechados en este rango.
          </div>
        )}
        {!loading && events.length > 0 && (
          <div className="flex h-full min-w-max gap-4 px-4 py-3">
            {years.map((y) => (
              <div key={y} className="flex h-full w-64 shrink-0 flex-col">
                <div className="mb-1.5 text-xs font-semibold uppercase tracking-widest text-accent">{y}</div>
                <div className="flex flex-col gap-1.5 overflow-y-auto pr-1 text-xs">
                  {byYear.get(y)!.slice(0, 30).map((e, i) => {
                    const srcColor = TYPE_META[e.source.type]?.color ?? "#94a3b8";
                    const tgtColor = TYPE_META[e.target.type]?.color ?? "#94a3b8";
                    return (
                      <div
                        key={i}
                        className="rounded-lg border border-white/5 bg-ink-800/60 p-2 transition hover:border-accent/40"
                      >
                        <div className="mb-1 flex items-center justify-between gap-2 text-[10px] text-slate-500">
                          <span>{fmtDate(e.date)}</span>
                          <span className="rounded bg-white/5 px-1 py-0.5 text-[9px] uppercase tracking-wider text-slate-400">
                            {e.type}
                          </span>
                        </div>
                        <div className="flex items-center gap-1 leading-tight">
                          <button
                            onClick={() => onPick(e.source.id)}
                            className="truncate text-left text-slate-200 hover:text-accent"
                            style={{ color: srcColor }}
                            title={e.source.name}
                          >
                            {e.source.name}
                          </button>
                          <span className="text-slate-600">→</span>
                          <button
                            onClick={() => onPick(e.target.id)}
                            className="truncate text-left text-slate-200 hover:text-accent"
                            style={{ color: tgtColor }}
                            title={e.target.name}
                          >
                            {e.target.name}
                          </button>
                        </div>
                        {e.quote && (
                          <p className="mt-1 line-clamp-2 text-[11px] leading-snug text-slate-500">
                            "{e.quote}"
                          </p>
                        )}
                      </div>
                    );
                  })}
                  {byYear.get(y)!.length > 30 && (
                    <p className="px-1 text-[10px] text-slate-600">+{byYear.get(y)!.length - 30} más</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
