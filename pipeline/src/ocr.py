"""Apple Vision OCR fallback for scanned/image PDF pages (macOS host only).

Renders a fitz page to a bitmap and recognizes Spanish text. Fast (~1s/page on
Apple Silicon) and accurate; no Tesseract install needed.
"""
from __future__ import annotations

import tempfile
from functools import lru_cache

from . import config


@lru_cache(maxsize=1)
def _available() -> bool:
    try:
        import ocrmac.ocrmac  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def ocr_page(page) -> str:
    """Render a PyMuPDF page to an image and OCR it. Returns '' if unavailable."""
    if not config.ENABLE_OCR or not _available():
        return ""
    from ocrmac import ocrmac

    pix = page.get_pixmap(dpi=config.OCR_DPI)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
        pix.save(tmp.name)
        try:
            results = ocrmac.OCR(tmp.name, language_preference=config.OCR_LANGS).recognize()
        except Exception:  # noqa: BLE001
            return ""
    # results: list of (text, confidence, bbox); Vision returns top-to-bottom.
    return "\n".join(r[0] for r in results).strip()
