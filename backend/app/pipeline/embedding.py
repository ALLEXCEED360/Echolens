"""Text embeddings.

`bge-large-en-v1.5`, 1024 dimensions, on the GPU. ~1.3 GB of VRAM, which fits
alongside nothing else — the worker serialises GPU stages, so Whisper has already
released the card by the time this runs.

**Asymmetric prefixing.** BGE models are trained with an instruction prefix on
*queries* but not on *documents*. Embedding both sides identically measurably
degrades retrieval, so `embed_query` and `embed_documents` are separate calls
rather than one function with a flag that is easy to forget.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from threading import Lock

from app.extras import missing

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-large-en-v1.5"
DIMENSIONS = 1024

# Prescribed by the BGE authors for retrieval. Applied to queries only.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_model = None
_model_key: tuple[str, str] | None = None
_lock = Lock()


def _resolve_device(preference: str) -> str:
    if preference != "auto":
        return preference
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _get_model(name: str, device_preference: str):
    """Load and cache the model. Lock guards loading from worker threads."""
    global _model, _model_key

    device = _resolve_device(device_preference)
    key = (name, device)

    with _lock:
        if _model_key == key:
            return _model, device

        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError as exc:
            raise missing("sentence-transformers", extra="embeddings") from exc

        logger.info("Loading embedding model %s on %s", name, device)
        _model = SentenceTransformer(name, device=device)
        _model_key = key
        return _model, device


def unload() -> None:
    """Release the model and its VRAM.

    Called between GPU stages: 8 GB will not hold this and a vision model at
    once, and Python's GC alone will not return CUDA memory promptly.
    """
    global _model, _model_key
    with _lock:
        if _model is None:
            return
        _model = None
        _model_key = None
    try:
        import torch

        torch.cuda.empty_cache()
    except ImportError:
        pass
    logger.info("Embedding model unloaded")


def embed_documents_sync(
    texts: list[str],
    *,
    model_name: str = DEFAULT_MODEL,
    device: str = "auto",
    batch_size: int = 32,
    progress: Callable[[float], None] | None = None,
) -> list[list[float]]:
    """Embed passages for storage. No query prefix."""
    if not texts:
        return []

    model, _ = _get_model(model_name, device)
    out: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        vectors = model.encode(
            batch,
            batch_size=batch_size,
            # Cosine similarity on unit vectors is a dot product, and pgvector's
            # cosine operator is cheaper on normalised input.
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        out.extend(v.tolist() for v in vectors)
        if progress:
            progress(min((start + len(batch)) / len(texts), 1.0))

    return out


def embed_query_sync(
    query: str, *, model_name: str = DEFAULT_MODEL, device: str = "auto"
) -> list[float]:
    """Embed a search query. Applies the BGE retrieval prefix."""
    model, _ = _get_model(model_name, device)
    vector = model.encode(
        QUERY_PREFIX + query,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return vector.tolist()


async def embed_documents(
    texts: list[str],
    *,
    model_name: str = DEFAULT_MODEL,
    device: str = "auto",
    batch_size: int = 32,
    progress: Callable[[float], None] | None = None,
) -> list[list[float]]:
    return await asyncio.to_thread(
        embed_documents_sync,
        texts,
        model_name=model_name,
        device=device,
        batch_size=batch_size,
        progress=progress,
    )


async def embed_query(
    query: str, *, model_name: str = DEFAULT_MODEL, device: str = "auto"
) -> list[float]:
    return await asyncio.to_thread(
        embed_query_sync, query, model_name=model_name, device=device
    )
