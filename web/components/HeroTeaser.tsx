"use client";

/**
 * Teaser de grafo auto-navegable para la portada.
 *
 * Renderiza el snapshot curado (`lib/teaserData.ts`) en un <canvas> propio con
 * su bucle rAF. La física está APAGADA — las posiciones vienen horneadas del
 * generador — así que en vez de montar el motor de fuerzas del explorador (peso
 * innecesario en la página más visitada) animamos a mano una GIRA: la cámara
 * vuela de una figura a otra y, en cada parada, revela primero el vecindario a
 * 1 salto y luego a 2 saltos. La coherencia visual con el explorador viene de
 * reutilizar los mismos helpers de estilo (color/radio/relColor/TYPE_META).
 *
 * Todo se mueve por INTERPOLACIÓN hacia objetivos (cámara y opacidad por nodo),
 * de modo que ninguna transición es brusca. Respeta prefers-reduced-motion y se
 * pausa cuando la tarjeta sale del viewport o el ratón está encima.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { TEASER } from "@/lib/teaserData";
import { nodeColor, nodeRadius, relColor, TYPE_META } from "@/lib/graphStyle";

type Cam = { x: number; y: number; k: number };
interface Phase {
  center: string;
  name: string;
  dur: number;
  // Cámara: o fija (aproximación de lejos) o encuadre de un conjunto de ids.
  fixed?: Cam;
  fitIds?: string[];
  // Opacidad objetivo por nodo revelado en esta fase (el resto → ambiente).
  reveal: Map<string, number>;
}

const AMBIENT = 0.08; // opacidad de fondo del entramado no enfocado
const EDGE_FACTOR = 0.55; // los enlaces van más tenues que los nodos
const CAM_TAU = 360; // ms — constante de tiempo del paneo/zoom
const ALPHA_TAU = 260; // ms — constante de tiempo del fundido de nodos

const NODE_BY_ID = new Map(TEASER.nodes.map((n) => [n.id, n]));

/** Construye la secuencia de fases de la gira desde el snapshot. Por parada:
 *  aproximación (de lejos) → 1 salto → 2 saltos → mantener. */
function buildPhases(): Phase[] {
  const phases: Phase[] = [];
  for (const stop of TEASER.tour) {
    const c = NODE_BY_ID.get(stop.center);
    if (!c) continue;
    const ring1 = stop.ring1.filter((id) => NODE_BY_ID.has(id));
    const ring2 = stop.ring2.filter((id) => NODE_BY_ID.has(id));

    const approach = new Map<string, number>([[stop.center, 1]]);
    const withRing1 = new Map(approach);
    ring1.forEach((id) => withRing1.set(id, 1));
    const withRing2 = new Map(withRing1);
    ring2.forEach((id) => withRing2.set(id, 0.62)); // anillo exterior atenuado

    phases.push({ center: stop.center, name: stop.name, dur: 1500, fixed: { x: c.x, y: c.y, k: 0.5 }, reveal: approach });
    phases.push({ center: stop.center, name: stop.name, dur: 1400, fitIds: [stop.center, ...ring1], reveal: withRing1 });
    phases.push({ center: stop.center, name: stop.name, dur: 1600, fitIds: [stop.center, ...ring1, ...ring2], reveal: withRing2 });
    phases.push({ center: stop.center, name: stop.name, dur: 1900, fitIds: [stop.center, ...ring1, ...ring2], reveal: withRing2 });
  }
  return phases;
}

function fitCam(ids: string[], w: number, h: number): Cam {
  const pts = ids.map((id) => NODE_BY_ID.get(id)!).filter(Boolean);
  if (!pts.length) return { x: 0, y: 0, k: 0.6 };
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const p of pts) {
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  }
  const pad = 150;
  const bw = Math.max(maxX - minX, 60) + pad;
  const bh = Math.max(maxY - minY, 60) + pad;
  const k = Math.min(w / bw, h / bh);
  return { x: (minX + maxX) / 2, y: (minY + maxY) / 2, k: Math.max(0.32, Math.min(2.2, k)) };
}

