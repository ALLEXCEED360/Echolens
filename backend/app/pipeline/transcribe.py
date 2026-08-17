"""Speech recognition via faster-whisper.

Timestamps are the product here, not the text. Every segment carries its
position in the video, which is what makes the transcript navigable and what
every later retrieval stage is anchored to.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

# Must precede any faster_whisper import: it puts the CUDA DLLs on PATH.
from app.gpu import resolve_device

logger = logging.getLogger(__name__)

_model = None
_model_key: tuple[str, str, str] | None = None
_model_lock = Lock()


@dataclass(frozen=True)
class Segment:
    start_s: float
    end_s: float
    text: str
    # Mean token log-probability: a usable confidence proxy for flagging
    # unreliable stretches later.
    avg_logprob: float
    no_speech_prob: float
    compression_ratio: float


@dataclass(frozen=True)
class Transcript:
    segments: list[Segment]
    language: str
    language_probability: float
    duration_s: float
    model: str


def _get_model(name: str, device_preference: str):
    """Load and cache the model.

    large-v3 takes ~45s to load and ~3 GB of VRAM, so this is a process-wide
    singleton. The lock matters because stages run in worker threads.
    """
    global _model, _model_key

    device, compute_type = resolve_device(device_preference)
    key = (name, device, compute_type)

    with _model_lock:
        if _model_key == key:
            return _model, device, compute_type

        from faster_whisper import WhisperModel

        logger.info("Loading Whisper %s on %s (%s)", name, device, compute_type)
        _model = WhisperModel(name, device=device, compute_type=compute_type)
        _model_key = key
        return _model, device, compute_type


def transcribe_sync(
    audio_path: Path,
    *,
    model_name: str = "large-v3",
    device: str = "auto",
    language: str | None = None,
    beam_size: int = 5,
    progress: Callable[[float], None] | None = None,
) -> Transcript:
    model, resolved_device, compute_type = _get_model(model_name, device)

    # vad_filter suppresses Whisper's well-documented habit of inventing text
    # over silence — "Thank you for watching" and similar training artefacts.
    raw_segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=beam_size,
        vad_filter=True,
        word_timestamps=False,
    )

    segments: list[Segment] = []
    # faster-whisper yields lazily; work happens as we iterate, which is what
    # lets progress track real decoding rather than jumping 0 → 100.
    iterator: Iterator = raw_segments
    for seg in iterator:
        segments.append(
            Segment(
                start_s=round(seg.start, 3),
                end_s=round(seg.end, 3),
                text=seg.text.strip(),
                avg_logprob=seg.avg_logprob,
                no_speech_prob=seg.no_speech_prob,
                compression_ratio=seg.compression_ratio,
            )
        )
        if progress and info.duration:
            progress(min(seg.end / info.duration, 1.0))

    logger.info(
        "Transcribed %.1fs into %d segments (%s, %s/%s)",
        info.duration,
        len(segments),
        info.language,
        resolved_device,
        compute_type,
    )

    return Transcript(
        segments=segments,
        language=info.language,
        language_probability=round(info.language_probability, 4),
        duration_s=round(info.duration, 3),
        model=f"faster-whisper/{model_name}",
    )


async def transcribe(
    audio_path: Path,
    *,
    model_name: str = "large-v3",
    device: str = "auto",
    language: str | None = None,
    beam_size: int = 5,
    progress: Callable[[float], None] | None = None,
) -> Transcript:
    """Transcribe off the event loop — inference blocks."""
    return await asyncio.to_thread(
        transcribe_sync,
        audio_path,
        model_name=model_name,
        device=device,
        language=language,
        beam_size=beam_size,
        progress=progress,
    )
