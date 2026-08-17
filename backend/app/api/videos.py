"""Video upload, retrieval and streaming."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Video, VideoStatus
from app.probe import ProbeError, probe
from app.ranges import InvalidRange, parse_range, range_headers
from app.schemas import VideoDetail, VideoList, VideoSummary, VideoUpdate
from app.storage import Storage, UploadTooLarge, get_storage, make_storage_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/videos", tags=["videos"])

# Formats libav decodes reliably and browsers can play back natively.
# Deliberately narrow — "support every container" was explicitly out of scope.
ALLOWED_SUFFIXES = frozenset({".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".mpg", ".mpeg"})

# Browsers only play a subset of what we accept. Anything else is stored and
# indexed fine but needs transcoding before playback (a later phase).
BROWSER_PLAYABLE = frozenset({".mp4", ".webm", ".m4v", ".mov"})

_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
}


def _content_type_for(filename: str) -> str:
    return _CONTENT_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


async def _get_video_or_404(session: AsyncSession, video_id: uuid.UUID) -> Video:
    video = await session.get(Video, video_id)
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")
    return video


@router.post(
    "",
    response_model=VideoDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a video",
)
async def upload_video(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=512, description="Original filename"),
    title: str | None = Query(None, max_length=512),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> Video:
    """Stream a raw request body straight into object storage.

    The body is the file itself, not multipart. Multipart would spool the whole
    upload to a temp file before we ever see a byte, doubling the I/O and the
    transient disk cost — noticeable at 2 GB, which is an ordinary lecture.
    `fetch(url, {method: 'POST', body: file})` sends exactly this.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported format {suffix!r}. Accepted: {', '.join(sorted(ALLOWED_SUFFIXES))}",
        )

    video_id = uuid.uuid4()
    storage_key = make_storage_key(video_id, filename)

    video = Video(
        id=video_id,
        title=(title or Path(filename).stem)[:512],
        original_filename=filename,
        storage_key=storage_key,
        mime_type=_content_type_for(filename),
        size_bytes=0,
        status=VideoStatus.UPLOADING,
    )
    session.add(video)
    await session.commit()

    # ─ Stream to storage ─
    try:
        stored = await storage.save_stream(
            storage_key,
            request.stream(),
            max_bytes=settings.max_upload_bytes,
        )
    except UploadTooLarge as exc:
        await _abandon(session, storage, video, f"Upload exceeds limit of {exc.limit} bytes")
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc
    except Exception as exc:
        logger.exception("Upload failed for video %s", video_id)
        await _abandon(session, storage, video, f"Upload failed: {exc}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Upload failed") from exc

    if stored.size_bytes == 0:
        await _abandon(session, storage, video, "Uploaded file was empty")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file was empty")

    video.size_bytes = stored.size_bytes
    video.checksum_sha256 = stored.checksum_sha256

    # ─ Probe ─
    # Fail loudly here: an unprobeable file is unprocessable, and finding out at
    # upload beats finding out three pipeline stages later.
    path = storage.local_path(storage_key)
    if path is None:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "Probing non-local storage requires a temp-file download (not implemented)",
        )

    try:
        result = await probe(path)
    except ProbeError as exc:
        await _abandon(session, storage, video, f"Not a decodable video: {exc}")
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Not a decodable video: {exc}"
        ) from exc

    video.duration_s = result.duration_s
    video.width = result.width
    video.height = result.height
    video.fps = result.fps
    video.video_codec = result.video_codec
    video.audio_codec = result.audio_codec
    video.has_audio = result.has_audio
    video.audio_channels = result.audio_channels
    video.audio_sample_rate = result.audio_sample_rate
    video.status = VideoStatus.UPLOADED

    if result.warnings:
        logger.info("Probe warnings for %s: %s", video_id, "; ".join(result.warnings))

    # No processing job is created here. Transcription is a multi-minute GPU
    # job, so it is triggered explicitly via POST /api/videos/{id}/process —
    # the user sees the probed metadata first and decides.
    await session.commit()
    await session.refresh(video)

    logger.info(
        "Uploaded %s (%.1f MB, %.1fs, audio=%s)",
        filename,
        stored.size_bytes / 1024**2,
        result.duration_s or 0.0,
        result.has_audio,
    )
    return video


