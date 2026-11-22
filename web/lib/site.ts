/* Canonical site identity (spec 035). entramado.org is THE public host; the
 * dormant Tailscale-Funnel hostname serves the same app, so every page emits
 * an absolute canonical to this URL to keep the duplicate host out of Google.
 * Server-side only env (SITE_URL) so a future host change is one compose var. */
export const SITE_URL = process.env.SITE_URL || "https://entramado.org";

export const SITE_NAME = "El Entramado";

export const REPO_URL = "https://github.com/entramado-org/entramado";

/* Interruptor de los enlaces a GitHub: con `false` la web no enlaza al repositorio (los enlaces
 * darían 404 si el repositorio estuviera en privado). */
export const REPO_PUBLIC = true;
