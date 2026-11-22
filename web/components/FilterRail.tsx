"use client";
import { ALL_TYPES, TYPE_META } from "@/lib/graphStyle";
import type { EntityType } from "@/lib/types";

interface Props {
  active: Set<EntityType>;
  counts: Record<string, number>;
  onToggle: (t: EntityType) => void;
}

export default function FilterRail({ active, counts, onToggle }: Props) {
  return (
    <div className="flex flex-col gap-1">
      <p className="px-2 pb-2 text-xs uppercase tracking-widest text-slate-500">Tipos</p>
      {ALL_TYPES.map((t) => {
        const on = active.has(t);
        const meta = TYPE_META[t];
        return (
          <button
            key={t}
            onClick={() => onToggle(t)}
            className={`flex items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-sm transition ${
              on ? "bg-white/5 text-slate-100" : "text-slate-500 hover:bg-white/5"
            }`}
          >
            <span className="flex items-center gap-2">
              <span
                className="h-3 w-3 rounded-full transition"
                style={{
                  background: on ? meta.color : "transparent",
                  boxShadow: on ? `0 0 8px ${meta.glow}` : "none",
                  border: `1.5px solid ${meta.color}`,
                }}
              />
              {meta.label}
            </span>
            <span className="text-xs tabular-nums text-slate-600">{counts[t] ?? 0}</span>
          </button>
        );
      })}
    </div>
  );
}
