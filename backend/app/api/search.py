"""Search endpoints."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.collections import resolve_video_ids
from app.concepts import build_timeline
from app.config import Settings, get_settings
from app.db import get_session
from app.models import Chunk, ChunkKind, ChunkLevel
from app.pipeline.embedding import embed_query
from app.schemas import (
    ConceptTimelineOut,
    OccurrenceOut,
    SearchHit,
    SearchResponse,
    TemporalContext,
    VideoTrackOut,
)
from app.search import hybrid_search

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchResponse, summary="Hybrid semantic + keyword search")
async def search(
    q: str = Query(..., min_length=2, max_length=512),
    video_id: uuid.UUID | None = Query(None, description="Restrict to one video"),
    collection_id: uuid.UUID | None = Query(
        None, description="Restrict to one collection (ignored if video_id is given)"
    ),
    kinds: str | None = Query(
        None, description="Comma-separated chunk kinds: transcript, ocr, caption, event"
    ),
    start_s: float | None = Query(None, ge=0, description="Only match after this time"),
    end_s: float | None = Query(None, ge=0, description="Only match before this time"),
    rerank: bool = Query(
        True, description="Cross-encoder rerank a wider candidate pool (~20ms)"
    ),
    with_context: bool = Query(
        True, description="Attach what was on screen and which topic each hit sits in"
    ),
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SearchResponse:
    """Rank child chunks by fused semantic and lexical relevance.

    Results carry their parent chunk's text, which is the wider context an LLM
    will reason over in Phase 7 — the child is what matched, the parent is what
    the match means.
    """
    started = time.perf_counter()

    indexed = (
        await session.execute(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.level == ChunkLevel.CHILD, Chunk.embedding.isnot(None))
        )
    ).scalar_one()

    if indexed == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Nothing is indexed yet. Process a video first (POST /api/videos/{id}/process).",
        )

    embed_started = time.perf_counter()
    vector = await embed_query(
        q, model_name=settings.embedding_model, device=settings.embedding_device
    )
    embed_ms = (time.perf_counter() - embed_started) * 1000

    wanted_kinds = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    if wanted_kinds:
        unknown = set(wanted_kinds) - ChunkKind.ALL
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Unknown kind(s): {', '.join(sorted(unknown))}. "
                f"Valid: {', '.join(sorted(ChunkKind.ALL))}.",
            )

    time_range = None
    if start_s is not None or end_s is not None:
        time_range = (start_s or 0.0, end_s if end_s is not None else float("inf"))
        if time_range[0] > time_range[1]:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "start_s must not exceed end_s"
            )

    rerank_started = time.perf_counter()
    result = await hybrid_search(
        session,
        q,
        vector,
        video_ids=await resolve_video_ids(
            session, video_id=video_id, collection_id=collection_id
        ),
        kinds=wanted_kinds,
        time_range=time_range,
        limit=limit,
        # Rerank a pool several times the requested size: the value is in
        # promoting from deeper than the fused top-k would ever show.
        rerank_candidates=(
            max(limit * 3, 30) if rerank and settings.rerank_enabled else 0
        ),
        with_context=with_context,
    )
    rerank_ms = (time.perf_counter() - rerank_started) * 1000 if rerank else None

    total_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "search %r -> %d hits (%.0f ms; embed %.0f ms; rerank=%s top=%.2f)",
        q, len(result.hits), total_ms, embed_ms, result.reranked,
        result.top_relevance if result.top_relevance is not None else float("nan"),
    )

    return SearchResponse(
        query=q,
        hits=[
            SearchHit(
                chunk_id=h.chunk_id,
                video_id=h.video_id,
                video_title=h.video_title,
                start_s=h.start_s,
                end_s=h.end_s,
                text=h.text,
                score=round(h.score, 6),
                matched_by=h.matched_by,
                semantic_rank=h.semantic_rank,
                lexical_rank=h.lexical_rank,
                parent_text=h.parent_text,
                parent_start_s=h.parent_start_s,
                parent_end_s=h.parent_end_s,
                rerank_score=round(h.rerank_score, 4) if h.rerank_score is not None else None,
                fused_rank=h.fused_rank,
                context=(
                    TemporalContext(
                        keyframe_id=h.context.keyframe_id,
                        keyframe_time_s=h.context.keyframe_time_s,
                        on_screen_text=h.context.on_screen_text,
                        events=h.context.events,
                        topic_title=h.context.topic_title,
                        topic_start_s=h.context.topic_start_s,
                    )
                    if h.context and not h.context.is_empty
                    else None
                ),
            )
            for h in result.hits
        ],
        total=len(result.hits),
        semantic_candidates=result.semantic_candidates,
        lexical_candidates=result.lexical_candidates,
        fused_candidates=result.fused_candidates,
        took_ms=round(total_ms, 1),
        embed_ms=round(embed_ms, 1),
        rerank_ms=round(rerank_ms, 1) if rerank_ms is not None else None,
        reranked=result.reranked,
        top_relevance=(
            round(result.top_relevance, 4) if result.top_relevance is not None else None
        ),
    )


@router.get("/stats", summary="Index size and coverage")
async def stats(session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    rows = (
        await session.execute(
            select(Chunk.level, func.count(), func.count(Chunk.embedding)).group_by(Chunk.level)
        )
    ).all()

    by_level = {
        level: {"chunks": int(count), "embedded": int(embedded)}
        for level, count, embedded in rows
    }
    videos_indexed = (
        await session.execute(select(func.count(func.distinct(Chunk.video_id))))
    ).scalar_one()

    return {
        "by_level": by_level,
        "videos_indexed": int(videos_indexed),
        "searchable": by_level.get(ChunkLevel.CHILD, {}).get("embedded", 0) > 0,
    }


@router.get(
    "/timeline",
    response_model=ConceptTimelineOut,
    summary="Where a concept appears across the corpus, chronologically",
)
async def concept_timeline(
    q: str = Query(..., min_length=2, max_length=512),
    collection_id: uuid.UUID | None = Query(None),
    video_id: uuid.UUID | None = Query(None),
    min_relevance: float | None = Query(
        2.0,
        description=(
            "Cross-encoder floor. A chronology of weak matches is a chronology "
            "of noise, and unlike a ranked list there is no position cue telling "
            "the reader to distrust the tail."
        ),
    ),
    per_video: int = Query(8, ge=1, le=30),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ConceptTimelineOut:
    """Group retrieval results by video and order them by time.

    A ranked list says "here are twenty relevant moments". A chronology says
    "introduced here, developed there, revisited at the end" — which is the
    question people actually ask of a course.
    """
    started = time.perf_counter()

    vector = await embed_query(
        q, model_name=settings.embedding_model, device=settings.embedding_device
    )
    result = await hybrid_search(
        session,
        q,
        vector,
        video_ids=await resolve_video_ids(
            session, video_id=video_id, collection_id=collection_id
        ),
        # Wide: a chronology needs coverage across the whole timeline, not the
        # few best moments.
        limit=60,
        rerank_candidates=120,
        with_context=True,
    )

    timeline = await build_timeline(
        session, q, result.hits, min_relevance=min_relevance, per_video=per_video
    )
    took_ms = (time.perf_counter() - started) * 1000

    return ConceptTimelineOut(
        query=q,
        tracks=[
            VideoTrackOut(
                video_id=t.video_id,
                video_title=t.video_title,
                duration_s=t.duration_s,
                occurrences=[
                    OccurrenceOut(
                        chunk_id=o.chunk_id,
                        start_s=o.start_s,
                        end_s=o.end_s,
                        text=o.text,
                        relevance=round(o.relevance, 4) if o.relevance is not None else None,
                        topic_title=o.topic_title,
                    )
                    for o in t.occurrences
                ],
            )
            for t in timeline.tracks
        ],
        total_occurrences=timeline.total_occurrences,
        first_video_id=timeline.first_video_id,
        first_video_title=timeline.first_video_title,
        first_start_s=timeline.first_start_s,
        took_ms=round(took_ms, 1),
    )
