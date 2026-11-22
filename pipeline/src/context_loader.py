"""Load the curated CONTEXT layer into Neo4j.

The graph is built only from judicial/police documents, so well-known public
context (offices, party affiliations, family ties) is missing. This loader reads
hand-curated YAML from `pipeline/data/context/*.yaml` and merges it on top of the
document-extracted graph, tagged `source_kind="contexto"` so the UI, the Obsidian
vault and the chat assistant keep it clearly separate from judicial facts.

It is idempotent: entities MERGE by the same deterministic `entity_id` the
pipeline uses, and context relationships are keyed by `source_kind` so re-running
never duplicates them and never overwrites a document-sourced edge of the same
type.

Retiradas y fuentes (spec 061): una entidad puede declarar `role: remove` (borra
el rol curado, MATCH-only: nunca crea el nodo), un summary puede declarar
`remove: true` (borra summary/summary_sources/summary_updated) y una relación
puede declarar `source_url:` (https) + `source_date:`, que se guardan como
`r.source_url` / `r.source_date` — una afirmación de contexto sin fuente es
exactamente lo que la auditoría dual retira. (La clave es `source_url`, no
`source`: en una relación `source:`/`target:` son los EXTREMOS.) Los ficheros
`*_qa061.yaml` se cargan LOS ÚLTIMOS para poder pisar a los anteriores.

⚠️ FAIL-CLOSED desde la spec 061: una fila de `relations:` SIN `source_url` válido
NO se carga. La retirada de las 49 aristas de contexto sin fuente se hizo en el
grafo por Path A y el siguiente `make context` las resucitaba desde estos mismos
YAML (47 recreadas, medido en el efímero de la Fase 1). Las filas se quedan en el
fichero a propósito: son el backlog de «documentar o retirar».

Usage:
    cd pipeline && uv run python -m src.context_loader
    cd pipeline && uv run python -m src.context_loader --dir ../some/other/dir
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import structlog

from . import config, neo4j_loader, seudonimos
from .entity_resolution import entity_id
from .ontologia import ontologia
from .schema import EntityType

log = structlog.get_logger()

CONTEXT_DIR = config.WORK_DIR / "context"

_ENTITY_TYPES = {t.value for t in EntityType}


ROLE_REMOVE = "remove"  # centinela de `role:` — borra el rol curado (spec 061)


def _ordered(directory: Path) -> list[Path]:
    """Ficheros del overlay en orden de aplicación: alfabético, pero los
    `*_qa061.yaml` SIEMPRE al final. Las listas se concatenan y gana el último
    que habla de una entidad, así que la curación de la auditoría dual (061)
    tiene que cargarse después de `roles_enriched.yaml` / `summaries.yaml`."""
    files = [*directory.glob("*.yaml"), *directory.glob("*.yml")]
    return sorted(files, key=lambda f: (f.stem.endswith("_qa061"), f.name))


def load_files(directory: Path) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    """Read and merge every *.yaml / *.yml file in the context directory. Spec 070 (AC161): each file
    passes the tree's whole form (`seudonimos.problemas_forma`: a map root, each section a list of
    maps, each field the inventory declares a list a list — `aliases: {'[]': …}` would load its keys
    as aliases and a text, its letters —, keys of the inventory and leaves of their type: an alias
    that is not a text is not dropped in silence); the roster, the tree's own rule for it
    (`seudonimos.ids_seudonimos`, AC114: a map, an empty list or a row without `id` would load its
    keys or no pseudonyms at all, and skip the anti-de-anonymisation guard); otherwise exit 2
    (`ErrorDeForma`) naming every file, section and row, never a value. AC192: with every file read,
    the tree's joint check (`seudonimos.problemas_contexto`): a relation endpoint no file declares
    stops too; a relation without an https source (fail-closed 061: never loaded) is a notice with
    its position, and the count — never silent."""
    files = _ordered(directory)
    documentos: list[tuple[str, dict]] = []   # spec 070 (AC192): every file but the roster, for the joint check
    entities: list[dict] = []
    relations: list[dict] = []
    merges: list[dict] = []
    summaries: list[dict] = []
    pseudonyms: list[dict] = []
    motivos: list[str] = []
    for f in files:
        rel = f"{seudonimos.CONTEXT_REL}/{f.name}"
        doc = seudonimos.leer_yaml(f.read_text(encoding="utf-8"), f.name)  # sin el texto de un YAML roto
        problemas = [f"forma inválida (spec 070): {m}" for m in seudonimos.problemas_forma(rel, doc)]
        if rel == seudonimos.PSEUDONYMS_REL:
            try:
                seudonimos.ids_seudonimos(f)
            except seudonimos.ErrorDeForma as err:
                problemas += err.motivos
        if problemas:
            motivos += problemas
            continue
        doc = doc if isinstance(doc, dict) else {}   # un fichero vacío (el roster, no: AC114)
        if rel != seudonimos.PSEUDONYMS_REL:
            documentos.append((rel, doc))
        entities.extend(doc.get("entities") or [])
        relations.extend(doc.get("relations") or [])
        merges.extend(doc.get("merges") or [])
        summaries.extend(doc.get("summaries") or [])
        pseudonyms.extend(doc.get("pseudonyms") or [])
        log.info("context_file", file=f.name,
                 entities=len(doc.get("entities") or []),
                 relations=len(doc.get("relations") or []),
                 merges=len(doc.get("merges") or []),
                 summaries=len(doc.get("summaries") or []),
                 pseudonyms=len(doc.get("pseudonyms") or []))
    if not motivos:   # AC192: the tree's joint check — an endpoint no file declares stops, a relation without an
        # https source is a notice with its position (fail-closed 061: never loaded, never silent)
        juntos, avisos = seudonimos.problemas_contexto(documentos)
        motivos += [f"forma inválida (spec 070): {m}" for m in juntos]
        for aviso in avisos:
            log.warning("context_aviso", aviso=aviso)
        if avisos:
            log.warning("context_avisos", count=len(avisos), note=seudonimos.SIN_FUENTE_HTTPS)
    if motivos:
        raise seudonimos.ErrorDeForma(motivos)
    return entities, relations, merges, summaries, pseudonyms


def _prepare(entities: list[dict], relations: list[dict]):
    """Resolve names to entity_ids; validate types. Returns (nodes, edges)."""
    nodes: list[dict] = []
    name_to_id: dict[str, str] = {}
    for e in entities:
        etype, name = e.get("type"), (e.get("name") or "").strip()
        motivo = seudonimos.forma_contexto("entities", e)
        if motivo:   # spec 070 (AC192): `load_files` stops it first; the tree's reason, never a value
            raise SystemExit(f"context: {motivo}")
        # `entity_id:` explícito (spec 061): los nodos cuyo id no se deriva del nombre
        # (seudónimos judiciales «-seudonimo-ts», homónimos escindidos) se pueden
        # apuntar directamente. Sin él, el id se deriva del nombre como siempre.
        raw_name = name
        if etype == "Crime":
            # Spec 069: crimes are ontology vocabulary (a non-canonical one already aborted, above).
            name = ontologia().validar("delitos", name, articulo=e.get("article")).valor
        eid = (e.get("entity_id") or "").strip() or entity_id(etype, name)
        name_to_id[raw_name] = eid
        role = e.get("role")
        # `role: remove` (spec 061) retira un rol curado que la auditoría tumbó.
        # Es MATCH-only: una retirada nunca puede crear el nodo que niega.
        role_remove = isinstance(role, str) and role.strip().lower() == ROLE_REMOVE
        nodes.append({
            "entity_id": eid, "type": etype, "name": name,
            "role": None if role_remove else role,
            "role_remove": role_remove,
            "aliases": [a.strip() for a in (e.get("aliases") or [])
                        if isinstance(a, str) and a.strip()],
        })

    edges: list[dict] = []
    unsourced = 0
    for r in relations:
        rtype = r.get("type")
        src, tgt = (r.get("source") or "").strip(), (r.get("target") or "").strip()
        # Spec 070 (AC192): a type outside the ontology, an endpoint no file declares — `load_files` stops them
        # first; here, the tree's reason, never a value.
        motivo = seudonimos.forma_contexto("relations", r)
        if motivo:
            raise SystemExit(f"context: {motivo}")
        sid = name_to_id.get(src) or _id_from_loose(src, entities)
        tid = name_to_id.get(tgt) or _id_from_loose(tgt, entities)
        if not sid or not tid:
            raise SystemExit(f"context: {seudonimos.EXTREMO_SIN_RESOLVER}")
        # ⚠️ La clave es `source_url:`, NO `source:` — en una relación de contexto
        # `source:`/`target:` ya son los EXTREMOS (nombres de entidad).
        source = (r.get("source_url") or "").strip() or None
        # Fail-closed (spec 061): una afirmación de contexto sin fuente pública https NO se
        # carga. La retirada de estas aristas se hizo en el grafo por Path A; sin este
        # gate, el siguiente `make context` las resucitaba desde el mismo YAML. `load_files`
        # ya avisó de cada una con su posición (spec 070, AC192).
        if not source or not source.startswith("https://"):
            unsourced += 1
            continue
        status = r.get("status")  # optional procedural stage (rendered "[estado: …]")
        if status is not None:
            # Spec 069: a curated file with an out-of-vocabulary status is a data error — abort the
            # load (fail-closed) rather than write it or silently drop the edge. A status whose card
            # demands a sentence (condena_anulada) must cite the sentence itself, on an official
            # publisher — never the press reporting it (Codex/peer review of #15). Spec 070 (AC184,
            # AC187): the tree's rule (`seudonimos.forma_contexto`, above, with its list of publishers)
            # already aborted any of those, with its reason — the tree and `load_files` stop it first.
            status = ontologia().validar("estados_procesales", status).valor
        edges.append({
            "source_id": sid, "target_id": tid, "type": rtype,
            "kind": r.get("kind"), "note": r.get("note"),
            "status": status,
            # Fuente pública de la afirmación de contexto (spec 061): sin ella la
            # arista es un dicho sin respaldo y el lint L17 la marca para retirada.
            "source_url": source, "source_date": (r.get("source_date") or "").strip() or None,
        })
    if unsourced:
        log.warning("context_relations_unsourced_skipped", count=unsourced, loaded=len(edges),
                    note="filas sin `source_url` https — se quedan en el YAML como backlog a documentar")
    return nodes, edges


def _id_from_loose(name: str, entities: list[dict]) -> str | None:
    """Allow a relation endpoint that wasn't listed under `entities` as long as
    we can find its declared type (so endpoints must appear in `entities`)."""
    for e in entities:
        if (e.get("name") or "").strip() == name and e.get("type") in _ENTITY_TYPES:
            return entity_id(e["type"], name)
    return None


def load(driver, nodes: list[dict], edges: list[dict]):
    # Entities: onCreate sets identity + flags it as context-only; onMatch only
    # enriches an existing (document-sourced) node with a role — it does NOT mark
    # that node is_context, so real document figures keep their provenance.
    node_q = """
    UNWIND $rows AS row
    CALL apoc.merge.node([row.type], {entity_id: row.entity_id},
      {name: row.name, canonical_name: row.name, role: row.role,
       source_kind: 'contexto', is_context: true, mentions: 0, degree: 0},
      {role: coalesce(row.role, '')}) YIELD node
    RETURN count(node)
    """
    rel_q = """
    UNWIND $rows AS row
    MATCH (a {entity_id: row.source_id})
    MATCH (b {entity_id: row.target_id})
    CALL apoc.merge.relationship(a, row.type,
      {source_kind: 'contexto'},
      {kind: row.kind, note: row.note, status: row.status,
       source_url: row.source_url, source_date: row.source_date},
      b, {kind: row.kind, note: row.note, status: row.status,
          source_url: row.source_url, source_date: row.source_date}) YIELD rel
    RETURN count(rel)
    """
    # `role: remove` — MATCH-only, después del MERGE de nodos para que gane sobre
    # el rol que hubiera fijado un fichero anterior del overlay.
    role_remove_q = """
    UNWIND $rows AS row
    MATCH (n {entity_id: row.entity_id})
    REMOVE n.role
    RETURN count(n) AS n
    """
    # Curated aliases (e.g. the official BOE registral name of a public figure):
    # additive union onto the EXISTING node's aliases, deduped, never subtracts
    # (removing an alias from YAML does not remove it from the graph — that goes
    # through a manual sweep, same channel as merges). MATCH-only: never creates.
    alias_q = """
    UNWIND $rows AS row
    MATCH (n {entity_id: row.entity_id})
    SET n.aliases = [a IN apoc.coll.toSet(coalesce(n.aliases, []) + row.aliases)
                     WHERE a IS NOT NULL AND a <> n.name]
    RETURN count(n)
    """
    alias_rows = [n for n in nodes if n.get("aliases")]
    # Una entrada `role: remove` no declara identidad: no entra en el MERGE (que
    # crearía el nodo), solo en el REMOVE y —si las trae— en los alias.
    merge_rows = [n for n in nodes if not n.get("role_remove")]
    remove_rows = [{"entity_id": n["entity_id"]} for n in nodes if n.get("role_remove")]
    removed = 0
    with driver.session() as s:
        s.run(node_q, rows=merge_rows)
        s.run(rel_q, rows=edges)
        if alias_rows:
            s.run(alias_q, rows=alias_rows)
        if remove_rows:
            removed = s.run(role_remove_q, rows=remove_rows).single()["n"]
    log.info("context_loaded", nodes=len(merge_rows), edges=len(edges),
             alias_nodes=len(alias_rows), roles_removed=removed)


# Curated de-duplication: the document extractor can leave two nodes for the
# same real entity (e.g. "PSOE" vs "Partido Socialista", "UCO" vs "Unidad
# Central Operativa") when their surface forms aren't fuzzy-similar. We fold the
# `duplicates` into the `canonical` node, combining their relationships and
# keeping the duplicate names as aliases. Duplicates default to the canonical's
# type unless given as {name, type}.
#
# NOT implemented with apoc.refactor.mergeNodes(mergeRels:true): that collapses
# ALL parallel same-type edges of the canonical — including pre-existing pairs
# the duplicate never touched — discarding their evidence props. Caught
# 2026-07-11: `load_context` re-creates already-merged duplicate names from the
# context YAMLs on every run, so every full `make context` re-ran those merges
# and silently destroyed legit parallel edges added since (3 BDNS tranches:
# Air Europa 240M / Plus Ultra 19M / Thinking Heads 160k). Instead:
#   * a duplicate with NO relationships (the re-created-YAML-stub case) is just
#     absorbed as an alias and deleted — no edge surgery at all;
#   * a real duplicate has each edge re-pointed 1:1 (apoc.refactor.from/to,
#     full props preserved), then only TRUE duplicates are pruned: parallel
#     same-type edges with identical properties, plus redundant parallel
#     MENTIONED_IN to the same chunk. Distinct evidence stays as distinct edges.

# Harvest duplicate names/aliases onto the canonical and classify each duplicate
# by degree (0 = alias-only absorption; >0 = needs edge re-pointing).
_MERGE_CLASSIFY_Q = """
MATCH (keep {entity_id:$keep})
MATCH (d) WHERE d.entity_id IN $drops AND d.entity_id <> $keep
WITH keep, collect(d) AS drops
WHERE size(drops) > 0
WITH keep, drops, apoc.coll.toSet(
       [x IN drops | x.name] +
       apoc.coll.flatten([x IN drops | coalesce(x.aliases, [])])) AS extra
