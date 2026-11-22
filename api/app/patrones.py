"""Curated «patrones llamativos» feed (spec 053).

Editorial layer: documented legal-but-striking patterns (threshold-riding minor
contracts, revolving doors, disproportionate capital…) built by crossing the
public registries (BORME/PLACSP/BDNS/BOE) with the corpus. NOT graph data — no
source_kind, no Patrón nodes. Lives in `pipeline/data/patrones/patrones.yaml`
(versioned; bind-mounted read-only into the api AND mcp containers) and is
served at runtime so a merged content PR is visible without rebuilding images.

Validation is the INVERSE of the vigía feed (`ultima_hora_feed.py`): that file
is edited by an autonomous routine, so bad items are skipped; this one is
edited by a human under presumption-of-innocence rules, so validation is
STRICT at file level — one invalid pattern fails the whole load (nothing is
silently omitted). An EXPLICIT empty list (`patrones: []`) is valid: it is the
takedown path, and it must propagate at runtime like any other edit. At
runtime a broken mounted file must not take the section down: we keep serving
the last good parse and log the error loudly (any exception class — the
never-500 contract outranks precise typing).

Pre-merge gate: `python -m app.patrones [ruta]` (exit != 0 on any defect).
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import structlog
import yaml

log = structlog.get_logger()


def _default_path() -> Path:
    env = os.environ.get("PATRONES_FILE")
    if env:
        return Path(env)
    mounted = Path("/data/patrones/patrones.yaml")  # container bind-mount
    if mounted.exists():
        return mounted
    return Path(__file__).parents[2] / "pipeline" / "data" / "patrones" / "patrones.yaml"


PATRONES_PATH = _default_path()

_ISO_FULL = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_LOOSE = re.compile(r"^\d{4}(-\d{2})?(-\d{2})?$")  # hitos: YYYY[-MM[-DD]]
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_ENTITY_LABELS = ("Person:", "Company:", "Organization:", "Court:", "Event:", "Contract:")
_REQUIRED = ("slug", "titulo", "tesis", "tipos", "estado", "estado_fecha",
             "entities", "hitos", "fuentes", "disclaimer")
_TOPES = ("obras", "servicios", "suministros")  # % del tope en la web va por este campo

_lock = threading.Lock()
_cache: tuple[float, list[dict]] | None = None  # (mtime del último parse BUENO, patrones)
_failed_mtime: float | None = None  # negative-cache: no re-parsear un fichero roto por request


def _fail(slug: str, msg: str) -> None:
    raise ValueError(f"patrones.yaml[{slug or '?'}]: {msg}")


def _validate(patrones: list[dict]) -> list[dict]:
    if not isinstance(patrones, list):
        raise ValueError("patrones.yaml: `patrones` debe ser una lista")
    seen: set[str] = set()
    for p in patrones:
        if not isinstance(p, dict):
            raise ValueError("patrones.yaml: cada patrón debe ser un mapping")
        p["slug"] = slug = str(p.get("slug") or "")  # coerción: el YAML puede traer escalares
        if not _SLUG_RE.match(slug):
            _fail(slug, "slug inválido (kebab-case)")
        if slug in seen:
            _fail(slug, "slug duplicado")
        seen.add(slug)
        for k in _REQUIRED:
            v = p.get(k)
            if v is None or (isinstance(v, (str, list)) and not v):
                _fail(slug, f"falta el campo obligatorio `{k}`")
        for k in ("estado_fecha", "descubierto"):
            v = p.get(k)
            if v is not None and not _ISO_FULL.match(str(v)):
                _fail(slug, f"`{k}` debe ser fecha ISO (YYYY-MM-DD), no {v!r}")
        if not isinstance(p["tipos"], list):
            _fail(slug, "`tipos` debe ser una lista")
        p["entities"] = [str(e) for e in p["entities"]]
        for e in p["entities"]:
            if not e.startswith(_ENTITY_LABELS):
                _fail(slug, f"entity sin prefijo de label válido: {e!r}")
        tope = p.setdefault("contratos_tope", "obras")
        if tope not in _TOPES:
            _fail(slug, f"`contratos_tope` debe ser uno de {_TOPES}, no {tope!r}")
        fuente_ids: set[str] = set()
        for f in p["fuentes"]:
            if not isinstance(f, dict):
                _fail(slug, f"fuente no es un mapping: {f!r}")
            fid = str(f.get("id") or "")
            if not fid:
                _fail(slug, "fuente sin `id`")
            if fid in fuente_ids:
                _fail(slug, f"fuente duplicada: {fid!r}")
            fuente_ids.add(fid)
            if not str(f.get("etiqueta") or "").strip():
                _fail(slug, f"fuente {fid!r} sin `etiqueta`")
            if not str(f.get("acredita") or "").strip():
                _fail(slug, f"fuente {fid!r} sin `acredita`")
            if not (f.get("url") or f.get("doc")):
                _fail(slug, f"fuente {fid!r} sin `url` ni `doc` (una de las dos)")
        for i, h in enumerate(p["hitos"]):
            if not isinstance(h, dict):
                _fail(slug, f"hitos[{i}] no es un mapping")
            if not _ISO_LOOSE.match(str(h.get("fecha") or "")):
                _fail(slug, f"hitos[{i}].fecha no es ISO (YYYY[-MM[-DD]]): {h.get('fecha')!r}")
            if not str(h.get("hecho") or "").strip():
                _fail(slug, f"hitos[{i}] sin `hecho`")
            refs = h.get("fuentes") or []
            if not refs:
                _fail(slug, f"hitos[{i}] sin `fuentes` (todo hecho lleva fuente)")
            for r in refs:
                if r not in fuente_ids:
                    _fail(slug, f"hitos[{i}] referencia fuente inexistente {r!r}")
        for i, c in enumerate(p.get("contratos") or []):
            if not isinstance(c, dict):
                _fail(slug, f"contratos[{i}] no es un mapping")
            if not _ISO_FULL.match(str(c.get("fecha") or "")):
                _fail(slug, f"contratos[{i}].fecha no es ISO: {c.get('fecha')!r}")
            for k in ("objeto", "organo", "referencia"):
                if not str(c.get(k) or "").strip():
                    _fail(slug, f"contratos[{i}] sin `{k}`")
            amt = c.get("importe_eur")
            if amt is not None and not isinstance(amt, (int, float)):
                _fail(slug, f"contratos[{i}].importe_eur no es numérico: {amt!r}")
        for i, r in enumerate(p.get("contratos_resumen") or []):
            if not isinstance(r, dict) or not str(r.get("periodo") or "").strip():
                _fail(slug, f"contratos_resumen[{i}] sin `periodo`")
            if not isinstance(r.get("n_contratos"), int):
                _fail(slug, f"contratos_resumen[{i}].n_contratos no es entero")
        p.setdefault("actores_sin_grafo", [])
        p.setdefault("contratos", [])
    return patrones


def _parse(path: Path) -> list[dict]:
    """Single parse+validate step shared by runtime and the CLI gate, so the
    gate is a faithful proxy of runtime behavior."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "patrones" not in raw:
        raise ValueError("patrones.yaml: falta la clave raíz `patrones` "
                         "(lista, [] explícito para retirar todo)")
    return _validate(raw["patrones"] if raw["patrones"] is not None else [])


