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
  await expect(page.locator("main")).toContainText("formalmente investigadas, acusadas o condenadas");
  await expect(page.locator("main")).toContainText("cargos y funcionarios públicos por su actuación oficial");
  await expect(page.locator("main")).toContainText("que no sean ellos mismos investigados, acusados o condenados, ni titulares con motes");
  await expect(page.locator("main")).not.toContainText("que es del caso");
  await page.goto("/aviso-legal");
  await expect(page.locator("main")).toContainText("sentencias ya dictadas");
  await expect(page.locator("main")).toContainText("cuyo titular se muestra");
});

test("el enlace a una pieza de agencia se describe como «se leyó en otro medio», sin afirmar nada de la agencia", async ({ page }) => {
  for (const ruta of ["/ultima-hora", "/aviso-legal"]) {
    await page.goto(ruta);
    await expect(page.locator("main")).toContainText("se leyó en otro medio");
    await expect(page.locator("main")).not.toContainText(/no la publica en abierto/i);
  }
});

test("el alcance se describe sin «los casos que rodean al Gobierno» y el JSON-LD no declara un publisher", async ({ page }) => {
  for (const ruta of PAGINAS) {
    await page.goto(ruta);
    await expect(page.locator("body"), ruta).not.toContainText(/rodean al Gobierno/i);
    const head = await page.evaluate(() =>
      [
        document.title,
        ...[...document.querySelectorAll("meta[name=description], meta[property='og:title'], meta[property='og:description']")].map((m) => m.getAttribute("content") ?? ""),
        ...[...document.querySelectorAll('script[type="application/ld+json"]')].map((n) => n.textContent ?? ""),
      ].join("\n"),
    );
    expect(head, ruta).not.toMatch(/rodean al Gobierno/i);
    expect(head, ruta).not.toMatch(/"publisher"|"Organization"/);
  }
});

/* /institucional: lista cerrada de personas físicas y de vocabulario. Las únicas personas que pueden
 * aparecer son las de PERSONAS_PERMITIDAS; cualquier otra secuencia de palabras con mayúscula inicial
 * («Nombre Apellido») debe estar hecha solo de palabras de VOCABULARIO (instituciones, normas, obras y
 * medios que cita la página). Si se añade texto con una palabra nueva, el test falla y obliga a
 * revisar si es una persona antes de ampliar el vocabulario. */
const PERSONAS_PERMITIDAS = [
  "Patrick Breyer", // exeurodiputado, citado como fuente de una filtración sobre Chat Control
  "Conde-Ruiz, García, Puch y Ruiz", // autores del estudio de Fedea que cita la sección de mercado laboral
];
const VOCABULARIO = new Set(
  `AIReF Activa Administración Ageing Agencia Agenda Al Anual Art Asuntos BCE Back Banco Better Boletines Boletín
Bruselas Buen CCAA CE CGPJ CSAR CTBG Cada Central Chat Comercio Comisión Confederación Conflictos Congreso Consejo
Constitucional Constitución Continua Control Crecimiento Criminal Cuentas Cuerpos Cultura Cámara Dato De Decreto
Derecho Digital EU El Electoral Empleados Employment En Encuesta Enjuiciamiento Entes España Española Estado Estados
Estadística Estadístico Estatal Estatuto Eurocámara Europa Europe Europea European Europeo Europeos Eurostat Fiscal
Fiscalía Fuerzas Función Fundación GRECO Gasto General Gobierno Grupo Hacienda Hay Hiperregulación IGAE INE IPC IRPF
Identity Inclusión Indicadores Infobae Informe Intereses Interior Inventario Investigate Judicial Justicia LOPJ LOREG
La Ley Libre Los M Maldita Media Migraciones Ministerio Ministros Más OCDE Objective Observatorio Oficial Oficina
Orgánica Orgánico Outlook PIB Parados Parlamento Parliament Penal Pleno Población Poder Practices Precios Presidente
Páginas Pública Público RDL RPT Real Reales Recaudación Reglas Regulation Report Resoluciones Rights Roll Régimen Salto
Sección Sector Security Seguridad Según Social Suelo Suiza Supremo Sí Síntesis Taxing The Transferencias Transparencia
Tratado Tribunal Tributaria Tu UE Un Unión Urbana Vivienda Vozpópuli Wages Wallet Web Will Índice`.split(/\s+/),
);
const PALABRA = "[A-ZÁÉÍÓÚÑ][\\wáéíóúñü]*(?:-[A-ZÁÉÍÓÚÑ][\\wáéíóúñü]*)?";
const SECUENCIA = new RegExp(`${PALABRA}(?:(?:\\s+(?:de la|del|de|von der|von|van der|van|der|y|e))?\\s+${PALABRA})+`, "g");

/** Palabras con mayúscula, dentro de secuencias de dos o más, que no están en el vocabulario. */
function palabrasDesconocidas(texto: string): string[] {
  let limpio = texto;
  for (const p of PERSONAS_PERMITIDAS) limpio = limpio.split(p).join(" ");
  const fuera = new Set<string>();
  for (const m of limpio.matchAll(SECUENCIA)) {
    for (const palabra of m[0].split(/[\s-]+/)) {
      if (/^[A-ZÁÉÍÓÚÑ]/.test(palabra) && !VOCABULARIO.has(palabra)) fuera.add(palabra);
    }
  }
  return [...fuera].sort();
}

test("/institucional solo nombra a las personas permitidas y no trata causas ni sus detalles", async ({ page }) => {
  await page.goto("/institucional");
  const texto = await page.locator("main").evaluate((m) => {
    const w = document.createTreeWalker(m, NodeFilter.SHOW_TEXT);
    const t: string[] = [];
    for (let n = w.nextNode(); n; n = w.nextNode()) t.push(n.textContent ?? "");
    return t.join(" ");
  });
  expect(texto).not.toMatch(/pfizer|qatar|\bsms\b|soborn|imputa|prisión provisional|amnist|junts|procés|carnet|chistera|hermano del presidente/i);
  expect(palabrasDesconocidas(texto)).toEqual([]);
});

test("el detector de nombres de /institucional caza un nombre y deja pasar a las personas permitidas", () => {
  expect(palabrasDesconocidas("según Pedro Sánchez y Patrick Breyer (Conde-Ruiz, García, Puch y Ruiz, 2023)")).toEqual(["Pedro", "Sánchez"]);
  expect(palabrasDesconocidas("Ursula von der Leyen presidió el Consejo de Ministros")).toEqual(["Leyen", "Ursula"]);
  expect(palabrasDesconocidas("el Banco de España y la Comisión Europea")).toEqual([]);
});
