"""Periodic link check of the published data (spec 077).

Lists every item whose link no longer answers or whose visible headline on the
outlet's site is no longer exactly the one we publish, and every official
document whose link no longer serves a PDF from its issuing body. It reports; it
never edits the data. Run it from the Mac inside the vigía cycle: outlets answer
403 to cloud runners.

    python scripts/enlaces.py datos/feed.yaml [datos/documentos.yaml]

Exit code 1 when there is anything to review.
"""
from __future__ import annotations

import pathlib
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

import yaml

from titulares import UA, Pieza, leer_todas
from validar_datos import ORGANISMOS

# Outlets whose first <h1> is not the piece's headline. Each entry says where the
# visible headline is instead; any other outlet is compared against its <h1> only.
SIN_H1 = {
    "www.poderjudicial.es": lambda pieza: pieza.title.split(" | ")[0],  # the <h1> is the cookie banner
    "www.eldebate.com": lambda pieza: pieza.og_title,  # headline rendered outside an <h1>
}


def titular_visible(url: str, pieza: Pieza) -> str:
    return SIN_H1.get(urlparse(url).hostname or "", lambda p: p.h1)(pieza)


def revisar_item(item: dict, pieza: Pieza) -> str | None:
    if pieza.status != "200":
        return f"ENLACE {pieza.status}\t{item['url']}"
    visible = titular_visible(item["url"], pieza)
    if visible != item["titulo"]:
        return f"TITULAR CAMBIADO\t{item['url']}\n\tpublicado: {item['titulo']}\n\ten la web: {visible or '(sin titular legible)'}"
    if pieza.published and pieza.published != item["iso"]:
        return f"FECHA DISTINTA\t{item['url']}\n\tpublicada: {item['iso']}\n\ten la web: {pieza.published}"
    return None


def revisar_documento(document: dict) -> str | None:
    """The link must still serve a PDF from the issuing body's own host."""
    request = urllib.request.Request(document["url"], headers={"User-Agent": UA, "Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            host = urlparse(response.geturl()).hostname
            head = response.read(1024)
            status = response.status
    except urllib.error.HTTPError as error:
        return f"DOCUMENTO {error.code}\t{document['url']}"
    except Exception as error:
        return f"DOCUMENTO error:{type(error).__name__}\t{document['url']}"
    if status not in (200, 206):
        return f"DOCUMENTO {status}\t{document['url']}"
    if host not in ORGANISMOS.get(document["organismo"], ()):
        return f"DOCUMENTO REDIRIGIDO a {host}\t{document['url']}"
    if document["url"].lower().endswith(".pdf") and not head.startswith(b"%PDF"):
        return f"DOCUMENTO YA NO ES PDF\t{document['url']}"
    return None


def revisar(feed: str, documentos: str | None = None) -> list[str]:
    items = yaml.safe_load(pathlib.Path(feed).read_text(encoding="utf-8"))["items"]
    piezas = leer_todas([item["url"] for item in items])
    findings = [revisar_item(item, pieza) for item, pieza in zip(items, piezas)]
    if documentos:
        findings += [revisar_documento(document) for document in yaml.safe_load(pathlib.Path(documentos).read_text(encoding="utf-8"))["documentos"]]
    return [finding for finding in findings if finding]


if __name__ == "__main__":
    found = revisar(*sys.argv[1:3])
    print("\n".join(found) if found else "todos los enlaces responden y los titulares coinciden")
    sys.exit(1 if found else 0)
