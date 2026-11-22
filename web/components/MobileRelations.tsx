"use client";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Network } from "lucide-react";
import type { GraphFilters, GraphNode, NodeDetail, SearchHit, SourceKind } from "@/lib/types";
import { fetchNodeDetail } from "@/lib/api";
import { TYPE_META, headlineStatus, statusMeta } from "@/lib/graphStyle";
import SearchBar from "./SearchBar";
import ConnectionsList from "./ConnectionsList";
import { SintesisBody, FuentesBody } from "./entityDetail";

type Tab = "conexiones" | "sintesis" | "fuentes";
const TABS: { id: Tab; label: string }[] = [
  { id: "conexiones", label: "Conexiones" },
  { id: "sintesis", label: "Síntesis" },
  { id: "fuentes", label: "Fuentes" },
];

interface Props {
  selected: GraphNode | null;
  filters: GraphFilters;
  /** loaded graph nodes — source for the "key entities" entry chips. */
  nodes: GraphNode[];
  /** mirror the canvas's source-layer toggles into the structured list (C2). */
  hiddenKinds: Set<SourceKind>;
  onSearchPick: (hit: SearchHit) => void;
  onPick: (id: string) => void;
  /** Open the A↔B pair (selected node + picked entity) and isolate it on the
   *  graph, instead of navigating to the other entity. */
  onOpenPair: (otherId: string, otherName: string) => void;
  onClose: () => void;
  /** Jump to the spatial graph view of the current selection (mobile only). */
  onOpenInGraph?: () => void;
}

/** Mobile-first relations surface: a legible, structured ego view that is the
 *  HERO on phones — entity header + connections grouped by relationship type
 *  (reusing ConnectionsList), with Síntesis/Fuentes a tap away. When nothing is
 *  selected it shows an entry screen (search + key entities). */
