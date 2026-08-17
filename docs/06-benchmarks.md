# 06 — Benchmarks

Measured results, not estimates. Every number here comes from a real run on the
dev machine (RTX 4070 Laptop, 8 GB, large-v3, float16).

---

## Answering (Phase 7)

Gemini 2.5 Flash over the 6-hour Unity corpus.

| | |
| --- | --- |
| Answered question | **2.0–2.6 s** |
| Refused question | **0.5 s** (no model call) |
| Fabricated citations | **0** across the probe set |
| Model wrote a timestamp | **never**, including when asked for one |

### The adversarial case

The failure this phase exists to prevent is a confident citation pointing at the
wrong second. The direct probe for it is to *ask* for a time:

> **Q:** At what point in the video are colliders explained?
>
> **A:** Colliders are explained during the demonstration of how Unity uses
> collider components to define an object's physical shape rather than relying
> solely on visuals [c_6] [c_11] …

No timestamp in the prose. The markers resolved to 0:26:12, 0:26:25 and 0:26:39
— from the database, not the model. This is structural rather than a matter of
prompt compliance: the evidence block contains no timestamps to copy, so there
is nothing to get wrong.

### Refusing is cheaper than answering

The relevance floor is checked before the prompt is built, so an unanswerable
question costs one embedding and one retrieval:

| question | top score | outcome | cost |
| --- | --- | --- | --- |
| What is a prefab? | 6.7 | answered | 2.5 s |
| How do I bake sourdough bread? | **-5.15** | refused | **0.52 s**, no API call |

---

## Reranking (Phase 6)

`cross-encoder/ms-marco-MiniLM-L-6-v2` over the fused candidate pool.

| | |
| --- | --- |
| Latency, 30 candidates warm | **13–24 ms** |
| Latency, cold | 2.3 s (hence preloaded at startup) |
| VRAM | **95 MB** |
| End-to-end search, p50 | ~350 ms |

### It changes the answer, not just the order

Top result changed for **5 of 5** probe queries, promoting from as deep as
rank 28:

| query | RRF #1 | reranked #1 |
| --- | --- | --- |
| what is a prefab used for | "what we need to do now is to use this prefab" | **"It's like a blueprint that you can use to make new enemies"** |
| detecting collisions | "like collision detection, you know, all of the stuff we learned" | **"Collision detection"** |

### The score means something

Unlike cosine similarity, the cross-encoder's output is calibrated enough to act
on. Observed on this corpus:

| | score |
| --- | --- |
| Strong match | 5 to 7 |
| Weak but plausible | 0 to 2 |
| Nothing relevant ("how to bake sourdough bread") | **-6.5** |

That is the difference between a system that always returns its least-bad match
and one that can say it did not find anything — which the Phase 7 evidence
contract requires.

---

## Events and topics (Phase 5)

6-hour Unity tutorial, 1,794 embedded transcript chunks.

| | |
| --- | --- |
| Wall time | **0.88 s** |
| Events | 583 (369 scene, 170 text, 44 topic) |
| Topics | 45 coarse / 103 fine |
| Mean coarse topic | ~8 min |
| Mean fine topic | 3.6 min |

### Segmentation without a model

Boundaries come from coherence dips between consecutive spans of chunk
embeddings — the depth of the valley, not its absolute depth, because a quiet
passage is uniformly less similar without changing subject. Sensitivity is
`mean + k·stdev` of the depth score, run twice at different `k` to give the two
levels of hierarchy.

Tuning on the real corpus:

| `depth_k` / `min_topic` | topics | mean | shortest | longest |
| --- | --- | --- | --- | --- |
| 0.6 / 90 s | 132 | 2.8 min | 0.5 min | 6.6 min |
| **1.0 / 120 s** | **103** | **3.6 min** | 0.5 min | 10.0 min |
| 1.5 / 180 s | 66 | 5.5 min | 3.1 min | 14.1 min |

### The IDF form matters more than expected

Labels initially returned "unity" for nearly every topic of a Unity tutorial.
The cause was the weighting: `log(1 + N/df)` still scores a term appearing in
*every* span at `log(2) ≈ 0.69`, which is easily enough to win.

`log((N+1)/(df+1))` goes to exactly zero when `df == N`, which is the intent.
After the change the same spans label as `Collider, Falling, Shape` and
`Platform, Rotating, Mass`. A one-line formula choice was the difference between
useful chapter titles and the video's own name repeated 45 times.

