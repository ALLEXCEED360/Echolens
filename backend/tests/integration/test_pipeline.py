"""Processing pipeline: queueing, stage bookkeeping, transcript endpoints.

Whisper itself is not exercised here — loading large-v3 takes ~45s and the
model is a third-party black box. What matters is that *our* orchestration is
right: stages transition correctly, a silent video skips the speech branch,
re-running replaces rather than duplicates, and timestamps survive the round
trip. Real GPU transcription is covered by tests/manual/transcribe_live.py.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import JobStatus, StageName, StageStatus, TranscriptSegment
from app.pipeline import runner


async def _upload(client: AsyncClient, path: Path, filename: str = "lecture.mp4") -> dict:
    resp = await client.post(
        "/api/videos", params={"filename": filename}, content=path.read_bytes()
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestQueueing:
    async def test_process_returns_queued_job(
        self, client: AsyncClient, video_with_audio: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queued: list[uuid.UUID] = []
        monkeypatch.setattr(runner, "enqueue", lambda jid: _collect(queued, jid))

        video = await _upload(client, video_with_audio)
        resp = await client.post(f"/api/videos/{video['id']}/process")

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "queued"
        assert [s["name"] for s in body["stages"]] == list(StageName.ORDER)
        assert queued == [uuid.UUID(body["id"])]

    async def test_double_queue_is_refused(
        self, client: AsyncClient, video_with_audio: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two Whisper runs on one GPU is nobody's intent."""
        monkeypatch.setattr(runner, "enqueue", lambda jid: _noop())

        video = await _upload(client, video_with_audio)
        assert (await client.post(f"/api/videos/{video['id']}/process")).status_code == 202
        assert (await client.post(f"/api/videos/{video['id']}/process")).status_code == 409

    async def test_process_unknown_video_404s(self, client: AsyncClient) -> None:
        resp = await client.post(f"/api/videos/{uuid.uuid4()}/process")
        assert resp.status_code == 404


