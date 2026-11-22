import Link from "next/link";
import {
  ArrowLeft,
  ExternalLink,
  FileText,
  Network,
  Radar,
  ShieldAlert,
  Users,
} from "lucide-react";
import { eur, loadPatrones, tipoMeta, topeVigente, type Patron } from "@/lib/patrones";
import { getIndexables } from "@/lib/fichas";

// force-dynamic: los patrones se sirven en runtime (spec 053) y el mapa
// id→ficha viene del API; congelarlos en el build Docker rompería el circuito.
export const dynamic = "force-dynamic";

export const metadata = {
  title: "Patrones llamativos",
  description:
    "Patrones documentados y llamativos —nada declarado ilegal—, sin foco mediático ni judicial: racimos de contratos menores al filo del umbral, puertas giratorias, capital desproporcionado. Cada hecho citado a su fuente pública. Rige la presunción de inocencia.",
  alternates: { canonical: "/patrones" },
};

/* ────────────────────────────────────────────────────────────────────────────
 * PATRONES LLAMATIVOS (spec 053): capa editorial curada — hechos LEGALES por sí
 * solos que, contados juntos, dibujan un patrón de interés público que no está
 * en ningún documento judicial ni en prensa. Sale del cruce de registros
 * públicos (BORME × PLACSP × BDNS × BOE) con el corpus del proyecto.
 * Fuente de datos: GET /patrones (pipeline/data/patrones/patrones.yaml servido
 * en runtime con validación estricta). NO es capa del grafo.
 * ──────────────────────────────────────────────────────────────────────────── */

function esUmbral(p: Patron): boolean {
  return p.tipos.includes("umbral-menores");
}

function TipoChips({ tipos }: { tipos: string[] }) {
  return (
    <>
      {tipos.map((t) => {
        const m = tipoMeta(t);
        return (
          <span
            key={t}
            className="rounded-full border px-2 py-0.5 text-[10px] font-medium"
            style={{ color: m.color, borderColor: `${m.color}66` }}
          >
            {m.label}
          </span>
        );
      })}
    </>
  );
}

function FuenteRef({ p, fid }: { p: Patron; fid: string }) {
  const f = p.fuentes.find((x) => x.id === fid);
  if (!f) return null;
  if (f.url) {
    return (
      <a
        href={f.url}
        target="_blank"
        rel="noopener noreferrer"
        title={f.acredita}
        className="text-accent/80 hover:underline"
      >
        {f.etiqueta}
      </a>
    );
  }
  return (
    <span title={f.acredita} className="text-slate-400">
      {f.doc}
      {f.pagina ? `, p.${f.pagina}` : ""}
    </span>
  );
}

