// Curated editorial timeline for the landing page. Deliberately hand-written
// (not derived from the graph) so the milestones stay few, sourced and neutral
// — the explorer's TimelineStrip already offers the exhaustive dynamic view.
// `briefingSlug` links to /briefings/{slug} (slugs verified against
// vault/_Briefings); `focus` deep-links to /explore?focus={entity_id}.

export interface TimelineMilestone {
  date: string; // display label, e.g. "Feb 2024"
  year: number; // for chronological ordering
  text: string; // neutral, presumption-of-innocence tone
  caseTag?: string; // short category chip label
  caseColor?: string; // hex for the chip (reuses the palette in graphStyle.ts)
  briefingSlug?: string; // -> /briefings/{slug}
  focus?: string; // -> /explore?focus={entity_id}
}

// Palette echoes graphStyle.ts (accent teal, type/relation hexes) so the
// timeline chips don't introduce new colors.
const C = {
  mascarillas: "#fbbf24", // amber — contracts / empresas
  obra: "#818cf8", // indigo — procurement / obra pública
  judicial: "#f87171", // red — fase judicial
  politica: "#a78bfa", // violet — político/institucional
  dinero: "#4ade80", // green — rastro del dinero
};

export const TIMELINE: TimelineMilestone[] = [
  {
    date: "2017–2019",
    year: 2017,
    text: "José Luis Ábalos asume Transportes (MITMA) y Santos Cerdán gana peso en la Secretaría de Organización del PSOE; Koldo García trabaja como asesor del ministro.",
    caseTag: "Contexto",
    caseColor: C.politica,
    focus: "Person:jose-luis-abalos",
  },
  {
    date: "2020",
    year: 2020,
    text: "Durante la pandemia, IB-Salut compra mascarillas a Soluciones de Gestión por 3,7 M€. La adjudicación es el origen del caso; la UCO documenta el expediente.",
    caseTag: "Mascarillas",
    caseColor: C.mascarillas,
    briefingSlug: "caso-koldo-mascarillas",
    focus: "Company:soluciones-de-gestion",
  },
  {
    date: "2020–2023",
    year: 2021,
    text: "Se documenta el papel de Víctor de Aldama como presunto intermediario y el rastro del dinero entre empresas, comisiones y pagos vinculados a la trama.",
    caseTag: "Dinero",
    caseColor: C.dinero,
    briefingSlug: "rastro-del-dinero",
    focus: "Person:victor-de-aldama",
  },
  {
    date: "Feb 2024",
    year: 2024,
    text: "Estalla el caso Koldo: detenciones por la trama de mascarillas. La investigación se hace pública y salpica al entorno del exministro de Transportes.",
    caseTag: "Judicial",
    caseColor: C.judicial,
    briefingSlug: "caso-koldo-mascarillas",
    focus: "Person:koldo-garcia",
  },
  {
    date: "Oct 2024",
    year: 2024,
    text: "Exposición razonada sobre José Luis Ábalos; por su condición de aforado, el caso escala al Tribunal Supremo.",
    caseTag: "Judicial",
    caseColor: C.judicial,
    focus: "Person:jose-luis-abalos",
  },
  {
    date: "2025",
    year: 2025,
    text: "Informes de la UCO sobre Santos Cerdán y la presunta red de adjudicación de obra pública. El foco se desplaza hacia la cúpula del partido.",
    caseTag: "Obra pública",
    caseColor: C.obra,
    briefingSlug: "cerdan-uco",
    focus: "Person:santos-cerdan",
  },
  {
    date: "Jun 2025",
    year: 2025,
    text: "Entrada y registro en la sede del PSOE en Ferraz en el marco de la investigación, una de las diligencias de mayor relevancia institucional del caso.",
    caseTag: "Judicial",
    caseColor: C.judicial,
    briefingSlug: "ferraz-entrada",
  },
  {
    date: "Jun 2025",
    year: 2025,
    text: "El Tribunal Supremo acuerda prisión provisional para Santos Cerdán, exsecretario de Organización del PSOE, en la causa sobre la presunta red de adjudicación de obra pública. Rige la presunción de inocencia.",
    caseTag: "Judicial",
    caseColor: C.judicial,
    briefingSlug: "cerdan-uco",
    focus: "Person:santos-cerdan",
  },
  {
    date: "Nov 2025",
    year: 2025,
    text: "El Tribunal Supremo acuerda prisión provisional para el exministro José Luis Ábalos en la causa derivada del caso Koldo. Rige la presunción de inocencia.",
    caseTag: "Judicial",
    caseColor: C.judicial,
    focus: "Person:jose-luis-abalos",
  },
  {
    date: "Dic 2025",
    year: 2025,
    text: "El Tribunal Supremo dicta la sentencia 1000/2025 (Causa Especial 20557/2024) y condena al fiscal general del Estado, Álvaro García Ortiz, por revelación de datos reservados —multa, dos años de inhabilitación y 10.000 € de indemnización a Alberto González Amador— absolviéndolo del resto de las acusaciones. Es la primera condena penal a un fiscal general en ejercicio; dos magistradas formularon votos particulares discrepantes.",
    caseTag: "Fiscal general",
    caseColor: C.judicial,
    focus: "Person:alvaro-garcia-ortiz",
  },
  {
    date: "2025–2026",
    year: 2025,
    text: "Pieza separada en Badajoz por la presunta contratación irregular de David Sánchez, hermano del presidente del Gobierno, en la Diputación. La causa es elevada a juicio en 2026. Rige la presunción de inocencia.",
    caseTag: "David Sánchez",
    caseColor: C.politica,
    briefingSlug: "caso-david-sanchez",
    focus: "Person:david-sanchez",
  },
  {
    date: "Abr 2026",
    year: 2026,
    text: "Auto de imputación a Begoña Gómez, esposa del presidente del Gobierno, en una pieza separada. Rige la presunción de inocencia.",
    caseTag: "Judicial",
    caseColor: C.judicial,
    briefingSlug: "begona-gomez",
    focus: "Person:begona-gomez",
  },
  {
    date: "2026",
    year: 2026,
    text: "El Juzgado Central de Instrucción nº 4 de la Audiencia Nacional investiga al expresidente José Luis Rodríguez Zapatero en la pieza vinculada al rescate de Plus Ultra y la trama Venezuela (DP 77/2024), por presuntos delitos de tráfico de influencias y blanqueo. Rige la presunción de inocencia.",
    caseTag: "Plus Ultra",
    caseColor: C.dinero,
    briefingSlug: "zapatero-plus-ultra",
    focus: "Person:jose-luis-rodriguez-zapatero",
  },
  {
    // Hito respaldado por prensa (juicio en curso; aún sin sentencia escrita).
    date: "May 2026",
    year: 2026,
    text: "El juicio del caso Koldo por los contratos de mascarillas queda visto para sentencia en el Tribunal Supremo. Según la Fiscalía Anticorrupción, las peticiones de pena alcanzan 24 años para José Luis Ábalos, 19 años y medio para Koldo García y 7 para Víctor de Aldama. Rige la presunción de inocencia.",
    caseTag: "Judicial",
    caseColor: C.judicial,
    briefingSlug: "caso-koldo-mascarillas",
    focus: "Person:jose-luis-abalos",
  },
  {
    date: "Jun 2026",
    year: 2026,
    text: "El juez Peinado abre juicio oral ante el Tribunal del Jurado contra Begoña Gómez, su asistente Cristina Álvarez y el empresario Juan Carlos Barrabés por la Cátedra de la UCM; la Fiscalía había pedido el sobreseimiento. La apertura de juicio no prejuzga el fallo: rige la presunción de inocencia.",
    caseTag: "Judicial",
    caseColor: C.judicial,
    briefingSlug: "begona-gomez",
    focus: "Person:begona-gomez",
  },
  {
    date: "Jun 2026",
    year: 2026,
    text: "Primera condena del corpus: el Tribunal Supremo dicta la sentencia 418/2026 (Causa Especial 20775/2020) y condena en instancia única a José Luis Ábalos, Koldo García y Víctor de Aldama por organización criminal, cohecho, tráfico de influencias y malversación, con absoluciones parciales y atenuante de colaboración para Aldama. Es firme.",
    caseTag: "Condena firme",
    caseColor: C.judicial,
    briefingSlug: "sentencia-abalos-koldo-aldama",
    focus: "Person:jose-luis-abalos",
  },
  {
    // Hito respaldado por prensa (instrucción en curso; autos aún no incorporados al corpus).
    date: "Jun–Jul 2026",
    year: 2026,
    text: "La Audiencia Nacional (juez Pedraz, DP 150/2025) investiga la presunta red de interferencia en investigaciones sensibles para el Gobierno y los presuntos amaños en la contratación de empresas públicas — el caso Leire Díez y la pieza SEPI. A finales de junio quedan imputadas la presidenta de la SEPI y la directora general de la Guardia Civil. Rige la presunción de inocencia.",
    caseTag: "Caso Leire",
    caseColor: C.politica,
    briefingSlug: "caso-leire-cloacas",
    focus: "Person:leire-diez",
  },
  {
    // Hito respaldado por prensa.
    date: "Jul 2026",
    year: 2026,
    text: "La Agencia Tributaria comunica al juez Calama que investiga las obligaciones tributarias del expresidente Zapatero, su mujer, sus hijas y Julio Martínez para los ejercicios 2021-2025, en paralelo a la causa de Plus Ultra. La apertura de actuaciones no prejuzga infracción alguna; rige la presunción de inocencia.",
    caseTag: "Plus Ultra",
    caseColor: C.dinero,
    briefingSlug: "zapatero-plus-ultra",
    focus: "Person:jose-luis-rodriguez-zapatero",
  },
  {
    date: "Jul 2026",
    year: 2026,
    text: "La Audiencia Provincial de Badajoz dicta la sentencia 152/2026: condena a los once acusados del caso David Sánchez —incluidos el hermano del presidente del Gobierno y el presidente de la Diputación, Miguel Ángel Gallardo— a nueve años de inhabilitación por prevaricación, y los absuelve del tráfico de influencias. La sentencia NO es firme: cabe apelación ante el TSJ de Extremadura.",
    caseTag: "David Sánchez",
    caseColor: C.politica,
    briefingSlug: "caso-david-sanchez",
    focus: "Person:david-sanchez",
  },
  {
    // Hito respaldado por prensa (auto aún no incorporado al corpus).
    date: "Ago 2026",
    year: 2026,
    text: "El juez Peinado remite a la Fiscalía Europea (EPPO) la pieza del contrato de 4 M€ de Red.es a la UTE de Innova Next —la empresa de Juan Carlos Barrabés— y KPMG, financiado con fondos europeos, y archiva sus actuaciones «sin más trámite». Son diligencias en curso ante la EPPO; rige la presunción de inocencia.",
    caseTag: "Judicial",
    caseColor: C.judicial,
    briefingSlug: "begona-gomez",
    focus: "Person:juan-carlos-barrabes",
  },
  {
    // Hito respaldado por prensa (auto aún no incorporado al corpus).
    date: "Ago 2026",
    year: 2026,
    text: "El juez Ismael Moreno prorroga seis meses la instrucción de la pieza principal del caso Koldo en la Audiencia Nacional, hasta el 8 de marzo de 2027, a la espera —entre otras diligencias— del análisis final de la UCO sobre los dispositivos intervenidos en los registros de 2024. Rige la presunción de inocencia.",
    caseTag: "Obra pública",
    caseColor: C.obra,
    briefingSlug: "cerdan-uco",
    focus: "Person:ismael-moreno",
  },
];
