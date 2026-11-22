export const meta = {
  name: 'enrich-entity-descriptions',
  description: 'Investiga (web) y redacta role+resumen factuales y citados para el cast central del grafo, con verificación adversarial y propuestas de corrección estructural',
  phases: [
    { title: 'Investigar' },
    { title: 'Verificar' },
  ],
}

// Spec 069 T7.5: pinned model (reproducibility).
const MODEL = 'opus'

// batch files: /tmp/cast_batch_00.json .. (6 entidades/lote)
// Each is a JSON array of {name,type,entity_id,current_role,aliases,degree}.
// nBatches via args (default 30, the original central-cast run).
const _A = typeof args === 'string' ? JSON.parse(args) : (args || {})
const N_BATCHES = _A.nBatches ?? 30
const BATCHES = Array.from({ length: N_BATCHES }, (_, i) => `/tmp/cast_batch_${String(i).padStart(2, '0')}.json`)

const VAULT = '$HOME/Documents/projects/entramado/vault'

// High-profile names → 3-vote adversarial verification.
const HIGH_PROFILE = [
  'José Luis Ábalos', 'Santos Cerdán', 'Víctor de Aldama', 'Koldo García',
  'Begoña Gómez', 'Pedro Sánchez', 'José Luis Rodríguez Zapatero', 'David Sánchez',
]

const ENTITY_ITEM = {
  type: 'object',
  additionalProperties: false,
  required: ['name', 'entity_id', 'type', 'role', 'resumen', 'aliases', 'sources', 'confidence', 'flags'],
  properties: {
    name: { type: 'string', description: 'nombre EXACTO del nodo (cópialo del batch)' },
    entity_id: { type: 'string', description: 'entity_id EXACTO del batch' },
    type: { type: 'string', enum: ['Person', 'Organization', 'Company', 'Court', 'LegalCase', 'Contract'] },
    role: { type: 'string', description: 'role corto (1 línea): cargo/partido/años/identidad. Vacío si is_noise.' },
    resumen: { type: 'string', description: '3-5 frases factuales con el papel en la trama, presunción de inocencia. Vacío si is_noise/no identificable.' },
    aliases: { type: 'array', items: { type: 'string' } },
    sources: { type: 'array', items: { type: 'string' }, description: 'URLs públicas que respaldan role+resumen' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    flags: {
      type: 'object',
      additionalProperties: false,
      required: ['is_noise', 'homonym', 'duplicate_of', 'misattributions', 'misleading_edges'],
      properties: {
        is_noise: { type: 'boolean', description: 'true si el nodo es basura/fragmento/no-entidad → candidato a blacklist' },
        homonym: { type: 'boolean', description: 'true si el nombre mezcla dos entes distintos a separar' },
        duplicate_of: { type: ['string', 'null'], description: 'nombre canónico del que este nodo es duplicado, si lo es' },
        misattributions: { type: 'array', items: { type: 'string' }, description: 'datos falsos/erróneos en el current_role o que el grafo insinúa' },
        misleading_edges: { type: 'array', items: { type: 'string' }, description: 'relaciones engañosas a negar' },
      },
    },
  },
}

const BATCH_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['entities'],
  properties: { entities: { type: 'array', items: ENTITY_ITEM } },
}

phase('Investigar')

