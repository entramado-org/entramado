import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { parse } from "yaml";

/* Smoke del sitio estático (spec 077) contra out/. Los conteos salen de los mismos YAML de datos/ con
 * los que se construyó. */
const DATOS = process.env.DATOS_DIR ?? path.join(__dirname, "..", "..", "datos");
const leer = (f: string, k: string) => parse(readFileSync(path.join(DATOS, f), "utf-8"))[k] as any[];
const ITEMS = leer("feed.yaml", "items") as { iso: string; titulo: string; medio: string; url: string; caso?: string }[];
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const CASOS = leer("casos.yaml", "casos") as { slug: string; nombre: string }[];
const DOCUMENTOS = leer("documentos.yaml", "documentos") as { url: string; caso?: string }[];

const PAGINAS = [
  "/",
  "/ultima-hora",
  ...CASOS.map((c) => `/caso/${c.slug}`),
  "/documentos",
  "/contribuir",
  "/aviso-legal",
  "/institucional",
];
const RETIRADAS = [
  "/explore",
  "/entidad/x",
  "/investigados",
  "/patrones",
  "/flujos",
  "/briefings",
  "/glosario",
  "/conectar",
  "/caso",
  "/api/ultima-hora",
  "/mcp",
];

/** Errores de consola y excepciones no capturadas; engancharse ANTES de navegar. */
function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

for (const ruta of PAGINAS) {
  test.describe(ruta, () => {
    test("carga sin errores de consola ni overflow horizontal", async ({ page }) => {
      const errors = collectErrors(page);
      const res = await page.goto(ruta);
      expect(res?.status()).toBe(200);
      await expect(page.locator("h1").first()).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      expect(errors).toEqual([]);
    });

    test("sin campo de búsqueda", async ({ page }) => {
      await page.goto(ruta);
      await expect(page.locator("input, textarea, select, [role=search], [type=search]")).toHaveCount(0);
    });

    test("los enlaces internos responden 200", async ({ page }) => {
      await page.goto(ruta);
      const hrefs = await page.$$eval('a[href^="/"]', (as) => [...new Set(as.map((a) => a.getAttribute("href")!.split("#")[0]))]);
      for (const href of hrefs.filter(Boolean)) {
        expect((await page.request.get(href)).status(), href).toBe(200);
      }
    });
  });
}

for (const ruta of RETIRADAS) {
  test(`ruta retirada ${ruta} → 404`, async ({ page }) => {
    const res = await page.goto(ruta);
    expect(res?.status()).toBe(404);
    await expect(page.locator("h1")).toHaveText("Página no encontrada");
  });
}

test("el 404 dice que la página no existe y enlaza a la portada", async ({ page }) => {
  await page.goto("/no-existe");
  await expect(page.locator("main")).toContainText("Esta página no existe.");
  await expect(page.locator("main")).not.toContainText(/cerrad/i);
  await expect(page.locator('main a[href="/"]')).toHaveCount(1);
});

test("/ultima-hora tiene una fila por ítem del feed", async ({ page }) => {
  await page.goto("/ultima-hora");
  await expect(page.getByTestId("item")).toHaveCount(ITEMS.length);
});

test("cada fila es fecha · medio · titular enlazado a la pieza, sin más", async ({ page }) => {
  await page.goto("/ultima-hora");
  const filas = await page.getByTestId("item").evaluateAll((lis) =>
    lis.map((li) => {
      const a = li.querySelector("a")!;
      return {
        texto: (li as HTMLElement).innerText.replace(/\s+/g, " ").trim(),
        enlaces: li.querySelectorAll("a").length,
        href: a.getAttribute("href"),
        target: a.getAttribute("target"),
        rel: a.getAttribute("rel"),
      };
    }),
  );
  expect(filas).toHaveLength(ITEMS.length);
  filas.forEach((f, i) => {
    const it = ITEMS[i];
    const [y, m, d] = it.iso.split("-");
    expect(f.texto).toBe(`${Number(d)} ${MESES[Number(m) - 1]} ${y} · ${it.medio} ${it.titulo}`);
    expect(f.enlaces).toBe(1);
    expect(f.href).toBe(it.url);
    expect(f.target).toBe("_blank");
    expect(f.rel).toBe("noopener noreferrer");
  });
});

test("cada caso muestra solo los ítems de su slug", async ({ page }) => {
  for (const caso of CASOS) {
    await page.goto(`/caso/${caso.slug}`);
    await expect(page.locator("h1")).toHaveText(caso.nombre);
    const esperadas = ITEMS.filter((i) => i.caso === caso.slug).map((i) => i.url);
    const mostradas = await page.getByTestId("item").locator("a").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
    expect([...mostradas].sort()).toEqual([...esperadas].sort());
    const docs = DOCUMENTOS.filter((d) => d.caso === caso.slug);
    await expect(page.getByTestId("documento")).toHaveCount(docs.length);
  }
});

test("la portada: qué es, últimos ítems y los casos con su número de ítems", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("main")).toContainText("No redacta ni afirma nada por su cuenta");
  await expect(page.getByTestId("item")).toHaveCount(10);
  for (const caso of CASOS) {
    const n = ITEMS.filter((i) => i.caso === caso.slug).length;
    await expect(page.locator(`main a[href="/caso/${caso.slug}"]`)).toContainText(`${n} ${n === 1 ? "titular" : "titulares"}`);
  }
  await expect(page.locator('main a[href="/documentos"]').first()).toBeVisible();
  await expect(page.locator('main a[href="/contribuir"]').first()).toBeVisible();
});

test("/documentos lista cada documento con enlace al organismo", async ({ page }) => {
  await page.goto("/documentos");
  await expect(page.getByTestId("documento")).toHaveCount(DOCUMENTOS.length);
  const hrefs = await page.getByTestId("documento").locator("a").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
  expect([...hrefs].sort()).toEqual(DOCUMENTOS.map((d) => d.url).sort());
});

test("contacto y aviso legal explican la rectificación y no mencionan el grafo, el MCP ni GitHub", async ({ page }) => {
  for (const ruta of ["/contribuir", "/aviso-legal"]) {
    await page.goto(ruta);
    const main = page.locator("main");
    await expect(main.locator('a[href="mailto:contacto@entramado.org"]').first()).toBeVisible();
    await expect(main).toContainText(/rectificaci/i);
    await expect(main).toContainText(/retirada|retira/i);
    await expect(main).not.toContainText(/grafo|MCP|GitHub|repositorio/i);
  }
});

test("el sitemap solo lista páginas que existen y robots no bloquea nada", async ({ request }) => {
  const sitemap = await (await request.get("/sitemap.xml")).text();
  const urls = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => new URL(m[1]).pathname);
  expect(urls.sort()).toEqual([...PAGINAS].sort());
  const robots = await (await request.get("/robots.txt")).text();
  expect(robots).not.toMatch(/disallow:\s*\S/i);
});
