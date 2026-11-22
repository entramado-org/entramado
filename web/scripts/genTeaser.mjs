// Genera el snapshot embebido del teaser de la portada (web/lib/teaserData.ts).
//
// Pega el endpoint de vecindario ya existente para un elenco curado de figuras,
// funde todos los vecindarios en UN grafo (deduplicado), hornea posiciones con
// d3-force (deterministas: centros anclados en un círculo amplio + semilla en
// anillos), y emite un módulo TS versionado. El teaser es decorativo: regenerar
// de vez en cuando y commitear.
//
//   TEASER_API_BASE=https://entramado.org/api node scripts/genTeaser.mjs
//   (default local: http://localhost:8000)

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCollide,
  forceX,
  forceY,
} from "d3-force";

const BASE = (process.env.TEASER_API_BASE || "http://localhost:8000").replace(/\/$/, "");

// Elenco curado — figuras centrales que comparten vecinos, de modo que la gira
// parezca navegar el mismo entramado saltando de un nodo a otro.
// Recorren distintos frentes del caso (Koldo → obra pública → intermediación →
// Plus Ultra/Venezuela → Begoña → trama Leire) para que la cámara vuele entre
// clústeres visualmente distintos aunque enlazados por los nodos-puente.
const CAST = [
  "Person:koldo-garcia",
  "Person:santos-cerdan",
  "Person:victor-de-aldama",
  "Person:jose-luis-rodriguez-zapatero",
  "Person:begona-gomez",
  "Person:leire-diez",
];

const RING1_LIMIT = 7; // vecinos directos revelados en el 1er salto
const RING2_LIMIT = 30; // tamaño del vecindario a 2 saltos por parada

async function fetchNeighborhood(id, hops, limit) {
  const url = `${BASE}/node/${encodeURIComponent(id)}/neighborhood?hops=${hops}&limit=${limit}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API ${res.status} en ${url}`);
  return res.json();
}

// Campos mínimos que necesita el render (mantiene el bundle pequeño).
function slimNode(n) {
  return { id: n.id, name: n.name, type: n.type, degree: n.degree ?? 0 };
}
function linkKey(l) {
  const s = typeof l.source === "string" ? l.source : l.source.id;
  const t = typeof l.target === "string" ? l.target : l.target.id;
  return `${s}|${t}|${l.type}`;
}

