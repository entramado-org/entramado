"""Load the curated RELATIONS-correction overlay into Neo4j.

The document extractor produces two kinds of relational error this overlay fixes
durably (pattern-based, so it survives a re-ingest — unlike per-elementId LLM
cleanups):

  * `deny:`  — misleading / hallucinated entity-entity edges that confuse the
    reader (e.g. a `Begoña Gómez -[ASSOCIATED_WITH]-> José Luis Rodríguez Zapatero`
    born from a chunk that actually says "esposa del señor presidente del Gobierno"
    — i.e. Pedro Sánchez). Each rule deletes the matching EXTRACTED edges only
    (`source_kind` null/'judicial'); curated/contexto/prensa/acusacion edges are
    never touched unless the row names their layer in `kinds:` (spec 063: an audited
    edge of another layer, e.g. `kinds: [prensa]` for an edge extracted from a press
    PDF, or the leftover of a row already removed from its layer YAML — the layer
    loaders re-create whatever their YAML still declares). Optional `doc` /
    `quote_contains` narrow the match.

  * `splits:` — disambiguation of two real-world people the resolver merged into
    one node because they share a name. `auto-zapatero.pdf` names a *namesake
    businessman* "José Luis Rodríguez Zapatero" (Análisis Relevante / Plus Ultra,
    blanqueo) that got folded into the ex-PM's node. A split MERGEs a new namesake
    node and reassigns to it every edge + `:MENTIONED_IN` that meets ALL its criteria
    (`by_doc`, `by_quote`, `by_quote_exclude`; spec 070, AC162), plus the listed
    `move_aliases`; `drop_aliases` are stripped from the canonical entirely.

  * `fix:` — in-place correction of an EXTRACTED edge that is real but mistyped or
    mis-directed (rather than spurious): retype it to the right `RelationType`
    (`corrected_type`) and/or reverse its endpoints (`swap_direction`). Provenance
    rides along (APOC `refactor.setType` / `refactor.invert` preserve properties).
    Only EXTRACTED edges (`source_kind` null/'judicial') are touched; narrow the
    match with optional `doc` / `quote_contains`. The `relations_swarm*.yaml` overlays keep the
    `verdict:"fix"` rows of the retired relation-verifier swarm; `qa_esc_apply` writes them today.
    Desde la spec 061 una fila `fix:` también puede llevar
    `set: {amount, currency, date, status, note, source_kind}` — corrección de las
    props sustantivas de la arista, con `source_kind` solo a la BAJA
    (judicial→contrato→acusacion→prensa→ultima_hora→contexto): degradar la capa de
    una arista mal respaldada es legítimo, ascenderla sería fabricar respaldo. Una
    fila con solo `set:` (sin retype ni swap) es válida.

  * An OPAQUE `deny:` row (`{unidad: <64 hex>, declaraciones?, kinds?, reason}`, spec 071 AC114/AC121/AC127) names
    no endpoint, type, document or quote: an anonymized withdrawal must not be reconstructible from the public overlay.
    It resolves against the PRIVATE audit root (`--root` or `QA_CENSO_ROOT`): the census unit with that key, as the
    apply writes its rule, plus exactly `declaraciones` predicates that `esc/reglas_opacas.yaml` declares for the key
    (a re-identified edge's current pair, or the full predicate of a withdrawal with no census unit), each prepared
    like an explicit row. No audit root, no private file, a key with neither, another number of predicates, a key
    the file declares and no row resolves, or a key whose only predicates are `ajena` (it says nothing it withdraws
    itself, AC157): the loader ABORTS (exit 2) — skipping it would bring the withdrawn edge back. With an opaque row
    the overlay is applied whole or not at all: any `--no-*` flag aborts before touching the graph (AC160). A deny
    row with neither form (the exact opaque one or a complete explicit one: a `Unidad` variant, an unknown key, no
    valid type, an unresolved endpoint) is never skipped: exit 2, naming the file and the position (AC159).

Curated truths (correct roles, family/party ties) live in the `contexto` overlay
(`context_loader.py` / `data/context/*.yaml`), not here — this file only corrects.

No row is ever skipped (spec 070, AC143/AC144): one the loader wouldn't apply whole stops it
before the driver opens (exit 2), with its position and never its values; the tree check
(`seudonimos_check.py`) judges every row with the same functions (`seudonimos.FORMAS`).

Idempotent and re-runnable. After a future `make ingest`, run
`make context && make relations && make accusations` to restore the curated truth.

Usage:
    cd pipeline && uv run python -m src.relations_loader
    cd pipeline && uv run python -m src.relations_loader --dir ../some/dir
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import structlog

from . import config, neo4j_loader, seudonimos
from .alcance import (CargaInvalida, Opacas, ReglaOpacaInvalida, YamlInvalido, es_opaca, leer_yaml, preparar_set)
from .entity_ids import entity_id   # sin rapidfuzz: la preparación de reglas corre también en el job de CI de la API
from .ontologia import ontologia
from .schema import EntityType, RelationType

log = structlog.get_logger()

RELATIONS_DIR = config.WORK_DIR / "relations"

_ENTITY_TYPES = {t.value for t in EntityType}
_REL_TYPES = {t.value for t in RelationType}
# Provenance relationship types never get rewired/deleted as "entity-entity" edges.
# Spec 070 (AC144): the list the tree judges a rule with (`seudonimos.PROCEDENCIA`).
_PROV = list(seudonimos.PROCEDENCIA)

# Props que un `fix: {set: …}` puede reescribir (spec 061). Lista cerrada: nada de
# tocar la procedencia documental (source_doc/source_quote) ni claves de registro —
# corregir un dato ≠ reescribir su evidencia. `source_page` sí (063, 2026-09-29): es el
# localizador de la cita, y una página equivocada abre el pasaje de OTRA persona
# (Koldo CONVICTED_OF cohecho apuntaba a la p.219, fallo de Ábalos; la cita está en la p.220).
# Spec 070 (AC143): la lista y la regla son las de `seudonimos` (`motivo_set_fix`), las mismas del árbol.
_SET_PROPS = set(seudonimos.SET_PROPS_FIX)
# Orden de confianza de las capas (menor = más autoritativa). Espejo de
# api/app/graph_queries._KIND_RANK para las capas que este overlay puede escribir.
# Ontología (spec 069): las capas hasta `contexto`; las de registros oficiales (mercantil,
# subvencion, contratacion, boe) las recrea su propio loader y este overlay no las toca.
# Spec 070 (AC144): las de `seudonimos.rango_capas_regla`, con las que el árbol juzga los `kinds` de una regla.
_KIND_RANK = seudonimos.rango_capas_regla()


def _ordered(directory: Path) -> list[Path]:
    """Ficheros del overlay en orden de aplicación: alfabético, pero los
    `*_qa061.yaml` SIEMPRE al final — son las correcciones de la auditoría dual y
    tienen que poder pisar lo que dijo cualquier fichero anterior. La misma
    enumeración que lee el lint L26 y el CI (spec 070)."""
    return seudonimos.ficheros_overlay(directory)


def load_files(directory: Path) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Read every *.yaml/*.yml; collect top-level `deny:`, `splits:`, `fix:`,
    `alias_deny:` lists. Spec 070 (AC156): before joining any rows, each file passes the tree's
    form (`seudonimos.problemas_forma` without its rows: a map root, sections of the inventory
    with their exact names — `DENY:` is not `deny:` — and each a list; the allow-list file has
    its own check); otherwise exit 2 (`ErrorDeForma`) naming every file and section, never a value.
    AC164: the root as read, never `or {}`: an empty file, `null`, `false`, `0`, `''` or `[]` is
    not «no rules», it stops («la raíz no es un mapa») like in the tree. A file that does not load
    — or repeats a key at any level, which PyYAML would silently resolve to the last one, or merges
    one with `<<` (spec 071 AC108, AC128) — aborts with exit 2, naming only the file and the
    position; so does a deny row with neither form (`_forma_deny`, AC159)."""
    deny: list[dict] = []
    splits: list[dict] = []
    fixes: list[dict] = []
    alias_deny: list[dict] = []
    motivos: list[str] = []
    for f in _ordered(directory):
        try:
            leer_yaml(f)   # spec 071 AC108/AC128: el lector de las comprobaciones (sin claves repetidas ni `<<`)
            doc = seudonimos.leer_yaml(f.read_text(encoding="utf-8"), f.name)  # sin el texto de un YAML roto
        except (SystemExit, YamlInvalido) as exc:
            print(exc, file=sys.stderr)
            raise seudonimos.ErrorDeForma([str(exc)]) from None
        problemas = seudonimos.problemas_forma(f"{seudonimos.RELATIONS_REL}/{f.name}", doc, filas=False)
        if problemas:
            motivos += problemas
            continue
        if not isinstance(doc, dict):   # la lista de permitidas: su forma la juzga `seudonimos.cargar_permitidos`
            continue
        deny.extend(doc.get("deny") or [])
        splits.extend(doc.get("splits") or [])
        fixes.extend(doc.get("fix") or [])
        alias_deny.extend(doc.get("alias_deny") or [])
        log.info("relations_file", file=f.name,
                 deny=len(doc.get("deny") or []), splits=len(doc.get("splits") or []),
                 fix=len(doc.get("fix") or []), alias_deny=len(doc.get("alias_deny") or []))
    if motivos:
        raise seudonimos.ErrorDeForma(f"forma inválida (spec 070): {m}" for m in motivos)
    # Spec 071 AC159: a deny row with neither form stops it already when reading the files; spec 070 (AC143/AC144):
    # with every row of every section it wouldn't apply whole, in one stop and by position (as `_preparar`).
    motivos = [f"{seccion}[{i}]: {motivo}"
               for seccion, filas in (("splits", splits), ("deny", deny), ("fix", fixes), ("alias_deny", alias_deny))
               for i, d in enumerate(filas) if (motivo := seudonimos.FORMAS[seccion](d))]
    if motivos:
        raise seudonimos.ErrorDeForma(motivos)
    return deny, splits, fixes, alias_deny


