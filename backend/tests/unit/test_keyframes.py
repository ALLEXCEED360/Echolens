"""Keyframe scanning and selection.

The scan is the gate that makes every visual stage affordable, so what matters
here is that it bounds its own output and never loses the timeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline.keyframes import (
    Keyframe,
    KeyframeError,
    _select,
    _thin,
    dhash,
    extract_sync,
    hamming,
    scan_sync,
)


class TestHashing:
    def test_hamming_identical(self) -> None:
        assert hamming(0xDEADBEEF, 0xDEADBEEF) == 0

    def test_hamming_counts_differing_bits(self) -> None:
        assert hamming(0b1010, 0b0000) == 2
        assert hamming(0b1111, 0b0000) == 4

    def test_dhash_is_stable_for_identical_frames(self, sample_video: Path) -> None:
        import av

        with av.open(str(sample_video)) as container:
            frames = [f for _, f in zip(range(2), container.decode(video=0), strict=False)]
        assert dhash(frames[0]) == dhash(frames[0])

    def test_dhash_ignores_uniform_brightness(self, sample_video: Path) -> None:
        """Flat frames hash to zero however bright they are.

        dHash compares horizontally adjacent pixels, so it measures *structure*,
        not luminance. That is the behaviour we want in production — a lighting
        shift is not a slide change — and it is why change-detection tests need
        the structured fixture instead.
        """
        import av

        with av.open(str(sample_video)) as container:
            frames = list(container.decode(video=0))
        assert dhash(frames[0]) == dhash(frames[-1]) == 0

    def test_dhash_separates_different_patterns(self, structured_video: Path) -> None:
        import av

        with av.open(str(structured_video)) as container:
            frames = list(container.decode(video=0))
        first, last = dhash(frames[0]), dhash(frames[-1])
        assert first != 0, "structured frame should produce a non-zero hash"
        assert hamming(first, last) > 4


class TestSelection:
    @staticmethod
    def samples(count: int, *, step: float = 1.0, changing: bool = True):
        """(time, hash) pairs; `changing` flips bits each step."""
        return [
            (i * step, (0xFFFF_FFFF_FFFF_FFFF if (changing and i % 2) else 0))
            for i in range(count)
        ]

    def test_static_content_emits_almost_nothing(self) -> None:
        """An unchanging picture should cost one keyframe per max_gap, not more."""
        selected = _select(
            [(i * 1.0, 12345) for i in range(300)],
            min_gap_s=4.0, max_gap_s=90.0, threshold=10, end_s=300.0,
        )
        assert len(selected) <= 300 / 90 + 2

    def test_min_gap_bounds_rapid_change(self) -> None:
        """A screencast changes constantly; min_gap is what keeps it finite."""
        selected = _select(
            self.samples(600, step=0.5),
            min_gap_s=4.0, max_gap_s=90.0, threshold=10, end_s=300.0,
        )
        gaps = [b.start_s - a.start_s for a, b in zip(selected, selected[1:], strict=False)]
        assert all(g >= 4.0 - 1e-6 for g in gaps)

    def test_max_gap_forces_periodic_emission(self) -> None:
        selected = _select(
            [(i * 1.0, 999) for i in range(400)],
            min_gap_s=4.0, max_gap_s=60.0, threshold=10, end_s=400.0,
        )
        gaps = [b.start_s - a.start_s for a, b in zip(selected, selected[1:], strict=False)]
        assert all(g <= 60.0 + 1.0 for g in gaps)

    def test_spans_are_contiguous(self) -> None:
        selected = _select(
            self.samples(200), min_gap_s=4.0, max_gap_s=90.0, threshold=10, end_s=200.0
        )
        for a, b in zip(selected, selected[1:], strict=False):
            assert a.end_s == pytest.approx(b.start_s)

    def test_covers_to_the_end(self) -> None:
        selected = _select(
            self.samples(100), min_gap_s=4.0, max_gap_s=90.0, threshold=10, end_s=250.0
        )
        assert selected[-1].end_s == 250.0

    def test_single_sample(self) -> None:
        selected = _select([(0.0, 42)], min_gap_s=4.0, max_gap_s=90.0, threshold=10, end_s=10.0)
        assert len(selected) == 1
        assert selected[0].end_s == 10.0


class TestThinning:
    @staticmethod
    def frames(n: int) -> list[Keyframe]:
        return [Keyframe(i * 10.0, (i + 1) * 10.0, i * 10.0, i, change=i) for i in range(n)]

    def test_respects_the_budget(self) -> None:
        assert len(_thin(self.frames(100), 20)) == 20

    def test_keeps_first_and_last(self) -> None:
        kept = _thin(self.frames(100), 10)
        assert kept[0].start_s == 0.0
        assert kept[-1].start_s == 990.0

    def test_keeps_the_most_changed(self) -> None:
        """A budget should discard redundancy, not a uniform slice."""
        kept = _thin(self.frames(100), 12)
        middle_changes = [k.change for k in kept[1:-1]]
        assert min(middle_changes) > 50

    def test_stays_contiguous(self) -> None:
        kept = _thin(self.frames(100), 15)
        for a, b in zip(kept, kept[1:], strict=False):
            assert a.end_s == pytest.approx(b.start_s)

    def test_below_budget_is_untouched(self) -> None:
        frames = self.frames(5)
        assert _thin(frames, 50) is frames


class TestScanning:
    async def test_detects_scene_changes(self, structured_video: Path) -> None:
        """Four distinct scenes should surface as separate keyframes."""
        keyframes = scan_sync(
            structured_video, min_gap_s=1.0, max_gap_s=1e9, threshold=4
        )
        assert len(keyframes) >= 3, f"expected the scene changes, got {len(keyframes)}"

    async def test_scans_a_real_file(self, sample_video: Path) -> None:
        keyframes = scan_sync(sample_video, min_gap_s=0.1, max_gap_s=10.0, threshold=5)
        assert keyframes
        assert all(k.end_s >= k.start_s for k in keyframes)
        assert keyframes[0].start_s == pytest.approx(0.0, abs=0.5)

    async def test_respects_max_count(self, sample_video: Path) -> None:
        keyframes = scan_sync(
            sample_video, min_gap_s=0.01, max_gap_s=0.5, threshold=0, max_keyframes=3
        )
        assert len(keyframes) <= 3

    async def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(KeyframeError, match="not found"):
            scan_sync(tmp_path / "nope.mp4")

    async def test_non_media_file(self, tmp_path: Path) -> None:
        junk = tmp_path / "fake.mp4"
        junk.write_bytes(b"definitely not a container")
        with pytest.raises(KeyframeError):
            scan_sync(junk)


class TestExtraction:
    async def test_writes_jpegs(self, sample_video: Path, tmp_path: Path) -> None:
        keyframes = scan_sync(sample_video, min_gap_s=0.1, max_gap_s=10.0, threshold=5)
        paths = extract_sync(sample_video, keyframes[:3], tmp_path / "frames")

        assert paths
        assert all(p.exists() and p.stat().st_size > 0 for p in paths)
        assert all(p.suffix == ".jpg" for p in paths)

    async def test_downscales_when_wider_than_max(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        from PIL import Image

        keyframes = scan_sync(sample_video, min_gap_s=0.1, max_gap_s=10.0, threshold=5)
        paths = extract_sync(sample_video, keyframes[:1], tmp_path / "frames", max_width=160)
        assert Image.open(paths[0]).width == 160

    async def test_does_not_upscale(self, sample_video: Path, tmp_path: Path) -> None:
        """The fixture is 320px wide; a 1280 cap must leave it alone."""
        from PIL import Image

        keyframes = scan_sync(sample_video, min_gap_s=0.1, max_gap_s=10.0, threshold=5)
        paths = extract_sync(sample_video, keyframes[:1], tmp_path / "frames", max_width=1280)
        assert Image.open(paths[0]).width == 320

    async def test_creates_destination(self, sample_video: Path, tmp_path: Path) -> None:
        keyframes = scan_sync(sample_video, min_gap_s=0.1, max_gap_s=10.0, threshold=5)
        out = tmp_path / "deep" / "nested"
        extract_sync(sample_video, keyframes[:1], out)
        assert out.exists()
