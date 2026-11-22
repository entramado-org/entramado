"use client";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, BookOpen, ChevronLeft, ChevronRight, ExternalLink, X } from "lucide-react";
import { fetchEdgeDetail, fetchRelationPair } from "@/lib/api";
import { CAPAS } from "@/lib/ontologia.generated";
import type { EdgeEvidence, GraphFilters, RelationGroup, SelectedEdge } from "@/lib/types";
import { anchorLabel, relColor, relLabel, SOURCE_KIND_META, statusMeta } from "@/lib/graphStyle";
import { SheetHandle, SNAP_HEIGHT, useSheetDrag, type SheetSnap } from "./MobileSheet";

interface Props {
  edge: SelectedEdge;
  filters: GraphFilters;
  onClose: () => void;
  /** Select one of the relation's endpoints as a node (closes this panel). */
  onPickNode: (id: string) => void;
  /** Open the source document of a citation at its page, with the quote highlighted. */
  onOpenDocument?: (docId: string, page?: number | null, quote?: string | null) => void;
  /** Mobile bottom-sheet snap height (lifted to GraphExplorer). Ignored on desktop. */
  snap: SheetSnap;
  onSnapChange: (s: SheetSnap) => void;
}

/** Fixed tier order: strongest provenance first, última hora isolated near the
 *  end, contexto last. Unknown kinds (future layers) append after these. */
const KIND_ORDER: readonly string[] = CAPAS;

function fmtAmount(amount: number, currency?: string | null): string {
  return `${Number(amount).toLocaleString("es-ES")} ${currency || "EUR"}`;
}

function StatusBadge({ status }: { status: string }) {
  const sm = statusMeta(status);
  return (
    <span
      title={sm?.presunto ? "Presunto — rige la presunción de inocencia" : undefined}
      className="rounded px-1.5 py-0.5"
      style={
        sm
          ? { color: sm.color, background: `${sm.color}22` }
          : { color: "#fca5a5", background: "rgba(244,63,94,0.15)" }
      }
    >
      {sm ? sm.label : status}
      {sm?.presunto ? " · presunto" : ""}
    </span>
  );
}

