"""Container probing via PyAV."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.probe import ProbeError, probe


class TestProbe:
    async def test_reads_dimensions_and_fps(self, sample_video: Path) -> None:
        result = await probe(sample_video)
        assert (result.width, result.height) == (320, 240)
        assert result.fps == pytest.approx(25.0, abs=0.5)

    async def test_duration_is_seconds_not_microseconds(self, sample_video: Path) -> None:
        """The fixture is 2s. A units mistake here shows up as 2_000_000."""
        result = await probe(sample_video)
        assert result.duration_s == pytest.approx(2.0, abs=0.3)

    async def test_codec_identified(self, sample_video: Path) -> None:
        assert (await probe(sample_video)).video_codec == "mpeg4"

    async def test_silent_video_flags_no_audio(self, sample_video: Path) -> None:
        """has_audio gates the whole speech branch, so it must be right."""
        result = await probe(sample_video)
        assert result.has_audio is False
        assert result.audio_codec is None
        assert any("no audio" in w.lower() for w in result.warnings)


class TestProbeFailures:
    async def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ProbeError, match="not found"):
            await probe(tmp_path / "nope.mp4")

    async def test_non_media_file(self, tmp_path: Path) -> None:
        junk = tmp_path / "fake.mp4"
        junk.write_bytes(b"this is definitely not a video container")
        with pytest.raises(ProbeError):
            await probe(junk)

    async def test_truncated_file(self, tmp_path: Path, sample_bytes: bytes) -> None:
        """Half a real MP4 has a valid signature but no usable moov atom."""
        broken = tmp_path / "truncated.mp4"
        broken.write_bytes(sample_bytes[: len(sample_bytes) // 2])
        with pytest.raises(ProbeError):
            await probe(broken)
