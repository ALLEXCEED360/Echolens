"""Topic segmentation and rule-derived events.

Segmentation is tested with synthetic embeddings whose structure is known
exactly: real vectors would make a failure impossible to attribute between the
algorithm and the model that produced them.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.pipeline.events import (
    RuleEvent,
    Segment,
    build_topic_hierarchy,
    build_topics,
    coherence_scores,
    depth_scores,
    find_boundaries,
    label_topics,
    locate_parent,
    scene_events,
    silence_events,
    text_events,
)

DIM = 32


def block_segments(
    blocks: int = 4, per_block: int = 12, *, noise: float = 0.05, step_s: float = 15.0
) -> list[Segment]:
    """Segments forming `blocks` distinct clusters — boundaries are known."""
    rng = np.random.default_rng(0)
    segments: list[Segment] = []
    index = 0

    for b in range(blocks):
        centre = np.zeros(DIM, dtype=np.float32)
        # Well-separated centroids: each block occupies its own dimension.
        centre[b % DIM] = 1.0
        for _ in range(per_block):
            vector = centre + rng.normal(0, noise, DIM).astype(np.float32)
            vector /= np.linalg.norm(vector)
            segments.append(
                Segment(
                    start_s=index * step_s,
                    end_s=(index + 1) * step_s,
                    text=f"block{b} word{index} content about subject{b}",
                    embedding=vector.tolist(),
                )
            )
            index += 1

    return segments


class TestCoherence:
    def test_identical_vectors_are_maximally_coherent(self) -> None:
        embeddings = np.ones((10, DIM), dtype=np.float32)
        assert coherence_scores(embeddings) == pytest.approx(1.0, abs=1e-5)

    def test_dips_at_a_real_boundary(self) -> None:
        segments = block_segments(blocks=2, per_block=10)
        embeddings = np.asarray([s.embedding for s in segments], dtype=np.float32)
        scores = coherence_scores(embeddings, window=3)

        # The boundary sits at index 9 (gap between chunk 9 and 10).
        assert scores[9] < scores[3], "coherence should drop at the subject change"
        assert scores[9] < scores[15]

    def test_length_is_one_less_than_input(self) -> None:
        assert len(coherence_scores(np.ones((7, DIM), dtype=np.float32))) == 6

    def test_single_vector(self) -> None:
        assert len(coherence_scores(np.ones((1, DIM), dtype=np.float32))) == 0


class TestDepth:
    def test_flat_signal_has_no_depth(self) -> None:
        assert depth_scores(np.full(10, 0.9)) == pytest.approx(0.0)

    def test_valley_scores_highest(self) -> None:
        signal = np.array([0.9, 0.9, 0.9, 0.2, 0.9, 0.9, 0.9])
        depth = depth_scores(signal)
        assert depth.argmax() == 3

    def test_uniformly_low_is_not_a_boundary(self) -> None:
        """Low similarity throughout is a quiet passage, not a subject change."""
        assert depth_scores(np.full(10, 0.1)).max() == pytest.approx(0.0)


class TestBoundaries:
    def test_finds_the_planted_boundaries(self) -> None:
        segments = block_segments(blocks=4, per_block=12, step_s=15.0)
        boundaries = find_boundaries(segments, window=3, min_topic_s=60.0, depth_k=0.5)

        found = {i + 1 for i, _ in boundaries}
        expected = {12, 24, 36}
        # Allow a chunk of slack: windowing smears the exact index.
        assert all(any(abs(f - e) <= 2 for f in found) for e in expected), (
            f"expected boundaries near {expected}, got {sorted(found)}"
        )

    def test_respects_minimum_spacing(self) -> None:
        segments = block_segments(blocks=8, per_block=4, step_s=10.0)
        boundaries = find_boundaries(segments, window=2, min_topic_s=120.0, depth_k=0.0)

        times = sorted(segments[i + 1].start_s for i, _ in boundaries)
        for a, b in zip(times, times[1:], strict=False):
            assert b - a >= 120.0

    def test_uniform_content_yields_no_boundaries(self) -> None:
        rng = np.random.default_rng(1)
        base = rng.normal(0, 1, DIM).astype(np.float32)
        base /= np.linalg.norm(base)
        segments = [
            Segment(i * 15.0, (i + 1) * 15.0, "same subject throughout", base.tolist())
            for i in range(40)
        ]
        assert find_boundaries(segments, depth_k=1.0) == []

    def test_too_few_segments(self) -> None:
        assert find_boundaries(block_segments(blocks=1, per_block=3), window=4) == []


class TestTopics:
    def test_topics_tile_the_timeline(self) -> None:
        segments = block_segments(blocks=4, per_block=12)
        topics = build_topics(segments, depth_k=0.5, min_topic_s=60.0)

        assert topics[0].start_s == segments[0].start_s
        assert topics[-1].end_s == segments[-1].end_s
        for a, b in zip(topics, topics[1:], strict=False):
            assert b.start_s >= a.start_s

    def test_every_segment_is_covered(self) -> None:
        segments = block_segments(blocks=3, per_block=10)
        topics = build_topics(segments, depth_k=0.5, min_topic_s=60.0)
        covered = sorted(i for t in topics for i in t.segment_indices)
        assert covered == list(range(len(segments)))

    def test_titles_are_not_empty(self) -> None:
        topics = build_topics(block_segments(), depth_k=0.5, min_topic_s=60.0)
        assert all(t.title.strip() for t in topics)

    def test_empty_input(self) -> None:
        assert build_topics([]) == []

    def test_single_segment(self) -> None:
        segments = block_segments(blocks=1, per_block=1)
        topics = build_topics(segments)
        assert len(topics) == 1


class TestLabelling:
    def test_surfaces_distinctive_terms(self) -> None:
        """A term common to every span carries no signal and must not win."""
        texts = [
            "unity unity rigidbody rigidbody physics physics collider",
            "unity unity sprite sprite animation animation frames",
            "unity unity audio audio sound sound mixer",
        ]
        labels = label_topics(texts, top_n=3)

        assert "rigidbody" in labels[0]
        assert "sprite" in labels[1]
        assert "audio" in labels[2]
        assert all("unity" not in label for label in labels), "ubiquitous term leaked into labels"

    def test_stopwords_removed(self) -> None:
        labels = label_topics(["the and but the and but collider collider collider"])
        assert labels[0] and "the" not in labels[0]

    def test_empty(self) -> None:
        assert label_topics([]) == []


class TestHierarchy:
    def test_coarse_is_coarser_than_fine(self) -> None:
        segments = block_segments(blocks=8, per_block=12, step_s=15.0)
        coarse, fine = build_topic_hierarchy(
            segments, coarse_depth_k=1.5, coarse_min_topic_s=600.0,
            fine_depth_k=0.5, fine_min_topic_s=120.0,
        )
        assert len(fine) >= len(coarse)

    def test_every_fine_topic_finds_a_parent(self) -> None:
        segments = block_segments(blocks=6, per_block=12, step_s=15.0)
        coarse, fine = build_topic_hierarchy(segments)
        positions = {c.position for c in coarse}
        assert all(locate_parent(coarse, f) in positions for f in fine)

    def test_parent_contains_child_midpoint(self) -> None:
        segments = block_segments(blocks=6, per_block=12, step_s=15.0)
        coarse, fine = build_topic_hierarchy(segments)
        for child in fine:
            parent = coarse[locate_parent(coarse, child)]
            midpoint = (child.start_s + child.end_s) / 2
            assert parent.start_s <= midpoint

    def test_no_parents(self) -> None:
        assert locate_parent([], Segment(0, 1, "", [])) is None  # type: ignore[arg-type]


class TestRuleEvents:
    def test_scene_change_needs_a_large_jump(self) -> None:
        frames = [
            {"id": "a", "start_s": 0.0, "end_s": 10.0, "change": 2},
            {"id": "b", "start_s": 10.0, "end_s": 20.0, "change": 40},
        ]
        events = scene_events(frames, threshold=22)
        assert len(events) == 1
        assert events[0].start_s == 10.0

    def test_scene_confidence_scales_and_saturates(self) -> None:
        frames = [{"id": "a", "start_s": 0.0, "end_s": 1.0, "change": 64}]
        assert scene_events(frames, threshold=22)[0].confidence == 1.0

    def test_scene_evidence_points_back(self) -> None:
        frames = [{"id": "kf-1", "start_s": 5.0, "end_s": 6.0, "change": 40}]
        assert scene_events(frames)[0].evidence[0]["id"] == "kf-1"

    def test_silence_from_transcript_gaps(self) -> None:
        spans = [(0.0, 10.0), (12.0, 20.0), (60.0, 70.0)]
        events = silence_events(spans, threshold_s=20.0)
        assert len(events) == 1
        assert (events[0].start_s, events[0].end_s) == (20.0, 60.0)

    def test_short_gaps_ignored(self) -> None:
        assert silence_events([(0.0, 10.0), (11.0, 20.0)], threshold_s=20.0) == []

    def test_text_appearing_is_an_edge_not_a_level(self) -> None:
        """Only the transition into text counts, not every frame that has it."""
        frames = [
            {"id": "a", "start_s": 0.0, "end_s": 5.0, "text": ""},
            {"id": "b", "start_s": 5.0, "end_s": 10.0, "text": "Chapter One"},
            {"id": "c", "start_s": 10.0, "end_s": 15.0, "text": "Chapter One"},
            {"id": "d", "start_s": 15.0, "end_s": 20.0, "text": ""},
            {"id": "e", "start_s": 20.0, "end_s": 25.0, "text": "Chapter Two"},
        ]
        events = text_events(frames)
        assert [e.start_s for e in events] == [5.0, 20.0]

    def test_text_event_carries_a_snippet(self) -> None:
        frames = [{"id": "a", "start_s": 0.0, "end_s": 1.0, "text": "Install Unity Hub"}]
        assert "Install Unity Hub" in text_events(frames)[0].title

    def test_empty_inputs(self) -> None:
        assert scene_events([]) == []
        assert silence_events([]) == []
        assert text_events([]) == []


class TestRuleEventShape:
    def test_defaults(self) -> None:
        event = RuleEvent(type="scene_change", start_s=0.0, end_s=1.0, title="x")
        assert event.confidence == 1.0
        assert event.evidence == []