function EvidenceRow({ e, onOpenDocument }: { e: EdgeEvidence; onOpenDocument?: Props["onOpenDocument"] }) {
  const kind = e.source_kind;

  if (kind === "contexto") {
    return (
      <li className="py-2">
        <p className="text-[12px] leading-snug text-slate-400">{e.note || "Contexto público"}</p>
        <p className="mt-1 text-[10px] text-slate-600">Contexto público · sin fuente judicial</p>
      </li>
    );
  }

  if (kind === "prensa" || kind === "ultima_hora") {
    const ultima = kind === "ultima_hora";
    return (
      <li className="py-2">
        <p className="text-[12px] leading-snug text-slate-300">
          {e.note || e.quote || (ultima ? "Desarrollo reciente" : "Dato de prensa")}
        </p>
        {e.note && e.quote && (
          <p className="mt-1 text-[11px] italic leading-snug text-slate-500">"{e.quote}"</p>
        )}
        <p
          className={`mt-1 flex flex-wrap items-center gap-x-1.5 text-[10px] ${
            ultima ? "text-orange-300/90" : "text-amber-300/90"
          }`}
        >
          {e.url ? (
            <a
              href={e.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 underline-offset-2 hover:underline"
            >
              {e.outlet || "Prensa"} <ExternalLink className="h-3 w-3" />
            </a>
          ) : (
            <span>{e.outlet || "Prensa"}</span>
          )}
          {e.date && <span className="tabular-nums">· {e.date}</span>}
          <span>· {ultima ? "de momento solo en prensa" : "sin fuente judicial"}</span>
        </p>
        {e.status && (
          <p className="mt-1 text-[10px]">
            <StatusBadge status={e.status} />
          </p>
        )}
      </li>
    );
  }

  if (kind === "mercantil") {
    return (
      <li className="py-2">
        <p className="text-[12px] leading-snug text-slate-300">
          {e.note || "Estructura societaria (Registro Mercantil / BORME)"}
        </p>
        <p className="mt-1 text-[10px] text-cyan-300/90">
          Registro mercantil / BORME (vía Nearius) · dato oficial público, sin fuente judicial
        </p>
        {e.status && (
          <p className="mt-1 text-[10px]">
            <StatusBadge status={e.status} />
          </p>
        )}
      </li>
    );
  }

  if (kind === "subvencion") {
    const importe = e.amount ?? e.importe;
    const bits = [
      e.date || e.fecha,
      importe != null ? fmtAmount(importe, e.currency) : null,
      e.instrumento,
      e.convocante_detalle,
    ].filter(Boolean);
    return (
      <li className="py-2">
        <p className="text-[12px] leading-snug text-slate-300">
          {e.note || "Subvención, ayuda o aval público (BDNS/SNPSAP)"}
        </p>
        {bits.length > 0 && (
          <p className="mt-1 text-[11px] tabular-nums text-slate-400">{bits.join(" · ")}</p>
        )}
        <p className="mt-1 text-[10px] text-lime-300/90">
          BDNS / SNPSAP · dato oficial público, sin fuente judicial · recibir una ayuda es legal
        </p>
      </li>
    );
  }

  if (kind === "contratacion") {
    // La capa cubre dos overlays: menores (MINOR_CONTRACTOR_OF, spec 046) y ampliada
    // (CONTRACTOR_OF, spec 055: licitaciones + agregadas + menores CCAA). Discrimina
    // por el TIPO de la relación (explícito); `ambito` queda solo de fallback.
    const esAmpliada = e.rel_type ? e.rel_type === "CONTRACTOR_OF" : e.ambito != null;
    const unidad = esAmpliada ? "contratos" : "contratos menores";
    const bits = [
      e.n_contratos != null ? `${e.n_contratos} ${unidad}` : null,
      e.total_eur != null ? fmtAmount(e.total_eur, "EUR") + " en total" : null,
      e.presupuesto_total_eur != null
        ? "presupuesto " + fmtAmount(e.presupuesto_total_eur, "EUR")
        : null,
      e.rango,
      e.organo_detalle,
      e.n_emergencia ? `${e.n_emergencia} en tramitación de emergencia` : null,
      e.n_negociado_sp ? `${e.n_negociado_sp} por negociado sin publicidad` : null,
      e.via_ute ? `vía ${e.via_ute}` : null,
    ].filter(Boolean);
    return (
      <li className="py-2">
        <p className="text-[12px] leading-snug text-slate-300">
          {e.note ||
            (esAmpliada
              ? "Agregado oficial de contratación pública (licitaciones / menores autonómicos)"
              : "Agregado de contratos menores publicados en PLACSP")}
        </p>
        {bits.length > 0 && (
          <p className="mt-1 text-[11px] tabular-nums text-slate-400">{bits.join(" · ")}</p>
        )}
        {e.ejemplo_link && (
          <p className="mt-1 text-[10px]">
            <a
              href={e.ejemplo_link}
              target="_blank"
              rel="noopener noreferrer"
              className="text-purple-300/90 underline decoration-dotted hover:text-purple-200"
              title={e.ejemplo_titulo ?? undefined}
            >
              Ver un contrato de ejemplo en la fuente oficial ↗
            </a>
          </p>
        )}
        <p className="mt-1 text-[10px] text-purple-300/90">
          {e.fuente || "PLACSP"} · agregado oficial público, sin fuente judicial · contratar con la administración es legal
        </p>
      </li>
    );
  }

  if (kind === "boe") {
    const periodo = `${e.fecha_nombramiento ?? "¿?"} → ${e.fecha_cese ?? "sin cese registrado en esta capa"}`;
    return (
      <li className="py-2">
        <p className="text-[12px] leading-snug text-slate-300">
          {e.cargo || "Cargo público (BOE, sección II.A)"}
        </p>
        <p className="mt-1 text-[11px] tabular-nums text-slate-400">{periodo}</p>
        {e.note && <p className="mt-1 text-[11px] leading-snug text-slate-500">{e.note}</p>}
        {(e.url_nombramiento || e.url_cese) && (
          <p className="mt-1 flex flex-wrap gap-x-3 text-[10px]">
            {e.url_nombramiento && (
              <a
                href={e.url_nombramiento}
                target="_blank"
                rel="noopener noreferrer"
                className="text-teal-300/90 underline decoration-dotted hover:text-teal-200"
              >
                Nombramiento en el BOE ({e.boe_id_nombramiento}) ↗
              </a>
            )}
            {e.url_cese && (
              <a
                href={e.url_cese}
                target="_blank"
                rel="noopener noreferrer"
                className="text-teal-300/90 underline decoration-dotted hover:text-teal-200"
              >
                Cese en el BOE ({e.boe_id_cese}) ↗
              </a>
            )}
          </p>
        )}
        <p className="mt-1 text-[10px] text-teal-300/90">
          BOE sección II.A · dato oficial público, sin fuente judicial · un nombramiento es un acto administrativo legal
        </p>
      </li>
    );
  }

  // judicial / contrato / acusacion — cited document evidence
  const canRead = !!e.file && onOpenDocument;
  return (
    <li className="py-2">
      {e.quote && <p className="text-[12px] italic leading-snug text-slate-400">"{e.quote}"</p>}
      {/* The note often QUALIFIES the quote (audit corrections: wrong page, scope, attribution) → never hide it. */}
      {e.note && (
        <p className={`text-[12px] leading-snug text-slate-400 ${e.quote ? "mt-1 not-italic text-slate-300" : ""}`}>
          {e.quote && <span className="font-medium text-slate-300">Nota: </span>}
          {e.note}
        </p>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10px] text-slate-500">
        {e.doc &&
          (canRead ? (
            <button
              onClick={() => onOpenDocument!(e.file!, e.page ?? null, e.quote ?? null)}
              title="Leer el pasaje en el documento"
              className="group inline-flex max-w-full items-center gap-1 truncate rounded bg-white/5 px-1.5 py-0.5 font-mono text-slate-400 transition hover:bg-accent/15 hover:text-accent"
            >
              <BookOpen className="h-3 w-3 shrink-0 opacity-60 group-hover:opacity-100" />
              <span className="truncate">
                {e.doc}
                {e.page != null ? ` · pág. ${e.page}` : ""}
              </span>
            </button>
          ) : (
            <span className="max-w-full truncate rounded bg-white/5 px-1.5 py-0.5 font-mono" title={e.file ?? undefined}>
              {e.doc}
              {e.page != null ? ` · pág. ${e.page}` : ""}
            </span>
          ))}
        {e.date && <span className="tabular-nums">{e.date}</span>}
        {e.amount != null && (
          <span className="font-mono tabular-nums text-emerald-400">{fmtAmount(e.amount, e.currency)}</span>
        )}
        {e.status && <StatusBadge status={e.status} />}
      </div>
    </li>
  );
}

/** Evidence grouped by provenance tier (KIND_ORDER); rows keep server order. */
function EvidenceGroups({
  evidence,
  onOpenDocument,
}: {
  evidence: EdgeEvidence[];
  onOpenDocument?: Props["onOpenDocument"];
}) {
  const byKind = new Map<string, EdgeEvidence[]>();
  for (const e of evidence) {
    const k = e.source_kind || "judicial";
    if (!byKind.has(k)) byKind.set(k, []);
    byKind.get(k)!.push(e);
  }
  const ordered = KIND_ORDER.filter((k) => byKind.has(k)).map((k) => ({ kind: k, rows: byKind.get(k)! }));
  for (const [k, rows] of byKind) if (!KIND_ORDER.includes(k)) ordered.push({ kind: k, rows });

  return (
    <div className="space-y-4">
      {ordered.map((g) => {
        const meta = SOURCE_KIND_META[g.kind];
        const ultima = g.kind === "ultima_hora";
        return (
          <section
            key={g.kind}
            className={ultima ? "rounded-lg border border-orange-500/20 bg-orange-500/[0.06] px-2.5 pb-1" : ""}
          >
            <header
              className={`mb-1 flex items-baseline justify-between border-b border-white/5 pb-1 ${ultima ? "pt-2" : ""}`}
              title={meta?.desc}
            >
              <h3 className="font-mono text-[10px] uppercase tracking-[0.2em]" style={{ color: meta?.color ?? "#94a3b8" }}>
                {meta?.label ?? g.kind}
              </h3>
              <span className="text-[10px] text-slate-600">
                {g.rows.length} {g.rows.length === 1 ? "evidencia" : "evidencias"}
              </span>
            </header>
            <ul className="divide-y divide-white/[0.04]">
              {g.rows.map((e, i) => (
                <EvidenceRow key={i} e={e} onOpenDocument={onOpenDocument} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

export default function EdgeDetailPanel({ edge, filters, onClose, onPickNode, onOpenDocument, snap, onSnapChange }: Props) {
  const isPair = !edge.type;
  // single-type mode keeps the EdgeDetail shape; pair mode holds groups.
  const [evidence, setEvidence] = useState<EdgeEvidence[]>([]);
  const [groups, setGroups] = useState<RelationGroup[] | null>(null);
  const [names, setNames] = useState<{ source: string; target: string } | null>(null);
  const [meta, setMeta] = useState<{ count: number; total: number; types?: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  // Desktop-only slim-strip collapse; mobile height comes from `snap`.
  const [collapsed, setCollapsed] = useState(false);
  const peek = snap === "peek";
  const { dragPx, handlers } = useSheetDrag(snap, onSnapChange);

  useEffect(() => {
    setCollapsed(false);
    setLoading(true);
    setError(false);
    setGroups(null);
    const run = isPair
      ? fetchRelationPair(edge, filters).then((p) => {
          setGroups(p.groups);
          setEvidence(p.groups.flatMap((g) => g.evidence));
          setNames({ source: p.endpoints[0]?.name ?? edge.sourceName ?? "", target: p.endpoints[1]?.name ?? edge.targetName ?? "" });
          setMeta(p.meta);
        })
      : fetchEdgeDetail(edge, filters).then((d) => {
          setEvidence(d.evidence);
          setNames({ source: d.source.name, target: d.target.name });
          setMeta(d.meta);
        });
    run
      .catch(() => {
        setError(true);
        setEvidence([]);
        setMeta(null);
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [edge.sourceId, edge.targetId, edge.type, filters.fromYear, filters.toYear]);

  const relC = isPair ? "#7dd3fc" : relColor(edge.type!);
  const sourceName = names?.source || edge.sourceName || anchorLabel(edge.sourceId).label;
  const targetName = names?.target || edge.targetName || anchorLabel(edge.targetId).label;

  const stats = useMemo(() => {
    const years = evidence.map((e) => e.date_year).filter((y): y is number => y != null);
    const lo = years.length ? Math.min(...years) : null;
    const hi = years.length ? Math.max(...years) : null;
    const period = lo == null ? "—" : lo === hi ? String(lo) : `${lo}–${hi}`;
    const total = evidence.reduce((s, e) => s + (e.amount ?? 0), 0);
    return { period, totalAmount: total > 0 ? `${total.toLocaleString("es-ES")} €` : "—" };
  }, [evidence]);

  const hiddenByRange = meta != null && meta.count === 0 && meta.total > 0;
  const rangeLabel = `${filters.fromYear ?? "…"}–${filters.toYear ?? "…"}`;
  const heading = isPair ? "Relación" : `Relación · ${relLabel(edge.type!)}`;

  return (
    <aside
      style={dragPx != null ? { height: dragPx } : undefined}
      className={`fixed inset-x-0 bottom-0 z-40 flex w-full flex-col rounded-t-2xl border-t border-white/10 glass animate-fade-in pb-safe
        ${dragPx == null ? SNAP_HEIGHT[snap] : ""} ${collapsed ? "md:w-12" : "md:w-[380px]"}
        md:static md:z-auto md:h-full md:shrink-0 md:rounded-none md:border-t-0 md:border-l md:border-white/5 md:pb-0`}
    >
      {/* mobile grab handle — tap cycles / drag resizes (md:hidden) */}
      <SheetHandle handlers={handlers} />
      {/* desktop-only collapsed strip */}
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
          {sourceName} · {targetName}
        </span>
      </div>

      <div className={collapsed ? "contents md:hidden" : "contents"}>
        {/* header */}
        <div className="relative border-b border-white/5 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="mb-1.5 flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: relC, boxShadow: `0 0 6px ${relC}` }} />
                <span className="font-mono text-[10px] uppercase tracking-[0.22em]" style={{ color: relC }}>
                  {heading}
                </span>
              </div>
              <h2 className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[15px] font-semibold leading-tight tracking-tight text-slate-50">
                <button
                  onClick={() => onPickNode(edge.sourceId)}
                  className="max-w-full truncate text-left underline-offset-2 transition hover:text-accent hover:underline"
                  title="Ver esta entidad"
                >
                  {sourceName}
                </button>
                <ArrowRight className="h-3.5 w-3.5 shrink-0" style={{ color: relC }} />
                <button
                  onClick={() => onPickNode(edge.targetId)}
                  className="max-w-full truncate text-left underline-offset-2 transition hover:text-accent hover:underline"
                  title="Ver esta entidad"
                >
                  {targetName}
                </button>
              </h2>
            </div>
            <div className="flex shrink-0 items-center gap-0.5">
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
              [
                "Evidencias",
                loading ? "…" : meta ? (meta.total > meta.count ? `${meta.count} de ${meta.total}` : `${meta.count}`) : "—",
              ],
              ["Periodo", loading ? "…" : stats.period],
              ["Importe", loading ? "…" : stats.totalAmount],
            ].map(([k, v]) => (
              <div key={k} className="bg-ink-900/70 px-2 py-2">
                <dt className="font-mono text-[9px] uppercase tracking-[0.18em] text-slate-600">{k}</dt>
                <dd className="mt-0.5 font-mono text-sm tabular-nums text-slate-100">{v}</dd>
              </div>
            ))}
          </dl>
          {isPair && meta?.types ? (
            <p className="mt-2 text-[10px] text-slate-500">
              {meta.types} {meta.types === 1 ? "tipo de relación" : "tipos de relación"} entre estas dos entidades.
            </p>
          ) : null}
        </div>

        {/* evidence */}
        <div className={`flex-1 overflow-auto ${peek ? "hidden md:block" : ""}`}>
          {loading && <p className="px-4 py-3 text-sm text-slate-500">Cargando…</p>}
          {error && !loading && <p className="px-4 py-3 text-sm text-slate-500">No se pudo cargar la relación.</p>}
          {hiddenByRange && !loading && (
            <p className="px-4 py-3 text-sm text-slate-500">
              Sin evidencias en el rango {rangeLabel} ({meta!.total} en total). Amplía la cronología para verlas.
            </p>
          )}
          {!loading && !error && !hiddenByRange && (
            <div className="space-y-5 px-4 pb-6 pt-2">
              {isPair && groups
                ? groups.map((g, i) => {
                    const gc = relColor(g.type);
                    return (
                      <section key={i}>
                        <header className="mb-2 flex items-center gap-1.5 text-[11px]">
                          <span className="h-1.5 w-1.5 rounded-full" style={{ background: gc }} />
                          <span className="font-medium text-slate-300">{relLabel(g.type)}</span>
                          <span className="text-slate-600">
                            · {g.fromName} <ArrowRight className="inline h-3 w-3" /> {g.toName}
                          </span>
                        </header>
                        {g.evidence.length ? (
                          <EvidenceGroups evidence={g.evidence} onOpenDocument={onOpenDocument} />
                        ) : (
                          <p className="text-[11px] text-slate-600">Sin evidencias en el rango.</p>
                        )}
                      </section>
                    );
                  })
                : <EvidenceGroups evidence={evidence} onOpenDocument={onOpenDocument} />}
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}
