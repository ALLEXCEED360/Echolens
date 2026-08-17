"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import ask, collections, events, jobs, keyframes, search, transcripts, videos
from app.config import get_settings
from app.db import engine
from app.pipeline import runner

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("echolens")


async def _warm_embedder() -> None:
    """Preload the embedding model so the first search is not the slow one."""
    try:
        from app.pipeline.embedding import embed_query

        await embed_query(
            "warmup",
            model_name=settings.embedding_model,
            device=settings.embedding_device,
        )
        logger.info("Embedding model warm: %s", settings.embedding_model)
    except Exception:  # noqa: BLE001 — a cold model is slow, not fatal
        logger.warning("Could not warm the embedding model", exc_info=True)


async def _warm_reranker() -> None:
    """Preload the cross-encoder. Cold it costs ~2.3s; warm, ~20ms."""
    try:
        from app.pipeline.rerank import rerank

        await rerank(
            "warmup",
            ["warmup document"],
            model_name=settings.rerank_model,
            device=settings.rerank_device,
        )
        logger.info("Reranker warm: %s", settings.rerank_model)
    except Exception:  # noqa: BLE001
        logger.warning("Could not warm the reranker", exc_info=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

    backend = "SQLite (dev bridge)" if settings.is_sqlite else "PostgreSQL"
    logger.info("Database: %s", backend)
    logger.info("Storage:  %s", settings.storage_backend)

    if settings.is_sqlite:
        logger.warning(
            "Running on the SQLite bridge. Fine for Phase 1-2; Phase 3 search "
            "requires Postgres + pgvector (see docs/05-environment.md)."
        )

    # The in-process queue does not survive a restart, so anything still marked
    # running was interrupted. Say so rather than leaving a stalled progress bar.
    orphaned = await runner.reap_orphaned_jobs()
    if orphaned:
        logger.warning("Marked %d interrupted job(s) as failed", orphaned)

    await runner.start_worker()
    logger.info("Whisper: %s (device=%s)", settings.whisper_model, settings.whisper_device)

    if settings.embedding_keep_warm:
        # Backgrounded so startup is not blocked by a ~7s model load; the first
        # query may still pay for it if it arrives immediately.
        asyncio.create_task(_warm_embedder())
    if settings.rerank_enabled and settings.rerank_keep_warm:
        asyncio.create_task(_warm_reranker())

    yield

    await runner.stop_worker()
    await engine.dispose()


app = FastAPI(
    title="EchoLens API",
    version="0.1.0",
    description="Multimodal video intelligence and temporal search.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # The browser cannot read Content-Range on cross-origin requests unless it
    # is explicitly exposed — and without it, seeking silently breaks in dev.
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)

app.include_router(videos.router)
app.include_router(jobs.router)
app.include_router(transcripts.router)
app.include_router(search.router)
app.include_router(keyframes.router)
app.include_router(events.router)
app.include_router(ask.router)
app.include_router(collections.router)


@app.get("/api/health", tags=["meta"])
async def health() -> dict[str, object]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        logger.exception("Health check: database unreachable")
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
        "storage_backend": settings.storage_backend,
        "vector_search": settings.supports_vectors,
        "queue_depth": runner.queue_depth(),
        "whisper_model": settings.whisper_model,
        "embedding_model": settings.embedding_model,
        "rerank_model": settings.rerank_model if settings.rerank_enabled else None,
        "llm_model": f"{settings.llm_provider}/{settings.llm_model}",
        "phase": 8,
    }
