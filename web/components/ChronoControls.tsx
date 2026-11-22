"use client";
import { CalendarRange, EyeOff } from "lucide-react";
import type { GraphFilters } from "@/lib/types";

interface Props {
  filters: GraphFilters;
  onChange: (f: GraphFilters) => void;
}

const MIN_YEAR = 2010;
const MAX_YEAR = 2026;

export default function ChronoControls({ filters, onChange }: Props) {
  const from = filters.fromYear ?? MIN_YEAR;
  const to = filters.toYear ?? MAX_YEAR;
  const active = filters.fromYear != null || filters.toYear != null;
  const hideNoise = (filters.minDegree ?? 1) > 1;

  function setFrom(v: number) {
    onChange({ ...filters, fromYear: Math.min(v, to) });
  }
  function setTo(v: number) {
    onChange({ ...filters, toYear: Math.max(v, from) });
  }
  function reset() {
    onChange({ ...filters, fromYear: null, toYear: null });
  }

  return (
    <div className="space-y-3">
      <div>
        <p className="mb-2 flex items-center gap-2 px-2 text-xs uppercase tracking-widest text-slate-500">
          <CalendarRange className="h-3 w-3" /> Cronología
        </p>
        <div className="rounded-lg border border-white/5 bg-ink-800/40 p-2.5">
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="text-slate-300 tabular-nums">{from}</span>
            <span className="text-slate-600">↔</span>
            <span className="text-slate-300 tabular-nums">{to}</span>
          </div>
          <div className="relative h-1.5 rounded-full bg-ink-700">
            <div
              className="absolute h-full rounded-full bg-accent/60"
              style={{
                left: `${((from - MIN_YEAR) / (MAX_YEAR - MIN_YEAR)) * 100}%`,
                right: `${((MAX_YEAR - to) / (MAX_YEAR - MIN_YEAR)) * 100}%`,
              }}
            />
          </div>
          <div className="mt-2 flex items-center gap-1.5">
            <input
              type="range"
              min={MIN_YEAR}
              max={MAX_YEAR}
              value={from}
              onChange={(e) => setFrom(Number(e.target.value))}
              className="w-full accent-accent"
            />
            <input
              type="range"
              min={MIN_YEAR}
              max={MAX_YEAR}
              value={to}
              onChange={(e) => setTo(Number(e.target.value))}
              className="w-full accent-accent"
            />
          </div>
          {active && (
            <button
              onClick={reset}
              className="mt-2 w-full rounded-md bg-white/5 px-2 py-1 text-[11px] text-slate-400 hover:bg-white/10"
            >
              Todos los años
            </button>
          )}
        </div>
      </div>

      <button
        onClick={() => onChange({ ...filters, minDegree: hideNoise ? 1 : 2 })}
        className={`flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-sm transition ${
          hideNoise ? "bg-accent/15 text-accent" : "text-slate-400 hover:bg-white/5"
        }`}
        title="Oculta entidades con una sola conexión"
      >
        <span className="flex items-center gap-2">
          <EyeOff className="h-3.5 w-3.5" /> Ocultar ruido
        </span>
        <span className="text-[11px]">{hideNoise ? "on" : "off"}</span>
      </button>
    </div>
  );
}
