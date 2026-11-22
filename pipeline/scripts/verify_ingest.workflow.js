export const meta = {
  name: 'entramado-verify-ingest',
  description: 'Verificación adversarial PRE-carga: jueces refutadores re-leen los chunks; doble voto en relaciones sensibles',
  phases: [
    { title: 'Refutar', detail: 'un juez refutador por lote de relaciones' },
    { title: 'Segundo voto', detail: 'refutador independiente sobre las sensibles supervivientes' },
  ],
}

// Spec 069 T7.5: pinned model (reproducibility of the verdicts).
const MODEL = 'opus'

// args: { repoRoot, start, end } — lotes [start, end) de data/reports/verify_dump.json
const A = typeof args === 'string' ? JSON.parse(args) : (args || {})
const repoRoot = A.repoRoot
const dumpPath = `${repoRoot}/pipeline/data/reports/verify_dump.json`
const start = A.start ?? 0
const end = A.end ?? 1
log(`verify swarm: lotes [${start},${end}) de ${dumpPath}`)

const SUMMARY_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['batch', 'n_ok', 'n_fuera', 'n_editar', 'n_duda', 'wrote_path'],
  properties: {
    batch: { type: 'integer' },
    n_ok: { type: 'integer' },
    n_fuera: { type: 'integer' },
    n_duda: { type: 'integer' },
    n_editar: { type: 'integer' },
    wrote_path: { type: 'string' },
  },
}

const CRITERIA = `CRITERIOS (protectores — el coste de un falso positivo es LEGAL; ante la duda, DUDA — nunca OK.
Una DUDA acusatoria la decide una persona en la cola de revisión; una no acusatoria entra marcada
como de confianza baja. Usa FUERA solo cuando el texto NO sostiene la relación — spec 069):
1. El texto del chunk debe soportar EXACTAMENTE source -[type]-> target, en esa dirección,
   con identidad inequívoca de ambos extremos (variantes de nombre valen si la identidad es clara).
2. NO sobreafirmar: una pregunta, insinuación o acusación POLÍTICA de un senador en un diario de
   sesiones NO es un hecho ni una acusación formal → FUERA. ACCUSED_OF solo si la acusación la
   formula formalmente un fiscal/juez/documento acusatorio, o el texto la atribuye como imputación
   procesal real. INVESTIGATED_BY solo si el texto afirma esa investigación como hecho procesal.
3. Testimonio de un compareciente: sobre hechos PROPIOS y afirmado con claridad → puede valer;
   sobre TERCEROS → solo si es afirmación factual clara del compareciente, y ante la duda DUDA.
4. Roles procesales exactos: investigado ≠ acusado ≠ condenado. CONVICTED_OF solo con condena
   expresa en el texto.
5. amount/date deben salir del texto del chunk; si no salen, EDITAR (ponlos a null) o FUERA.
6. Relaciones vacías de contenido (boilerplate, cabeceras, co-mención trivial) → FUERA.
7. EDITAR solo para arreglos acotados: corregir type dentro del vocabulario, invertir dirección,
   o poner amount/date a null/valor literal del texto. NUNCA re-apuntar a otra entidad distinta.
8. Tipos permitidos: SOLO los del vocabulario controlado que cargas en el PASO 0. Cada type se
   juzga con SU ficha (definición, incluye, excluye): si el texto cae en «excluye», no es ese type.`

function judgePrompt(idx, outPath, roundNote) {
  return `Eres un juez REFUTADOR en un grafo de conocimiento sobre corrupción en España construido
desde documentos judiciales y diarios de sesiones. Tu trabajo es intentar TUMBAR relaciones
extraídas por otro modelo. Solo sobrevive lo irreprochable.
${roundNote}
PASO 0 — Carga el vocabulario controlado (spec 069; ejecuta con Bash, no lo inventes):
  cd ${repoRoot} && python3 pipeline/scripts/ontologia_gen.py --fichas tipos_relacion --extraccion

PASO 1 — Carga TU lote (ejecuta con Bash, no lo inventes):
  cd ${repoRoot} && python3 -c "import json; d=json.load(open('${dumpPath}')); print(json.dumps(d['batches'][${idx}], ensure_ascii=False))"
Es una lista de {chunk_id, doc_id, page, text, relations:[{key, source, type, target, quote, amount, currency, date, sensitive}]}.

PASO 2 — Para CADA relación, re-lee el campo "text" ÍNTEGRO de su chunk y emite veredicto.
${CRITERIA}

PASO 3 — Escribe con Write EXACTAMENTE en ${outPath} un JSON ARRAY (sin texto alrededor), un
elemento POR CADA relación del lote:
  {"key": "<copia exacta>", "verdict": "OK" | "FUERA" | "EDITAR" | "DUDA",
   "reason": "<una frase concreta>",
   "fix": null | {"type": "...", "swap_direction": true|false, "amount": <num|null>, "date": "<...|null>"}}
"fix" solo si verdict=EDITAR.

PASO 4 — Devuelve el resumen estructurado {batch=${idx}, n_ok, n_fuera, n_editar, n_duda, wrote_path="${outPath}"}.
No proceses ningún lote que no sea el ${idx}.`
}

phase('Refutar')

const results = await pipeline(
  Array.from({ length: end - start }, (_, i) => start + i),
  idx => {
    const outA = `${repoRoot}/pipeline/data/reports/verify_verdicts/a/batch_${idx}.json`
    return agent(judgePrompt(idx, outA, ''), {
      label: `refutar:batch-${idx}`, phase: 'Refutar', schema: SUMMARY_SCHEMA, model: MODEL,
    })
  },
  (resA, idx) => {
    const outB = `${repoRoot}/pipeline/data/reports/verify_verdicts/b/batch_${idx}.json`
    const note = `SEGUNDO VOTO INDEPENDIENTE: otro juez ya revisó este lote; su fichero de veredictos está en
${repoRoot}/pipeline/data/reports/verify_verdicts/a/batch_${idx}.json. Léelo SOLO para saber qué
keys revisar: revisa ÚNICAMENTE las relaciones del lote que (a) tengan sensitive=true Y (b) cuyo
veredicto previo sea OK o EDITAR. IGNORA sus razones: juzga desde cero, desde el texto. Si no hay
ninguna que revisar, escribe un array vacío [] en el fichero de salida y devuelve ceros.
`
    return agent(judgePrompt(idx, outB, note), {
      label: `voto2:batch-${idx}`, phase: 'Segundo voto', schema: SUMMARY_SCHEMA, model: MODEL,
    }).then(resB => ({ a: resA, b: resB }))
  }
)

const ok = results.filter(Boolean)
const tot = ok.reduce((t, r) => ({
  ok: t.ok + ((r.a && r.a.n_ok) || 0),
  fuera: t.fuera + ((r.a && r.a.n_fuera) || 0),
  editar: t.editar + ((r.a && r.a.n_editar) || 0),
  b_fuera: t.b_fuera + ((r.b && r.b.n_fuera) || 0),
}), { ok: 0, fuera: 0, editar: 0, b_fuera: 0 })
log(`ronda A: OK=${tot.ok} FUERA=${tot.fuera} EDITAR=${tot.editar} · ronda B tumba ${tot.b_fuera} más`)
return { batches: ok.length, ...tot }
