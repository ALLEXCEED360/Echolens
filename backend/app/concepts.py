"""Concept timelines across a corpus.

Answers "when was this first introduced, and where else does it come up" — the
question the vision doc frames as *"When was CNN first introduced?"* returning a
first occurrence plus related later coverage.

Retrieval already finds the moments; what this adds is **grouping by video and
ordering by time**, which is what turns a ranked list into a chronology. A
ranked list says "here are twenty relevant moments"; a chronology says "it is
introduced here, developed there, and revisited at the end".

Deliberately not an LLM: the ordering is a fact about the data, and inventing a
narrative over it would be exactly the kind of unfalsifiable summary this
project avoids.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Video
from app.search import Hit

logger = logging.getLogger(__name__)


@dataclass
class Occurrence:
    chunk_id: uuid.UUID
    start_s: float
    end_s: float
    text: str
    relevance: float | None = None
    topic_title: str | None = None


@dataclass
class VideoTrack:
    """One video's occurrences of a concept, in chronological order."""

    video_id: uuid.UUID
    video_title: str
    duration_s: float | None
    occurrences: list[Occurrence] = field(default_factory=list)

    @property
    def first_s(self) -> float:
        return self.occurrences[0].start_s if self.occurrences else 0.0

    @property
    def best_relevance(self) -> float:
        scores = [o.relevance for o in self.occurrences if o.relevance is not None]
        return max(scores) if scores else 0.0


@dataclass
class ConceptTimeline:
    query: str
    tracks: list[VideoTrack] = field(default_factory=list)
    total_occurrences: int = 0
    # The earliest confident mention across the corpus — "first introduced".
    first_video_id: uuid.UUID | None = None
    first_start_s: float | None = None
    first_video_title: str | None = None


async def build_timeline(
    session: AsyncSession,
    query: str,
    hits: list[Hit],
    *,
    min_relevance: float | None = None,
    per_video: int = 8,
) -> ConceptTimeline:
    """Group ranked hits into per-video chronologies.

    `min_relevance` filters on the cross-encoder score before grouping: a
    chronology built from weak matches is a chronology of noise, and unlike a
    ranked list there is no position cue telling the reader to distrust the tail.
    """
    kept = [
        h
        for h in hits
        if min_relevance is None or (h.rerank_score is not None and h.rerank_score >= min_relevance)
    ]
    if not kept:
        return ConceptTimeline(query=query)

    by_video: dict[uuid.UUID, list[Hit]] = {}
    for hit in kept:
        by_video.setdefault(hit.video_id, []).append(hit)

    durations = dict(
        (
            await session.execute(
                select(Video.id, Video.duration_s).where(Video.id.in_(by_video))
            )
        ).all()
    )

    tracks: list[VideoTrack] = []
    for video_id, video_hits in by_video.items():
        ordered = sorted(video_hits, key=lambda h: h.start_s)[:per_video]
        tracks.append(
            VideoTrack(
                video_id=video_id,
                video_title=video_hits[0].video_title,
                duration_s=durations.get(video_id),
                occurrences=[
                    Occurrence(
                        chunk_id=h.chunk_id,
                        start_s=h.start_s,
                        end_s=h.end_s,
                        text=h.text,
                        relevance=h.rerank_score,
                        topic_title=h.context.topic_title if h.context else None,
                    )
                    for h in ordered
                ],
            )
        )

    # Order videos by their strongest match, not by name: "where is this best
    # covered" is the more useful reading order across a course.
    tracks.sort(key=lambda t: t.best_relevance, reverse=True)

    # "First introduced" is chronological within the most relevant video, which
    # is a claim about that video rather than about the corpus. Across videos
    # there is no reliable ordering — upload time is not teaching order — so the
    # answer is deliberately scoped to one track.
    leader = tracks[0]
    return ConceptTimeline(
        query=query,
        tracks=tracks,
        total_occurrences=sum(len(t.occurrences) for t in tracks),
        first_video_id=leader.video_id,
        first_start_s=leader.first_s,
        first_video_title=leader.video_title,
    )
