# 03 — Retrieval and evidence

## Four retrievers, one ranking

```
                        query
                          │
        ┌────────┬────────┼────────┬─────────┐
        ▼        ▼        ▼        ▼         │
     lexical  semantic  metadata temporal    │
      (FTS)   (vector)  (filter) (range)     │
        │        │        │        │         │
        └────────┴───┬────┴────────┘         │
                     ▼                       │
          reciprocal rank fusion             │
                     ▼                       │
              cross-encoder rerank           │
                     ▼                       │
              parent expansion  ◄────────────┘
                     ▼
                 evidence set
```

**Lexical** — Postgres FTS over `chunks.tsv`. Non-negotiable: acronyms, symbols,
proper nouns and API names are exactly where embeddings fail. A query for `∂L/∂w` or
`ResNet-50` needs exact matching.

**Semantic** — pgvector cosine over child embeddings. Handles paraphrase, which is
most natural-language questions.

**Metadata** — speaker, video, collection, event type, `is_slide`. Applied as ranking
boosts where the signal is unreliable (speaker), hard filters only where the user
asked explicitly ("in lecture 3").

**Temporal** — given a hit, pull everything overlapping its span from other
modalities. This is what turns "the transcript says backpropagation" into "the
transcript says backpropagation *while a network diagram is on screen*".

### Fusion

Reciprocal rank fusion, `k = 60`:

```
score(d) = Σ  1 / (k + rank_r(d))
          r∈R
```

Chosen over weighted score blending because the four retrievers produce scores on
incomparable scales, and tuning those weights is a rabbit hole with no ground truth
to tune against until Phase 9.

### Expansion

Fusion ranks *child* chunks. What goes to the LLM is their *parents*, deduplicated
and merged where they overlap. Precision from the child, comprehension from the
parent.

---

## Citation integrity

**The rule: the model never writes a timestamp.**

Evidence reaches the LLM tagged with opaque IDs:

```
[c_1842] (transcript, 00:24:17–00:25:03, Speaker 1)
Backpropagation lets us compute the gradient of the loss with respect to every
weight by applying the chain rule backwards through the network.

[c_1847] (slide text, 00:24:11–00:28:40)
BACKPROPAGATION — ∂L/∂w = ∂L/∂y · ∂y/∂w
```

The model must cite as `[c_1842]`. Post-processing then:

1. Parses every `[c_NNNN]` from the answer.
2. **Rejects any ID not in the evidence set actually retrieved for this query.**
3. Resolves surviving IDs to `(video_id, start_s)` from the database.
4. Renders them as clickable timestamps.

A fabricated citation cannot survive step 2, and a real citation cannot carry a wrong
timestamp because the model never supplied one — the database did.

This does not stop the model from misdescribing what a chunk says. It does eliminate
the entire class of confidently-wrong timestamps, which is the failure users actually
notice and the one that destroys trust fastest.

### Answer contract

- Every factual claim carries at least one citation.
- Zero retrieved evidence above threshold → "I could not find this in the video."
  No answer from parametric knowledge, ever.
- Uncited sentences are stripped before display, and the strip rate is logged as a
  quality metric.

---

## Latency

3s budget, streamed:

| Stage | Target |
| --- | --- |
| Embed query | 100 ms (local GPU, warm) |
| Four retrievers (parallel) | 200 ms |
| Rerank top 50 | 400 ms |
| Parent expansion | 50 ms |
| LLM first token | ~800 ms |
| **Perceived** | **~1.5 s** |

The full answer takes longer; time-to-first-token is what the user experiences.
Stream it.

Cache: query embeddings (queries repeat), reranker scores per `(query, chunk)` pair,
and full answers keyed by `(query_hash, collection_version)`.

---

## Evaluation (Phase 9)

Hand-labelling 500 questions across 100 videos is months of unglamorous work that
will not get finished. Bootstrap instead:

1. Source videos that ship **official chapter markers or published transcripts** —
   conference talks and university courses often do. Chapters give free ground-truth
   topic boundaries.
2. LLM-generate candidate questions from transcripts, each with a source span.
3. **Hand-verify a subset.** 40 videos and ~150 verified questions is defensible,
   honest and achievable.

Metrics:

| Metric | Definition |
| --- | --- |
| Recall@k | Correct span in top *k* retrieved, k ∈ {1,5,10} |
| MRR | Mean reciprocal rank of first correct span |
| Timestamp error | Median \|predicted − true\| start, in seconds |
| Citation validity | % citations resolving to a chunk that supports the claim |
| Uncited rate | % of answer sentences stripped for lacking citation |
| Event P/R/F1 | Against chapter markers where available |
| p50 / p95 latency | End-to-end query |

Report real numbers or none. Illustrative benchmark tables in a README are a
liability the moment somebody asks how they were produced.
