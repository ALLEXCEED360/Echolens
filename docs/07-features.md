# 07 — Feature inventory and verification

Everything built through Phase 8, with a way to check each piece yourself.

**Before anything else**, the stack must be up:

```bash
docker compose up -d
```

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --port 8000
```

```bash
npm --prefix frontend run dev
```

One command tells you what is configured and alive:

```bash
curl -s http://localhost:8000/api/health
```

```json
{"status":"ok","database":"up","storage_backend":"local","vector_search":true,
 "queue_depth":0,"whisper_model":"large-v3","embedding_model":"BAAI/bge-large-en-v1.5",
 "rerank_model":"cross-encoder/ms-marco-MiniLM-L-6-v2","llm_model":"gemini/gemini-2.5-flash",
 "phase":8}
```

`vector_search: false` means you are on the retired SQLite bridge and nothing from
Phase 3 onward will work.

---

## 1 · Ingest and playback

| Feature | Where |
| --- | --- |
| Streaming upload of multi-GB files | `POST /api/videos` |
| Container probing without ffmpeg | `app/probe.py` (PyAV) |
| Range-aware video streaming | `GET /api/videos/{id}/stream` |
| Library with filter | `/` |

**Verify — upload and probe.** Drag a video onto <http://localhost:3000>. Within
seconds the row should show real duration, resolution and size. Or:

```bash
curl -s -X POST "http://localhost:8000/api/videos?filename=test.mp4" --data-binary @video.mp4 | head -c 400
```

Expect `201` and populated `duration_s`, `width`, `height`, `fps`, `has_audio`.

**Verify — seeking actually works.** This is the one people assume and skip. A
`<video>` element cannot scrub unless the server answers byte ranges:

```bash
curl -s -o /dev/null -D - -H "Range: bytes=0-99" http://localhost:8000/api/videos/{id}/stream | head -6
```

Expect `HTTP/1.1 206 Partial Content` and `Content-Range: bytes 0-99/<size>`.
A `200` here means seeking is silently broken.

**Verify — bad input is refused early.** Upload a `.txt`, or a `.mp4` containing
junk. Expect `415` and `422` respectively, not a failure three pipeline stages
later.

---

## 2 · Transcription

| Feature | Where |
| --- | --- |
| GPU transcription (faster-whisper large-v3) | `app/pipeline/transcribe.py` |
| Audio extraction to 16 kHz mono | `app/pipeline/audio.py` (PyAV, no ffmpeg) |
| Timestamped, clickable transcript | video page |
| Virtualised list for long videos | `components/TranscriptPanel.tsx` |
| Substring search across transcripts | `GET /api/search/transcript` |

**Verify — transcribe.** Open a video, click **Transcribe**, watch the pipeline
panel. Roughly 8 minutes of GPU time per hour of video.

```bash
curl -s -X POST "http://localhost:8000/api/videos/{id}/process" | head -c 200
curl -s "http://localhost:8000/api/videos/{id}/transcript" | head -c 300
```

**Verify — click-to-seek precision.** Click any transcript line. The player must
jump to that line's exact start, not near it. Measured at 0.00 / 3.50 / 9.96 s,
all landing within 0.05 s.

**Verify — long-video performance.** On a 6-hour video the transcript is ~6,000
rows. Only ~30 should exist in the DOM at once:

```js
// browser console, on a video page
document.querySelectorAll('.transcript-row').length   // ~30, not ~6000
```

**Verify — a silent video degrades rather than fails.** Process a video with no
audio track. The speech stages must read `skipped` with reason "no audio stream"
and the job must still succeed.

---

## 3 · Visual pipeline

| Feature | Where |
| --- | --- |
| Keyframe scan (~1000× realtime) | `app/pipeline/keyframes.py` |
| OCR on keyframes (CPU, 8 threads) | `app/pipeline/ocr.py` |
| Keyframe filmstrip | `components/KeyframeStrip.tsx` |
| On-screen text indexed as searchable chunks | `kind=ocr` |

**Verify — keyframes.**

```bash
curl -s "http://localhost:8000/api/videos/{id}/keyframes?limit=5&with_text_only=true" | head -c 400
```

Or scroll the filmstrip under the player. Frames carrying text are badged **T**;
hover shows what was read.

**Verify — OCR quality is resolution-bound.** This is a property of your source,
not a bug. At 640×360 expect ~0.75 mean confidence with visible errors
("SoriteRenderer"). At 1080p it is materially better. See
[06-benchmarks.md](06-benchmarks.md).

**Verify — the visual branch is non-critical.** If OCR fails, the transcript must
survive. The job reports partial success rather than failing outright.

---

## 4 · Search

| Feature | Where |
| --- | --- |
| Semantic retrieval (pgvector, HNSW) | `app/search.py` |
| Lexical retrieval (Postgres FTS) | same |
| Reciprocal rank fusion | `reciprocal_rank_fusion()` |
| Cross-encoder reranking | `app/pipeline/rerank.py` |
| Temporal context per hit | `temporal_context()` |
| Filters: video, collection, kind, time range | `_apply_filters()` |

**Verify — it finds things.** <http://localhost:3000/search>, ask for something
in your corpus.

```bash
curl -s "http://localhost:8000/api/search?q=what+is+a+prefab&limit=3" | head -c 600
```

Each hit shows `vec` / `kw` tags for which retriever found it, a cross-encoder
score, and `↑N` when reranking promoted it.

**Verify — reranking actually changes the answer.** Compare:

```bash
curl -s "http://localhost:8000/api/search?q=what+is+a+prefab+used+for&limit=3&rerank=false"
curl -s "http://localhost:8000/api/search?q=what+is+a+prefab+used+for&limit=3&rerank=true"
```

The reranked top hit should be the *definition* rather than a passing mention.
Observed: the top result changed for 5 of 5 probe queries.

**But do not read that as reranking being better.** Measured over 46 questions
it is neutral at best for ranking, at double the latency — see
[08-evaluation.md](08-evaluation.md). It stays on for the refusal signal.

**Verify — the relevance score means something.** Ask something absent from your
corpus:

```bash
curl -s "http://localhost:8000/api/search?q=how+to+bake+sourdough+bread" | grep -o '"top_relevance":[^,]*'
```

Expect a **negative** number. Real matches score 5–7; off-corpus queries score
below −5. The refusal floor sits at −3.0, calibrated against the Phase 9
benchmark — see [08-evaluation.md](08-evaluation.md).

**Verify — filters.**

```bash
# Only what was on screen
curl -s "http://localhost:8000/api/search?q=rigidbody&kinds=ocr&limit=3"
# Only the second half of a video
curl -s "http://localhost:8000/api/search?q=respawn&start_s=18000&limit=3"
```

---

## 5 · Timeline and structure

| Feature | Where |
| --- | --- |
| Rule-derived events (scene, silence, text) | `app/pipeline/events.py` |
| Topic segmentation from embeddings | `build_topic_hierarchy()` |
| Two-level chapter list | video page sidebar |
| Filterable event timeline | `components/Timeline.tsx` |

**Verify — events and topics.**

```bash
curl -s "http://localhost:8000/api/videos/{id}/events" | grep -o '"by_type":{[^}]*}'
curl -s "http://localhost:8000/api/videos/{id}/topics" | head -c 400
```

On the video page the timeline bar shows coloured markers — click anywhere to
seek, toggle event types in the legend. The sidebar chapter list auto-expands to
wherever the playhead is.

**Verify — topic labels are sensible.** They come from class-based TF-IDF, so
they should be *distinctive*, not just frequent. On a Unity tutorial expect
"Collider, Falling, Shape" — never "Unity" repeated for every chapter.

**Regenerate without re-transcribing:**

```bash
curl -s -X POST "http://localhost:8000/api/videos/{id}/process?stages=events"
```

---

## 6 · Answering

| Feature | Where |
| --- | --- |
| Evidence-backed answers | `POST /api/ask`, `/ask` |
| Citations resolved from the database | `app/answer.py` |
| Fabricated citations rejected | `resolve_citations()` |
| Uncited sentences stripped | `strip_uncited()` |
| Refusal below the relevance floor | `answer_question()` |

**Verify — a normal question.**

```bash
curl -s -X POST http://localhost:8000/api/ask -H "content-type: application/json" \
  -d '{"question":"What is a prefab and why would I use one?"}' | head -c 500
