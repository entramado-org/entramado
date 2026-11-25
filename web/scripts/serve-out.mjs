// Servidor estático mínimo de out/ para el smoke e2e (spec 077): misma resolución que un host de
// ficheros estáticos y 404.html con estado 404 para lo que no existe (rutas retiradas incluidas).
// Uso: node scripts/serve-out.mjs [puerto]   (por defecto 3100; build previo: npm run build)
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "out");
const PORT = Number(process.argv[2] ?? process.env.PORT ?? 3100);
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".txt": "text/plain; charset=utf-8",
  ".xml": "application/xml",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

const isFile = (f) => stat(f).then((s) => s.isFile(), () => false);

// Orden: fichero exacto → «.html» → «/index.html». Antes que el directorio: Next deja a la vez
// caso/x.html y un directorio caso/x/ con los segmentos RSC.
async function resolve(pathname) {
  const rel = path.normalize(decodeURIComponent(pathname)).replace(/^(\.\.[/\\])+/, "");
  const base = path.join(OUT, rel);
  if (!base.startsWith(OUT)) return null;
  for (const f of [base, `${base}.html`, path.join(base, "index.html")]) if (await isFile(f)) return f;
  return null;
}

createServer(async (req, res) => {
  const { pathname } = new URL(req.url, "http://localhost"); // la query (?_rsc=…) no cuenta
  const file = await resolve(pathname);
  const status = file ? 200 : 404;
  const target = file ?? path.join(OUT, "404.html");
  try {
    const body = await readFile(target);
    res.writeHead(status, { "content-type": TYPES[path.extname(target)] ?? "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    res.end("404");
  }
}).listen(PORT, () => console.log(`out/ en http://localhost:${PORT}`));
