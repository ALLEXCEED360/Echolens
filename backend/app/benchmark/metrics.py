"""Retrieval metrics.

Every number in docs/08-evaluation.md is produced here, so this module is
written to be read and checked rather than trusted. Each function takes a
ranked relevance vector — `[False, True, False]` means the second result was
correct — and nothing else. No database, no configuration, no hidden state.

**On the name "Recall@k".** With one gold span per question, "what fraction of
relevant items did we retrieve" and "did we retrieve the relevant item" are the
same quantity, and the second is what docs/03-retrieval.md specifies. It is
strictly a *hit rate*; the alias is provided because that is the honest name and
the two must not be reported as if they were different measurements.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import median

__all__ = [
    "average_precision",
    "dcg",
    "first_relevant_rank",
    "median_abs_error",
    "ndcg_at_k",
    "percentile",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
]


def first_relevant_rank(relevance: Sequence[bool]) -> int | None:
    """1-based rank of the first correct result, or None if there is none."""
    for position, is_relevant in enumerate(relevance):
        if is_relevant:
            return position + 1
    return None


def recall_at_k(relevance: Sequence[bool], k: int) -> float:
    """Was a correct span retrieved in the top *k*? 1.0 or 0.0 per query.

    Averaged over the question set this is the headline number: the share of
    questions whose answer the user could actually reach.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    return 1.0 if any(relevance[:k]) else 0.0


def precision_at_k(relevance: Sequence[bool], k: int) -> float:
    """Share of the top *k* that were correct.

    Reported alongside recall because they pull in opposite directions and a
    system can be tuned to look good on either alone. Note that with a single
    gold span, precision is bounded well below 1.0 whenever *k* exceeds the
    number of chunks overlapping that span — it measures how tightly results
    cluster on the answer, not how often the answer was found.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    window = relevance[:k]
    if not window:
        return 0.0
    return sum(window) / len(window)


def reciprocal_rank(relevance: Sequence[bool]) -> float:
    """1 / rank of the first correct result; 0.0 if none was found.

    Averaged, this is MRR. It rewards putting the answer *first* rather than
    merely somewhere in the list, which is what matters when the top hit is the
    one the user clicks and the one the answerer cites.
    """
    rank = first_relevant_rank(relevance)
    return 1.0 / rank if rank else 0.0


def average_precision(relevance: Sequence[bool]) -> float:
    """Mean of precision@i over every position holding a correct result."""
    hits = 0
    total = 0.0
    for position, is_relevant in enumerate(relevance):
        if is_relevant:
            hits += 1
            total += hits / (position + 1)
    return total / hits if hits else 0.0


def dcg(relevance: Sequence[bool], k: int) -> float:
    """Discounted cumulative gain with binary gains."""
    return sum(
        1.0 / math.log2(position + 2)
        for position, is_relevant in enumerate(relevance[:k])
        if is_relevant
    )


def ndcg_at_k(relevance: Sequence[bool], k: int) -> float:
    """nDCG@k against the ideal ordering *of the results actually retrieved*.

    The ideal is every correct result retrieved, moved to the front. This is
    the standard choice when the full set of relevant documents is unknown, and
    it is the situation here: many chunks could arguably overlap a gold span,
    so an oracle count does not exist. The consequence is that nDCG cannot
    punish a correct result that was never retrieved at all — recall@k is the
    metric that does that, which is why both are reported.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    ideal = dcg(sorted(relevance, reverse=True), k)
    return dcg(relevance, k) / ideal if ideal else 0.0


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile, `p` in [0, 100].

    `statistics.quantiles` is deliberately not used: it defaults to a different
    estimator, and p95 over the ~50 measurements a benchmark run produces is
    sensitive enough to the choice that the two disagree visibly. Pinning the
    definition here keeps reported latencies comparable across runs.
    """
    if not values:
        raise ValueError("percentile of an empty sequence")
    if not 0 <= p <= 100:
        raise ValueError("p must be in [0, 100]")

    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])

    position = (len(ordered) - 1) * (p / 100.0)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    return float(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


def median_abs_error(predicted: Sequence[float], actual: Sequence[float]) -> float:
    """Median |predicted - actual|.

    Median rather than mean: a single result landing in the wrong hour would
    otherwise dominate a statistic meant to describe the typical case.
    """
    if len(predicted) != len(actual):
        raise ValueError("predicted and actual must be the same length")
    if not predicted:
        raise ValueError("median of an empty sequence")
    return float(median(abs(p - a) for p, a in zip(predicted, actual, strict=True)))
