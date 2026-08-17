"""Processing job status.

Phase 1 exposes the records; the arq worker that advances them arrives in
Phase 2. The shape is settled now so the UI can be built against it.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import JobStatus, ProcessingJob, Video
from app.schemas import JobOut

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/videos/{video_id}/jobs", response_model=list[JobOut], summary="Jobs for a video")
async def list_jobs_for_video(
    video_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[ProcessingJob]:
    if await session.get(Video, video_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")

    stmt = (
        select(ProcessingJob)
        .where(ProcessingJob.video_id == video_id)
        .options(selectinload(ProcessingJob.stages))
        .order_by(ProcessingJob.seq.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


@router.get("/videos/{video_id}/job", response_model=JobOut, summary="Current job for a video")
async def latest_job(
    video_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> ProcessingJob:
    """The job the UI should be watching.

    An *active* job always wins over a newer finished one. Ordering by
    `created_at` alone is not enough, as this database demonstrated: the host
    clock stepped backwards about an hour, leaving older finished jobs stamped
    *ahead* of a job that was still running, so the UI polled a stale
    "succeeded" record while work was genuinely in flight.

    Ordering therefore uses `seq`, a monotonic identity column, rather than a
    timestamp — and an active job still wins outright, because liveness is the
    property callers actually want and it is selectable directly.
    """
    base = (
        select(ProcessingJob)
        .where(ProcessingJob.video_id == video_id)
        .options(selectinload(ProcessingJob.stages))
    )

    active = (
        await session.execute(
            base.where(ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
            .order_by(ProcessingJob.seq.desc())
            .limit(1)
        )
    ).scalars().first()
    if active is not None:
        return active

    job = (
        await session.execute(base.order_by(ProcessingJob.seq.desc()).limit(1))
    ).scalars().first()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No job for this video")
    return job


@router.get("/jobs/{job_id}", response_model=JobOut, summary="Job by id")
async def get_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ProcessingJob:
    stmt = (
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .options(selectinload(ProcessingJob.stages))
    )
    job = (await session.execute(stmt)).scalars().first()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job