export default function HeroTeaser() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const phases = useMemo(buildPhases, []);
  const [activeName, setActiveName] = useState(phases[0]?.name ?? "");
  const [activeCenter, setActiveCenter] = useState(phases[0]?.center ?? "");

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap || !phases.length) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    let W = 0, H = 0;
    const cam: Cam = { x: 0, y: 0, k: 0.5 };
    const camT: Cam = { x: 0, y: 0, k: 0.5 };
    const alpha = new Map<string, number>(); // opacidad actual por nodo
    const alphaT = new Map<string, number>(); // opacidad objetivo
    TEASER.nodes.forEach((n) => { alpha.set(n.id, AMBIENT); alphaT.set(n.id, AMBIENT); });

    let pi = -1;
    let phaseElapsed = 0;
    let raf = 0;
    let last = 0;
    let visible = true;
    let hovered = false;
    let started = false;
    let running = false; // evita bucles rAF concurrentes
    let alive = true; // false tras el cleanup — corta callbacks async huérfanos (StrictMode)

    const paused = () => hovered;

    function resize() {
      const r = wrap!.getBoundingClientRect();
      W = Math.max(1, r.width);
      H = Math.max(1, r.height);
      canvas!.width = Math.round(W * dpr);
      canvas!.height = Math.round(H * dpr);
      canvas!.style.width = `${W}px`;
      canvas!.style.height = `${H}px`;
    }

    function enterPhase(idx: number) {
      const p = phases[idx];
      // objetivos de opacidad: todo a ambiente salvo lo revelado por la fase
      TEASER.nodes.forEach((n) => alphaT.set(n.id, AMBIENT));
      p.reveal.forEach((v, id) => alphaT.set(id, v));
      // objetivo de cámara
      const target = p.fixed ?? fitCam(p.fitIds ?? [p.center], W, H);
      camT.x = target.x; camT.y = target.y; camT.k = target.k;
      setActiveName(p.name);
      setActiveCenter(p.center);
    }

    function ease(cur: number, tgt: number, dt: number, tau: number) {
      return cur + (tgt - cur) * (1 - Math.exp(-dt / tau));
    }

    function draw() {
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx!.clearRect(0, 0, W, H);
      // transform mundo→pantalla
      ctx!.save();
      ctx!.translate(W / 2, H / 2);
      ctx!.scale(cam.k, cam.k);
      ctx!.translate(-cam.x, -cam.y);

      // enlaces (opacidad derivada de sus extremos → se escalonan solos)
      ctx!.lineWidth = 0.8;
      for (const l of TEASER.links) {
        const s = NODE_BY_ID.get(l.source);
        const t = NODE_BY_ID.get(l.target);
        if (!s || !t) continue;
        const a = Math.min(alpha.get(l.source) ?? 0, alpha.get(l.target) ?? 0) * EDGE_FACTOR;
        if (a < 0.015) continue;
        ctx!.globalAlpha = a;
        ctx!.strokeStyle = relColor(l.type);
        ctx!.beginPath();
        ctx!.moveTo(s.x, s.y);
        ctx!.lineTo(t.x, t.y);
        ctx!.stroke();
      }

      // nodos
      for (const n of TEASER.nodes) {
        const a = alpha.get(n.id) ?? 0;
        if (a < 0.02) continue;
        const r = nodeRadius(n.degree);
        ctx!.globalAlpha = a;
        ctx!.fillStyle = nodeColor(n.type);
        ctx!.beginPath();
        ctx!.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx!.fill();
      }
      ctx!.globalAlpha = 1;
      ctx!.restore();

      // etiquetas + halo en espacio de pantalla (tamaño constante y legible)
      const toScreen = (wx: number, wy: number) => ({
        x: (wx - cam.x) * cam.k + W / 2,
        y: (wy - cam.y) * cam.k + H / 2,
      });
      // Etiqueta solo el vecindario ACTIVO: la figura central (grande) y sus
      // asociados directos a 1 salto (pequeños). Enfoca la atención en la parada
      // actual en vez de rotular las seis figuras a la vez.
      const short = (s: string, n = 22) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
      const activeStop = TEASER.tour.find((t) => t.center === activeCenterRef.current);
      if (activeStop) {
        const c = NODE_BY_ID.get(activeStop.center);
        // asociados directos primero (debajo), luego el centro encima
        for (const id of activeStop.ring1) {
          const n = NODE_BY_ID.get(id);
          if (!n) continue;
          const a = alpha.get(id) ?? 0;
          if (a < 0.62) continue;
          const sc = toScreen(n.x, n.y);
          ctx!.globalAlpha = Math.min(1, (a - 0.55) / 0.4);
          ctx!.font = "500 10px ui-sans-serif, system-ui, sans-serif";
          ctx!.fillStyle = "#cbd5e1";
          ctx!.textAlign = "center";
          ctx!.shadowColor = "rgba(0,0,0,0.9)";
          ctx!.shadowBlur = 5;
          ctx!.fillText(short(n.name, 20), sc.x, sc.y - nodeRadius(n.degree) * cam.k - 5);
          ctx!.shadowBlur = 0;
        }
        if (c) {
          const ca = alpha.get(c.id) ?? 0;
          const sc = toScreen(c.x, c.y);
          const pulse = 0.5 + 0.5 * Math.sin(last / 620);
          const rr = nodeRadius(c.degree) * cam.k + 6 + pulse * 4;
          ctx!.globalAlpha = 0.4 * ca;
          ctx!.strokeStyle = TYPE_META[c.type]?.glow ?? "#5ef2d8";
          ctx!.lineWidth = 1.5;
          ctx!.beginPath();
          ctx!.arc(sc.x, sc.y, rr, 0, Math.PI * 2);
          ctx!.stroke();
          ctx!.globalAlpha = Math.min(1, ca);
          ctx!.font = "600 13px ui-sans-serif, system-ui, sans-serif";
          ctx!.fillStyle = "#f1f5f9";
          ctx!.textAlign = "center";
          ctx!.shadowColor = "rgba(0,0,0,0.9)";
          ctx!.shadowBlur = 6;
          ctx!.fillText(short(c.name, 24), sc.x, sc.y - nodeRadius(c.degree) * cam.k - 8);
          ctx!.shadowBlur = 0;
        }
      }
      ctx!.globalAlpha = 1;
    }

    // ref espejo para que draw() lea el centro activo sin recrear el bucle
    const activeCenterRef = { current: phases[0].center };

    function frame(now: number) {
      const dt = Math.min(50, last ? now - last : 16);
      last = now;

      if (!paused()) {
        phaseElapsed += dt;
        if (pi < 0 || phaseElapsed >= phases[pi].dur) {
          pi = pi < 0 ? 0 : (pi + 1) % phases.length;
          enterPhase(pi);
          phaseElapsed = 0;
        }
      }
      activeCenterRef.current = phases[Math.max(0, pi)].center;

      // interpolación suave hacia los objetivos
      cam.x = ease(cam.x, camT.x, dt, CAM_TAU);
      cam.y = ease(cam.y, camT.y, dt, CAM_TAU);
      cam.k = cam.k * Math.exp(Math.log(Math.max(0.001, camT.k) / Math.max(0.001, cam.k)) * (1 - Math.exp(-dt / CAM_TAU)));
      for (const n of TEASER.nodes) {
        alpha.set(n.id, ease(alpha.get(n.id)!, alphaT.get(n.id)!, dt, ALPHA_TAU));
      }

      draw();
      if (running && alive) raf = requestAnimationFrame(frame);
    }

    function ensureRunning() {
      if (running || reduce || !alive) return;
      running = true;
      last = 0;
      raf = requestAnimationFrame(frame);
    }

    function start() {
      if (started || !alive) return;
      started = true;
      resize();
      if (reduce) {
        // estático: encuadra todo el entramado y revela sin animar
        const all = TEASER.nodes.map((n) => n.id);
        const c = fitCam(all, W, H);
        cam.x = camT.x = c.x; cam.y = camT.y = c.y; cam.k = camT.k = c.k;
        const centers = new Set(TEASER.tour.map((t) => t.center));
        TEASER.nodes.forEach((n) => {
          const v = centers.has(n.id) ? 1 : 0.5;
          alpha.set(n.id, v); alphaT.set(n.id, v);
        });
        draw();
        return;
      }
      // arranca "de lejos" en el primer centro
      const c0 = NODE_BY_ID.get(phases[0].center)!;
      cam.x = camT.x = c0.x; cam.y = camT.y = c0.y; cam.k = camT.k = 0.42;
      ensureRunning();
    }

    function stop() {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      last = 0;
    }

    const ro = new ResizeObserver(() => {
      if (!alive) return;
      resize();
      if (pi >= 0 && !reduce) {
        const p = phases[pi];
        if (!p.fixed) { const tt = fitCam(p.fitIds ?? [p.center], W, H); camT.x = tt.x; camT.y = tt.y; camT.k = tt.k; }
      }
      if (reduce) start();
    });
    ro.observe(wrap);

    const io = new IntersectionObserver(
      (entries) => {
        if (!alive) return;
        visible = entries[0]?.isIntersecting ?? true;
        if (reduce) return;
        if (visible && started) ensureRunning();
        else if (visible && !started) start();
        else stop();
      },
      { threshold: 0.05 },
    );
    io.observe(wrap);

    const onEnter = () => { hovered = true; };
    const onLeave = () => { hovered = false; };
    wrap.addEventListener("pointerenter", onEnter);
    wrap.addEventListener("pointerleave", onLeave);

    return () => {
      alive = false;
      stop();
      ro.disconnect();
      io.disconnect();
      wrap.removeEventListener("pointerenter", onEnter);
      wrap.removeEventListener("pointerleave", onLeave);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phases]);

  return (
    <Link
      href={activeCenter ? `/explore?focus=${encodeURIComponent(activeCenter)}` : "/explore"}
      aria-label="Explorar el grafo interactivo"
      className="group relative block"
    >
      <div
        ref={wrapRef}
        className="glass relative h-[340px] overflow-hidden rounded-2xl border border-white/5 sm:h-[400px] lg:h-[440px]"
      >
        <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" aria-hidden />
        {/* viñeta para fundir los bordes del grafo */}
        <div
          className="pointer-events-none absolute inset-0"
          style={{ background: "radial-gradient(120% 90% at 50% 45%, transparent 55%, rgba(7,10,18,0.9) 100%)" }}
        />
        {/* caption: figura que se está explorando */}
        <div className="pointer-events-none absolute left-4 top-4 flex items-center gap-2">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
          <span className="text-xs font-medium text-slate-300">
            {activeName || "El entramado"}
          </span>
        </div>
        {/* hint de CTA al hacer hover */}
        <div className="pointer-events-none absolute bottom-4 right-4 inline-flex items-center gap-1.5 rounded-lg bg-ink-900/70 px-3 py-1.5 text-xs font-medium text-slate-200 opacity-0 backdrop-blur-sm transition group-hover:opacity-100">
          Explorar el grafo <ArrowRight className="h-3.5 w-3.5" />
        </div>
      </div>
    </Link>
  );
}
