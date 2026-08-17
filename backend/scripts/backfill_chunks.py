"""Chunk and embed transcripts that predate Phase 3.

Videos transcribed before chunking existed have `transcript_segments` but no
`chunks`. This builds them without re-running Whisper — the transcript is
already correct, only the retrieval layer is missing.

    python backend/scripts/backfill_chunks.py            # all unindexed videos
    python backend/scripts/backfill_chunks.py --force    # re-chunk everything
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Chunk, ChunkKind, ChunkLevel, TranscriptSegment, Video  # noqa: E402
from app.pipeline.chunking import SourceSegment, build_chunks, estimate_tokens  # noqa: E402
from app.pipeline.embedding import embed_documents  # noqa: E402
from app.pipeline.runner import _collect_ocr_units, _parent_at  # noqa: E402


async def backfill_video(session, video: Video, settings) -> dict:
    rows = (
        await session.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.video_id == video.id)
            .order_by(TranscriptSegment.position)
        )
    ).scalars().all()

    if not rows:
        return {"video": video.title, "skipped": "no transcript"}

    started = time.perf_counter()
    parents, children = build_chunks(
        [
            SourceSegment(r.start_s, r.end_s, r.text, r.speaker_id)
            for r in rows
        ]
    )

    await session.execute(Chunk.__table__.delete().where(Chunk.video_id == video.id))
    await session.commit()

    parent_models = [
        Chunk(
            video_id=video.id, kind=ChunkKind.TRANSCRIPT, level=ChunkLevel.PARENT,
            position=p.position, start_s=p.start_s, end_s=p.end_s, text=p.text,
            token_count=estimate_tokens(p.text),
            meta={"speakers": p.speakers} if p.speakers else None,
        )
        for p in parents
    ]
    session.add_all(parent_models)
    await session.commit()
    parent_ids = {m.position: m.id for m in parent_models}

    # OCR text is indexed alongside speech, exactly as the embed stage does it.
    ocr_units = await _collect_ocr_units(session, video.id)

    print(
        f"    embedding {len(children):,} transcript + {len(ocr_units):,} visual chunks…",
        flush=True,
    )
    last = [0.0]

    def progress(fraction: float) -> None:
        if fraction - last[0] >= 0.1 or fraction >= 1.0:
            last[0] = fraction
            print(f"      {fraction:.0%}", flush=True)

    vectors = await embed_documents(
        [c.text for c in children] + [t for _, _, t in ocr_units],
        model_name=settings.embedding_model,
        device=settings.embedding_device,
        batch_size=settings.embedding_batch_size,
        progress=progress,
    )

    transcript_vectors = vectors[: len(children)]
    ocr_vectors = vectors[len(children) :]

    # Insert in batches: a 6-hour video is a few thousand 1024-dim vectors.
    batch: list[Chunk] = []
    for c, vector in zip(children, transcript_vectors, strict=True):
        batch.append(
            Chunk(
                video_id=video.id, parent_id=parent_ids.get(c.parent_position),
                kind=ChunkKind.TRANSCRIPT, level=ChunkLevel.CHILD,
                position=c.position, start_s=c.start_s, end_s=c.end_s, text=c.text,
                token_count=estimate_tokens(c.text),
                embedding=vector, embedding_model=settings.embedding_model,
                meta={"speakers": c.speakers} if c.speakers else None,
            )
        )
        if len(batch) >= 500:
            session.add_all(batch)
            await session.commit()
            batch = []
    if batch:
        session.add_all(batch)
        await session.commit()

    if ocr_units:
        session.add_all(
            [
                Chunk(
                    video_id=video.id,
                    parent_id=_parent_at(parents, parent_ids, start_s),
                    kind=ChunkKind.OCR, level=ChunkLevel.CHILD, position=i,
                    start_s=start_s, end_s=end_s, text=text,
                    token_count=estimate_tokens(text),
                    embedding=vector, embedding_model=settings.embedding_model,
                    meta={"source": "ocr"},
                )
                for i, ((start_s, end_s, text), vector) in enumerate(
                    zip(ocr_units, ocr_vectors, strict=True)
                )
            ]
        )
        await session.commit()

    return {
        "video": video.title,
        "segments": len(rows),
        "parents": len(parents),
        "children": len(children),
        "ocr_chunks": len(ocr_units),
        "wall_s": round(time.perf_counter() - started, 1),
    }


async def main(force: bool) -> int:
    settings = get_settings()
    if settings.is_sqlite:
        print("Postgres required — SQLite has no vector support.")
        return 1

    async with SessionLocal() as session:
        videos = (await session.execute(select(Video).order_by(Video.created_at))).scalars().all()
        if not videos:
            print("No videos.")
            return 0

        for video in videos:
            existing = (
                await session.execute(
                    select(func.count()).select_from(Chunk).where(Chunk.video_id == video.id)
                )
            ).scalar_one()

            if existing and not force:
                print(f"  {video.title}: already has {existing:,} chunks (use --force to redo)")
                continue

            print(f"  {video.title}:", flush=True)
            result = await backfill_video(session, video, settings)
            if "skipped" in result:
                print(f"    skipped — {result['skipped']}")
            else:
                print(
                    f"    {result['segments']:,} segments -> {result['parents']:,} parents, "
                    f"{result['children']:,} children, "
                    f"{result['ocr_chunks']:,} visual in {result['wall_s']}s"
                )

        total = (await session.execute(select(func.count()).select_from(Chunk))).scalar_one()
        print(f"done. {total:,} chunks indexed.")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force", action="store_true", help="re-chunk videos that already have chunks"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.force)))
