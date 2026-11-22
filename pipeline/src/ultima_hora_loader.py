"""Load the curated ÚLTIMA HORA (breaking-news) overlay into Neo4j.

Fourth provenance tier, sibling of context/press/accusations. It captures
high-signal case DEVELOPMENTS that, as of now, exist only in the press —
sentence requests, new UCO findings, fresh indictments — for which there is
no downloadable official document yet (so they can't enter the judicial,
source-anchored graph).

How it differs from the `press` layer: press is strictly NEUTRAL biographical
facts and forbids alleged wrongdoing. Última hora DELIBERATELY allows
allegation-type developments (a per-edge `status`: investigado / imputado /
acusado …) BECAUSE it is kept fully separate — its own `source_kind`, its own
UI toggle, its own page and badge — and EVERY edge carries full provenance
(outlet + url + date + verbatim quote). Presumption of innocence always applies.

Tagged `source_kind="ultima_hora"` and `is_ultima_hora=true`. Idempotent:
nodes MERGE by deterministic `entity_id`; edges are keyed by `source_kind` so
re-running never duplicates and never overwrites a judicial / contexto / prensa
edge of the same (source, target, type). On an existing node it only *fills* a
missing role — it never clobbers judicial data nor flags that node.

CADUCIDAD (spec 061): una relación ACUSATORIA (ACCUSED_OF / INVESTIGATED_BY /
CONVICTED_OF) con `date` de hace más de 90 días NO se carga salvo que la persona
tenga fila en el registro de estado de `accusations.yaml`. Una imputación que solo
dijo un medio y que nadie refrescó en tres meses ya no es «última hora»: o el caso
avanzó — y entonces el estado real lo declara el registro — o la noticia se quedó
sin recorrido. El resto de la capa (MET_WITH, PAID, WORKS_FOR…) no se ve afectada.

Usage:
    cd pipeline && uv run python -m src.ultima_hora_loader
    cd pipeline && uv run python -m src.ultima_hora_loader --dir ../some/other/dir
    cd pipeline && uv run python -m src.ultima_hora_loader --as-of 2026-09-09
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import structlog
import yaml

from . import accusations_loader, config, neo4j_loader
from .entity_resolution import entity_id
from . import revision
from .ontologia import ontologia
from .schema import EntityType, RelationType

log = structlog.get_logger()

ULTIMA_HORA_DIR = config.WORK_DIR / "ultima_hora"
SOURCE_KIND = "ultima_hora"

_ENTITY_TYPES = {t.value for t in EntityType}
_REL_TYPES = {t.value for t in RelationType}

# --- Caducidad de las acusaciones de prensa (spec 061) ------------------------
# Una imputación que solo dijo un medio y que nadie ha refrescado en 3 meses deja
# de ser «última hora»: o el caso avanzó (y entonces el estado real vive en el
# registro de accusations.yaml) o la noticia se quedó sin recorrido. Publicarla
# indefinidamente señala a una persona con una fuente que ya nadie sostiene.
_ACCUSATION_RELS = set(ontologia().procesales)
_STALE_DAYS = 90


def registered_defendants(directory: Path | None = None) -> set[str]:
    """entity_ids con estado procesal registrado en `accusations.yaml`: fila con
    bloque `estado` (spec 061) o con el `status:` legacy citando un `doc`."""
    directory = accusations_loader.ACCUSATIONS_DIR if directory is None else directory
    ids: set[str] = set()
    if not directory.is_dir():
        log.warning("ultima_hora_accusations_dir_missing", dir=str(directory))
        return ids
    for a in accusations_loader.load_files(directory):
        if not (a.get("estado") or (a.get("status") and a.get("doc"))):
            continue
        name = (a.get("person") or "").strip()
        pid = (a.get("entity_id") or "").strip() or (entity_id("Person", name) if name else "")
        if pid:
            ids.add(pid)
    return ids


def _is_stale(rel: dict, as_of: date) -> bool:
    """`date` anterior a `as_of` - 90 días. Una fila sin fecha (o con fecha no
    ISO) NO se considera caducada: la retirada aprobada es la de las aristas
    CON `date` > 90 días; lo demás lo decide un lector, no este loader."""
    try:
        when = date.fromisoformat(str(rel.get("date") or "")[:10])
    except ValueError:
        return False
    return (as_of - when).days > _STALE_DAYS


def load_files(directory: Path) -> tuple[list[dict], list[dict]]:
    """Read and merge every *.yaml / *.yml file in the última hora directory."""
    files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    entities: list[dict] = []
    relations: list[dict] = []
    for f in files:
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        entities.extend(doc.get("entities") or [])
        relations.extend(doc.get("relations") or [])
        log.info("ultima_hora_file", file=f.name,
                 entities=len(doc.get("entities") or []),
                 relations=len(doc.get("relations") or []))
    return entities, relations


def _year_of(rel: dict) -> int | None:
    """Explicit `year`, else a leading 4-digit year in `date` ('2026-06-02' → 2026)."""
    y = rel.get("year")
    if isinstance(y, int):
        return y
    date = str(rel.get("date") or "")
    if len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def _prepare(entities: list[dict], relations: list[dict], as_of: date | None = None,
             registered: set[str] | None = None):
    """Resolve names to entity_ids; validate types. Returns (nodes, edges).

    Spec 061: una relación ACUSATORIA (ACCUSED_OF / INVESTIGATED_BY /
    CONVICTED_OF) con `date` de hace más de 90 días respecto a `as_of` solo se
    carga si la persona tiene fila en el registro de `accusations.yaml`. El resto
    de la capa (MET_WITH, PAID, WORKS_FOR…) no se toca."""
    as_of = date.today() if as_of is None else as_of
    registered = registered_defendants() if registered is None else registered
    stale = 0
    nodes: list[dict] = []
    name_to_id: dict[str, str] = {}
    rejected: set[str] = set()
    for e in entities:
        etype, name = e.get("type"), (e.get("name") or "").strip()
        if etype not in _ENTITY_TYPES or not name:
            log.warning("ultima_hora_skip_entity", name=name, type=etype)
            continue
        raw_name = name
        if etype == "Crime":
            # Spec 069: crimes are ontology vocabulary. The feed is autonomous: a crime with no
            # canonical form goes to the review queue and its edges stay unresolved (skipped).
            res = ontologia().validar("delitos", name, articulo=e.get("article"))
            if not res.ok:
                revision.encolar("ultima_hora", "delito_no_canonico", e, detalle=res.motivo, termino=name)
                log.error("ultima_hora_skip_crime", name=name)
                rejected.add(name)
                continue
            name = res.valor
        eid = entity_id(etype, name)
        name_to_id[raw_name] = eid
        nodes.append({"entity_id": eid, "type": etype, "name": name, "role": e.get("role")})

    edges: list[dict] = []
    for r in relations:
        rtype = r.get("type")
        src, tgt = (r.get("source") or "").strip(), (r.get("target") or "").strip()
        if rtype not in _REL_TYPES:
            log.warning("ultima_hora_skip_rel_type", type=rtype, source=src, target=tgt)
            continue
        if src in rejected or tgt in rejected:
            log.warning("ultima_hora_skip_rel_rejected_crime", source=src, target=tgt)
            continue
        sid = name_to_id.get(src) or _id_from_loose(src, entities)
        tid = name_to_id.get(tgt) or _id_from_loose(tgt, entities)
        if not sid or not tid:
            log.warning("ultima_hora_skip_rel_unresolved", source=src, target=tgt)
            continue
        outlet, url = (r.get("outlet") or "").strip(), (r.get("url") or "").strip()
        if not outlet or not url:
            # Última hora without attribution defeats the whole point.
            log.warning("ultima_hora_skip_rel_unattributed", source=src, target=tgt, type=rtype)
            continue
        # Acusación de prensa caducada y sin estado registrado → no se carga (spec
        # 061). Sin este gate, `make ultima-hora` resucitaba en el siguiente Path A
        # las 41 aristas que la migración de retiradas había borrado.
        if (rtype in _ACCUSATION_RELS and _is_stale(r, as_of)
                and sid not in registered and tid not in registered):
            stale += 1
            log.warning("ultima_hora_skip_stale_accusation", source=src, target=tgt,
                        type=rtype, date=r.get("date"), outlet=outlet,
                        note=f"acusación de prensa de hace >{_STALE_DAYS} días sin fila "
                             "en accusations.yaml (spec 061)")
            continue
        # Spec 069: the procedural status is ontology vocabulary. The feed is written by an
        # autonomous routine, so a bad status does not abort the load: the edge goes to the
        # review queue (never the graph) and the rest loads.
        status = None
        if rtype in _ACCUSATION_RELS and r.get("status") is not None:
            res = ontologia().validar("estados_procesales", r.get("status"))
            if not res.ok:
                revision.encolar("ultima_hora", "estado_fuera_de_vocabulario", r, detalle=res.motivo,
                                 termino=str(r.get("status")))
                log.error("ultima_hora_skip_bad_status", source=src, target=tgt, status=r.get("status"))
                continue
            status = res.valor
        edges.append({
            "source_id": sid, "target_id": tid, "type": rtype,
            "outlet": outlet, "url": url,
            "date": r.get("date"), "year": _year_of(r),
            "quote": (r.get("quote") or "").strip()[:300] or None,
            "note": r.get("note"),
            # 063 (2026-09-29): el estado procesal solo va en las acusaciones. En WORKS_FOR/ASSOCIATED_WITH la web
            # lo pintaba como distintivo («Imputado · presunto») y se saltaba la caducidad y el registro; None lo
            # BORRA en el siguiente merge (SET a null), así que el cambio es idempotente.
            "status": status,
        })
    if stale:
        log.warning("ultima_hora_stale_accusations_skipped", count=stale, as_of=as_of.isoformat(),
                    note="acusaciones de prensa caducadas sin registro de estado (spec 061)")
    return nodes, edges


def _id_from_loose(name: str, entities: list[dict]) -> str | None:
    """Allow a relation endpoint that wasn't listed under `entities` as long as
    we can find its declared type (so endpoints must appear in `entities`)."""
    for e in entities:
        if (e.get("name") or "").strip() == name and e.get("type") in _ENTITY_TYPES:
            return entity_id(e["type"], name)
    return None


def load(driver, nodes: list[dict], edges: list[dict]):
    # Entities: onCreate sets identity + flags the new node as última-hora-sourced.
    # The role is only ever *filled* (coalesce), never overwritten — so a real
    # document figure keeps its provenance and we never clobber judicial data.
    node_q = """
    UNWIND $rows AS row
    CALL apoc.merge.node([row.type], {entity_id: row.entity_id},
      {name: row.name, canonical_name: row.name,
       source_kind: 'ultima_hora', is_ultima_hora: true, mentions: 0, degree: 0},
      {}) YIELD node
    SET node.role = coalesce(node.role, row.role)
    RETURN count(node)
    """
    # Relations keyed by source_kind so an última-hora edge never collides with
    # (or overwrites) a judicial / contexto / prensa edge of the same triple.
    rel_q = """
    UNWIND $rows AS row
    MATCH (a {entity_id: row.source_id})
    MATCH (b {entity_id: row.target_id})
    CALL apoc.merge.relationship(a, row.type,
      {source_kind: 'ultima_hora'},
      {outlet: row.outlet, url: row.url, date: row.date, date_year: row.year,
       source_quote: row.quote, note: row.note, status: row.status},
      b,
      {outlet: row.outlet, url: row.url, date: row.date, date_year: row.year,
       source_quote: row.quote, note: row.note, status: row.status}) YIELD rel
    RETURN count(rel)
    """
    with driver.session() as s:
        s.run(node_q, rows=nodes)
        s.run(rel_q, rows=edges)
    log.info("ultima_hora_loaded", nodes=len(nodes), edges=len(edges))


def main():
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default=str(ULTIMA_HORA_DIR))
    p.add_argument("--as-of", default=None, metavar="YYYY-MM-DD",
                   help="fecha de referencia para la caducidad de las acusaciones "
                        "de prensa (por defecto, hoy)")
    args = p.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("ultima_hora_dir_missing", dir=str(directory))
        return
    entities, relations = load_files(directory)
    nodes, edges = _prepare(entities, relations, as_of=as_of)
    if not nodes and not edges:
        log.warning("ultima_hora_empty", dir=str(directory))
        return

    driver = neo4j_loader.get_driver()
    try:
        load(driver, nodes, edges)
        neo4j_loader.compute_degree(driver)  # última hora edges now count toward degree
    finally:
        driver.close()
    log.info("ultima_hora_done")


if __name__ == "__main__":
    main()