async function main() {
  const nodeMap = new Map(); // id -> slim node
  const linkMap = new Map(); // key -> slim link
  const tour = [];

  for (const centerId of CAST) {
    let hop1, hop2;
    try {
      [hop1, hop2] = await Promise.all([
        fetchNeighborhood(centerId, 1, RING1_LIMIT),
        fetchNeighborhood(centerId, 2, RING2_LIMIT),
      ]);
    } catch (e) {
      console.warn(`⚠  omito ${centerId}: ${e.message}`);
      continue;
    }
    const centerNode = hop2.nodes.find((n) => n.id === centerId) || hop1.nodes.find((n) => n.id === centerId);
    if (!centerNode) {
      console.warn(`⚠  omito ${centerId}: el endpoint no devolvió el nodo central`);
      continue;
    }

    // Anillo 1 = vecinos directos (del fetch hops=1), sin el propio centro.
    const ring1 = hop1.nodes.map((n) => n.id).filter((id) => id !== centerId);
    const ring1Set = new Set(ring1);
    // Anillo 2 = resto del vecindario a 2 saltos.
    const ring2 = hop2.nodes.map((n) => n.id).filter((id) => id !== centerId && !ring1Set.has(id));

    // Funde nodos y enlaces en los mapas globales (deduplicado).
    for (const n of hop2.nodes) if (!nodeMap.has(n.id)) nodeMap.set(n.id, slimNode(n));
    for (const l of hop2.links) {
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      const k = linkKey(l);
      if (!linkMap.has(k)) linkMap.set(k, { source: s, target: t, type: l.type, source_kind: l.source_kind ?? null });
    }

    tour.push({ center: centerId, name: centerNode.name, ring1, ring2 });
    console.log(`✓ ${centerNode.name}: anillo1=${ring1.length} anillo2=${ring2.length}`);
  }

  if (tour.length < 2) throw new Error("No se generaron suficientes paradas para la gira");

  // ── Layout d3-force (determinista) ───────────────────────────────────────
  // Los centros de la gira se anclan en un círculo amplio para que sus clústeres
  // no se solapen (la cámara vuela de uno a otro). El resto se siembra cerca del
  // primer centro donde aparece, en anillos, y la simulación los asienta.
  const nodes = [...nodeMap.values()];
  const links = [...linkMap.values()].filter((l) => nodeMap.has(l.source) && nodeMap.has(l.target));

  const centerIndex = new Map(tour.map((t, i) => [t.center, i]));
  const HUB_R = 560; // radio del círculo de centros
  const stopAnchor = tour.map((_, i) => {
    const ang = (i / tour.length) * 2 * Math.PI;
    return { cx: Math.cos(ang) * HUB_R, cy: Math.sin(ang) * HUB_R };
  });
  // Punto de siembra por nodo. Los CENTROS se anclan siempre en SU posición de
  // gira (aunque aparezcan como vecino de un centro anterior — el elenco está muy
  // interconectado); el resto se siembra en el primer centro que lo contiene.
  const seedAnchor = new Map();
  tour.forEach((t, i) => {
    const { cx, cy } = stopAnchor[i];
    seedAnchor.set(t.center, { cx, cy, ring: 0 }); // los centros mandan sobre cualquier siembra previa
  });
  tour.forEach((t, i) => {
    const { cx, cy } = stopAnchor[i];
    t.ring1.forEach((id) => { if (!seedAnchor.has(id)) seedAnchor.set(id, { cx, cy, ring: 1 }); });
    t.ring2.forEach((id) => { if (!seedAnchor.has(id)) seedAnchor.set(id, { cx, cy, ring: 2 }); });
  });

  // Posiciones iniciales deterministas (sin Math.random → layout reproducible).
  let k = 0;
  for (const n of nodes) {
    const a = seedAnchor.get(n.id) || { cx: 0, cy: 0, ring: 2 };
    const r = a.ring === 0 ? 0 : a.ring === 1 ? 90 : 190;
    const ang = (k * 2.399963) % (2 * Math.PI); // ángulo áureo → reparto uniforme
    n.x = a.cx + Math.cos(ang) * r + (a.ring === 0 ? 0 : 1); // +1 evita coincidencia exacta
    n.y = a.cy + Math.sin(ang) * r;
    if (centerIndex.has(n.id)) { n.fx = a.cx; n.fy = a.cy; } // ancla los centros
    k++;
  }

  const sim = forceSimulation(nodes)
    .force("link", forceLink(links).id((d) => d.id).distance(46).strength(0.28))
    .force("charge", forceManyBody().strength(-120))
    .force("collide", forceCollide().radius(16))
    // Gravedad suave hacia el ancla de siembra para que cada clúster no se disperse.
    .force("x", forceX((d) => (seedAnchor.get(d.id)?.cx ?? 0)).strength(0.06))
    .force("y", forceY((d) => (seedAnchor.get(d.id)?.cy ?? 0)).strength(0.06))
    .stop();
  for (let i = 0; i < 400; i++) sim.tick();

  const round = (v) => Math.round(v * 10) / 10;
  const outNodes = nodes.map((n) => ({
    id: n.id,
    name: n.name,
    type: n.type,
    degree: n.degree,
    x: round(n.x),
    y: round(n.y),
  }));
  // forceLink mutó source/target de string a objeto-nodo → volver a ids.
  const idOf = (v) => (typeof v === "string" ? v : v.id);
  const outLinks = links.map((l) => ({
    source: idOf(l.source),
    target: idOf(l.target),
    type: l.type,
    source_kind: l.source_kind ?? null,
  }));

  // ── Emitir el módulo TS ──────────────────────────────────────────────────
  const banner = `// AUTO-GENERADO por scripts/genTeaser.mjs — NO editar a mano.\n// Snapshot curado para el teaser de la portada. Regenerar con:\n//   TEASER_API_BASE=https://entramado.org/api npm run gen:teaser\n// Elenco: ${tour.map((t) => t.name).join(", ")}\n`;
  const body =
    `import type { EntityType } from "./types";\n\n` +
    `export interface TeaserNode { id: string; name: string; type: EntityType; degree: number; x: number; y: number }\n` +
    `export interface TeaserLink { source: string; target: string; type: string; source_kind: string | null }\n` +
    `export interface TeaserStop { center: string; name: string; ring1: string[]; ring2: string[] }\n` +
    `export interface TeaserData { nodes: TeaserNode[]; links: TeaserLink[]; tour: TeaserStop[] }\n\n` +
    `export const TEASER: TeaserData = ${JSON.stringify({ nodes: outNodes, links: outLinks, tour }, null, 0)};\n`;

  const __dirname = dirname(fileURLToPath(import.meta.url));
  const out = join(__dirname, "..", "lib", "teaserData.ts");
  writeFileSync(out, banner + "\n" + body);
  console.log(`\n✓ Escrito ${out}\n  ${outNodes.length} nodos · ${links.length} enlaces · ${tour.length} paradas`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
