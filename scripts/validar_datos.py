"""Closed schema of the published data (spec 077).

The site republishes; it does not write. An item therefore carries only the five
fields below, and any other field is an error: that is the gate that keeps our own
text (summaries, quotes, statuses, entity links) out of the site.

    python scripts/validar_datos.py feed.yaml casos.yaml [documentos.yaml]
"""
from __future__ import annotations

import datetime
import sys
from urllib.parse import urlparse

import yaml

ITEM = {"iso", "titulo", "medio", "url", "caso"}
ITEM_REQUIRED = ITEM - {"caso"}
CASO = {"slug", "nombre"}
DOCUMENTO = {"organismo", "designacion", "fecha", "url", "caso"}
DOCUMENTO_REQUIRED = DOCUMENTO - {"caso"}


def _load(path: str, key: str) -> list:
    data = yaml.safe_load(open(path, encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {key} or not isinstance(data[key], list):
        raise SystemExit(f"{path}: se espera un único bloque «{key}:» con una lista")
    return data[key]


def _check(row, allowed: set, required: set, where: str, slugs: set, errors: list) -> None:
    if not isinstance(row, dict):
        errors.append(f"{where}: no es un mapa")
        return
    for extra in sorted(set(row) - allowed):
        errors.append(f"{where}: campo no permitido «{extra}»")
    for missing in sorted(required - set(row)):
        errors.append(f"{where}: falta «{missing}»")
    for field, value in row.items():
        if field in allowed and (not isinstance(value, str) or not value.strip() or value != value.strip()):
            errors.append(f"{where}: «{field}» debe ser texto no vacío y sin espacios en los extremos")
    url = row.get("url")
    if isinstance(url, str) and (urlparse(url).scheme != "https" or not urlparse(url).netloc):
        errors.append(f"{where}: la url debe ser https")
    for field in ("iso", "fecha"):
        value = row.get(field)
        if isinstance(value, str):
            try:
                datetime.date.fromisoformat(value)
            except ValueError:
                errors.append(f"{where}: «{field}» no es una fecha AAAA-MM-DD")
    if "caso" in row and row["caso"] not in slugs:
        errors.append(f"{where}: el caso «{row['caso']}» no existe")


def validar(feed: str, casos: str, documentos: str | None = None) -> list[str]:
    errors: list[str] = []
    cases = _load(casos, "casos")
    for index, case in enumerate(cases):
        _check(case, CASO, CASO, f"casos[{index}]", set(), errors)
    slugs = {case["slug"] for case in cases if isinstance(case, dict) and isinstance(case.get("slug"), str)}
    if len(slugs) != len(cases):
        errors.append("casos: hay slugs repetidos o ausentes")
    items = _load(feed, "items")
    for index, item in enumerate(items):
        _check(item, ITEM, ITEM_REQUIRED, f"items[{index}]", slugs, errors)
    urls = [item.get("url") for item in items if isinstance(item, dict)]
    for url in sorted({url for url in urls if urls.count(url) > 1}):
        errors.append(f"items: url repetida {url}")
    dates = [item.get("iso") for item in items if isinstance(item, dict)]
    if dates != sorted(dates, reverse=True):
        errors.append("items: no están ordenados del más reciente al más antiguo")
    if documentos:
        for index, document in enumerate(_load(documentos, "documentos")):
            _check(document, DOCUMENTO, DOCUMENTO_REQUIRED, f"documentos[{index}]", slugs, errors)
    return errors


if __name__ == "__main__":
    problems = validar(*sys.argv[1:4])
    print("\n".join(problems) if problems else "datos válidos")
    sys.exit(1 if problems else 0)
