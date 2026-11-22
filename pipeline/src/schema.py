"""Closed-set graph schema + Pydantic models for LLM extraction output.

The extractor returns JSON validated against `ChunkExtraction`. Node/relationship
type vocabularies are closed so the graph stays clean and queryable.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .ontologia import ontologia

_ONT = ontologia()


def _member(value: str) -> str:
    """Enum member name for an ontology term: `LegalCase` → `LEGAL_CASE`, `MET_WITH` → `MET_WITH`."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", "_", value).upper()


# Both enums are DERIVED from pipeline/data/ontologia/ontologia.yaml (spec 069): never add a
# member here, add a term card there.
EntityType = Enum("EntityType", [(_member(t), t) for t in _ONT.tipos_entidad], type=str)
RelationType = Enum("RelationType", [(_member(t), t) for t in _ONT.tipos_relacion], type=str)


class ExtractedEntity(BaseModel):
    name: str = Field(..., description="Canonical surface form, honorifics stripped")
    type: EntityType
    aliases: list[str] = Field(default_factory=list)
    role: Optional[str] = Field(None, description="Cargo / descripción breve")

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split()).strip()


class ExtractedRelation(BaseModel):
    source: str = Field(..., description="Entity name of the source")
    target: str = Field(..., description="Entity name of the target")
    type: RelationType
    source_quote: str = Field("", description="Verbatim snippet supporting this fact")
    # Optional typed properties (mostly for PAID/RECEIVED/Event edges)
    amount: Optional[float] = None
    currency: Optional[str] = None
    date: Optional[str] = None

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v):
        if isinstance(v, str):
            # Unknown values pass through unchanged so Pydantic rejects them.
            return _ONT.canon_relacion(v) or v
        return v

    @field_validator("source_quote")
    @classmethod
    def _truncate_quote(cls, v: str) -> str:
        return (v or "").strip()[:300]


class ChunkExtraction(BaseModel):
    """Top-level structure the LLM must return for one chunk."""
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)


# Canonical crimes (spec 069): the prompt lists these; synonyms are canonicalised in code.
CRIME_TERMS = list(_ONT.delitos)
