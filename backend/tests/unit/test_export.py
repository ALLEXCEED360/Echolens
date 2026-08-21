"""Transcript export formats.

Subtitle formats fail quietly. A malformed cue does not raise — it shifts every
later cue, and the file only looks wrong once someone plays it against the
video twenty minutes in. So the traps get their own cases here rather than
being discovered later.
"""

from __future__ import annotations

import pytest

from app.export import (
    ExportSegment,
    render,
    safe_filename,
    to_markdown,
    to_srt,
    to_text,
    to_vtt,
)

VIDEO_ID = "86657e11-4460-415c-bd27-26db5da73ac4"


def seg(start: float, end: float, text: str, speaker: str | None = None) -> ExportSegment:
    return ExportSegment(start_s=start, end_s=end, text=text, speaker_id=speaker)


SAMPLE = [
    seg(0.0, 3.5, "Mr. President, we have to get you out of here!"),
    seg(3.5, 4.25, "Where is my daughter?"),
    seg(3661.75, 3665.0, "Thunderstrike from Chamberlain"),
]


class TestSrt:
    def test_structure(self) -> None:
        out = to_srt(SAMPLE)
        assert out.startswith("1\n00:00:00,000 --> 00:00:03,500\n")
        assert "2\n00:00:03,500 --> 00:00:04,250\n" in out

    def test_uses_comma_for_milliseconds(self) -> None:
        """SRT uses a comma; VTT uses a dot. Players reject the wrong one."""
        assert "00:00:03,500" in to_srt(SAMPLE)
        assert "00:00:03.500" not in to_srt(SAMPLE)

    def test_hours_past_one_hour(self) -> None:
        assert "01:01:01,750" in to_srt(SAMPLE)

    def test_indices_are_sequential_from_one(self) -> None:
        indices = [b.split("\n")[0] for b in to_srt(SAMPLE).strip().split("\n\n")]
        assert indices == ["1", "2", "3"]

    def test_blank_line_inside_a_caption_cannot_split_the_cue(self) -> None:
        """The trap this format punishes hardest.

        A blank line is the record separator. A caption containing one ends its
        cue early, and every subsequent cue is misnumbered — the file silently
        desynchronises rather than failing.
        """
        out = to_srt([seg(0.0, 2.0, "first line\n\nsecond line"), seg(2.0, 4.0, "next")])
        blocks = out.strip().split("\n\n")

        assert len(blocks) == 2, "a blank line inside a caption split the cue"
        assert blocks[1].startswith("2\n")

    def test_internal_newlines_are_kept_as_line_breaks(self) -> None:
        out = to_srt([seg(0.0, 2.0, "line one\nline two")])
        assert "line one\nline two" in out

    def test_empty_transcript(self) -> None:
        assert to_srt([]) == ""


class TestVtt:
    def test_header_is_required(self) -> None:
        """A VTT file without the WEBVTT signature is rejected outright."""
        assert to_vtt(SAMPLE).startswith("WEBVTT\n")

    def test_uses_dot_for_milliseconds(self) -> None:
        out = to_vtt(SAMPLE)
        assert "00:00:03.500" in out
        assert "00:00:03,500" not in out

    def test_still_has_a_header_when_empty(self) -> None:
        assert to_vtt([]).strip() == "WEBVTT"


class TestOrdering:
    def test_segments_are_sorted_by_time(self) -> None:
        out = to_srt([seg(10.0, 12.0, "later"), seg(1.0, 2.0, "earlier")])
        assert out.index("earlier") < out.index("later")

    def test_zero_length_segment_gets_a_visible_span(self) -> None:
        """Players treat a zero-duration cue as a malformed file."""
        out = to_srt([seg(5.0, 5.0, "instant")])
        assert "00:00:05,000 --> 00:00:05,500" in out

    def test_negative_time_is_clamped(self) -> None:
        assert "00:00:00,000" in to_srt([seg(-3.0, 1.0, "before the start")])


class TestText:
    def test_is_prose_without_timestamps(self) -> None:
        out = to_text(SAMPLE)
        assert "00:00" not in out
        assert "Where is my daughter?" in out


class TestMarkdown:
    def test_timestamps_are_plain_without_a_base_url(self) -> None:
        out = to_markdown(SAMPLE, title="Match", video_id=VIDEO_ID)
        assert "`00:00:00`" in out
        assert "http" not in out

    def test_links_back_to_the_moment(self) -> None:
        out = to_markdown(
            SAMPLE, title="Match", video_id=VIDEO_ID, base_url="http://localhost:3000"
        )
        assert f"(http://localhost:3000/videos/{VIDEO_ID}?t=3661)" in out

    def test_trailing_slash_on_the_base_url_does_not_double(self) -> None:
        out = to_markdown(
            SAMPLE, title="M", video_id=VIDEO_ID, base_url="http://localhost:3000/"
        )
        assert "3000//videos" not in out

    def test_title_becomes_the_heading(self) -> None:
        assert to_markdown(SAMPLE, title="Unity Course", video_id=VIDEO_ID).startswith(
            "# Unity Course"
        )

    def test_speaker_is_shown_when_known(self) -> None:
        out = to_markdown([seg(0.0, 1.0, "Hello", "Speaker 1")], title="T", video_id=VIDEO_ID)
        assert "**Speaker 1:**" in out

    def test_one_line_per_segment(self) -> None:
        """A multi-line caption must not become two bullets."""
        out = to_markdown([seg(0.0, 2.0, "line one\nline two")], title="T", video_id=VIDEO_ID)
        assert len([ln for ln in out.splitlines() if ln.startswith("- ")]) == 1


class TestRender:
    @pytest.mark.parametrize("fmt", ["srt", "vtt", "txt", "md"])
    def test_every_advertised_format_produces_output(self, fmt: str) -> None:
        out = render(fmt, SAMPLE, title="T", video_id=VIDEO_ID)
        assert out.strip()

    def test_unknown_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown export format"):
            render("docx", SAMPLE, title="T", video_id=VIDEO_ID)


class TestFilename:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Unity 2D Crash Course", "Unity 2D Crash Course.srt"),
            ("a/b\\c", "a b c.srt"),
            ("Lecture 05: Backprop", "Lecture 05 Backprop.srt"),
            ("   ", "transcript.srt"),
            ("", "transcript.srt"),
        ],
    )
    def test_sanitises(self, title: str, expected: str) -> None:
        assert safe_filename(title, "srt") == expected

    def test_long_titles_are_truncated(self) -> None:
        name = safe_filename("x" * 500, "vtt")
        assert len(name) <= 84
        assert name.endswith(".vtt")

    def test_no_character_illegal_on_windows_survives(self) -> None:
        name = safe_filename('a<b>c:d"e|f?g*h', "txt")
        assert not set(name) & set('<>:"/\\|?*')