def _resolve(item: dict, side: str) -> str | None:
    """Resolve a deny endpoint to an entity_id. Accepts `<side>_id`, or
    `<side>` (name) + `<side>_type` (default Person). Shared with the lint/CI (spec 070)."""
    return seudonimos.extremo(item, side, _ENTITY_TYPES)


_KINDS_OK = set(_KIND_RANK)


# Spec 070 (AC63, AC143/AC144): no row is ever skipped with a warning. One without the form the loader applies (its
# section's function in `seudonimos.FORMAS`, the one the tree judges it with) stops the loader with exit 2 and the
# position of every such row in its section's concatenated list (`deny[i]`, `fix[i]`, `alias_deny[i]`, `splits[i]`),
# never its values: the rule is public, but the apply's `check`/`dry-run` runs these on rows built from the census,
# and their output ends up in terminals and PRs.
def _parar(seccion: str, filas: list, forma) -> None:
    motivos = [f"{seccion}[{i}]: {motivo}" for i, d in enumerate(filas) if (motivo := forma(d))]
    if motivos:
        raise seudonimos.ErrorDeForma(motivos)


def _prepare_kinds(d: dict) -> list[str] | None:
    """`kinds:` opcional de una fila (spec 063): capas NO extraídas que la fila puede
    tocar además de null/judicial. None = solo aristas extraídas (el default), solo si
    la fila no los lleva o los lleva nulos (spec 070, AC165: un valor inválido nunca se
    lee como el alcance por defecto). Los valida la forma de la fila
    (`seudonimos.forma_deny`/`forma_fix`), antes."""
    kinds = d.get("kinds")
    return None if kinds is None else [k.strip() for k in kinds]


