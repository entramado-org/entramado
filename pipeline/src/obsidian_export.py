"""Export the Neo4j knowledge graph as an Obsidian vault.

Each entity becomes a Markdown note linked via [[wikilinks]]. Documents,
yearly MOCs and hand-curated case MOCs give human entry points.

The vault is a copy of what the graph serves on the day of the export, and nothing else (spec 075):
- only the entities the API serves get a note (same rule as `_served` in `api/app/graph_queries.py`);
- the `Resumen` of a note is the node's own `summary` (the one the loaders put there, with every later correction applied),
  never a YAML read on the side nor a text generated here;
- a judicial pseudonym or an anonymised relative gets no source passages, as in the API;
- every text taken from the graph goes through the API's own mask of personal identifiers (`api/app/redact.py`) before it is
  cut, linked or serialised, so the files carry what the API would serve, not the raw corpus;
- the folders it writes are its own (`MANAGED`): each export builds them aside and then replaces the previous ones, so nothing
  withdrawn from the graph survives and a failure halfway leaves the previous export intact; anything else in the vault
  (briefings, pattern notes, hand-written notes) is never touched. It only works on a root it recognises as a vault;
- links go to the note of the entity, by id: two entities with the same name never share a note nor each other's links.

Usage:
    cd pipeline && uv run python -m src.obsidian_export
    cd pipeline && uv run python -m src.obsidian_export --root ../sm-vault
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import structlog
import yaml

from . import config, neo4j_loader

log = structlog.get_logger()

# ---------- folder layout + tagging ----------

FOLDER: dict[str, str] = {
    "Person": "People",
    "Organization": "Organizations",
    "Company": "Companies",
    "Court": "Courts",
    "LegalCase": "Cases",
    "Crime": "Crimes",
    "Location": "Locations",
    "Event": "Events",
    "MoneyAmount": "Money",
}
TAG: dict[str, str] = {
    "Person": "person", "Organization": "organization", "Company": "company",
    "Court": "court", "LegalCase": "case", "Crime": "crime",
    "Location": "location", "Event": "event", "MoneyAmount": "money",
}

# Doc-id → human title (without extension). Documents folder uses these.
DOC_TITLE_OVERRIDES = {
    # ATS_8919_2025.pdf -> "ATS 8919/2025" reads nicer in wikilinks
}

# The folders this exporter owns. Hand-written notes do not belong in them: every export empties and rewrites them.
MANAGED = tuple(sorted({*FOLDER.values(), "Other", "Documents", "Timeline", "_MOC"}))


def _api_redactor():
    """`redact_text` of the API, loaded from its file (it only needs `re`): one mask for what is served and what is exported."""
    path = config.REPO_ROOT / "api" / "app" / "redact.py"
    spec = importlib.util.spec_from_file_location("_api_redact", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.redact_text


# ---------- name / filename helpers ----------

_FN_ILLEGAL = re.compile(r'[\\/:*?"<>|#^\[\]]')
_FN_MAX = 150     # well under macOS APFS 255-byte cap, leaves headroom

import hashlib  # localized: only used by fn_safe


def fn_safe(name: str) -> str:
    """Filename-safe rendering: Obsidian disallows \\/:*?"<>|#^[] in titles, and
    the OS caps at 255 bytes. We strip illegal chars and, if needed, truncate
    with a short deterministic hash suffix so collisions don't merge notes."""
    s = _FN_ILLEGAL.sub("-", name).strip().strip(".")
    s = re.sub(r"\s+", " ", s) or "untitled"
    if len(s.encode("utf-8")) <= _FN_MAX:
        return s
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
    # Truncate to fit suffix " (#hash)" within the byte budget.
    truncated = s
    while len((truncated + f" (#{h})").encode("utf-8")) > _FN_MAX and truncated:
        truncated = truncated[:-1].rstrip()
    return f"{truncated} (#{h})"


