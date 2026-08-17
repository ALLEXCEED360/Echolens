"""Stage selection.

The point of the feature is that re-running the visual branch must not
re-transcribe, and — just as important — must not *destroy* the transcript it
skipped. Both are asserted here.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.api.transcripts import _resolve_stages
from app.db import SessionLocal
from app.models import StageName, StageStatus, TranscriptSegment
from app.pipeline import runner


async def _noop(*_args, **_kwargs):
    return None


@pytest.fixture(autouse=True)
def _no_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jobs are created but never executed unless a test runs them explicitly."""
    monkeypatch.setattr(runner, "enqueue", _noop)


async def _upload(client: AsyncClient, data: bytes, filename: str = "lecture.mp4") -> dict:
    resp = await client.post("/api/videos", params={"filename": filename}, content=data)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestResolution:
    def test_default_is_everything(self) -> None:
        assert _resolve_stages(None) == set(StageName.ORDER)
        assert _resolve_stages("all") == set(StageName.ORDER)

    def test_visual_preset_excludes_transcription(self) -> None:
        stages = _resolve_stages("visual")
        assert StageName.TRANSCRIBE not in stages
        assert StageName.AUDIO_EXTRACT not in stages
        assert {StageName.KEYFRAMES, StageName.OCR, StageName.EMBED} <= stages

    def test_speech_preset_excludes_visual(self) -> None:
        stages = _resolve_stages("speech")
        assert StageName.KEYFRAMES not in stages
        assert StageName.OCR not in stages
        assert StageName.TRANSCRIBE in stages

    def test_index_preset_is_embed_only(self) -> None:
        assert _resolve_stages("index") == {StageName.PROBE, StageName.EMBED}

    def test_explicit_list(self) -> None:
        assert _resolve_stages("keyframes,ocr") == {
            StageName.PROBE, StageName.KEYFRAMES, StageName.OCR
        }

    def test_whitespace_and_case_tolerated(self) -> None:
        assert _resolve_stages("  KeyFrames , OCR  ") == {
            StageName.PROBE, StageName.KEYFRAMES, StageName.OCR
        }

    def test_probe_always_included(self) -> None:
        """Its absence would leave a confusing hole at the head of the pipeline."""
        assert StageName.PROBE in _resolve_stages("embed")

    def test_unknown_stage_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _resolve_stages("keyframes,teleport")
        assert "teleport" in exc.value.detail

    def test_empty_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _resolve_stages(",  ,")


