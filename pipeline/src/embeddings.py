"""BGE-m3 embeddings via sentence-transformers, accelerated on Apple MPS."""
from __future__ import annotations

from functools import lru_cache

from . import config


@lru_cache(maxsize=1)
def _model():
    import torch
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    return SentenceTransformer(config.EMBEDDING_MODEL, device=device)


def embed_texts(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Return normalized embeddings (cosine == dot product)."""
    if not texts:
        return []
    model = _model()
    vecs = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > 200,
        convert_to_numpy=True,
    )
    return [v.tolist() for v in vecs]


def embed_one(text: str) -> list[float]:
    return embed_texts([text])[0]
