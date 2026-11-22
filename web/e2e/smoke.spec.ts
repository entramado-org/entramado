import { expect, test, type Page } from "@playwright/test";

/** Errores de consola y excepciones no capturadas; engancharse ANTES de navegar. */
function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

test("la home renderiza sin errores de consola", async ({ page }) => {
  const errors = collectErrors(page);
  await page.goto("/");
  await expect(page.locator("h1").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("sin overflow horizontal", async ({ page }) => {
  await page.goto("/");
  const ok = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(ok).toBe(true);
});

test("el explorador monta el canvas del grafo con datos", async ({ page, isMobile }) => {
  test.skip(isMobile, "en móvil el explorador abre en la vista de relaciones, sin canvas");
  const overview = page.waitForResponse((r) => r.url().includes("/graph/overview") && r.ok());
  await page.goto("/explore"); // el canvas vive en /explore, no en /entidad/[slug]
  await overview;
  await expect(page.locator("canvas").first()).toBeVisible();
  // Guardia contra el bug latente de useGraphData.merge: con datos, este aviso no aparece.
  await expect(page.getByText("Sin datos. ¿Has ejecutado la ingesta?")).toHaveCount(0);
});
