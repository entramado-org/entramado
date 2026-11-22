"use client";
import { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import { searchEntities } from "@/lib/api";
import type { SearchHit } from "@/lib/types";
import { TYPE_META } from "@/lib/graphStyle";

export default function SearchBar({ onPick }: { onPick: (hit: SearchHit) => void }) {
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
        setHits(results);
        setOpen(true);
      } catch {
        /* aborted */
      }
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <div className="relative w-full max-w-md">
      <div className="flex items-center gap-2 rounded-xl glass px-3 py-2">
        <Search className="h-4 w-4 text-accent" />
        <input
          data-search-input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => hits.length && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder="Buscar persona, organización, empresa…"
          className="w-full bg-transparent text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none"
        />
      </div>
      {open && hits.length > 0 && (
        <ul className="absolute z-30 mt-2 max-h-80 w-full overflow-auto rounded-xl glass p-1 shadow-2xl">
          {hits.map((h) => (
            <li key={h.id}>
              <button
                onMouseDown={() => {
                  onPick(h);
                  setOpen(false);
                  setQ(h.name);
                }}
                className="flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left hover:bg-white/5"
              >
                <span className="flex items-center gap-2">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ background: TYPE_META[h.type]?.color }}
                  />
                  <span className="text-sm text-slate-100">{h.name}</span>
                </span>
                <span className="text-xs text-slate-500">{TYPE_META[h.type]?.label ?? h.type}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
