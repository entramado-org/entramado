import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { parse } from "yaml";

/* Smoke del sitio estático (spec 077) contra out/. Los conteos salen de los mismos YAML de datos/ con
 * los que se construyó. */
const DATOS = process.env.DATOS_DIR ?? path.join(__dirname, "..", "..", "datos");
const leer = (f: string, k: string) => parse(readFileSync(path.join(DATOS, f), "utf-8"))[k] as any[];
const ITEMS = leer("feed.yaml", "items") as { iso: string; titulo: string; medio: string; url: string }[];
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const DOCUMENTOS = leer("documentos.yaml", "documentos") as { url: string }[];

const PAGINAS = ["/", "/ultima-hora", "/documentos", "/contribuir", "/aviso-legal", "/institucional"];
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
  "/caso/caso-leire",
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

test("la portada: qué es, últimos ítems y enlaces a cronología, documentos, institucional y contacto", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("main")).toContainText("No redacta ni afirma nada por su cuenta");
  await expect(page.getByTestId("item")).toHaveCount(10);
  for (const href of ["/ultima-hora", "/documentos", "/institucional", "/contribuir"]) {
    await expect(page.locator(`main a[href="${href}"]`).first()).toBeVisible();
  }
});

test("/ultima-hora es una sola lista por fecha, sin agrupar ni filtrar", async ({ page }) => {
  await page.goto("/ultima-hora");
  await expect(page.locator("main ol")).toHaveCount(1);
  await expect(page.locator("main button, main select, main h2, main h3")).toHaveCount(0);
  const fechas = await page.getByTestId("item").locator("time").evaluateAll((ts) => ts.map((t) => t.getAttribute("datetime")!));
  expect(fechas).toEqual([...fechas].sort().reverse());
});

test("ninguna página enlaza a /caso/ ni habla de página de caso", async ({ page }) => {
  for (const ruta of PAGINAS) {
    await page.goto(ruta);
    await expect(page.locator('a[href^="/caso"]'), ruta).toHaveCount(0);
    await expect(page.locator("body"), ruta).not.toContainText(/página de su caso/i);
  }
});

test("«no redacta ni afirma» nunca aparece sin la excepción de la sección institucional", async ({ page }) => {
  const FRASE = /no redacta ni afirma/gi;
  let apariciones = 0;
  for (const ruta of PAGINAS) {
    await page.goto(ruta);
    const textos = await page.evaluate(() => {
      const meta = (sel: string) => document.querySelector(sel)?.getAttribute("content") ?? "";
      return [
        document.body.innerText,
        meta('meta[name="description"]'),
        meta('meta[property="og:description"]'),
        ...[...document.querySelectorAll('script[type="application/ld+json"]')].map((n) => n.textContent ?? ""),
      ];
    });
    for (const texto of textos) {
      for (const m of texto.matchAll(FRASE)) {
        apariciones++;
        const cola = texto.slice(m.index!, m.index! + 200).replace(/\s+/g, " ");
        expect(cola, `${ruta}: «${cola}»`).toMatch(/salvo en la sección institucional, que es análisis y opinión propios/i);
      }
    }
  }
  expect(apariciones).toBeGreaterThan(0); // la comprobación no es vacía
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
    await expect(main).not.toContainText(/casos abiertos|página de su caso/i);
  }
});

test("el sitemap solo lista páginas que existen y robots no bloquea nada", async ({ request }) => {
  const sitemap = await (await request.get("/sitemap.xml")).text();
  const urls = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => new URL(m[1]).pathname);
  expect(urls.sort()).toEqual([...PAGINAS].sort());
  const robots = await (await request.get("/robots.txt")).text();
  expect(robots).not.toMatch(/disallow:\s*\S/i);
});

test("contacto fija el criterio de entrada y el aviso legal admite las sentencias ya dictadas", async ({ page }) => {
  await page.goto("/contribuir");
  await expect(page.locator("main")).toContainText("parte del procedimiento o cargos públicos, y sin motes");
  await expect(page.locator("main")).not.toContainText("que es del caso");
  await page.goto("/aviso-legal");
  await expect(page.locator("main")).toContainText("sentencias ya dictadas");
  await expect(page.locator("main")).toContainText("cuyo titular se muestra");
});

test("/institucional no trata la amnistía ni nombra personas con causa", async ({ page }) => {
  await page.goto("/institucional");
  await expect(page.locator("body")).not.toContainText(/amnist|junts|procés|carnet|hermano del presidente|Griñán|Chaves|Puigdemont/i);
  await expect(page.locator("#amnistia")).toHaveCount(0);
});
