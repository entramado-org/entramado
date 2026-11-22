"use client";
import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Network, X } from "lucide-react";
import { fetchNodeDetail } from "@/lib/api";
import type { GraphFilters, GraphNode, NodeDetail } from "@/lib/types";
import { TYPE_META, headlineStatus, statusMeta } from "@/lib/graphStyle";
import ConnectionsList from "./ConnectionsList";
import ClaimsInDispute from "./ClaimsInDispute";
import { SintesisBody, FuentesBody } from "./entityDetail";
import { SheetHandle, SNAP_HEIGHT, useSheetDrag, type SheetSnap } from "./MobileSheet";

type Tab = "sintesis" | "conexiones" | "fuentes";

const TABS: { id: Tab; label: string; n: string }[] = [
  { id: "sintesis", label: "Síntesis", n: "01" },
  { id: "conexiones", label: "Conexiones", n: "02" },
  { id: "fuentes", label: "Fuentes", n: "03" },
];

interface Props {
  node: GraphNode | null;
  filters: GraphFilters;
  onClose: () => void;
  onExpand: (id: string) => void;
  /** False once this node's neighbourhood is loaded to the depth cap — the
   *  "Expandir red" button then shows a disabled "Red completa" state. */
  canExpand: boolean;
  onPick: (id: string) => void;
  /** Open the A↔B pair (this node + the picked entity) and isolate it on the
   *  canvas, instead of navigating to the other entity. */
  onOpenPair: (otherId: string, otherName: string) => void;
  /** Mobile bottom-sheet snap height (lifted to GraphExplorer so the canvas can
   *  nudge the focused node above it). Ignored by the desktop side rail. */
  snap: SheetSnap;
  onSnapChange: (s: SheetSnap) => void;
}

