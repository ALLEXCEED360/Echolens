"""Hybrid retrieval.

Two retrievers over the same `chunks` table, fused by reciprocal rank:

  **semantic** — pgvector cosine over child embeddings. Handles paraphrase,
  which is most natural-language questions.

  **lexical** — Postgres full-text search over the generated `tsv` column.
  Non-negotiable: acronyms, symbols, proper nouns and API names are exactly
  where embeddings fail. A query for `RigidBody2D` or `∂L/∂w` needs exact terms.

**Why RRF and not weighted scores.** Cosine distance and `ts_rank` live on
incomparable scales, and tuning blend weights needs ground truth we will not
have until the Phase 9 benchmark. RRF only reads *rank*, so it needs no
calibration and cannot be skewed by one retriever's score distribution.

    score(d) = Σ  1 / (k + rank_r(d))          k = 60
              r∈R

**Why children rank but parents return.** Children are ~18 s, precise about
*when*. Parents are ~70 s, wide enough for an LLM to reason over. Ranking the
small unit and returning the large one is the whole point of the parent/child
split in the data model.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import Float, Text, cast, func, literal, select
from sqlalchemy.dialects.postgresql import REGCONFIG, TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Chunk, ChunkLevel, Event, Keyframe, Topic, Video

logger = logging.getLogger(__name__)

RRF_K = 60
CANDIDATES_PER_RETRIEVER = 50

# Measured in Phase 9, over the 46-question benchmark. The module docstring
# above argues RRF needs no calibration because it reads only rank — true of
# the *formula*, but not of the decision to give both lists equal say.
#
# Once the lexical arm gained an OR fallback it returns 50 candidates for every
# query, including ones where nothing matches well. At equal weight those
# near-misses outvoted good semantic hits: MRR 0.708 against 0.780 for semantic
# search on its own. Down-weighting recovers the lexical contribution without
# the noise — MRR 0.793, Recall@10 0.957.
#
# **The exact value is not meaningful.** Anything in 0.1-0.25 scores the same
# within one question out of 46, and these weights were chosen on the same
# questions they were scored on. What the benchmark supports is "much less than
# semantic", not "0.25 precisely".
DEFAULT_WEIGHTS = {"semantic": 1.0, "lexical": 0.25}

# How many top lexical hits are guaranteed a place in the rerank pool,
# regardless of fused score. Small on purpose: this is a safety net for
# exact-term queries semantic search cannot see, not a second ranking.
LEXICAL_GUARANTEED_SLOTS = 5


@dataclass
class TemporalEvidence:
    """What else the pipeline recorded around a hit's moment."""

    keyframe_id: uuid.UUID | None = None
    keyframe_time_s: float | None = None
    # OCR text from the frame on screen at that moment.
    on_screen_text: str | None = None
    events: list[dict] = field(default_factory=list)
    topic_title: str | None = None
    topic_start_s: float | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.on_screen_text or self.events or self.topic_title)


def _apply_filters(stmt, *, video_ids, kinds, time_range):
    """Metadata filters, shared by both retrievers so they cannot drift apart.

    These are hard filters, applied only where the user asked explicitly. Signals
    that are merely unreliable — speaker, once diarization lands — belong in
    ranking, never in a WHERE clause that silently discards correct results.

    **`None` and `[]` mean opposite things and must not be conflated.** `None` is
    "no scope, search everything"; `[]` is "a scope that contains nothing", which
    an empty collection produces. Testing truthiness makes an empty collection
    silently widen to the whole corpus — results that look entirely plausible
    and answer a different question than the one asked.
    """
    if video_ids is not None:
        stmt = stmt.where(Chunk.video_id.in_(video_ids))
    if kinds is not None:
        stmt = stmt.where(Chunk.kind.in_(kinds))
    if time_range:
        start, end = time_range
        stmt = stmt.where(Chunk.end_s >= start, Chunk.start_s <= end)
    return stmt


