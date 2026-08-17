"""Cross-encoder reranking.

Bi-encoder retrieval embeds the query and the document separately, so it can
only ever compare summaries of each. A cross-encoder reads the pair together and
scores the actual match — far more accurate, far too slow to run over a corpus.
The standard arrangement is therefore: retrieve wide and cheap, rerank narrow
and expensive.

Measured on this corpus, over 30 real candidates: **13–24 ms warm, ~95 MB VRAM**,
and the top result changed for 5 of 5 probe queries — promoting, for instance,
"It's like a blueprint that you can use to make new enemies" over "what we need
to do now is to use this prefab" for the query "what is a prefab used for".

**The scores are calibrated enough to mean something.** Roughly: above ~2 is a
genuine match, below ~0 means nothing retrieved actually answers the query. That
gives the "I could not find this in the video" contract from
docs/03-retrieval.md a real signal to fire on, rather than a similarity
threshold that always returns *something*.
"""

from __future__ import annotations

import asyncio
import logging
from threading import Lock

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Below this, the reranker is saying nothing in the candidate set answers the
# query.
#
# **Measured in Phase 9, not guessed.** The original 0.0 came from two observed
# scores — relevant hits around 5-7, one off-corpus query at -0.30 — and it sat
# inside the answerable distribution. Over the 46-question benchmark:
#
#     answerable   min -1.75, p10 1.30, median 5.49
#     off-corpus   max -5.01, min -10.32
#
# A floor of 0.0 refused two questions the system had *already retrieved
# correctly* — "how do I stop my game running at different speeds on slower
# computers" retrieved the frame-rate independence explanation and then declined
# to use it. False refusal is the worse failure here: a wrong answer is visible
# and arguable, while a refusal on retrievable content looks like the corpus
# simply does not cover it.
#
# -3.0 sits in the gap, with 2.0 of margin below the worst true positive and
# 2.0 above the best true negative. **On 46 questions and 6 negatives** — a
# larger corpus may well close that gap, and the number to watch is the
# false-refusal rate in docs/08-evaluation.md, not this constant.
RELEVANCE_FLOOR = -3.0

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
    global _model, _model_key

    device = _resolve_device(device_preference)
    key = (name, device)

    with _lock:
        if _model_key == key:
            return _model

        from sentence_transformers import CrossEncoder

        logger.info("Loading reranker %s on %s", name, device)
        _model = CrossEncoder(name, device=device, max_length=512)
        _model_key = key
        return _model


def unload() -> None:
    """Release the model and its VRAM."""
    global _model, _model_key
    with _lock:
        if _model is None:
            return
        _model, _model_key = None, None
    try:
        import torch

        torch.cuda.empty_cache()
    except ImportError:
        pass


def rerank_sync(
    query: str,
    documents: list[str],
    *,
    model_name: str = DEFAULT_MODEL,
    device: str = "auto",
    batch_size: int = 32,
) -> list[float]:
    """Relevance score per document. Higher is better; may be negative."""
    if not documents:
        return []

    model = _get_model(model_name, device)
    scores = model.predict(
        [(query, doc) for doc in documents],
        batch_size=batch_size,
        show_progress_bar=False,
    )
    return [float(s) for s in scores]


async def rerank(
    query: str,
    documents: list[str],
    *,
    model_name: str = DEFAULT_MODEL,
    device: str = "auto",
    batch_size: int = 32,
) -> list[float]:
    return await asyncio.to_thread(
        rerank_sync,
        query,
        documents,
        model_name=model_name,
        device=device,
        batch_size=batch_size,
    )