def _forma_deny(d) -> str | None:
    """Why the deny row `d` has not the form the loader applies, or None: spec 071's name, order and reasons (AC159).
    Spec 070 (AC144): the rule is `seudonimos.forma_deny`, the one the tree checks."""
    return seudonimos.forma_deny(d)


def _opacas(raiz) -> Opacas:
    """The opaque-rule resolver of the audit root `raiz` (an `Opacas` passes through; None: `QA_CENSO_ROOT`). Without
    it an opaque row cannot be applied: exit 2, never a skip (spec 071 AC114)."""
    if isinstance(raiz, Opacas):
        return raiz
    raiz = raiz or os.environ.get("QA_CENSO_ROOT")
    if not raiz:
        log.error("deny_opaque_without_census")
        raise SystemExit(2)
    try:
        return Opacas(raiz)
    except ReglaOpacaInvalida as exc:
        log.error("deny_opaque_invalid", reason=str(exc))
        raise SystemExit(2) from None


def _preparar_deny(d: dict, registrar: bool = True) -> dict | None:
    """One deny row as the loader applies it, the same for an explicit row and for each predicate an opaque one resolves
    to (spec 071 AC129): endpoints through `_resolve` (an id stripped, or a name with its stripped type, Person by
    default), the type stripped, `kinds` checked. An explicit row (`registrar`) without a valid type or with an
    unresolved endpoint aborts with exit 2 (AC159: it is never skipped; `_prepare_deny` already stopped it by its form);
    for a private predicate, None: its caller aborts. Spec 070 (AC63): no log carries its values."""
    rtype = (d.get("type") or "").strip()
    sid, tid = _resolve(d, "source"), _resolve(d, "target")
    if rtype not in _REL_TYPES or rtype in _PROV:
        if registrar:
            log.error("deny_invalid_type")
            raise SystemExit(2)
        return None
    if not sid or not tid:
        if registrar:
            log.error("deny_invalid_unresolved")
            raise SystemExit(2)
        return None
    return {"source_id": sid, "target_id": tid, "type": rtype, "doc": d.get("doc"),
            "quote_contains": d.get("quote_contains"), "kinds": _prepare_kinds(d), "reason": d.get("reason")}


def _prepare_deny(deny: list[dict], raiz=None, completo: bool = False, registro=None) -> list[dict]:
    """The deny rows the loader applies; one without either form (`_forma_deny`: spec 070 AC144, spec 071 AC159) stops
    it with its position (`_parar`). An opaque row resolves through the private audit root `raiz` (`_opacas`) into
    one row per predicate, each prepared like an explicit one (`_preparar_deny`); one that does not resolve, or a
    resolved predicate the preparation rejects, aborts with exit 2 and no value in the log. With `completo` (the whole
    overlay), the private file may not declare a key no opaque row resolves (spec 071 AC127), nor a row of `filas:`
    that is not one of the public register `registro` admitting it (AC171), and each row admitting one has its own
    (AC183: a lost declaration would leave its withdrawal unchecked) — checked whenever there is a private audit root,
    with or without opaque rows, always both ways: no caller here can narrow it."""
    _parar("deny", deny, _forma_deny)
    rows: list[dict] = []
    opacas, claves = None, []
    for d in deny:
        if es_opaca(d):
            opacas = opacas or _opacas(raiz)
            try:
                resueltas = opacas.resolver(d)
            except ReglaOpacaInvalida as exc:
                log.error("deny_opaque_invalid", reason=str(exc))
                raise SystemExit(2) from None
            claves.append(d["unidad"])
            for r in resueltas:
                fila = _preparar_deny(r, registrar=False)
                if fila is None:
                    log.error("deny_opaque_invalid", reason="un predicado resuelto sin la forma de un deny")
                    raise SystemExit(2)
                rows.append({**fila, "unidad": r["unidad"]})
            continue
        rows.append(_preparar_deny(d))
    if completo and (opacas is not None or raiz or os.environ.get("QA_CENSO_ROOT")):
        # AC171: with a private audit root, its private file is always checked against the whole overlay, opaque rows
        # or not: a declaration nothing resolves is a withdrawal that is never applied.
        opacas = opacas or _opacas(raiz)
        try:
            opacas.verificar(claves, registro)
        except ReglaOpacaInvalida as exc:
            log.error("deny_opaque_invalid", reason=str(exc))
            raise SystemExit(2) from None
    return rows