async def _abandon(session: AsyncSession, storage: Storage, video: Video, reason: str) -> None:
    """Mark a video failed and drop whatever bytes reached storage."""
    video.status = VideoStatus.FAILED
    video.error = reason
    await session.commit()
    try:
        await storage.delete(video.storage_key)
    except Exception:  # noqa: BLE001 — cleanup must not mask the original error
        logger.warning("Could not clean up storage for %s", video.id, exc_info=True)


@router.get("", response_model=VideoList, summary="List videos")
async def list_videos(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=256, description="Title substring filter"),
    session: AsyncSession = Depends(get_session),
) -> VideoList:
    stmt = select(Video)
    count_stmt = select(func.count()).select_from(Video)

    if q:
        clause = Video.title.ilike(f"%{q}%")
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)

    stmt = stmt.order_by(Video.created_at.desc()).limit(limit).offset(offset)

    rows = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar_one()

    return VideoList(
        items=[VideoSummary.model_validate(v) for v in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{video_id}", response_model=VideoDetail, summary="Video detail")
async def get_video(
    video_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Video:
    return await _get_video_or_404(session, video_id)


@router.patch("/{video_id}", response_model=VideoDetail, summary="Rename or describe")
async def update_video(
    video_id: uuid.UUID,
    payload: VideoUpdate,
    session: AsyncSession = Depends(get_session),
) -> Video:
    video = await _get_video_or_404(session, video_id)
    if payload.title is not None:
        video.title = payload.title
    if payload.description is not None:
        video.description = payload.description
    video.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(video)
    return video


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a video")
async def delete_video(
    video_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Response:
    video = await _get_video_or_404(session, video_id)
    key = video.storage_key

    await session.execute(delete(Video).where(Video.id == video_id))
    await session.commit()

    # Storage cleanup after the row is gone: an orphaned file is recoverable
    # waste, an orphaned row is a broken player.
    try:
        await storage.delete(key)
    except Exception:  # noqa: BLE001
        logger.warning("Orphaned storage object %s", key, exc_info=True)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{video_id}/stream", summary="Stream video bytes (Range-aware)")
async def stream_video(
    video_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Response:
    """Serve video with byte-range support.

    Seeking in a `<video>` element depends on 206 responses; without them the
    browser buffers the entire file before allowing a scrub.
    """
    video = await _get_video_or_404(session, video_id)

    if not await storage.exists(video.storage_key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video bytes are missing from storage")

    file_size = await storage.size(video.storage_key)

    try:
        byte_range = parse_range(request.headers.get("range"), file_size)
    except InvalidRange as exc:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"},
            content=str(exc),
        )

    start, end = (byte_range.start, byte_range.end) if byte_range else (0, file_size - 1)

    return StreamingResponse(
        storage.read_range(video.storage_key, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT if byte_range else status.HTTP_200_OK,
        headers=range_headers(byte_range, file_size, video.mime_type),
    )


@router.get("/{video_id}/playable", summary="Whether the browser can play this natively")
async def playability(
    video_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    """Tell the UI whether to show a player or a 'transcoding required' notice.

    Extension is a coarse proxy — an .mp4 can still hold codecs the browser
    refuses. Good enough to avoid presenting a permanently black player.
    """
    video = await _get_video_or_404(session, video_id)
    suffix = Path(video.original_filename).suffix.lower()
    return {
        "playable": suffix in BROWSER_PLAYABLE,
        "container": suffix,
        "video_codec": video.video_codec,
        "audio_codec": video.audio_codec,
    }
