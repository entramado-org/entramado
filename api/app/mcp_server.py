"""Servidor MCP público de solo lectura sobre el grafo + vault de El Entramado.

Spec 009. Streamable HTTP en modo stateless/JSON (compatible con los conectores
personalizados de claude.ai/Claude Desktop y con `claude mcp add --transport http`).
Las reglas de uso (citas, presunción de inocencia, identidad de contexto) vienen
del system prompt del chat GraphRAG eliminado en bcca65a, ahora como
`instructions` del servidor para que las aplique el Claude del usuario.

Arranque: uvicorn app.mcp_server:asgi --host 0.0.0.0 --port 8008
"""
from __future__ import annotations

import functools
import time
import unicodedata
from typing import Annotated, Literal

import structlog
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from pydantic import Field

from . import cases, graph_queries, ontologia, patrones
from .redact import redact
from .briefing_store import vault_available, vault_entity_aliases, vault_entity_notes, vault_read, vault_search
from .mcp_ratelimit import RateLimitMiddleware
from .metrics import MCP_TOOL_CALLS, MCP_TOOL_DURATION, metrics_asgi_app

log = structlog.get_logger()

_ESTADOS = "|".join(ontologia.estados_publicos())

INSTRUCTIONS = f"""Herramientas de consulta sobre casos de corrupción en España: un grafo de \
conocimiento construido desde documentos judiciales y policiales públicos (caso Koldo, \
Cerdán/UCO, Ábalos, Aldama, Begoña Gómez, mascarillas…) más un vault de notas y briefings. \
Reglas al usar los resultados:
- Responde en español. Cita SIEMPRE las fuentes inline como [documento, p.N] usando los \
campos doc/página que devuelven las herramientas.
- Presunción de inocencia: distingue hechos probados, indicios y acusaciones. Respeta el \
[estado: {_ESTADOS}] — \
"imputado" o "acusado" NO es "condenado", y "condenado_no_firme" es una condena RECURRIDA o \
recurrible (no firme): sigue rigiendo la presunción de inocencia; solo "condenado" es firme. \
"condena_anulada" significa que el Tribunal Constitucional ANULÓ la condena: NO hay condena \
vigente; preséntalo como «no condenado» a la espera de una sentencia nueva, nunca como condena. \
Un ACCUSED_OF sin estado registrado se presenta como investigado.
- ⚠️ SEUDÓNIMO JUDICIAL: los nodos marcados "⚠️ seudónimo judicial" son nombres de pila \
ficticios del roster de anonimización del CENDOJ («David», «Benito», «Felix»…) que un auto del \
Tribunal Supremo usa en lugar del nombre real. Cada uno es una persona real DISTINTA de \
cualquier homónimo con nombre civil del grafo. NUNCA los identifiques con una persona \
concreta —ni aunque la prensa lo haga o el cargo lo sugiera—, ni fusiones sus hechos con los \
de un nodo con nombre real; cita lo que dice el auto y di que la fuente no da el nombre.
- Las relaciones "(procedencia no registrada)" carecen de marcador de capa: trátalas como \
las de menor peso (ni judicial ni oficial) hasta que se verifique su origen.
- Las relaciones "(contexto público, sin fuente judicial)" son conocimiento general (cargos, \
partidos, parentesco): son la fuente AUTORITATIVA para identificar a las personas, pero NO \
son hechos ni indicios del caso y no llevan cita [doc, p.N]. NUNCA infieras matrimonio o \
parentesco de relaciones genéricas como ASSOCIATED_WITH o MET_WITH (solo indican \
co-aparición o contacto).
- Las relaciones "(según prensa: <medio>…)" o "(última hora — según prensa…)" son datos \
reportados por ese medio, no por los documentos judiciales: atribúyelas explícitamente \
("según <medio>") y nunca como hecho probado ni con cita [doc, p.N].
- Las relaciones "(según registro mercantil / BORME…)" (p. ej. ADMINISTRA) son datos \
societarios oficiales del BORME (administrador↔empresa): estructura societaria pública, NO \
hecho ni indicio del caso, sin cita [doc, p.N] (se cita el borme_id). El BORME capta \
administradores registrales, no la titularidad real en la sombra: la presencia como \
administrador no imputa y la ausencia no exculpa. Presunción de inocencia. (Datos \
societarios cortesía de Nearius, https://www.nearius.com/.)
- Las relaciones "(según BDNS/SNPSAP…)" (p. ej. RECEIVED_GRANT) son subvenciones, ayudas o \
avales públicos registrados en la Base de Datos Nacional de Subvenciones: dato oficial \
público, NO hecho ni indicio del caso, sin cita [doc, p.N]. Recibir una subvención o un \
aval es LEGAL: la señal editorial es el cruce con la trama (o su consecuencia, p. ej. \
avales públicos seguidos de concurso), nunca la ayuda en sí. Presunción de inocencia.
- Las relaciones "(según PLACSP…)" (p. ej. MINOR_CONTRACTOR_OF) son AGREGADOS oficiales de \
contratos menores publicados en la Plataforma de Contratación del Sector Público \
(adjudicataria↔órgano: nº de contratos, total y rango temporal, con enlace de ejemplo a \
PLACSP): dato oficial público, NO hecho ni indicio del caso, sin cita [doc, p.N]. \
Adjudicar o recibir un contrato menor es LEGAL: la señal editorial es el cruce con el \
reparto del caso (o su consecuencia, p. ej. contratar con el Estado hasta la víspera de un \
concurso de acreedores), nunca el contrato en sí. La marca "sin contratos menores" es un \
negativo informativo del barrido 2018-2026, no una sospecha. Presunción de inocencia.
- Las relaciones "(según PLACSP/plataformas oficiales…)" (p. ej. CONTRACTOR_OF) son \
AGREGADOS oficiales de contratación pública AMPLIADA: licitaciones de PLACSP (2012→), \
licitaciones de plataformas autonómicas/locales agregadas a PLACSP (2016→) y contratos \
menores de plataformas autonómicas (Junta de Andalucía, Euskadi, Xunta de Galicia), \
adjudicataria↔órgano con señales de procedimiento (nº en tramitación de emergencia, nº por \
negociado sin publicidad, vía UTE, presupuesto vs adjudicado): dato oficial público, NO \
hecho ni indicio del caso, sin cita [doc, p.N] (cada relación indica su fuente y lleva \
enlace de ejemplo). Adjudicarse una licitación es LEGAL — también en emergencia o por \
negociado, que son procedimientos previstos por la ley: la señal editorial es el cruce con \
el reparto del caso y el patrón de procedimiento, nunca la adjudicación en sí. La marca \
"sin adjudicaciones directas" es un negativo informativo del barrido completo 2012-2026 \
(la actividad vía UTE, si existe, se muestra como relación con anotación vía-UTE), no una \
sospecha. La ausencia no exculpa y la presencia no imputa. Presunción de inocencia.
- Las relaciones "(según BOE — nombramiento/cese oficial…)" (p. ej. APPOINTED_TO) son \
trayectorias de cargos públicos de la sección II.A del BOE (persona↔organismo: cargo, fecha \
y disposición citable de nombramiento y, si consta, de cese): dato oficial público, NO \
hecho ni indicio del caso, sin cita [doc, p.N] (se cita el id BOE-A-… con su enlace). Un \
nombramiento o un cese es un acto administrativo LEGAL: la señal editorial es la \
trayectoria cruzada con el reparto del caso (quién dirigía qué organismo cuándo), nunca el \
cargo en sí. Un cargo sin fecha de cese significa "sin cese registrado en esta capa", NO \
"vigente". Presunción de inocencia.
- Los resultados de `patrones_llamativos` son la capa EDITORIAL del proyecto: patrones \
legales-pero-llamativos (racimos al filo del tope del contrato menor, puertas giratorias, \
capital desproporcionado…) construidos cruzando registros públicos con el corpus, SIN foco \
mediático ni judicial conocido a su fecha de estado. NO son datos del grafo ni hechos o \
indicios del caso: atribúyelos siempre como «patrón editorial de El Entramado», reproduce \
su disclaimer, distingue «legal pero llamativo» de indicio judicial y no presentes a nadie \
como investigado por ellos (el estado procesal real, si existe, está en ficha_entidad). \
Cada hito lleva su fuente pública verificable (BORME id, expediente PLACSP, [doc, p.N]): \
cítala. Presunción de inocencia.
- Lo dicho en transcripciones parlamentarias es debate político, no prueba judicial.
- Si las herramientas no devuelven la respuesta, dilo claramente; no inventes.
- Flujo típico: buscar_entidades para obtener el `id` exacto → ficha_entidad / \
relaciones_entidad / camino_entre con ese id. buscar_documentos busca en el texto de los \
documentos judiciales; buscar_vault en las notas y briefings. Las cinco capas de datos \
oficiales tienen tool propia: datos_mercantil (estructura societaria BORME), subvenciones \
(ayudas públicas BDNS), contratos_menores (contratación menor PLACSP), contratacion_publica \
(licitaciones + plataformas agregadas + menores autonómicos) y trayectoria_boe \
(cargos públicos BOE); todas admiten llamarse sin id para ver la capa completa. \
patrones_llamativos sirve la capa editorial de patrones (sin slug lista; con slug, detalle)."""

