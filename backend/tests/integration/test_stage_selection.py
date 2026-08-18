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
from app.models import (
    JobStage,
    JobStatus,
    OcrBlock,
    ProcessingJob,
    StageName,
    StageStatus,
    TranscriptSegment,
    Video,
)
from app.models import Keyframe as Keyframe_
from app.pipeline import runner
from app.pipeline.keyframes import Keyframe


async def _noop(*_args, **_kwargs):
    return None


@pytest.fixture(autouse=True)
def _no_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jobs are created but never executed unless a test runs them explicitly."""
    monkeypatch.setattr(runner, "enqueue", _noop)


async def _seed_job(video_id: uuid.UUID) -> uuid.UUID:
    """A job row with stage rows, so `_set_stage` has something to update."""
    async with SessionLocal() as session:
        job = ProcessingJob(video_id=video_id, status=JobStatus.RUNNING)
        session.add(job)
        await session.flush()
        session.add_all(
            JobStage(job_id=job.id, name=name, status=StageStatus.WAITING, position=i)
            for i, name in enumerate(StageName.ORDER)
        )
        await session.commit()
        return job.id


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


class TestKeyframeReconciliation:
    """Re-running the keyframe stage must not destroy OCR.

    The original code deleted every keyframe for the video before inserting
    fresh ones. OCR blocks hang off keyframe rows, so the delete cascaded and
    took them with it — and when the *later* OCR stage then failed (a server
    restart mid-run is enough), the video was left with frames carrying no text
    at all.

    Nothing surfaced it. The `ocr` chunks live in a different table and kept
    answering searches, so search looked healthy while `on_screen_text` was
    None for the entire corpus, silently disabling the frame text badges, the
    `with_text_only` filter, and the "On screen at this moment" line in every
    answer prompt.
    """

    @staticmethod
    def _patch_scan(monkeypatch: pytest.MonkeyPatch, frames: list[Keyframe]) -> None:
        async def fake_scan(*_a, **_k):
            return frames

        async def fake_extract(_source, keyframes, destination, **_k):
            destination.mkdir(parents=True, exist_ok=True)
            paths = []
            for i, _ in enumerate(keyframes):
                p = destination / f"{i:05d}.jpg"
                p.write_bytes(b"x")
                paths.append(p)
            return paths

        monkeypatch.setattr(runner, "scan_keyframes", fake_scan)
        monkeypatch.setattr(runner, "extract_keyframes", fake_extract)

    async def _run_keyframe_stage(self, video_id: uuid.UUID, job_id: uuid.UUID) -> list:
        async with SessionLocal() as session:
            video = await session.get(Video, video_id)
            return await runner._stage_keyframes(session, job_id, video)

    async def test_rerun_preserves_ocr_blocks(
        self, client: AsyncClient, sample_bytes: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        frames = [
            Keyframe(start_s=0.0, end_s=5.0, time_s=0.0, phash=0xABCD, change=0),
            Keyframe(start_s=5.0, end_s=10.0, time_s=5.0, phash=0x1234, change=20),
        ]
        self._patch_scan(monkeypatch, frames)

        video = await _upload(client, sample_bytes)
        video_id = uuid.UUID(video["id"])
        job_id = await _seed_job(video_id)

        await self._run_keyframe_stage(video_id, job_id)

        # Attach OCR to the first frame, as the ocr stage would.
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(Keyframe_).where(Keyframe_.video_id == video_id)
                )
            ).scalars().all()
            assert len(rows) == 2
            session.add(
                OcrBlock(
                    keyframe_id=rows[0].id, text="Rigidbody2D", confidence=0.9,
                    bbox={"x1": 0, "y1": 0, "x2": 10, "y2": 10},
                )
            )
            await session.commit()

        # Re-run with the identical deterministic scan.
        await self._run_keyframe_stage(video_id, job_id)

        async with SessionLocal() as session:
            blocks = (
                await session.execute(select(func.count(OcrBlock.id)))
            ).scalar()
            frames_now = (
                await session.execute(
                    select(func.count(Keyframe_.id)).where(Keyframe_.video_id == video_id)
                )
            ).scalar()

        assert blocks == 1, "re-running the keyframe stage destroyed OCR text"
        assert frames_now == 2, "re-running duplicated keyframes"

    async def test_changed_frames_are_replaced(
        self, client: AsyncClient, sample_bytes: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reconciliation must still drop frames the video no longer contains."""
        first = [
            Keyframe(start_s=0.0, end_s=5.0, time_s=0.0, phash=0xABCD, change=0),
            Keyframe(start_s=5.0, end_s=10.0, time_s=5.0, phash=0x1234, change=20),
        ]
        self._patch_scan(monkeypatch, first)

        video = await _upload(client, sample_bytes)
        video_id = uuid.UUID(video["id"])
        job_id = await _seed_job(video_id)
        await self._run_keyframe_stage(video_id, job_id)

        # A different scan: one frame survives, one is gone, one is new.
        second = [
            Keyframe(start_s=0.0, end_s=5.0, time_s=0.0, phash=0xABCD, change=0),
            Keyframe(start_s=20.0, end_s=25.0, time_s=20.0, phash=0x9999, change=30),
        ]
        self._patch_scan(monkeypatch, second)
        await self._run_keyframe_stage(video_id, job_id)

        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(Keyframe_)
                    .where(Keyframe_.video_id == video_id)
                    .order_by(Keyframe_.position)
                )
            ).scalars().all()

        assert [r.phash for r in rows] == ["000000000000abcd", "0000000000009999"]
        assert [r.position for r in rows] == [0, 1]


