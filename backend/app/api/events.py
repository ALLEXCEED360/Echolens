"""Timeline: events and topic hierarchy."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Event, Topic, Video
from app.schemas import EventList, EventOut, TopicNode, TopicTree

router = APIRouter(prefix="/api", tags=["timeline"])


@router.get("/videos/{video_id}/events", response_model=EventList, summary="Timeline events")
async def list_events(
    video_id: uuid.UUID,
    types: str | None = Query(None, description="Comma-separated event types to include"),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(1000, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
) -> EventList:
    if await session.get(Video, video_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")

    stmt = (
        select(Event)
        .where(Event.video_id == video_id, Event.confidence >= min_confidence)
        .order_by(Event.start_s)
        .limit(limit)
    )
    if types:
        wanted = {t.strip() for t in types.split(",") if t.strip()}
        stmt = stmt.where(Event.type.in_(wanted))

    rows = (await session.execute(stmt)).scalars().all()

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.type] = counts.get(row.type, 0) + 1

    return EventList(
        video_id=video_id,
        items=[EventOut.model_validate(r) for r in rows],
        total=len(rows),
        by_type=counts,
    )


@router.get("/videos/{video_id}/topics", response_model=TopicTree, summary="Topic hierarchy")
async def list_topics(
    video_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> TopicTree:
    """Coarse topics with their fine-grained children nested underneath."""
    if await session.get(Video, video_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")

    rows = (
        await session.execute(
            select(Topic).where(Topic.video_id == video_id).order_by(Topic.depth, Topic.position)
        )
    ).scalars().all()

    children: dict[uuid.UUID, list[Topic]] = {}
    for row in rows:
        if row.depth > 0 and row.parent_id:
            children.setdefault(row.parent_id, []).append(row)

    def to_node(topic: Topic) -> TopicNode:
        return TopicNode(
            id=topic.id,
            position=topic.position,
            depth=topic.depth,
            start_s=topic.start_s,
            end_s=topic.end_s,
            title=topic.title,
            keywords=(topic.keywords or {}).get("terms", []),
            boundary_strength=topic.boundary_strength,
            children=[
                to_node(c)
                for c in sorted(children.get(topic.id, []), key=lambda t: t.start_s)
            ],
        )

    roots = [to_node(t) for t in rows if t.depth == 0]
    return TopicTree(
        video_id=video_id,
        items=roots,
        total=len(rows),
        coarse=len(roots),
        fine=sum(len(v) for v in children.values()),
    )
