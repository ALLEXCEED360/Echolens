"""Transcript chunking.

This module decides search quality more than any model choice in the project.

**The problem.** Whisper segments average ~3 seconds — roughly eight words. Embed
those directly and you get vectors for fragments like "and then we go to" that
match everything and mean nothing. The context that makes a passage *findable*
lives in the surrounding half-minute, not the fragment.

**The approach — parent/child.** Build two levels over the same timeline:

  - **child** (~15 s): the embedded unit. Small enough that its vector describes
    one idea, so a hit is precise about *when*.
  - **parent** (~60 s): the unit handed to the LLM. Large enough to carry the
    argument around the hit, so the answer has context to reason over.

Retrieval ranks children and returns parents. Precision from one, comprehension
from the other.

Children overlap by ~20% so a concept split across a boundary survives intact
somewhere, and boundaries prefer sentence ends over hard cuts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Targets are in seconds of wall-clock video, not tokens: the timeline is the
# organising axis here, and a fixed token window would drift against it.
CHILD_TARGET_S = 15.0
CHILD_MAX_S = 22.0
CHILD_OVERLAP_S = 3.0
PARENT_TARGET_S = 60.0
PARENT_MAX_S = 90.0

_SENTENCE_END = re.compile(r"[.!?]['\")\]]*$")


@dataclass
class SourceSegment:
    """A raw ASR segment being fed into chunking."""

    start_s: float
    end_s: float
    text: str
    speaker_id: str | None = None


@dataclass
class BuiltChunk:
    level: str
    position: int
    start_s: float
    end_s: float
    text: str
    segment_indices: list[int] = field(default_factory=list)
    speakers: list[str] = field(default_factory=list)
    parent_position: int | None = None

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def estimate_tokens(text: str) -> int:
    """Rough token count. ~0.75 words per token for English prose.

    Deliberately an estimate: loading a tokenizer here would couple chunking to
    a specific model, and the value is only used for budgeting context.
    """
    words = len(text.split())
    return max(1, int(words / 0.75))


def _ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.strip()))


def _join(segments: list[SourceSegment]) -> str:
    return " ".join(s.text.strip() for s in segments if s.text.strip()).strip()


def _build_level(
    segments: list[SourceSegment],
    *,
    target_s: float,
    max_s: float,
    overlap_s: float,
    level: str,
) -> list[BuiltChunk]:
    """Greedily accumulate segments to `target_s`, preferring sentence endings."""
    if not segments:
        return []

    chunks: list[BuiltChunk] = []
    index = 0
    position = 0

    while index < len(segments):
        buf: list[int] = []
        start = segments[index].start_s

        while index < len(segments):
            seg = segments[index]
            span = seg.end_s - start

            # Always take at least one segment, or a single long segment could
            # produce an empty chunk and stall the loop.
            if buf and span > max_s:
                break

            buf.append(index)
            index += 1

            # Past target: stop here on a clean sentence end, else keep
            # going a little in search of one, up to max_s.
            if span >= target_s and (_ends_sentence(seg.text) or span >= max_s):
                break

        # Looking forward alone finds a sentence end only when one happens to
        # fall in the narrow target..max window. If it did not, fall back to the
        # last sentence end already inside the buffer — a slightly short chunk
        # that ends on a full thought retrieves better than a longer one cut
        # mid-clause. Only backtrack if the result is still substantial.
        if len(buf) > 1 and not _ends_sentence(segments[buf[-1]].text):
            floor = target_s * 0.6
            for k in range(len(buf) - 2, 0, -1):
                candidate = segments[buf[k]]
                if _ends_sentence(candidate.text) and candidate.end_s - start >= floor:
                    index = buf[k] + 1
                    buf = buf[: k + 1]
                    break

        picked = [segments[i] for i in buf]
        chunks.append(
            BuiltChunk(
                level=level,
                position=position,
                start_s=picked[0].start_s,
                end_s=picked[-1].end_s,
                text=_join(picked),
                segment_indices=list(buf),
                speakers=sorted({s.speaker_id for s in picked if s.speaker_id}),
            )
        )
        position += 1

        if index >= len(segments):
            break

        # Rewind for overlap so a concept spanning a boundary appears whole in
        # at least one chunk. Guarded to always make forward progress.
        if overlap_s > 0:
            boundary = segments[index - 1].end_s
            rewind = index - 1
            while rewind > buf[0] and segments[rewind - 1].end_s > boundary - overlap_s:
                rewind -= 1
            index = max(rewind, buf[0] + 1)

    return chunks


def build_chunks(segments: list[SourceSegment]) -> tuple[list[BuiltChunk], list[BuiltChunk]]:
    """Return `(parents, children)` with each child linked to its parent.

    A child is assigned to the parent containing its midpoint, so every child
    has exactly one parent even where overlapping children straddle a boundary.
    """
    usable = [s for s in segments if s.text and s.text.strip()]
    if not usable:
        return [], []

    parents = _build_level(
        usable, target_s=PARENT_TARGET_S, max_s=PARENT_MAX_S, overlap_s=0.0, level="parent"
    )
    children = _build_level(
        usable,
        target_s=CHILD_TARGET_S,
        max_s=CHILD_MAX_S,
        overlap_s=CHILD_OVERLAP_S,
        level="child",
    )

    for child in children:
        midpoint = (child.start_s + child.end_s) / 2
        child.parent_position = _locate(parents, midpoint)

    return parents, children


def _locate(parents: list[BuiltChunk], t: float) -> int | None:
    """Index of the parent covering `t`; nearest by start if none contains it."""
    if not parents:
        return None

    lo, hi, found = 0, len(parents) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if parents[mid].start_s <= t:
            found = mid
            lo = mid + 1
        else:
            hi = mid - 1

    if found < 0:
        return parents[0].position
    # Parents do not overlap, so containment is the common case; clamping to the
    # last started parent handles the tail after the final segment ends.
    return parents[found].position
