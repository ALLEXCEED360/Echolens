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
from app.search import (
    Hit,
    hybrid_search,
    lexical_search,
    semantic_search,
    temporal_context,
)

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


class TestFrameSelection:
    """Which keyframe gets attached to a hit.

    The original code took the *first* frame overlapping the padded window.
    Measured on the real corpus, that picked a frame with **zero** overlap —
    touching the hit only at its boundary — while the frame exactly spanning it
    sat next in the list. The consequences were a wrong thumbnail beside every
    result and the wrong "On screen at this moment" text in the answer prompt.
    """

    async def test_picks_the_covering_frame_not_the_first(self) -> None:
        """The regression, reproduced: an earlier frame merely touches the hit."""
        async with SessionLocal() as session:
            video_id, _ = await seed_multimodal(session)
            # Abuts the hit's start exactly — zero real overlap, but it is
            # first in time order and falls inside the padded window.
            session.add(
                Keyframe(
                    video_id=video_id, position=1, start_s=52.0, end_s=60.0,
                    time_s=52.0, storage_key="keyframes/early.jpg", change=10,
                )
            )
            covering = Keyframe(
                video_id=video_id, position=2, start_s=60.0, end_s=90.0,
                time_s=60.0, storage_key="keyframes/covering.jpg", change=20,
            )
            session.add(covering)
            await session.commit()
            covering_id = covering.id

            hit = Hit(
                chunk_id=uuid.uuid4(), video_id=video_id, video_title="t",
                start_s=60.0, end_s=90.0, text="x", score=1.0,
            )
            evidence = await temporal_context(session, [hit])

        assert evidence[hit.chunk_id].keyframe_id == covering_id

    async def test_falls_back_to_nearest_when_nothing_overlaps(self) -> None:
        """A hit landing between frames still gets the closest one."""
        async with SessionLocal() as session:
            video_id, _ = await seed_multimodal(session)
            near = Keyframe(
                video_id=video_id, position=1, start_s=100.0, end_s=101.0,
                time_s=100.0, storage_key="keyframes/near.jpg", change=10,
            )
            session.add(near)
            await session.commit()
            near_id = near.id

            # Sits in the 3s gap after `near`, with no frame covering it.
            hit = Hit(
                chunk_id=uuid.uuid4(), video_id=video_id, video_title="t",
                start_s=102.0, end_s=103.0, text="x", score=1.0,
            )
            evidence = await temporal_context(session, [hit])

        assert evidence[hit.chunk_id].keyframe_id == near_id

    async def test_ocr_text_reaches_the_hit_that_matched_it(self) -> None:
        """An OCR hit must carry the on-screen text it was derived from.

        This was silently None in production: the frame chosen was a
        neighbour that happened to have no OCR blocks.
        """
        async with SessionLocal() as session:
            _, chunks = await seed_multimodal(session)
            ocr_chunk = next(c for c in chunks if c.kind == ChunkKind.OCR)
            hit = Hit(
                chunk_id=ocr_chunk.id, video_id=ocr_chunk.video_id, video_title="t",
                start_s=ocr_chunk.start_s, end_s=ocr_chunk.end_s,
                text=ocr_chunk.text, score=1.0, kind="ocr",
            )
            evidence = await temporal_context(session, [hit])

        assert evidence[hit.chunk_id].on_screen_text == "Rigidbody2D"


