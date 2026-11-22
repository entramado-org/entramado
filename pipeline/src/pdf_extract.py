"""PDF text extraction with PyMuPDF + Spanish-legal normalization.

Produces per-page records with a `needs_ocr` flag for pages that yield almost no
text (scanned/image exhibits). OCR is out of scope — such pages are flagged and
skipped, never silently turned into empty chunks.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

import fitz  # PyMuPDF

from . import config, ocr


@dataclass
class Page:
    doc_id: str
    page_num: int          # 1-based
    text: str
    char_count: int
    needs_ocr: bool = False


@dataclass
class Document:
    doc_id: str
    title: str
    page_count: int
    pages: list[Page] = field(default_factory=list)
    low_text_pages: list[int] = field(default_factory=list)


_HYPHEN_BREAK = re.compile(r"(\w)-\n([a-záéíóúñü])")
_MULTI_NL = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
# Standalone line-number gutter (transcripts): a short integer alone on a line.
_LINE_NUMBER = re.compile(r"^\s*\d{1,3}\s*$")
# A short phrase repeated 3+ times (diagonal watermarks like "VICTORIA EST ...").
_REPEAT = re.compile(r"(.{6,45}?)(?:\s*\1){2,}", re.IGNORECASE)


def _strip_watermark_line(line: str) -> str | None:
    """Drop a line if a repeated phrase covers most of it (watermark)."""
    m = _REPEAT.search(line)
    if m and len(m.group(0)) >= 0.5 * len(line.strip()) and len(line.strip()) > 20:
        return None
    return line


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    # Join words split across line breaks ("cohe-\nrecho" -> "coherecho")
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _MULTI_SPACE.sub(" ", text)
    # Collapse runs of blank / whitespace-only lines into a single paragraph break
    lines = [ln.rstrip() for ln in text.splitlines()]
    out, blank = [], False
    for ln in lines:
        if ln.strip():
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def _detect_running_lines(pages_raw: list[str], min_pages: int) -> set[str]:
    """Lines (headers/footers) that repeat on most pages are boilerplate."""
    counter: Counter[str] = Counter()
    for raw in pages_raw:
        seen = set()
        for line in raw.splitlines():
            s = line.strip()
            if 3 <= len(s) <= 120 and s not in seen:
                counter[s] += 1
                seen.add(s)
    threshold = max(min_pages, int(0.6 * len(pages_raw)))
    return {line for line, c in counter.items() if c >= threshold}


def _strip_boilerplate(raw: str, running: set[str]) -> str:
    out = []
    for line in raw.splitlines():
        s = line.strip()
        if s in running:
            continue
        if _LINE_NUMBER.match(line):
            continue
        if _strip_watermark_line(line) is None:
            continue
        out.append(line)
    return "\n".join(out)


def extract_document(pdf_path) -> Document:
    doc_id = pdf_path.name
    title = pdf_path.stem
    fitz_doc = fitz.open(pdf_path)

    raw_pages = [p.get_text("text") for p in fitz_doc]
    running = _detect_running_lines(raw_pages, min_pages=4) if len(raw_pages) >= 6 else set()

    pages: list[Page] = []
    low_text: list[int] = []
    for i, page in enumerate(fitz_doc):
        page_num = i + 1
        cleaned = _strip_boilerplate(raw_pages[i], running)
        text = _normalize(cleaned)
        char_count = len(text)
        needs_ocr = False

        # Scanned/image page: text layer is empty -> OCR fallback.
        if char_count < config.MIN_PAGE_CHARS:
            ocr_text = _normalize(ocr.ocr_page(page))
            if len(ocr_text) > char_count:
                text = ocr_text
                char_count = len(text)
            if char_count < config.MIN_PAGE_CHARS:
                needs_ocr = True  # still nothing usable
                low_text.append(page_num)

        pages.append(
            Page(
                doc_id=doc_id,
                page_num=page_num,
                text=text,
                char_count=char_count,
                needs_ocr=needs_ocr,
            )
        )

    fitz_doc.close()
    return Document(
        doc_id=doc_id,
        title=title,
        page_count=len(pages),
        pages=pages,
        low_text_pages=low_text,
    )
