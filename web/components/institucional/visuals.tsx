/* ────────────────────────────────────────────────────────────────────────────
 * Componentes visuales ligeros para la página de contexto institucional.
 * Sin dependencias de charting: SVG/CSS puro + Tailwind, en la estética del sitio
 * (accent #38e1c4, tonos ink/slate, .glass/.text-glow). Server components.
 *
 * Regla editorial: el dato manda. Cada cifra que se pinta aquí debe poder
 * trazarse a una fuente oficial citada en la sección que la contiene.
 * ──────────────────────────────────────────────────────────────────────────── */

import React from "react";

/* ── Número grande con glow, unidad y etiqueta. El titular de cada bloque. ── */
export function BigStat({
  value,
  unit,
  label,
  sub,
  tone = "accent",
}: {
  value: string;
  unit?: string;
  label: string;
  sub?: string;
  tone?: "accent" | "amber" | "rose";
}) {
  const toneCls =
    tone === "amber"
      ? "text-amber-400"
      : tone === "rose"
        ? "text-rose-400"
        : "text-accent text-glow";
  return (
    <div className="rounded-xl border border-white/5 bg-ink-900/40 p-4">
      <div className="flex items-baseline gap-1">
        <span className={`text-3xl font-bold tracking-tight tabular-nums ${toneCls}`}>
          {value}
        </span>
        {unit && <span className="text-sm font-medium text-slate-400">{unit}</span>}
      </div>
      <p className="mt-1.5 text-[13px] font-medium leading-snug text-slate-300">{label}</p>
      {sub && <p className="mt-0.5 text-[11px] leading-snug text-slate-500">{sub}</p>}
    </div>
  );
}

/* ── Rejilla de BigStat para abrir cada sección económica. ── */
export function StatGrid({ children }: { children: React.ReactNode }) {
  return (
    <div className="my-5 grid grid-cols-2 gap-3 sm:grid-cols-3">{children}</div>
  );
}

/* ── Barras horizontales comparativas. Una serie de {label, value}, escala común.
 *    `highlight` marca la barra que queremos destacar (España, p. ej.). ── */
export function BarCompare({
  title,
  unit,
  data,
  source,
}: {
  title?: string;
  unit?: string;
  data: { label: string; value: number; display?: string; highlight?: boolean }[];
  source?: string;
}) {
  const max = Math.max(...data.map((d) => d.value));
  return (
    <figure className="my-5 rounded-xl border border-white/5 bg-ink-900/40 p-4">
      {title && (
        <figcaption className="mb-3 text-[13px] font-medium text-slate-300">
          {title}
          {unit && <span className="ml-1 text-[11px] text-slate-500">({unit})</span>}
        </figcaption>
      )}
      <div className="space-y-2.5">
        {data.map((d) => {
          const pct = max > 0 ? Math.max(2, (d.value / max) * 100) : 0;
          return (
            <div key={d.label} className="grid grid-cols-[7.5rem_1fr] items-center gap-3">
              <span className="truncate text-right text-[12px] text-slate-400">{d.label}</span>
              <div className="flex items-center gap-2">
                <div className="h-5 flex-1 overflow-hidden rounded bg-ink-800/80">
                  <div
                    className={`h-full rounded ${
                      d.highlight ? "bg-accent" : "bg-slate-600"
                    }`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span
                  className={`w-16 shrink-0 text-right text-[12px] tabular-nums ${
                    d.highlight ? "font-semibold text-accent" : "text-slate-400"
                  }`}
                >
                  {d.display ?? d.value}
                </span>
              </div>
            </div>
          );
        })}
      </div>
      {source && (
        <p className="mt-3 text-[10px] leading-snug text-slate-600">{source}</p>
      )}
    </figure>
  );
}

/* ── Sparkline / franja de tendencia. points = valores; se autoescala. ── */
export function TrendStrip({
  title,
  points,
  labelStart,
  labelEnd,
  caption,
  source,
}: {
  title?: string;
  points: number[];
  labelStart?: string;
  labelEnd?: string;
  caption?: string;
  source?: string;
}) {
  const w = 600;
  const h = 120;
  const pad = 8;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const coords = points.map((p, i) => {
    const x = pad + (i / (points.length - 1)) * (w - 2 * pad);
    const y = h - pad - ((p - min) / span) * (h - 2 * pad);
    return [x, y] as const;
  });
  const line = coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area =
    `${pad},${h - pad} ` + line + ` ${w - pad},${h - pad}`;
  return (
    <figure className="my-5 rounded-xl border border-white/5 bg-ink-900/40 p-4">
      {title && (
        <figcaption className="mb-2 text-[13px] font-medium text-slate-300">{title}</figcaption>
      )}
      <svg
        viewBox={`0 0 ${w} ${h}`}
        className="h-24 w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label={title ?? "tendencia"}
      >
        <defs>
          <linearGradient id="trendfill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#38e1c4" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#38e1c4" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={area} fill="url(#trendfill)" />
        <polyline
          points={line}
          fill="none"
          stroke="#38e1c4"
          strokeWidth="2.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {coords.length > 0 && (
          <circle cx={coords[coords.length - 1][0]} cy={coords[coords.length - 1][1]} r="4" fill="#5ef2d8" />
        )}
      </svg>
      {(labelStart || labelEnd) && (
        <div className="mt-1 flex justify-between text-[11px] text-slate-500">
          <span>{labelStart}</span>
          <span>{labelEnd}</span>
        </div>
      )}
      {caption && <p className="mt-2 text-[12px] leading-snug text-slate-400">{caption}</p>}
      {source && <p className="mt-2 text-[10px] leading-snug text-slate-600">{source}</p>}
    </figure>
  );
}

/* ── Caja de "idea fuerza" / interpretación. Marca explícitamente que es lectura
 *    crítica, no dato neutro — coherente con el encuadre declarado de la página. ── */
export function TakeBox({
  children,
  label = "la lectura",
}: {
  children: React.ReactNode;
  label?: string;
}) {
  return (
    <div className="my-4 rounded-xl border-l-2 border-accent/50 bg-accent/[0.04] p-4">
      <p className="mb-1.5 font-mono text-[9px] uppercase tracking-[0.22em] text-accent/70">
        {label}
      </p>
      <div className="space-y-2 text-[14px] leading-relaxed text-slate-300">{children}</div>
    </div>
  );
}