SET keep.aliases = [a IN apoc.coll.toSet(coalesce(keep.aliases, []) + extra)
                    WHERE a IS NOT NULL AND a <> keep.name]
WITH drops
UNWIND drops AS d
RETURN d.entity_id AS id, COUNT { (d)--() } AS deg
"""

# Edges between the duplicate and the canonical describe the pair we are about
# to unify — they would become self-loops. Drop them before re-pointing.
_MERGE_SELFREL_Q = """
MATCH (keep {entity_id:$keep})-[r]-(d {entity_id:$drop})
DELETE r
"""

_MERGE_REPOINT_OUT_Q = """
MATCH (keep {entity_id:$keep})
MATCH (d {entity_id:$drop})-[r]->(x)
CALL apoc.refactor.from(r, keep) YIELD output
RETURN count(output) AS n
"""

_MERGE_REPOINT_IN_Q = """
MATCH (keep {entity_id:$keep})
MATCH (x)-[r]->(d {entity_id:$drop})
CALL apoc.refactor.to(r, keep) YIELD output
RETURN count(output) AS n
"""

# Prune parallel same-type same-direction edges of the canonical whose property
# maps are IDENTICAL (indistinguishable rows — safe to drop). Distinct evidence
# (different quote/doc/amount/bdns_key…) is kept as separate edges.
_MERGE_DEDUP_IDENTICAL_Q = """
MATCH (keep {entity_id:$keep})-[r]-(x)
WHERE x <> keep
WITH keep, x, type(r) AS t, startNode(r) = keep AS outgoing,
     properties(r) AS p, collect(r) AS rs
