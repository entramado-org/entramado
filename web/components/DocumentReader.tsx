"use client";
import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, FileText, X } from "lucide-react";
import { fetchDocument } from "@/lib/api";
import type { DocumentRead } from "@/lib/types";

interface Props {
  docId: string;
  /** Page to open at (from a citation). Omit to open at the document's first page. */
  initialPage?: number | null;
  /** Cited passage to highlight on the opened page, if present. */
  highlightQuote?: string | null;
  onClose: () => void;
}

function fold(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** Render page text with the cited quote highlighted (accent/case-insensitive,
 *  falling back to a shorter prefix when the full quote isn't a clean substring
 *  — OCR/whitespace drift). */
function Highlighted({ text, quote }: { text: string; quote?: string | null }) {
  const q = (quote ?? "").trim();
  if (!q) return <>{text}</>;
  const ft = fold(text);
  for (const probe of [q, q.slice(0, 80), q.slice(0, 40)]) {
    const fp = fold(probe).trim();
    if (fp.length < 12) break;
    const at = ft.indexOf(fp);
    if (at !== -1) {
      return (
        <>
          {text.slice(0, at)}
          <mark className="rounded bg-accent/25 text-slate-100">{text.slice(at, at + fp.length)}</mark>
          {text.slice(at + fp.length)}
        </>
      );
    }
  }
  return <>{text}</>;
}

export default function DocumentReader({ docId, initialPage, highlightQuote, onClose }: Props) {
  const [data, setData] = useState<DocumentRead | null>(null);
  const [pages, setPages] = useState<number[]>([]);
  const [page, setPage] = useState<number | null>(initialPage ?? null);
  const [title, setTitle] = useState<string>(docId);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  // Phase 1: resolve the page index + title once per document.
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(false);
    fetchDocument(docId)
      .then((d) => {
        if (!alive) return;
        if (d.candidates?.length) {
          setError(true);
          return;
        }
        const idx = d.pages ?? [];
        setPages(idx);
        if (d.doc?.title) setTitle(d.doc.title);
        setPage((p) => p ?? initialPage ?? idx[0] ?? null);
      })
      .catch(() => alive && setError(true))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  // Phase 2: load the selected page's text.
  useEffect(() => {
    if (page == null) return;
    let alive = true;
    setLoading(true);
    fetchDocument(docId, page, 0)
      .then((d) => alive && setData(d))
      .catch(() => alive && setError(true))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [docId, page]);

  const pos = page != null ? pages.indexOf(page) : -1;
  const prevPage = pos > 0 ? pages[pos - 1] : null;
  const nextPage = pos >= 0 && pos < pages.length - 1 ? pages[pos + 1] : null;

  const bodyText = useMemo(
    () => (data?.chunks ?? []).map((c) => c.text).join("\n\n"),
    [data],
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/70 p-4 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        className="flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-white/10 glass shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* header */}
        <div className="flex items-center justify-between gap-3 border-b border-white/5 px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            <FileText className="h-4 w-4 shrink-0 text-accent" />
            <span className="truncate text-sm font-medium text-slate-100" title={docId}>
              {title}
            </span>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-slate-500 hover:bg-white/5 hover:text-slate-200"
            aria-label="Cerrar lector"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* pager */}
        <div className="flex items-center justify-between gap-3 border-b border-white/5 px-4 py-2 text-[12px] text-slate-400">
          <button
            disabled={prevPage == null}
            onClick={() => prevPage != null && setPage(prevPage)}
            className="inline-flex items-center gap-1 rounded px-2 py-1 enabled:hover:bg-white/5 disabled:opacity-30"
          >
            <ChevronLeft className="h-3.5 w-3.5" /> Anterior
          </button>
          <span className="tabular-nums">
            {page != null ? `Página ${page}` : "—"}
            {pages.length ? ` · ${pos + 1}/${pages.length}` : ""}
          </span>
          <button
            disabled={nextPage == null}
            onClick={() => nextPage != null && setPage(nextPage)}
            className="inline-flex items-center gap-1 rounded px-2 py-1 enabled:hover:bg-white/5 disabled:opacity-30"
          >
            Siguiente <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* body */}
        <div className="flex-1 overflow-auto px-5 py-4">
          {error ? (
            <p className="text-sm text-slate-500">
              No se pudo abrir el documento (puede haber varios con ese título, o no está en el corpus).
            </p>
          ) : loading && !bodyText ? (
            <p className="text-sm text-slate-500">Cargando…</p>
          ) : bodyText ? (
            <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-slate-300">
              <Highlighted text={bodyText} quote={highlightQuote} />
            </p>
          ) : (
            <p className="text-sm text-slate-500">Esta página no tiene texto indexado.</p>
          )}
        </div>
      </div>
    </div>
  );
}
