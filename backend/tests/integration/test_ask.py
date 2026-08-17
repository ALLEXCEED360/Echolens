"""End-to-end answering.

The LLM is stubbed. What matters here is the contract around it: that a
fabricated citation is rejected, that a low-relevance question is refused
*without* calling the model, and that timestamps in the response come from the
database rather than the generated text.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.db import SessionLocal
from app.models import Chunk, ChunkKind, ChunkLevel, Video
from app.pipeline import llm as llm_module
from app.pipeline.llm import StubProvider

DIM = 1024


def unit_vector(index: int) -> list[float]:
    v = [0.0] * DIM
    v[index % DIM] = 1.0
    return v


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    llm_module.set_provider(None)


@pytest.fixture
def stub_llm():
    """Install a stub provider and hand it back for inspection."""

    def install(response: str) -> StubProvider:
        provider = StubProvider(response)
        llm_module.set_provider(provider)
        return provider

    return install


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch: pytest.MonkeyPatch):
    """Avoid loading a 1.3 GB model to assert citation handling."""

    async def fake_embed_query(query, **kwargs):
        return unit_vector(0)

    monkeypatch.setattr("app.pipeline.embedding.embed_query", fake_embed_query)


@pytest.fixture
def fake_rerank(monkeypatch: pytest.MonkeyPatch):
    """Control the relevance score, which drives the refusal path."""

    def install(score: float):
        async def scorer(query, documents, **kwargs):
            return [score] * len(documents)

        monkeypatch.setattr("app.pipeline.rerank.rerank", scorer)

    return install


async def seed(session, *, count: int = 4) -> uuid.UUID:
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

    session.add_all(
        [
            Chunk(
                video_id=video.id, kind=ChunkKind.TRANSCRIPT, level=ChunkLevel.CHILD,
                position=i, start_s=i * 100.0, end_s=i * 100.0 + 20.0,
                text=f"Chunk {i} explains colliders and physics in Unity.",
                token_count=8, embedding=unit_vector(i), embedding_model="test",
            )
            for i in range(count)
        ]
    )
    await session.commit()
    return video.id


class TestAnswering:
    async def test_returns_answer_with_resolved_citation(
        self, client: AsyncClient, stub_llm, fake_rerank
    ) -> None:
        async with SessionLocal() as session:
            await seed(session)
        fake_rerank(5.0)
        stub_llm("Colliders define physical shape [c_1]. They stop objects falling [c_2].")

        body = (await client.post("/api/ask", json={"question": "how do colliders work"})).json()

        assert body["refused"] is False
        assert len(body["citations"]) == 2
        # Timestamps come from the database, never from the generated text.
        assert all(c["start_s"] is not None for c in body["citations"])
        assert body["fabricated_citations"] == []

    async def test_fabricated_citation_is_rejected(
        self, client: AsyncClient, stub_llm, fake_rerank
    ) -> None:
        """The guarantee: an invented reference cannot reach the user."""
        async with SessionLocal() as session:
            await seed(session)
        fake_rerank(5.0)
        stub_llm("A real claim [c_1]. An invented one [c_77].")

        body = (await client.post("/api/ask", json={"question": "how do colliders work"})).json()

        assert body["fabricated_citations"] == [77]
        assert "[c_77]" not in body["answer"]
        assert all(c["marker"] != 77 for c in body["citations"])

    async def test_uncited_sentence_is_stripped(
        self, client: AsyncClient, stub_llm, fake_rerank
    ) -> None:
        async with SessionLocal() as session:
            await seed(session)
        fake_rerank(5.0)
        stub_llm("Supported claim [c_1]. Outside knowledge with no source.")

        body = (await client.post("/api/ask", json={"question": "how do colliders work"})).json()

        assert body["uncited_sentences"] == 1
        assert "Outside knowledge" not in body["answer"]

    async def test_entirely_uncited_answer_becomes_a_refusal(
        self, client: AsyncClient, stub_llm, fake_rerank
    ) -> None:
        async with SessionLocal() as session:
            await seed(session)
        fake_rerank(5.0)
        stub_llm("Claim one. Claim two. Claim three.")

        body = (await client.post("/api/ask", json={"question": "how do colliders work"})).json()

        assert body["refused"] is True
        assert "not" in body["answer"].lower()

    async def test_citations_orphaned_by_stripping_are_dropped(
        self, client: AsyncClient, stub_llm, fake_rerank
    ) -> None:
        """A citation only present in a removed sentence must not be reported."""
        async with SessionLocal() as session:
            await seed(session)
        fake_rerank(5.0)
        stub_llm("Kept claim [c_1]. Removed claim with no marker at all.")

        body = (await client.post("/api/ask", json={"question": "how do colliders work"})).json()
        assert [c["marker"] for c in body["citations"]] == [1]


class TestRefusal:
    async def test_low_relevance_refuses(
        self, client: AsyncClient, stub_llm, fake_rerank
    ) -> None:
        async with SessionLocal() as session:
            await seed(session)
        fake_rerank(-6.0)
        provider = stub_llm("This should never be produced.")

        body = (await client.post("/api/ask", json={"question": "how to bake bread"})).json()

        assert body["refused"] is True
        assert "below the relevance floor" in body["refusal_reason"]
        # The point of refusing early: no prompt is sent and nothing is paid for.
        assert provider.calls == [], "the model was called despite the refusal"

    async def test_refusal_still_reports_evidence(
        self, client: AsyncClient, stub_llm, fake_rerank
    ) -> None:
        """Even when declining, show what was considered — that is inspectable."""
        async with SessionLocal() as session:
            await seed(session)
        fake_rerank(-6.0)
        stub_llm("unused")

        body = (await client.post("/api/ask", json={"question": "how to bake bread"})).json()
        assert body["evidence"], "refusal should still surface the candidates"

    async def test_409_when_nothing_indexed(self, client: AsyncClient) -> None:
        resp = await client.post("/api/ask", json={"question": "anything at all"})
        assert resp.status_code == 409


class TestPromptContract:
    async def test_prompt_contains_no_timestamps(
        self, client: AsyncClient, stub_llm, fake_rerank
    ) -> None:
        """The model cannot copy a timestamp it was never shown."""
        async with SessionLocal() as session:
            await seed(session)
        fake_rerank(5.0)
        provider = stub_llm("Answer [c_1].")

        await client.post("/api/ask", json={"question": "how do colliders work"})

        assert provider.calls
        _system, user = provider.calls[0]
        import re

        assert not re.search(r"\b\d{1,2}:\d{2}\b", user), "a timestamp leaked into the prompt"
        assert "start_s" not in user

    async def test_system_prompt_forbids_timestamps(
        self, client: AsyncClient, stub_llm, fake_rerank
    ) -> None:
        async with SessionLocal() as session:
            await seed(session)
        fake_rerank(5.0)
        provider = stub_llm("Answer [c_1].")

        await client.post("/api/ask", json={"question": "how do colliders work"})
        system, _user = provider.calls[0]
        assert "NEVER write a timestamp" in system


class TestValidation:
    async def test_short_question_rejected(self, client: AsyncClient) -> None:
        assert (await client.post("/api/ask", json={"question": "a"})).status_code == 422

    async def test_unknown_kind_rejected(
        self, client: AsyncClient, stub_llm, fake_rerank
    ) -> None:
        async with SessionLocal() as session:
            await seed(session)
        fake_rerank(5.0)
        stub_llm("x [c_1].")
        resp = await client.post(
            "/api/ask", json={"question": "how do colliders work", "kinds": "bogus"}
        )
        assert resp.status_code == 422
