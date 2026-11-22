"""Deterministic guards on an extractor's output (spec 069), shared by EVERY extraction path:
MLX (`extract_facts`) and the raw Claude swarm (`scripts/_ingest_apply`). Before 069 the swarm
path ran only the Pydantic check and skipped all of these.

`validar_extraccion` validates item by item and never discards a whole chunk. It returns the
accepted extraction AND the rejections that need a person (`revision.encolar`), never the graph:
- a relation or entity type outside the controlled vocabulary, or a loader-only relation type;
- a crime name with no canonical form (or an ambiguous one);
- endpoint types the term card does not admit;
- with `acusatorias_a_revision`, every accusatory relation (no verifier on that path).
Known extraction artefacts (money as a node, boilerplate LegalCase, OWNS of a brand…) are still
dropped with a log line: those are fixed rules, not doubts.
"""
from __future__ import annotations

import re
import unicodedata

import structlog

from .cleanup import is_boilerplate_legalcase
from .ontologia import ontologia
from .schema import ChunkExtraction, EntityType, ExtractedEntity, ExtractedRelation, RelationType

log = structlog.get_logger()
_ONT = ontologia()


# Relation types whose SUBJECT (owner) must be grounded in the chunk that asserts
# them. Ownership/titularidad is locally asserted: if the owner is named nowhere in
# the chunk text, the model invented the attribution — typically pinning it onto a
# salient SEED person (e.g. David Sánchez's AEAT patrimony in Informe-InstruccionN3
# p.27 got hung off Begoña Gómez, who appears nowhere in that document). See
# specs / audit run 20260618-140803.
_SUBJECT_MUST_APPEAR = {RelationType.OWNS}
# ACCUSED_OF must point at a delito (:Crime), never a company/organism/event/person.
# The extractor often mis-models "X influyó a favor de <empresa>" or "<acusador> acusó a
# <persona>" as ACCUSED_OF; those are schema violations (and the real accusation-of-crime
# is captured by a correctly-directed Person→Crime edge). See manual_fix_accused_of_20260619
# + audit run 20260619. Dropped only when the target's type is KNOWN and ≠ Crime.
_CRIME_ONLY_TARGET = {RelationType.ACCUSED_OF, RelationType.CONVICTED_OF}
# PARTICIPATED_IN must never point at a Person, and INVOLVES must never link two Persons:
# nothing "participates in" a person, and a person does not "involve" another person.
# The extractor mis-models "Ábalos participó con Sánchez" / "Koldo involucra a Sánchez" as
# these event-shaped edges (PARTICIPATED_IN→Person 62, INVOLVES Person→Person 49 graph-wide;
# see Pedro Sánchez ego audit, run 20260619). The genuine link is usually MET_WITH/
# ASSOCIATED_WITH, which the extractor emits separately; here we only DROP the schema
# violation. Dropped only when the offending endpoint type(s) are KNOWN (and Person).
_NO_PERSON_TARGET = {RelationType.PARTICIPATED_IN}
_NO_PERSON_TO_PERSON = {RelationType.INVOLVES}
# Amount/currency are meaningful ONLY on a genuine money-flow edge. The extractor sprays
# them onto any relation that sits near a number (ACCUSED_OF, OCCURRED_AT, WORKS_FOR,
# ASSOCIATED_WITH, PARTICIPATED_IN…), polluting /money-flows, flujos_dinero and the
# briefings money trail. We strip amount/currency off non-flow edges (the edge itself is
# kept). Durable root-fix for the retroactive money_amount_sanitize (442 edges; QA def-3
# INCOMPLETO / spurious cuantía). Mirrors the sanitize's MONEY_RELS set.
_MONEY_FLOW_RELS = {RelationType.PAID, RelationType.RECEIVED,
                    RelationType.AWARDED_CONTRACT, RelationType.CONTRACT_AWARDED_TO}
# Backstop (the prompt is the primary fix): consumer device/electronics brands the
# extractor recurrently mis-models as an OWNS target — "teléfonos DE LA MARCA iPhone",
# "grabadora marca Philips" → Koldo OWNS iPhone/Philips. Owning a device of brand X is
# not owning the brand/company. Normalised names; dropped only as OWNS targets. See QA
# runs 20260622-133830 / -181811 (Koldo OWNS iPhone/Philips). Bank-account titularidad
# (Koldo OWNS "Caja Rural") is left to the prompt — banks are legitimate OWNS targets in
# general, so a blocklist would over-drop.
_OWNS_BRAND_BLOCKLIST = {
    "iphone", "philips", "apple", "samsung", "nokia", "huawei", "xiaomi",
    "blackberry", "motorola", "sony", "lg",
}
# Money-as-node backstop (the prompt is the primary fix, the enum no longer advertises
# MoneyAmount). An amount is never a graph node nor a relation target: a bare number /
# "15.000 euros" / "53 millones" / "91,7%" target means the extractor modelled the amount
# as a counterparty. The value belongs in rel.amount/currency. We DROP the relation and
# never materialise the money entity. See manual_fix_money_nodes_20260623 (949 nodes).
# NB: the "millones" multiplier requires a leading space so the company "3M" (mascarillas
# supplier) is NOT mistaken for "3 millones". Currency/percent suffix may be space-attached.
_MONEY_NAME_RE = re.compile(
    r"^[€$]?\s*[0-9][0-9.,]*\s*(?:euros?|eur|€|%|\$|usd|d[óo]lares?)?"
    r"(?:\s+(?:millones?|mill[óo]n))?(?:\s+de\s+(?:euros?|d[óo]lares?))?\s*$",
    re.IGNORECASE,
)


