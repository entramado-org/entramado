"""Read-only view of the graph's controlled vocabulary (spec 069) for the API and the MCP.

The single source is `pipeline/data/ontologia/ontologia.yaml`; the pipeline validates it
(`pipeline/src/ontologia.py`, CI). Here we only read the few things the public layers serve:
procedural statuses with their rank, provenance layers with their trust rank, entity types.
Baked into the Render image at /data/ontologia (like patrones) and bind-mounted read-only in compose.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml


def _default_path() -> Path:
    env = os.environ.get("ONTOLOGIA_FILE")
    if env:
        return Path(env)
    mounted = Path("/data/ontologia/ontologia.yaml")  # container bake / bind-mount
    if mounted.exists():
        return mounted
    return Path(__file__).parents[2] / "pipeline" / "data" / "ontologia" / "ontologia.yaml"


@lru_cache(maxsize=1)
def _raw() -> dict:
    return yaml.safe_load(_default_path().read_text(encoding="utf-8"))


def version() -> int:
    return _raw()["version"]


def estados() -> tuple[str, ...]:
    """File order = procedural ladder (closed dispositions first)."""
    return tuple(_raw()["estados_procesales"])


def rango_estado() -> dict[str, int]:
    """Seriousness rank used to headline the most advanced charge (ties allowed)."""
    return {k: f["orden"] for k, f in _raw()["estados_procesales"].items()}


def estados_publicos() -> tuple[str, ...]:
    """Statuses in reading order for texts: the ladder, then the closed dispositions."""
    e = _raw()["estados_procesales"]
    return (*(k for k in e if e[k]["fase"] != "cerrada"), *(k for k in e if e[k]["fase"] == "cerrada"))


def rango_capa() -> dict[str, int]:
    """source_kind trust order (lower = more authoritative); `judicial` is a null source_kind."""
    return {k: f["rango"] for k, f in _raw()["capas"].items()}


def capas() -> tuple[str, ...]:
    c = _raw()["capas"]
    return tuple(sorted(c, key=lambda k: c[k]["rango"]))


def tipos_entidad() -> tuple[str, ...]:
    return tuple(_raw()["tipos_entidad"])
