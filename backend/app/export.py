"""Transcript export.

Everything the pipeline produces has, until now, been reachable only through
this application's own interface. A 5,765-segment transcript that cannot leave
is a transcript you cannot subtitle a video with, paste into notes, diff against
a corrected version, or hand to anyone else.

Pure functions over a list of segments, deliberately: the formats have real
edge cases — a blank line inside a caption silently truncates an SRT file — and
those are far easier to pin down in tests when nothing here touches a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FORMATS = ("srt", "vtt", "txt", "md")

MEDIA_TYPES = {
    # `application/x-subrip` is the registered type; browsers download it rather
    # than trying to render it, which is what we want.
    "srt": "application/x-subrip",
    "vtt": "text/vtt",
    "txt": "text/plain",
    "md": "text/markdown",
}


@dataclass(frozen=True)
class ExportSegment:
    """The minimum an exporter needs. Keeps this module free of ORM imports."""

    start_s: float
    end_s: float
    text: str
    speaker_id: str | None = None


def _clock(seconds: float, *, millis_sep: str) -> str:
    """`HH:MM:SS<sep>mmm`, with hours always present.

    Both formats permit a two-field form (`MM:SS.mmm`), and both are read by
    tools that quietly disagree about it. The three-field form is unambiguous
    everywhere, so it is what gets written.
    """
    if seconds < 0 or seconds != seconds:  # negative, or NaN
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{millis_sep}{millis:03d}"


def _caption_text(text: str) -> str:
    """Collapse a caption to safe cue text.

    A blank line is the *record separator* in SRT and VTT. A segment whose text
    happens to contain one does not merely look wrong — it ends the cue early
    and every later cue shifts, so the whole file silently desynchronises from
    that point on. Internal newlines are fine and are preserved as line breaks;
    empty ones are not.
    """
    lines = [line.strip() for line in text.strip().splitlines()]
    return "\n".join(line for line in lines if line) or "…"


def _ordered(segments: list[ExportSegment]) -> list[ExportSegment]:
    """Chronological, and never zero-length.

    Whisper occasionally emits a segment whose start equals its end. Players
    treat a zero-duration cue as a malformed file rather than skipping it, so
    it gets a minimal visible span instead.
    """
    out = []
    for seg in sorted(segments, key=lambda s: (s.start_s, s.end_s)):
        end = seg.end_s if seg.end_s > seg.start_s else seg.start_s + 0.5
        out.append(ExportSegment(seg.start_s, end, seg.text, seg.speaker_id))
    return out


def to_srt(segments: list[ExportSegment]) -> str:
    blocks = []
    for index, seg in enumerate(_ordered(segments), start=1):
        blocks.append(
            f"{index}\n"
            f"{_clock(seg.start_s, millis_sep=',')} --> {_clock(seg.end_s, millis_sep=',')}\n"
            f"{_caption_text(seg.text)}\n"
        )
    return "\n".join(blocks)


def to_vtt(segments: list[ExportSegment]) -> str:
    blocks = ["WEBVTT\n"]
    for seg in _ordered(segments):
        blocks.append(
            f"{_clock(seg.start_s, millis_sep='.')} --> {_clock(seg.end_s, millis_sep='.')}\n"
            f"{_caption_text(seg.text)}\n"
        )
    return "\n".join(blocks)


def to_text(segments: list[ExportSegment]) -> str:
    """Prose, no timestamps — for reading, or for feeding to something else."""
    return "\n".join(_caption_text(s.text) for s in _ordered(segments)) + "\n"


def to_markdown(
    segments: list[ExportSegment],
    *,
    title: str,
    video_id: str,
    base_url: str | None = None,
) -> str:
    """Timestamped Markdown, with each timestamp linking back to the moment.

    This is the format that makes the export worth having rather than merely
    possible: pasted into notes, every line is still a click away from the
    footage it came from.
    """
    lines = [f"# {title}", ""]
    for seg in _ordered(segments):
        stamp = _clock(seg.start_s, millis_sep=".")[:8]  # HH:MM:SS
        label = f"`{stamp}`"
        if base_url:
            link = f"{base_url.rstrip('/')}/videos/{video_id}?t={int(seg.start_s)}"
            label = f"[`{stamp}`]({link})"
        speaker = f"**{seg.speaker_id}:** " if seg.speaker_id else ""
        lines.append(f"- {label} {speaker}{_caption_text(seg.text).replace(chr(10), ' ')}")
    return "\n".join(lines) + "\n"


def render(
    fmt: str,
    segments: list[ExportSegment],
    *,
    title: str,
    video_id: str,
    base_url: str | None = None,
) -> str:
    if fmt == "srt":
        return to_srt(segments)
    if fmt == "vtt":
        return to_vtt(segments)
    if fmt == "txt":
        return to_text(segments)
    if fmt == "md":
        return to_markdown(segments, title=title, video_id=video_id, base_url=base_url)
    raise ValueError(f"Unknown export format {fmt!r}")


def safe_filename(title: str, fmt: str) -> str:
    """A download name derived from the title, safe on every filesystem.

    Titles come from uploaded filenames and from the rename box, so they can
    contain anything at all — path separators, colons that Windows rejects,
    or the entire first paragraph of a description.
    """
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = cleaned[:80].strip() or "transcript"
    return f"{cleaned}.{fmt}"
