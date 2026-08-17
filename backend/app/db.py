"""Async SQLAlchemy engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()

_engine_kwargs: dict[str, object] = {"echo": _settings.database_echo}
if not _settings.is_sqlite:
    # Pool tuning is meaningless for SQLite's single-file driver.
    _engine_kwargs |= {"pool_size": 10, "max_overflow": 20, "pool_pre_ping": True}

engine = create_async_engine(_settings.database_url, **_engine_kwargs)

if _settings.is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn: object, _record: object) -> None:
        """SQLite disables foreign keys per connection by default.

        Without this, ON DELETE CASCADE is silently ignored and deleting a video
        leaves orphaned jobs behind — a difference in behaviour from Postgres
        that would only surface much later.
        """
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")  # concurrent reads during writes
        cursor.close()

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # keep objects usable after commit, for response serialisation
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that rolls back on error."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
