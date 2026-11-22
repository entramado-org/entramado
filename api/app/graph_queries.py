"""Cypher queries returning JSON-ready {nodes, links, meta} for the graph UI."""
from __future__ import annotations

import re

from neo4j import Query

from . import config, ontologia
from .deps import get_driver

_EXCLUDE = config.PROVENANCE_RELS


def _served(v: str) -> str:
    """Cypher predicate: `v` may be served as an entity. A person left with no relation to any other entity (its relations were
    withdrawn: rectification, deny rules, pseudonym policy) is only a name next to the passages that mention it — there is nothing
    sourced to say about it — so no entity surface serves it: search, ficha, graph, rankings or the chunk filters. The documents
    stay readable as a whole. Other labels are served as before. A posture on (or being the subject of) a Claim counts as a relation:
    claims are sourced and name their holders."""
    return (f"(NOT {v}:Person OR EXISTS {{ ({v})-[_sr]-(_sm) WHERE (_sm.entity_id IS NOT NULL OR _sm:Claim) "
            f"AND NOT type(_sr) IN {_EXCLUDE} }})")


def existing_entity_ids(ids: list[str]) -> set[str]:
    """Which of `ids` exist in the graph, served or not (integrity gates must not confuse «hidden» with «missing»)."""
    with get_driver().session() as s:
        return {r["id"] for r in s.run("MATCH (n) WHERE n.entity_id IN $ids RETURN n.entity_id AS id", ids=ids)}


def served_entity_ids(ids: list[str]) -> set[str]:
    """Which of `ids` exist and are served."""
    cypher = "MATCH (n) WHERE n.entity_id IN $ids AND " + _served("n") + " RETURN n.entity_id AS id"
    with get_driver().session() as s:
        return {r["id"] for r in s.run(cypher, ids=ids)}


def entities_with_public_note(ids: list[str]) -> set[str]:
    """Which of `ids` may have their vault note served: the entity exists today, is served, and is neither a judicial pseudonym nor
    an anonymised relative (their notes quote the chunks that the ficha hides). The vault is an older export than the graph, so a
    note can outlive the withdrawal of its entity."""
    cypher = ("MATCH (n) WHERE n.entity_id IN $ids AND " + _served("n") + " AND NOT coalesce(n.is_pseudonym, false) "
              "AND NOT n.entity_id STARTS WITH $anon RETURN n.entity_id AS id")
    with get_driver().session() as s:
        return {r["id"] for r in s.run(cypher, ids=ids, anon=_ANON_RELATIVE_PREFIX)}


def unserved_person_names() -> list[str]:
    """Names and aliases of the persons that exist and are not served (they lost their last relation). The curated notes of
    the vault must not name them either, whether or not the vault carries a note of theirs. Judicial pseudonyms are left out:
    a fictitious first name identifies nobody and would scrub every homonym."""
    cypher = ("MATCH (n:Person) WHERE n.entity_id IS NOT NULL AND NOT " + _served("n") + " AND NOT coalesce(n.is_pseudonym, false) "
              "RETURN n.name AS name, coalesce(n.aliases, []) AS aliases")
    out: list[str] = []
    with get_driver().session() as s:
        for r in s.run(cypher):
            out.extend(x for x in [r["name"], *r["aliases"]] if isinstance(x, str) and x.strip())
    return out


# Map projections reused across queries (entity nodes / entity-entity rels).
# `summary` is truncated to a ~160-char teaser here so the overview payload stays
# light (it can return up to `limit` nodes); the full curated text is served by
# /node/{id} (Síntesis tab). KeyFigures on the landing uses this teaser directly.
_NODE_PROJ = "{id:x.entity_id, type:labels(x)[0], name:x.name, role:x.role, degree:coalesce(x.degree,0), mentions:coalesce(x.mentions,0), context:coalesce(x.is_context,false), press:coalesce(x.is_press,false), betweenness:coalesce(x.betweenness,0.0), closeness:coalesce(x.closeness,0.0), community:x.community, community_label:x.community_label, isPseudonym:coalesce(x.is_pseudonym,false), summary:CASE WHEN x.summary IS NULL THEN null ELSE left(x.summary,160) END}"


# Anonymised relatives (familiares_revisados.yaml, decision «anonimizar»): the chunks that mention them
# name them in clear (police reports carry name, kinship and DNI), so serving those chunks on the node's
# own ficha would undo the anonymisation. The documents stay readable/searchable as a whole.
_ANON_RELATIVE_PREFIX = "Person:familiar-de-"


def is_anonymised_relative(node_id: str) -> bool:
    return node_id.startswith(_ANON_RELATIVE_PREFIX)


def _unless_relative(expr: str, *nodes: str) -> str:
    """Cypher for `expr`, or null when any of `nodes` is an anonymised relative. The literal quote (and the free-text note) of a
    relation comes from the same documents as the chunks: it can name the relative in clear («su pareja, la señora …»), so no
    surface serves it for a relation that touches one. Document, page and the rest of the provenance stay."""
    cond = " OR ".join(f"{n}.entity_id STARTS WITH '{_ANON_RELATIVE_PREFIX}'" for n in nodes)
    return f"CASE WHEN {cond} THEN null ELSE {expr} END"


# Tail appended to a query that has produced `ns` (the node-set in scope). It
# aggregates parallel evidence edges into one display link per (source,target,
# type) with a count + representative quote. Optional $from_year/$to_year filter
# narrows the aggregated count to evidence within that window; when both are
# null the behaviour is identical to the unfiltered case. Links that end up with
# zero matching evidence (in chronology mode) are dropped, and nodes are kept
# even when they have no surviving links so the canvas isn't suddenly empty.
# `carry` threads extra variables (e.g. the ordered path sequences in
# paths_between) through the aggregation pipeline untouched.
# The representative is the first parallel edge in natural-key order (everything the link serves, never an internal id), so a rebuild of
# the same graph serves the same quote, document, status and amount; and the links come out ordered by (source, target, type), so the
# first link of a pair (what the MCP path tool shows) is the same too.
def _link_tail(carry: str = "") -> str:
    keep = f", {carry}" if carry else ""
    return f"""
UNWIND ns AS a
OPTIONAL MATCH (a)-[r]->(b)
  WHERE b IN ns AND NOT type(r) IN {_EXCLUDE}
WITH ns{keep}, a, b, r
  ORDER BY r.source_doc, r.source_page, r.source_quote, r.amount, r.currency, r.date_norm, r.date, r.date_year, r.source_kind, r.note,
           r.status, r.status_fuente_tipo, r.status_fecha, r.status_fuente, r.outlet, r.url
WITH ns{keep}, a, b, type(r) AS rt,
     [x IN collect(r) WHERE ($from_year IS NULL OR (x.date_year IS NOT NULL AND x.date_year >= $from_year))
                        AND ($to_year   IS NULL OR (x.date_year IS NOT NULL AND x.date_year <= $to_year))] AS rs
WITH ns{keep}, a, b, rt, rs ORDER BY a.entity_id, b.entity_id, rt
WITH ns{keep}, collect(CASE WHEN b IS NOT NULL AND size(rs) > 0 THEN {{
     source:a.entity_id, target:b.entity_id, type:rt, count:size(rs),
     quote:{_unless_relative('rs[0].source_quote', 'a', 'b')}, doc:rs[0].source_doc, page:rs[0].source_page,
     amount:rs[0].amount, currency:rs[0].currency,
     date:coalesce(rs[0].date_norm, rs[0].date), date_year:rs[0].date_year,
     context:coalesce(rs[0].source_kind,'') = 'contexto', note:{_unless_relative('rs[0].note', 'a', 'b')},
     source_kind:coalesce(rs[0].source_kind,'sin_procedencia'), status:rs[0].status,
     status_fuente_tipo:rs[0].status_fuente_tipo, status_fecha:rs[0].status_fecha,
     status_fuente:rs[0].status_fuente,
     outlet:rs[0].outlet, url:{_unless_relative('rs[0].url', 'a', 'b')}}} END) AS rawlinks
RETURN [x IN ns | {_NODE_PROJ}] AS nodes,
       [l IN rawlinks WHERE l IS NOT NULL] AS links{keep}
"""


_LINK_TAIL = _link_tail()


def _lucene(q: str, require_all: bool = True, phrase_boost: bool = False) -> str:
    """Build a fuzzy Lucene query: each alnum term gets a ~1 edit fuzz.

    With `require_all` (default) every term is prefixed `+` so it is REQUIRED —
    "Plus Ultra" must match BOTH "Plus" and "Ultra", not either, which kills the
    OR-noise (e.g. an unrelated chunk that only fuzzy-hits one term). Set
    require_all=False for recall-first OR behaviour. With `phrase_boost`, a
    multi-term query also appends an OPTIONAL exact-phrase clause `"t1 t2"^4`,
    so a chunk where the words appear together (e.g. the literal "Plus Ultra")
    scores above one that merely hits both terms scattered far apart (e.g. a
    "Begoña Gómez" subsidy chunk that happens to fuzzy-match). Returns "" when the
    input has no indexable terms — never pass the raw string through to the
    fulltext index (Lucene operators would be parsed).

    Each term matches fuzzily (~1 edit) OR by prefix (`term*`), so a partial word
    like "cripto" reaches "criptomonedas" (fuzzy alone, edit-distance 1, can't).
    The prefix clause is lowercased because Lucene does NOT analyse wildcard terms
    while the index is lowercased at write time; it is only added for terms of 3+
    chars (a prefix on a 1-2 char stem matches almost everything).
    """
    terms = re.findall(r"[\wáéíóúñüÁÉÍÓÚÑÜ]+", q)
    if not terms:
        return ""

    def _clause(t: str) -> str:
        # fuzzy (typo-tolerant) OR prefix (partial-word). Prefix lowercased + 3+ chars.
        return f"({t}~1 OR {t.lower()}*)" if len(t) >= 3 else f"{t}~1"

    # A single term needs no operator; multi-term gets required-AND when asked.
    if len(terms) > 1:
        clauses = [f"+{_clause(t)}" if require_all else _clause(t) for t in terms]
        if phrase_boost:
            clauses.append('"' + " ".join(terms) + '"^4')
        return " ".join(clauses)
    return _clause(terms[0])


def search_entities(q: str, limit: int = 25, types: list[str] | None = None) -> list[dict]:
    lucene = _lucene(q)
    if not lucene:
        return []
    cypher = """
    CALL db.index.fulltext.queryNodes('entityNames', $q) YIELD node, score
    WHERE node.entity_id IS NOT NULL
      AND ($types IS NULL OR labels(node)[0] IN $types)
      AND """ + _served("node") + """
    RETURN node.entity_id AS id, labels(node)[0] AS type, node.name AS name,
           coalesce(node.degree,0) AS degree, score,
           coalesce(node.is_pseudonym,false) AS isPseudonym
    ORDER BY score DESC, degree DESC, id
    LIMIT $limit
    """
    with get_driver().session() as s:
        rows = s.run(cypher, q=lucene, limit=limit, types=types).data()
    return rows


