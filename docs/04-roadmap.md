# 04 — Roadmap

The original plan estimated 15 weeks. Realistically this is 30–40 weeks solo at a
sustainable pace. That is not an argument for cutting the vision — it is an argument
for making **Phase 3 excellent**, because "upload a lecture, search it semantically,
click to seek" is already a demoable product and carries most of the value.

Estimates below assume part-time solo work.

---

### Phase 0 — Design ✅ *done*
Architecture, data model, pipeline order, retrieval strategy, evaluation plan.
The documents in this directory.

### Phase 1 — Video platform ✅ *done*
Upload, storage abstraction, probe, metadata, library UI, player with range-request
seeking.

No auth. Single user for months yet; it adds surface area and delivers nothing.

### Phase 2 — Transcription ✅ *done*
Audio extraction (PyAV → 16 kHz mono), faster-whisper large-v3 on the 4070,
`transcript_segments`, in-process worker with live stage progress, transcript panel
with click-to-seek and follow-the-playhead, substring search across transcripts.

**Verified:** clicking a segment seeks to its exact start (0.00 / 3.50 / 9.96 s all
landed within 0.05 s), the active line tracks the playhead, and a silent video marks
the whole speech branch `skipped` rather than failing.

Two deviations from the original plan, both documented in
[00-architecture.md](00-architecture.md): the queue is in-process rather than arq
(no Redis), and processing is explicitly triggered rather than automatic on upload —
it is a multi-minute GPU job and the user should see the metadata first and decide.

**Validated on real content:** a 6-hour Unity tutorial produced 6,625 segments in
45.6 min (7.9× realtime), covering 5.79 h of the 5.99 h runtime, coherent throughout.
Numbers in [05-environment.md](05-environment.md).

That run exposed a UI problem synthetic clips never could: rendering 6,625 rows blocked
the main thread for **568 ms** on load and on every search-box clear. The transcript
list is now virtualised (`@tanstack/react-virtual`) — 240 DOM nodes instead of 26,643,
and list rebuilds dropped to ~26 ms. Follow-the-playhead also snaps instantly on large
jumps instead of smooth-scrolling through six hours of transcript.

### Phase 2.5 — Diarization — ~1 week
Deferred out of Phase 2 rather than dropped. `pyannote.audio` pulls in torch, which
is a far heavier dependency than anything currently installed, and 3.13 wheel
support is unverified. Speaker stays a soft ranking signal, never a hard filter.

### Phase 3 — Search ✅ *done*
Postgres 17 + pgvector 0.8.6 via Docker, SQLite bridge retired and data migrated.
Parent/child chunking, `bge-large-en-v1.5` embeddings on the GPU, hybrid retrieval
(pgvector cosine + Postgres FTS fused by RRF), HNSW index, search UI with
cross-video results, and `?t=` deep links from a result to the exact moment.

**Verified on the 6-hour Unity tutorial:** 6,625 transcript segments became 301
parents and 1,716 children, embedded in **14.9 s**. Warm query latency **~70–210 ms**.
Real queries land correctly — *"what is a prefab"* returns 5:47:13, *"how do I make
the player jump"* returns 2:44:57, and an ML query correctly surfaces the *other*
video, so cross-video retrieval works.

Tests now run against Postgres, not SQLite: from here the schema uses `vector` and
`tsvector`, and testing retrieval against a database that cannot retrieve would be
theatre. `docker compose up -d` is required to run them.

**Not yet done from the Phase 3 plan:** the cross-encoder reranker. RRF alone is
already good; reranking is worth adding only once the Phase 9 benchmark can show
whether it helps.

### Phase 4 — Visual intelligence ✅ *keyframes + OCR done*
Keyframe scanning via keyframe-only decode (~1,000× realtime), extraction, RapidOCR
on CPU, OCR text indexed as `kind=ocr` chunks attached to the transcript parent
covering them, and a keyframe filmstrip with click-to-seek.

Measured on the 6-hour tutorial: **1,100 keyframes in 43.9 s**, OCR in **7.5 min**,
608 frames with usable text, 590 indexed visual chunks. Full numbers and the
confidence-vs-usefulness finding in [06-benchmarks.md](06-benchmarks.md).

**VLM captions are deliberately not built.** On a 360p screencast a captioner would
emit "a screenshot of a code editor" a thousand times — noise rather than signal —
while a 2–3 B model competes for the 8 GB the embedder already occupies. Captions
earn their place on lecture and diagram content; revisit when such a video is in
the corpus rather than building it speculatively.

**Stage selection** closes the obvious gap: `POST /api/videos/{id}/process`
takes `stages` — a preset (`all`, `visual`, `speech`, `index`) or an explicit
list. Unselected stages are recorded as `skipped` at job creation, and each
stage reads its inputs from storage when a prior stage did not produce them in
this run.

