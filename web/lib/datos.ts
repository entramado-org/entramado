/* Datos publicados (spec 077): tres YAML de `datos/` que se leen AL CONSTRUIR, sin fetch ni API.
 * El esquema es cerrado, igual que `scripts/validar_datos.py`: un campo de más o de menos
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
}

export interface Documento {
  organismo: string;
  designacion: string;
  fecha: string; // AAAA-MM-DD
  url: string;
}

const DIR = process.env.DATOS_DIR || path.join(process.cwd(), "..", "datos");

const ITEM = ["iso", "titulo", "medio", "url"];
const DOCUMENTO = ["organismo", "designacion", "fecha", "url"];

// Misma tabla que `scripts/validar_datos.py`: un documento oficial solo enlaza a la web de su organismo.
const ORGANISMOS: Record<string, string[]> = {
  Senado: ["www.senado.es"],
  "Congreso de los Diputados": ["www.congreso.es"],
  "Poder Judicial": ["www.poderjudicial.es"],
  "Boletín Oficial del Estado": ["www.boe.es"],
  "Tribunal de Cuentas": ["www.tcu.es"],
};

// Misma expresión que `validar_datos.py`: https, host en minúsculas sin credenciales ni puerto, y ruta.
const URL_HTTPS = /^https:\/\/([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+)\/[^\s\\]*$/;

/** Fecha de calendario real (2026-13-01 o 2026-02-30 no lo son). */
function esFecha(v: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(v)) return false;
  const d = new Date(`${v}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === v && v <= new Date().toISOString().slice(0, 10);
}

function repetidas(urls: string[], donde: string) {
  const vistas = new Set<string>();
  for (const u of urls) {
    const clave = u.split(/[?#]/)[0].replace(/\/+$/, ""); // la misma pieza con otra grafía
    if (vistas.has(clave)) throw new Error(`${donde}: url repetida ${u}`);
    vistas.add(clave);
  }
}

function leer(fichero: string, clave: string): unknown[] {
  // YAML 1.1, como PyYAML: una fecha sin comillas o un «No» no son texto y se rechazan en los dos lados.
  const data = parse(readFileSync(path.join(DIR, fichero), "utf-8"), { version: "1.1" });
  if (!data || typeof data !== "object" || Object.keys(data).join() !== clave || !Array.isArray(data[clave])) {
    throw new Error(`${fichero}: se espera un único bloque «${clave}:» con una lista`);
  }
  return data[clave];
}

function validar(fila: unknown, permitidos: string[], donde: string) {
  if (!fila || typeof fila !== "object" || Array.isArray(fila)) throw new Error(`${donde}: no es un mapa`);
  const f = fila as Record<string, unknown>;
  for (const k of Object.keys(f)) if (!permitidos.includes(k)) throw new Error(`${donde}: campo no permitido «${k}»`);
  for (const k of permitidos) if (!(k in f)) throw new Error(`${donde}: falta «${k}»`);
  for (const [k, v] of Object.entries(f)) {
    if (typeof v !== "string" || !v.trim() || v !== v.trim()) throw new Error(`${donde}: «${k}» debe ser texto no vacío`);
  }
  if (typeof f.url === "string") {
    const host = URL_HTTPS.exec(f.url)?.[1];
    if (!host) throw new Error(`${donde}: la url debe ser https y absoluta`);
    const u = { hostname: host };
    if (typeof f.organismo === "string") {
      const hosts = ORGANISMOS[f.organismo];
      if (!hosts) throw new Error(`${donde}: organismo desconocido «${f.organismo}»`);
      if (!hosts.includes(u.hostname)) throw new Error(`${donde}: la url no es de la web de «${f.organismo}»`);
    }
  }
  for (const k of ["iso", "fecha"]) {
    if (typeof f[k] === "string" && !esFecha(f[k] as string)) throw new Error(`${donde}: «${k}» no es una fecha AAAA-MM-DD`);
  }
}

const items: Item[] = leer("feed.yaml", "items").map((it, i) => {
  validar(it, ITEM, `items[${i}]`);
  return it as Item;
});
repetidas(items.map((i) => i.url), "items");

const documentos: Documento[] = leer("documentos.yaml", "documentos").map((d, i) => {
  validar(d, DOCUMENTO, `documentos[${i}]`);
  return d as Documento;
});
repetidas(documentos.map((d) => d.url), "documentos");

/** Del más reciente al más antiguo (el YAML ya viene así; el orden se garantiza aquí). */
export const ITEMS: Item[] = [...items].sort((a, b) => (a.iso < b.iso ? 1 : a.iso > b.iso ? -1 : 0));
export const DOCUMENTOS: Documento[] = [...documentos].sort((a, b) => (a.fecha < b.fecha ? 1 : a.fecha > b.fecha ? -1 : 0));

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** «2026-08-14» → «14 ago 2026». Sin Date/Intl: evita el desfase de zona horaria y la variante «sept». */
export function fechaLarga(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${Number(d)} ${MESES[Number(m) - 1]} ${y}`;
}