WHERE size(rs) > 1
UNWIND rs[1..] AS dup
DELETE dup
RETURN count(dup) AS n
"""

# MENTIONED_IN is per (entity, chunk): after re-pointing, two parallel edges to
# the same chunk are redundant whatever their props say — keep one.
_MERGE_DEDUP_MENTIONS_Q = """
MATCH (keep {entity_id:$keep})-[r:MENTIONED_IN]->(c)
WITH keep, c, collect(r) AS rs
WHERE size(rs) > 1
UNWIND rs[1..] AS dup
DELETE dup
RETURN count(dup) AS n
"""

_MERGE_DELETE_DROP_Q = """
MATCH (d) WHERE d.entity_id IN $ids
DETACH DELETE d
"""


def _split_summaries(summaries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separa la lista de summaries en (escrituras, retiradas).

    `remove: true` (spec 061) retira la prosa curada de un nodo — el destino de un
    resumen que la auditoría tumba (seudónimo judicial, meta-texto editorial, frase
    sin fuente). Declarar a la vez `remove` y un `resumen` es contradictorio y
    aborta la carga: el fichero tiene que decir sin ambigüedad qué se publica."""
    rows: list[dict] = []
    removes: list[dict] = []
    for s in summaries:
        # Spec 070 (AC187/AC192): a row without `entity_id`, without `resumen` nor removal, or a removal with one —
        # `load_files` stops it first; here, the tree's reason (`seudonimos.forma_contexto`), never the entity.
        motivo = seudonimos.forma_contexto("summaries", s)
        if motivo:
            raise SystemExit(f"context: {motivo}")
        eid = s["entity_id"].strip()
        resumen = (s.get("resumen") or "").strip()
        if s.get("remove"):
            removes.append({"entity_id": eid})
            continue
        rows.append({"entity_id": eid, "resumen": resumen,
                     "sources": [u for u in (s.get("sources") or []) if u],
                     "updated": (s.get("updated") or "").strip()})
    return rows, removes


