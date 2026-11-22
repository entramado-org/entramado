"""Per-chunk entity/relationship extraction via the host MLX model.

Uses prompt-driven JSON mode (not native tool-calls, which mlx_lm.server emits
unreliably for Qwen3) with a validate -> repair -> dead-letter loop. Results are
cached per document at chunk granularity so re-runs never re-bill compute.
"""
from __future__ import annotations

import asyncio
import hashlib
import json

import structlog

from . import config, llm_client
from .chunking import Chunk
from .extraction_guards import encolar_rechazos, validar_extraccion
from .ontologia import ontologia
from .schema import ChunkExtraction

log = structlog.get_logger()

# The vocabulary and its decision rules come from the controlled ontology (spec 069): the prompt
# carries each term's card (definition, includes, excludes, synthetic examples) instead of a bare
# list of names plus a few hand-written per-type rules. MoneyAmount is not advertised (a money
# amount is never a node: 949 money-as-node artifacts, manual_fix_money_nodes_20260623) and the
# `solo_loader` relation types (official aggregates) are not offered to the extractor.
_ONT = ontologia()
_ENTITY_CARDS = _ONT.fichas_prompt("tipos_entidad", _ONT.tipos_entidad_extraibles)
_REL_CARDS = _ONT.fichas_prompt("tipos_relacion", _ONT.tipos_extraibles)
_SEED = ", ".join(sorted(set(config.SEED_ALIASES.values())))
_CRIMES = ", ".join(_ONT.delitos)

SYSTEM_PROMPT = f"""Eres un analista forense que construye un grafo de conocimiento a partir de \
documentos judiciales y policiales españoles (sumarios, autos, informes de la UCO/Guardia Civil, \
diarios de sesiones). Tu tarea es extraer ENTIDADES y RELACIONES de un fragmento de texto.

Devuelve EXCLUSIVAMENTE un objeto JSON válido con esta forma:
{{
  "entities": [
    {{"name": "<nombre canónico sin tratamientos>", "type": "<tipo>", "aliases": ["..."], "role": "<cargo/descripción o null>"}}
  ],
  "relations": [
    {{"source": "<nombre entidad>", "target": "<nombre entidad>", "type": "<tipo_relación>",
      "source_quote": "<cita textual breve que respalda la relación>",
      "amount": <número o null>, "currency": "<EUR/USD o null>", "date": "<fecha o null>"}}
  ]
}}

VOCABULARIO CONTROLADO (versión {_ONT.version}). Usa SOLO estos tipos. Decide cada caso con la ficha \
del tipo: si el caso cae en «Excluye», NO es ese tipo; si ningún tipo encaja, no emitas nada.

Tipos de entidad:
{_ENTITY_CARDS}

Tipos de relación (dirección: sujeto → objeto; «A ↔ B» = simétrica):
{_REL_CARDS}

Reglas generales:
- Usa el nombre canónico, quitando tratamientos (Sr., Sra., D., Dª, Don, Doña). Ponlos en "aliases".
- Para personas centrales usa exactamente estas formas si aparecen: {_SEED}.
- El nombre de un Crime es uno de estos delitos canónicos: {_CRIMES}. Si el documento dice la \
modalidad (p. ej. «cohecho pasivo»), usa esa. Si califica otro delito penal, escribe su nombre literal: \
irá a revisión. Un tema o una conducta sin calificación jurídica no es un Crime.
- Los importes NUNCA son una entidad ni el destino de una relación. Van en "amount" (número) y \
"currency" de la PROPIA relación de flujo (PAID, RECEIVED, RECEIVED_GRANT).
- Cada relación DEBE incluir "source_quote": una cita textual BREVE (máximo 100 caracteres).
- Devuelve como máximo 18 entidades y 18 relaciones por fragmento: prioriza las más relevantes \
y evita duplicar la misma relación. Sé conciso para no exceder el límite de salida.
- No inventes hechos: extrae solo lo que el texto afirma o atribuye. Una acusación o sospecha no es \
un hecho probado.
- La CO-OCURRENCIA no es una relación: que dos nombres aparezcan en el mismo párrafo o en una lista \
no basta. Emite una relación solo si el texto afirma ESE vínculo entre ESOS dos referentes. Ante la \
duda, NO emitas la relación.
- Atribuye cada relación al referente que el texto nombra, no al más cercano: si el delito o el pago \
es de OTRA persona del pasaje, la relación es de esa otra.
- El lenguaje CONDICIONAL o PROSPECTIVO («percibiría», «se comprometió a pagar») no es un hecho \
consumado.
- Si no hay entidades o relaciones, devuelve listas vacías. No añadas texto fuera del JSON."""
PROMPT_SHA256 = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def _user_prompt(chunk: Chunk) -> str:
    return (
        f"Documento: {chunk.doc_title} (página {chunk.page_start}).\n"
        f"Fragmento:\n\"\"\"\n{chunk.text}\n\"\"\"\n\n"
        "Extrae el JSON de entidades y relaciones."
    )