def overview(
    types: list[str] | None = None,
    limit: int = 300,
    min_degree: int = 1,
    from_year: int | None = None,
    to_year: int | None = None,
) -> dict:
    # Contract nodes are few but structurally central (the procurement triangle
    # body→contract→awardee); their degree (~2) sits far below the top-by-degree
    # cap, so include them unconditionally (when not type-filtered out) — otherwise
    # they'd never appear in the overview / "Personas" layout.
    cypher = f"""
    MATCH (n) WHERE n.entity_id IS NOT NULL
      AND ($types IS NULL OR labels(n)[0] IN $types)
      AND coalesce(n.degree,0) >= $min_degree
      AND {_served('n')}
    WITH n ORDER BY coalesce(n.degree,0) DESC, n.entity_id LIMIT $limit
    WITH collect(n) AS top
    OPTIONAL MATCH (k:Contract) WHERE ($types IS NULL OR 'Contract' IN $types)
    WITH top, collect(k) AS contracts
    WITH apoc.coll.toSet(top + contracts) AS ns
    {_LINK_TAIL}
    """
    with get_driver().session() as s:
        rec = s.run(
            cypher,
            types=types,
            limit=limit,
            min_degree=min_degree,
            from_year=from_year,
            to_year=to_year,
        ).single()
    if not rec:
        return {"nodes": [], "links": [], "meta": {"truncated": False}}
    return {"nodes": rec["nodes"], "links": rec["links"], "meta": {"truncated": False}}


def neighborhood(
    node_id: str,
    hops: int = 1,
    limit: int = 150,
    types: list[str] | None = None,
    min_degree: int = 1,
    from_year: int | None = None,
    to_year: int | None = None,
) -> dict:
    hops = max(1, min(hops, 3))  # validated literal injected into var-length
    cypher = f"""
    MATCH (c {{entity_id:$id}}) WHERE {_served('c')}
    CALL {{
      WITH c
      MATCH (c)-[rels*1..{hops}]-(m)
      WHERE m.entity_id IS NOT NULL
        AND all(x IN rels WHERE NOT type(x) IN {_EXCLUDE})
        AND ($types IS NULL OR labels(m)[0] IN $types)
        AND coalesce(m.degree,0) >= $min_degree
      RETURN DISTINCT m
      ORDER BY coalesce(m.degree,0) DESC, m.entity_id
      LIMIT $limit
    }}
    WITH c, collect(m) AS neighbors
    WITH neighbors + c AS ns
    {_LINK_TAIL}
    """
    with get_driver().session() as s:
        rec = s.run(
            cypher,
            id=node_id,
            limit=limit,
            types=types,
            min_degree=min_degree,
            from_year=from_year,
            to_year=to_year,
        ).single()
    if not rec:
        return {"nodes": [], "links": [], "meta": {"truncated": False}}
    truncated = len(rec["nodes"]) - 1 >= limit
    return {"nodes": rec["nodes"], "links": rec["links"], "meta": {"truncated": truncated}}


# Relationship-type allowlist for path finding. allShortestPaths plans against
# the PATTERN, so excluding MENTIONED_IN/PART_OF in a WHERE post-filter would
# make the planner pick the trivial 2-hop Chunk-mediated paths between any two
# co-mentioned entities and then discard them — returning "no path" almost
# always. The exclusion must live in the pattern itself, as a `:A|B|...`
# alternation of the real relation types. Names are regex-validated before
# being interpolated into Cypher. Cached per process: curated reloads restart
# the API container, so staleness isn't a concern in practice.
_REL_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_rel_types_cache: list[str] | None = None


def _allowed_rel_types() -> list[str]:
    global _rel_types_cache
    if _rel_types_cache is None:
        with get_driver().session() as s:
            rows = s.run(
                "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
            ).value()
        _rel_types_cache = [t for t in rows if t not in _EXCLUDE and _REL_NAME.match(t)]
    return _rel_types_cache


def paths_between(
    from_id: str,
    to_id: str,
    max_len: int = 4,
    max_paths: int = 5,
) -> dict:
    """Shortest evidence chains between two entities.

    Returns the standard {nodes, links} union subgraph of all found paths
    (mergeable client-side like any other payload) plus `paths`: the ordered
    entity_id sequences, so the UI can highlight/list each chain. Paths are
    NOT year-filtered ($from_year/$to_year = null): the chronology filter
    could drop a hop's display link and visually break an otherwise valid
    chain. Unknown ids or no path within max_len → found:false, not an error.
    """
    max_len = max(1, min(max_len, 6))  # validated literal injected into var-length
    rels = _allowed_rel_types()
    empty = {"nodes": [], "links": [], "paths": [], "meta": {"found": False, "maxLen": max_len}}
    if not rels:
        return empty
    rel_pattern = "|".join(rels)  # names regex-validated in _allowed_rel_types
    cypher = f"""
    MATCH (a {{entity_id:$from_id}}), (b {{entity_id:$to_id}})
    MATCH p = allShortestPaths((a)-[:{rel_pattern}*..{max_len}]-(b))
    WHERE all(n IN nodes(p) WHERE n.entity_id IS NOT NULL)
    // Parallel relations between the same node pair yield one path per rel —
    // dedupe to DISTINCT node sequences before applying the cap (the link
    // aggregation below surfaces every parallel relation anyway).
    WITH [n IN nodes(p) | n.entity_id] AS seq, collect(nodes(p))[0] AS pnodes
    WITH seq, pnodes LIMIT $max_paths
    WITH collect(seq) AS pathSeqs,
         apoc.coll.toSet(apoc.coll.flatten(collect(pnodes))) AS ns
    {_link_tail(carry="pathSeqs")}
    """
    with get_driver().session() as s:
        rec = s.run(
            cypher,
            from_id=from_id,
            to_id=to_id,
            max_paths=max_paths,
            from_year=None,
            to_year=None,
        ).single()
    if not rec or not rec["pathSeqs"]:
        return empty
    return {
        "nodes": rec["nodes"],
        "links": rec["links"],
        "paths": rec["pathSeqs"],
        "meta": {"found": True, "pathCount": len(rec["pathSeqs"]), "maxLen": max_len},
    }


def estado_tag(status: str | None, fuente_tipo: str | None = None, fecha: str | None = None,
               fuente: str | None = None) -> str:
    """` [estado: X]`, y cuando ese estado solo lo sostiene un medio (`status_fuente_tipo == 'prensa'`), con su
    procedencia: ` [estado: acusado, según prensa (2026-09-21): <url>]`. Spec 063 (revisión Codex 29-09): el estado
    se propaga a aristas judiciales anteriores, y sin esto se leía como respaldado por su cita [doc, p.N]."""
    if not status:
        return ""
    press = ""
    if fuente_tipo == "prensa":
        press = ", según prensa" + (f" ({fecha})" if fecha else "") + (f": {fuente}" if fuente else "")
    elif fuente:
        # 063 (30-09): el estado lo fija un acto del corpus que puede no ser el documento de la cita de la arista.
        acto = (fuente_tipo or "resolución").replace("_", " ")
        press = f", fijado por {acto}" + (f" de {fecha}" if fecha else "") + f" [{fuente}]"
    return f" [estado: {status}{press}]"


def relation_detail(
    from_id: str,
    to_id: str,
    rel_type: str,
    from_year: int | None = None,
    to_year: int | None = None,
    kinds: list[str] | None = None,
) -> dict | None:
    """All parallel evidence relations of one type between two entities.

    The canvas shows one aggregated display link per directed (source, target,
    type) with a ×count (see _link_tail); this de-aggregates it for the edge
    detail panel. The same $from_year/$to_year window keeps the panel in
    agreement with the ×count drawn on the edge; `total` is the unfiltered
    count so the UI can disclose "N de M en el rango". `kinds` (provenance)
    further narrows to those `source_kind`s (null/'judicial' default). A window
    that filters out ALL evidence still returns the relation (evidence=[]) —
    only a relation that doesn't exist at all returns None (→ 404). Each item
    carries `file` (doc_id) + `page` so the caller can chain to read_document.
    """
    if not _REL_NAME.match(rel_type) or rel_type in _EXCLUDE:
        return None
    cypher = """
    MATCH (a {entity_id:$from_id})-[r]->(b {entity_id:$to_id})
    WHERE type(r) = $rel_type
    WITH a, b, collect(r) AS allr
    WITH a, b, size(allr) AS total,
         [x IN allr WHERE ($from_year IS NULL OR (x.date_year IS NOT NULL AND x.date_year >= $from_year))
                      AND ($to_year   IS NULL OR (x.date_year IS NOT NULL AND x.date_year <= $to_year))
                      AND ($kinds IS NULL OR coalesce(x.source_kind,'sin_procedencia') IN $kinds)] AS rs
    // [null] keeps the row alive when the filters empty the evidence,
    // so the panel can say "0 de M en el rango" instead of a 404.
    UNWIND (CASE WHEN size(rs) = 0 THEN [null] ELSE rs END) AS r
    OPTIONAL MATCH (d:Document {doc_id: r.source_doc})
    WITH a, b, total, r, d
    ORDER BY r.date_norm IS NULL, r.date_norm DESC, type(r), r.source_doc, r.source_page, r.source_quote
    RETURN {id:a.entity_id, type:labels(a)[0], name:a.name, isPseudonym:coalesce(a.is_pseudonym,false)} AS source,
           {id:b.entity_id, type:labels(b)[0], name:b.name, isPseudonym:coalesce(b.is_pseudonym,false)} AS target,
           total,
           [e IN collect(CASE WHEN r IS NULL THEN null ELSE {
              rel_type: type(r),
              quote: """ + _unless_relative("r.source_quote", "a", "b") + """, file: r.source_doc,
              doc: coalesce(d.title, r.source_doc), page: r.source_page,
              date: coalesce(r.date_norm, r.date), date_year: r.date_year,
              amount: r.amount, currency: r.currency, status: r.status,
              status_fuente_tipo: r.status_fuente_tipo, status_fecha: r.status_fecha,
              status_fuente: r.status_fuente,
              source_kind: coalesce(r.source_kind,'sin_procedencia'),
              outlet: r.outlet, url: """ + _unless_relative("r.url", "a", "b") + """, note: """ + _unless_relative("r.note", "a", "b") + """,
              fecha: r.fecha, importe: r.importe, instrumento: r.instrumento,
              convocante_detalle: r.convocante_detalle,
              n_contratos: r.n_contratos, total_eur: r.total_eur, rango: r.rango,
              organo_detalle: r.organo_detalle, via_ute: r.via_ute,
              ambito: r.ambito, fuente: r.fuente, n_emergencia: r.n_emergencia,
              n_negociado_sp: r.n_negociado_sp,
              presupuesto_total_eur: r.presupuesto_total_eur,
              ejemplo_link: r.ejemplo_link, ejemplo_titulo: r.ejemplo_titulo,
              cargo: r.cargo, fecha_nombramiento: r.fecha_nombramiento,
              boe_id_nombramiento: r.boe_id_nombramiento, url_nombramiento: r.url_nombramiento,
              fecha_cese: r.fecha_cese, boe_id_cese: r.boe_id_cese, url_cese: r.url_cese
           } END) WHERE e IS NOT NULL] AS evidence
    """
    with get_driver().session() as s:
        rec = s.run(
            cypher,
            from_id=from_id,
            to_id=to_id,
            rel_type=rel_type,
            from_year=from_year,
            to_year=to_year,
            kinds=kinds,
        ).single()
    if not rec:
        return None
    return {
        "type": rel_type,
        "source": rec["source"],
        "target": rec["target"],
        "evidence": rec["evidence"],
        "meta": {"count": len(rec["evidence"]), "total": rec["total"]},
    }