def load_summaries(driver, summaries: list[dict]) -> dict:
    """Attach curated summaries (web-sourced, presumption-of-innocence) onto the
    EXISTING graph node: n.summary, n.summary_sources, n.summary_updated. Keyed by
    entity_id; never creates a node (merged/deleted entities are simply skipped).
    The explorer's Síntesis tab reads these. Idempotent.

    Las retiradas (`remove: true`) se aplican DESPUÉS de las escrituras: si un
    fichero anterior del overlay publica prosa que la 061 retira, gana la retirada."""
    rows, removes = _split_summaries(summaries)
    q = """
    UNWIND $rows AS row
    MATCH (n {entity_id: row.entity_id})
    SET n.summary = row.resumen,
        n.summary_sources = row.sources,
        n.summary_updated = row.updated
    RETURN count(n) AS c
    """
    remove_q = """
    UNWIND $rows AS row
    MATCH (n {entity_id: row.entity_id})
    REMOVE n.summary, n.summary_sources, n.summary_updated
    RETURN count(n) AS c
    """
    removed = 0
    with driver.session() as s:
        c = s.run(q, rows=rows).single()["c"]
        if removes:
            removed = s.run(remove_q, rows=removes).single()["c"]
    log.info("context_summaries", requested=len(rows), applied=c,
             remove_requested=len(removes), removed=removed)
    return {"applied": c, "removed": removed}


