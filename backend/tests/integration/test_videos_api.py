"""End-to-end: upload → probe → list → stream → delete."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from httpx import AsyncClient


async def _upload(client: AsyncClient, data: bytes, filename: str = "lecture.mp4") -> dict:
    resp = await client.post(
        "/api/videos", params={"filename": filename}, content=data,
        headers={"Content-Type": "video/mp4"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestUpload:
    async def test_returns_probed_metadata(self, client: AsyncClient, sample_bytes: bytes) -> None:
        body = await _upload(client, sample_bytes)

        assert body["status"] == "uploaded"
        assert body["size_bytes"] == len(sample_bytes)
        assert (body["width"], body["height"]) == (320, 240)
        assert body["duration_s"] == pytest.approx(2.0, abs=0.3)
        assert body["has_audio"] is False

    async def test_title_defaults_to_filename_stem(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        body = await _upload(client, sample_bytes, filename="ML Lecture 03.mp4")
        assert body["title"] == "ML Lecture 03"

    async def test_explicit_title_wins(self, client: AsyncClient, sample_bytes: bytes) -> None:
        resp = await client.post(
            "/api/videos",
            params={"filename": "raw.mp4", "title": "Backprop Deep Dive"},
            content=sample_bytes,
        )
        assert resp.json()["title"] == "Backprop Deep Dive"

    async def test_checksum_matches_uploaded_bytes(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        body = await _upload(client, sample_bytes)
        assert body["checksum_sha256"] == hashlib.sha256(sample_bytes).hexdigest()

    async def test_upload_does_not_start_processing(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        """Transcription is a multi-minute GPU job, so it must be opt-in.

        Uploading used to create a placeholder job; that phantom then blocked
        the real one with a 409.
        """
        video = await _upload(client, sample_bytes)
        assert (await client.get(f"/api/videos/{video['id']}/job")).status_code == 404
        assert (await client.get(f"/api/videos/{video['id']}/jobs")).json() == []


class TestUploadRejection:
    async def test_unsupported_extension(self, client: AsyncClient) -> None:
        resp = await client.post("/api/videos", params={"filename": "notes.pdf"}, content=b"x")
        assert resp.status_code == 415

    async def test_empty_body(self, client: AsyncClient) -> None:
        resp = await client.post("/api/videos", params={"filename": "empty.mp4"}, content=b"")
        assert resp.status_code == 400

    async def test_undecodable_content_is_rejected(self, client: AsyncClient) -> None:
        """Right extension, wrong bytes. Must fail at upload, not three stages in."""
        resp = await client.post(
            "/api/videos", params={"filename": "fake.mp4"}, content=b"not a video" * 500
        )
        assert resp.status_code == 422

    async def test_rejected_upload_is_not_listed(self, client: AsyncClient) -> None:
        await client.post("/api/videos", params={"filename": "fake.mp4"}, content=b"junk" * 500)
        listing = (await client.get("/api/videos")).json()
        assert all(v["status"] != "uploaded" for v in listing["items"])


class TestListing:
    async def test_empty(self, client: AsyncClient) -> None:
        body = (await client.get("/api/videos")).json()
        assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}

    async def test_newest_first(self, client: AsyncClient, sample_bytes: bytes) -> None:
        for name in ("first.mp4", "second.mp4", "third.mp4"):
            await _upload(client, sample_bytes, filename=name)

        items = (await client.get("/api/videos")).json()["items"]
        assert [i["title"] for i in items] == ["third", "second", "first"]

    async def test_title_filter(self, client: AsyncClient, sample_bytes: bytes) -> None:
        await _upload(client, sample_bytes, filename="Lecture 01.mp4")
        await _upload(client, sample_bytes, filename="Conference Talk.mp4")

        body = (await client.get("/api/videos", params={"q": "lecture"})).json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Lecture 01"

    async def test_pagination(self, client: AsyncClient, sample_bytes: bytes) -> None:
        for i in range(5):
            await _upload(client, sample_bytes, filename=f"v{i}.mp4")

        body = (await client.get("/api/videos", params={"limit": 2, "offset": 2})).json()
        assert body["total"] == 5
        assert len(body["items"]) == 2


class TestStreaming:
    """The seek path. If these break, scrubbing breaks."""

    async def test_full_request_returns_200_and_whole_file(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        video = await _upload(client, sample_bytes)
        resp = await client.get(f"/api/videos/{video['id']}/stream")

        assert resp.status_code == 200
        assert resp.content == sample_bytes
        assert resp.headers["accept-ranges"] == "bytes"

    async def test_range_returns_206_with_exact_bytes(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        video = await _upload(client, sample_bytes)
        resp = await client.get(
            f"/api/videos/{video['id']}/stream", headers={"Range": "bytes=0-99"}
        )

        assert resp.status_code == 206
        assert resp.content == sample_bytes[:100]
        assert resp.headers["content-range"] == f"bytes 0-99/{len(sample_bytes)}"
        assert resp.headers["content-length"] == "100"

    async def test_open_ended_range_reaches_eof(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        """What the browser sends after a seek."""
        video = await _upload(client, sample_bytes)
        offset = len(sample_bytes) // 2
        resp = await client.get(
            f"/api/videos/{video['id']}/stream", headers={"Range": f"bytes={offset}-"}
        )

        assert resp.status_code == 206
        assert resp.content == sample_bytes[offset:]

    async def test_unsatisfiable_range_returns_416(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        video = await _upload(client, sample_bytes)
        resp = await client.get(
            f"/api/videos/{video['id']}/stream",
            headers={"Range": f"bytes={len(sample_bytes) + 10}-"},
        )

        assert resp.status_code == 416
        assert resp.headers["content-range"] == f"bytes */{len(sample_bytes)}"

    async def test_reassembled_chunks_equal_original(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        """Fetching the file in 512-byte windows must reconstruct it exactly.

        Catches inclusive/exclusive off-by-one errors that a single range test
        can miss.
        """
        video = await _upload(client, sample_bytes)
        step, buf = 512, b""

        for start in range(0, len(sample_bytes), step):
            end = min(start + step - 1, len(sample_bytes) - 1)
            resp = await client.get(
                f"/api/videos/{video['id']}/stream", headers={"Range": f"bytes={start}-{end}"}
            )
            assert resp.status_code == 206
            buf += resp.content

        assert buf == sample_bytes

    async def test_missing_video_404s(self, client: AsyncClient) -> None:
        resp = await client.get("/api/videos/00000000-0000-0000-0000-000000000000/stream")
        assert resp.status_code == 404


class TestMutation:
    async def test_rename(self, client: AsyncClient, sample_bytes: bytes) -> None:
        video = await _upload(client, sample_bytes)
        resp = await client.patch(
            f"/api/videos/{video['id']}", json={"title": "Renamed", "description": "Week 3"}
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Renamed"
        assert resp.json()["description"] == "Week 3"

    async def test_delete_removes_video_and_bytes(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        video = await _upload(client, sample_bytes)

        assert (await client.delete(f"/api/videos/{video['id']}")).status_code == 204
        assert (await client.get(f"/api/videos/{video['id']}")).status_code == 404
        assert (await client.get(f"/api/videos/{video['id']}/stream")).status_code == 404

    async def test_delete_cascades_to_jobs(
        self, client: AsyncClient, sample_bytes: bytes, monkeypatch
    ) -> None:
        from app.pipeline import runner

        async def noop(_):
            return None

        monkeypatch.setattr(runner, "enqueue", noop)

        video = await _upload(client, sample_bytes)
        job = (await client.post(f"/api/videos/{video['id']}/process")).json()

        await client.delete(f"/api/videos/{video['id']}")
        assert (await client.get(f"/api/jobs/{job['id']}")).status_code == 404


class TestHealth:
    async def test_reports_database_and_phase(self, client: AsyncClient) -> None:
        body = (await client.get("/api/health")).json()
        assert body["status"] == "ok"
        assert body["database"] == "up"
        # Postgres + pgvector since Phase 3; the SQLite bridge is retired.
        assert body["vector_search"] is True
        assert body["phase"] >= 3


class TestRename:
    """Renaming an uploaded video.

    Uploaded filenames are rarely what you want to read later — a six-hour
    course arriving as "videoplayback" is the normal case. The endpoint has
    existed since Phase 1; nothing called it until the UI gained an editable
    title.
    """

    async def test_renames(self, client: AsyncClient, sample_bytes: bytes) -> None:
        video = (
            await client.post("/api/videos", params={"filename": "videoplayback.mp4"},
                              content=sample_bytes)
        ).json()

        response = await client.patch(
            f"/api/videos/{video['id']}", json={"title": "Unity Crash Course"}
        )

        assert response.status_code == 200
        assert response.json()["title"] == "Unity Crash Course"
        # And it survives a re-read, rather than only echoing back.
        again = await client.get(f"/api/videos/{video['id']}")
        assert again.json()["title"] == "Unity Crash Course"

    async def test_trims_surrounding_whitespace(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        video = (
            await client.post("/api/videos", params={"filename": "a.mp4"}, content=sample_bytes)
        ).json()

        response = await client.patch(
            f"/api/videos/{video['id']}", json={"title": "  Lecture 05  "}
        )
        assert response.json()["title"] == "Lecture 05"

    @pytest.mark.parametrize("title", ["", "   ", "\t\n"])
    async def test_rejects_a_blank_title(
        self, client: AsyncClient, sample_bytes: bytes, title: str
    ) -> None:
        """`min_length=1` alone is satisfied by a single space, which would
        leave a library row with no name and no obvious way to fix it."""
        video = (
            await client.post("/api/videos", params={"filename": "a.mp4"}, content=sample_bytes)
        ).json()

        response = await client.patch(f"/api/videos/{video['id']}", json={"title": title})
        assert response.status_code == 422

    async def test_leaves_other_fields_alone(
        self, client: AsyncClient, sample_bytes: bytes
    ) -> None:
        """A rename must not clear the description or touch the source file."""
        video = (
            await client.post("/api/videos", params={"filename": "a.mp4"}, content=sample_bytes)
        ).json()
        await client.patch(f"/api/videos/{video['id']}", json={"description": "notes"})

        await client.patch(f"/api/videos/{video['id']}", json={"title": "Renamed"})

        body = (await client.get(f"/api/videos/{video['id']}")).json()
        assert body["title"] == "Renamed"
        assert body["description"] == "notes"
        assert body["original_filename"] == "a.mp4"

    async def test_missing_video_is_404(self, client: AsyncClient) -> None:
        response = await client.patch(f"/api/videos/{uuid.uuid4()}", json={"title": "x"})
        assert response.status_code == 404
