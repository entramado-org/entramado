"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { fetchOverview, fetchNodeDetail } from "@/lib/api";
import { STATUS_META, headlineStatus } from "@/lib/graphStyle";
import type { GraphNode } from "@/lib/types";

// Deterministic entity_id slug (mirrors the pipeline's _slug).
function slug(name: string): string {
  return name
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

const SEED: { name: string; role: string }[] = [
  { name: "Pedro Sánchez", role: "Presidente del Gobierno" },
  { name: "José Luis Ábalos", role: "Ex ministro de Transportes" },
  { name: "Koldo García", role: "Ex asesor del ministro" },
  { name: "Santos Cerdán", role: "Ex secretario de Organización del PSOE" },
  { name: "Víctor de Aldama", role: "Empresario / intermediario" },
  { name: "Begoña Gómez", role: "Esposa del presidente" },
  { name: "José Luis Rodríguez Zapatero", role: "Ex presidente del Gobierno" },
  { name: "Delcy Rodríguez", role: "Vicepresidenta de Venezuela" },
];

interface Figure {
  id: string;
  name: string;
  role: string;
  degree?: number;
  summary?: string | null;
  status?: string | null; // most-advanced procedural stage (epistemic tier)
}

export default function KeyFigures() {
  const [figures, setFigures] = useState<Figure[]>(
    SEED.map((s) => ({ id: `Person:${slug(s.name)}`, name: s.name, role: s.role })),
  );

  useEffect(() => {
    let cancelled = false;
    fetchOverview(["Person"], 30)
      .then(async (g) => {
        if (cancelled || !g.nodes.length) return;
        const top = [...g.nodes].sort((a, b) => (b.degree ?? 0) - (a.degree ?? 0)).slice(0, 8);
        const base: Figure[] = top.map((n: GraphNode) => ({
          id: n.id,
          name: n.name,
          role: n.role ?? SEED.find((s) => slug(s.name) === n.id.split(":")[1])?.role ?? "",
          degree: n.degree,
          summary: n.summary ?? undefined,
        }));
        setFigures(base);
        // Enrich the 8 shown figures with their procedural status (only the
        // node-detail endpoint carries chargeStatuses — bounded to 8 calls).
        const details = await Promise.allSettled(top.map((n) => fetchNodeDetail(n.id)));
        if (cancelled) return;
        setFigures(
          base.map((f, i) => {
            const d = details[i];
            if (d.status !== "fulfilled") return f;
            return { ...f, status: headlineStatus(d.value.node.chargeStatuses) };
          }),
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {figures.map((f) => {
        const sm = f.status ? STATUS_META[f.status] : null;
        return (
          <Link
            key={f.id}
            href={`/explore?focus=${encodeURIComponent(f.id)}`}
            className="group flex flex-col rounded-2xl glass p-4 transition hover:border-accent/40 hover:bg-white/5"
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="h-2.5 w-2.5 rounded-full bg-[#38bdf8] shadow-[0_0_10px_#7dd3fc]" />
              <ArrowUpRight className="h-4 w-4 text-slate-600 transition group-hover:text-accent" />
            </div>
            <h3 className="text-sm font-semibold leading-tight text-slate-100">{f.name}</h3>
            <p className="mt-1 text-xs text-slate-500">{f.role}</p>
            {sm && (
              <span
                className="mt-2 inline-flex w-fit items-center rounded-full border px-2 py-0.5 text-[10px] font-medium"
                style={{ color: sm.color, borderColor: `${sm.color}66` }}
              >
                {sm.label}
              </span>
            )}
            {f.summary && (
              <p className="mt-2 line-clamp-2 text-[11px] leading-snug text-slate-400">{f.summary}</p>
            )}
            {f.degree != null && (
              <p className="mt-2 text-[11px] text-accent/80">{f.degree} conexiones</p>
            )}
          </Link>
        );
      })}
    </div>
  );
}
