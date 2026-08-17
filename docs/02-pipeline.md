# 02 — Processing pipeline

## Stage order

```
             upload
                │
                ▼
         ┌─────────────┐
         │ 1. probe    │  container metadata, has_audio
         └──────┬──────┘
                │
        ┌───────┴────────┐
        ▼                ▼
   AUDIO BRANCH     VISUAL BRANCH
        │                │
  2. audio_extract  5. stability scan   ◄── the expensive part, done cheaply
        │                │
  3. transcribe     6. keyframe extract
        │                │
  4. diarize        7. ocr + caption    ◄── runs on ~300 frames, not 900,000
        │                │
        └───────┬────────┘
                ▼
          8. chunk + fuse
                │
                ▼
          9. events
                │
                ▼
         10. embed
                │
                ▼
              ready
```

The two branches are independent and run in parallel. A silent video skips 2–4 and
marks them `skipped`.

## Why stability detection comes before OCR

The original plan ran OCR (stage 6) before slide detection (stage 7). That ordering
is backwards and it is the difference between a pipeline that finishes and one that
does not.

A slide sitting on screen for four minutes at 30fps is **7,200 near-identical
frames**. OCR-then-dedupe pays for 7,200 inferences to keep one result.
Detect-then-OCR pays for one.

Stability detection is not a downstream feature. It is the sampling strategy that
makes every subsequent visual stage affordable.

### How it works

1. Decode at a low fixed rate (2 fps) at reduced resolution. Cheap — no model.
2. Compute a perceptual hash per sampled frame.
3. Hamming distance between consecutive hashes gives a change signal.
4. A run of frames below the change threshold is a **stable segment**.
5. Emit one full-resolution keyframe per stable segment, taken from the *middle* of
   the run — the start is often mid-transition or mid-animation.

Tunables: `stability_threshold` (hash distance), `min_segment_s` (default 2.0 —
shorter runs are transitions, not content).

For a 100-minute lecture this typically yields 200–400 keyframes from ~180,000
sampled frames, and only those reach OCR and the VLM.

### Adaptive density

Where the change signal is *dense* — rapid slide flipping, live coding, screen
scrolling — drop `min_segment_s` locally. Where nothing changes for ten minutes,
one keyframe is the correct answer. This is a refinement, not Phase 4 scope.

## Stage detail

### 1. probe
PyAV reads container metadata directly through libav — no ffmpeg binary needed, which
matters because the dev machine has no ffmpeg on PATH. Populates duration, dimensions,
fps, codecs, `has_audio`.

Fails loudly. A video we cannot probe is a video we cannot process, and it is far
better to know at upload than three stages in.

### 2. audio_extract
16 kHz mono WAV — Whisper's native input rate. Resampling once here avoids Whisper
doing it internally per chunk.

### 3. transcribe
`faster-whisper` `large-v3`, float16 on the 4070. Roughly 4.7 GB VRAM, ~10–20 min for
a two-hour video. Store raw segments verbatim into `transcript_segments` before any
chunking — that table is the audit trail and must stay unmodified.

VAD filtering on, to avoid Whisper's well-known habit of hallucinating text during
silence.

### 4. diarize
`pyannote/speaker-diarization-3.1`. Requires a HuggingFace token and accepting the
model's gated licence — a manual step, document it. Assigns `speaker_id` to segments
by temporal overlap.

Expect 85–90% on clean two-speaker audio and worse in a lecture hall with crosstalk.
Hence the soft-filter rule in [01](01-data-model.md).

### 5–6. stability scan and keyframe extract
Above.

### 7. ocr + caption
- **OCR**: PaddleOCR on each keyframe. Better than Tesseract on slide layouts and
  handles rotated/low-contrast text considerably better.
- **Caption**: a small VLM on each keyframe. 8 GB VRAM caps this around 2–3B params —
  Qwen2-VL-2B or Florence-2-large both fit. Run after Whisper has released the GPU,
  not concurrently.

### 8. chunk + fuse
Merge ASR segments into child/parent chunks (see [01](01-data-model.md)). Attach
overlapping OCR and captions by time range. This is the point where the modalities
first share a record.

### 9. events
Rule-derived events fall directly out of the stability scan and diarization.
Model-derived events come from an LLM pass over aligned windows: transcript + OCR +
caption for a ~60s span, asked for topic boundaries and salient moments.

### 10. embed
`bge-large-en-v1.5` (1024-dim) over child chunks. Batched on GPU. Text only — a joint
image embedding space (CLIP) is a Phase 6 question, and captions already put visual
content into the text index.

## Failure policy

Stages fail independently. A failed OCR stage must not discard a perfectly good
transcript. Each stage writes its own status; the job succeeds if the **critical
path** (probe → transcribe → chunk → embed) succeeds, and reports partial success
otherwise.

Videos are immutable once uploaded, so every stage is safely retryable. Re-running
creates a new `processing_jobs` row rather than mutating the old one.

## Resource contention

One GPU, 8 GB. Whisper large-v3 and a VLM will not coexist comfortably. Stages
declare a `gpu` requirement and the worker runs at most one GPU stage at a time.
CPU-bound stages (probe, stability scan, OCR on CPU) run freely alongside.

This is why the queue is a real queue and not `asyncio.gather`.
