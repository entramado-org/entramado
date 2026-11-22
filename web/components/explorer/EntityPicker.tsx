"use client";
import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { searchEntities } from "@/lib/api";
import type { SearchHit } from "@/lib/types";
import { TYPE_META } from "@/lib/graphStyle";

export interface PickedEntity {
  id: string;
  name: string;
}

interface Props {
  label: string;
  value: PickedEntity | null;
  onChange: (v: PickedEntity | null) => void;
  /** When set, render a "usar selección" chip that picks the currently-selected node. */
  onUseSelected?: (() => void) | null;
}

/** Compact debounced entity search for the path tool (same 250ms debounce +
 *  abort pattern as SearchBar, sized for the rail). */
export default function EntityPicker({ label, value, onChange, onUseSelected }: Props) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (q.trim().length < 2) {
      setHits([]);
      return;
    }
    const t = setTimeout(async () => {
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      try {
        const { results } = await searchEntities(q.trim(), ac.signal);
        setHits(results.slice(0, 6));
        setOpen(true);
      } catch {
        /* aborted */
      }
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  if (value) {
    return (
      <div className="flex items-center justify-between gap-2 rounded-lg bg-white/5 px-2.5 py-1.5">
        <span className="min-w-0 flex-1">
          <span className="block font-mono text-[9px] uppercase tracking-wider text-slate-600">{label}</span>
          <span className="block truncate text-xs text-slate-200">{value.name}</span>
        </span>
        <button
          onClick={() => onChange(null)}
          className="rounded p-1 text-slate-500 hover:bg-white/5 hover:text-slate-200"
          aria-label={`Quitar ${label}`}
        >
          <X className="h-3 w-3" />
        </button>
      </div>
    );
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-1.5">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => hits.length && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder={label}
          className="w-full min-w-0 rounded-lg bg-white/5 px-2.5 py-1.5 text-xs text-slate-100 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-accent/50"
        />
        {onUseSelected && (
          <button
            onClick={onUseSelected}
            className="shrink-0 whitespace-nowrap rounded-lg bg-white/5 px-2 py-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-400 transition hover:bg-white/10 hover:text-accent"
            title="Usar la entidad seleccionada en el grafo"
          >
            usar sel.
          </button>
        )}
      </div>
      {open && hits.length > 0 && (
        <ul className="absolute z-30 mt-1 max-h-56 w-full overflow-auto rounded-xl glass p-1 shadow-2xl">
          {hits.map((h) => (
            <li key={h.id}>
              <button
                onMouseDown={() => {
                  onChange({ id: h.id, name: h.name });
                  setOpen(false);
                  setQ("");
                }}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-white/5"
              >
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: TYPE_META[h.type]?.color }} />
                <span className="truncate text-xs text-slate-100">{h.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
