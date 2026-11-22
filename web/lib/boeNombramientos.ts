/* Evidencia BOE de nombramientos (spec 039) — dataset editorial paralelo a
 * lib/institucional.ts. La clave del Record es el `id` de la <Section> de
 * app/institucional/page.tsx (mismo invariante que Problema.id, spec 007).
 *
 * REGLAS EDITORIALES:
 *  · `fuente` enlaza a la disposición oficial (BOE salvo que se indique otra
 *    fuente primaria, p. ej. la nota corporativa de Correos).
 *  · `procedencia` = SOLO el cargo público u orgánico previo, verificable y en
 *    redacción neutra. Un nombramiento legal con afiliación es un DATO de
 *    nombramiento, no una acusación ni una insinuación de ilegalidad.
 *  · Cada fila debe poder defenderse sola: si no hay disposición enlazable,
 *    no entra. */

export interface NombramientoBoe {
  cargo: string;
  persona: string;
  /** ISO de la disposición (aaaa-mm-dd) — se muestra tal cual si no es ISO. */
  fecha?: string;
  fuente: { ref: string; href: string };
  procedencia?: string;
  nota?: string;
}

export const NOMBRAMIENTOS_BOE: Record<string, NombramientoBoe[]> = {
  fiscalia: [
    {
      cargo: "Fiscal General del Estado",
      persona: "Dolores Delgado García",
      fecha: "2020-02-25",
      fuente: { ref: "RD 410/2020 (BOE-A-2020-2672)", href: "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2020-2672" },
      procedencia: "Ministra de Justicia del mismo Gobierno hasta enero de 2020",
      nota: "Nombrada Fiscal de Sala de Derechos Humanos y Memoria Democrática al cesar (RD 461/2023).",
    },
    {
      cargo: "Fiscal General del Estado",
      persona: "Álvaro García Ortiz",
      fecha: "2022-08-01",
      fuente: { ref: "RD 675/2022 (BOE-A-2022-12946)", href: "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2022-12946" },
      procedencia: "Jefe de la Secretaría Técnica de la FGE con Dolores Delgado",
      nota: "Renovado en el cargo por RD 1182/2023 (BOE-A-2023-26470).",
    },
  ],
  nombramientos: [
    {
      cargo: "Magistrado del Tribunal Constitucional (cuota del Gobierno)",
      persona: "Juan Carlos Campo Moreno",
      fecha: "2022-12-30",
      fuente: { ref: "RD 1092/2022 (BOE-A-2022-24441)", href: "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2022-24441" },
      procedencia: "Ministro de Justicia (2020-2021) del Gobierno que lo designó",
    },
    {
      cargo: "Magistrada del Tribunal Constitucional (cuota del Gobierno)",
      persona: "Laura Díez Bueso",
      fecha: "2022-12-30",
      fuente: { ref: "RD 1093/2022 (BOE-A-2022-24442)", href: "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2022-24442" },
      procedencia: "Directora general en el Gabinete de la Presidencia del Gobierno (Moncloa) hasta su designación",
    },
  ],
  "empleo-publico": [
    {
      cargo: "Presidente del CIS",
      persona: "José Félix Tezanos Tortajada",
      fecha: "2018-06-29",
      fuente: { ref: "RD 771/2018 (BOE-A-2018-9099)", href: "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2018-9099" },
      procedencia: "Miembro de la Ejecutiva Federal del PSOE (secretario de Estudios y Programas) hasta su nombramiento",
    },
    {
      cargo: "Presidente de la Corporación RTVE",
      persona: "José Pablo López Sánchez",
      fecha: "2024-11-28",
      fuente: { ref: "Acuerdo del Pleno del Congreso (BOE-A-2024-25104)", href: "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2024-25104" },
      procedencia: "Designado por la mayoría absoluta del bloque parlamentario de investidura",
    },
    {
      cargo: "Presidente de Correos",
      persona: "Pedro Saura García",
      fuente: { ref: "Correos — nota corporativa (no BOE)", href: "https://www.correos.com/en/sala-prensa/pedro-saura-nuevo-presidente-de-correos/" },
      procedencia: "Exdiputado y ex secretario de Estado de Infraestructuras (PSOE)",
    },
  ],
};

/** ¿Tiene esta sección evidencia BOE? (chip en la card del ProblemMap) */
export function hasBoeEvidence(sectionId: string): boolean {
  return (NOMBRAMIENTOS_BOE[sectionId]?.length ?? 0) > 0;
}
