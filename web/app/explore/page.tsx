import { Suspense } from "react";
import type { Metadata } from "next";
import GraphExplorer from "@/components/GraphExplorer";

export const metadata: Metadata = {
  title: "Explorador del grafo",
  description:
    "Explora la red de personas, empresas, organismos y causas de los casos de corrupción que rodean al Gobierno en España, con cada conexión anclada a su documento fuente.",
  alternates: { canonical: "/explore" },
};

export default function ExplorePage() {
  return (
    <Suspense fallback={<div className="p-8 text-slate-500">Cargando explorador…</div>}>
      <GraphExplorer />
    </Suspense>
  );
}
