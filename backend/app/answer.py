"""Evidence-backed answering.

**The rule: the model never writes a timestamp.**

Every claim EchoLens makes has to be traceable to a moment in a video, and the
failure users notice — the one that destroys trust fastest — is a confident
citation pointing at the wrong second. Prompting a model to "be accurate with
timestamps" does not prevent that; it just makes it rarer and harder to spot.

So timestamps are made structurally impossible to fabricate:

1. Retrieved evidence is presented with **opaque, per-request IDs** (`[c_3]`).
   The model never sees a timestamp it could copy, paraphrase or invent.
2. The model is required to cite by ID.
3. Afterwards, every `[c_N]` is parsed and **checked against the evidence set
   actually retrieved for this query**. Anything else is deleted.
4. Surviving IDs are resolved to `(video_id, start_s)` *from the database*.

A fabricated citation cannot survive step 3, and a real citation cannot carry a
wrong timestamp because the model never supplied one — the database did.

This does not stop the model misdescribing what a chunk says. It eliminates the
entire class of confidently-wrong timestamps, and it makes the remaining failure
mode inspectable, because every sentence points at the exact text it came from.

**The refusal path.** Retrieval always returns *something*. The cross-encoder
score is calibrated enough to tell when that something is worthless — over the
Phase 9 benchmark, answerable questions score a median 5.49 and never below
-1.75, while off-corpus ones never rise above -5.01 — so the system can decline
rather than dress up its least-bad match as an answer.

The floor sits at -3.0, in the gap between those distributions. It must not be
tightened by eye: at 0.0 it refused two questions the retriever had *already
found the answer to*. See docs/08-evaluation.md.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field

from app.pipeline.rerank import RELEVANCE_FLOOR
from app.search import Hit

logger = logging.getLogger(__name__)

# Opaque per-request handles. Short, so they cost few tokens and are easy for a
# model to reproduce exactly; per-request, so they leak nothing about the corpus.
#
# The canonical form is one marker per bracket, `[c_3]`, and everything
# downstream — sentence stripping, the frontend renderer — assumes it.
CITATION_RE = re.compile(r"\[c_(\d+)\]")

# What models *actually* emit. Grouping several markers in one bracket
# (`[c_1, c_2, c_3]`) is a perfectly reasonable rendering of "these sources
# support this claim", and different models differ: gemini-3.7-flash writes one
# per bracket, gemini-2.5-flash groups them.
#
# Parsing only the canonical form was silently catastrophic rather than merely
# lossy — zero citations were found, every sentence was stripped as unsupported,
# and a correct answer became a refusal. The parser must therefore accept the
# variants and normalise, not assume one model's habit.
_CITATION_GROUP = re.compile(r"\[\s*c_\d+(?:\s*[,;]\s*c_\d+)*\s*\]")
_MARKER_IN_GROUP = re.compile(r"c_(\d+)")

# Sentence splitter that keeps the trailing citation attached to its sentence.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")

SYSTEM_PROMPT = """You answer questions about video content using ONLY the \
evidence provided.

Rules, in order of importance:

