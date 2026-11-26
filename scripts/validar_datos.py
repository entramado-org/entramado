"""Closed schema of the published data (spec 077).

The site republishes; it does not write. An item therefore carries only the four
fields below, and any other field is an error: that is the gate that keeps our own
text (summaries, quotes, statuses, entity links) out of the site.

    python scripts/validar_datos.py feed.yaml [documentos.yaml]
"""
from __future__ import annotations

import datetime
import re
import sys

import yaml

# Same expression as `web/lib/datos.ts`: https, lowercase host without credentials or port, and a path.
URL_HTTPS = re.compile(r"https://([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+)/[^\s\\]*")
FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")
ITEM = {"iso", "titulo", "medio", "url"}
DOCUMENTO = {"organismo", "designacion", "fecha", "url"}
# An official document is only a link to its issuing body's own site.
ORGANISMOS = {
    "Senado": ("www.senado.es",),
    "Congreso de los Diputados": ("www.congreso.es",),
    "Poder Judicial": ("www.poderjudicial.es",),
    "Boletín Oficial del Estado": ("www.boe.es",),
    "Tribunal de Cuentas": ("www.tcu.es",),
}


class _SinClavesRepetidas(yaml.SafeLoader):
    """PyYAML keeps the last of two equal keys; the web build rejects them. So do we."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        for key in keys:
            if keys.count(key) > 1:
                raise yaml.constructor.ConstructorError(None, None, f"clave repetida «{key}»", node.start_mark)
        return super().construct_mapping(node, deep=deep)


def clave_url(url: str) -> str:
    """The same piece under another spelling: no query, no fragment, no trailing slash."""
    return re.split(r"[?#]", url)[0].rstrip("/")


def _load(path: str, key: str) -> list:
    with open(path, encoding="utf-8") as handle:
        try:
            data = yaml.load(handle, Loader=_SinClavesRepetidas)  # noqa: S506 - a SafeLoader subclass
        except yaml.YAMLError as error:
            raise SystemExit(f"{path}: {error}")
    if not isinstance(data, dict) or set(data) != {key} or not isinstance(data[key], list):
        raise SystemExit(f"{path}: se espera un único bloque «{key}:» con una lista")
    return data[key]


def _check(row, allowed: set, where: str, errors: list) -> None:
    if not isinstance(row, dict):
        errors.append(f"{where}: no es un mapa")
        return
    for extra in sorted(set(row) - allowed):
        errors.append(f"{where}: campo no permitido «{extra}»")
    for missing in sorted(allowed - set(row)):
        errors.append(f"{where}: falta «{missing}»")
    for field, value in row.items():
        if field in allowed and (not isinstance(value, str) or not value.strip() or value != value.strip()):
            errors.append(f"{where}: «{field}» debe ser texto no vacío y sin espacios en los extremos")
    url = row.get("url")
    match = URL_HTTPS.fullmatch(url) if isinstance(url, str) else None
    if isinstance(url, str) and not match:
        errors.append(f"{where}: la url debe ser https y absoluta")
    if "organismo" in row and match:
        hosts = ORGANISMOS.get(row["organismo"])
        if hosts is None:
            errors.append(f"{where}: organismo desconocido «{row['organismo']}»")
        elif match.group(1) not in hosts:
            errors.append(f"{where}: la url no es de la web de «{row['organismo']}»")
    for field in ("iso", "fecha"):
        value = row.get(field)
        if isinstance(value, str):
            try:
                if not FECHA.fullmatch(value):
                    raise ValueError(value)
                if datetime.date.fromisoformat(value) > datetime.date.today():
                    raise ValueError(value)
            except ValueError:
                errors.append(f"{where}: «{field}» no es una fecha AAAA-MM-DD")


def validar(feed: str, documentos: str | None = None) -> list[str]:
    errors: list[str] = []
    items = _load(feed, "items")
    for index, item in enumerate(items):
        _check(item, ITEM, f"items[{index}]", errors)
    urls = [clave_url(item["url"]) for item in items if isinstance(item, dict) and isinstance(item.get("url"), str)]
    for url in sorted({url for url in urls if urls.count(url) > 1}):
        errors.append(f"items: url repetida {url}")
    dates = [item.get("iso") for item in items if isinstance(item, dict)]
    if dates != sorted(dates, reverse=True):
        errors.append("items: no están ordenados del más reciente al más antiguo")
    if documentos:
        documents = _load(documentos, "documentos")
        for index, document in enumerate(documents):
            _check(document, DOCUMENTO, f"documentos[{index}]", errors)
        doc_urls = [clave_url(document["url"]) for document in documents if isinstance(document, dict) and isinstance(document.get("url"), str)]
        for url in sorted({url for url in doc_urls if doc_urls.count(url) > 1}):
            errors.append(f"documentos: url repetida {url}")
    return errors


if __name__ == "__main__":
    problems = validar(*sys.argv[1:3])
    print("\n".join(problems) if problems else "datos válidos")
    sys.exit(1 if problems else 0)
