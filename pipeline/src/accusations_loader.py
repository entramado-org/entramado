"""Load the curated ACCUSED_OF overlay into Neo4j.

The extractor both over- and under-attributes accusations: it wires context
figures to crimes AND misses real charges —
either because a chunk used "la investigada" instead of the name, or because the
penal category was dropped by cleanup's CRIME_ALLOWLIST (e.g. "corrupción en los
negocios", art. 286 bis). This overlay declares the CORRECT, document-grounded
charge set per defendant, authoritatively and durably.

It MERGEs the Crime node + a `source_kind='acusacion'` ACCUSED_OF edge per charge,
each carrying its provenance (doc + article + verbatim quote); idempotent, never
overwrites a document-sourced edge. For a listed defendant the curated set is
**authoritative**: extracted (non-curated) ACCUSED_OF edges to crimes *outside*
that set are pruned (the spurious leakage the extractor produces — e.g. Begoña
wired to cohecho) — and so are the CURATED edges whose (crime, type) pair left the
YAML, so a charge that turns from condena into absolución doesn't keep both. Context
/ press edges are always kept. `--no-prune` keeps it purely additive. Re-running (or
a re-ingest + `make accusations`) always restores the curated truth.

GROUND RULE (presumption of innocence): every curated charge cites a real corpus
document + article + quote. These are *acusaciones/imputaciones* — alleged, in an
open procedure — exactly as the source states; never presented as proven facts.

REGISTRO DE ESTADO PROCESAL (spec 061): un defendant puede declarar `estado:
{status, fecha, fuente: {doc|url, tipo}, revisado, nota}`. Es la fuente
AUTORITATIVA de su fase procesal: tras cargar la capa curada, ese status (con su
fecha y su fuente) se propaga a las ACCUSED_OF de esa persona — extraídas
judiciales incluidas —, que es donde estaba el defecto sistémico (aristas sin
`status` que la web presentaba como «acusado»). Sin fila, la API muestra
«investigado». `acusado`/`procesado` exigen fuente de acto acusatorio formal.
La propagación NO pisa un `status:` declarado en la fila del delito
(`status_explicit`) ni una disposición ya cerrada (absuelto/archivado): quien está
investigado por un cargo y absuelto de otro conserva las dos verdades.

Usage:
    cd pipeline && uv run python -m src.accusations_loader
    cd pipeline && uv run python -m src.accusations_loader --dir ../some/dir
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import structlog
import yaml

from . import config, neo4j_loader
from .documents_loader import sentencia_firme_map
from .entity_resolution import entity_id
from .ontologia import ontologia
from .schema import RelationType

log = structlog.get_logger()

ACCUSATIONS_DIR = config.WORK_DIR / "accusations"
SOURCE_KIND = "acusacion"

_ONT = ontologia()
# Procedural stage of a charge (presunción de inocencia: most are open/alleged). The vocabulary
# is the ontology's (spec 069). A conviction is written `condenado` or `condenado_no_firme`; the
# loader DERIVES the published one from documents.yaml `sentencia_firme` and aborts when the
# registry says `condenado_no_firme` of a sentence declared firm.
_STATUSES = set(_ONT.estados)
_CONDENA = set(_ONT.estados_condena)
# Sin acto acusatorio declarado nadie es «acusado»: el default es la fase más leve.
_DEFAULT_STATUS = "investigado"
# A curated charge is normally an open accusation; only a firm sentence is CONVICTED_OF.
_REL_TYPES = {RelationType.ACCUSED_OF.value, RelationType.CONVICTED_OF.value}

# --- Registro de estado procesal por persona (spec 061) -----------------------
# `estado:` es AUTORITATIVO sobre las aristas de acusación extraídas: sin fila, la
# API sirve «investigado» (nunca «acusado»); con fila, se propaga su status + su
# fuente a TODAS las ACCUSED_OF de esa persona. Por eso valida fail-closed.
_FUENTE_TIPOS = {"escrito_acusacion", "auto_apertura_juicio_oral", "auto_procesamiento",
                 "sentencia", "auto", "providencia", "prensa"}
# Solo un acto acusatorio formal permite decir «acusado»/«procesado» de alguien.
_TIPOS_ACUSATORIOS = {"escrito_acusacion", "auto_apertura_juicio_oral", "auto_procesamiento"}
# La prensa nunca basta para una fase avanzada; y cuando basta, la API lo marca
# como «según prensa» a través de status_fuente_tipo.
_TIPO_PRENSA_STATUSES = set(_ONT.estados_prensa)
# What source each status needs comes from its ontology card (`exige_fuente`), not from its phase:
# procesado/acusado need a formal accusatory act; condena_anulada, the annulling sentence.
_TIPOS_EXIGIDOS = {"acto_acusatorio": _TIPOS_ACUSATORIOS, "sentencia": {"sentencia"}}
_FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# La prensa/última hora reportan el estado que dice su medio: el registro no las pisa.
_ESTADO_KINDS_EXCLUIDOS = ["ultima_hora", "prensa"]
# Disposiciones cerradas: el registro nunca las reabre ni las pisa (revisión T0d).
_CLOSED_STATUSES = [s for s in _ONT.estados if s in _ONT.estados_cerrados]
_OPEN_STATUSES = [s for s in _ONT.estados if s in _ONT.estados_abiertos]


def _crime_name(person: str, c: dict) -> str:
    """Canonical crime name of a registry row (spec 069), resolved with its `article:` when the
    name is ambiguous. The registry is curated: a name with no canonical form aborts the load."""
    raw = (c.get("name") or "").strip()
    res = _ONT.validar("delitos", raw, articulo=c.get("article"))
    if not res.ok:
        raise SystemExit(f"accusations[{person}]: {res.motivo} — usa un delito de la ontología "
                         "(pipeline/data/ontologia/ontologia.yaml) o añade su `article:` — spec 069")
    return res.valor


def _firmeza(person: str, declarado: str, doc: str, firme: bool) -> str:
    """Published conviction status: documents.yaml decides firmness (spec 059). The registry may
    say `condenado_no_firme` (spec 069) but never of a sentence declared firm."""
    if declarado == "condenado_no_firme" and firme:
        raise SystemExit(f"accusations[{person}]: `condenado_no_firme` pero documents.yaml declara "
                         f"firme {doc!r} — spec 069")
    return "condenado" if firme else "condenado_no_firme"


def load_files(directory: Path) -> list[dict]:
    """Read every *.yaml/*.yml; each has a top-level `accusations:` list."""
    out: list[dict] = []
    for f in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        items = doc.get("accusations") or []
        out.extend(items)
        log.info("accusations_file", file=f.name, defendants=len(items))
    return out


def _parse_estado(entry: dict) -> dict | None:
    """Valida y normaliza el bloque `estado:` de un defendant (spec 061).

    Devuelve None si no hay bloque (el campo legacy `status:` sigue mandando).
    Cualquier bloque incompleto o incoherente ABORTA la carga: un estado procesal
    mal declarado es exactamente el daño que la spec 061 viene a evitar."""
    est = entry.get("estado")
    person = (entry.get("person") or entry.get("entity_id") or "?").strip()
    if est is None:
        return None
    if not isinstance(est, dict):
        raise SystemExit(f"accusations[{person}]: `estado` debe ser un bloque, no {type(est).__name__}")

    def bad(msg: str) -> SystemExit:
        return SystemExit(f"accusations[{person}]: estado — {msg} (spec 061)")

    status = (est.get("status") or "").strip().lower()
    if status not in _STATUSES:
        raise bad(f"status {status!r} fuera de {sorted(_STATUSES)}")
    fecha = str(est.get("fecha") or "").strip()
    if not _FECHA_RE.match(fecha):
        raise bad("falta `fecha` del acto procesal en formato YYYY-MM-DD")
    revisado = str(est.get("revisado") or "").strip()
    if not _FECHA_RE.match(revisado):
        raise bad("falta `revisado` (última revisión humana) en formato YYYY-MM-DD")
    fuente = est.get("fuente")
    if not isinstance(fuente, dict):
        raise bad("falta `fuente: {doc|url, tipo}`")
    doc, url = (fuente.get("doc") or "").strip(), (fuente.get("url") or "").strip()
    if bool(doc) == bool(url):
        raise bad("`fuente` necesita exactamente uno de `doc` (PDF del corpus) o `url`")
    if url and not url.startswith("https://"):
        raise bad(f"`fuente.url` debe ser https:// ({url!r})")
    tipo = (fuente.get("tipo") or "").strip().lower()
    if tipo not in _FUENTE_TIPOS:
        raise bad(f"`fuente.tipo` {tipo!r} fuera de {sorted(_FUENTE_TIPOS)}")
    exige = _ONT.fuente_exigida[status]
    if exige in _TIPOS_EXIGIDOS and tipo not in _TIPOS_EXIGIDOS[exige]:
        raise bad(f"status {status!r} exige como fuente {sorted(_TIPOS_EXIGIDOS[exige])}, no {tipo!r}")
    if tipo == "prensa" and status not in _TIPO_PRENSA_STATUSES:
        raise bad(f"una fuente de prensa solo sostiene {sorted(_TIPO_PRENSA_STATUSES)}, no {status!r}")
    return {"status": status, "fecha": fecha, "fuente": doc or url, "fuente_tipo": tipo,
            "revisado": revisado, "nota": (est.get("nota") or "").strip() or None}


def _prepare_estados(accusations: list[dict],
                     firme_by_doc: dict[str, bool] | None = None) -> list[dict]:
    """Filas de propagación (una por defendant con `estado`), resueltas a entity_id.

    Revisión T0d (2026-09-15), dos reglas más, ambas fail-closed:
    · `status: condenado` en el registro exige `fuente.tipo: sentencia` y un `doc` del
      corpus con `sentencia_firme` declarada (documents.yaml, spec 059): lo que se
      propaga a las ACCUSED_OF es 'condenado' solo si es firme y, si no,
      'condenado_no_firme' — nunca una condena recurrida vestida de firme.
    · `closed`: los delitos de la fila con un status CERRADO propio (absuelto/
      archivado) viajan con la fila para que la propagación aplique ESA disposición
      a las aristas extraídas del mismo delito (quien está condenado por un cargo y
      absuelto de otro no puede publicarse como condenado en los dos)."""
    rows: list[dict] = []
    for a in accusations:
        estado = _parse_estado(a)
        if estado is None:
            continue
        person = (a.get("person") or "").strip()
        pid = (a.get("entity_id") or "").strip() or (entity_id("Person", person) if person else "")
        if not pid:
            raise SystemExit(f"accusations: bloque `estado` sin persona resoluble — {a!r}")
        if estado["status"] in _CONDENA:
            firme_by_doc = sentencia_firme_map() if firme_by_doc is None else firme_by_doc
            doc = estado["fuente"]
            if estado["fuente_tipo"] != "sentencia" or doc not in firme_by_doc:
                raise SystemExit(
                    f"accusations[{person}]: estado `{estado['status']}` exige `fuente: {{doc: <sentencia del "
                    f"corpus>, tipo: sentencia}}` con `sentencia_firme` declarada en documents.yaml "
                    f"(spec 059); recibido {doc!r} / {estado['fuente_tipo']!r}")
            estado = {**estado, "status": _firmeza(person, estado["status"], doc, firme_by_doc[doc])}
        closed = []
        for c in a.get("crimes") or []:
            cstatus = (c.get("status") or "").strip().lower()
            if not (c.get("name") or "").strip() or not cstatus:
                continue
            cname = _crime_name(person, c)
            if cstatus not in _CLOSED_STATUSES:
                raise SystemExit(
                    f"accusations[{person}]: con `estado`, un status por delito solo puede ser una "
                    f"disposición cerrada ({_CLOSED_STATUSES}), no {cstatus!r} («{cname}») — spec 061")
            closed.append({"crime": entity_id("Crime", cname), "status": cstatus,
                           "doc": c.get("doc") or a.get("doc")})
        rows.append({"person_id": pid, "person_name": person, **estado, "closed": closed})
    return rows


def _prepare(accusations: list[dict], firme_by_doc: dict[str, bool] | None = None) -> list[dict]:
    """Flatten to per-(person, crime) edge rows. Resolves entity_ids the same way
    the pipeline does, so curated crimes coincide with extracted ones.

    Spec 059 — firmeza fail-closed: a `status: condenado` charge must cite a doc
    whose `sentencia_firme` is declared in documents.yaml; the stored status is then
    'condenado' (firme) or 'condenado_no_firme'. An undeclared doc aborts the load
    rather than shipping a non-firm conviction as firm."""
    firme_by_doc = sentencia_firme_map() if firme_by_doc is None else firme_by_doc
    edges: list[dict] = []
    for a in accusations:
        person = (a.get("person") or "").strip()
        pid = (a.get("entity_id") or "").strip() or (entity_id("Person", person) if person else "")
        default_doc = a.get("doc")
        if not pid:
            log.warning("accusation_skip_no_person", entry=a)
            continue
        # El registro `estado` (spec 061) manda sobre el campo legacy `status:`;
        # un `status:` por delito sigue ganando (p. ej. un solo cargo absuelto).
        estado = _parse_estado(a)
        default_status = (estado["status"] if estado
                          else (a.get("status") or _DEFAULT_STATUS).strip().lower())
        for c in a.get("crimes") or []:
            if not (c.get("name") or "").strip():
                continue
            cname = _crime_name(person, c)
            # Un `status:` en la FILA DEL DELITO es una disposición propia de ese
            # cargo (p. ej. absuelto de uno y acusado de otro): se marca para que
            # la propagación del registro `estado` no lo pise después (spec 061).
            status_explicit = bool((c.get("status") or "").strip())
            status = (c.get("status") or default_status).strip().lower()
            # Un status desconocido NO cae al default: publicaría una fase inventada.
            if status not in _STATUSES:
                raise SystemExit(
                    f"accusations[{person}]: status {status!r} fuera de {sorted(_STATUSES)} («{cname}»)")
            # A condena is modelled with CONVICTED_OF; everything else stays ACCUSED_OF.
            rel_type = (c.get("type") or "").strip().upper() or (
                "CONVICTED_OF" if status in _CONDENA else "ACCUSED_OF")
            if rel_type not in _REL_TYPES:
                rel_type = "ACCUSED_OF"
            # Un `type: CONVICTED_OF` escrito a mano no puede vestir de condena una
            # disposición cerrada: una absolución no se publica como condena.
            if status in _CLOSED_STATUSES and rel_type == "CONVICTED_OF":
                raise SystemExit(
                    f"accusations[{person}]: «{cname}» tiene status cerrado {status!r} pero "
                    "type CONVICTED_OF — spec 061")
            doc = c.get("doc") or default_doc
            # La firmeza de la condena se calcula con el doc del delito; si el registro
            # `estado` cita otro, los dos caminos darían firmezas distintas.
            if estado and estado["status"] in _CONDENA and rel_type == "CONVICTED_OF" \
                    and doc != estado["fuente"]:
                raise SystemExit(
                    f"accusations[{person}]: la condena por «{cname}» cita {doc!r} pero el registro "
                    f"`estado` cita {estado['fuente']!r}: la firmeza se calcularía con dos "
                    "documentos distintos — spec 061")
            firme = None
            if rel_type == "CONVICTED_OF":
                if doc not in firme_by_doc:
                    raise SystemExit(
                        f"accusations: {person} condenado por «{cname}» cita {doc!r}, cuya "
                        "firmeza no está declarada (documents.yaml: sentencia_firme) — spec 059")
                firme = firme_by_doc[doc]
                status = _firmeza(person, status, doc, firme)
            edges.append({
                "person_id": pid,
                "person_name": person,
                "crime_id": entity_id("Crime", cname),
                "crime_name": cname,
                "rel_type": rel_type,
                "status": status,
                # None (no False) para que APOC BORRE la marca si el YAML deja de
                # declarar el status por delito — idempotente en ambos sentidos.
                "status_explicit": True if status_explicit else None,
                "firme": firme,
                "doc": doc,
                "article": c.get("article"),
                "page": c.get("page"),
                "quote": (c.get("quote") or "").strip()[:300] or None,
            })
    return edges


def load(driver, edges: list[dict]) -> None:
    # Person: only ensure it exists (never clobber a real document figure).
    # Crime: MERGE on entity_id so a curated charge coincides with any extracted
    # Crime of the same canonical name, and otherwise creates the missing one.
    # Edge: keyed by source_kind='acusacion' so it never collides with a judicial
    # / contexto / prensa ACCUSED_OF of the same (person, crime).
    # Edge keyed by (rel_type, source_kind='acusacion'); `status` records the
    # procedural stage (imputado/acusado/condenado…) so the chat + UI never blur
    # an alleged charge with a proven one (presunción de inocencia).
    q = """
    UNWIND $rows AS row
    CALL apoc.merge.node(['Person'], {entity_id: row.person_id},
      {name: coalesce(row.person_name, row.person_id), canonical_name: coalesce(row.person_name, row.person_id),
       mentions: 0, degree: 0}, {}) YIELD node AS p
    CALL apoc.merge.node(['Crime'], {entity_id: row.crime_id},
      {name: row.crime_name, canonical_name: row.crime_name, mentions: 0, degree: 0}, {}) YIELD node AS c
    CALL apoc.merge.relationship(p, row.rel_type, {source_kind: 'acusacion'},
      {source_doc: row.doc, article: row.article, source_page: row.page, source_quote: row.quote,
       status: row.status, status_explicit: row.status_explicit, firme: row.firme},
      c,
      {source_doc: row.doc, article: row.article, source_page: row.page, source_quote: row.quote,
       status: row.status, status_explicit: row.status_explicit, firme: row.firme}) YIELD rel
    RETURN count(rel) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=edges).single()["n"]
    log.info("accusations_loaded", edges=len(edges), merged=n)


# Propagación del registro de estado (spec 061): el estado declarado por persona
# pisa el de TODAS sus ACCUSED_OF —extraídas judiciales incluidas—, que es donde
# vivía el defecto sistémico (220 aristas sin status → la web decía «acusado»).
# Nunca toca CONVICTED_OF (la firmeza la declara documents.yaml, spec 059) ni las
# aristas de prensa/última hora (reportan el estado que dice su medio, atribuido).
#
# DOS EXCEPCIONES MÁS, por delito (revisión adversarial de Codex, 2026-09-09):
#  · `status_explicit` — el YAML declaró el status EN LA FILA DEL DELITO: es una
#    disposición propia de ese cargo y el estado de la persona no la pisa (si no,
#    quien está investigado por un delito y ABSUELTO de otro se publicaba como
#    investigado en los dos).
#  · disposición cerrada — una arista ya `absuelto`/`archivado` no vuelve a una
#    fase abierta por el estado general de la persona. Absolver es definitivo;
#    reabrirlo tendría que declararlo el delito, no el registro.

# Guardas compartidas por la consulta de propagación y la de diagnóstico.
_ESTADO_ELIGIBLE = """
  (r.source_kind IS NULL OR NOT r.source_kind IN $excluded_kinds)
  AND coalesce(r.status_explicit, false) <> true
"""

# `row.closed` (delitos con disposición cerrada declarada en la fila) manda sobre la
# arista extraída de ESE delito: status cerrado + su doc; el resto recibe el estado
# general. Una arista ya cerrada no se toca nunca (ni para reabrirla ni para pisarla).
# `st` se calcula UNA vez y de ahí sale también `r.firme`: un status propagado de
# condena sin su firmeza dejaría la arista incoherente (la lee el lint L2).
_ESTADO_Q = f"""
UNWIND $rows AS row
MATCH (p {{entity_id: row.person_id}})
OPTIONAL MATCH (p)-[r:ACCUSED_OF]->(c:Crime)
WHERE {_ESTADO_ELIGIBLE}
  AND NOT coalesce(r.status,'') IN $closed_statuses
WITH row, r,
     coalesce(head([x IN row.closed WHERE x.crime = c.entity_id | x.status]), row.status) AS st,
     coalesce(head([x IN row.closed WHERE x.crime = c.entity_id | x.doc]), row.fuente) AS fuente
FOREACH (_ IN CASE WHEN r IS NULL THEN [] ELSE [1] END |
  SET r.status = st,
      r.status_fecha = row.fecha,
      r.status_fuente = fuente,
      r.status_fuente_tipo = row.fuente_tipo, r.status_revisado = row.revisado,
      r.firme = CASE st WHEN 'condenado' THEN true WHEN 'condenado_no_firme' THEN false ELSE null END)
RETURN row.person_id AS person_id, count(r) AS n
"""

# Solo lectura: cuántas aristas se respetan por llevar ya una disposición cerrada.
_ESTADO_KEPT_CLOSED_Q = f"""
UNWIND $rows AS row
MATCH (p {{entity_id: row.person_id}})-[r:ACCUSED_OF]->(:Crime)
WHERE {_ESTADO_ELIGIBLE}
  AND coalesce(r.status,'') IN $closed_statuses
RETURN row.person_id AS person_id, count(r) AS n
"""


def apply_estados(driver, rows: list[dict]) -> int:
    """SET del estado procesal declarado sobre las ACCUSED_OF de cada persona.
    Idempotente (reescribe los mismos valores). Devuelve las aristas tocadas.
    Respeta el status declarado por delito y las disposiciones ya cerradas."""
    if not rows:
        return 0
    params = {"rows": rows, "excluded_kinds": _ESTADO_KINDS_EXCLUIDOS,
              "closed_statuses": _CLOSED_STATUSES, "open_statuses": _OPEN_STATUSES}
    with driver.session() as s:
        kept = s.run(_ESTADO_KEPT_CLOSED_Q, **params).data()
        counts = s.run(_ESTADO_Q, **params).data()
    for k in kept:
        if k["n"]:
            log.info("accusation_estado_kept_closed", person=k["person_id"], edges=k["n"],
                     note="absuelto/archivado: el registro no la reabre ni la pisa")
    kept_total = sum(k["n"] for k in kept)
    total = sum(c["n"] for c in counts)
    for c in counts:
        log.info("accusation_estado_applied", person=c["person_id"], edges=c["n"])
    log.info("accusations_estados", defendants=len(rows), edges=total, kept_closed=kept_total)
    return total


def prune(driver, edges: list[dict]) -> int:
    """Make the curated set authoritative: for each listed defendant, delete the
    EXTRACTED (source_kind null/'judicial') ACCUSED_OF edges to crimes NOT in their
    curated set. Contexto/prensa edges are never touched.

    También poda las CURADAS (source_kind='acusacion') cuyo par (delito, tipo) ya no
    está en el YAML, para que la capa sea idempotente en los DOS sentidos: si una
    condena curada se convierte en absolución del mismo cargo, la vieja CONVICTED_OF
    no sobrevive junto a la nueva ACCUSED_OF."""
    keep: dict[str, set[str]] = {}
    keep_typed: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    for e in edges:
        keep.setdefault(e["person_id"], set()).add(e["crime_id"])
        keep_typed.setdefault(e["person_id"], set()).add(f'{e["crime_id"]}|{e["rel_type"]}')
        names[e["person_id"]] = e["person_name"]
    rows = [{"person_id": pid, "keep": sorted(cids), "keep_typed": sorted(keep_typed[pid])}
            for pid, cids in keep.items()]
    q = """
    UNWIND $rows AS row
    MATCH (p {entity_id: row.person_id})-[r:ACCUSED_OF]->(c:Crime)
    WHERE (r.source_kind IS NULL OR r.source_kind = 'judicial')
      AND NOT c.entity_id IN row.keep
    DELETE r RETURN count(r) AS n
    """
    q_curated = """
    UNWIND $rows AS row
    MATCH (p {entity_id: row.person_id})-[r]->(c:Crime)
    WHERE r.source_kind = 'acusacion' AND type(r) IN ['ACCUSED_OF','CONVICTED_OF']
      AND NOT (c.entity_id + '|' + type(r)) IN row.keep_typed
    DELETE r RETURN count(r) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
        n_curated = s.run(q_curated, rows=rows).single()["n"]
    log.info("accusations_pruned", defendants=len(rows), edges_removed=n)
    log.info("accusations_pruned_curated", defendants=len(rows), edges_removed=n_curated)
    return n + n_curated


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default=str(ACCUSATIONS_DIR))
    p.add_argument("--no-prune", action="store_true",
                   help="keep purely additive (don't remove spurious extracted charges)")
    args = p.parse_args()

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("accusations_dir_missing", dir=str(directory))
        return
    accusations = load_files(directory)
    edges = _prepare(accusations)
    estados = _prepare_estados(accusations)
    if not edges:
        log.warning("accusations_empty", dir=str(directory))
        return

    driver = neo4j_loader.get_driver()
    try:
        load(driver, edges)
        if not args.no_prune:
            prune(driver, edges)
        # Después del prune: no gastamos SETs en aristas que se van a borrar.
        apply_estados(driver, estados)
        neo4j_loader.compute_degree(driver)  # curated edges count toward degree
    finally:
        driver.close()
    log.info("accusations_done")


if __name__ == "__main__":
    main()
