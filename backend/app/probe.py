"""Container inspection via PyAV.

PyAV binds libav directly and ships its own ffmpeg libraries in the wheel, so
probing works with no `ffprobe.exe` on PATH — which matters, because this dev
machine has no ffmpeg and no package manager to install one.

Probing fails loudly. A video we cannot probe is a video we cannot process, and
finding out at upload time beats finding out three stages in.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import av

logger = logging.getLogger(__name__)


class ProbeError(RuntimeError):
    """The file is not a decodable media container."""


@dataclass
class ProbeResult:
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    has_audio: bool = False
    audio_channels: int | None = None
    audio_sample_rate: int | None = None
    format_name: str | None = None
    warnings: list[str] = field(default_factory=list)


def _fps(stream: av.video.stream.VideoStream) -> float | None:
    """Frame rate, preferring the container's average over the guessed base rate.

    `base_rate` is the nominal rate declared in the header and lies about
    variable-frame-rate recordings — screen captures and phone video especially.
    `average_rate` is computed from actual packet timestamps.
    """
    for attr in ("average_rate", "guessed_rate", "base_rate"):
        rate = getattr(stream, attr, None)
        if isinstance(rate, Fraction) and rate.denominator and rate > 0:
            return round(float(rate), 4)
    return None


def _channel_count(stream: av.audio.stream.AudioStream) -> int | None:
    """Channel count across PyAV versions.

    `AudioStream.channels` was deprecated in PyAV 13 in favour of the layout
    object, so try the modern path first and fall back.
    """
    layout = getattr(stream, "layout", None) or getattr(
        getattr(stream, "codec_context", None), "layout", None
    )
    if layout is not None and (n := getattr(layout, "nb_channels", None)):
        return int(n)
    if (n := getattr(stream, "channels", None)) is not None:
        return int(n)
    return None


def probe_sync(path: Path) -> ProbeResult:
    """Blocking probe. Call via `probe()` from async code."""
    if not path.exists():
        raise ProbeError(f"File not found: {path}")

    try:
        # av.FFmpegError is the base of every libav error. (av.AVError was the
        # old name and no longer exists — PyAV renamed it at v10.)
        container = av.open(str(path))
    except (av.FFmpegError, OSError, ValueError) as exc:
        raise ProbeError(f"Could not open media container: {exc}") from exc

    with container:
        result = ProbeResult(format_name=getattr(container.format, "name", None))

        # container.duration is in av.time_base units (microseconds).
        if container.duration is not None:
            result.duration_s = round(container.duration / av.time_base, 3)

        video_streams = container.streams.video
        audio_streams = container.streams.audio

        if not video_streams and not audio_streams:
            raise ProbeError("Container has no video or audio streams")

        if video_streams:
            v = video_streams[0]
            result.width = v.width or None
            result.height = v.height or None
            result.fps = _fps(v)
            result.video_codec = getattr(v.codec_context, "name", None)

            # Stream duration is more reliable than container duration for some
            # formats (notably fragmented MP4 and raw streams).
            if result.duration_s is None and v.duration and v.time_base:
                result.duration_s = round(float(v.duration * v.time_base), 3)
        else:
            result.warnings.append("No video stream; audio-only file")

        if audio_streams:
            a = audio_streams[0]
            result.has_audio = True
            result.audio_codec = getattr(a.codec_context, "name", None)
            result.audio_channels = _channel_count(a)
            result.audio_sample_rate = a.sample_rate or None
            if len(audio_streams) > 1:
                result.warnings.append(
                    f"{len(audio_streams)} audio streams; only the first will be transcribed"
                )
        else:
            # Gates the entire speech branch of the pipeline.
            result.warnings.append("No audio stream; speech stages will be skipped")

        if result.duration_s is None:
            result.warnings.append("Duration unavailable; seeking may be unreliable")

        return result


async def probe(path: Path) -> ProbeResult:
    """Probe off the event loop — libav I/O is blocking."""
    return await asyncio.to_thread(probe_sync, path)
