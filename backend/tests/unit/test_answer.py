"""Citation integrity.

This is the guarantee Phase 7 exists to provide: a timestamp the model did not
write cannot be wrong, and a reference the model invented cannot survive. These
tests attack that guarantee directly — every way a model could try to smuggle a
bad citation past the validator gets its own case.
"""

from __future__ import annotations

import uuid

import pytest

from app.answer import (
    CITATION_RE,
    EvidenceItem,
    build_prompt,
    looks_like_timestamp,
    render_evidence,
    resolve_citations,
    split_sentences,
    strip_uncited,
)


def evidence(count: int = 3) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            marker=i,
            chunk_id=uuid.uuid4(),
            video_id=uuid.uuid4(),
            video_title=f"Video {i}",
            start_s=i * 100.0,
            end_s=i * 100.0 + 20.0,
            text=f"Evidence body number {i}",
        )
        for i in range(1, count + 1)
    ]


class TestCitationParsing:
    def test_matches_marker(self) -> None:
        assert CITATION_RE.findall("see [c_3] and [c_12]") == ["3", "12"]

    def test_ignores_prose_brackets(self) -> None:
        assert CITATION_RE.findall("[note] [c3] [c_] (c_4)") == []


class TestResolution:
    def test_valid_marker_resolves_to_database_timestamp(self) -> None:
        items = evidence()
        text, citations, fabricated = resolve_citations("The player jumps [c_2].", items)

        assert fabricated == []
        assert len(citations) == 1
        # The timestamp comes from the evidence record, never from the model.
        assert citations[0].start_s == items[1].start_s
        assert citations[0].chunk_id == items[1].chunk_id
        assert "[c_2]" in text

    def test_fabricated_marker_is_rejected_and_removed(self) -> None:
        """The core guarantee: an invented reference cannot reach the reader."""
        text, citations, fabricated = resolve_citations(
            "This is true [c_99].", evidence(count=3)
        )
        assert fabricated == [99]
        assert citations == []
        assert "[c_99]" not in text
        assert "99" not in text

    def test_mixed_valid_and_fabricated(self) -> None:
        text, citations, fabricated = resolve_citations(
            "Real claim [c_1] and invented claim [c_42].", evidence()
        )
        assert fabricated == [42]
        assert [c.marker for c in citations] == [1]
        assert "[c_1]" in text
        assert "[c_42]" not in text

    def test_repeated_marker_yields_one_citation(self) -> None:
        _, citations, _ = resolve_citations("A [c_1]. B [c_1]. C [c_1].", evidence())
        assert len(citations) == 1

    def test_citations_sorted_by_marker(self) -> None:
        _, citations, _ = resolve_citations("C [c_3] A [c_1] B [c_2]", evidence())
        assert [c.marker for c in citations] == [1, 2, 3]

    def test_zero_and_negative_lookalikes(self) -> None:
        """Marker 0 does not exist; a minus sign is not part of the pattern."""
        _, citations, fabricated = resolve_citations("x [c_0] y [c_-1]", evidence())
        assert citations == []
        assert fabricated == [0]

    def test_whitespace_tidied_after_removal(self) -> None:
        text, _, _ = resolve_citations("The claim [c_99] stands.", evidence())
        assert "  " not in text
        assert text == "The claim stands."

    def test_space_before_punctuation_removed(self) -> None:
        text, _, _ = resolve_citations("A claim [c_99].", evidence())
        assert text == "A claim."

    def test_no_citations_at_all(self) -> None:
        text, citations, fabricated = resolve_citations("Bare assertion.", evidence())
        assert (text, citations, fabricated) == ("Bare assertion.", [], [])

    def test_empty_evidence_rejects_everything(self) -> None:
        _, citations, fabricated = resolve_citations("Claim [c_1].", [])
        assert citations == []
        assert fabricated == [1]


class TestGroupedCitations:
    """Models group markers differently, and the parser must accept all of it.

    gemini-3.7-flash writes `[c_1] [c_2]`; gemini-2.5-flash writes
    `[c_1, c_2]`. Parsing only the first shape was silently catastrophic: zero
    citations found, every sentence stripped as unsupported, a correct answer
    turned into a refusal.
    """

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("A claim [c_1].", [1]),
            ("A claim [c_1, c_2, c_3].", [1, 2, 3]),
            ("A claim [c_1 , c_2].", [1, 2]),
            ("A claim [c_1; c_2].", [1, 2]),
            ("A claim [c_1][c_2].", [1, 2]),
            ("A claim [ c_1, c_2 ].", [1, 2]),
        ],
    )
    def test_accepts_every_grouping(self, text: str, expected: list[int]) -> None:
        _, citations, fabricated = resolve_citations(text, evidence(count=3))
        assert [c.marker for c in citations] == expected
        assert fabricated == []

    def test_normalises_to_one_marker_per_bracket(self) -> None:
        """Downstream code assumes the canonical shape."""
        text, _, _ = resolve_citations("A claim [c_1, c_2].", evidence())
        assert text == "A claim [c_1][c_2]."

    def test_fabricated_inside_a_group_is_still_rejected(self) -> None:
        text, citations, fabricated = resolve_citations(
            "A claim [c_1, c_99, c_2].", evidence(count=3)
        )
        assert fabricated == [99]
        assert [c.marker for c in citations] == [1, 2]
        assert "99" not in text

    def test_group_of_only_fabricated_markers_vanishes(self) -> None:
        text, citations, fabricated = resolve_citations(
            "A claim [c_98, c_99].", evidence(count=3)
        )
        assert citations == []
        assert fabricated == [98, 99]
        assert text == "A claim."

    def test_grouped_citation_survives_stripping(self) -> None:
        """The regression in full: grouped markers must count as cited."""
        text, _, _ = resolve_citations(
            "A prefab is a copy [c_1, c_2, c_3]. It makes enemies [c_2].", evidence(count=3)
        )
        kept, removed, total = strip_uncited(text)
        assert removed == 0, "grouped citations were not recognised as citations"
        assert total == 2


