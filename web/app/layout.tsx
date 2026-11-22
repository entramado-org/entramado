import type { Metadata, Viewport } from "next";
import "./globals.css";
import JsonLd from "@/components/seo/JsonLd";
import { SITE_URL } from "@/lib/site";

const DESCRIPTION =
  "El grafo de los casos de corrupción que rodean al Gobierno en España — caso " +
  "Koldo, caso Cerdán, Ábalos, Aldama, Begoña Gómez, Plus Ultra, caso Leire: " +
  "personas, empresas y flujos de dinero conectados a partir de autos judiciales " +
  "e informes de la UCO, cada conexión con su cita de fuente.";

export const metadata: Metadata = {
  // Absolute base for every relative canonical/OG url below; entramado.org is
  // the canonical host (the dormant *.ts.net fallback must never index).
  metadataBase: new URL(SITE_URL),
  title: {
    default: "El Entramado — El grafo de los casos de corrupción que rodean al Gobierno en España",
    template: "%s — El Entramado",
  },
  // NB: no `alternates.canonical` here — a root-layout canonical is inherited
  // by every page that doesn't override it, silently pointing them all at "/".
  // Each page declares its own canonical instead.
  description: DESCRIPTION,
  openGraph: {
    title: "El Entramado — El grafo de los casos de corrupción que rodean al Gobierno en España",
    description: DESCRIPTION,
    type: "website",
    locale: "es_ES",
    siteName: "El Entramado",
    url: "/",
  },
};

// Explicit viewport so the graph explorer behaves on phones: device-width +
// viewport-fit=cover enables the env(safe-area-inset-*) padding the bottom
// sheets/header rely on. Zoom is left enabled (accessibility — never disable it).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#070a12", // matches bg-ink-950 so the browser chrome blends in
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" className="dark">
      <body className="min-h-screen">
        <JsonLd
          data={{
            "@context": "https://schema.org",
            "@type": "WebSite",
            name: "El Entramado",
            url: SITE_URL,
            description: DESCRIPTION,
            inLanguage: "es",
            publisher: {
              "@type": "Organization",
              name: "El Entramado",
              url: SITE_URL,
            },
          }}
        />
        {children}
      </body>
    </html>
  );
}