def apply_deny(driver, rows: list[dict]) -> int:
    """Delete matching EXTRACTED edges (undirected: both A->B and B->A), keeping
    curated/contexto/prensa/acusacion edges. Optional doc / quote substring filter."""
    q = """
    UNWIND $rows AS row
    MATCH (a {entity_id: row.source_id})-[r]-(b {entity_id: row.target_id})
    WHERE type(r) = row.type
      AND (r.source_kind IS NULL OR r.source_kind = 'judicial' OR r.source_kind IN coalesce(row.kinds, []))
      AND (row.doc IS NULL OR r.source_doc = row.doc)
      AND (row.quote_contains IS NULL
           OR toLower(coalesce(r.source_quote,'')) CONTAINS toLower(row.quote_contains))
    DELETE r
    RETURN count(r) AS n
    """
    with driver.session() as s:
        n = s.run(q, rows=rows).single()["n"]
    log.info("relations_denied", rules=len(rows), edges_removed=n)
    return n


# --- Fix (retype / redirect) ---------------------------------------------------

def _prepare_set(props, donde: str) -> dict | None:
    """The `set:` block (spec 061) as the loader writes it: None without one; otherwise `alcance.preparar_set`, the same
    preparation the spec 071 checks read a decision's load with (the procedural status in its canonical form, spec 069,
    and, with `date`, `date_norm`/`date_year`). Spec 070 (AC143/AC144): the rule is `seudonimos.motivo_set_fix`, the
    tree's; a block it or `preparar_set` rejects stops the loader with its position, never a skip."""
    if props is None:
        return None
    motivo = seudonimos.motivo_set_fix(props)
    if motivo:
        raise seudonimos.ErrorDeForma([f"{donde}: {motivo}"])
    try:
        return preparar_set(props)
    except CargaInvalida as exc:   # su texto: el prop y un motivo neutro, nunca el valor (spec 071 AC78/AC82)
        raise seudonimos.ErrorDeForma([f"{donde}: {exc}"]) from None


def _prepare_fix(fixes: list[dict]) -> list[dict]:
    """Validate fix rows: resolve endpoints, check current `type` and optional
    `corrected_type` against the closed set. A row must do something (retype,
    swap_direction and/or `set:` de props). Spec 070 (AC143/AC144): none is skipped; one the
    loader wouldn't apply whole (`seudonimos.forma_fix`, the tree's rule) stops it with its
    position (`_parar`), never its values."""
    _parar("fix", fixes, seudonimos.forma_fix)
    rows: list[dict] = []
    for i, d in enumerate(fixes):
        rtype = d["type"].strip()
        corrected = (d.get("corrected_type") or "").strip() or None
        rows.append({
            "source_id": _resolve(d, "source"), "target_id": _resolve(d, "target"), "type": rtype,
            # A pure retype to the same type is a no-op; drop the corrected_type then.
            "corrected_type": None if corrected == rtype else corrected,
            "swap": d.get("swap_direction") is True, "set": _prepare_set(d.get("set"), f"fix[{i}]"),
            "doc": d.get("doc"), "quote_contains": d.get("quote_contains"),
            "kinds": _prepare_kinds(d), "reason": d.get("reason"),
        })
    return rows


# Symmetric relations have no meaningful direction: a rule written A→B must also hit a
# stored B→A (the load-time canonicalisation is not guaranteed for every layer — 063,
# 2026-09-28: four `set: note` rules on ASSOCIATED_WITH never applied). Only the prop SET is
# undirected: a retype can turn a symmetric edge into a directed one, so it and swap stay directed.
_SYMMETRIC_TYPES = sorted(ontologia().simetricas)

# Retype directed EXTRACTED edges in place (provenance preserved by setType).
# Dedup per edge before the CALL: several rules can match the same relationship
# (the swarm files repeat near-identical rules), and apoc.refactor.setType/invert
# delete+recreate the rel — a second CALL on the stale reference materializes a
# property-less duplicate edge (observed 2026-07-09: 6 empty SUBSIDIARY_OF).
_RETYPE_Q = """
UNWIND $rows AS row
MATCH (a {entity_id: row.source_id})-[r]->(b {entity_id: row.target_id})
WHERE type(r) = row.type AND row.corrected_type IS NOT NULL
  AND (r.source_kind IS NULL OR r.source_kind = 'judicial' OR r.source_kind IN coalesce(row.kinds, []))
  AND (row.doc IS NULL OR r.source_doc = row.doc)
  AND (row.quote_contains IS NULL
       OR toLower(coalesce(r.source_quote,'')) CONTAINS toLower(row.quote_contains))
WITH r, head(collect(row.corrected_type)) AS corrected
CALL apoc.refactor.setType(r, corrected) YIELD output
RETURN count(output) AS n
"""

# Reverse endpoints of directed EXTRACTED edges in place (after any retype, so the
# match keys on the corrected type when present). Same per-edge dedup as _RETYPE_Q.
_SWAP_Q = """
UNWIND $rows AS row
MATCH (a {entity_id: row.source_id})-[r]->(b {entity_id: row.target_id})
WHERE type(r) = coalesce(row.corrected_type, row.type) AND row.swap
  AND (r.source_kind IS NULL OR r.source_kind = 'judicial' OR r.source_kind IN coalesce(row.kinds, []))
  AND (row.doc IS NULL OR r.source_doc = row.doc)
  AND (row.quote_contains IS NULL
       OR toLower(coalesce(r.source_quote,'')) CONTAINS toLower(row.quote_contains))
WITH DISTINCT r
CALL apoc.refactor.invert(r) YIELD output
RETURN count(output) AS n
"""


