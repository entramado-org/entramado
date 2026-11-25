import { Newspaper, ShieldAlert } from "lucide-react";
import ItemList from "@/components/ItemList";
import { ITEMS } from "@/lib/datos";

export const metadata = {
  title: "Última hora",
  description:
    "Cronología de los titulares publicados por la prensa sobre los casos que rodean al Gobierno, con su medio y su fecha, enlazados a la pieza original. Rige la presunción de inocencia.",
  alternates: { canonical: "/ultima-hora" },
};

export default function UltimaHoraPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <header className="mb-8">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
          <Newspaper className="h-3.5 w-3.5" /> Última hora
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">Cronología</h1>
        <p className="mt-4 text-lg leading-relaxed text-slate-400">
          Los titulares que ha publicado la prensa, del más reciente al más antiguo. Cada uno enlaza a la pieza
          original en la web de su medio.
        </p>
      </header>

      <ItemList items={ITEMS} />

      <section className="mt-10 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">Titulares de medios.</span> El titular es el de la pieza
            enlazada, no una afirmación de este sitio: no constituye una declaración de culpabilidad y puede
            haber cambiado en la web del medio. Rige la presunción de inocencia.
          </p>
        </div>
      </section>
    </main>
  );
}