const verified = await pipeline(
  BATCHES,
  // Etapa 1 — Investigador
  (batchFile, _orig, idx) => agent(
    `Eres un investigador documentalista experto en los casos de corrupción en España
(caso Koldo / mascarillas, trama Cerdán-Servinabar / informes UCO, Ábalos, Víctor de
Aldama, Begoña Gómez, hermano de Pedro Sánchez / Diputación de Badajoz, auto Zapatero /
Plus Ultra). Trabajas con presunción de inocencia y rigor periodístico.

Lee tu lote: \`cat ${batchFile}\` — es un array JSON de entidades del grafo, cada una con
{name, type, entity_id, current_role, aliases, degree}.

Para CADA entidad del lote devuelve una entrada enriquecida:

1. IDENTIFICA de quién/qué se trata (persona, organismo, empresa, tribunal, causa,
   contrato). Si hay nota de dominio en el vault, ojéala como pista (NO como verdad —
   la generó un LLM y puede tener errores): \`ls ${VAULT}\` y p.ej.
   \`cat "${VAULT}/People/<Nombre>.md"\` (carpetas: People, Organizations, Companies,
   Courts, Cases, Crimes, Events, Locations).
2. INVESTIGA en internet. Carga las tools con ToolSearch query "select:WebSearch,WebFetch"
   y úsalas (busca en español; fuentes fiables: medios de referencia, BOE, notas de
   prensa judiciales, Congreso). Abre alguna fuente con WebFetch para confirmar datos.
3. REDACTA en español:
   - \`role\`: 1 línea — cargo(s), partido, años, identidad. Ej: "Exministro de
     Transportes (2018-2021); exsecretario de Organización del PSOE".
   - \`resumen\`: 3-5 frases. Incluye su PAPEL EN LA TRAMA con **presunción de inocencia
     OBLIGATORIA**: "investigado", "imputado", "presunta trama", "según el informe de la
     UCO", "el juez le atribuye". NUNCA afirmes culpabilidad ni des por probado un delito
     no juzgado. Solo hechos públicos verificables.
   - \`sources\`: URLs que respaldan role+resumen.
4. FLAGS (auditoría de errores del grafo):
   - is_noise=true si el "nombre" es un fragmento de OCR, un placeholder genérico
     (p.ej. "subsecretario", "investigados", "Cuenta bancaria", "mesa de contratación"),
     un número de teléfono/WhatsApp, o una no-entidad inidentificable → role/resumen
     vacíos, confidence "low".
   - homonym=true si el nombre confunde dos entes distintos (ej. clásico: "Zapatero"
     expresidente vs un homónimo). OJO: el auto-zapatero.pdf investiga al PROPIO
     expresidente (Plus Ultra), NO es un empresario homónimo — no marques eso como homónimo.
   - duplicate_of=<nombre canónico> si es alias/duplicado de otro nodo.
   - misattributions: datos falsos en current_role o que el grafo insinúa.
   - misleading_edges: relaciones engañosas a negar.
5. \`name\` y \`entity_id\` DEBEN ser EXACTOS (cópialos del batch) — casan contra el grafo.

Si no encuentras información fiable, NO inventes: confidence "low", role/resumen vacíos
si no identificable, y anota lo que sepas en misattributions/aliases. Devuelve TODAS las
entidades del lote en \`entities\`.

ANTES de devolver, PERSISTE en disco con la tool Write: escribe el JSON {"entities":[...]}
en \`/tmp/enrich_out/research_b${idx}.json\` (copia de seguridad por si falla la verificación).`,
    { label: `research:b${idx}`, phase: 'Investigar', schema: BATCH_SCHEMA, model: MODEL }
  ),
  // Etapa 2 — Verificador adversarial
  (draft, _batchFile, idx) => agent(
    `Eres un VERIFICADOR DE HECHOS adversarial y escéptico. Por defecto DESCONFÍA de cada
afirmación hasta verla respaldada por una fuente.

Entradas propuestas a verificar (con sus fuentes):
${JSON.stringify(draft, null, 1)}

Para CADA entidad:
1. Carga WebSearch/WebFetch (ToolSearch query "select:WebSearch,WebFetch"). Comprueba que
   cada afirmación de \`role\` y \`resumen\` está respaldada por las \`sources\` u otras
   fuentes fiables que verifiques tú. Abre URLs con WebFetch para confirmar.
2. ELIMINA o SUAVIZA toda afirmación no respaldada. Si una fuente no sostiene lo que
   dice, quítala o baja la confianza.
3. EXIGE presunción de inocencia: reescribe cualquier frase que insinúe culpabilidad en
   causa abierta ("condenado"→"investigado/imputado/procesado" según corresponda; si hay
   sentencia firme, dilo con su fuente).
4. Corrige role/aliases/type si la evidencia los contradice. Revalida homonym/duplicate_of.
5. Si algo no es verificable, deja role/resumen vacíos y confidence "low" antes que
   arriesgar un error. Las personas son reales y las causas están abiertas: el coste de
   un error factual es alto.

ANTES de devolver tu salida estructurada, PERSISTE tu resultado en disco con la tool
Write: escribe el JSON {"entities":[...]} (exactamente lo que vas a devolver) en el
fichero \`/tmp/enrich_out/verify_b${idx}.json\`. Esto es OBLIGATORIO — el resultado debe
quedar en disco aunque el workflow falle después.

Luego devuelve el MISMO formato (\`entities\`) ya corregido y verificado, con \`name\` y
\`entity_id\` EXACTOS. Añade en misattributions cualquier afirmación que hayas eliminado.`,
    { label: `verify:b${idx}`, phase: 'Verificar', schema: BATCH_SCHEMA, model: MODEL }
  )
)

// Barrera implícita: el pipeline terminó. Recolecta todas las entidades verificadas.
const all = []
for (const v of verified.filter(Boolean)) {
  if (v && Array.isArray(v.entities)) all.push(...v.entities)
}
log(`Entidades verificadas: ${all.length} de ${N_BATCHES} lotes`)