# Corrección de props sustantivas de una arista EXTRAÍDA (spec 061): importe,
# moneda, fecha, estado procesal, nota y —solo a la baja— la capa de procedencia.
# Nunca toca la evidencia (source_doc/source_quote/source_page). `qa_set_run` deja
# el rastro de qué regla la escribió, para poder auditarla después. Mismo dedup
# por arista que _RETYPE_Q (varias reglas pueden casar la misma relación).
_SET_Q = """
UNWIND $rows AS row
MATCH (a {entity_id: row.source_id})-[r]-(b {entity_id: row.target_id})
WHERE type(r) = coalesce(row.corrected_type, row.type) AND row.set IS NOT NULL
  AND (startNode(r) = a OR type(r) IN $symmetric)
  AND (r.source_kind IS NULL OR r.source_kind = 'judicial' OR r.source_kind IN coalesce(row.kinds, []))
  AND (row.doc IS NULL OR r.source_doc = row.doc)
  AND (row.quote_contains IS NULL
       OR toLower(coalesce(r.source_quote,'')) CONTAINS toLower(row.quote_contains))
WITH r, head(collect(row.set)) AS props, head(collect(row.reason)) AS reason
SET r += props, r.qa_set_run = reason
RETURN count(r) AS n
"""


def apply_fix(driver, rows: list[dict]) -> dict:
    """Retype, set props, then redirect matching EXTRACTED edges. Idempotent: a 2nd
    run finds nothing (retype keys on the original type; swap keys on the post-retype
    type and the inverted edge no longer matches the original orientation). Un `set:`
    se reaplica igual salvo que haya degradado `source_kind` — entonces la arista ya
    no es «extraída» y la regla queda consumida, que es lo que se pretende."""
    if not rows:
        return {"retyped": 0, "swapped": 0, "props_set": 0}
    with driver.session() as s:
        retyped = s.run(_RETYPE_Q, rows=rows).single()["n"]
        # El SET va DESPUÉS del retype (casa por el tipo corregido) y ANTES del
        # swap (que voltearía la arista y rompería el match dirigido).
        props_set = s.run(_SET_Q, rows=rows, symmetric=_SYMMETRIC_TYPES).single()["n"]
        swapped = s.run(_SWAP_Q, rows=rows).single()["n"]
    log.info("relations_fixed", rules=len(rows), retyped=retyped, swapped=swapped,
             props_set=props_set)
    return {"retyped": retyped, "swapped": swapped, "props_set": props_set}


# --- Homonym split -------------------------------------------------------------
# Every move applies ALL the criteria the split declares at once (spec 070, AC162): `by_doc`
# (the edge's `source_doc`, the chunk's `doc_id`), `by_quote` (evidence that names the namesake:
# the edge's `source_quote`, the chunk's text; for two real people co-mentioned in the SAME docs,
# where by_doc can't separate them) and `by_quote_exclude` (evidence that names the canonical:
# never moved). A criterion the split doesn't declare filters nothing ($by_doc null, $inc empty).

_MOVE_OUT_Q = """
MATCH (canon {entity_id:$canon})-[r]->(o)
WHERE o.entity_id IS NOT NULL AND NOT type(r) IN $prov
  AND ($by_doc IS NULL OR coalesce(r.source_doc,'') = $by_doc)
  AND (size($inc) = 0 OR any(p IN $inc WHERE toLower(coalesce(r.source_quote,'')) CONTAINS p))
  AND none(p IN $exc WHERE toLower(coalesce(r.source_quote,'')) CONTAINS p)
MATCH (ns {entity_id:$ns})
CALL apoc.refactor.from(r, ns) YIELD output
RETURN count(output) AS n
"""

_MOVE_IN_Q = """
MATCH (o)-[r]->(canon {entity_id:$canon})
WHERE o.entity_id IS NOT NULL AND NOT type(r) IN $prov
  AND ($by_doc IS NULL OR coalesce(r.source_doc,'') = $by_doc)
  AND (size($inc) = 0 OR any(p IN $inc WHERE toLower(coalesce(r.source_quote,'')) CONTAINS p))
  AND none(p IN $exc WHERE toLower(coalesce(r.source_quote,'')) CONTAINS p)
MATCH (ns {entity_id:$ns})
CALL apoc.refactor.to(r, ns) YIELD output
RETURN count(output) AS n
"""

_MOVE_MENTIONS_Q = """
MATCH (canon {entity_id:$canon})-[m:MENTIONED_IN]->(ch:Chunk)
WHERE ($by_doc IS NULL OR coalesce(ch.doc_id,'') = $by_doc)
  AND (size($inc) = 0 OR any(p IN $inc WHERE toLower(coalesce(ch.text,'')) CONTAINS p))
  AND none(p IN $exc WHERE toLower(coalesce(ch.text,'')) CONTAINS p)
MATCH (ns {entity_id:$ns})
CALL apoc.refactor.from(m, ns) YIELD output
RETURN count(output) AS n
"""

