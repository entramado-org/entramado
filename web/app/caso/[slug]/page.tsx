import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { FolderOpen } from "lucide-react";
import DocumentList from "@/components/DocumentList";
import ItemList from "@/components/ItemList";
import { CASOS, casoPorSlug, documentosDeCaso, itemsDeCaso } from "@/lib/datos";

/* Página de caso (spec 077): el nombre, los ítems de ese caso y sus documentos oficiales. Sin
 * descripción, sin reparto, sin estados. Estática: solo existen los slugs de casos.yaml. */
export const dynamicParams = false;

export function generateStaticParams() {
  return CASOS.map((c) => ({ slug: c.slug }));
}

export async function generateMetadata(props: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await props.params;
  const caso = casoPorSlug(slug);
  if (!caso) return {};
  return {
    title: caso.nombre,
    description: `${caso.nombre}: titulares de prensa enlazados a su medio y documentos oficiales.`,
    alternates: { canonical: `/caso/${caso.slug}` },
  };
}

export default async function CasoPage(props: { params: Promise<{ slug: string }> }) {
  const { slug } = await props.params;
  const caso = casoPorSlug(slug);
  if (!caso) notFound();
  const items = itemsDeCaso(slug);
  const documentos = documentosDeCaso(slug);

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <header className="mb-8">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
          <FolderOpen className="h-3.5 w-3.5" /> Caso
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">{caso.nombre}</h1>
        <p className="mt-4 text-sm text-slate-500">
          Titulares de prensa enlazados a su medio. Rige la presunción de inocencia.
        </p>
      </header>

      <section>
        <h2 className="mb-4 text-xl font-semibold text-slate-100">Titulares</h2>
        {items.length > 0 ? (
          <ItemList items={items} />
        ) : (
          <p className="text-sm text-slate-500">Todavía no hay titulares de este caso.</p>
        )}
      </section>

      {documentos.length > 0 && (
        <section className="mt-12">
          <h2 className="mb-4 text-xl font-semibold text-slate-100">Documentos oficiales</h2>
          <DocumentList documentos={documentos} />
        </section>
      )}
    </main>
  );
}