class TestJobCreation:
    async def test_unselected_stages_are_pre_skipped(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        video = await _upload(client, sample_bytes)
        job = (
            await client.post(f"/api/videos/{video['id']}/process", params={"stages": "visual"})
        ).json()

        by_name = {s["name"]: s for s in job["stages"]}
        assert by_name[StageName.TRANSCRIBE]["status"] == StageStatus.SKIPPED
        assert by_name[StageName.TRANSCRIBE]["metrics"] == {"reason": "not requested"}
        assert by_name[StageName.KEYFRAMES]["status"] == StageStatus.WAITING

    async def test_every_stage_still_listed(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        """The pipeline view shows the whole pipeline, selected or not."""
        video = await _upload(client, sample_bytes)
        job = (
            await client.post(f"/api/videos/{video['id']}/process", params={"stages": "index"})
        ).json()
        assert [s["name"] for s in job["stages"]] == list(StageName.ORDER)

    async def test_invalid_stage_returns_422(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        video = await _upload(client, sample_bytes)
        resp = await client.post(
            f"/api/videos/{video['id']}/process", params={"stages": "nonsense"}
        )
        assert resp.status_code == 422
        assert "nonsense" in resp.json()["detail"]

    async def test_default_selects_all(self, client: AsyncClient, sample_bytes: bytes) -> None:
        video = await _upload(client, sample_bytes)
        job = (await client.post(f"/api/videos/{video['id']}/process")).json()
        assert all(s["status"] == StageStatus.WAITING for s in job["stages"])


class TestExecution:
    async def test_skipped_transcribe_preserves_the_transcript(
        self, client: AsyncClient, video_with_audio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point: adding visual stages must not cost the transcript."""
        video = await _upload(client, video_with_audio.read_bytes())
        video_id = uuid.UUID(video["id"])

        async with SessionLocal() as session:
            session.add_all(
                [
                    TranscriptSegment(
                        video_id=video_id, position=i, start_s=i * 3.0, end_s=(i + 1) * 3.0,
                        text=f"segment {i} about colliders", model="test",
                    )
                    for i in range(5)
                ]
            )
            await session.commit()

        # Stub the expensive stages; we are testing orchestration, not models.
        async def fake_keyframes(session, job_id, vid):
            await runner._set_stage(
                session, job_id, StageName.KEYFRAMES, status=StageStatus.SUCCEEDED
            )
            return []

        async def fake_ocr(session, job_id, vid, keyframes):
            await runner._set_stage(
                session, job_id, StageName.OCR, status=StageStatus.SUCCEEDED
            )
            return 0

        async def fake_embed(session, job_id, vid):
            await runner._set_stage(
                session, job_id, StageName.EMBED, status=StageStatus.SUCCEEDED
            )
            return 0

        transcribed = []

        async def spy_transcribe(session, job_id, vid, audio_path):
            transcribed.append(True)
            return 0

        monkeypatch.setattr(runner, "_stage_keyframes", fake_keyframes)
        monkeypatch.setattr(runner, "_stage_ocr", fake_ocr)
        monkeypatch.setattr(runner, "_stage_embed", fake_embed)
        monkeypatch.setattr(runner, "_stage_transcribe", spy_transcribe)

        job = (
            await client.post(f"/api/videos/{video['id']}/process", params={"stages": "visual"})
        ).json()
        await runner.run_job(uuid.UUID(job["id"]))

        assert transcribed == [], "transcription ran despite being skipped"

        async with SessionLocal() as session:
            remaining = (
                await session.execute(
                    select(func.count())
                    .select_from(TranscriptSegment)
                    .where(TranscriptSegment.video_id == video_id)
                )
            ).scalar_one()
        assert remaining == 5, "the skipped stage destroyed its own inputs"

        result = (await client.get(f"/api/jobs/{job['id']}")).json()
        assert result["status"] == "succeeded"

    async def test_ocr_without_keyframes_reads_stored_ones(
        self, client: AsyncClient, sample_bytes: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running OCR alone must load keyframes from the database."""
        video = await _upload(client, sample_bytes)

        loaded: list[int] = []

        async def spy_load(session, video_id):
            loaded.append(1)
            return []

        async def fake_ocr(session, job_id, vid, keyframes):
            await runner._set_stage(
                session, job_id, StageName.OCR, status=StageStatus.SUCCEEDED
            )
            return 0

        monkeypatch.setattr(runner, "_load_keyframes", spy_load)
        monkeypatch.setattr(runner, "_stage_ocr", fake_ocr)

        job = (
            await client.post(f"/api/videos/{video['id']}/process", params={"stages": "ocr"})
        ).json()
        await runner.run_job(uuid.UUID(job["id"]))

        assert loaded == [1], "OCR did not fall back to stored keyframes"

    async def test_index_preset_runs_only_embed(
        self, client: AsyncClient, sample_bytes: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran: list[str] = []

        async def spy(name):
            async def inner(session, job_id, vid, *args):
                ran.append(name)
                await runner._set_stage(
                    session, job_id, name, status=StageStatus.SUCCEEDED
                )
                return 0
            return inner

        monkeypatch.setattr(runner, "_stage_keyframes", await spy(StageName.KEYFRAMES))
        monkeypatch.setattr(runner, "_stage_embed", await spy(StageName.EMBED))

        video = await _upload(client, sample_bytes)
        job = (
            await client.post(f"/api/videos/{video['id']}/process", params={"stages": "index"})
        ).json()
        await runner.run_job(uuid.UUID(job["id"]))

        assert ran == [StageName.EMBED]
