"""Transcript retrieval and processing triggers."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import (
    JobStage,
    JobStatus,
    ProcessingJob,
    StageName,
    StageStatus,
    TranscriptSegment,
    Video,
)
from app.pipeline import runner
from app.schemas import (
    JobOut,
    TranscriptOut,
    TranscriptSearchHit,
    TranscriptSearchResults,
    TranscriptSegmentOut,
)

router = APIRouter(prefix="/api", tags=["transcripts"])


# Named groups for the selections people actually want, so the common case is
# not a stage list you have to remember.
STAGE_PRESETS: dict[str, tuple[str, ...]] = {
    "all": StageName.ORDER,
    # Everything except transcription — the expensive stage you rarely want twice.
    "visual": (StageName.PROBE, StageName.KEYFRAMES, StageName.OCR, StageName.EMBED),
    "speech": (StageName.PROBE, StageName.AUDIO_EXTRACT, StageName.TRANSCRIBE, StageName.EMBED),
    # Re-chunk and re-embed from whatever is already stored.
    "index": (StageName.PROBE, StageName.EMBED),
}


def _resolve_stages(raw: str | None) -> set[str]:
    """Parse a preset name or comma-separated stage list."""
    if not raw or raw.strip().lower() == "all":
        return set(StageName.ORDER)

    value = raw.strip().lower()
    if value in STAGE_PRESETS:
        return set(STAGE_PRESETS[value])

    requested = {part.strip() for part in value.split(",") if part.strip()}
    unknown = requested - set(StageName.ORDER)
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Unknown stage(s): {', '.join(sorted(unknown))}. "
            f"Valid: {', '.join(StageName.ORDER)}. "
            f"Presets: {', '.join(STAGE_PRESETS)}.",
        )
    if not requested:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No stages requested")

    # Probe is free — it only records already-known metadata — and its absence
    # would leave a confusing hole at the head of the pipeline view.
    requested.add(StageName.PROBE)
    return requested


@router.post(
    "/videos/{video_id}/process",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue processing for a video",
)
async def start_processing(
    video_id: uuid.UUID,
    stages: str | None = Query(
        None,
        description=(
            "Stages to run: a preset (all, visual, speech, index) or a "
            "comma-separated list. Defaults to all. Unselected stages are marked "
            "skipped and their inputs are read from what is already stored — so "
            "`visual` adds keyframes and OCR without re-transcribing."
        ),
        examples=["visual", "keyframes,ocr,embed"],
    ),
    session: AsyncSession = Depends(get_session),
) -> ProcessingJob:
    video = await session.get(Video, video_id)
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")

    wanted = _resolve_stages(stages)

    # Refuse to double-queue: two Whisper runs on one GPU is nobody's intent.
    existing = (
        await session.execute(
            select(ProcessingJob)
            .where(
                ProcessingJob.video_id == video_id,
                ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
            .limit(1)
        )
    ).scalars().first()

    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Processing is already queued or running for this video"
        )

    # A fresh job per attempt — videos are immutable, so history is worth keeping.
    # Unselected stages are recorded as skipped up front rather than passed to the
    # worker: the request survives a restart, and the job row stays self-describing.
    job = ProcessingJob(
        video_id=video_id,
        status=JobStatus.QUEUED,
        stages=[
            JobStage(
                name=name,
                position=i,
                status=StageStatus.WAITING if name in wanted else StageStatus.SKIPPED,
                progress=0.0,
                metrics=None if name in wanted else {"reason": "not requested"},
            )
            for i, name in enumerate(StageName.ORDER)
        ],
    )
    session.add(job)
    await session.commit()
    await session.refresh(job, attribute_names=["stages"])

    await runner.enqueue(job.id)
    return job


@router.get(
    "/videos/{video_id}/transcript",
    response_model=TranscriptOut,
    summary="Full transcript for a video",
)
async def get_transcript(
    video_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> TranscriptOut:
    if await session.get(Video, video_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")

    rows = (
        await session.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.video_id == video_id)
            .order_by(TranscriptSegment.position)
        )
    ).scalars().all()

    speech = sum(r.end_s - r.start_s for r in rows)
    return TranscriptOut(
        video_id=video_id,
        segments=[TranscriptSegmentOut.model_validate(r) for r in rows],
        total=len(rows),
        model=rows[0].model if rows else None,
        speech_duration_s=round(speech, 2),
    )


@router.get(
    "/search/transcript",
    response_model=TranscriptSearchResults,
    summary="Keyword search across transcripts",
)
async def search_transcripts(
    q: str = Query(..., min_length=2, max_length=256),
    video_id: uuid.UUID | None = Query(None, description="Restrict to one video"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> TranscriptSearchResults:
    """Substring matching over transcript text.

    Deliberately literal. Real hybrid retrieval — semantic vectors fused with
    BM25 — is Phase 3 and needs Postgres. This exists so the transcript is
    searchable the moment it exists, and it is genuinely the right tool for
    exact strings like `ResNet-50`, which embeddings handle badly.
    """
    pattern = f"%{q}%"
    stmt = select(TranscriptSegment).where(TranscriptSegment.text.ilike(pattern))
    count_stmt = (
        select(func.count())
        .select_from(TranscriptSegment)
        .where(TranscriptSegment.text.ilike(pattern))
    )

    if video_id is not None:
        stmt = stmt.where(TranscriptSegment.video_id == video_id)
        count_stmt = count_stmt.where(TranscriptSegment.video_id == video_id)

    stmt = stmt.order_by(TranscriptSegment.video_id, TranscriptSegment.start_s).limit(limit)

    rows = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar_one()

    return TranscriptSearchResults(
        query=q,
        hits=[TranscriptSearchHit.model_validate(r) for r in rows],
        total=total,
    )
