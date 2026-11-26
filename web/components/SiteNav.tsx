import Link from "next/link";
import { SITE_NAME } from "@/lib/site";

const ENLACES = [
  { href: "/ultima-hora", label: "Última hora" },
  { href: "/documentos", label: "Documentos" },
  { href: "/contribuir", label: "Contacto" },
];

export default function SiteNav() {
  return (
    <nav aria-label="Principal" className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-6 pt-6">
      <Link href="/" className="text-sm font-semibold tracking-tight text-slate-100 hover:text-accent">
        {SITE_NAME}
      </Link>
      <ul className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-slate-400">
        {ENLACES.map((e) => (
          <li key={e.href}>
            <Link href={e.href} className="hover:text-accent">
              {e.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
