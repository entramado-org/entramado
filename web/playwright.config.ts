import { defineConfig } from "@playwright/test";

// El sitio es estático (spec 077): el smoke corre contra out/ servido en local con scripts/serve-out.mjs.
// Build previo: `npm run build`. E2E_BASE_URL=https://entramado.org para el smoke post-deploy (sin servidor local).
const externa = process.env.E2E_BASE_URL;
const baseURL = externa ?? "http://localhost:3100";

export default defineConfig({
  testDir: "./e2e",
  reporter: "list",
  use: { baseURL, channel: "chrome", launchOptions: { chromiumSandbox: true }, trace: "retain-on-failure" },
  webServer: externa ? undefined : { command: "node scripts/serve-out.mjs 3100", url: baseURL, reuseExistingServer: false },
  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 800 } } },
    { name: "mobile-390", use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
});