---

## Visual pipeline (Phase 4)

Same 6-hour Unity tutorial. **Source is 640x360**, which turns out to dominate
every OCR result below.

| stage | result |
| --- | --- |
| Keyframe scan | 4,183 scanned -> **1,100 selected**, 43.9 s |
| Scan throughput | ~1,000x realtime (keyframe-only decode) |
| OCR | 1,100 frames, **7.5 min**, 8 threads |
| Frames with usable text | **608 / 1,100** (55%) |
| OCR blocks | 7,715, mean confidence 0.758 |
| Indexed visual chunks | 590 |

### Confidence does not predict usefulness

The instinct is to filter OCR by confidence. On this data that is wrong. The
*highest*-confidence blocks (0.94) include:

```
Eueryonehastherighttofreedomofthought,conscienceandreligion,
Designingwithvariablefontsin
```

That is real text — a font specimen — read correctly at the glyph level with the
word boundaries lost, because at 360p the recogniser resolves letters but not
inter-word gaps. No tokenizer recovers words from it, so it is useless to an
embedding model despite scoring 0.94.

Length cannot decide either: `StopCoroutine(damageFeedbackCoroutine)` is just as
space-free and worth indexing. **Structure separates them** — identifiers carry
punctuation, run-together prose does not. That heuristic (`ocr.is_indexable`)
gates indexing while every block is still stored as the audit trail.

### Upscaling makes OCR worse

| scale | mean confidence | blocks above 0.80 |
| --- | --- | --- |
| 1x (640x360) | **0.70** | **5** |
| 2x | 0.64 | 0 |
| 3x | 0.63 | 0 |

Interpolation invents edges the recogniser then misreads. Frames are OCR'd at
native resolution.

### Does OCR earn its place?

Honestly: **partially, on this source.** Across four probe queries, a visual
chunk ranked first exactly once — "subscribe to the channel" surfaced an overlay
graphic at 0:00:47 that the transcript can never contain, which is a genuine win.
The other three were correctly won by speech.

Garbled *code* still passes the structure filter (it legitimately has colons and
semicolons) and occasionally surfaces as noise. At 1080p — a slide deck or a
conference talk — this stage should be clearly positive. At 360p it is marginal.

The general rule: **OCR value is bounded by source resolution, not by the engine.**

---

## Retrieval (Phase 3)

Corpus: the 6-hour Unity tutorial plus one short test clip. 1,717 embedded child
chunks, 302 parents.

| | |
| --- | --- |
| Chunking + embedding, 6,625 segments | **14.9 s** (1,716 children, GPU) |
| Query embedding, warm | ~15 ms |
| Full hybrid query, warm | **~70–210 ms** |
| Query embedding, cold | ~7 s (model load) |
| Index | HNSW, `m=16`, `ef_construction=64`, cosine |

The embedding model is held resident (`embedding_keep_warm`, ~1.3 GB) because a
cold load costs ~7 s — the entire latency budget spent before the search begins.

### GPU contention dominates everything else

Measured while an unrelated application was saturating the GPU (99–100% util,
5.8 GB): identical query embeddings ranged from **182 ms to 65 seconds**, a 350×
spread on the same work. Once the scheduler settled, latency returned to ~200 ms.

Worth knowing before optimising anything: if search feels slow, check
`nvidia-smi` before touching the code. A CPU device for *query* embedding
(`ECHOLENS_EMBEDDING_DEVICE=cpu`) would be immune to this — a single short query
is cheap on CPU — while document embedding still wants the GPU. Untested.

### Lexical retrieval contributes less on ASR text than expected

The case for hybrid search usually rests on exact identifiers, where embeddings
fail. On transcripts that argument weakens: Whisper writes what it hears, so the
corpus contains *"rigid body 2D"*, not `Rigidbody2D`. A query for the written form
returned only **1** lexical candidate against 50 semantic ones.

Where the spoken and written forms coincide it works well — *"colliders"* returned
49 lexical candidates. So lexical retrieval stays, but it earns its place through
ordinary vocabulary matching rather than the exact-identifier case that motivates
it for written documents. Worth quantifying properly in the Phase 9 ablation.

---

## Batched vs sequential inference

**Question.** `faster-whisper` ships `BatchedInferencePipeline`. Is it worth adopting,
given that timestamps — not text — are this product's deliverable?