def relation_pair(
    a_id: str,
    b_id: str,
    from_year: int | None = None,
    to_year: int | None = None,
    kinds: list[str] | None = None,
) -> dict | None:
    """Every relation (all types, BOTH directions) between two entities, grouped
    by (direction, type). Powers the web edge panel when the canvas collapses the
    parallel edges between a pair into a single line. Mirrors `relation_detail`'s
    evidence projection so the panel renders identically; here we return one group
    per (start, type, end) that exists at all (total>0), with `evidence` possibly
    emptied by the year/kind window (so the panel can disclose "0 de M en el
    rango"). Returns None (→404) only when the pair has no relation of any kind."""
    cypher = f"""
    MATCH (a {{entity_id:$a}})-[r]-(b {{entity_id:$b}})
    WHERE NOT type(r) IN {_EXCLUDE}
    WITH startNode(r) AS sn, endNode(r) AS en, type(r) AS rt, collect(r) AS allr
    WITH sn, en, rt, size(allr) AS total,
         [x IN allr WHERE ($from_year IS NULL OR (x.date_year IS NOT NULL AND x.date_year >= $from_year))
                      AND ($to_year   IS NULL OR (x.date_year IS NOT NULL AND x.date_year <= $to_year))
                      AND ($kinds IS NULL OR coalesce(x.source_kind,'sin_procedencia') IN $kinds)] AS rs
    UNWIND (CASE WHEN size(rs) = 0 THEN [null] ELSE rs END) AS r
    OPTIONAL MATCH (d:Document {{doc_id: r.source_doc}})
    WITH sn, en, rt, total, r, d
    ORDER BY r.date_norm IS NULL, r.date_norm DESC, r.source_doc, r.source_page, r.source_quote
    WITH sn, en, rt, total,
         [e IN collect(CASE WHEN r IS NULL THEN null ELSE {{
            rel_type: type(r),
            quote: {_unless_relative('r.source_quote', 'sn', 'en')}, file: r.source_doc,
            doc: coalesce(d.title, r.source_doc), page: r.source_page,
            date: coalesce(r.date_norm, r.date), date_year: r.date_year,
            amount: r.amount, currency: r.currency, status: r.status,
            source_kind: coalesce(r.source_kind,'sin_procedencia'),
            outlet: r.outlet, url: {_unless_relative('r.url', 'sn', 'en')}, note: {_unless_relative('r.note', 'sn', 'en')},
            fecha: r.fecha, importe: r.importe, instrumento: r.instrumento,
            convocante_detalle: r.convocante_detalle,
            n_contratos: r.n_contratos, total_eur: r.total_eur, rango: r.rango,
            organo_detalle: r.organo_detalle, via_ute: r.via_ute,
            ambito: r.ambito, fuente: r.fuente, n_emergencia: r.n_emergencia,
            n_negociado_sp: r.n_negociado_sp,
            presupuesto_total_eur: r.presupuesto_total_eur,
            ejemplo_link: r.ejemplo_link, ejemplo_titulo: r.ejemplo_titulo,
            cargo: r.cargo, fecha_nombramiento: r.fecha_nombramiento,
            boe_id_nombramiento: r.boe_id_nombramiento, url_nombramiento: r.url_nombramiento,
            fecha_cese: r.fecha_cese, boe_id_cese: r.boe_id_cese, url_cese: r.url_cese
         }} END) WHERE e IS NOT NULL] AS evidence
    RETURN {{fromId: sn.entity_id, fromType: labels(sn)[0], fromName: sn.name,
             toId: en.entity_id, toType: labels(en)[0], toName: en.name,
             type: rt, total: total, count: size(evidence), evidence: evidence}} AS grp
    ORDER BY total DESC, rt, sn.entity_id, en.entity_id
    """
    with get_driver().session() as s:
        rows = s.run(
            Query(cypher, timeout=15),
            a=a_id, b=b_id, from_year=from_year, to_year=to_year, kinds=kinds,
        ).data()
    groups = [r["grp"] for r in rows]
    if not groups:
        return None
    # Endpoints for the panel header: the two requested entities, named from
    # whichever group references them (direction varies across groups).
    names: dict[str, dict] = {}
    for g in groups:
        names.setdefault(g["fromId"], {"id": g["fromId"], "type": g["fromType"], "name": g["fromName"]})
        names.setdefault(g["toId"], {"id": g["toId"], "type": g["toType"], "name": g["toName"]})
    endpoints = [names.get(a_id) or {"id": a_id}, names.get(b_id) or {"id": b_id}]
    return {
        "endpoints": endpoints,
        "groups": groups,
        "meta": {
            "types": len(groups),
            "count": sum(g["count"] for g in groups),
            "total": sum(g["total"] for g in groups),
        },
    }


def _claim_rows(where: str, params: dict) -> list[dict]:
    """Shared body for entity_claims / claim_detail: every Claim matched by
    `where`, each with its for/against postures (cited + attributed). Claims are
    a reified overlay (spec 011) — the `Claim` node has no entity_id, so it lives
    only in this layer, never in the graph/overview."""
    cypher = f"""
    MATCH (cl:Claim) {where}
    WITH DISTINCT cl
    OPTIONAL MATCH (h)-[r:ASSERTS|DISPUTES]->(cl)
    OPTIONAL MATCH (d:Document {{doc_id: r.source_doc}})
    WITH cl, h, r, d
    ORDER BY type(r), r.date_norm, h.entity_id, r.source_doc, r.source_page, r.source_quote
    WITH cl, [x IN collect(CASE WHEN h IS NULL THEN null ELSE {{
        entityId: h.entity_id, entityName: h.name,
        stance: CASE type(r) WHEN 'ASSERTS' THEN 'asserts' ELSE 'disputes' END,
        source_kind: coalesce(r.source_kind,'sin_procedencia'),
        quote: {_unless_relative('r.source_quote', 'h')}, doc: coalesce(d.title, r.source_doc),
        file: r.source_doc, page: r.source_page,
        date: coalesce(r.date_norm, r.date), outlet: r.outlet, url: r.url
    }} END) WHERE x IS NOT NULL] AS postures
    RETURN cl.claim_id AS claimId, cl.text AS text, cl.topic AS topic, postures
    ORDER BY claimId
    """
    with get_driver().session() as s:
        rows = s.run(Query(cypher, timeout=15), **params).data()
    # asserts before disputes within each claim (the accusation, then the denial).
    for row in rows:
        row["postures"].sort(key=lambda p: 0 if p["stance"] == "asserts" else 1)
    return rows


def entity_claims(node_id: str) -> list[dict]:
    """Disputed claims this entity is party to — as a poster (ASSERTS/DISPUTES)
    or as the subject (ABOUT)."""
    where = ("WHERE EXISTS { (cl)<-[:ASSERTS|DISPUTES]-({entity_id:$id}) } "
             "OR EXISTS { (cl)-[:ABOUT]->({entity_id:$id}) }")
    return _claim_rows(where, {"id": node_id})


def claim_detail(claim_id: str) -> dict | None:
    rows = _claim_rows("WHERE cl.claim_id = $cid", {"cid": claim_id})
    return rows[0] if rows else None


# --- SEO fichas (/entidad, spec 035) ------------------------------------------
# "Reparto central": the only entities that get a public indexable ficha page.
# Thresholds calibrated against the live graph 2026-07-10 → ~300 entities
# (299 curated summaries + 1 hub without one). Raising SUMMARY_MIN_DEGREE
# trades long-tail coverage for page quality; keep the count in the 150-300
# band the spec targets.
FICHA_LABELS = ["Person", "Company", "Organization"]
FICHA_SUMMARY_MIN_DEGREE = 10
FICHA_HUB_MIN_DEGREE = 40

_FICHA_WHERE = """labels(n)[0] IN $ficha_labels
      AND NOT coalesce(n.is_pseudonym, false)
      AND """ + _served("n") + """
      AND ((n.summary IS NOT NULL AND coalesce(n.degree,0) >= $summary_min)
           OR coalesce(n.degree,0) >= $hub_min)"""

_FICHA_PARAMS = {
    "ficha_labels": FICHA_LABELS,
    "summary_min": FICHA_SUMMARY_MIN_DEGREE,
    "hub_min": FICHA_HUB_MIN_DEGREE,
}

# On slug collision across labels the person keeps the bare slug (people are
# what searchers type); companies/organizations get a Spanish suffix.
_SLUG_PRIORITY = {"Person": 0, "Organization": 1, "Company": 2}
_SLUG_SUFFIX = {"Organization": "-organizacion", "Company": "-empresa"}


