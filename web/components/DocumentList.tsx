import { ExternalLink } from "lucide-react";
import { fechaLarga, type Documento } from "@/lib/datos";

/* Documentos oficiales (spec 077): organismo · fecha · designación enlazada a la web del organismo.
 * El sitio no aloja ni lee ningún documento. */
export default function DocumentList({ documentos }: { documentos: Documento[] }) {
  return (
    <ul className="divide-y divide-white/5 rounded-2xl glass px-5">
      {documentos.map((d) => (
        <li key={d.url} data-testid="documento" className="py-4">
          <p className="flex flex-wrap items-center gap-x-2 text-xs">
            <span className="font-medium text-slate-300">{d.organismo}</span>
            <span aria-hidden className="text-slate-600">
              ·
            </span>
            <time dateTime={d.fecha} className="text-slate-500">
              {fechaLarga(d.fecha)}
            </time>
          </p>
          <a
            href={d.url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1 inline-flex items-baseline gap-1.5 break-words text-[15px] font-medium leading-snug text-slate-100 hover:text-accent"
          >
            <span>{d.designacion}</span>
            <ExternalLink className="h-3.5 w-3.5 shrink-0 self-center text-slate-500" />
          </a>
        </li>
      ))}
    </ul>
  );
}
