"""Smoke-test every MCP tool against the live graph: OK/fallo + latencia.

Diagnoses a session in one step (which tool is broken, and how slow). Read-only:
it only queries. Each tool is invoked with representative arguments; a tool counts
as OK when it executes and returns a string that is not the Neo4j-down sentinel.
A "no existe / sin resultados" answer is still OK — it means the tool runs.

Run: `make healthcheck` (against whatever NEO4J_URI points to), or
  docker run --rm --network entramado_default -e NEO4J_URI=bolt://neo4j:7687 \
    -e NEO4J_PASSWORD=… entramado-api python -m app.healthcheck
"""
from __future__ import annotations

import asyncio
import sys
import time

from . import graph_queries, patrones
from . import mcp_server as m
from .briefing_store import vault_search
from .mcp_server import _NO_DB

# Prompts the server must expose (spec 023).
_EXPECTED_PROMPTS = {"investigar_persona", "seguir_el_dinero", "relacion_entre", "panorama_del_caso"}

# (label, callable, kwargs) — representative args using ids known to exist.
_KOLDO = "Person:koldo-garcia"
_ABALOS = "Person:jose-luis-abalos"

CHECKS = [
    ("buscar_entidades", m.buscar_entidades, {"consulta": "Koldo"}),
    ("ficha_entidad", m.ficha_entidad, {"id": _KOLDO}),
    ("listar_investigados", m.listar_investigados, {}),
    ("listar_casos", m.listar_casos, {}),
    ("ficha_caso", m.ficha_caso, {"slug": "caso-koldo"}),
    ("relaciones_entidad", m.relaciones_entidad, {"id": _KOLDO, "limite": 5}),
    # Spec 059: el marcador de seudónimo judicial viaja en las relaciones, no solo en la ficha.
    ("relaciones_entidad(seudónimo)", m.relaciones_entidad, {"id": "Person:david", "limite": 3}),
    ("camino_entre", m.camino_entre, {"desde_id": _KOLDO, "hasta_id": _ABALOS}),
    ("evidencia_relacion", m.evidencia_relacion,
     {"desde_id": _KOLDO, "hasta_id": _ABALOS, "tipo": "ASSOCIATED_WITH"}),
    ("flujos_dinero", m.flujos_dinero, {"limite": 5}),
    ("subvenciones", m.subvenciones, {"limite": 5}),
    ("datos_mercantil", m.datos_mercantil, {"limite": 5}),
    ("contratos_menores", m.contratos_menores, {"limite": 5}),
    ("contratacion_publica", m.contratacion_publica, {"limite": 5}),
    ("trayectoria_boe", m.trayectoria_boe, {"limite": 5}),
    ("patrones_llamativos", m.patrones_llamativos, {}),
    ("metricas_centralidad(grado)", m.metricas_centralidad, {"limite": 5}),
    ("metricas_centralidad(intermediacion)", m.metricas_centralidad,
     {"limite": 5, "metrica": "intermediacion"}),
    ("metricas_centralidad(cercania)", m.metricas_centralidad,
     {"limite": 5, "metrica": "cercania"}),
    ("comunidades", m.comunidades, {"limite": 5}),
    ("claims_entidad", m.claims_entidad, {"id": _KOLDO}),
    ("cronologia", m.cronologia, {"limite": 5}),
    ("buscar_documentos", m.buscar_documentos, {"consulta": "mascarillas", "limite": 3}),
    ("buscar_documentos(fecha)", m.buscar_documentos,
     {"consulta": "mascarillas", "limite": 3, "desde_fecha": "2024-01-01"}),
    ("documentos_recientes", m.documentos_recientes, {"limite": 5}),
    ("buscar_vault", m.buscar_vault, {"consulta": "Koldo"}),
]