def _assign_slugs(rows: list[dict]) -> list[dict]:
    """Set row['slug'] in place: entity_id minus the label prefix, with
    deterministic suffixes when the same base slug appears under two labels
    (0 collisions in today's set, but the rule must not depend on that)."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        base = r["id"].split(":", 1)[1] if ":" in r["id"] else r["id"]
        groups.setdefault(base, []).append(r)
    for base, group in groups.items():
        group.sort(key=lambda r: _SLUG_PRIORITY.get(r["type"], 9))
        group[0]["slug"] = base
        for r in group[1:]:
            r["slug"] = base + _SLUG_SUFFIX.get(r["type"], "-" + r["type"].lower())
    return rows


def indexable_entities() -> list[dict]:
    """The central cast, one row per public ficha — feeds the sitemap, the
    /entidad index, the landing Protagonistas section and slug resolution."""
    cypher = f"""
    MATCH (n) WHERE {_FICHA_WHERE}
    RETURN n.entity_id AS id, labels(n)[0] AS type, n.name AS name, n.role AS role,
           coalesce(n.degree,0) AS degree,
           n.summary IS NOT NULL AS hasSummary,
           CASE WHEN n.summary IS NULL THEN null ELSE left(n.summary,160) END AS teaser,
           n.summary_updated AS summaryUpdated,
           coalesce(n.is_mercantil,false) AS isMercantil
    ORDER BY degree DESC, id
    """
    with get_driver().session() as s:
        rows = s.run(cypher, **_FICHA_PARAMS).data()
    return _assign_slugs(rows)


def ficha_slug(node_id: str) -> str | None:
    """Public ficha slug for one node, or None when it isn't indexable.
    Applies the same criteria + collision rule as indexable_entities() but
    only queries the node's own slug-group (cheap: runs on every /node hit)."""
    base = node_id.split(":", 1)[1] if ":" in node_id else node_id
    cypher = f"""
    MATCH (n) WHERE n.entity_id ENDS WITH $suffix AND {_FICHA_WHERE}
    RETURN n.entity_id AS id, labels(n)[0] AS type
    """
    with get_driver().session() as s:
        rows = s.run(cypher, suffix=":" + base, **_FICHA_PARAMS).data()
    if node_id not in {r["id"] for r in rows}:
        return None
    _assign_slugs(rows)
    return next(r["slug"] for r in rows if r["id"] == node_id)


# --- Directorio de investigados (spec 037) ------------------------------------
# Least→most advanced procedural stage (ontology `orden`, spec 069), so the directory can
# headline the most advanced charge. condenado_no_firme < condenado (firmeza: spec 059).
_STATUS_RANK = ontologia.rango_estado()


def accused_entities() -> list[dict]:
    """Every entity with charges (ACCUSED_OF/CONVICTED_OF → :Crime), one row per
    entity with its full charge list. Status semantics are EXACTLY node_detail's
    (CONVICTED_OF → 'condenado_no_firme' sin firmeza declarada, else
    coalesce(acc.status,'investigado')) so the directory can never contradict a
    ficha's badge. Spec 061: una acusación SIN estado registrado se presenta como
    «investigado», nunca como «acusado» — el estado lo declara el registro de
    accusations.yaml, que lo propaga a las aristas con su fecha y su fuente
    (status_fecha / status_fuente / status_fuente_tipo)."""
    cypher = """
    MATCH (n)-[acc:ACCUSED_OF|CONVICTED_OF]->(c:Crime)
    WHERE n.entity_id IS NOT NULL
    WITH n, acc, c, CASE
            WHEN type(acc) = 'CONVICTED_OF' THEN coalesce(acc.status, 'condenado_no_firme')
            ELSE coalesce(acc.status, 'investigado') END AS status
    WITH n, collect(DISTINCT {
            crime: c.name, article: acc.article, status: status,
            sourceKind: coalesce(acc.source_kind, 'sin_procedencia'),
            doc: acc.source_doc, page: acc.source_page,
            quote: acc.source_quote, outlet: acc.outlet,
            statusFecha: acc.status_fecha, statusFuente: acc.status_fuente,
            statusFuenteTipo: acc.status_fuente_tipo}) AS charges
    RETURN n.entity_id AS id, labels(n)[0] AS type, n.name AS name,
           n.role AS role, coalesce(n.degree,0) AS degree, charges,
           coalesce(n.is_pseudonym,false) AS isPseudonym
    """
    with get_driver().session() as s:
        rows = s.run(cypher).data()

    def _rank(row: dict) -> int:
        return max((_STATUS_RANK.get(c["status"], 0) for c in row["charges"]), default=0)

    rows.sort(key=lambda r: (-_rank(r), -r["degree"], r["id"]))
    return rows


def entities_with_charges(ids: list[str]) -> list[dict]:
    """Batch lookup for a curated case's cast (spec 038): each entity with its
    (possibly empty) charge list, same status semantics as accused_entities()."""
    cypher = """
    MATCH (n) WHERE n.entity_id IN $ids AND """ + _served("n") + """
    OPTIONAL MATCH (n)-[acc:ACCUSED_OF|CONVICTED_OF]->(c:Crime)
    WITH n, acc, c, CASE
            WHEN acc IS NULL THEN null
            WHEN type(acc) = 'CONVICTED_OF' THEN coalesce(acc.status, 'condenado_no_firme')
            ELSE coalesce(acc.status, 'investigado') END AS status
    WITH n, [x IN collect(DISTINCT CASE WHEN acc IS NULL THEN null ELSE {
            crime: c.name, article: acc.article, status: status,
            sourceKind: coalesce(acc.source_kind, 'sin_procedencia'),
            doc: acc.source_doc, page: acc.source_page,
            quote: acc.source_quote, outlet: acc.outlet,
            statusFecha: acc.status_fecha, statusFuente: acc.status_fuente,
            statusFuenteTipo: acc.status_fuente_tipo} END)
         WHERE x IS NOT NULL] AS charges
    RETURN n.entity_id AS id, labels(n)[0] AS type, n.name AS name,
           n.role AS role, coalesce(n.degree,0) AS degree, charges,
           coalesce(n.is_pseudonym,false) AS isPseudonym
    """
    with get_driver().session() as s:
        return s.run(cypher, ids=ids).data()


def documents_by_ids(doc_ids: list[str]) -> list[dict]:
    """The documents backing a curated case, chronological. Same row shape as
    recent_documents() so the web can reuse its rendering."""
    cypher = """
    MATCH (d:Document) WHERE d.doc_id IN $ids
    RETURN d.doc_id AS file, d.title AS doc, d.doc_type AS docType,
           d.doc_date AS date, coalesce(d.page_count,0) AS pageCount
    ORDER BY coalesce(d.doc_date, '9999') ASC, d.doc_id
    """
    with get_driver().session() as s:
        return s.run(cypher, ids=doc_ids).data()




# CENDOJ pseudonyms (pseudonyms.yaml): the auto's own wording often gives the office or body of the
# anonymised person («el Presidente de …, don Elias»). Serving those chunks on the pseudonym's ficha,
# next to its other relations, would let a reader re-identify them; the auto stays readable as a whole.


def node_detail(node_id: str) -> dict | None:
    cypher = """
    MATCH (n {entity_id:$id}) WHERE """ + _served("n") + """
    OPTIONAL MATCH (n)-[:MENTIONED_IN]->(ch:Chunk)-[:PART_OF]->(d:Document)
    WITH n, ch, d ORDER BY d.title, ch.page_start, d.doc_id, ch.chunk_id
    WITH n, collect(DISTINCT {
        doc: d.title, file: d.doc_id, page: ch.page_start,
        quote: ch.text, chunkId: ch.chunk_id
    })[0..20] AS sources
    // Procedural stage(s) of this entity's charges, surfaced so the panel can
    // headline "imputado / acusado / condenado" without walking the neighborhood.
    // collect() drops the null produced when there are no charges.
    OPTIONAL MATCH (n)-[acc:ACCUSED_OF|CONVICTED_OF]->(:Crime)
    WITH n, sources, collect(DISTINCT CASE
            WHEN acc IS NULL THEN null
            WHEN type(acc) = 'CONVICTED_OF' THEN coalesce(acc.status, 'condenado_no_firme')
            ELSE coalesce(acc.status, 'investigado') END) AS charge_statuses,
         [x IN collect(DISTINCT CASE WHEN acc.status_fuente_tipo = 'prensa' THEN
            {status: acc.status, fecha: acc.status_fecha, fuente: acc.status_fuente} END)
          WHERE x IS NOT NULL] AS press_statuses
    RETURN {id:n.entity_id, type:labels(n)[0], name:n.name, role:n.role,
            aliases:n.aliases, degree:coalesce(n.degree,0),
            mentions:coalesce(n.mentions,0),
            context:coalesce(n.is_context,false),
            press:coalesce(n.is_press,false),
            isPseudonym:coalesce(n.is_pseudonym,false),
            pseudonymNote:n.pseudonym_note,
            summary:n.summary,
            summarySources:coalesce(n.summary_sources,[]),
            summaryUpdated:n.summary_updated,
            mercantil:CASE WHEN coalesce(n.is_mercantil,false) THEN {
                nif:n.nif, hoja:n.hoja_registral, capital:n.capital_social,
                cnae:n.cnae, fecha:n.fecha_constitucion, provincia:n.provincia,
                domicilio:n.domicilio, borme:n.borme_id,
                objeto_social:n.objeto_social, capital_historico:n.capital_historico,
                en_concurso:coalesce(n.en_concurso,false), concurso_fecha:n.concurso_fecha,
                concurso_juzgado:n.concurso_juzgado, disuelta:coalesce(n.disuelta,false),
                disuelta_fecha:n.disuelta_fecha,
                en_liquidacion:coalesce(n.en_liquidacion,false), liquidacion_fecha:n.liquidacion_fecha} ELSE null END,
            chargeStatuses:charge_statuses, chargeStatusesPress:press_statuses} AS node,
           sources
    """
    with get_driver().session() as s:
        rec = s.run(cypher, id=node_id).single()
    if not rec:
        return None
    node = rec["node"]
    # Public ficha link (spec 035): lets the explorer panel offer "Ver ficha
    # pública" without duplicating the indexability criteria client-side.
    node["fichaSlug"] = ficha_slug(node_id)
    hidden = is_anonymised_relative(node_id) or node.get("isPseudonym")
    sources = [] if hidden else rec["sources"]
    return {"node": node, "sources": sources}