export default async function PatronesPage() {
  const [patrones, indexables] = await Promise.all([loadPatrones(), getIndexables()]);
  const bySlug = new Map(indexables.map((e) => [e.id, e]));
  return (
    <main className="mx-auto max-w-4xl px-6 py-12">
      {/* nav */}
      <div className="mb-8 flex items-center justify-between">
        <Link href="/" className="inline-flex items-center gap-2 text-sm text-slate-400 hover:text-accent">
          <ArrowLeft className="h-4 w-4" /> Inicio
        </Link>
        <Link
          href="/explore"
          className="inline-flex items-center gap-2 rounded-lg glass px-3 py-1.5 text-sm text-slate-200 hover:bg-white/5"
        >
          <Network className="h-4 w-4" /> Abrir el grafo
        </Link>
      </div>

      {/* header */}
      <header className="mb-8">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
          <Radar className="h-3.5 w-3.5" /> Patrones llamativos
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">
          Llamativo, pero nada declarado ilegal
        </h1>
        <p className="mt-4 text-lg leading-relaxed text-slate-400">
          Al cruzar los registros públicos —mercantil, subvenciones, contratación, BOE— con los
          documentos de los casos, emergen pautas que <span className="text-slate-300">nadie había
          contado juntas</span>: racimos de contratos menores pegados al tope que evita licitación,
          puertas giratorias, capitales que se multiplican. <span className="text-slate-300">Nada de
          esto ha sido declarado ilegal ni está bajo investigación conocida</span>; se documenta aquí,
          hecho a hecho y con su fuente, porque el patrón es de interés público.
        </p>
      </header>

      {/* disclaimer arriba — obligatorio antes del contenido */}
      <section className="mb-10 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">Presunción de inocencia.</span> Contratos
            menores, ampliaciones de capital y fichajes de excargos no han sido declarados ilegales. Nadie de los citados
            está acusado por estos hechos, salvo el estado procesal que conste en su ficha del grafo.
            La señal editorial es el patrón, no el contrato aislado; cada afirmación enlaza a una
            fuente pública verificable (BORME, expediente PLACSP, página de informe). Si un patrón
            salta a prensa o a un juzgado, su estado se anotará aquí.
          </p>
        </div>
      </section>

      {/* índice */}
      {patrones.length > 1 && (
        <nav className="mb-12 grid gap-3 sm:grid-cols-2">
          {patrones.map((p) => (
            <a
              key={p.slug}
              href={`#${p.slug}`}
              className="rounded-xl border border-white/5 bg-white/[0.02] p-4 transition hover:bg-white/[0.05]"
            >
              <div className="mb-1.5 flex flex-wrap gap-1.5">
                <TipoChips tipos={p.tipos} />
              </div>
              <p className="text-sm font-semibold leading-snug text-slate-100">{p.titulo}</p>
            </a>
          ))}
        </nav>
      )}

      {patrones.length === 0 && (
        <p className="text-slate-500">
          Los patrones no están disponibles ahora mismo. Vuelve a intentarlo en unos minutos.
        </p>
      )}

      {/* patrones */}
      {patrones.map((p) => (
        <article key={p.slug} id={p.slug} className="mb-16 scroll-mt-8">
          <header className="mb-5">
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <TipoChips tipos={p.tipos} />
              <span className="rounded-full bg-white/5 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-400">
                {p.estado} · a {p.estado_fecha}
              </span>
            </div>
            <h2 className="text-2xl font-bold leading-snug text-slate-50">{p.titulo}</h2>
            <p className="mt-2 leading-relaxed text-slate-300">{p.tesis}</p>
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
              {p.entities[0] && (
                <Link
                  href={`/explore?focus=${encodeURIComponent(p.entities[0])}`}
                  className="inline-flex items-center gap-1 text-slate-400 hover:text-accent"
                >
                  <Network className="h-3.5 w-3.5" /> Ver en el grafo
                </Link>
              )}
              {p.entities
                .map((id) => bySlug.get(id))
                .filter((e): e is NonNullable<typeof e> => Boolean(e))
                .slice(0, 4)
                .map((e) => (
                  <Link
                    key={e.id}
                    href={`/entidad/${e.slug}`}
                    className="inline-flex items-center gap-1 text-slate-400 hover:text-accent"
                  >
                    <Users className="h-3.5 w-3.5" /> {e.name}
                  </Link>
                ))}
            </div>
            {p.actores_sin_grafo.length > 0 && (
              <p className="mt-2 text-xs leading-relaxed text-slate-500">
                También se menciona (sin nodo en el grafo): {p.actores_sin_grafo.join(" · ")}
              </p>
            )}
          </header>

          {/* hitos */}
          <ol className="relative mb-6 border-l border-white/10 pl-6">
            {p.hitos.map((h, i) => (
              <li key={i} className="mb-6">
                <span
                  className="absolute -left-[7px] mt-1 h-3 w-3 rounded-full"
                  style={{ background: "#38bdf8", boxShadow: "0 0 10px #38bdf866" }}
                />
                <p className="text-xs font-medium uppercase tracking-widest text-sky-300">{h.fecha}</p>
                <p className="mt-1 leading-relaxed text-slate-300">{h.hecho}</p>
                <p className="mt-1 text-xs text-slate-500">
                  Fuente
                  {h.fuentes.length > 1 ? "s" : ""}:{" "}
                  {h.fuentes.map((fid, j) => (
                    <span key={fid}>
                      {j > 0 && " · "}
                      <FuenteRef p={p} fid={fid} />
                    </span>
                  ))}
                </p>
              </li>
            ))}
          </ol>

          {/* contratos itemizados */}
          {p.contratos.length > 0 && (
            <section className="mb-6">
              <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider text-slate-400">
                Contratos itemizados ({p.contratos.length})
              </h3>
              <div className="overflow-x-auto rounded-xl border border-white/5">
                <table className="w-full min-w-[640px] text-left text-[13px]">
                  <thead>
                    <tr className="border-b border-white/10 text-[11px] uppercase tracking-wider text-slate-500">
                      <th className="px-3 py-2 font-medium">Fecha</th>
                      <th className="px-3 py-2 font-medium">Objeto</th>
                      <th className="px-3 py-2 text-right font-medium">Importe (c/IVA)</th>
                      {esUmbral(p) && (
                        <th className="px-3 py-2 text-right font-medium">
                          % del tope ({p.contratos_tope})
                        </th>
                      )}
                      <th className="px-3 py-2 font-medium">Órgano</th>
                      <th className="px-3 py-2 font-medium">Expediente</th>
                    </tr>
                  </thead>
                  <tbody>
                    {p.contratos.map((c, i) => {
                      const pct =
                        esUmbral(p) && c.importe_eur != null
                          ? (c.importe_eur / topeVigente(c.fecha, p.contratos_tope)) * 100
                          : undefined;
                      const alFilo = pct !== undefined && pct >= 95;
                      return (
                        <tr key={i} className="border-b border-white/5 last:border-0">
                          <td className="whitespace-nowrap px-3 py-2 tabular-nums text-slate-400">
                            {c.fecha}
                            {c.fecha_nota && <span title={c.fecha_nota}>†</span>}
                          </td>
                          <td className="px-3 py-2 leading-snug text-slate-300">
                            {c.objeto}
                            {c.via && <span className="text-slate-500"> · {c.via}</span>}
                            {c.nota && (
                              <span className="mt-0.5 block text-[11px] text-amber-300/80">
                                ⚠︎ {c.nota}
                              </span>
                            )}
                          </td>
                          <td
                            className={`whitespace-nowrap px-3 py-2 text-right tabular-nums ${
                              alFilo ? "font-semibold text-red-300" : "text-slate-300"
                            }`}
                          >
                            {eur(c.importe_eur)}
                          </td>
                          {esUmbral(p) && (
                            <td
                              className={`whitespace-nowrap px-3 py-2 text-right tabular-nums ${
                                alFilo ? "font-semibold text-red-300" : "text-slate-500"
                              }`}
                            >
                              {pct !== undefined ? `${pct.toFixed(1)} %` : "—"}
                            </td>
                          )}
                          <td className="px-3 py-2 leading-snug text-slate-500">{c.organo}</td>
                          <td className="whitespace-nowrap px-3 py-2 font-mono text-[11.5px] text-slate-400">
                            {c.url ? (
                              <a
                                href={c.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 hover:text-accent hover:underline"
                              >
                                {c.referencia} <ExternalLink className="h-3 w-3" />
                              </a>
                            ) : (
                              c.referencia
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {p.contratos_nota && (
                <p className="mt-2 text-xs leading-relaxed text-slate-500">{p.contratos_nota}</p>
              )}
            </section>
          )}

          {/* serie resumida (p. ej. los 521 de la EHU) */}
          {p.contratos_resumen && p.contratos_resumen.length > 0 && (
            <section className="mb-6">
              <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider text-slate-400">
                Serie completa, resumida
              </h3>
              <div className="flex flex-wrap gap-2">
                {p.contratos_resumen.map((r) => (
                  <div
                    key={r.periodo}
                    className="rounded-xl border border-white/5 bg-white/[0.02] px-3.5 py-2 text-center"
                  >
                    <p className="text-[11px] uppercase tracking-wider text-slate-500">{r.periodo}</p>
                    <p className="text-lg font-semibold tabular-nums text-slate-200">{r.n_contratos}</p>
                    <p className="text-[11px] tabular-nums text-slate-500">{eur(r.total_eur)}</p>
                  </div>
                ))}
              </div>
            </section>
          )}

          {p.nota_negativos && (
            <p className="mb-6 rounded-xl border border-white/5 bg-white/[0.02] p-4 text-sm leading-relaxed text-slate-400">
              <span className="font-medium text-slate-300">Lo que no aparece también cuenta:</span>{" "}
              {p.nota_negativos}
            </p>
          )}

          {/* fuentes */}
          <section>
            <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-slate-400">
              <FileText className="h-3.5 w-3.5" /> Fuentes (todo público)
            </h3>
            <ul className="space-y-1.5 text-sm">
              {p.fuentes.map((f) => (
                <li key={f.id} className="leading-relaxed text-slate-400">
                  {f.url ? (
                    <a
                      href={f.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-accent/90 hover:underline"
                    >
                      {f.etiqueta} <ExternalLink className="h-3 w-3" />
                    </a>
                  ) : (
                    <Link href="/documentos" className="text-accent/90 hover:underline">
                      {f.etiqueta}
                    </Link>
                  )}{" "}
                  — {f.acredita}
                </li>
              ))}
            </ul>
          </section>

          <p className="mt-6 border-l-2 border-amber-400/30 pl-3 text-xs italic leading-relaxed text-slate-500">
            {p.disclaimer}
          </p>
        </article>
      ))}

      {/* pie metodológico */}
      <section className="mt-4 rounded-2xl glass p-5">
        <p className="text-sm leading-relaxed text-slate-400">
          <span className="font-medium text-slate-200">Método.</span> Estos patrones salen de
          preguntar al grafo por señales que no están en los documentos judiciales y de contrastar
          cada candidato con el registro mercantil (BORME), la Plataforma de Contratación del Sector
          Público (itemización contrato a contrato del barrido estatal 2018–2026) y el corpus del
          proyecto. Lo que no supera el contraste, se descarta. Puedes reproducir cada cifra con las
          fuentes enlazadas, o preguntar por esta capa desde tu propio Claude con el{" "}
          <Link href="/conectar" className="text-accent hover:underline">
            conector MCP
          </Link>
          .
        </p>
      </section>
    </main>
  );
}