export default function MobileRelations({
  selected, filters, nodes, hiddenKinds, onSearchPick, onPick, onOpenPair, onClose, onOpenInGraph,
}: Props) {
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<Tab>("conexiones");

  useEffect(() => {
    setTab("conexiones");
    if (!selected) { setDetail(null); return; }
    setLoading(true);
    fetchNodeDetail(selected.id)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [selected]);

  // Top entities by degree → entry chips (one or two taps from landing to a
  // readable relation view, spec B3/B8).
  const keyEntities = useMemo(
    () => [...nodes].sort((a, b) => (b.degree ?? 0) - (a.degree ?? 0)).slice(0, 16),
    [nodes],
  );

  // ---- entry screen: no selection ----
  if (!selected) {
    return (
      <div className="flex h-full w-full flex-col overflow-auto px-4 py-5">
        <h2 className="text-lg font-semibold tracking-tight text-slate-100">
          Explora las relaciones
        </h2>
        <p className="mt-1 text-sm leading-snug text-slate-400">
          Busca una persona, empresa u organismo y mira con quién se conecta y cómo.
        </p>
        <div className="mt-4">
          <SearchBar onPick={onSearchPick} />
        </div>
        {keyEntities.length > 0 && (
          <div className="mt-6">
            <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">
              Figuras y entidades clave
            </p>
            <div className="flex flex-wrap gap-2">
              {keyEntities.map((n) => {
                const meta = TYPE_META[n.type];
                return (
                  <button
                    key={n.id}
                    onClick={() => onPick(n.id)}
                    className="flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-200 transition active:bg-white/10"
                  >
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ background: meta?.color ?? "#94a3b8" }}
                    />
                    <span className="max-w-[60vw] truncate">{n.name}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
    );
  }

  // ---- selected: entity header + grouped relations / síntesis / fuentes ----
  const meta = TYPE_META[selected.type];
  const procMeta = statusMeta(headlineStatus(detail?.node.chargeStatuses));

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <div className="shrink-0 border-b border-white/5 px-4 pb-3 pt-3">
        <button
          onClick={onClose}
          className="mb-2 flex items-center gap-1.5 text-sm text-slate-400 active:text-accent"
        >
          <ArrowLeft className="h-4 w-4" /> Entidades
        </button>
        <div className="flex items-center gap-2">
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: meta.color, boxShadow: `0 0 6px ${meta.glow}` }}
          />
          <span className="font-mono text-[10px] uppercase tracking-[0.22em]" style={{ color: meta.color }}>
            {meta.label}
          </span>
        </div>
        <h2 className="mt-0.5 text-[19px] font-semibold leading-tight tracking-tight text-slate-50">
          {selected.name}
        </h2>
        {detail?.node.role && (
          <p className="mt-0.5 line-clamp-2 text-sm text-slate-400" title={detail.node.role}>
            {detail.node.role}
          </p>
        )}
        {/* badges — one wrapping row, mirroring NodeDetailPanel */}
        {(procMeta || detail?.node.context || detail?.node.press || detail?.node.isPseudonym || detail?.node.fichaSlug) && (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {procMeta && (
              <span
                title={
                  procMeta.presunto
                    ? "Estado procesal más avanzado en el grafo. Rige la presunción de inocencia: no hay condena."
                    : "Estado procesal más avanzado registrado en el grafo."
                }
                className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider"
                style={{ color: procMeta.color, background: `${procMeta.color}1f`, border: `1px solid ${procMeta.color}55` }}
              >
                {procMeta.label}
                {procMeta.presunto && (
                  <span className="font-normal normal-case tracking-normal text-slate-400">· presunto</span>
                )}
              </span>
            )}
            {detail?.node.context && (
              <span
                title="Entidad de contexto público — no procede de los documentos judiciales"
                className="inline-flex items-center gap-1 rounded-full border border-slate-500/30 bg-slate-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-slate-300"
              >
                contexto público
              </span>
            )}
            {detail?.node.press && !detail?.node.context && (
              <span
                title="Entidad de prensa — no procede de los documentos judiciales"
                className="inline-flex items-center gap-1 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-amber-300"
              >
                prensa
              </span>
            )}
            {detail?.node.isPseudonym && (
              <span
                title={detail.node.pseudonymNote ?? "Nombre ficticio con el que un auto judicial anonimiza a una persona real distinta. No es un hub real ni un homónimo."}
                className="inline-flex items-center gap-1 rounded-full border border-violet-500/40 bg-violet-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-violet-300"
              >
                seudónimo judicial
              </span>
            )}
            {detail?.node.fichaSlug && (
              <a
                href={`/entidad/${detail.node.fichaSlug}`}
                title="Ficha pública de esta entidad — URL estable para leer y compartir"
                className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-accent hover:bg-accent/20"
              >
                ver ficha pública
              </a>
            )}
          </div>
        )}
        <dl className="mt-3 grid grid-cols-3 gap-px overflow-hidden rounded-lg bg-white/[0.04] text-center">
          {[
            ["Conexiones", selected.degree ?? 0],
            ["Menciones", selected.mentions ?? 0],
            ["Fuentes", detail?.sources.length ?? 0],
          ].map(([k, v]) => (
            <div key={k as string} className="bg-ink-900/70 px-2 py-2">
              <dt className="font-mono text-[9px] uppercase tracking-[0.18em] text-slate-600">{k}</dt>
              <dd className="mt-0.5 font-mono text-base tabular-nums text-slate-100">{v as number}</dd>
            </div>
          ))}
        </dl>
        {onOpenInGraph && (
          <button
            onClick={onOpenInGraph}
            className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-lg bg-accent/15 px-3 py-2.5 text-sm text-accent transition active:bg-accent/25"
          >
            <Network className="h-4 w-4" /> Ver en el grafo
          </button>
        )}
      </div>

      {/* tab switch */}
      <div className="flex shrink-0 border-b border-white/5">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex-1 py-2.5 text-sm transition ${
              t.id === tab ? "border-b-2 border-accent text-slate-100" : "text-slate-500 active:text-slate-300"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* body */}
      <div className="min-h-0 flex-1 overflow-auto">
        {tab === "conexiones" && (
          <div className="pt-2">
            <ConnectionsList nodeId={selected.id} filters={filters} onPick={onPick} onOpenPair={onOpenPair} hiddenKinds={hiddenKinds} />
          </div>
        )}
        {tab === "sintesis" && <SintesisBody node={selected} detail={detail} loading={loading} />}
        {tab === "fuentes" && <FuentesBody detail={detail} loading={loading} />}
      </div>
    </div>
  );
}