def timeline(
    entity_id: str | None = None,
    from_year: int | None = None,
    to_year: int | None = None,
    types: list[str] | None = None,
    limit: int = 500,
    entities: list[str] | None = None,
    docs: list[str] | None = None,
) -> list[dict]:
    """Chronological dated relations, optionally scoped to an entity's involvement.

    Returns events ordered by normalized date ascending. Includes inline
    provenance (doc/page/quote) so the timeline UI can cite directly.

    `entities`/`docs` (spec 038, additive — `entity_id` stays for the MCP
    `cronologia` tool and the explorer) scope the events to a curated case:
    any endpoint in `entities`, and/or `r.source_doc` in `docs`.
    """
    cypher = f"""
    MATCH (a)-[r]->(b)
    WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
      AND NOT type(r) IN {_EXCLUDE}
      AND r.date_year IS NOT NULL
      AND ($entity IS NULL OR a.entity_id = $entity OR b.entity_id = $entity)
      AND ($entities IS NULL OR a.entity_id IN $entities OR b.entity_id IN $entities)
      AND ($docs IS NULL OR r.source_doc IN $docs)
      AND ($from_year IS NULL OR r.date_year >= $from_year)
      AND ($to_year   IS NULL OR r.date_year <= $to_year)
      AND ($types IS NULL OR labels(a)[0] IN $types OR labels(b)[0] IN $types)
    WITH r, a, b, type(r) AS rt
    ORDER BY coalesce(r.date_norm, toString(r.date_year)), a.entity_id, b.entity_id, rt, r.date_year,
             r.source_doc, r.source_page, r.source_quote, r.amount, r.currency
    LIMIT $limit
    RETURN {{
      date: coalesce(r.date_norm, toString(r.date_year)),
      year: r.date_year,
      type: rt,
      source: {{id: a.entity_id, name: a.name, type: labels(a)[0]}},
      target: {{id: b.entity_id, name: b.name, type: labels(b)[0]}},
      doc: r.source_doc, page: r.source_page, quote: {_unless_relative('r.source_quote', 'a', 'b')},
      amount: r.amount, currency: r.currency
    }} AS event
    """
    with get_driver().session() as s:
        return [r["event"] for r in s.run(
            cypher,
            entity=entity_id,
            entities=entities,
            docs=docs,
            from_year=from_year,
            to_year=to_year,
            types=types,
            limit=limit,
        )]


# --- Helpers restored from the deleted GraphRAG chat (bcca65a^) for the MCP ---
# connector (spec 009). Adapted: per-query timeout (public no-auth endpoint) and
# the 4th provenance tier `ultima_hora` added after the original deletion.

def keyword_search_chunks(q: str, k: int = 4, doc_types: list[str] | None = None,
                          from_date: str | None = None, to_date: str | None = None,
                          mentions: str | None = None) -> list[dict]:
    """Fulltext (Lucene, fuzzy, required-AND) search over judicial document chunks.

    `doc_types` filters by `Document.doc_type`; `from_date`/`to_date` (ISO
    YYYY-MM-DD) filter by `Document.doc_date`; `mentions` (an entity_id) keeps
    only chunks that mention that entity. NB: doc_type/doc_date only exist on
    curated documents (the `documents` overlay), so those filters exclude docs
    without that metadata — coverage is partial."""
    lucene = _lucene(q, phrase_boost=True)
    if not lucene or (mentions and is_anonymised_relative(mentions)):
        return []
    cypher = """
    CALL db.index.fulltext.queryNodes('chunk_fulltext', $q) YIELD node, score
    MATCH (node)-[:PART_OF]->(d:Document)
    WHERE ($doc_types IS NULL OR d.doc_type IN $doc_types)
      AND ($from_date IS NULL OR (d.doc_date IS NOT NULL AND d.doc_date >= $from_date))
      AND ($to_date   IS NULL OR (d.doc_date IS NOT NULL AND d.doc_date <= $to_date))
      AND ($mentions IS NULL OR EXISTS { ({entity_id:$mentions})-[:MENTIONED_IN]->(node) })
      AND ($mentions IS NULL OR NOT EXISTS { MATCH (p {entity_id:$mentions}) WHERE coalesce(p.is_pseudonym, false) })
      AND ($mentions IS NULL OR EXISTS { MATCH (q {entity_id:$mentions}) WHERE """ + _served("q") + """ })
    RETURN node.chunk_id AS chunkId, node.text AS text, node.page_start AS page,
           d.title AS doc, d.doc_id AS file, d.doc_type AS docType, score
    ORDER BY score DESC, chunkId LIMIT $k
    """
    with get_driver().session() as s:
        return s.run(Query(cypher, timeout=15), q=lucene, k=k, doc_types=doc_types,
                     from_date=from_date, to_date=to_date, mentions=mentions).data()


def count_chunks_missing_date(q: str, doc_types: list[str] | None = None,
                              mentions: str | None = None) -> int:
    """How many chunks match the fulltext query (+ doc_types/mentions filters) but
    live in a document WITHOUT a curated `doc_date` — i.e. how many a date filter
    silently drops for lacking metadata, not for being out of range. Lets
    buscar_documentos report honest coverage."""
    lucene = _lucene(q, phrase_boost=True)
    # Same guards as keyword_search_chunks: a count per (query, hidden person) would be a yes/no oracle for re-identifying them.
    if not lucene or (mentions and is_anonymised_relative(mentions)):
        return 0
    cypher = """
    CALL db.index.fulltext.queryNodes('chunk_fulltext', $q) YIELD node
    MATCH (node)-[:PART_OF]->(d:Document)
    WHERE d.doc_date IS NULL
      AND ($doc_types IS NULL OR d.doc_type IN $doc_types)
      AND ($mentions IS NULL OR EXISTS { ({entity_id:$mentions})-[:MENTIONED_IN]->(node) })
      AND ($mentions IS NULL OR NOT EXISTS { MATCH (p {entity_id:$mentions}) WHERE coalesce(p.is_pseudonym, false) })
      AND ($mentions IS NULL OR EXISTS { MATCH (q {entity_id:$mentions}) WHERE """ + _served("q") + """ })
    RETURN count(node) AS n
    """
    with get_driver().session() as s:
        rec = s.run(Query(cypher, timeout=15), q=lucene, doc_types=doc_types,
                    mentions=mentions).single()
        return rec["n"] if rec else 0


# source_kind trust order — used to pick the representative citation when several
# documents corroborate the same flow (lower = more authoritative).
_KIND_RANK = ontologia.rango_capa()


def _flow_orientation(row: dict) -> tuple[str, str] | None:
    """The (payer_id, payee_id) a row votes for, or None when its relation type
    doesn't carry a reliable direction. PAID/CONTRACT_AWARDED_TO go source→target;
    RECEIVED and RECEIVED_GRANT (BDNS: beneficiary→awarding body) are the inverse.
    AWARDED_CONTRACT abstains: the corpus uses it both as awarder→awardee AND
    awardee→awarder, so it can't be trusted for direction."""
    t = row["type"]
    if t in ("PAID", "CONTRACT_AWARDED_TO"):
        return (row["sourceId"], row["targetId"])
    if t in ("RECEIVED", "RECEIVED_GRANT", "MINOR_CONTRACTOR_OF", "CONTRACTOR_OF"):  # ontologia-ok: flujos receptor→pagador
        # MINOR_CONTRACTOR_OF / CONTRACTOR_OF (PLACSP y ampliada) never carry `amount`
        # so they should never reach the money view — the branch is defensive only
        # (awardee → contracting body).
        return (row["targetId"], row["sourceId"])
    return None


def _collapse_flows(rows: list[dict], limit: int) -> list[dict]:
    """Collapse raw amount-bearing edges into one row per real flow.

    The graph records the same transfer several ways — the same edge cited in
    several documents, the inverse PAID/RECEIVED twin, and contract awards mirrored
    as AWARDED_CONTRACT/CONTRACT_AWARDED_TO — so a naive dump repeats each flow up
    to 5×. We group by {unordered pair, amount, currency}; per group we keep the
    best-sourced citation, count corroborating `sources`, and infer a direction
    from the votes (`directed`). When the votes disagree — a genuine data conflict,
    e.g. both endpoints recorded as payer for the same amount — we fall back to an
    undirected pairing (`directed=False`) instead of asserting a wrong arrow."""
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for r in rows:
        key = (frozenset((r["sourceId"], r["targetId"])),
               round(float(r["amount"]), 2), r["currency"])
        groups[key].append(r)

    out: list[dict] = []
    for (_pair, amount, currency), grp in groups.items():
        rep = min(grp, key=lambda r: (_KIND_RANK.get(r.get("source_kind"), 9),
                                      0 if r.get("page") is not None else 1))
        votes = {o for o in (_flow_orientation(r) for r in grp) if o is not None}
        if len(votes) == 1:
            payer, payee = next(iter(votes))
            directed = True
        else:  # no trustworthy vote, or conflicting votes → undirected pair
            payer, payee = rep["sourceId"], rep["targetId"]
            directed = False
        names = {r["sourceId"]: r["source"] for r in grp}
        names.update({r["targetId"]: r["target"] for r in grp})
        # RECEIVED reads "a received from b"; once we orient the row payer→payee it
        # would contradict its own label, so show it as PAID (the only reverse-
        # semantics type in the corpus). Other types are already payer→payee.
        disp_type = "PAID" if (directed and rep["type"] == "RECEIVED") else rep["type"]
        out.append({
            "source": names.get(payer, rep["source"]), "sourceId": payer,
            "target": names.get(payee, rep["target"]), "targetId": payee,
            "type": disp_type, "amount": amount, "currency": currency,
            "date": rep.get("date"), "doc": rep.get("doc"), "page": rep.get("page"),
            "quote": rep.get("quote"),
            "source_kind": rep.get("source_kind", "judicial"),
            "outlet": rep.get("outlet"), "directed": directed,
            "sources": len({(r.get("doc"), r.get("page")) for r in grp}),
            "label": rep.get("label"),
        })
    # Deterministic order: amount desc, then dated-before-undated, then date and
    # pair as tiebreaks — equal amounts otherwise shuffle between requests
    # (dict/session ordering), which reads as data churn on the public page.
    out.sort(key=lambda r: (-r["amount"], r.get("date") is None,
                            r.get("date") or "", r["sourceId"] or "",
                            r["targetId"] or ""))
    return out[:limit]


