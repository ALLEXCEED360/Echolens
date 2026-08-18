"""Pydantic request/response models — the API contract."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ─── Videos ────────────────────────────────────────────────────────────────


class VideoSummary(ORMModel):
    """List-view payload. Deliberately excludes codec detail."""

    id: uuid.UUID
    title: str
    status: str
    duration_s: float | None
    width: int | None
    height: int | None
    size_bytes: int
    has_audio: bool
    created_at: datetime
    # First extracted keyframe, when the visual stage has run. `None` simply
    # means there is no frame to show yet, not an error.
    poster_url: str | None = None


class VideoDetail(VideoSummary):
    description: str | None
    original_filename: str
    mime_type: str
    checksum_sha256: str | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    audio_channels: int | None
    audio_sample_rate: int | None
    error: str | None
    updated_at: datetime


class VideoUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("title")
    @classmethod
    def _title_is_not_blank(cls, value: str | None) -> str | None:
        """Reject a title made only of whitespace.

        `min_length=1` is satisfied by a single space, which would leave the
        library showing a row with no name and no obvious way to fix it.
        """
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Title cannot be blank")
        return cleaned


class VideoList(BaseModel):
    items: list[VideoSummary]
    total: int
    limit: int
    offset: int


# ─── Collections ───────────────────────────────────────────────────────────


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=10_000)


class CollectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=10_000)


class CollectionSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    video_count: int
    # How many of those videos actually have chunks — an unprocessed video is
    # in the collection but invisible to search.
    indexed_count: int
    total_duration_s: float
    created_at: datetime


class CollectionDetail(CollectionSummary):
    videos: list[VideoSummary]


class CollectionList(BaseModel):
    items: list[CollectionSummary]
    total: int
    unfiled_videos: int


# ─── Concept timelines ─────────────────────────────────────────────────────


class OccurrenceOut(BaseModel):
    chunk_id: uuid.UUID
    start_s: float
    end_s: float
    text: str
    relevance: float | None
    topic_title: str | None


class VideoTrackOut(BaseModel):
    video_id: uuid.UUID
    video_title: str
    duration_s: float | None
    occurrences: list[OccurrenceOut]


class ConceptTimelineOut(BaseModel):
    query: str
    tracks: list[VideoTrackOut]
    total_occurrences: int
    # Earliest confident mention within the best-covered video.
    first_video_id: uuid.UUID | None
    first_video_title: str | None
    first_start_s: float | None
    took_ms: float


# ─── Jobs ──────────────────────────────────────────────────────────────────


class JobStageOut(ORMModel):
    name: str
    position: int
    status: str
    progress: float
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    metrics: dict | None


class JobOut(ORMModel):
    id: uuid.UUID
    video_id: uuid.UUID
    status: str
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress: float
    stages: list[JobStageOut]


# ─── Transcript ────────────────────────────────────────────────────────────


class TranscriptSegmentOut(ORMModel):
    id: uuid.UUID
    position: int
    start_s: float
    end_s: float
    text: str
    speaker_id: str | None
    # Mean token log-probability. Near 0 is confident; very negative is not.
    avg_logprob: float | None
    no_speech_prob: float | None


class TranscriptOut(BaseModel):
    video_id: uuid.UUID
    segments: list[TranscriptSegmentOut]
    total: int
    model: str | None
    # Total speech duration, which is less than video duration once silence is
    # filtered — useful for spotting a transcript that only half-covers a video.
    speech_duration_s: float


class TranscriptSearchHit(ORMModel):
    id: uuid.UUID
    video_id: uuid.UUID
    start_s: float
    end_s: float
    text: str
    speaker_id: str | None


class TranscriptSearchResults(BaseModel):
    query: str
    hits: list[TranscriptSearchHit]
    total: int


# ─── Keyframes ─────────────────────────────────────────────────────────────


class OcrBlockOut(ORMModel):
    text: str
    confidence: float
    bbox: dict | None


class KeyframeOut(ORMModel):
    id: uuid.UUID
    position: int
    # The span this frame represents, and the instant it was taken from.
    start_s: float
    end_s: float
    time_s: float
    # Hamming distance from the previous keyframe — how much changed here.
    change: int
    image_url: str
    # OCR blocks joined in reading order; empty when the frame had no usable text.
    text: str
    ocr_blocks: list[OcrBlockOut]


class KeyframeList(BaseModel):
    video_id: uuid.UUID
    items: list[KeyframeOut]
    total: int


# ─── Timeline ──────────────────────────────────────────────────────────────


class EventOut(ORMModel):
    id: uuid.UUID
    type: str
    # "rule" (deterministic) or "model" (derived from embedding structure).
    source: str
    start_s: float
    end_s: float
    title: str
    confidence: float
    evidence: dict | None


class EventList(BaseModel):
    video_id: uuid.UUID
    items: list[EventOut]
    total: int
    by_type: dict[str, int]


class TopicNode(BaseModel):
    id: uuid.UUID
    position: int
    depth: int
    start_s: float
    end_s: float
    title: str
    keywords: list[str]
    # How sharp the boundary that opened this topic was.
    boundary_strength: float
    children: list[TopicNode] = []


class TopicTree(BaseModel):
    video_id: uuid.UUID
    items: list[TopicNode]
    total: int
    coarse: int
    fine: int


# ─── Search ────────────────────────────────────────────────────────────────


class TemporalContext(BaseModel):
    """What else the pipeline recorded around this moment."""

    keyframe_id: uuid.UUID | None = None
    keyframe_time_s: float | None = None
    # OCR text from the frame on screen when this was said.
    on_screen_text: str | None = None
    events: list[dict] = []
    topic_title: str | None = None
    topic_start_s: float | None = None


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    video_id: uuid.UUID
    video_title: str
    start_s: float
    end_s: float
    # The child chunk: what actually matched, ~18s, precise about *when*.
    text: str
    # "transcript" (spoken) or "ocr" (read off the screen). Clients must be able
    # to tell these apart: OCR of a 360p code editor is a genuinely useful
    # *locator* but poor reading material, and presented unlabelled it looks
    # like the search returned nonsense.
    kind: str
    score: float
    # Which retrievers found it — "semantic", "lexical", or both.
    matched_by: list[str]
    semantic_rank: int | None
    lexical_rank: int | None
    # The enclosing parent: wider context, ~70s. What Phase 7 hands the LLM.
    parent_text: str | None
    parent_start_s: float | None
    parent_end_s: float | None
    # Cross-encoder relevance when reranking ran. >2 is a genuine match,
    # <0 means nothing retrieved answers the query.
    rerank_score: float | None = None
    # Position before reranking, so promotions are visible.
    fused_rank: int | None = None
    context: TemporalContext | None = None


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
    total: int
    # Retriever contributions, exposed so ablations are observable from the API
    # rather than requiring instrumentation.
    semantic_candidates: int
    lexical_candidates: int
    fused_candidates: int
    took_ms: float
    embed_ms: float
    rerank_ms: float | None = None
    reranked: bool = False
    # Top cross-encoder score — the "did we actually find anything" signal.
    top_relevance: float | None = None


# ─── Answering ─────────────────────────────────────────────────────────────


class CitationOut(BaseModel):
    """A citation the model made, resolved to a moment from the database."""

    marker: int
    chunk_id: uuid.UUID
    video_id: uuid.UUID
    video_title: str
    start_s: float
    end_s: float
    text: str


class EvidenceOut(BaseModel):
    marker: int
    chunk_id: uuid.UUID
    video_id: uuid.UUID
    video_title: str
    start_s: float
    end_s: float
    text: str
    on_screen_text: str | None = None
    topic_title: str | None = None
    relevance: float | None = None


class AnswerResponse(BaseModel):
    question: str
    # Markers appear inline as [c_1]; the client renders them from `citations`.
    answer: str
    citations: list[CitationOut]
    evidence: list[EvidenceOut]
    refused: bool
    refusal_reason: str | None
    # Quality telemetry. `fabricated_citations` should stay empty — anything
    # else means the model invented a reference that was then rejected.
    fabricated_citations: list[int]
    uncited_sentences: int
    total_sentences: int
    model: str | None
    took_ms: float


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    video_id: uuid.UUID | None = None
    # Scope to a collection. Ignored when video_id is given.
    collection_id: uuid.UUID | None = None
    kinds: str | None = None


# ─── Errors ────────────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    detail: str
