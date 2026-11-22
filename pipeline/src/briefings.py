"""Auto-generated investigative briefings.

A graph is great for exploration but bad for synthesis. These briefings give
the reader narrative entry points: a journalistic write-up per case, grounded
in the corpus and cited inline. Written by the host MLX model from a rich
context (top chunks mentioning the case's anchor entities).

Output lands in `sm-vault/_Briefings/` (Obsidian) and is also served by the API
at `/briefings` and `/briefing/{slug}` so the web UI can render them.

Usage: `cd pipeline && uv run python -m src.briefings [--no-llm]`

Convención de procedencia MERCANTIL (BORME/Nearius) para briefings CURADOS (spec 031)
------------------------------------------------------------------------------------
Los briefings marcados `curated: true` se escriben a mano y el generador los respeta
(no los sobrescribe). Al citar datos de estructura societaria en ellos, seguir la misma
disciplina de procedencia que la capa `mercantil` del grafo — que NO es la judicial:

- Los datos del Registro Mercantil (BORME) son estructura oficial pública, NO un hecho
  ni un indicio del caso. Atribúyelos explícitamente ("según el Registro Mercantil
  (BORME) · vía Nearius") y **nunca** con cita `[doc, p.N]`; el ancla es el `borme_id`
  o la hoja registral (p. ej. "hoja NA35314").
- El BORME capta administradores registrales, no la titularidad real en la sombra: la
  presencia como administrador no imputa y la ausencia no exculpa. Presunción de inocencia.
- Preséntalos en su propio inciso o en la sección de contexto, separados de los hechos
  judiciales, igual que la sección "(según prensa)" separa lo reportado por medios.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import structlog

from . import config, llm_client, neo4j_loader
from .ontologia import ontologia

log = structlog.get_logger()

VAULT = config.REPO_ROOT / "sm-vault"
OUT_DIR = VAULT / "_Briefings"

# ---------- briefings: which cases, anchored on which entity_ids ----------

BRIEFINGS: list[dict] = [
    {
        "slug": "caso-koldo-mascarillas",
        "title": "Caso Koldo · mascarillas y obra pública del MITMA",
        "lede": (
            "Cómo el asesor del ministro de Transportes operó como nexo entre "
            "el ministerio, empresarios e intermediarios para favorecer "
            "adjudicaciones durante y después de la pandemia — hechos que el "
            "Tribunal Supremo ha declarado probados."
        ),
        "anchors": [
            "Person:koldo-garcia", "Person:jose-luis-abalos",
            "Person:victor-de-aldama", "Person:santos-cerdan",
            "Company:soluciones-de-gestion",
        ],
    },
    {
        "slug": "cerdan-uco",
        "title": "Santos Cerdán y la red de obra pública (informes UCO)",
        "lede": (
            "Lo que documentan los informes de la UCO sobre el ex secretario "
            "de Organización del PSOE, sus contactos y las presuntas "
            "adjudicaciones a empresas del entorno — y por qué el instructor "
            "del Supremo lo envió a prisión provisional."
        ),
        "anchors": [
            "Person:santos-cerdan", "Person:koldo-garcia",
            "Person:antxon", "Organization:acciona",
        ],
    },
    {
        "slug": "begona-gomez",
        "title": "La imputación de Begoña Gómez",
        "lede": (
            "Origen, hechos imputados, actores y diligencias del procedimiento "
            "contra la esposa del Presidente del Gobierno — ya con juicio oral "
            "abierto ante el Tribunal del Jurado."
        ),
        "anchors": [
            "Person:begona-gomez", "Person:juan-carlos-barrabes",
            "Person:pedro-sanchez",
        ],
    },
    {
        "slug": "ferraz-entrada",
        "title": "Entrada y registro en Ferraz",
        "lede": (
            "Qué buscaba la Guardia Civil en el marco de la Operación Delorme, "
            "qué se cita en los oficios y por qué los papeles del corpus no "
            "acreditan un registro de la sede del PSOE."
        ),
        "anchors": [
            "Person:santos-cerdan", "Person:koldo-garcia",
            "Organization:psoe", "Organization:guardia-civil",
        ],
    },
    {
        "slug": "sintesis-general",
        "title": "Síntesis general · el entramado",
        "lede": (
            "Visión de conjunto del corpus: las líneas de investigación, los "
            "actores centrales, los delitos y el estado procesal de cada "
            "causa, 2018–2026."
        ),
        "anchors": [
            "Person:koldo-garcia", "Person:jose-luis-abalos",
            "Person:santos-cerdan", "Person:victor-de-aldama",
            "Person:begona-gomez", "Person:pedro-sanchez",
        ],
    },
    {
        "slug": "sentencia-abalos-koldo-aldama",
        "title": "La sentencia del Tribunal Supremo · Ábalos, Koldo y Aldama",
        "lede": (
            "Qué condena, qué absuelve y con qué penas: la sentencia núm. "
            "418/2026 de la Sala de lo Penal del Tribunal Supremo, de 22 de "
            "junio de 2026."
        ),
        "anchors": [
            "Person:jose-luis-abalos", "Person:koldo-garcia",
            "Person:victor-de-aldama",
        ],
    },
    {
        "slug": "zapatero-plus-ultra",
        "title": "Zapatero, Plus Ultra y la intermediación (informes UDEF)",
        "lede": (
            "La investigación judicial sobre el rescate público de Plus Ultra "
            "por la SEPI y la presunta intermediación atribuida al entorno del "
            "expresidente Rodríguez Zapatero — una causa separada del caso Koldo."
        ),
        "anchors": [
            "Person:jose-luis-rodriguez-zapatero", "Company:analisis-relevante",
            "Company:focus-social-research", "Company:grupo-gloria",
        ],
    },
    {
        # Press-only briefing: hand-written and marked `curated: true` in the
        # .md, so the loop above skips it (no judicial corpus to regenerate it
        # from yet). Listed here for canonical consistency.
        "slug": "caso-leire-cloacas",
        "title": "Caso Leire · la trama de las cloacas y la pieza SEPI",
        "lede": (
            "La causa que instruye el juez Pedraz sobre una presunta red de "
            "interferencia en investigaciones sensibles para el Gobierno y de "
            "amaños en la contratación de empresas públicas — un frente "
            "documentado, por ahora, solo en la prensa."
        ),
        "anchors": [
            "Person:leire-diez", "Person:jacobo-teijelo", "Organization:sepi",
            "Person:maria-belen-gualda-gonzalez", "Company:tubos-reunidos",
        ],
    },
    {
        # Hand-written and marked `curated: true` in the .md (anchored in the
        # ingested sentencia 152/2026 of the AP de Badajoz), so the loop above
        # skips it. Listed here for canonical consistency.
        "slug": "caso-david-sanchez",
        "title": "La sentencia de Badajoz · David Sánchez y Gallardo",
        "lede": (
            "Qué condena y qué absuelve la sentencia 152/2026 de la Audiencia "
            "Provincial de Badajoz sobre la contratación del hermano del "
            "presidente del Gobierno en la Diputación — una condena por "
            "prevaricación que NO es firme: cabe apelación ante el TSJ de "
            "Extremadura."
        ),
        "anchors": [
            "Person:david-sanchez", "Person:miguel-angel-gallardo",
            "Person:luis-carrero", "Organization:diputacion-de-badajoz",
        ],
    },
    {
        # Hand-written and marked `curated: true` in the .md (anchored in the
        # ingested sentencia 1000/2025 and autos of the Causa Especial
        # 20557/2024), so the loop above skips it. Listed here for canonical
        # consistency.
        "slug": "causa-fiscal-general",
        "title": "La causa del fiscal general · la filtración del correo de González Amador",
        "lede": (
            "Qué condena, qué descarta y qué discuten los votos particulares "
            "de la sentencia 1000/2025 del Tribunal Supremo: la revelación del "
            "correo en que la defensa de González Amador ofrecía una "
            "conformidad reconociendo dos delitos fiscales — la primera "
            "condena penal a un fiscal general del Estado en ejercicio."
        ),
        "anchors": [
            "Person:alvaro-garcia-ortiz", "Person:alberto-gonzalez-amador",
            "Person:pilar-rodriguez-fernandez",
        ],
    },
]


# ---------- retrieval ----------

def fetch_briefing_chunks(driver, anchor_ids: list[str], limit: int = 30) -> list[dict]:
    """Top chunks mentioning multiple anchor entities (richer context first)."""
    cypher = """
    UNWIND $anchors AS aid
    MATCH ({entity_id: aid})-[:MENTIONED_IN]->(ch:Chunk)
    WITH ch, count(DISTINCT aid) AS hits
    ORDER BY hits DESC, ch.chunk_id
    LIMIT $limit
    MATCH (ch)-[:PART_OF]->(d:Document)
    RETURN ch.chunk_id AS id, ch.text AS text, ch.page_start AS page,
           d.title AS doc, d.doc_id AS doc_id, hits
    """
    with driver.session() as s:
        return [dict(r) for r in s.run(cypher, anchors=anchor_ids, limit=limit)]


def fetch_money_trail(driver, top: int = 60) -> list[dict]:
    """Largest documented financial flows in the corpus.

    Tightened to genuine transfers between real economic actors: both endpoints
    must be a Person/Company/Organization (this excludes MoneyAmount, Location,
    Event, Contract and Crime nodes, which otherwise surfaced as garbage rows like
    `Soluciones de Gestión -> [[3,7 millones de euros]]` or `[[70]] -> [[Sevilla]]`),
    and the amount must be positive. This deterministic output is now only a
    FALLBACK — the live `Rastro del dinero.md` is curated and protected from being
    overwritten (see the `curated:` guard in `main_async`)."""
    cypher = """
    MATCH (a)-[r:PAID|RECEIVED]->(b)
    WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
      AND (a:Person OR a:Company OR a:Organization)
      AND (b:Person OR b:Company OR b:Organization)
      AND r.amount IS NOT NULL AND r.amount > 0
    RETURN a.name AS payer, a.entity_id AS payer_id,
           b.name AS payee, b.entity_id AS payee_id,
           type(r) AS dir, r.amount AS amount, r.currency AS currency,
           coalesce(r.date_norm, r.date) AS date, r.date_year AS year,
           r.source_doc AS doc, r.source_page AS page, r.source_quote AS quote
    ORDER BY r.amount DESC, year DESC
    LIMIT $top
    """
    with driver.session() as s:
        return [dict(r) for r in s.run(cypher, top=top)]


# ---------- prompt + LLM ----------

BRIEFING_SYSTEM = """Eres un periodista de investigación que escribe un \
briefing en español para un lector informado. Tu única fuente son los \
fragmentos que se te proporcionan; NO inventes hechos, fechas ni cifras.

