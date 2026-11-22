/* Glosario judicial (spec 036) — data file editorial, mismo patrón que
 * lib/institucional.ts: el contenido vive aquí tipado y la página lo renderiza.
 *
 * INVARIANTES:
 *  · Los `id` de la categoría "estados" son EXACTAMENTE las claves de
 *    STATUS_META (lib/graphStyle.ts) — los badges de estado enlazan con ellos.
 *  · Los `id` de la categoría "delitos" son el slug (slugifyTermino) del
 *    `name` de cada Crime de pipeline/data/accusations/accusations.yaml —
 *    los chips de delito de fichas/directorio enlazan con ellos.
 *  · Lenguaje llano y neutral: qué significa el término, nunca a quién se
 *    aplica. Ninguna persona ni caso concreto en este archivo.
 */

export type CategoriaGlosario = "estados" | "delitos" | "instituciones" | "procedimiento";

export interface CategoriaGlosarioDef {
  id: CategoriaGlosario;
  titulo: string;
  intro: string;
}

export interface Termino {
  /** Ancla estable (== id HTML de la entrada). */
  id: string;
  termino: string;
  categoria: CategoriaGlosario;
  /** Una frase, sin jerga. */
  corta: string;
  /** 2-4 frases de desarrollo, sin jerga. */
  larga: string;
  /** Texto legal u oficial de referencia. */
  fuente?: { ref: string; href: string };
  /** Anclas de términos relacionados. */
  relacionados?: string[];
}

/** Slug compartido para estados y nombres de delito: minúsculas, sin tildes,
 *  espacios → guiones. "tráfico de influencias" → "trafico-de-influencias". */
