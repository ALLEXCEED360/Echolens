"""Collections and cross-video scoping.

The behaviour worth guarding: scoping must never silently widen. A query aimed
at one collection that quietly searched the whole corpus would be worse than an
error, because the results would look plausible.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.api.collections import resolve_video_ids
from app.concepts import build_timeline
from app.db import SessionLocal
from app.models import Chunk, ChunkKind, ChunkLevel, Video
from app.search import Hit

DIM = 1024


def unit_vector(index: int) -> list[float]:
    v = [0.0] * DIM
    v[index % DIM] = 1.0
    return v


async def make_video(session, title: str, *, chunks: int = 3, offset: int = 0) -> uuid.UUID:
    video = Video(
        title=title,
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

    session.add_all(
        [
            Chunk(
                video_id=video.id, kind=ChunkKind.TRANSCRIPT, level=ChunkLevel.CHILD,
                position=i, start_s=i * 100.0, end_s=i * 100.0 + 20.0,
                text=f"{title} chunk {i} about colliders",
                token_count=8, embedding=unit_vector(offset + i), embedding_model="test",
            )
            for i in range(chunks)
        ]
    )
    await session.commit()
    return video.id


class TestCrud:
    async def test_create_and_fetch(self, client: AsyncClient) -> None:
        created = (
            await client.post("/api/collections", json={"name": "ML Course"})
        ).json()
        assert created["name"] == "ML Course"
        assert created["video_count"] == 0

        fetched = (await client.get(f"/api/collections/{created['id']}")).json()
        assert fetched["id"] == created["id"]

    async def test_duplicate_name_conflicts(self, client: AsyncClient) -> None:
        await client.post("/api/collections", json={"name": "Unity"})
        resp = await client.post("/api/collections", json={"name": "Unity"})
        assert resp.status_code == 409

    async def test_rename(self, client: AsyncClient) -> None:
        c = (await client.post("/api/collections", json={"name": "Old"})).json()
        updated = (
            await client.patch(f"/api/collections/{c['id']}", json={"name": "New"})
        ).json()
        assert updated["name"] == "New"

    async def test_missing_collection_404s(self, client: AsyncClient) -> None:
        assert (await client.get(f"/api/collections/{uuid.uuid4()}")).status_code == 404


class TestMembership:
    async def test_add_and_count(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            video_id = await make_video(session, "Lecture 1")
        c = (await client.post("/api/collections", json={"name": "Course"})).json()

        detail = (
            await client.put(f"/api/collections/{c['id']}/videos/{video_id}")
        ).json()
        assert detail["video_count"] == 1
        assert detail["indexed_count"] == 1
        assert [v["id"] for v in detail["videos"]] == [str(video_id)]

    async def test_video_belongs_to_one_collection(self, client: AsyncClient) -> None:
        """Assigning to a second collection moves it rather than duplicating."""
        async with SessionLocal() as session:
            video_id = await make_video(session, "Lecture 1")
        a = (await client.post("/api/collections", json={"name": "A"})).json()
        b = (await client.post("/api/collections", json={"name": "B"})).json()

        await client.put(f"/api/collections/{a['id']}/videos/{video_id}")
        await client.put(f"/api/collections/{b['id']}/videos/{video_id}")

        assert (await client.get(f"/api/collections/{a['id']}")).json()["video_count"] == 0
        assert (await client.get(f"/api/collections/{b['id']}")).json()["video_count"] == 1

    async def test_remove_unfiles(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            video_id = await make_video(session, "Lecture 1")
        c = (await client.post("/api/collections", json={"name": "Course"})).json()
        await client.put(f"/api/collections/{c['id']}/videos/{video_id}")

        detail = (
            await client.delete(f"/api/collections/{c['id']}/videos/{video_id}")
        ).json()
        assert detail["video_count"] == 0

        # The video itself survives.
        assert (await client.get(f"/api/videos/{video_id}")).status_code == 200

    async def test_deleting_collection_keeps_videos(self, client: AsyncClient) -> None:
        """A tidy-up must never destroy a six-hour transcript."""
        async with SessionLocal() as session:
            video_id = await make_video(session, "Lecture 1")
        c = (await client.post("/api/collections", json={"name": "Course"})).json()
        await client.put(f"/api/collections/{c['id']}/videos/{video_id}")

        assert (await client.delete(f"/api/collections/{c['id']}")).status_code == 204
        assert (await client.get(f"/api/videos/{video_id}")).status_code == 200

        async with SessionLocal() as session:
            video = await session.get(Video, video_id)
            assert video.collection_id is None

        async with SessionLocal() as session:
            remaining = (await session.execute(select(Chunk))).scalars().all()
        assert remaining, "chunks must survive their collection"

    async def test_unfiled_count(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            await make_video(session, "Loose 1")
            await make_video(session, "Loose 2")
        body = (await client.get("/api/collections")).json()
        assert body["unfiled_videos"] == 2


class TestScopeResolution:
    async def test_no_scope_means_everything(self) -> None:
        async with SessionLocal() as session:
            assert await resolve_video_ids(session) is None

    async def test_video_id_wins_over_collection(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            a = await make_video(session, "A")
            b = await make_video(session, "B")
        c = (await client.post("/api/collections", json={"name": "C"})).json()
        await client.put(f"/api/collections/{c['id']}/videos/{b}")

        async with SessionLocal() as session:
            scoped = await resolve_video_ids(
                session, video_id=a, collection_id=uuid.UUID(c["id"])
            )
        assert scoped == [a]

    async def test_empty_collection_matches_nothing(self, client: AsyncClient) -> None:
        """An empty scope must not silently widen to the whole corpus."""
        async with SessionLocal() as session:
            await make_video(session, "Outside the collection")
        c = (await client.post("/api/collections", json={"name": "Empty"})).json()

        async with SessionLocal() as session:
            scoped = await resolve_video_ids(session, collection_id=uuid.UUID(c["id"]))
        assert scoped == [], "an empty collection must not resolve to None"

    async def test_unknown_collection_404s(self) -> None:
        from fastapi import HTTPException

        async with SessionLocal() as session:
            with pytest.raises(HTTPException) as exc:
                await resolve_video_ids(session, collection_id=uuid.uuid4())
        assert exc.value.status_code == 404


class TestScopedSearch:
    async def test_collection_scope_excludes_outsiders(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            inside = await make_video(session, "Inside", offset=0)
            await make_video(session, "Outside", offset=10)
        c = (await client.post("/api/collections", json={"name": "Scoped"})).json()
        await client.put(f"/api/collections/{c['id']}/videos/{inside}")

        body = (
            await client.get(
                "/api/search",
                params={"q": "colliders", "collection_id": c["id"], "rerank": "false"},
            )
        ).json()

        assert body["hits"], "expected hits from inside the collection"
        assert {h["video_id"] for h in body["hits"]} == {str(inside)}

    async def test_empty_collection_returns_nothing(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            await make_video(session, "Unrelated")
        c = (await client.post("/api/collections", json={"name": "Empty"})).json()

        body = (
            await client.get(
                "/api/search",
                params={"q": "colliders", "collection_id": c["id"], "rerank": "false"},
            )
        ).json()
        assert body["total"] == 0


class TestConceptTimeline:
    @staticmethod
    def hit(video_id, title, start_s, score) -> Hit:
        return Hit(
            chunk_id=uuid.uuid4(), video_id=video_id, video_title=title,
            start_s=start_s, end_s=start_s + 20.0, text=f"mention at {start_s}",
            score=0.1, rerank_score=score,
        )

    async def test_groups_by_video_and_orders_by_time(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        hits = [
            self.hit(a, "Lecture 1", 300.0, 6.0),
            self.hit(a, "Lecture 1", 100.0, 5.0),
            self.hit(b, "Lecture 2", 200.0, 3.0),
        ]
        async with SessionLocal() as session:
            timeline = await build_timeline(session, "cnn", hits, min_relevance=2.0)

        assert len(timeline.tracks) == 2
        first = timeline.tracks[0]
        # Chronological within a track, even though 300s ranked higher.
        assert [o.start_s for o in first.occurrences] == [100.0, 300.0]

    async def test_tracks_ordered_by_best_match(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        hits = [self.hit(a, "Weak", 10.0, 2.5), self.hit(b, "Strong", 10.0, 6.5)]
        async with SessionLocal() as session:
            timeline = await build_timeline(session, "cnn", hits, min_relevance=2.0)
        assert timeline.tracks[0].video_title == "Strong"

    async def test_relevance_floor_filters_noise(self) -> None:
        a = uuid.uuid4()
        hits = [self.hit(a, "L", 10.0, 5.0), self.hit(a, "L", 20.0, -3.0)]
        async with SessionLocal() as session:
            timeline = await build_timeline(session, "cnn", hits, min_relevance=2.0)
        assert timeline.total_occurrences == 1

    async def test_first_occurrence_reported(self) -> None:
        a = uuid.uuid4()
        hits = [self.hit(a, "Lecture 1", 900.0, 6.0), self.hit(a, "Lecture 1", 60.0, 5.5)]
        async with SessionLocal() as session:
            timeline = await build_timeline(session, "cnn", hits, min_relevance=2.0)
        assert timeline.first_start_s == 60.0
        assert timeline.first_video_title == "Lecture 1"

    async def test_no_qualifying_hits(self) -> None:
        a = uuid.uuid4()
        async with SessionLocal() as session:
            timeline = await build_timeline(
                session, "cnn", [self.hit(a, "L", 10.0, -5.0)], min_relevance=2.0
            )
        assert timeline.tracks == []
        assert timeline.first_video_id is None

    async def test_per_video_cap(self) -> None:
        a = uuid.uuid4()
        hits = [self.hit(a, "L", i * 10.0, 5.0) for i in range(20)]
        async with SessionLocal() as session:
            timeline = await build_timeline(session, "cnn", hits, min_relevance=2.0, per_video=5)
        assert len(timeline.tracks[0].occurrences) == 5

    async def test_endpoint(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            await make_video(session, "Lecture 1")
        body = (
            await client.get(
                "/api/search/timeline",
                params={"q": "colliders", "min_relevance": -99},
            )
        ).json()
        assert body["query"] == "colliders"
        assert body["total_occurrences"] >= 1