def load_patrones() -> list[dict]:
    """Validated patterns in editorial (file) order, cached by file mtime.

    Runtime contract: strict validation, but a broken or transiently-missing
    mounted file keeps serving the last good parse (never a 500, never a
    silently-dropped pattern) while logging `patrones_invalid`. A failed mtime
    is negative-cached so a broken file costs one parse, not one per request.
    """
    global _cache, _failed_mtime
    try:
        mtime = PATRONES_PATH.stat().st_mtime
    except FileNotFoundError:
        # Ventana unlink+create de un git pull (mount de directorio): servir el
        # last-good; solo devolver vacío si nunca hubo un parse bueno.
        log.warning("patrones_missing", path=str(PATRONES_PATH))
        return _cache[1] if _cache else []
    with _lock:
        if _cache and _cache[0] == mtime:
            return _cache[1]
        if _failed_mtime == mtime:
            return _cache[1] if _cache else []
        try:
            patrones = _parse(PATRONES_PATH)
        except Exception as e:  # noqa: BLE001 — contrato nunca-500: cualquier
            # defecto del fichero montado (parse, validación, shape inesperado)
            # degrada a last-good con log, jamás propaga al endpoint/tool.
            log.error("patrones_invalid", path=str(PATRONES_PATH), error=str(e))
            _failed_mtime = mtime
            return _cache[1] if _cache else []
        _failed_mtime = None
        _cache = (mtime, patrones)
        return patrones


def get_patron(slug: str) -> dict | None:
    return next((p for p in load_patrones() if p["slug"] == slug), None)


if __name__ == "__main__":  # pre-merge gate: python -m app.patrones [ruta]
    import sys

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else PATRONES_PATH
    try:
        patrones = _parse(path)
    except Exception as e:  # noqa: BLE001 — mismo alcance que el runtime
        sys.exit(f"INVALID — {e}")
    for p in patrones:
        print(f"  {p['slug']}: {len(p['hitos'])} hitos · {len(p['contratos'])} contratos "
              f"(tope {p['contratos_tope']}) · {len(p['fuentes'])} fuentes · "
              f"tipos={','.join(p['tipos'])}")
    print(f"OK — {len(patrones)} patrones válidos en {path}")