mcp = FastMCP(
    "El Entramado",
    instructions=INSTRUCTIONS,
    stateless_http=True,
    json_response=True,
    # DNS-rebinding protection guards localhost-bound servers from hostile
    # websites; this endpoint is deliberately public behind Caddy and serves
    # under whatever hostname the deployment uses (ts.net Funnel, sslip.io…),
    # so Host validation only produces 421s — off by design.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# RGPD: every tool's output leaves with personal identifiers and home addresses masked (`app.redact`). Wrapping the
# decorator keeps each tool's signature (functools.wraps) so the generated JSON schemas do not change.
_register_tool = mcp.tool


def _redacting_tool(*args, **kwargs):
    register = _register_tool(*args, **kwargs)

    def wrap(fn):
        @functools.wraps(fn)
        def tool(*a, **k):
            return redact(fn(*a, **k))
        return register(tool)
    return wrap


mcp.tool = _redacting_tool

# Enumerations surfaced verbatim in the tool schemas: a Literal becomes a
# JSON-schema `enum`, so the host rejects invalid values before they reach the
# function body (tighter than the old runtime checks) and Claude sees the choices.
# Entity types and provenance layers come from the ontology (spec 069).
EntityLabel = Literal[ontologia.tipos_entidad()]
Procedencia = Literal[ontologia.capas()]
TipoDocumento = Literal[
    "auto_judicial", "informe_uco", "transcripcion_parlamentaria",
    "escrito_acusacion", "auditoria", "otros",
]
Metrica = Literal["grado", "intermediacion", "cercania"]


def _ro(title: str) -> ToolAnnotations:
    """Annotations for a read-only, self-contained tool (only reads Neo4j / the
    local vault — no external world). readOnlyHint lets hosts auto-approve the
    call; openWorldHint=False keeps the network indicator off. Directory-required."""
    return ToolAnnotations(
        title=title, readOnlyHint=True, idempotentHint=True, openWorldHint=False
    )


# How curated `contexto` ties (subject -> object) read in Spanish identity lines.
_REL_ES = {
    "FAMILY_OF": lambda k, o: f"{k or 'familiar'} de {o}",
    "MEMBER_OF": lambda k, o: f"miembro de {o}",
    "WORKS_FOR": lambda k, o: f"cargo/empleo en {o}",
    "OWNS": lambda k, o: f"propietario/fundador de {o}",
    "CONTROLS": lambda k, o: f"controla {o}",
    "SUBSIDIARY_OF": lambda k, o: f"filial de {o}",
}

_NO_DB = ("No se pudo consultar la base de datos en este momento. "
          "Inténtalo de nuevo en unos minutos.")


def _guard(fn):
    """Neo4j down → clean Spanish error string, never a stacktrace.

    Doubles as per-tool instrumentation (spec 017): records count + latency by tool
    name and outcome. This is the granularity Caddy can't see — every public MCP
    call is just `POST /mcp` at the proxy.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        tool = fn.__name__
        outcome = "ok"
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        except (ServiceUnavailable, Neo4jError, OSError):
            outcome = "db_error"
            return _NO_DB
        except Exception:
            outcome = "error"
            raise
        finally:
            MCP_TOOL_DURATION.labels(tool=tool).observe(time.perf_counter() - t0)
            MCP_TOOL_CALLS.labels(tool=tool, outcome=outcome).inc()
    return wrapper


def _seud(n: dict | None) -> str:
    """Marcador de seudónimo judicial (spec 059): se imprime junto al nombre en TODAS
    las tools que emiten nodos, no solo en ficha_entidad."""
    return " ⚠️ seudónimo judicial" if n and n.get("isPseudonym") else ""


_KIND_TAGS = {
    "prensa": "según prensa", "ultima_hora": "última hora — según prensa",
    "contexto": "contexto público", "mercantil": "según registro mercantil / BORME",
    "subvencion": "según BDNS/SNPSAP — subvención/ayuda pública",
    "contratacion": "según PLACSP/plataformas oficiales — contratación pública",
    "boe": "según BOE — nombramiento/cese oficial", "acusacion": "registro curado de acusaciones, con cita judicial",
    "contrato": "contrato citado en documento judicial",
    "sin_procedencia": "procedencia no registrada",
}


def _kind_tag(kind: str | None) -> str:
    """Etiqueta corta de procedencia; '' para judicial. Fail-closed: un kind ausente
    o desconocido se etiqueta 'procedencia no registrada' (spec 059)."""
    k = kind or "sin_procedencia"
    if k == "judicial":
        return ""
    return f" ({_KIND_TAGS.get(k, 'procedencia no registrada')})"


def _quote(text: str | None, max_chars: int = 300) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


def _cite(doc: str | None, page) -> str:
    if not doc:
        return ""
    return f"[{doc}, p.{page}]" if page is not None else f"[{doc}]"


def _identity_lines(identities: list[dict]) -> list[str]:
    lines: list[str] = []
    for it in identities:
        bits: list[str] = []
        if it.get("role"):
            bits.append(it["role"])
        for t in it.get("ties") or []:
            fn = _REL_ES.get(t.get("rel"))
            if fn and t.get("other"):
                bits.append(fn(t.get("kind"), t["other"]))
        if bits:
            lines.append(f"- {it['name']}: " + "; ".join(bits))
    return lines


def _mercantil_block(m: dict) -> str:
    """Render the BORME registry projection (spec 031) as a text block for ficha_entidad.

    Structure, not culpability: official public data cited by borme_id, never [doc, p.N].
    Tolerant of missing fields and of the richer props added by later tandas (concurso,
    disolución, objeto social, histórico de capital)."""
    lines: list[str] = ["\n## Datos registrales · BORME (vía Nearius)"]
    rows: list[tuple[str, str]] = []
    if m.get("nif"):
        rows.append(("NIF/CIF", str(m["nif"])))
    if m.get("hoja"):
        rows.append(("Hoja registral", str(m["hoja"])))
    if m.get("capital") is not None:
        rows.append(("Capital social", f"{m['capital']:,.0f} €".replace(",", ".")))
    if m.get("cnae"):
        rows.append(("CNAE", str(m["cnae"])))
    if m.get("objeto_social"):
        rows.append(("Objeto social", str(m["objeto_social"])))
    if m.get("fecha"):
        rows.append(("Constitución", str(m["fecha"])))
    if m.get("provincia"):
        rows.append(("Provincia", str(m["provincia"])))
    if m.get("domicilio"):
        rows.append(("Domicilio", str(m["domicilio"])))
    lines.extend(f"- {k}: {v}" for k, v in rows)
    # Histórico de capital (tanda de profundización) — cadena pre-formateada
    # "fecha: importe; …" (Neo4j no admite listas de mapas como prop de nodo).
    if m.get("capital_historico"):
        lines.append(f"- Histórico de capital: {m['capital_historico']}")
    # Situación mercantil (concurso / disolución) — señal, no imputación.
    if m.get("en_concurso"):
        det = ", ".join(x for x in (m.get("concurso_fecha"), m.get("concurso_juzgado")) if x)
        lines.append(f"- ⚠️ En concurso de acreedores{f' ({det})' if det else ''}")
    if m.get("en_liquidacion"):
        det = m.get("liquidacion_fecha")
        lines.append(f"- ⚠️ En liquidación{f' ({det})' if det else ''}")
    if m.get("disuelta"):
        det = m.get("disuelta_fecha")
        lines.append(f"- ⚠️ Disuelta/extinguida{f' ({det})' if det else ''}")
    borme = f" · {m['borme']}" if m.get("borme") else ""
    lines.append(
        f"_Registro Mercantil (público){borme}. Estructura societaria oficial, NO un hecho ni "
        "un indicio del caso; sin cita [doc, p.N]. El BORME capta administradores registrales, no "
        "la titularidad real en la sombra: no imputa ni exculpa. Presunción de inocencia._"
    )
    return "\n".join(lines)


@mcp.tool(annotations=_ro("Buscar entidades"))
@_guard
def buscar_entidades(
    consulta: Annotated[str, Field(description="nombre o parte de un nombre; tolera tildes y erratas")],
    limite: Annotated[int, Field(description="máx. resultados (se acota a 25)")] = 10,
    tipo: Annotated[list[EntityLabel] | None,
                    Field(description="acota por una o varias etiquetas de nodo")] = None,
) -> str:
    """Busca personas, empresas, organismos, causas, delitos… por nombre (búsqueda
    difusa: tolera tildes y erratas). PRIMER PASO ante cualquier nombre: devuelve el
    `id` exacto que requieren las demás herramientas del grafo."""
    limite = max(1, min(limite, 25))
    rows = graph_queries.search_entities(consulta, limite, types=tipo)
    if not rows:
        return "Sin resultados. Prueba con otra grafía o un término más corto."
    return "\n".join(
        f"- {r['id']} · {r['name']}{_seud(r)} ({r['type']}, {r['degree']} conexiones)"
        for r in rows
    )


@mcp.tool(annotations=_ro("Ficha de entidad"))
@_guard
def ficha_entidad(
    id: Annotated[str, Field(description="id exacto de la entidad, obtenido con buscar_entidades")],
) -> str:
    """Ficha completa de una entidad por su `id` (obtenido con buscar_entidades):
    quién es (identidad curada de contexto público: cargo, partido, parentesco — fuente
    autoritativa), estado procesal de sus cargos, resumen, y citas textuales de los
    documentos con [documento, p.N]."""
    detail = graph_queries.node_detail(id)
    if detail is None:
        return f"No existe la entidad '{id}'. Usa buscar_entidades para obtener el id exacto."
    node = detail["node"]
    out: list[str] = [f"# {node['name']} ({node['type']})"]
    if node.get("isPseudonym"):
        nota = node.get("pseudonymNote") or ("Nombre ficticio de anonimización judicial: "
            "una persona real distinta a la que un auto sustituye por este nombre de pila. "
            "No lo trates como un hub real ni lo confundas con homónimos.")
        out.append(f"⚠️ SEUDÓNIMO JUDICIAL — {nota}")
    if node.get("role"):
        out.append(f"Cargo/rol: {node['role']}")
    if node.get("aliases"):
        out.append("Alias: " + ", ".join(node["aliases"]))
    ident = _identity_lines(graph_queries.entity_identities([id]))
    if ident:
        out.append("\n## Identidad (contexto público — autoritativa, sin cita [doc, p.N])")
        out.extend(ident)
    statuses = [s for s in node.get("chargeStatuses") or [] if s]
    if statuses:
        out.append(f"\nEstado procesal de sus cargos: {', '.join(sorted(set(statuses)))} "
                   "(respétalo: solo 'condenado' es condena firme; 'condenado_no_firme' está "
                   "recurrida o es recurrible y rige la presunción de inocencia; 'condena_anulada' = el TC "
                   "anuló la condena y NO hay condena vigente).")
        for p in node.get("chargeStatusesPress") or []:
            out.append(f"  · El estado «{p['status']}» solo lo sostiene la prensa"
                       + (f" ({p['fecha']})" if p.get("fecha") else "")
                       + (f": {p['fuente']}" if p.get("fuente") else "")
                       + "; no hay resolución judicial en el corpus que lo respalde.")
    if node.get("summary"):
        out.append(f"\n## Resumen\n{node['summary']}")
    merc = node.get("mercantil")
    if merc:
        out.append(_mercantil_block(merc))
    sources = detail.get("sources") or []
    real = [s for s in sources if s.get("doc")][:5]
    if real:
        out.append("\n## Menciones en documentos (citas)")
        for s in real:
            out.append(f"- {_cite(s['doc'], s.get('page'))} «{_quote(s.get('quote'))}»")
    return "\n".join(out)


@mcp.tool(annotations=_ro("Listar casos"))
@_guard
def listar_casos() -> str:
    """Los casos curados del sitio (caso Koldo, Cerdán, Begoña Gómez, Zapatero/Plus
    Ultra, Leire, David Sánchez…): slug, estado de la causa y de qué va cada uno.
    El `slug` es el parámetro de ficha_caso."""
    rows = cases.case_index()
    if not rows:
        return "No hay casos curados registrados."
    out = []
    for c in rows:
        out.append(f"- {c['slug']} · {c['titulo']} [{c['estadoKind']}]")
        out.append(f"  {c['dek']}")
        if c.get("estado"):
            out.append(f"  Estado: {c['estado']}")
    return "\n".join(out)


@mcp.tool(annotations=_ro("Ficha de caso"))
@_guard
def ficha_caso(
    slug: Annotated[str, Field(description="slug del caso, obtenido con listar_casos")],
) -> str:
    """Ficha completa de un caso curado: estado de la causa, implicados con su
    estado procesal (del grafo en vivo, citado), documentos fuente, cronología
    con citas [documento, p.N] y flujos de dinero. Presunción de inocencia:
    respeta el [estado: …] de cada implicado."""
    p = cases.case_payload(slug)
    if p is None:
        disponibles = ", ".join(c["slug"] for c in cases.case_index())
        return f"No existe el caso '{slug}'. Disponibles: {disponibles}."
    meta = p["meta"]
    out = [f"# {meta['titulo']}", meta["dek"], ""]
    if meta.get("causa"):
        out.append(f"Causa: {meta['causa']}")
    out.append(f"Estado [{meta['estadoKind']}]: {meta['estado']} (actualizado {meta['updated']})")
    out.append("\n## Implicados (estado procesal del grafo en vivo — presunción de inocencia)")
    for imp in p["implicados"]:
        papel = f" — {imp['papel']}" if imp.get("papel") else ""
        out.append(f"- {imp['name']} ({imp['id']}){papel}")
        for c in imp["charges"]:
            art = f" ({c['article']})" if c.get("article") else ""
            cite = f" {_cite(c['doc'], c.get('page'))}" if c.get("doc") else ""
            estado = graph_queries.estado_tag(c["status"], c.get("statusFuenteTipo"), c.get("statusFecha"),
                                              c.get("statusFuente"))
            out.append(f"    · {c['crime']}{art}{estado}{cite}")
    if p["documentos"]:
        out.append("\n## Documentos del caso")
        for d in p["documentos"]:
            fecha = f" · {d['date']}" if d.get("date") else ""
            out.append(f"- {d['file']}{fecha} ({d['pageCount']} págs)")
    if p["cronologia"]:
        out.append("\n## Cronología (hitos fechados, citados)")
        for ev in p["cronologia"][-40:]:
            amt = (f" ({ev['amount']:,.0f} {ev.get('currency') or 'EUR'})"
                   if ev.get("amount") else "")
            cite = f" {_cite(ev['doc'], ev.get('page'))}" if ev.get("doc") else ""
            out.append(f"- {ev['date']} · {ev['source']['name']} -[{ev['type']}]-> "
                       f"{ev['target']['name']}{amt}{cite}")
    if p["flujos"]:
        out.append("\n## Flujos de dinero del caso")
        for f in p["flujos"][:15]:
            out.append(f"- {f['source']} → {f['target']}: {f['amount']:,.0f} "
                       f"{f.get('currency') or 'EUR'}"
                       + (f" {_cite(f['doc'], f.get('page'))}" if f.get("doc") else ""))
    if p["relacionados"]:
        out.append("\nCasos relacionados: " + ", ".join(r["slug"] for r in p["relacionados"]))
    return "\n".join(out)


@mcp.tool(annotations=_ro("Listar investigados"))
@_guard
def listar_investigados() -> str:
    """Directorio de las personas y entidades con cargos en el grafo (capas
    judicial/acusación/contexto/última hora), agrupado por entidad y ordenado por
    fase procesal (condenados primero). Cada cargo indica delito, artículo del CP,
    estado procesal, procedencia y cita [documento, p.N] cuando existe. Respeta el
    estado: solo 'condenado' es condena firme; 'condenado_no_firme' y el resto son fases presuntas."""
    rows = graph_queries.accused_entities()
    if not rows:
        return "No hay entidades con cargos en el grafo."
    out = ["PRESUNCIÓN DE INOCENCIA: una imputación o acusación NO es una condena; "
           "solo 'condenado' indica condena FIRME. 'condenado_no_firme' = sentencia recurrida "
           "o recurrible (presunción vigente). 'condena_anulada' = el TC anuló la condena: "
           "NO hay condena vigente. Ordenado por fase procesal más avanzada."]
    for r in rows:
        statuses = ", ".join(sorted({c["status"] for c in r["charges"]}))
        out.append(f"\n## {r['name']}{_seud(r)} ({r['id']}) — estado: {statuses}")
        if r.get("role"):
            out.append(f"Cargo/rol: {r['role']}")
        for c in r["charges"]:
            art = f" ({c['article']})" if c.get("article") else ""
            outlet = (f" (según {c['outlet']})"
                      if c.get("outlet") and c["sourceKind"] in ("prensa", "ultima_hora") else "")
            cite = f" {_cite(c['doc'], c.get('page'))}" if c.get("doc") else ""
            estado = graph_queries.estado_tag(c["status"], c.get("statusFuenteTipo"), c.get("statusFecha"),
                                              c.get("statusFuente"))
            out.append(f"- {c['crime']}{art}{estado} "
                       f"({c['sourceKind']}){outlet}{cite}")
    return "\n".join(out)


@mcp.tool(annotations=_ro("Relaciones de una entidad"))
@_guard
def relaciones_entidad(
    id: Annotated[str, Field(description="id exacto de la entidad (de buscar_entidades)")],
    limite: Annotated[int, Field(description="máx. relaciones (se acota a 50)")] = 25,
    procedencia: Annotated[list[Procedencia] | None,
                           Field(description="filtra por procedencia de la evidencia")] = None,
    tipo_relacion: Annotated[str | None,
        Field(description="un tipo concreto en MAYÚSCULAS, p.ej. PAID, ACCUSED_OF, WORKS_FOR")] = None,
) -> str:
    """Relaciones de una entidad con el resto del grafo, como tripletas legibles,
    ordenadas por RELEVANCIA: las más evidenciadas primero (×N evidencias desc),
    desempate por procedencia (judicial > contrato > prensa > resto). Las marcadas
    "(contexto público…)" identifican cargos/parentesco (autoritativas, pero bajan
    en este orden — para identidad usa ficha_entidad); las "(según prensa…)" se
    atribuyen al medio; el [estado: …] es la fase procesal. Filtra con `procedencia`
    (una o varias capas del enum del parámetro) y/o `tipo_relacion`
    (un tipo concreto en MAYÚSCULAS, p. ej. PAID, ACCUSED_OF, WORKS_FOR). En hubs
    grandes (Koldo ~1480 conexiones) sube `limite` o filtra: la salida indica el
    total real y cuántas se muestran."""
    limite = max(1, min(limite, 50))
    res = graph_queries.ego_triples(id, limite, kinds=procedencia, rel_type=tipo_relacion)
    triples, total, shown = res["triples"], res["total"], res["shown"]
    if not triples:
        return (f"Sin relaciones para '{id}'"
                + (f" con tipo {tipo_relacion}" if tipo_relacion else "")
                + ". ¿Es el id exacto? Usa buscar_entidades primero.")
    out = [f"- {t}" for t in triples]
    if total > shown:
        out.append(f"\n(mostrando {shown} de {total}; sube `limite` o filtra por "
                   "procedencia/tipo_relacion para acotar.)")
    return "\n".join(out)


@mcp.tool(annotations=_ro("Camino entre dos entidades"))
@_guard
def camino_entre(
    desde_id: Annotated[str, Field(description="id de la entidad de origen (de buscar_entidades)")],
    hasta_id: Annotated[str, Field(description="id de la entidad de destino (de buscar_entidades)")],
    max_saltos: Annotated[int, Field(description="longitud máx. del camino (se acota a 6)")] = 4,
) -> str:
    """Cadenas de conexión más cortas entre dos entidades del grafo (ids de
    buscar_entidades). Úsala para responder "¿qué relación hay entre X e Y?" cuando
    no están conectadas directamente. Cada salto incluye su evidencia citable."""
    max_saltos = max(1, min(max_saltos, 6))
    if desde_id == hasta_id:
        return "Indica dos entidades distintas."
    res = graph_queries.paths_between(desde_id, hasta_id, max_saltos, max_paths=5)
    if not res["meta"].get("found"):
        return (f"Sin camino de ≤{max_saltos} saltos entre '{desde_id}' y '{hasta_id}' "
                "(o algún id no existe).")
    names = {n["id"]: n["name"] + _seud(n) for n in res["nodes"]}
    # Index display links both ways: paths are undirected.
    by_pair: dict[tuple[str, str], dict] = {}
    for l in res["links"]:
        by_pair.setdefault((l["source"], l["target"]), l)
        by_pair.setdefault((l["target"], l["source"]), l)
    out: list[str] = []
    for i, seq in enumerate(res["paths"], 1):
        steps: list[str] = []
        evid: list[str] = []
        for a, b in zip(seq, seq[1:]):
            link = by_pair.get((a, b))
            rel = link["type"] if link else "?"
            steps.append(f"{names.get(a, a)} -[{rel}]-")
            if link and link.get("quote"):
                tag = _kind_tag(link.get("source_kind"))
                estado = graph_queries.estado_tag(link.get("status"), link.get("status_fuente_tipo"),
                                                  link.get("status_fecha"), link.get("status_fuente"))
                evid.append(f"  · {names.get(a, a)} → {names.get(b, b)}: "
                            f"«{_quote(link['quote'], 200)}» "
                            f"{_cite(link.get('doc'), link.get('page'))}{tag}{estado}")
        steps.append(names.get(seq[-1], seq[-1]))
        out.append(f"Camino {i}: " + "> ".join(steps))
        out.extend(evid)
    return "\n".join(out)


@mcp.tool(annotations=_ro("Evidencia de una relación"))
@_guard
def evidencia_relacion(
    desde_id: Annotated[str, Field(description="id de la entidad de origen (de buscar_entidades)")],
    hasta_id: Annotated[str, Field(description="id de la entidad de destino (de buscar_entidades)")],
    tipo: Annotated[str, Field(description="tipo de relación en MAYÚSCULAS, p.ej. PAID, ACCUSED_OF, AWARDED_TO")],
    procedencia: Annotated[list[Procedencia] | None,
                           Field(description="filtra por procedencia de la evidencia")] = None,
    desde_anio: Annotated[int | None, Field(description="año inicial AAAA (inclusive)")] = None,
    hasta_anio: Annotated[int | None, Field(description="año final AAAA (inclusive)")] = None,
    offset: Annotated[int, Field(description="desplazamiento para paginar (página de 25)")] = 0,
) -> str:
    """Toda la evidencia detrás de una relación concreta entre dos entidades
    (tipo en MAYÚSCULAS, p.ej. PAID, ACCUSED_OF, AWARDED_TO — como aparece en
    relaciones_entidad o camino_entre). Cada ítem trae la cita textual ÍNTEGRA (sin
    truncar), [documento, p.N], fecha, importe, nota/url y procedencia. Filtra con
    `procedencia` (una o varias capas del enum del parámetro) y/o
    `desde_anio`/`hasta_anio` (año AAAA). Si hay muchas evidencias, se paginan: usa `offset` para
    pedir la siguiente página (igual que buscar_documentos)."""
    page_size = 25
    offset = max(0, offset)
    detail = graph_queries.relation_detail(
        desde_id, hasta_id, tipo, from_year=desde_anio, to_year=hasta_anio, kinds=procedencia)
    if detail is None:
        # Display links are directed; the user may have them reversed.
        detail = graph_queries.relation_detail(
            hasta_id, desde_id, tipo, from_year=desde_anio, to_year=hasta_anio, kinds=procedencia)
    if detail is None:
        return (f"No existe la relación {desde_id} -[{tipo}]-> {hasta_id}. "
                "Comprueba ids y tipo con relaciones_entidad.")
    total = detail["meta"]["total"]      # sin filtrar
    count = detail["meta"]["count"]      # tras procedencia / año
    shown = detail["evidence"][offset:offset + page_size]
    filtered = procedencia is not None or desde_anio is not None or hasta_anio is not None
    label = f"{count} de {total}" if filtered and count != total else f"{total}"
    out = [f"{detail['source']['name']}{_seud(detail['source'])} -[{detail['type']}]-> "
           f"{detail['target']['name']}{_seud(detail['target'])} ({label} evidencias)"]
    if count == 0:
        out.append("(Sin evidencias con esos filtros.)")
    for e in shown:
        bits: list[str] = []
        if e.get("date"):
            bits.append(str(e["date"]))
        elif e.get("fecha"):  # concesión BDNS sin date_norm (fuera de la vista dinero)
            bits.append(str(e["fecha"]))
        if e.get("amount") is not None:
            bits.append(f"{e['amount']} {e.get('currency') or 'EUR'}")
        elif e.get("importe") is not None:
            bits.append(f"{e['importe']} EUR")
        if e.get("instrumento"):
            bits.append(str(e["instrumento"]))
        if e.get("convocante_detalle"):
            bits.append(str(e["convocante_detalle"]))
        if e.get("n_contratos") is not None:  # agregado PLACSP (spec 046) / ampliada (spec 055)
            unidad = "contratos" if detail["type"] == "CONTRACTOR_OF" else "contratos menores"
            bits.append(f"{e['n_contratos']} {unidad}")
        if e.get("total_eur") is not None:
            bits.append(f"{_eur(e['total_eur'])} en total")
        if e.get("rango"):
            bits.append(str(e["rango"]))
        if e.get("organo_detalle"):
            bits.append(str(e["organo_detalle"]))
        bits.extend(_senales_bits(e))
        if e.get("fuente"):
            bits.append(f"fuente: {e['fuente']}")
        if e.get("status"):
            bits.append(graph_queries.estado_tag(e["status"], e.get("status_fuente_tipo"), e.get("status_fecha"),
                                                 e.get("status_fuente")).strip()[1:-1])
        kind = e.get("source_kind") or "sin_procedencia"
        if kind == "prensa" or kind == "ultima_hora":
            bits.append(f"según prensa: {e.get('outlet') or 'medio no indicado'}")
        elif kind == "contexto":
            bits.append("contexto público, sin fuente judicial")
        elif kind == "mercantil":
            bits.append("según registro mercantil / BORME, sin fuente judicial")
        elif kind == "subvencion":
            bits.append("según BDNS/SNPSAP — subvención o ayuda pública, legal; sin fuente judicial")
        elif kind == "contratacion":
            if detail["type"] == "CONTRACTOR_OF":
                bits.append("según PLACSP/plataformas oficiales — contratación pública agregada, legal; sin fuente judicial")
            else:
                bits.append("según PLACSP — contratación menor pública agregada, legal; sin fuente judicial")
        elif kind == "boe":
            bits.append("según BOE — nombramiento/cese oficial, acto administrativo legal; sin fuente judicial")
        elif kind == "acusacion":
            bits.append("registro curado de acusaciones, con cita judicial")
        elif kind != "judicial":
            bits.append("procedencia no registrada")
        meta = f" ({'; '.join(bits)})" if bits else ""
        quote = " ".join((e.get("quote") or "").split())  # íntegra, solo normaliza espacios
        cita = f"«{quote}» " if quote else ""  # sin cita servida (p. ej. relación con un familiar anonimizado)
        out.append(f"- {cita}{_cite(e.get('doc'), e.get('page'))}{meta}")
        if e.get("note"):
            out.append(f"  nota: {e['note']}")
        if e.get("url"):
            out.append(f"  url: {e['url']}")
        if e.get("ejemplo_link"):
            ej = f"  ejemplo: {e['ejemplo_link']}"
            if e.get("ejemplo_titulo"):
                ej += f" ({e['ejemplo_titulo']})"
            out.append(ej)
    end = offset + len(shown)
    if end < count:
        out.append(f"… {count - end} evidencias más; pide la siguiente página con "
                   f"offset={end} (o acota con procedencia / desde_anio / hasta_anio).")
    return "\n".join(out)


@mcp.tool(annotations=_ro("Flujos de dinero"))
@_guard
def flujos_dinero(
    entidad: Annotated[str | None,
                       Field(description="id (de buscar_entidades) como origen o destino del flujo")] = None,
    min_importe: Annotated[float | None, Field(description="importe mínimo en EUR")] = None,
    desde: Annotated[int | None, Field(description="año inicial AAAA (inclusive)")] = None,
    hasta: Annotated[int | None, Field(description="año final AAAA (inclusive)")] = None,
    limite: Annotated[int, Field(description="máx. flujos (se acota a 100)")] = 50,
) -> str:
    """Rastro de dinero: relaciones con importe (pagos, comisiones, adjudicaciones) ordenadas
    de mayor a menor. Acota con `entidad` (id de buscar_entidades, como origen o destino),
    `min_importe` (EUR) y/o el rango de años `desde`/`hasta`. Cada línea trae su cita y
    procedencia. Úsala para seguir el dinero (comisiones, entregas, préstamos)."""
    limite = max(1, min(limite, 100))
    rows = graph_queries.money_flows(entidad, min_importe, desde, hasta, limite)
    if not rows:
        return "Sin flujos de dinero para esos filtros."
    out: list[str] = []
    for r in rows:
        kind = r.get("source_kind") or "sin_procedencia"
        proc = ""
        if kind in ("prensa", "ultima_hora"):
            proc = f" (según prensa: {r.get('outlet') or 'medio no indicado'})"
        elif kind == "contexto":
            proc = " (contexto público)"
        elif kind == "mercantil":
            proc = " (según registro mercantil / BORME)"
        elif kind == "subvencion":
            proc = " (según BDNS/SNPSAP — subvención/ayuda pública)"
        elif kind == "contratacion":
            proc = " (según PLACSP/plataformas oficiales — contratación pública)"
        elif kind == "boe":
            proc = " (según BOE — nombramiento/cese oficial)"
        elif kind == "acusacion":
            proc = " (registro curado de acusaciones, con cita judicial)"
        elif kind != "judicial":
            proc = " (procedencia no registrada)"
        fecha = f" · {r['date']}" if r.get("date") and r["date"] != "null" else ""
        arrow = f"-[{r['type']}]->" if r.get("directed", True) else f"-[{r['type']}]- (dirección no determinada)"
        nsrc = r.get("sources") or 1
        corro = f" · {nsrc} fuentes" if nsrc > 1 else ""
        lbl = f" · {r['label']}" if r.get("label") else ""
        s_seud = " ⚠️ seudónimo judicial" if r.get("sourceSeud") else ""
        t_seud = " ⚠️ seudónimo judicial" if r.get("targetSeud") else ""
        out.append(f"- {r['amount']:,.2f} {r['currency']} · {r['source']}{s_seud} {arrow} "
                   f"{r['target']}{t_seud}{lbl}{fecha} {_cite(r.get('doc'), r.get('page'))}{proc}{corro}")
    return "\n".join(out)


@mcp.tool(annotations=_ro("Subvenciones y ayudas públicas (BDNS)"))
@_guard
def subvenciones(
    entidad: Annotated[str | None,
                       Field(description="id (de buscar_entidades) como beneficiaria o convocante")] = None,
    min_importe: Annotated[float | None, Field(description="importe mínimo en EUR")] = None,
    limite: Annotated[int, Field(description="máx. concesiones (se acota a 50)")] = 25,
) -> str:
    """Capa BDNS/SNPSAP completa: subvenciones, ayudas y avales públicos que constan en la
    Base de Datos Nacional de Subvenciones para entidades del grafo, INCLUIDAS las concesiones
    que no entran en flujos_dinero (avales y ayudas menores sin vista de dinero). Sin `entidad`
    lista toda la capa, ordenada por importe desc; acota con `entidad` (beneficiaria o
    convocante) y/o `min_importe` (EUR). Cada línea trae importe, instrumento, convocante con
    detalle, fecha y referencia BDNS. Dato oficial público: recibir una ayuda es LEGAL y no es
    hecho ni indicio del caso — sin cita [doc, p.N]. Presunción de inocencia."""
    limite = max(1, min(limite, 50))
    res = graph_queries.grants(entidad, min_importe, limite)
    rows, total, shown = res["rows"], res["total"], res["shown"]
    if not rows:
        return ("Sin concesiones BDNS para esos filtros."
                + (" ¿Es el id exacto? Usa buscar_entidades primero." if entidad else ""))
    out: list[str] = []
    for r in rows:
        if r.get("importe") is not None:
            imp = f"{r['importe']:,.2f} EUR".replace(",", "_").replace(".", ",").replace("_", ".")
        else:
            imp = "importe no registrado"
        instr = f" · {r['instrumento']}" if r.get("instrumento") else ""
        det = f" ({r['convocante_detalle']})" if r.get("convocante_detalle") else ""
        fecha = f" · {r['fecha']}" if r.get("fecha") else ""
        ref = r.get("bdns_endpoint") or (f"bdns_key {r['bdns_key']}" if r.get("bdns_key") else "")
        ref = f" · BDNS: {ref}" if ref else ""
        nota = f" · nota: {_quote(r['note'], 160)}" if r.get("note") else ""
        flujos = "" if r.get("en_flujos") else " · (fuera de la vista de flujos de dinero)"
        out.append(f"- {imp}{instr} · {r['beneficiario']} ← {r['convocante']}{det}"
                   f"{fecha}{ref}{nota}{flujos}")
    if total > shown:
        out.append(f"\n(mostrando {shown} de {total}; sube `limite` o filtra por "
                   "entidad/min_importe.)")
    out.append(
        "\n_BDNS/SNPSAP (público). Recibir una subvención, ayuda o aval es legal y NO es un "
        "hecho ni un indicio del caso; sin cita [doc, p.N]. La señal editorial es el cruce con "
        "la trama, nunca la ayuda en sí. Presunción de inocencia._"
    )
    return "\n".join(out)


def _eur(value) -> str:
    """1234567.8 → '1.234.568 EUR' (formato ES, sin decimales para agregados)."""
    return f"{value:,.0f} EUR".replace(",", ".")


@mcp.tool(annotations=_ro("Contratos menores (PLACSP)"))
@_guard
def contratos_menores(
    entidad: Annotated[str | None,
                       Field(description="id (de buscar_entidades): adjudicataria u órgano de contratación")] = None,
    limite: Annotated[int, Field(description="máx. relaciones destacadas (se acota a 50)")] = 25,
) -> str:
    """Capa PLACSP completa (contratación menor pública, spec 046): AGREGADOS oficiales de
    contratos menores 2018-2026 para empresas del grafo — relaciones destacadas
    adjudicataria↔órgano (nº contratos, total, rango, enlace de ejemplo a PLACSP), totales
    globales por empresa (también las que no tienen relación destacada) y los negativos
    informativos («sin contratos menores» en 8,5 años de dumps). Sin `entidad` lista toda la
    capa; con `entidad` (id de buscar_entidades) la acota como adjudicataria u órgano. Estos
    agregados NUNCA aparecen en flujos_dinero ni en la cronología. Dato oficial público:
    adjudicar o recibir un contrato menor es LEGAL y no es hecho ni indicio del caso — sin
    cita [doc, p.N]. Presunción de inocencia."""
    limite = max(1, min(limite, 50))
    res = graph_queries.contratacion_layer(entidad, limite)
    rows, total, shown = res["rows"], res["total"], res["shown"]
    adjs, negs = res["adjudicatarias"], res["sin_menores"]
    if not rows and not adjs and not negs:
        return ("Sin datos de contratación menor PLACSP para esos filtros."
                + (" ¿Es el id exacto? Usa buscar_entidades primero." if entidad else ""))
    out: list[str] = []
    if rows:
        out.append("## Relaciones destacadas adjudicataria → órgano (agregados PLACSP)")
        for r in rows:
            det = f" ({r['organo_detalle']})" if r.get("organo_detalle") else ""
            ute = f" · vía {r['via_ute']}" if r.get("via_ute") else ""
            imp = _eur(r["total_eur"]) if r.get("total_eur") is not None else "importe no agregado"
            rango = f" · {r['rango']}" if r.get("rango") else ""
            nota = f" · nota: {_quote(r['note'], 160)}" if r.get("note") else ""
            out.append(f"- {r['adjudicataria']} → {r['organo']}{det}: "
                       f"{r.get('n_contratos') or '?'} contratos menores · {imp}{rango}{ute}{nota}")
            ej = _ejemplo_line(r)
            if ej:
                out.append(ej)
        if total > shown:
            out.append(f"(mostrando {shown} de {total}; sube `limite` o filtra por entidad.)")
    if adjs:
        out.append("\n## Totales globales por adjudicataria (props de nodo)")
        for a in sorted(adjs, key=lambda x: -(x.get("total_eur") or 0)):
            imp = _eur(a["total_eur"]) if a.get("total_eur") is not None else "?"
            rango = f" · {a['rango']}" if a.get("rango") else ""
            orgs = f" · órganos top: {a['organos_top']}" if a.get("organos_top") else ""
            nota = f" · nota: {_quote(a['nota'], 200)}" if a.get("nota") else ""
            out.append(f"- {a['entidad']}: {a.get('n_contratos', '?')} contratos menores · "
                       f"{imp}{rango}{orgs}{nota}")
    if negs:
        out.append("\n## Sin contratos menores en el barrido (negativo informativo)")
        for n in negs:
            nota = f" — {n['nota']}" if n.get("nota") else ""
            out.append(f"- {n['entidad']}{nota}")
    out.append(
        "\n_PLACSP (público, dumps 2018-2026). Adjudicar o recibir un contrato menor es legal "
        "y NO es un hecho ni un indicio del caso; sin cita [doc, p.N] (el enlace de ejemplo va "
        "a PLACSP). La señal editorial es el cruce con el reparto del caso, nunca el contrato "
        "en sí. «Sin contratos menores» es un negativo informativo, no una sospecha. "
        "Presunción de inocencia._"
    )
    return "\n".join(out)


_AMBITO_LABEL = {
    "lic": "licitaciones PLACSP",
    "agr": "licitaciones (plataformas agregadas)",
    "ccaa_andalucia": "menores Junta de Andalucía",
    "ccaa_euskadi": "menores Euskadi",
    "ccaa_galicia": "menores Xunta de Galicia",
}


def _senales_bits(props: dict) -> list[str]:
    """Procurement-signal bits (ampliada 055) — the ONE wording, shared by
    evidencia_relacion and contratacion_publica so the two tools never drift."""
    bits: list[str] = []
    if props.get("n_emergencia"):
        bits.append(f"{props['n_emergencia']} en tramitación de emergencia")
    if props.get("n_negociado_sp"):
        bits.append(f"{props['n_negociado_sp']} por negociado sin publicidad")
    if props.get("presupuesto_total_eur") is not None:
        bits.append(f"presupuesto {_eur(props['presupuesto_total_eur'])}")
    if props.get("via_ute"):
        bits.append(f"vía {props['via_ute']}")
    return bits


def _ejemplo_line(row: dict) -> str | None:
    """The '  ejemplo: <link> (<título>)' line shared by the three layer tools."""
    if not row.get("ejemplo_link"):
        return None
    ej = f"  ejemplo: {row['ejemplo_link']}"
    if row.get("ejemplo_titulo"):
        ej += f" ({row['ejemplo_titulo']})"
    return ej


def _amp_ambitos_str(ambitos: dict) -> str:
    """Render the amp_<ambito>_* node props as 'licitaciones PLACSP: 147 · 573.419.415 EUR · rango'.
    An ámbito renders if ANY of its props is present (a curated total without `n`
    must never become invisible)."""
    parts: list[str] = []
    for amb, label in _AMBITO_LABEL.items():
        n = ambitos.get(f"amp_{amb}_n")
        tot = ambitos.get(f"amp_{amb}_total_eur")
        rango = ambitos.get(f"amp_{amb}_rango")
        nota = ambitos.get(f"amp_{amb}_nota")
        if n is None and tot is None and not rango and not nota:
            continue
        bit = f"{label}: {n if n is not None else '?'}"
        if tot is not None:
            bit += f" · {_eur(tot)}"
        if rango:
            bit += f" · {rango}"
        if nota:
            bit += f" ({nota})"
        parts.append(bit)
    return " | ".join(parts)


@mcp.tool(annotations=_ro("Contratación pública ampliada (PLACSP + CCAA)"))
@_guard
def contratacion_publica(
    entidad: Annotated[str | None,
                       Field(description="id (de buscar_entidades): adjudicataria u órgano de contratación")] = None,
    limite: Annotated[int, Field(description="máx. relaciones destacadas (se acota a 50)")] = 25,
) -> str:
    """Capa de contratación pública AMPLIADA (spec 055): AGREGADOS oficiales de
    licitaciones de PLACSP (2012-2026), licitaciones de plataformas autonómicas/locales
    agregadas a PLACSP y contratos menores autonómicos (Junta de Andalucía, Euskadi,
    Xunta de Galicia) para empresas del grafo — relaciones destacadas
    adjudicataria↔órgano con señales de procedimiento (nº en emergencia, nº por
    negociado sin publicidad, vía UTE, presupuesto vs adjudicado, fuente y enlace de
    ejemplo), totales por ámbito de cada empresa (también sin relación destacada) y los
    negativos informativos («sin adjudicaciones directas» en todo el barrido). Los
    contratos MENORES estatales tienen su propia tool (contratos_menores, overlay 046).
    Sin `entidad` lista toda la capa; con `entidad` (id de buscar_entidades) la acota.
    Estos agregados NUNCA aparecen en flujos_dinero ni en la cronología. Dato oficial
    público: adjudicarse una licitación o un menor es LEGAL — también en emergencia o
    por negociado, procedimientos previstos por la ley — y no es hecho ni indicio del
    caso; sin cita [doc, p.N]. Presunción de inocencia."""
    limite = max(1, min(limite, 50))
    res = graph_queries.contratacion_ampliada_layer(entidad, limite)
    rows, total, shown = res["rows"], res["total"], res["shown"]
    adjs, negs = res["adjudicatarias"], res["sin_adjudicaciones"]
    if not rows and not adjs and not negs:
        return ("Sin datos de contratación pública ampliada para esos filtros."
                + (" ¿Es el id exacto? Usa buscar_entidades primero." if entidad else ""))
    out: list[str] = []
    if rows:
        out.append("## Relaciones destacadas adjudicataria → órgano (agregados oficiales)")
        for r in rows:
            det = f" ({r['organo_detalle']})" if r.get("organo_detalle") else ""
            amb = _AMBITO_LABEL.get(r.get("ambito"), r.get("ambito") or "?")
            imp = _eur(r["total_eur"]) if r.get("total_eur") is not None else "importe no agregado"
            rango = f" · {r['rango']}" if r.get("rango") else ""
            senales = _senales_bits(r)
            sen = (" · " + " · ".join(senales)) if senales else ""
            nota = f" · nota: {_quote(r['note'], 200)}" if r.get("note") else ""
            fuente = f" · fuente: {r['fuente']}" if r.get("fuente") else ""
            out.append(f"- {r['adjudicataria']} → {r['organo']}{det} [{amb}]: "
                       f"{r.get('n_contratos') or '?'} contratos · {imp}{rango}{sen}{fuente}{nota}")
            ej = _ejemplo_line(r)
            if ej:
                out.append(ej)
        if total > shown:
            out.append(f"(mostrando {shown} de {total}; sube `limite` o filtra por entidad.)")
    if adjs:
        out.append("\n## Totales por ámbito de cada adjudicataria (props de nodo)")
        for a in adjs:
            ambs = _amp_ambitos_str(a.get("ambitos") or {})
            nota = f" · nota: {_quote(a['nota'], 240)}" if a.get("nota") else ""
            den = f" ({a['denominacion']})" if a.get("denominacion") else ""
            out.append(f"- {a['entidad']}{den}: {ambs or 'sin totales'}{nota}")
    if negs:
        out.append("\n## Sin adjudicaciones directas en el barrido (negativo informativo)")
        for n in negs:
            nota = f" — {n['nota']}" if n.get("nota") else ""
            out.append(f"- {n['entidad']}{nota}")
    out.append(
        "\n_Fuentes oficiales públicas: PLACSP (licitaciones 2012-2026 y plataformas "
        "agregadas 2016-2026), datos abiertos de la Junta de Andalucía, api.euskadi.eus y "
        "contratosdegalicia.gal (importes gallegos normalizados s/IVA; la fuente publica "
        "c/IVA). Adjudicarse una licitación o un contrato menor es legal — también en "
        "emergencia o por negociado, procedimientos previstos por la ley — y NO es un hecho "
        "ni un indicio del caso; sin cita [doc, p.N] (cada relación lleva su fuente y "
        "enlace). La señal editorial es el cruce con el reparto del caso y el patrón de "
        "procedimiento, nunca la adjudicación en sí. «Sin adjudicaciones directas» es un "
        "negativo informativo, no una sospecha; la ausencia no exculpa y la presencia no "
        "imputa. Presunción de inocencia._"
    )
    return "\n".join(out)


@mcp.tool(annotations=_ro("Trayectoria BOE (cargos públicos)"))
@_guard
def trayectoria_boe(
    entidad: Annotated[str | None,
                       Field(description="id (de buscar_entidades): persona u organismo público")] = None,
    limite: Annotated[int, Field(description="máx. cargos mostrados (se acota a 100)")] = 25,
) -> str:
    """Capa BOE completa (trayectorias de cargos públicos, spec 049): nombramientos y ceses
    de la sección II.A del BOE (2004→hoy) para personas del grafo, agregados por cargo —
    cada etapa lleva su disposición citable (id BOE-A-… + enlace) de nombramiento y, si
    consta, de cese; más el resumen de trayectoria por persona (nº de cargos, rango) y sus
    incidencias administrativas (acuerdos CGPJ, excedencias — sin relación en el grafo).
    Sin `entidad` lista toda la capa; con `entidad` (id de buscar_entidades) la acota como
    persona u organismo. Estas etapas NUNCA aparecen en flujos_dinero ni en la cronología.
    Un cargo sin fecha de cese = «sin cese registrado en esta capa» (NO necesariamente
    vigente: la semilla no cubre RD colectivos como los de jun-2018 ni ceses pre-2004).
    Dato oficial público: un nombramiento o un cese es un acto administrativo LEGAL y no es
    hecho ni indicio del caso — sin cita [doc, p.N]. Presunción de inocencia."""
    limite = max(1, min(limite, 100))
    res = graph_queries.boe_layer(entidad, limite)
    rows, total, shown, personas = res["rows"], res["total"], res["shown"], res["personas"]
    if not rows and not personas:
        return ("Sin trayectoria BOE para esos filtros."
                + (" ¿Es el id exacto? Usa buscar_entidades primero." if entidad else ""))
    out: list[str] = []
    if rows:
        out.append("## Etapas persona → organismo (sección II.A del BOE)")
        for r in rows:
            desde = r.get("fecha_nombramiento") or "¿?"
            hasta = r.get("fecha_cese") or "sin cese registrado en esta capa"
            cita_n = (f" [{r['boe_id_nombramiento']}]({r['url_nombramiento']})"
                      if r.get("boe_id_nombramiento") else "")
            cita_c = (f" · cese [{r['boe_id_cese']}]({r['url_cese']})"
                      if r.get("boe_id_cese") else "")
            nota = f" · nota: {_quote(r['nota'], 200)}" if r.get("nota") else ""
            out.append(f"- {r['persona']} — {r['cargo']} ({r['organismo']}): "
                       f"{desde} → {hasta}{cita_n}{cita_c}{nota}")
        if total > shown:
            out.append(f"(mostrando {shown} de {total}; sube `limite` o filtra por entidad.)")
    if personas:
        out.append("\n## Resumen de trayectoria por persona (props de nodo)")
        for p in sorted(personas, key=lambda x: -(x.get("n_cargos") or 0)):
            rango = f" · {p['rango']}" if p.get("rango") else ""
            nota = f" · nota: {_quote(p['nota'], 200)}" if p.get("nota") else ""
            out.append(f"- {p['persona']}: {p.get('n_cargos', '?')} cargos en la capa{rango}{nota}")
            if p.get("incidencias"):
                for line in p["incidencias"].splitlines():
                    out.append(f"  · incidencia (sin relación en el grafo): {line}")
    out.append(
        "\n_BOE sección II.A (público, ventana 2004→hoy; solo personas ya presentes en el "
        "grafo). Un nombramiento o un cese es un acto administrativo legal y NO es un hecho "
        "ni un indicio del caso; sin cita [doc, p.N] (se cita la disposición BOE-A-… con su "
        "enlace). La señal editorial es la trayectoria cruzada con el reparto del caso — "
        "quién dirigía qué organismo cuándo —, nunca el cargo en sí. Un cargo sin cese en la "
        "capa NO se debe presentar como «vigente». Presunción de inocencia._"
    )
    return "\n".join(out)


@mcp.tool(annotations=_ro("Patrones llamativos (capa editorial)"))
@_guard
def patrones_llamativos(
    slug: Annotated[str | None,
                    Field(description="slug de un patrón (de la lista sin argumentos) para el detalle completo")] = None,
) -> str:
    """Patrones documentados LEGALES pero llamativos (spec 053), sin foco mediático ni
    judicial conocido: racimos de contratos menores al filo del tope del art. 118 LCSP,
    puertas giratorias, capital desproporcionado, el menor usado como bolsa de gasto…
    Capa EDITORIAL curada del proyecto (cruce BORME × PLACSP × corpus), NO datos del grafo
    ni hechos del caso. Sin `slug` lista los patrones publicados; con `slug`, el detalle
    completo: tesis, hitos fechados con su fuente, contratos itemizados y fuentes
    verificables. Nada de lo descrito es ilegal por sí solo ni está bajo investigación
    conocida; atribuye SIEMPRE como «patrón editorial de El Entramado» y reproduce su
    disclaimer. Presunción de inocencia."""
    pats = patrones.load_patrones()
    if not pats:
        return "No hay patrones publicados."
    if slug is None:
        out = ["## Patrones llamativos publicados (capa editorial)"]
        for p in pats:
            out.append(f"- `{p['slug']}` · {p['titulo']} [{', '.join(p['tipos'])}]")
            out.append(f"  {p['tesis']}")
            out.append(f"  Estado: {p['estado']} (a {p['estado_fecha']})")
        out.append("\n_Capa editorial del proyecto: patrones legales-pero-llamativos SIN "
                   "foco mediático ni judicial conocido. No son hechos ni indicios "
                   "judiciales. Detalle con `slug`. Presunción de inocencia._")
        return "\n".join(out)
    p = patrones.get_patron(slug)
    if p is None:
        disponibles = ", ".join(x["slug"] for x in pats)
        return f"No existe el patrón '{slug}'. Disponibles: {disponibles}."
    fuentes = {f["id"]: f for f in p["fuentes"]}

    def _cite(fid: str) -> str:
        f = fuentes[fid]
        return f["etiqueta"] if not f.get("doc") else f"{f['doc']}, p.{f.get('pagina', '?')}"

    out = [f"# {p['titulo']}", f"⚖️ {p['disclaimer']}", "",
           f"Tesis: {p['tesis']}",
           f"Tipos: {', '.join(p['tipos'])} · Estado: {p['estado']} (a {p['estado_fecha']})"]
    if p["entities"]:
        out.append("Entidades del grafo: " + ", ".join(p["entities"])
                   + " (usa ficha_entidad para su estado procesal)")
    if p["actores_sin_grafo"]:
        out.append("Mencionados sin nodo en el grafo: " + "; ".join(p["actores_sin_grafo"]))
    out.append("\n## Hitos (cada uno con su fuente)")
    for h in p["hitos"]:
        refs = "; ".join(_cite(r) for r in h["fuentes"])
        out.append(f"- {h['fecha']}: {h['hecho']} [{refs}]")
    if p["contratos"]:
        out.append(f"\n## Contratos itemizados ({len(p['contratos'])})")
        for c in p["contratos"]:
            imp = _eur(c["importe_eur"]) if c.get("importe_eur") is not None else "?"
            via = f" · {c['via']}" if c.get("via") else ""
            nota = f" · NOTA: {c['nota']}" if c.get("nota") else ""
            out.append(f"- {c['fecha']} · {imp} · {c['objeto']} · {c['organo']} · "
                       f"exp. {c['referencia']}{via}{nota}")
    if p.get("contratos_resumen"):
        out.append("\n## Serie resumida")
        for r in p["contratos_resumen"]:
            imp = _eur(r["total_eur"]) if r.get("total_eur") is not None else "?"
            out.append(f"- {r['periodo']}: {r['n_contratos']} contratos · {imp}")
    if p.get("contratos_nota"):
        out.append(f"\nNota sobre los contratos: {p['contratos_nota']}")
    if p.get("nota_negativos"):
        out.append(f"\nNegativos informativos: {p['nota_negativos']}")
    out.append("\n## Fuentes")
    for f in p["fuentes"]:
        dest = f.get("url") or f"{f.get('doc')}, p.{f.get('pagina', '?')}"
        out.append(f"- {f['etiqueta']} — {f['acredita']} ({dest})")
    out.append("\n_Patrón editorial de El Entramado: hechos legales por sí solos, contados "
               "juntos por su interés público. NO es un hecho ni un indicio judicial y nadie "
               "está acusado por esto. Presunción de inocencia._")
    return "\n".join(out)


@mcp.tool(annotations=_ro("Capa mercantil (BORME)"))
@_guard
def datos_mercantil(
    id: Annotated[str | None,
                  Field(description="id (de buscar_entidades): empresa o persona; omítelo para ver la capa completa")] = None,
    tipo_relacion: Annotated[str | None,
        Field(description="un tipo concreto en MAYÚSCULAS, p.ej. ADMINISTRA, CONTROLS")] = None,
    limite: Annotated[int, Field(description="máx. aristas (se acota a 50)")] = 25,
) -> str:
    """Capa mercantil (BORME, vía Nearius) consolidada. Con `id` de una EMPRESA devuelve sus
    datos registrales (NIF, hoja, capital, CNAE, domicilio, concurso/disolución…) más sus
    administradores y socios; con `id` de una PERSONA, las empresas que administra o participa.
    Sin `id`, panorámica de la capa completa (nº de entidades con datos registrales y todas las
    aristas societarias). Filtra con `tipo_relacion` (ADMINISTRA, CONTROLS…). Estructura
    societaria oficial, NO hecho ni indicio del caso — se cita el borme_id, nunca [doc, p.N].
    El BORME capta administradores registrales, no la titularidad real en la sombra: no imputa
    ni exculpa. Presunción de inocencia."""
    limite = max(1, min(limite, 50))
    out: list[str] = []
    # _mercantil_block already ends in the BORME disclaimer; only append it
    # ourselves when that block wasn't rendered.
    disclaimed = False
    if id:
        detail = graph_queries.node_detail(id)
        if not detail:
            return f"No existe la entidad '{id}'. Usa buscar_entidades para obtener el id exacto."
        node = detail["node"]
        out.append(f"# {node['name']} ({node['type']})")
        if node.get("mercantil"):
            out.append(_mercantil_block(node["mercantil"]))
            disclaimed = True
        else:
            out.append("(Sin ficha registral BORME cargada para esta entidad.)")
    res = graph_queries.mercantil_layer(id, tipo_relacion, limite)
    rows, total, shown = res["rows"], res["total"], res["shown"]
    if not id:
        out.append(f"Capa mercantil: {res['registry_nodes']} entidades con ficha registral "
                   f"BORME · {total} aristas societarias"
                   + (f" de tipo {tipo_relacion}" if tipo_relacion else "") + ".")
    if rows:
        out.append("\n## Aristas societarias (según registro mercantil / BORME)")
        for r in rows:
            rol = f" ({r['rol']})" if r.get("rol") else ""
            desde = f" · desde {r['cargo_desde']}" if r.get("cargo_desde") else ""
            borme = f" · {r['borme_id']}" if r.get("borme_id") else ""
            nota = f" · nota: {_quote(r['note'], 160)}" if r.get("note") else ""
            out.append(f"- {r['desde']} -[{r['tipo']}]-> {r['hasta']}{rol}{desde}{borme}{nota}")
        if total > shown:
            out.append(f"\n(mostrando {shown} de {total}; sube `limite` o filtra por "
                       "tipo_relacion.)")
    elif id:
        out.append("\nSin aristas societarias BORME para esta entidad"
                   + (f" con tipo {tipo_relacion}" if tipo_relacion else "") + ".")
    if not disclaimed:
        out.append(
            "\n_Registro Mercantil (público, vía Nearius). Estructura societaria oficial, NO un "
            "hecho ni un indicio del caso; sin cita [doc, p.N]. El BORME capta administradores "
            "registrales, no la titularidad real en la sombra: no imputa ni exculpa. Presunción "
            "de inocencia._"
        )
    return "\n".join(out)


@mcp.tool(annotations=_ro("Métricas de centralidad"))
@_guard
def metricas_centralidad(
    limite: Annotated[int, Field(description="máx. nodos (se acota a 50)")] = 20,
    tipo: Annotated[EntityLabel | None, Field(description="acota a un solo tipo de nodo")] = None,
    metrica: Annotated[Metrica, Field(description="métrica de centralidad a ordenar")] = "grado",
) -> str:
    """Nodos más centrales del grafo según `metrica`, opcionalmente de un solo `tipo`
    (Person/Organization/Company/…). Métricas:
    - `grado` (por defecto): nº de conexiones — quién aparece más conectado.
    - `intermediacion`: betweenness — nodos-PUENTE que conectan sub-tramas distintas
      (un intermediario clave puede tener grado modesto pero intermediación alta).
    - `cercania`: closeness — quién está a pocos pasos del resto del grafo.
    Cada fila muestra el score de la métrica pedida Y el grado, para contrastar
    "muy conectado" vs "muy puente". intermediacion/cercania se precalculan offline
    (`make centralidad`); si aún no se han calculado, la herramienta lo avisa."""
    limite = max(1, min(limite, 50))
    rows = graph_queries.centrality(limite, tipo, metric=metrica)
    if not rows:
        if metrica != "grado":
            return (f"Sin datos de '{metrica}' todavía: ejecuta `make centralidad` para "
                    "calcular betweenness/cercanía. (El grado sí está disponible.)")
        return "Sin nodos para ese filtro."
    def _seud(r: dict) -> str:
        return " ⚠️ seudónimo judicial" if r.get("isPseudonym") else ""
    if metrica == "grado":
        return "\n".join(
            f"- {int(r['score'])} conexiones · {r['name']} ({r['type']}){_seud(r)} — id: {r['id']}"
            for r in rows
        )
    etiqueta = "intermediación" if metrica == "intermediacion" else "cercanía"
    return "\n".join(
        f"- {etiqueta} {r['score']:.4f} · {int(r['degree'])} conexiones · "
        f"{r['name']} ({r['type']}){_seud(r)} — id: {r['id']}"
        for r in rows
    )


@mcp.tool(annotations=_ro("Comunidades"))
@_guard
def comunidades(
    tipo: Annotated[EntityLabel | None,
                    Field(description="acota los miembros listados a un tipo de nodo")] = None,
    algoritmo: Annotated[Literal["louvain"] | None,
                         Field(description="reservado para futuras variantes; hoy solo Louvain")] = None,
    limite: Annotated[int, Field(description="máx. comunidades (se acota a 50)")] = 20,
) -> str:
    """Sub-tramas del grafo detectadas por detección de comunidades (Louvain). Úsala
    para que las sub-redes emerjan solas (p. ej. ERE, rescates SEPI/Plus Ultra/Air
    Europa, Koldo-Ábalos-Aldama, fontaneros/Leire Díez). Devuelve las comunidades más
    grandes con una etiqueta tentativa (sus miembros principales) y su tamaño. `tipo`
    acota los miembros listados a una etiqueta; `algoritmo` se reserva para futuras
    variantes (hoy: Louvain). Se precalcula offline (`make centralidad`); si aún no se
    ha calculado, lo avisa. La pertenencia a una comunidad es estructural (co-aparición
    en el grafo), NO una imputación: no implica culpabilidad ni vínculo probado."""
    limite = max(1, min(limite, 50))
    rows = graph_queries.communities(etype=tipo, limit=limite)
    if not rows:
        return ("Sin comunidades calculadas todavía: ejecuta `make centralidad` "
                "(detección Louvain) para poblarlas.")
    out: list[str] = []
    for r in rows:
        out.append(f"Comunidad {r['community']} ({r['size']} miembros) — {r['label']}")
        for m in r["members"]:
            seud = " ⚠️ seudónimo judicial" if m.get("isPseudonym") else ""
            out.append(f"  · {m['name']} ({m['type']}, {int(m['degree'])} conexiones){seud} "
                       f"— id: {m['id']}")
    return "\n".join(out)


def _fmt_claim(c: dict) -> str:
    """Render a disputed claim: the statement + each posture (afirma/niega) with
    its cited, attributed source. Presunción de inocencia is explicit."""
    head = f"AFIRMACIÓN EN DISPUTA — {c['text']}"
    if c.get("topic"):
        head += f"  ({c['topic']})"
    head += f"  [claim_id: {c['claimId']}]"
    lines = [head]
    for p in c.get("postures", []):
        verbo = "AFIRMA" if p["stance"] == "asserts" else "NIEGA"
        kind = p.get("source_kind") or "sin_procedencia"
        if kind in ("prensa", "ultima_hora"):
            proc = f" (según prensa: {p.get('outlet') or 'medio no indicado'})"
            cita = f"«{_quote(p.get('quote'), 400)}»" if p.get("quote") else ""
        elif kind == "contexto":
            proc = " (contexto público)"
            cita = p.get("quote") or ""
        elif kind == "mercantil":
            proc = " (según registro mercantil / BORME)"
            cita = p.get("quote") or ""
        elif kind == "contratacion":
            proc = " (según PLACSP/plataformas oficiales — contratación pública)"
            cita = p.get("quote") or ""
        elif kind == "boe":
            proc = " (según BOE — nombramiento/cese oficial)"
            cita = p.get("quote") or ""
        else:
            proc = ""
            cita = (f"«{_quote(p.get('quote'), 400)}» " if p.get("quote") else "") + _cite(p.get("doc"), p.get("page"))
        fecha = f" · {p['date']}" if p.get("date") and p["date"] != "null" else ""
        quien = p.get("entityName") or p.get("entityId")
        lines.append(f"  {verbo} · {quien}{fecha}: {cita}{proc}".rstrip())
    return "\n".join(lines)


@mcp.tool(annotations=_ro("Afirmaciones en disputa (entidad)"))
@_guard
def claims_entidad(
    id: Annotated[str, Field(description="id de la entidad (de buscar_entidades)")],
) -> str:
    """Afirmaciones EN DISPUTA en las que participa una entidad (id de buscar_entidades): una
    postura y su contraria, cada una atribuida a su fuente. Presunción de inocencia: una acusación
    es alegación —se muestra junto al desmentido, nunca como hecho probado. Úsala para ver
    acusación vs negación sobre una persona; profundiza con evidencia_claim(claim_id)."""
    rows = graph_queries.entity_claims(id)
    if not rows:
        return (f"Sin afirmaciones en disputa para '{id}'. ¿Es el id exacto? Usa buscar_entidades.")
    return "\n\n".join(_fmt_claim(c) for c in rows)


@mcp.tool(annotations=_ro("Evidencia de una afirmación"))
@_guard
def evidencia_claim(
    claim_id: Annotated[str, Field(description="claim_id de una afirmación, obtenido con claims_entidad")],
) -> str:
    """Una afirmación en disputa concreta (claim_id, de claims_entidad) con TODAS sus posturas
    (quién la afirma vs quién la niega) y sus fuentes citadas. Presunción de inocencia."""
    c = graph_queries.claim_detail(claim_id)
    if c is None:
        return f"No existe el claim '{claim_id}'. Usa claims_entidad para encontrar el id."
    return _fmt_claim(c)


@mcp.tool(annotations=_ro("Cronología"))
@_guard
def cronologia(
    id: Annotated[str | None, Field(description="id de una entidad para acotar (de buscar_entidades)")] = None,
    desde_anio: Annotated[int | None, Field(description="año inicial AAAA (inclusive)")] = None,
    hasta_anio: Annotated[int | None, Field(description="año final AAAA (inclusive)")] = None,
    limite: Annotated[int, Field(description="máx. hechos (se acota a 100)")] = 50,
) -> str:
    """Cronología de hechos fechados del grafo, opcionalmente acotada a una entidad
    (su `id`) y/o a un rango de años. Úsala para "¿qué pasó en 2020?" o "cronología
    del caso X". Cada línea trae su cita [documento, p.N]."""
    limite = max(1, min(limite, 100))
    events = graph_queries.timeline(id, desde_anio, hasta_anio, None, limite)
    if not events:
        return "Sin hechos fechados para esos filtros."
    out: list[str] = []
    for e in events:
        amount = f" ({e['amount']} {e.get('currency') or 'EUR'})" if e.get("amount") is not None else ""
        out.append(f"- {e['date']} · {e['source']['name']} -[{e['type']}]-> "
                   f"{e['target']['name']}{amount} {_cite(e.get('doc'), e.get('page'))}")
    return "\n".join(out)


@mcp.tool(annotations=_ro("Buscar en documentos"))
@_guard
def buscar_documentos(
    consulta: Annotated[str, Field(description="texto a buscar en el corpus documental")],
    limite: Annotated[int, Field(description="máx. fragmentos (se acota a 10)")] = 6,
    offset: Annotated[int, Field(description="desplazamiento DENTRO del texto de cada fragmento (offset=800 continúa donde cortó)")] = 0,
    tipo_documento: Annotated[list[TipoDocumento] | None,
                              Field(description="acota por una o varias clases de documento")] = None,
    desde_fecha: Annotated[str | None, Field(description="fecha inicial AAAA-MM-DD (solo docs con fecha curada)")] = None,
    hasta_fecha: Annotated[str | None, Field(description="fecha final AAAA-MM-DD (solo docs con fecha curada)")] = None,
    entidad_mencionada: Annotated[str | None,
                                  Field(description="id (de buscar_entidades) para acotar a fragmentos que la mencionan")] = None,
) -> str:
    """Búsqueda de texto completo en los documentos judiciales y policiales del corpus
    (autos, informes UCO, transcripciones, auditorías). Úsala cuando necesites el texto
    literal de los documentos — no el grafo. Devuelve fragmentos con [documento, p.N].
    `offset` pagina dentro del texto de cada fragmento (offset=800 continúa donde cortó);
    para el pasaje íntegro de una página usa leer_documento. Filtra con `tipo_documento`
    (auto_judicial|informe_uco|transcripcion_parlamentaria|escrito_acusacion|auditoria|otros),
    `desde_fecha`/`hasta_fecha` (AAAA-MM-DD) — solo afectan a documentos con metadatos curados —
    y `entidad_mencionada` (un id de buscar_entidades) para acotar a fragmentos que la mencionan."""
    limite = max(1, min(limite, 10))
    offset = max(0, offset)
    rows = graph_queries.keyword_search_chunks(
        consulta, limite, doc_types=tipo_documento, from_date=desde_fecha,
        to_date=hasta_fecha, mentions=entidad_mencionada)
    # Honest coverage: with a date filter active, count text matches dropped only
    # for lacking a curated doc_date (not for being out of range).
    sin_fecha = 0
    if desde_fecha or hasta_fecha:
        sin_fecha = graph_queries.count_chunks_missing_date(
            consulta, doc_types=tipo_documento, mentions=entidad_mencionada)
    aviso_fecha = (
        f"\n\n(Aviso: {sin_fecha} fragmento(s) que coinciden con la consulta quedaron FUERA "
        "del filtro de fecha por no tener fecha en sus metadatos curados, no por estar fuera "
        "de rango.)" if sin_fecha else "")
    if not rows:
        msg = "Sin resultados en los documentos."
        if tipo_documento or desde_fecha or hasta_fecha:
            msg += " (Recuerda: el filtro por tipo/fecha excluye documentos sin metadatos curados.)"
        return msg + aviso_fecha
    out: list[str] = []
    for r in rows:
        tag = f" [{r['docType']}]" if r.get("docType") else ""
        norm = " ".join((r.get("text") or "").split())
        seg = norm[offset:offset + 800]
        if not seg:
            body = "(fin del fragmento)"
        else:
            pre = "…" if offset else ""
            suf = "…" if offset + 800 < len(norm) else ""
            body = f"{pre}{seg}{suf}"
        out.append(f"{_cite(r['doc'], r.get('page'))}{tag}\n«{body}»")
    return "\n\n".join(out) + aviso_fecha


@mcp.tool(annotations=_ro("Leer documento"))
@_guard
def leer_documento(
    documento: Annotated[str, Field(description="título o doc_id tal como aparece en la cita [documento, p.N]")],
    pagina: Annotated[int | None, Field(description="nº de página a leer; sin ella lista las páginas con texto")] = None,
    ventana: Annotated[int, Field(description="páginas adyacentes a incluir a cada lado (se acota a 5)")] = 0,
) -> str:
    """Lee el texto íntegro de un documento del corpus por su título o doc_id (tal como
    aparece en las citas [documento, p.N]). Sin `pagina` lista las páginas con texto; con
    `pagina` devuelve el texto completo de esa página y ±`ventana` páginas adyacentes.
    Úsala para leer el pasaje entero detrás de una cita de evidencia_relacion / buscar_documentos."""
    ventana = max(0, min(ventana, 5))
    res = graph_queries.read_document(documento, pagina, ventana)
    if res is None:
        return (f"No encuentro el documento '{documento}'. Usa el título o el doc_id exacto "
                "tal como aparece en la cita [documento, p.N].")
    if res.get("candidates"):
        out = ["Varios documentos coinciden; precisa con el doc_id:"]
        out += [f"- {c['doc_id']} · {c['title']}" for c in res["candidates"]]
        return "\n".join(out)
    d = res["doc"]
    header = f"# {d['title']}" + (f" [{d['docType']}]" if d.get("docType") else "")
    if "pages" in res:
        pages = res["pages"]
        if not pages:
            return f"{header}\n(Sin texto indexado para este documento.)"
        rng = f"{pages[0]}–{pages[-1]}" if len(pages) > 1 else str(pages[0])
        return (f"{header}\nPáginas con texto: {rng} ({len(pages)} págs). "
                "Vuelve a llamar con `pagina=N` para leer una.")
    chunks = res.get("chunks") or []
    if not chunks:
        return f"{header}\nSin texto en la página {res['page']}."
    out, cur, body_chars, truncated = [header], None, 0, False
    MAX = 50_000
    for ch in chunks:
        if ch["page"] != cur:
            cur = ch["page"]
            out.append(f"\n[p.{cur}]")
        text = ch.get("text") or ""
        if body_chars + len(text) > MAX:
            out.append(text[: MAX - body_chars])
            truncated = True
            break
        out.append(text)
        body_chars += len(text)
    if truncated:
        out.append("\n[truncado]")
    return "\n".join(out)


@mcp.tool(annotations=_ro("Documentos recientes"))
@_guard
def documentos_recientes(
    limite: Annotated[int, Field(description="máx. documentos (se acota a 25)")] = 10,
    tipo: Annotated[TipoDocumento | None, Field(description="acota por clase de documento")] = None,
) -> str:
    """Documentos más recientes del corpus, ordenados por su fecha (AAAA-MM-DD). `tipo`
    acota por clase (auto_judicial|informe_uco|transcripcion_parlamentaria|escrito_acusacion|
    auditoria|otros). NB: ordena por la fecha curada del documento (no por fecha de ingesta) y
    solo lista los documentos que la tienen."""
    limite = max(1, min(limite, 25))
    rows = graph_queries.recent_documents(limite, tipo)
    if not rows:
        return ("Sin documentos con fecha. (La fecha solo está en documentos con metadatos "
                "curados; puede que aún no se haya anotado ninguno.)")
    out: list[str] = []
    for r in rows:
        tag = f" [{r['docType']}]" if r.get("docType") else ""
        out.append(f"- {r['date']} · {r['doc']}{tag} ({r['pageCount']} págs) — id: {r['file']}")
    return "\n".join(out)


_OCULTOS_TTL = 300.0
_ocultos_cache: tuple[float, tuple[frozenset[str], frozenset[str]]] | None = None


_VAULT_SIN_GRAFO = ("El vault no está disponible ahora mismo (no se puede contrastar con el grafo qué notas siguen "
                    "vigentes). Inténtalo de nuevo en unos minutos o usa buscar_documentos.")


def _titulos_ocultos() -> tuple[frozenset[str], frozenset[str]] | None:
    """(ids de las entidades cuya ficha no se sirve, nombres plegados de las entidades que tampoco se enlazan ni se nombran). El vault
    es una exportación más vieja que el grafo y no puede ser la vía por la que reaparece algo retirado.
    - No se sirve la ficha de una entidad que el grafo no sirve hoy: su id ya no existe (fusionada, renombrada o retirada),
      es una persona sin relaciones, un seudónimo o un familiar anonimizado (sus fichas citan fragmentos que el panel oculta).
    - Además, las demás notas se sirven sin los bloques que enlazan o nombran a una persona que existe y no se sirve: por el
      nombre y los alias que le da el grafo y, si el vault trae su ficha, por el título y los alias de esta.
    Si el grafo no responde devuelve None y las herramientas del vault no sirven nada: sin él no se sabe qué retirar."""
    global _ocultos_cache
    now = time.monotonic()
    if _ocultos_cache and now - _ocultos_cache[0] < _OCULTOS_TTL:
        return _ocultos_cache[1]
    try:
        notas = vault_entity_notes()
        ids = sorted(set(notas.values()))
        visibles = graph_queries.entities_with_public_note(ids) if ids else set()
        retiradas = (graph_queries.existing_entity_ids(ids) - graph_queries.served_entity_ids(ids)) if ids else set()
        # Asked of the graph, not of the vault: the name is withdrawn even if the vault carries no note of that person
        # (and with no notes at all the graph is still asked, so «sin grafo no se sirve nada» always holds).
        del_grafo = graph_queries.unserved_person_names()
    except Exception as exc:  # noqa: BLE001 — sin grafo (o sin poder leer las fichas) no se sirve el vault
        log.warning("vault_entity_check_failed", error=str(exc))
        return None
    try:
        alias = vault_entity_aliases()
    except Exception as exc:  # noqa: BLE001 — sin el índice de alias no se sabe qué retirar
        log.warning("vault_entity_check_failed", error=type(exc).__name__)
        return None
    fold = lambda t: "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")  # noqa: E731
    ocultos = (frozenset(i for i in notas.values() if i not in visibles),
               frozenset(fold(r.rsplit("/", 1)[-1][:-3]) for r, i in notas.items() if i in retiradas)
               | frozenset(a for i in retiradas for a in alias.get(i, ()))
               | frozenset(fold(n) for n in del_grafo))
    _ocultos_cache = (now, ocultos)
    return ocultos


