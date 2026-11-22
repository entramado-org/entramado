"""Page-aware, token-bounded chunking with deterministic IDs for idempotency."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from functools import lru_cache

from . import config
from .pdf_extract import Document, Page


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    page_start: int
    page_end: int
    chunk_index: int
    char_offset: int
    text: str


@lru_cache(maxsize=1)
def _tokenizer():
    """Qwen3 tokenizer for accurate sizing; falls back to a char estimate."""
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(config.MLX_MODEL)
    except Exception:  # offline / load failure -> heuristic
        return None


def count_tokens(text: str) -> int:
    tok = _tokenizer()
    if tok is None:
        return max(1, len(text) // 4)
    return len(tok.encode(text, add_special_tokens=False))


# Spanish-legal separators, highest priority first.
_SEPARATORS = [
    r"\n(?=(?:PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|SÉPTIMO|OCTAVO|NOVENO|DÉCIMO)[\.\-\)])",
    r"\n(?=(?:FUNDAMENTO|HECHO|ANTECEDENTE|RAZONAMIENTO|ACUERDO|DISPONGO|FALLO)[S]?)",
    r"\n\n",
    r"(?<=[\.\?!])\s+",
    r"\s+",
]


def _hard_split(text: str, max_tokens: int) -> list[str]:
    approx = max_tokens * 4  # ~4 chars/token
    return [text[i : i + approx] for i in range(0, len(text), approx)]


def _split_recursive(text: str, max_tokens: int, sep_idx: int = 0) -> list[str]:
    """Split into atomic segments each <= max_tokens, descending separators."""
    if count_tokens(text) <= max_tokens:
        return [text] if text.strip() else []
    if sep_idx >= len(_SEPARATORS):
        return [p for p in _hard_split(text, max_tokens) if p.strip()]
    parts = [p for p in re.split(_SEPARATORS[sep_idx], text) if p and p.strip()]
    if len(parts) <= 1:
        return _split_recursive(text, max_tokens, sep_idx + 1)
    out: list[str] = []
    for p in parts:
        if count_tokens(p) > max_tokens:
            out.extend(_split_recursive(p, max_tokens, sep_idx + 1))
        else:
            out.append(p)
    return out


def _chunk_id(doc_id: str, page: int, offset: int) -> str:
    h = hashlib.sha1(f"{doc_id}:{page}:{offset}".encode()).hexdigest()
    return h[:16]


def _pack_page(page: Page, doc_title: str, start_index: int) -> list[Chunk]:
    """Greedily pack a page's segments into token-bounded chunks with overlap."""
    target = config.CHUNK_TARGET_TOKENS
    overlap = config.CHUNK_OVERLAP_TOKENS
    segments = _split_recursive(page.text, target)
    if not segments:
        return []

    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_tokens = 0
    char_offset = 0
    index = start_index

    def flush(carry: list[str]):
        nonlocal buf, buf_tokens, char_offset, index
        text = " ".join(buf).strip()
        if text:
            chunks.append(
                Chunk(
                    chunk_id=_chunk_id(page.doc_id, page.page_num, char_offset),
                    doc_id=page.doc_id,
                    doc_title=doc_title,
                    page_start=page.page_num,
                    page_end=page.page_num,
                    chunk_index=index,
                    char_offset=char_offset,
                    text=text,
                )
            )
            index += 1
            char_offset += len(text)
        buf = list(carry)
        buf_tokens = sum(count_tokens(s) for s in buf)

    for seg in segments:
        seg_tokens = count_tokens(seg)
        if buf_tokens + seg_tokens > target and buf:
            # carry tail segments for overlap
            carry: list[str] = []
            carry_tokens = 0
            for s in reversed(buf):
                t = count_tokens(s)
                if carry_tokens + t > overlap:
                    break
                carry.insert(0, s)
                carry_tokens += t
            flush(carry)
        buf.append(seg)
        buf_tokens += seg_tokens

    if buf:
        flush([])
    return chunks


def chunk_document(doc: Document) -> list[Chunk]:
    chunks: list[Chunk] = []
    index = 0
    for page in doc.pages:
        if page.needs_ocr or not page.text.strip():
            continue
        page_chunks = _pack_page(page, doc.title, index)
        chunks.extend(page_chunks)
        index += len(page_chunks)
    return chunks


def chunk_to_dict(c: Chunk) -> dict:
    return asdict(c)
