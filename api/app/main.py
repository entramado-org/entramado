"""FastAPI app: graph search, ego-graph expansion, provenance, briefings."""
from __future__ import annotations

import re
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from prometheus_fastapi_instrumentator import Instrumentator

from . import cases, config, graph_queries, patrones, ultima_hora_feed
from .redact import redact
from .briefing_store import BRIEFINGS_DIR, _briefing_files, _parse_briefing
from .deps import close_driver, get_driver

# Structured JSON logs to stdout (spec 017): Promtail tails the container logs into
# Loki, so every line must be machine-parseable. `merge_contextvars` pulls in the
# per-request `request_id` bound by the access-log middleware below.
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_driver()  # open pool eagerly
    yield
    close_driver()




class RedactedJSONResponse(JSONResponse):
    """Every JSON body goes out with personal identifiers and home addresses masked (RGPD, `app.redact`)."""

    def render(self, content) -> bytes:
        return super().render(redact(content))


app = FastAPI(title="El Entramado — Knowledge Graph API", lifespan=lifespan,
              default_response_class=RedactedJSONResponse)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    # Dev/LAN only: the web app on localhost or a private-range device. In
    # production the browser talks same-origin through Caddy, so CORS never fires.
    # :3100 = build de producción de la web servida en el host para e2e/playwright-cli.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|192\.168\.[0-9.]+|10\.[0-9.]+):(3000|3100)",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Per-endpoint request count + latency histograms, exposed at GET /metrics for
# Prometheus to scrape on the internal compose network (spec 017). Path-templated
# (e.g. /node/{node_id}) so cardinality stays bounded.
Instrumentator().instrument(app).expose(app, include_in_schema=False)


@app.middleware("http")
async def access_log(request: Request, call_next):
    """Structured access log + request-id correlation for every request."""
    request_id = uuid.uuid4().hex[:12]
    structlog.contextvars.bind_contextvars(request_id=request_id)
    client = request.client.host if request.client else None
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000
        response.headers["X-Request-ID"] = request_id
        # Don't log the Prometheus scrape (every ~15s) — it would flood Loki.
        if request.url.path != "/metrics":
            log.info("request", method=request.method, path=request.url.path,
                     status=response.status_code, duration_ms=round(ms, 1), client=client)
        return response
    except Exception:
        ms = (time.perf_counter() - t0) * 1000
        log.error("request_error", method=request.method, path=request.url.path,
                  duration_ms=round(ms, 1), client=client)
        raise  # let ServerErrorMiddleware turn it into a 500
    finally:
        structlog.contextvars.clear_contextvars()


# Paths whose GET responses are cacheable: graph data only changes on curated
# reloads (make context/relations/…), so a short shared TTL saves Neo4j round
# trips without risking long staleness.
_CACHEABLE = re.compile(r"^/(graph/|node/|timeline|briefing|document|documents|money-flows|claim|entities/|ultima-hora|patrones)")


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    response = await call_next(request)
    if (request.method == "GET" and response.status_code == 200
            and _CACHEABLE.match(request.url.path)):
        response.headers.setdefault("Cache-Control", "public, max-age=300")
    return response


@app.exception_handler(ServiceUnavailable)
@app.exception_handler(Neo4jError)
@app.exception_handler(OSError)
async def neo4j_unavailable(request: Request, exc: Exception):
    """Graph endpoints hit Neo4j directly; if it's down, answer a clean 503
    instead of a 500 traceback."""
    log.error("neo4j_unavailable", path=request.url.path, error=str(exc))
    return JSONResponse(status_code=503, content={"detail": "database unavailable"})


def _types(types: str | None) -> list[str] | None:
    return [t for t in types.split(",") if t] if types else None


@app.get("/health")
def health():
    neo = graph_queries.ping()
    status = "ok" if neo else "degraded"
    return {"status": status, "neo4j": neo}


@app.get("/search")
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(25, ge=1, le=100),
    types: str | None = None,
):
    """Entity name search. `types` (comma-separated labels: Person,Organization,
    Company,Court,LegalCase,Crime,Location,Event,MoneyAmount,Contract) narrows by
    node label — same filter the MCP `buscar_entidades(tipo=…)` exposes."""
    return {"results": graph_queries.search_entities(q, limit, _types(types))}


@app.get("/graph/overview")
def graph_overview(
    types: str | None = None,
    limit: int = Query(300, ge=1, le=1000),
    min_degree: int = Query(1, ge=0),
    from_year: int | None = Query(None, ge=1900, le=2100),
    to_year: int | None = Query(None, ge=1900, le=2100),
):
    return graph_queries.overview(_types(types), limit, min_degree, from_year, to_year)


