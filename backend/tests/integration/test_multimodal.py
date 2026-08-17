"""Phase 6: metadata filters, temporal context and reranking.

The cross-encoder is not loaded here — a ~90 MB download to assert that sorting
works would be absurd. Reranking is exercised through a stub so the *plumbing*
is what gets tested: that a wider pool is retrieved, that scores reorder hits,
and that promotion is reported. Whether the model ranks well is a benchmark
question (Phase 9).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.db import SessionLocal
from app.models import (
    Chunk,
    ChunkKind,
    ChunkLevel,
    Event,
    EventSource,
    Keyframe,
    OcrBlock,
    Topic,
    Video,
)
from app.search import hybrid_search, lexical_search, semantic_search, temporal_context

DIM = 1024


def unit_vector(index: int) -> list[float]:
    v = [0.0] * DIM
    v[index % DIM] = 1.0
    return v


async def seed_multimodal(session) -> tuple[uuid.UUID, list[Chunk]]:
    """A video with transcript chunks, an OCR chunk, a keyframe, an event and a topic."""
    video = Video(
        title="Unity Tutorial",
        original_filename="t.mp4",
        storage_key=f"videos/{uuid.uuid4()}/source.mp4",
        mime_type="video/mp4",
        size_bytes=1,
        status="ready",
        has_audio=True,
        duration_s=600.0,
    )
    session.add(video)
    await session.commit()

    keyframe = Keyframe(
        video_id=video.id, position=0, start_s=10.0, end_s=40.0, time_s=25.0,
        storage_key="keyframes/0.jpg", change=30,
    )
    session.add(keyframe)
    await session.commit()
    session.add(
        OcrBlock(
            keyframe_id=keyframe.id, text="Rigidbody2D", confidence=0.9,
            bbox={"x1": 0, "y1": 0, "x2": 10, "y2": 10},
        )
    )
    session.add(
        Topic(
            video_id=video.id, position=0, depth=0, start_s=0.0, end_s=600.0,
            title="Physics, Colliders, Rigidbody", keywords={"terms": ["physics"]},
        )
    )
    session.add(
        Event(
            video_id=video.id, type="scene_change", source=EventSource.RULE,
            start_s=20.0, end_s=25.0, title="Scene change", confidence=0.9,
        )
    )
    await session.commit()

    texts = [
        "Add a rigidbody component so the player falls",
        "Colliders detect when two objects touch",
        "Prefabs let you reuse a configured object",
    ]
    chunks = [
        Chunk(
            video_id=video.id, kind=ChunkKind.TRANSCRIPT, level=ChunkLevel.CHILD,
            position=i, start_s=i * 30.0, end_s=(i + 1) * 30.0, text=text,
            token_count=8, embedding=unit_vector(i), embedding_model="test",
        )
        for i, text in enumerate(texts)
    ]
    chunks.append(
        Chunk(
            video_id=video.id, kind=ChunkKind.OCR, level=ChunkLevel.CHILD,
            position=0, start_s=10.0, end_s=40.0, text="Rigidbody2D",
            token_count=2, embedding=unit_vector(50), embedding_model="test",
            meta={"source": "ocr"},
        )
    )
    session.add_all(chunks)
    await session.commit()
    return video.id, chunks


class TestKindFilter:
    async def test_transcript_only(self) -> None:
        async with SessionLocal() as session:
            await seed_multimodal(session)
            results = await semantic_search(
                session, unit_vector(0), kinds=[ChunkKind.TRANSCRIPT]
            )
            ids = [r[0] for r in results]
            from sqlalchemy import select

            kinds = (
                await session.execute(select(Chunk.kind).where(Chunk.id.in_(ids)))
            ).scalars().all()

        assert set(kinds) == {ChunkKind.TRANSCRIPT}

    async def test_ocr_only(self) -> None:
        async with SessionLocal() as session:
            await seed_multimodal(session)
            results = await semantic_search(session, unit_vector(50), kinds=[ChunkKind.OCR])
        assert len(results) == 1

    async def test_filter_applies_to_lexical_too(self) -> None:
        """Both retrievers must honour the same filters or results disagree."""
        async with SessionLocal() as session:
            await seed_multimodal(session)
            transcript_only = await lexical_search(
                session, "Rigidbody2D", kinds=[ChunkKind.TRANSCRIPT]
            )
            ocr_only = await lexical_search(session, "Rigidbody2D", kinds=[ChunkKind.OCR])

        assert ocr_only, "the OCR chunk contains the exact term"
        assert not transcript_only


class TestTimeRange:
    async def test_restricts_to_window(self) -> None:
        async with SessionLocal() as session:
            await seed_multimodal(session)
            results = await semantic_search(session, unit_vector(0), time_range=(55.0, 95.0))
            from sqlalchemy import select

            ids = [r[0] for r in results]
            starts = (
                await session.execute(select(Chunk.start_s).where(Chunk.id.in_(ids)))
            ).scalars().all()

        assert starts, "expected the chunk overlapping 55-95s"
        assert all(s < 95.0 for s in starts)

    async def test_window_with_no_content(self) -> None:
        async with SessionLocal() as session:
            await seed_multimodal(session)
            results = await semantic_search(session, unit_vector(0), time_range=(5000.0, 6000.0))
        assert results == []


class TestTemporalContext:
    async def test_attaches_on_screen_text(self) -> None:
        """The point of the whole phase: what was *shown* while this was said."""
        async with SessionLocal() as session:
            await seed_multimodal(session)
            result = await hybrid_search(
                session, "rigidbody", unit_vector(0), limit=3, with_context=True
            )

        top = result.hits[0]
        assert top.context is not None
        assert top.context.on_screen_text == "Rigidbody2D"

    async def test_attaches_topic(self) -> None:
        async with SessionLocal() as session:
            await seed_multimodal(session)
            result = await hybrid_search(
                session, "rigidbody", unit_vector(0), limit=3, with_context=True
            )
        assert "Rigidbody" in result.hits[0].context.topic_title

    async def test_attaches_nearby_events(self) -> None:
        async with SessionLocal() as session:
            await seed_multimodal(session)
            result = await hybrid_search(
                session, "rigidbody", unit_vector(0), limit=3, with_context=True
            )
        assert any(e["type"] == "scene_change" for e in result.hits[0].context.events)

    async def test_off_by_default(self) -> None:
        async with SessionLocal() as session:
            await seed_multimodal(session)
            result = await hybrid_search(session, "rigidbody", unit_vector(0), limit=3)
        assert all(h.context is None for h in result.hits)

    async def test_empty_hits(self) -> None:
        async with SessionLocal() as session:
            assert await temporal_context(session, []) == {}


class TestRerank:
    async def test_reorders_by_score(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.pipeline import rerank as rerank_module

        async def reverse_scores(query, documents, **kwargs):
            # Score ascending so the fused order is exactly inverted.
            return [float(i) for i in range(len(documents))]

        monkeypatch.setattr(rerank_module, "rerank", reverse_scores)

        async with SessionLocal() as session:
            await seed_multimodal(session)
            plain = await hybrid_search(session, "objects", unit_vector(0), limit=4)
            reranked = await hybrid_search(
                session, "objects", unit_vector(0), limit=4, rerank_candidates=10
            )

        assert reranked.reranked is True
        assert plain.reranked is False
        assert [h.chunk_id for h in reranked.hits] == [h.chunk_id for h in plain.hits][::-1]

    async def test_reports_promotion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.pipeline import rerank as rerank_module

        async def reverse_scores(query, documents, **kwargs):
            return [float(i) for i in range(len(documents))]

        monkeypatch.setattr(rerank_module, "rerank", reverse_scores)

        async with SessionLocal() as session:
            await seed_multimodal(session)
            result = await hybrid_search(
                session, "objects", unit_vector(0), limit=4, rerank_candidates=10
            )

        top = result.hits[0]
        assert top.fused_rank is not None and top.fused_rank > 1, "expected a promotion"
        assert result.top_relevance == top.rerank_score

    async def test_pool_is_wider_than_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reranking only helps if it sees more than the final result set."""
        from app.pipeline import rerank as rerank_module

        seen: list[int] = []

        async def spy(query, documents, **kwargs):
            seen.append(len(documents))
            return [0.0] * len(documents)

        monkeypatch.setattr(rerank_module, "rerank", spy)

        async with SessionLocal() as session:
            await seed_multimodal(session)
            await hybrid_search(
                session, "objects", unit_vector(0), limit=1, rerank_candidates=10
            )

        assert seen and seen[0] > 1


class TestSearchApi:
    async def test_invalid_kind_rejected(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            await seed_multimodal(session)
        resp = await client.get("/api/search", params={"q": "rigidbody", "kinds": "bogus"})
        assert resp.status_code == 422
        assert "bogus" in resp.json()["detail"]

    async def test_inverted_time_range_rejected(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            await seed_multimodal(session)
        resp = await client.get(
            "/api/search", params={"q": "rigidbody", "start_s": 100, "end_s": 10}
        )
        assert resp.status_code == 422
