"""Curated case registry (spec 038).

A "case" here is EDITORIAL AGGREGATION — which entities, documents and feed
tags tell one story — not a graph fact. The registry lives in
`app/data/cases.yaml` (versioned, baked into the api/mcp image); this module
parses/validates it and composes the ficha payload from live graph queries.
Nothing in this module writes to Neo4j.
"""
from __future__ import annotations

import threading
from pathlib import Path

import structlog
import yaml

from . import graph_queries

log = structlog.get_logger()

DATA_PATH = Path(__file__).parent / "data" / "cases.yaml"

_ESTADO_KINDS = {"instruccion", "juicio", "sentencia", "archivado"}
_ENTITY_LABELS = ("Person:", "Company:", "Organization:", "Court:", "Event:", "Contract:")

_lock = threading.Lock()
_cache: tuple[float, list[dict]] | None = None  # (mtime, cases)
_docs_warned = False
_entities_checked = False
_entity_problems: list[str] = []


def _validate(cases: list[dict]) -> list[dict]:
    seen: set[str] = set()
    for c in cases:
        slug = c.get("slug") or ""
        if not slug or slug != slug.strip().lower() or " " in slug:
            raise ValueError(f"cases.yaml: slug inválido {slug!r}")
        if slug in seen:
            raise ValueError(f"cases.yaml: slug duplicado {slug!r}")
        seen.add(slug)
        if not (c.get("dek") or "").strip():
            raise ValueError(f"cases.yaml[{slug}]: falta dek (obligatorio — es el fallback del briefing)")
        if c.get("estado_kind") not in _ESTADO_KINDS:
            raise ValueError(f"cases.yaml[{slug}]: estado_kind debe ser uno de {sorted(_ESTADO_KINDS)}")
        for e in c.get("entidades") or []:
            if not str(e.get("id", "")).startswith(_ENTITY_LABELS):
                raise ValueError(f"cases.yaml[{slug}]: entidad sin prefijo de label: {e.get('id')!r}")
        c.setdefault("entidades", [])
        c.setdefault("documentos", [])
        c.setdefault("ultima_hora_tags", [])
        c.setdefault("relacionados", [])
    for c in cases:
        for rel in c["relacionados"]:
            if rel not in seen:
                raise ValueError(f"cases.yaml[{c['slug']}]: relacionado desconocido {rel!r}")
    return cases


def load_cases() -> list[dict]:
    """Parsed + validated case registry, cached by file mtime.

    Fail-closed (spec 061): un entity_id que no resuelve a ningún nodo del grafo
    aborta la carga en vez de avisar — un implicado fantasma en una ficha de caso
    es una persona señalada sin respaldo, que es justo lo que no puede publicarse."""
    global _cache, _docs_warned, _entities_checked
    mtime = DATA_PATH.stat().st_mtime
    with _lock:
        if _cache and _cache[0] == mtime:
            cases = _cache[1]
        else:
            raw = yaml.safe_load(DATA_PATH.read_text(encoding="utf-8")) or {}
            cases = _validate(raw.get("cases") or [])
            _cache = (mtime, cases)
            _docs_warned = False        # fichero nuevo → se revalidan sus ids
            _entities_checked = False
            _entity_problems.clear()
    _warn_unknown_docs(cases)
    _check_unknown_entities(cases)
    return cases


def _check_unknown_entities(cases: list[dict]) -> None:
    """Gate de ids huérfanos. Se resuelve una vez por fichero y se recuerda: si el
    grafo no está disponible se reintenta en la siguiente carga (no se da por
    bueno), y si hay ids que no existen, TODA carga posterior falla igual."""
    global _entities_checked
    if not _entities_checked:
        wanted = sorted({e["id"] for c in cases for e in c["entidades"]})
        if not wanted:
            _entities_checked = True
            return
        try:
            known = graph_queries.existing_entity_ids(wanted)
        except Exception as exc:  # noqa: BLE001 — Neo4j caído al importar: se reintenta
            log.warning("case_entities_check_skipped", error=str(exc))
            return
        _entity_problems[:] = [e for e in wanted if e not in known]
        _entities_checked = True
    if _entity_problems:
        raise ValueError("cases.yaml: entidades que no existen en el grafo: "
                         + ", ".join(_entity_problems))


