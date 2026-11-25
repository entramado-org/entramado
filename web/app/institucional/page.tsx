import Link from "next/link";
import {
  Landmark,
  Scale,
  Gavel,
  Vote,
  Users,
  ShieldAlert,
  PiggyBank,
  Home,
  Receipt,
  TrendingDown,
  Banknote,
  Building2,
  FileStack,
  Hourglass,
  HandCoins,
  Briefcase,
  Megaphone,
  ScrollText,
  Handshake,
  EyeOff,
  Globe,
  MessagesSquare,
  Radar,
  Fingerprint,
  Euro,
  FileSearch,
  Crown,
} from "lucide-react";
import { BigStat, StatGrid, BarCompare, TrendStrip, TakeBox } from "@/components/institucional/visuals";
import ProblemMap from "@/components/institucional/ProblemMap";

export const metadata = {
  title: "Contexto institucional",
  alternates: { canonical: "/institucional" },
  description:
    "Mapa interactivo de los problemas del régimen del 78 y del poder sin urnas de Bruselas: nombramientos judiciales, Fiscalía, aforamiento, sistema electoral, democracia directa, decretazo, amnistía, deuda, empleo público, hiperregulación, justicia lenta, partitocracia, transparencia, paro, pensiones, vivienda, impuestos, poder adquisitivo, soberanía en la UE, Pfizergate, Chat Control, identidad digital, euro digital y las cuentas de Bruselas. Crítica desde una óptica de mercado, con cada cifra citada a fuente oficial.",
};

/* ────────────────────────────────────────────────────────────────────────────
 * Esta página NO trata los casos judiciales (eso son la cronología y los documentos del sitio).
 * Arriba va el MAPA DE PROBLEMAS (isla client ProblemMap, datos en
 * lib/institucional.ts: tarjeta = cifra-gancho + explicación llana + anchor a
 * su sección). Debajo, cuatro partes de secciones largas:
 *   1. "Las reglas del juego" — el diseño institucional, con cita a la NORMA.
 *      Aquí la voz es estrictamente descriptiva; las valoraciones se atribuyen
 *      (los TakeBox son la lectura editorial, declarada como tal).
 *   2. "La maquinaria del Estado" — deuda, plantilla, normas, justicia
 *      colapsada, financiación de la partitocracia y transparencia sin dientes.
 *   3. "Tu bolsillo" (#coste, id conservado) — paro, pensiones, vivienda,
 *      impuestos y poder adquisitivo.
 *   4. "El poder sin urnas" (#europa, spec 034) — el nivel europeo: soberanía,
 *      Pfizergate, Chat Control, identidad digital, euro digital, las cuentas
 *      de la UE y los privilegios de la Eurocámara.
 *   Las partes 2 a 4 son crítica abierta desde una óptica liberal / de
 *      mercado (eficiencia e intereses alineados con el ciudadano, no contra los
 *      servicios públicos). El ENCUADRE es declaradamente crítico; el DATO, no:
 *      cada cifra se cita a fuente oficial (INE, AEAT, OCDE, AIReF, Banco de
 *      España, Comisión Europea, Min. de Vivienda, Seguridad Social/IGAE).
 *   En lo judicial (amnistía, Pfizergate, Qatargate) rige la presunción de
 *      inocencia: se distingue lo probado de lo acusado, y las sentencias sobre
 *      transparencia no se presentan como culpabilidad penal.
 *
 * Disciplina que sostiene la credibilidad:
 *   - Dato oficial → como hecho, con enlace.
 *   - Think-tank o fuente con sesgo / privada (Civismo, IEE-CEOE, U. Hespérides,
 *     Fedea, OCU, idealista) → ATRIBUIDO por su nombre en el texto, nunca como
 *     dato neutro.
 *   - Cifra sin fuente única (deuda implícita de pensiones, tax gap, recaudación
 *     nacional de ITP) → NO se publica como número cerrado.
 * Override consciente de la norma "sin panfleto" del 2026-06-02: la interpretación
 * es nuestra y se declara como tal; la prueba sigue siendo irrefutable.
 * ──────────────────────────────────────────────────────────────────────────── */

interface Source {
  ref: string; // cita autoritativa: norma + artículo, o documento
  href: string; // enlace a la fuente oficial
}

