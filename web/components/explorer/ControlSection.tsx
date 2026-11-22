"use client";
import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";

export type SectionId = "vista" | "filtros" | "explorar" | "claves";

interface Props {
  id: SectionId;
  title: string;
  open: boolean;
  onToggle: (id: SectionId) => void;
  children: ReactNode;
  /** Optional tiny annotation shown next to the title (e.g. active-filter count). */
  badge?: ReactNode;
}

/** Collapsible section of the explorer controls rail. Controlled — the
 *  open/closed map lives in GraphExplorer (persisted) because the rail and the
 *  mobile bottom-sheet can both be mounted and must agree. */
export default function ControlSection({ id, title, open, onToggle, children, badge }: Props) {
  return (
    <div className="border-t border-white/5 pt-1 first:border-t-0 first:pt-0">
      <button
        onClick={() => onToggle(id)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-2 text-left transition hover:bg-white/5"
      >
        <span className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500">
          {title}
          {badge}
        </span>
        <ChevronDown
          className={`h-3.5 w-3.5 shrink-0 text-slate-600 transition-transform ${open ? "" : "-rotate-90"}`}
        />
      </button>
      {open && <div className="pb-2">{children}</div>}
    </div>
  );
}
