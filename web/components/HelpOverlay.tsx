"use client";
import { useEffect } from "react";
import { X } from "lucide-react";
import { Kbd } from "./Kbd";

const SHORTCUTS: { keys: React.ReactNode; label: string }[] = [
  { keys: <Kbd>/</Kbd>, label: "Foco al buscador" },
  { keys: <Kbd>Esc</Kbd>, label: "Cerrar / salir de selección" },
  { keys: <><Kbd>g</Kbd> <Kbd>g</Kbd></>, label: "Volver a vista general" },
  { keys: <Kbd>?</Kbd>, label: "Mostrar esta ayuda" },
  { keys: <><Kbd>←</Kbd> <Kbd>→</Kbd></>, label: "Navegar el rastro de selecciones" },
  { keys: <Kbd>T</Kbd>, label: "Mostrar / ocultar cronología" },
];

export default function HelpOverlay({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/70 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative w-full max-w-lg rounded-2xl glass p-6 shadow-2xl"
      >
        <button
          onClick={onClose}
          aria-label="Cerrar ayuda"
          className="absolute right-3 top-3 rounded p-1 text-slate-500 hover:bg-white/5 hover:text-slate-200"
        >
          <X className="h-4 w-4" />
        </button>
        <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-accent">
          ◇ atajos de teclado
        </p>
        <h2 className="mb-5 mt-1 text-xl font-semibold text-slate-100">Cómo navegar más rápido</h2>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-[auto,1fr]">
          {SHORTCUTS.map((s, i) => (
            <div key={i} className="contents">
              <dt className="flex items-center gap-1.5 whitespace-nowrap">{s.keys}</dt>
              <dd className="text-sm text-slate-300">{s.label}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}