@mcp.tool(annotations=_ro("Buscar en el vault"))
def buscar_vault(
    consulta: Annotated[str, Field(description="texto a buscar en los briefings y notas de patrones del vault")],
    limite: Annotated[int, Field(description="máx. notas (se acota a 15)")] = 10,
) -> str:
    """Busca en las notas editoriales del vault (briefings curados y notas de patrones).
    Complementa al grafo con texto redactado; las fichas de entidades y las cronologías
    se consultan con las herramientas del grafo. Devuelve rutas que puedes abrir con leer_nota."""
    limite = max(1, min(limite, 15))
    if not vault_available():
        return ("El vault de notas no está disponible en este despliegue; usa buscar_documentos "
                "y las herramientas del grafo en su lugar.")
    filtro = _titulos_ocultos()
    if filtro is None:
        return _VAULT_SIN_GRAFO
    ocultas, sin_nombrar = filtro
    hits = vault_search(consulta, limite, hidden=ocultas, unnamed=sin_nombrar)
    if not hits:
        return "Sin notas que coincidan."
    out: list[str] = []
    for h in hits:
        line = f"- {h['path']} · {h['title']}"
        if h["snippet"]:
            line += f"\n  «{h['snippet']}»"
        out.append(line)
    return "\n".join(out)


@mcp.tool(annotations=_ro("Leer nota"))
def leer_nota(
    ruta: Annotated[str, Field(description="ruta relativa de la nota (tal como la da buscar_vault), p.ej. '_Briefings/Caso Koldo.md'")],
) -> str:
    """Lee una nota del vault por su ruta relativa (tal como la devuelve buscar_vault,
    p.ej. '_Briefings/Caso Koldo.md'). Solo notas .md; las muy largas se truncan."""
    try:
        filtro = _titulos_ocultos()
        if filtro is None:
            return _VAULT_SIN_GRAFO
        ocultas, sin_nombrar = filtro
        return vault_read(ruta, hidden=ocultas, unnamed=sin_nombrar)
    except FileNotFoundError:
        return f"No existe la nota '{ruta}'. Usa buscar_vault para encontrar la ruta."
    except ValueError:
        return f"No existe la nota '{ruta}'. Usa buscar_vault para encontrar la ruta."