def load_pseudonyms(driver, rows: list[dict]) -> int:
    """Flag court-anonymisation pseudonyms (e.g. 'Paulino', 'Alejo' — fake first
    names the Tribunal Supremo autos use to anonymise an investigated person).
    These are REAL, distinct people, so they are NOT merged; we set
    n.is_pseudonym=true + n.pseudonym_note so the UI/MCP can mark them and they
    aren't read as mystery hubs. Keyed by entity_id; never creates a node. Idempotent.
    Spec 070 (AC83): the node's `name` and `canonical_name` become the roster's sample form
    (`seudonimos.forma_de_muestra`: the row's `name`, else the capitalised first name), never
    a name from the census or the graph."""
    clean = [{"entity_id": (p.get("id") or "").strip(),
              "note": (p.get("note") or "").strip(),
              "name": seudonimos.forma_de_muestra(p)}
             for p in rows if (p.get("id") or "").strip()]
    # A pseudonym whose id/name gave a hint (e.g. a company) is re-keyed with `rename_from` +
    # `name`: the old node takes the new id/name, unless the new id already exists. Idempotent.
    renames = [{"old": p["rename_from"].strip(), "new": (p.get("id") or "").strip(),
                "name": (p.get("name") or "").strip() or None}
               for p in rows if (p.get("rename_from") or "").strip() and (p.get("id") or "").strip()]
    rename_q = """
    UNWIND $rows AS row
    MATCH (n {entity_id: row.old})
    WHERE NOT EXISTS { MATCH (m {entity_id: row.new}) }
    SET n.entity_id = row.new, n.name = coalesce(row.name, n.name),
        n.aliases = [a IN coalesce(n.aliases, []) WHERE NOT toLower(a) CONTAINS toLower(split(row.old, '-')[-1])]
    RETURN count(n) AS c
    """
    if renames:
        with driver.session() as s:
            r = s.run(rename_q, rows=renames).single()["c"]
        log.info("context_pseudonym_renames", requested=len(renames), applied=r)
    q = """
    UNWIND $rows AS row
    MATCH (n {entity_id: row.entity_id})
    SET n.is_pseudonym = true, n.pseudonym_note = row.note, n.name = row.name, n.canonical_name = row.name
    RETURN count(n) AS c
    """
    with driver.session() as s:
        c = s.run(q, rows=clean).single()["c"]
    log.info("context_pseudonyms", requested=len(clean), applied=c)
    return c