def wl(name: str) -> str:
    """Wikilink to a note. When the filename had to be truncated, render the
    Obsidian piped form `[[truncated|full display]]` so prose stays readable."""
    safe = fn_safe(name)
    if safe != name:
        # Pipe-escape: '|' and ']]' aren't valid inside the display portion.
        display = name.replace("|", "-").replace("]]", "] ]")
        return f"[[{safe}|{display}]]"
    return f"[[{safe}]]"


# entity id → title of its note, set by `export` before anything is rendered (two entities can share a name; never a note).
_TITLES: dict[str, str] = {}


def link(entity_id: str | None, name: str) -> str:
    """Wikilink to the note of that entity (by id), shown with its name. An entity without a note (below the degree
    threshold) is plain text: a link by name could land on a homonym."""
    title = _TITLES.get(entity_id or "")
    if title is None:
        return name
    if title == name:
        return f"[[{title}]]"
    return f"[[{title}|{name.replace('|', '-').replace(']]', '] ]')}]]"


def wl_doc(doc_id: str) -> str:
    return link(f"doc:{doc_id}", doc_id.removesuffix(".pdf"))


# ---------- data fetch ----------

# Same rule as `_served` in `api/app/graph_queries.py` (kept in step by `test_obsidian_export`): a person left with no relation
# to any other entity (nor a posture on a Claim) is only a name next to the passages that mention it, and is not exported.
_PROVENANCE_RELS = ["MENTIONED_IN", "PART_OF"]
SERVED = ("(NOT n:Person OR EXISTS { (n)-[_sr]-(_sm) WHERE (_sm.entity_id IS NOT NULL OR _sm:Claim) "
          f"AND NOT type(_sr) IN {_PROVENANCE_RELS} }})")


def fetch_entities(driver, min_degree: int) -> list[dict]:
    cypher = f"""
    MATCH (n) WHERE n.entity_id IS NOT NULL
      AND (labels(n)[0] IN ['LegalCase','Crime','Court']
           OR coalesce(n.degree,0) >= $min_degree)
      AND {SERVED}
    RETURN n.entity_id AS id, labels(n)[0] AS type, n.name AS name,
           n.role AS role, coalesce(n.aliases,[]) AS aliases,
           coalesce(n.degree,0) AS degree, coalesce(n.mentions,0) AS mentions,
           coalesce(n.is_context,false) AS context,
           n.summary AS summary, coalesce(n.is_pseudonym,false) AS pseudonym
    ORDER BY n.degree DESC, n.name, n.entity_id
    """
    with driver.session() as s:
        return [dict(r) for r in s.run(cypher, min_degree=min_degree)]


def fetch_documents(driver) -> list[dict]:
    cypher = """
    MATCH (d:Document) OPTIONAL MATCH (d)<-[:PART_OF]-(c:Chunk)
    WITH d, count(c) AS chunks
    RETURN d.doc_id AS id, d.title AS title, d.page_count AS pages,
           coalesce(d.low_text_pages, []) AS low_text, chunks
    ORDER BY chunks DESC
    """
    with driver.session() as s:
        return [dict(r) for r in s.run(cypher)]


def fetch_top_relations(driver, entity_id: str, limit: int = 60) -> list[dict]:
    """Aggregated neighbors with rel-type + direction + evidence count."""
    cypher = """
    MATCH (c {entity_id:$id})-[r]-(m)
    WHERE m.entity_id IS NOT NULL AND NOT type(r) IN ['MENTIONED_IN','PART_OF']
    WITH c, m, type(r) AS rt,
         CASE WHEN startNode(r) = c THEN 'out' ELSE 'in' END AS dir,
         count(r) AS cnt,
         max(CASE WHEN r.source_kind = 'contexto' THEN 1 ELSE 0 END) = 1 AS context
    // Context edges (count 1) must never be cut by the top-N on high-degree
    // hubs, so sort them to the front before the document edges by evidence.
    ORDER BY context DESC, cnt DESC LIMIT $limit
    RETURN m.entity_id AS id, m.name AS name, labels(m)[0] AS type,
           rt, dir, cnt, context
    """
    with driver.session() as s:
        return [dict(r) for r in s.run(cypher, id=entity_id, limit=limit)]


