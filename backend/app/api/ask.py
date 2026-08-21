"""Question answering over indexed video."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.answer import answer_question
from app.api.collections import resolve_video_ids
from app.config import Settings, get_settings
from app.db import get_session
from app.models import Chunk, ChunkKind, ChunkLevel
from app.pipeline.llm import LLMError, LLMQuotaExceeded, LLMUnavailable
from app.schemas import AnswerResponse, AskRequest, CitationOut, EvidenceOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["ask"])


@router.post("/ask", response_model=AnswerResponse, summary="Ask a question about the videos")
async def ask(
    payload: AskRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AnswerResponse:
    """Answer from retrieved evidence, with citations resolved from the database.

    The model never writes a timestamp — see app/answer.py. Markers it invents
    are rejected before the answer is returned, and `fabricated_citations`
    reports any that were.
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
            "Nothing is indexed yet. Process a video first.",
        )

    kinds = None
    if payload.kinds:
        kinds = [k.strip() for k in payload.kinds.split(",") if k.strip()]
        unknown = set(kinds) - ChunkKind.ALL
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Unknown kind(s): {', '.join(sorted(unknown))}",
            )

    try:
        result = await answer_question(
            session,
            payload.question,
            video_ids=await resolve_video_ids(
                session, video_id=payload.video_id, collection_id=payload.collection_id
            ),
            kinds=kinds,
            candidates=settings.llm_evidence_items,
            relevance_floor=settings.llm_relevance_floor,
            max_tokens=settings.llm_max_tokens,
        )
    except LLMQuotaExceeded as exc:
        # 429 rather than a generic 502: the caller can act on this, and
        # the message carries the limit and the wait.
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except LLMUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except LLMError as exc:
        logger.exception("LLM call failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    took_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "ask %r -> %s (%.0f ms, %d citations, %d fabricated, %d/%d uncited)",
        payload.question,
        "refused" if result.refused else "answered",
        took_ms,
        len(result.citations),
        len(result.fabricated_citations),
        result.uncited_sentences,
        result.total_sentences,
    )

    return AnswerResponse(
        question=payload.question,
        answer=result.text,
        citations=[
            CitationOut(
                marker=c.marker,
                chunk_id=c.chunk_id,
                video_id=c.video_id,
                video_title=c.video_title,
                start_s=c.start_s,
                end_s=c.end_s,
                text=c.text,
                quote=c.quote or c.text,
            )
            for c in result.citations
        ],
        evidence=[
            EvidenceOut(
                marker=e.marker,
                chunk_id=e.chunk_id,
                video_id=e.video_id,
                video_title=e.video_title,
                start_s=e.start_s,
                end_s=e.end_s,
                text=e.text,
                quote=e.quote or e.text,
                on_screen_text=e.on_screen_text,
                topic_title=e.topic_title,
                relevance=e.relevance,
            )
            for e in result.evidence
        ],
        refused=result.refused,
        refusal_reason=result.refusal_reason,
        fabricated_citations=result.fabricated_citations,
        uncited_sentences=result.uncited_sentences,
        total_sentences=result.total_sentences,
        model=result.model,
        took_ms=round(took_ms, 1),
    )