def _best_frame(keyframes, start_s: float, end_s: float, lo: float, hi: float):
    """The keyframe covering a hit, or the nearest one if none covers it.

    Overlap is measured against the hit's real span; the padded window only
    decides which frames are eligible. Ties break toward the earlier frame,
    which is the one whose content was on screen as the moment began.
    """
    eligible = [k for k in keyframes if k.start_s <= hi and k.end_s >= lo]
    if not eligible:
        return None

    def overlap(frame) -> float:
        return max(0.0, min(frame.end_s, end_s) - max(frame.start_s, start_s))

    best = max(eligible, key=overlap)
    if overlap(best) > 0:
        return best

    # Nothing genuinely overlaps — the hit falls between frames. Fall back to
    # whichever sits closest, rather than whichever happens to be first.
    midpoint = (start_s + end_s) / 2
    return min(eligible, key=lambda k: abs(k.time_s - midpoint))


@dataclass
class Hit:
    chunk_id: uuid.UUID
    video_id: uuid.UUID
    video_title: str
    start_s: float
    end_s: float
    text: str
    score: float
    # Which modality this came from: spoken transcript, or text read off the
    # screen. Absent from the API, the frontend had no way to tell a user that
    # `publc Rigidbody2o rbi` is OCR of a code editor rather than a garbled
    # transcript — so search looked broken when it was working correctly.
    kind: str = "transcript"
    semantic_rank: int | None = None
    lexical_rank: int | None = None
    parent_text: str | None = None
    parent_start_s: float | None = None
    parent_end_s: float | None = None
    # Cross-encoder relevance, when reranking ran. Calibrated: >2 is a genuine
    # match, <0 means nothing retrieved answers the query.
    rerank_score: float | None = None
    # Position before reranking, so a promotion is visible in the response.
    fused_rank: int | None = None
    context: TemporalEvidence | None = None

    @property
    def matched_by(self) -> list[str]:
        out = []
        if self.semantic_rank is not None:
            out.append("semantic")
        if self.lexical_rank is not None:
            out.append("lexical")
        return out


@dataclass
class SearchResult:
    query: str
    hits: list[Hit] = field(default_factory=list)
    semantic_candidates: int = 0
    lexical_candidates: int = 0
    fused_candidates: int = 0
    reranked: bool = False
    top_relevance: float | None = None


async def semantic_search(
    session: AsyncSession,
    embedding: list[float],
    *,
    video_ids: list[uuid.UUID] | None = None,
    kinds: list[str] | None = None,
    time_range: tuple[float, float] | None = None,
    limit: int = CANDIDATES_PER_RETRIEVER,
    level: ChunkLevel = ChunkLevel.CHILD,
) -> list[tuple[uuid.UUID, float]]:
    """Child chunks by cosine distance. Returns `(chunk_id, distance)`.

    `level` exists for the Phase 9 ablation that ranks parents directly. Only
    children carry embeddings in a normal index, so a parent-level call returns
    nothing unless something has populated them.
    """
    distance = Chunk.embedding.cosine_distance(embedding).label("distance")

    stmt = (
        select(Chunk.id, distance)
        .where(Chunk.level == level, Chunk.embedding.isnot(None))
        .order_by(distance)
        .limit(limit)
    )
    stmt = _apply_filters(stmt, video_ids=video_ids, kinds=kinds, time_range=time_range)

    rows = (await session.execute(stmt)).all()
    return [(r[0], float(r[1])) for r in rows]


