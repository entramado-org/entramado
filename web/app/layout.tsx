import type { Metadata, Viewport } from "next";
import "./globals.css";
import JsonLd from "@/components/seo/JsonLd";
import SiteFooter from "@/components/SiteFooter";
import SiteNav from "@/components/SiteNav";
import { SITE_URL } from "@/lib/site";

const TITLE = "El Entramado — Titulares y documentos oficiales de causas judiciales de relevancia política en España";
const DESCRIPTION =
  "Los titulares que la prensa ha publicado sobre causas judiciales de relevancia política en España y los " +
  "documentos oficiales relacionados, siempre enlazados a su fuente. El sitio republica: no redacta " +
  "ni afirma nada por su cuenta, salvo en la sección institucional, que es análisis y opinión propios.";

export const metadata: Metadata = {
  // Absolute base for every relative canonical/OG url below; entramado.org is the canonical host.
  metadataBase: new URL(SITE_URL),
  title: {
    default: TITLE,
    template: "%s — El Entramado",
  },
  // NB: no `alternates.canonical` here — a root-layout canonical is inherited
  // by every page that doesn't override it, silently pointing them all at "/".
  // Each page declares its own canonical instead.
  description: DESCRIPTION,
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "website",
    locale: "es_ES",
    siteName: "El Entramado",
    url: "/",
  },
};

// Zoom is left enabled (accessibility — never disable it).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
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
          }}
        />
        <SiteNav />
        {children}
        <SiteFooter />
      </body>
    </html>
  );
}
