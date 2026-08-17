"""Visual sampling.

A 6-hour 30 fps video is 648,000 frames. Running OCR or a vision model over
those is not a tuning problem, it is arithmetic that does not work. This module
is the gate that makes every later visual stage affordable, which is why it runs
*before* OCR rather than after — see docs/02-pipeline.md.

**How it stays cheap.** The scan decodes keyframes only (`skip_frame='NONKEY'`),
so libav discards inter-frames without reconstructing them. Measured at **1602x
realtime** on the 6-hour test video: a full pass in ~13 seconds. Encoders also
insert I-frames at scene cuts, so the sampling grid is biased toward moments
something actually changed rather than being merely regular.

**What it emits.** Perceptual hashes drive a change signal; a keyframe is emitted
when accumulated change crosses a threshold, subject to a minimum and maximum
gap. Static content therefore costs almost nothing, while a screencast that
changes constantly is still bounded by `min_gap_s`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np

logger = logging.getLogger(__name__)

# dHash grid: 9x8 grayscale compared horizontally gives 64 bits.
_HASH_W, _HASH_H = 9, 8

DEFAULT_MIN_GAP_S = 4.0
DEFAULT_MAX_GAP_S = 90.0
# Hamming distance out of 64. ~10 tolerates cursor movement and video noise
# while still catching a slide change or a new editor panel.
DEFAULT_THRESHOLD = 10


class KeyframeError(RuntimeError):
    pass


@dataclass
class Keyframe:
    """One visually stable span, represented by a single frame."""

    start_s: float
    end_s: float
    time_s: float  # timestamp of the representative frame
    phash: int
    change: int  # hamming distance from the previously emitted keyframe

    @property
    def duration_s(self) -> float:
        return max(self.end_s - self.start_s, 0.0)


def dhash(frame: av.VideoFrame) -> int:
    """64-bit difference hash.

    Resizing is done by libav during reformat rather than in numpy — it is the
    same work either way, but libav does it in C on data it already holds.
    """
    small = frame.reformat(width=_HASH_W, height=_HASH_H, format="gray").to_ndarray()
    diff = small[:, 1:] > small[:, :-1]
    bits = np.packbits(diff.astype(np.uint8).ravel())
    return int.from_bytes(bits.tobytes(), "big")


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def scan_sync(
    path: Path,
    *,
    min_gap_s: float = DEFAULT_MIN_GAP_S,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
    threshold: int = DEFAULT_THRESHOLD,
    max_keyframes: int | None = None,
    progress: Callable[[float], None] | None = None,
) -> list[Keyframe]:
    """Scan for visually distinct moments. Blocking; call via `scan()`."""
    if not path.exists():
        raise KeyframeError(f"File not found: {path}")

    try:
        container = av.open(str(path))
    except (av.FFmpegError, OSError, ValueError) as exc:
        raise KeyframeError(f"Could not open {path.name}: {exc}") from exc

    with container:
        if not container.streams.video:
            raise KeyframeError("No video stream")

        stream = container.streams.video[0]
        # The whole point: never reconstruct an inter-frame.
        stream.codec_context.skip_frame = "NONKEY"
        stream.thread_type = "AUTO"

        duration_s = float(container.duration / av.time_base) if container.duration else None

        samples: list[tuple[float, int]] = []
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            samples.append((frame.time, dhash(frame)))
            if progress and duration_s:
                progress(min(frame.time / duration_s, 1.0))

    if not samples:
        raise KeyframeError("No decodable keyframes")

    # B-frame reordering means decode order is not presentation order.
    samples.sort(key=lambda s: s[0])

    keyframes = _select(
        samples,
        min_gap_s=min_gap_s,
        max_gap_s=max_gap_s,
        threshold=threshold,
        end_s=duration_s or samples[-1][0],
    )

    if max_keyframes and len(keyframes) > max_keyframes:
        keyframes = _thin(keyframes, max_keyframes)

    logger.info(
        "Scanned %d keyframes -> %d selected (%.1f min of video)",
        len(samples),
        len(keyframes),
        (duration_s or 0) / 60,
    )
    return keyframes


def _select(
    samples: list[tuple[float, int]],
    *,
    min_gap_s: float,
    max_gap_s: float,
    threshold: int,
    end_s: float,
) -> list[Keyframe]:
    """Emit a keyframe when the picture has changed enough, or too long has passed."""
    selected: list[Keyframe] = []
    anchor_time, anchor_hash = samples[0]
    run_start = anchor_time
    selected.append(Keyframe(run_start, run_start, anchor_time, anchor_hash, 0))

    for time_s, phash in samples[1:]:
        gap = time_s - anchor_time
        if gap < min_gap_s:
            continue

        change = hamming(phash, anchor_hash)
        if change < threshold and gap < max_gap_s:
            continue

        # Close the previous span at this boundary and open a new one.
        selected[-1].end_s = time_s
        selected.append(Keyframe(time_s, time_s, time_s, phash, change))
        anchor_time, anchor_hash = time_s, phash
        run_start = time_s

    selected[-1].end_s = max(end_s, selected[-1].start_s)
    return selected


def _thin(keyframes: list[Keyframe], limit: int) -> list[Keyframe]:
    """Drop the least-changed keyframes to fit a budget.

    Keeps the first and last, then the highest-change frames — a cap on OCR and
    caption cost should discard the most redundant frames, not a uniform slice.
    """
    if len(keyframes) <= limit:
        return keyframes

    middle = sorted(keyframes[1:-1], key=lambda k: k.change, reverse=True)[: limit - 2]
    kept = [keyframes[0], *middle, keyframes[-1]]
    kept.sort(key=lambda k: k.start_s)

    # Spans must stay contiguous after removal.
    for a, b in zip(kept, kept[1:], strict=False):
        a.end_s = b.start_s
    return kept


def extract_sync(
    path: Path,
    keyframes: list[Keyframe],
    destination: Path,
    *,
    max_width: int = 1280,
    quality: int = 85,
    progress: Callable[[float], None] | None = None,
) -> list[Path]:
    """Write one JPEG per keyframe, seeking rather than decoding sequentially."""
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    try:
        container = av.open(str(path))
    except (av.FFmpegError, OSError, ValueError) as exc:
        raise KeyframeError(f"Could not open {path.name}: {exc}") from exc

    with container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"

        for i, keyframe in enumerate(keyframes):
            out = destination / f"{i:06d}_{keyframe.time_s:.2f}.jpg"
            try:
                container.seek(
                    int(keyframe.time_s / stream.time_base), stream=stream, any_frame=False
                )
                frame = next(container.decode(stream), None)
                if frame is None:
                    continue

                image = frame.to_image()
                if image.width > max_width:
                    ratio = max_width / image.width
                    image = image.resize(
                        (max_width, max(int(image.height * ratio), 1))
                    )
                image.save(out, "JPEG", quality=quality, optimize=True)
                written.append(out)
            except Exception:  # noqa: BLE001 — one bad frame must not lose the rest
                logger.debug("Could not extract keyframe at %.2fs", keyframe.time_s, exc_info=True)

            if progress:
                progress((i + 1) / len(keyframes))

    return written


async def scan(path: Path, **kwargs) -> list[Keyframe]:
    return await asyncio.to_thread(scan_sync, path, **kwargs)


async def extract(path: Path, keyframes: list[Keyframe], destination: Path, **kwargs) -> list[Path]:
    return await asyncio.to_thread(extract_sync, path, keyframes, destination, **kwargs)
