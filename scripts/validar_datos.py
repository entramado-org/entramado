"""Closed schema of the published data (spec 077).

The site republishes; it does not write. An item therefore carries only the four
fields below, and any other field is an error: that is the gate that keeps our own
text (summaries, quotes, statuses, entity links) out of the site.

    python scripts/validar_datos.py feed.yaml [documentos.yaml]

`medios.yaml`, next to the feed, lists the outlet sites an item may link to.
"""
from __future__ import annotations

import datetime
import pathlib
import posixpath
import re
import sys
import unicodedata
import urllib.parse

import yaml

# Same expression as `web/lib/datos.ts`: https, lowercase host without credentials or port, and a path.
URL_HTTPS = re.compile(r"https://([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+)/[^\s\\]*")
ROMANOS = {"14": "XIV", "15": "XV", "16": "XVI"}
# The designation is derived from the file name of the official PDF: chamber, series, legislature and
# number. A document of a series not listed here does not enter until its pattern is added.
SERIES = {
    "Senado": [(re.compile(r"DS_C_(\d+)_(\d+)\.PDF"), "Diario de Sesiones del Senado. Comisiones, núm. {n} ({leg} Legislatura)")],
    "Congreso de los Diputados": [
        (re.compile(r"DSCD-(\d+)-CI-(\d+)\.PDF"), "Diario de Sesiones del Congreso de los Diputados. Comisiones de Investigación, núm. {n} ({leg} Legislatura)"),
    ],
}


def designacion_de(organismo: str, fichero: str) -> str | None:
    for patron, plantilla in SERIES.get(organismo, ()):
        hallado = patron.fullmatch(fichero)
        if hallado and hallado.group(1) in ROMANOS:
            return plantilla.format(n=hallado.group(2), leg=ROMANOS[hallado.group(1)])
    return None


# A press item links to a page, never to an attached file (a ruling, an image, a spreadsheet):
# the last path segment has no dot at all, or its only dot is that of a page extension.
FICHERO = re.compile(r"\.(?!(?:html?|shtml|php|aspx?)$)[^/]*$", re.I)
# The address of a press piece, as every outlet in the feed writes it: plain path segments, nothing
# percent-encoded, no "." or ".." segment, no query, no fragment. What the browser opens is what is written.
PIEZA = re.compile(r"https://[a-z0-9.-]+(?:/[A-Za-z0-9_~-]+(?:\.[A-Za-z0-9_~-]+)*)+/?")
HOST = re.compile(r"(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
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
    """The same piece under another spelling: host plus decoded, normalised path; no query or fragment."""
    parts = urllib.parse.urlsplit(url)
    path = posixpath.normpath(urllib.parse.unquote(parts.path)) if parts.path else "/"
    host = (parts.hostname or "").removeprefix("www.")
    return f"{host}{path}".rstrip("/").lower()


def _raros(texto: str) -> list[str]:
    """Caracteres que un lector de YAML puede tratar como salto de línea, o que no se ven, y otro no.

    El validador (PyYAML) y la web (paquete `yaml`) leen el mismo fichero: solo se admiten caracteres visibles,
    el espacio y el salto de línea `\\n`. Fuera quedan los de control (U+0085 incluido), los separadores de línea y
    de párrafo, los de formato invisibles y los no asignados."""
    return sorted({f"U+{ord(c):04X}" for c in texto
                   if c != "\n" and (unicodedata.category(c)[0] == "C" or unicodedata.category(c) in ("Zl", "Zp"))})


def _load(path: str, key: str) -> list:
    with open(path, encoding="utf-8", newline="") as handle:
        texto = handle.read()
    if raros := _raros(texto):
        raise SystemExit(f"{path}: caracteres no admitidos: {', '.join(raros)}")
    if True:
        try:
            data = yaml.load(texto, Loader=_SinClavesRepetidas)  # noqa: S506 - a SafeLoader subclass
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
        elif field in allowed and any(unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") for char in value):
            errors.append(f"{where}: «{field}» lleva saltos de línea o caracteres de control")
    url = row.get("url")
    match = URL_HTTPS.fullmatch(url) if isinstance(url, str) else None
    if isinstance(url, str) and not match:
        errors.append(f"{where}: la url debe ser https y absoluta")
    elif match and not posixpath.normpath(urllib.parse.unquote(urllib.parse.urlsplit(url).path) or "/").strip("/"):
        errors.append(f"{where}: la url es una portada, no una pieza")
    if "titulo" in row and match and not PIEZA.fullmatch(url):
        errors.append(f"{where}: la url de una pieza solo lleva tramos de letras, números, guiones y puntos, sin codificar, sin consulta y sin fragmento")
    elif "titulo" in row and match and FICHERO.search(posixpath.normpath(urllib.parse.unquote(urllib.parse.urlsplit(url).path))):
        errors.append(f"{where}: la url es un fichero adjunto, no una pieza de un medio")
    if "organismo" in row and match:
        hosts = ORGANISMOS.get(row["organismo"])
        if hosts is None:
            errors.append(f"{where}: organismo desconocido «{row['organismo']}»")
        elif match.group(1) not in hosts:
            errors.append(f"{where}: la url no es de la web de «{row['organismo']}»")
        else:
            # The link must be the announced document itself: a PDF whose file name carries its number.
            fichero = posixpath.basename(urllib.parse.urlsplit(url).path)
            esperada = designacion_de(row["organismo"], fichero)
            if esperada is None:
                errors.append(f"{where}: «{fichero}» no es una serie conocida de «{row['organismo']}»")
            elif row.get("designacion") != esperada:
                errors.append(f"{where}: la designación de «{fichero}» es «{esperada}»")
    for field in ("iso", "fecha"):
        value = row.get(field)
        if isinstance(value, str):
            try:
                if not FECHA.fullmatch(value):
                    raise ValueError(value)
                parsed = datetime.date.fromisoformat(value)
                if not datetime.date(2000, 1, 1) <= parsed <= datetime.datetime.now(datetime.timezone.utc).date():
                    raise ValueError(value)
            except ValueError:
                errors.append(f"{where}: «{field}» no es una fecha AAAA-MM-DD")


def validar(feed: str, documentos: str | None = None) -> list[str]:
    errors: list[str] = []
    items = _load(feed, "items")
    if not items:
        errors.append("items: la lista está vacía")
    medios = _load(str(pathlib.Path(feed).with_name("medios.yaml")), "medios")
    if not medios or any(not isinstance(host, str) or not HOST.fullmatch(host) for host in medios) or len(set(medios)) != len(medios):
        errors.append("medios: se espera una lista de hosts distintos, en minúsculas")
    for index, item in enumerate(items):
        host = urllib.parse.urlsplit(item["url"]).hostname if isinstance(item, dict) and isinstance(item.get("url"), str) else None
        if host is not None and host not in medios:
            errors.append(f"items[{index}]: «{host}» no es un medio dado de alta en medios.yaml")
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
        for url in sorted(set(urls) & set(doc_urls)):
            errors.append(f"items: {url} es un documento oficial, no una pieza de un medio")
    return errors


if __name__ == "__main__":
    problems = validar(*sys.argv[1:3])
    print("\n".join(problems) if problems else "datos válidos")
    sys.exit(1 if problems else 0)
