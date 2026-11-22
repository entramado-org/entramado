"use client";
import { useState } from "react";
import { Scale, X } from "lucide-react";
import { EVIDENCE_LEGEND } from "@/lib/graphStyle";

/** A small, dismissible legend that explains the epistemic tiers of the graph
 *  — the difference between "documentado" and "condenado". Sits bottom-left next
 *  to the shortcuts chip. Self-contained: no props, no layout impact. */
export default function EvidenceLegend() {
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="pointer-events-auto absolute bottom-4 left-32 z-20 hidden items-center gap-1.5 rounded-full glass px-2.5 py-1 text-[11px] text-slate-400 transition hover:text-accent sm:flex"
        title="Qué significan los niveles de prueba"
      >
        <Scale className="h-3 w-3" /> Niveles de prueba
      </button>
    );
  }

  return (
    <div className="pointer-events-auto absolute bottom-4 left-4 z-30 w-[320px] max-w-[calc(100vw-2rem)] rounded-2xl glass p-4 shadow-2xl animate-fade-in">
      <div className="mb-2 flex items-start justify-between">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-accent">
            ◇ niveles de prueba
          </p>
          <h3 className="mt-0.5 text-sm font-semibold text-slate-100">
            No todo pesa lo mismo
          </h3>
        </div>
        <button
          onClick={() => setOpen(false)}
          className="rounded p-1 text-slate-500 hover:bg-white/5 hover:text-slate-200"
          aria-label="Cerrar"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <ul className="space-y-2">
        {EVIDENCE_LEGEND.map((row) => (
          <li key={row.label} className="flex gap-2.5">
            <span
              aria-hidden
              className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ background: row.color, boxShadow: `0 0 6px ${row.color}99` }}
            />
            <div>
              <p className="text-[12px] font-medium leading-tight text-slate-200">{row.label}</p>
              <p className="text-[11px] leading-snug text-slate-500">{row.desc}</p>
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-3 border-t border-white/5 pt-2 text-[11px] leading-snug text-amber-300/80">
        Imputación ≠ condena. Toda relación enlaza a su documento de origen.
      </p>
    </div>
  );
}
