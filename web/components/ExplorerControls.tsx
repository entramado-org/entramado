"use client";
import ControlSection, { type SectionId } from "./explorer/ControlSection";
import VistaSection, { type VistaProps } from "./explorer/VistaSection";
import FiltrosSection, { type FiltrosProps } from "./explorer/FiltrosSection";
import ExplorarSection, { type ExplorarProps } from "./explorer/ExplorarSection";
import ClavesSection, { type ClavesProps } from "./explorer/ClavesSection";

interface Props {
  sections: Record<SectionId, boolean>;
  onToggleSection: (id: SectionId) => void;
  vista: VistaProps;
  filtros: FiltrosProps;
  explorar: ExplorarProps;
  claves: ClavesProps;
  onReset: () => void;
}

/** The explorer's control surface, organized into collapsible sections
 *  (Vista / Filtros / Explorar / Claves). Shared between the desktop left rail
 *  and the mobile bottom-sheet so the two never drift apart; the open/closed
 *  state lives in GraphExplorer (persisted) for the same reason. */
export default function ExplorerControls({
  sections, onToggleSection, vista, filtros, explorar, claves, onReset,
}: Props) {
  // Cheap activity cue: how many edge-filter dimensions deviate from default.
  const f = filtros.edgeFilters;
  const activeFilterCount =
    (f.hiddenRels.size > 0 ? 1 : 0) +
    (f.hiddenKinds.size > 0 ? 1 : 0) +
    (f.minStatusRank > 0 ? 1 : 0) +
    (f.emphasis !== "none" ? 1 : 0);

  return (
    <>
      <ControlSection id="vista" title="Vista" open={sections.vista} onToggle={onToggleSection}>
        <VistaSection {...vista} />
      </ControlSection>

      <ControlSection
        id="filtros"
        title="Filtros"
        open={sections.filtros}
        onToggle={onToggleSection}
        badge={
          activeFilterCount > 0 ? (
            <span className="rounded-full bg-accent/20 px-1.5 font-mono text-[9px] normal-case tracking-normal text-accent">
              {activeFilterCount}
            </span>
          ) : undefined
        }
      >
        <FiltrosSection {...filtros} />
      </ControlSection>

      <ControlSection id="explorar" title="Explorar" open={sections.explorar} onToggle={onToggleSection}>
        <ExplorarSection {...explorar} />
      </ControlSection>

      <ControlSection id="claves" title="Claves" open={sections.claves} onToggle={onToggleSection}>
        <ClavesSection {...claves} />
      </ControlSection>

      <div className="mt-3 border-t border-white/5 pt-3">
        <button
          onClick={onReset}
          className="w-full rounded-lg bg-white/5 px-2.5 py-2 text-xs text-slate-400 hover:bg-white/10"
        >
          ↺ Vista general
        </button>
      </div>
    </>
  );
}
