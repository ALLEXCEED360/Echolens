"""Test fixtures.

Environment is set *before* any app import, because `app.db` builds its engine at
import time from the cached settings object.

**Tests run against Postgres**, not SQLite. From Phase 3 the schema uses `vector`
and `tsvector` columns, which SQLite cannot represent at all — and testing
retrieval against a database that cannot do retrieval would be theatre. Requires
`docker compose up -d`; the suite creates and drops its own database so it never
touches development data.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="echolens-tests-"))

ADMIN_URL = os.environ.get(
    "ECHOLENS_TEST_ADMIN_URL", "postgresql+asyncpg://echolens:echolens@localhost:5432/postgres"
)
TEST_DB = os.environ.get("ECHOLENS_TEST_DB", "echolens_test")

os.environ["ECHOLENS_DATABASE_URL"] = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}"
os.environ["ECHOLENS_STORAGE_BACKEND"] = "local"
os.environ["ECHOLENS_STORAGE_LOCAL_PATH"] = str(_TMP / "storage")
os.environ["ECHOLENS_LOG_LEVEL"] = "WARNING"

import av  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    return _TMP


@pytest.fixture(scope="session", autouse=True)
async def _database() -> AsyncIterator[None]:
    """Create a dedicated test database, with extensions, for the session."""
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    except Exception as exc:  # noqa: BLE001
        pytest.exit(
            f"Cannot reach Postgres at {ADMIN_URL.split('@')[-1]} ({exc}).\n"
            "Run `docker compose up -d` first — the suite needs pgvector.",
            returncode=1,
        )
    finally:
        await admin.dispose()

    async with engine.begin() as conn:
        for extension in ("vector", "pg_trgm", "btree_gist"):
            await conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))

    yield

    await engine.dispose()
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
    await admin.dispose()


@pytest.fixture(autouse=True)
async def _schema() -> AsyncIterator[None]:
    """Fresh schema per test — these are cheap and isolation is worth more."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _write_test_video(path: Path, *, seconds: float = 2.0, fps: int = 25) -> Path:
    """Generate a real, decodable MP4.

    mpeg4 rather than libx264: it is built into every FFmpeg configuration, so
    the fixture does not depend on how the PyAV wheel was compiled.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width, stream.height = 320, 240
    stream.pix_fmt = "yuv420p"

    total = int(seconds * fps)
    for i in range(total):
        # Ramp the luma plane so frames genuinely differ and the encoder produces
        # realistic packet sizes. Planes are filled via `update()` rather than
        # numpy, keeping the test suite free of a heavyweight dependency it does
        # not otherwise need until Phase 4.
        frame = av.VideoFrame(320, 240, "yuv420p")
        luma = 16 + (i * 200 // max(total - 1, 1))
        for plane, value in zip(frame.planes, (luma, 128, 128), strict=True):
            plane.update(bytes([value]) * plane.buffer_size)
        frame.pts = i

        for packet in stream.encode(frame):
            container.mux(packet)

    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


def _write_video_with_audio(
    path: Path, *, seconds: float = 3.0, fps: int = 25, audio_rate: int = 44100
) -> Path:
    """Generate an MP4 carrying both a video and an audio stream.

    Audio is 44.1 kHz stereo on purpose: extraction must downmix and resample to
    16 kHz mono, and a fixture that is already in the target format would not
    exercise either path.
    """
    import math
    import struct

    path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(path), mode="w")

    video = container.add_stream("mpeg4", rate=fps)
    video.width, video.height = 320, 240
    video.pix_fmt = "yuv420p"

    audio = container.add_stream("aac", rate=audio_rate)

    total_frames = int(seconds * fps)
    for i in range(total_frames):
        frame = av.VideoFrame(320, 240, "yuv420p")
        luma = 16 + (i * 200 // max(total_frames - 1, 1))
        for plane, value in zip(frame.planes, (luma, 128, 128), strict=True):
            plane.update(bytes([value]) * plane.buffer_size)
        frame.pts = i
        for packet in video.encode(frame):
            container.mux(packet)

    # AAC expects 1024-sample frames.
    chunk = 1024
    total_samples = int(seconds * audio_rate)
    for start in range(0, total_samples, chunk):
        aframe = av.AudioFrame(format="s16", layout="stereo", samples=chunk)
        aframe.sample_rate = audio_rate
        aframe.pts = start
        payload = bytearray()
        for n in range(chunk):
            value = int(6000 * math.sin(2 * math.pi * 440 * (start + n) / audio_rate))
            payload += struct.pack("<hh", value, value)  # interleaved stereo
        aframe.planes[0].update(bytes(payload))
        for packet in audio.encode(aframe):
            container.mux(packet)

    for packet in video.encode():
        container.mux(packet)
    for packet in audio.encode():
        container.mux(packet)
    container.close()
    return path


@pytest.fixture(scope="session")
def sample_video(tmp_root: Path) -> Path:
    return _write_test_video(tmp_root / "fixtures" / "sample.mp4")


@pytest.fixture(scope="session")
def video_with_audio(tmp_root: Path) -> Path:
    return _write_video_with_audio(tmp_root / "fixtures" / "with_audio.mp4")


def _write_structured_video(
    path: Path, *, scenes: int = 4, scene_s: float = 3.0, fps: int = 10
) -> Path:
    """Video with distinct *spatial* patterns per scene.

    The flat-luma fixture cannot exercise keyframe detection: dHash compares
    horizontally adjacent pixels, so a uniformly filled frame hashes to zero no
    matter how bright it is. That invariance is desirable in production — a
    lighting change is not a slide change — but it means change detection needs
    a fixture with actual structure.
    """
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width, stream.height = 320, 240
    stream.pix_fmt = "yuv420p"

    pts = 0
    for scene in range(scenes):
        plane = np.zeros((240, 320), dtype=np.uint8)
        # A different stripe period per scene gives each one a distinct hash.
        period = 8 + scene * 11
        plane[:, ::period] = 235
        plane[:: period + 3, :] = 200

        for _ in range(int(scene_s * fps)):
            frame = av.VideoFrame(320, 240, "yuv420p")
            frame.planes[0].update(plane.tobytes())
            for p in frame.planes[1:]:
                p.update(bytes([128]) * p.buffer_size)
            frame.pts = pts
            pts += 1
            for packet in stream.encode(frame):
                container.mux(packet)

    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


@pytest.fixture(scope="session")
def structured_video(tmp_root: Path) -> Path:
    """Four visually distinct scenes, 3s each."""
    return _write_structured_video(tmp_root / "fixtures" / "structured.mp4")


@pytest.fixture
def sample_bytes(sample_video: Path) -> bytes:
    return sample_video.read_bytes()
