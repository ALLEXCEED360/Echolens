"""ORM models.

Phase 1 only: videos and the job records that will drive the processing pipeline.
The full target schema — chunks, keyframes, events, topics — is specified in
docs/01-data-model.md and lands in later phases.

Two conventions hold across the whole schema and must not drift:

1. **Time is float seconds from container origin.** Never frame numbers, never
   timecode strings. Formatting happens in the UI.
2. **Enum-like columns are String, not native DB enums** (decision D7). Keeps the
   SQLite bridge working and avoids ALTER TYPE migrations.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON as SAJson
from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import Uuid as SAUuid
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# ─── Status vocabularies ───────────────────────────────────────────────────


class VideoStatus:
    UPLOADING: Final = "uploading"
    UPLOADED: Final = "uploaded"
    PROCESSING: Final = "processing"
    READY: Final = "ready"
    FAILED: Final = "failed"
    ALL: Final = frozenset({UPLOADING, UPLOADED, PROCESSING, READY, FAILED})


class JobStatus:
    QUEUED: Final = "queued"
    RUNNING: Final = "running"
    SUCCEEDED: Final = "succeeded"
    FAILED: Final = "failed"
    CANCELLED: Final = "cancelled"
    ALL: Final = frozenset({QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED})


class StageStatus:
    WAITING: Final = "waiting"
    RUNNING: Final = "running"
    SUCCEEDED: Final = "succeeded"
    FAILED: Final = "failed"
    # A real state, not an error: a silent video skips the entire speech branch
    # and the UI must say so rather than showing a stalled bar.
    SKIPPED: Final = "skipped"
    ALL: Final = frozenset({WAITING, RUNNING, SUCCEEDED, FAILED, SKIPPED})


class StageName:
    PROBE: Final = "probe"
    AUDIO_EXTRACT: Final = "audio_extract"
    TRANSCRIBE: Final = "transcribe"
    DIARIZE: Final = "diarize"
    KEYFRAMES: Final = "keyframes"
    OCR: Final = "ocr"
    CAPTION: Final = "caption"
    EVENTS: Final = "events"
    EMBED: Final = "embed"

    # Declaration order is execution order for display purposes.
    ORDER: Final = (
        PROBE,
        AUDIO_EXTRACT,
        TRANSCRIBE,
        DIARIZE,
        KEYFRAMES,
        OCR,
        CAPTION,
        EVENTS,
        EMBED,
    )

    # Stages requiring exclusive GPU access. The worker serialises these —
    # 8 GB will not hold Whisper and a VLM simultaneously.
    GPU_BOUND: Final = frozenset({TRANSCRIBE, DIARIZE, CAPTION, EMBED})

    # If any of these fail the job has failed. Everything else degrades to
    # partial success: a broken OCR stage must not discard a good transcript.
    CRITICAL_PATH: Final = frozenset({PROBE, TRANSCRIBE, EMBED})


# ─── Models ────────────────────────────────────────────────────────────────


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)


class Collection(Base):
    """A named group of videos — a course, a conference, a project.

    Collections are the unit cross-video questions are asked over. Without them
    "compare how these lectures treat backpropagation" has no way to say which
    lectures, and a growing corpus makes every query noisier.
    """

    __tablename__ = "collections"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    videos: Mapped[list[Video]] = relationship(
        back_populates="collection", order_by="Video.created_at"
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = _uuid_pk()

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Null means unfiled. Deleting a collection releases its videos rather than
    # destroying them — losing a 6-hour transcript to a tidy-up would be absurd.
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        SAUuid, ForeignKey("collections.id", ondelete="SET NULL"), index=True
    )

    # ─ Source ─
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Computed while streaming the upload; the dedupe key.
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), index=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=VideoStatus.UPLOADING, index=True
    )
    error: Mapped[str | None] = mapped_column(Text)

    # ─ Probed metadata: null until the probe stage runs ─
    duration_s: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    video_codec: Mapped[str | None] = mapped_column(String(64))
    audio_codec: Mapped[str | None] = mapped_column(String(64))
    # Gates the entire speech branch of the pipeline.
    has_audio: Mapped[bool] = mapped_column(default=False, nullable=False)
    audio_channels: Mapped[int | None] = mapped_column(Integer)
    audio_sample_rate: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    collection: Mapped[Collection | None] = relationship(back_populates="videos")
    jobs: Mapped[list[ProcessingJob]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
        order_by="ProcessingJob.seq.desc()",
    )

    __table_args__ = (Index("ix_videos_created_at", "created_at"),)


class ProcessingJob(Base):
    """One row per processing *attempt*.

    Re-running a video creates a new job rather than mutating the old one —
    videos are immutable once uploaded, so every stage is safely retryable and
    the history is worth keeping.
    """

    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Monotonic insertion order, independent of any clock.
    #
    # "Most recent job" was ordered by created_at, which broke when the host
    # clock stepped backwards an hour: finished jobs were stamped *ahead* of a
    # running one, so the UI polled a stale record. Wall time is not a reliable
    # ordering key — NTP corrects, DST shifts, and rows arrive from other hosts.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False, unique=True)
    video_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobStatus.QUEUED, index=True
    )
    error: Mapped[str | None] = mapped_column(Text)

    # Per-run choices that are not derivable from the stage plan.
    #
    # Some settings are properties of *this media*, not of the installation:
    # whether voice-activity detection should run depends on whether the audio
    # is a lecture or a firefight. Holding that only in server configuration
    # made it unusable — changing it meant restarting the API with an
    # environment variable, and the choice was lost the moment the video was
    # re-uploaded. Recorded on the job so it survives, is visible afterwards,
    # and can be set per request.
    options: Mapped[dict] = mapped_column(SAJson, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    video: Mapped[Video] = relationship(back_populates="jobs")
    stages: Mapped[list[JobStage]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobStage.position"
    )

    @property
    def progress(self) -> float:
        """Mean progress across stages, skipped stages counting as complete."""
        if not self.stages:
            return 0.0
        return sum(s.progress for s in self.stages) / len(self.stages)


class JobStage(Base):
    """Per-stage progress, driving the pipeline UI.

    Speech    ✓
    Frames    ✓
    OCR       ⋯ 64%
    Events    waiting
    """

    __tablename__ = "job_stages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=StageStatus.WAITING)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    # Stage-specific: model used, wall time, item counts.
    metrics: Mapped[dict | None] = mapped_column(SAJson)

    job: Mapped[ProcessingJob] = relationship(back_populates="stages")

    __table_args__ = (Index("ix_job_stages_job_position", "job_id", "position"),)


class TranscriptSegment(Base):
    """Raw ASR output, stored verbatim.

    This table is the audit trail and must stay unmodified: chunking, merging
    and embedding all happen downstream into `chunks` (Phase 3). When a
    retrieval result looks wrong, this is where you check what was actually
    said versus what the pipeline made of it.
    """

    __tablename__ = "transcript_segments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    video_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Ordinal within the video — stable sort key that survives equal timestamps.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # Speaker attribution arrives with diarization; null until then.
    speaker_id: Mapped[str | None] = mapped_column(String(64), index=True)

    # Quality signals from the decoder, kept for filtering and for diagnosing
    # bad stretches later. avg_logprob near 0 is confident; very negative is not.
    avg_logprob: Mapped[float | None] = mapped_column(Float)
    no_speech_prob: Mapped[float | None] = mapped_column(Float)
    compression_ratio: Mapped[float | None] = mapped_column(Float)

    model: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # The workhorse index: timeline rendering and "what was said near here".
        Index("ix_transcript_segments_video_start", "video_id", "start_s"),
        Index("ix_transcript_segments_video_position", "video_id", "position"),
    )


# Dimension of bge-large-en-v1.5. Changing the embedding model means a new
# migration and a full re-embed — the column type carries the dimension.
EMBEDDING_DIM: Final = 1024


class ChunkKind:
    TRANSCRIPT: Final = "transcript"
    OCR: Final = "ocr"          # Phase 4
    CAPTION: Final = "caption"  # Phase 4
    EVENT: Final = "event"      # Phase 5
    ALL: Final = frozenset({TRANSCRIPT, OCR, CAPTION, EVENT})


class ChunkLevel:
    CHILD: Final = "child"    # ~15s, embedded — the precision unit
    PARENT: Final = "parent"  # ~60s, returned to the LLM — the comprehension unit
    ALL: Final = frozenset({CHILD, PARENT})


class Chunk(Base):
    """The retrieval unit.

    **Parent-child retrieval.** Whisper's native segments average ~3s, too small
    to embed meaningfully — they lose the context that makes them findable. So
    child chunks (~15s) are embedded for precision, and the enclosing parent
    (~60s) is what actually reaches the LLM. This single decision buys more
    retrieval quality than any reranker.

    Both the vector and the lexical index live on this table, because hybrid
    search fuses them and a split store would mean two things to keep consistent.
    """

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    video_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Child chunks point at the parent whose span contains them.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        SAUuid, ForeignKey("chunks.id", ondelete="CASCADE"), index=True
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False, default=ChunkKind.TRANSCRIPT)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Null on parents: only children are embedded.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(128))

    # Lexical half of hybrid search, maintained by Postgres. A stored generated
    # column beats calling to_tsvector() per query, and it can be reused for
    # ts_rank scoring. This is where exact strings like "RigidBody2D" get found —
    # precisely what embeddings are worst at.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )

    # Speaker, slide number, source segment ids, confidence.
    meta: Mapped[dict | None] = mapped_column(SAJson)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_chunks_video_start", "video_id", "start_s"),
        Index("ix_chunks_video_level", "video_id", "level"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        # Approximate nearest-neighbour index for the semantic retriever.
        # Declared here rather than created at runtime so the schema stays
        # reproducible and `alembic check` stays clean. m/ef_construction are
        # pgvector's defaults; raising them trades build time for recall.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Keyframe(Base):
    """One visually stable span, represented by a single extracted frame.

    Rows are per *stable segment*, not per sampled frame. A slide on screen for
    four minutes is 7,200 near-identical frames and exactly one row here — which
    is what makes OCR and captioning affordable at all (docs/02-pipeline.md).
    """

    __tablename__ = "keyframes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    video_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # The span this frame represents, and the instant it was taken from.
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    time_s: Mapped[float] = mapped_column(Float, nullable=False)

    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)

    # Perceptual hash as text: 64-bit values exceed a signed BIGINT unsigned range
    # and are only ever compared, never summed.
    phash: Mapped[str | None] = mapped_column(String(32))
    # Hamming distance from the previous keyframe — how much changed here.
    change: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    ocr_blocks: Mapped[list[OcrBlock]] = relationship(
        back_populates="keyframe", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_keyframes_video_start", "video_id", "start_s"),
        Index("ix_keyframes_video_position", "video_id", "position"),
    )


class EventSource:
    """How an event was derived. Never blur these.

    Rule-derived events are deterministic and cheap. Model-derived ones are a
    summarisation pass over aligned streams. Keeping the distinction in the data
    means a precision problem can always be traced to the half that caused it.
    """

    RULE: Final = "rule"
    MODEL: Final = "model"
    ALL: Final = frozenset({RULE, MODEL})


class EventType:
    # ─ Rule-derived: cheap, deterministic, high precision ─
    SCENE_CHANGE: Final = "scene_change"
    SLIDE_CHANGE: Final = "slide_change"
    SILENCE: Final = "silence"
    TEXT_APPEARED: Final = "text_appeared"
    SPEAKER_CHANGE: Final = "speaker_change"  # needs diarization (Phase 2.5)

    # ─ Derived from embedding structure rather than rules or an LLM ─
    TOPIC_CHANGE: Final = "topic_change"

    ALL: Final = frozenset(
        {SCENE_CHANGE, SLIDE_CHANGE, SILENCE, TEXT_APPEARED, SPEAKER_CHANGE, TOPIC_CHANGE}
    )


class Event(Base):
    """A moment worth navigating to.

    Events are the timeline's structure: the answer to "what happened in this
    video" without watching it. Each carries `evidence` — the records it was
    derived from — so a marker can always be traced back to why it exists.
    """

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    video_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )

    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=EventSource.RULE)

    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    # Human-readable, shown on the timeline.
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # References to the rows this was derived from: [{kind, id, start_s}, ...].
    evidence: Mapped[dict | None] = mapped_column(SAJson)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_events_video_start", "video_id", "start_s"),
        Index("ix_events_video_type", "video_id", "type"),
    )


class Topic(Base):
    """A contiguous span of the video about one thing.

    Topics form the timeline hierarchy from docs/02-pipeline.md — the
    "Introduction / Neural Networks / Backpropagation / Q&A" structure. Unlike
    events, which are points of change, topics tile the video end to end.
    """

    __tablename__ = "topics"

    id: Mapped[uuid.UUID] = _uuid_pk()
    video_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        SAUuid, ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    # Distinctive terms for this span, highest-scoring first.
    keywords: Mapped[dict | None] = mapped_column(SAJson)
    # How sharp the boundary that opened this topic was.
    boundary_strength: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_topics_video_start", "video_id", "start_s"),
        Index("ix_topics_video_position", "video_id", "position"),
    )


class OcrBlock(Base):
    """Raw OCR output, stored verbatim as the audit trail.

    Kept separate from `chunks` for the same reason `transcript_segments` is:
    when a retrieval result looks wrong, this is where you check what the
    recogniser actually saw versus what the pipeline made of it.
    """

    __tablename__ = "ocr_blocks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    keyframe_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("keyframes.id", ondelete="CASCADE"), nullable=False, index=True
    )

    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # [x1, y1, x2, y2] in source pixels.
    bbox: Mapped[dict | None] = mapped_column(SAJson)
    engine: Mapped[str] = mapped_column(String(64), nullable=False, default="rapidocr")

    keyframe: Mapped[Keyframe] = relationship(back_populates="ocr_blocks")
