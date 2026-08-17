"""Metric correctness.

Every headline number in docs/08-evaluation.md is produced by this module, so a
bug here does not cause a visible failure — it causes a plausible wrong number
that gets written into a document and quoted later. These tests check the
metrics against values worked out by hand rather than against the
implementation's own output.
"""

from __future__ import annotations

import math

import pytest

from app.benchmark.metrics import (
    average_precision,
    dcg,
    first_relevant_rank,
    median_abs_error,
    ndcg_at_k,
    percentile,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

T, F = True, False


class TestFirstRelevantRank:
    @pytest.mark.parametrize(
        ("relevance", "expected"),
        [([T, F, F], 1), ([F, T, F], 2), ([F, F, T], 3), ([F, F, F], None), ([], None)],
    )
    def test_rank(self, relevance: list[bool], expected: int | None) -> None:
        assert first_relevant_rank(relevance) == expected


class TestRecall:
    def test_hit_inside_cutoff(self) -> None:
        assert recall_at_k([F, F, T, F], 3) == 1.0

    def test_hit_outside_cutoff(self) -> None:
        """The distinction the metric exists for: rank 4 is not a top-3 hit."""
        assert recall_at_k([F, F, F, T], 3) == 0.0

    def test_k_larger_than_result_list(self) -> None:
        assert recall_at_k([F, T], 10) == 1.0

    def test_no_results_at_all(self) -> None:
        assert recall_at_k([], 5) == 0.0

    def test_rejects_nonsense_cutoff(self) -> None:
        with pytest.raises(ValueError):
            recall_at_k([T], 0)


class TestPrecision:
    def test_half_correct(self) -> None:
        assert precision_at_k([T, F, T, F], 4) == 0.5

    def test_truncates_to_k(self) -> None:
        assert precision_at_k([T, T, F, F], 2) == 1.0

    def test_short_list_divides_by_actual_length(self) -> None:
        """Two results, one correct, is precision 0.5 — not 0.2 at k=5.

        Dividing by k would silently punish a variant for returning fewer
        results than the cutoff, which is a different property entirely.
        """
        assert precision_at_k([T, F], 5) == 0.5

    def test_empty(self) -> None:
        assert precision_at_k([], 5) == 0.0


class TestReciprocalRank:
    @pytest.mark.parametrize(
        ("relevance", "expected"),
        [([T], 1.0), ([F, T], 0.5), ([F, F, T], 1 / 3), ([F, F, F], 0.0)],
    )
    def test_values(self, relevance: list[bool], expected: float) -> None:
        assert reciprocal_rank(relevance) == pytest.approx(expected)

    def test_only_the_first_hit_counts(self) -> None:
        assert reciprocal_rank([F, T, T, T]) == pytest.approx(0.5)


class TestAveragePrecision:
    def test_worked_example(self) -> None:
        # Hits at ranks 1 and 3: (1/1 + 2/3) / 2
        assert average_precision([T, F, T]) == pytest.approx((1.0 + 2 / 3) / 2)

    def test_no_hits(self) -> None:
        assert average_precision([F, F]) == 0.0


class TestDCG:
    def test_first_position_is_undiscounted(self) -> None:
        assert dcg([T], 1) == pytest.approx(1.0)

    def test_second_position_discount(self) -> None:
        assert dcg([F, T], 2) == pytest.approx(1 / math.log2(3))

    def test_respects_cutoff(self) -> None:
        assert dcg([F, F, T], 2) == 0.0


class TestNDCG:
    def test_perfect_ordering_is_one(self) -> None:
        assert ndcg_at_k([T, T, F], 3) == pytest.approx(1.0)

    def test_worst_ordering_penalised(self) -> None:
        assert ndcg_at_k([F, T], 2) == pytest.approx((1 / math.log2(3)) / 1.0)

    def test_no_relevant_results_is_zero_not_nan(self) -> None:
        """Ideal DCG is 0 here; the guard is what stops a divide-by-zero."""
        assert ndcg_at_k([F, F], 2) == 0.0

    def test_worked_example(self) -> None:
        # Hits at ranks 2 and 3; ideal puts them at 1 and 2.
        actual = 1 / math.log2(3) + 1 / math.log2(4)
        ideal = 1 / math.log2(2) + 1 / math.log2(3)
        assert ndcg_at_k([F, T, T], 3) == pytest.approx(actual / ideal)


class TestPercentile:
    def test_median_of_odd_count(self) -> None:
        assert percentile([1, 2, 3], 50) == 2.0

    def test_median_of_even_count_interpolates(self) -> None:
        assert percentile([1, 2, 3, 4], 50) == 2.5

    def test_bounds(self) -> None:
        values = [10, 20, 30, 40]
        assert percentile(values, 0) == 10.0
        assert percentile(values, 100) == 40.0

    def test_single_value(self) -> None:
        assert percentile([7.5], 95) == 7.5

    def test_input_is_not_mutated(self) -> None:
        """The harness reuses its latency list for p50 and p95."""
        values = [3.0, 1.0, 2.0]
        percentile(values, 50)
        assert values == [3.0, 1.0, 2.0]

    def test_worked_interpolation(self) -> None:
        # position = (5-1) * 0.95 = 3.8 -> between index 3 (4) and 4 (5)
        assert percentile([1, 2, 3, 4, 5], 95) == pytest.approx(4.8)

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            percentile([], 50)

    @pytest.mark.parametrize("p", [-1, 101])
    def test_rejects_out_of_range(self, p: float) -> None:
        with pytest.raises(ValueError):
            percentile([1, 2], p)


class TestMedianAbsError:
    def test_worked_example(self) -> None:
        # errors 2, 5, 0 -> sorted [0, 2, 5] -> median 2
        assert median_abs_error([10, 20, 30], [12, 25, 30]) == 2.0

    def test_ignores_a_single_outlier(self) -> None:
        """Why median and not mean: one wrong hour must not set the headline."""
        assert median_abs_error([1, 2, 3, 4, 9999], [1, 2, 3, 4, 0]) == 0.0

    def test_rejects_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError):
            median_abs_error([1, 2], [1])

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            median_abs_error([], [])
