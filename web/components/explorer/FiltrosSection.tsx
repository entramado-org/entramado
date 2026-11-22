"use client";
import type { EdgeFilters, EmphasisMode, EntityType, GraphFilters, SourceKind } from "@/lib/types";
import { SOURCE_KIND_META } from "@/lib/graphStyle";
import FilterRail from "../FilterRail";
import ChronoControls from "../ChronoControls";
import RelationLegend from "../RelationLegend";

export interface FiltrosProps {
  active: Set<EntityType>;
  counts: Record<string, number>;
  onToggle: (t: EntityType) => void;
  filters: GraphFilters;
  onFiltersChange: (f: GraphFilters) => void;
  presentRels: Set<string>;
  edgeFilters: EdgeFilters;
  onEdgeFiltersChange: (f: EdgeFilters) => void;
  hideOrphans: boolean;
  onHideOrphansChange: (v: boolean) => void;
}

const STATUS_STEPS: [number, string][] = [
  [0, "Todos"],
  [2, "Investigado+"],
  [4, "Acusado+"],
  [6, "Condenado"],
];

const KIND_ORDER = Object.keys(SOURCE_KIND_META) as SourceKind[];

/** "Filtros": entity types, year range, source layers, procedural status,
 *  money emphasis and the (clickable) relation legend. All edge filters are
 *  render-level — they hide/emphasize edges without relayout. */