Estilo y forma:
- Prosa periodística, sobria, sin adjetivos cargados. Frases cortas.
- Distingue claramente: HECHOS (lo que el documento afirma), INDICIOS (lo \
que sugiere o se está investigando), ACUSACIONES o IMPUTACIONES (lo que se \
imputa pero no está probado). Respeta la presunción de inocencia.
- Cita las fuentes inline con el formato [doc, p.N] usando exactamente los \
identificadores entre corchetes que verás en cada fragmento.
- Lenguaje claro en «Hallazgos», «Rastro del dinero» y «Cronología»: una \
afirmación verificable por frase, con su cita; máximo 25 palabras; voz activa \
con sujeto explícito. La «Síntesis» puede ser prosa más fluida, con las mismas citas.
- Estado procesal: usa SOLO la palabra que dé la fuente entre {estados}, y no \
la cambies por un sinónimo dentro del briefing.
- PROHIBIDO: «parece», «al parecer», «cabe destacar», «evidentemente», «sin duda».
- Nombra a cada persona o sociedad siempre igual, tal como aparece en los fragmentos.
- Devuelve Markdown con esta estructura EXACTA:
  ## Síntesis
  (un párrafo de ~180-220 palabras)
  ## Hallazgos
  1. **Título corto** — desarrollo con citas; ~80 palabras.
  2. **Título corto** — ...
  (entre 4 y 7 hallazgos)
  ## Rastro del dinero
  Bullets con cifras concretas y partes (si las fuentes las dan); 3-6 ítems.
  ## Cronología
  Bullets fechados (YYYY-MM-DD o YYYY) con cita; 5-8 ítems.
  ## Líneas abiertas
  2-4 bullets con lo no resuelto, lo que falta por probar o esclarecer.

