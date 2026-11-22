"use client";
import type { ColorMode, LayoutMode } from "@/lib/types";

export interface VistaProps {
  mode: LayoutMode;
  onModeChange: (m: LayoutMode) => void;
  colorBy: ColorMode;
  onColorByChange: (c: ColorMode) => void;
  sizeScale: number;
  onSizeChange: (n: number) => void;
}

/** "Vista": layout regime + node colour scheme + node size. */
export default function VistaSection({ mode, onModeChange, colorBy, onColorByChange, sizeScale, onSizeChange }: VistaProps) {
  return (
    <>
      <p className="px-2 pb-2 text-[11px] text-slate-600">Disposición</p>
      {/* Two views: "Libre" (pure force-directed, Obsidian-style; the default)
          and "Ordenada" (the anchored radial layout formerly called Personas).
          The old Casos/Cronología modes were dropped from the UI (specs 005/008).
          Mode isn't persisted, so nothing else can select them. */}
      <div className="flex flex-wrap gap-1 px-1">
        {([["free", "Libre"], ["radial", "Ordenada"]] as [LayoutMode, string][]).map(
          ([val, lbl]) => (
            <button
              key={val}
              onClick={() => onModeChange(val)}
              className={`flex-1 rounded-lg px-2 py-2 text-xs font-medium transition ${
                mode === val ? "bg-accent/20 text-accent" : "bg-white/5 text-slate-400 hover:bg-white/10"
              }`}
            >
              {lbl}
            </button>
          ),
        )}
      </div>
      <p className="px-2 pt-1.5 text-[11px] leading-snug text-slate-600">
        {mode === "free"
          ? "La red completa (~1.000 entidades, incluida la periferia) sin anclas: los nodos se acomodan solos por atracción y repulsión, como el grafo de Obsidian. Los grupos emergen de las propias conexiones."
          : "Las personas en el centro, agrupadas por caso; empresas, organismos, delitos y dinero en el anillo exterior. Enfoca a alguien para ver con qué se conecta."}
      </p>

      <p className="px-2 pb-1 pt-3 text-[11px] text-slate-600">Colorear por</p>
      {/* Orthogonal to layout: recolours either view. 'type' = current behaviour;
          'community' tints each sub-trama (Louvain communities, spec 013). */}
      <div className="flex gap-1 px-1">
        {([["type", "Tipo"], ["community", "Comunidad"]] as [ColorMode, string][]).map(([val, lbl]) => (
          <button
            key={val}
            onClick={() => onColorByChange(val)}
            className={`flex-1 rounded-lg px-2 py-2 text-xs font-medium transition ${
              colorBy === val ? "bg-accent/20 text-accent" : "bg-white/5 text-slate-400 hover:bg-white/10"
            }`}
          >
            {lbl}
          </button>
        ))}
      </div>
      <p className="px-2 pt-1.5 text-[11px] leading-snug text-slate-600">
        {colorBy === "community"
          ? "Cada color es una sub-trama detectada por co-aparición en el grafo (comunidades de Louvain). Es agrupación estructural, NO una imputación."
          : "Color por tipo de entidad (personas, empresas, organismos…)."}
      </p>

      <p className="px-2 pb-1 pt-3 text-[11px] text-slate-600">Tamaño de nodos</p>
      <div className="flex gap-1 px-1">
        {([["S", 0.7], ["M", 1], ["L", 1.5], ["XL", 2.2]] as [string, number][]).map(([lbl, val]) => (
          <button
            key={lbl}
            onClick={() => onSizeChange(val)}
            className={`flex-1 rounded-lg px-2 py-2 text-xs font-medium transition ${
              sizeScale === val ? "bg-accent/20 text-accent" : "bg-white/5 text-slate-400 hover:bg-white/10"
            }`}
          >
            {lbl}
          </button>
        ))}
      </div>
    </>
  );
}