def money_flows(entity_id: str | None = None, min_amount: float | None = None,
                from_year: int | None = None, to_year: int | None = None,
                limit: int = 50, entity_ids: list[str] | None = None) -> list[dict]:
    """Money trail: collapsed entity→entity transfers carrying an `amount`, ordered
    by amount desc, each with its best citation + provenance, a `sources` count and
    a `directed` flag. Optionally scoped to an entity (payer or payee), a minimum
    amount, and a year range.

    Endpoints are restricted to real economic actors (Person/Company/Organization);
    contracts reified through an intermediate `Contract` node are traversed back to
    their awarder→awardee actors so they read as a single transfer instead of two
    rows pointing at the contract node. The raw rows are then de-duplicated by
    {pair, amount, currency} (see `_collapse_flows`)."""
    # Fetch a generous candidate set so post-collapse we can still fill `limit`
    # (each real flow appears up to ~5× before collapsing). The cap must exceed
    # the corpus' raw amount-bearing edge count (~770 at 154 real flows) or the
    # smallest flows get truncated before they can collapse.
    fetch = min(max(limit * 6, 60), 1200)
    params = dict(entity=entity_id, entity_ids=entity_ids, min=min_amount,
                  from_year=from_year, to_year=to_year, fetch=fetch)
    # Direct edges between real actors.
    direct = f"""
    MATCH (a)-[r]->(b)
    WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
      AND (a:Person OR a:Company OR a:Organization)
      AND (b:Person OR b:Company OR b:Organization)
      AND NOT type(r) IN {_EXCLUDE}
      AND r.amount IS NOT NULL AND r.amount > 0
      AND ($entity IS NULL OR a.entity_id = $entity OR b.entity_id = $entity)
      AND ($entity_ids IS NULL OR a.entity_id IN $entity_ids OR b.entity_id IN $entity_ids)
      AND ($min IS NULL OR r.amount >= $min)
      AND ($from_year IS NULL OR (r.date_year IS NOT NULL AND r.date_year >= $from_year))
      AND ($to_year   IS NULL OR (r.date_year IS NOT NULL AND r.date_year <= $to_year))
    RETURN a.name AS source, a.entity_id AS sourceId,
           coalesce(a.is_pseudonym,false) AS sourceSeud, coalesce(b.is_pseudonym,false) AS targetSeud,
           type(r) AS type, b.name AS target, b.entity_id AS targetId,
           r.amount AS amount, coalesce(r.currency,'EUR') AS currency,
           coalesce(r.date_norm, toString(r.date_year)) AS date,
           r.source_doc AS doc, r.source_page AS page, {_unless_relative('r.source_quote', 'a', 'b')} AS quote,
           coalesce(r.source_kind,'sin_procedencia') AS source_kind, r.outlet AS outlet,
           r.instrumento AS label
    ORDER BY r.amount DESC, sourceId, targetId, type, date, doc, page, r.source_quote LIMIT $fetch
    """
    # Contracts reified as (awarder)-[:AWARDED_CONTRACT]->(:Contract)-[:CONTRACT_AWARDED_TO]->(awardee),
    # rebuilt as a single awarder→awardee transfer.
    reified = """
    MATCH (a)-[r1:AWARDED_CONTRACT]->(c:Contract)-[r2:CONTRACT_AWARDED_TO]->(b)
    WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL
      AND coalesce(r2.amount, r1.amount) > 0
      AND ($entity IS NULL OR a.entity_id = $entity OR b.entity_id = $entity)
      AND ($entity_ids IS NULL OR a.entity_id IN $entity_ids OR b.entity_id IN $entity_ids)
      AND ($min IS NULL OR coalesce(r2.amount, r1.amount) >= $min)
      AND ($from_year IS NULL OR (coalesce(r2.date_year, r1.date_year) IS NOT NULL AND coalesce(r2.date_year, r1.date_year) >= $from_year))
      AND ($to_year   IS NULL OR (coalesce(r2.date_year, r1.date_year) IS NOT NULL AND coalesce(r2.date_year, r1.date_year) <= $to_year))
    RETURN a.name AS source, a.entity_id AS sourceId,
           'CONTRACT_AWARDED_TO' AS type, b.name AS target, b.entity_id AS targetId,
           coalesce(r2.amount, r1.amount) AS amount,
           coalesce(r2.currency, r1.currency, 'EUR') AS currency,
           coalesce(r2.date_norm, r1.date_norm, toString(coalesce(r2.date_year, r1.date_year))) AS date,
           coalesce(r2.source_doc, r1.source_doc) AS doc,
           coalesce(r2.source_page, r1.source_page) AS page,
           """ + _unless_relative("coalesce(r2.source_quote, r1.source_quote)", "a", "b") + """ AS quote,
           coalesce(r2.source_kind, r1.source_kind, 'sin_procedencia') AS source_kind,
           coalesce(r2.outlet, r1.outlet) AS outlet,
           c.name AS label
    ORDER BY amount DESC, sourceId, targetId, date, doc, page, coalesce(r2.source_quote, r1.source_quote), label LIMIT $fetch
    """
    with get_driver().session() as s:
        rows = s.run(Query(direct, timeout=15), **params).data()
        rows += s.run(Query(reified, timeout=15), **params).data()
    return _collapse_flows(rows, limit)


# Centrality metric -> the node property holding its precomputed score. `grado`
# reads the always-present `degree`; the others read properties materialized by
# the offline `make centralidad` job (pipeline/src/network_metrics.py).
_CENTRALITY_PROP = {"grado": "degree", "intermediacion": "betweenness", "cercania": "closeness"}


def centrality(limit: int = 20, etype: str | None = None,
               metric: str = "grado") -> list[dict]:
    """Top entity nodes by a centrality `metric` (grado|intermediacion|cercania),
    each property materialized on the node. `grado` (degree) is always present;
    `intermediacion`/`cercania` are written by the offline `make centralidad` job.
    `etype` narrows to one label. Every row carries BOTH the requested `score` and
    the raw `degree`, so "muy conectado" (grado) can be contrasted with "muy
    puente" (intermediacion). `available` is False when the metric's property has
    not been computed yet on any node."""
    prop = _CENTRALITY_PROP.get(metric, "degree")
    cypher = f"""
    MATCH (n) WHERE n.entity_id IS NOT NULL AND n.`{prop}` IS NOT NULL
      AND ($etype IS NULL OR labels(n)[0] = $etype)
      AND {_served('n')}
    RETURN n.entity_id AS id, n.name AS name, labels(n)[0] AS type,
           coalesce(n.degree,0) AS degree, n.`{prop}` AS score,
           coalesce(n.is_pseudonym,false) AS isPseudonym
    ORDER BY score DESC, id LIMIT $limit
    """
    with get_driver().session() as s:
        return s.run(Query(cypher, timeout=15), etype=etype, limit=limit).data()


def communities(etype: str | None = None, limit: int = 20,
                members_per: int = 6) -> list[dict]:
    """Sub-networks detected by the offline community job (`make centralidad`,
    Louvain by default). Returns the largest `limit` communities, each with its
    tentative `label`, size, and top `members_per` members by degree. `etype`
    narrows membership listing to one label (size still counts all members).
    Empty when communities have not been computed yet."""
    # Order by degree DESC *before* collect so members come out principal-first
    # (Neo4j preserves row order through collect) — no apoc sort needed. `size`
    # counts all members; the etype filter only narrows the listed members.
    cypher = """
    MATCH (n) WHERE n.entity_id IS NOT NULL AND n.community IS NOT NULL
      AND """ + _served("n") + """
    WITH n ORDER BY coalesce(n.degree,0) DESC, n.entity_id
    WITH n.community AS community, head(collect(n.community_label)) AS label,
         count(*) AS size,
         collect(CASE WHEN $etype IS NULL OR labels(n)[0] = $etype
                      THEN {id:n.entity_id, name:n.name, type:labels(n)[0],
                            degree:coalesce(n.degree,0),
                            isPseudonym:coalesce(n.is_pseudonym,false)} END) AS members_raw
    WITH community, label, size,
         [x IN members_raw WHERE x IS NOT NULL][0..$members_per] AS members
    RETURN community, label, size, members
    ORDER BY size DESC, community LIMIT $limit
    """
    with get_driver().session() as s:
        return s.run(Query(cypher, timeout=15), etype=etype, limit=limit,
                     members_per=members_per).data()


def recent_documents(limit: int = 10, doc_type: str | None = None) -> list[dict]:
    """Most recent documents by `Document.doc_date` (desc). Only documents with
    a curated `doc_date` appear — recency of the rest can't be ordered."""
    cypher = """
    MATCH (d:Document) WHERE d.doc_date IS NOT NULL
      AND ($doc_type IS NULL OR d.doc_type = $doc_type)
    RETURN d.doc_id AS file, d.title AS doc, d.doc_type AS docType,
           d.doc_date AS date, coalesce(d.page_count,0) AS pageCount
    ORDER BY d.doc_date DESC, d.doc_id LIMIT $limit
    """
    with get_driver().session() as s:
        return s.run(Query(cypher, timeout=15), limit=limit, doc_type=doc_type).data()


def read_document(doc: str, page: int | None = None, window: int = 0) -> dict | None:
    """Resolve a citation `[documento, p.N]` to its full passage text.

    `doc` matches `Document.doc_id` exactly or `Document.title` (exact first,
    else case-insensitive substring) — citations surface the title, not the id.
    Returns None when nothing matches. When several titles match it returns
    `{"candidates": [...]}` so the caller can disambiguate. Without `page` it
    returns the page index (`{"doc":…, "pages":[…]}`); with `page` it returns
    the full text of that page's chunks ordered by `chunk_index`, plus ±`window`
    adjacent pages (`{"doc":…, "page":p, "window":w, "chunks":[…]}`)."""
    resolve = """
    MATCH (d:Document)
    WHERE d.doc_id = $doc OR d.title = $doc
    RETURN d.doc_id AS doc_id, d.title AS title, d.doc_type AS docType,
           d.doc_date AS date, coalesce(d.page_count,0) AS pageCount
    LIMIT 11
    """
    fuzzy = """
    MATCH (d:Document) WHERE toLower(d.title) CONTAINS toLower($doc)
    RETURN d.doc_id AS doc_id, d.title AS title, d.doc_type AS docType,
           d.doc_date AS date, coalesce(d.page_count,0) AS pageCount
    LIMIT 11
    """
    with get_driver().session() as s:
        cands = s.run(Query(resolve, timeout=15), doc=doc).data()
        if not cands:
            cands = s.run(Query(fuzzy, timeout=15), doc=doc).data()
        if not cands:
            return None
        if len(cands) > 1:
            return {"candidates": cands[:10]}
        d = cands[0]
        if page is None:
            pages = s.run(
                Query("MATCH (c:Chunk {doc_id:$id}) RETURN DISTINCT c.page_start AS page "
                      "ORDER BY page", timeout=15),
                id=d["doc_id"],
            ).value()
            return {"doc": d, "pages": [p for p in pages if p is not None]}
        lo, hi = page - max(0, window), page + max(0, window)
        chunks = s.run(
            Query("MATCH (c:Chunk {doc_id:$id}) WHERE c.page_start >= $lo AND c.page_start <= $hi "
                  "RETURN c.page_start AS page, c.chunk_index AS idx, c.text AS text "
                  "ORDER BY page, idx", timeout=15),
            id=d["doc_id"], lo=lo, hi=hi,
        ).data()
        return {"doc": d, "page": page, "window": window, "chunks": chunks}


