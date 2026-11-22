"use client";
import { REL_LEGEND } from "@/lib/graphStyle";

interface Props {
  /** Relation types present in the current view; when empty, all are shown. */
  present?: Set<string>;
  /** When provided, rows become toggles: types in this set are hidden on the canvas. */
  hiddenRels?: Set<string>;
  onToggleRel?: (type: string) => void;
  /** Clear every hidden relation type (shown as a "todas" chip when any are hidden). */
  onShowAllRels?: () => void;
}

export default function RelationLegend({ present, hiddenRels, onToggleRel, onShowAllRels }: Props) {
  const rows = present && present.size > 0
    ? REL_LEGEND.filter((r) => present.has(r.type))
    : REL_LEGEND;
  if (rows.length === 0) return null;

  const interactive = !!onToggleRel;
  const anyHidden = !!hiddenRels && hiddenRels.size > 0;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between px-2 pb-2">
        <p className="text-xs uppercase tracking-widest text-slate-500">Relaciones</p>
        {interactive && anyHidden && (
          <button
            onClick={onShowAllRels}
            className="rounded-full bg-white/5 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-400 transition hover:bg-white/10 hover:text-accent"
          >
            todas
          </button>
        )}
      </div>
      {interactive && (
        <p className="-mt-1 px-2 pb-1 text-[11px] leading-snug text-slate-600">
          Pulsa un tipo para ocultarlo o mostrarlo en el grafo.
        </p>
      )}
      {rows.map((r) => {
        const hidden = hiddenRels?.has(r.type) ?? false;
        const inner = (
          <>
            <span
              className="h-0.5 w-4 shrink-0 rounded-full"
              style={{
                background: hidden ? "#475569" : r.color,
                boxShadow: hidden ? "none" : `0 0 6px ${r.color}`,
              }}
            />
            <span className={`truncate ${hidden ? "text-slate-600" : ""}`}>{r.label}</span>
          </>
        );
        if (!interactive) {
          return (
            <div key={r.type} className="flex items-center gap-2 rounded-lg px-2.5 py-1 text-sm text-slate-300">
              {inner}
            </div>
          );
        }
        return (
          <button
            key={r.type}
            onClick={() => onToggleRel!(r.type)}
            aria-pressed={!hidden}
            className={`flex items-center gap-2 rounded-lg px-2.5 py-1 text-left text-sm transition hover:bg-white/5 ${
              hidden ? "opacity-50" : ""
            } text-slate-300`}
          >
            {inner}
          </button>
        );
      })}
    </div>
  );
}
