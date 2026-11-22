"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, FileText, Network, Search } from "lucide-react";
import { fetchDocumentsSearch, fetchRecentDocuments } from "@/lib/api";
import type { DocSearchHit, RecentDoc } from "@/lib/types";
import DocumentReader from "./DocumentReader";

// The curated doc_type vocabulary (coverage is partial — only curated docs).
const DOC_TYPES: { value: string; label: string }[] = [
  { value: "", label: "Todos los tipos" },
  { value: "auto_judicial", label: "Autos judiciales" },
  { value: "informe_uco", label: "Informes UCO" },
  { value: "transcripcion_parlamentaria", label: "Transcripciones parlamentarias" },
  { value: "auditoria", label: "Auditorías" },
];

function docTypeLabel(t?: string | null): string | null {
  if (!t) return null;
  return DOC_TYPES.find((d) => d.value === t)?.label ?? t;
}

export default function DocumentosBrowser() {
  const [q, setQ] = useState("");
  const [tipo, setTipo] = useState("");
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [results, setResults] = useState<DocSearchHit[] | null>(null);
  const [recent, setRecent] = useState<RecentDoc[]>([]);
  const [loading, setLoading] = useState(false);
  const [reader, setReader] = useState<{ docId: string; page?: number | null; quote?: string | null } | null>(null);
  const acRef = useRef<AbortController | null>(null);

  useEffect(() => {
    fetchRecentDocuments(16).then((r) => setRecent(r.documents)).catch(() => setRecent([]));
  }, []);

  // Debounced corpus search; empty query clears back to the recent list.
  useEffect(() => {
    const query = q.trim();
    if (query.length < 2) {
      setResults(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    const id = setTimeout(() => {
      acRef.current?.abort();
      const ac = new AbortController();
      acRef.current = ac;
      fetchDocumentsSearch(query, { tipo: tipo || undefined, desde: desde || undefined, hasta: hasta || undefined, limit: 20 }, ac.signal)
        .then((r) => setResults(r.results))
        .catch((e) => {
          if (e?.name !== "AbortError") setResults([]);
        })
        .finally(() => setLoading(false));
    }, 280);
    return () => clearTimeout(id);
  }, [q, tipo, desde, hasta]);

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <div className="mb-8 flex items-center justify-between">
        <Link href="/" className="inline-flex items-center gap-2 text-sm text-slate-400 hover:text-accent">
          <ArrowLeft className="h-4 w-4" /> Inicio
        </Link>
        <Link
          href="/explore"
          className="inline-flex items-center gap-2 rounded-lg glass px-3 py-1.5 text-sm text-slate-200 hover:bg-white/5"
        >
          <Network className="h-4 w-4" /> Abrir el grafo
        </Link>
      </div>

      <header className="mb-7">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
          <FileText className="h-3.5 w-3.5" /> Documentos
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">El corpus documental</h1>
        <p className="mt-4 text-lg leading-relaxed text-slate-400">
          Busca en el texto íntegro de los autos, informes de la UCO, transcripciones y auditorías que
          sostienen el grafo, y <span className="text-slate-300">lee el pasaje exacto</span> de cualquier
          cita. La cobertura de tipo y fecha es parcial (solo documentos con metadatos curados).
        </p>
      </header>

      {/* search controls */}
      <div className="mb-6 space-y-3">
        <div className="flex items-center gap-2 rounded-xl glass px-3 py-2">
          <Search className="h-4 w-4 shrink-0 text-slate-500" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar en el corpus (p. ej. «comisión 1%», «Plus Ultra»)…"
            className="w-full bg-transparent text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none"
            aria-label="Buscar en el corpus"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <select
            value={tipo}
            onChange={(e) => setTipo(e.target.value)}
            className="rounded-lg glass px-2 py-1.5 text-slate-300 focus:outline-none"
            aria-label="Tipo de documento"
          >
            {DOC_TYPES.map((d) => (
              <option key={d.value} value={d.value} className="bg-ink-900">
                {d.label}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-1.5 text-slate-500">
            desde
            <input type="date" value={desde} onChange={(e) => setDesde(e.target.value)} className="rounded-lg glass px-2 py-1 text-slate-300 focus:outline-none" />
          </label>
          <label className="flex items-center gap-1.5 text-slate-500">
            hasta
            <input type="date" value={hasta} onChange={(e) => setHasta(e.target.value)} className="rounded-lg glass px-2 py-1 text-slate-300 focus:outline-none" />
          </label>
        </div>
      </div>

      {/* results or recent */}
      {loading && <p className="py-4 text-sm text-slate-500">Buscando…</p>}

      {!loading && results != null && (
        <section>
          <h2 className="mb-2 text-xs uppercase tracking-widest text-slate-500">
            {results.length ? `${results.length} resultado${results.length === 1 ? "" : "s"}` : "Sin resultados"}
          </h2>
          <ul className="space-y-2">
            {results.map((r) => (
              <li key={r.chunkId}>
                <button
                  onClick={() => setReader({ docId: r.file, page: r.page, quote: q.trim() })}
                  className="w-full rounded-xl glass px-4 py-3 text-left transition hover:bg-white/5"
                >
                  <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                    <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-slate-400">
                      {r.doc} · pág. {r.page}
                    </span>
                    {docTypeLabel(r.docType) && <span>{docTypeLabel(r.docType)}</span>}
                  </div>
                  <p className="line-clamp-3 text-[13px] leading-snug text-slate-300">{r.text}</p>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!loading && results == null && (
        <section>
          <h2 className="mb-2 text-xs uppercase tracking-widest text-slate-500">Documentos recientes</h2>
          {recent.length === 0 ? (
            <p className="py-2 text-sm text-slate-500">No hay documentos con fecha curada.</p>
          ) : (
            <ul className="grid gap-2 sm:grid-cols-2">
              {recent.map((d) => (
                <li key={d.file}>
                  <button
                    onClick={() => setReader({ docId: d.file, page: null, quote: null })}
                    className="flex w-full items-start gap-2 rounded-xl glass px-3 py-2.5 text-left transition hover:bg-white/5"
                  >
                    <FileText className="mt-0.5 h-4 w-4 shrink-0 text-accent/80" />
                    <span className="min-w-0">
                      <span className="block truncate text-[13px] text-slate-200">{d.doc}</span>
                      <span className="mt-0.5 block text-[11px] text-slate-500">
                        {d.date}
                        {docTypeLabel(d.docType) ? ` · ${docTypeLabel(d.docType)}` : ""}
                        {d.pageCount ? ` · ${d.pageCount} pág.` : ""}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {reader && (
        <DocumentReader
          docId={reader.docId}
          initialPage={reader.page}
          highlightQuote={reader.quote}
          onClose={() => setReader(null)}
        />
      )}
    </main>
  );
}
