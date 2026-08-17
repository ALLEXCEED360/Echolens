"""HTTP Range request handling for video streaming.

Native `<video>` seeking depends entirely on the server answering byte-range
requests with `206 Partial Content`. Without it the browser must download the
whole file before it will let you scrub — which for a 2 GB lecture means seeking
does not work at all.

This is small, easy to get subtly wrong, and load-bearing for the core UX, so it
lives in its own module with its own tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_RANGE_RE = re.compile(r"^bytes=(?P<start>\d*)-(?P<end>\d*)$")


class InvalidRange(ValueError):
    """The Range header is unsatisfiable for this resource."""


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclusive, per RFC 9110

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    def content_range(self, total: int) -> str:
        return f"bytes {self.start}-{self.end}/{total}"


def parse_range(header: str | None, file_size: int) -> ByteRange | None:
    """Parse a single-range `Range` header against a known file size.

    Returns None when the whole resource should be served (no header, or a
    header we decline to honour). Raises `InvalidRange` when the client asked
    for something unsatisfiable, which must become a 416.

    Multi-range requests (`bytes=0-99,200-299`) are intentionally not supported:
    they require multipart/byteranges, and no browser video element sends them.
    """
    if not header:
        return None

    match = _RANGE_RE.match(header.strip())
    if not match:
        # Malformed or multi-range. RFC 9110 permits ignoring it entirely.
        return None

    raw_start, raw_end = match.group("start"), match.group("end")

    if not raw_start and not raw_end:
        return None  # "bytes=-" is meaningless

    if file_size == 0:
        raise InvalidRange("Resource is empty")

    if not raw_start:
        # Suffix form: "bytes=-500" means the final 500 bytes.
        suffix = int(raw_end)
        if suffix == 0:
            raise InvalidRange("Zero-length suffix range")
        start = max(0, file_size - suffix)
        end = file_size - 1
    else:
        start = int(raw_start)
        # Open-ended "bytes=1000-" runs to EOF. Clamp an over-long end rather
        # than rejecting it — RFC 9110 requires clamping here.
        end = min(int(raw_end), file_size - 1) if raw_end else file_size - 1

    if start >= file_size or start > end:
        raise InvalidRange(f"Range {start}-{end} not satisfiable for size {file_size}")

    return ByteRange(start=start, end=end)


def range_headers(
    byte_range: ByteRange | None, file_size: int, content_type: str
) -> dict[str, str]:
    """Response headers for a full or partial body."""
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": content_type,
        # Immutable: video bytes never change once uploaded.
        "Cache-Control": "private, max-age=3600",
    }
    if byte_range is None:
        headers["Content-Length"] = str(file_size)
    else:
        headers["Content-Length"] = str(byte_range.length)
        headers["Content-Range"] = byte_range.content_range(file_size)
    return headers
