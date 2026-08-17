"""The benchmark question set.

Ground-truth labels are the one thing in an evaluation that nothing else can
check. A wrong metric usually produces an obviously wrong number; a wrong label
produces a plausible one. These tests pin the semantics of a label so it cannot
drift quietly.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.benchmark.dataset import Dataset, Question


def question(**overrides) -> Question:
    base = {
        "id": "q001",
        "question": "What is a prefab?",
        "category": "generated",
        "video_id": uuid.uuid4(),
        "gold_spans": [[100.0, 160.0]],
        "verified": True,
    }
    return Question(**(base | overrides))


class TestOverlap:
    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            (120.0, 140.0, True),   # inside
            (90.0, 110.0, True),    # straddles the start
            (150.0, 200.0, True),   # straddles the end
            (50.0, 250.0, True),    # contains the gold span
            (100.0, 160.0, True),   # exact
            (40.0, 60.0, False),    # entirely before
            (200.0, 260.0, False),  # entirely after
        ],
    )
    def test_cases(self, start: float, end: float, expected: bool) -> None:
        assert question().overlaps(start, end) is expected

    @pytest.mark.parametrize(("start", "end"), [(60.0, 100.0), (160.0, 200.0)])
    def test_touching_boundaries_count(self, start: float, end: float) -> None:
        """A chunk ending exactly where gold begins shares a moment with it.

        The alternative is a criterion that flips on floating-point noise.
        """
        assert question().overlaps(start, end) is True

    def test_any_gold_span_counts(self) -> None:
        """A six-hour tutorial explains the same thing more than once.

        Marking only the sampled occurrence correct would score a retriever
        that found a genuine explanation as having failed.
        """
        multi = question(gold_spans=[[100.0, 160.0], [900.0, 960.0]])
        assert multi.overlaps(910.0, 930.0) is True
        assert multi.overlaps(500.0, 520.0) is False

    def test_negative_matches_nothing(self) -> None:
        negative = question(gold_spans=[])
        assert negative.is_negative is True
        assert negative.overlaps(0.0, 100_000.0) is False


class TestAnchor:
    def test_nearest_gold_start_picks_the_closer_span(self) -> None:
        multi = question(gold_spans=[[100.0, 160.0], [900.0, 960.0]])
        assert multi.nearest_gold_start_s(910.0) == 900.0
        assert multi.nearest_gold_start_s(105.0) == 100.0

    def test_no_anchor_by_default(self) -> None:
        """Hand-written questions have no source chunk, so no anchor.

        They must be excluded from timestamp error rather than fall back to a
        parent boundary, which would score parent-level retrieval at zero.
        """
        assert question().has_anchor is False

    def test_anchor_round_trips(self) -> None:
        restored = Question.from_json(question(anchor_s=123.5).to_json())
        assert restored.anchor_s == 123.5
        assert restored.has_anchor is True


class TestSerialisation:
    def test_round_trip(self) -> None:
        original = question(source_chunk_id=uuid.uuid4(), note="checked")
        restored = Question.from_json(json.loads(json.dumps(original.to_json())))
        assert restored == original

    def test_accepts_the_single_span_form(self) -> None:
        """The generator writes one span; the reviewer may add more."""
        restored = Question.from_json(
            {
                "id": "q1", "question": "?", "category": "generated",
                "video_id": None, "gold_start_s": 10.0, "gold_end_s": 70.0,
            }
        )
        assert restored.gold_spans == [[10.0, 70.0]]

    def test_unknown_fields_are_ignored(self) -> None:
        """An older or newer file must not crash the loader."""
        restored = Question.from_json(
            {"id": "q1", "question": "?", "category": "x", "invented_field": 1}
        )
        assert restored.id == "q1"

    def test_file_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "q.jsonl"
        Dataset(questions=[question(), question(id="q002", gold_spans=[])]).save(path)
        assert len(Dataset.load(path)) == 2

    def test_duplicate_ids_are_rejected(self, tmp_path: Path) -> None:
        """Two rows with one id silently drops a question from every report."""
        path = tmp_path / "q.jsonl"
        path.write_text(
            "\n".join(json.dumps(q.to_json()) for q in [question(), question()]),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Duplicate"):
            Dataset.load(path)

    def test_missing_file_names_the_fix(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="build_benchmark"):
            Dataset.load(tmp_path / "absent.jsonl")


class TestScoping:
    def test_unverified_questions_are_not_scored(self) -> None:
        """A benchmark that scores machine-proposed labels measures the proposer."""
        dataset = Dataset(
            questions=[question(), question(id="q002", verified=False)]
        )
        assert [q.id for q in dataset.scorable] == ["q001"]

    def test_negatives_are_separated_from_scorable(self) -> None:
        dataset = Dataset(
            questions=[question(), question(id="n1", category="negative", gold_spans=[])]
        )
        assert [q.id for q in dataset.scorable] == ["q001"]
        assert [q.id for q in dataset.negatives] == ["n1"]

    def test_unverified_negatives_are_excluded_too(self) -> None:
        dataset = Dataset(
            questions=[question(id="n1", category="negative", gold_spans=[], verified=False)]
        )
        assert dataset.negatives == []

    def test_categories_come_from_scorable_only(self) -> None:
        dataset = Dataset(
            questions=[
                question(category="lexical"),
                question(id="q002", category="draft", verified=False),
            ]
        )
        assert dataset.categories == ["lexical"]
        assert len(dataset.by_category("lexical")) == 1


class TestRealDataset:
    """The committed question set itself."""

    def test_loads_and_is_labelled_consistently(self) -> None:
        dataset = Dataset.load()

        assert len(dataset.scorable) >= 40, "too small to report a percentage from"
        assert dataset.negatives, "without negatives, refusal is unmeasured"

        for q in dataset.scorable:
            assert q.video_id is not None, f"{q.id} is scorable but names no video"
            assert q.gold_spans, f"{q.id} is scorable but has no gold span"
            for start, end in q.gold_spans:
                assert end > start, f"{q.id} has an inverted span"

        for q in dataset.negatives:
            assert not q.gold_spans, f"{q.id} is a negative but carries a gold span"

    def test_question_ids_are_unique(self) -> None:
        ids = [q.id for q in Dataset.load()]
        assert len(ids) == len(set(ids))