**Corpus.** A 6-hour Unity tutorial: 5.99 h, 640×360, AAC. The existing sequential
transcript (6,625 segments) is the reference.

### Headline

| | sequential | batched + re-segmented |
| --- | --- | --- |
| Wall time | 45.6 min | **9.6 min** |
| Realtime factor | 7.9× | **37.4×** |
| Segments | 6,625 | 6,252 |
| Mean segment | 3.15 s | 2.85 s |
| Peak VRAM | 7,258 MB | **5,897 MB** |
| Word retention | — | 98.1% |
| Median timestamp agreement | — | **0.28 s** |

**4.75× faster, comparable granularity, lower memory.** Recommended.

### Batch size

Probed on a 12-minute slice taken from mid-video.

| config | wall | realtime | speedup | segments | peak VRAM |
| --- | --- | --- | --- | --- | --- |
| sequential | 122.5 s | 5.9× | 1.00× | 110 | 7,258 MB |
| batch 4 | 18.0 s | 40.0× | 6.81× | 24 | 6,977 MB |
| batch 8 | 16.0 s | 45.0× | **7.66×** | 24 | 7,776 MB |
| batch 16 | 67.7 s | 10.6× | 1.81× | 24 | 7,930 MB |

Batch 16 **regresses below batch 4** — it reaches 7,930 MB of 8,188 MB and thrashes.
On an 8 GB card, more batch is not more speed.

Batch 8 is fastest but leaves only ~600 MB headroom, and the desktop already holds
~1.8 GB before the model loads. **Batch 4 is the production setting**: 5,897 MB peak
on the full run, ~2.3 GB of headroom, and still 4.75× end to end.

### The catch, and the fix

Batched inference emits **~30 s segments** where sequential gives ~3 s:

| | sequential | batched (raw) |
| --- | --- | --- |
| Segments | 6,625 | 717 |
| Mean segment | 3.15 s | 29.85 s |
| Words per segment | 8.4 | 76.4 |

A 30-second segment is a 30-second citation. That is disqualifying for click-to-seek
and for the evidence contract in [03 — Retrieval](03-retrieval.md).

The fix is cheap: `word_timestamps=True` costs **~3%** (15.1 s → 15.6 s on the slice),
and real per-word times allow re-segmenting afterwards at pause and sentence
boundaries. Rebuilt segments average 2.85 s — slightly finer than sequential.

### Fidelity against the reference

Word streams aligned with `difflib`, block by block.

| metric | raw batched | re-segmented |
| --- | --- | --- |
| Words aligned | 95.3% | 95.3% |
| Content agreement (30 s windows) | 0.920 | **0.946** |
| Median timestamp delta | 0.88 s | **0.28 s** |
| p90 timestamp delta | 2.88 s | **0.95 s** |
| Within 1 s | 54.9% | **90.8%** |
| Within 2 s | 80.1% | **97.4%** |

Re-segmenting from real word stamps roughly triples timestamp precision against
interpolating inside coarse segments — as expected, since interpolation assumes an
even speaking rate that nobody has.

**Read this as agreement, not accuracy.** The sequential transcript is a reference,
not ground truth; both runs could be wrong together. Establishing absolute timestamp
accuracy needs the human-verified benchmark set in [03](03-retrieval.md), Phase 9.

### Methodology note

The first comparison bucketed each segment's words into the window of its *start*
time. Against 30 s batched segments that is biased by construction — it reports
segment length as if it were drift, and produced a misleading 0.542 agreement score.

The corrected method interpolates each word's position across its segment before
bucketing, which is also what the application would have to do to cite a moment
inside a coarse segment. Agreement rose to 0.920 for the same data. Recorded here
because the flawed version looked plausible.

### Proposed configuration

```python
BatchedInferencePipeline(model)          # large-v3, float16
    batch_size=4                         # 8 GB ceiling; 16 thrashes
    word_timestamps=True                 # ~3% cost, enables re-segmentation
    vad_filter=True
    beam_size=5
```

Then re-segment: break on a pause ≥ 0.6 s, sentence-final punctuation, 14 words, or
8 s — whichever comes first.

Not yet wired into `app/pipeline/transcribe.py`. Adopting it means storing word-level
stamps, which is also what Phase 3 chunking wants, so the two changes should land
together.