def fetch_dated_for(driver, entity_id: str, limit: int = 60) -> list[dict]:
    cypher = """
    MATCH (a)-[r]->(b)
    WHERE (a.entity_id = $id OR b.entity_id = $id)
      AND a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
      AND r.date_year IS NOT NULL
      AND NOT type(r) IN ['MENTIONED_IN','PART_OF']
    RETURN coalesce(r.date_norm, toString(r.date_year)) AS date,
           r.date_year AS year, type(r) AS rt,
           a.name AS s, a.entity_id AS sid,
           b.name AS t, b.entity_id AS tid,
           r.source_doc AS doc, r.source_page AS page
    ORDER BY date LIMIT $limit
    """
    with driver.session() as s:
        return [dict(r) for r in s.run(cypher, id=entity_id, limit=limit)]


# Anonymised relatives: their chunks and the literal quotes of their relations name them in clear, so the vault never writes
# them (same rule as the API, `api/app/graph_queries.py`). The documents stay readable as a whole.
_ANON_RELATIVE_PREFIX = "Person:familiar-de-"


def fetch_chunks_for(driver, entity_id: str, limit: int = 8) -> list[dict]:
    if entity_id.startswith(_ANON_RELATIVE_PREFIX):
        return []
    cypher = """
    MATCH (c {entity_id:$id})-[:MENTIONED_IN]->(ch:Chunk)-[:PART_OF]->(d:Document)
    RETURN d.title AS doc, d.doc_id AS doc_id, ch.page_start AS page,
           ch.text AS text, ch.chunk_id AS chunk_id
    ORDER BY d.title, ch.page_start LIMIT $limit
    """
    with driver.session() as s:
        return [dict(r) for r in s.run(cypher, id=entity_id, limit=limit)]


def fetch_year_events(driver, year: int, limit: int = 400) -> list[dict]:
    cypher = """
    MATCH (a)-[r]->(b)
    WHERE r.date_year = $year AND a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
      AND NOT type(r) IN ['MENTIONED_IN','PART_OF']
    RETURN coalesce(r.date_norm, toString(r.date_year)) AS date,
           a.name AS s, b.name AS t, type(r) AS rt,
           a.entity_id AS sid, b.entity_id AS tid,
           r.source_doc AS doc, r.source_page AS page,
           CASE WHEN a.entity_id STARTS WITH $anon OR b.entity_id STARTS WITH $anon THEN null ELSE r.source_quote END AS quote
    ORDER BY date LIMIT $limit
    """
    with driver.session() as s:
        return [dict(r) for r in s.run(cypher, year=year, limit=limit, anon=_ANON_RELATIVE_PREFIX)]


def fetch_years(driver) -> list[int]:
    cypher = "MATCH ()-[r]->() WHERE r.date_year IS NOT NULL RETURN DISTINCT r.date_year AS y ORDER BY y"
    with driver.session() as s:
        return [r["y"] for r in s.run(cypher) if r["y"]]


# ---------- rendering ----------

