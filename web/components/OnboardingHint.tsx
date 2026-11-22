"use client";
import { useEffect, useState } from "react";
import { X, MousePointer2, Hand, ZoomIn, Search } from "lucide-react";
import { Kbd } from "./Kbd";

const KEY = "sm:onboard.v1";

const HINTS = [
  { icon: MousePointer2, body: "Click un nodo para enfocar su red." },
  { icon: Hand, body: "Arrastra el canvas para mover." },
  { icon: ZoomIn, body: "Rueda del ratón para hacer zoom." },
  { icon: Search, body: <>Pulsa <Kbd>/</Kbd> para buscar una entidad.</> },
];

export default function OnboardingHint() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!localStorage.getItem(KEY)) setVisible(true);
  }, []);
  if (!visible) return null;
  function dismiss() {
    localStorage.setItem(KEY, "1");
    setVisible(false);
  }
  return (
    <div className="pointer-events-none absolute inset-x-0 top-3 z-30 hidden justify-center px-4 animate-fade-in md:flex">
      <div className="pointer-events-auto relative max-w-md rounded-2xl glass px-5 py-4 shadow-[0_8px_40px_-20px_rgba(56,225,196,0.4)]">
        <button
          onClick={dismiss}
          className="absolute right-3 top-3 rounded p-0.5 text-slate-500 hover:bg-white/5 hover:text-slate-200"
          aria-label="Cerrar"
        >
          <X className="h-3.5 w-3.5" />
        </button>
        <p className="mb-3 font-mono text-[10px] uppercase tracking-[0.22em] text-accent">
          ◇ cómo navegar
        </p>
        <ul className="space-y-1.5">
          {HINTS.map((h, i) => {
            const Icon = h.icon;
            return (
              <li key={i} className="flex items-center gap-3 text-[13px] text-slate-300">
                <Icon className="h-3.5 w-3.5 shrink-0 text-slate-500" />
                <span>{h.body}</span>
              </li>
            );
          })}
        </ul>
        <p className="mt-3 border-t border-white/5 pt-2 text-[11px] text-slate-500">
          Pulsa <Kbd>?</Kbd> en cualquier momento para ver atajos.
        </p>
      </div>
    </div>
  );
}