def _norm_tokens(text: str) -> set[str]:
    """Tokens significativos de un nombre/alias: sin honoríficos ni artículos, sin acentos,
    sin paréntesis (p. ej. «Pedro (seudónimo TS)» → {pedro})."""
    import re as _re
    import unicodedata as _ud
    hon = {"sr", "sra", "srta", "sres", "d", "dna", "da", "don", "dona", "el", "la", "los", "las",
           "excmo", "excma", "ilmo", "ilma", "aforado", "investigado", "investigada", "tambien",
           "hermano", "hermana", "de", "del", "y"}
    t = _re.sub(r"\([^)]*\)", " ", text or "")
    t = "".join(c for c in _ud.normalize("NFD", t) if _ud.category(c) != "Mn").lower()
    return {w for w in _re.split(r"[^a-z0-9]+", t) if w and w not in hon}


def pseudonym_guard(driver, rows: list[dict], merges: list[dict]) -> list[str]:
    """Spec 059 (anti-de-anonimización) — hard gate, runs after load_pseudonyms.
    A node flagged `is_pseudonym` is a REAL person the court anonymised: it must
    never acquire a civil identity by the back door. Fails (returns problems) if:
      · its name carries a token absent from its entity_id slug (a civil surname
        replaced or extended the pseudonym: «David» → «David Sánchez»);
      · any alias carries a token absent from slug ∪ name («Tecol Basilio Luis
        Sánchez Portillo» on Person:basilio) — honorifics/articles ignored;
      · it appears as canonical or duplicate in merges.yaml (a merge would fold it
        into / absorb a named person);
      · a pseudonyms.yaml id no longer exists (the node was merged away).
    The rule lives in decisions 2026-06-29 / 2026-07-29 and pseudonyms.yaml; this
    makes it executable so a Path A cycle cannot ship a de-anonymised node."""
    ids = [(p.get("id") or "").strip() for p in rows if (p.get("id") or "").strip()]
    q = """
    UNWIND $ids AS id
    OPTIONAL MATCH (n {entity_id: id})
    RETURN id, n IS NOT NULL AS present, n.name AS name, coalesce(n.aliases, []) AS aliases
    """
    problems: list[str] = []
    with driver.session() as s:
        for r in s.run(q, ids=ids):
            if not r["present"]:
                problems.append(f"{r['id']}: ausente (¿fusionado?)")
                continue
            slug_tokens = set(r["id"].split(":", 1)[1].split("-"))
            name_tokens = _norm_tokens(r["name"] or "")
            if name_tokens - slug_tokens:
                problems.append(f"{r['id']}: nombre civil «{r['name']}»")
            for a in r["aliases"]:
                if _norm_tokens(a) - slug_tokens - name_tokens:
                    problems.append(f"{r['id']}: alias civil «{a}»")
    # Static check: no pseudonym may be a merge canonical or duplicate.
    from .entity_resolution import entity_id as _eid
    for g in merges or []:
        can = g.get("canonical") or {}
        names = [(can.get("name"), can.get("type"))] if isinstance(can, dict) else [(can, None)]
        for d in g.get("duplicates") or []:
            names.append((d.get("name"), d.get("type")) if isinstance(d, dict) else (d, None))
        for name, etype in names:
            if not name:
                continue
            cand = _eid(etype or "Person", name)
            if cand in ids:
                problems.append(f"{cand}: aparece en merges.yaml («{name}»)")
    if problems:
        log.error("pseudonym_guard_failed", count=len(problems), problems=problems)
    else:
        log.info("pseudonym_guard_ok", checked=len(ids))
    return problems


