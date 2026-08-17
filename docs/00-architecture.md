# 00 — Architecture

## Component map

```
                              Browser
                                 │
                    ┌────────────▼────────────┐
                    │  Next.js 15 (app dir)   │
                    │  player · library · ask │
                    └────────────┬────────────┘
                                 │ REST + SSE
                    ┌────────────▼────────────┐
                    │        FastAPI          │
                    │  videos · jobs · search │
                    └──┬──────────────────┬───┘
                       │                  │
        ┌──────────────▼──────┐    ┌──────▼──────────────┐
        │   Job queue (arq)   │    │  Retrieval service  │
        └──────────┬──────────┘    └──────┬──────────────┘
                   │                      │
     ┌─────────────┼─────────────┐        │
     ▼             ▼             ▼        │
  Transcribe   Keyframes    Embeddings    │
  (whisper)    (+OCR/VLM)   (bge/clip)    │
     │             │             │        │
     └─────────────┼─────────────┘        │
                   ▼                      ▼
            ┌──────────────────────────────────┐
            │   PostgreSQL 17 + pgvector       │
            │   rows · vectors · FTS · ranges  │
            └──────────────────────────────────┘
                   ▲
            ┌──────┴───────┐
            │ Object store │  local disk (dev) → S3/MinIO (prod)
            └──────────────┘
```

## Decisions

### D1 — Postgres + pgvector is the only datastore

Vector similarity, BM25 lexical search (Postgres FTS), metadata filtering and temporal
range queries all live in one engine, under one transaction boundary. A dedicated
vector database would add an operational component and a consistency problem —
"the vector index says chunk 4821 exists, the row was deleted" — in exchange for
performance we do not need at this scale.

`tsvector` handles keyword search. `vector` handles semantics. `tstzrange`/numeric
ranges with a GiST index handle "what else was happening at 00:24:17".

### D2 — Queue: in-process now, arq when Redis exists

Celery dropped functional Windows support at v4; the prefork pool does not work and
the workarounds are fragile. So the target is **arq** — asyncio-native, ~1k lines,
Redis-backed, matching the FastAPI model.

Redis is not installed (no Docker, no WSL), so Phase 2 ships an **in-process asyncio
queue with a single consumer** — a deliberate bridge in the same spirit as SQLite.

It is more than a stopgap: one consumer serialises GPU stages for free, which is
exactly the constraint 8 GB of VRAM imposes anyway. Whisper large-v3 and a VLM will
not coexist, so the queue *has* to serialise them regardless of backend.

What is genuinely lost until Redis arrives is durability. A job interrupted by a
restart cannot be resumed; it is reaped and marked failed at next boot, which is at
least honest. Swapping in arq replaces `enqueue()` and the consumer loop only — the
stage functions take a session and a job id and know nothing about either.

### D3 — Object storage is abstracted from day one

`Storage` is a protocol with `LocalStorage` (dev) and `S3Storage` (MinIO/AWS)
implementations. Nothing above the storage layer knows which is active. Video bytes
never pass through the ORM.

### D4 — No object detection

YOLO-style detection was in the original plan and is cut. For lectures, meetings,
talks and tutorials, `person`/`laptop`/`chair` labels carry near-zero retrieval
signal — the query "find the chair" does not exist. A vision-language model caption
("three-layer network diagram with labelled weight matrices") is embeddable,
searchable and actually answers user questions.

Revisit only if security footage becomes a target use case, where object and
person-count signals genuinely matter.

### D5 — Citations are resolved, never generated

The LLM is shown evidence tagged with opaque IDs (`[c_1842]`) and must cite by ID.
A post-processing step maps IDs to timestamps and **rejects any citation that does
not resolve to a retrieved chunk**. The model cannot emit a timestamp directly, so
hallucinated timestamps are structurally impossible rather than statistically
unlikely. See [03 — Retrieval](03-retrieval.md).

### D6 — Async SQLAlchemy 2.0

The workload is IO-bound: streaming multi-GB uploads, range-serving video, fanning
out four retrieval strategies per query. Async is the right default for all three and
retrofitting it later is invasive.

### D7 — Portable column types in migrations

Enums are stored as `String` with application-level validation, not native Postgres
enum types. This keeps the SQLite dev bridge working and sidesteps the notoriously
awkward `ALTER TYPE` migration path. Costs a database-level integrity check we did
not rely on anyway.

### D8 — Progress writes use isolated sessions

Stages run under `asyncio.to_thread`, so their progress callbacks are scheduled back
onto the event loop while the stage coroutine may itself be mid-`commit()`. Two
concurrent commits on one `AsyncSession` raise `IllegalStateChangeError` — sessions
are explicitly not concurrency-safe.

Progress therefore writes on its own short-lived session, guarded by
`WHERE status = 'running'` so a late-arriving callback cannot reset a finished
stage's progress from 1.0 back to 0.97. Progress failures are swallowed: it is
cosmetic and must never fail a job.

## Rejected

| Option | Why not |
| --- | --- |
| Dedicated vector DB (Qdrant, Weaviate) | Second store to operate, consistency risk, no benefit at this scale |
| Celery | Broken on Windows without WSL |
| Kafka | Redis handles our throughput by three orders of magnitude |
| Knowledge graph (original §19) | High build cost, speculative query value. Deferred indefinitely |
| Auth in Phase 1 | Single user for months. Adds surface area, delivers nothing |
| Training any model | The system is the contribution, not the weights |
| Kubernetes | One machine |

## Where this gets hard

1. **Sampling** — 900k frames in a two-hour video. Visual stability detection is the
   gate that makes everything downstream affordable. Covered in [02](02-pipeline.md).
2. **Temporal alignment** — speech, frames, OCR and events must agree on a shared
   clock. Every record carries `start_s`/`end_s` in float seconds from container
   origin. No frame numbers, no timecodes, no exceptions.
3. **Retrieval spread** — the answer to one question may live across a transcript
   chunk, a slide and a caption. Fusion ranking, not a single index.
4. **Hallucination** — addressed structurally (D5), not with prompt pleading.
5. **Latency** — 3s budget: ~100ms embed, ~200ms four parallel searches, ~400ms
   rerank, remainder is the LLM. Implies streaming the answer token by token.
6. **Cost** — cache aggressively, keep transcription local on the GPU, reserve API
   calls for reasoning over already-retrieved evidence.
