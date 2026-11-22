"use client";
import { Loader2, Route } from "lucide-react";
import type { PathResult } from "@/lib/types";
import EntityPicker, { type PickedEntity } from "./EntityPicker";
import PathResults from "./PathResults";

export interface ExplorarProps {
  hopDepth: 1 | 2;
  onHopDepthChange: (d: 1 | 2) => void;
  pathA: PickedEntity | null;
  pathB: PickedEntity | null;
  onPathAChange: (v: PickedEntity | null) => void;
  onPathBChange: (v: PickedEntity | null) => void;
  /** Currently-selected graph node, offered as a one-tap pick. Null when none. */
  selectedEntity: PickedEntity | null;
  onRunPath: () => void;
  pathLoading: boolean;
  pathNotFound: boolean;
  pathResult: PathResult | null;
  activePathIdx: number | null;
  onSelectPath: (idx: number | null) => void;
  onClearPath: () => void;
  onPickNode: (id: string) => void;
  maxLen: number;
}

/** "Explorar": expansion depth (relations of relations) + path between two entities. */
export default function ExplorarSection({
  hopDepth, onHopDepthChange, pathA, pathB, onPathAChange, onPathBChange,
  selectedEntity, onRunPath, pathLoading, pathNotFound, pathResult,
  activePathIdx, onSelectPath, onClearPath, onPickNode, maxLen,
}: ExplorarProps) {
  return (
    <>
      <p className="px-2 pb-2 text-[11px] text-slate-600">Profundidad de expansión</p>
      <div className="flex gap-1 px-1">
        {([[1, "1 salto"], [2, "2 saltos"]] as [1 | 2, string][]).map(([val, lbl]) => (
          <button
            key={val}
            onClick={() => onHopDepthChange(val)}
            className={`flex-1 rounded-lg px-2 py-2 text-xs font-medium transition ${
              hopDepth === val ? "bg-accent/20 text-accent" : "bg-white/5 text-slate-400 hover:bg-white/10"
            }`}
          >
            {lbl}
          </button>
        ))}
      </div>
      <p className="px-2 pt-1.5 text-[11px] leading-snug text-slate-600">
        Con 2 saltos, al seleccionar una entidad se cargan también las relaciones de sus
        relaciones (más tenues en el lienzo).
      </p>

      <div className="mt-4 border-t border-white/5 pt-3">
        <p className="px-2 pb-2 text-[11px] text-slate-600">Ruta entre entidades</p>
        <div className="space-y-1.5 px-1">
          <EntityPicker
            label="Entidad A"
            value={pathA}
            onChange={onPathAChange}
            onUseSelected={selectedEntity && !pathA ? () => onPathAChange(selectedEntity) : null}
          />
          <EntityPicker
            label="Entidad B"
            value={pathB}
            onChange={onPathBChange}
            onUseSelected={selectedEntity && !pathB ? () => onPathBChange(selectedEntity) : null}
          />
          <button
            onClick={onRunPath}
            disabled={!pathA || !pathB || pathA.id === pathB.id || pathLoading}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent/15 px-2.5 py-2 text-xs font-medium text-accent transition hover:bg-accent/25 disabled:cursor-not-allowed disabled:bg-white/5 disabled:text-slate-600"
          >
            {pathLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Route className="h-3.5 w-3.5" />}
            ¿Cómo se conectan?
          </button>
        </div>

        {pathNotFound && (
          <p className="px-2 pt-2 text-[11px] leading-snug text-slate-500">
            Sin conexión en ≤ {maxLen} saltos entre estas entidades (excluyendo co-menciones
            documentales).
          </p>
        )}

        {pathResult && pathResult.paths.length > 0 && (
          <PathResults
            result={pathResult}
            activeIdx={activePathIdx}
            onSelectPath={onSelectPath}
            onPickNode={onPickNode}
            onClear={onClearPath}
          />
        )}

        {!pathResult && !pathNotFound && (
          <p className="px-2 pt-1.5 text-[11px] leading-snug text-slate-600">
            Elige dos entidades y traza las cadenas de evidencia más cortas que las conectan,
            resaltadas sobre el grafo.
          </p>
        )}
      </div>
    </>
  );
}
