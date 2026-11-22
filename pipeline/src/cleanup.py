"""One-shot graph cleanup + chronology enrichment.

Run after a full ingestion. Idempotent. Does four things:

1. Drops "topic" Crime nodes that name no crime at all (the LLM occasionally labels themes
   like "corrupción" or "delito" as Crime); a non-canonical crime name goes to the review queue.
2. Strips junk aliases (single chars, pronouns, dupes of canonical name)
   so fuzzy entity search isn't polluted by "S", "él", "jefe", etc.
3. Normalizes every relation `date` (Spanish or ISO) to `date_norm` (YYYY-MM-DD)
   and `date_year` (int) so the web can filter / build a timeline.
4. Recomputes `n.degree` after the deletions.

CLI: `uv run python -m src.cleanup`
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

import structlog

from . import config, neo4j_loader, revision
# La fecha la normaliza `alcance`, que también prepara el `set:` del overlay de relaciones (spec 071 AC77).
from .alcance import normalize_date as _normalize_date
from .documents_loader import transcript_doc_ids
from .ontologia import ontologia

log = structlog.get_logger()

# --- 1. Crimes: the ontology's canonical list (spec 069) ---
# A Crime node is kept when its name is a canonical crime of pipeline/data/ontologia/ontologia.yaml.
# A name that LOOKS like a crime («delito de cohecho», a synonym, an ambiguous name) is kept and sent
# to the review queue so a person maps it (a Path A migration renames it); only a topic that names no
# crime at all («corrupción», «delito») is deleted as before.
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    return _strip_accents(s or "").strip().lower()


def _crime_is_real(name: str) -> bool:
    """Canonical crime of the ontology (exact name or synonym, never a substring)."""
    return ontologia().validar("delitos", name).ok


# --- Generic-noun Event blocklist ---
# The extractor often labels a bare generic noun ("contrato", "registro", "software",
# "SWIFT", "emergencia", "audiencia"…) as an :Event. These are extraction NOISE: every
# edge to them is co-occurrence boilerplate (X PARTICIPATED_IN contrato / X OWNS software),
# the object being a generic word rather than a named referent. Each real attribution lives
# on the actor's own node + the source :Chunk text, so deleting the generic Event node loses
# nothing. Unlike the Crime case there is no finite allowlist of "real events", so this is a
# BLOCKLIST matched by EXACT normalized name (NOT substring — "Reunión del Consejo del 3-mar"
# must survive even though it contains "reunión"). Curated against the live graph + verified
# edge-by-edge (2026-06-20; see data/audit/patches/manual_fix_generic_events_*). NB: this
# also covers "cuenta bancaria", which JUNK_NODE_NAMES lists as "Cuenta bancaria" but
# _delete_junk_nodes never matched (its `n.name IN $names` is case-sensitive; the live node
# is lower-case).
GENERIC_EVENT_NAMES = [
    "contrato", "registro", "reclamación", "software", "audiencia", "cuenta bancaria",
    "SWIFT", "correo electrónico", "auditoría", "cambio de divisas", "reintegros",
    "ingresos en efectivo", "autorización judicial", "diligencias", "expediente",
    "documentación", "reunión", "volcado", "licitación", "documento de identificación",
    "transferencias", "subsecretaria", "prisión provisional", "clonado", "obra pública",
    "cheques", "cadena de custodia", "emergencia",
    # plural/singular + multi-word variants of the same noise (low-degree tail)
    "contratación", "contratación pública", "contrataciones directas", "contrato de comisionista",
    "actividad contratada", "prohibición de contratar", "licitaciones", "reuniones",
    "reunión de trabajo", "diligencias previas", "documentación bancaria", "correos institucionales",
    "correos particulares", "número de cuenta", "cuentas corrientes", "apertura de cuenta",
    "bloqueo de cuenta", "bloqueo de cuentas", "cancelación de cuenta", "dación de cuenta",
    "depósitos de cuentas", "transferencia bancaria", "autorizaciones administrativas",
    "autorización de vuelos", "obra de emergencia", "8 expedientes", "tres reclamaciones",
    # Revisión caso-a-caso de proper-noun dudosos (2026-06-20): "Telecomunicaciones" es una
    # palabra-tema (0 aristas entidad-entidad); "rescate" conflaciona el rescate de Air Europa
    # con material de rescate de montaña — el hecho real vive en Event:rescate-de-air-europa +
    # Company:air-europa. ("Panadero" SÍ es un proyecto real con nombre propio → se conserva.)
    "Telecomunicaciones", "rescate",
    # Residuo post-Option D (2026-07-09): tras retirar las aristas de transcripciones (PR #157),
    # a Event:trama solo le quedaba 1 arista judicial de co-ocurrencia ("a través de la trama",
    # auto-zapatero p.34) — sustantivo genérico sin referente, mismo patrón que los 56 de arriba.
    "trama",
]
_GENERIC_EVENT_NAMES_N = {_norm(x) for x in GENERIC_EVENT_NAMES}


def _event_name_is_generic(name: str) -> bool:
    return _norm(name) in _GENERIC_EVENT_NAMES_N


# --- Boilerplate :LegalCase filter (spec 024) ---
# The LLM freely assigns the `LegalCase` type with no negative guidance, so it
# materialises hundreds of nodes that are NOT cases: statute/regulation citations
# (`Real Decreto 54/05`, `Ley de Enjuiciamiento Criminal`, `Código Penal`), article
# citations (`artículo 428`), and bare procedural terms (`auto de imputación`, `causa`,
# `expediente`, `proceso judicial`). Each drags false `PARTY_TO` edges. Unlike the
# generic-Event case this is a PATTERN filter (not an exact-name blocklist): the space
# of boilerplate is open-ended. The predicate is shared with extract_facts._lenient_validate
# so the junk never even reaches the facts cache. Validated against pipeline/data/facts/*.jsonl
# (266 distinct / 558 mentions boilerplate) — see specs/024-boilerplate-legalcase/spec.md.
#
# CRITICAL: keep genuine judicial dockets / admin expedientes / named cases. A STATUTE with a
# number (`Ley 9/2017`) is still boilerplate — the number is the law's, not a docket — but a
# DOCKET (`Causa Especial 20775/2020`, `Diligencias Previas 1146/2024`, `044/20-ED`) is real.
_LC_STATUTE = re.compile(
    r"^(ley\b|ley\s+org|real\s+decreto|decreto[\-\s]|orden\s|orden\b|reglamento\b|"
    r"c[oó]digo\b|constituci[oó]n\b|directiva\b|instrucci[oó]n\s|texto\s+refundido|"
    r"presupuestos\s+generales|convenio\b|pacto\s+internacional|tratado\b|declaraci[oó]n\b)",
    re.I,
)
_LC_ACRONYMS = {"lecrim", "lecr", "lopj", "lcsp", "trlcsp", "irpf", "lgp", "lgt",
                "loreg", "lisos", "let", "lo", "cp"}
_LC_ARTICLE = re.compile(r"^\s*(art[íi]culo|art\.|artículos|articulos)\b", re.I)
_LC_PLACEHOLDER = re.compile(r"NUM0*\d|DIRECCION0*\d", re.I)
# procedural ACTS: keep only when they carry a docket-style number; a dated/bare one is noise.
_LC_PROC_ACT = re.compile(r"^\s*(auto|resoluci[oó]n|providencia)\b", re.I)
_LC_NUMBERED = re.compile(r"\d{1,5}\s*/\s*\d{2,4}|\bn[ºo°.]{0,2}\s*\d{2,}", re.I)
# genuine docket / expediente / code shapes → KEEP (N/AAAA, nºN, N-XX-N, N.N/…, long ids).
_LC_DOCKET = re.compile(
    r"\d{1,5}\s*/\s*\d{2,4}|\bn[ºo°.]{0,2}\s*\d+|\d+\s*[\-/]\s*\w{1,3}\s*[\-/]\s*\d+|"
    r"\d+\.\d+\s*/|\d{5,}",
    re.I,
)
_LC_GENERIC_NAMES = {
    "legalcase", "proceso judicial", "proceso", "proceso penal", "proceso penal ordinario",
    "causa", "causa especial", "causa judicial", "causa penal", "contrato menor", "contrato",
    "auto", "autos", "sumario", "sumario de la causa", "expediente", "expediente judicial",
    "procedimiento", "juicio oral", "pieza separada", "pieza", "diligencias previas",
    "diligencias", "diligencias de investigacion", "querella", "denuncia", "recurso",
    "providencia", "oficio", "atestado", "informe", "prision provisional",
    "autorizacion judicial", "mandamiento judicial", "delincuencia economica",
    "expediente de reclamacion",
}
_LC_GENERIC_PREFIX = re.compile(
    r"^\s*(evidencia\b|informe\s+policial\b|mandamiento\b|recurso\s+de\b|proceso\s+penal\b|"
    r"causa\s+(judicial|penal)\b|expediente\s+(judicial|de\s+reclamaci)|sumario\s+de\b|"
    r"diligencias\s+de\s+investigaci)",
    re.I,
)


def is_boilerplate_legalcase(name: str) -> bool:
    """True if `name` is a statute/article/generic-procedural phrase mislabelled as a
    :LegalCase node (spec 024). Conservative: any name carrying a genuine docket/expediente
    number (that isn't a statute) is kept. Shared with extract_facts._lenient_validate."""
    if not name:
        return False
    n = _norm(name)
    if n in _LC_ACRONYMS:
        return True
    if _LC_STATUTE.match(name) or _LC_ARTICLE.match(name):
        return True  # a statute/article is boilerplate even when numbered
    if _LC_PLACEHOLDER.search(name):
        return True
    if re.match(r"^\s*auto-", name, re.I):
        return False  # slug like auto-zapatero / auto-prision-cerdan-… → a real case doc
    if _LC_PROC_ACT.match(name):
        return not _LC_NUMBERED.search(name)  # numbered auto/resolución kept; dated/bare dropped
    if _LC_DOCKET.search(name):
        return False  # genuine docket / expediente / code
    if n in _LC_GENERIC_NAMES:
        return True
    if _LC_GENERIC_PREFIX.match(name):
        return True
    return False


# --- 2. Alias junk filter ---
_STOPWORD_ALIASES = {
    "el", "la", "lo", "los", "las", "un", "una", "uno", "unos", "unas",
    "él", "ella", "ellos", "ellas", "se", "su", "sus", "mi", "tu",
    "jefe", "jefa", "señor", "señora", "señorita", "sr", "sra", "srta",
    "don", "dona", "doña", "ministro", "ministra", "presidente", "presidenta",
    "excmo", "ilmo", "the", "and", "of", "ni", "y", "o", "que",
}


def _tokens(s: str) -> list[str]:
    """Significant tokens in order (>= 3 chars, alnum-only, accent-folded)."""
    return [t for t in re.findall(r"[a-z0-9áéíóúñü]+", _norm(s)) if len(t) >= 3]


def _alias_is_junk(alias: str, canonical: str, label: str) -> bool:
    if not alias or len(alias) <= 2:
        return True
    a = _norm(alias).rstrip(".:,;")
    if a in _STOPWORD_ALIASES:
        return True
    # pure digits, or "Sr."/"D." style
    if re.fullmatch(r"[\d.,:]+", a):
        return True
    # exact dupe of canonical (case-insensitive, accent-insensitive)
    if a == _norm(canonical):
        return True
    # For named entities (Person/Org/Company/Court), require either:
    #   - the alias is a substring/superstring of the canonical (e.g. "Koldo" for
    #     "Koldo García", "Koldo García Izaguirre" for "Koldo García"), OR
    #   - the alias shares the LAST significant token (typically the surname) with
    #     the canonical.
    # Matching only on a first-name token like "José" or "Pedro" is too permissive
    # and merges unrelated people into the canonical's alias list.
    if label in {"Person", "Organization", "Company", "Court"}:
        a_norm = _norm(alias)
        c_norm = _norm(canonical)
        if a_norm in c_norm or c_norm in a_norm:
            return False
        a_tokens = set(_tokens(alias))
        c_tokens_list = _tokens(canonical)
        if not c_tokens_list:
            return True
        if c_tokens_list[-1] not in a_tokens:
            return True
    return False


def _clean_aliases(driver):
    cypher_read = """
    MATCH (n) WHERE n.entity_id IS NOT NULL AND n.aliases IS NOT NULL
    RETURN n.entity_id AS id, labels(n)[0] AS lbl, n.name AS canonical, n.aliases AS aliases
    """
    updates: list[dict] = []
    with driver.session() as s:
        for r in s.run(cypher_read):
            canon = r["canonical"] or ""
            kept, seen = [], set()
            for a in r["aliases"] or []:
                a_clean = " ".join(a.split())  # also collapses OCR line breaks inside the alias
                if not a_clean or _alias_is_junk(a_clean, canon, r["lbl"]):
                    continue
                key = _norm(a_clean)
                if key in seen:
                    continue
                seen.add(key)
                kept.append(a_clean)
            if kept != (r["aliases"] or []):
                updates.append({"id": r["id"], "lbl": r["lbl"], "aliases": kept})

    # Group by label so we can match on the right label (entity_id is unique per label).
    by_label: dict[str, list[dict]] = {}
    for u in updates:
        by_label.setdefault(u["lbl"], []).append({"id": u["id"], "aliases": u["aliases"]})
    with driver.session() as s:
        for lbl, rows in by_label.items():
            s.run(
                f"UNWIND $rows AS row MATCH (n:{lbl} {{entity_id: row.id}}) SET n.aliases = row.aliases",
                rows=rows,
            )
    log.info("aliases_cleaned", entities_updated=len(updates))


# --- 3. Non-canonical crimes ---
def _queue_noncanonical_crimes(driver) -> int:
    """Spec 069: a Crime node whose name is not the canonical ontology form goes to the review
    queue; cleanup never deletes one. A name the ontology cannot place (ambiguous like «revelación
    de secretos», or unknown like «abuso de poder») may still be a real charge with accusatory
    edges hanging off it, so deleting it would be deciding alone. Merges run as a Path A migration
    (`gen_crime_canonical_migration.py`). Extraction already rejects non-canonical crimes, so
    new topic-Crimes cannot reach the graph."""
    ont = ontologia()
    queued = 0
    with driver.session() as s:
        for r in s.run("MATCH (c:Crime) RETURN c.entity_id AS id, c.name AS name"):
            res = ont.validar("delitos", r["name"])
            if res.ok and res.valor == r["name"]:
                continue
            if res.ok:
                motivo = "delito_sin_canonizar"
            elif ont.parece_delito(r["name"]):
                motivo = "delito_no_canonico"
            else:
                motivo = "delito_desconocido"
            revision.encolar("cleanup", motivo, {"entity_id": r["id"], "name": r["name"]},
                             detalle=res.valor or res.motivo, termino=r["name"])
            queued += 1
    log.info("noncanonical_crimes_queued", n=queued)
    return queued


def _delete_generic_events(driver) -> int:
    """Drop :Event nodes whose name is a generic noun (see GENERIC_EVENT_NAMES).
    Blocklist-based + exact normalized match."""
    cypher = "MATCH (e:Event) RETURN e.entity_id AS id, e.name AS name"
    to_delete: list[str] = []
    with driver.session() as s:
        for r in s.run(cypher):
            if _event_name_is_generic(r["name"]):
                to_delete.append(r["id"])
    if to_delete:
        with driver.session() as s:
            s.run(
                "UNWIND $ids AS id MATCH (e:Event {entity_id: id}) DETACH DELETE e",
                ids=to_delete,
            )
    log.info("generic_events_deleted", n=len(to_delete))
    return len(to_delete)


def _delete_boilerplate_legalcase(driver) -> int:
    """Drop :LegalCase nodes whose name is a statute / article / generic procedural phrase
    (spec 024), removing their false PARTY_TO edges. Pattern-based (is_boilerplate_legalcase),
    idempotent, durable — survives `make ingest` (which the exact-name JUNK_NODE_NAMES list
    did not cover generically). Genuine dockets/expedientes/named cases are preserved."""
    cypher = "MATCH (n:LegalCase) RETURN n.entity_id AS id, n.name AS name"
    to_delete: list[str] = []
    with driver.session() as s:
        for r in s.run(cypher):
            if is_boilerplate_legalcase(r["name"]):
                to_delete.append(r["id"])
    if to_delete:
        with driver.session() as s:
            s.run(
                "UNWIND $ids AS id MATCH (n:LegalCase {entity_id: id}) DETACH DELETE n",
                ids=to_delete,
            )
    log.info("boilerplate_legalcase_deleted", n=len(to_delete))
    return len(to_delete)


# --- 3b. High-precision hallucination cleanup ---
# Curated non-entities the extractor mislabels as nodes (laws / generic terms);
# every edge to them is noise.
CONCEPT_NODES = [
    "Código Penal",
    "Ley de Enjuiciamiento Criminal",
    "empresa",
]
# Known-bad entity pairs: relations hallucinated from noisy parliamentary
# transcripts whose quotes don't support them (e.g. Begoña Gómez ↔ PP). ALL
# edges between the pair are removed.
BAD_ENTITY_PAIRS = [
    ("Person:begona-gomez", "Organization:partido-popular"),
]
# Parliamentary debate transcripts (Diario de Sesiones): debate, not judicial
# proof. The 2026-07-02 read-only review of all 679 transcript-sourced edges
# (findings/aristas-transcripciones-parlamentarias) found them overwhelmingly
# defective — ~65% grounded in the repeated page-header boilerplate, ~26% Q&A
# co-mention (ASSOCIATED_WITH), ~24% pointing at non-actor Event/Location/Crime
# nodes, plus rhetorical/accusatory claims and misattributions — and the few
# genuine facts are redundant with the judicial sources. Owner decision "Option D":
# keep the docs consultable (Document + Chunks + MENTIONED_IN are untouched) but
# remove ALL their entity-entity edges. The authoritative doc list is derived from
# documents.yaml doc_type (see documents_loader.transcript_doc_ids); this hardcoded
# list is only a fallback and now lists all 5 (the old 3-of-5 list let the two 2026
# SEPI transcripts slip the cleanup entirely). Genuine party membership etc. lives
# in the curated context/press layers, which carry no source_doc and are untouched.
TRANSCRIPT_DOCS = [
    "DSCD-15-CI-8.pdf", "DS_C_15_103.pdf", "DS_C_15_233.pdf",
    "DS_C_15_659.pdf", "DS_C_15_717.pdf",
]
_EXCL_PROV = ["MENTIONED_IN", "PART_OF"]
# Placeholder "quotes" the extractor emitted instead of real source text — the
# accusation was invented, never grounded in the document. Cheap deterministic
# backstop; the graph audit (spec 063) judges the rest against the source.
PLACEHOLDER_QUOTES = ["cita textual", "cita breve", "sin cita", "texto del documento"]


# Non-entity "nodes" the extractor invents: bare years/dates, forensic file
# names from the phone dump, and a few generic references. They cluster into the
# centre ("otros") and add noise. Unlike the per-edge LLM verdicts, these WOULD
# reappear on re-ingest, so the rule is baked here (idempotent, durable).
JUNK_NODE_NAMES = [
    "Norma 43", "NORMA 43", "Ten-T", "TEN-T", "Instalaciones de seguridad",
    "ChalStorage sqlite",  # OCR-mangled WhatsApp ChatStorage.sqlite (phone dump)
    "Actuaciones en estaciones protección de pasos entre andenes",  # railway/TEN-T noise
    "Consejo Superior Bancario",  # irrelevant defunct-org extraction
    "WhatsApp",  # messaging app treated as an actor (PARTICIPATED_IN noise), not an entity
    "UTE",  # generic "Unión Temporal de Empresas" form — many real UTEs collapsed into one
    # Forensic artifacts: phone-dump DB tables / files mistaken for entities
    "ZWA_MESSAGE", "ZWAMESSAGE",  # WhatsApp message table from a phone extraction
    "CloudRecordings.db", "CloudRecordings.db-wal", "CloudRecordings.dbwal",  # sqlite WAL files
    # Type-name / generic-role placeholders the LLM emitted as nodes (no real identity)
    "Person", "MoneyAmount", "Money Amount",
    "ministro", "funcionario", "otra persona",
    "personas autorizadas", "personal laboral", "personal eventual",
    # Role/office placeholders + phone identifiers flagged is_noise by the
    # enrichment swarm (2026-05-30). NOT real named entities. (Persons like
    # Fermín/Alfonso/Jaime and acronyms OMF/DGOI were withheld — real or homonyms.)
    "Cuenta bancaria", "mesa de contratación", "subsecretario",
    "Ministro de Fomento", "Secretario General de PdE",
    "Administración de Justicia", "investigados", "Servicios Centrales",
    # (phone numbers, WhatsApp JIDs and e-mail addresses as node names are matched by the regex clauses in the
    # Cypher below: an identifier never goes in this list)
    # Reviewed batch: OMF = unidentifiable OCR sigla (source probe.bin), edges only
    # to generic cuenta/transferencias; DGOI = office acronym ("Director General de
    # Organización e Inspección") mistyped as a Person, like the placeholders above.
    "OMF", "DGOI",
    # "S": conflated non-entity — a scoring-formula variable in informe_guardia_civil.pdf
    # ("… + S, PE: Puntuación …") plus an UNIDENTIFIED transcript voice talking to Koldo
    # (it addresses "KOLDO"), so it is neither Koldo nor provably Santos Cerdán. Drop it.
    "S",
    # Law citations + procedural placeholders the extractor mislabels as entities (and
    # splits across Organization/LegalCase/Court labels), like CONCEPT_NODES above. The
    # new UDEF/Plus Ultra docs introduced several (2026-05-31 dedup sweep).
    "Ley del Impuesto sobre la Renta de las Personas Físicas",
    "Ley Orgánica 5/1984", "Ley 38/2002", "Ley Orgánica 15/1999",
    "Magistrado-Instructor", "acuse popular",
]


def _delete_junk_nodes(driver) -> int:
    cypher = """
    MATCH (n) WHERE n.entity_id IS NOT NULL AND (
        n.name =~ '^(19|20)\\\\d{2}$' OR
        n.name =~ '^\\\\d{1,2}[/.\\\\-]\\\\d{1,2}[/.\\\\-]\\\\d{2,4}$' OR
        n.name =~ '^\\\\d{4}[/.\\\\-]\\\\d{2}[/.\\\\-]\\\\d{2}$' OR
        toLower(n.name) =~ '.*[ ._\\\\-](sqlite|db|sql|csv|xlsx|xls|docx|doc|txt|json|xml|bak)$' OR
        toLower(n.name) CONTAINS 'chalstorage' OR
        n.name =~ '^\\\\+?\\\\d{9,15}(@s\\\\.whatsapp\\\\.net)?$' OR
        n.name =~ '^[^\\\\s@]+@[^\\\\s@]+\\\\.[A-Za-z]{2,}$' OR
        n.name IN $names
    )
    DETACH DELETE n RETURN count(n) AS c
    """
    with driver.session() as s:
        c = s.run(cypher, names=JUNK_NODE_NAMES).single()["c"]
    log.info("junk_nodes_deleted", n=c)
    return c


def _delete_hallucinations(driver) -> dict:
    """Remove high-confidence hallucinated nodes/edges only. Deliberately narrow:
    structural type-rules OVER-match (most loosely-typed ACCUSED_OF / WORKS_FOR
    edges are legitimate accusations / participations), so we drop only the
    *impossible* cases, never merely atypical ones."""
    stats: dict[str, int] = {}
    with driver.session() as s:
        # (a) concept / non-entity nodes + all their edges
        r = s.run("MATCH (n) WHERE n.name IN $names DETACH DELETE n RETURN count(n) AS c",
                  names=CONCEPT_NODES).single()
        stats["concept_nodes"] = r["c"] if r else 0
        # (b) FAMILY_OF where an endpoint isn't a Person (impossible kinship)
        r = s.run("MATCH (a)-[r:FAMILY_OF]->(b) WHERE NOT 'Person' IN labels(a) OR NOT 'Person' IN labels(b) "
                  "DELETE r RETURN count(r) AS c").single()
        stats["bad_family"] = r["c"] if r else 0
        # (c) entity-entity self-loops
        r = s.run(f"MATCH (a)-[r]->(a) WHERE a.entity_id IS NOT NULL AND NOT type(r) IN {_EXCL_PROV} "
                  "DELETE r RETURN count(r) AS c").single()
        stats["self_loops"] = r["c"] if r else 0
        # (d) curated bad entity pairs
        n = 0
        for src, tgt in BAD_ENTITY_PAIRS:
            r = s.run("MATCH (a {entity_id:$s})-[r]-(b {entity_id:$t}) DELETE r RETURN count(r) AS c",
                      s=src, t=tgt).single()
            n += r["c"] if r else 0
        stats["bad_pairs"] = n
        # (e) ALL entity-entity edges from parliamentary transcripts (debate, not
        #     proof — Option D). Document/Chunk/MENTIONED_IN kept: docs stay
        #     consultable via /documentos + buscar_documentos + leer_documento.
        tdocs = list(transcript_doc_ids()) or TRANSCRIPT_DOCS
        r = s.run(
            "MATCH (a)-[r]->(b) WHERE r.source_doc IN $docs "
            "AND a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL "
            "AND NOT type(r) IN $excl DELETE r RETURN count(r) AS c",
            docs=tdocs, excl=_EXCL_PROV,
        ).single()
        stats["transcript_edges"] = r["c"] if r else 0
        # (f) ACCUSED_OF whose quote is a model placeholder (fabricated accusation):
        #     the no-model backstop.
        r = s.run(
            "MATCH (:Person)-[r:ACCUSED_OF]->(:Crime) WHERE r.source_quote IS NOT NULL "
            "AND any(p IN $ph WHERE toLower(r.source_quote) CONTAINS p) "
            "DELETE r RETURN count(r) AS c",
            ph=PLACEHOLDER_QUOTES,
        ).single()
        stats["placeholder_accusations"] = r["c"] if r else 0
    log.info("hallucinations_deleted", **stats)
    return stats


def _delete_transcript_orphans(driver) -> int:
    """Drop entity nodes left with ZERO entity-entity edges whose every MENTIONED_IN
    points into a parliamentary transcript — the leftovers of the transcript-edge
    retirement (Option D, e.g. Event:operacion-delorme: all its edges came from the
    committee-title boilerplate, so removing them dangles the node). Both conditions
    are required, so a mention-only node that appears in ANY non-transcript document
    survives. Runs after _delete_hallucinations (whose rule (e) removes the transcript
    edges), making a facts-cache re-ingest unable to resurrect the orphans."""
    tdocs = list(transcript_doc_ids()) or TRANSCRIPT_DOCS
    with driver.session() as s:
        r = s.run(
            "MATCH (n) WHERE n.entity_id IS NOT NULL "
            "AND NOT EXISTS { MATCH (n)-[r]-(m) WHERE m.entity_id IS NOT NULL "
            "                 AND NOT type(r) IN $excl } "
            "AND EXISTS { (n)-[:MENTIONED_IN]->(:Chunk) } "
            "AND NOT EXISTS { MATCH (n)-[:MENTIONED_IN]->(c:Chunk) "
            "                 WHERE NOT c.doc_id IN $docs } "
            "DETACH DELETE n RETURN count(n) AS c",
            docs=tdocs, excl=_EXCL_PROV,
        ).single()
    n = r["c"] if r else 0
    log.info("transcript_orphans_deleted", n=n)
    return n


# --- 4. Date normalization (`_normalize_date`, de `alcance`) ---
def _normalize_relation_dates(driver) -> dict:
    cypher_read = """
    MATCH ()-[r]->()
    WHERE r.date IS NOT NULL AND r.date_norm IS NULL
    RETURN elementId(r) AS rid, r.date AS raw
    """
    rows = []
    with driver.session() as s:
        for r in s.run(cypher_read):
            iso, year = _normalize_date(r["raw"])
            if iso or year:
                rows.append({"rid": r["rid"], "iso": iso, "year": year})
    if rows:
        with driver.session() as s:
            s.run(
                """
                UNWIND $rows AS row
                MATCH ()-[r]->() WHERE elementId(r) = row.rid
                SET r.date_norm = row.iso, r.date_year = row.year
                """,
                rows=rows,
            )
    log.info("dates_normalized", n=len(rows))
    return {"normalized": len(rows)}


# --- 5. Driver ---
def main():
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    driver = neo4j_loader.get_driver()
    try:
        crimes_queued = _queue_noncanonical_crimes(driver)
        generic_events_removed = _delete_generic_events(driver)
        boilerplate_legalcase_removed = _delete_boilerplate_legalcase(driver)
        _delete_junk_nodes(driver)
        _delete_hallucinations(driver)
        transcript_orphans_removed = _delete_transcript_orphans(driver)
        _clean_aliases(driver)
        _normalize_relation_dates(driver)
        # Recompute degree post-deletions.
        neo4j_loader.compute_degree(driver)
        log.info("cleanup_complete", crimes_queued=crimes_queued,
                 generic_events_removed=generic_events_removed,
                 boilerplate_legalcase_removed=boilerplate_legalcase_removed,
                 transcript_orphans_removed=transcript_orphans_removed)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