_ALIAS_Q = """
MATCH (canon {entity_id:$canon})
SET canon.aliases = [a IN coalesce(canon.aliases,[]) WHERE NOT a IN $move AND NOT a IN $drop]
WITH canon
MATCH (ns {entity_id:$ns})
SET ns.aliases = [a IN apoc.coll.toSet(coalesce(ns.aliases,[]) + $move) WHERE a <> ns.name]
RETURN size($move) AS moved, size($drop) AS dropped
"""


def _prepare_splits(splits: list[dict]) -> list[dict]:
    """Spec 070 (AC144): the splits, checked before the driver opens (`seudonimos.forma_split`, the tree's rule): one
    without both entities or without a criterion stops the loader with its position (`_parar`); `apply_split` used to
    skip it with a warning halfway through the load."""
    _parar("splits", splits, seudonimos.forma_split)
    return splits


def apply_split(driver, sp: dict, donde: str = "") -> dict:
    motivo = seudonimos.forma_split(sp)
    if motivo:   # `main` checks them all before opening the driver (`_prepare_splits`); none is ever skipped
        raise seudonimos.ErrorDeForma([f"{donde}: {motivo}"])
    canon = sp.get("canonical") or {}
    nsk = sp.get("namesake") or {}
    by_doc = (sp.get("by_doc") or "").strip() or None
    by_quote = [str(s).lower() for s in (sp.get("by_quote") or []) if str(s).strip()]
    by_quote_exclude = [str(s).lower() for s in (sp.get("by_quote_exclude") or []) if str(s).strip()]
    ctype, cname = canon.get("type"), (canon.get("name") or "").strip()
    ntype, nname = nsk.get("type"), (nsk.get("name") or "").strip()
    canon_id, ns_id = entity_id(ctype, cname), entity_id(ntype, nname)
    move = [a for a in (sp.get("move_aliases") or [])]
    drop = [a for a in (sp.get("drop_aliases") or [])]

    create_ns = """
    CALL apoc.merge.node([$type], {entity_id:$ns},
      {name:$name, canonical_name:$name, role:$role, mentions:0, degree:0},
      {role: coalesce($role, '')}) YIELD node
    RETURN node.entity_id AS id
    """
    criterios = {"by_doc": by_doc, "inc": by_quote, "exc": by_quote_exclude}   # todos, en cada consulta (AC162)
    with driver.session() as s:
        s.run(create_ns, type=ntype, ns=ns_id, name=nname, role=nsk.get("role"))
        out = s.run(_MOVE_OUT_Q, canon=canon_id, ns=ns_id, prov=_PROV, **criterios).single()["n"]
        inc = s.run(_MOVE_IN_Q, canon=canon_id, ns=ns_id, prov=_PROV, **criterios).single()["n"]
        men = s.run(_MOVE_MENTIONS_Q, canon=canon_id, ns=ns_id, **criterios).single()["n"]
        s.run(_ALIAS_Q, canon=canon_id, ns=ns_id, move=move, drop=drop)
        # A spent split (its moves already live in the lineage, or the namesake was
        # since pruned) must not leave a freshly-minted orphan stub behind on every
        # replay (jaime-far got re-pruned 8 times). If THIS run moved nothing and the
        # namesake ended up with no relationships at all, drop it; a real pre-existing
        # namesake keeps its edges/mentions and survives untouched.
        if out + inc + men == 0:
            gone = s.run(
                "MATCH (ns {entity_id:$ns}) WHERE NOT (ns)--() DELETE ns RETURN count(*) AS n",
                ns=ns_id).single()["n"]
            if gone:
                log.info("split_stub_dropped", namesake=nname, id=ns_id)
    res = {"namesake": nname, "out": out, "in": inc, "mentions": men,
           "by": "+".join(c for c, v in (("doc", by_doc), ("quote", by_quote), ("exclude", by_quote_exclude)) if v),
           "aliases_moved": len(move), "aliases_dropped": len(drop)}
    log.info("relations_split", canonical=cname, **res)
    return res


# --- Alias-deny (per-entity alias hygiene) ------------------------------------
# Editorial alias pruning for over-fused "alias magnet" nodes (spec 025). Two modes
# per entry, exactly one required:
#   keep_only: [..]  — whitelist; every alias NOT in the list is dropped (for magnet
#                      hubs that keep re-absorbing junk on re-ingest, e.g. the covid
#                      commission — encode the ~20 that name the commission).
#   drop: [..]       — blacklist; drop just these aliases (e.g. bare "LANDSIDE").
# Endpoint: `id:` (full entity_id) or `{type, name}`. Never touches edges/mentions,
# only n.aliases. Idempotent (re-run removes 0). Reversible (edit the YAML).

def _prepare_alias_deny(entries: list[dict]) -> list[dict]:
    """The alias_deny rows as the loader applies them. Spec 070 (AC144): one without its entity or without exactly one
    mode (`seudonimos.forma_alias_deny`, the tree's rule) stops the loader with its position (`_parar`)."""
    _parar("alias_deny", entries, seudonimos.forma_alias_deny)
    return [{"id": seudonimos.entidad(d, _ENTITY_TYPES), "keep_only": seudonimos.textos_de_lista(d.get("keep_only")),
             "drop": seudonimos.textos_de_lista(d.get("drop")), "reason": d.get("reason")} for d in entries]


