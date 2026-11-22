import type { TimelineEvent } from "@/lib/types";
import { relLabel } from "@/lib/graphStyle";

/* Cronología citada de un caso (spec 038) — server component, lista vertical.
 * NO reutiliza TimelineStrip (canvas del explorer): aquí manda la legibilidad
 * y la cita [doc, p.N] por hito. */

function fmtDate(d: string): string {
  // "2024-02-20" → "20-02-2024"; "2024" queda tal cual.
  const m = d.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : d;
}

export default function CaseTimeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) return null;
  return (
    <ol className="relative border-l border-white/10 pl-5">
      {events.map((ev, i) => (
        <li key={`${ev.date}-${ev.source.id}-${ev.target.id}-${ev.type}-${i}`} className="mb-5">
          <span className="absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full bg-accent/70" />
          <p className="text-xs font-medium uppercase tracking-widest text-accent">{fmtDate(ev.date)}</p>
          <p className="mt-0.5 text-[14px] leading-snug text-slate-200">
            <span className="text-slate-300">{ev.source.name}</span>{" "}
            <span className="text-accent">{relLabel(ev.type).toLowerCase()}</span>{" "}
            <span className="text-slate-300">{ev.target.name}</span>
            {ev.amount != null && (
              <span className="ml-2 font-mono text-[12px] tabular-nums text-emerald-300">
                {ev.amount.toLocaleString("es-ES")} {ev.currency ?? "€"}
              </span>
            )}
          </p>
          {ev.quote && (
            <p className="mt-1 border-l-2 border-white/10 pl-2.5 text-[12px] italic leading-relaxed text-slate-500">
              «{ev.quote.length > 200 ? ev.quote.slice(0, 200).trimEnd() + "…" : ev.quote}»
            </p>
          )}
          {ev.doc && (
            <p className="mt-0.5 font-mono text-[11px] text-slate-600">
              [{ev.doc}
              {ev.page != null ? `, p.${ev.page}` : ""}]
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}
