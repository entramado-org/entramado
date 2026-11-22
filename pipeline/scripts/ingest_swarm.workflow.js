export const meta = {
  name: 'entramado-ingest-swarm',
  description: 'Claude swarm: extract entities+relations from new-document chunks (replaces MLX)',
  phases: [{ title: 'Extract', detail: 'one Claude extractor per chunk-batch' }],
}

// Spec 069 T7.5: pinned model (reproducibility). Keep in sync with _ingest_dump.SWARM_MODEL,
// which stamps it on every facts-cache line this swarm produces.
const MODEL = 'opus'

// args: { repoRoot, dumpPath, start, end }  — extract batches [start, end)
const A = typeof args === 'string' ? JSON.parse(args) : (args || {})
const repoRoot = A.repoRoot
const dumpPath = A.dumpPath || `${repoRoot}/pipeline/data/reports/ingest_chunks.json`
const start = A.start ?? 0
const end = A.end ?? 1
log(`ingest swarm: repoRoot=${repoRoot} dump=${dumpPath} batches [${start},${end})`)

// One validated record per chunk; the apply step lenient-validates again vs schema.py.
const CHUNK_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['batch', 'n_chunks', 'n_entities', 'n_relations', 'wrote_path'],
  properties: {
    batch: { type: 'integer' },
    n_chunks: { type: 'integer' },
    n_entities: { type: 'integer' },
    n_relations: { type: 'integer' },
    wrote_path: { type: 'string' },
  },
}

phase('Extract')

const thunks = []
for (let i = start; i < end; i++) {
  const idx = i
  const outPath = `${repoRoot}/pipeline/data/reports/extractions/batch_${idx}.json`
  const prompt = `Eres un analista forense que construye un grafo de conocimiento sobre casos de
corrupción en España (autos del Tribunal Supremo, informes UDEF/UCO/Guardia Civil, autos de
instrucción). Extraes ENTIDADES y RELACIONES de fragmentos de documentos judiciales/policiales.

PASO 1 — Carga el CONTRATO de extracción EXACTO (system prompt) y TU batch (ejecútalos con Bash, NO los inventes):
  cd ${repoRoot} && python3 -c "import json; d=json.load(open('${dumpPath}')); print(d['meta']['system_prompt'])"
  cd ${repoRoot} && python3 -c "import json; d=json.load(open('${dumpPath}')); print(json.dumps(d['batches'][${idx}], ensure_ascii=False))"
El segundo imprime {batch_id, chunks:[{chunk_id, doc_id, doc_title, page_start, page_end, text}]}.

PASO 2 — Para CADA chunk del batch, aplica el contrato del PASO 1 al campo "text":
  - Extrae entidades (name canónico sin tratamientos, type del vocabulario cerrado, aliases, role) y
    relaciones (source, target, type del vocabulario cerrado, source_quote ≤100 chars VERBATIM del
    texto, amount/currency/date o null).
  - Máximo 18 entidades y 18 relaciones por chunk; prioriza lo relevante, no dupliques.
  - NO inventes hechos: extrae solo lo que el texto afirma o atribuye. Acusación/sospecha →
    ACCUSED_OF (no como hecho probado). Presunción de inocencia.
  - Los autos del TS usan SEUDÓNIMOS (Segismundo, Everardo, Felix, Gabino…): extráelos tal cual
    como Person; son la fuente, no un error. NO los "traduzcas" a nombres reales.
  - source_quote debe ser una subcadena real del "text" del chunk (no parafrasees).

PASO 3 — Escribe el resultado EXACTAMENTE en este fichero (herramienta Write), un JSON que sea un
ARRAY (sin texto alrededor), un elemento por chunk:
  ${outPath}
Formato de cada elemento:
  {"chunk_id": "<copia EXACTA del chunk>", "doc_id": "<copia EXACTA>",
   "entities": [{"name": "...", "type": "...", "aliases": [...], "role": null}],
   "relations": [{"source": "...", "target": "...", "type": "...", "source_quote": "...",
                  "amount": null, "currency": null, "date": null}]}
Incluye TODOS los chunks del batch (aunque entities/relations queden vacíos). Copia chunk_id y
doc_id VERBATIM del batch.

PASO 4 — Devuelve el resumen estructurado: {batch=${idx}, n_chunks, n_entities (total), n_relations (total), wrote_path="${outPath}"}.

No proceses ningún batch que no sea el ${idx}.`

  thunks.push(() => agent(prompt, {
    label: `extract:batch-${idx}`,
    phase: 'Extract',
    schema: CHUNK_SCHEMA,
    model: MODEL,
  }).then(r => r || { batch: idx, n_chunks: 0, n_entities: 0, n_relations: 0, wrote_path: '' }))
}

const results = await parallel(thunks)
const ok = results.filter(Boolean)
const tot = ok.reduce((a, r) => ({
  chunks: a.chunks + (r.n_chunks || 0),
  ents: a.ents + (r.n_entities || 0),
  rels: a.rels + (r.n_relations || 0),
}), { chunks: 0, ents: 0, rels: 0 })
log(`batches ${start}..${end - 1}: chunks=${tot.chunks} entities=${tot.ents} relations=${tot.rels}`)
return { batches: ok.length, ...tot }