def report_isolated(driver) -> list[str]:
    """Post-run guard: list entity nodes left with no relationships at all.

    In a healthy graph this is empty (mention-only nodes keep MENTIONED_IN, and
    e.g. manos-limpias keeps ASSERTS). A non-empty result usually means an
    `entities:` YAML entry re-created a node that a graph sweep had deleted
    (gotcha 2026-07-09: los barridos #201/#202 vs `make context`) — prune the
    YAML entry instead of leaving the orphan."""
    q = ("MATCH (n) WHERE n.entity_id IS NOT NULL AND NOT (n)--() "
         "RETURN n.entity_id AS id ORDER BY id")
    with driver.session() as s:
        return [r["id"] for r in s.run(q)]


def load_merges(driver, groups: list[dict]) -> int:
    merged = 0
    with driver.session() as s:
        for g in groups:
            can = g.get("canonical") or {}
            ctype, cname = can.get("type"), (can.get("name") or "").strip()
            if ctype not in _ENTITY_TYPES or not cname:   # spec 070 (AC192): `load_files` stops it first
                log.warning("merge_skip_canonical", note=seudonimos.forma_contexto("merges", g))
                continue
            keep_id = entity_id(ctype, cname)
            drops: list[str] = []
            for dup in (g.get("duplicates") or []):
                dname = (dup if isinstance(dup, str) else dup.get("name") or "").strip()
                dtype = ctype if isinstance(dup, str) else (dup.get("type") or ctype)
                if dname:
                    drops.append(entity_id(dtype, dname))
            if not drops:
                continue
            found = list(s.run(_MERGE_CLASSIFY_Q, keep=keep_id, drops=drops))
            if not found:
                log.info("context_merge", canonical=cname, requested=len(drops), merged=0)
                continue
            empty = [r["id"] for r in found if r["deg"] == 0]
            real = [r["id"] for r in found if r["deg"] > 0]
            deduped = 0
            for drop_id in real:
                s.run(_MERGE_SELFREL_Q, keep=keep_id, drop=drop_id)
                s.run(_MERGE_REPOINT_OUT_Q, keep=keep_id, drop=drop_id)
                s.run(_MERGE_REPOINT_IN_Q, keep=keep_id, drop=drop_id)
            if real:
                deduped += s.run(_MERGE_DEDUP_IDENTICAL_Q, keep=keep_id).single()["n"]
                deduped += s.run(_MERGE_DEDUP_MENTIONS_Q, keep=keep_id).single()["n"]
            s.run(_MERGE_DELETE_DROP_Q, ids=empty + real)
            merged += len(found)
            log.info("context_merge", canonical=cname, requested=len(drops),
                     merged=len(found), alias_only=len(empty), repointed=len(real),
                     deduped=deduped)
    return merged


