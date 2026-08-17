"""Hybrid retrieval.

The embedding model is not loaded here — a 1.3 GB download per CI run to assert
that cosine distance works would be absurd. Vectors are constructed by hand so
the *fusion and SQL* are what get tested, which is the part we wrote.

Real semantic quality is a benchmark question (Phase 9), not a unit test.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Chunk, ChunkKind, ChunkLevel, Video
from app.search import (
    hybrid_search,
    lexical_search,
    reciprocal_rank_fusion,
    semantic_search,
)

DIM = 1024


def unit_vector(index: int) -> list[float]:
    """A one-hot vector: orthogonal to every other index, so distances are exact."""
    v = [0.0] * DIM
    v[index % DIM] = 1.0
    return v


async def seed(session, *, title: str = "Unity Tutorial") -> tuple[uuid.UUID, list[Chunk]]:
    video = Video(
        title=title,
        original_filename="t.mp4",
        storage_key=f"videos/{uuid.uuid4()}/source.mp4",
        mime_type="video/mp4",
        size_bytes=1,
        status="ready",
        has_audio=True,
    )
    session.add(video)
    await session.commit()

    parent = Chunk(
        video_id=video.id, kind=ChunkKind.TRANSCRIPT, level=ChunkLevel.PARENT,
        position=0, start_s=0.0, end_s=60.0,
        text="Full parent context about rigidbody physics and colliders.", token_count=10,
    )
    session.add(parent)
    await session.commit()

    texts = [
        "Add a Rigidbody2D component to the player object",
        "Colliders detect when two objects touch",
        "Prefabs let you reuse a configured object",
        "The camera follows the player smoothly",
    ]
    children = [
        Chunk(
            video_id=video.id, parent_id=parent.id, kind=ChunkKind.TRANSCRIPT,
            level=ChunkLevel.CHILD, position=i, start_s=i * 15.0, end_s=(i + 1) * 15.0,
            text=text, token_count=8, embedding=unit_vector(i),
            embedding_model="test",
        )
        for i, text in enumerate(texts)
    ]
    session.add_all(children)
    await session.commit()
    return video.id, children


class TestSemanticSearch:
    async def test_nearest_vector_ranks_first(self) -> None:
        async with SessionLocal() as session:
            _, children = await seed(session)
            results = await semantic_search(session, unit_vector(2))

        assert results, "expected at least one hit"
        assert results[0][0] == children[2].id
        assert results[0][1] == pytest.approx(0.0, abs=1e-6)

    async def test_parents_are_never_returned(self) -> None:
        """Only children carry embeddings; parents must stay out of ranking."""
        async with SessionLocal() as session:
            _, _ = await seed(session)
            results = await semantic_search(session, unit_vector(0))
            ids = [r[0] for r in results]
            levels = (
                await session.execute(select(Chunk.level).where(Chunk.id.in_(ids)))
            ).scalars().all()

        assert set(levels) == {ChunkLevel.CHILD}

    async def test_scoped_to_video(self) -> None:
        async with SessionLocal() as session:
            video_a, _ = await seed(session, title="A")
            video_b, _ = await seed(session, title="B")
            results = await semantic_search(session, unit_vector(0), video_ids=[video_a])
            ids = [r[0] for r in results]
            owners = (
                await session.execute(select(Chunk.video_id).where(Chunk.id.in_(ids)))
            ).scalars().all()

        assert set(owners) == {video_a}
        assert video_b not in set(owners)


class TestLexicalSearch:
    async def test_finds_exact_identifier(self) -> None:
        """The case embeddings are worst at, and why lexical is not optional."""
        async with SessionLocal() as session:
            _, children = await seed(session)
            results = await lexical_search(session, "Rigidbody2D")

        assert results
        assert results[0][0] == children[0].id

    async def test_case_insensitive(self) -> None:
        async with SessionLocal() as session:
            _, children = await seed(session)
            results = await lexical_search(session, "PREFABS")

        assert any(r[0] == children[2].id for r in results)

    async def test_stemming(self) -> None:
        """English config should match 'collider' to 'colliders'."""
        async with SessionLocal() as session:
            _, children = await seed(session)
            results = await lexical_search(session, "collider")

        assert any(r[0] == children[1].id for r in results)

    async def test_no_match_returns_empty(self) -> None:
        async with SessionLocal() as session:
            await seed(session)
            assert await lexical_search(session, "kubernetes") == []

    @pytest.mark.parametrize("query", ["a & b", "foo | bar", "!!!", "'unclosed", "a <-> b"])
    async def test_operator_characters_do_not_raise(self, query: str) -> None:
        """websearch_to_tsquery must swallow syntax that to_tsquery would reject."""
        async with SessionLocal() as session:
            await seed(session)
            await lexical_search(session, query)  # must not raise


class TestLexicalRelaxation:
    """The OR fallback.

    `websearch_to_tsquery` ANDs every term, so a natural-language question
    demands all of its content words inside one ~17 s chunk. The Phase 9
    benchmark measured the damage: 30 of 46 questions retrieved *nothing*
    lexically, and the few incidental matches that survived were promoted by
    fusion — dragging hybrid Recall@1 below semantic search alone.
    """

    async def test_question_that_ands_to_nothing_still_retrieves(self) -> None:
        """No chunk contains all of these words; several contain some."""
        async with SessionLocal() as session:
            await seed(session)
            strict_terms = "How do I add a Rigidbody2D to a player and detect colliders?"
            results = await lexical_search(session, strict_terms)

        assert results, "relaxation should retrieve where the ANDed query cannot"

    async def test_strict_matches_outrank_relaxed_ones(self) -> None:
        """Relaxation extends the list; it must not reorder what was already found."""
        async with SessionLocal() as session:
            _, children = await seed(session)
            results = await lexical_search(session, "Prefabs reuse camera")

        ids = [r[0] for r in results]
        # "Prefabs ... reuse" both appear in one chunk; "camera" is elsewhere.
        assert ids[0] == children[2].id
        assert children[3].id in ids

    async def test_still_empty_when_nothing_matches_any_term(self) -> None:
        """Relaxation widens the query, it does not invent matches."""
        async with SessionLocal() as session:
            await seed(session)
            assert await lexical_search(session, "kubernetes ingress helm") == []

    async def test_quoted_phrase_survives_relaxation(self) -> None:
        """A phrase compiles to `<->`, not `&`, so rewriting must leave it alone."""
        async with SessionLocal() as session:
            _, children = await seed(session)
            results = await lexical_search(session, '"detect when two objects"')

        assert [r[0] for r in results][:1] == [children[1].id]

    async def test_respects_the_limit(self) -> None:
        async with SessionLocal() as session:
            await seed(session)
            results = await lexical_search(session, "rigidbody collider prefab camera", limit=2)

        assert len(results) == 2

    async def test_no_duplicates_across_passes(self) -> None:
        async with SessionLocal() as session:
            await seed(session)
            results = await lexical_search(session, "Colliders detect objects touch")

        ids = [r[0] for r in results]
        assert len(ids) == len(set(ids))


class TestFusion:
    def test_agreement_outranks_single_retriever(self) -> None:
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        fused = reciprocal_rank_fusion({"semantic": [a, b], "lexical": [c, a]})
        ranked = sorted(fused.items(), key=lambda kv: kv[1][0], reverse=True)

        # `a` is found by both retrievers, so it must come first.
        assert ranked[0][0] == a

    def test_score_matches_the_formula(self) -> None:
        a = uuid.uuid4()
        fused = reciprocal_rank_fusion({"semantic": [a], "lexical": [a]}, k=60)
        expected = 1 / 61 + 1 / 61
        assert fused[a][0] == pytest.approx(expected)

    def test_ranks_are_recorded_per_retriever(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        fused = reciprocal_rank_fusion({"semantic": [b, a], "lexical": [a]})
        assert fused[a][1] == {"semantic": 2, "lexical": 1}

    def test_empty(self) -> None:
        assert reciprocal_rank_fusion({"semantic": [], "lexical": []}) == {}


class TestWeightedFusion:
    """Down-weighting the lexical arm.

    Measured in Phase 9: at equal weight the OR-expanded lexical list outvoted
    good semantic hits, costing 0.07 MRR against semantic search alone.
    """

    def test_weight_scales_the_contribution(self) -> None:
        a = uuid.uuid4()
        fused = reciprocal_rank_fusion(
            {"semantic": [a], "lexical": [a]}, k=60, weights={"lexical": 0.25}
        )
        assert fused[a][0] == pytest.approx(1 / 61 + 0.25 / 61)

    def test_missing_weight_defaults_to_one(self) -> None:
        a = uuid.uuid4()
        weighted = reciprocal_rank_fusion({"semantic": [a]}, weights={"lexical": 0.25})
        plain = reciprocal_rank_fusion({"semantic": [a]})
        assert weighted[a][0] == pytest.approx(plain[a][0])

    def test_downweighted_retriever_cannot_outvote(self) -> None:
        """The regression the weight exists to prevent."""
        top_semantic, top_lexical = uuid.uuid4(), uuid.uuid4()
        fused = reciprocal_rank_fusion(
            {"semantic": [top_semantic], "lexical": [top_lexical]},
            weights={"semantic": 1.0, "lexical": 0.25},
        )
        ranked = sorted(fused.items(), key=lambda kv: kv[1][0], reverse=True)
        assert ranked[0][0] == top_semantic

    def test_equal_weights_reproduce_plain_rrf(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        lists = {"semantic": [a, b], "lexical": [b, a]}
        assert reciprocal_rank_fusion(
            lists, weights={"semantic": 1.0, "lexical": 1.0}
        ) == reciprocal_rank_fusion(lists)

    def test_zero_weight_removes_a_retriever_from_ranking(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        fused = reciprocal_rank_fusion(
            {"semantic": [a], "lexical": [b]}, weights={"lexical": 0.0}
        )
        # `b` is still recorded as retrieved, but contributes no score.
        assert fused[b][0] == 0.0
        assert fused[b][1] == {"lexical": 1}


class TestHybrid:
    async def test_returns_parent_context(self) -> None:
        """Children rank; parents are what an LLM would actually read."""
        async with SessionLocal() as session:
            await seed(session)
            result = await hybrid_search(session, "Rigidbody2D", unit_vector(0), limit=3)

        assert result.hits
        assert result.hits[0].parent_text is not None
        assert "parent context" in result.hits[0].parent_text
        assert result.hits[0].parent_end_s == 60.0

    async def test_reports_which_retriever_matched(self) -> None:
        async with SessionLocal() as session:
            await seed(session)
            result = await hybrid_search(session, "Rigidbody2D", unit_vector(0), limit=4)

        top = result.hits[0]
        assert "semantic" in top.matched_by
        assert "lexical" in top.matched_by

    async def test_semantic_only_query_still_returns(self) -> None:
        """No lexical overlap at all — vectors must carry it alone."""
        async with SessionLocal() as session:
            await seed(session)
            result = await hybrid_search(session, "zzzz nonexistent", unit_vector(1), limit=3)

        assert result.hits
        assert result.lexical_candidates == 0
        assert result.hits[0].matched_by == ["semantic"]

    async def test_carries_video_title(self) -> None:
        async with SessionLocal() as session:
            await seed(session, title="Unity Crash Course")
            result = await hybrid_search(session, "prefabs", unit_vector(2), limit=2)

        assert result.hits[0].video_title == "Unity Crash Course"

    async def test_limit_respected(self) -> None:
        async with SessionLocal() as session:
            await seed(session)
            result = await hybrid_search(session, "objects", unit_vector(0), limit=2)

        assert len(result.hits) <= 2


class TestSearchApi:
    async def test_409_when_nothing_indexed(self, client: AsyncClient) -> None:
        resp = await client.get("/api/search", params={"q": "anything"})
        assert resp.status_code == 409
        assert "process a video" in resp.json()["detail"].lower()

    async def test_short_query_rejected(self, client: AsyncClient) -> None:
        assert (await client.get("/api/search", params={"q": "a"})).status_code == 422

    async def test_stats_reports_empty_index(self, client: AsyncClient) -> None:
        body = (await client.get("/api/search/stats")).json()
        assert body["searchable"] is False
        assert body["videos_indexed"] == 0

    async def test_stats_counts_chunks(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            await seed(session)

        body = (await client.get("/api/search/stats")).json()
        assert body["searchable"] is True
        assert body["videos_indexed"] == 1
        assert body["by_level"]["child"]["chunks"] == 4
        assert body["by_level"]["child"]["embedded"] == 4
        # Parents are stored but deliberately not embedded.
        assert body["by_level"]["parent"]["embedded"] == 0


class TestCascade:
    async def test_deleting_video_removes_chunks(self, client: AsyncClient) -> None:
        async with SessionLocal() as session:
            video_id, _ = await seed(session)

        await client.delete(f"/api/videos/{video_id}")

        async with SessionLocal() as session:
            remaining = (await session.execute(select(Chunk))).scalars().all()
        assert remaining == []