```

Expect `refused: false`, citations with real `start_s` values, and
`fabricated_citations: []`.

**Verify — the guarantee, adversarially.** Ask directly for a time:

> At what point in the video are colliders explained?

The answer must contain **no timestamp in its prose**. Times appear only as
citation chips. This is structural: the prompt contains no timestamps to copy.

**Verify — refusal.**

```bash
curl -s -X POST http://localhost:8000/api/ask -H "content-type: application/json" \
  -d '{"question":"How do I bake sourdough bread?"}' | head -c 300
```

Expect `refused: true`, a reason naming the score, and **~0.5 s** — the model is
never called, so it costs no quota.

**Verify — invented citations cannot reach you.** Covered by
`tests/unit/test_answer.py`, 45 cases including marker `0`, `[c_-1]`, markers
outside the evidence set, citations orphaned by sentence stripping, and every
grouping style a model might use.

**If answers start refusing unexpectedly**, check `uncited_sentences` in the
response. A high strip rate with `citations: []` means the model is emitting a
citation format the parser does not recognise — see the note on grouped
citations in `app/answer.py`.

**Quota.** The free tier differs sharply between models: `gemini-2.5-flash` has
a usable daily allowance, while newer preview models can be as low as **20
requests per day**. A 429 is surfaced immediately with the limit and wait time,
and is deliberately never retried — retrying a quota error spends the remaining
allowance faster.

---

## 7 · Collections and cross-video

| Feature | Where |
| --- | --- |
| Collections CRUD | `/collections`, `/api/collections` |
| Exclusive membership | `PUT /api/collections/{id}/videos/{vid}` |
| Scoped search and ask | `?collection_id=` |
| Concept timelines | `/timeline`, `GET /api/search/timeline` |

**Verify — scoping.** Create a collection, add one video, then:

```bash
curl -s "http://localhost:8000/api/search?q=<something>&collection_id={cid}&limit=3"
```

Hits must come only from videos in that collection.

**Verify — the empty-collection trap.** Create a collection with *no* videos and
search it. Expect **zero hits**. A non-zero count means scoping has silently
widened to the whole corpus — the bug this was written to catch.

**Verify — deleting a collection keeps its videos.**

```bash
curl -s -X DELETE http://localhost:8000/api/collections/{cid} -o /dev/null -w "%{http_code}\n"
curl -s http://localhost:8000/api/videos/{vid} -o /dev/null -w "%{http_code}\n"   # still 200
```

**Verify — concept timeline.** <http://localhost:3000/timeline>, trace a concept.
Occurrences are chronological within each video; videos are ordered by how well
they cover it.

---

## 8 · Pipeline control

**Stage selection** — transcription is the expensive stage, so it is opt-out:

| preset | runs | use |
| --- | --- | --- |
| `all` (default) | everything | first pass |
| `visual` | keyframes, OCR, embed | add or refresh the visual layer |
| `speech` | audio, transcribe, embed | re-transcribe only |
| `index` | embed | re-chunk and re-embed what is stored |
| explicit | e.g. `keyframes,ocr` | anything else |

```bash
curl -s -X POST "http://localhost:8000/api/videos/{id}/process?stages=visual"
```

**Verify:** the transcript segment count must be identical before and after.
Measured at 8.9 min versus ~40 for a full re-run.

**Note:** re-running the visual stages deletes keyframes before rebuilding them,
so `ocr_blocks` is empty for the ~7 minutes OCR takes. Existing OCR *chunks*
remain searchable throughout; only the raw block records are transiently absent.

---

## Whole-system check

```bash
.venv/Scripts/python.exe -m pytest backend -q
```

302 tests, run against a throwaway Postgres database. Requires
`docker compose up -d`.

```bash
.venv/Scripts/python.exe -m ruff check backend/app backend/tests backend/scripts
.venv/Scripts/alembic.exe -c backend/alembic.ini check
npx --prefix frontend tsc --noEmit
NEXT_DIST_DIR=.next-build npx --prefix frontend next build
```

All four should be clean. The last one matters: `next build` catches prerender
errors that `next dev` does not.

---

## Not built

Honest gaps, so nothing here is mistaken for missing-but-broken:

- **Diarization** (speaker labels) — deferred; pulls in a heavy dependency chain
- **VLM captions** — deliberate; on a 360p screencast a captioner emits "a
  screenshot of a code editor" a thousand times
- **Streaming answers** — citation validation needs the whole text first
- **Authentication** — single user
- **S3 storage** — the interface exists, the backend raises
- **arq/Redis queue** — the in-process worker covers current needs; jobs do not
  survive a restart
- **The Phase 9 benchmark** — every number in
  [06-benchmarks.md](06-benchmarks.md) is a measurement, not a Recall@k against
  verified ground truth
