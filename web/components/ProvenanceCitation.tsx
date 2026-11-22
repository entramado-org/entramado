"use client";
import { FileText } from "lucide-react";
import type { Provenance } from "@/lib/types";

export default function ProvenanceCitation({ p }: { p: Provenance }) {
  const snippet = p.quote.length > 280 ? p.quote.slice(0, 280) + "…" : p.quote;
  return (
    <div className="rounded-lg border border-white/5 bg-ink-800/50 p-3">
      <div className="mb-1.5 flex items-center gap-2 text-xs text-accent">
        <FileText className="h-3.5 w-3.5" />
        <span className="font-medium">{p.doc}</span>
        <span className="text-slate-500">· pág. {p.page}</span>
      </div>
      <p className="text-xs leading-relaxed text-slate-400">{snippet}</p>
    </div>
  );
}