def entity_identities(ids: list[str]) -> list[dict]:
    """Curated identity for the given entities: their role plus any `contexto`
    (public-knowledge) family/party/office ties. Authoritative for WHO each
    person is, so clients never infer identity/kinship from judicial
    co-occurrence edges (e.g. ASSOCIATED_WITH)."""
    if not ids:
        return []
    cypher = """
    MATCH (n) WHERE n.entity_id IN $ids AND """ + _served("n") + """
    OPTIONAL MATCH (n)-[r]->(m)
      WHERE m.entity_id IS NOT NULL AND r.source_kind = 'contexto'
    WITH n, collect(CASE WHEN m IS NOT NULL
                         THEN {rel:type(r), kind:r.kind, other:m.name} END) AS ties
    WITH n, [t IN ties WHERE t IS NOT NULL] AS ties
    WHERE n.role IS NOT NULL OR size(ties) > 0
    RETURN n.entity_id AS id, n.name AS name, labels(n)[0] AS type, n.role AS role, ties
    """
    with get_driver().session() as s:
        return s.run(Query(cypher, timeout=15), ids=ids).data()


def ego_triples(node_id: str, limit: int = 25, kinds: list[str] | None = None,
                rel_type: str | None = None) -> dict:
    """Triples around a focus node, ordered by RELEVANCE: most-evidenced first
    (×count desc), tie-broken by provenance (judicial > contrato > prensa > resto).
    Parallel edges for the same (subject, type, object, procedencia) collapse into
    ONE line with a ×count. Essential for big hubs (Koldo ~1480 edges) where a flat
    cap of 25 would show <2% as noise. `kinds` narrows to those `source_kind`s;
    `rel_type` narrows to one relationship type (e.g. PAID). Identity/family ties
    (contexto) sink under this order by design — `entity_identities`/ficha is the
    authoritative place for WHO someone is. Returns `{triples, total, shown}` where
    `total` is the real number of relation groups matching the filters."""
    cypher = f"""
    MATCH (c {{entity_id:$id}})-[r]-(m)
    WHERE m.entity_id IS NOT NULL AND NOT type(r) IN {_EXCLUDE}
      AND ($kinds IS NULL OR coalesce(r.source_kind,'sin_procedencia') IN $kinds)
      AND ($rel_type IS NULL OR type(r) = $rel_type)
    WITH startNode(r).name AS s, type(r) AS rel, endNode(r).name AS o,
         coalesce(startNode(r).is_pseudonym,false) AS s_seud,
         coalesce(endNode(r).is_pseudonym,false) AS o_seud,
         coalesce(r.source_kind,'sin_procedencia') AS kind, collect(r) AS rs
    WITH {{s:s, rel:rel, o:o, kind:kind, count:size(rs), s_seud:s_seud, o_seud:o_seud,
          outlet: head([x IN rs WHERE x.outlet IS NOT NULL | x.outlet]),
          status: head([x IN rs WHERE x.status IS NOT NULL | x.status]),
          st: head([x IN rs WHERE x.status IS NOT NULL | [x.status_fuente_tipo, x.status_fecha, x.status_fuente]])}} AS g
    WITH collect(g) AS groups
    WITH groups, size(groups) AS total
    UNWIND groups AS g
    WITH g, total
    ORDER BY g.count DESC,
             CASE g.kind WHEN 'judicial' THEN 0 WHEN 'contrato' THEN 1
                         WHEN 'prensa' THEN 2 ELSE 3 END,
             g.rel, g.kind, g.s, g.o, g.s_seud, g.o_seud
    LIMIT $limit
    RETURN g.s AS s, g.rel AS rel, g.o AS o, g.kind AS kind,
           g.s_seud AS s_seud, g.o_seud AS o_seud,
           g.count AS count, g.outlet AS outlet, g.status AS status, g.st AS st, total
    """
    with get_driver().session() as s:
        rows = s.run(Query(cypher, timeout=15), id=node_id, limit=limit,
                     kinds=kinds, rel_type=rel_type).data()

    def _suffix(r: dict) -> str:
        # Procedural stage of a charge — kept on EVERY provenance branch so
        # "imputado/procesado/condenado" is never silently dropped. Spec 061: una
        # ACCUSED_OF sin estado registrado se presenta como «investigado», que es
        # el mínimo que sostiene una acusación, nunca como «acusado».
        status = r.get("status") or (
            "investigado" if r.get("rel") == "ACCUSED_OF"
            else "condenado_no_firme" if r.get("rel") == "CONVICTED_OF" else None)
        st = r.get("st") or [None, None, None]
        estado = estado_tag(status, *st) if r.get("status") else (f" [estado: {status}]" if status else "")
        if r["kind"] == "contexto":
            base = " (contexto público, sin fuente judicial)"
        elif r["kind"] == "prensa":
            medio = f": {r['outlet']}" if r.get("outlet") else ""
            base = f" (según prensa{medio}, sin fuente judicial)"
        elif r["kind"] == "ultima_hora":
            medio = f": {r['outlet']}" if r.get("outlet") else ""
            base = f" (última hora — según prensa{medio}, sin contraste judicial)"
        elif r["kind"] == "mercantil":
            base = " (según registro mercantil / BORME, sin fuente judicial)"
        elif r["kind"] == "subvencion":
            base = " (según BDNS/SNPSAP — subvención o ayuda pública, legal; sin fuente judicial)"
        elif r["kind"] == "contratacion":
            if r.get("rel") == "CONTRACTOR_OF":
                base = " (según PLACSP/plataformas oficiales de contratación — contratación pública agregada, legal; sin fuente judicial)"
            else:
                base = " (según PLACSP — contratación menor pública agregada, legal; sin fuente judicial)"
        elif r["kind"] == "boe":
            base = " (según BOE — nombramiento/cese oficial, acto administrativo legal; sin fuente judicial)"
        elif r["kind"] == "acusacion":
            base = " (registro curado de acusaciones, con cita judicial)"
        elif r["kind"] == "judicial":
            base = ""
        else:  # fail-closed (spec 059): sin marcador de capa ≠ judicial
            base = " (procedencia no registrada)"
        return base + estado

    def _veces(r: dict) -> str:
        n = r.get("count") or 1
        return f" (×{n} evidencias)" if n > 1 else ""

    seud = " ⚠️ seudónimo judicial"  # spec 059: el marcador viaja con el nombre
    triples = [f"{r['s']}{seud if r.get('s_seud') else ''} -[{r['rel']}]-> "
               f"{r['o']}{seud if r.get('o_seud') else ''}" + _suffix(r) + _veces(r) for r in rows]
    total = rows[0]["total"] if rows else 0
    return {"triples": triples, "total": total, "shown": len(triples)}


def grants(entity_id: str | None = None, min_amount: float | None = None,
           limit: int = 25) -> dict:
    """The full BDNS/subvenciones overlay (spec 042/043): every RECEIVED_GRANT edge
    with source_kind='subvencion', ordered by importe desc (undated/unpriced last).
    Unlike money_flows this includes the concessions WITHOUT `en_flujos` (they carry
    `importe`, not `amount`, and never enter the money view by construction).
    Optionally scoped to one entity (beneficiary or awarding body) and a minimum
    amount. Returns `{rows, total, shown}`."""
    cypher = """
    MATCH (a)-[r:RECEIVED_GRANT]->(b)
    WHERE r.source_kind = 'subvencion'
      AND ($entity IS NULL OR a.entity_id = $entity OR b.entity_id = $entity)
    WITH a, r, b, coalesce(r.amount, r.importe) AS importe
    WHERE $min IS NULL OR (importe IS NOT NULL AND importe >= $min)
    WITH collect({
        beneficiario: a.name, beneficiario_id: a.entity_id,
        convocante: b.name, convocante_id: b.entity_id,
        convocante_detalle: r.convocante_detalle,
        fecha: r.fecha, importe: importe, instrumento: r.instrumento,
        bdns_endpoint: r.bdns_endpoint, nif: r.nif,
        denominacion_bdns: r.denominacion_bdns, note: r.note,
        bdns_key: r.bdns_key, en_flujos: r.amount IS NOT NULL}) AS rows
    WITH rows, size(rows) AS total
    UNWIND rows AS row
    WITH row, total
    ORDER BY coalesce(row.importe, -1) DESC, row.fecha, row.beneficiario
    LIMIT $limit
    RETURN row, total
    """
    with get_driver().session() as s:
        recs = s.run(Query(cypher, timeout=15), entity=entity_id,
                     min=min_amount, limit=limit).data()
    rows = [r["row"] for r in recs]
    total = recs[0]["total"] if recs else 0
    return {"rows": rows, "total": total, "shown": len(rows)}


def mercantil_layer(entity_id: str | None = None, rel_type: str | None = None,
                    limit: int = 25) -> dict:
    """The BORME/mercantil overlay (specs 026-032): every edge with
    source_kind='mercantil' (ADMINISTRA/CONTROLS…), optionally scoped to one
    entity (either endpoint — a person's companies or a company's administrators)
    and/or one relation type. Directed as stored (source→target keeps the loader's
    semantics, e.g. administrador -[ADMINISTRA]-> empresa). Also counts the layer's
    registry nodes (is_mercantil=true) so the panorámica can report coverage.
    Returns `{rows, total, shown, registry_nodes}`."""
    cypher = """
    OPTIONAL MATCH (m) WHERE coalesce(m.is_mercantil, false) AND """ + _served("m") + """
    WITH count(m) AS registry_nodes
    OPTIONAL MATCH (a)-[r]->(b)
    WHERE r.source_kind = 'mercantil'
      AND ($entity IS NULL OR a.entity_id = $entity OR b.entity_id = $entity)
      AND ($rel_type IS NULL OR type(r) = $rel_type)
    WITH registry_nodes, collect(CASE WHEN r IS NULL THEN NULL ELSE {
        desde: a.name, desde_id: a.entity_id, desde_tipo: labels(a)[0],
        tipo: type(r),
        hasta: b.name, hasta_id: b.entity_id, hasta_tipo: labels(b)[0],
        rol: r.rol, cargo_desde: r.desde, borme_id: r.borme_id, note: r.note
    } END) AS rows0
    WITH registry_nodes, [x IN rows0 WHERE x IS NOT NULL] AS rows
    WITH registry_nodes, rows, size(rows) AS total
    UNWIND (CASE WHEN size(rows) = 0 THEN [NULL] ELSE rows END) AS row
    WITH registry_nodes, total, row
    ORDER BY row.tipo, row.desde, row.hasta
    LIMIT $limit
    RETURN row, total, registry_nodes
    """
    with get_driver().session() as s:
        recs = s.run(Query(cypher, timeout=15), entity=entity_id,
                     rel_type=rel_type, limit=limit).data()
    rows = [r["row"] for r in recs if r["row"] is not None]
    total = recs[0]["total"] if recs else 0
    registry_nodes = recs[0]["registry_nodes"] if recs else 0
    return {"rows": rows, "total": total, "shown": len(rows),
            "registry_nodes": registry_nodes}


