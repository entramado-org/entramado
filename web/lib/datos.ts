/* Datos publicados (spec 077): tres YAML de `datos/` que se leen AL CONSTRUIR, sin fetch ni API.
 * El esquema es cerrado, igual que `scripts/validar_datos.py`: un campo de más, un caso inexistente
 * o una URL que no sea https rompen el build. Esa es la puerta que impide que vuelva a entrar
 * elaboración propia (resúmenes, citas, estados) en el sitio. `DATOS_DIR` solo lo usa la prueba de
 * retirada de un ítem. */
import { readFileSync } from "node:fs";
import path from "node:path";
import { parse } from "yaml";

export interface Item {
  iso: string; // AAAA-MM-DD
  titulo: string; // el de la pieza enlazada, literal
  medio: string; // medio de origen
  url: string;
  caso?: string; // slug de un caso; ausente = solo cronología
}

export interface Caso {
  slug: string;
  nombre: string;
}

export interface Documento {
  organismo: string;
  designacion: string;
  fecha: string; // AAAA-MM-DD
  url: string;
  caso?: string;
}

const DIR = process.env.DATOS_DIR || path.join(process.cwd(), "..", "datos");

const ITEM = ["iso", "titulo", "medio", "url", "caso"];
const CASO = ["slug", "nombre"];
const DOCUMENTO = ["organismo", "designacion", "fecha", "url", "caso"];

function leer(fichero: string, clave: string): unknown[] {
  const data = parse(readFileSync(path.join(DIR, fichero), "utf-8"));
  if (!data || typeof data !== "object" || Object.keys(data).join() !== clave || !Array.isArray(data[clave])) {
    throw new Error(`${fichero}: se espera un único bloque «${clave}:» con una lista`);
  }
  return data[clave];
}

function validar(fila: unknown, permitidos: string[], obligatorios: string[], donde: string, slugs: Set<string> | null) {
  if (!fila || typeof fila !== "object" || Array.isArray(fila)) throw new Error(`${donde}: no es un mapa`);
  const f = fila as Record<string, unknown>;
  for (const k of Object.keys(f)) if (!permitidos.includes(k)) throw new Error(`${donde}: campo no permitido «${k}»`);
  for (const k of obligatorios) if (!(k in f)) throw new Error(`${donde}: falta «${k}»`);
  for (const [k, v] of Object.entries(f)) {
    if (typeof v !== "string" || !v.trim() || v !== v.trim()) throw new Error(`${donde}: «${k}» debe ser texto no vacío`);
  }
  if (typeof f.url === "string" && !f.url.startsWith("https://")) throw new Error(`${donde}: la url debe ser https`);
  for (const k of ["iso", "fecha"]) {
    if (typeof f[k] === "string" && !/^\d{4}-\d{2}-\d{2}$/.test(f[k])) throw new Error(`${donde}: «${k}» no es AAAA-MM-DD`);
  }
  if (slugs && "caso" in f && !slugs.has(f.caso as string)) throw new Error(`${donde}: el caso «${f.caso}» no existe`);
}

const casos: Caso[] = leer("casos.yaml", "casos").map((c, i) => {
  validar(c, CASO, CASO, `casos[${i}]`, null);
  return c as Caso;
});
const slugs = new Set(casos.map((c) => c.slug));
if (slugs.size !== casos.length) throw new Error("casos.yaml: slugs repetidos");

const items: Item[] = leer("feed.yaml", "items").map((it, i) => {
  validar(it, ITEM, ["iso", "titulo", "medio", "url"], `items[${i}]`, slugs);
  return it as Item;
});

const documentos: Documento[] = leer("documentos.yaml", "documentos").map((d, i) => {
  validar(d, DOCUMENTO, ["organismo", "designacion", "fecha", "url"], `documentos[${i}]`, slugs);
  return d as Documento;
});

/** Del más reciente al más antiguo (el YAML ya viene así; el orden se garantiza aquí). */
export const ITEMS: Item[] = [...items].sort((a, b) => (a.iso < b.iso ? 1 : a.iso > b.iso ? -1 : 0));
export const CASOS: Caso[] = casos;
export const DOCUMENTOS: Documento[] = [...documentos].sort((a, b) => (a.fecha < b.fecha ? 1 : a.fecha > b.fecha ? -1 : 0));

export const itemsDeCaso = (slug: string) => ITEMS.filter((i) => i.caso === slug);
export const documentosDeCaso = (slug: string) => DOCUMENTOS.filter((d) => d.caso === slug);
export const casoPorSlug = (slug: string) => CASOS.find((c) => c.slug === slug);

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** «2026-08-14» → «14 ago 2026». Sin Date/Intl: evita el desfase de zona horaria y la variante «sept». */
export function fechaLarga(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${Number(d)} ${MESES[Number(m) - 1]} ${y}`;
}