_ALIAS_READ_Q = "MATCH (n {entity_id: $id}) RETURN coalesce(n.aliases, []) AS aliases"
_ALIAS_WRITE_Q = "MATCH (n {entity_id: $id}) SET n.aliases = $aliases"


def apply_alias_deny(driver, rows: list[dict]) -> int:
    """Read-then-write so we can refuse to wipe a node: an exact, case-sensitive
    match means a typo/casing slip in `keep_only` could match 0 aliases and empty
    the node. If keep_only matches nothing on a node that HAD aliases, skip + warn
    (the caller likely mistyped the whitelist) rather than silently wiping it.
    Spec 070 (enmienda 34): these two graph-dependent skips (a well-formed id the graph
    lacks, a whitelist that would empty the node) are logged by section and position in
    the concatenated list (`_prepare_alias_deny` keeps every row), never by id or alias."""
    total = 0
    with driver.session() as s:
        for i, row in enumerate(rows):
            rec = s.run(_ALIAS_READ_Q, id=row["id"]).single()
            if rec is None:
                log.warning("alias_deny_node_missing", donde=f"alias_deny[{i}]")
                continue
            cur = list(rec["aliases"] or [])
            if row["keep_only"]:
                keep = set(row["keep_only"])
                filtered = [a for a in cur if a in keep]
                if cur and not filtered:
                    log.error("alias_deny_keep_would_empty", donde=f"alias_deny[{i}]", before=len(cur),
                              note="keep_only matched 0 stored aliases — skipping to avoid "
                                   "wiping the node (check for typos / casing / accents)")
                    continue
            else:
                drop = set(row["drop"])
                filtered = [a for a in cur if a not in drop]
            if filtered == cur:  # idempotent no-op
                log.info("alias_deny_noop", id=row["id"], kept=len(cur),
                         mode="keep_only" if row["keep_only"] else "drop")
                continue
            s.run(_ALIAS_WRITE_Q, id=row["id"], aliases=filtered)
            removed = len(cur) - len(filtered)
            total += removed
            log.info("alias_deny_applied", id=row["id"], removed=removed, kept=len(filtered),
                     mode="keep_only" if row["keep_only"] else "drop")
    log.info("relations_alias_deny", rules=len(rows), aliases_removed=total)
    return total


# --- Seudónimos judiciales: denegación por defecto (spec 070) ----------------------
# Un seudónimo (context/pseudonyms.yaml) solo conserva las aristas con sociedades u
# organismos y los alias que enumera `seudonimos_permitidos.yaml` (por tripleta, capa
# y, si la entrada acota `docs`, documento); todo lo demás se borra, de CUALQUIER capa.
# Una arista sin `source_kind` es `seudonimos.SIN_CAPA`, que ningún `kinds` lista.
# Una sin `source_doc` es '' (un null en el IN la salvaría del DELETE), que ningún `docs` lista.
# La sociedad u organismo se reconoce por etiqueta: uno sin `entity_id` es '', que ninguna
# entrada lista, y su arista se borra (por prefijo del id, la esquivaría).
# Va tras splits/fix/deny/alias_deny para que nada de lo anterior lo esquive. El log
# da solo recuentos: los extremos retirados no se publican.

_POLITICA_ARISTAS_Q = """
UNWIND $ids AS sid
MATCH (p {entity_id: sid})-[r]-(o)
WHERE o:Company OR o:Organization
WITH r, sid, coalesce(o.entity_id, '') AS otro, type(r) AS tipo, coalesce(r.source_kind, $sin_capa) AS kind
WHERE NOT any(e IN $permitidas WHERE e.seudonimo = sid AND e.otro = otro AND e.tipo = tipo
                                 AND (e.kinds IS NULL OR kind IN e.kinds)
                                 AND (e.docs IS NULL OR coalesce(r.source_doc, '') IN e.docs))
DELETE r
RETURN count(r) AS n
"""

# Solo el alias: `name` queda como está (es el propio seudónimo).
_POLITICA_ALIAS_Q = """
UNWIND $filas AS fila
MATCH (p {entity_id: fila.id})
WITH p, fila, [a IN coalesce(p.aliases, []) WHERE NOT a IN fila.permitidos] AS fuera
WHERE size(fuera) > 0
SET p.aliases = [a IN p.aliases WHERE a IN fila.permitidos]
RETURN sum(size(fuera)) AS n
"""

_POLITICA_CONSERVADAS_Q = """
UNWIND $ids AS sid
MATCH (p {entity_id: sid})-[r]-(o)
WHERE o:Company OR o:Organization
RETURN count(r) AS n
"""


def _prepare_politica(directory: Path) -> dict:
    """Valida la lista ANTES de abrir el driver: ausente o mal formada, el loader para
    (sin lista, la denegación por defecto lo retiraría todo). Los tipos de relación, contra la
    ontología (spec 069): la misma validación que el CI y el apply de la 063."""
    path = directory / seudonimos.PERMITIDOS_NOMBRE
    if not path.exists():
        raise SystemExit(f"relations: falta {path.name} (spec 070)")
    try:
        ids = seudonimos.ids_seudonimos()
    except seudonimos.ErrorDeForma as err:   # AC114: sin roster, la política no alcanzaría a nadie
        print(f"relations: {err}", file=sys.stderr)
        raise
    data = seudonimos.cargar_permitidos(path)
    problemas = seudonimos.validar_permitidos(data, ids)
    if problemas:
        raise SystemExit(f"relations: {path.name} inválida: " + "; ".join(m for _, _, m in problemas))
    return seudonimos.parametros_politica(ids, data)


