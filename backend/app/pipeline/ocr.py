"""Text recognition on keyframes.

RapidOCR (ONNX) rather than PaddleOCR or EasyOCR: it is a 12 MB pip install with
no paddle runtime and no torch, and it runs on CPU — which matters because the
GPU is already holding the embedding model, and Whisper wants it back for the
next job.

**On quality.** OCR value is bounded by source resolution, not by the engine.
Measured on the 6-hour test video, which is 640x360: mean confidence ~0.70 with
visible errors ("SoriteRenderer" for "SpriteRenderer"). Upscaling does not help
and measurably hurts — 2x dropped mean confidence to 0.64 and eliminated every
detection above 0.80, because interpolation invents edges the recogniser then
misreads. Frames are therefore OCR'd at native resolution.

Low-confidence results are filtered rather than stored: a wrong string in the
search index is worse than a missing one, since it can only ever produce a bad
match.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from app.extras import missing

logger = logging.getLogger(__name__)

# Below this the string is more likely wrong than right.
DEFAULT_MIN_CONFIDENCE = 0.60
# Single characters and stray marks carry no retrieval signal.
DEFAULT_MIN_LENGTH = 3

_engine = None
_engine_lock = Lock()


@dataclass(frozen=True)
class TextBlock:
    text: str
    confidence: float
    # [x1, y1, x2, y2] in pixels — kept for future highlight overlays.
    bbox: tuple[int, int, int, int]


@dataclass
class FrameText:
    path: Path
    blocks: list[TextBlock]

    @property
    def text(self) -> str:
        """Blocks joined top-to-bottom, which is reading order for slides and UI."""
        ordered = sorted(self.blocks, key=lambda b: (b.bbox[1], b.bbox[0]))
        return "\n".join(b.text for b in ordered)

    @property
    def mean_confidence(self) -> float:
        if not self.blocks:
            return 0.0
        return sum(b.confidence for b in self.blocks) / len(self.blocks)


# Code-ish punctuation. Its presence is what separates a legitimate identifier
# from prose whose spaces were lost.
_CODE_MARKS = set("()[]{}.;:=_<>/\\-+*&|!#@$\"'")
_RUN_ON_LENGTH = 24


def is_indexable(text: str) -> bool:
    """Should this block reach the search index?

    Confidence is a poor filter here and the data says so: the *highest*
    confidence blocks on a 360p source include
    "Eueryonehastherighttofreedomofthought,conscienceandreligion," — real text
    whose word boundaries the recogniser could not resolve. It is read
    correctly at the glyph level and is still useless to an embedding model,
    because no tokenizer recovers words from it.

    Length alone cannot decide, because "StopCoroutine(damageFeedbackCoroutine)"
    is just as space-free and genuinely worth indexing. Structure separates
    them: identifiers carry punctuation, run-together prose does not.

    Blocks are still *stored* either way — this gates indexing, not the audit
    trail.
    """
    stripped = text.strip()
    if len(stripped) < DEFAULT_MIN_LENGTH:
        return False
    if " " in stripped or len(stripped) <= _RUN_ON_LENGTH:
        return True
    return any(c in _CODE_MARKS for c in stripped)


def _get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ModuleNotFoundError as exc:
                raise missing("rapidocr-onnxruntime", extra="ocr") from exc

            logger.info("Loading RapidOCR (CPU)")
            _engine = RapidOCR()
        return _engine


def _to_bbox(points: object) -> tuple[int, int, int, int]:
    """Collapse a quadrilateral to an axis-aligned box."""
    try:
        xs = [float(p[0]) for p in points]  # type: ignore[index]
        ys = [float(p[1]) for p in points]  # type: ignore[index]
        return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
    except Exception:  # noqa: BLE001
        return (0, 0, 0, 0)


def read_frame_sync(
    path: Path,
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_length: int = DEFAULT_MIN_LENGTH,
) -> FrameText:
    engine = _get_engine()
    try:
        result, _ = engine(str(path))
    except Exception:  # noqa: BLE001 — one unreadable frame must not fail the stage
        logger.debug("OCR failed on %s", path.name, exc_info=True)
        return FrameText(path=path, blocks=[])

    blocks: list[TextBlock] = []
    for row in result or []:
        try:
            points, text, confidence = row[0], row[1], row[2]
            # RapidOCR returns confidence as a string in some versions.
            score = float(confidence)
        except (ValueError, TypeError, IndexError):
            continue

        cleaned = str(text).strip()
        if score < min_confidence or len(cleaned) < min_length:
            continue
        blocks.append(TextBlock(text=cleaned, confidence=score, bbox=_to_bbox(points)))

    return FrameText(path=path, blocks=blocks)


def read_frames_sync(
    paths: list[Path],
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_length: int = DEFAULT_MIN_LENGTH,
    workers: int = 4,
    progress: Callable[[float], None] | None = None,
) -> list[FrameText]:
    """OCR many frames in parallel.

    ONNX Runtime releases the GIL during inference, so threads genuinely
    parallelise here. At ~1.5 s per frame single-threaded, 1,100 keyframes would
    take ~27 minutes; four workers brings that down to single digits.
    """
    if not paths:
        return []

    results: list[FrameText | None] = [None] * len(paths)
    done = 0
    lock = Lock()

    def work(index: int) -> None:
        nonlocal done
        results[index] = read_frame_sync(
            paths[index], min_confidence=min_confidence, min_length=min_length
        )
        with lock:
            done += 1
            if progress:
                progress(done / len(paths))

    # Warm the engine once before fanning out, so workers do not all race to
    # construct it and load the same ONNX graphs concurrently.
    _get_engine()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, range(len(paths))))

    return [r for r in results if r is not None]


async def read_frames(paths: list[Path], **kwargs) -> list[FrameText]:
    return await asyncio.to_thread(read_frames_sync, paths, **kwargs)
