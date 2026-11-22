// «Patrones llamativos» (spec 053). Los datos viven en
// `pipeline/data/patrones/patrones.yaml` (versionado, servido en runtime por
// GET /patrones con caché-mtime y validación estricta fail-loud) — un patrón
// añadido/corregido por PR es visible SIN rebuild de la imagen web. Este módulo
// aporta los tipos, el mapa tipo→color/etiqueta y el loader server-side.

export interface PatronHito {
  fecha: string; // ISO YYYY[-MM[-DD]]
  hecho: string;
  fuentes: string[]; // ids que resuelven en PatronFuente.id
}

export interface PatronContrato {
  fecha: string; // ISO YYYY-MM-DD (adjudicación; ver fecha_nota)
  objeto: string;
  importe_eur?: number; // c/IVA (dato PLACSP)
  organo: string;
  referencia: string; // expediente PLACSP — el dato verificable si muere el deeplink
  url?: string;
  via?: string; // p. ej. UTE
  nota?: string; // discrepancias del dato fuente, caveats
  fecha_nota?: string;
}

export interface PatronResumen {
  periodo: string;
  n_contratos: number;
  total_eur?: number;
}

export interface PatronFuente {
  id: string;
  etiqueta: string;
  acredita: string;
  url?: string; // fuente externa (BORME, PLACSP, nota oficial)…
  doc?: string; // …o documento del corpus
  pagina?: number;
}

export type ContratosTope = "obras" | "servicios" | "suministros";

export interface Patron {
  slug: string;
  titulo: string;
  tesis: string;
  tipos: string[];
  estado: string; // «sin foco mediático ni judicial público conocido»…
  estado_fecha: string; // …a esta fecha (mantenimiento editorial manual)
  descubierto?: string;
  contratos_tope: ContratosTope; // validado en la API; default obras
  entities: string[]; // entity_ids del grafo (con prefijo de label, validados)
  actores_sin_grafo: string[]; // mencionados sin nodo → texto plano, nunca enlace roto
  hitos: PatronHito[];
  contratos: PatronContrato[];
  contratos_nota?: string;
  contratos_resumen?: PatronResumen[];
  nota_negativos?: string;
  fuentes: PatronFuente[];
  disclaimer: string;
}

// Paleta espejo de lib/ultimaHora.ts / lib/timeline.ts — sin colores nuevos.
const C = {
  umbral: "#f87171", // red — pegado al tope legal
  puerta: "#a78bfa", // violet — puerta giratoria / institucional
  capital: "#4ade80", // green — dinero/capital
  societario: "#818cf8", // indigo — estructura societaria
  uso: "#fb923c", // orange — uso desviado de la figura
};

export const TIPO_META: Record<string, { label: string; color: string }> = {
  "umbral-menores": { label: "Al filo del umbral", color: C.umbral },
  fraccionamiento: { label: "Fraccionamiento", color: C.umbral },
  "uso-desviado-menor": { label: "Uso desviado del menor", color: C.uso },
  "puerta-giratoria": { label: "Puerta giratoria", color: C.puerta },
  "capital-desproporcionado": { label: "Capital desproporcionado", color: C.capital },
  "interposicion-societaria": { label: "Interposición societaria", color: C.societario },
  "recurrencia-organo": { label: "Recurrencia de órgano", color: C.societario },
};

const NEUTRAL = "#94a3b8"; // slate-400 — tipo desconocido de un patrón futuro

export function tipoMeta(tipo: string): { label: string; color: string } {
  return TIPO_META[tipo] ?? { label: tipo, color: NEUTRAL };
}

/** Tope del contrato menor c/IVA vigente en la fecha, según el tipo declarado
 *  por el patrón (`contratos_tope`, validado en la API): obras 50.000→40.000 €
 *  s/IVA (TRLCSP → art. 118 LCSP, 09-03-2018); servicios/suministros
 *  18.000→15.000 € s/IVA. La columna «% del tope» siempre divide por el tope
 *  del tipo del patrón — nunca por el de obras a secas. */
const TOPES_CIVA: Record<ContratosTope, { pre: number; post: number }> = {
  obras: { pre: 60500, post: 48400 },
  servicios: { pre: 21780, post: 18150 },
  suministros: { pre: 21780, post: 18150 },
};

export function topeVigente(fechaISO: string, tipo: ContratosTope): number {
  const t = TOPES_CIVA[tipo] ?? TOPES_CIVA.obras;
  return fechaISO < "2018-03-09" ? t.pre : t.post;
}

export function eur(n?: number): string {
  if (n === undefined || n === null) return "—";
  return n.toLocaleString("es-ES", { maximumFractionDigits: 2 }) + " €";
}

const API_BASE =
  process.env.API_INTERNAL_BASE || process.env.NEXT_PUBLIC_API_BASE || "http://api:8000";

/** Server-side loader (RSC), no-store: la página es force-dynamic y el feed en
 *  runtime es la gracia del circuito. Degrada a [] con la API caída, nunca lanza. */
export async function loadPatrones(): Promise<Patron[]> {
  try {
    const r = await fetch(`${API_BASE}/patrones`, { cache: "no-store" });
    if (!r.ok) return [];
    const j = await r.json();
    return j.patrones ?? [];
  } catch {
    return [];
  }
}