Measured on the 6-hour video: `stages=visual` completes in **8.9 min** against
~40 for a full re-run, with the transcript verifiably untouched.

### Phase 5 — Event engine ✅ *done, without an LLM*
Rule-derived events (scene changes from keyframe change magnitude, silences from
transcript gaps, text appearing from OCR), a two-level topic hierarchy, and a
timeline UI with filterable markers plus a collapsible chapter list.

**Topic boundaries do not need an LLM.** The plan assumed one, but chunk
embeddings already encode meaning, so a subject change is a measurable dip in
similarity between consecutive spans — TextTiling with embeddings substituted
for lexical overlap. It is deterministic, costs nothing beyond vectors already
stored, and sends no data anywhere. Titles come from class-based TF-IDF.

**Measured on the 6-hour tutorial:** 583 events and 148 topics in **0.88 s** —
369 scene changes, 170 text appearances, 44 topic changes, 45 coarse topics over
103 fine ones. Labels are accurate on inspection: `Collider, Falling, Shape`,
`Velocity, Rigid, Body`, `Raycast, Ground, Distance`, `Respawn, Prefab, Enemy`.

**What an LLM would still add:** richer event *descriptions* — the design doc's
"the professor explains backpropagation while showing a diagram" — rather than
better boundaries. `GEMINI_API_KEY` is present in the environment but unused:
sending transcripts to an external service on the user's key is their call to
make, not a default.

`speaker_change` events are specified but inert until diarization (Phase 2.5).

### Phase 6 — Multimodal retrieval ✅ *done*
The temporal retriever, metadata filters (kind, time range, video) and
cross-encoder reranking, with evidence assembled across modalities.

**The temporal retriever is what makes this multimodal** rather than a
transcript search with extra tables. Every hit now carries what else the
pipeline recorded at that moment: the frame on screen and its OCR text, the
topic it sits in, and the events around it. Fetched in three queries per video
rather than per hit — a per-hit round trip is what turns a 200 ms search into a
2 s one.

**~~Reranking earns its place.~~ Superseded by Phase 9.** This claim was made
from 5 probe queries judged by eye: the top result changed every time and the
promotions looked better. A 46-question benchmark disagrees — the cross-encoder
is *neutral at best* for ranking (Recall@5 0.870 against 0.935 without it) at
double the latency. It stays switched on for the refusal signal below, not for
reordering. See [08-evaluation.md](08-evaluation.md).

**The cross-encoder score doubles as a "not found" signal.** It is calibrated
enough to be meaningful: relevant hits score 5–7, while "how to bake sourdough
bread" against a Unity corpus tops out at **-6.5**. That gives the evidence
contract in [03](03-retrieval.md) something real to fire on, instead of a
similarity threshold that always returns *something*. The UI warns when the best
score falls below zero.

**Latency:** ~350–470 ms p50 with reranking, against a 3 s budget. Both models
are preloaded at startup — cold, the first reranked query cost 2.3 s.

**Not done:** speaker as a ranking signal, which needs diarization (Phase 2.5).

### Phase 7 — LLM reasoning ✅ *done*
Evidence prompting with opaque per-request markers, citation resolution and
rejection, the refusal path, and jump-to-timestamp from every claim.
Gemini 2.5 Flash, chosen by the user; the provider is a thin swappable interface
and every guarantee lives outside it in `app/answer.py`.

**Done when: you cannot make it cite a timestamp that does not exist.** The
adversarial probe was asking directly for one — *"At what point in the video are
colliders explained?"* — and the model wrote no timestamp, letting the citations
carry the answer. It cannot do otherwise: the prompt contains no timestamps to
copy, and a marker outside the retrieved set is deleted before display.

**The refusal path works and is cheap.** "How do I bake sourdough bread?" against
a Unity corpus refuses in **520 ms without calling the model at all** — the
cross-encoder scored the best candidate at -5.15, below the floor, so no prompt
is worth sending.

Measured: ~2–2.6 s per answered question, 0 fabricated citations across the
probe set, uncited sentences stripped (1/2 and 1/3 on two answers).

**Not done:** streaming. Answers are returned whole, because citation validation
needs the full text before anything can be shown. Time-to-first-token would need
the raw stream sent first and citations resolved in a trailing event — worth
doing, not yet done.

### Phase 8 — Cross-video ✅ *built, under-demonstrated*
Collections with exclusive membership, collection-scoped search and ask, and
concept timelines that group retrieval by video and order it by time.

