import { ExternalLink } from "lucide-react";
import { fechaLarga, type Item } from "@/lib/datos";

/* Cronología de ítems (spec 077): fecha · medio · titular enlazado a la pieza. Nada más: ni resumen,
 * ni cita, ni estado procesal, ni enlaces a personas. El titular es el de la pieza enlazada. */
export default function ItemList({ items }: { items: Item[] }) {
  return (
    <ol className="relative border-l border-white/10 pl-6">
      {items.map((it) => (
        <li key={it.url} data-testid="item" className="mb-6">
          <span
            className="absolute -left-[7px] mt-1 h-3 w-3 rounded-full"
            style={{ background: "#fb923c", boxShadow: "0 0 10px #fb923c" }}
          />
          <p className="flex flex-wrap items-center gap-x-2 text-xs">
            <time dateTime={it.iso} className="font-medium tracking-wide text-[#fdba74]">
              {fechaLarga(it.iso)}
            </time>
            <span aria-hidden className="text-slate-600">
              ·
            </span>
            <span className="text-slate-500">{it.medio}</span>
          </p>
          <a
            href={it.url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1 inline-flex items-baseline gap-1.5 break-words text-[17px] font-semibold leading-snug text-slate-100 hover:text-accent"
          >
            <span>{it.titulo}</span>
            <ExternalLink className="h-3.5 w-3.5 shrink-0 self-center text-slate-500" />
          </a>
        </li>
      ))}
    </ol>
  );
}
