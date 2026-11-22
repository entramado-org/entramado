/* Server-side data access for the public case fichas (/caso, spec 038).
 * Mirror of lib/fichas.ts: talks to the API over the compose network with ISR
 * (`next.revalidate`), degrading to null when the API is down. */
import type { MoneyFlow, TimelineEvent } from "./types";
import type { AccusedCharge } from "./fichas";

const API_BASE =
  process.env.API_INTERNAL_BASE || process.env.NEXT_PUBLIC_API_BASE || "http://api:8000";

export const CASO_REVALIDATE = 3600;

export interface CasoMeta {
  slug: string;
  titulo: string;
  dek: string;
  estado: string;
  estadoKind: "instruccion" | "juicio" | "sentencia" | "archivado";
  causa?: string | null;
  updated?: string | null;
  briefingSlug?: string | null;
  nImplicados: number;
}

export interface CasoImplicado {
  id: string;
  type: string;
  name: string;
  role?: string | null;
  degree: number;
  charges: AccusedCharge[];
  papel?: string | null;
  core: boolean;
  fichaSlug?: string | null;
}

export interface CasoDocumento {
  file: string;
  doc?: string | null;
  docType?: string | null;
  date?: string | null;
  pageCount: number;
}

export interface CasoPayload {
  meta: CasoMeta;
  entidadesIds: string[];
  ultimaHoraTags: string[];
  relacionados: CasoMeta[];
  implicados: CasoImplicado[];
  documentos: CasoDocumento[];
  cronologia: TimelineEvent[];
  flujos: MoneyFlow[];
}

async function apiJson<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`${API_BASE}${path}`, { next: { revalidate: CASO_REVALIDATE } });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null; // API down → callers degrade (404 / empty section), never throw
  }
}

export async function getCasos(): Promise<CasoMeta[]> {
  const j = await apiJson<{ cases: CasoMeta[] }>("/cases");
  return j?.cases ?? [];
}

export async function getCaso(slug: string): Promise<CasoPayload | null> {
  return apiJson<CasoPayload>(`/case/${encodeURIComponent(slug)}`);
}

/** Briefing content for the case summary — degrades to null (the page then
 *  falls back to the dek) when the vault isn't mounted. */
export async function getCasoBriefing(slug: string): Promise<string | null> {
  const j = await apiJson<{ content?: string }>(`/briefing/${encodeURIComponent(slug)}`);
  return j?.content ?? null;
}
