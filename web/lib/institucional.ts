/* ────────────────────────────────────────────────────────────────────────────
 * Mapa de problemas de /institucional — single source of truth de las TARJETAS
 * del dashboard (ProblemMap). Las secciones largas siguen siendo JSX prose en
 * app/institucional/page.tsx; aquí solo vive lo que la tarjeta necesita.
 *
 * Invariantes (spec 007):
 *   - `id` de cada problema == `id` HTML de su sección en page.tsx (anchor).
 *   - La cifra-gancho debe poder trazarse a la caja de fuentes de su sección;
 *     ante discrepancia manda la sección y se corrige aquí.
 *   - `llano` y `cotidiano` son explicaciones para cualquiera (sin jerga);
 *     son lectura editorial declarada, igual que los TakeBox de la página.
 * ──────────────────────────────────────────────────────────────────────────── */

export type CategoriaId = "poder-justicia" | "reglas-juego" | "estado-gasto" | "bolsillo" | "europa";

export interface CategoriaDef {
  id: CategoriaId;
  label: string;
  descripcion: string;
}

export interface Problema {
  id: string; // slug estable; ES el anchor de la sección larga
  categoria: CategoriaId;
  titulo: string;
  stat: { value: string; unit?: string; label: string; tone?: "accent" | "amber" | "rose" };
  llano: string; // 1-2 frases "explicado para cualquiera"
  cotidiano: string; // 1 frase modo "¿cómo me afecta?"
  fuente: { ref: string; href: string };
  parte: 1 | 2 | 3 | 4;
}

export const CATEGORIAS: CategoriaDef[] = [
  {
    id: "poder-justicia",
    label: "Poder y justicia",
    descripcion: "Quién nombra a los jueces y fiscales, y qué pasa cuando juzgan al poder.",
  },
  {
    id: "reglas-juego",
    label: "Reglas del juego",
    descripcion: "Cómo se vota, quién puede acusar y quién paga a los partidos.",
  },
  {
    id: "estado-gasto",
    label: "Estado y gasto",
    descripcion: "La maquinaria: deuda, plantilla, normas y entes que pagas aunque no los veas.",
  },
  {
    id: "bolsillo",
    label: "Tu bolsillo",
    descripcion: "Paro, pensiones, vivienda, impuestos e inflación: la factura en tu nómina.",
  },
  {
    id: "europa",
    label: "Europa / UE",
    descripcion: "El nivel que no votas: Bruselas, tus mensajes, tu identidad y tu dinero.",
  },
];

