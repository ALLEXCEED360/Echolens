"""Local storage: streaming writes, checksums, range reads, traversal safety."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.storage import LocalStorage, StorageError, UploadTooLarge, make_storage_key


async def _chunks(data: bytes, size: int = 7) -> AsyncIterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "store")


class TestSaveStream:
    async def test_writes_bytes_and_reports_size(self, storage: LocalStorage) -> None:
        data = b"echolens" * 100
        result = await storage.save_stream("v/a.bin", _chunks(data))

        assert result.size_bytes == len(data)
        assert await storage.size("v/a.bin") == len(data)
        assert storage.local_path("v/a.bin").read_bytes() == data

    async def test_checksum_matches_hashlib(self, storage: LocalStorage) -> None:
        data = b"deterministic content"
        result = await storage.save_stream("v/b.bin", _chunks(data))
        assert result.checksum_sha256 == hashlib.sha256(data).hexdigest()

    async def test_empty_stream_is_allowed_but_zero_length(self, storage: LocalStorage) -> None:
        # The API layer rejects empty uploads; storage just reports the truth.
        result = await storage.save_stream("v/empty.bin", _chunks(b""))
        assert result.size_bytes == 0

    async def test_over_limit_raises_and_leaves_nothing_behind(
        self, storage: LocalStorage
    ) -> None:
        with pytest.raises(UploadTooLarge):
            await storage.save_stream("v/big.bin", _chunks(b"x" * 500), max_bytes=100)

        assert not await storage.exists("v/big.bin")
        # The temp file must be gone too, not merely the final name.
        assert list(storage.root.rglob("*.partial")) == []

    async def test_producer_error_leaves_nothing_behind(self, storage: LocalStorage) -> None:
        async def failing() -> AsyncIterator[bytes]:
            yield b"partial"
            raise ConnectionError("client went away")

        with pytest.raises(ConnectionError):
            await storage.save_stream("v/broken.bin", failing())

        assert not await storage.exists("v/broken.bin")
        assert list(storage.root.rglob("*.partial")) == []


class TestReadRange:
    @pytest.fixture
    async def populated(self, storage: LocalStorage) -> LocalStorage:
        await storage.save_stream("v/data.bin", _chunks(bytes(range(256))))
        return storage

    async def _collect(self, storage: LocalStorage, start: int, end: int) -> bytes:
        return b"".join([c async for c in storage.read_range("v/data.bin", start, end)])

    async def test_full_range(self, populated: LocalStorage) -> None:
        assert await self._collect(populated, 0, 255) == bytes(range(256))

    async def test_partial_range_is_inclusive(self, populated: LocalStorage) -> None:
        result = await self._collect(populated, 10, 19)
        assert result == bytes(range(10, 20))
        assert len(result) == 10

    async def test_single_byte(self, populated: LocalStorage) -> None:
        assert await self._collect(populated, 42, 42) == bytes([42])

    async def test_tail(self, populated: LocalStorage) -> None:
        assert await self._collect(populated, 250, 255) == bytes(range(250, 256))

    async def test_inverted_range_yields_nothing(self, populated: LocalStorage) -> None:
        assert await self._collect(populated, 100, 50) == b""


class TestSafety:
    async def test_traversal_is_refused(self, storage: LocalStorage) -> None:
        with pytest.raises(StorageError):
            await storage.exists("../../../etc/passwd")

    async def test_absolute_escape_is_refused(self, storage: LocalStorage) -> None:
        with pytest.raises(StorageError):
            await storage.exists("videos/../../outside.bin")


class TestKeys:
    def test_preserves_extension_and_shards(self) -> None:
        vid = uuid.uuid4()
        key = make_storage_key(vid, "Lecture 03.MP4")
        assert key.endswith(".mp4")
        assert str(vid) in key
        assert key.startswith(f"videos/{str(vid)[:2]}/")

    def test_handles_missing_extension(self) -> None:
        key = make_storage_key(uuid.uuid4(), "noextension")
        assert key.endswith("/source")

    def test_distinct_ids_never_collide(self) -> None:
        a = make_storage_key(uuid.uuid4(), "same.mp4")
        b = make_storage_key(uuid.uuid4(), "same.mp4")
        assert a != b