function Section({
  id,
  icon: Icon,
  title,
  children,
  sources,
}: {
  id?: string;
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  children: React.ReactNode;
  sources: Source[];
}) {
  return (
    <section id={id} className="scroll-mt-24 border-t border-white/5 py-8 first:border-t-0">
      <h2 className="mb-3 flex items-center gap-2.5 text-xl font-semibold tracking-tight text-slate-100">
        <Icon className="h-5 w-5 shrink-0 text-accent" />
        {title}
      </h2>
      <div className="space-y-3 text-[15px] leading-relaxed text-slate-300">{children}</div>
      <div className="mt-4 rounded-xl border border-white/5 bg-ink-900/40 p-3">
        <p className="mb-1.5 font-mono text-[9px] uppercase tracking-[0.2em] text-slate-600">
          fuentes
        </p>
        <ul className="space-y-1">
          {sources.map((s) => (
            <li key={s.ref} className="text-[12px] leading-snug text-slate-500">
              <a
                href={s.href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent/80 hover:text-accent hover:underline"
              >
                {s.ref}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/* Encabezado de cada una de las dos partes de la página. */
function PartHeader({
  id,
  kicker,
  title,
  intro,
}: {
  id: string;
  kicker: string;
  title: string;
  intro: React.ReactNode;
}) {
  return (
    <header id={id} className="mb-2 mt-16 scroll-mt-20 first:mt-0">
      <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.28em] text-accent/70">
        {kicker}
      </p>
      <h2 className="text-2xl font-bold tracking-tight text-slate-100">{title}</h2>
      <p className="mt-3 text-[15px] leading-relaxed text-slate-400">{intro}</p>
    </header>
  );
}

export default function InstitucionalPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      {/* header */}
      <header className="mb-10">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full glass px-3 py-1 text-xs text-accent">
          <Landmark className="h-3.5 w-3.5" /> Contexto institucional
        </p>
        <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-50">
          El régimen del 78 y su coste
        </h1>
        <p className="mt-4 text-lg leading-relaxed text-slate-400">
          Esta sección no juzga los casos, sino que mira el
          <span className="text-slate-300"> sistema que los enmarca</span>, en cuatro capas:{" "}
          <em>las reglas</em> (cómo se nombran los jueces, de quién depende la Fiscalía,
          cómo se vota y qué NO puedes votar), <em>la maquinaria</em> (deuda, plantilla,
          normas, justicia atascada, partidos subvencionados y opacidad), <em>tu
          bolsillo</em> (paro, pensiones, vivienda, impuestos y el valor real del salario)
          y <em>el poder sin urnas</em> (el nivel europeo: quién decide en Bruselas, qué
          quiere saber de tus mensajes, tu identidad y tu dinero). Empieza por el mapa:
          cada tarjeta lleva a su sección con los datos y las fuentes.
        </p>
        <div className="mt-5 rounded-xl border border-white/5 bg-ink-900/40 p-4">
          <p className="text-[14px] leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">Desde qué óptica.</span> La
            segunda parte es una <span className="text-slate-200">crítica abierta desde
            una perspectiva de mercado</span> —eficiencia e incentivos alineados con el
            ciudadano—. <span className="text-slate-300">No es estar contra los servicios
            públicos</span>: es exigir que funcionen y que el dinero rinda. El encuadre es
            nuestro y se declara como tal; <span className="text-slate-200">cada cifra,
            en cambio, va citada a su fuente oficial</span> (INE, AEAT, OCDE, AIReF, Banco
            de España, Comisión Europea…) y es comprobable. Lo persuasivo no es lo
            incendiario, sino lo irrefutable.
          </p>
        </div>
        <nav className="mt-5 flex flex-wrap gap-2 text-[13px]">
          <a href="#reglas" className="rounded-lg glass px-3 py-1.5 text-slate-300 hover:bg-white/5">
            Las reglas del juego
          </a>
          <a href="#maquinaria" className="rounded-lg glass px-3 py-1.5 text-slate-300 hover:bg-white/5">
            La maquinaria del Estado
          </a>
          <a href="#coste" className="rounded-lg glass px-3 py-1.5 text-slate-300 hover:bg-white/5">
            Tu bolsillo
          </a>
          <a href="#europa" className="rounded-lg glass px-3 py-1.5 text-slate-300 hover:bg-white/5">
            El poder sin urnas
          </a>
        </nav>
      </header>

      {/* mapa de problemas — dashboard interactivo (isla client) */}
      <ProblemMap />

      <PartHeader
        id="reglas"
        kicker="Parte 1 · El diseño"
        title="Las reglas del juego"
        intro={
          <>
            Cómo está construido el poder. <span className="text-slate-300">Cada
            afirmación cita la norma y el artículo concreto</span>, que es la fuente que
            vale; el enlace lleva al texto oficial. Aquí las valoraciones se atribuyen a
            quien las hace —GRECO, Comisión Europea— y no se afirman como propias.
          </>
        }
      />

      <Section
        id="nombramientos"
        icon={Scale}
        title="Separación de poderes y nombramientos judiciales"
        sources={[
          { ref: "Constitución Española, art. 122 (CGPJ) y art. 159 (Tribunal Constitucional)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1978-31229" },
          { ref: "Ley Orgánica 6/1985 del Poder Judicial (LOPJ)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1985-12666" },
          { ref: "GRECO — Grupo de Estados contra la Corrupción (Consejo de Europa), informes sobre España", href: "https://www.coe.int/en/web/greco" },
          { ref: "Comisión Europea — Informe sobre el Estado de Derecho (capítulo España)", href: "https://commission.europa.eu/strategy-and-policy/policies/justice-and-fundamental-rights/upholding-rule-law/rule-law/rule-law-mechanism/rule-law-reports_en" },
        ]}
      >
        <p>
          El <strong>Consejo General del Poder Judicial (CGPJ)</strong> es el órgano de
          gobierno de los jueces. Tiene 20 vocales más su presidente; la Constitución
          (art. 122.3) reserva 12 para jueces y magistrados y 8 para juristas de reconocido
          prestigio, estos últimos elegidos por el Congreso y el Senado por mayoría de tres
          quintos. Desde la reforma de la LOPJ de 1985, en la práctica las Cortes
          intervienen en la designación del conjunto de vocales.
        </p>
        <p>
          El <strong>Tribunal Constitucional</strong> se compone de 12 magistrados
          (art. 159 CE): 4 propuestos por el Congreso y 4 por el Senado —ambos por tres
          quintos—, 2 por el Gobierno y 2 por el propio CGPJ, con mandato de nueve años y
          renovación por tercios.
        </p>
        <p>
          Sobre este modelo, <strong>el GRECO (Consejo de Europa) y la Comisión Europea</strong>{" "}
          han recomendado de forma reiterada que los vocales judiciales del CGPJ sean
          elegidos por los propios jueces, para reforzar la percepción de independencia
          frente a la influencia política. Es una recomendación de esos organismos, no una
          conclusión de este sitio.
        </p>
      </Section>

      <Section
        id="fiscalia"
        icon={Gavel}
        title="¿De quién depende la Fiscalía?"
        sources={[
          { ref: "Constitución Española, art. 124 (Ministerio Fiscal)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1978-31229" },
          { ref: "Ley 50/1981 — Estatuto Orgánico del Ministerio Fiscal", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1982-837" },
          { ref: "Fiscalía General del Estado — sitio oficial", href: "https://www.fiscal.es/" },
        ]}
      >
        <p>
          El <strong>Fiscal General del Estado</strong> es nombrado por el Rey a propuesta
          del Gobierno, oído el Consejo General del Poder Judicial (art. 124.4 CE). El
          Ministerio Fiscal se rige por los principios de <em>unidad de actuación</em> y{" "}
          <em>dependencia jerárquica</em> y, a la vez, de sujeción a la legalidad e
          imparcialidad.
        </p>
        <p>
          La combinación de esos principios con un nombramiento de origen gubernamental
          alimenta un <strong>debate recurrente sobre la autonomía de la Fiscalía</strong>{" "}
          respecto del Ejecutivo. Es un debate público y doctrinal de largo recorrido; aquí
          se constata su existencia y su base normativa, sin tomar partido.
        </p>
      </Section>

      <Section
        id="aforamiento"
        icon={Users}
        title="Aforamiento: cuántos hay"
        sources={[
          { ref: "Constitución Española, arts. 71.3 (diputados y senadores) y 102 (Presidente y miembros del Gobierno)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1978-31229" },
          { ref: "Ley Orgánica 6/1985 del Poder Judicial — aforamiento de jueces, magistrados y fiscales", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1985-12666" },
          { ref: "eldiario.es — «Los 17.600 aforados que hay en España»: desglose de la cifra total", href: "https://www.eldiario.es/politica/aforados-necesita-reducir-numero_1_1935946.html" },
          { ref: "The Objective — «España, paraíso de los aforados»: comparación europea (Francia, Italia)", href: "https://theobjective.com/espana/2025-06-01/espana-paraiso-de-los-aforados/" },
        ]}
      >
        <p>
          El <strong>aforamiento</strong> es la garantía por la que determinados cargos solo
          pueden ser juzgados por un tribunal superior (p. ej. diputados y senadores ante la
          Sala de lo Penal del Tribunal Supremo, art. 71.3 CE). Cubre a cargos políticos
          pero también, por la LOPJ, a jueces, magistrados y fiscales.
        </p>
        <p>
          No existe un registro oficial único, y la cifra que suele circular —«250.000
          aforados»— exige un desglose honesto: <strong>unos 232.000 son miembros de las
          Fuerzas y Cuerpos de Seguridad con un aforamiento parcial</strong> ligado a los
          actos de servicio. El aforamiento institucional propiamente dicho cubre a{" "}
          <strong>unas 17.600 personas</strong> —en torno a 2.000 cargos políticos, unos
          5.500 jueces y magistrados y unos 2.700 fiscales, más otros órganos del Estado—
          según el desglose publicado por eldiario.es. Aun con la cifra depurada, la comparación
          europea sigue siendo elocuente: <strong>Francia afora a menos de 20 personas e
          Italia, en la práctica, a una</strong> (recuento de The Objective). La cuestión de
          fondo no cambia: si tú y un diputado cometéis el mismo delito, no os juzga el
          mismo tribunal.
        </p>
      </Section>

      <Section
        id="sistema-electoral"
        icon={Vote}
        title="El sistema electoral: listas cerradas y valor del voto"
        sources={[
          { ref: "Constitución Española, art. 68 (circunscripción provincial)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1978-31229" },
          { ref: "Ley Orgánica 5/1985 del Régimen Electoral General (LOREG), arts. 162–163 (reparto de escaños y fórmula D'Hondt)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1985-11672" },
        ]}
      >
        <p>
          Al Congreso se vota en <strong>listas cerradas y bloqueadas</strong>: el elector
          escoge una candidatura completa y no puede alterar el orden ni mezclar nombres
          (LOREG). El orden lo fija el partido, lo que refuerza la disciplina interna.
        </p>
        <p>
          La circunscripción es la provincia (art. 68 CE). La LOREG asigna un mínimo de
          escaños por provincia y reparte el resto por población mediante la fórmula{" "}
          <strong>D'Hondt</strong> (arts. 162–163). El efecto combinado es que{" "}
          <strong>un escaño cuesta muchos menos votos en una provincia poco poblada que en
          una grande</strong>: el voto no tiene el mismo peso en Soria que en Madrid o
          Barcelona. Es una consecuencia matemática del diseño, verificable a partir de la
          norma y los resultados oficiales.
        </p>
      </Section>

      <Section
        id="acusacion-popular"
        icon={ShieldAlert}
        title="La acusación popular y su reforma"
        sources={[
          { ref: "Constitución Española, art. 125 (acción popular)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1978-31229" },
          { ref: "Ley de Enjuiciamiento Criminal, arts. 101 y 270 (ejercicio de la acción popular)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1882-6036" },
          { ref: "Ministerio de Justicia — proyecto de Ley Orgánica de Enjuiciamiento Criminal (nueva LECrim, en tramitación)", href: "https://www.mjusticia.gob.es/es/institucional/gabinete-comunicacion/noticias-ministerio/proyecto-lec" },
        ]}
      >
        <p>
          La <strong>acción popular</strong> permite a cualquier ciudadano —y a asociaciones,
          partidos u otras personas jurídicas— personarse como acusación en un proceso penal
          aunque no sean víctimas (art. 125 CE; arts. 101 y 270 LECrim). Es una vía poco
          común en el derecho comparado y ha sido decisiva en varios casos de relevancia
          pública.
        </p>
        <p>
          Ya no son solo propuestas: el Consejo de Ministros aprobó el{" "}
          <strong>proyecto de nueva Ley de Enjuiciamiento Criminal</strong> —en tramitación
          parlamentaria, con entrada en vigor prevista para 2028— que atribuye la
          instrucción a los fiscales (hoy en manos del juez de instrucción) y{" "}
          <strong>acota la acusación popular</strong>: quedan fuera las personas jurídicas
          públicas, los partidos y los sindicatos, y se limita a un catálogo de delitos.
          Según sus impulsores, homologa el modelo al europeo y frena usos abusivos; según
          sus críticos, reduce el control ciudadano sobre la persecución de delitos. Ambas
          lecturas se recogen como posiciones del debate, no como conclusión propia.
        </p>
      </Section>

      <Section
        id="democracia-directa"
        icon={Megaphone}
        title="Democracia directa: lo que un suizo puede y tú no"
        sources={[
          { ref: "Constitución Española, arts. 92 (referéndum consultivo) y 87.3 (iniciativa legislativa popular)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1978-31229" },
          { ref: "Congreso de los Diputados — sinopsis oficial del artículo 92", href: "https://app.congreso.es/consti/constitucion/indice/sinopsis/sinopsis.jsp?art=92&tipo=2" },
          { ref: "Maldita.es — «El saldo de las ILP en 37 años: 143 presentadas, 4 aprobadas» (11-abr-2024)", href: "https://maldita.es/malditodato/20240411/iniciativa-legislativa-popular-aprobadas-presentadas/" },
          { ref: "Civio — «Solo 1 de cada 10 ILP llega a votarse en el Congreso»", href: "https://civio.es/2019/05/30/iniciativas-legislativas-populares/" },
          { ref: "Confederación Suiza (portal oficial) — la democracia directa: referéndum facultativo e iniciativa popular", href: "https://www.aboutswitzerland.eda.admin.ch/es/democracia-directa" },
          { ref: "SWI swissinfo.ch — «Cómo funciona la democracia directa en Suiza»", href: "https://www.swissinfo.ch/spa/democracia-suiza/c%C3%B3mo-funciona-la-democracia-directa-en-suiza/89088930" },
        ]}
      >
        <p>
          En España el ciudadano <strong>no puede frenar ninguna ley</strong>. El referéndum
          del art. 92 CE es <strong>solo consultivo</strong> y únicamente puede convocarlo
          el Rey <em>a propuesta del Presidente del Gobierno</em>, con autorización del
          Congreso: no existe vía para que la ciudadanía lo fuerce. Y la iniciativa
          legislativa popular (art. 87.3 CE) exige <strong>500.000 firmas</strong> para
          simplemente <em>proponer</em> una ley que el Congreso puede archivar — y tiene
          vetadas justo las materias sensibles: leyes orgánicas, tributos, asuntos
          internacionales y la prerrogativa de gracia.
        </p>
        <StatGrid>
          <BigStat value="4" unit="de 143" label="ILP que han prosperado desde 1986" sub="Maldita.es — 2 incorporadas, 2 con enmiendas" tone="rose" />
          <BigStat value="500.000" label="Firmas para proponer una ley (España) — sin efecto vinculante" sub="Art. 87.3 CE — el Congreso decide" tone="amber" />
          <BigStat value="50.000" label="Firmas para forzar referéndum VINCULANTE sobre una ley (Suiza)" sub="Portal oficial suizo — plazo de 100 días" tone="accent" />
          <BigStat value="100.000" label="Firmas para proponer una reforma constitucional a votación (Suiza)" sub="Portal oficial suizo — plazo de 18 meses" tone="accent" />
        </StatGrid>
        <p>
          El contraste suizo no es una anécdota: allí <strong>50.000 firmas en 100 días
          someten cualquier ley aprobada por el Parlamento a votación popular vinculante</strong>{" "}
          (referéndum facultativo), y 100.000 firmas permiten proponer una enmienda
          constitucional que el pueblo vota. El resultado práctico: en Suiza el Parlamento
          legisla sabiendo que el ciudadano puede desautorizarlo; en España, sabiendo que no.
          El saldo español lo documenta Maldita.es: <strong>de 143 ILP presentadas entre
          1986 y 2023, solo 4 salieron adelante</strong>; según Civio, solo una de cada diez
          llegó siquiera a votarse.
        </p>
        <TakeBox>
          <p>
            Esta es la pieza que explica muchas otras de esta página: <strong>sin freno
            ciudadano, la única disciplina del poder son las siguientes elecciones</strong> —
            un cheque en blanco de cuatro años que se paga entero o no se paga. Una ley tan
            divisiva como la <a href="#amnistia" className="text-accent hover:underline">amnistía</a>{" "}
            se aprobó por mayoría simple sin que existiera mecanismo alguno para someterla a
            las urnas; con las reglas suizas, 50.000 firmas habrían bastado para que la
            última palabra la tuvieran los ciudadanos. Quien diseñó el sistema decidió que
            era mejor no preguntarte.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="decretazo"
        icon={ScrollText}
        title="El decreto-ley como forma de gobierno"
        sources={[
          { ref: "Constitución Española, art. 86 (decreto-ley: «extraordinaria y urgente necesidad»)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1978-31229" },
          { ref: "Congreso de los Diputados — estadística oficial de leyes y reales decretos-leyes por legislatura", href: "https://app.congreso.es/est_leyes/" },
          { ref: "Economist & Jurist — «Real Decreto-ley: un abuso permanente y en alza» (655 RDL; 95 en la XIV legislatura)", href: "https://www.economistjurist.es/actualidad-juridica/real-decreto-ley-un-abuso-permanente-y-en-alza/" },
        ]}
      >
        <p>
          El art. 86 CE reserva el decreto-ley para casos de <strong>«extraordinaria y
          urgente necesidad»</strong>: entra en vigor al publicarse, sin debate parlamentario
          previo, y el Congreso solo puede <strong>convalidarlo o derogarlo en bloque</strong>{" "}
          en 30 días — sin enmendar una coma. Es una herramienta de excepción diseñada para
          emergencias.
        </p>
        <p>
          El uso real la ha convertido en vía ordinaria de legislar: el recuento de
          Economist &amp; Jurist sobre los datos del Congreso suma <strong>655 reales
          decretos-leyes</strong> en democracia, con <strong>95 solo en la XIV legislatura
          (2019-2023)</strong> — legislatura en la que los decretos-leyes superaron con
          holgura a las leyes ordinarias tramitadas con debate completo. A eso se añade la
          técnica del RDL «ómnibus», que mezcla materias sin relación para forzar su
          convalidación conjunta — crítica recurrente de la doctrina que esa misma
          publicación recoge.
        </p>
        <StatGrid>
          <BigStat value="95" unit="RDL" label="Reales decretos-leyes en la XIV legislatura (2019-2023)" sub="Economist & Jurist, s/ datos del Congreso" tone="rose" />
          <BigStat value="655" label="RDL acumulados en 15 legislaturas" sub="Economist & Jurist, s/ datos del Congreso" tone="amber" />
          <BigStat value="0" label="Enmiendas admite un decreto-ley: se convalida o se deroga en bloque" sub="Art. 86.2 CE" tone="amber" />
        </StatGrid>
        <TakeBox>
          <p>
            Cada decreto-ley es un trozo de Parlamento amputado: <strong>la norma nace del
            Consejo de Ministros y el debate llega después, si llega</strong>. Cuando la
            «urgencia extraordinaria» se invoca dos veces al mes, deja de ser urgencia y
            pasa a ser método — y el Legislativo queda reducido a un botón de sí o no. La
            separación de poderes no suele morir de un golpe: se erosiona decreto a decreto.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="amnistia"
        icon={Handshake}
        title="La ley de amnistía: delitos borrados a cambio de votos"
        sources={[
          { ref: "BOE — Ley Orgánica 1/2024, de 10 de junio, de amnistía (BOE-A-2024-11776)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-2024-11776" },
          { ref: "Comisión de Venecia (Consejo de Europa) — dictamen CDL-AD(2024)003 sobre la ley de amnistía española (15-mar-2024)", href: "https://www.venice.coe.int/webforms/documents/default.aspx?pdffile=CDL-AD(2024)003-e" },
          { ref: "Newtral — el dictamen de la Comisión de Venecia, explicado (22-mar-2024)", href: "https://www.newtral.es/comision-venecia-amnistia-paises/20240322/" },
          { ref: "Poder Judicial — el Tribunal Supremo declara NO amnistiada la malversación del procés", href: "https://www.poderjudicial.es/cgpj/es/Poder-Judicial/Tribunal-Supremo/Noticias-Judiciales/El-Tribunal-Supremo-declara-no-amnistiado-el-delito-de-malversacion-de-caudales-publicos-en-la-causa-del--proces-" },
          { ref: "TJUE (Gran Sala) — nota de prensa 112/26: «El Derecho de la Unión no se opone a la ley española de amnistía» (asuntos C-523/24 y C-666/24, 16-jul-2026)", href: "https://curia.europa.eu/site/upload/docs/application/pdf/2026-07/cp260112es.pdf" },
          { ref: "COPE — el Tribunal de Luxemburgo dice que la ley de amnistía es conforme al derecho comunitario (16-jul-2026)", href: "https://www.cope.es/actualidad/espana/noticias/tribunal-luxemburgo-dice-ley-amnistia-conforme-derecho-comunitario-20260716_3405719.html" },
        ]}
      >
        <p>
          Los hechos, en orden: en noviembre de 2023 Pedro Sánchez fue investido con los{" "}
          <strong>7 votos de Junts</strong>, cuyo acuerdo público de investidura incluía una
          ley de amnistía para los responsables del procés. En junio de 2024 se aprobó la{" "}
          <strong>Ley Orgánica 1/2024</strong>, que extingue la responsabilidad penal,
          administrativa y contable de los actos vinculados al procés entre 2011 y 2023.
        </p>
        <p>
          La <strong>Comisión de Venecia</strong> —el órgano consultivo del Consejo de
          Europa en materia constitucional, consultado por el Senado— avaló que las
          amnistías puedan existir, pero criticó lo esencial del caso español: recomendó que
          una ley así se apruebe con <strong>mayorías cualificadas</strong>, no por mayoría
          simple; censuró la <strong>tramitación por vía de urgencia</strong> como
          proposición de ley (sin informes de los órganos consultivos); y advirtió de que
          una amnistía debe unir a la sociedad, no profundizar su división. El Tribunal
          Constitucional la avaló en junio de 2025 con el tribunal partido — <strong>votos
          particulares de sus cuatro magistrados conservadores</strong>.
        </p>
        <p>
          El 16 de julio de 2026 llegó el pronunciamiento europeo: la <strong>Gran Sala del
          TJUE declaró que el Derecho de la Unión no se opone a la ley de amnistía</strong>{" "}
          (sentencias C-523/24 y C-666/24, sobre las cuestiones prejudiciales del Tribunal
          de Cuentas y de la Audiencia Nacional). Los intereses financieros de la UE no se
          ven afectados «por la mera disminución de la renta nacional bruta» que pudiera
          derivarse de una secesión — el argumento que sostenía una de las dos excepciones
          que aplicaba el Supremo a la malversación —; la directiva antiterrorista «no queda
          menoscabada»; y el control europeo se limita a problemas «de carácter sistémico»
          del sistema judicial, que el Tribunal no aprecia. Único límite que fija: el plazo
          de dos meses para aplicar la amnistía no puede pasar por encima de cuestiones
          prejudiciales pendientes — si lo hiciera, esas disposiciones «deberían dejarse
          inaplicadas».
        </p>
        <p>
          La pelota vuelve así a los tribunales españoles, que deciden caso a caso conforme
          a esa interpretación: el <strong>Tribunal Supremo</strong>, que mantenía que la
          malversación del procés no era amnistiable (beneficio patrimonial + afectación a
          los intereses financieros de la UE — esta segunda pata, tocada por el fallo), y el{" "}
          <strong>Tribunal Constitucional</strong>.
        </p>
        <TakeBox>
          <p>
            Despejemos el eufemismo de la «normalización institucional»: <strong>los
            beneficiarios de la ley aportaron los votos que la hicieron posible</strong>, y
            los aportaron precisamente a cambio de ella. Que el instrumento sea
            constitucional no lo hace menos corrosivo: si la consecuencia penal de un delito
            depende de cuántos escaños controlen sus autores, la igualdad ante la ley pasa a
            tener excepciones parlamentarias. Y el diseño institucional cierra el círculo:
            como España carece de <a href="#democracia-directa" className="text-accent hover:underline">
            freno ciudadano a las leyes</a>, la única instancia que pudo pronunciarse fue un
            Constitucional <a href="#nombramientos" className="text-accent hover:underline">nombrado
            por los mismos partidos</a> que negociaron la amnistía.
          </p>
          <p>
            El fallo del TJUE no cambia ese diagnóstico — lo consuma. Luxemburgo solo
            respondía a si el Derecho de la UE <em>impide</em> la ley, no a si es sana:
            el propio Tribunal subraya que amnistiar es competencia de los Estados y que su
            control se limita a lo sistémico. Con todos los avales judiciales ya emitidos,
            queda a la vista lo esencial: <strong>ninguna instancia juzgará jamás el fondo
            del intercambio</strong> — delitos borrados a cambio de una investidura. Todo
            fue legal; eso es exactamente lo que denunciamos del diseño.
          </p>
        </TakeBox>
      </Section>

      {/* ════════════════ PARTE 2 — LA MAQUINARIA DEL ESTADO ════════════════ */}
      <PartHeader
        id="maquinaria"
        kicker="Parte 2 · La maquinaria"
        title="La maquinaria del Estado"
        intro={
          <>
            El Estado no es un árbitro neutral: es una organización con incentivos
            propios — endeudarse, crecer, regular y financiar a quien lo gobierna. Una
            lectura crítica de cinco engranajes: deuda, plantilla, normas, justicia y
            partitocracia. El encuadre es nuestro;{" "}
            <span className="text-slate-300">cada cifra está citada a su fuente
            oficial</span>, y las estimaciones de think-tanks van nombradas.
          </>
        }
      />

      <Section
        id="deuda-publica"
        icon={Banknote}
        title="Deuda pública: la factura aplazada"
        sources={[
          { ref: "Banco de España — Deuda de las AAPP según el PDE, avance mensual (abril 2026)", href: "https://www.bde.es/wbe/es/noticias-eventos/actualidad-banco-espana/deuda-aapp-abril26.html" },
          { ref: "Eurostat — estadísticas de déficit y deuda (EDP): serie deuda/PIB e intereses pagados (D41), España", href: "https://ec.europa.eu/eurostat/web/government-finance-statistics/data/main-tables" },
          { ref: "INE — Estadística Continua de Población, 1 de abril de 2026", href: "https://www.ine.es/dyngs/Prensa/ECP1T26.htm" },
        ]}
      >
        <p>
          La deuda pública es la forma más cómoda de gastar: la disfruta el votante de hoy
          y la paga el contribuyente de mañana, que no estaba en la votación. En abril de
          2026 el saldo alcanzó <strong>1,74 billones de euros</strong>, el{" "}
          <strong>101% del PIB</strong> (Banco de España). Y ya no es gratis: en 2025 el
          conjunto de administraciones pagó <strong>40.314 millones de euros solo en
          intereses</strong> (Eurostat, notificación EDP) — la primera vez que se supera la
          barrera de los 40.000 millones.
        </p>
        <StatGrid>
          <BigStat value="1,74" unit="B€" label="Deuda de las AAPP (abril 2026)" sub="Banco de España — PDE" tone="rose" />
          <BigStat value="101" unit="% PIB" label="Más de un año entero de producción nacional" sub="Banco de España" tone="amber" />
          <BigStat value="40.314" unit="M€" label="Intereses pagados en 2025" sub="Eurostat (EDP) — récord de la serie" tone="rose" />
          <BigStat value="~35.000" unit="€" label="Deuda por habitante" sub="Cálculo a partir de BdE (abr. 2026) e INE (49,69 M hab.)" tone="amber" />
        </StatGrid>
        <TrendStrip
          title="Deuda pública, % del PIB — de 2007 a 2025"
          points={[35.7, 89.6, 104.4, 97.7, 119.3, 100.7]}
          labelStart="2007 · 35,7%"
          labelEnd="2025 · 100,7%"
          caption="2007: 35,7% → 2012: 89,6% → 2014: 104,4% → 2019: 97,7% → 2020: 119,3% → 2025: 100,7%. El porcentaje baja desde 2020 porque el PIB nominal crece con la inflación; el importe absoluto sigue marcando máximos."
          source="Fuente: Eurostat, serie EDP de deuda de las AAPP (España)."
        />
        <TakeBox>
          <p>
            La deuda es un <strong>impuesto diferido que nadie votó</strong>: se emite hoy
            y se cobra a quien aún no vota — o no ha nacido. Y los intereses son el
            recordatorio anual: <strong>más de 40.000 millones de tus impuestos que no
            compran ni una consulta médica ni un kilómetro de vía</strong>, solo pagan el
            gasto de ayer. Cada punto de tipo de interés que suba convierte la comodidad
            fiscal de una legislatura en la hipoteca de una generación.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="empleo-publico"
        icon={Building2}
        title="Empleo público y clientelismo: la maquinaria que se reparte"
        sources={[
          { ref: "INE — Encuesta de Población Activa, 1.er trimestre 2026 (empleo público: 3.662.500)", href: "https://www.ine.es/dyngs/Prensa/EPA1T26.htm" },
          { ref: "Inventario de Entes del Sector Público (Invente) — Ministerio de Hacienda / IGAE", href: "https://www.pap.hacienda.gob.es/invente2/pagMenuPrincipalV2.aspx" },
          { ref: "Vozpópuli — «El disparate de los entes públicos: hay más de 19.300», a partir del Inventario de Hacienda", href: "https://www.vozpopuli.com/economia/el-disparate-de-los-entes-publicos-hay-mas-de-19300-y-un-tercio-son-instrumentales.html" },
          { ref: "The Objective — recuento de asesores y personal eventual sobre el Boletín Estadístico de Función Pública y la RPT", href: "https://theobjective.com/espana/politica/2025-01-06/estadistica-oficial-oculta-asesores-cargos-confianza-gobierno/" },
        ]}
      >
        <p>
          En el primer trimestre de 2026 el empleo público alcanzó{" "}
          <strong>3.662.500 personas</strong> (EPA, INE) — el máximo de la serie, según el
          recuento de The Objective sobre esos mismos datos: <strong>+544.700 desde
          2018</strong> (+17,5%). Alrededor de esa plantilla orbita un archipiélago de{" "}
          <strong>más de 19.300 entes públicos</strong> —organismos, sociedades,
          fundaciones, consorcios— según el recuento de Vozpópuli a partir del Inventario
          de Entes del propio Ministerio de Hacienda: 440 estatales, 1.776 autonómicos y
          17.113 locales.
        </p>
        <StatGrid>
          <BigStat value="3,66" unit="M" label="Empleados públicos (EPA, 1T 2026)" sub="INE — máximo de la serie s/ The Objective" tone="amber" />
          <BigStat value="+544.700" label="Más empleados públicos que en 2018 (+17,5%)" sub="The Objective, sobre datos EPA" tone="amber" />
          <BigStat value="19.319" label="Entes públicos inventariados (2025)" sub="Vozpópuli, s/ Inventario de Hacienda" tone="rose" />
          <BigStat value="1.217" label="Asesores y cargos de confianza del Gobierno (1-1-2025)" sub="The Objective, s/ RPT — la estadística oficial recoge 842" tone="rose" />
        </StatGrid>
        <BarCompare
          title="¿Dónde están los 19.319 entes públicos?"
          unit="entes inventariados, 2025 — Vozpópuli s/ Inventario de Hacienda"
          data={[
            { label: "Locales", value: 17113, display: "17.113", highlight: true },
            { label: "Autonómicos", value: 1776, display: "1.776" },
            { label: "Estatales", value: 440, display: "440" },
          ]}
          source="Cada ente añade consejos, sedes y nóminas — y diluye la responsabilidad: cuanto más lejos del presupuesto central, menos foco."
        />
        <TakeBox>
          <p>
            El problema no es el funcionario que aprueba una oposición: es que{" "}
            <strong>el perímetro del Estado se ha vuelto una agencia de colocación</strong>.
            Cada ente nuevo son consejos, asesores y contratos con menos foco encima; cada
            presidencia repartida por carnet convierte una institución de todos en una
            delegación del partido. Y el incentivo se realimenta:{" "}
            <strong>quien debe su nómina al BOE tiende a votar que el BOE crezca</strong>.
            Auditar entes, fusionarlos y nombrar por concurso no es ideología: es higiene
            democrática.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="hiperregulacion"
        icon={FileStack}
        title="Hiperregulación: la avalancha normativa"
        sources={[
          { ref: "CEOE — informe «La producción normativa en 2024» (10.ª edición, nota de prensa)", href: "https://www.ceoe.es/es/ceoe-news/empresa/el-gobierno-aprobo-719-normas-en-2024-un-5-mas-que-el-ano-anterior-pese-la" },
          { ref: "Cámara de Comercio de España — propuestas de simplificación administrativa recogidas por la OCDE (Better Regulation Practices across the EU 2025)", href: "https://www.camara.es/la-ocde-reconoce-el-trabajo-realizado-por-camara-de-espana-para-la-reduccion-de-cargas" },
          { ref: "Agencia Estatal Boletín Oficial del Estado", href: "https://www.boe.es/" },
        ]}
      >
        <p>
          La ley ya no es algo que un ciudadano pueda conocer. Según el informe anual de
          la CEOE sobre producción normativa —los datos son suyos y se citan como tales—,
          en 2024 se aprobaron <strong>719 normas de ámbito estatal</strong>,
          «prácticamente dos normas al día», y el conjunto de boletines oficiales (BOE más
          autonómicos) publicó <strong>1.298.086 páginas</strong>: estar al día exigiría
          leer unas <strong>3.557 páginas diarias</strong>.
        </p>
        <StatGrid>
          <BigStat value="719" label="Normas estatales aprobadas en 2024" sub="CEOE — «dos normas al día»" tone="amber" />
          <BigStat value="1,3" unit="M" label="Páginas publicadas por los boletines oficiales (2024)" sub="CEOE — BOE + autonómicos" tone="amber" />
          <BigStat value="3.557" unit="págs/día" label="Lectura diaria para estar al día de lo publicado" sub="CEOE (cálculo del informe)" tone="rose" />
        </StatGrid>
        <BarCompare
          title="¿De dónde sale el papel oficial? Páginas publicadas en 2024"
          unit="páginas, según CEOE"
          data={[
            { label: "Boletines CCAA", value: 1038641, display: "1.038.641", highlight: true },
            { label: "BOE (estatal)", value: 259445, display: "259.445" },
          ]}
          source="Fuente: CEOE, La producción normativa en 2024. El 80% del volumen es autonómico — 17 reguladores paralelos sobre el mismo ciudadano."
        />
        <p>
          El coste no es teórico: la Cámara de Comercio de España presentó en 2024
          un paquete de 52 propuestas de simplificación administrativa cuyo ahorro estimó
          en <strong>~298 millones de euros</strong> para las empresas — trabajo recogido
          por la OCDE en su informe sobre prácticas regulatorias en la UE. Es una
          estimación de la Cámara, no un dato del INE, y se atribuye como tal.
        </p>
        <TakeBox>
          <p>
            La maraña no es neutral: <strong>es regresiva</strong>. La gran empresa
            absorbe la regulación con un departamento jurídico; el autónomo y la pyme la
            pagan con su tiempo o renunciando a crecer. Y un ordenamiento que nadie puede
            conocer entero convierte la <strong>seguridad jurídica en discrecionalidad</strong>:
            cumplir deja de depender de tu diligencia y pasa a depender de qué inspector y
            qué ventanilla te toquen. Menos normas y más claras no es desregular: es que
            la ley vuelva a ser conocible.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="justicia-lenta"
        icon={Hourglass}
        title="Justicia lenta: el atasco que beneficia al poder"
        sources={[
          { ref: "CGPJ — «La Justicia Dato a Dato. Año 2024» (duraciones estimadas, tasa de congestión, jueces por 100.000 hab.)", href: "https://www.poderjudicial.es/stfls/ESTADISTICA/FICHEROS/JusticaDatoaDato/Justicia%20Dato%20a%20Dato%202024.pdf" },
          { ref: "CGPJ — estadística judicial, datos anuales 2025 (asuntos en trámite a 31-dic-2025)", href: "https://www.poderjudicial.es/cgpj/es/Temas/Estadistica-Judicial/Estudios-e-Informes/Justicia-Dato-a-Dato/" },
          { ref: "CEPEJ (Consejo de Europa) — informe 2024 sobre sistemas judiciales europeos; cifra España vs media recogida por LegalToday (Aranzadi)", href: "https://www.legaltoday.com/actualidad-juridica/noticias-de-derecho/el-consejo-de-europa-mantiene-a-espana-muy-por-encima-en-presupuesto-judicial-por-habitante-pero-por-debajo-en-jueces-2024-10-17/" },
          { ref: "Newtral — acuerdo de renovación del CGPJ tras cinco años y medio caducado (25-jun-2024)", href: "https://www.newtral.es/acuerdo-renovacion-cgpj/20240625/" },
        ]}
      >
        <p>
          La estadística oficial del CGPJ retrata un sistema saturado: al cierre de 2025
          había <strong>4.673.596 asuntos en trámite</strong> (+3,4% en un año, tras el
          salto de medio millón de 2024) y la primera instancia tardó de media{" "}
          <strong>7,5 meses</strong> en 2024 (un 12,5% más que en 2023). Un pleito civil con recurso suma 8,5 meses en primera
          instancia y 13,3 en la Audiencia; en lo contencioso —pleitear contra la
          Administración— la sala del TSJ tarda <strong>19 meses</strong> de media.
        </p>
        <StatGrid>
          <BigStat value="4,67" unit="M" label="Asuntos en trámite al cierre de 2025" sub="CGPJ — estadística judicial" tone="rose" />
          <BigStat value="7,5" unit="meses" label="Duración media en 1.ª instancia (2024)" sub="CGPJ — +12,5% en un año" tone="amber" />
          <BigStat value="19" unit="meses" label="Recurso contencioso en el TSJ: pleitear contra la Administración" sub="CGPJ (duración estimada)" tone="amber" />
          <BigStat value="5,5" unit="años" label="Estuvo caducado el CGPJ (dic. 2018 – jun. 2024)" sub="Newtral / Maldita" tone="rose" />
        </StatGrid>
        <TrendStrip
          title="Asuntos en trámite en todas las jurisdicciones, 2022-2025"
          points={[3393599, 3977112, 4518944, 4673596]}
          labelStart="2022 · 3,39 M"
          labelEnd="2025 · 4,67 M"
          caption="+1,3 millones de asuntos atascados en tres años: cada año entran más casos (7,8 M en 2024; 7,55 M en 2025) de los que se resuelven."
          source="Fuente: CGPJ, La Justicia Dato a Dato 2024 y datos anuales 2025."
        />
        <BarCompare
          title="Jueces por 100.000 habitantes"
          data={[
            { label: "España", value: 11.92, display: "11,9", highlight: true },
            { label: "Media europea", value: 17.43, display: "17,4" },
          ]}
          source="Fuente: CEPEJ (Consejo de Europa), informe 2024 con datos de 2022, recogido por LegalToday. España lleva años «significativamente por debajo» de la media en jueces por habitante."
        />
        <p>
          Mientras tanto, el órgano que debía gobernar ese sistema estuvo{" "}
          <strong>cinco años y medio caducado</strong>: el mandato del CGPJ venció el 4 de
          diciembre de 2018 y los dos grandes partidos no pactaron su renovación hasta el
          25 de junio de 2024. Esta sección y la de{" "}
          <a href="#nombramientos" className="text-accent hover:underline">nombramientos
          judiciales</a> son la misma historia: el poder político administra los tiempos
          de la justicia.
        </p>
        <TakeBox>
          <p>
            Una justicia que tarda años no es neutral: <strong>favorece a quien puede
            esperar</strong> — el moroso frente al acreedor, la Administración frente al
            administrado, el aforado con buenos abogados frente al testigo que cambia de
            trabajo. Y en la corrupción el atasco juega directamente para el infractor:
            instrucciones eternas, piezas que prescriben, condenas que llegan cuando ya no
            hay carrera política que perder. La lentitud no es un fallo del sistema: es
            una característica que a sus gestores nunca les ha urgido arreglar.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="partitocracia"
        icon={HandCoins}
        title="Partitocracia: el sistema se financia a sí mismo"
        sources={[
          { ref: "BOE — Resolución de 7-ene-2025, subvenciones de funcionamiento a partidos, 4.º trimestre 2024 (BOE-A-2025-693)", href: "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2025-693" },
          { ref: "Tribunal de Cuentas — fiscalización de las contabilidades electorales de las elecciones generales de 2023", href: "https://www.cuentaspartidospoliticos.es/es/noticias/detalle/El-Tribunal-de-Cuentas-considera-justificados-gastos-por-valor-de-58-millones-de-euros-realizados-por-los-partidos-politicos-para-financiar-la-campana-de-las-elecciones-generales-de-2023/" },
          { ref: "BOE — RD 963/2024, subvención a fundaciones de partidos (BOE-A-2024-19174)", href: "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2024-19174" },
          { ref: "El Debate — evolución de las subvenciones del Ministerio de Trabajo a los sindicatos, 2020-2025", href: "https://www.eldebate.com/economia/20260421/sindicatos-quedan-yolanda-diaz-gran-benefactora-cuadriplico-ayudas-cinco-anos_408895.html" },
          { ref: "Fundación Hay Derecho — Estudio sobre las puertas giratorias en la AGE (2017)", href: "https://hayderecho.com/wp-content/uploads/2017/04/Estudio-sobre-Puertas-Giratorias_Vconsolidada-2.pdf" },
        ]}
      >
        <p>
          Los partidos cobran del presupuesto que ellos mismos aprueban. Solo la
          subvención estatal de <em>funcionamiento ordinario</em> sumó{" "}
          <strong>13,18 millones de euros en el cuarto trimestre de 2024</strong> (tabla
          oficial del BOE) — <strong>unos 53 millones al año</strong>, cálculo a partir de
          esa tabla. A eso se añade la subvención electoral: en las generales de 2023 el
          Tribunal de Cuentas dio por justificados <strong>58 millones</strong> de gastos
          de campaña, financiados sobre todo con crédito bancario y adelantos de
          subvención — las aportaciones privadas fueron el 1%.
        </p>
        <StatGrid>
          <BigStat value="~53" unit="M€/año" label="Subvención estatal ordinaria a partidos" sub="Cálculo a partir del BOE (13,18 M€/trimestre)" tone="amber" />
          <BigStat value="58" unit="M€" label="Gastos electorales justificados, generales 2023" sub="Tribunal de Cuentas" tone="amber" />
          <BigStat value="32" unit="M€" label="Subvenciones de Trabajo a sindicatos en 2025 (+260% en 5 años)" sub="El Debate, s/ resoluciones del Ministerio" tone="rose" />
          <BigStat value="377 / 6" label="Autorizaciones vs denegaciones a ex altos cargos para irse a la privada" sub="F. Hay Derecho, s/ datos OCI (hasta 2016)" tone="rose" />
        </StatGrid>
        <BarCompare
          title="Subvención de funcionamiento ordinario, 4.º trimestre 2024"
          unit="€, tabla oficial del BOE"
          data={[
            { label: "PP", value: 4720921, display: "4,72 M€" },
            { label: "PSOE", value: 3707746, display: "3,71 M€", highlight: true },
            { label: "VOX", value: 1538546, display: "1,54 M€" },
            { label: "PSC", value: 687661, display: "0,69 M€" },
          ]}
          source="Fuente: BOE-A-2025-693 (Resolución de 7-ene-2025). Cobran todos los partidos con representación — los financias votes a quien votes."
        />
        <p>
          El circuito se completa con las <strong>fundaciones de partido</strong> (4,93
          millones de subvención pública en 2021-2022 según el último informe del Tribunal
          de Cuentas, más una línea anual del Ministerio de Cultura — 350.000 € en 2024,
          RD 963/2024) y con las <strong>puertas giratorias</strong>: la Fundación Hay
          Derecho documentó que la Oficina de Conflictos de Intereses autorizó 377 saltos
          de ex altos cargos a la empresa privada y solo denegó 6, con una de cada diez
          autorizaciones hacia empresas del IBEX-35. Son datos de ese estudio (sobre
          registros oficiales) y se citan como tales.
        </p>
        <TakeBox>
          <p>
            Aquí el conflicto de interés no es una anécdota: <strong>es el diseño</strong>.
            Quien legisla sobre financiación de partidos es el financiado; quien aprueba
            la subvención al sindicato es quien negocia con él; quien regula el sector es
            quien acabará sentado en su consejo. Ninguna de estas piezas es ilegal —
            precisamente porque las reglas las escriben sus beneficiarios. Cortar la
            dependencia del presupuesto —financiación por afiliados y donantes con
            transparencia total— alinearía a los partidos con sus votantes, no con la
            caja común.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="transparencia"
        icon={EyeOff}
        title="Transparencia: el jefe no puede ver las cuentas"
        sources={[
          { ref: "Ley 19/2013, de transparencia, acceso a la información pública y buen gobierno", href: "https://www.boe.es/buscar/act.php?id=BOE-A-2013-12887" },
          { ref: "Consejo de Transparencia y Buen Gobierno (CTBG) — portal oficial", href: "https://www.consejodetransparencia.es/" },
          { ref: "Infobae — «El Gobierno incumplió una de cada diez resoluciones del Consejo de Transparencia en 2025» (14-ene-2026)", href: "https://www.infobae.com/espana/agencias/2026/01/14/el-gobierno-incumplio-una-de-cada-diez-resoluciones-del-consejo-de-transparencia-en-2025/" },
          { ref: "The Objective — resoluciones de transparencia incumplidas por las administraciones (14-ene-2026)", href: "https://theobjective.com/medios/2026-01-14/transparencia-administraciones-publicas-resoluciones/" },
        ]}
      >
        <p>
          La Ley 19/2013 reconoció el derecho de acceso a la información pública y creó un
          árbitro, el <strong>Consejo de Transparencia y Buen Gobierno</strong>. El diseño
          tiene un agujero de origen: cuando el CTBG estima una reclamación y ordena
          entregar la información, <strong>no puede sancionar a la administración que lo
          ignora</strong>. Y lo ignoran: según los datos recogidos por Infobae y The
          Objective, las administraciones acumulaban <strong>en torno a 1.400 resoluciones
          sin cumplir</strong> (aproximadamente una de cada cinco) a mediados de 2025, y el
          Gobierno central incumplió el <strong>11,5%</strong> de las que le afectaron en
          2025.
        </p>
        <StatGrid>
          <BigStat value="~1.400" label="Resoluciones del CTBG incumplidas por las administraciones" sub="Infobae / The Objective (ene-2026)" tone="rose" />
          <BigStat value="11,5" unit="%" label="De las resoluciones al Gobierno central, incumplidas en 2025" sub="Infobae, s/ datos CTBG" tone="amber" />
          <BigStat value="0" label="Sanciones puede imponer el CTBG a quien ignora sus resoluciones" sub="Ley 19/2013 — sin potestad coercitiva" tone="rose" />
        </StatGrid>
        <p>
          La asimetría con el ciudadano es completa: si tú ignoras un requerimiento de
          Hacienda, hay recargo, sanción y apremio; si una administración ignora la orden
          del órgano de transparencia, no pasa nada. El derecho existe sobre el papel — su
          cumplimiento es, en la práctica, voluntario para el obligado.
        </p>
        <TakeBox>
          <p>
            La relación está invertida: <strong>el ciudadano es el jefe que paga cada
            nómina y cada contrato, y aun así tiene que litigar durante años para ver en qué
            se gasta su dinero</strong> — mientras el Estado le exige a él transparencia
            total e inmediata sobre sus ingresos. En una relación sana de agencia, el
            empleado rinde cuentas al empleador por defecto: cada euro publicado en abierto,
            en tiempo real y a nivel de factura. Que eso suene utópico es la medida exacta
            del problema.
          </p>
        </TakeBox>
      </Section>

      {/* ════════════════ PARTE 3 — TU BOLSILLO ════════════════ */}
      <PartHeader
        id="coste"
        kicker="Parte 3 · Tu bolsillo"
        title="El coste del régimen"
        intro={
          <>
            Las mismas reglas, vistas por la factura que dejan. Una lectura crítica desde
            la eficiencia y los incentivos: ¿a quién benefician realmente el mercado
            laboral, el sistema de pensiones, el precio de la vivienda, la presión fiscal
            y la inflación? El encuadre es nuestro; <span className="text-slate-300">cada
            cifra está citada a su fuente oficial</span>, y las estimaciones de
            think-tanks van nombradas.
          </>
        }
      />

      <Section
        id="mercado-laboral"
        icon={Briefcase}
        title="Mercado laboral: paro crónico por diseño"
        sources={[
          { ref: "Eurostat — Euro indicators, desempleo armonizado mayo 2026 (publicado 2-jul-2026)", href: "https://ec.europa.eu/eurostat/web/products-euro-indicators/w/3-02072026-ap" },
          { ref: "INE — Encuesta de Población Activa, 1.er trimestre 2026", href: "https://www.ine.es/dyngs/Prensa/EPA1T26.htm" },
          { ref: "Fedea — «Los efectos de la reforma laboral: temporalidad contractual vs empírica» (Conde-Ruiz, García, Puch y Ruiz, 2023)", href: "https://fedea.net/los-efectos-de-la-reforma-laboral-temporalidad-contractual-vs-empirica/" },
        ]}
      >
        <p>
          El paro español no es un accidente cíclico: es <strong>estructural y crónico</strong>.
          Según Eurostat, en mayo de 2026 España tenía un <strong>10,3% de paro</strong> —
          casi el doble de la media de la UE (5,9%)— y solo en febrero de 2026 dejó de ser
          el país con más paro de la Unión, superada por Finlandia, tras una racha que la
          prensa cifra en 39 meses consecutivos liderando la tabla y en una posición de
          cabeza casi ininterrumpida desde 2013.
        </p>
        <StatGrid>
          <BigStat value="10,3" unit="%" label="Tasa de paro (mayo 2026)" sub="Eurostat — media UE: 5,9%" tone="rose" />
          <BigStat value="23,7" unit="%" label="Paro juvenil (menores de 25)" sub="Eurostat — media UE: 15,2%" tone="rose" />
          <BigStat value="2,7" unit="M" label="Parados (EPA, 1T 2026)" sub="INE — 2.708.600 personas" tone="amber" />
          <BigStat value="14,8" unit="%" label="Tasa de temporalidad (1T 2026)" sub="EPA (INE)" />
        </StatGrid>
        <BarCompare
          title="Tasa de paro, mayo 2026 — España frente a sus vecinos"
          unit="% de la población activa, dato armonizado"
          data={[
            { label: "Finlandia", value: 10.6, display: "10,6%" },
            { label: "España", value: 10.3, display: "10,3%", highlight: true },
            { label: "Media eurozona", value: 6.2, display: "6,2%" },
            { label: "Media UE", value: 5.9, display: "5,9%" },
          ]}
          source="Fuente: Eurostat, mayo 2026. España casi dobla la media de la UE; el dato finlandés es un pico cíclico reciente, el español lleva décadas."
        />
        <p>
          La dualidad tampoco ha desaparecido: la reforma laboral de 2021 redujo la
          temporalidad <em>contractual</em> hasta cifras europeas (14,8% en la EPA), pero
          según Fedea los fijos-discontinuos replican «casi exactamente la situación
          anterior en términos de estabilidad laboral» — el contrato cambió de etiqueta,
          no de naturaleza. Es la tesis de ese estudio (Conde-Ruiz, García, Puch y Ruiz) y
          se cita como tal.
        </p>
        <TakeBox>
          <p>
            Medio siglo con el paro doblando al vecindario no se explica por la mala
            suerte: es el resultado de <strong>encarecer y arriesgar la contratación</strong>
            —cotizaciones de las más altas de la OCDE (ver Impuestos), despido incierto,
            convenios rígidos— que protege a quien ya está dentro a costa de quien está
            fuera. Los de fuera son, sistemáticamente, <strong>los jóvenes</strong>: uno de
            cada cuatro no encuentra trabajo, y eso retrasa emancipación, vivienda y
            familia. El coste del régimen laboral no lo paga «la empresa»: lo paga el que
            no llega ni a firmar contrato.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="pensiones"
        icon={PiggyBank}
        title="Pensiones: el agujero que tapa el Estado"
        sources={[
          { ref: "Ministerio de Inclusión, Seg. Social y Migraciones — cierre de la ejecución presupuestaria de la Seguridad Social 2025 (31-mar-2026)", href: "https://www.lamoncloa.gob.es/serviciosdeprensa/notasprensa/inclusion/paginas/2026/310326-seguridad-social-deficit-pib.aspx" },
          { ref: "Seguridad Social — el gasto en pensiones contributivas supone el 11,5% del PIB (2024)", href: "https://www.inclusion.gob.es/w/el-gasto-en-pensiones-contributivas-supone-el-11-5-del-pib-2" },
          { ref: "Comisión Europea — 2024 Ageing Report, country fiche España (proyección de gasto)", href: "https://economy-finance.ec.europa.eu/document/download/392994f2-c025-4066-bea8-f65cf4bcd411_en?filename=2024-ageing-report-country-fiche-Spain.pdf" },
          { ref: "AIReF — Opinión sobre la sostenibilidad del sistema de pensiones", href: "https://www.airef.es/es/divulgacion/es-sostenible-el-sistema-de-pensiones/" },
          { ref: "INE — Proyecciones de población 2024-2074", href: "https://www.ine.es/dyngs/Prensa/PROP20242074.htm" },
        ]}
      >
        <p>
          El sistema español es de <strong>reparto</strong>: las cotizaciones de quienes
          trabajan hoy pagan las pensiones de hoy. No hay un fondo capitalizado que
          respalde lo prometido. Cuando lo que entra no llega, la diferencia la pone el
          Estado vía impuestos y deuda — es decir, la pagan los contribuyentes de hoy y de
          mañana. En 2025 el sistema ingresó 176.918 millones por cotizaciones y recibió{" "}
          <strong>52.990 millones en transferencias del Estado</strong>; aun así cerró con{" "}
          <strong>7.387 millones de déficit</strong> (0,4% del PIB, el menor desde 2011 —
          la mejora del ciclo es real, pero el sostén estatal sigue siendo estructural).
        </p>
        <StatGrid>
          <BigStat value="52.990" unit="M€" label="Transferencias del Estado a la Seguridad Social en 2025" sub="Ejecución presupuestaria 2025" tone="rose" />
          <BigStat value="7.387" unit="M€" label="Déficit de 2025 pese a esas transferencias (0,4% del PIB)" sub="Min. Inclusión — mínimo desde 2011" tone="amber" />
          <BigStat value="2,44" label="Cotizantes por cada pensionista (2024)" sub="Seguridad Social" />
          <BigStat value="182.526" unit="M€" label="Gasto en pensiones en 2025 (+5,7% en un año)" sub="Min. Inclusión — ejecución 2025" />
          <BigStat value="16,1" unit="% PIB" label="Gasto de pensiones proyectado a 2050" sub="Ageing Report 2024 (CE)" tone="amber" />
          <BigStat value="53,7" unit="%" label="Tasa de dependencia (mayores/activos) en 2050" sub="Ivie, s/ proyecciones INE" />
        </StatGrid>
        <TrendStrip
          title="Gasto en pensiones, % del PIB — proyección de la Comisión Europea"
          points={[12.7, 13.3, 14.2, 15.1, 15.8, 16.1]}
          labelStart="2022 · 12,7%"
          labelEnd="2050 · 16,1%"
          caption="España proyecta +3,4 puntos de PIB hasta 2050, frente a +0,7 de media en la UE — una de las mayores subidas del continente."
          source="Fuente: Comisión Europea, 2024 Ageing Report (country fiche España)."
        />
        <p>
          La aritmética es demográfica: hoy hay <strong>2,44 cotizantes por pensionista</strong>{" "}
          y bajando. La AIReF estima que el envejecimiento llevaría la <strong>deuda pública
          al 123% del PIB en 2050</strong> si no se ajusta el sistema, y que se pagarán{" "}
          <strong>17,4 millones de pensiones</strong> (frente a ~11,6 hoy).
        </p>
        <TakeBox>
          <p>
            El problema de incentivos: el votante mayoritario cobra hoy, y el coste se
            difiere a quien todavía no vota. Por eso ninguna mayoría tiene incentivo para
            sincerar la cuenta. Un sistema con <strong>cuentas individuales o capitalización
            parcial</strong> ataría lo que cada uno recibe a lo que aporta, en vez de
            depender de que la pirámide demográfica aguante. No es recortar el Estado del
            bienestar: es que sea solvente y no una transferencia de deuda a los jóvenes.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="vivienda"
        icon={Home}
        title="Vivienda: el Estado es el primer interesado en que suba"
        sources={[
          { ref: "Ministerio de Vivienda y Agenda Urbana — Observatorio de Vivienda y Suelo, Boletín Anual 2024 (visados, iniciadas y terminadas)", href: "https://www.mivau.gob.es/el-ministerio/sala-de-prensa/noticias/vie-04072025-1252" },
          { ref: "INE — Índice de Precios de Vivienda (IPV), 1.er trimestre 2026", href: "https://www.ine.es/dyngs/Prensa/IPV1T26.htm" },
          { ref: "Banco de España — Síntesis de Indicadores, 1.5 mercado inmobiliario (esfuerzo y años de renta)", href: "https://www.bde.es/webbe/es/estadisticas/compartido/datos/pdf/si_1_5.pdf" },
          { ref: "Agencia Tributaria — impuestos en la compra de vivienda (IVA / ITP / AJD)", href: "https://sede.agenciatributaria.gob.es/Sede/iva/iva-operaciones-inmobiliarias/compro-vivienda-tengo-que-pagar-itp.html" },
        ]}
      >
        <p>
          El precio sube por lo de siempre: <strong>mucha demanda y poca oferta</strong>.
          Y la oferta no es escasa por azar — está estrangulada por decisión política. Se
          construye una fracción de lo que se construía, y casi todo el suelo está vetado a
          la edificación.
        </p>
        <BarCompare
          title="Viviendas de obra nueva al año: del pico a hoy"
          unit="viviendas iniciadas / visadas"
          data={[
            { label: "2006 (pico)", value: 736000, display: "~736.000" },
            { label: "2024", value: 127721, display: "127.721", highlight: true },
          ]}
          source="Fuente: Ministerio de Vivienda, Observatorio de Vivienda y Suelo. La oferta de obra nueva de 2024 es ~1/6 de la del pico de 2006."
        />
        <StatGrid>
          <BigStat value="+12,9" unit="%" label="Subida del precio de la vivienda, 1T 2026" sub="INE — IPV (interanual)" tone="amber" />
          <BigStat value="8,0" unit="años" label="De renta bruta del hogar para comprar una vivienda (1T 2026)" sub="Banco de España — Síntesis de Indicadores" tone="amber" />
          <BigStat value="36,1" unit="%" label="Esfuerzo teórico anual de acceso a la vivienda (1T 2026)" sub="Banco de España (umbral sano: 30%)" />
          <BigStat value="~2,5" unit="%" label="Del parque es vivienda social en España" sub="UPF / Provivienda — media UE ~9,3%" />
        </StatGrid>
        <p>
          ¿Por qué no se construye? Según el Centro Ruth Richardson (U. de las Hespérides,
          think-tank de orientación liberal), <strong>en torno al 95% del suelo está
          clasificado como no urbanizable</strong> y solo una porción mínima admite vivienda
          a corto plazo; reclasificar suelo lleva una década. Es una estimación de ese
          centro, no un dato del INE, pero apunta a la causa estructural: la oferta la
          raciona el planeamiento.
        </p>
        <BarCompare
          title="Vivienda social como % del parque total"
          data={[
            { label: "España", value: 2.5, display: "~2,5%", highlight: true },
            { label: "Media UE", value: 9.3, display: "~9,3%" },
            { label: "Austria", value: 24, display: "~24%" },
            { label: "Países Bajos", value: 30, display: "~30%" },
          ]}
          source="Fuente: estudio UPF / Provivienda. El Estado que más encarece la compra es, a la vez, el que menos vivienda asequible aporta."
        />
        <TakeBox>
          <p>
            El conflicto de interés es directo: comprar una vivienda paga <strong>IVA del
            10%</strong> (obra nueva) o <strong>ITP del 6-10%</strong> (segunda mano), más
            AJD, y tenerla paga IBI y plusvalía municipal. Sumando tipos, el Estado se lleva
            del orden del <strong>10-13% del precio</strong> en la transmisión (cálculo a
            partir de los tipos vigentes, no una cifra publicada). Cuanto más caro, más
            recauda: <strong>el primer interesado en que el precio suba es quien debería
            moderarlo</strong>. Lo que abarataría la vivienda —liberar suelo, agilizar
            licencias, dejar construir— es justo lo que reduce esa recaudación.
          </p>
          <p className="text-[13px] text-slate-400">
            El control de precios del alquiler (Ley de Vivienda) es objeto de debate: sus
            defensores lo ven como protección; sus críticos, como menos oferta. El dato
            observable es que en 2024 el alquiler siguió en máximos (+11,5%, según el índice
            de idealista — fuente privada). El precio no obedece a decretos, sino a cuánta
            oferta haya.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="impuestos"
        icon={Receipt}
        title="Impuestos: para recaudar, el Estado sí es eficiente"
        sources={[
          { ref: "OCDE — Taxing Wages 2026 (cuña fiscal 2025, España)", href: "https://www.oecd.org/en/publications/taxing-wages-2026_3a5169ef-en.html" },
          { ref: "Agencia Tributaria — Informe Anual de Recaudación Tributaria 2024", href: "https://sede.agenciatributaria.gob.es/Sede/estadisticas/recaudacion-tributaria/informe-anual/ejercicio-2024/resumen.html" },
          { ref: "Eurostat — estadísticas de ingresos tributarios (presión fiscal / PIB)", href: "https://ec.europa.eu/eurostat/web/government-finance-statistics/data/main-tables" },
          { ref: "Agencia Tributaria — IRPF (escala y tipos)", href: "https://sede.agenciatributaria.gob.es/Sede/irpf.html" },
        ]}
      >
        <p>
          Una crítica de mercado no dice «menos servicios»; dice <strong>que cada euro
          rinda y que la carga sea proporcionada</strong>. El contraste español es
          revelador: la maquinaria recaudatoria es de las mejores del mundo, mientras que el
          gasto que financia rara vez se audita por resultados.
        </p>
        <StatGrid>
          <BigStat value="41,4" unit="%" label="Cuña fiscal sobre el salario medio (soltero)" sub="OCDE 2025 — media OCDE: 35,1%" tone="amber" />
          <BigStat value="294.734" unit="M€" label="Recaudación tributaria de la AEAT en 2024" sub="+8,4% sobre 2023" />
          <BigStat value="47" unit="%" label="Tipo marginal máximo estatal del IRPF" sub="(+ tramo autonómico)" />
          <BigStat value="30 jul" label="Día de la liberación fiscal: 212 días para pagar impuestos" sub="Fund. Civismo (think-tank liberal)" tone="amber" />
        </StatGrid>
        <BarCompare
          title="Cuña fiscal sobre el trabajo: lo que se queda el Estado del coste laboral"
          unit="% del coste salarial, trabajador medio soltero, 2025"
          data={[
            { label: "España", value: 41.4, display: "41,4%", highlight: true },
            { label: "Media OCDE", value: 35.1, display: "35,1%" },
          ]}
          source="Fuente: OCDE, Taxing Wages 2026. España es la 10.ª más alta de 38 países; el grueso son cotizaciones, que encarecen contratar."
        />
        <p>
          Sobre la presión fiscal total (en torno al <strong>37% del PIB</strong> según
          Eurostat), el IEE —el instituto de la CEOE— sostiene que el «esfuerzo fiscal»
          español supera en ~18% la media de la UE por hacerse sobre una renta per cápita
          menor. Es la tesis de una patronal, no un dato neutro; se cita como tal.
        </p>
        <TakeBox>
          <p>
            La asimetría es el problema: <strong>Hacienda funciona como un reloj para
            ingresar</strong> —y eso demuestra que el Estado <em>sabe</em> ser eficiente
            cuando le interesa—, pero esa misma exigencia no se aplica al gasto. Y, de
            propina, los tramos del IRPF no se ajustan a la inflación (ver abajo), así que
            la presión sube sola sin que nadie vote una subida. Eficiencia recaudatoria con
            laxitud en el gasto es, exactamente, incentivos desalineados con el ciudadano.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="poder-adquisitivo"
        icon={TrendingDown}
        title="Poder adquisitivo: el impuesto silencioso"
        sources={[
          { ref: "INE — variación del IPC entre dos fechas (calculadora oficial)", href: "https://www.ine.es/varipc/" },
          { ref: "INE — nota de prensa del IPC, diciembre 2025 (anual +2,9%)", href: "https://www.ine.es/dyngs/Prensa/IPC1225.htm" },
          { ref: "OCDE — Employment Outlook (evolución de salarios reales)", href: "https://www.oecd.org/en/publications/oecd-employment-outlook_19991266.html" },
          { ref: "Eurostat — salarios y costes laborales (comparación europea)", href: "https://ec.europa.eu/eurostat/web/labour-market/earnings" },
          { ref: "AIReF y Fedea — efecto de no deflactar el IRPF por inflación (estimaciones)", href: "https://www.airef.es/es/centro-documental/" },
        ]}
      >
        <p>
          La inflación no es neutral: erosiona el salario y, a la vez, engorda la
          recaudación. Entre 2020 y 2025 los precios subieron un <strong>23,5%</strong> y la
          cesta de alimentos bastante más, mientras el salario real apenas se movió.
        </p>
        <TrendStrip
          title="Nivel de precios al consumo (IPC), base ene-2019 = 100"
          points={[100, 99.7, 103.0, 111.5, 115.3, 120.6, 124.1]}
          labelStart="ene 2019 · 100"
          labelEnd="dic 2025 · 124,1"
          caption="+23,5% acumulado entre 2020 y 2025 (serie 2020-2024, +20,0%, verificada en la calculadora oficial del INE; 2025 cerró con un IPC anual del +2,9%). Alimentos y bebidas subieron aún más, ~+30% hasta 2024."
          source="Fuente: INE, calculadora del IPC (ine.es/varipc) y nota de prensa IPC diciembre 2025."
        />
        <StatGrid>
          <BigStat value="+23,5" unit="%" label="Inflación acumulada 2020-2025" sub="INE (calculadora oficial + IPC dic-2025)" tone="amber" />
          <BigStat value="+6" unit="%" label="Subida del salario real medio en España, 1995-2023" sub="Eurostat/OCDE — Alemania +21%, Francia +27%" tone="rose" />
          <BigStat value=">10.100" unit="M€/año" label="Recaudación extra por no deflactar el IRPF" sub="AIReF (estimación) · ~0,6% del PIB" tone="amber" />
        </StatGrid>
        <BarCompare
          title="Crecimiento del salario real medio, 1995-2023"
          data={[
            { label: "España", value: 6, display: "+6%", highlight: true },
            { label: "Alemania", value: 21, display: "+21%" },
            { label: "Portugal", value: 26, display: "+26%" },
            { label: "Francia", value: 27, display: "+27%" },
          ]}
          source="Fuente: datos Eurostat/OCDE. En casi 30 años el salario real apenas avanzó en España; en 2024 seguía por debajo del nivel de 2019."
        />
        <TakeBox>
          <p>
            El doble golpe: <strong>la inflación te empobrece en términos reales</strong> y,
            como el Estado <strong>no deflacta los tramos del IRPF</strong>, una subida
            salarial que solo compensa los precios te empuja a tipos más altos y pagas más
            impuesto sobre un poder de compra que no ha crecido. Es la «progresividad en
            frío»: la AIReF la cifra en más de 10.000 M€ al año y Fedea estima que una
            familia de renta media paga ~30% más de IRPF que en 2015 por este motivo. Una
            subida de impuestos que nadie aprobó en el Parlamento.
          </p>
        </TakeBox>
      </Section>

      {/* ════════════════ PARTE 4 — EL PODER SIN URNAS (UE) ════════════════ */}
      <PartHeader
        id="europa"
        kicker="Parte 4 · Bruselas"
        title="El poder sin urnas"
        intro={
          <>
            Encima de todo lo anterior hay otra capa de poder: la Unión Europea, cuyas
            normas España debe aplicar y cuyo único órgano con iniciativa legislativa no
            sale de ninguna urna. Mismas reglas que en el resto de la página:{" "}
            <span className="text-slate-300">cada cifra citada a su fuente</span> —aquí,
            sobre todo, sentencias, dictámenes y auditorías oficiales de la propia UE—, la
            crítica declarada como nuestra, y{" "}
            <span className="text-slate-300">presunción de inocencia</span> en lo judicial:
            lo probado se distingue de lo acusado.
          </>
        }
      />

      <Section
        id="soberania-ue"
        icon={Globe}
        title="Soberanía: un gobierno para los gobiernos, que nadie vota"
        sources={[
          { ref: "Tratado de la Unión Europea, art. 17 (texto consolidado, EUR-Lex): iniciativa legislativa de la Comisión y elección de su presidente", href: "https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:12016M017" },
          { ref: "Parlamento Europeo — resolución P9_TA(2022)0242 reclamando un derecho de iniciativa propio (art. 225 TFUE)", href: "https://eur-lex.europa.eu/legal-content/ES/TXT/PDF/?uri=CELEX:52022IP0242" },
          { ref: "Euronews — la investidura de Von der Leyen en 2019: 383 votos, sin haber sido candidata (Spitzenkandidat)", href: "https://es.euronews.com/2019/07/03/ha-sido-un-fraude-la-eleccion-de-ursula-von-der-leyen-como-presidenta-de-la-comision-euro" },
          { ref: "Le Monde diplomatique en español — «¿Quién ha elegido a Ursula von der Leyen?»", href: "https://mondiplo.com/quien-ha-elegido-a-ursula-von-der-leyen" },
        ]}
      >
        <p>
          En la arquitectura de la UE, <strong>solo la Comisión Europea puede proponer
          leyes</strong> (art. 17.2 TUE). El Parlamento Europeo —la única institución
          elegida directamente por los ciudadanos— <strong>no tiene derecho de
          iniciativa</strong>: solo puede <em>pedirle</em> a la Comisión que legisle
          (art. 225 TFUE), y lleva años reclamando formalmente ese derecho sin obtenerlo.
          Las normas que luego rigen en España nacen, así, de un colegio de comisarios que
          ningún elector ha puesto ahí.
        </p>
        <p>
          ¿Y quién elige a quien manda en la Comisión? Los gobiernos, entre ellos. El
          sistema del <em>Spitzenkandidat</em> —que el presidente saliera del cabeza de
          lista más votado en las europeas— se ensayó en 2014 para dar un barniz electoral
          al cargo… y se abandonó en cuanto estorbó: en 2019 el Consejo Europeo descartó a
          los dos cabezas de lista (Weber y Timmermans) y sacó de la chistera a{" "}
          <strong>Ursula von der Leyen, que no había concurrido a las elecciones</strong>.
          El Parlamento la ratificó por <strong>383 votos contra 327 — 9 de margen</strong>.
        </p>
        <StatGrid>
          <BigStat value="0" label="Papeletas ciudadanas llevaban el nombre de Von der Leyen en 2019" sub="No fue candidata en las europeas" tone="rose" />
          <BigStat value="9" unit="votos" label="Margen de su investidura en el Parlamento (383-327)" sub="Euronews, 16-jul-2019" tone="amber" />
          <BigStat value="1" label="Institución puede proponer leyes en la UE: la Comisión, no electa" sub="Art. 17.2 TUE" tone="rose" />
        </StatGrid>
        <TakeBox>
          <p>
            La pregunta incómoda no es antieuropea, es democrática: <strong>¿por qué
            necesitan los gobiernos un gobierno por encima al que el ciudadano no puede ni
            poner ni quitar?</strong> Cada competencia que sube a Bruselas es una decisión
            que se aleja una capa más de tu papeleta: eliges a quien elige a quien negocia
            con quien decide. A esa distancia, la rendición de cuentas llega tan diluida
            que ya no disciplina a nadie — y las secciones siguientes muestran lo que pasa
            cuando el poder deja de temer al votante.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="pfizergate"
        icon={MessagesSquare}
        title="Pfizergate: 35.000 millones negociados por SMS"
        sources={[
          { ref: "Tribunal General de la UE — asunto T-36/23, sentencia de 14-may-2025 (página oficial del caso en CURIA)", href: "https://curia.europa.eu/juris/liste.jsf?num=T-36/23" },
          { ref: "Defensora del Pueblo Europeo — decisión en el caso 1316/2021/MIG: mala administración de la Comisión por no buscar los SMS", href: "https://www.ombudsman.europa.eu/en/decision/en/158295" },
          { ref: "Público — la Comisión aprueba el contrato de hasta 1.800 millones de dosis con Pfizer (mayo 2021)", href: "https://www.publico.es/politica/comision-europea-aprueba-contrato-1-800-millones-dosis-vacuna-pfizer.html" },
          { ref: "Infobae — la presidenta de la Comisión supera la moción de censura por el Pfizergate (10-jul-2025: 360 en contra, 175 a favor)", href: "https://www.infobae.com/espana/2025/07/10/von-der-leyen-sobrevive-a-la-mocion-de-censura-que-deja-sus-apoyos-mermados/" },
        ]}
      >
        <p>
          En mayo de 2021 la Comisión cerró el mayor contrato de vacunas de la UE:{" "}
          <strong>hasta 1.800 millones de dosis de Pfizer/BioNTech</strong>, con un coste
          estimado por la prensa en torno a <strong>35.000 millones de euros</strong>. Parte
          de esa negociación la condujo personalmente la presidenta de la Comisión{" "}
          <strong>intercambiando SMS con el CEO de Pfizer</strong> — lo
          reveló The New York Times, y la propia presidenta lo había descrito antes en otra
          entrevista.
        </p>
        <p>
          Cuando una periodista pidió esos mensajes por la vía de acceso a documentos, la
          Comisión respondió que <em>no los poseía</em>. La <strong>Defensora del Pueblo
          Europeo concluyó en 2022 que hubo «mala administración»</strong> — ni siquiera se
          los pidió al gabinete de la presidenta. Y el 14 de mayo de 2025 el{" "}
          <strong>Tribunal General de la UE anuló la denegación</strong> (asunto T-36/23):
          la Comisión «no dio una explicación plausible» de por qué no tenía los
          documentos. Tras la sentencia, la Comisión anunció una nueva decisión «más
          detallada»; <strong>los SMS siguen sin publicarse</strong>. En julio de 2025 la
          presidenta de la Comisión superó una moción de censura motivada por el caso (360
          votos en contra, 175 a favor). Precisión debida: la sentencia versa sobre
          transparencia — no hay condena penal alguna, y nada de lo anterior debe leerse
          como tal.
        </p>
        <StatGrid>
          <BigStat value="1.800" unit="M" label="Dosis del contrato negociado en parte por SMS (mayo 2021)" sub="Comisión Europea / Público" tone="amber" />
          <BigStat value="~35.000" unit="M€" label="Coste estimado del contrato" sub="Estimación de prensa (Público, El Español)" tone="rose" />
          <BigStat value="4" unit="años" label="Y contando: los SMS siguen sin enseñarse pese a Defensora y Tribunal" sub="Solicitud de 2022; sentencia T-36/23 (2025)" tone="rose" />
        </StatGrid>
        <TakeBox>
          <p>
            Quien administra dinero ajeno no elige qué enseña — <strong>eso lo decide el
            dueño del dinero, que aquí eres tú</strong>. Si el mayor contrato sanitario de
            la historia europea cabe en mensajes que su firmante dice no conservar, y hacen
            falta una Defensora, un periódico y cuatro años de litigio para que un tribunal
            declare ilegal la opacidad — sin que a día de hoy haya ni mensajes ni
            consecuencias —, la lección que aprende todo cargo público es exacta:{" "}
            <strong>borrar sale gratis</strong>.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="chat-control"
        icon={Radar}
        title="Chat Control: la tentación de leerlo todo"
        sources={[
          { ref: "Euronews — el Consejo de la UE renuncia al escaneo obligatorio de chats y lo deja «voluntario» (26-nov-2025)", href: "https://es.euronews.com/2025/11/26/ue-abandona-control-obligatorio-chats-vigilancia-voluntaria-abuso-menores" },
          { ref: "El Salto — España, entre los países que apoyaron la versión más dura del reglamento (vigilancia de 450 millones de personas)", href: "https://www.elsaltodiario.com/derechos-digitales/iniciativa-ue-pretende-vigilar-conversaciones-450-millones-personas-sanchez-apoya" },
          { ref: "EDRi (European Digital Rights) — análisis y oposición de expertos al reglamento CSAR", href: "https://edri.org/" },
          { ref: "Maldita.es — qué es (y qué no es) el «chat control», verificado (mayo 2026)", href: "https://maldita.es/malditobulo/20260505/chat-control-acceso-mensajes-ue-2025/" },
          { ref: "Euronews — «Chat Control 1.0 passed the European Parliament — through the back door» (10-jul-2026)", href: "https://www.euronews.com/next/2026/07/10/chat-control-10-passed-the-european-parliament-through-the-back-door" },
          { ref: "Newtral — qué ha aprobado exactamente el Parlamento Europeo sobre «Chat Control»: votación 314-276-17, regla 68 y prórroga hasta abril de 2028 (10-jul-2026)", href: "https://www.newtral.es/parlamento-europeo-chat-control-leer-mensajes-privados-abuso-infantil/20260710/" },
          { ref: "Patrick Breyer (ex-MEP) — filtración: los ministros del Interior de la UE querían eximir del escaneo las cuentas de policía, inteligencia y militares (borrador del Consejo, CSAR)", href: "https://www.patrick-breyer.de/en/leak-eu-interior-ministers-want-to-exempt-themselves-from-chat-control-bulk-scanning-of-private-messages/" },
        ]}
      >
        <p>
          Desde 2022 la Comisión impulsa el reglamento <strong>CSAR</strong> («chat
          control»): con el argumento —real y grave— de combatir el abuso infantil,
          obligaría a los servicios de mensajería a <strong>escanear las comunicaciones
          privadas</strong>, incluidas las cifradas de extremo a extremo, de{" "}
          <strong>450 millones de europeos</strong>. Criptógrafos, la industria y
          organizaciones de derechos digitales como EDRi llevan tres años advirtiendo de lo
          obvio: un escáner universal es vigilancia masiva de inocentes, y una puerta
          trasera en el cifrado lo rompe para todos.
        </p>
        <p>
          La presión pública ha ido frenándolo: en noviembre de 2025 el Consejo retiró el
          escaneo obligatorio y dejó la detección como <strong>«voluntaria»</strong> para
          las plataformas (añadiendo verificación de edad), y el expediente sigue
          negociándose en el trílogo con el Parlamento. Conviene no relajarse por ello: la
          propuesta ha revivido con cada presidencia del Consejo, y{" "}
          <strong>España apoyó la versión más dura</strong> — la que incluía el escaneo
          obligatorio incluso de las comunicaciones cifradas (posición documentada por El
          Salto sobre las filtraciones de las negociaciones).
        </p>
        <p>
          En julio de 2026 quedó retratado, además, el método. El escaneo «voluntario» ya
          vigente —la excepción al secreto de las comunicaciones conocida como{" "}
          <strong>«Chat Control 1.0»</strong> (Reglamento 2021/1232)— había <strong>caducado
          en abril de 2026</strong> porque el Parlamento se negó a prorrogarla sin proteger
          el cifrado. ¿La respuesta? El Consejo adoptó su posición el 2 de julio, devolvió
          el expediente a la Eurocámara <strong>en vísperas del receso estival</strong> y el
          Pleno lo tramitó <strong>por procedimiento de urgencia</strong> el día 9. El
          resultado de la votación: <strong>314 eurodiputados a favor de rechazarlo, 276 en
          contra y 17 abstenciones</strong> — una mayoría clara de la cámara contra la
          prórroga. Pero la regla 68 del Reglamento interno exige <strong>mayoría absoluta
          (361 de 720)</strong> para rechazar en segunda lectura, así que el texto que la
          mayoría votó tumbar quedó <strong>aprobado «por defecto»</strong>: ganó el
          calendario, no los votos. Si el Consejo acepta las modificaciones, el escaneo
          queda prorrogado hasta abril de 2028.
        </p>
        <p>
          Y un detalle que define el proyecto entero: según los borradores del Consejo
          filtrados sobre el reglamento permanente (CSAR), quedarían <strong>exentas del
          escaneo las cuentas profesionales de la policía, los servicios de inteligencia y
          los militares</strong>, además de la «información confidencial» como los secretos
          profesionales (filtración documentada por el exeurodiputado Patrick Breyer).
          Quienes ordenan el escáner universal se excluyen de él.
        </p>
        <StatGrid>
          <BigStat value="450" unit="M" label="Europeos cuyas comunicaciones cubriría el escaneo" sub="El Salto / EDRi" tone="rose" />
          <BigStat value="314–276" label="El Pleno votó rechazar la prórroga; se aprobó igualmente — tumbarla exigía mayoría absoluta (361)" sub="Parlamento Europeo, 9-jul-2026 (Newtral / Euronews)" tone="rose" />
          <BigStat value="Sí" label="España votó a favor de la versión con escaneo obligatorio" sub="El Salto (atribuido)" tone="rose" />
        </StatGrid>
        <TakeBox>
          <p>
            El principio que se rompe no es técnico, es constitucional:{" "}
            <strong>escanear a todos por si alguien delinque es presunción de culpabilidad
            universal</strong> — el equivalente digital de abrir todas las cartas del país
            por si alguna contiene un delito. Y las infraestructuras de vigilancia tienen
            una ley propia: una vez construidas, jamás se quedan en su propósito original;
            solo cambia el «por si acaso». La carga de la prueba debería ser la contraria:
            es el Estado quien vigila al ciudadano el que necesita una orden judicial — no
            el ciudadano quien necesita permiso para hablar en privado.
          </p>
          <p>
            Julio de 2026 añadió la lección de procedimiento: <strong>una cámara votó «no»
            por mayoría y la norma sobrevivió</strong>, porque se eligió la única vía —
            urgencia, calendario de vacaciones y un umbral de mayoría absoluta — en la que
            el «no» no podía ganar. Es la misma técnica que la Comisión de Venecia censuró
            en la <a href="#amnistia" className="text-accent hover:underline">amnistía
            española</a>: la urgencia como herramienta para esquivar el escrutinio. Cuando
            aprobar depende de convencer y rechazar depende de un quórum imposible en julio,
            el procedimiento ha sustituido a la votación. Y la autoexención de los aparatos
            del Estado en los borradores es la confesión de diseño: <strong>el escáner
            universal se concibe para el ciudadano, nunca para quien lo ordena</strong>. Si
            la vigilancia masiva fuera tan inocua como la venden, sus autores no se sacarían
            de debajo de ella.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="identidad-digital"
        icon={Fingerprint}
        title="Identidad digital europea: una llave única para todo"
        sources={[
          { ref: "Reglamento (UE) 2024/1183 (eIDAS 2) — marco para una identidad digital europea (EUR-Lex)", href: "https://eur-lex.europa.eu/eli/reg/2024/1183/oj" },
          { ref: "Comisión Europea — EU Digital Identity Wallet: calendario de despliegue (carteras nacionales antes de fin de 2026)", href: "https://digital-strategy.ec.europa.eu/es/policies/eudi-wallet" },
          { ref: "Carta abierta contra el artículo 45 de eIDAS 2 (Mozilla y +500 expertos en seguridad): certificados impuestos a los navegadores", href: "https://last-chance-for-eidas.org/" },
          { ref: "EFF — «Article 45 Will Roll Back Web Security by 12 Years» (nov-2023)", href: "https://www.eff.org/deeplinks/2023/11/article-45-will-roll-back-web-security-12-years" },
        ]}
      >
        <p>
          El reglamento <strong>eIDAS 2</strong> (Reg. 2024/1183) obliga a cada Estado
          miembro a ofrecer a sus ciudadanos una <strong>cartera europea de identidad
          digital</strong> — en la práctica, antes de que acabe 2026 — y obliga al sector
          público y a sectores privados clave (banca, telecomunicaciones, grandes
          plataformas) a <strong>aceptarla</strong> como medio de identificación. Para el
          ciudadano es, oficialmente, voluntaria, y la norma incluye salvaguardas escritas:
          prohibición de perfilado, divulgación selectiva de datos, sin base de datos
          centralizada.
        </p>
        <p>
          Las críticas van a la arquitectura, no a la letra. Primero, la{" "}
          <strong>voluntariedad de facto</strong>: cuando tu banco, tu administración y tu
          operadora acepten —y prefieran— la cartera, la alternativa analógica se irá
          volviendo el carril lento hasta desaparecer; nadie tendrá que obligarte, igual
          que nadie te obligó a tener smartphone. Segundo, el <strong>artículo 45</strong>:
          obliga a los navegadores a confiar en certificados web designados por los
          Estados, lo que según Mozilla y buena parte de la comunidad de seguridad
          debilita las garantías con que hoy se protege el tráfico web y abre la puerta a
          interceptación estatal — objeción técnica publicada y atribuida.
        </p>
        <TakeBox>
          <p>
            Una llave única para todo es también <strong>un punto único de observación — y
            de fallo</strong>. Hoy tu banco no sabe lo que le dices a tu médico y tu
            ayuntamiento no sabe dónde viajas; una identidad transversal hace técnicamente
            trivial cruzarlo todo, y deja como única barrera una promesa regulatoria. Las
            arquitecturas sobreviven a las promesas y a los gobiernos que las hicieron: lo
            que se construya para la comodidad de 2026 estará disponible para las
            tentaciones de 2036.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="euro-digital"
        icon={Euro}
        title="El euro digital: dinero con rastro por diseño"
        sources={[
          { ref: "Banco Central Europeo — «Progresos en el euro digital»: fase de preparación 2025-2028, posible emisión hacia 2029", href: "https://www.ecb.europa.eu/euro/digital_euro/progress/html/index.es.html" },
          { ref: "ADICAE (servicios jurídicos) — límite de tenencia en torno a 3.000 € y tratamiento de la privacidad en la propuesta", href: "https://www.adicae.legal/2025/05/08/euro-digital-aproximacion-normativa-sobre-limite-de-tenecia-y-privacidad/" },
          { ref: "LawAndTrends — el BCE acelera el euro digital: dudas sobre privacidad y papel de los intermediarios", href: "https://www.lawandtrends.com/noticias/ue-internacional/el-bce-acelera-el-euro-digital-en-plena-negociacion-politica-dudas-sobre-privacidad-dependencia-tecnologica-y-1.html" },
        ]}
      >
        <p>
          El BCE prepara una moneda digital de banco central (CBDC): fase de preparación
          técnica hasta 2028, posible piloto en 2027 y <strong>emisión a gran escala
          orientada a 2029</strong>, condicionada a que la legislación europea se apruebe.
          El diseño planteado limita la tenencia individual (la cifra que se baraja ronda
          los <strong>3.000 €</strong>) y prohíbe remunerarla — es un medio de pago, no de
          ahorro, para no vaciar los depósitos bancarios.
        </p>
        <p>
          Seamos precisos con lo que el BCE promete, porque los matices son la sustancia:
          dice que el euro digital <strong>no será dinero programable</strong> y que las
          transacciones serán anónimas <em>para el banco central</em> — pero{" "}
          <strong>los intermediarios (tu banco) sí verán los pagos</strong>, como hoy con la
          tarjeta. Es decir: no añade el anonimato del efectivo, y llega justo cuando el
          efectivo —el único dinero que no deja rastro ni necesita permiso de nadie—
          retrocede año tras año. Los límites, la remuneración y las reglas de uso no son
          leyes de la física: son parámetros que quien controla el sistema puede revisar.
        </p>
        <StatGrid>
          <BigStat value="3.000" unit="€" label="Límite de tenencia que se baraja" sub="ADICAE, s/ propuesta de reglamento" tone="amber" />
          <BigStat value="2029" label="Horizonte de emisión a gran escala" sub="BCE — condicionado a la legislación" tone="amber" />
          <BigStat value="0" unit="%" label="Interés: no podrá remunerarse" sub="Propuesta — medio de pago, no de ahorro" />
        </StatGrid>
        <TakeBox>
          <p>
            El problema no es el euro digital aislado: es <strong>el mundo hacia el que
            empuja</strong> — uno donde cada pago de cada ciudadano queda registrado en
            algún sitio y el dinero sin rastro ya no existe. En ese mundo, «no será
            programable» y «3.000 € de límite» son decisiones revisables de quien tenga la
            mano en el interruptor, no derechos tuyos. La defensa concreta es prosaica:{" "}
            <strong>que el efectivo siga siendo un derecho efectivo</strong> — curso legal
            obligatorio, sin topes asfixiantes — porque es la única moneda que no pide
            permiso.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="cuentas-ue"
        icon={FileSearch}
        title="Las cuentas de la UE: seis años sin el visto bueno del auditor"
        sources={[
          { ref: "Tribunal de Cuentas Europeo — Informe Anual 2024: error estimado del 3,6% del gasto y sexta opinión desfavorable consecutiva", href: "https://www.eca.europa.eu/en/publications/AR-2024" },
          { ref: "Parlamento Europeo — ficha temática sobre el Tribunal de Cuentas Europeo", href: "https://www.europarl.europa.eu/factsheets/es/sheet/14/el-tribunal-de-cuentas" },
          { ref: "eldiario.es — el «Qatargate»: la trama de sobornos que atravesó el Parlamento Europeo (dic-2022)", href: "https://www.eldiario.es/internacional/qatargate-trama-corrupcion-atravesado-parlamento-europeo_1_9799255.html" },
        ]}
      >
        <p>
          El auditor oficial de la UE lleva <strong>seis años consecutivos</strong> emitiendo
          una <strong>opinión desfavorable</strong> sobre el gasto del presupuesto europeo.
          En su Informe Anual 2024, el Tribunal de Cuentas Europeo estimó un{" "}
          <strong>nivel de error del 3,6%</strong> —del orden de 6.000 millones de euros
          pagados sin ajustarse a las reglas—, que en los fondos de cohesión sube al 5,7%,
          y volvió a señalar debilidades sistémicas en el control de los fondos de
          recuperación. No es una acusación de fraude generalizado —el error no equivale a
          fraude—, pero sí la constatación anual de que el control no funciona.
        </p>
        <p>
          Y cuando el dinero corre, aparece quien lo recoge: en diciembre de 2022 la
          policía belga incautó <strong>en torno a 1,5 millones de euros en efectivo</strong>{" "}
          —en domicilios particulares y en una maleta de hotel— en la trama de sobornos conocida como{" "}
          <strong>Qatargate</strong>. Presunción de inocencia estricta: hay imputaciones y
          hubo prisión provisional, pero <strong>a día de hoy no hay condena firme</strong>;
          el caso sigue abierto años después — lo que, para el ciudadano que mira, es parte
          del problema.
        </p>
        <StatGrid>
          <BigStat value="6º" unit="año" label="Consecutivo con opinión desfavorable del auditor de la UE" sub="Tribunal de Cuentas Europeo, AR-2024" tone="rose" />
          <BigStat value="3,6" unit="%" label="Error estimado en los pagos de 2024 (~6.000 M€); 5,7% en cohesión" sub="Tribunal de Cuentas Europeo" tone="amber" />
          <BigStat value="~1,5" unit="M€" label="En efectivo incautado en el Qatargate (dic-2022) — sin condena firme aún" sub="eldiario.es, s/ fiscalía belga" tone="rose" />
        </StatGrid>
        <TakeBox>
          <p>
            Traslada la escena a tu vida: si tu gestoría llevara <strong>seis años
            seguidos</strong> con la opinión del auditor en contra, no le confiarías ni la
            declaración de la renta — le retirarías el dinero. Al presupuesto de la UE, en
            cambio, cada año le entra más, y la opinión desfavorable se archiva como un
            trámite. Cuando el dinero es de todos y el auditado no teme a nadie, el
            «error del 3,6%» deja de ser una nota técnica: es el precio anual de gastar sin
            dueño.
          </p>
        </TakeBox>
      </Section>

      <Section
        id="privilegios-ue"
        icon={Crown}
        title="Los privilegios de la Eurocámara: pensiones de lujo con aval público"
        sources={[
          { ref: "Investigate Europe — recorte del 50% a las pensiones de lujo de los eurodiputados; el público seguirá pagando parte de la factura", href: "https://www.investigate-europe.eu/es/posts/meps-face-50-cut-to-luxury-pensions-but-public-still-set-for-hefty-bill" },
          { ref: "infoLibre — el Parlamento Europeo recorta las pensiones voluntarias e inyectará dinero público para evitar la quiebra del fondo", href: "https://www.infolibre.es/politica/parlamento-europeo-recorta-pensiones-voluntarias-diputados-inyectara-dinero-publico-evitar-quiebra-fondo_1_1503786.html" },
          { ref: "Parlamento Europeo — asignaciones, pensiones y dietas de los eurodiputados (página oficial)", href: "https://www.europarl.europa.eu/news/es/faq/11/asignaciones-pensiones-y-dietas" },
        ]}
      >
        <p>
          Entre 1991 y 2009 los eurodiputados dispusieron de un <strong>fondo de pensiones
          voluntario</strong> con condiciones que ningún trabajador reconocería como suyas:{" "}
          <strong>dos tercios de la aportación los ponía el Parlamento</strong> —dinero
          público— y bastaban entre 2 y 5 años de aportaciones para asegurar una pensión
          vitalicia complementaria. El resultado, también reconocible: el fondo acumuló un{" "}
          <strong>agujero de unos 310 millones de euros</strong> y amenazaba quiebra.
        </p>
        <p>
          La «solución» de 2023-2024: recortar esas pensiones un 50%, congelar su
          actualización y subir la edad de cobro de 65 a 67 años. Aun así, según los
          cálculos recogidos por Investigate Europe e infoLibre,{" "}
          <strong>en torno a 86 millones de euros del déficit acabarán cubiertos con dinero
          del contribuyente</strong>. Quien quiera comparar con su propia pensión puede
          hacerlo con la página oficial de asignaciones de la Eurocámara a la vista.
        </p>
        <StatGrid>
          <BigStat value="310" unit="M€" label="Agujero del fondo de pensiones voluntario de los eurodiputados" sub="Investigate Europe" tone="rose" />
          <BigStat value="2/3" label="De cada aportación al fondo las pagaba el Parlamento (dinero público)" sub="Investigate Europe" tone="amber" />
          <BigStat value="~86" unit="M€" label="Que aun tras el recorte cubrirá el presupuesto público" sub="Investigate Europe / infoLibre" tone="rose" />
        </StatGrid>
        <TakeBox>
          <p>
            Es la misma lógica de la <a href="#partitocracia" className="text-accent hover:underline">
            partitocracia</a> elevada a escala continental: <strong>quien diseña sus propias
            condiciones se sirve primero, y la distancia hace el resto</strong> — un
            ayuntamiento no sobreviviría al escándalo de votarse una pensión así; en
            Bruselas apenas fue noticia. Mientras a ti se te explica que tu pensión exige
            «reformas» y años extra de cotización, el agujero de la de tus representantes
            se tapa con tu dinero. La factura es pequeña comparada con un presupuesto
            nacional; el mensaje que manda, no.
          </p>
        </TakeBox>
      </Section>

      {/* disclaimer */}
      <section className="mt-8 rounded-2xl glass p-5">
        <div className="flex gap-3">
          <Scale className="h-5 w-5 shrink-0 text-amber-400" />
          <p className="text-sm leading-relaxed text-slate-400">
            <span className="font-medium text-slate-200">Nota sobre el método.</span> La
            primera parte («las reglas») es descriptiva y cita la norma. Las partes segunda
            a cuarta («la maquinaria», «tu bolsillo» y «el poder sin urnas») son una{" "}
            <span className="text-slate-200">crítica abierta desde una óptica
            de mercado</span>: el <em>encuadre</em> es nuestro y se declara como tal, pero{" "}
            <span className="text-slate-200">cada cifra está citada a su fuente oficial</span>{" "}
            (INE, AEAT, OCDE, AIReF, Banco de España, Comisión Europea, Tribunal de Cuentas
            Europeo, BCE, EUR-Lex…) y las estimaciones de think-tanks y los datos de prensa
            van nombrados. En lo judicial (amnistía, Pfizergate, Qatargate) rige la{" "}
            <span className="text-slate-200">presunción de inocencia</span>: distinguimos lo
            probado de lo acusado, y una sentencia sobre transparencia no es una condena
            penal. No inventamos datos: lo que no
            tiene fuente sólida no se publica como cifra cerrada. Los casos concretos, con sus
            titulares y sus documentos oficiales, están en la{" "}
            <Link href="/ultima-hora" className="text-accent hover:underline">cronología</Link> y en los{" "}
            <Link href="/documentos" className="text-accent hover:underline">documentos</Link>.
          </p>
        </div>
      </section>
    </main>
  );
}