async def _extract_one(chunk: Chunk) -> dict:
    """Returns a fact record for one chunk. A parseable JSON response is accepted
    (lenient); only unparseable output (e.g. truncation) is retried then dropped."""
    system = SYSTEM_PROMPT
    user = _user_prompt(chunk)

    for _ in range(2):  # initial + one repair
        raw = await llm_client.complete(system, user, json_mode=True)
        data = llm_client.parse_json(raw)
        if data is not None:
            ex, rechazos = validar_extraccion(data, chunk.text, acusatorias_a_revision=True)
            rec = _to_record(chunk, ex)
            if rechazos:
                encolar_rechazos("extraccion_mlx", rec, rechazos, prompt_sha256=PROMPT_SHA256)
                log.info("extraction_to_review", chunk_id=chunk.chunk_id, n=len(rechazos))
            return rec
        user = (
            _user_prompt(chunk)
            + "\n\nTu respuesta no fue JSON válido o estaba incompleta. Devuelve un objeto "
            "JSON COMPLETO y más breve (menos relaciones, citas más cortas)."
        )

    log.warning("extraction_failed", chunk_id=chunk.chunk_id, doc=chunk.doc_id)
    rec = _to_record(chunk, ChunkExtraction())
    rec["_failed"] = True  # signals: do not cache, retry on next run
    return rec


def _to_record(chunk: Chunk, ex: ChunkExtraction) -> dict:
    """One facts-cache line = one decision record (spec 069): it carries the model, the prompt
    hash and the ontology version that produced it. Lines without them are pre-069 (legacy)."""
    return {
        "modelo": config.MLX_MODEL,
        "prompt_sha256": PROMPT_SHA256,
        "ontologia_version": _ONT.version,
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "doc_title": chunk.doc_title,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "entities": [e.model_dump(mode="json") for e in ex.entities],
        "relations": [r.model_dump(mode="json") for r in ex.relations],
    }


def _cache_path(doc_id: str):
    return config.FACTS_DIR / f"{doc_id}.jsonl"


def _load_cache(doc_id: str) -> dict[str, dict]:
    path = _cache_path(doc_id)
    out: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["chunk_id"]] = rec
    return out


async def extract_doc(doc_id: str, chunks: list[Chunk]) -> list[dict]:
    """Extract facts for one document's chunks, using the per-doc cache."""
    cache = _load_cache(doc_id)
    todo = [c for c in chunks if c.chunk_id not in cache]
    log.info("extract_doc", doc=doc_id, chunks=len(chunks), cached=len(cache), todo=len(todo))

    if todo:
        path = _cache_path(doc_id)
        write_lock = asyncio.Lock()
        # Write each chunk's result as soon as it completes (incremental cache =
        # a crash mid-document loses at most one chunk, not the whole doc).
        with path.open("a", encoding="utf-8") as f:

            async def run(chunk):
                rec = await _extract_one(chunk)
                if rec.pop("_failed", False):
                    return  # leave uncached so it retries on the next run
                async with write_lock:
                    cache[rec["chunk_id"]] = rec
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()

            await asyncio.gather(*[run(c) for c in todo])

    # Preserve input chunk order.
    return [cache[c.chunk_id] for c in chunks if c.chunk_id in cache]
