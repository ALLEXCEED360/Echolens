# 05 — Environment

Detected on this machine, 2026-08-16.

| Component | Status |
| --- | --- |
| GPU | RTX 4070 Laptop, **8 GB** VRAM, driver 610.88 ✅ |
| Python | 3.13.7 ✅ |
| Node | 22.18.0 ✅ |
| git | 2.50.1 ✅ |
| Docker | ❌ not installed |
| WSL2 | ❌ not installed |
| ffmpeg / ffprobe | ❌ not on PATH |
| winget / choco | ❌ neither available |
| PostgreSQL | ❌ not installed |

## What this means

### Phase 1 and 2 run with zero additional installs

- **Database** → SQLite via `aiosqlite`. Phase 1 tables have no vector columns, so
  the same models and the same Alembic migration work on both engines. Portable
  column types (decision D7) exist precisely for this.
- **Storage** → local filesystem under `storage/`, behind the same `Storage`
  protocol that `S3Storage` implements.
- **Probing** → **PyAV**, which binds libav directly and ships its own ffmpeg
  libraries in the wheel. No `ffprobe.exe` required. This is why probe works today.

### Phase 3 requires Docker

SQLite has no vector search. There is no workaround worth having.

Native Windows alternatives were considered and rejected: pgvector needs an MSVC
build against a Postgres install, and Redis has no maintained native Windows port
(Memurai is a commercial fork). Docker Desktop is one install that solves both, and
it brings WSL2 with it.

Download: <https://www.docker.com/products/docker-desktop/> · ~1 GB, requires a
reboot. Then `docker compose up -d` and switch two lines in `.env`.

### ffmpeg is needed from Phase 2

PyAV covers probing but audio extraction is cleaner through the CLI. No package
manager here, so it is a manual install:

1. Download a release build from <https://www.gyan.dev/ffmpeg/builds/> (`essentials`
   is enough).
2. Extract to `C:\ffmpeg`.
3. Add `C:\ffmpeg\bin` to PATH.
4. Verify: `ffprobe -version`.

Installing Docker Desktop first is also fine — you can run ffmpeg in a container —
but a local binary is simpler for iterating.

## GPU budget

8 GB is the binding constraint on model selection.

| Model | VRAM (fp16) | Verdict |
| --- | --- | --- |
| faster-whisper large-v3 | ~4.7 GB | ✅ comfortable |
| pyannote 3.1 | ~1.5 GB | ✅ |
| bge-large-en-v1.5 | ~1.3 GB | ✅ |
| Qwen2-VL-2B | ~5.5 GB | ✅ alone |
| Florence-2-large | ~1.6 GB | ✅ |
| Any 7B VLM | 15 GB+ | ❌ |

**Never run two GPU stages concurrently.** The worker serialises stages tagged
`gpu`; this is enforced in the queue, not by convention.

## Python 3.13 — cleared

The flagged risk did not materialise. `ctranslate2` ships a `cp313-win_amd64` wheel
(4.8.1) and `faster-whisper` 1.2.1 is pure Python. PyAV 18.1 also has cp313 wheels,
and its bundled FFmpeg includes **libx264 and h264_nvenc** — so H.264 encoding is
available without installing ffmpeg at all.

`pyannote.audio` (diarization) still needs checking before that stage is built; it
pulls in torch, which is a much heavier dependency than anything here so far.

## CUDA on Windows — the DLL trap

Solved in `backend/app/gpu.py`, and worth understanding because the failure mode is
deeply misleading.

faster-whisper on GPU needs cuBLAS and cuDNN. Installed via pip:

```bash
.venv/Scripts/python.exe -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

That puts the DLLs inside `site-packages/nvidia/*/bin`, where nothing looks for them.
The symptom is that `ctranslate2.get_cuda_device_count()` cheerfully returns `1` and
the model loads fine, then the **first inference** dies with:

```
RuntimeError: Library cublas64_12.dll is not found or cannot be loaded
```

`os.add_dll_directory()` does **not** fix this. It governs Python's own extension
loading, whereas ctranslate2 resolves cuBLAS lazily from its C++ side via a plain
`LoadLibrary`, which follows the default Windows search order. The fix is to prepend
those directories to `PATH` **before** faster-whisper is imported.

Note `nvidia` is a namespace package, so `nvidia.__file__` is `None` — iterate
`nvidia.__path__` instead.

## Measured performance

RTX 4070 Laptop, large-v3, float16. **These are from a real 6-hour Unity tutorial**,
not a synthetic clip — the short-clip figure of ~14× realtime was optimistic and is
superseded.

| | |
| --- | --- |
| Source | 5.99 h, 0.56 GB, AAC |
| Audio extraction | **23 s** (PyAV, ~940× realtime) |
| Transcription | **45.6 min → 7.9× realtime** |
| Segments produced | 6,625 |
| Speech detected | 5.79 h of 5.99 h |
| Language confidence | 0.998 (en) |
| VRAM | ~3 GB |
| Model load (warm) | ~5 s (~45 s cold) |

**Rule of thumb: about 8 minutes of GPU time per hour of video.** A 2-hour lecture is
~15 minutes, not the ~9 the short-clip benchmark implied.

Quality on real content held up: coherent from first segment to last, no hallucination
loops, only one silence gap over 30 s, mean segment 3.15 s.

### Faster transcription — benchmarked

`BatchedInferencePipeline` at `batch_size=4` with `word_timestamps=True` and
post-hoc re-segmentation runs the same 6-hour video in **9.6 min instead of 45.6**
(4.75×), at **5,897 MB** peak — less memory than sequential, not more.

Batch 16 is slower than batch 4 on this card: it hits 7,930 MB of 8,188 MB and
thrashes. Full numbers and methodology in [06 — Benchmarks](06-benchmarks.md).

## Configuration

Everything is environment-driven with the `ECHOLENS_` prefix; see `.env.example`.
The only two settings that change between the dev bridge and the real stack:

```
ECHOLENS_DATABASE_URL=postgresql+asyncpg://echolens:echolens@localhost:5432/echolens
ECHOLENS_STORAGE_BACKEND=s3
```