export const PROBLEMAS: Problema[] = [
  /* ── Parte 1 · Las reglas del juego ── */
  {
    id: "nombramientos",
    categoria: "poder-justicia",
    titulo: "Jueces nombrados por políticos",
    stat: { value: "20", label: "vocales del CGPJ con designación de origen parlamentario", tone: "amber" },
    llano:
      "El órgano que gobierna a los jueces —ascensos, destinos, sanciones— lo eligen las Cortes, es decir, los partidos. Europa lleva años pidiendo que a los vocales jueces los elijan los propios jueces.",
    cotidiano:
      "Si un caso roza al partido que impulsó el nombramiento de la cúpula judicial, ¿quién vigila al vigilante?",
    fuente: { ref: "Constitución, art. 122 · GRECO", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1978-31229" },
    parte: 1,
  },
  {
    id: "fiscalia",
    categoria: "poder-justicia",
    titulo: "La Fiscalía depende del Gobierno",
    stat: { value: "art. 124", unit: "CE", label: "el Fiscal General lo propone el Gobierno; el Ministerio Fiscal es jerárquico", tone: "amber" },
    llano:
      "El jefe de todos los fiscales lo propone el Gobierno, y los fiscales funcionan por dependencia jerárquica. Los fiscales deciden a quién se acusa — también cuando el investigado está cerca del propio Gobierno.",
    cotidiano:
      "Cuando el investigado es el poder, el acusador público depende jerárquicamente… del poder.",
    fuente: { ref: "Constitución, art. 124 · Ley 50/1981", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1978-31229" },
    parte: 1,
  },
  {
    id: "aforamiento",
    categoria: "poder-justicia",
    titulo: "17.600 aforados institucionales",
    stat: { value: "~17.600", label: "políticos, jueces y fiscales aforados — ~250.000 si se cuenta a policías con aforamiento parcial", tone: "rose" },
    llano:
      "Miles de cargos no son juzgados por el tribunal ordinario que te juzgaría a ti, sino por uno superior. En la mayoría de Europa esto se reserva a un puñado de altos cargos: Francia afora a menos de 20 personas.",
    cotidiano:
      "Si tú y un diputado cometéis el mismo delito, no os juzga el mismo tribunal.",
    fuente: { ref: "Constitución, arts. 71.3 y 102 · LOPJ", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1978-31229" },
    parte: 1,
  },
  {
    id: "sistema-electoral",
    categoria: "reglas-juego",
    titulo: "Listas cerradas y voto desigual",
    stat: { value: "0", label: "nombres puedes cambiar en la papeleta: la lista la ordena el partido", tone: "amber" },
    llano:
      "No eliges a tu diputado: votas una lista bloqueada que ordena el partido. Y por el reparto provincial, un escaño cuesta muchos menos votos en Soria que en Madrid.",
    cotidiano:
      "Tu diputado le debe el escaño a quien hizo la lista, no a ti. Adivina a quién responde.",
    fuente: { ref: "LOREG, arts. 162-163 (D'Hondt)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1985-11672" },
    parte: 1,
  },
  {
    id: "acusacion-popular",
    categoria: "reglas-juego",
    titulo: "La acusación popular, en reforma",
    stat: { value: "art. 125", unit: "CE", label: "permite a cualquier ciudadano acusar; el proyecto de nueva LECrim la acota", tone: "accent" },
    llano:
      "Cualquier ciudadano puede sostener una acusación penal aunque la Fiscalía no quiera. El proyecto de nueva LECrim, ya en las Cortes, la acota.",
    cotidiano:
      "Hoy cualquier ciudadano puede personarse como acusación en un proceso penal aunque no sea víctima; la reforma limita quién puede hacerlo y por qué delitos.",
    fuente: { ref: "Constitución, art. 125 · LECrim 101 y 270", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1882-6036" },
    parte: 1,
  },
  {
    id: "democracia-directa",
    categoria: "reglas-juego",
    titulo: "Sin frenos ciudadanos a las leyes",
    stat: { value: "4", unit: "de 143", label: "iniciativas legislativas populares han prosperado desde 1986", tone: "rose" },
    llano:
      "En España no hay ninguna vía para que los ciudadanos frenen una ley: el referéndum es solo consultivo y lo convoca el Presidente. En Suiza, 50.000 firmas bastan para someter cualquier ley a votación vinculante.",
    cotidiano:
      "Si el Parlamento aprueba una ley que detestas, no puedes hacer nada hasta las siguientes elecciones — un suizo sí.",
    fuente: { ref: "Constitución, arts. 92 y 87.3 · Maldita (ILP)", href: "https://maldita.es/malditodato/20240411/iniciativa-legislativa-popular-aprobadas-presentadas/" },
    parte: 1,
  },
  {
    id: "decretazo",
    categoria: "reglas-juego",
    titulo: "Gobernar por decreto",
    stat: { value: "95", unit: "RDL", label: "reales decretos-leyes aprobó el Gobierno solo en la XIV legislatura (2019-2023)", tone: "amber" },
    llano:
      "El decreto-ley está pensado para urgencias, pero se ha vuelto la vía normal de legislar: entra en vigor sin debate parlamentario previo y el Congreso solo puede convalidarlo o tumbarlo en bloque, sin enmendarlo.",
    cotidiano:
      "Normas que tocan tus impuestos o tu contrato entran en vigor de un día para otro, sin que tu diputado las haya podido debatir.",
    fuente: { ref: "Constitución, art. 86 · Congreso (estadística de leyes)", href: "https://app.congreso.es/est_leyes/" },
    parte: 1,
  },
  /* ── Parte 2 · La maquinaria del Estado ── */
  {
    id: "deuda-publica",
    categoria: "estado-gasto",
    titulo: "Deuda: la factura aplazada",
    stat: { value: "1,74", unit: "B€", label: "de deuda pública — el 101% del PIB (abril 2026)", tone: "rose" },
    llano:
      "Cada año que el Estado gasta más de lo que ingresa, la diferencia se apunta a tu nombre. Debemos 1,74 billones —un año entero de PIB— y solo en intereses ya se van más de 40.000 millones al año.",
    cotidiano:
      "Tocas a unos 35.000 € de deuda pública por cabeza — también cada recién nacido.",
    fuente: { ref: "Banco de España — deuda PDE, abril 2026", href: "https://www.bde.es/wbe/es/noticias-eventos/actualidad-banco-espana/deuda-aapp-abril26.html" },
    parte: 2,
  },
  {
    id: "empleo-publico",
    categoria: "estado-gasto",
    titulo: "Récord de empleo público",
    stat: { value: "3,66", unit: "M", label: "empleados públicos (EPA 1T 2026), máximo de la serie", tone: "amber" },
    llano:
      "Nunca hubo tantos empleados públicos, rodeados de más de 19.300 entes públicos y un millar de asesores de confianza. No es solo sanidad o educación.",
    cotidiano:
      "Pagas la nómina de 3,66 millones de empleados públicos y el funcionamiento de más de 19.300 entes públicos.",
    fuente: { ref: "EPA (INE) · Inventario de Entes (Hacienda)", href: "https://www.ine.es/dyngs/Prensa/EPA1T26.htm" },
    parte: 2,
  },
  {
    id: "hiperregulacion",
    categoria: "estado-gasto",
    titulo: "La avalancha normativa",
    stat: { value: "3.557", unit: "págs/día", label: "publicaron los boletines oficiales en 2024 (según CEOE)", tone: "amber" },
    llano:
      "Los boletines oficiales publicaron 1,3 millones de páginas en 2024 — unas 3.557 al día, según CEOE. Nadie puede cumplir lo que nadie puede ni leer, y la maraña encarece cada negocio y cada trámite.",
    cotidiano:
      "Cada trámite tuyo y cada precio que pagas cargan con una maraña que ni los funcionarios dominan.",
    fuente: { ref: "CEOE — La producción normativa en 2024", href: "https://www.ceoe.es/es/ceoe-news/empresa/el-gobierno-aprobo-719-normas-en-2024-un-5-mas-que-el-ano-anterior-pese-la" },
    parte: 2,
  },
  {
    id: "justicia-lenta",
    categoria: "poder-justicia",
    titulo: "Justicia lenta, poder cómodo",
    stat: { value: "5,5", unit: "años", label: "estuvo caducado el CGPJ sin que los partidos lo renovaran", tone: "rose" },
    llano:
      "Hay 4,7 millones de asuntos atascados en los tribunales (cierre de 2025) y el gobierno de los jueces estuvo caducado 5 años y medio porque a los partidos no les urgía. La justicia lenta favorece siempre al que tiene más poder y más abogados.",
    cotidiano:
      "Si te deben dinero, esperarás años una sentencia; mientras, los casos de corrupción se acercan a la prescripción.",
    fuente: { ref: "CGPJ — estadística judicial (La Justicia Dato a Dato)", href: "https://www.poderjudicial.es/cgpj/es/Temas/Estadistica-Judicial/Estudios-e-Informes/Justicia-Dato-a-Dato/" },
    parte: 2,
  },
  {
    id: "partitocracia",
    categoria: "reglas-juego",
    titulo: "Partidos pagados con tus impuestos",
    stat: { value: "~53", unit: "M€/año", label: "de subvención ordinaria a los partidos (cálculo sobre la tabla del BOE)", tone: "amber" },
    llano:
      "Partidos, sindicatos y fundaciones de partido viven de subvenciones públicas que ellos mismos aprueban en los Presupuestos. Quien debería vigilar el gasto, cobra del gasto.",
    cotidiano:
      "Financias con tus impuestos a todos los partidos — también a los que jamás votarías.",
    fuente: { ref: "BOE — financiación de partidos · Tribunal de Cuentas", href: "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2025-693" },
    parte: 2,
  },
  {
    id: "transparencia",
    categoria: "reglas-juego",
    titulo: "Transparencia sin dientes",
    stat: { value: "~1.400", label: "resoluciones del Consejo de Transparencia incumplidas por las administraciones", tone: "amber" },
    llano:
      "Pagas cada euro del presupuesto, pero si pides ver cómo se gasta pueden ignorarte: el Consejo de Transparencia ordena entregar la información y las administraciones incumplen sin consecuencia, porque el órgano no puede sancionar.",
    cotidiano:
      "Eres el jefe que paga la nómina de los políticos, pero si les pides las cuentas pueden no dártelas — y no pasa nada.",
    fuente: { ref: "CTBG · Infobae (14-ene-2026)", href: "https://www.infobae.com/espana/agencias/2026/01/14/el-gobierno-incumplio-una-de-cada-diez-resoluciones-del-consejo-de-transparencia-en-2025/" },
    parte: 2,
  },

  /* ── Parte 3 · Tu bolsillo ── */
  {
    id: "mercado-laboral",
    categoria: "bolsillo",
    titulo: "Paro crónico, el doble que Europa",
    stat: { value: "10,3", unit: "%", label: "de paro (mayo 2026) — la media de la UE es el 5,9%", tone: "rose" },
    llano:
      "España pasó años como campeona del paro de la UE (hoy es segunda, tras Finlandia) y casi dobla la media europea. El juvenil: 23,7%. No es mala suerte: es un mercado laboral que penaliza contratar.",
    cotidiano:
      "Casi uno de cada cuatro jóvenes que busca trabajo no lo encuentra.",
    fuente: { ref: "Eurostat — desempleo armonizado, mayo 2026", href: "https://ec.europa.eu/eurostat/web/products-euro-indicators/w/3-02072026-ap" },
    parte: 3,
  },
  {
    id: "pensiones",
    categoria: "bolsillo",
    titulo: "Pensiones sin fondo",
    stat: { value: "52.990", unit: "M€", label: "de transferencias del Estado para sostener la Seguridad Social en 2025", tone: "rose" },
    llano:
      "Tu cotización no se guarda para tu pensión: se gasta hoy en las pensiones de hoy. En 2025 el Estado transfirió 52.990 M€ al sistema y aun así cerró con 7.387 M€ de déficit — ese sostén se paga con impuestos y deuda, que también pagarás tú.",
    cotidiano:
      "Hoy hay unos 2,4 trabajadores por pensionista, y bajando. Cuando te jubiles, la pirámide será peor.",
    fuente: { ref: "Min. Inclusión — ejecución presupuestaria SS 2025", href: "https://www.lamoncloa.gob.es/serviciosdeprensa/notasprensa/inclusion/paginas/2026/310326-seguridad-social-deficit-pib.aspx" },
    parte: 3,
  },
  {
    id: "vivienda",
    categoria: "bolsillo",
    titulo: "Vivienda: oferta estrangulada",
    stat: { value: "8,0", unit: "años", label: "de renta bruta del hogar para comprar una vivienda (1T 2026)", tone: "amber" },
    llano:
      "Se construye una sexta parte de lo que en 2006 y casi todo el suelo está vetado a edificar. El Estado se lleva en torno al 10% de cada compra: cuanto más cara, más recauda.",
    cotidiano:
      "Necesitas ocho años de renta íntegra —sin comer ni pagar nada más— para un piso.",
    fuente: { ref: "Banco de España — Síntesis de Indicadores 1.5 (mercado inmobiliario)", href: "https://www.bde.es/webbe/es/estadisticas/compartido/datos/pdf/si_1_5.pdf" },
    parte: 3,
  },
  {
    id: "impuestos",
    categoria: "bolsillo",
    titulo: "El 40% de tu trabajo, para el Estado",
    stat: { value: "41,4", unit: "%", label: "del coste de tu trabajo se lo queda el Estado (media OCDE: 35,1%)", tone: "amber" },
    llano:
      "De lo que cuesta tu trabajo a la empresa, el Estado se queda el 41,4% entre IRPF y cotizaciones — antes de que toques un euro, y más que la media de los países ricos (10.º de 38 en la OCDE).",
    cotidiano:
      "De cada 100 € que cuestas a tu empresa, a tu cuenta llegan unos 60.",
    fuente: { ref: "OCDE — Taxing Wages 2026", href: "https://www.oecd.org/en/publications/taxing-wages-2026_3a5169ef-en.html" },
    parte: 3,
  },
  {
    id: "poder-adquisitivo",
    categoria: "bolsillo",
    titulo: "El impuesto silencioso",
    stat: { value: "+23,5", unit: "%", label: "de inflación acumulada 2020-2025; el salario real, casi plano", tone: "rose" },
    llano:
      "Los precios subieron un 23,5% en cinco años y tu salario real casi nada. Y como los tramos del IRPF no se ajustan a la inflación, pagas más impuesto sin ser más rico.",
    cotidiano:
      "La compra de 2020 te cuesta hoy bastante más; la subida que 'te compensó' te subió de tramo en el IRPF.",
    fuente: { ref: "INE — calculadora del IPC", href: "https://www.ine.es/varipc/" },
    parte: 3,
  },

  /* ── Parte 4 · Bruselas: el poder sin urnas ── */
  {
    id: "soberania-ue",
    categoria: "europa",
    titulo: "Un gobierno que nadie vota",
    stat: { value: "0", unit: "votos", label: "ciudadanos han elegido a la presidenta de la Comisión, el único órgano que propone las leyes de la UE", tone: "rose" },
    llano:
      "Las normas europeas que acaban rigiendo en España solo puede proponerlas la Comisión (art. 17 TUE), cuya presidencia pactan los gobiernos: en 2019 Von der Leyen ni siquiera fue candidata en las elecciones europeas.",
    cotidiano:
      "Buena parte de las normas que cumples cada día nacen de un órgano al que jamás has podido votar — ni echar.",
    fuente: { ref: "Tratado de la UE, art. 17 (EUR-Lex)", href: "https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:12016M017" },
    parte: 4,
  },
  {
    id: "pfizergate",
    categoria: "europa",
    titulo: "Pfizergate: el contrato por SMS",
    stat: { value: "~35.000", unit: "M€", label: "en vacunas negociadas por SMS que la Comisión se niega a enseñar", tone: "rose" },
    llano:
      "La presidenta de la Comisión negoció por SMS con el CEO de Pfizer el mayor contrato de vacunas de la UE (hasta 1.800 millones de dosis). La Comisión se negó a enseñar los mensajes; la Justicia europea anuló esa negativa en 2025 — y siguen sin publicarse.",
    cotidiano:
      "Se compraron con tu dinero 1.800 millones de dosis y no puedes leer cómo se cerró el trato.",
    fuente: { ref: "Tribunal General de la UE — sentencia T-36/23 (14-may-2025)", href: "https://curia.europa.eu/juris/liste.jsf?num=T-36/23" },
    parte: 4,
  },
  {
    id: "chat-control",
    categoria: "europa",
    titulo: "Chat Control: mensajes escaneados",
    stat: { value: "314", unit: "votos", label: "contra la prórroga del escaneo (vs 276) no bastaron: rechazarla exigía mayoría absoluta — se aprobó «por defecto»", tone: "rose" },
    llano:
      "Bruselas lleva desde 2022 intentando que se escaneen las comunicaciones privadas. En julio de 2026 el Parlamento Europeo prorrogó el escaneo «voluntario» (Chat Control 1.0) por la puerta de atrás: urgencia en vísperas de vacaciones y una regla que exigía mayoría absoluta (361) para rechazarlo — 314 votos en contra frente a 276 no bastaron. El escaneo obligatorio (CSAR) sigue negociándose, y los borradores filtrados eximen del escáner a policía, inteligencia y militares.",
    cotidiano:
      "La conversación con tu pareja o con tu médico pasaría por un detector automático antes de llegar a su destino — un detector del que sus impulsores se declaran exentos.",
    fuente: { ref: "Euronews — «Chat Control 1.0 passed the European Parliament — through the back door» (10-jul-2026)", href: "https://www.euronews.com/next/2026/07/10/chat-control-10-passed-the-european-parliament-through-the-back-door" },
    parte: 4,
  },
  {
    id: "identidad-digital",
    categoria: "europa",
    titulo: "Identidad digital para todo",
    stat: { value: "dic 2026", label: "fecha límite para que cada Estado ofrezca la cartera europea de identidad digital (eIDAS 2)", tone: "amber" },
    llano:
      "Cada Estado debe ofrecer una cartera digital de identidad y el sector público aceptarla. Oficialmente es voluntaria para el ciudadano; los críticos avisan de que cuando banca, sanidad y administración la pidan, la opción habrá desaparecido.",
    cotidiano:
      "Identificarte en cada vez más ventanillas — públicas y privadas — podría acabar pasando por una única app trazable.",
    fuente: { ref: "Reglamento (UE) 2024/1183 (EUR-Lex)", href: "https://eur-lex.europa.eu/eli/reg/2024/1183/oj" },
    parte: 4,
  },
  {
    id: "euro-digital",
    categoria: "europa",
    titulo: "El euro digital del BCE",
    stat: { value: "3.000", unit: "€", label: "de tenencia máxima prevista para el euro digital; emisión a gran escala orientada a 2029", tone: "amber" },
    llano:
      "El BCE prepara una moneda digital de banco central. Promete que no será programable y que él no verá tus pagos — pero los intermediarios sí los ven, y el efectivo, el único dinero anónimo, pierde terreno cada año.",
    cotidiano:
      "Un dinero que deja rastro por diseño va sustituyendo poco a poco al único que no lo deja: el efectivo.",
    fuente: { ref: "BCE — proyecto del euro digital (calendario oficial)", href: "https://www.ecb.europa.eu/euro/digital_euro/progress/html/index.es.html" },
    parte: 4,
  },
  {
    id: "cuentas-ue",
    categoria: "europa",
    titulo: "El gasto que el auditor no aprueba",
    stat: { value: "6º", unit: "año", label: "consecutivo con opinión desfavorable del Tribunal de Cuentas Europeo — error del 3,6% (~6.000 M€) en 2024", tone: "rose" },
    llano:
      "El auditor de la UE lleva seis años seguidos negándose a dar por bueno el gasto del presupuesto europeo. Y en el Parlamento Europeo, el Qatargate destapó bolsas con 1,5 millones en efectivo — el caso sigue sin condena firme.",
    cotidiano:
      "De cada 100 € que gasta la UE — también los tuyos —, unos 3,6 se pagaron saltándose las reglas.",
    fuente: { ref: "Tribunal de Cuentas Europeo — Informe Anual 2024", href: "https://www.eca.europa.eu/en/publications/AR-2024" },
    parte: 4,
  },
  {
    id: "privilegios-ue",
    categoria: "europa",
    titulo: "Los privilegios de la Eurocámara",
    stat: { value: "310", unit: "M€", label: "de agujero en el fondo de pensiones voluntario que los eurodiputados se crearon para sí", tone: "amber" },
    llano:
      "El fondo de pensiones extra que los eurodiputados se montaron en 1991 — dos tercios lo pagaba la Cámara — quebró con 310 millones de agujero. Se recortó a la mitad, pero decenas de millones los cubrirá igualmente el presupuesto público.",
    cotidiano:
      "Tu pensión depende de que las cuentas cuadren; la suya, de que tú se las cuadres.",
    fuente: { ref: "Investigate Europe / infoLibre", href: "https://www.investigate-europe.eu/es/posts/meps-face-50-cut-to-luxury-pensions-but-public-still-set-for-hefty-bill" },
    parte: 4,
  },
];
