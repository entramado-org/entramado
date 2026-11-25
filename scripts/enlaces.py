"""Periodic link check of the published data (spec 077).

Lists every item whose link no longer answers or whose headline on the outlet's
site is no longer the one we publish, and every official document whose link no
longer answers. It reports; it never edits the data. Run it from the Mac inside
the vigía cycle: outlets answer 403 to cloud runners.

    python scripts/enlaces.py datos/feed.yaml [datos/documentos.yaml]

Exit code 1 when there is anything to review.
"""
from __future__ import annotations

import concurrent.futures
import sys
import urllib.error
import urllib.request

import yaml

from titulares import UA, headline


def _status(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-2000"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return str(response.status)
    except urllib.error.HTTPError as error:
        return str(error.code)
    except Exception as error:
        return f"error:{type(error).__name__}"


def revisar(feed: str, documentos: str | None = None) -> list[str]:
    items = yaml.safe_load(open(feed, encoding="utf-8"))["items"]
    findings: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda item: headline(item["url"]), items))
        for item, (status, og_title, h1, title) in zip(items, results):
            if status != "200":
                findings.append(f"ENLACE {status}\t{item['url']}")
            elif item["titulo"] not in (h1, og_title) and item["titulo"] not in title:
                findings.append(f"TITULAR CAMBIADO\t{item['url']}\n\tpublicado: {item['titulo']}\n\ten la web: {h1 or og_title or title}")
        if documentos:
            documents = yaml.safe_load(open(documentos, encoding="utf-8"))["documentos"]
            for document, status in zip(documents, pool.map(lambda document: _status(document["url"]), documents)):
                if status not in ("200", "206"):
                    findings.append(f"DOCUMENTO {status}\t{document['url']}")
    return findings


if __name__ == "__main__":
    found = revisar(*sys.argv[1:3])
    print("\n".join(found) if found else "todos los enlaces responden y los titulares coinciden")
    sys.exit(1 if found else 0)
