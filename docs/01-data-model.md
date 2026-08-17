# 01 — Data model

Full target schema. **Phase 1 migrates only the tables marked ✅**; the rest are
specified now so the shape is decided before code depends on it.

All times are **float seconds from container origin**. Never frame numbers, never
timecode strings. Formatting to `HH:MM:SS` happens in the UI and nowhere else.

---

## Core

### `videos` ✅

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid pk | |
| `collection_id` | uuid fk null | Phase 8 |
| `title` | text | Defaults to filename stem |
| `description` | text null | |
| `original_filename` | text | |
| `storage_key` | text | Opaque key into the object store |
| `mime_type` | text | |
| `size_bytes` | bigint | |
| `checksum_sha256` | text | Computed during upload streaming; dedupe key |
| `status` | text | `uploading` `uploaded` `processing` `ready` `failed` |
| `duration_s` | float null | Null until probed |
| `width` / `height` | int null | |
| `fps` | float null | |
| `video_codec` / `audio_codec` | text null | |
| `has_audio` | bool | Gates the whole speech branch |
| `audio_channels` | int null | |
| `audio_sample_rate` | int null | |
| `created_at` / `updated_at` | timestamptz | |

### `processing_jobs` ✅

One row per processing attempt. Re-running a video creates a new job; history is kept.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid pk | |
| `video_id` | uuid fk | |
| `status` | text | `queued` `running` `succeeded` `failed` `cancelled` |
| `created_at` / `started_at` / `finished_at` | timestamptz null | |
| `error` | text null | |

### `job_stages` ✅

Drives the per-stage progress UI (Speech ✓ / Frames ✓ / OCR ⋯ / Embeddings ⋯).

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid pk | |
| `job_id` | uuid fk | |
| `name` | text | `probe` `audio_extract` `transcribe` `diarize` `keyframes` `ocr` `caption` `events` `embed` |
| `status` | text | `waiting` `running` `succeeded` `failed` `skipped` |
| `progress` | float | 0.0–1.0 |
| `started_at` / `finished_at` | timestamptz null | |
| `error` | text null | |
| `metrics` | json null | Stage-specific: model used, wall time, item counts |

`skipped` is a real state — a silent video skips the entire speech branch and the UI
must say so rather than showing a stalled bar.

---

## Speech (Phase 2)

### `transcript_segments`

Raw ASR output, unmodified. The audit trail.

`id`, `video_id`, `start_s`, `end_s`, `text`, `speaker_id` (null), `confidence`,
`no_speech_prob`, `model` (e.g. `faster-whisper/large-v3`).

### `speakers`

`id`, `video_id`, `label` (`SPEAKER_00`), `display_name` (user-assigned, e.g.
"Professor"), `embedding` (vector(192), for cross-video identity later).

Diarization on real lecture audio lands around 85–90%. Speaker is therefore a
**ranking boost, never a hard filter** — a `WHERE speaker = X` clause would silently
discard correct results one time in eight.

---

## Chunking (Phase 3)

### `chunks`

The retrieval unit. **This table decides search quality more than any model choice.**

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid pk | |
| `video_id` | uuid fk | |
| `parent_id` | uuid fk null | Parent–child retrieval |
| `kind` | text | `transcript` `ocr` `caption` `event` |
| `level` | text | `child` (~15s, embedded) or `parent` (~60s, returned to LLM) |
| `start_s` / `end_s` | float | |
| `text` | text | |
| `token_count` | int | |
| `embedding` | vector(1024) | ivfflat/hnsw index |
| `tsv` | tsvector | Generated column, GIN index |
| `meta` | json | Speaker, slide number, confidence, source frame |

**Parent–child retrieval.** Whisper's native 5–10s segments are too small to embed
meaningfully — they lose the context that makes them findable. So: embed the small
child chunk for precision, then hand the LLM the enclosing ~60s parent for
comprehension. This one decision buys more retrieval quality than any reranker.

Child chunks are built by merging ASR segments to a target window with sentence-aware
boundaries and ~20% overlap, so a concept split across a boundary survives in one
piece somewhere.

---

## Vision (Phase 4)

### `keyframes`

One row per *visually stable segment*, not per sampled frame. See
[02 — Pipeline](02-pipeline.md) for why that ordering matters.

`id`, `video_id`, `start_s`, `end_s`, `storage_key` (extracted JPEG), `phash`,
`is_slide` (bool), `slide_index` (int null), `stability_score`.

### `ocr_blocks`

`id`, `keyframe_id`, `text`, `bbox` (json), `confidence`.

Attached to keyframes, not timestamps — a slide on screen for four minutes is OCR'd
**once**, not 7,200 times.

### `frame_captions`

`id`, `keyframe_id`, `text`, `model`.

VLM description of what is shown. This replaces object detection entirely (D4).

---

## Events (Phase 5)

### `events`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid pk | |
| `video_id` | uuid fk | |
| `type` | text | See taxonomy below |
| `start_s` / `end_s` | float | |
| `title` | text | Human-readable, shown on the timeline |
| `confidence` | float | |
| `source` | text | `rule` or `model` — never blur these |
| `evidence` | json | Array of `{kind, id, start_s}` refs |

Two classes of event, deliberately kept distinguishable by `source`:

**Rule-derived** (cheap, deterministic, high precision): `slide_change`,
`scene_change`, `speaker_change`, `silence`, `text_appeared`.

**Model-derived** (LLM over a time-aligned multimodal window): `topic_change`,
`concept_explained`, `worked_example`, `question_asked`.

Being explicit about this split matters. The flagship example — "the professor
explains backpropagation while displaying a network diagram" — is a summarisation
pass over aligned streams, not a rules engine. Weeks disappear into building the
rules engine that was always going to need a model.

### `topics`

The timeline hierarchy (`Introduction` → `Neural Networks` → `Backpropagation`).

`id`, `video_id`, `parent_id` (self-fk), `title`, `start_s`, `end_s`, `depth`.

---

## Cross-video (Phase 8)

### `collections`
`id`, `name`, `description`, `created_at`.

### `concepts` / `concept_mentions`
Deferred. Specified only if cross-video queries prove it necessary.

---

## Indexes that matter

```sql
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON chunks USING gin (tsv);
CREATE INDEX ON chunks (video_id, start_s);
CREATE INDEX ON events (video_id, start_s);
CREATE INDEX ON keyframes (video_id, start_s);
```

`(video_id, start_s)` is the workhorse: every "what else happened near here" query
and every timeline render depends on it.
