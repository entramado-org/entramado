"use client";
import type { GraphLink, GraphNode, PairAgg } from "@/lib/types";
import { relColor, relLabel, statusMeta } from "@/lib/graphStyle";

interface Props {
  link: GraphLink | null;
  /** When the hovered line is a collapsed per-pair line, its aggregated list. */
  pair?: PairAgg | null;
  x: number;
  y: number;
  /** Canvas bounds — used to flip the chip so it stays on-screen on a phone. */
  boundsW?: number;
  boundsH?: number;
}

function fmtAmount(l: GraphLink): string | null {
  if (l.amount == null) return null;
  const c = l.currency || "EUR";
  return `${Number(l.amount).toLocaleString("es-ES")} ${c}`;
}

function compactAmount(n?: number | null): string | null {
  if (!n) return null;
  return new Intl.NumberFormat("es-ES", { notation: "compact", maximumFractionDigits: 1 }).format(n) + " €";
}

function endpointName(v: string | GraphNode): string {
  return typeof v === "object" ? v.name : v;
}

export default function EdgeTooltip({ link, pair, x, y, boundsW, boundsH }: Props) {
  if (!link) return null;
  // Flip the chip toward the centre near an edge so it never spills off a phone
  // screen (~280px wide). Grows up by default; drops below when near the top.
  const flipX = boundsW != null && x > boundsW * 0.62;
  const flipY = boundsH != null && y < 140;
  const pos = {
    left: flipX ? x - 12 : x + 12,
    top: flipY ? y + 12 : y - 6,
    transform: `translate(${flipX ? "-100%" : "0"}, ${flipY ? "0" : "-100%"})`,
  };

  // Collapsed pair: list every relation between the two nodes.
  if (pair) {
    const a = endpointName(link.source);
    const b = endpointName(link.target);
    const shown = pair.types.slice(0, 6);
    return (
      <div className="pointer-events-none absolute z-30" style={pos}>
        <div className="min-w-[200px] max-w-[300px] rounded-xl glass px-3 py-2 shadow-[0_8px_24px_-12px_rgba(0,0,0,0.6)] animate-fade-in">
          <div className="flex items-baseline justify-between gap-3">
            <span className="truncate text-[12px] font-medium text-slate-100">
              {a} · {b}
            </span>
            <span className="shrink-0 font-mono text-[10px] text-slate-500">
              {pair.types.length} {pair.types.length === 1 ? "vínculo" : "vínculos"}
            </span>
          </div>
          <ul className="mt-1.5 space-y-1">
            {shown.map((t, i) => (
              <li key={i} className="flex items-center gap-1.5 text-[11px] text-slate-300">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: relColor(t.type) }} />
                <span className="truncate">{relLabel(t.type, t.status)}</span>
                {t.count > 1 && <span className="text-slate-500">×{t.count}</span>}
                {compactAmount(t.amount) && (
                  <span className="ml-auto font-mono text-emerald-400">{compactAmount(t.amount)}</span>
                )}
              </li>
            ))}
            {pair.types.length > shown.length && (
              <li className="text-[10px] text-slate-500">+{pair.types.length - shown.length} más</li>
            )}
          </ul>
          <p className="mt-1.5 text-[9px] text-slate-600">Clic para ver la evidencia</p>
        </div>
      </div>
    );
  }

  const amount = fmtAmount(link);
  const isPress = link.source_kind === "prensa";
  const isUltima = link.source_kind === "ultima_hora";
  const sm = statusMeta(link.status);
  return (
    <div className="pointer-events-none absolute z-30" style={pos}>
      <div className="min-w-[200px] max-w-[280px] rounded-xl glass px-3 py-2 shadow-[0_8px_24px_-12px_rgba(0,0,0,0.6)] animate-fade-in">
        <div className="flex items-center justify-between gap-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-accent">
            {link.type}
          </span>
          {isUltima ? (
            <span className="rounded-full bg-orange-500/15 px-1.5 py-0.5 font-mono text-[10px] text-orange-300">
              última hora
            </span>
          ) : isPress ? (
            <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 font-mono text-[10px] text-amber-300">
              prensa
            </span>
          ) : link.context ? (
            <span className="rounded-full bg-slate-500/15 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
              contexto
            </span>
          ) : (
            <span className="rounded-full bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
              ×{link.count ?? 1}
            </span>
          )}
        </div>
        {isUltima ? (
          <div className="mt-1.5 text-[11px] leading-snug text-slate-400">
            <p>{link.note || "Desarrollo reciente"}</p>
            <p className="mt-1 text-orange-300/90">
              {link.outlet || "Prensa"}
              {link.date ? ` · ${link.date}` : ""} · de momento solo en prensa
            </p>
            {link.status && (
              <p className="mt-1 text-orange-200/80">{statusMeta(link.status)?.label ?? link.status} · presunto</p>
            )}
            {link.quote && (
              <p className="mt-1 line-clamp-2 italic text-slate-500">"{link.quote}"</p>
            )}
          </div>
        ) : isPress ? (
          <div className="mt-1.5 text-[11px] leading-snug text-slate-400">
            <p>{link.note || "Dato de prensa"}</p>
            <p className="mt-1 text-amber-300/90">
              {link.outlet || "Prensa"}
              {link.date_year ? ` · ${link.date}` : ""} · sin fuente judicial
            </p>
            {link.quote && (
              <p className="mt-1 line-clamp-2 italic text-slate-500">"{link.quote}"</p>
            )}
          </div>
        ) : link.context ? (
          <p className="mt-1.5 text-[11px] leading-snug text-slate-400">
            {link.note || "Contexto público"} · sin fuente judicial
          </p>
        ) : (
          <>
            {(link.date || link.date_year || amount || link.status) && (
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                {link.date_year && <span className="tabular-nums">{link.date}</span>}
                {amount && <span className="text-emerald-400">{amount}</span>}
                {link.status && (
                  <span
                    className="rounded px-1.5 py-0.5"
                    style={
                      sm
                        ? { color: sm.color, background: `${sm.color}22` }
                        : { color: "#fca5a5", background: "rgba(244,63,94,0.15)" }
                    }
                  >
                    {sm ? sm.label : link.status}
                    {sm?.presunto ? " · presunto" : ""}
                  </span>
                )}
              </div>
            )}
            {link.quote && (
              <p className="mt-1.5 line-clamp-2 text-[11px] italic leading-snug text-slate-500">
                "{link.quote}"
              </p>
            )}
            {link.note && (
              <p className="mt-1 line-clamp-3 text-[11px] leading-snug text-slate-400">
                <span className="font-medium text-slate-300">Nota: </span>
                {link.note}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
