"""One-off migration from the SQLite dev bridge to Postgres.

The SQLite bridge carried Phases 1-2 while Docker was unavailable. Phase 3 needs
pgvector, so this moves the accumulated rows across. Video *files* are untouched:
they live in the object store and are referenced by `storage_key`, so only
database rows move.

Usage (with ECHOLENS_DATABASE_URL already pointing at Postgres):

    python backend/scripts/migrate_sqlite_to_postgres.py echolens.db

Idempotent: rows whose id already exists in Postgres are skipped, so a partial
run can simply be repeated.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import JobStage, ProcessingJob, TranscriptSegment, Video  # noqa: E402


def _uuid(value: object) -> uuid.UUID | None:
    """SQLAlchemy's Uuid type stores 32-char hex on SQLite."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    # SQLite writes "YYYY-MM-DD HH:MM:SS[.ffffff]"; fromisoformat wants a T.
    return datetime.fromisoformat(text.replace(" ", "T"))


def _json(value: object) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


async def main(sqlite_path: Path) -> int:
    settings = get_settings()
    if settings.is_sqlite:
        print("ECHOLENS_DATABASE_URL still points at SQLite — nothing to migrate into.")
        return 1
    if not sqlite_path.exists():
        print(f"Source not found: {sqlite_path}")
        return 1

    print(f"source: {sqlite_path}")
    print(f"target: {settings.database_url.split('@')[-1]}\n")

    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row

    counts: dict[str, int] = {}

    async with SessionLocal() as session:
        # ─ videos ─
        existing = {
            r for r in (await session.execute(select(Video.id))).scalars().all()
        }
        added = 0
        for row in conn.execute("SELECT * FROM videos"):
            vid = _uuid(row["id"])
            if vid in existing:
                continue
            session.add(
                Video(
                    id=vid,
                    title=row["title"],
                    description=row["description"],
                    original_filename=row["original_filename"],
                    storage_key=row["storage_key"],
                    mime_type=row["mime_type"],
                    size_bytes=row["size_bytes"],
                    checksum_sha256=row["checksum_sha256"],
                    status=row["status"],
                    error=row["error"],
                    duration_s=row["duration_s"],
                    width=row["width"],
                    height=row["height"],
                    fps=row["fps"],
                    video_codec=row["video_codec"],
                    audio_codec=row["audio_codec"],
                    has_audio=bool(row["has_audio"]),
                    audio_channels=row["audio_channels"],
                    audio_sample_rate=row["audio_sample_rate"],
                    created_at=_dt(row["created_at"]),
                    updated_at=_dt(row["updated_at"]),
                )
            )
            added += 1
        await session.commit()
        counts["videos"] = added

        # ─ processing_jobs ─
        existing_jobs = set(
            (await session.execute(select(ProcessingJob.id))).scalars().all()
        )
        added = 0
        for row in conn.execute("SELECT * FROM processing_jobs"):
            jid = _uuid(row["id"])
            if jid in existing_jobs:
                continue
            session.add(
                ProcessingJob(
                    id=jid,
                    video_id=_uuid(row["video_id"]),
                    status=row["status"],
                    error=row["error"],
                    created_at=_dt(row["created_at"]),
                    started_at=_dt(row["started_at"]),
                    finished_at=_dt(row["finished_at"]),
                )
            )
            added += 1
        await session.commit()
        counts["processing_jobs"] = added

        # ─ job_stages ─
        existing_stages = set(
            (await session.execute(select(JobStage.id))).scalars().all()
        )
        added = 0
        for row in conn.execute("SELECT * FROM job_stages"):
            sid = _uuid(row["id"])
            if sid in existing_stages:
                continue
            session.add(
                JobStage(
                    id=sid,
                    job_id=_uuid(row["job_id"]),
                    name=row["name"],
                    position=row["position"],
                    status=row["status"],
                    progress=row["progress"],
                    started_at=_dt(row["started_at"]),
                    finished_at=_dt(row["finished_at"]),
                    error=row["error"],
                    metrics=_json(row["metrics"]),
                )
            )
            added += 1
        await session.commit()
        counts["job_stages"] = added

        # ─ transcript_segments ─ batched: a 6-hour video is ~6,600 rows
        existing_segs = set(
            (await session.execute(select(TranscriptSegment.id))).scalars().all()
        )
        added, batch = 0, []
        for row in conn.execute("SELECT * FROM transcript_segments"):
            tid = _uuid(row["id"])
            if tid in existing_segs:
                continue
            batch.append(
                TranscriptSegment(
                    id=tid,
                    video_id=_uuid(row["video_id"]),
                    position=row["position"],
                    start_s=row["start_s"],
                    end_s=row["end_s"],
                    text=row["text"],
                    speaker_id=row["speaker_id"],
                    avg_logprob=row["avg_logprob"],
                    no_speech_prob=row["no_speech_prob"],
                    compression_ratio=row["compression_ratio"],
                    model=row["model"],
                    created_at=_dt(row["created_at"]),
                )
            )
            added += 1
            if len(batch) >= 1000:
                session.add_all(batch)
                await session.commit()
                batch = []
        if batch:
            session.add_all(batch)
            await session.commit()
        counts["transcript_segments"] = added

        conn.close()

        print("migrated:")
        for table, n in counts.items():
            print(f"  {table:22} {n:>6}")

        print("\npostgres totals:")
        for model, label in (
            (Video, "videos"),
            (ProcessingJob, "processing_jobs"),
            (JobStage, "job_stages"),
            (TranscriptSegment, "transcript_segments"),
        ):
            total = (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
            print(f"  {label:22} {total:>6}")

    return 0


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("echolens.db")
    raise SystemExit(asyncio.run(main(source)))
