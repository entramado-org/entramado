"""Editorial «última hora» feed (spec 044).

The feed is press-only case developments for the public site — the editorial
counterpart of the graph's `ultima_hora` overlay, NOT graph data. It lives in
`pipeline/data/ultima_hora/feed.yaml` (versioned; bind-mounted read-only into
the api container) and is served at runtime so a merged feed PR is visible
without rebuilding any image. Same mtime-cache pattern as `cases.py`.

Unlike cases.yaml (human-edited, baked into the image, fails loud), this file
is edited by an autonomous routine twice a day: a malformed item is SKIPPED
with a structured warning and the endpoint stays 200 — one bad item must never
take the whole feed down.
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
    env = os.environ.get("ULTIMA_HORA_FEED")
    if env:
        return Path(env)
    mounted = Path("/data/ultima_hora/feed.yaml")  # container bind-mount
    if mounted.exists():
        return mounted
    return Path(__file__).parents[2] / "pipeline" / "data" / "ultima_hora" / "feed.yaml"


FEED_PATH = _default_path()

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# URL patterns of agency teletipos republished by another outlet; such items must
# credit the agency in `outlet` («EFE (vía Infobae)») — see the feed.yaml header rule.
_AGENCY_REPUBLISH_RE = re.compile(r"infobae\.com/(?:[a-z]+/)+agencias/")
_REQUIRED = ("iso", "date", "headline", "dek", "outlet", "url")
_ENTITY_LABELS = ("Person:", "Company:", "Organization:", "Court:", "Event:", "Contract:")
# yaml key -> API key (camelCase, matching the shape the web consumed pre-044)
_OPTIONAL = {"quote": "quote", "status": "status", "case_tag": "caseTag",
             "focus": "focus", "briefing_slug": "briefingSlug"}

_lock = threading.Lock()
_cache: tuple[float, list[dict]] | None = None  # (mtime, items)


def _clean(raw: list) -> list[dict]:
    items: list[dict] = []
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            log.warning("ultima_hora_item_skipped", index=i, reason="not a mapping")
            continue
        missing = [k for k in _REQUIRED if not str(it.get(k) or "").strip()]
        if missing:
            log.warning("ultima_hora_item_skipped", index=i,
                        iso=it.get("iso"), reason=f"missing {missing}")
            continue
        if not _ISO_RE.match(str(it["iso"])):
            log.warning("ultima_hora_item_skipped", index=i,
                        iso=it.get("iso"), reason="iso is not YYYY-MM-DD")
            continue
        entities = []
        for e in it.get("entities") or []:
            if str(e).startswith(_ENTITY_LABELS):
                entities.append(str(e))
            else:
                log.warning("ultima_hora_entity_dropped", index=i, iso=it["iso"], entity=e)
        out = {k: str(it[k]).strip() for k in _REQUIRED}
        if _AGENCY_REPUBLISH_RE.search(out["url"]) and "vía" not in out["outlet"]:
            log.warning("ultima_hora_agency_unattributed", index=i, iso=out["iso"],
                        outlet=out["outlet"], url=out["url"])
        out["entities"] = entities
        for src, dst in _OPTIONAL.items():
            v = it.get(src)
            if v is not None and str(v).strip():
                out[dst] = str(v).strip()
        items.append(out)
    # Stable sort newest-first: shields against an append instead of a prepend.
    items.sort(key=lambda x: x["iso"], reverse=True)
    return items


def load_feed() -> list[dict]:
    """Parsed feed items, newest first, cached by file mtime."""
    global _cache
    try:
        mtime = FEED_PATH.stat().st_mtime
    except FileNotFoundError:
        log.warning("ultima_hora_feed_missing", path=str(FEED_PATH))
        return []
    with _lock:
        if _cache and _cache[0] == mtime:
            return _cache[1]
        try:
            raw = yaml.safe_load(FEED_PATH.read_text(encoding="utf-8")) or {}
            items = _clean(raw.get("items") or [])
        except yaml.YAMLError as e:
            # A broken merge must not 500 the site; keep serving the last good
            # parse if we have one, an empty feed otherwise.
            log.error("ultima_hora_feed_unparseable", path=str(FEED_PATH), error=str(e))
            return _cache[1] if _cache else []
        _cache = (mtime, items)
        return items