async def lexical_search(
    session: AsyncSession,
    query: str,
    *,
    video_ids: list[uuid.UUID] | None = None,
    kinds: list[str] | None = None,
    time_range: tuple[float, float] | None = None,
    limit: int = CANDIDATES_PER_RETRIEVER,
    level: ChunkLevel = ChunkLevel.CHILD,
) -> list[tuple[uuid.UUID, float]]:
    """Child chunks by BM25-style ts_rank. Returns `(chunk_id, rank)`.

    `websearch_to_tsquery` is deliberate: it accepts quoted phrases, OR and -
    the way users already expect from a search box, and — unlike `to_tsquery` —
    it never raises a syntax error on arbitrary input.

    **It also ANDs every term, which on its own made this retriever useless.**
    The Phase 9 benchmark measured it: `"How do I access a Rigidbody in a Unity
    script?"` becomes `'access' & 'rigidbodi' & 'uniti' & 'script'`, which
    demands all four stems inside one ~17 s chunk. **30 of 46 benchmark
    questions returned zero lexical candidates**, and the handful that squeaked
    through were mostly incidental matches — which RRF then promoted, dragging
    fused Recall@1 *below* semantic search alone.

    So a strict pass runs first and keeps its ranks, then a relaxed pass tops
    up the remainder. The relaxed query is the strict one with its `&`
    operators rewritten to `|`, which preserves stemming and quoted phrases
    (those compile to `<->` and are left alone) instead of re-parsing user
    syntax by hand. `ts_rank_cd` still ranks chunks matching more of the query
    higher, so the strict results are not diluted — they are extended.
    """
    # The config argument is `regconfig`, not text. Without the cast SQLAlchemy
    # binds it as varchar and Postgres finds no matching function overload.
    strict = func.websearch_to_tsquery(cast(literal("english"), REGCONFIG), query)

    def _query(tsquery, exclude: list[uuid.UUID], remaining: int):
        rank = func.ts_rank_cd(Chunk.tsv, tsquery).cast(Float).label("rank")
        stmt = (
            select(Chunk.id, rank)
            .where(Chunk.level == level, Chunk.tsv.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(remaining)
        )
        if exclude:
            stmt = stmt.where(Chunk.id.notin_(exclude))
        return _apply_filters(stmt, video_ids=video_ids, kinds=kinds, time_range=time_range)

    rows = (await session.execute(_query(strict, [], limit))).all()
    results = [(r[0], float(r[1])) for r in rows]

    if len(results) < limit:
        relaxed = cast(
            func.replace(cast(strict, Text), " & ", " | "), TSQUERY
        )
        rows = (
            await session.execute(
                _query(relaxed, [cid for cid, _ in results], limit - len(results))
            )
        ).all()
        results.extend((r[0], float(r[1])) for r in rows)

    return results


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[uuid.UUID]],
    k: int = RRF_K,
    weights: dict[str, float] | None = None,
) -> dict[uuid.UUID, tuple[float, dict[str, int]]]:
    """Fuse ranked lists. Returns `id -> (score, {retriever: rank})`.

    Weights default to 1.0, which is plain RRF. `DEFAULT_WEIGHTS` is what
    `hybrid_search` actually passes, and why.
    """
    weights = weights or {}
    scores: dict[uuid.UUID, float] = {}
    ranks: dict[uuid.UUID, dict[str, int]] = {}

    for retriever, ids in ranked_lists.items():
        weight = weights.get(retriever, 1.0)
        for position, chunk_id in enumerate(ids):
            rank = position + 1
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (k + rank)
            ranks.setdefault(chunk_id, {})[retriever] = rank

    return {cid: (scores[cid], ranks[cid]) for cid in scores}