1. Cite every factual claim with the evidence marker it came from, like [c_3]. \
Place the marker at the end of the sentence it supports. A sentence may carry \
several markers.
2. Use ONLY the provided evidence. Never use outside knowledge, and never \
invent an evidence marker that was not given to you.
3. NEVER write a timestamp, a time range, or a phrase like "at 5 minutes in". \
Timestamps are attached automatically from the markers you cite. Writing one \
yourself will be wrong.
4. If the evidence does not answer the question, say so plainly in one \
sentence. Do not speculate and do not pad.
5. Be concise. Two to four sentences is usually right. Do not repeat the \
question back.
"""


@dataclass
class EvidenceItem:
    """One retrieved chunk, as presented to the model."""

    marker: int  # the N in [c_N]
    chunk_id: uuid.UUID
    video_id: uuid.UUID
    video_title: str
    start_s: float
    end_s: float
    text: str
    # The child chunk — what was said in the span the timestamps describe.
    # `text` above is the wider parent passage the model reads. See `Citation`.
    quote: str = ""
    kind: str = "transcript"
    on_screen_text: str | None = None
    topic_title: str | None = None
    relevance: float | None = None


@dataclass
class Citation:
    """A marker the model used, resolved back to a moment."""

    marker: int
    chunk_id: uuid.UUID
    video_id: uuid.UUID
    video_title: str
    start_s: float
    end_s: float
    text: str
    # What was actually said between `start_s` and `end_s`.
    #
    # `text` is the *parent* passage — roughly a minute of surrounding argument,
    # included so the model can answer rather than guess. But the timestamps
    # are the child's, so `text` and `start_s` describe different spans. Quoting
    # `text` under that timestamp produces a paragraph attributed to a moment
    # inside it, which is a misquote however you look at it.
    quote: str = ""


@dataclass
class Answer:
    text: str
    citations: list[Citation] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None
    # Quality telemetry — the numbers worth watching in Phase 9.
    fabricated_citations: list[int] = field(default_factory=list)
    uncited_sentences: int = 0
    total_sentences: int = 0
    model: str | None = None

    @property
    def uncited_rate(self) -> float:
        return self.uncited_sentences / self.total_sentences if self.total_sentences else 0.0


def build_evidence(hits: list[Hit], *, max_items: int = 12) -> list[EvidenceItem]:
    """Turn retrieved hits into numbered evidence.

    Parents rather than children where available: the child is what matched and
    is precise about *when*, but the parent carries the surrounding argument the
    model needs to actually answer. The citation still resolves to the child's
    timestamp, so precision is not lost.

    **One entry per passage.** Several children usually share a parent — they
    are adjacent moments in the same stretch of speech — and expanding each of
    them to that parent produced the *same paragraph* several times over. On a
    two-minute video, twelve evidence slots carried three distinct passages;
    the model was shown one of them five times and cited all five, rendering a
    single four-word claim with eleven timestamps after it.

    Deduplicating costs nothing and fixes three things at once: the prompt
    stops wasting slots that could hold genuinely different evidence, the model
    is no longer nudged into over-citing by apparent corroboration that is only
    repetition, and the reader gets a citation list they can actually use.

    Hits arrive ranked, so the first occurrence of a passage is its best-scoring
    child — which is also the timestamp worth citing.
    """
    items: list[EvidenceItem] = []
    seen: set[tuple] = set()

    for hit in hits:
        if len(items) >= max_items:
            break
        # Identify the passage, not the hit. Parent boundaries where there is a
        # parent; the chunk itself otherwise.
        identity = (
            (hit.video_id, round(hit.parent_start_s, 2))
            if hit.parent_text and hit.parent_start_s is not None
            else (hit.video_id, hit.chunk_id)
        )
        if identity in seen:
            continue
        seen.add(identity)

        items.append(
            EvidenceItem(
                marker=len(items) + 1,
                chunk_id=hit.chunk_id,
                video_id=hit.video_id,
                video_title=hit.video_title,
                start_s=hit.start_s,
                end_s=hit.end_s,
                text=(hit.parent_text or hit.text).strip(),
                quote=hit.text.strip(),
                on_screen_text=hit.context.on_screen_text if hit.context else None,
                topic_title=hit.context.topic_title if hit.context else None,
                relevance=hit.rerank_score,
            )
        )
    return items


def render_evidence(items: list[EvidenceItem], *, multi_video: bool) -> str:
    """Format evidence for the prompt. Contains no timestamps, by design."""
    blocks: list[str] = []
    for item in items:
        header = f"[c_{item.marker}]"
        if multi_video:
            header += f" from \"{item.video_title}\""
        if item.topic_title:
            header += f" (section: {item.topic_title})"

        block = f"{header}\n{item.text}"
        if item.on_screen_text:
            shown = " / ".join(item.on_screen_text.split("\n"))[:200]
            block += f"\nOn screen at this moment: {shown}"
        blocks.append(block)

    return "\n\n".join(blocks)


def build_prompt(question: str, items: list[EvidenceItem], *, multi_video: bool) -> str:
    return (
        f"Question: {question}\n\n"
        f"Evidence:\n\n{render_evidence(items, multi_video=multi_video)}\n\n"
        f"Answer the question using only this evidence, citing markers like [c_1]."
    )


def resolve_citations(
    text: str, items: list[EvidenceItem]
) -> tuple[str, list[Citation], list[int]]:
    """Validate every marker against the evidence actually retrieved.

    Returns `(cleaned_text, citations, fabricated_markers)`. Markers the model
    invented are removed from the text entirely rather than rendered as dead
    links — a citation the reader cannot follow is worse than no citation.
    """
    by_marker = {item.marker: item for item in items}
    used: dict[int, Citation] = {}
    fabricated: list[int] = []

    def replace(match: re.Match[str]) -> str:
        """Validate a bracket, which may hold one marker or several."""
        kept: list[str] = []

        for raw in _MARKER_IN_GROUP.findall(match.group(0)):
            marker = int(raw)
            item = by_marker.get(marker)
            if item is None:
                fabricated.append(marker)
                continue  # drop it

            if marker not in used:
                used[marker] = Citation(
                    marker=marker,
                    chunk_id=item.chunk_id,
                    video_id=item.video_id,
                    video_title=item.video_title,
                    start_s=item.start_s,
                    end_s=item.end_s,
                    text=item.text,
                    quote=item.quote,
                )
            kept.append(f"[c_{marker}]")

        # Normalised to one marker per bracket, so everything downstream sees a
        # single shape regardless of which model produced the text.
        return "".join(kept)

    cleaned = _CITATION_GROUP.sub(replace, text)
    # Removing a marker can leave doubled spaces or a space before punctuation.
    cleaned = re.sub(r" {2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned).strip()

    citations = [used[m] for m in sorted(used)]
    return cleaned, citations, fabricated


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def strip_uncited(text: str) -> tuple[str, int, int]:
    """Delete sentences carrying no citation.

    Returns `(kept_text, removed_count, total_count)`. An uncited sentence is
    either outside knowledge or an unsupported inference; neither belongs in an
    evidence-backed answer.

    A single-sentence answer is left alone: it is almost always the refusal
    ("the evidence does not cover this"), and stripping it would leave nothing.
    """
    sentences = split_sentences(text)
    if len(sentences) <= 1:
        return text.strip(), 0, len(sentences)

    kept = [s for s in sentences if CITATION_RE.search(s)]
    removed = len(sentences) - len(kept)

    # If nothing survives, the answer was entirely unsupported. Say that rather
    # than returning an empty string.
    if not kept:
        return "", removed, len(sentences)

    return " ".join(kept), removed, len(sentences)


async def answer_question(
    session,
    question: str,
    *,
    video_ids: list[uuid.UUID] | None = None,
    kinds: list[str] | None = None,
    candidates: int = 12,
    relevance_floor: float = RELEVANCE_FLOOR,
    max_tokens: int = 1024,
) -> Answer:
    """Retrieve, decide whether to answer at all, then generate and validate.

    The refusal check happens *before* the model is called: if nothing retrieved
    is relevant, there is no prompt worth sending and no answer worth paying
    for.
    """
    from app.config import get_settings
    from app.pipeline.embedding import embed_query
    from app.pipeline.llm import get_provider
    from app.search import hybrid_search

    settings = get_settings()
    vector = await embed_query(
        question, model_name=settings.embedding_model, device=settings.embedding_device
    )
    result = await hybrid_search(
        session,
        question,
        vector,
        video_ids=video_ids,
        kinds=kinds,
        limit=candidates,
        rerank_candidates=max(candidates * 3, 30),
        with_context=True,
    )

    if not result.hits:
        return Answer(
            text="I could not find anything about that in the indexed videos.",
            refused=True,
            refusal_reason="no results",
        )

    # The calibrated score earns its keep here: retrieval always returns
    # something, and this is what distinguishes "found it" from "found the
    # least-bad thing".
    if result.top_relevance is not None and result.top_relevance < relevance_floor:
        items = build_evidence(result.hits)
        return Answer(
            text="I could not find this in the indexed videos.",
            evidence=items,
            refused=True,
            refusal_reason=(
                f"best match scored {result.top_relevance:.2f}, "
                f"below the relevance floor of {relevance_floor:.1f}"
            ),
        )

    items = build_evidence(result.hits)
    multi_video = len({i.video_id for i in items}) > 1
    prompt = build_prompt(question, items, multi_video=multi_video)

    provider = get_provider()
    completion = await provider.complete(
        SYSTEM_PROMPT, prompt, temperature=0.0, max_tokens=max_tokens
    )

    if looks_like_timestamp(completion.text):
        # Not corrected — the citation is authoritative regardless — but a
        # rising rate means the prompt is losing its grip on the model.
        logger.warning("Model wrote a timestamp despite instruction: %r", completion.text[:120])

    cleaned, citations, fabricated = resolve_citations(completion.text, items)
    if fabricated:
        logger.warning("Rejected %d fabricated citation(s): %s", len(fabricated), fabricated)

    final, removed, total = strip_uncited(cleaned)

    if not final:
        return Answer(
            text="I could not find this in the indexed videos.",
            evidence=items,
            refused=True,
            refusal_reason="no sentence in the generated answer was supported by evidence",
            fabricated_citations=fabricated,
            uncited_sentences=removed,
            total_sentences=total,
            model=completion.model,
        )

    # Stripping can orphan a citation that only appeared in a removed sentence.
    surviving = {int(m) for m in CITATION_RE.findall(final)}
    citations = [c for c in citations if c.marker in surviving]

    return Answer(
        text=final,
        citations=citations,
        evidence=items,
        fabricated_citations=fabricated,
        uncited_sentences=removed,
        total_sentences=total,
        model=completion.model,
    )


def looks_like_timestamp(text: str) -> bool:
    """Did the model write a timestamp despite being told not to?

    Only worth logging — the citation is authoritative either way — but a rising
    rate means the prompt is losing its grip on the model.
    """
    return bool(
        re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", text)
        or re.search(r"\bat\s+\d+\s*(seconds?|minutes?|hours?)\b", text, re.IGNORECASE)
    )
