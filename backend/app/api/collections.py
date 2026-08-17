"""Collections: named groups of videos.

The unit that cross-video questions are asked over. "Compare how these lectures
treat backpropagation" needs a way to say *which* lectures, and on a growing
corpus an unscoped query only gets noisier.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Chunk, ChunkLevel, Collection, Video
from app.schemas import (
    CollectionCreate,
    CollectionDetail,
    CollectionList,
    CollectionSummary,
    CollectionUpdate,
    VideoSummary,
)

router = APIRouter(prefix="/api/collections", tags=["collections"])


async def _summary(session: AsyncSession, collection: Collection) -> CollectionSummary:
    counts = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(Video.duration_s), 0.0)).where(
                Video.collection_id == collection.id
            )
        )
    ).one()
    indexed = (
        await session.execute(
            select(func.count(func.distinct(Chunk.video_id)))
            .select_from(Chunk)
            .join(Video, Video.id == Chunk.video_id)
            .where(Video.collection_id == collection.id, Chunk.level == ChunkLevel.CHILD)
        )
    ).scalar_one()

    return CollectionSummary(
        id=collection.id,
        name=collection.name,
        description=collection.description,
        video_count=int(counts[0]),
        indexed_count=int(indexed),
        total_duration_s=float(counts[1] or 0.0),
        created_at=collection.created_at,
    )


@router.post("", response_model=CollectionDetail, status_code=status.HTTP_201_CREATED)
async def create_collection(
    payload: CollectionCreate, session: AsyncSession = Depends(get_session)
) -> CollectionDetail:
    collection = Collection(name=payload.name.strip(), description=payload.description)
    session.add(collection)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A collection named {payload.name!r} already exists"
        ) from exc

    await session.refresh(collection)
    return CollectionDetail(**(await _summary(session, collection)).model_dump(), videos=[])


@router.get("", response_model=CollectionList)
async def list_collections(session: AsyncSession = Depends(get_session)) -> CollectionList:
    rows = (
        await session.execute(select(Collection).order_by(Collection.name))
    ).scalars().all()
    items = [await _summary(session, c) for c in rows]

    unfiled = (
        await session.execute(
            select(func.count()).select_from(Video).where(Video.collection_id.is_(None))
        )
    ).scalar_one()

    return CollectionList(items=items, total=len(items), unfiled_videos=int(unfiled))


@router.get("/{collection_id}", response_model=CollectionDetail)
async def get_collection(
    collection_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> CollectionDetail:
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")

    videos = (
        await session.execute(
            select(Video)
            .where(Video.collection_id == collection_id)
            .order_by(Video.created_at)
        )
    ).scalars().all()

    return CollectionDetail(
        **(await _summary(session, collection)).model_dump(),
        videos=[VideoSummary.model_validate(v) for v in videos],
    )


@router.patch("/{collection_id}", response_model=CollectionDetail)
async def update_collection(
    collection_id: uuid.UUID,
    payload: CollectionUpdate,
    session: AsyncSession = Depends(get_session),
) -> CollectionDetail:
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")

    if payload.name is not None:
        collection.name = payload.name.strip()
    if payload.description is not None:
        collection.description = payload.description

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That name is already taken") from exc

    return await get_collection(collection_id, session)


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    """Delete the collection. Its videos survive, unfiled.

    A tidy-up must never destroy a six-hour transcript, so the foreign key is
    ON DELETE SET NULL rather than CASCADE.
    """
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")

    await session.delete(collection)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{collection_id}/videos/{video_id}", response_model=CollectionDetail)
async def add_video(
    collection_id: uuid.UUID,
    video_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> CollectionDetail:
    if await session.get(Collection, collection_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")
    video = await session.get(Video, video_id)
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")

    # A video belongs to at most one collection; assigning moves it.
    video.collection_id = collection_id
    await session.commit()
    return await get_collection(collection_id, session)


@router.delete("/{collection_id}/videos/{video_id}", response_model=CollectionDetail)
async def remove_video(
    collection_id: uuid.UUID,
    video_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> CollectionDetail:
    if await session.get(Collection, collection_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")

    await session.execute(
        update(Video)
        .where(Video.id == video_id, Video.collection_id == collection_id)
        .values(collection_id=None)
    )
    await session.commit()
    return await get_collection(collection_id, session)


async def resolve_video_ids(
    session: AsyncSession,
    *,
    video_id: uuid.UUID | None = None,
    collection_id: uuid.UUID | None = None,
) -> list[uuid.UUID] | None:
    """Scope a query to a video, a collection, or the whole corpus.

    Returns None for "everything" — the retrievers treat that as no filter.
    An empty collection returns an empty list, which correctly matches nothing
    rather than silently widening to the whole corpus.
    """
    if video_id is not None:
        return [video_id]
    if collection_id is None:
        return None

    if await session.get(Collection, collection_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")

    rows = (
        await session.execute(select(Video.id).where(Video.collection_id == collection_id))
    ).scalars().all()
    return list(rows)


@router.get("/{collection_id}/videos", response_model=list[VideoSummary])
async def list_collection_videos(
    collection_id: uuid.UUID,
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[Video]:
    if await session.get(Collection, collection_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")

    rows = (
        await session.execute(
            select(Video)
            .where(Video.collection_id == collection_id)
            .order_by(Video.created_at)
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)
