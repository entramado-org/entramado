import type { Metadata } from "next";
import DocumentosBrowser from "@/components/DocumentosBrowser";

export const metadata: Metadata = {
  title: "Documentos",
  description:
    "Busca en el texto íntegro de los autos judiciales, informes de la UCO, transcripciones parlamentarias y auditorías del corpus, y lee el pasaje exacto de cualquier cita.",
  alternates: { canonical: "/documentos" },
};

export default function DocumentosPage() {
  return <DocumentosBrowser />;
}