No añadas frontmatter, comentarios ni meta-texto fuera del Markdown."""
# Spec 069: the procedural-status words come from the ontology (ordered by stage), never a copy.
_ESTADOS = sorted(ontologia().estados, key=ontologia().rango_estado.get)
BRIEFING_SYSTEM = BRIEFING_SYSTEM.replace(
    "{estados}", ", ".join(e.replace("_", " ") for e in _ESTADOS[:-1]) + f" y {_ESTADOS[-1].replace('_', ' ')}")


def _chunks_block(chunks: list[dict]) -> str:
    return "\n\n---\n\n".join(
        f"[{c['doc']}, p.{c['page']}]\n{c['text'].strip()}" for c in chunks
    )


async def write_briefing(b: dict, chunks: list[dict]) -> str:
    user = (
        f"Briefing solicitado: **{b['title']}**\n"
        f"Idea-fuerza: {b['lede']}\n\n"
        f"Fragmentos de origen (cita por estos identificadores):\n\n"
        f"{_chunks_block(chunks)}\n\n"
        "Escribe el briefing ahora en Markdown, siguiendo la estructura indicada."
    )
    return await llm_client.complete(
        BRIEFING_SYSTEM, user, json_mode=False, max_tokens=3000, retries=2
    )


# ---------- rendering ----------

def _slug(name: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", name)
                if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def render_briefing_note(b: dict, body: str) -> str:
    fm = (
        "---\n"
        f"type: Briefing\n"
        f"slug: {b['slug']}\n"
        f"title: {b['title']!r}\n"
        f"anchors: {b['anchors']}\n"
        "---\n\n"
    )
    return fm + f"# {b['title']}\n\n*{b['lede']}*\n\n" + body.strip() + "\n\n#briefing\n"


def render_money_trail(rows: list[dict]) -> str:
    # Collapse near-duplicate rows (same amount + same pair, regardless of
    # direction) that the extraction produces from several chunks of one fact.
    seen: set = set()
    deduped = []
    for r in rows:
        key = (int(r["amount"] or 0), frozenset((r.get("payer"), r.get("payee"))))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    rows = deduped
    out = [
        "---\ntype: Briefing\nslug: rastro-del-dinero\ntitle: Rastro del dinero\n---\n",
        "# Rastro del dinero\n",
        "*Mayores flujos económicos documentados en el corpus, "
        f"ordenados por cuantía. {len(rows)} ítems.*\n",
        "## Flujos\n",
    ]
    for r in rows:
        amt = f"€{int(r['amount']):,}" if r.get("amount") else "(sin cifra)"
        currency = r.get("currency") or ""
        date = r.get("date") or (str(r.get("year")) if r.get("year") else "s/f")
        payer_link = f"[[{r['payer']}]]"
        payee_link = f"[[{r['payee']}]]"
        arrow = "→" if r["dir"] == "PAID" else "←"
        line = f"- **{amt} {currency}** · {date} · {payer_link} {arrow} {payee_link}"
        if r.get("doc"):
            line += f" · [[{r['doc'].rstrip('.pdf')}]] p.{r['page']}"
        out.append(line)
        if r.get("quote"):
            out.append(f"  > {r['quote'][:200].strip()}")
    out.append("\n#briefing #dinero\n")
    return "\n".join(out)


# ---------- driver ----------

async def main_async(args):
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    driver = neo4j_loader.get_driver()
    try:
        # Money trail is deterministic — write it first. But never clobber a
        # CURATED version: the auto-extracted flows proved unreliable for a public
        # legal site (wrong magnitudes, MoneyAmount nodes as endpoints, edges that
        # are not real transfers), so the live file is hand-verified and carries a
        # `curated: true` marker. Only (re)generate when no curated file exists.
        money_path = OUT_DIR / "Rastro del dinero.md"
        curated = money_path.exists() and "curated: true" in (
            money_path.read_text(encoding="utf-8")[:400]
        )
        if curated:
            log.info("money_trail_curated_skip", path=str(money_path))
        else:
            rows = fetch_money_trail(driver, top=60)
            money_path.write_text(render_money_trail(rows), encoding="utf-8")
            log.info("money_trail_written", n=len(rows))

        if args.no_llm:
            log.info("llm_skipped"); return

        # Briefings — process SEQUENTIALLY. Each briefing has a ~12k-token
        # prompt; running them in parallel blows past Metal memory with
        # decode-concurrency 6 (uncaught METAL OOM in mlx_lm.server).
        for b in BRIEFINGS:
            # Never clobber a hand-curated briefing. Some briefings (e.g. the
            # press-only "Caso Leire") are written by hand and marked
            # `curated: true`; there is no judicial corpus to regenerate them
            # from, so skip any file already carrying that marker.
            out_path = OUT_DIR / f"{b['title']}.md"
            if out_path.exists() and "curated: true" in (
                out_path.read_text(encoding="utf-8")[:400]
            ):
                log.info("briefing_curated_skip", slug=b["slug"], path=str(out_path))
                continue
            chunks = fetch_briefing_chunks(driver, b["anchors"], limit=20)
            log.info("briefing_start", slug=b["slug"], chunks=len(chunks))
            try:
                body = await write_briefing(b, chunks)
            except Exception as e:  # noqa: BLE001
                log.error("briefing_failed", slug=b["slug"], error=str(e))
                continue
            (OUT_DIR / f"{b['title']}.md").write_text(
                render_briefing_note(b, body), encoding="utf-8"
            )
            log.info("briefing_written", slug=b["slug"], chars=len(body))
    finally:
        driver.close()
        await llm_client.aclose()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-llm", action="store_true",
                   help="Skip LLM briefings, write only the deterministic Money trail")
    args = p.parse_args()
    asyncio.run(main_async(args))
    os._exit(0)


if __name__ == "__main__":
    main()
