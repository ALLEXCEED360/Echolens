"""Keyframe listing and image serving."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Keyframe, Video
from app.schemas import KeyframeList, KeyframeOut, OcrBlockOut

router = APIRouter(prefix="/api", tags=["keyframes"])


@router.get(
    "/videos/{video_id}/keyframes",
    response_model=KeyframeList,
    summary="Keyframes with their OCR text",
)
async def list_keyframes(
    video_id: uuid.UUID,
    with_text_only: bool = Query(False, description="Only frames that produced OCR text"),
    limit: int = Query(500, ge=1, le=3000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> KeyframeList:
    if await session.get(Video, video_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")

    stmt = (
        select(Keyframe)
        .where(Keyframe.video_id == video_id)
        .options(selectinload(Keyframe.ocr_blocks))
        .order_by(Keyframe.position)
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()

    items = []
    for row in rows:
        blocks = sorted(
            row.ocr_blocks,
            key=lambda b: ((b.bbox or {}).get("y1", 0), (b.bbox or {}).get("x1", 0)),
        )
        if with_text_only and not blocks:
            continue
        items.append(
            KeyframeOut(
                id=row.id,
                position=row.position,
                start_s=row.start_s,
                end_s=row.end_s,
                time_s=row.time_s,
                change=row.change,
                image_url=f"/api/keyframes/{row.id}/image",
                text="\n".join(b.text for b in blocks),
                ocr_blocks=[
                    OcrBlockOut(text=b.text, confidence=b.confidence, bbox=b.bbox)
                    for b in blocks
                ],
            )
        )

    return KeyframeList(video_id=video_id, items=items, total=len(items))


@router.get("/keyframes/{keyframe_id}/image", summary="Keyframe JPEG")
async def keyframe_image(
    keyframe_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    keyframe = await session.get(Keyframe, keyframe_id)
    if keyframe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Keyframe not found")

    path = (settings.storage_local_path / keyframe.storage_key).resolve()
    root = settings.storage_local_path.resolve()
    # The key comes from the database, but treat it as untrusted anyway.
    if root not in path.parents:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid keyframe path")
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Keyframe image is missing from storage")

    return FileResponse(
        path,
        media_type="image/jpeg",
        # Frames are immutable once extracted.
        headers={"Cache-Control": "private, max-age=86400"},
    )