# --- Prompts: guided multi-tool workflows (appear as slash-commands/starters in
# the client). Each returns a Spanish user message encoding a correct tool flow
# from INSTRUCTIONS; they don't change any tool. -----------------------------
_REGLAS = ("Cita SIEMPRE las fuentes inline como [documento, p.N]. Respeta la presunción "
           "de inocencia y el [estado]: 'imputado'/'acusado' NO es 'condenado'. Atribuye "
           "lo de prensa ('según <medio>') y no lo des por probado. Si las herramientas no "
           "responden, dilo; no inventes.")


@mcp.prompt(title="Investigar una persona")
def investigar_persona(nombre: str) -> str:
    """Perfil de una persona: identidad, relaciones y afirmaciones en disputa, con fuentes."""
    return (
        f"Investiga a «{nombre}» en el grafo de El Entramado y hazme un perfil. Sigue este flujo:\n"
        f"1. `buscar_entidades` con «{nombre}» para obtener su `id` exacto.\n"
        "2. `ficha_entidad` con ese id: quién es (identidad de contexto), estado procesal y resumen.\n"
        "3. `relaciones_entidad` para sus vínculos más relevantes (sube `limite` o filtra si es un hub).\n"
        "4. `claims_entidad` para ver acusaciones vs. desmentidos.\n"
        f"Resume quién es, sus vínculos clave y qué se le atribuye (y quién lo niega). {_REGLAS}"
    )