def contratacion_layer(entity_id: str | None = None, limit: int = 25) -> dict:
    """The PLACSP/contratación overlay (spec 046): every aggregated
    MINOR_CONTRACTOR_OF edge with source_kind='contratacion' (awardee → contracting
    body, n_contratos/total_eur/rango — aggregates, never in the money view), PLUS the
    layer's props-only data that has no edges: awardee nodes marked `is_contratacion`
    (global totals) and the informative negatives (`placsp_sin_menores`, 0 minor
    contracts in the sweep window). Optionally scoped to one entity (either endpoint,
    or a props-only/negative node). Returns `{rows, total, shown, adjudicatarias,
    sin_menores}`."""
    cypher = """
    OPTIONAL MATCH (n) WHERE coalesce(n.is_contratacion, false) AND """ + _served("n") + """
      AND ($entity IS NULL OR n.entity_id = $entity)
    WITH collect({
        entidad: n.name, entidad_id: n.entity_id,
        n_contratos: n.placsp_n_contratos, total_eur: n.placsp_total_eur,
        rango: n.placsp_rango, organos_top: n.placsp_organos_top,
        nota: n.placsp_nota, nif: n.placsp_nif,
        denominacion_placsp: n.placsp_denominacion}) AS adjudicatarias
    OPTIONAL MATCH (m) WHERE coalesce(m.placsp_sin_menores, false) AND """ + _served("m") + """
      AND ($entity IS NULL OR m.entity_id = $entity)
    WITH adjudicatarias, collect({
        entidad: m.name, entidad_id: m.entity_id, nota: m.placsp_nota}) AS sin_menores
    OPTIONAL MATCH (a)-[r:MINOR_CONTRACTOR_OF]->(b)
    WHERE r.source_kind = 'contratacion'
      AND ($entity IS NULL OR a.entity_id = $entity OR b.entity_id = $entity)
    WITH adjudicatarias, sin_menores, collect(CASE WHEN r IS NULL THEN NULL ELSE {
        adjudicataria: a.name, adjudicataria_id: a.entity_id,
        organo: b.name, organo_id: b.entity_id, organo_detalle: r.organo_detalle,
        n_contratos: r.n_contratos, total_eur: r.total_eur, rango: r.rango,
        via_ute: r.via_ute, ejemplo_link: r.ejemplo_link,
        ejemplo_titulo: r.ejemplo_titulo, nif: r.nif,
        denominacion_placsp: r.denominacion_placsp, note: r.note,
        placsp_key: r.placsp_key} END) AS rows0
    WITH adjudicatarias, sin_menores, [x IN rows0 WHERE x IS NOT NULL] AS rows
    WITH adjudicatarias, sin_menores, rows, size(rows) AS total
    UNWIND (CASE WHEN size(rows) = 0 THEN [NULL] ELSE rows END) AS row
    WITH adjudicatarias, sin_menores, total, row
    ORDER BY coalesce(row.total_eur, -1) DESC, row.adjudicataria
    LIMIT $limit
    RETURN row, total, adjudicatarias, sin_menores
    """
    with get_driver().session() as s:
        recs = s.run(Query(cypher, timeout=15), entity=entity_id, limit=limit).data()
    rows = [r["row"] for r in recs if r["row"] is not None]
    total = recs[0]["total"] if recs else 0
    adjudicatarias = [a for a in (recs[0]["adjudicatarias"] if recs else [])
                      if a.get("entidad_id")]
    sin_menores = [n for n in (recs[0]["sin_menores"] if recs else [])
                   if n.get("entidad_id")]
    return {"rows": rows, "total": total, "shown": len(rows),
            "adjudicatarias": adjudicatarias, "sin_menores": sin_menores}


def contratacion_ampliada_layer(entity_id: str | None = None, limit: int = 25) -> dict:
    """The CONTRATACION AMPLIADA overlay (spec 055): every aggregated CONTRACTOR_OF
    edge with source_kind='contratacion' (awardee → contracting body; PLACSP tenders,
    aggregated CCAA/EELL platforms and CCAA minors, with procurement signals —
    n_emergencia/n_negociado_sp/via_ute/presupuesto — aggregates, never in the money
    view), PLUS the layer's props-only data: awardee nodes marked `is_contratacion_amp`
    (per-ámbito totals in the amp_* namespace) and the informative negatives
    (`amp_sin_adjudicaciones`, 0 DIRECT awards in the whole ampliada sweep). Optionally
    scoped to one entity (either endpoint, or a props-only/negative node). Returns
    `{rows, total, shown, adjudicatarias, sin_adjudicaciones}`; each adjudicataria's
    `ambitos` is a dict amp-prop → value (lic/agr/ccaa_*)."""
    cypher = """
    OPTIONAL MATCH (n) WHERE coalesce(n.is_contratacion_amp, false) AND """ + _served("n") + """
      AND ($entity IS NULL OR n.entity_id = $entity)
    WITH collect({
        entidad: n.name, entidad_id: n.entity_id,
        nota: n.amp_nota, nif: n.amp_nif, denominacion: n.amp_denominacion,
        ambitos: [k IN keys(n) WHERE k STARTS WITH 'amp_'
                  AND NOT k IN ['amp_nota','amp_nif','amp_denominacion',
                                'amp_sin_adjudicaciones'] | [k, n[k]]]
        }) AS adjudicatarias
    OPTIONAL MATCH (m) WHERE coalesce(m.amp_sin_adjudicaciones, false) AND """ + _served("m") + """
      AND ($entity IS NULL OR m.entity_id = $entity)
    WITH adjudicatarias, collect({
        entidad: m.name, entidad_id: m.entity_id, nota: m.amp_nota}) AS sin_adj
    OPTIONAL MATCH (a)-[r:CONTRACTOR_OF]->(b)
    WHERE r.source_kind = 'contratacion'
      AND ($entity IS NULL OR a.entity_id = $entity OR b.entity_id = $entity)
    WITH adjudicatarias, sin_adj, collect(CASE WHEN r IS NULL THEN NULL ELSE {
        adjudicataria: a.name, adjudicataria_id: a.entity_id,
        organo: b.name, organo_id: b.entity_id, organo_detalle: r.organo_detalle,
        ambito: r.ambito, fuente: r.fuente,
        n_contratos: r.n_contratos, total_eur: r.total_eur, rango: r.rango,
        n_emergencia: r.n_emergencia, n_negociado_sp: r.n_negociado_sp,
        presupuesto_total_eur: r.presupuesto_total_eur,
        via_ute: r.via_ute, ejemplo_link: r.ejemplo_link,
        ejemplo_titulo: r.ejemplo_titulo, nif: r.nif,
        denominacion: r.denominacion, note: r.note,
        placsp_key: r.placsp_key} END) AS rows0
    WITH adjudicatarias, sin_adj, [x IN rows0 WHERE x IS NOT NULL] AS rows
    WITH adjudicatarias, sin_adj, rows, size(rows) AS total
    UNWIND (CASE WHEN size(rows) = 0 THEN [NULL] ELSE rows END) AS row
    WITH adjudicatarias, sin_adj, total, row
    ORDER BY coalesce(row.total_eur, -1) DESC, row.adjudicataria
    LIMIT $limit
    RETURN row, total, adjudicatarias, sin_adj
    """
    with get_driver().session() as s:
        recs = s.run(Query(cypher, timeout=15), entity=entity_id, limit=limit).data()
    rows = [r["row"] for r in recs if r["row"] is not None]
    total = recs[0]["total"] if recs else 0
    adjudicatarias = []
    for a in (recs[0]["adjudicatarias"] if recs else []):
        if not a.get("entidad_id"):
            continue
        a["ambitos"] = dict(a.get("ambitos") or [])
        adjudicatarias.append(a)
    sin_adjudicaciones = [n for n in (recs[0]["sin_adj"] if recs else [])
                          if n.get("entidad_id")]
    return {"rows": rows, "total": total, "shown": len(rows),
            "adjudicatarias": adjudicatarias,
            "sin_adjudicaciones": sin_adjudicaciones}


def boe_layer(entity_id: str | None = None, limit: int = 25) -> dict:
    """The BOE overlay (spec 049): every aggregated APPOINTED_TO edge with
    source_kind='boe' (person → public body; one edge per office tenure with its
    citable BOE dispositions — fecha_nombramiento/fecha_cese, never in /flujos nor
    the cronología by construction), PLUS the layer's props-only data: persons
    marked `is_boe` (trajectory aggregate + incidencias without edges). A null
    fecha_cese means "no cese in this layer" (live office or cese outside the recon
    seed) — never word it as "vigente". Optionally scoped to one entity (either
    endpoint or a props-only person). Returns `{rows, total, shown, personas}`."""
    cypher = """
    OPTIONAL MATCH (n) WHERE coalesce(n.is_boe, false) AND """ + _served("n") + """
      AND ($entity IS NULL OR n.entity_id = $entity)
    WITH collect({
        persona: n.name, persona_id: n.entity_id,
        n_cargos: n.boe_n_cargos, rango: n.boe_rango,
        nota: n.boe_nota, incidencias: n.boe_incidencias}) AS personas
    OPTIONAL MATCH (a)-[r:APPOINTED_TO]->(b)
    WHERE r.source_kind = 'boe'
      AND ($entity IS NULL OR a.entity_id = $entity OR b.entity_id = $entity)
    WITH personas, collect(CASE WHEN r IS NULL THEN NULL ELSE {
        persona: a.name, persona_id: a.entity_id,
        organismo: b.name, organismo_id: b.entity_id,
        cargo: r.cargo,
        fecha_nombramiento: r.fecha_nombramiento,
        boe_id_nombramiento: r.boe_id_nombramiento,
        url_nombramiento: r.url_nombramiento,
        fecha_cese: r.fecha_cese, boe_id_cese: r.boe_id_cese,
        url_cese: r.url_cese, nota: r.note,
        boe_key: r.boe_key} END) AS rows0
    WITH personas, [x IN rows0 WHERE x IS NOT NULL] AS rows
    WITH personas, rows, size(rows) AS total
    UNWIND (CASE WHEN size(rows) = 0 THEN [NULL] ELSE rows END) AS row
    WITH personas, total, row
    ORDER BY row.persona, coalesce(row.fecha_nombramiento, row.fecha_cese)
    LIMIT $limit
    RETURN row, total, personas
    """
    with get_driver().session() as s:
        recs = s.run(Query(cypher, timeout=15), entity=entity_id, limit=limit).data()
    rows = [r["row"] for r in recs if r["row"] is not None]
    total = recs[0]["total"] if recs else 0
    personas = [p for p in (recs[0]["personas"] if recs else [])
                if p.get("persona_id")]
    return {"rows": rows, "total": total, "shown": len(rows), "personas": personas}


def ping() -> bool:
    try:
        with get_driver().session() as s:
            s.run("RETURN 1").single()
        return True
    except Exception:  # noqa: BLE001
        return False
