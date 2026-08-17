"""Range parsing.

Seeking in a `<video>` element is exactly this code being correct, so it gets
proper coverage including the off-by-one cases.
"""

from __future__ import annotations

import pytest

from app.ranges import ByteRange, InvalidRange, parse_range, range_headers

SIZE = 1000


class TestFullBody:
    def test_no_header(self) -> None:
        assert parse_range(None, SIZE) is None

    def test_empty_header(self) -> None:
        assert parse_range("", SIZE) is None

    @pytest.mark.parametrize("header", ["bytes=-", "items=0-99", "bytes=abc-def", "garbage"])
    def test_malformed_is_ignored(self, header: str) -> None:
        """RFC 9110 permits ignoring an unparseable Range and sending the whole body."""
        assert parse_range(header, SIZE) is None

    def test_multi_range_is_ignored(self) -> None:
        # Would need multipart/byteranges; no browser video element sends it.
        assert parse_range("bytes=0-99,200-299", SIZE) is None


class TestExplicitRange:
    def test_basic(self) -> None:
        assert parse_range("bytes=0-499", SIZE) == ByteRange(0, 499)

    def test_open_ended_runs_to_eof(self) -> None:
        # What browsers actually send on first load: "bytes=0-"
        assert parse_range("bytes=0-", SIZE) == ByteRange(0, 999)

    def test_mid_file_open_ended(self) -> None:
        # And what they send after a seek.
        assert parse_range("bytes=500-", SIZE) == ByteRange(500, 999)

    def test_end_is_clamped_not_rejected(self) -> None:
        """RFC 9110 requires clamping an over-long end, not a 416."""
        assert parse_range("bytes=900-99999", SIZE) == ByteRange(900, 999)

    def test_single_byte(self) -> None:
        assert parse_range("bytes=0-0", SIZE) == ByteRange(0, 0)

    def test_final_byte(self) -> None:
        assert parse_range("bytes=999-999", SIZE) == ByteRange(999, 999)

    def test_whitespace_tolerated(self) -> None:
        assert parse_range("  bytes=10-20  ", SIZE) == ByteRange(10, 20)


class TestSuffixRange:
    def test_last_n_bytes(self) -> None:
        assert parse_range("bytes=-500", SIZE) == ByteRange(500, 999)

    def test_suffix_longer_than_file_clamps_to_start(self) -> None:
        assert parse_range("bytes=-5000", SIZE) == ByteRange(0, 999)

    def test_zero_suffix_is_unsatisfiable(self) -> None:
        with pytest.raises(InvalidRange):
            parse_range("bytes=-0", SIZE)


class TestUnsatisfiable:
    def test_start_past_eof(self) -> None:
        with pytest.raises(InvalidRange):
            parse_range("bytes=1000-1500", SIZE)

    def test_inverted(self) -> None:
        with pytest.raises(InvalidRange):
            parse_range("bytes=500-100", SIZE)

    def test_any_range_on_empty_file(self) -> None:
        with pytest.raises(InvalidRange):
            parse_range("bytes=0-10", 0)


class TestLength:
    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [(0, 0, 1), (0, 499, 500), (500, 999, 500), (999, 999, 1)],
    )
    def test_inclusive_length(self, start: int, end: int, expected: int) -> None:
        """Off-by-one here truncates every response by a byte."""
        assert ByteRange(start, end).length == expected


class TestHeaders:
    def test_full_body(self) -> None:
        h = range_headers(None, SIZE, "video/mp4")
        assert h["Content-Length"] == "1000"
        assert h["Accept-Ranges"] == "bytes"
        assert "Content-Range" not in h

    def test_partial_body(self) -> None:
        h = range_headers(ByteRange(200, 599), SIZE, "video/mp4")
        assert h["Content-Length"] == "400"
        assert h["Content-Range"] == "bytes 200-599/1000"
        assert h["Content-Type"] == "video/mp4"