def _frontmatter(d: dict) -> str:
    body = yaml.safe_dump(d, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{body}---\n"


def _trunc(s: str | None, n: int = 220) -> str:
    if not s:
        return ""
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def render_entity_note(e: dict, rels: list[dict], dated: list[dict], chunks: list[dict]) -> str:
    type_ = e["type"]
    aliases = [a for a in (e["aliases"] or []) if a]
    fm = {
        "entity_id": e["id"],
        "type": type_,
        **({"role": e["role"]} if e.get("role") else {}),
        **({"aliases": aliases} if aliases else {}),
        "degree": e["degree"],
        "mentions": e["mentions"],
        **({"context": True} if e.get("context") else {}),
        **({"pseudonym": True} if e.get("pseudonym") else {}),
    }
    out = [_frontmatter(fm), f"# {e['name']}\n"]
    if e.get("role"):
        out.append(f"*{e['role']}*\n")
    if e.get("context"):
        out.append("> [!info] Contexto público\n"
                   "> Esta entidad procede del **contexto público**, no de los "
                   "documentos judiciales del corpus.\n")

    if e.get("pseudonym"):
        out.append("> [!warning] Seudónimo judicial\n"
                   "> Nombre de pila ficticio con el que la resolución judicial sustituye al nombre real. Es una persona "
                   "distinta de cualquier homónimo con nombre civil; la fuente no da su identidad.\n")

    if (e.get("summary") or "").strip():
        out.append("## Resumen\n")
        out.append(e["summary"].strip() + "\n")

    # group neighbors by relation type
    if rels:
        out.append("## Conexiones principales\n")
        by_rt: dict[str, list[dict]] = defaultdict(list)
        for r in rels:
            by_rt[r["rt"]].append(r)
        # Stable display order: most-used rel types first
        for rt in sorted(by_rt, key=lambda k: -sum(x["cnt"] for x in by_rt[k])):
            out.append(f"### {rt}\n")
            for r in by_rt[rt][:25]:
                arrow = "→" if r["dir"] == "out" else "←"
                tag = "contexto público" if r.get("context") else f"{r['cnt']} evidencias"
                out.append(f"- {arrow} {link(r['id'], r['name'])} · {tag}\n")
            out.append("")

    # chronology — only dated relations involving this entity
    if dated:
        out.append("## Cronología\n")
        for d in dated[:40]:
            other_name, other_id = (d["t"], d["tid"]) if d["sid"] == e["id"] else (d["s"], d["sid"])
            arrow = "→" if d["sid"] == e["id"] else "←"
            citation = f" · {wl_doc(d['doc'])} p.{d['page']}" if d.get("doc") else ""
            out.append(f"- **{d['date']}** — {d['rt']} {arrow} {link(other_id, other_name)}{citation}\n")
        out.append("")

    # source quotes
    if chunks:
        out.append("## Fuentes\n")
        for c in chunks:
            out.append(f"> {_trunc(c['text'])}\n> — {wl_doc(c['doc_id'])} p.{c['page']}\n")
        out.append("")

    # tags
    tags = [f"#{TAG.get(type_, 'entity')}"]
    years = sorted({d["year"] for d in dated if d.get("year")})
    tags += [f"#year/{y}" for y in years[:8]]
    out.append(" ".join(tags) + "\n")

    return "\n".join(out)


def render_document_note(doc: dict) -> str:
    fm = {
        "type": "Document",
        "doc_id": doc["id"],
        "pages": doc["pages"],
        "chunks": doc["chunks"],
    }
    out = [_frontmatter(fm), f"# {doc['title']}\n"]
    out.append(f"*Documento de origen — {doc['chunks']} fragmentos, {doc['pages']} páginas.*\n")
    if doc.get("low_text"):
        out.append(f"\n> Páginas sin texto extraíble (sólo imagen): {len(doc['low_text'])} de {doc['pages']}.\n")
    out.append("\n## Aparece en\n")
    out.append("```dataview\nLIST FROM outgoing([[]]) WHERE type != \"Document\"\nLIMIT 40\n```\n")
    out.append("\n#document\n")
    return "\n".join(out)


def render_year_moc(year: int, events: list[dict]) -> str:
    fm = {"type": "YearMOC", "year": year, "events": len(events)}
    out = [_frontmatter(fm), f"# {year}\n"]
    out.append(f"*Hechos fechados en {year} extraídos del corpus ({len(events)}).*\n")
    by_month: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        month = ev["date"][:7] if len(ev["date"]) >= 7 else str(year)
        by_month[month].append(ev)
    for month in sorted(by_month):
        out.append(f"\n## {month}\n")
        for ev in by_month[month][:80]:
            cite = f" · {wl_doc(ev['doc'])} p.{ev['page']}" if ev.get("doc") else ""
            out.append(f"- **{ev['date']}** — {link(ev.get('sid'), ev['s'])} {ev['rt']} {link(ev.get('tid'), ev['t'])}{cite}")
            if ev.get("quote"):
                out.append(f"  > {_trunc(ev['quote'], 180)}")
    out.append(f"\n\n#year/{year} #timeline\n")
    return "\n".join(out)


CASE_MOCS: dict[str, dict] = {
    "Caso Koldo": {
        "summary": (
            "Trama de presunto cohecho y tráfico de influencias en la adjudicación "
            "de contratos públicos durante la pandemia y en obra pública del Ministerio "
            "de Transportes. El asesor del ministro José Luis Ábalos, Koldo García, "
            "habría operado como nexo con empresarios e intermediarios."
        ),
        "people": ["Koldo García", "José Luis Ábalos", "Víctor de Aldama", "Santos Cerdán"],
        "orgs": ["Ministerio de Transportes", "UCO", "Guardia Civil", "Tribunal Supremo"],
        "crimes": ["cohecho", "tráfico de influencias", "organización criminal", "blanqueo de capitales"],
    },
    "Mascarillas (auditoría)": {
        "summary": (
            "Auditoría del Ministerio de Sanidad sobre la ejecución de la orden de "
            "adquisición de mascarillas durante la pandemia y los contratos vinculados "
            "a la red de intermediarios señalada por la UCO."
        ),
        "people": ["Koldo García", "Víctor de Aldama", "José Luis Ábalos"],
        "orgs": ["Ministerio de Transportes", "Tribunal de Cuentas"],
        "crimes": ["cohecho", "malversación", "contratación pública"],
    },
    "Begoña Gómez (imputación)": {
        "summary": (
            "Auto de imputación de la esposa del Presidente del Gobierno por presuntos "
            "tráfico de influencias y otros delitos, con origen en denuncias y diligencias "
            "previas instruidas por el Juzgado nº 41 de Madrid."
        ),
        "people": ["Begoña Gómez", "Pedro Sánchez", "Juan Carlos Barrabés"],
        "orgs": ["Universidad Complutense", "Manos Limpias"],
        "crimes": ["tráfico de influencias", "apropiación indebida"],
    },
    "Cerdán-UCO": {
        "summary": (
            "Informes de la Unidad Central Operativa centrados en Santos Cerdán, "
            "secretario de Organización del PSOE, y su entorno; presuntas adjudicaciones "
            "de obra pública a empresas vinculadas."
        ),
        "people": ["Santos Cerdán", "Koldo García", "Antxón"],
        "orgs": ["UCO", "Guardia Civil", "Acciona"],
        "crimes": ["cohecho", "tráfico de influencias", "financiación ilegal"],
    },
}


_MOC_TYPES = {"people": ("Person",), "orgs": ("Organization", "Company", "Court"), "crimes": ("Crime",)}


def render_case_moc(name: str, m: dict, exported: dict[tuple[str, str], str]) -> str:
    """`exported`: (type, name) → id of the entities this export writes (the first of each, by degree). A case map only lists what has a note today — the fixed lists
    above never bring back an entity the graph no longer serves."""
    fm = {"type": "CaseMOC", "case": name}
    out = [_frontmatter(fm), f"# {name}\n", f"*{m['summary']}*\n"]
    for title, key in (("Personas clave", "people"), ("Organizaciones", "orgs"), ("Delitos", "crimes")):
        out.append(f"\n## {title}\n")
        for x in m[key]:
            eid = next((exported[(t, x)] for t in _MOC_TYPES[key] if (t, x) in exported), None)
            if eid:
                out.append(f"- {link(eid, x)}")
    out.append("\n## Cronología\n")
    out.append("```dataview\nLIST FROM #timeline\nSORT file.name asc\n```\n")
    out.append(f"\n#case/{fn_safe(name).lower().replace(' ', '-')} #moc\n")
    return "\n".join(out)


README = """# El Entramado — vault

Este *vault* de Obsidian contiene el grafo de conocimiento de El
Entramado: personas, organizaciones, empresas, causas, delitos, eventos y lugares
extraídos de documentos judiciales y policiales públicos.

## Cómo usarlo

- Abre **`_MOC/Entramado.md`** como punto de partida.
- Cada entidad tiene una página propia en su carpeta (`People/`,
  `Organizations/`, `Cases/`, `Crimes/`, `Events/`, `Documents/`, …).
- Los enlaces internos `[[Nombre]]` te llevan a otras entidades; el panel de
  **backlinks** (lateral) muestra quién la menciona.
- Etiquetas útiles: `#person`, `#organization`, `#case`, `#crime`, `#year/2021`, …
- La carpeta **`Timeline/`** tiene un MOC por año con todos los hechos fechados.
- La carpeta **`_MOC/`** tiene síntesis por caso (Koldo, mascarillas, Begoña Gómez,
  Cerdán-UCO).

## Vista de grafo

Filtra la vista de grafo (`Ctrl/Cmd+G`) por carpeta o etiqueta para evitar el
caos visual: por ejemplo, sólo `People/` + `Cases/` con `#year/2020`.

## Presunción de inocencia

Este vault organiza información de procedimientos en curso. Las relaciones
reflejan lo que afirman las fuentes citadas — indicios, imputaciones o
acusaciones — y no constituyen una declaración de culpabilidad. Cada hecho
enlaza a su documento y página de origen.
"""


def render_top_moc(case_names: Iterable[str]) -> str:
    out = ["# Entramado · punto de entrada\n",
           "Mapa del grafo. Cada caso enlaza con sus personas, organizaciones, delitos y cronología.\n",
           "\n## Casos\n"]
    for c in case_names:
        out.append(f"- {link(f'moc:{c}', c)}")
    out.append("\n## Acceso rápido\n")
    out.append("- Personas más conectadas: `dataview LIST FROM \"People\" SORT degree desc LIMIT 12`")
    out.append("- Línea de tiempo: ver carpeta **Timeline/**")
    out.append("- Documentos de origen: ver carpeta **Documents/**")
    out.append("\n#moc\n")
    return "\n".join(out)


# ---------- main ----------

def write(root: Path, folder: str, name: str, body: str) -> Path:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    fn = d / f"{name}.md"   # `name` is a title already made filename-safe and unique by `export`
    fn.write_text(body, encoding="utf-8")
    return fn


_MARK = ".entramado-vault"
_STAGING = ".exportando"


def _check_root(root: Path) -> None:
    """Refuse a root that is not a vault of ours: an empty folder, one a previous export marked, or the existing vault (it has
    `_Briefings`). And refuse to write or delete through a link."""
    briefings = root / "_Briefings"
    known = ((root / _MARK).is_file() and not (root / _MARK).is_symlink()) or (briefings.is_dir() and not briefings.is_symlink())
    if not known and any(root.iterdir()):
        raise RuntimeError(f"{root} no es un vault de El Entramado (ni está vacío): no se exporta")
    for name in (*MANAGED, "README.md", _STAGING, _MARK):
        d = root / name
        if d.is_symlink() or (name in MANAGED and d.exists() and not d.is_dir()):
            raise RuntimeError(f"{name!r} es un enlace o no es un directorio real: no se exporta")


def _mask(redact, rows: list[dict], *keys: str) -> list[dict]:
    for r in rows:
        for k in keys:
            v = r.get(k)
            if isinstance(v, str):
                r[k] = redact(v)
            elif isinstance(v, list):
                r[k] = [redact(x) if isinstance(x, str) else x for x in v]
    return rows


def export(driver, root: Path, min_degree: int) -> dict:
    redact = _api_redactor()
    _check_root(root)
    entities = _mask(redact, fetch_entities(driver, min_degree), "name", "role", "aliases", "summary")
    documents = fetch_documents(driver)
    log.info("vault_export_start", entities=len(entities), docs=len(documents), root=str(root))
    if not entities or not documents:
        # An empty or half-loaded graph must not empty the vault.
        raise RuntimeError("el grafo no devuelve entidades o documentos: no se exporta (¿Neo4j equivocado o sin cargar?)")

    # One title per entity, unique in the whole vault whatever the case or the Unicode form (Obsidian resolves a link by
    # title, and the filesystems that host the vault fold both): the second entity of a name carries a short hash of its id.
    _TITLES.clear()
    taken: set[str] = set()

    def claim(title: str, ident: str) -> str:
        title = fn_safe(title)
        if unicodedata.normalize("NFC", title).casefold() in taken:
            title = fn_safe(f"{title} ({hashlib.sha1(ident.encode('utf-8')).hexdigest()[:6]})")
        key = unicodedata.normalize("NFC", title).casefold()
        if key in taken:
            raise RuntimeError(f"dos notas con el mismo título: {title}")
        taken.add(key)
        return title

    for e in entities:
        _TITLES[e["id"]] = claim(e["name"], e["id"])
    for d in documents:
        _TITLES[f"doc:{d['id']}"] = claim(d["id"].removesuffix(".pdf"), f"doc:{d['id']}")
    for name in (*CASE_MOCS, "Entramado"):
        _TITLES[f"moc:{name}"] = claim(name, f"moc:{name}")
    exported: dict[tuple[str, str], str] = {}
    for e in entities:
        exported.setdefault((e["type"], e["name"]), e["id"])

    # The root is ours from here on (a first export that fails halfway can be retried).
    (root / _MARK).write_text("Carpetas gestionadas por pipeline/src/obsidian_export.py: " + ", ".join(MANAGED) + "\n", encoding="utf-8")
    stage = root / _STAGING
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    written = 0

    def put(folder: str, title: str, body: str) -> None:
        nonlocal written
        write(stage, folder, title, body)
        written += 1

    # Entity notes. Events are bucketed by earliest dated year so a thousand of them don't sit flat in one folder.
    for i, e in enumerate(entities, 1):
        rels = _mask(redact, fetch_top_relations(driver, e["id"], limit=60), "name")
        dated = _mask(redact, fetch_dated_for(driver, e["id"], limit=60), "s", "t")
        chunks = [] if e.get("pseudonym") else _mask(redact, fetch_chunks_for(driver, e["id"], limit=8), "text", "doc")
        folder = FOLDER.get(e["type"], "Other")
        if e["type"] == "Event":
            years = sorted({d["year"] for d in dated if d.get("year")})
            folder = f"Events/{years[0]}" if years else "Events/sin-fecha"
        put(folder, _TITLES[e["id"]], render_entity_note(e, rels, dated, chunks))
        if i % 200 == 0:
            log.info("entity_notes_progress", written=i, total=len(entities))

    for d in documents:
        put("Documents", _TITLES[f"doc:{d['id']}"], render_document_note(d))

    for y in fetch_years(driver):
        events = _mask(redact, fetch_year_events(driver, y, limit=400), "s", "t", "quote")
        if events:
            put("Timeline", claim(str(y), f"year:{y}"), render_year_moc(y, events))   # nothing links to a year by title

    for name, m in CASE_MOCS.items():
        put("_MOC", _TITLES[f"moc:{name}"], render_case_moc(name, m, exported))
    put("_MOC", _TITLES["moc:Entramado"], render_top_moc(CASE_MOCS.keys()))
    (stage / "README.md").write_text(README, encoding="utf-8")

    # Everything is read and written: only now does the previous export step aside, and it is deleted when the new one is in
    # place. If a move fails, what was moved is put back.
    aside = stage / ".anterior"
    aside.mkdir()
    moved: list[tuple[Path, Path]] = []
    try:
        for name in MANAGED:
            old = root / name
            if old.exists():
                old.rename(aside / name)
                moved.append((aside / name, old))
            if (stage / name).exists():
                (stage / name).rename(old)
                moved.append((old, stage / name))
    except OSError:
        for src, dst in reversed(moved):
            src.rename(dst)
        raise
    (stage / "README.md").replace(root / "README.md")
    shutil.rmtree(stage)   # does not follow links inside the tree

    stats = {"entities": len(entities), "documents": len(documents), "written": written}
    log.info("vault_export_done", root=str(root), **stats)
    return stats


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=str(config.REPO_ROOT / "sm-vault"))
    p.add_argument("--min-degree", type=int, default=2)
    args = p.parse_args()
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    driver = neo4j_loader.get_driver()
    try:
        export(driver, root, args.min_degree)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
