# EchoLens

Multimodal video intelligence and temporal search. Turn hours of raw video into a
searchable, structured, evidence-backed knowledge base.

> **Status: Phase 9** — upload, transcribe on the GPU, read on-screen text,
> search it with hybrid retrieval, ask questions answered with clickable
> timestamps the model could not have fabricated, group videos into
> collections, and **measure all of it against a benchmark**.
> See [docs/04-roadmap.md](docs/04-roadmap.md) for what lands when, and
> [docs/08-evaluation.md](docs/08-evaluation.md) for how well it actually works.

> **Read the evaluation before believing the pitch.** The benchmark found three
> real bugs, corrected an earlier claim of mine about reranking, and records
> what is still unmeasured. Numbers here are from one 6-hour video.

---

## The one-sentence version

EchoLens converts raw video into a temporally structured knowledge base, so you can
search, analyse and question hours of footage and get answers linked to the exact
moment they came from.

## Why not just feed the video to an LLM

For a single two-hour video, a long-context multimodal model is a genuine competitor
and you should be honest about that. EchoLens earns its architecture at corpus scale:

| | Video → LLM → Answer | EchoLens |
| --- | --- | --- |
| 100 hours of video | Impossible / absurdly expensive | Indexed once, queried forever |
| Query latency | Minutes | Target < 3s |
| Cost per query | Scales with video length | Scales with result count |
| Citations | Model asserts a timestamp | Timestamp resolved from a retrieved record |
| Cross-video reasoning | Not possible | First-class |

The pipeline is the product. The models are commodity parts.

---

## Quick start

Requires **Python 3.13**, **Node 22** and **Docker Desktop**. Postgres + pgvector
is not optional from Phase 3 — semantic search needs a vector index.

```bash
docker compose up -d
```

One-time setup:

```bash
python -m venv .venv && .venv/Scripts/python.exe -m pip install -e "backend[dev]" faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12
```

The two `nvidia-*` wheels supply cuBLAS and cuDNN. Without them the model loads and
then the first inference fails — see [docs/05-environment.md](docs/05-environment.md).

```bash
cp .env.example .env && .venv/Scripts/alembic.exe -c backend/alembic.ini upgrade head
```

Already have transcripts from before Phase 3? Index them without re-transcribing:

```bash
.venv/Scripts/python.exe backend/scripts/backfill_chunks.py
```

### Re-running part of the pipeline

Transcription is the expensive stage, so it is opt-out. `POST /api/videos/{id}/process`
accepts `stages` — a preset or a comma-separated list:

| preset | runs | use |
| --- | --- | --- |
| `all` (default) | everything | first pass |
| `visual` | keyframes, OCR, embed | add or refresh the visual layer |
| `speech` | audio, transcribe, embed | re-transcribe only |
| `index` | embed | re-chunk and re-embed what is stored |

Unselected stages read their inputs from storage, so `visual` reuses the existing
transcript — 8.9 min instead of ~40 on a 6-hour video. The **Analyse visuals**
button on the video page does exactly this.

Then run the two servers in separate terminals:

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --app-dir backend --port 8000
```

```bash
npm --prefix frontend install && npm --prefix frontend run dev
```

Open <http://localhost:3000>. API docs at <http://localhost:8000/docs>.

To run a production build while the dev server is up, use a separate output
directory — they otherwise share `.next/` and clobber each other:

```bash
NEXT_DIST_DIR=.next-build npx --prefix frontend next build
```

### Tests

The suite runs against Postgres and creates its own throwaway database, so
`docker compose up -d` must be running first.

```bash
.venv/Scripts/python.exe -m pytest backend -q
```

---

## Layout

```
backend/
  app/
    main.py         FastAPI app, lifespan, health
    config.py       Environment-driven settings
    models.py       ORM: videos, jobs, transcripts, chunks, keyframes, events, topics
    storage.py      Storage protocol + local/S3 backends
    probe.py        Container inspection via PyAV (no ffmpeg binary needed)
    ranges.py       HTTP Range parsing — the seek path
    gpu.py          CUDA DLL discovery (the Windows cuBLAS trap)
    pipeline/
      audio.py      Decode → 16 kHz mono WAV
      transcribe.py faster-whisper wrapper, cached model
      runner.py     Stage orchestration + in-process queue
      chunking.py   Parent/child chunking — decides retrieval quality
      embedding.py  bge-large-en-v1.5 on the GPU
      keyframes.py  Keyframe-only decode scan (~1000x realtime)
      ocr.py        RapidOCR on CPU + indexability heuristic
      events.py     Embedding-based topic segmentation + rule events
      rerank.py     Cross-encoder reranking (~20ms, calibrated scores)
      llm.py        Swappable LLM provider (Gemini / stub)
    search.py       Hybrid retrieval: RRF fusion, filters, temporal context
    answer.py       Citation integrity — the model never writes a timestamp
    concepts.py     Concept timelines: retrieval grouped by video, ordered by time
    api/            videos, jobs, transcripts, search, keyframes, events, ask,
                    collections
  alembic/          Migrations
  scripts/          One-off migration and backfill utilities
  tests/            302 tests, run against Postgres
frontend/           Next.js 15 app router, Tailwind v4
docs/               Architecture, data model, pipeline, retrieval, roadmap
infra/              Postgres init SQL
storage/            Local object store (gitignored) — S3 stand-in for dev
```

## Documentation

- [00 — Architecture](docs/00-architecture.md) — components, decisions, rejected options
- [01 — Data model](docs/01-data-model.md) — full target schema, not just Phase 1
- [02 — Processing pipeline](docs/02-pipeline.md) — stage order and why OCR comes last
- [03 — Retrieval and evidence](docs/03-retrieval.md) — hybrid search, citation integrity
- [04 — Roadmap](docs/04-roadmap.md) — phases with honest estimates
- [05 — Environment](docs/05-environment.md) — machine-specific setup and constraints
- [06 — Benchmarks](docs/06-benchmarks.md) — measured results: retrieval latency, batched ASR

---

## License

MIT — see [LICENSE](LICENSE). Use it, change it, ship it; keep the copyright
notice.

The corpus used to produce the numbers in [docs/08-evaluation.md](docs/08-evaluation.md)
is not included and is not covered by this licence: the videos belong to their
respective creators.
