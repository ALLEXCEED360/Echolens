"""Event and topic extraction.

Two independent halves, deliberately kept distinguishable in the data:

**Rule-derived events** — deterministic signals that fall out of what earlier
stages already produced. Keyframe change magnitude gives scene and slide
changes, transcript gaps give silences, OCR appearing gives text events. Cheap,
high precision, no model involved.

**Topic segmentation** — where the video changes subject. The design doc
anticipated an LLM pass here, and that remains the route to *rich* events
("the professor explains backpropagation while showing a diagram"). But
boundaries specifically do not need one: chunk embeddings already encode
meaning, and a subject change is a measurable dip in similarity between
consecutive spans.

That is TextTiling with embeddings substituted for lexical overlap. It is
deterministic, costs nothing beyond vectors already in the database, and sends
no data anywhere. Titles come from distinctive-term extraction (c-TF-IDF) for
the same reason. An LLM would write better titles; it would not find better
boundaries.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

# ─── Topic segmentation ────────────────────────────────────────────────────

# Comparison window, in chunks either side of a candidate boundary. Wider is
# smoother but blurs genuine short topics.
WINDOW = 4
# A topic shorter than this is a digression, not a subject change.
MIN_TOPIC_S = 90.0
# Boundary threshold as (mean + k * stdev) of the depth score.
DEPTH_K = 0.6

# Words that carry no topical signal in spoken tutorials. Deliberately short:
# aggressive stoplists start deleting domain vocabulary.
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "cant", "do",
    "does", "dont", "for", "from", "get", "gets", "go", "going", "gonna", "got", "had",
    "has", "have", "here", "how", "i", "if", "in", "into", "is", "it", "its", "just",
    "know", "let", "lets", "like", "ll", "make", "makes", "making", "me", "my", "no",
    "not", "now", "of", "off", "ok", "okay", "on", "one", "or", "our", "out", "over", "re",
    "right", "say", "see", "so", "some", "something", "take", "than", "that", "thats",
    "the", "their", "them", "then", "there", "these", "they", "thing", "things", "think",
    "this", "those", "to", "too", "up", "us", "use", "using", "ve", "want", "was", "we",
    "well", "were", "what", "when", "where", "which", "who", "why", "will", "with",
    "would", "yeah", "yes", "you", "your", "youre", "it's", "that's", "we're", "actually",
    "basically", "really", "very", "much", "more", "most", "also", "again"
})

_WORD = re.compile(r"[a-z][a-z0-9'+#-]{1,}")


@dataclass
class Segment:
    """Input unit for segmentation: an embedded transcript chunk."""

    start_s: float
    end_s: float
    text: str
    embedding: list[float]


@dataclass
class TopicSpan:
    position: int
    start_s: float
    end_s: float
    title: str
    keywords: list[str] = field(default_factory=list)
    boundary_strength: float = 0.0
    segment_indices: list[int] = field(default_factory=list)


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def coherence_scores(embeddings: np.ndarray, window: int = WINDOW) -> np.ndarray:
    """Similarity across each gap between consecutive chunks.

    Compares the mean of `window` chunks before the gap against `window` after,
    rather than adjacent chunks alone — single-chunk comparison is dominated by
    sentence-level noise and finds a boundary at almost every pause.
    """
    unit = _normalise(embeddings)
    n = len(unit)
    scores = np.zeros(max(n - 1, 0))

    for i in range(n - 1):
        left = unit[max(0, i - window + 1) : i + 1].mean(axis=0)
        right = unit[i + 1 : min(n, i + 1 + window)].mean(axis=0)
        denom = np.linalg.norm(left) * np.linalg.norm(right)
        scores[i] = float(left @ right / denom) if denom else 0.0

    return scores


def depth_scores(coherence: np.ndarray) -> np.ndarray:
    """How much of a valley each gap is.

    A low similarity only marks a boundary if it is low *relative to its
    surroundings* — quiet passages are uniformly less similar without changing
    subject. Depth measures the climb to the nearest peak on each side, which is
    the standard TextTiling formulation.
    """
    n = len(coherence)
    depth = np.zeros(n)

    for i in range(n):
        left = coherence[i]
        j = i
        while j > 0 and coherence[j - 1] >= coherence[j]:
            j -= 1
            left = max(left, coherence[j])

        right = coherence[i]
        k = i
        while k < n - 1 and coherence[k + 1] >= coherence[k]:
            k += 1
            right = max(right, coherence[k])

        depth[i] = (left - coherence[i]) + (right - coherence[i])

    return depth


def find_boundaries(
    segments: list[Segment],
    *,
    window: int = WINDOW,
    min_topic_s: float = MIN_TOPIC_S,
    depth_k: float = DEPTH_K,
) -> list[tuple[int, float]]:
    """Indices where a new topic starts, with the strength of each boundary."""
    if len(segments) < 2 * window:
        return []

    embeddings = np.asarray([s.embedding for s in segments], dtype=np.float32)
    coherence = coherence_scores(embeddings, window=window)
    depth = depth_scores(coherence)

    threshold = depth.mean() + depth_k * depth.std()
    candidates = [
        (i, float(depth[i]))
        for i in range(len(depth))
        if depth[i] > threshold
        # Local maximum: consecutive gaps above threshold describe one boundary.
        and (i == 0 or depth[i] >= depth[i - 1])
        and (i == len(depth) - 1 or depth[i] >= depth[i + 1])
    ]
    candidates.sort(key=lambda c: c[1], reverse=True)

    # Greedily accept the strongest boundaries that respect the minimum spacing,
    # so a cluster of dips inside one subject yields a single split.
    accepted: list[tuple[int, float]] = []
    for index, strength in candidates:
        boundary_time = segments[index + 1].start_s
        if all(
            abs(boundary_time - segments[other + 1].start_s) >= min_topic_s
            for other, _ in accepted
        ):
            accepted.append((index, strength))

    accepted.sort(key=lambda c: c[0])
    return accepted


# ─── Labelling ─────────────────────────────────────────────────────────────


def _tokenise(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2]


def label_topics(texts: list[str], top_n: int = 6) -> list[list[str]]:
    """Distinctive terms per span, via class-based TF-IDF.

    Plain term frequency would return "unity" for every topic in a Unity
    tutorial. Weighting by how *few* other spans use a term surfaces what makes
    each span different, which is what a label needs to convey.
    """
    if not texts:
        return []

    per_span = [Counter(_tokenise(t)) for t in texts]
    total_spans = len(per_span)
    document_frequency = Counter()
    for counts in per_span:
        document_frequency.update(counts.keys())

    labels: list[list[str]] = []
    for counts in per_span:
        length = sum(counts.values()) or 1
        scored = {
            # log((N+1)/(df+1)) rather than log(1 + N/df): the latter still
            # scores a term appearing in *every* span at log(2), which is enough
            # to win, so "unity" labelled every topic of a Unity tutorial. This
            # form goes to exactly zero when df == N, which is the intent.
            term: (freq / length) * math.log((total_spans + 1) / (document_frequency[term] + 1))
            for term, freq in counts.items()
            if freq > 1 or total_spans == 1
        }
        # With one span every term has df == N and scores zero, so fall back to
        # raw frequency rather than returning nothing.
        if total_spans == 1 or not any(scored.values()):
            scored = {t: f / length for t, f in counts.items()}

        ranked = sorted(scored.items(), key=lambda kv: -kv[1])
        labels.append([t for t, score in ranked[:top_n] if score > 0])

    return labels


def _titlecase(word: str) -> str:
    """Capitalise the first letter only.

    `str.title()` capitalises after every non-alpha character, turning "let's"
    into "Let'S" and "textmesh-pro" into "Textmesh-Pro".
    """
    return word[:1].upper() + word[1:] if word else word


def _title_from(keywords: list[str], fallback_index: int) -> str:
    if not keywords:
        return f"Section {fallback_index + 1}"
    # Three terms reads as a label; six reads as a tag cloud.
    return ", ".join(_titlecase(k) for k in keywords[:3])


def build_topics(
    segments: list[Segment],
    *,
    window: int = WINDOW,
    min_topic_s: float = MIN_TOPIC_S,
    depth_k: float = DEPTH_K,
) -> list[TopicSpan]:
    """Segment a video into topics and label them."""
    if not segments:
        return []

    boundaries = find_boundaries(
        segments, window=window, min_topic_s=min_topic_s, depth_k=depth_k
    )

    # Boundary index i means "a new topic starts at segment i+1".
    starts = [0] + [i + 1 for i, _ in boundaries]
    strengths = [0.0] + [s for _, s in boundaries]
    ends = starts[1:] + [len(segments)]

    texts = [" ".join(s.text for s in segments[a:b]) for a, b in zip(starts, ends, strict=True)]
    keyword_sets = label_topics(texts)

    topics: list[TopicSpan] = []
    for position, (a, b) in enumerate(zip(starts, ends, strict=True)):
        keywords = keyword_sets[position] if position < len(keyword_sets) else []
        topics.append(
            TopicSpan(
                position=position,
                start_s=segments[a].start_s,
                end_s=segments[b - 1].end_s,
                title=_title_from(keywords, position),
                keywords=keywords,
                boundary_strength=round(strengths[position], 4),
                segment_indices=list(range(a, b)),
            )
        )

    logger.info("Segmented %d chunks into %d topics", len(segments), len(topics))
    return topics


def build_topic_hierarchy(
    segments: list[Segment],
    *,
    coarse_depth_k: float = 1.5,
    coarse_min_topic_s: float = 300.0,
    fine_depth_k: float = 1.0,
    fine_min_topic_s: float = 120.0,
) -> tuple[list[TopicSpan], list[TopicSpan]]:
    """Two levels of segmentation: `(coarse, fine)`.

    The same signal read at two sensitivities. Coarse spans are chapters; fine
    ones are the steps inside them. Running the detector twice is cheaper and
    simpler than clustering one pass into a tree, and the levels stay
    independently tunable — which matters because the right granularity differs
    between a 40-minute lecture and a 6-hour tutorial.

    Each fine topic is assigned to the coarse span containing its midpoint, so
    the nesting is total even where boundaries do not coincide exactly.
    """
    coarse = build_topics(segments, depth_k=coarse_depth_k, min_topic_s=coarse_min_topic_s)
    fine = build_topics(segments, depth_k=fine_depth_k, min_topic_s=fine_min_topic_s)
    return coarse, fine


def locate_parent(parents: list[TopicSpan], child: TopicSpan) -> int | None:
    """Position of the coarse topic containing a fine topic's midpoint."""
    if not parents:
        return None
    midpoint = (child.start_s + child.end_s) / 2
    found = None
    for parent in parents:
        if parent.start_s <= midpoint:
            found = parent
        else:
            break
    return (found or parents[0]).position