export function slugifyTermino(raw: string): string {
  return raw
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Href de glosario para un estado procesal o un nombre de delito. */
export function glosarioHref(statusOrCrime: string): string {
  // 'condenado_no_firme' (spec 059) no es un término propio: enlaza a «sentencia firme».
  if (statusOrCrime === "condenado_no_firme") return "/glosario#sentencia-firme";
  return `/glosario#${slugifyTermino(statusOrCrime)}`;
}

const CP = "https://www.boe.es/buscar/act.php?id=BOE-A-1995-25444"; // Código Penal (LO 10/1995, consolidado)
const LECRIM = "https://www.boe.es/buscar/act.php?id=BOE-A-1882-6036"; // Ley de Enjuiciamiento Criminal (consolidada)

export const CATEGORIAS_GLOSARIO: CategoriaGlosarioDef[] = [
  {
    id: "estados",
    titulo: "Estados procesales",
    intro:
      "La escalera por la que avanza (o no) una causa contra una persona. Hasta la condena firme, todos los peldaños son compatibles con la inocencia: rige la presunción de inocencia.",
  },
  {
    id: "delitos",
    titulo: "Delitos",
    intro:
      "Los tipos penales que aparecen en las causas del sitio, con su artículo del Código Penal. Que un delito se investigue o se impute no significa que se haya cometido.",
  },
  {
    id: "instituciones",
    titulo: "Instituciones",
    intro: "Quién es quién entre los organismos que investigan, acusan y juzgan.",
  },
  {
    id: "procedimiento",
    titulo: "Procedimiento",
    intro: "Las piezas del proceso penal español que van apareciendo en autos y noticias.",
  },
];

export const GLOSARIO: Termino[] = [
  /* ── Estados procesales (ids == claves de STATUS_META) ─────────────────── */
  {
    id: "investigado",
    termino: "Investigado",
    categoria: "estados",
    corta: "La justicia indaga si participó en los hechos; no pesa acusación formal.",
    larga:
      "Es la persona a la que el juez de instrucción atribuye una posible participación en los hechos que investiga. Desde 2015 este término sustituye legalmente a «imputado». Estar investigado da derechos (declarar asistido de abogado, conocer la causa) y no supone culpabilidad alguna: muchas investigaciones se archivan sin más.",
    fuente: { ref: "LO 13/2015 (reforma LECrim)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-2015-10725" },
    relacionados: ["imputado", "instruccion", "archivado"],
  },
  {
    id: "imputado",
    termino: "Imputado",
    categoria: "estados",
    corta: "Nombre tradicional de «investigado»: sigue siendo una fase de indagación, no una condena.",
    larga:
      "Término clásico, todavía de uso común en prensa, para la persona formalmente señalada en una instrucción. Desde la reforma de 2015 la ley lo llama «investigado» (y «encausado» en fases posteriores). Ser imputado significa que el juez ve motivos para investigar a esa persona con garantías — nada más. La presunción de inocencia rige íntegra.",
    fuente: { ref: "LO 13/2015 (reforma LECrim)", href: "https://www.boe.es/buscar/act.php?id=BOE-A-2015-10725" },
    relacionados: ["investigado", "procesado"],
  },
  {
    id: "procesado",
    termino: "Procesado",
    categoria: "estados",
    corta: "El juez aprecia indicios racionales de delito y dicta auto de procesamiento.",
    larga:
      "En el procedimiento penal ordinario (el sumario, reservado a delitos graves), el juez dicta un auto de procesamiento cuando aprecia «algún indicio racional de criminalidad» contra alguien. Es un escalón más que la mera investigación, pero sigue siendo un juicio provisional sobre indicios: el procesado no ha sido juzgado y puede ser absuelto.",
    fuente: { ref: "art. 384 LECrim", href: `${LECRIM}#a384` },
    relacionados: ["investigado", "acusado", "auto"],
  },
  {
    id: "acusado",
    termino: "Acusado",
    categoria: "estados",
    corta: "La fiscalía (u otra acusación) pide formalmente penas contra él y la causa va a juicio.",
    larga:
      "Cuando la instrucción termina, las acusaciones presentan su escrito de acusación: los hechos que atribuyen a la persona, su calificación penal y las penas que piden. Con la apertura del juicio oral, el investigado pasa a ser acusado. Las penas «solicitadas» son la petición de una parte, no una decisión judicial: el tribunal puede condenar por menos o absolver.",
    fuente: { ref: "arts. 650 y ss. LECrim", href: `${LECRIM}#a650` },
    relacionados: ["escrito-de-acusacion", "juicio-oral", "condenado"],
  },
  {
    id: "condenado",
    termino: "Condenado",
    categoria: "estados",
    corta: "Un tribunal le ha declarado culpable en sentencia; es firme cuando ya no cabe recurso.",
    larga:
      "La condena es la única fase en la que la presunción de inocencia deja de operar — y solo cuando la sentencia es firme, es decir, cuando ya no puede recurrirse. Una condena recurrida puede ser revocada. En este sitio «condenado» se reserva a condenas y se distingue siempre de los estados anteriores.",
    relacionados: ["sentencia-firme", "absuelto"],
  },
  {
    id: "condenado-no-firme",
    termino: "Condenado (no firme)",
    categoria: "estados",
    corta: "Una sentencia le condena, pero cabe recurso o está recurrida; sigue rigiendo la presunción de inocencia.",
    larga:
      "Una sentencia condenatoria que todavía puede recurrirse, o que ya está recurrida, no es firme: un tribunal superior puede confirmarla, rebajarla o anularla. Hasta que sea firme, la persona no es culpable a efectos legales. Este sitio la distingue siempre de «condenado», que se reserva a la condena firme.",
    relacionados: ["sentencia-firme", "condenado", "condena-anulada"],
  },
  {
    id: "absuelto",
    termino: "Absuelto",
    categoria: "estados",
    corta: "Juzgado y declarado no culpable.",
    larga:
      "El tribunal celebró el juicio y concluyó que no procede condenar: porque los hechos no se probaron, no son delito o no los cometió el acusado. La absolución confirma lo que la presunción de inocencia ya garantizaba durante todo el proceso.",
    relacionados: ["condenado", "sobreseimiento"],
  },
  {
    id: "archivado",
    termino: "Archivado",
    categoria: "estados",
    corta: "La causa (o la parte que afectaba a esa persona) se cerró sin juicio.",
    larga:
      "El juez pone fin a la investigación mediante sobreseimiento: libre (equivale a una absolución anticipada) o provisional (puede reabrirse si aparecen nuevos indicios). Una causa archivada no deja «culpables a medias»: sin juicio no hay culpabilidad.",
    fuente: { ref: "arts. 634 y ss. LECrim", href: `${LECRIM}#a634` },
    relacionados: ["sobreseimiento", "investigado"],
  },

  {
    id: "condena-anulada",
    termino: "Condena anulada (TC)",
    categoria: "estados",
    corta: "El Tribunal Constitucional anuló la condena y ordenó dictar una sentencia nueva; no hay condena vigente.",
    larga:
      "El Tribunal Constitucional otorga el amparo cuando una condena vulnera un derecho fundamental. Si anula la condena y devuelve la causa al tribunal que juzgó, ese tribunal tiene que dictar una sentencia nueva. Mientras tanto no hay condena vigente y rige la presunción de inocencia.",
    relacionados: ["sentencia-firme", "absuelto"],
  },

  /* ── Delitos (ids == slug del Crime.name de accusations.yaml) ──────────── */
  {
    id: "organizacion-criminal",
    termino: "Organización criminal",
    categoria: "delitos",
    corta: "Grupo estable de dos o más personas que se reparte tareas para cometer delitos.",
    larga:
      "El Código Penal castiga pertenecer a (y sobre todo dirigir) una agrupación estable, con reparto de funciones, formada para cometer delitos. Es el tipo que convierte una suma de delitos individuales en una trama. No exige violencia: una red de comisiones y adjudicaciones amañadas puede serlo.",
    fuente: { ref: "art. 570 bis CP", href: `${CP}#a570bis` },
    relacionados: ["cohecho", "trafico-de-influencias"],
  },
  {
    id: "cohecho",
    termino: "Cohecho",
    categoria: "delitos",
    corta: "El soborno: un cargo público recibe (o pide) algo a cambio de su función.",
    larga:
      "Comete cohecho la autoridad o funcionario que recibe, solicita o acepta una dádiva o ventaja a cambio de actuar (o de no actuar) en el ejercicio de su cargo — y también el particular que la ofrece o entrega (cohecho activo). Es el delito nuclear de la corrupción: dinero, regalos o favores a cambio de decisiones públicas.",
    fuente: { ref: "arts. 419–427 bis CP", href: `${CP}#a419` },
    relacionados: ["trafico-de-influencias", "malversacion"],
  },
  {
    id: "trafico-de-influencias",
    termino: "Tráfico de influencias",
    categoria: "delitos",
    corta: "Usar una relación de poder o cercanía para torcer una decisión pública en beneficio propio o ajeno.",
    larga:
      "Influye ilícitamente quien se aprovecha de su cargo, o de su relación personal con quien lo tiene, para conseguir una resolución que genere un beneficio económico. A diferencia del cohecho no hace falta soborno: basta la presión por la vía de la cercanía. También se castiga ofrecerse a hacerlo cobrando.",
    fuente: { ref: "arts. 428–430 CP", href: `${CP}#a428` },
    relacionados: ["cohecho", "prevaricacion"],
  },
  {
    id: "malversacion",
    termino: "Malversación",
    categoria: "delitos",
    corta: "Un gestor público desvía o da un destino indebido a dinero o bienes públicos.",
    larga:
      "La comete la autoridad o funcionario que, teniendo a su cargo patrimonio público, se lo apropia, consiente que otro lo haga o le da un uso privado o distinto del previsto. Es «robar (o dejar robar) la caja común». La pena crece con la cuantía y con el daño al servicio público.",
    fuente: { ref: "arts. 432 y ss. CP", href: `${CP}#a432` },
    relacionados: ["apropiacion-indebida", "prevaricacion"],
  },
  {
    id: "blanqueo-de-capitales",
    termino: "Blanqueo de capitales",
    categoria: "delitos",
    corta: "Dar apariencia legal a dinero que procede de un delito.",
    larga:
      "Blanquea quien adquiere, convierte, transmite u oculta bienes sabiendo que proceden de una actividad delictiva, para disimular su origen. Facturas por servicios inexistentes, sociedades pantalla o compras en efectivo son mecanismos típicos. Se castiga aunque el delito de origen lo cometiera otro.",
    fuente: { ref: "art. 301 CP", href: `${CP}#a301` },
    relacionados: ["organizacion-criminal", "falsedad-documental"],
  },
  {
    id: "prevaricacion",
    termino: "Prevaricación",
    categoria: "delitos",
    corta: "Un cargo público dicta a sabiendas una resolución injusta.",
    larga:
      "La comete la autoridad o funcionario que, sabiendo que es arbitraria, dicta una resolución injusta en un asunto administrativo — por ejemplo, una adjudicación o un nombramiento contrarios a derecho hechos a conciencia. No lleva prisión sino inhabilitación: la pena es perder el poder que se usó mal.",
    fuente: { ref: "art. 404 CP", href: `${CP}#a404` },
    relacionados: ["trafico-de-influencias", "malversacion"],
  },
  {
    id: "falsedad-documental",
    termino: "Falsedad documental",
    categoria: "delitos",
    corta: "Crear o alterar documentos para que digan lo que no es.",
    larga:
      "Falsificar un documento público, oficial o mercantil — o hacer que uno auténtico falte a la verdad en algo relevante — es delito tanto para el funcionario como, con penas menores, para el particular. En las tramas de corrupción suele ser el delito instrumental: contratos, facturas o actas retocados para tapar el resto.",
    fuente: { ref: "arts. 390–399 CP", href: `${CP}#a390` },
    relacionados: ["blanqueo-de-capitales"],
  },
  {
    id: "corrupcion-en-los-negocios",
    termino: "Corrupción en los negocios",
    categoria: "delitos",
    corta: "Sobornos entre particulares: pagar o cobrar por favorecer a alguien en una relación comercial.",
    larga:
      "Es el «cohecho privado»: directivos, administradores o empleados que reciben o prometen un beneficio no justificado para favorecer indebidamente a otro en la contratación o en relaciones comerciales. No hace falta que intervenga la Administración — protege la competencia leal entre empresas.",
    fuente: { ref: "art. 286 bis CP", href: `${CP}#a286bis` },
    relacionados: ["cohecho"],
  },
  {
    id: "apropiacion-indebida",
    termino: "Apropiación indebida",
    categoria: "delitos",
    corta: "Quedarse con dinero o bienes que se recibieron legítimamente para otro fin.",
    larga:
      "La comete quien recibe dinero o cosas por título que obliga a devolverlos o a darles un uso concreto (un depósito, una administración, un encargo) y se los apropia o niega haberlos recibido. La diferencia con el robo es que aquí la cosa llegó a sus manos legalmente; el delito está en quedársela.",
    fuente: { ref: "art. 253 CP", href: `${CP}#a253` },
    relacionados: ["malversacion"],
  },
  {
    // Término de glosario sin nodo Crime desde 061 T1 (16-09-2026): la única arista que lo
    // sostenía era una calificación sin fuente y se retiró de ultima_hora.yaml.
    id: "obstruccion-a-la-justicia",
    termino: "Obstrucción a la justicia",
    categoria: "delitos",
    corta: "Presionar o comprar a testigos, peritos o partes para torcer lo que cuentan a la justicia.",
    larga:
      "El Código Penal castiga a quien intenta influir sobre un denunciante, parte, perito o testigo para que modifique su actuación en un procedimiento — desde la intimidación hasta el ofrecimiento de dinero por cambiar una declaración. Protege la prueba misma: sin testigos libres no hay juicio limpio. No exige que el autor sea funcionario, a diferencia del cohecho.",
    fuente: { ref: "art. 464 CP", href: `${CP}#a464` },
    relacionados: ["cohecho"],
  },

  /* ── Instituciones ─────────────────────────────────────────────────────── */
  {
    id: "uco",
    termino: "UCO — Unidad Central Operativa",
    categoria: "instituciones",
    corta: "La unidad de élite de investigación de la Guardia Civil; firma los grandes informes de estas causas.",
    larga:
      "Unidad central de la Policía Judicial de la Guardia Civil, especializada en delincuencia organizada, económica y corrupción. Trabaja a las órdenes de jueces y fiscales: sus informes (análisis de comunicaciones, patrimonio, contratos) son pieza central de la instrucción, pero son atestados policiales — indicios que el juez valora, no sentencias.",
    relacionados: ["instruccion", "audiencia-nacional"],
  },
  {
    id: "audiencia-nacional",
    termino: "Audiencia Nacional",
    categoria: "instituciones",
    corta: "Tribunal con sede en Madrid que centraliza las causas de gran corrupción y delincuencia organizada.",
    larga:
      "Órgano judicial con competencia en toda España para ciertos delitos graves: entre otros, la delincuencia económica que afecta a más de una provincia, la criminalidad organizada o el terrorismo. Sus Juzgados Centrales de Instrucción son los que instruyen muchas de las macrocausas de corrupción.",
    relacionados: ["tribunal-supremo", "diligencias-previas"],
  },
  {
    id: "tribunal-supremo",
    termino: "Tribunal Supremo (Sala Segunda)",
    categoria: "instituciones",
    corta: "La máxima instancia penal; juzga directamente a los aforados (diputados, senadores, ministros).",
    larga:
      "La Sala Segunda (de lo Penal) del Tribunal Supremo es el último escalón de la justicia penal española. Además de resolver recursos, instruye y juzga en única instancia las causas contra aforados — por eso las causas que afectan a diputados o ministros se tramitan allí como «causas especiales». Contra sus sentencias no cabe recurso ordinario: son firmes.",
    relacionados: ["aforado", "causa-especial", "sentencia-firme"],
  },
  {
    id: "fiscalia-anticorrupcion",
    termino: "Fiscalía Anticorrupción",
    categoria: "instituciones",
    corta: "La fiscalía especializada en los delitos económicos y de corrupción de mayor trascendencia.",
    larga:
      "La Fiscalía Especial contra la Corrupción y la Criminalidad Organizada interviene en los casos de especial trascendencia de cohecho, malversación, tráfico de influencias, fraudes o blanqueo. Es parte acusadora: investiga, pide diligencias y formula acusación — decide un juez o tribunal, no ella.",
    relacionados: ["escrito-de-acusacion", "querella"],
  },
  {
    id: "fiscalia-europea",
    termino: "Fiscalía Europea",
    categoria: "instituciones",
    corta: "La fiscalía de la UE; investiga los fraudes que afectan a fondos europeos.",
    larga:
      "Órgano independiente de la Unión Europea que investiga y acusa por delitos contra los intereses financieros de la UE: fraudes en subvenciones y contratos financiados con fondos europeos, corrupción o blanqueo asociados. Cuando un contrato investigado se pagó con fondos UE, puede asumir la causa o parte de ella.",
    relacionados: ["fiscalia-anticorrupcion"],
  },
  {
    id: "cgpj",
    termino: "CGPJ — Consejo General del Poder Judicial",
    categoria: "instituciones",
    corta: "El órgano de gobierno de los jueces: nombra, asciende y sanciona; no juzga.",
    larga:
      "Gobierna el Poder Judicial: hace los nombramientos discrecionales de la cúpula judicial (Tribunal Supremo, presidencias de tribunales), asciende, inspecciona y sanciona. No dicta sentencias. Su composición se elige con intervención decisiva de las Cortes, lo que alimenta el debate permanente sobre la politización de la justicia.",
    relacionados: ["tribunal-supremo"],
  },
  {
    id: "borme",
    termino: "BORME — Registro Mercantil",
    categoria: "instituciones",
    corta: "El boletín oficial donde se publican los actos de las empresas: administradores, capital, domicilios.",
    larga:
      "El Boletín Oficial del Registro Mercantil publica los actos inscritos de las sociedades: constituciones, nombramientos y ceses de administradores, ampliaciones de capital, disoluciones. Es la fuente de la capa societaria de este sitio. Recoge administradores registrales, no la titularidad real en la sombra: figurar no imputa y no figurar no exculpa.",
    fuente: { ref: "BORME (boe.es)", href: "https://www.boe.es/diario_borme/" },
  },
  {
    id: "contrato-menor",
    termino: "Contrato menor — PLACSP",
    categoria: "instituciones",
    corta: "Adjudicación directa sin concurso, bajo un umbral legal; pública en PLACSP desde 2018.",
    larga:
      "La Ley de Contratos del Sector Público permite adjudicar directamente, sin concurrencia, los contratos por debajo de un umbral (con carácter general 15.000 € en servicios y suministros y 40.000 € en obras, sin IVA), a cambio de publicarlos en la Plataforma de Contratación del Sector Público (PLACSP). Es un mecanismo legal — y el vector clásico de clientelismo cuando se abusa (fraccionamiento, adjudicatario habitual). La capa «Contratación» de este sitio muestra agregados oficiales por empresa y órgano (nº de contratos, total y rango temporal) — en morado los menores de PLACSP y en fucsia la capa ampliada de licitaciones y menores autonómicos —, distinta de la capa «Contrato» (adjudicaciones citadas en los documentos judiciales). Contratar con la administración no imputa: la señal es siempre el cruce con el caso.",
    fuente: { ref: "PLACSP — datos abiertos", href: "https://contrataciondelsectorpublico.gob.es/" },
    relacionados: ["borme", "licitacion-publica"],
  },
  {
    id: "licitacion-publica",
    termino: "Licitación — PLACSP y plataformas oficiales",
    categoria: "instituciones",
    corta: "El concurso público por el que se adjudican los contratos grandes; publicado con procedimiento, licitadores e importes.",
    larga:
      "Por encima de los umbrales del contrato menor, la Ley de Contratos del Sector Público obliga a licitar: publicar el contrato, recibir ofertas y adjudicar con un procedimiento reglado (abierto, restringido, negociado con o sin publicidad, o tramitación de emergencia en casos tasados). Todo queda publicado en PLACSP — también lo que licitan las plataformas autonómicas y locales que se agregan a ella — con el presupuesto, el adjudicatario, el importe final y el número de licitadores. La capa «Contratación» ampliada de este sitio agrega esas adjudicaciones por empresa y órgano, incluidas las señales de procedimiento (cuántas en emergencia, cuántas por negociado sin publicidad, cuáles vía UTE) y los contratos menores de las plataformas autonómicas (Junta de Andalucía, Euskadi, Xunta de Galicia). Licitar y ganar es legal — también en emergencia o por negociado, que son procedimientos previstos por la ley —: la señal es siempre el cruce con el caso y el patrón de procedimiento, nunca la adjudicación en sí.",
    fuente: { ref: "PLACSP — datos abiertos", href: "https://contrataciondelsectorpublico.gob.es/" },
    relacionados: ["contrato-menor", "borme"],
  },
  {
    id: "nombramiento-boe",
    termino: "Nombramiento — BOE (sección II.A)",
    categoria: "instituciones",
    corta: "El acto oficial por el que se nombra o cesa a un cargo público, publicado en el BOE con disposición citable.",
    larga:
      "La sección II.A del Boletín Oficial del Estado («Nombramientos, situaciones e incidencias») publica cada nombramiento y cese de cargos públicos — ministros, secretarios de Estado, directores generales, presidencias de entes públicos, carrera judicial y fiscal — con su real decreto, orden o acuerdo citable. Es la fuente de la capa «Cargos (BOE)» de este sitio: la trayectoria institucional de personas del caso, agregada por cargo (nombramiento → cese, con enlace a cada disposición). Un nombramiento es un acto administrativo legal y no imputa: la señal es la trayectoria cruzada con el caso — quién dirigía qué organismo cuándo. Un cargo sin cese en la capa significa que el cese no consta en el barrido (2004→hoy, solo títulos de la sección II.A), no necesariamente que siga vigente.",
    fuente: { ref: "BOE — datos abiertos (sumarios)", href: "https://www.boe.es/datosabiertos/" },
    relacionados: ["borme", "contrato-menor"],
  },

  /* ── Procedimiento ─────────────────────────────────────────────────────── */
  {
    id: "instruccion",
    termino: "Instrucción",
    categoria: "procedimiento",
    corta: "La fase de investigación de una causa penal, dirigida por un juez.",
    larga:
      "Es la etapa en la que un juez de instrucción reúne el material del caso: declara a investigados y testigos, encarga informes policiales y periciales, interviene comunicaciones, acuerda registros. Su objetivo es decidir si hay base para juzgar a alguien — no declarar culpabilidades. Buena parte de lo que publica la prensa sobre estas causas sale de esta fase.",
    relacionados: ["diligencias-previas", "auto", "investigado"],
  },
  {
    id: "diligencias-previas",
    termino: "Diligencias previas",
    categoria: "procedimiento",
    corta: "El expediente con el que arranca la investigación en el procedimiento abreviado.",
    larga:
      "Nombre técnico de la fase de instrucción del procedimiento abreviado (el cauce de la mayoría de delitos económicos). Cuando se lee «Diligencias Previas 77/2024», es el número de expediente de una investigación en curso — no una acusación ni una condena.",
    fuente: { ref: "arts. 774 y ss. LECrim", href: `${LECRIM}#a774` },
    relacionados: ["instruccion", "causa-especial"],
  },
  {
    id: "causa-especial",
    termino: "Causa especial",
    categoria: "procedimiento",
    corta: "Una causa tramitada directamente por el Tribunal Supremo porque afecta a aforados.",
    larga:
      "Cuando la investigación alcanza a un aforado (diputado, senador, ministro…), la competencia sube al Tribunal Supremo, que abre una «causa especial» con su propio número (por ejemplo, «Causa Especial 20775/2020»). Un magistrado de la Sala Segunda instruye y la propia Sala juzga en única instancia.",
    relacionados: ["tribunal-supremo", "aforado"],
  },
  {
    id: "aforado",
    termino: "Aforado",
    categoria: "procedimiento",
    corta: "Persona a la que, por su cargo, solo puede juzgarla un tribunal superior.",
    larga:
      "El aforamiento fija qué tribunal puede investigar y juzgar a ciertos cargos: a diputados, senadores y miembros del Gobierno, el Tribunal Supremo; a cargos autonómicos, los tribunales superiores de justicia. No es inmunidad — es un cambio de tribunal. Su efecto práctico: una única instancia, sin apelación ordinaria.",
    relacionados: ["causa-especial", "tribunal-supremo"],
  },
  {
    id: "auto",
    termino: "Auto",
    categoria: "procedimiento",
    corta: "Resolución motivada del juez durante el proceso; decide pasos, no culpabilidades.",
    larga:
      "Forma que adoptan las decisiones judiciales motivadas que no son sentencia: admitir una querella, acordar un registro, imputar, procesar, enviar a prisión provisional, archivar. Muchos de los documentos citados en este sitio son autos: fijan indicios y decisiones provisionales del instructor, no hechos probados.",
    relacionados: ["instruccion", "procesado"],
  },
  {
    id: "querella",
    termino: "Querella",
    categoria: "procedimiento",
    corta: "El escrito con el que alguien pide formalmente a un juez que investigue un delito, personándose como parte.",
    larga:
      "A diferencia de la denuncia (que solo pone hechos en conocimiento de la autoridad), el querellante se convierte en parte del proceso y puede pedir diligencias y acusar. Puede presentarla el fiscal, el perjudicado o — peculiaridad española — cualquier ciudadano como acción popular. Que se admita una querella solo abre la investigación.",
    fuente: { ref: "arts. 270 y ss. LECrim", href: `${LECRIM}#a270` },
    relacionados: ["acusacion-popular", "instruccion"],
  },
  {
    id: "acusacion-popular",
    termino: "Acusación popular",
    categoria: "procedimiento",
    corta: "La figura que permite a cualquier ciudadano u organización acusar sin ser víctima.",
    larga:
      "La Constitución permite ejercer la acción penal a quien no ha sido perjudicado por el delito: asociaciones, sindicatos, partidos o particulares pueden personarse y acusar. En las causas de corrupción es habitual — y polémico: puede mantener viva una acusación cuando la fiscalía no acusa, y también prestarse a un uso partidista.",
    fuente: { ref: "art. 125 CE", href: "https://www.boe.es/buscar/act.php?id=BOE-A-1978-31229#a125" },
    relacionados: ["querella", "escrito-de-acusacion"],
  },
  {
    id: "escrito-de-acusacion",
    termino: "Escrito de acusación",
    categoria: "procedimiento",
    corta: "El documento donde cada acusación fija los hechos que atribuye y las penas que pide.",
    larga:
      "Cerrada la instrucción, fiscal y demás acusaciones presentan su calificación: hechos, delitos que aprecian, autores y penas solicitadas. Las cifras de «piden X años» que publica la prensa salen de aquí. Es la pretensión de una parte: el juicio puede rebajarla o tumbarla entera.",
    fuente: { ref: "arts. 650 y ss. LECrim", href: `${LECRIM}#a650` },
    relacionados: ["acusado", "juicio-oral"],
  },
  {
    id: "juicio-oral",
    termino: "Juicio oral",
    categoria: "procedimiento",
    corta: "La vista pública donde se practica la prueba y se decide de verdad.",
    larga:
      "Es la fase central del proceso: ante el tribunal que va a sentenciar, declaran acusados, testigos y peritos y se examinan los documentos, con publicidad y contradicción. Solo lo probado aquí puede fundar una condena — lo instruido antes son indicios que deben confirmarse en la vista.",
    relacionados: ["acusado", "sentencia-firme"],
  },
  {
    id: "prision-provisional",
    termino: "Prisión provisional",
    categoria: "procedimiento",
    corta: "Cárcel cautelar antes del juicio, para evitar fugas o destrucción de pruebas; no es una pena.",
    larga:
      "Medida cautelar excepcional que el juez puede acordar cuando hay indicios sólidos y riesgo de fuga, de destrucción de pruebas o de reiteración delictiva. Quien la sufre sigue siendo presunto inocente: si luego es absuelto, tiene derecho a indemnización. Su duración está legalmente tasada.",
    fuente: { ref: "arts. 502 y ss. LECrim", href: `${LECRIM}#a502` },
    relacionados: ["auto", "investigado"],
  },
  {
    id: "sobreseimiento",
    termino: "Sobreseimiento",
    categoria: "procedimiento",
    corta: "El archivo de la causa: libre (definitivo) o provisional (reabrible con nuevos indicios).",
    larga:
      "Cierre de la causa sin juicio. El sobreseimiento libre se dicta cuando el hecho no existió, no es delito o el investigado no participó — equivale materialmente a una absolución. El provisional, cuando los indicios son insuficientes: la causa queda archivada pero puede reabrirse. Ambos dejan intacta la inocencia.",
    fuente: { ref: "arts. 634 y ss. LECrim", href: `${LECRIM}#a634` },
    relacionados: ["archivado", "absuelto"],
  },
  {
    id: "pieza-separada",
    termino: "Pieza separada",
    categoria: "procedimiento",
    corta: "Una rama de la causa que se tramita aparte para no atascar el conjunto.",
    larga:
      "En las macrocausas, el juez separa en «piezas» los bloques de hechos o de investigados que pueden avanzar a ritmos distintos: cada pieza tiene su propia instrucción y puede llegar a juicio por separado. Por eso un mismo caso puede tener a la vez condenas firmes en una pieza e investigaciones abiertas en otra.",
    relacionados: ["instruccion", "causa-especial"],
  },
  {
    id: "sentencia-firme",
    termino: "Sentencia firme",
    categoria: "procedimiento",
    corta: "La sentencia contra la que ya no cabe recurso; solo entonces hay culpables (o inocentes) definitivos.",
    larga:
      "Una sentencia es firme cuando no puede recurrirse: porque se agotaron los recursos o porque no caben (como en las causas de aforados juzgadas en única instancia por el Supremo). Es la línea que separa el «presunto» del «probado»: este sitio solo usa «condenado» sin matices cuando la condena es firme.",
    relacionados: ["condenado", "tribunal-supremo"],
  },
];

/** Términos de una categoría, en el orden del array (orden editorial). */
export function terminosDe(cat: CategoriaGlosario): Termino[] {
  return GLOSARIO.filter((t) => t.categoria === cat);
}
