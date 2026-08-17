"""Audio extraction to 16 kHz mono WAV."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.pipeline.audio import SAMPLE_RATE, AudioExtractionError, extract_audio


class TestExtraction:
    async def test_produces_16k_mono_pcm(self, video_with_audio: Path, tmp_path: Path) -> None:
        """Whisper's native format. Getting this wrong makes it resample internally."""
        out = tmp_path / "audio.wav"
        result = await extract_audio(video_with_audio, out)

        assert out.exists()
        with wave.open(str(out)) as w:
            assert w.getframerate() == SAMPLE_RATE
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2  # s16
            assert w.getnframes() > 0

        assert result.sample_rate == SAMPLE_RATE

    async def test_duration_survives_resampling(
        self, video_with_audio: Path, tmp_path: Path
    ) -> None:
        """A missing resampler or encoder flush truncates the tail."""
        result = await extract_audio(video_with_audio, tmp_path / "audio.wav")
        assert result.duration_s == pytest.approx(3.0, abs=0.25)

    async def test_creates_parent_directories(
        self, video_with_audio: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "deep" / "nested" / "audio.wav"
        await extract_audio(video_with_audio, out)
        assert out.exists()

    async def test_reports_progress(self, video_with_audio: Path, tmp_path: Path) -> None:
        seen: list[float] = []
        await extract_audio(video_with_audio, tmp_path / "a.wav", progress=seen.append)

        assert seen, "progress callback was never invoked"
        assert all(0.0 <= f <= 1.0 for f in seen)
        assert seen == sorted(seen), "progress went backwards"


class TestFailures:
    async def test_silent_video_raises(self, sample_video: Path, tmp_path: Path) -> None:
        """The runner catches this and marks the speech branch skipped."""
        with pytest.raises(AudioExtractionError, match="No audio stream"):
            await extract_audio(sample_video, tmp_path / "a.wav")

    async def test_missing_source(self, tmp_path: Path) -> None:
        with pytest.raises(AudioExtractionError, match="not found"):
            await extract_audio(tmp_path / "nope.mp4", tmp_path / "a.wav")

    async def test_non_media_source(self, tmp_path: Path) -> None:
        junk = tmp_path / "fake.mp4"
        junk.write_bytes(b"not a container at all")
        with pytest.raises(AudioExtractionError):
            await extract_audio(junk, tmp_path / "a.wav")