// Verificación reforzada (3 votos) para el alto perfil.
phase('Verificar')
const highList = all.filter((e) => HIGH_PROFILE.includes(e.name))
const VOTE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['name', 'entity_id', 'role', 'resumen', 'sources', 'ok', 'changes'],
  properties: {
    name: { type: 'string' }, entity_id: { type: 'string' },
    role: { type: 'string' }, resumen: { type: 'string' },
    sources: { type: 'array', items: { type: 'string' } },
    ok: { type: 'boolean', description: 'true si todo está respaldado y con tono correcto' },
    changes: { type: 'string', description: 'qué corregiste y por qué' },
  },
}
const reinforced = await parallel(highList.map((e) => () =>
  parallel([0, 1, 2].map((v) => () =>
    agent(
      `Verificación reforzada (voto ${v + 1}/3) de una figura de ALTO PERFIL en causa abierta.
Entrada:
${JSON.stringify(e, null, 1)}
Carga WebSearch/WebFetch (ToolSearch "select:WebSearch,WebFetch"). Verifica cada dato
contra fuentes fiables. Corrige errores, exige presunción de inocencia, elimina lo no
respaldado.
ANTES de devolver, PERSISTE en disco con la tool Write: escribe el JSON de tu entrada
final en \`/tmp/enrich_out/vote_${e.entity_id.replace(/[^a-zA-Z0-9]/g, '_')}__${v + 1}.json\`.
Luego devuelve la entrada final (role, resumen, sources) con name/entity_id exactos.`,
      { label: `vote:${e.name}#${v + 1}`, phase: 'Verificar', schema: VOTE_SCHEMA, model: MODEL }
    )
  )).then((votes) => {
    const ok = votes.filter(Boolean)
    const good = ok.filter((x) => x.ok)
    const pick = (good.length ? good : ok).sort((a, b) => (b.resumen || '').length - (a.resumen || '').length)[0]
    return pick ? { ...e, role: pick.role, resumen: pick.resumen, sources: pick.sources, _votes: ok.length } : e
  })
))
const byId = new Map(all.map((e) => [e.entity_id, e]))
for (const r of reinforced.filter(Boolean)) byId.set(r.entity_id, r)
const finalAll = [...byId.values()]

phase('Sintetizar')

// Bucketing DETERMINISTA de finalAll → salida para la capa de contexto.
// NO es trabajo de LLM: antes esto era un agent() al que se le serializaban las ~175
// entidades en un único prompt (>1M tok) y se ahogaba sin emitir tool calls. Hacerlo en
// JS puro es instantáneo y reproducible. (Recuperación equivalente: _enrich_recover.py,
// que reconstruye esto desde el journal si una corrida muere en este punto.)
const ORG_TYPES = new Set(['Organization', 'Company', 'Court', 'LegalCase', 'Contract'])
const has = (s) => Boolean((s || '').trim())

const figures_people = [], org_entities = [], summaries = []
const blacklist = [], split_nodes = [], deny_edges = [], duplicates = []
const byConf = { high: 0, medium: 0, low: 0 }

for (const e of finalAll) {
  const { name, type, entity_id } = e
  const role = e.role || '', resumen = e.resumen || ''
  const conf = e.confidence || 'low'
  const flags = e.flags || {}
  const sources = e.sources || []
  byConf[conf] = (byConf[conf] || 0) + 1

  if (flags.duplicate_of) {
    // alias/duplicado → propuesta de fusión; NO lo listamos también como figura/org.
    duplicates.push({ name, type, duplicate_of: flags.duplicate_of, reason: 'flagged duplicate_of por verificador' })
    continue
  }

  if (flags.is_noise || (conf === 'low' && !has(role) && !has(resumen))) {
    blacklist.push(name)
  }

  if (has(role) && (conf === 'high' || conf === 'medium')) {
    if (type === 'Person') figures_people.push({ name, type: 'Person', role, sources })
    else if (ORG_TYPES.has(type)) org_entities.push({ name, type, role, sources })
  }

  if (has(resumen)) summaries.push({ entity_id, name, resumen, sources })

  if (flags.homonym) split_nodes.push({ name, type, reason: 'homónimo a separar (flag verificador)' })

  // misleading_edges son texto libre del verificador; quedan con source/target/type por
  // fijar a mano antes de volcarlos al overlay.
  for (const me of (flags.misleading_edges || [])) {
    deny_edges.push({ source: name, target: '', type: '', reason: me })
  }
}

const synth = {
  figures_people, org_entities, summaries, blacklist, split_nodes, deny_edges, duplicates,
  summary: `${finalAll.length} entidades verificadas. ` +
    `figures_people=${figures_people.length}, org_entities=${org_entities.length}, ` +
    `summaries=${summaries.length}, blacklist=${blacklist.length}, ` +
    `split_nodes=${split_nodes.length}, deny_edges=${deny_edges.length}, ` +
    `duplicates=${duplicates.length}. Confianza: ${JSON.stringify(byConf)}. ` +
    `deny_edges llevan reason en texto libre (misleading_edges): requieren pase manual ` +
    `para fijar source/target/type.`,
}
log(synth.summary)

return synth
