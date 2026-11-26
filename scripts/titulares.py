"""Reads what each linked piece shows on its own site (spec 077).

For one URL: the visible headline (first `<h1>` with text), the `og:title`, the
`<title>` and the publication date the page declares. `enlaces.py` and the
migration both build on `leer()`. Nothing here rewrites the data.

    python scripts/titulares.py datos/feed.yaml out.tsv
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import html.parser
import re
import subprocess
import pathlib
import sys
import urllib.error
import urllib.request

import yaml

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
UA_CURL = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
# Declared publication date, most specific first; a bare <time> is the last resort and never a dateModified.
PUBLISHED = ("article:published_time", "og:article:published_time", "datePublished", "DC.date.issued", "publish-date", "date")
ISO_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
JSON_LD_DATE = re.compile(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})')


@dataclasses.dataclass
class Pieza:
    status: str = ""
    final_url: str = ""
    content_type: str = ""
    h1: str = ""
    og_title: str = ""
    title: str = ""
    published: str = ""
    otros_h1: list = dataclasses.field(default_factory=list)


class _Page(html.parser.HTMLParser):
    """Collects every `<h1>`, the `<title>` and the metadata we need."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.h1s: list[str] = []
        self.title = ""
        self.meta: dict[str, str] = {}
        self.article_h1s: list[str] = []
        self._open: str | None = None
        self._buffer: list[str] = []
        self._hidden: list[str] = []  # open elements the reader does not see (hidden, <template>)
        self._articles = 0

    def handle_starttag(self, tag, attrs):
        names = {name for name, _ in attrs}
        if self._hidden:
            # Only the element that opened the hidden region closes it: optional end tags
            # (<li>, <p>…) inside it must not leave the region open for the rest of the page.
            if tag == self._hidden[0]:
                self._hidden.append(tag)
            return
        if tag not in VOID and (tag == "template" or "hidden" in names or dict(attrs).get("aria-hidden") == "true"):
            self._hidden.append(tag)
            return
        if tag == "article":
            self._articles += 1
        if tag == "meta":
            fields = {name: value or "" for name, value in attrs}
            key = fields.get("property") or fields.get("name") or fields.get("itemprop")
            if key and "content" in fields:
                self.meta.setdefault(key, fields["content"])
        elif tag == "time" and "time" not in self.meta and "modif" not in (dict(attrs).get("itemprop") or "").lower():
            self.meta["time"] = dict(attrs).get("datetime") or ""
        elif tag in ("h1", "title") and self._open is None:
            self._open, self._buffer = tag, []

    def handle_endtag(self, tag):
        if self._hidden:
            if tag == self._hidden[0]:
                self._hidden.pop()
            return
        if tag == "article" and self._articles:
            self._articles -= 1
        if tag == self._open:
            text = " ".join("".join(self._buffer).split())
            if tag == "h1" and text:
                self.h1s.append(text)
                if self._articles:
                    self.article_h1s.append(text)
            elif tag == "title" and not self.title:
                self.title = text
            self._open = None

    def handle_data(self, data):
        if self._open is not None and not self._hidden:
            self._buffer.append(data)


def _fetch(url: str) -> tuple[str, str, str, str]:
    """Returns (status, final url, content type, page). Some outlets refuse
    Python's client and answer `curl`, so a failed read is retried with it."""
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read(1_500_000)
            return str(response.status), response.geturl(), response.headers.get_content_type(), body.decode(charset, errors="replace")
    except urllib.error.HTTPError as error:
        status = str(error.code)
    except Exception as error:  # timeouts, TLS, DNS: recorded, never fatal
        status = f"error:{type(error).__name__}"
    try:
        done = subprocess.run(
            ["curl", "-s", "-L", "-m", "30", "-A", UA_CURL, "-H", "Accept-Language: es-ES",
             "-w", "\n%{http_code}\t%{url_effective}\t%{content_type}", url],
            capture_output=True, timeout=45, check=False,
        )
    except Exception:
        return status, url, "", ""
    body, _, trailer = done.stdout.decode("utf-8", errors="replace").rpartition("\n")
    code, _, rest = trailer.partition("\t")
    final_url, _, content_type = rest.partition("\t")
    if code != "200":
        return status, url, "", ""
    return code, final_url, content_type.split(";")[0].strip(), body


def leer(url: str) -> Pieza:
    status, final_url, content_type, page = _fetch(url)
    pieza = Pieza(status=status, final_url=final_url, content_type=content_type)
    if not page or "html" not in content_type:
        return pieza
    parser = _Page()
    try:
        parser.feed(page)
    except Exception:  # malformed markup: keep what was read so far
        pass
    # The piece's headline is the <h1> of its <article>; outside one, the first visible <h1>.
    candidatos = parser.article_h1s or parser.h1s
    pieza.h1 = candidatos[0] if candidatos else ""
    pieza.otros_h1 = sorted(set(candidatos[1:]) - {pieza.h1})
    pieza.og_title = " ".join(parser.meta.get("og:title", "").split())
    pieza.title = parser.title
    for key in PUBLISHED:
        found = ISO_DATE.search(parser.meta.get(key, ""))
        if found:
            pieza.published = found.group(1)
            break
    else:
        found = JSON_LD_DATE.search(page) or ISO_DATE.search(parser.meta.get("time", ""))
        pieza.published = found.group(1) if found else ""
    return pieza


def leer_todas(urls: list[str]) -> list[Pieza]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(leer, urls))


def main(feed_path: str, out_path: str) -> None:
    items = yaml.safe_load(pathlib.Path(feed_path).read_text(encoding="utf-8"))["items"]
    piezas = leer_todas([item["url"] for item in items])
    with open(out_path, "w", encoding="utf-8") as out:
        out.write("n\tiso\tstatus\tpublished\tmedio\turl\th1\tog_title\ttitle\n")
        for number, (item, pieza) in enumerate(zip(items, piezas)):
            fields = [str(number), str(item["iso"]), pieza.status, pieza.published, item["medio"],
                      item["url"], pieza.h1, pieza.og_title, pieza.title]
            out.write("\t".join(field.replace("\t", " ") for field in fields) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