def apply_politica_seudonimos(driver, politica: dict) -> dict:
    """Borra las aristas seudónimo↔sociedad/organismo y los alias de seudónimo que la
    lista no permite. Idempotente: la segunda pasada no encuentra nada."""
    with driver.session() as s:
        aristas = s.run(_POLITICA_ARISTAS_Q, ids=politica["ids"], permitidas=politica["aristas"],
                        sin_capa=seudonimos.SIN_CAPA).single()["n"]
        alias = s.run(_POLITICA_ALIAS_Q, filas=politica["alias"]).single()["n"]
        conservadas = s.run(_POLITICA_CONSERVADAS_Q, ids=politica["ids"]).single()["n"]
    log.info("politica_seudonimos", aristas_retiradas=aristas, alias_retirados=alias,
             conservadas=conservadas)
    return {"aristas_retiradas": aristas, "alias_retirados": alias, "conservadas": conservadas}


def _preparar(splits: list[dict], deny: list[dict], fixes: list[dict], alias_deny: list[dict], **opacas) -> tuple:
    """Spec 070 (AC143/AC144): the four sections as the loader applies them, before the driver opens, with a single
    stop (`ErrorDeForma`, exit 2) listing every row of all of them it wouldn't apply whole. `opacas` goes to
    `_prepare_deny` (the private audit root of the opaque rows and the whole-overlay check, spec 071)."""
    motivos, preparadas = [], []
    for preparar, filas in ((_prepare_splits, splits), (lambda filas: _prepare_deny(filas, **opacas), deny),
                            (_prepare_fix, fixes), (_prepare_alias_deny, alias_deny)):
        try:
            preparadas.append(preparar(filas))
        except seudonimos.ErrorDeForma as err:
            motivos += err.motivos
    if motivos:
        raise seudonimos.ErrorDeForma(motivos)
    return tuple(preparadas)


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default=str(RELATIONS_DIR))
    p.add_argument("--no-deny", action="store_true", help="skip the deny rules")
    p.add_argument("--no-splits", action="store_true", help="skip the homonym splits")
    p.add_argument("--no-fix", action="store_true", help="skip the retype/redirect fixes")
    p.add_argument("--no-alias-deny", action="store_true", help="skip the per-entity alias hygiene")
    p.add_argument("--no-politica-seudonimos", action="store_true",
                   help="solo diagnóstico: NO aplicar la denegación por defecto de los seudónimos (spec 070)")
    p.add_argument("--root", default=os.environ.get("QA_CENSO_ROOT"),
                   help="private audit root (census.sqlite + esc/reglas_opacas.yaml) the opaque deny rows resolve "
                        "against; default QA_CENSO_ROOT")
    args = p.parse_args()

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        log.error("relations_dir_missing", dir=str(directory))
        return
    try:
        deny, splits, fixes, alias_deny = load_files(directory)
        # AC160 (spec 071): an overlay with opaque rows is applied whole or not at all — leaving a part out (the deny
        # itself, or the fix/split/alias phases the comparison of `privatizar` composes them with) aborts before
        # touching the graph. Spec 070: the pseudonym policy too (a graph without it is not publishable).
        banderas = (("--no-deny", args.no_deny), ("--no-fix", args.no_fix), ("--no-splits", args.no_splits),
                    ("--no-alias-deny", args.no_alias_deny),
                    ("--no-politica-seudonimos", args.no_politica_seudonimos))
        omitidas = [bandera for bandera, activa in banderas if activa]
        if omitidas and any(es_opaca(d) for d in deny):
            log.error("relations_partial_with_opaque", flags=omitidas)
            raise SystemExit(2)
        # The register of the same data tree: the private `filas:` declarations are checked against its rows (AC171).
        splits, deny_rows, fix_rows, alias_deny_rows = _preparar(
            splits, deny, fixes, alias_deny, raiz=args.root, completo=True,
            registro=directory.parent / "rectificaciones" / "registro.yaml")
    except seudonimos.ErrorDeForma as err:   # AC143/AC144/AC156: by file and section, or by position
        print(f"relations: {err}", file=sys.stderr)
        raise
    politica = None if args.no_politica_seudonimos else _prepare_politica(directory)
    if politica is None:
        log.warning("politica_seudonimos_omitida", note="diagnóstico: el grafo resultante no es publicable")
    if not deny_rows and not splits and not fix_rows and not alias_deny_rows and politica is None:
        log.warning("relations_empty", dir=str(directory))
        return

    driver = neo4j_loader.get_driver()
    try:
        if splits and not args.no_splits:
            for i, sp in enumerate(splits):
                apply_split(driver, sp, f"splits[{i}]")
        # Retype/redirect BEFORE deny, so a fixable edge isn't deleted first.
        if fix_rows and not args.no_fix:
            apply_fix(driver, fix_rows)
        if deny_rows and not args.no_deny:
            apply_deny(driver, deny_rows)
        if alias_deny_rows and not args.no_alias_deny:
            apply_alias_deny(driver, alias_deny_rows)
        if politica is not None:
            apply_politica_seudonimos(driver, politica)
        neo4j_loader.compute_degree(driver)  # degrees shift after split/fix/deny/política
    finally:
        driver.close()
    log.info("relations_done")


if __name__ == "__main__":
    main()
