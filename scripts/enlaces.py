"""Periodic link check of the published data (spec 077).

It is an aid for the person who runs the vigía cycle, not a gate: it reads the
page as served (no JavaScript, no stylesheets), so text an outlet hides with a
CSS class, or changes only for browsers, is outside its reach. What it reports
is reviewed by hand; what it does not report is not thereby verified.

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

import yaml

from titulares import UA, Pieza, leer_todas

def _sin_fragmento(url: str) -> str:
    """Only the fragment is noise: path and query can each select another piece."""
    return url.split("#")[0]


def revisar_item(item: dict, pieza: Pieza) -> str | None:
    if pieza.status != "200":
        return f"ENLACE {pieza.status}\t{item['url']}"
    if _sin_fragmento(pieza.final_url) != _sin_fragmento(item["url"]):
        return f"ENLACE REDIRIGIDO a {pieza.final_url}\t{item['url']}"
    if pieza.otros_h1:
        return f"VARIOS TITULARES\t{item['url']}\n\tpublicado: {item['titulo']}\n\ten la web: {pieza.h1} / {' / '.join(pieza.otros_h1)}"
    if pieza.h1 != item["titulo"]:
        return f"TITULAR CAMBIADO\t{item['url']}\n\tpublicado: {item['titulo']}\n\ten la web: {pieza.h1 or '(sin titular legible)'}"
    if not pieza.published:
        return f"FECHA NO LEGIBLE\t{item['url']}"
    if pieza.published and pieza.published != item["iso"]:
        return f"FECHA DISTINTA\t{item['url']}\n\tpublicada: {item['iso']}\n\ten la web: {pieza.published}"
    return None


def _cola(url: str) -> bytes:
    """Last bytes of the resource: a complete PDF ends with its %%EOF marker."""
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=-2048"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return response.read()[-2048:]
    except Exception:
        return b""


def revisar_documento(document: dict) -> str | None:
    """The link must still serve a PDF from the issuing body's own host."""
    request = urllib.request.Request(document["url"], headers={"User-Agent": UA, "Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            final = response.geturl()
            head = response.read(1024)
            status = response.status
    except urllib.error.HTTPError as error:
        return f"DOCUMENTO {error.code}\t{document['url']}"
    except Exception as error:
        return f"DOCUMENTO error:{type(error).__name__}\t{document['url']}"
    if status not in (200, 206):
        return f"DOCUMENTO {status}\t{document['url']}"
    if final != document["url"]:
        return f"DOCUMENTO REDIRIGIDO a {final}\t{document['url']}"
    if not head.startswith(b"%PDF") or b"%%EOF" not in _cola(document["url"]):
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