@mcp.prompt(title="Seguir el dinero")
def seguir_el_dinero(entidad: str = "") -> str:
    """Rastro de dinero: pagos, comisiones y adjudicaciones, con sus fuentes."""
    foco = (f"en torno a «{entidad}»" if entidad else "de toda la trama")
    paso1 = (f"1. Si «{entidad}» es un nombre, primero `buscar_entidades` para su `id`.\n"
             if entidad else "")
    return (
        f"Sigue el rastro del dinero {foco} en el grafo de El Entramado:\n"
        f"{paso1}"
        f"2. `flujos_dinero`{' con esa entidad' if entidad else ''}, de mayor a menor importe.\n"
        "3. Para los flujos más grandes, `evidencia_relacion` para leer la cita íntegra que los respalda.\n"
        f"Resume los importes principales, entre quién y con qué respaldo documental. {_REGLAS}"
    )


@mcp.prompt(title="Relación entre dos personas")
def relacion_entre(persona_a: str, persona_b: str) -> str:
    """Qué conecta a dos entidades: el camino más corto y la evidencia de cada salto."""
    return (
        f"Averigua qué relación hay entre «{persona_a}» y «{persona_b}» en el grafo de El Entramado:\n"
        f"1. `buscar_entidades` para el `id` de «{persona_a}» y el de «{persona_b}».\n"
        "2. `camino_entre` con ambos ids para la(s) cadena(s) de conexión más cortas.\n"
        "3. `evidencia_relacion` en cada salto que quieras confirmar, para la cita íntegra.\n"
        f"Explica cómo se conectan y con qué pruebas, salto a salto. {_REGLAS}"
    )


