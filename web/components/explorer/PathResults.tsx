"use client";
import { useMemo } from "react";
import { ArrowLeft, ArrowRight, X } from "lucide-react";
import type { GraphLink, GraphNode, PathResult } from "@/lib/types";
import { SOURCE_KIND_META, relLabel, statusMeta } from "@/lib/graphStyle";

interface Props {
  result: PathResult;
  activeIdx: number | null; // null = all paths highlighted
  onSelectPath: (idx: number | null) => void;
  onPickNode: (id: string) => void;
  onClear: () => void;
}

function endId(e: string | GraphNode): string {
  return typeof e === "string" ? e : e.id;
}

/** Hop-by-hop evidence list for the paths returned by /graph/path. */
export default function PathResults({ result, activeIdx, onSelectPath, onPickNode, onClear }: Props) {
  const byId = useMemo(
    () => new Map(result.nodes.map((n) => [n.id, n])),
    [result.nodes],
  );

  // links indexed by undirected pair so each hop can list every parallel relation.
  const byPair = useMemo(() => {
    const m = new Map<string, GraphLink[]>();
    for (const l of result.links) {
      const s = endId(l.source);
      const t = endId(l.target);
      const k = s < t ? `${s}|${t}` : `${t}|${s}`;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(l);
    }
    return m;
  }, [result.links]);

  return (
    <div className="mt-2 space-y-2">
      <div className="flex items-center justify-between px-1">
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
          {result.paths.length === 1 ? "1 ruta encontrada" : `${result.paths.length} rutas encontradas`}
        </span>
        <button
          onClick={onClear}
          className="flex items-center gap-1 rounded-full bg-white/5 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-400 transition hover:bg-white/10 hover:text-accent"
        >
          <X className="h-3 w-3" /> Limpiar ruta
        </button>
      </div>

      {result.paths.map((seq, idx) => {
        const isActive = activeIdx === null || activeIdx === idx;
        const names = seq.map((id) => byId.get(id)?.name ?? id);
        return (
          <div
            key={idx}
            className={`rounded-lg border px-2 py-1.5 transition ${
              isActive ? "border-accent/30 bg-white/[0.04]" : "border-white/5 opacity-60"
            }`}
          >
            <button
              onClick={() => onSelectPath(activeIdx === idx ? null : idx)}
              className="w-full text-left"
              title={activeIdx === idx ? "Mostrar todas las rutas" : "Resaltar solo esta ruta"}
            >
              <span className="block text-[11px] leading-snug text-slate-300">
                {names.map((n, i) => (
                  <span key={i}>
                    {i > 0 && <span className="text-slate-600"> → </span>}
                    <span className={i === 0 || i === names.length - 1 ? "text-accent" : ""}>{n}</span>
                  </span>
                ))}
              </span>
              <span className="mt-0.5 block font-mono text-[9px] uppercase tracking-wider text-slate-600">
                {seq.length - 1} {seq.length - 1 === 1 ? "salto" : "saltos"}
              </span>
            </button>

            {(activeIdx === idx || result.paths.length === 1) && (
              <ul className="mt-1.5 space-y-1.5 border-t border-white/5 pt-1.5">
                {seq.slice(0, -1).map((a, i) => {
                  const b = seq[i + 1];
                  const k = a < b ? `${a}|${b}` : `${b}|${a}`;
                  const hops = byPair.get(k) ?? [];
                  return (
                    <li key={i} className="text-[11px] leading-snug">
                      <button
                        onClick={() => onPickNode(b)}
                        className="text-slate-400 transition hover:text-accent"
                      >
                        {byId.get(a)?.name ?? a} <span className="text-slate-600">↔</span>{" "}
                        {byId.get(b)?.name ?? b}
                      </button>
                      {hops.map((l, j) => {
                        const kind = (l.source_kind ?? "judicial") as keyof typeof SOURCE_KIND_META;
                        const km = SOURCE_KIND_META[kind];
                        const sm = statusMeta(l.status);
                        const forward = endId(l.source) === a;
                        const Arrow = forward ? ArrowRight : ArrowLeft;
                        return (
                          <div key={j} className="mt-0.5 flex flex-wrap items-center gap-1.5 pl-2">
                            <Arrow className="h-3 w-3 shrink-0 text-slate-600" aria-hidden />
                            <span className="text-slate-300">{relLabel(l.type, l.status)}</span>
                            {sm && (
                              <span
                                className="rounded-full px-1.5 font-mono text-[9px] uppercase tracking-wider"
                                style={{ color: sm.color, background: `${sm.color}22` }}
                              >
                                {sm.label}
                              </span>
                            )}
                            {km && (
                              <span
                                className="rounded-full px-1.5 font-mono text-[9px] uppercase tracking-wider"
                                style={{ color: km.color, background: `${km.color}22` }}
                              >
                                {l.outlet ?? km.label}
                              </span>
                            )}
                            {l.doc && (
                              <span className="font-mono text-[9px] text-slate-600">
                                {l.doc}
                                {l.page != null ? ` · p. ${l.page}` : ""}
                              </span>
                            )}
                            {(l.quote ?? l.note) && (
                              <span className="line-clamp-2 w-full pl-4 italic text-slate-500">
                                «{(l.quote ?? l.note ?? "").slice(0, 160)}»
                              </span>
                            )}
                          </div>
                        );
                      })}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        );
      })}

      <p className="px-1 text-[10px] leading-snug text-slate-600">
        Cadenas más cortas según los vínculos del grafo; cada eslabón cita su fuente. Rige la
        presunción de inocencia.
      </p>
    </div>
  );
}
