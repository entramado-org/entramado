import type { Metadata } from "next";
import { FileText } from "lucide-react";
import DocumentList from "@/components/DocumentList";
import { DOCUMENTOS } from "@/lib/datos";

export const metadata: Metadata = {
  title: "Documentos oficiales",
  description:
    "Enlaces a documentos oficiales relacionados con los casos, en la web del organismo que los emitió. El sitio no aloja copia de ninguno.",
  alternates: { canonical: "/documentos" },
};

export default function DocumentosPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <header className="mb-8">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
          <FileText className="h-3.5 w-3.5" /> Documentos
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">Documentos oficiales</h1>
        <p className="mt-4 text-lg leading-relaxed text-slate-400">
          Cada documento enlaza a la web del organismo que lo emitió. Este sitio no aloja copia ni texto de
          ninguno: para leerlo hay que ir a su fuente.
        </p>
      </header>

      <DocumentList documentos={DOCUMENTOS} />
    </main>
  );
}
