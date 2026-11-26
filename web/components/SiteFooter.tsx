import Link from "next/link";
import { CONTACTO } from "@/lib/site";

export default function SiteFooter() {
  return (
    <footer className="mx-auto mt-16 max-w-5xl px-6 pb-10">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-white/5 pt-6 text-xs text-slate-600">
        <span>Titulares de medios y documentos oficiales, siempre enlazados a su fuente. Rige la presunción de inocencia.</span>
        <span aria-hidden>·</span>
        <Link href="/aviso-legal" className="hover:text-accent">
          Aviso legal
        </Link>
        <span aria-hidden>·</span>
        <Link href="/contribuir" className="hover:text-accent">
          Rectificaciones
        </Link>
        <span aria-hidden>·</span>
        <a href={`mailto:${CONTACTO}`} className="hover:text-accent">
          {CONTACTO}
        </a>
      </div>
    </footer>
  );
}
