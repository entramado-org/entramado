"use client";
import type { ReactNode } from "react";
import type { Insights } from "@/lib/insights";
import { TYPE_META, statusMeta } from "@/lib/graphStyle";
import { caseLabel } from "@/lib/clustering";
import type { GraphNode } from "@/lib/types";

export interface ClavesProps {
  insights: Insights;
  onPick: (id: string) => void;
  /** Focus a whole sub-trama: colour by community + centre on its principal node. */
  onFocusCommunity: (principalId: string) => void;
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mb-3 last:mb-0">
      <p className="px-2 pb-1 font-mono text-[10px] uppercase tracking-[0.2em] text-accent">{title}</p>
      {children}
    </div>
  );
}

function Empty() {
  return <p className="px-2 py-1 text-[11px] text-slate-600">Sin datos en la vista actual.</p>;
}

function Row({
  node, onPick, right, sub,
}: {
  node: GraphNode;
  onPick: (id: string) => void;
  right?: ReactNode;
  sub?: ReactNode;
}) {
  const meta = TYPE_META[node.type];
  return (
    <button
      onClick={() => onPick(node.id)}
      className="group flex w-full flex-wrap items-center gap-2 rounded-lg px-2 py-1 text-left transition hover:bg-white/5"
    >
      <span
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ background: meta?.color ?? "#94a3b8", boxShadow: `0 0 8px ${meta?.glow ?? "transparent"}33` }}
      />
      <span className="min-w-0 flex-1 truncate text-xs text-slate-200 group-hover:text-accent">{node.name}</span>
      {right}
      {sub && <span className="w-full pl-4 text-[10px] leading-snug text-slate-500">{sub}</span>}
    </button>
  );
}

/** "Claves": computed entry points into the graph — top connectors, money
 *  flows, most-accused entities and inter-case bridges. Every row focuses the
 *  graph on that entity. */
export default function ClavesSection({ insights, onPick, onFocusCommunity }: ClavesProps) {
  const { hubs, money, accused, bridges, communities } = insights;
  return (
    <>
      <Block title="Conectores">
        {hubs.length === 0 ? <Empty /> : hubs.map(({ node, degree }) => (
          <Row
            key={node.id}
            node={node}
            onPick={onPick}
            right={<span className="font-mono text-[10px] tabular-nums text-slate-500">×{degree}</span>}
          />
        ))}
      </Block>

      <Block title="Flujos de dinero">
        {money.length === 0 ? <Empty /> : money.map(({ link, source, target, amount }, i) => (
          <Row
            key={`${source.id}-${target.id}-${i}`}
            node={source}
            onPick={onPick}
            right={
              <span className="font-mono text-[10px] tabular-nums text-emerald-400">
                {amount.toLocaleString("es-ES")} {link.currency ?? "€"}
              </span>
            }
            sub={<>→ {target.name}</>}
          />
        ))}
      </Block>

      <Block title="Más señalados">
        {accused.length === 0 ? <Empty /> : accused.map(({ node, topStatus, count }) => {
          const sm = statusMeta(topStatus);
          return (
            <Row
              key={node.id}
              node={node}
              onPick={onPick}
              right={
                <>
                  {sm && (
                    <span
                      className="rounded-full px-1.5 font-mono text-[9px] uppercase tracking-wider"
                      style={{ color: sm.color, background: `${sm.color}22` }}
                    >
                      {sm.label}
                    </span>
                  )}
                  <span className="font-mono text-[10px] tabular-nums text-slate-500">×{count}</span>
                </>
              }
            />
          );
        })}
        {accused.length > 0 && (
          <p className="px-2 pt-1 text-[10px] leading-snug text-slate-600">
            Recuento de vínculos de acusación en el grafo; rige la presunción de inocencia.
          </p>
        )}
      </Block>

      <Block title="Puentes entre casos">
        {bridges.length === 0 ? <Empty /> : bridges.map(({ node, cases }) => (
          <Row
            key={node.id}
            node={node}
            onPick={onPick}
            sub={cases.map((c) => caseLabel(c)).join(" ↔ ")}
          />
        ))}
      </Block>

      <Block title="Comunidades (sub-tramas)">
        {communities.length === 0 ? <Empty /> : communities.map((c) => (
          <button
            key={c.community}
            onClick={() => onFocusCommunity(c.principalId)}
            className="group flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left transition hover:bg-white/5"
          >
            <span className="min-w-0 flex-1 truncate text-xs text-slate-200 group-hover:text-accent">{c.label}</span>
            <span className="font-mono text-[10px] tabular-nums text-slate-500">{c.size}</span>
          </button>
        ))}
        {communities.length > 0 && (
          <p className="px-2 pt-1 text-[10px] leading-snug text-slate-600">
            Sub-redes por co-aparición en el grafo (estructural, no imputación). Pulsa para colorear y centrar.
          </p>
        )}
      </Block>
    </>
  );
}