def _dynamic_checks() -> list[tuple]:
    """Checks whose args are derived from real data (a doc_id, a vault path, a
    claim id) so the smoke test exercises live identifiers, not guesses. Each
    derivation degrades to a static fallback so the tool is always exercised."""
    extra = []
    # leer_documento: a real doc_id from the corpus (fallback: a plausible title).
    doc_arg = "auto"
    try:
        docs = graph_queries.recent_documents(1)
        if docs and docs[0].get("file"):
            doc_arg = docs[0]["file"]
    except Exception:  # noqa: BLE001
        pass
    extra.append(("leer_documento", m.leer_documento, {"documento": doc_arg}))
    # leer_nota: a real vault path (fallback: a path that just returns "not found").
    nota_arg = "_Briefings/Caso Koldo.md"
    try:
        hits = vault_search("Koldo", limit=1)
        if hits and hits[0].get("path"):
            nota_arg = hits[0]["path"]
    except Exception:  # noqa: BLE001
        pass
    extra.append(("leer_nota", m.leer_nota, {"ruta": nota_arg}))
    # evidencia_claim: a real claim id from the disputed-claims overlay, if any.
    try:
        claims = graph_queries.entity_claims(_KOLDO)
        if claims and claims[0].get("claimId"):
            extra.append(("evidencia_claim", m.evidencia_claim,
                          {"claim_id": claims[0]["claimId"]}))
    except Exception:  # noqa: BLE001
        pass
    # subvenciones(entidad): a real beneficiary from the BDNS overlay, if loaded.
    try:
        g = graph_queries.grants(limit=1)
        if g["rows"]:
            extra.append(("subvenciones(entidad)", m.subvenciones,
                          {"entidad": g["rows"][0]["beneficiario_id"]}))
    except Exception:  # noqa: BLE001
        pass
    # contratos_menores(entidad): a real awardee from the PLACSP overlay, if loaded.
    try:
        cl = graph_queries.contratacion_layer(limit=1)
        if cl["rows"]:
            extra.append(("contratos_menores(entidad)", m.contratos_menores,
                          {"entidad": cl["rows"][0]["adjudicataria_id"]}))
    except Exception:  # noqa: BLE001
        pass
    # contratacion_publica(entidad): a real awardee from the ampliada overlay, if loaded.
    try:
        al = graph_queries.contratacion_ampliada_layer(limit=1)
        if al["rows"]:
            extra.append(("contratacion_publica(entidad)", m.contratacion_publica,
                          {"entidad": al["rows"][0]["adjudicataria_id"]}))
    except Exception:  # noqa: BLE001
        pass
    # trayectoria_boe(entidad): a real person from the BOE overlay, if loaded.
    try:
        bl = graph_queries.boe_layer(limit=1)
        if bl["rows"]:
            extra.append(("trayectoria_boe(entidad)", m.trayectoria_boe,
                          {"entidad": bl["rows"][0]["persona_id"]}))
    except Exception:  # noqa: BLE001
        pass
    # datos_mercantil(id): a real endpoint of a BORME edge, if the overlay is loaded.
    try:
        ml = graph_queries.mercantil_layer(limit=1)
        if ml["rows"]:
            extra.append(("datos_mercantil(id)", m.datos_mercantil,
                          {"id": ml["rows"][0]["hasta_id"]}))
    except Exception:  # noqa: BLE001
        pass
    # patrones_llamativos(slug): the FIRST live editorial slug, so the detail
    # formatter keeps smoke coverage when patterns are renamed/retired (a
    # hardcoded slug would degrade to "No existe" and still count OK forever).
    slug_arg = "aquaterra-racimo-y-puerta-giratoria"
    try:
        pats = patrones.load_patrones()
        if pats:
            slug_arg = pats[0]["slug"]
    except Exception:  # noqa: BLE001
        pass
    extra.append(("patrones_llamativos(slug)", m.patrones_llamativos, {"slug": slug_arg}))
    return extra


def _metadata_checks() -> int:
    """Assert the schema-level contract of spec 023: every tool is annotated
    read-only with a title, and the 4 guided prompts are registered. These are
    metadata (no Neo4j needed), so they run even against an empty DB."""
    print("Metadatos MCP (anotaciones + prompts)\n" + "-" * 52)
    fails = 0
    tools = asyncio.run(m.mcp.list_tools())
    for t in tools:
        ann = t.annotations
        if ann and ann.readOnlyHint is True and (ann.title or "").strip():
            status, detail = "OK", f"readOnly · «{ann.title}»"
        else:
            status, detail = "FALLO", "falta readOnlyHint=True o title"
            fails += 1
        print(f"  [{status:5}] tool {t.name:34} {detail}")
    prompts = {p.name for p in asyncio.run(m.mcp.list_prompts())}
    missing = _EXPECTED_PROMPTS - prompts
    if missing:
        print(f"  [FALLO] prompts — faltan: {', '.join(sorted(missing))}")
        fails += 1
    else:
        print(f"  [OK   ] prompts {len(_EXPECTED_PROMPTS)} registrados: {', '.join(sorted(_EXPECTED_PROMPTS))}")
    print("-" * 52)
    return fails


def run() -> int:
    meta_fails = _metadata_checks()
    checks = CHECKS + _dynamic_checks()
    tool_fails = 0
    print(f"Healthcheck MCP — {len(checks)} herramientas\n" + "-" * 52)
    for label, fn, kwargs in checks:
        t0 = time.perf_counter()
        try:
            res = fn(**kwargs)
            ms = (time.perf_counter() - t0) * 1000
            if not isinstance(res, str):
                status, detail = "FALLO", f"devolvió {type(res).__name__}, no str"
                tool_fails += 1
            elif res == _NO_DB:
                status, detail = "FALLO", "Neo4j no disponible"
                tool_fails += 1
            else:
                status, detail = "OK", f"{len(res)} chars"
        except Exception as exc:  # noqa: BLE001
            ms = (time.perf_counter() - t0) * 1000
            status, detail = "FALLO", f"{type(exc).__name__}: {exc}"
            tool_fails += 1
        print(f"  [{status:5}] {label:38} {ms:7.0f} ms  {detail}")
    print("-" * 52)
    fails = tool_fails + meta_fails
    print(f"{len(checks) - tool_fails}/{len(checks)} herramientas OK"
          + (f" · {fails} FALLO(S) (incl. {meta_fails} de metadatos)" if fails else " · todo verde"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(run())