@app.get("/graph/path")
def graph_path(
    from_id: str = Query(..., alias="from", min_length=1),
    to_id: str = Query(..., alias="to", min_length=1),
    max_len: int = Query(4, ge=1, le=6),
    max_paths: int = Query(5, ge=1, le=10),
):
    """Shortest evidence chains between two entities (provenance rels excluded).

    No path / unknown id → 200 with meta.found=false (it's an answer, not an
    error). Lives under /graph/ so the cache middleware applies.
    """
    if from_id == to_id:
        raise HTTPException(422, "from and to must be different entities")
    return graph_queries.paths_between(from_id, to_id, max_len, max_paths)


@app.get("/graph/relation")
def graph_relation(
    from_id: str = Query(..., alias="from", min_length=1),
    to_id: str = Query(..., alias="to", min_length=1),
    rel_type: str | None = Query(None, alias="type"),
    from_year: int | None = Query(None, ge=1900, le=2100),
    to_year: int | None = Query(None, ge=1900, le=2100),
):
    """Full evidence between two entities.

    With `type` → the evidence behind one aggregated display edge (the ×count
    link), grouped by provenance in the panel. WITHOUT `type` → ALL relations
    (every type, both directions) between the pair, one group per (direction,
    type): this backs the panel when the canvas collapses the parallel edges
    into a single per-pair line. Query params (not path) because entity ids
    contain ':'. Lives under /graph/ so the cache middleware applies; the year
    window mirrors the canvas aggregation so counts match the drawn line.
    """
    if rel_type:
        detail = graph_queries.relation_detail(from_id, to_id, rel_type, from_year, to_year)
        if detail is None:
            raise HTTPException(404, f"Relation not found: {from_id} -{rel_type}-> {to_id}")
        return detail
    pair = graph_queries.relation_pair(from_id, to_id, from_year, to_year)
    if pair is None:
        raise HTTPException(404, f"No relation between {from_id} and {to_id}")
    return pair


@app.get("/document/{doc_id:path}")
def document(
    doc_id: str,
    page: int | None = Query(None, ge=1, le=100000),
    window: int = Query(0, ge=0, le=5),
):
    """Resolve a citation `[documento, p.N]` to its passage text (web twin of
    the MCP `leer_documento`). Without `page` → the page index; with `page` →
    the full text of that page's chunks ±`window` adjacent pages."""
    doc = graph_queries.read_document(doc_id, page, window)
    if doc is None:
        raise HTTPException(404, f"Document not found: {doc_id}")
    return doc


@app.get("/documents/recent")
def documents_recent(
    limit: int = Query(10, ge=1, le=100),
    tipo: str | None = None,
):
    """Most recent documents by curated `doc_date` (twin of `documentos_recientes`)."""
    return {"documents": graph_queries.recent_documents(limit, tipo)}


@app.get("/documents/search")
def documents_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(8, ge=1, le=50),
    tipo: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    mentions: str | None = None,
):
    """Full-text search over judicial document chunks (twin of `buscar_documentos`).
    `tipo` filters by doc_type; `desde`/`hasta` (ISO) by doc_date; `mentions` by
    an entity_id. doc_type/doc_date coverage is partial (curated docs only)."""
    return {"results": graph_queries.keyword_search_chunks(
        q, limit, _types(tipo), desde, hasta, mentions)}


@app.get("/node/{node_id:path}/claims")
def node_claims(node_id: str):
    """Disputed claims this entity is party to (asserts/disputes/about), each
    with its for/against postures and sources (twin of `claims_entidad`)."""
    return {"claims": graph_queries.entity_claims(node_id)}


@app.get("/claim/{claim_id}")
def claim(claim_id: str):
    """One disputed claim with both postures and their sources (twin of
    `evidencia_claim`)."""
    detail = graph_queries.claim_detail(claim_id)
    if detail is None:
        raise HTTPException(404, f"Claim not found: {claim_id}")
    return detail


@app.get("/money-flows")
def money_flows(
    entity: str | None = None,
    entities: str | None = None,
    min_amount: float | None = Query(None, ge=0, alias="min"),
    from_year: int | None = Query(None, ge=1900, le=2100),
    to_year: int | None = Query(None, ge=1900, le=2100),
    limit: int = Query(50, ge=1, le=200),
):
    """Aggregated money trail: entity→entity edges carrying an `amount`, ordered
    by amount desc, with citation + provenance (twin of `flujos_dinero`).
    `entities` (CSV de entity_ids, spec 038) acota a los flujos que tocan un
    reparto curado — p. ej. las entidades de un caso."""
    entity_ids = [e for e in (entities or "").split(",") if e] or None
    return {"flows": graph_queries.money_flows(entity, min_amount, from_year, to_year,
                                               limit, entity_ids=entity_ids)}


@app.get("/node/{node_id:path}/neighborhood")
def node_neighborhood(
    node_id: str,
    hops: int = Query(1, ge=1, le=3),
    limit: int = Query(150, ge=1, le=500),
    types: str | None = None,
    min_degree: int = Query(1, ge=0),
    from_year: int | None = Query(None, ge=1900, le=2100),
    to_year: int | None = Query(None, ge=1900, le=2100),
):
    return graph_queries.neighborhood(
        node_id, hops, limit, _types(types), min_degree, from_year, to_year
    )


