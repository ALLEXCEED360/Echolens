"""Job execution.

**Queue decision.** The architecture calls for arq on Redis, and Redis is not
installed (no Docker, no WSL — see docs/05-environment.md). So this is an
in-process asyncio queue with a *single* consumer, which is a deliberate bridge
in the same spirit as the SQLite one.

It is not merely a stopgap: a single consumer serialises GPU stages for free,
which is exactly the constraint an 8 GB card imposes anyway (Whisper large-v3
and a VLM will not coexist). Swapping in arq later means replacing `enqueue()`
and the consumer loop — the stage functions below are untouched, because they
take a session and a job id and nothing else.

What is genuinely lost until Redis arrives: durability across restarts. A job
interrupted by a server restart is marked failed on the next boot rather than
resumed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    Chunk,
    ChunkKind,
    ChunkLevel,
    Event,
    EventSource,
    EventType,
    JobStage,
    JobStatus,
    OcrBlock,
    ProcessingJob,
    StageName,
    StageStatus,
    Topic,
    TranscriptSegment,
    Video,
    VideoStatus,
)
from app.models import Keyframe as KeyframeRow
from app.pipeline.audio import AudioExtractionError, extract_audio
from app.pipeline.chunking import SourceSegment, build_chunks, estimate_tokens
from app.pipeline.embedding import embed_documents
from app.pipeline.embedding import unload as unload_embedder
from app.pipeline.events import Segment as EventSegment
from app.pipeline.events import (
    build_topic_hierarchy,
    locate_parent,
    scene_events,
    silence_events,
    text_events,
)
from app.pipeline.keyframes import KeyframeError
from app.pipeline.keyframes import extract as extract_keyframes
from app.pipeline.keyframes import scan as scan_keyframes
from app.pipeline.ocr import is_indexable, read_frames
from app.pipeline.transcribe import transcribe
from app.storage import derived_dir, get_storage

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[UUID] | None = None
_consumer: asyncio.Task | None = None


# ─── Stage bookkeeping ─────────────────────────────────────────────────────


class StageFailed(RuntimeError):
    """A stage failed. Critical-path stages abort the job; others degrade."""


async def _set_stage(
    session: AsyncSession,
    job_id: UUID,
    name: str,
    *,
    status: str | None = None,
    progress: float | None = None,
    error: str | None = None,
    metrics: dict | None = None,
) -> None:
    values: dict[str, object] = {}
    if status is not None:
        values["status"] = status
        if status == StageStatus.RUNNING:
            values["started_at"] = datetime.now(UTC)
        elif status in (StageStatus.SUCCEEDED, StageStatus.FAILED, StageStatus.SKIPPED):
            values["finished_at"] = datetime.now(UTC)
            values["progress"] = 1.0 if status != StageStatus.FAILED else 0.0
    if progress is not None:
        values["progress"] = max(0.0, min(progress, 1.0))
    if error is not None:
        values["error"] = error
    if metrics is not None:
        values["metrics"] = metrics

    await session.execute(
        update(JobStage)
        .where(JobStage.job_id == job_id, JobStage.name == name)
        .values(**values)
    )
    await session.commit()


async def _report_progress(job_id: UUID, name: str, fraction: float) -> None:
    """Write stage progress on its **own** session.

    Reusing the stage's session deadlocks: stages run under `asyncio.to_thread`,
    so progress callbacks are scheduled back onto the loop while the stage
    coroutine is mid-`commit()`. Two concurrent commits on one AsyncSession
    raise IllegalStateChangeError — sessions are not concurrency-safe.

    The `status == running` guard makes a late-arriving update a no-op, so a
    callback still in flight when the stage finishes cannot reset progress
    from 1.0 back to 0.97.
    """
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(JobStage)
                .where(
                    JobStage.job_id == job_id,
                    JobStage.name == name,
                    JobStage.status == StageStatus.RUNNING,
                )
                .values(progress=max(0.0, min(fraction, 1.0)))
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — progress is cosmetic, never fail a job for it
        logger.debug("Progress update dropped for %s/%s", job_id, name, exc_info=True)


def _throttled(job_id: UUID, name: str, loop: asyncio.AbstractEventLoop):
    """Sync progress callback safe to call from a worker thread.

    Throttled to ~1/sec: a per-segment write on a two-hour lecture is thousands
    of pointless round trips.
    """
    last = 0.0

    def callback(fraction: float) -> None:
        nonlocal last
        now = time.monotonic()
        if now - last < 1.0 and fraction < 1.0:
            return
        last = now
        asyncio.run_coroutine_threadsafe(_report_progress(job_id, name, fraction), loop)

    return callback


# ─── Stages ────────────────────────────────────────────────────────────────


async def _stage_probe(session: AsyncSession, job_id: UUID, video: Video) -> None:
    """Already done at upload; recorded so the timeline is complete."""
    await _set_stage(
        session,
        job_id,
        StageName.PROBE,
        status=StageStatus.SUCCEEDED,
        metrics={"duration_s": video.duration_s, "has_audio": video.has_audio},
    )


async def _stage_audio(session: AsyncSession, job_id: UUID, video: Video) -> Path:
    name = StageName.AUDIO_EXTRACT
    await _set_stage(session, job_id, name, status=StageStatus.RUNNING)

    storage = get_storage()
    source = storage.local_path(video.storage_key)
    if source is None:
        raise StageFailed("Non-local storage requires a temp download (not implemented)")

    settings = get_settings()
    destination = derived_dir(settings, video.id) / "audio16k.wav"

    loop = asyncio.get_running_loop()
    started = time.perf_counter()
    try:
        result = await extract_audio(
            source, destination, progress=_throttled(job_id, name, loop)
        )
    except AudioExtractionError as exc:
        raise StageFailed(str(exc)) from exc

    await _set_stage(
        session,
        job_id,
        name,
        status=StageStatus.SUCCEEDED,
        metrics={
            "duration_s": round(result.duration_s, 2),
            "sample_rate": result.sample_rate,
            "wall_s": round(time.perf_counter() - started, 2),
        },
    )
    return result.path


async def _stage_transcribe(
    session: AsyncSession,
    job_id: UUID,
    video: Video,
    audio_path: Path,
    vad_filter: bool | None = None,
    vocabulary: str | None = None,
) -> int:
    name = StageName.TRANSCRIBE
    await _set_stage(session, job_id, name, status=StageStatus.RUNNING)

    settings = get_settings()
    loop = asyncio.get_running_loop()
    started = time.perf_counter()

    result = await transcribe(
        audio_path,
        model_name=settings.whisper_model,
        device=settings.whisper_device,
        language=settings.whisper_language or None,
        vad_filter=vad_filter if vad_filter is not None else settings.whisper_vad_filter,
        vad_threshold=settings.whisper_vad_threshold,
        vocabulary=vocabulary,
        progress=_throttled(job_id, name, loop),
    )

    # Replace rather than append: re-running must not duplicate the transcript.
    await session.execute(
        TranscriptSegment.__table__.delete().where(TranscriptSegment.video_id == video.id)
    )
    session.add_all(
        [
            TranscriptSegment(
                video_id=video.id,
                position=i,
                start_s=seg.start_s,
                end_s=seg.end_s,
                text=seg.text,
                avg_logprob=seg.avg_logprob,
                no_speech_prob=seg.no_speech_prob,
                compression_ratio=seg.compression_ratio,
                model=result.model,
            )
            for i, seg in enumerate(result.segments)
        ]
    )
    await session.commit()

    wall = time.perf_counter() - started
    await _set_stage(
        session,
        job_id,
        name,
        status=StageStatus.SUCCEEDED,
        metrics={
            "segments": len(result.segments),
            "vad_filter": vad_filter if vad_filter is not None else settings.whisper_vad_filter,
            "vocabulary": vocabulary or None,
            "dropped_repeats": result.dropped_segments,
            "language": result.language,
            "language_probability": result.language_probability,
            "model": result.model,
            "wall_s": round(wall, 2),
            "realtime_factor": round(result.duration_s / wall, 1) if wall > 0 else None,
        },
    )
    return len(result.segments)


async def _collect_ocr_units(
    session: AsyncSession, video_id: UUID
) -> list[tuple[float, float, str]]:
    """One `(start_s, end_s, text)` per keyframe that produced usable text."""
    rows = (
        await session.execute(
            select(KeyframeRow)
            .where(KeyframeRow.video_id == video_id)
            .options(selectinload(KeyframeRow.ocr_blocks))
            .order_by(KeyframeRow.position)
        )
    ).scalars().all()

    units: list[tuple[float, float, str]] = []
    for keyframe in rows:
        if not keyframe.ocr_blocks:
            continue
        # Reading order: top to bottom, then left to right. Blocks whose word
        # boundaries were lost are stored but not indexed — see ocr.is_indexable.
        ordered = sorted(
            (b for b in keyframe.ocr_blocks if is_indexable(b.text)),
            key=lambda b: ((b.bbox or {}).get("y1", 0), (b.bbox or {}).get("x1", 0)),
        )
        text = "\n".join(b.text for b in ordered).strip()
        if len(text) >= 8:  # a couple of stray words is not a retrievable unit
            units.append((keyframe.start_s, keyframe.end_s, text))
    return units


def _parent_at(parents, parent_ids: dict, t: float) -> UUID | None:
    """Id of the transcript parent whose span contains `t`."""
    found = None
    for parent in parents:
        if parent.start_s <= t:
            found = parent
        else:
            break
    return parent_ids.get(found.position) if found else None


async def _stage_keyframes(session: AsyncSession, job_id: UUID, video: Video) -> list[KeyframeRow]:
    """Scan for visually distinct moments and extract one frame each."""
    name = StageName.KEYFRAMES
    await _set_stage(session, job_id, name, status=StageStatus.RUNNING)

    settings = get_settings()
    storage = get_storage()
    source = storage.local_path(video.storage_key)
    if source is None:
        raise StageFailed("Non-local storage requires a temp download (not implemented)")

    loop = asyncio.get_running_loop()
    started = time.perf_counter()

    try:
        candidates = await scan_keyframes(
            source,
            min_gap_s=settings.keyframe_min_gap_s,
            max_gap_s=settings.keyframe_max_gap_s,
            threshold=settings.keyframe_threshold,
            max_keyframes=settings.keyframe_max_count,
            progress=_throttled(job_id, name, loop),
        )
    except KeyframeError as exc:
        raise StageFailed(str(exc)) from exc

    frames_dir = derived_dir(settings, video.id) / "keyframes"
    paths = await extract_keyframes(
        source, candidates, frames_dir, max_width=settings.keyframe_max_width
    )

    # Reconcile rather than replace.
    #
    # This used to delete every keyframe for the video and insert fresh rows.
    # That is correct in isolation and quietly destructive in practice: OCR
    # blocks hang off keyframe rows, so the delete cascaded and wiped them, and
    # if the *later* OCR stage then failed — a server restart mid-run is enough
    # — the video was left with frames carrying no text at all. Nothing
    # surfaced it, because the `ocr` chunks live in a different table and kept
    # answering searches. `on_screen_text` was silently None for the whole
    # corpus, taking the frame text badges, the `with_text_only` filter and the
    # "On screen at this moment" line of every answer prompt with it.
    #
    # Scanning is deterministic, so re-running normally produces the identical
    # frames. Matching on `(start_s, phash)` keeps those rows — and their OCR —
    # and touches only what genuinely changed.
    existing = (
        await session.execute(
            select(KeyframeRow).where(KeyframeRow.video_id == video.id)
        )
    ).scalars().all()
    by_identity = {(round(k.start_s, 2), k.phash): k for k in existing}

    rows: list[KeyframeRow] = []
    reused = 0
    for i, (candidate, path) in enumerate(zip(candidates, paths, strict=False)):
        storage_key = str(path.relative_to(settings.storage_local_path)).replace("\\", "/")
        identity = (round(candidate.start_s, 2), f"{candidate.phash:016x}")

        row = by_identity.pop(identity, None)
        if row is not None:
            # Same frame, possibly at a new index if neighbours changed.
            row.position = i
            row.end_s = candidate.end_s
            row.time_s = candidate.time_s
            row.storage_key = storage_key
            row.change = candidate.change
            reused += 1
        else:
            row = KeyframeRow(
                video_id=video.id,
                position=i,
                start_s=candidate.start_s,
                end_s=candidate.end_s,
                time_s=candidate.time_s,
                storage_key=storage_key,
                phash=f"{candidate.phash:016x}",
                change=candidate.change,
            )
            session.add(row)
        rows.append(row)

    # Whatever the new scan did not claim is genuinely gone from the video.
    for stale in by_identity.values():
        await session.delete(stale)
    await session.commit()

    await _set_stage(
        session, job_id, name, status=StageStatus.SUCCEEDED,
        metrics={
            "candidates": len(candidates),
            "extracted": len(rows),
            "reused": reused,
            "per_minute": round(len(rows) / max((video.duration_s or 1) / 60, 1), 2),
            "wall_s": round(time.perf_counter() - started, 2),
        },
    )
    return rows


async def _stage_ocr(
    session: AsyncSession, job_id: UUID, video: Video, keyframes: list[KeyframeRow]
) -> int:
    """Read text from keyframes.

    Quality is bounded by source resolution, not by the engine — see
    app/pipeline/ocr.py. Low-confidence blocks are dropped rather than stored.
    """
    name = StageName.OCR
    settings = get_settings()

    if not settings.ocr_enabled:
        await _set_stage(
            session, job_id, name, status=StageStatus.SKIPPED,
            metrics={"reason": "ocr disabled"},
        )
        return 0
    if not keyframes:
        await _set_stage(
            session, job_id, name, status=StageStatus.SKIPPED,
            metrics={"reason": "no keyframes"},
        )
        return 0

    await _set_stage(session, job_id, name, status=StageStatus.RUNNING)
    loop = asyncio.get_running_loop()
    started = time.perf_counter()

    root = settings.storage_local_path
    paths = [root / k.storage_key for k in keyframes]

    results = await read_frames(
        paths,
        min_confidence=settings.ocr_min_confidence,
        workers=settings.ocr_workers,
        progress=_throttled(job_id, name, loop),
    )

    # Clear what these frames already hold before writing the new read.
    #
    # This stage only ever appended. It got away with it because the keyframes
    # stage used to delete every frame first, which cascaded and took the old
    # blocks with it — so a re-run happened to start clean. Making keyframes
    # reconcile instead of replace (which it must, or a failed OCR run destroys
    # existing text) removed that accident, and a second OCR pass began
    # duplicating every block: measured at exactly 2.00x on a re-analysed clip.
    #
    # Duplicated blocks are not merely untidy. They are concatenated into
    # `on_screen_text` and into the `kind=ocr` chunks, so every line reached
    # search and the answer prompt twice over.
    await session.execute(
        OcrBlock.__table__.delete().where(
            OcrBlock.keyframe_id.in_([k.id for k in keyframes])
        )
    )
    await session.commit()

    by_path = {r.path: r for r in results}
    blocks: list[OcrBlock] = []
    frames_with_text = 0

    for keyframe, path in zip(keyframes, paths, strict=True):
        frame = by_path.get(path)
        if frame is None or not frame.blocks:
            continue
        frames_with_text += 1
        blocks.extend(
            OcrBlock(
                keyframe_id=keyframe.id,
                text=b.text,
                confidence=b.confidence,
                bbox={"x1": b.bbox[0], "y1": b.bbox[1], "x2": b.bbox[2], "y2": b.bbox[3]},
            )
            for b in frame.blocks
        )

    for start in range(0, len(blocks), 500):
        session.add_all(blocks[start : start + 500])
        await session.commit()

    confidences = [b.confidence for b in blocks]
    await _set_stage(
        session, job_id, name, status=StageStatus.SUCCEEDED,
        metrics={
            "frames": len(keyframes),
            "frames_with_text": frames_with_text,
            "blocks": len(blocks),
            "mean_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0,
            "wall_s": round(time.perf_counter() - started, 2),
        },
    )
    return len(blocks)


async def _stage_embed(session: AsyncSession, job_id: UUID, video: Video) -> int:
    """Chunk the transcript and embed the children.

    Both halves live in one stage because they are meaningless apart: chunks
    without vectors are not searchable, and vectors without their chunk rows
    have nothing to point at. Doing them together keeps the table consistent
    even if the job dies midway, since it is all one transaction boundary.
    """
    name = StageName.EMBED
    await _set_stage(session, job_id, name, status=StageStatus.RUNNING)

    settings = get_settings()
    loop = asyncio.get_running_loop()
    started = time.perf_counter()

    rows = (
        await session.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.video_id == video.id)
            .order_by(TranscriptSegment.position)
        )
    ).scalars().all()

    if not rows:
        await _set_stage(
            session, job_id, name, status=StageStatus.SKIPPED,
            metrics={"reason": "no transcript to chunk"},
        )
        return 0

    parents, children = build_chunks(
        [
            SourceSegment(
                start_s=r.start_s, end_s=r.end_s, text=r.text, speaker_id=r.speaker_id
            )
            for r in rows
        ]
    )

    # Replace rather than append: re-running must not duplicate chunks.
    await session.execute(Chunk.__table__.delete().where(Chunk.video_id == video.id))
    await session.commit()

    # Parents first, so children have real ids to point at.
    parent_models = [
        Chunk(
            video_id=video.id,
            kind=ChunkKind.TRANSCRIPT,
            level=ChunkLevel.PARENT,
            position=p.position,
            start_s=p.start_s,
            end_s=p.end_s,
            text=p.text,
            token_count=estimate_tokens(p.text),
            meta={"speakers": p.speakers} if p.speakers else None,
        )
        for p in parents
    ]
    session.add_all(parent_models)
    await session.commit()
    parent_ids = {m.position: m.id for m in parent_models}

    # OCR text becomes searchable alongside speech. Each keyframe's text is one
    # chunk spanning the segment that frame represents, so a hit on a slide
    # resolves to the moment it was on screen.
    ocr_units = await _collect_ocr_units(session, video.id)

    texts = [c.text for c in children] + [text for _, _, text in ocr_units]
    vectors = await embed_documents(
        texts,
        model_name=settings.embedding_model,
        device=settings.embedding_device,
        batch_size=settings.embedding_batch_size,
        progress=_throttled(job_id, name, loop),
    )
    transcript_vectors = vectors[: len(children)]
    ocr_vectors = vectors[len(children) :]

    session.add_all(
        [
            Chunk(
                video_id=video.id,
                parent_id=parent_ids.get(c.parent_position),
                kind=ChunkKind.TRANSCRIPT,
                level=ChunkLevel.CHILD,
                position=c.position,
                start_s=c.start_s,
                end_s=c.end_s,
                text=c.text,
                token_count=estimate_tokens(c.text),
                embedding=vector,
                embedding_model=settings.embedding_model,
                meta={"speakers": c.speakers} if c.speakers else None,
            )
            for c, vector in zip(children, transcript_vectors, strict=True)
        ]
    )
    await session.commit()

    if ocr_units:
        # Attach each OCR chunk to the transcript parent covering it, so
        # expanding a slide hit shows what was being said over it.
        session.add_all(
            [
                Chunk(
                    video_id=video.id,
                    parent_id=_parent_at(parents, parent_ids, start_s),
                    kind=ChunkKind.OCR,
                    level=ChunkLevel.CHILD,
                    position=i,
                    start_s=start_s,
                    end_s=end_s,
                    text=text,
                    token_count=estimate_tokens(text),
                    embedding=vector,
                    embedding_model=settings.embedding_model,
                    meta={"source": "ocr"},
                )
                for i, ((start_s, end_s, text), vector) in enumerate(
                    zip(ocr_units, ocr_vectors, strict=True)
                )
            ]
        )
        await session.commit()

    # Keep the embedder resident unless VRAM is needed elsewhere: search has to
    # embed every query, and a cold load costs ~7s against a 3s latency budget.
    if not settings.embedding_keep_warm:
        unload_embedder()

    wall = time.perf_counter() - started
    await _set_stage(
        session, job_id, name, status=StageStatus.SUCCEEDED,
        metrics={
            "parents": len(parents),
            "children": len(children),
            "model": settings.embedding_model,
            "dimensions": len(vectors[0]) if vectors else 0,
            "wall_s": round(wall, 2),
        },
    )
    return len(children)


async def _stage_events(session: AsyncSession, job_id: UUID, video: Video) -> int:
    """Derive events and topics from what the earlier stages produced.

    Runs after `embed` because topic segmentation reads chunk embeddings — the
    boundaries come from where meaning shifts, which is already encoded in the
    vectors rather than needing a second pass over the text.
    """
    name = StageName.EVENTS
    await _set_stage(session, job_id, name, status=StageStatus.RUNNING)
    started = time.perf_counter()

    # Replace rather than append: re-running must not duplicate the timeline.
    await session.execute(Event.__table__.delete().where(Event.video_id == video.id))
    await session.execute(Topic.__table__.delete().where(Topic.video_id == video.id))
    await session.commit()

    # ─ Rule-derived events ─
    keyframe_rows = (
        await session.execute(
            select(KeyframeRow)
            .where(KeyframeRow.video_id == video.id)
            .options(selectinload(KeyframeRow.ocr_blocks))
            .order_by(KeyframeRow.position)
        )
    ).scalars().all()

    keyframes = [
        {
            "id": k.id,
            "start_s": k.start_s,
            "end_s": k.end_s,
            "change": k.change,
            "text": " ".join(b.text for b in k.ocr_blocks),
        }
        for k in keyframe_rows
    ]

    transcript_spans = [
        (r.start_s, r.end_s)
        for r in (
            await session.execute(
                select(TranscriptSegment)
                .where(TranscriptSegment.video_id == video.id)
                .order_by(TranscriptSegment.position)
            )
        ).scalars().all()
    ]

    rule_events = [
        *scene_events(keyframes),
        *silence_events(transcript_spans),
        *text_events(keyframes),
    ]

    session.add_all(
        [
            Event(
                video_id=video.id,
                type=e.type,
                source=EventSource.RULE,
                start_s=e.start_s,
                end_s=e.end_s,
                title=e.title[:512],
                confidence=e.confidence,
                evidence={"refs": e.evidence} if e.evidence else None,
            )
            for e in rule_events
        ]
    )
    await session.commit()

    # ─ Topics, from embedding structure ─
    chunks = (
        await session.execute(
            select(Chunk)
            .where(
                Chunk.video_id == video.id,
                Chunk.level == ChunkLevel.CHILD,
                Chunk.kind == ChunkKind.TRANSCRIPT,
                Chunk.embedding.isnot(None),
            )
            .order_by(Chunk.start_s)
        )
    ).scalars().all()

    coarse_count = fine_count = 0
    if chunks:
        segments = [
            EventSegment(c.start_s, c.end_s, c.text, list(c.embedding)) for c in chunks
        ]
        coarse, fine = build_topic_hierarchy(segments)

        coarse_models = [
            Topic(
                video_id=video.id, position=t.position, depth=0,
                start_s=t.start_s, end_s=t.end_s, title=t.title,
                keywords={"terms": t.keywords}, boundary_strength=t.boundary_strength,
            )
            for t in coarse
        ]
        session.add_all(coarse_models)
        await session.commit()
        parent_ids = {m.position: m.id for m in coarse_models}

        session.add_all(
            [
                Topic(
                    video_id=video.id,
                    parent_id=parent_ids.get(locate_parent(coarse, t)),
                    position=t.position, depth=1,
                    start_s=t.start_s, end_s=t.end_s, title=t.title,
                    keywords={"terms": t.keywords}, boundary_strength=t.boundary_strength,
                )
                for t in fine
            ]
        )
        await session.commit()
        coarse_count, fine_count = len(coarse), len(fine)

        # A topic boundary is itself an event, so it appears on the timeline
        # alongside the visual ones.
        session.add_all(
            [
                Event(
                    video_id=video.id,
                    type=EventType.TOPIC_CHANGE,
                    # Not `rule`: this comes from embedding structure, not a
                    # deterministic rule over metadata.
                    source=EventSource.MODEL,
                    start_s=t.start_s, end_s=t.end_s, title=t.title[:512],
                    confidence=round(min(t.boundary_strength * 2, 1.0), 3),
                    evidence={"keywords": t.keywords},
                )
                for t in coarse[1:]  # the first span opens at 0s, not a change
            ]
        )
        await session.commit()

    by_type = Counter(e.type for e in rule_events)
    total = len(rule_events) + max(coarse_count - 1, 0)

    await _set_stage(
        session, job_id, name, status=StageStatus.SUCCEEDED,
        metrics={
            "events": total,
            "by_type": dict(by_type),
            "topics_coarse": coarse_count,
            "topics_fine": fine_count,
            "wall_s": round(time.perf_counter() - started, 2),
        },
    )
    return total


# Stages beyond what is built. Marked skipped so the UI shows honest state
# rather than a bar that will never move.
_NOT_YET_BUILT = (
    StageName.DIARIZE,
    StageName.CAPTION,
)


# ─── Orchestration ─────────────────────────────────────────────────────────


async def _load_keyframes(session: AsyncSession, video_id: UUID) -> list[KeyframeRow]:
    """Keyframes already in the database, for stages run without a fresh scan."""
    return list(
        (
            await session.execute(
                select(KeyframeRow)
                .where(KeyframeRow.video_id == video_id)
                .order_by(KeyframeRow.position)
            )
        ).scalars().all()
    )


async def run_job(job_id: UUID) -> None:
    """Execute one job. Never raises — failures are recorded on the job.

    **Stage selection.** Stages the caller did not ask for arrive already marked
    `skipped` by the API, and this reads that state rather than taking a
    parameter — so a restart cannot lose the request, and the job row stays
    self-describing about what it was asked to do.

    Skipped stages are not gaps. Each stage reads its inputs from the database
    when a prior stage did not produce them in this run: `embed` chunks whatever
    `transcript_segments` and `ocr_blocks` already exist, and `ocr` reads stored
    keyframes. That is what makes re-running the visual branch cost minutes
    instead of re-transcribing for half an hour.
    """
    async with SessionLocal() as session:
        job = (
            await session.execute(
                select(ProcessingJob)
                .where(ProcessingJob.id == job_id)
                .options(selectinload(ProcessingJob.stages))
            )
        ).scalars().first()

        if job is None:
            logger.warning("Job %s vanished before it ran", job_id)
            return

        video = await session.get(Video, job.video_id)
        if video is None:
            logger.warning("Video for job %s vanished", job_id)
            return

        # Pre-skipped stages are the caller's selection, captured at creation.
        wanted = {s.name for s in job.stages if s.status != StageStatus.SKIPPED}

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)
        video.status = VideoStatus.PROCESSING
        await session.commit()

        logger.info(
            "Job %s starting for %r (stages: %s)",
            str(job_id)[:8],
            video.title,
            ", ".join(n for n in StageName.ORDER if n in wanted) or "none",
        )

        try:
            if StageName.PROBE in wanted:
                await _stage_probe(session, job_id, video)

            # ─ Speech branch ─
            if not video.has_audio:
                # A real state, not a failure. Skip the whole speech branch.
                for name in (StageName.AUDIO_EXTRACT, StageName.TRANSCRIBE):
                    if name in wanted:
                        await _set_stage(
                            session, job_id, name, status=StageStatus.SKIPPED,
                            metrics={"reason": "no audio stream"},
                        )
            elif StageName.TRANSCRIBE in wanted or StageName.AUDIO_EXTRACT in wanted:
                settings = get_settings()
                audio_path = (
                    derived_dir(settings, video.id) / "audio16k.wav"
                )
                # Transcription needs the WAV; extract it if this run did not,
                # and a previous run left nothing behind.
                if StageName.AUDIO_EXTRACT in wanted or not audio_path.exists():
                    audio_path = await _stage_audio(session, job_id, video)
                if StageName.TRANSCRIBE in wanted:
                    await _stage_transcribe(
                        session, job_id, video, audio_path,
                        vad_filter=(job.options or {}).get("vad_filter"),
                        vocabulary=(job.options or {}).get("vocabulary"),
                    )

            # ─ Visual branch ─
            # Failures here are non-critical: a broken OCR stage must not discard
            # a perfectly good transcript, so the job degrades to partial success.
            if StageName.KEYFRAMES in wanted or StageName.OCR in wanted:
                try:
                    keyframes = (
                        await _stage_keyframes(session, job_id, video)
                        if StageName.KEYFRAMES in wanted
                        else await _load_keyframes(session, video.id)
                    )
                    if StageName.OCR in wanted:
                        await _stage_ocr(session, job_id, video, keyframes)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Visual stages failed for %s: %s", video.id, exc, exc_info=True
                    )
                    for stage in (StageName.KEYFRAMES, StageName.OCR):
                        if stage in wanted:
                            await _set_stage(
                                session, job_id, stage, status=StageStatus.FAILED, error=str(exc)
                            )

            if StageName.EMBED in wanted:
                await _stage_embed(session, job_id, video)

            # After embed: topic boundaries are read from chunk embeddings.
            if StageName.EVENTS in wanted:
                await _stage_events(session, job_id, video)

            for name in _NOT_YET_BUILT:
                if name in wanted:
                    await _set_stage(
                        session, job_id, name, status=StageStatus.SKIPPED,
                        metrics={"reason": "not implemented yet"},
                    )

            job.status = JobStatus.SUCCEEDED
            video.status = VideoStatus.READY
            logger.info("Job %s succeeded", str(job_id)[:8])

        except Exception as exc:
            logger.exception("Job %s failed", str(job_id)[:8])
            job.status = JobStatus.FAILED
            job.error = str(exc)
            video.status = VideoStatus.FAILED
            video.error = str(exc)

            # Whichever stage was mid-flight owns the error.
            await session.execute(
                update(JobStage)
                .where(JobStage.job_id == job_id, JobStage.status == StageStatus.RUNNING)
                .values(status=StageStatus.FAILED, error=str(exc), finished_at=datetime.now(UTC))
            )

        finally:
            job.finished_at = datetime.now(UTC)
            await session.commit()


# ─── In-process queue ──────────────────────────────────────────────────────


async def _consume() -> None:
    assert _queue is not None
    while True:
        job_id = await _queue.get()
        try:
            await run_job(job_id)
        except Exception:  # noqa: BLE001 — the consumer must never die
            logger.exception("Unhandled error running job %s", job_id)
        finally:
            _queue.task_done()


async def start_worker() -> None:
    global _queue, _consumer
    if _consumer is not None:
        return
    _queue = asyncio.Queue()
    _consumer = asyncio.create_task(_consume(), name="echolens-worker")
    logger.info("In-process worker started (single consumer; GPU stages serialised)")


async def stop_worker() -> None:
    global _queue, _consumer
    if _consumer is not None:
        _consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _consumer
    _consumer, _queue = None, None


async def enqueue(job_id: UUID) -> None:
    if _queue is None:
        raise RuntimeError("Worker is not running")
    await _queue.put(job_id)


def queue_depth() -> int:
    return _queue.qsize() if _queue is not None else 0


async def reap_orphaned_jobs() -> int:
    """Fail jobs left RUNNING by a previous process.

    The in-process queue does not survive a restart, so a job still marked
    running at boot was interrupted. Saying so beats a progress bar that will
    never advance.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.status == JobStatus.RUNNING)
            .values(
                status=JobStatus.FAILED,
                error="Interrupted by a server restart",
                finished_at=datetime.now(UTC),
            )
        )
        await session.execute(
            update(JobStage)
            .where(JobStage.status == StageStatus.RUNNING)
            .values(status=StageStatus.FAILED, error="Interrupted by a server restart")
        )
        await session.commit()
        return result.rowcount or 0
