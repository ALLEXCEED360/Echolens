"""Audio extraction.

Produces a 16 kHz mono WAV — Whisper's native input rate. Resampling once here
means Whisper does not redo it internally, and diarization (Phase 2) plus every
later audio stage reads the same small file instead of re-decoding a multi-GB
video each time.

Uses PyAV rather than the ffmpeg CLI, which is not installed on this machine.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import av
from av.audio.resampler import AudioResampler

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
LAYOUT = "mono"
FORMAT = "s16"


class AudioExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtractedAudio:
    path: Path
    duration_s: float
    sample_rate: int


def extract_audio_sync(
    source: Path,
    destination: Path,
    *,
    progress: Callable[[float], None] | None = None,
) -> ExtractedAudio:
    """Decode the first audio stream to 16 kHz mono WAV."""
    if not source.exists():
        raise AudioExtractionError(f"Source not found: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        container = av.open(str(source))
    except (av.FFmpegError, OSError, ValueError) as exc:
        raise AudioExtractionError(f"Could not open {source.name}: {exc}") from exc

    with container:
        if not container.streams.audio:
            raise AudioExtractionError("No audio stream")

        stream = container.streams.audio[0]
        # Decoding one stream on several threads; the rest of the file is skipped.
        stream.thread_type = "AUTO"

        total_s = float(container.duration / av.time_base) if container.duration else None
        resampler = AudioResampler(format=FORMAT, layout=LAYOUT, rate=SAMPLE_RATE)

        output = av.open(str(destination), mode="w")
        # layout and format must be set explicitly: the encoder otherwise
        # defaults to stereo and silently re-widens the mono frames the
        # resampler just produced.
        out_stream = output.add_stream(
            "pcm_s16le", rate=SAMPLE_RATE, layout=LAYOUT, format=FORMAT
        )

        written_samples = 0
        try:
            for frame in container.decode(stream):
                # AudioResampler.resample returns a list: one input frame can
                # produce zero or several output frames depending on buffering.
                for resampled in resampler.resample(frame):
                    written_samples += resampled.samples
                    for packet in out_stream.encode(resampled):
                        output.mux(packet)

                if progress and total_s and frame.time is not None:
                    progress(min(frame.time / total_s, 1.0))

            # Flush the resampler, then the encoder. Skipping either truncates
            # the tail of the audio.
            for resampled in resampler.resample(None):
                written_samples += resampled.samples
                for packet in out_stream.encode(resampled):
                    output.mux(packet)
            for packet in out_stream.encode():
                output.mux(packet)
        finally:
            output.close()

    if written_samples == 0:
        raise AudioExtractionError("Audio stream decoded to zero samples")

    duration = written_samples / SAMPLE_RATE
    logger.info("Extracted %.1fs of audio -> %s", duration, destination.name)
    return ExtractedAudio(path=destination, duration_s=duration, sample_rate=SAMPLE_RATE)


async def extract_audio(
    source: Path,
    destination: Path,
    *,
    progress: Callable[[float], None] | None = None,
) -> ExtractedAudio:
    """Extract off the event loop — decoding is blocking and CPU-bound."""
    return await asyncio.to_thread(extract_audio_sync, source, destination, progress=progress)