# ─── Rule-derived events ───────────────────────────────────────────────────


@dataclass
class RuleEvent:
    type: str
    start_s: float
    end_s: float
    title: str
    confidence: float = 1.0
    evidence: list[dict] = field(default_factory=list)


# Keyframe hamming distance (out of 64) above which a change is a scene cut
# rather than incremental motion. The p90 of observed change was ~21.
SCENE_CHANGE_THRESHOLD = 22
# A gap in speech longer than this is worth marking as a break.
SILENCE_THRESHOLD_S = 20.0


def scene_events(
    keyframes: list[dict], *, threshold: int = SCENE_CHANGE_THRESHOLD
) -> list[RuleEvent]:
    """Large visual changes. `keyframes` are dicts of id/start_s/end_s/change."""
    events: list[RuleEvent] = []
    for frame in keyframes:
        change = frame.get("change") or 0
        if change < threshold:
            continue
        events.append(
            RuleEvent(
                type="scene_change",
                start_s=frame["start_s"],
                end_s=frame["end_s"],
                title="Scene change",
                # Saturating at double the threshold keeps this in 0.5–1.0.
                confidence=round(min(change / (threshold * 2), 1.0), 3),
                evidence=[
                    {"kind": "keyframe", "id": str(frame["id"]), "start_s": frame["start_s"]}
                ],
            )
        )
    return events


