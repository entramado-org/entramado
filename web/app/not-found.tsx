import Link from "next/link";
import { ArrowLeft } from "lucide-react";

export default function NotFound() {
  return (
    <main className="flex min-h-[70vh] flex-col items-center justify-center px-6 text-center">
      <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-accent">◇ 404</p>
      <h1 className="mt-2 text-4xl font-bold text-slate-50">Página no encontrada</h1>
      <p className="mt-3 max-w-md text-sm leading-relaxed text-slate-400">Esta página no existe.</p>
      <div className="mt-8">
        <Link
          href="/"
          className="inline-flex items-center gap-2 rounded-lg glass px-4 py-2 text-sm text-slate-200 transition hover:bg-white/5"
        >
          <ArrowLeft className="h-4 w-4" /> Volver a la portada
        </Link>
      </div>
    </main>
  );
}