async def temporal_context(
    session: AsyncSession,
    hits: list[Hit],
    *,
    padding_s: float = 5.0,
) -> dict[uuid.UUID, TemporalEvidence]:
    """What else was happening around each hit.

    This is the retriever that makes the system multimodal rather than a
    transcript search with extra tables. A speech hit alone says "backpropagation
    was mentioned here"; joined with the frame on screen, the topic it sits in
    and the events around it, it says what was *happening*.

    Everything is fetched in three queries rather than per-hit, because a
    per-hit round trip is what turns a 200 ms search into a 2 s one.
    """
    if not hits:
        return {}

    by_video: dict[uuid.UUID, list[Hit]] = {}
    for hit in hits:
        by_video.setdefault(hit.video_id, []).append(hit)

    evidence = {h.chunk_id: TemporalEvidence() for h in hits}

    for video_id, video_hits in by_video.items():
        low = min(h.start_s for h in video_hits) - padding_s
        high = max(h.end_s for h in video_hits) + padding_s

        keyframes = (
            await session.execute(
                select(Keyframe)
                .where(
                    Keyframe.video_id == video_id,
                    Keyframe.end_s >= low,
                    Keyframe.start_s <= high,
                )
                .options(selectinload(Keyframe.ocr_blocks))
                .order_by(Keyframe.start_s)
            )
        ).scalars().all()

        events = (
            await session.execute(
                select(Event)
                .where(Event.video_id == video_id, Event.end_s >= low, Event.start_s <= high)
                .order_by(Event.start_s)
            )
        ).scalars().all()

        topics = (
            await session.execute(
                select(Topic)
                .where(
                    Topic.video_id == video_id,
                    Topic.depth == 0,
                    Topic.end_s >= low,
                    Topic.start_s <= high,
                )
                .order_by(Topic.start_s)
            )
        ).scalars().all()

        for hit in video_hits:
            bundle = evidence[hit.chunk_id]
            lo, hi = hit.start_s - padding_s, hit.end_s + padding_s

            # The frame that best *covers* the hit, not merely the first one
            # that brushes the padded window.
            #
            # Taking the first match in time order picked a frame with **zero**
            # overlap — touching the hit only at its boundary — while the frame
            # exactly spanning it sat next in the list. Padding widens the
            # search so a hit between two frames still finds one; it was never
            # meant to let a neighbour outrank the frame actually on screen.
            # The cost was a wrong thumbnail beside every result and, worse,
            # the wrong "On screen at this moment" text in the answer prompt.
            frame = _best_frame(keyframes, hit.start_s, hit.end_s, lo, hi)
            if frame is not None:
                bundle.keyframe_id = frame.id
                bundle.keyframe_time_s = frame.time_s
                bundle.on_screen_text = "\n".join(
                    b.text for b in sorted(
                        frame.ocr_blocks,
                        key=lambda b: ((b.bbox or {}).get("y1", 0), (b.bbox or {}).get("x1", 0)),
                    )
                ) or None

            bundle.events = [
                {"type": e.type, "title": e.title, "start_s": e.start_s}
                for e in events
                if e.start_s <= hi and e.end_s >= lo
            ][:5]

            topic = next((t for t in topics if t.start_s <= hit.start_s <= t.end_s), None)
            if topic is not None:
                bundle.topic_title = topic.title
                bundle.topic_start_s = topic.start_s

    return evidence


