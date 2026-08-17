"""Application configuration, read from the environment with an ECHOLENS_ prefix."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Safe to import: the module's heavy dependencies (torch, sentence-transformers)
# are all loaded lazily inside functions.
from app.pipeline.rerank import RELEVANCE_FLOOR

# Repository root: backend/app/config.py -> backend/app -> backend -> repo
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ECHOLENS_",
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ─── Database ──────────────────────────────────────────────────────────
    # Defaults to the SQLite bridge so a fresh clone runs with no installs.
    # Phase 3 requires Postgres: SQLite has no vector search.
    database_url: str = "sqlite+aiosqlite:///./echolens.db"
    database_echo: bool = False

    # ─── Object storage ────────────────────────────────────────────────────
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: Path = REPO_ROOT / "storage"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket: str = "echolens"
    s3_access_key: str = "echolens"
    s3_secret_key: str = "echolens-dev-secret"
    s3_region: str = "us-east-1"

    # ─── Queue ─────────────────────────────────────────────────────────────
    # Unused until Redis is available; the in-process worker covers Phase 2.
    redis_url: str = "redis://localhost:6379/0"

    # ─── Speech recognition ────────────────────────────────────────────────
    # large-v3 needs ~3 GB VRAM and ~45s to load, then runs ~14x realtime on an
    # RTX 4070. Drop to "small" or "base" for faster iteration during dev.
    whisper_model: str = "large-v3"
    whisper_device: Literal["auto", "cuda", "cpu"] = "auto"
    # Empty means auto-detect. Pinning the language skips detection and avoids
    # a misdetected opening line derailing the whole transcript.
    whisper_language: str = ""

    # ─── Embeddings ────────────────────────────────────────────────────────
    # bge-large-en-v1.5 is 1024-dim and ~1.3 GB VRAM. Changing this needs a
    # migration (the vector column carries the dimension) and a full re-embed.
    embedding_model: str = "BAAI/bge-large-en-v1.5"
    embedding_device: Literal["auto", "cuda", "cpu"] = "auto"
    embedding_batch_size: int = 32
    # Hold the embedder in VRAM so queries stay fast. Cold-loading it costs ~7s,
    # which is the entire latency budget spent before the search even starts.
    # ~1.3 GB, affordable next to Whisper's ~3 GB — revisit when a VLM arrives
    # in Phase 4 and the 8 GB ceiling actually binds.
    embedding_keep_warm: bool = True

    # ─── Reranking (Phase 6) ───────────────────────────────────────────────
    # Cross-encoder over the fused candidate pool. ~95 MB VRAM, ~20 ms for 30
    # candidates once warm — but ~2.3 s cold, so it is preloaded like the
    # embedder rather than paid for by whoever searches first.
    rerank_enabled: bool = True
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_device: Literal["auto", "cuda", "cpu"] = "auto"
    rerank_keep_warm: bool = True

    # ─── Answering (Phase 7) ───────────────────────────────────────────────
    # The provider only turns a prompt into text. Citation integrity lives in
    # app/answer.py and is provider-agnostic, so swapping backends cannot
    # weaken the guarantees.
    llm_provider: Literal["gemini", "stub"] = "gemini"
    # gemini-2.5-flash rather than a newer preview model: the newest ones
    # carry a free-tier limit as low as 20 requests *per day*, which a
    # single afternoon of development exhausts. 2.5-flash is the stable
    # workhorse with a usable free allowance.
    llm_model: str = "gemini-2.5-flash"
    llm_max_tokens: int = 1024
    # Provider latency is not ours to control and is not always well behaved.
    llm_timeout_s: float = 30.0
    # Gemini returns 503 'high demand' for roughly one call in six on this
    # key; transient failures are retried with backoff, bad requests are not.
    llm_max_attempts: int = 3
    # Evidence below this cross-encoder score is treated as "not found" rather
    # than answered over.
    #
    # Defaulted from the reranker's own constant rather than repeated. These
    # were two independent 0.0s that had to agree, and they silently stopped
    # agreeing the moment one was recalibrated — the answer path kept refusing
    # at the old threshold while the search UI used the new one. A duplicated
    # constant is a drift waiting to happen; see docs/08-evaluation.md for how
    # the value itself was chosen.
    llm_relevance_floor: float = RELEVANCE_FLOOR
    # How many chunks reach the prompt. More is not better — it dilutes the
    # evidence and invites the model to pick something tangential.
    llm_evidence_items: int = 12

    # ─── Visual pipeline (Phase 4) ─────────────────────────────────────────
    # Keyframe selection. The scan decodes keyframes only, so it runs at ~1000x
    # realtime; these bound how many frames reach the expensive OCR stage.
    keyframe_min_gap_s: float = 4.0
    keyframe_max_gap_s: float = 90.0
    # Hamming distance out of 64, near the p75 of observed frame-to-frame change.
    keyframe_threshold: int = 10
    # Hard ceiling per video, so a pathological source cannot blow up OCR cost.
    keyframe_max_count: int = 2500
    keyframe_max_width: int = 1280

    # OCR runs on CPU (ONNX) to leave the GPU for embeddings and Whisper.
    # ONNX releases the GIL, so threads genuinely parallelise.
    ocr_enabled: bool = True
    ocr_workers: int = 8
    # Below this the recognised string is more likely wrong than right, and a
    # wrong string in the index can only ever produce a bad match.
    ocr_min_confidence: float = 0.60

    # ─── Uploads ───────────────────────────────────────────────────────────
    # Uploads stream to storage in chunks, so this bounds disk usage, not memory.
    max_upload_bytes: int = 16 * 1024**3
    upload_chunk_bytes: int = 1024**2

    # ─── API ───────────────────────────────────────────────────────────────
    # NoDecode suppresses pydantic-settings' default JSON decoding of complex
    # types. Without it, a plain comma-separated value in .env raises before any
    # validator gets a chance to parse it.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    log_level: str = "INFO"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept a comma-separated string so .env stays readable."""
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def supports_vectors(self) -> bool:
        """False on the SQLite bridge. Phase 3 features must gate on this."""
        return not self.is_sqlite


@lru_cache
def get_settings() -> Settings:
    return Settings()
