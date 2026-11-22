import type { Metadata } from "next";
import MoneyFlowsView from "@/components/MoneyFlowsView";

export const metadata: Metadata = {
  title: "Flujos de dinero",
  description:
    "El rastro del dinero del entramado: pagos, cobros y contratos con importe documentado, ordenados de mayor a menor, cada uno con su cita y procedencia.",
  alternates: { canonical: "/flujos" },
};

export default function FlujosPage() {
  return <MoneyFlowsView />;
}
