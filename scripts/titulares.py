"""Reads the headline each linked piece shows on its own site (spec 077).

For every item of the feed, fetch its URL and take the piece's own headline:
`og:title` first, then the first `<h1>`, then `<title>`. Writes a TSV for review;
it never rewrites the feed.

    python scripts/titulares.py pipeline/data/ultima_hora/feed.yaml out.tsv
"""
from __future__ import annotations

import concurrent.futures
import html
import re
import subprocess
import sys
import urllib.error
import urllib.request

import yaml

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
UA_CURL = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
OG = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\'](.*?)["\']', re.I | re.S)
OG_REV = re.compile(r'<meta[^>]+content=["\'](.*?)["\'][^>]*property=["\']og:title["\']', re.I | re.S)
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
TAG = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return " ".join(html.unescape(TAG.sub("", text)).split())


def _fetch(url: str) -> tuple[str, str]:
    """Returns (status, page). Some outlets refuse Python's TLS stack or its
    client signature and answer `curl`, so a failed read is retried with it."""
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return str(response.status), response.read(1_500_000).decode(charset, errors="replace")
    except urllib.error.HTTPError as error:
        status = str(error.code)
    except Exception as error:  # timeouts, TLS, DNS: recorded, never fatal
        status = f"error:{type(error).__name__}"
    try:
        done = subprocess.run(
            ["curl", "-s", "-L", "-m", "30", "-A", UA_CURL, "-H", "Accept-Language: es-ES", "-w", "\n%{http_code}", url],
            capture_output=True, timeout=45, check=False,
        )
    except Exception:
        return status, ""
    body, _, code = done.stdout.decode("utf-8", errors="replace").rpartition("\n")
    return (code, body) if code == "200" else (status, "")


def headline(url: str) -> tuple[str, str, str, str]:
    """Returns (status, og:title, h1, title) for one URL."""
    status, page = _fetch(url)
    og = OG.search(page) or OG_REV.search(page)
    h1 = H1.search(page)
    title = TITLE.search(page)
    return status, _clean(og.group(1)) if og else "", _clean(h1.group(1)) if h1 else "", _clean(title.group(1)) if title else ""


def main(feed_path: str, out_path: str) -> None:
    items = yaml.safe_load(open(feed_path, encoding="utf-8"))["items"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda item: headline(item["url"]), items))
    with open(out_path, "w", encoding="utf-8") as out:
        out.write("n\tiso\tstatus\toutlet\turl\tog_title\th1\ttitle\n")
        for number, (item, result) in enumerate(zip(items, results)):
            fields = [str(number), str(item["iso"]), result[0], item["outlet"], item["url"], *result[1:]]
            out.write("\t".join(field.replace("\t", " ") for field in fields) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