def _warn_unknown_docs(cases: list[dict]) -> None:
    """One-shot startup guard for the silent-typo failure mode: a doc_id that
    doesn't exist in the graph silently empties the case chronology."""
    global _docs_warned
    if _docs_warned:
        return
    _docs_warned = True
    wanted = sorted({d for c in cases for d in c["documentos"]})
    if not wanted:
        return
    try:
        known = {r["file"] for r in graph_queries.documents_by_ids(wanted)}
    except Exception as exc:  # noqa: BLE001 — Neo4j down at import time is fine
        log.warning("case_docs_check_skipped", error=str(exc))
        _docs_warned = False  # retry on next load
        return
    missing = [d for d in wanted if d not in known]
    if missing:
        log.warning("case_unknown_docs", missing=missing)


def _served_cast(cases: list[dict]) -> set[str] | None:
    """The cast ids the graph serves today, or None when the graph cannot be asked (then nothing is filtered here: every
    surface that resolves an id still applies the rule)."""
    wanted = sorted({e["id"] for c in cases for e in c["entidades"]})
    try:
        return graph_queries.served_entity_ids(wanted) if wanted else set()
    except Exception as exc:  # noqa: BLE001
        log.warning("case_cast_served_check_skipped", error=str(exc))
        return None


def case_index() -> list[dict]:
    cases = load_cases()
    served = _served_cast(cases)
    return [
        {
            "slug": c["slug"],
            "titulo": c["titulo"],
            "dek": c["dek"].strip(),
            "estado": (c.get("estado") or "").strip(),
            "estadoKind": c["estado_kind"],
            "causa": c.get("causa"),
            "updated": c.get("updated"),
            "briefingSlug": c.get("briefing_slug"),
            "nImplicados": sum(1 for e in c["entidades"] if served is None or e["id"] in served),
        }
        for c in cases
    ]


def entity_case_map() -> dict[str, list[dict]]:
    """entity_id → [{slug, titulo}] — feeds the inverse links on entity fichas
    and the 'casos' column of /entities/accused (spec 037)."""
    out: dict[str, list[dict]] = {}
    for c in load_cases():
        for e in c["entidades"]:
            out.setdefault(e["id"], []).append({"slug": c["slug"], "titulo": c["titulo"]})
    return out


def get_case(slug: str) -> dict | None:
    return next((c for c in load_cases() if c["slug"] == slug), None)


def case_payload(slug: str) -> dict | None:
    """Everything the /caso/<slug> page needs, composed server-side from the
    registry + live graph. Charges/statuses always come from the graph so the
    ficha can never disagree with the entity pages."""
    case = get_case(slug)
    if case is None:
        return None
    ids = [e["id"] for e in case["entidades"]]
    by_case = {e["id"]: e for e in case["entidades"]}

    implicados = graph_queries.entities_with_charges(ids)
    slug_by_id = {e["id"]: e["slug"] for e in graph_queries.indexable_entities()}
    for row in implicados:
        spec = by_case.get(row["id"], {})
        row["papel"] = spec.get("papel")
        row["core"] = bool(spec.get("core"))
        row["fichaSlug"] = slug_by_id.get(row["id"])
    # Core cast first, then by degree — stable, editorial-first ordering.
    implicados.sort(key=lambda r: (not r["core"], -r["degree"], r["id"]))

    docs = case["documentos"]
    documentos = graph_queries.documents_by_ids(docs) if docs else []
    # limit=1000 (el tope del REST): timeline ordena ASC y con LIMIT bajo un
    # caso denso truncaría los hitos RECIENTES (koldo: 544 hitos fechados).
    # La web ya recorta a los últimos N para el render.
    cronologia = graph_queries.timeline(
        docs=docs or None,
        entities=None if docs else ids,
        limit=1000,
    )
    flujos = graph_queries.money_flows(entity_ids=ids, limit=40)

    meta = case_index()
    return {
        "meta": next(m for m in meta if m["slug"] == slug),
        # Only the cast that is served (a member left without relations is not named here either), in the curated order.
        "entidadesIds": [i for i in ids if i in {r["id"] for r in implicados}],
        "ultimaHoraTags": case["ultima_hora_tags"],
        "relacionados": [m for m in meta if m["slug"] in case["relacionados"]],
        "implicados": implicados,
        "documentos": documentos,
        "cronologia": cronologia,
        "flujos": flujos,
    }


if __name__ == "__main__":  # gate pre-merge: python -m app.cases
    import sys

    try:
        registry = load_cases()
    except ValueError as e:
        sys.exit(f"INVALID — {e}")
    for c in registry:
        print(f"  {c['slug']}: {len(c['entidades'])} implicados · "
              f"{len(c['documentos'])} documentos · [{c['estado_kind']}]")
    checked = "ids verificados contra el grafo" if _entities_checked else (
        "⚠️ grafo no disponible: ids de entidad SIN verificar")
    print(f"OK — {len(registry)} casos válidos en {DATA_PATH} ({checked})")
