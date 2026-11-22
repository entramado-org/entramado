"use client";
import { useEffect, useState } from "react";
import { Scale } from "lucide-react";
import { fetchEntityClaims } from "@/lib/api";
import type { Claim, ClaimPosture } from "@/lib/types";

function PostureRow({ p }: { p: ClaimPosture }) {
  const asserts = p.stance === "asserts";
  const isPress = p.source_kind === "prensa" || p.source_kind === "ultima_hora";
  const src = isPress
    ? `según prensa${p.outlet ? `: ${p.outlet}` : ""}`
    : p.source_kind === "contexto"
      ? "contexto público"
      : [p.doc ? `${p.doc}${p.page != null ? ` · p.${p.page}` : ""}` : null].filter(Boolean).join(" ");
  return (
    <li className="py-1.5">
      <p className="flex items-baseline gap-1.5 text-[12px] leading-snug">
        <span
          className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider ${
            asserts ? "bg-rose-500/15 text-rose-300" : "bg-emerald-500/15 text-emerald-300"
          }`}
        >
          {asserts ? "afirma" : "niega"}
        </span>
        <span className="text-slate-300">{p.entityName}</span>
      </p>
      {p.quote && <p className="mt-0.5 pl-1 text-[11px] italic leading-snug text-slate-500">"{p.quote}"</p>}
      <p className="mt-0.5 pl-1 text-[10px] text-slate-600">
        {src}
        {p.date ? ` · ${p.date}` : ""}
      </p>
    </li>
  );
}

function ClaimCard({ c }: { c: Claim }) {
  return (
    <li className="rounded-lg border border-white/10 bg-white/[0.02] p-3">
      <p className="text-[13px] font-medium leading-snug text-slate-200">{c.text}</p>
      {c.topic && <p className="mt-0.5 text-[10px] uppercase tracking-wider text-slate-600">{c.topic}</p>}
      <ul className="mt-1.5 divide-y divide-white/[0.04]">
        {c.postures.map((p, i) => (
          <PostureRow key={i} p={p} />
        ))}
      </ul>
    </li>
  );
}

/** "En disputa": reified claims this entity is party to — accusation vs denial,
 *  each attributed to its source. Renders nothing when the entity has no claims. */
export default function ClaimsInDispute({ nodeId }: { nodeId: string }) {
  const [claims, setClaims] = useState<Claim[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoaded(false);
    fetchEntityClaims(nodeId)
      .then((r) => alive && setClaims(r.claims))
      .catch(() => alive && setClaims([]))
      .finally(() => alive && setLoaded(true));
    return () => {
      alive = false;
    };
  }, [nodeId]);

  if (!loaded || claims.length === 0) return null;

  return (
    <section className="border-t border-white/5 px-4 py-3">
      <h3 className="mb-2 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.2em] text-slate-400">
        <Scale className="h-3.5 w-3.5 text-amber-300/80" /> En disputa
      </h3>
      <p className="mb-2 text-[10px] leading-snug text-slate-600">
        Afirmaciones contestadas: la acusación y el desmentido, cada uno atribuido a su fuente. Rige la
        presunción de inocencia.
      </p>
      <ul className="space-y-2">
        {claims.map((c) => (
          <ClaimCard key={c.claimId} c={c} />
        ))}
      </ul>
    </section>
  );
}
