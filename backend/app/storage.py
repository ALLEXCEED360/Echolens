"""Object storage abstraction.

Video bytes never pass through the ORM and nothing above this layer knows whether
it is talking to a local disk or to S3. `LocalStorage` is the dev backend (no
installs needed); `S3Storage` targets MinIO in dev and AWS in production.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import aiofiles
import aiofiles.os

from app.config import Settings, get_settings


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    checksum_sha256: str


class StorageError(RuntimeError):
    pass


class UploadTooLarge(StorageError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"Upload exceeds the {limit} byte limit")
        self.limit = limit


@runtime_checkable
class Storage(Protocol):
    async def save_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        max_bytes: int | None = None,
    ) -> StoredObject: ...

    async def read_range(self, key: str, start: int, end: int) -> AsyncIterator[bytes]: ...

    async def size(self, key: str) -> int: ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None: ...

    def local_path(self, key: str) -> Path | None:
        """Filesystem path if one exists, else None.

        Processing stages need a real path to hand to PyAV/ffmpeg. S3-backed
        deployments must download to a temp file first; this returning None is
        the signal to do that.
        """
        ...


def make_storage_key(video_id: uuid.UUID, filename: str) -> str:
    """Sharded, collision-free key that preserves the original extension.

    Sharding by id prefix keeps directory listings small on local disk and
    avoids S3 hot-partitioning on sequential keys.
    """
    suffix = Path(filename).suffix.lower()[:16]
    sid = str(video_id)
    return f"videos/{sid[:2]}/{sid}/source{suffix}"


class LocalStorage:
    """Filesystem-backed storage rooted at a configured directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        # Reject traversal: the resolved path must stay under the root.
        candidate = (self.root / key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise StorageError(f"Refusing to access key outside storage root: {key!r}")
        return candidate

    async def save_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        max_bytes: int | None = None,
    ) -> StoredObject:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        digest = hashlib.sha256()
        total = 0
        # Write to a temp sibling and rename, so a failed upload never leaves a
        # partial file that looks complete.
        tmp = path.with_suffix(path.suffix + ".partial")

        try:
            async with aiofiles.open(tmp, "wb") as fh:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if max_bytes is not None and total > max_bytes:
                        raise UploadTooLarge(max_bytes)
                    digest.update(chunk)
                    await fh.write(chunk)
            await aiofiles.os.replace(tmp, path)
        except BaseException:
            # Covers UploadTooLarge, client disconnect, and disk errors alike.
            with suppress_errors():
                await aiofiles.os.remove(tmp)
            raise

        return StoredObject(key=key, size_bytes=total, checksum_sha256=digest.hexdigest())

    async def read_range(self, key: str, start: int, end: int) -> AsyncIterator[bytes]:
        """Yield bytes in [start, end] inclusive — HTTP Range semantics."""
        path = self._resolve(key)
        remaining = end - start + 1
        if remaining <= 0:
            return

        async with aiofiles.open(path, "rb") as fh:
            await fh.seek(start)
            while remaining > 0:
                chunk = await fh.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    async def size(self, key: str) -> int:
        stat = await aiofiles.os.stat(self._resolve(key))
        return stat.st_size

    async def exists(self, key: str) -> bool:
        return await aiofiles.os.path.exists(self._resolve(key))

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        with suppress_errors():
            await aiofiles.os.remove(path)
        # Prune the now-empty per-video directory.
        with suppress_errors():
            shutil.rmtree(path.parent, ignore_errors=True)

    def local_path(self, key: str) -> Path | None:
        return self._resolve(key)


class S3Storage:
    """S3/MinIO backend. Wired in when Docker lands — see docs/05-environment.md."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _unavailable(self) -> StorageError:
        return StorageError(
            "S3Storage is not implemented yet. Set ECHOLENS_STORAGE_BACKEND=local, "
            "or implement this backend once Docker/MinIO is available."
        )

    async def save_stream(
        self, key: str, chunks: AsyncIterator[bytes], *, max_bytes: int | None = None
    ) -> StoredObject:
        raise self._unavailable()

    async def read_range(self, key: str, start: int, end: int) -> AsyncIterator[bytes]:
        raise self._unavailable()
        yield b""  # pragma: no cover — makes this an async generator

    async def size(self, key: str) -> int:
        raise self._unavailable()

    async def exists(self, key: str) -> bool:
        raise self._unavailable()

    async def delete(self, key: str) -> None:
        raise self._unavailable()

    def local_path(self, key: str) -> Path | None:
        # No filesystem path: callers must download to a temp file first.
        return None


class suppress_errors:
    """Context manager swallowing OSError during best-effort cleanup."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        settings = get_settings()
        if settings.storage_backend == "s3":
            _storage = S3Storage(settings)
        else:
            _storage = LocalStorage(settings.storage_local_path)
    return _storage


__all__ = [
    "LocalStorage",
    "S3Storage",
    "Storage",
    "StorageError",
    "StoredObject",
    "UploadTooLarge",
    "get_storage",
    "make_storage_key",
]