class TestUncitedStripping:
    def test_removes_unsupported_sentence(self) -> None:
        text = "Supported claim [c_1]. Unsupported claim from nowhere."
        kept, removed, total = strip_uncited(text)

        assert removed == 1
        assert total == 2
        assert "Unsupported" not in kept
        assert "[c_1]" in kept

    def test_keeps_all_cited(self) -> None:
        text = "First [c_1]. Second [c_2]."
        kept, removed, total = strip_uncited(text)
        assert (removed, total) == (0, 2)
        assert kept == text

    def test_single_sentence_is_left_alone(self) -> None:
        """A one-sentence answer is almost always the refusal; stripping it
        would leave the user with nothing."""
        text = "I could not find this in the indexed videos."
        kept, removed, _ = strip_uncited(text)
        assert kept == text
        assert removed == 0

    def test_entirely_uncited_multi_sentence_yields_nothing(self) -> None:
        kept, removed, total = strip_uncited("Claim one. Claim two. Claim three.")
        assert kept == ""
        assert (removed, total) == (3, 3)

    def test_sentence_starting_with_a_citation(self) -> None:
        kept, removed, _ = strip_uncited("[c_1] establishes this. Unsupported follow-up.")
        assert "[c_1]" in kept
        assert removed == 1


class TestSentenceSplitting:
    def test_basic(self) -> None:
        assert len(split_sentences("One. Two. Three.")) == 3

    def test_citation_stays_with_its_sentence(self) -> None:
        parts = split_sentences("First claim [c_1]. Second claim [c_2].")
        assert all(CITATION_RE.search(p) for p in parts)

    @pytest.mark.parametrize("text", ["", "   ", "\n"])
    def test_empty(self, text: str) -> None:
        assert split_sentences(text) == []


class TestTimestampDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "This happens at 12:34 in the video.",
            "See 1:02:33 for details.",
            "Explained at 45 seconds.",
            "It starts at 3 minutes.",
        ],
    )
    def test_flags_written_timestamps(self, text: str) -> None:
        assert looks_like_timestamp(text)

    @pytest.mark.parametrize(
        "text",
        [
            "The player jumps when space is pressed [c_1].",
            "Version 2.5 of the engine [c_2].",
            "Set the value to 100 [c_3].",
        ],
    )
    def test_does_not_flag_ordinary_numbers(self, text: str) -> None:
        assert not looks_like_timestamp(text)


class TestPrompt:
    def test_contains_no_timestamps(self) -> None:
        """The model cannot copy a timestamp it was never shown."""
        rendered = render_evidence(evidence(), multi_video=False)
        assert not looks_like_timestamp(rendered)
        assert "100.0" not in rendered
        assert "start_s" not in rendered

    def test_markers_are_present_and_sequential(self) -> None:
        rendered = render_evidence(evidence(count=4), multi_video=False)
        assert CITATION_RE.findall(rendered) == ["1", "2", "3", "4"]

    def test_video_title_only_when_multiple(self) -> None:
        items = evidence(count=2)
        assert "Video 1" not in render_evidence(items, multi_video=False)
        assert "Video 1" in render_evidence(items, multi_video=True)

    def test_on_screen_text_included(self) -> None:
        items = evidence(count=1)
        items[0].on_screen_text = "Rigidbody2D\npublic float speed"
        rendered = render_evidence(items, multi_video=False)
        assert "Rigidbody2D" in rendered
        assert "On screen" in rendered

    def test_topic_included(self) -> None:
        items = evidence(count=1)
        items[0].topic_title = "Physics, Colliders"
        assert "Physics, Colliders" in render_evidence(items, multi_video=False)

    def test_prompt_carries_the_question(self) -> None:
        prompt = build_prompt("How do I jump?", evidence(), multi_video=False)
        assert "How do I jump?" in prompt
        assert "[c_1]" in prompt