class TestOcrRerun:
    """Re-running OCR must replace text, not accumulate it.

    The stage only ever appended, which was survivable while the keyframes
    stage deleted every frame first and cascaded the old blocks away. Making
    keyframes reconcile — necessary, or a failed OCR run destroys existing
    text — removed that accident and a second pass began duplicating every
    block, at exactly 2.00x on a re-analysed clip.

    Duplicates are not cosmetic: they are concatenated into `on_screen_text`
    and into the `kind=ocr` chunks, so each line reached search and the answer
    prompt twice.
    """

    async def test_second_pass_replaces_rather_than_appends(
        self, client: AsyncClient, sample_bytes: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        video = await _upload(client, sample_bytes)
        video_id = uuid.UUID(video["id"])
        job_id = await _seed_job(video_id)

        async with SessionLocal() as session:
            frame = Keyframe_(
                video_id=video_id, position=0, start_s=0.0, end_s=5.0, time_s=0.0,
                storage_key="keyframes/a.jpg", change=0,
            )
            session.add(frame)
            await session.commit()
            frame_id = frame.id

        class _Block:
            def __init__(self, text: str) -> None:
                self.text = text
                self.confidence = 0.9
                self.bbox = (0, 0, 10, 10)

        class _Frame:
            def __init__(self, path) -> None:
                self.path = path
                self.blocks = [_Block("Objective Failed")]

        async def fake_read_frames(paths, **_kwargs):
            return [_Frame(p) for p in paths]

        monkeypatch.setattr(runner, "read_frames", fake_read_frames)

        async def run_ocr() -> None:
            async with SessionLocal() as session:
                v = await session.get(Video, video_id)
                frames = (
                    await session.execute(
                        select(Keyframe_).where(Keyframe_.video_id == video_id)
                    )
                ).scalars().all()
                await runner._stage_ocr(session, job_id, v, list(frames))

        await run_ocr()
        await run_ocr()

        async with SessionLocal() as session:
            blocks = (
                await session.execute(
                    select(func.count(OcrBlock.id)).where(OcrBlock.keyframe_id == frame_id)
                )
            ).scalar()

        assert blocks == 1, f"re-running OCR duplicated blocks ({blocks} for one frame)"


class TestAudioTypeOption:
    """The VAD choice belongs to the media, and must survive.

    It began as server configuration only, which made it unusable: changing it
    meant restarting the API with an environment variable, and the choice
    vanished the moment the video was re-uploaded — the transcript silently
    reverted to the thin one. It is now recorded on the job.
    """

    async def test_noisy_disables_vad_on_the_job(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        video = await _upload(client, sample_bytes)
        response = await client.post(
            f"/api/videos/{video['id']}/process", params={"audio": "noisy"}
        )
        assert response.status_code == 202

        async with SessionLocal() as session:
            job = (
                await session.execute(
                    select(ProcessingJob).where(
                        ProcessingJob.video_id == uuid.UUID(video["id"])
                    )
                )
            ).scalars().first()

        assert job.options["vad_filter"] is False

    async def test_clear_is_the_default(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        video = await _upload(client, sample_bytes)
        await client.post(f"/api/videos/{video['id']}/process")

        async with SessionLocal() as session:
            job = (
                await session.execute(
                    select(ProcessingJob).where(
                        ProcessingJob.video_id == uuid.UUID(video["id"])
                    )
                )
            ).scalars().first()

        assert job.options["vad_filter"] is True

    async def test_vocabulary_is_recorded_on_the_job(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        video = await _upload(client, sample_bytes)
        await client.post(
            f"/api/videos/{video['id']}/process",
            params={"vocabulary": "  Makarov, Harkov  "},
        )

        async with SessionLocal() as session:
            job = (
                await session.execute(
                    select(ProcessingJob).where(
                        ProcessingJob.video_id == uuid.UUID(video["id"])
                    )
                )
            ).scalars().first()

        assert job.options["vocabulary"] == "Makarov, Harkov"

    async def test_blank_vocabulary_is_stored_as_absent(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        """An empty box must not become an empty hotword string."""
        video = await _upload(client, sample_bytes)
        await client.post(
            f"/api/videos/{video['id']}/process", params={"vocabulary": "   "}
        )

        async with SessionLocal() as session:
            job = (
                await session.execute(
                    select(ProcessingJob).where(
                        ProcessingJob.video_id == uuid.UUID(video["id"])
                    )
                )
            ).scalars().first()

        assert job.options["vocabulary"] is None

    async def test_rejects_an_unknown_audio_type(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        video = await _upload(client, sample_bytes)
        response = await client.post(
            f"/api/videos/{video['id']}/process", params={"audio": "underwater"}
        )
        assert response.status_code == 422