def _is_money_name(name: str) -> bool:
    return bool(_MONEY_NAME_RE.match((name or "").strip()))


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", s)


def _subject_grounded(name: str, ntext: str) -> bool:
    """True if `name` (or any significant token of it) appears in normalised chunk
    text. Conservative: only drops when NONE of the name's >3-char tokens appears —
    a strong signal the subject is absent, not mere coreference by pronoun."""
    nnm = _norm(name).strip()
    if not nnm:
        return True  # nothing to check against; don't drop
    if nnm in ntext:
        return True
    toks = [t for t in nnm.split() if len(t) > 3]
    return any(t in ntext for t in toks) if toks else (nnm in ntext)


def _rechazo(motivo: str, detalle: str, termino, item: dict, entidades: list[dict] | None = None) -> dict:
    return {"motivo": motivo, "detalle": detalle, "termino": termino, "item": item,
            "entidades": entidades or []}


def validar_extraccion(data: dict, chunk_text: str = "", *, acusatorias_a_revision: bool = False
                       ) -> tuple[ChunkExtraction, list[dict]]:
    """Validate an extractor's JSON item by item. Returns (accepted, rejections for review)."""
    ents, rels, rechazos = [], [], []
    dropped_boiler_lc: set[str] = set()  # names dropped as boilerplate LegalCase (spec 024)
    crime_alias: dict[str, str] = {}     # normalised raw crime name → canonical name
    crime_rejected: dict[str, dict] = {}  # normalised raw crime name → its entity dict
    for e in data.get("entities") or []:
        etype = e.get("type") if isinstance(e, dict) else None
        if isinstance(e, dict) and not _ONT.validar("tipos_entidad", etype).ok:
            rechazos.append(_rechazo("tipo_entidad_fuera_de_vocabulario", f"tipo {etype!r}", etype, e))
            continue
        try:
            ent = ExtractedEntity.model_validate(e)
        except Exception:  # noqa: BLE001
            continue
        # An amount is never an entity (drop both the explicit MoneyAmount type and any
        # bare-number name the model labelled as something else, e.g. Event "91,7%").
        if ent.type is EntityType.MONEY_AMOUNT or _is_money_name(ent.name):
            log.info("money_node_guard_dropped_entity", name=ent.name, type=ent.type.value)
            continue
        # A statute / article / generic procedural phrase is not a case (spec 024). Drop the
        # boilerplate LegalCase node so it never reaches the facts cache / graph; remember its
        # name to also drop the false PARTY_TO edges the extractor hung off it.
        if ent.type is EntityType.LEGAL_CASE and is_boilerplate_legalcase(ent.name):
            dropped_boiler_lc.add(_norm(ent.name))
            log.info("boilerplate_legalcase_guard_dropped_entity", name=ent.name)
            continue
        # Crimes are a closed vocabulary (spec 069): canonical name, or review.
        if ent.type is EntityType.CRIME:
            r = _ONT.validar("delitos", ent.name)
            if not r.ok:
                crime_rejected[_norm(ent.name)] = ent.model_dump(mode="json")
                rechazos.append(_rechazo("delito_no_canonico", r.motivo, ent.name, ent.model_dump(mode="json")))
                continue
            crime_alias[_norm(ent.name)] = r.valor
            ent.name = r.valor
        ents.append(ent)
    ntext = _norm(chunk_text)
    type_by_name = {_norm(e.name): e.type for e in ents}
    ent_by_name = {_norm(e.name): e.model_dump(mode="json") for e in ents}
    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            continue
        ends = [x for x in (ent_by_name.get(_norm(r.get("source") or "")),
                            ent_by_name.get(_norm(r.get("target") or ""))) if x]
        rt = _ONT.validar("tipos_relacion", r.get("type"), extraccion=True)
        if not rt.ok:
            motivo = ("tipo_solo_loader" if _ONT.canon_relacion(r.get("type")) else "tipo_fuera_de_vocabulario")
            rechazos.append(_rechazo(motivo, rt.motivo, r.get("type"), r, ends))
            continue
        if _norm(r.get("target") or "") in crime_rejected:
            rechazos.append(_rechazo("delito_no_canonico", "el objeto es un delito sin forma canónica",
                                     rt.valor, r, [*ends, crime_rejected[_norm(r["target"])]]))
            continue
        try:
            rel = ExtractedRelation.model_validate(r)
        except Exception:  # noqa: BLE001
            continue
        if _norm(rel.target) in crime_alias:
            rel.target = crime_alias[_norm(rel.target)]
        # Money-as-node: a relation whose target is an amount (explicit MoneyAmount type or a
        # bare-number/"15.000 euros"/"53 millones"/"%" name) has no counterparty — drop it;
        # the value belongs in rel.amount/currency on a proper actor↔actor flow edge.
        if type_by_name.get(_norm(rel.target)) is EntityType.MONEY_AMOUNT or _is_money_name(rel.target):
            log.info("money_node_guard_dropped_rel", rel=rel.type.value,
                     source=rel.source, target=rel.target)
            continue
        # Boilerplate-LegalCase target (spec 024): the edge points at a statute/generic term
        # (e.g. PARTY_TO "auto de imputación"). Drop it if the target was dropped above, or if
        # it's a bare/unknown target the predicate flags (a KNOWN non-LegalCase type is spared,
        # so a real Organization like "Orden de Malta" is never touched).
        _tgt_type = type_by_name.get(_norm(rel.target))
        if _norm(rel.target) in dropped_boiler_lc or (
            _tgt_type in (None, EntityType.LEGAL_CASE) and is_boilerplate_legalcase(rel.target)
        ):
            log.info("boilerplate_legalcase_guard_dropped_rel", rel=rel.type.value,
                     source=rel.source, target=rel.target)
            continue
        if rel.type in _SUBJECT_MUST_APPEAR and ntext and not _subject_grounded(rel.source, ntext):
            log.info("owns_guard_dropped", subject=rel.source, target=rel.target)
            continue
        if rel.type is RelationType.OWNS and _norm(rel.target).strip() in _OWNS_BRAND_BLOCKLIST:
            log.info("owns_brand_guard_dropped", subject=rel.source, target=rel.target)
            continue
        if rel.type in _CRIME_ONLY_TARGET:
            tgt_type = type_by_name.get(_norm(rel.target))
            if tgt_type is not None and tgt_type != EntityType.CRIME:
                # Spec 069: an accusation whose object is not a crime is outside its term card —
                # it goes to review (never dropped in silence), even when the subject is unknown.
                rechazos.append(_rechazo("extremos_fuera_de_ficha", f"objeto {tgt_type.value}",
                                         rel.type.value, rel.model_dump(mode="json"), ends))
                continue
        if rel.type in _NO_PERSON_TARGET:
            tgt_type = type_by_name.get(_norm(rel.target))
            if tgt_type == EntityType.PERSON:
                log.info("event_rel_guard_dropped", rel=rel.type.value,
                         source=rel.source, target=rel.target, reason="target_is_person")
                continue
        if rel.type in _NO_PERSON_TO_PERSON:
            s_type = type_by_name.get(_norm(rel.source))
            t_type = type_by_name.get(_norm(rel.target))
            if s_type == EntityType.PERSON and t_type == EntityType.PERSON:
                log.info("event_rel_guard_dropped", rel=rel.type.value,
                         source=rel.source, target=rel.target, reason="person_to_person")
                continue
        if rel.type not in _MONEY_FLOW_RELS and (rel.amount is not None or rel.currency is not None):
            log.info("amount_guard_stripped", rel=rel.type.value, source=rel.source,
                     target=rel.target, amount=rel.amount)
            rel.amount = None
            rel.currency = None
        # Endpoint types the term card admits (checked only when both types are known).
        s_type = type_by_name.get(_norm(rel.source))
        t_type = type_by_name.get(_norm(rel.target))
        if s_type and t_type and not _ONT.extremos_ok(rel.type.value, s_type.value, t_type.value):
            rechazos.append(_rechazo("extremos_fuera_de_ficha", f"{s_type.value} → {t_type.value}",
                                     rel.type.value, rel.model_dump(mode="json"), ends))
            continue
        # No verifier on this path: a relation whose doubt policy is `revision` (every accusatory
        # type, plus the per-type exceptions such as CONTROLS and FAMILY_OF) is never decided by
        # the extractor alone.
        if acusatorias_a_revision and _ONT.ante_duda(rel.type.value) == "revision":
            motivo = ("acusatoria_sin_verificador" if rel.type.value in _ONT.acusatorias
                      else "revision_sin_verificador")
            rechazos.append(_rechazo(motivo, "vía de extracción sin verificador",
                                     rel.type.value, rel.model_dump(mode="json"), ends))
            continue
        rels.append(rel)
    return ChunkExtraction(entities=ents, relations=rels), rechazos


def encolar_rechazos(punto: str, meta: dict, rechazos: list[dict], *, prompt_sha256: str | None = None) -> int:
    """Send one chunk's rejections to the review queue with what `aprobar` needs to write them
    back (doc, chunk, page, the item and its endpoint entities)."""
    from . import revision  # local: revision imports config (paths), guards stay import-light

    for r in rechazos:
        payload = {"doc_id": meta["doc_id"], "chunk_id": meta["chunk_id"],
                   "page_start": meta.get("page_start"), "entities": r["entidades"]}
        payload["relation" if "source" in r["item"] else "entity"] = r["item"]
        revision.encolar(punto, r["motivo"], payload, detalle=r["detalle"], termino=r["termino"],
                         prompt_sha256=prompt_sha256)
    return len(rechazos)