@mcp.prompt(title="Panorama de un caso")
def panorama_del_caso(caso: str) -> str:
    """Visión de conjunto de un caso: sub-tramas, figuras centrales y briefings."""
    return (
        f"Dame un panorama del caso «{caso}» en El Entramado:\n"
        f"1. `listar_casos` y, si «{caso}» coincide con uno, `ficha_caso` con su slug: es la "
        "vista curada (implicados con estado procesal, documentos, cronología y flujos).\n"
        f"2. `buscar_vault` con «{caso}» por si hay un briefing curado que resuma el caso.\n"
        "3. `comunidades` para ver qué sub-tramas emergen del grafo.\n"
        "4. `metricas_centralidad` (grado e intermediacion) para las figuras más centrales y puente.\n"
        f"5. `buscar_documentos` con «{caso}» para el texto literal de los documentos clave.\n"
        f"Sintetiza qué es el caso, quién es central y cuáles son las sub-tramas. {_REGLAS}"
    )


# ASGI app served by uvicorn; FastMCP's streamable-HTTP app answers at /mcp
# itself, so Caddy proxies `handle /mcp*` WITHOUT stripping the prefix.
#
# `/metrics` is intercepted by a thin wrapper that sits OUTSIDE the rate limiter
# (Prometheus scrapes every few seconds — it must not count against the public
# cap) and delegates everything else — crucially the `lifespan` scope that boots
# FastMCP's session manager — untouched to the inner chain. Wrapping the inner
# app in a parent Starlette+Mount would swallow that lifespan; this does not.
class _MetricsRoute:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/metrics":
            await metrics_asgi_app(scope, receive, send)
            return
        await self.app(scope, receive, send)


asgi = _MetricsRoute(RateLimitMiddleware(mcp.streamable_http_app()))
