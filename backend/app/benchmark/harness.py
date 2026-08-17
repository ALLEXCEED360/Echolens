"""Running the benchmark.

Each *variant* is one retrieval configuration, and every variant answers the
same questions over the same corpus so the differences between them are
attributable to the configuration and nothing else.

The variants exist to answer the questions docs/04-roadmap.md says make this
engineering rather than assembly:

  `lexical` / `semantic`   — does either retriever alone suffice?
  `hybrid`                 — does fusing them beat the better one?
  `hybrid+rerank`          — does the cross-encoder earn its 400 ms?
  `parent-direct`          — is ranking small units and returning large ones
                             better than ranking the large ones?

`parent-direct` needs parent embeddings, which a normal index does not have.
They are computed **in-process and never written to the database**: adding 335
vectors at a second granularity to a shared HNSW index would change the
behaviour of every other variant through post-filtering recall loss, which is
precisely the kind of contamination that makes an ablation meaningless.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.benchmark.dataset import Dataset, Question
from app.benchmark.metrics import (
    median_abs_error,
    ndcg_at_k,
    percentile,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from app.models import Chunk, ChunkLevel
from app.search import (
    DEFAULT_WEIGHTS,
    lexical_search,
    reciprocal_rank_fusion,
    semantic_search,
)

logger = logging.getLogger(__name__)

CUTOFFS = (1, 5, 10)
RETRIEVE_K = 10
RERANK_POOL = 30

VARIANTS = (
    "lexical",
    "semantic",
    "hybrid-equal",
    "hybrid",
    "hybrid+rerank",
    "parent-direct",
)


@dataclass
class Retrieved:
    chunk_id: uuid.UUID
    start_s: float
    end_s: float
    text: str


@dataclass
class QueryOutcome:
    question_id: str
    category: str
    relevance: list[bool]
    latency_ms: float
    top_start_s: float | None
    anchor_s: float | None
    span_widths: list[float]
    top_score: float | None = None


@dataclass
class VariantReport:
    variant: str
    n: int
    recall: dict[int, float] = field(default_factory=dict)
    precision: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    ndcg_at_10: float = 0.0
    median_start_error_s: float | None = None
    start_error_n: int = 0
    mean_span_width_s: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    by_category: dict[str, float] = field(default_factory=dict)

    def row(self) -> str:
        err = (
            f"{self.median_start_error_s:.1f}" if self.median_start_error_s is not None else "—"
        )
        return (
            f"| {self.variant:14} | {self.recall.get(1, 0):.3f} | {self.recall.get(5, 0):.3f} "
            f"| {self.recall.get(10, 0):.3f} | {self.mrr:.3f} | {self.ndcg_at_10:.3f} "
            f"| {err} | {self.mean_span_width_s:.0f} | {self.p50_ms:.0f} | {self.p95_ms:.0f} |"
        )


class ParentIndex:
    """In-memory exact cosine search over parent chunks.

    Exact rather than approximate, and that is not a shortcut: at a few hundred
    parents a brute-force dot product is both instant and free of the recall
    loss an ANN index introduces. Giving one variant an approximate index and
    another an exact one would put a confound straight through the middle of the
    comparison.
    """

    def __init__(self, ids: list[uuid.UUID], matrix: np.ndarray) -> None:
        self.ids = ids
        self.matrix = matrix

    @classmethod
    async def build(cls, session: AsyncSession, video_ids: Sequence[uuid.UUID]) -> ParentIndex:
        from app.pipeline.embedding import embed_documents

        rows = (
            await session.execute(
                select(Chunk.id, Chunk.text)
                .where(Chunk.level == ChunkLevel.PARENT, Chunk.video_id.in_(list(video_ids)))
                .order_by(Chunk.video_id, Chunk.start_s)
            )
        ).all()
        if not rows:
            return cls([], np.zeros((0, 0), dtype=np.float32))

        vectors = await embed_documents([r[1] for r in rows])
        # `embed_documents` normalises, so cosine similarity is a dot product.
        return cls([r[0] for r in rows], np.asarray(vectors, dtype=np.float32))

    def search(self, embedding: list[float], limit: int) -> list[tuple[uuid.UUID, float]]:
        if not self.ids:
            return []
        query = np.asarray(embedding, dtype=np.float32)
        scores = self.matrix @ query
        top = np.argsort(-scores)[:limit]
        return [(self.ids[i], float(scores[i])) for i in top]


async def _hydrate(
    session: AsyncSession, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Retrieved]:
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(Chunk.id, Chunk.start_s, Chunk.end_s, Chunk.text).where(
                Chunk.id.in_(list(ids))
            )
        )
    ).all()
    return {r[0]: Retrieved(chunk_id=r[0], start_s=r[1], end_s=r[2], text=r[3]) for r in rows}


async def retrieve(
    session: AsyncSession,
    variant: str,
    query: str,
    embedding: list[float],
    *,
    parent_index: ParentIndex | None = None,
    limit: int = RETRIEVE_K,
) -> tuple[list[Retrieved], float | None]:
    """Run one variant. Returns ranked results and the top score where defined."""
    if variant == "parent-direct":
        if parent_index is None:
            raise ValueError("parent-direct requires a parent index")
        semantic = parent_index.search(embedding, limit=50)
        lexical = await lexical_search(session, query, level=ChunkLevel.PARENT)
        ranked = _fuse(semantic, lexical)
    elif variant == "lexical":
        ranked = [cid for cid, _ in await lexical_search(session, query)]
    elif variant == "semantic":
        ranked = [cid for cid, _ in await semantic_search(session, embedding)]
    elif variant in ("hybrid", "hybrid+rerank", "hybrid-equal"):
        semantic = await semantic_search(session, embedding)
        lexical = await lexical_search(session, query)
        # `hybrid-equal` is plain RRF, the configuration this project shipped
        # before Phase 9 measured it. Kept as a variant so the change is
        # visible in the ablation table rather than only in a commit message.
        ranked = _fuse(semantic, lexical, equal=variant == "hybrid-equal")
    else:
        raise ValueError(f"Unknown variant {variant!r}")

    pool = RERANK_POOL if variant == "hybrid+rerank" else limit
    hydrated = await _hydrate(session, ranked[:pool])
    results = [hydrated[cid] for cid in ranked[:pool] if cid in hydrated]

    top_score = None
    if variant == "hybrid+rerank" and results:
        from app.pipeline.rerank import rerank as cross_encode

        scores = await cross_encode(query, [r.text for r in results])
        order = sorted(zip(results, scores, strict=True), key=lambda p: p[1], reverse=True)
        results = [r for r, _ in order]
        top_score = order[0][1] if order else None

    return results[:limit], top_score


def _fuse(
    semantic: list[tuple[uuid.UUID, float]],
    lexical: list[tuple[uuid.UUID, float]],
    *,
    equal: bool = False,
) -> list[uuid.UUID]:
    fused = reciprocal_rank_fusion(
        {"semantic": [c for c, _ in semantic], "lexical": [c for c, _ in lexical]},
        weights=None if equal else DEFAULT_WEIGHTS,
    )
    return [cid for cid, _ in sorted(fused.items(), key=lambda kv: kv[1][0], reverse=True)]


async def run_variant(
    session: AsyncSession,
    variant: str,
    questions: Sequence[Question],
    embeddings: dict[str, list[float]],
    *,
    parent_index: ParentIndex | None = None,
) -> tuple[VariantReport, list[QueryOutcome]]:
    outcomes: list[QueryOutcome] = []

    for question in questions:
        started = time.perf_counter()
        results, top_score = await retrieve(
            session,
            variant,
            question.question,
            embeddings[question.id],
            parent_index=parent_index,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0

        outcomes.append(
            QueryOutcome(
                question_id=question.id,
                category=question.category,
                relevance=[question.overlaps(r.start_s, r.end_s) for r in results],
                latency_ms=latency_ms,
                top_start_s=results[0].start_s if results else None,
                anchor_s=question.anchor_s,
                span_widths=[r.end_s - r.start_s for r in results],
                top_score=top_score,
            )
        )

    return summarise(variant, outcomes), outcomes


def summarise(variant: str, outcomes: Sequence[QueryOutcome]) -> VariantReport:
    """Aggregate per-query outcomes into the reported numbers."""
    report = VariantReport(variant=variant, n=len(outcomes))
    if not outcomes:
        return report

    for k in CUTOFFS:
        report.recall[k] = sum(recall_at_k(o.relevance, k) for o in outcomes) / len(outcomes)
        report.precision[k] = sum(
            precision_at_k(o.relevance, k) for o in outcomes
        ) / len(outcomes)

    report.mrr = sum(reciprocal_rank(o.relevance) for o in outcomes) / len(outcomes)
    report.ndcg_at_10 = sum(ndcg_at_k(o.relevance, 10) for o in outcomes) / len(outcomes)

    # Timestamp error is measured only where the top result was correct *and*
    # the question carries a child-level anchor. Over wrong results it would
    # report the distance between two unrelated moments, describing the corpus
    # rather than the system; without an anchor it would compare a retrieved
    # start against a parent boundary, which scores parent-level retrieval at
    # zero by construction. See Question.anchor_s.
    correct_top = [
        o
        for o in outcomes
        if o.relevance
        and o.relevance[0]
        and o.top_start_s is not None
        and o.anchor_s is not None
    ]
    report.start_error_n = len(correct_top)
    if correct_top:
        report.median_start_error_s = median_abs_error(
            [o.top_start_s for o in correct_top],  # type: ignore[misc]
            [o.anchor_s for o in correct_top],  # type: ignore[misc]
        )

    widths = [w for o in outcomes for w in o.span_widths]
    report.mean_span_width_s = sum(widths) / len(widths) if widths else 0.0

    latencies = [o.latency_ms for o in outcomes]
    report.p50_ms = percentile(latencies, 50)
    report.p95_ms = percentile(latencies, 95)

    categories = {o.category for o in outcomes}
    for category in sorted(categories):
        subset = [o for o in outcomes if o.category == category]
        report.by_category[category] = sum(
            recall_at_k(o.relevance, 5) for o in subset
        ) / len(subset)

    return report


async def embed_questions(questions: Sequence[Question]) -> dict[str, list[float]]:
    """Embed every question once and reuse across variants.

    Re-embedding per variant would put identical GPU work inside each variant's
    latency and make the retrieval differences harder to see, without changing
    any ranking.
    """
    from app.pipeline.embedding import embed_query

    return {q.id: await embed_query(q.question) for q in questions}


async def run_all(
    session: AsyncSession,
    dataset: Dataset,
    *,
    variants: Sequence[str] = VARIANTS,
) -> dict[str, tuple[VariantReport, list[QueryOutcome]]]:
    questions = dataset.scorable
    if not questions:
        raise ValueError("No verified, answerable questions to score")

    embeddings = await embed_questions(questions)

    parent_index = None
    if "parent-direct" in variants:
        video_ids = sorted({q.video_id for q in questions if q.video_id}, key=str)
        parent_index = await ParentIndex.build(session, video_ids)

    out = {}
    for variant in variants:
        logger.info("Running variant %s over %d questions", variant, len(questions))
        out[variant] = await run_variant(
            session, variant, questions, embeddings, parent_index=parent_index
        )
    return out


async def sweep_fusion(
    session: AsyncSession,
    dataset: Dataset,
    embeddings: dict[str, list[float]],
    *,
    weights: Sequence[float] = (0.0, 0.1, 0.25, 0.5, 0.75, 1.0),
    ks: Sequence[int] = (10, 30, 60, 120),
) -> list[dict]:
    """Sweep the lexical weight and the RRF constant.

    docs/03-retrieval.md chose unweighted RRF explicitly because "tuning those
    weights is a rabbit hole with no ground truth to tune against until Phase
    9". There is ground truth now, so the choice can be measured instead of
    assumed.

    **Read the result as indicative, not settled.** These weights are selected
    on the same 46 questions they are scored on, which is exactly the setup that
    produces numbers that do not survive contact with new data. A held-out split
    is what would make a recommendation here safe, and 46 questions cannot spare
    one.
    """
    questions = dataset.scorable

    # Retrieve once; the sweep only changes how the two lists are combined.
    per_question: dict[str, tuple[list[uuid.UUID], list[uuid.UUID]]] = {}
    for question in questions:
        semantic = await semantic_search(session, embeddings[question.id])
        lexical = await lexical_search(session, question.question)
        per_question[question.id] = ([c for c, _ in semantic], [c for c, _ in lexical])

    all_ids = {cid for pair in per_question.values() for lst in pair for cid in lst}
    spans = await _hydrate(session, sorted(all_ids, key=str))

    rows = []
    for k in ks:
        for weight in weights:
            outcomes = []
            for question in questions:
                semantic_ids, lexical_ids = per_question[question.id]
                scores: dict[uuid.UUID, float] = {}
                for rank, cid in enumerate(semantic_ids, start=1):
                    scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
                for rank, cid in enumerate(lexical_ids, start=1):
                    scores[cid] = scores.get(cid, 0.0) + weight / (k + rank)

                ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:RETRIEVE_K]
                relevance = [
                    question.overlaps(spans[cid].start_s, spans[cid].end_s)
                    for cid, _ in ranked
                    if cid in spans
                ]
                outcomes.append(relevance)

            rows.append(
                {
                    "k": k,
                    "lexical_weight": weight,
                    "recall_at_1": sum(recall_at_k(r, 1) for r in outcomes) / len(outcomes),
                    "recall_at_5": sum(recall_at_k(r, 5) for r in outcomes) / len(outcomes),
                    "recall_at_10": sum(recall_at_k(r, 10) for r in outcomes) / len(outcomes),
                    "mrr": sum(reciprocal_rank(r) for r in outcomes) / len(outcomes),
                    "ndcg_at_10": sum(ndcg_at_k(r, 10) for r in outcomes) / len(outcomes),
                }
            )
    return rows
