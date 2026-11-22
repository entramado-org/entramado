import { ExternalLink, FileCheck2 } from "lucide-react";
import { NOMBRAMIENTOS_BOE } from "@/lib/boeNombramientos";

/* Bloque "Evidencia BOE" (spec 039): la disposición oficial de cada
 * nombramiento, con la procedencia verificable del nombrado. El hecho duro es
 * el decreto; la lectura, del lector. Server component sin estado. */

export default function BoeEvidence({ sectionId }: { sectionId: string }) {
  const rows = NOMBRAMIENTOS_BOE[sectionId];
  if (!rows || rows.length === 0) return null;
  return (
    <div className="mt-5 rounded-xl border border-white/8 bg-ink-900/40">
      <p className="flex items-center gap-2 border-b border-white/5 px-4 py-2.5 font-mono text-[10px] uppercase tracking-[0.22em] text-accent/70">
        <FileCheck2 className="h-3.5 w-3.5" /> evidencia · nombramientos en el BOE
      </p>
      <ul className="divide-y divide-white/5">
        {rows.map((n) => (
          <li key={`${n.cargo}-${n.persona}`} className="px-4 py-3">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5">
              <p className="text-[13.5px] leading-snug text-slate-200">
                <span className="font-medium">{n.persona}</span>
                <span className="text-slate-400"> — {n.cargo}</span>
              </p>
              {n.fecha && <span className="font-mono text-[11px] tabular-nums text-slate-600">{n.fecha}</span>}
            </div>
            {n.procedencia && (
              <p className="mt-0.5 text-[12px] leading-snug text-slate-500">
                Procedencia: {n.procedencia}
              </p>
            )}
            <p className="mt-1 flex flex-wrap items-center gap-x-3 text-[11px]">
              <a
                href={n.fuente.href}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 font-mono text-accent/80 hover:text-accent hover:underline"
              >
                <ExternalLink className="h-3 w-3" /> {n.fuente.ref}
              </a>
              {n.nota && <span className="text-slate-600">{n.nota}</span>}
            </p>
          </li>
        ))}
      </ul>
      <p className="border-t border-white/5 px-4 py-2.5 text-[11px] leading-snug text-slate-600">
        Hechos de nombramiento publicados en fuentes oficiales. La procedencia es el cargo previo
        verificable de cada nombrado; un nombramiento legal con afiliación es un dato, no una
        acusación.
      </p>
    </div>
  );
}