async def hybrid_search(
    session: AsyncSession,
    query: str,
    embedding: list[float],
    *,
    video_ids: list[uuid.UUID] | None = None,
    kinds: list[str] | None = None,
    time_range: tuple[float, float] | None = None,
    limit: int = 10,
    include_parents: bool = True,
    rerank_candidates: int = 0,
    with_context: bool = False,
) -> SearchResult:
    """Run both retrievers, fuse, optionally rerank, hydrate and expand.

    `rerank_candidates > 0` widens the pool taken from fusion before a
    cross-encoder reorders it. Reranking a set the size of the final result
    would be pointless — the value is in promoting from deeper than the fused
    top-k would ever show.
    """
    semantic = await semantic_search(
        session, embedding, video_ids=video_ids, kinds=kinds, time_range=time_range
    )
    lexical = await lexical_search(
        session, query, video_ids=video_ids, kinds=kinds, time_range=time_range
    )

    fused = reciprocal_rank_fusion(
        {
            "semantic": [cid for cid, _ in semantic],
            "lexical": [cid for cid, _ in lexical],
        },
        weights=DEFAULT_WEIGHTS,
    )
    if not fused:
        return SearchResult(query=query)

    pool = max(rerank_candidates, limit) if rerank_candidates else limit
    top = sorted(fused.items(), key=lambda kv: kv[1][0], reverse=True)[:pool]
    ids = [cid for cid, _ in top]

    # Guarantee the strongest exact-term matches reach the pool.
    #
    # Down-weighting lexical fixed one problem and created another. At weight
    # 0.25 the *best possible* lexical-only score is 0.25/(k+1) = 0.0041, while
    # the *worst* semantic score is 1/(k+50) = 0.0091 — so a chunk found only by
    # lexical cannot outrank any semantic result, however exact the match.
    #
    # Observed: a query for "launch codes" against a clip whose transcript says
    # "I want the launch codes, Mr. President" put that chunk at lexical rank 1,
    # absent from semantic, and **fused rank 51** — past the pool entirely. The
    # search returned ten unrelated OCR fragments and reported nothing relevant.
    #
    # Fusion still decides the *order*; this only decides who gets considered.
    # The cross-encoder is the arbiter, and it is well placed to reject a
    # keyword match that happens to be irrelevant.
    seen = set(ids)
    for chunk_id, _ in lexical[:LEXICAL_GUARANTEED_SLOTS]:
        if chunk_id not in seen and chunk_id in fused:
            ids.append(chunk_id)
            seen.add(chunk_id)
            top.append((chunk_id, fused[chunk_id]))

    # Hydrate children with their video title, and their parent if requested.
    parent = Chunk.__table__.alias("parent")
    stmt = (
        select(
            Chunk.id, Chunk.video_id, Video.title, Chunk.start_s, Chunk.end_s, Chunk.text,
            parent.c.text, parent.c.start_s, parent.c.end_s, Chunk.kind,
        )
        .join(Video, Video.id == Chunk.video_id)
        .outerjoin(parent, parent.c.id == Chunk.parent_id)
        .where(Chunk.id.in_(ids))
    )
    rows = {r[0]: r for r in (await session.execute(stmt)).all()}

    hits: list[Hit] = []
    for chunk_id, (score, ranks) in top:
        row = rows.get(chunk_id)
        if row is None:
            continue
        hits.append(
            Hit(
                chunk_id=row[0],
                video_id=row[1],
                video_title=row[2],
                start_s=row[3],
                end_s=row[4],
                text=row[5],
                score=score,
                semantic_rank=ranks.get("semantic"),
                lexical_rank=ranks.get("lexical"),
                parent_text=row[6] if include_parents else None,
                parent_start_s=row[7] if include_parents else None,
                parent_end_s=row[8] if include_parents else None,
                kind=str(getattr(row[9], "value", row[9])),
            )
        )

    reranked = False
    if rerank_candidates and hits:
        from app.pipeline.rerank import rerank as cross_encode

        scores = await cross_encode(query, [h.text for h in hits])
        for position, (hit, score) in enumerate(zip(hits, scores, strict=True)):
            hit.rerank_score = score
            # Captured by position, not `list.index`: dataclasses compare by
            # value, so identical-text hits would resolve to the same rank.
            hit.fused_rank = position + 1
        hits.sort(
            key=lambda h: h.rerank_score if h.rerank_score is not None else float("-inf"),
            reverse=True,
        )
        reranked = True

    hits = hits[:limit]

    if with_context and hits:
        evidence = await temporal_context(session, hits)
        for hit in hits:
            hit.context = evidence.get(hit.chunk_id)

    return SearchResult(
        query=query,
        hits=hits,
        semantic_candidates=len(semantic),
        lexical_candidates=len(lexical),
        fused_candidates=len(fused),
        reranked=reranked,
        # The reranker's top score doubles as a "did we find anything" signal.
        top_relevance=hits[0].rerank_score if reranked and hits else None,
    )