def main():
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default=str(CONTEXT_DIR))
    args = p.parse_args()

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("context_dir_missing", dir=str(directory))
        return
    try:
        entities, relations, merges, summaries, pseudonyms = load_files(directory)
    except seudonimos.ErrorDeForma as err:   # AC161: by file, section and row, never a value
        print(f"context: {err}", file=sys.stderr)
        raise
    nodes, edges = _prepare(entities, relations)
    if not nodes and not merges and not summaries and not pseudonyms:
        log.warning("context_empty", dir=str(directory))
        return

    driver = neo4j_loader.get_driver()
    try:
        if nodes:
            load(driver, nodes, edges)
        if merges:
            load_merges(driver, merges)  # fold duplicate nodes into their canonical
        if summaries:
            load_summaries(driver, summaries)  # attach curated summaries to nodes
        if pseudonyms:
            load_pseudonyms(driver, pseudonyms)  # flag judicial-anonymisation pseudonyms
        neo4j_loader.compute_degree(driver)  # context edges now count toward degree
        isolated = report_isolated(driver)
        if isolated:
            log.warning("context_isolated_nodes", count=len(isolated), ids=isolated)
        problems = pseudonym_guard(driver, pseudonyms, merges) if pseudonyms else []
    finally:
        driver.close()
    if problems:
        raise SystemExit(f"pseudonym_guard: {len(problems)} problema(s) — ver log (spec 059)")
    log.info("context_done")


if __name__ == "__main__":
    main()