**Honest caveat:** the corpus is one real 6-hour video plus a 14-second test
clip. Every cross-video path is built and tested — scoping was verified to
include the right video and exclude the other — but "compare how these three
lectures treat backpropagation" cannot be *shown* on a corpus of one. Judge this
phase again after a second real video is indexed.

**Concept timelines are deliberately not an LLM.** Retrieval already finds the
moments; grouping by video and ordering by time is what turns a ranked list into
a chronology, and the ordering is a fact about the data. Generating a narrative
over it would be exactly the unfalsifiable summary this project avoids. Measured:
`"colliders"` returns 8 mentions from 25:26 to 1:40:13 in **315 ms**.

**A dangerous bug this phase surfaced**: scoping to an *empty* collection
silently searched the entire corpus, because `if video_ids:` treats `[]` the
same as "no filter". Plausible-looking results answering a different question
than the one asked. `None` (no scope) and `[]` (empty scope) are now explicitly
distinguished, with a test named for the trap.

### Phase 9 — Evaluation ✅ *done, on a corpus of one*
46 verified questions, five retrieval variants, a fusion sweep, and per-query
results committed to `backend/benchmarks/`. Full write-up in
[08-evaluation.md](08-evaluation.md).

**The ablation found a bug that had been shipping since Phase 3.** Lexical
retrieval scored Recall@5 **0.239** — not weak, broken. `websearch_to_tsquery`
ANDs every term, so a natural question demanded all its content words inside one
17 s chunk: **30 of 46 questions retrieved zero lexical candidates**, and the
few incidental matches that survived were promoted by fusion, dragging hybrid
*below* semantic alone. An OR fallback took lexical Recall@5 to **0.630**.

Five probe queries would never have caught it. The system returned good answers
throughout, from one retriever while the other silently contributed nothing.

**Equal-weight RRF was also wrong**, once lexical became a recall net: MRR 0.708
against 0.780 for semantic on its own. Down-weighting to 0.25 gives the best
configuration measured — R@1 0.696, R@5 0.935, MRR 0.793. The exact value is
noise; "much less than semantic" is the finding.

**The honest headline is smaller than the pitch.** Hybrid beats semantic alone
by 2.2 points of Recall@5 — one question out of 46. What the category split
shows is that neither retriever is *safe* alone: lexical manages 0.750 on
identifier queries and 0.400 on paraphrased ones, semantic the mirror image.

**Ranking children and returning parents is vindicated**: near-identical recall,
but 0.0 s median error to the exact spoken moment against 21.9 s for
parent-level ranking, and 17 s spans against 64 s.

**The refusal floor was in the wrong place.** Refusing off-corpus questions was
always tested; *not* refusing answerable ones never was. The floor of 0.0 sat
inside the answerable score distribution and declined 2 of 46 questions the
system had already retrieved correctly — including one where it found the exact
frame-rate explanation and then said it could not. Answerable scores bottom out
at -1.75, off-corpus scores top out at -5.01; the floor now sits at -3.0 in the
gap, and false refusals are 0/46 with negatives still 6/6.

**Not measured:** citation validity and uncited rate — the daily free-tier LLM
quota (20 requests) ran out. Event P/R/F1 needs a video with official chapters,
and the corpus has none.

---

## Not doing

Training any model · mobile app · Kubernetes · Kafka · knowledge graph · agent swarm ·
autonomy for its own sake · supporting every container format under the sun.

## Risk register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| ~~Python 3.13 wheels for ctranslate2~~ | — | **Cleared** — cp313 wheels exist |
| Python 3.13 wheels for pyannote/torch | Blocks Phase 2.5 | Verify before starting; 3.12 venv is a drop-in swap |
| **Docker not installed** | **Blocks Phase 3** | Install before starting — SQLite has no vector search |
| 8 GB VRAM ceiling | Limits VLM size | ≤3B captioners; never two GPU stages at once |
| No job durability across restarts | Interrupted jobs lost | Reaped and marked failed at boot; fixed by arq + Redis |
| ~~Untested on full-length video~~ | — | **Cleared** — 6 h video verified end to end |
| Transcript payload grows with corpus | 1.5 MB per 6 h video, unbounded | Paginate `/transcript` before multi-video views (Phase 8) |
| Diarization accuracy | Degrades speaker queries | Soft filter, never hard `WHERE` |
| Scope creep into the knowledge graph | Months lost | Explicitly deferred in [00](00-architecture.md) |
| **Benchmark is one video, 46 questions** | Every retrieval number is a claim about one 360p Unity screencast | Index a lecture and a multi-speaker talk, then re-run `scripts/run_benchmark.py` |
| LLM free tier is 20 requests/day | Blocks answer-quality metrics | Measured what needs no quota; enable billing or switch models to finish |
