"""Stable entity ids (`Type:slug`) — pure stdlib, no pipeline dependencies.

`entity_resolution` mints them during ingestion and every loader resolves names with
them; the spec-070 checks (`seudonimos`, the CI) resolve overlay endpoints with the very
same function without importing the resolver's model stack.
"""
from __future__ import annotations

import re
import unicodedata


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def slug(name: str) -> str:
    s = strip_accents(name).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "entity"


def entity_id(etype: str, canonical: str) -> str:
    return f"{etype}:{slug(canonical)}"
