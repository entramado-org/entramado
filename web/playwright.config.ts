import { defineConfig } from "@playwright/test";

// Sin webServer a propósito: la web se sirve aparte con el build de producción en el host
// (`npm run build:e2e && npm run start:e2e` → :3100; ver CONTRIBUTING.md «Pruebas de navegador»).
// `next dev` no vale para el grafo (StrictMode + useGraphData.merge).
// E2E_BASE_URL=https://entramado.org para el smoke post-deploy.
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3100";

export default defineConfig({
  testDir: "./e2e",
  reporter: "list",
  use: { baseURL, channel: "chrome", launchOptions: { chromiumSandbox: true }, trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 800 } } },
    { name: "mobile-390", use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
});
