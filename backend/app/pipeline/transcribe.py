"""Speech recognition via faster-whisper.

Timestamps are the product here, not the text. Every segment carries its
position in the video, which is what makes the transcript navigable and what
every later retrieval stage is anchored to.
"""

from __future__ import annotations

import asyncio
import logging
import re
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
    # Verbatim repeats of the preceding line, discarded as decoder loops.
    # Reported rather than silently swallowed: a filter nobody can see is a
    # filter nobody can question.
    dropped_segments: int = 0


def _norm(text: str) -> str:
    """Compare lines ignoring case, punctuation and spacing."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


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
    vad_filter: bool = True,
    vad_threshold: float = 0.5,
    vocabulary: str | None = None,
    progress: Callable[[float], None] | None = None,
) -> Transcript:
    model, resolved_device, compute_type = _get_model(model_name, device)

    # vad_filter suppresses Whisper's well-documented habit of inventing text
    # over silence — "Thank you for watching" and similar training artefacts.
    #
    # It is not free, though: VAD judges what is speech *before* the model
    # hears it, so on audio where dialogue is buried under effects it throws
    # away real lines. Measured on a 65 s game clip, it kept 8.3 s where
    # disabling it recovered 39 s — every extra line confirmed by the game's
    # own burned-in subtitles. Hence a setting rather than a constant.
    options: dict = {"vad_filter": vad_filter}
    if vad_filter:
        options["vad_parameters"] = {"threshold": vad_threshold}

    # Domain vocabulary, biasing the decoder toward names it would otherwise
    # guess at. Whisper rendered a character called Harkov as "Raccoon" until
    # given the cast list.
    #
    # `hotwords` rather than `initial_prompt`: measured on the same clip, an
    # initial prompt combined with `condition_on_previous_text=False` caused the
    # model to transcribe *the prompt itself* — the tail of the transcript came
    # back as "Harkov, Vorshevsky, Modern Warfare". Hotwords bias without
    # entering the decoding context.
    if vocabulary:
        options["hotwords"] = vocabulary

    raw_segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=beam_size,
        word_timestamps=False,
        **options,
    )

    segments: list[Segment] = []
    dropped = 0
    # faster-whisper yields lazily; work happens as we iterate, which is what
    # lets progress track real decoding rather than jumping 0 → 100.
    iterator: Iterator = raw_segments
    for seg in iterator:
        text = seg.text.strip()

        # Drop a line that merely repeats the one before it.
        #
        # Near the end of noisy audio the decoder falls into repetition loops
        # and emits the same sentence two or three times. That is the artefact
        # worth removing, and identical consecutive lines are essentially never
        # genuine — a speaker repeating themselves verbatim, back to back, with
        # no gap, does not happen.
        #
        # **This replaces a `no_speech_prob` threshold, which was unsound.** That
        # score is relative to the decoding conditions, not an absolute measure:
        # validated against a clean corpus transcribed with VAD on it never
        # exceeded 0.125, but with VAD off genuine dialogue routinely scored
        # 0.44 — *higher* than the 0.30 hallucination it was meant to catch. The
        # threshold deleted five real lines and reduced one video to nothing.
        if segments and _norm(text) and _norm(text) == _norm(segments[-1].text):
            dropped += 1
            logger.debug("Dropped repeated segment at %.1fs: %r", seg.start, text[:60])
            continue

        segments.append(
            Segment(
                start_s=round(seg.start, 3),
                end_s=round(seg.end, 3),
                text=text,
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

    if dropped:
        logger.info("Dropped %d repeated segment(s)", dropped)

    return Transcript(
        segments=segments,
        dropped_segments=dropped,
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
    vad_filter: bool = True,
    vad_threshold: float = 0.5,
    vocabulary: str | None = None,
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
        vad_filter=vad_filter,
        vad_threshold=vad_threshold,
        vocabulary=vocabulary,
        progress=progress,
    )