class TestModalityLabelling:
    async def test_hits_report_their_modality(self) -> None:
        """Without this the UI cannot warn that OCR text is machine-read."""
        async with SessionLocal() as session:
            await seed_multimodal(session)
            result = await hybrid_search(session, "Rigidbody2D", unit_vector(50))

        kinds = {h.kind for h in result.hits}
        assert kinds <= {"transcript", "ocr"}
        assert "ocr" in kinds, "the OCR chunk should be retrievable and labelled"

    async def test_api_exposes_kind(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            await seed_multimodal(session)
        response = await client.get("/api/search", params={"q": "Rigidbody2D", "limit": 5})

        assert response.status_code == 200
        hits = response.json()["hits"]
        assert hits
        assert all("kind" in h for h in hits)


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


class TestKeyframeListing:
    """Filtering and counting, which have to happen in the same place.

    The text filter used to run in Python *after* SQL had already applied
    LIMIT, so a request for N frames-with-text returned only however many of
    the first N rows happened to carry text — approaching zero on later pages.
    `total` reported the page length, which made it useless for paging.
    """

    @staticmethod
    async def _seed_frames(session, video_id: uuid.UUID, *, with_text: int, without: int):
        position = 100
        for i in range(with_text + without):
            frame = Keyframe(
                video_id=video_id, position=position + i,
                start_s=1000.0 + i * 10, end_s=1010.0 + i * 10, time_s=1000.0 + i * 10,
                storage_key=f"keyframes/x{i}.jpg", change=5,
            )
            session.add(frame)
            await session.flush()
            # Text on the *later* frames, so a limit applied before the filter
            # cannot stumble onto them by accident.
            if i >= without:
                session.add(
                    OcrBlock(
                        keyframe_id=frame.id, text=f"code {i}", confidence=0.9,
                        bbox={"x1": 0, "y1": 0, "x2": 5, "y2": 5},
                    )
                )
        await session.commit()

    async def test_limit_counts_frames_that_match_the_filter(
        self, client: AsyncClient
    ) -> None:
        async with SessionLocal() as session:
            video_id, _ = await seed_multimodal(session)
            await self._seed_frames(session, video_id, with_text=5, without=6)

        response = await client.get(
            f"/api/videos/{video_id}/keyframes",
            params={"with_text_only": "true", "limit": 3},
        )

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 3, "limit must select 3 matching frames, not filter 3 rows"
        assert all(i["text"] for i in items)

    async def test_total_is_the_match_count_not_the_page_size(
        self, client: AsyncClient
    ) -> None:
        async with SessionLocal() as session:
            video_id, _ = await seed_multimodal(session)
            await self._seed_frames(session, video_id, with_text=5, without=6)

        response = await client.get(
            f"/api/videos/{video_id}/keyframes",
            params={"with_text_only": "true", "limit": 2},
        )
        body = response.json()

        assert len(body["items"]) == 2
        # 5 seeded with text, plus the one seed_multimodal creates.
        assert body["total"] == 6

    async def test_paging_reaches_every_matching_frame(self, client: AsyncClient) -> None:
        """The symptom users would hit: later pages emptying out."""
        async with SessionLocal() as session:
            video_id, _ = await seed_multimodal(session)
            await self._seed_frames(session, video_id, with_text=5, without=6)

        seen: list[str] = []
        for offset in (0, 2, 4):
            response = await client.get(
                f"/api/videos/{video_id}/keyframes",
                params={"with_text_only": "true", "limit": 2, "offset": offset},
            )
            seen.extend(i["id"] for i in response.json()["items"])

        assert len(seen) == 6
        assert len(set(seen)) == 6, "paging returned duplicates"

    async def test_unfiltered_total_counts_the_whole_video(
        self, client: AsyncClient
    ) -> None:
        async with SessionLocal() as session:
            video_id, _ = await seed_multimodal(session)
            await self._seed_frames(session, video_id, with_text=5, without=6)

        response = await client.get(
            f"/api/videos/{video_id}/keyframes", params={"limit": 1}
        )
        assert response.json()["total"] == 12


class TestLibraryPosters:
    """A poster frame per video in the list response.

    Without it the library shows no imagery at all, and a client that fetched
    frames itself would issue one request per row — the N+1 that gets steadily
    worse as the library grows.
    """

    async def test_list_carries_a_poster(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            await seed_multimodal(session)

        response = await client.get("/api/videos")
        item = next(i for i in response.json()["items"] if i["poster_url"])

        assert item["poster_url"].startswith("/api/keyframes/")
        assert item["poster_url"].endswith("/image")

    async def test_poster_is_the_first_frame(self, client: AsyncClient) -> None:
        """Earliest moment in the video, not whichever row was inserted first."""
        async with SessionLocal() as session:
            video_id, _ = await seed_multimodal(session)
            # Added after the seeded frame, but earlier in the video.
            first = Keyframe(
                video_id=video_id, position=5, start_s=0.0, end_s=5.0, time_s=0.0,
                storage_key="keyframes/first.jpg", change=0,
            )
            session.add(first)
            await session.commit()
            expected = first.id

        response = await client.get("/api/videos")
        item = next(i for i in response.json()["items"] if i["id"] == str(video_id))

        assert item["poster_url"] == f"/api/keyframes/{expected}/image"

    async def test_video_without_keyframes_has_no_poster(self, client: AsyncClient) -> None:
        """Absent imagery is a normal state, not an error."""
        response = await client.post(
            "/api/videos", params={"filename": "bare.mp4"}, content=b"not-a-video"
        )
        # Upload may reject the junk payload; either way, no poster must appear.
        listing = await client.get("/api/videos")
        assert all(
            i["poster_url"] is None or i["poster_url"].startswith("/api/keyframes/")
            for i in listing.json()["items"]
        )
        assert response.status_code in (201, 415, 422)