def silence_events(
    segments: list[tuple[float, float]], *, threshold_s: float = SILENCE_THRESHOLD_S
) -> list[RuleEvent]:
    """Gaps between consecutive transcript segments."""
    events: list[RuleEvent] = []
    for (_, end_a), (start_b, _) in zip(segments, segments[1:], strict=False):
        gap = start_b - end_a
        if gap < threshold_s:
            continue
        events.append(
            RuleEvent(
                type="silence",
                start_s=end_a,
                end_s=start_b,
                title=f"Silence ({int(gap)}s)",
                confidence=1.0,
            )
        )
    return events


def text_events(keyframes: list[dict]) -> list[RuleEvent]:
    """Frames where on-screen text appears after a frame that had none."""
    events: list[RuleEvent] = []
    previous_had_text = False

    for frame in keyframes:
        has_text = bool((frame.get("text") or "").strip())
        if has_text and not previous_had_text:
            snippet = " ".join((frame.get("text") or "").split())[:60]
            events.append(
                RuleEvent(
                    type="text_appeared",
                    start_s=frame["start_s"],
                    end_s=frame["end_s"],
                    title=f"Text: {snippet}" if snippet else "Text appeared",
                    confidence=0.8,
                    evidence=[
                        {"kind": "keyframe", "id": str(frame["id"]), "start_s": frame["start_s"]}
                    ],
                )
            )
        previous_had_text = has_text

    return events
