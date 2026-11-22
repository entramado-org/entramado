// Cloudflare Worker — página de mantenimiento para entramado.org (spec 040).
//
// El sitio se sirve desde un Mac vía Cloudflare Tunnel (infra/cloudflared/). Cuando
// el Mac se apaga o pierde internet, `cloudflared` se desconecta y el edge de
// Cloudflare muestra su error 1033 ("Argo Tunnel error"). Este Worker corre en la
// ruta `entramado.org/*` (edge, siempre activo), proxea el tráfico de forma
// transparente cuando el origen responde, y SOLO cuando el túnel/origen está caído
// sustituye la respuesta por una página propia de mantenimiento.
//
// Nota anti-bucle: una subrequest de un Worker a su propia ruta/zona NO re-invoca el
// Worker — va directa al origen —, así que `fetch(request)` no genera recursión.
//
// Deploy / test / rollback: ver README.md de este directorio.

// Origen/túnel inalcanzable = error de gateway.
// El túnel caído se sirve al CLIENTE directo como 1033/HTTP 530, PERO a través de una
// subrequest de este Worker suele llegar como **502** (Bad Gateway) — probado en vivo:
// con el Mac sin internet, el edge devolvía 502, no 530. También cuentan 503/504 y el
// rango 52x de Cloudflare (521/522/523/525/526/530). Los errores APLICATIVOS de la app
// (FastAPI/Next con el Mac encendido) son **500/501** → NO los enmascaramos; cualquier
// 5xx >= 502 es un problema de gateway/túnel y merece la página de mantenimiento.
function isOriginDown(status) {
  return status >= 502;
}

function wantsHtml(request) {
  return (request.headers.get("Accept") || "").includes("text/html");
}

function maintenanceResponse(request) {
  const url = new URL(request.url);
  const isMachine = url.pathname.startsWith("/api") || url.pathname.startsWith("/mcp");
  const common = {
    "Retry-After": "120",
    "Cache-Control": "no-store, no-cache, must-revalidate",
  };

  // Navegadores (y solo rutas humanas) → la página bonita.
  if (!isMachine && wantsHtml(request)) {
    return new Response(MAINTENANCE_HTML, {
      status: 503,
      headers: { ...common, "Content-Type": "text/html; charset=utf-8" },
    });
  }

  // /api, /mcp y clientes no-HTML → JSON claro, más útil que un 1033 para un cliente.
  return new Response(
    JSON.stringify({
      status: "maintenance",
      message:
        "El Entramado está en mantenimiento. Volveremos lo antes posible.",
    }),
    {
      status: 503,
      headers: { ...common, "Content-Type": "application/json; charset=utf-8" },
    },
  );
}

export default {
  async fetch(request) {
    try {
      const response = await fetch(request);
      if (isOriginDown(response.status)) {
        return maintenanceResponse(request);
      }
      return response;
    } catch (err) {
      // El origen ni siquiera resolvió (excepción de red) → mantenimiento.
      return maintenanceResponse(request);
    }
  },
};

// Página autocontenida (CSS inline, sin recursos externos), paleta on-brand tomada de
// web/tailwind.config.ts y web/app/globals.css. Se recarga sola cada 30 s para volver
// al sitio en cuanto el Mac reconecta.
const MAINTENANCE_HTML = `<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta http-equiv="refresh" content="30">
<meta name="robots" content="noindex">
<title>Mantenimiento — El Entramado</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    color: #e2e8f0;
    background-color: #070a12;
    background-image:
      radial-gradient(900px 500px at 12% -10%, rgba(56, 225, 196, 0.10), transparent 60%),
      radial-gradient(800px 600px at 100% 0%, rgba(167, 139, 250, 0.08), transparent 55%);
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100%;
    padding: 24px;
    -webkit-font-smoothing: antialiased;
  }
  .card {
    width: 100%;
    max-width: 30rem;
    background: rgba(10, 14, 26, 0.70);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 18px;
    padding: 40px 34px;
    box-shadow: 0 24px 60px -20px rgba(0, 0, 0, 0.6);
    text-align: center;
  }
  .brand {
    display: inline-flex;
    align-items: center;
    gap: 9px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: #9fb0c3;
    margin-bottom: 26px;
  }
  .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: #38e1c4;
    box-shadow: 0 0 14px rgba(56, 225, 196, 0.9);
    animation: pulse 2.4s ease-in-out infinite;
  }
  @keyframes pulse { 0%, 100% { opacity: 0.55; } 50% { opacity: 1; } }
  @media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
  h1 {
    margin: 0 0 14px;
    font-size: 1.6rem;
    line-height: 1.2;
    color: #f1f5f9;
  }
  p {
    margin: 0 0 12px;
    font-size: 1rem;
    line-height: 1.6;
    color: #b6c2d2;
  }
  .accent { color: #38e1c4; }
  .foot {
    margin: 26px 0 0;
    font-size: 0.82rem;
    color: #6b7a90;
  }
</style>
</head>
<body>
  <main class="card" role="main">
    <div class="brand"><span class="dot"></span> El Entramado</div>
    <h1>Estamos en mantenimiento</h1>
    <p>El sitio no está <span class="accent">disponible</span> en este momento.</p>
    <p>Volveremos lo antes posible. Esta página se actualizará sola cuando el sitio vuelva.</p>
    <p class="foot">El grafo de los casos de corrupción en España</p>
  </main>
</body>
</html>`;