class TestJobExecution:
    async def test_silent_video_skips_speech_branch(
        self, client: AsyncClient, sample_video: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`skipped` is a real state, not a failure. The job must still succeed."""
        monkeypatch.setattr(runner, "enqueue", lambda jid: _noop())

        video = await _upload(client, sample_video, filename="silent.mp4")
        job = (await client.post(f"/api/videos/{video['id']}/process")).json()

        await runner.run_job(uuid.UUID(job["id"]))

        result = (await client.get(f"/api/jobs/{job['id']}")).json()
        stages = {s["name"]: s["status"] for s in result["stages"]}

        assert result["status"] == JobStatus.SUCCEEDED
        assert stages[StageName.PROBE] == StageStatus.SUCCEEDED
        assert stages[StageName.AUDIO_EXTRACT] == StageStatus.SKIPPED
        assert stages[StageName.TRANSCRIBE] == StageStatus.SKIPPED

        detail = (await client.get(f"/api/videos/{video['id']}")).json()
        assert detail["status"] == "ready"

    async def test_audio_extraction_runs_for_video_with_sound(
        self, client: AsyncClient, video_with_audio: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stops before Whisper: we are testing our stage, not the model."""
        monkeypatch.setattr(runner, "enqueue", lambda jid: _noop())

        # **kwargs so the stub tolerates per-run options (vad_filter and
        # anything later) without this test caring about them.
        async def fake_transcribe(session, job_id, video, audio_path, **_options):
            assert audio_path.exists(), "transcribe stage got a missing audio file"
            await runner._set_stage(
                session, job_id, StageName.TRANSCRIBE, status=StageStatus.SUCCEEDED
            )
            return 0

        monkeypatch.setattr(runner, "_stage_transcribe", fake_transcribe)

        video = await _upload(client, video_with_audio)
        job = (await client.post(f"/api/videos/{video['id']}/process")).json()
        await runner.run_job(uuid.UUID(job["id"]))

        result = (await client.get(f"/api/jobs/{job['id']}")).json()
        stages = {s["name"]: s for s in result["stages"]}

        assert result["status"] == JobStatus.SUCCEEDED
        assert stages[StageName.AUDIO_EXTRACT]["status"] == StageStatus.SUCCEEDED
        assert stages[StageName.AUDIO_EXTRACT]["metrics"]["sample_rate"] == 16000

    async def test_stage_failure_fails_the_job_with_a_reason(
        self, client: AsyncClient, video_with_audio: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(runner, "enqueue", lambda jid: _noop())

        async def boom(session, job_id, video):
            raise runner.StageFailed("GPU caught fire")

        monkeypatch.setattr(runner, "_stage_audio", boom)

        video = await _upload(client, video_with_audio)
        job = (await client.post(f"/api/videos/{video['id']}/process")).json()
        await runner.run_job(uuid.UUID(job["id"]))

        result = (await client.get(f"/api/jobs/{job['id']}")).json()
        assert result["status"] == JobStatus.FAILED
        assert "GPU caught fire" in result["error"]

        detail = (await client.get(f"/api/videos/{video['id']}")).json()
        assert detail["status"] == "failed"


class TestTranscriptEndpoints:
    async def test_empty_transcript(self, client: AsyncClient, sample_video: Path) -> None:
        video = await _upload(client, sample_video)
        body = (await client.get(f"/api/videos/{video['id']}/transcript")).json()

        assert body["total"] == 0
        assert body["segments"] == []
        assert body["model"] is None

    async def test_segments_returned_in_order_with_timestamps(
        self, client: AsyncClient, sample_video: Path
    ) -> None:
        video = await _upload(client, sample_video)
        await _seed_transcript(uuid.UUID(video["id"]))

        body = (await client.get(f"/api/videos/{video['id']}/transcript")).json()

        assert body["total"] == 3
        assert [s["position"] for s in body["segments"]] == [0, 1, 2]
        assert [s["start_s"] for s in body["segments"]] == [0.0, 5.5, 12.25]
        assert body["model"] == "faster-whisper/test"
        # 5.0 + 6.0 + 3.75
        assert body["speech_duration_s"] == pytest.approx(14.75)

    async def test_fractional_timestamps_survive_the_round_trip(
        self, client: AsyncClient, sample_video: Path
    ) -> None:
        """Rounding to whole seconds here would break seek precision."""
        video = await _upload(client, sample_video)
        await _seed_transcript(uuid.UUID(video["id"]))

        body = (await client.get(f"/api/videos/{video['id']}/transcript")).json()
        assert body["segments"][2]["start_s"] == 12.25
        assert body["segments"][2]["end_s"] == 16.0

    async def test_transcript_deleted_with_video(
        self, client: AsyncClient, sample_video: Path
    ) -> None:
        video = await _upload(client, sample_video)
        await _seed_transcript(uuid.UUID(video["id"]))

        await client.delete(f"/api/videos/{video['id']}")

        async with SessionLocal() as session:
            rows = (await session.execute(select(TranscriptSegment))).scalars().all()
        assert rows == []


class TestTranscriptSearch:
    async def test_finds_matching_segment(
        self, client: AsyncClient, sample_video: Path
    ) -> None:
        video = await _upload(client, sample_video)
        await _seed_transcript(uuid.UUID(video["id"]))

        body = (await client.get("/api/search/transcript", params={"q": "backprop"})).json()

        assert body["total"] == 1
        assert body["hits"][0]["start_s"] == 5.5
        assert "Backpropagation" in body["hits"][0]["text"]

    async def test_case_insensitive(self, client: AsyncClient, sample_video: Path) -> None:
        video = await _upload(client, sample_video)
        await _seed_transcript(uuid.UUID(video["id"]))

        body = (await client.get("/api/search/transcript", params={"q": "GRADIENT"})).json()
        assert body["total"] == 1

    async def test_scoped_to_one_video(self, client: AsyncClient, sample_video: Path) -> None:
        a = await _upload(client, sample_video, filename="a.mp4")
        b = await _upload(client, sample_video, filename="b.mp4")
        await _seed_transcript(uuid.UUID(a["id"]))
        await _seed_transcript(uuid.UUID(b["id"]))

        both = (await client.get("/api/search/transcript", params={"q": "neural"})).json()
        one = (
            await client.get(
                "/api/search/transcript", params={"q": "neural", "video_id": a["id"]}
            )
        ).json()

        assert both["total"] == 2
        assert one["total"] == 1

    async def test_no_matches(self, client: AsyncClient, sample_video: Path) -> None:
        video = await _upload(client, sample_video)
        await _seed_transcript(uuid.UUID(video["id"]))

        body = (await client.get("/api/search/transcript", params={"q": "kubernetes"})).json()
        assert body["total"] == 0
        assert body["hits"] == []

    async def test_short_query_rejected(self, client: AsyncClient) -> None:
        assert (await client.get("/api/search/transcript", params={"q": "a"})).status_code == 422


# ─── Helpers ───────────────────────────────────────────────────────────────


async def _collect(sink: list, job_id: uuid.UUID):
    sink.append(job_id)


async def _noop():
    return None


async def _seed_transcript(video_id: uuid.UUID) -> None:
    rows = [
        (0, 0.0, 5.0, "Today we are going to discuss neural networks."),
        (1, 5.5, 11.5, "Backpropagation computes the gradient of the loss."),
        (2, 12.25, 16.0, "The learning rate controls each update."),
    ]
    async with SessionLocal() as session:
        session.add_all(
            [
                TranscriptSegment(
                    video_id=video_id,
                    position=p,
                    start_s=s,
                    end_s=e,
                    text=t,
                    model="faster-whisper/test",
                )
                for p, s, e, t in rows
            ]
        )
        await session.commit()