export default function FiltrosSection({
  active, counts, onToggle, filters, onFiltersChange, presentRels,
  edgeFilters, onEdgeFiltersChange, hideOrphans, onHideOrphansChange,
}: FiltrosProps) {
  function toggleKind(k: SourceKind) {
    const next = new Set(edgeFilters.hiddenKinds);
    next.has(k) ? next.delete(k) : next.add(k);
    onEdgeFiltersChange({ ...edgeFilters, hiddenKinds: next });
  }
  function toggleRel(t: string) {
    const next = new Set(edgeFilters.hiddenRels);
    next.has(t) ? next.delete(t) : next.add(t);
    onEdgeFiltersChange({ ...edgeFilters, hiddenRels: next });
  }

  return (
    <>
      <FilterRail active={active} counts={counts} onToggle={onToggle} />

      <div className="mt-4 border-t border-white/5 pt-3">
        <button
          onClick={() => onHideOrphansChange(!hideOrphans)}
          title="Oculta los nodos que no tienen ningún vínculo visible con los filtros activos (como el interruptor «Orphans» de Obsidian)."
          className="flex w-full items-center justify-between gap-2 rounded-lg bg-white/5 px-2.5 py-1.5 text-left text-xs transition hover:bg-white/10"
          aria-pressed={hideOrphans}
        >
          <span className="text-slate-300">Nodos sueltos</span>
          <span
            className={`rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${
              hideOrphans ? "bg-white/5 text-slate-500" : "bg-accent/20 text-accent"
            }`}
          >
            {hideOrphans ? "ocultos" : "visibles"}
          </span>
        </button>
      </div>

      <div className="mt-4 border-t border-white/5 pt-3">
        <ChronoControls filters={filters} onChange={onFiltersChange} />
      </div>

      <div className="mt-4 border-t border-white/5 pt-3">
        <p className="px-2 pb-2 text-[11px] text-slate-600">Capas de fuentes</p>
        <div className="flex flex-col gap-1">
          {KIND_ORDER.map((k) => {
            const meta = SOURCE_KIND_META[k];
            const visible = !edgeFilters.hiddenKinds.has(k);
            return (
              <button
                key={k}
                onClick={() => toggleKind(k)}
                title={meta.desc}
                className="flex w-full items-center justify-between gap-2 rounded-lg bg-white/5 px-2.5 py-1.5 text-left text-xs transition hover:bg-white/10"
                aria-pressed={visible}
              >
                <span className="flex items-center gap-2">
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-full"
                    style={{ background: visible ? meta.color : "#475569" }}
                  />
                  <span className={visible ? "text-slate-300" : "text-slate-500"}>{meta.label}</span>
                </span>
                <span
                  className={`rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${
                    visible ? "text-slate-300" : "bg-white/5 text-slate-500"
                  }`}
                  style={visible ? { background: meta.color + "26", color: meta.color } : undefined}
                >
                  {visible ? "visible" : "oculta"}
                </span>
              </button>
            );
          })}
        </div>
        <p className="px-2 pt-1.5 text-[11px] leading-snug text-slate-600">
          Cada vínculo del grafo procede de una capa con su grado de respaldo. Las capas no
          judiciales van siempre atribuidas; rige la presunción de inocencia.
        </p>
      </div>

      <div className="mt-4 border-t border-white/5 pt-3">
        <p className="px-2 pb-2 text-[11px] text-slate-600">Estado procesal</p>
        <div className="flex flex-wrap gap-1 px-1">
          {STATUS_STEPS.map(([rank, lbl]) => (
            <button
              key={rank}
              onClick={() => onEdgeFiltersChange({ ...edgeFilters, minStatusRank: rank })}
              className={`flex-1 whitespace-nowrap rounded-lg px-2 py-1.5 text-[11px] font-medium transition ${
                edgeFilters.minStatusRank === rank
                  ? "bg-accent/20 text-accent"
                  : "bg-white/5 text-slate-400 hover:bg-white/10"
              }`}
            >
              {lbl}
            </button>
          ))}
        </div>
        <p className="px-2 pt-1.5 text-[11px] leading-snug text-slate-600">
          Filtra solo los vínculos de acusación por su fase (investigado → condenado); el resto de
          relaciones no se ve afectado.
        </p>
      </div>

      <div className="mt-4 border-t border-white/5 pt-3">
        <p className="px-2 pb-1.5 text-[11px] text-slate-600">Énfasis</p>
        {/* Single active emphasis (mutually exclusive) — spec 013. money scales
            €-edges; bridges highlights the top betweenness nodes (connectors). */}
        <div className="flex gap-1 px-1">
          {([["none", "Ninguno"], ["money", "Dinero"], ["bridges", "Puentes"]] as [EmphasisMode, string][]).map(
            ([val, lbl]) => (
              <button
                key={val}
                onClick={() => onEdgeFiltersChange({ ...edgeFilters, emphasis: val })}
                aria-pressed={edgeFilters.emphasis === val}
                className={`flex-1 rounded-lg px-2 py-2 text-xs font-medium transition ${
                  edgeFilters.emphasis === val ? "bg-accent/20 text-accent" : "bg-white/5 text-slate-400 hover:bg-white/10"
                }`}
              >
                {lbl}
              </button>
            ),
          )}
        </div>
        <p className="px-2 pt-1.5 text-[11px] leading-snug text-slate-600">
          {edgeFilters.emphasis === "money"
            ? "Pinta en verde los vínculos con importe documentado — grosor e intensidad escalan con la cantidad, y los mayores flujos muestran su cuantía en €. El resto del grafo se atenúa sin desaparecer."
            : edgeFilters.emphasis === "bridges"
            ? "Resalta los nodos-PUENTE (mayor intermediación): los que conectan sub-tramas distintas. Destacan los principales con su nombre; el resto se atenúa. Ser puente es una posición en la red, NO un indicio de culpabilidad."
            : "Sin énfasis: el grafo se muestra con su aspecto normal."}
        </p>
      </div>

      <div className="mt-4 border-t border-white/5 pt-3">
        <RelationLegend
          present={presentRels}
          hiddenRels={edgeFilters.hiddenRels}
          onToggleRel={toggleRel}
          onShowAllRels={() => onEdgeFiltersChange({ ...edgeFilters, hiddenRels: new Set() })}
        />
      </div>
    </>
  );
}