@app.get("/timeline")
def timeline(
    entity: str | None = None,
    entities: str | None = None,
    docs: str | None = None,
    from_year: int | None = Query(None, ge=1900, le=2100),
    to_year: int | None = Query(None, ge=1900, le=2100),
    types: str | None = None,
    limit: int = Query(500, ge=1, le=1000),
):
    """`entities`/`docs` (CSV, spec 038) acotan la cronología a un caso curado:
    cualquiera de sus entidades y/o hitos citados a sus documentos."""
    entity_list = [e for e in (entities or "").split(",") if e] or None
    doc_list = [d for d in (docs or "").split(",") if d] or None
    return {
        "events": graph_queries.timeline(entity, from_year, to_year, _types(types), limit,
                                         entities=entity_list, docs=doc_list)
    }


@app.get("/entities/indexable")
def entities_indexable():
    """Central cast for the public /entidad fichas (spec 035): the ~300
    entities that clear the ficha criteria, with their public slug. Feeds
    the web sitemap, the /entidad index and slug→id resolution."""
    return {"entities": graph_queries.indexable_entities()}


@app.get("/entities/accused")
def entities_accused():
    """Directorio de investigados (spec 037): toda entidad con cargos
    (ACCUSED_OF/CONVICTED_OF → :Crime) con su lista de cargos citados. El
    fichaSlug sale del set indexable global (misma regla de colisiones que
    /entidad); null → la web enlaza al explorador."""
    rows = graph_queries.accused_entities()
    slug_by_id = {e["id"]: e["slug"] for e in graph_queries.indexable_entities()}
    case_map = _entity_case_map_safe()
    for r in rows:
        r["fichaSlug"] = slug_by_id.get(r["id"])
        r["casos"] = case_map.get(r["id"], [])
    return {"entities": rows}


def _entity_case_map_safe() -> dict:
    """entity→casos link map; a broken cases.yaml must not take down the
    endpoints that only decorate with it."""
    try:
        return cases.entity_case_map()
    except Exception as e:  # noqa: BLE001
        log.warning("cases_registry_unavailable", error=str(e))
        return {}


@app.get("/cases")
def list_cases():
    """Índice del registro curado de casos (spec 038) — alimenta /caso y el
    sitemap."""
    return {"cases": cases.case_index()}


@app.get("/case/{slug}")
def case_detail(slug: str):
    """Ficha completa de un caso curado: meta editorial + implicados con cargos
    en vivo + documentos + cronología citada + flujos de dinero."""
    payload = cases.case_payload(slug)
    if payload is None:
        raise HTTPException(404, f"Case not found: {slug}")
    return payload


@app.get("/node/{node_id:path}")
def node(node_id: str):
    detail = graph_queries.node_detail(node_id)
    if detail is None:
        raise HTTPException(404, f"Node not found: {node_id}")
    # Enlace inverso a los casos curados (spec 038) — chips "Casos" en la ficha.
    detail["node"]["casos"] = _entity_case_map_safe().get(node_id, [])
    return detail


@app.get("/ultima-hora")
def ultima_hora():
    """Editorial última-hora feed (spec 044) — read from disk per mtime, so a
    merged feed PR is live without rebuilding images."""
    return {"items": ultima_hora_feed.load_feed()}


@app.get("/patrones")
def patrones_feed(response: Response):
    """Curated «patrones llamativos» (spec 053) — same runtime-mount circuit as
    /ultima-hora, but strictly validated (human-edited editorial layer). An
    empty answer (cold start, mount not there yet) must NOT be pinned by the
    shared cache for 300s, so it goes out no-store."""
    items = patrones.load_patrones()
    if not items:
        response.headers["Cache-Control"] = "no-store"
    return {"patrones": items}


@app.get("/briefings")
def list_briefings():
    if not BRIEFINGS_DIR.exists():
        return {"briefings": []}
    items = []
    for p in _briefing_files():
        try:
            b = _parse_briefing(p)
            items.append({"slug": b["slug"], "title": b["title"],
                          "dek": b["dek"], "anchors": b["anchors"],
                          "updated": b.get("updated")})
        except Exception as e:  # noqa: BLE001
            log.warning("briefing_parse_failed", file=p.name, error=str(e))
    return {"briefings": items}


@app.get("/briefing/{slug}")
def get_briefing(slug: str):
    if not BRIEFINGS_DIR.exists():
        raise HTTPException(404, "briefings not generated yet")
    for p in _briefing_files():
        try:
            b = _parse_briefing(p)
        except Exception as e:  # noqa: BLE001
            log.warning("briefing_parse_failed", file=p.name, error=str(e))
            continue
        if b["slug"] == slug:
            return b
    raise HTTPException(404, f"briefing not found: {slug}")