export default function NodeDetailPanel({ node, filters, onClose, onExpand, canExpand, onPick, onOpenPair, snap, onSnapChange }: Props) {
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<Tab>("sintesis");
  // Desktop-only: collapse the side rail to a slim strip. Mobile height is driven
  // by `snap` (peek/half/full) instead — see MobileSheet. On phones the grab
  // handle changes height; the X (onClose) drops the selection entirely.
  const [collapsed, setCollapsed] = useState(false);
  const peek = snap === "peek";
  const { dragPx, handlers } = useSheetDrag(snap, onSnapChange);

  useEffect(() => {
    setTab("sintesis");
    setCollapsed(false); // a freshly selected node always opens expanded
    if (!node) {
      setDetail(null);
      return;
    }
    setLoading(true);
    fetchNodeDetail(node.id)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [node]);

  if (!node) return null;
  const meta = TYPE_META[node.type];
  const tabIdx = TABS.findIndex((t) => t.id === tab);

  // Most-advanced procedural stage of this entity's charges (accusations layer),
  // headlined so the reader immediately sees "imputado / acusado / condenado".
  const procStatus = headlineStatus(detail?.node.chargeStatuses);
  const procMeta = statusMeta(procStatus);

  return (
    <aside
      style={dragPx != null ? { height: dragPx } : undefined}
      className={`fixed inset-x-0 bottom-0 z-40 flex w-full flex-col rounded-t-2xl border-t border-white/10 glass animate-fade-in pb-safe
        ${dragPx == null ? SNAP_HEIGHT[snap] : ""} ${collapsed ? "md:w-12" : "md:w-[380px]"}
        md:static md:z-auto md:h-full md:shrink-0 md:rounded-none md:border-t-0 md:border-l md:border-white/5 md:pb-0`}
    >
      {/* mobile grab handle — tap cycles / drag resizes (md:hidden) */}
      <SheetHandle handlers={handlers} />
      {/* desktop-only collapsed strip: restore button + vertical name */}
      <div className={`${collapsed ? "hidden md:flex" : "hidden"} flex-col items-center gap-3 p-2`}>
        <button
          onClick={() => setCollapsed(false)}
          className="rounded p-1 text-slate-500 hover:bg-white/5 hover:text-slate-200"
          aria-label="Expandir panel"
          title="Expandir"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="max-h-[55vh] overflow-hidden truncate text-xs text-slate-400 [writing-mode:vertical-rl]">
          {node.name}
        </span>
      </div>

      {/* full panel — hidden on desktop when collapsed (restored via the strip) */}
      <div className={collapsed ? "contents md:hidden" : "contents"}>
      {/* header */}
      <div className="relative border-b border-white/5 p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="mb-1.5 flex items-center gap-2">
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ background: meta.color, boxShadow: `0 0 6px ${meta.glow}` }}
              />
              <span
                className="font-mono text-[10px] uppercase tracking-[0.22em]"
                style={{ color: meta.color }}
              >
                {meta.label}
              </span>
            </div>
            <h2 className="text-[20px] font-semibold leading-tight tracking-tight text-slate-50">
              {node.name}
            </h2>
            {detail?.node.role && (
              <p className="mt-0.5 line-clamp-2 text-sm text-slate-400" title={detail.node.role}>
                {detail.node.role}
              </p>
            )}
            {/* badges — one wrapping row so they stay compact instead of stacking */}
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
                    style={{
                      color: procMeta.color,
                      background: `${procMeta.color}1f`,
                      border: `1px solid ${procMeta.color}55`,
                    }}
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
          </div>
          <div className="flex shrink-0 items-center gap-0.5">
            {/* minimize — desktop side rail (collapse to a slim strip); on mobile
                the grab handle changes height instead */}
            <button
              onClick={() => setCollapsed(true)}
              className="hidden rounded p-1 text-slate-500 hover:bg-white/5 hover:text-slate-200 md:inline-flex"
              aria-label="Minimizar panel"
              title="Minimizar"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
            <button
              onClick={onClose}
              className="flex h-11 w-11 items-center justify-center rounded-lg text-slate-500 hover:bg-white/5 hover:text-slate-200 md:h-auto md:w-auto md:rounded md:p-1"
              aria-label="Cerrar"
            >
              <X className="h-5 w-5 md:h-4 md:w-4" />
            </button>
          </div>
        </div>

        {/* stat strip */}
        <dl className={`mt-3 grid grid-cols-3 gap-px overflow-hidden rounded-lg bg-white/[0.04] text-center ${peek ? "hidden md:grid" : ""}`}>
          {[
            ["Conexiones", node.degree ?? 0],
            ["Menciones", node.mentions ?? 0],
            ["Fuentes", detail?.sources.length ?? 0],
          ].map(([k, v]) => (
            <div key={k as string} className="bg-ink-900/70 px-2 py-2">
              <dt className="font-mono text-[9px] uppercase tracking-[0.18em] text-slate-600">
                {k}
              </dt>
              <dd className="mt-0.5 font-mono text-base tabular-nums text-slate-100">{v as number}</dd>
            </div>
          ))}
        </dl>

        {/* actions */}
        <div className={`mt-3 flex gap-2 ${peek ? "hidden md:flex" : ""}`}>
          <button
            onClick={() => onExpand(node.id)}
            disabled={!canExpand}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition ${
              canExpand
                ? "bg-accent/15 text-accent hover:bg-accent/25"
                : "cursor-default bg-white/5 text-slate-500"
            }`}
          >
            <Network className="h-4 w-4" /> {canExpand ? "Expandir red" : "Red completa"}
          </button>
        </div>
      </div>

      {/* tabs */}
      <div className={`relative flex border-b border-white/5 ${peek ? "hidden md:flex" : ""}`}>
        {TABS.map((t, i) => {
          const active = t.id === tab;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex flex-1 items-baseline justify-center gap-1.5 py-2.5 text-sm transition ${
                active ? "text-slate-100" : "text-slate-500 hover:text-slate-300"
              }`}
            >
              <span className="font-mono text-[9px] tabular-nums text-slate-600">{t.n}</span>
              <span>{t.label}</span>
            </button>
          );
        })}
        <span
          aria-hidden
          className="absolute bottom-0 h-[2px] bg-accent transition-[left,width] duration-300 ease-out"
          style={{
            left: `${(tabIdx * 100) / TABS.length}%`,
            width: `${100 / TABS.length}%`,
            boxShadow: "0 0 8px rgba(56,225,196,0.6)",
          }}
        />
      </div>

      {/* tab content */}
      <div className={`flex-1 overflow-auto ${peek ? "hidden md:block" : ""}`}>
        {tab === "sintesis" && (
          <>
            <SintesisBody node={node} detail={detail} loading={loading} />
            <ClaimsInDispute nodeId={node.id} />
          </>
        )}

        {tab === "conexiones" && (
          <div className="pt-2">
            <ConnectionsList nodeId={node.id} filters={filters} onPick={onPick} onOpenPair={onOpenPair} />
          </div>
        )}

        {tab === "fuentes" && <FuentesBody detail={detail} loading={loading} />}
      </div>
      </div>
    </aside>
  );
}
