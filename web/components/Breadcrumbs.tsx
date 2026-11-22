"use client";
import { ChevronRight, Home } from "lucide-react";
import type { GraphNode } from "@/lib/types";
import { TYPE_META } from "@/lib/graphStyle";

interface Props {
  trail: GraphNode[];          // oldest -> newest
  currentId: string | null;
  onPick: (id: string) => void;
  onReset: () => void;
}

export default function Breadcrumbs({ trail, currentId, onPick, onReset }: Props) {
  if (trail.length === 0) return null;
  const showHome = trail.length > 0;
  return (
    <nav
      aria-label="Rastro de selecciones"
      className="pointer-events-auto absolute left-4 right-4 top-4 z-20 flex justify-center"
    >
      <ol className="flex max-w-[88%] items-center gap-1 overflow-x-auto rounded-full glass px-2 py-1 text-xs">
        {showHome && (
          <li>
            <button
              onClick={onReset}
              title="Volver a vista general"
              className="flex h-6 w-6 items-center justify-center rounded-full text-slate-500 hover:bg-white/5 hover:text-accent"
            >
              <Home className="h-3 w-3" />
            </button>
          </li>
        )}
        {trail.map((n, i) => {
          const active = n.id === currentId;
          const meta = TYPE_META[n.type];
          return (
            <li key={n.id + i} className="flex items-center gap-1">
              <ChevronRight className="h-3 w-3 text-slate-700" />
              <button
                onClick={() => onPick(n.id)}
                className={`flex items-center gap-1.5 rounded-full px-2 py-0.5 transition ${
                  active
                    ? "bg-accent/15 text-accent"
                    : "text-slate-400 hover:bg-white/5 hover:text-slate-100"
                }`}
              >
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  style={{
                    background: meta?.color ?? "#94a3b8",
                    boxShadow: active ? `0 0 6px ${meta?.glow}` : "none",
                  }}
                />
                <span className="max-w-[180px] truncate">{n.name}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
