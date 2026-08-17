# 08 — Evaluation

Phase 9. Everything below was produced by
[`scripts/run_benchmark.py`](../backend/scripts/run_benchmark.py) against the
indexed corpus, and the per-query record is in `backend/benchmarks/results.json`
so any number here can be traced to the query that produced it.

Reproduce:

```bash
python backend/scripts/run_benchmark.py --sweep
```

---

## 1 · What the benchmark is, and what it is not

**46 verified questions over one 6-hour Unity tutorial.** That is small, and it
is one video. [03-retrieval.md](03-retrieval.md) budgeted "40 videos and ~150
verified questions"; the corpus has one real video, so this is a fifth of the
plan on a twentieth of the content. Every conclusion below should be read as
*measured on this corpus*, and differences smaller than about one question
(0.022) are noise.

| | count |
| --- | --- |
| Generated, reviewed, kept | 33 |
| Hand-written `lexical` | 8 |
| Hand-written `conceptual` | 5 |
| Negatives (no answer in corpus) | 6 |
| Generated, awaiting review — **excluded** | 11 |

**How questions were made.** LLM-proposed from sampled passages, then read
one by one against the full gold span. Four were dropped and three reworded;
the reasons are recorded in
[`scripts/curate_benchmark.py`](../backend/scripts/curate_benchmark.py). Nothing
is scored until a human has read it — the `verified` flag exists because a
benchmark that scores machine-proposed labels measures the proposer.

**The bias this leaves.** A question written *from* a passage shares that
passage's framing, which flatters semantic retrieval. The prompt forbids reusing
distinctive wording and the two hand-written categories are authored
independently, but the bias is not eliminated. Read absolute numbers as
optimistic. The *differences between variants* are the point, and they share the
bias equally.

**Ground truth is a time span, not a chunk id**, because chunk boundaries are
what the ablations change. A result is correct when it overlaps a region a human
would accept. Questions carry several gold spans where the corpus explains
something more than once — marking only the sampled occurrence correct would
score a retriever that found a genuine explanation as having failed.

---

## 2 · Retrieval ablations

46 questions, top 10 retrieved. `err` is median distance from the top result to
the exact moment the answer is given; `span` is mean returned width. Latency is
the retrieval query only — query embedding (~30 ms, GPU, warm) is measured once
per question and shared across variants.

| variant | R@1 | R@5 | R@10 | MRR | nDCG@10 | err(s) | span(s) | p50 | p95 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lexical | 0.283 | 0.630 | 0.783 | 0.426 | 0.513 | 0.0 | 19 | 8 | 17 |
| semantic | 0.674 | 0.913 | 0.935 | 0.780 | 0.744 | 0.0 | 17 | 52 | 55 |
| hybrid-equal | 0.587 | 0.891 | 0.913 | 0.708 | 0.711 | 0.0 | 18 | 57 | 63 |
| **hybrid** | **0.696** | **0.935** | 0.957 | **0.793** | 0.769 | 0.0 | 17 | 57 | 66 |
| hybrid+rerank | 0.674 | 0.870 | **0.978** | 0.765 | **0.770** | 0.0 | 17 | 81 | 135 |
| parent-direct | 0.435 | 0.913 | 0.935 | 0.609 | 0.682 | 21.9 | 64 | 4 | 6 |

Recall@5 split by how the question was written:

| variant | conceptual (n=5) | generated (n=33) | lexical (n=8) |
| --- | --- | --- | --- |
| lexical | 0.400 | 0.636 | 0.750 |
| semantic | 0.800 | 0.939 | 0.875 |
| hybrid-equal | 0.600 | 0.939 | 0.875 |
| hybrid | 0.800 | 0.970 | 0.875 |
| hybrid+rerank | 0.600 | 0.909 | 0.875 |
| parent-direct | 0.600 | 0.939 | 1.000 |

---

## 3 · What the benchmark found

### It found a bug that had been shipping since Phase 3

The first run scored the lexical retriever at Recall@5 **0.239**. That is not a
weak retriever, that is a broken one, and the cause was in the SQL:
`websearch_to_tsquery` ANDs every term. *"How do I access a Rigidbody in a Unity
script?"* compiles to `'access' & 'rigidbodi' & 'uniti' & 'script'`, demanding
all four stems inside one ~17 s chunk.

**30 of 46 questions retrieved zero lexical candidates.** Worse, the handful
that squeaked through were mostly incidental matches, and fusion promoted them —
which is why hybrid search scored *below* semantic search alone.

The fix runs the strict query first, keeps its ranks, then tops up from the same
query with `&` rewritten to `|`. Phrases compile to `<->` and are untouched, so
quoted search still works.

| | before | after |
| --- | --- | --- |
| Questions with zero lexical results | 30 / 46 | **0 / 46** |
| Lexical Recall@5 | 0.239 | **0.630** |
| Lexical MRR | 0.214 | **0.426** |

This is the entire argument for building an evaluation harness. Five probe
queries and a look at the results would never have surfaced it, because the
failure is invisible from the fused output — the system returned good answers
the whole time, from one retriever while the other silently contributed nothing.

### Equal-weight RRF was the wrong default

[03-retrieval.md](03-retrieval.md) chose unweighted RRF because "tuning those
weights is a rabbit hole with no ground truth to tune against until Phase 9."
There is ground truth now.

Once the lexical arm became an OR-expanded recall net it returns 50 candidates
for every query, including ones where nothing matches well. At equal weight
those near-misses outvoted good semantic hits:

| configuration | R@1 | R@5 | MRR |
| --- | --- | --- | --- |
| semantic only | 0.674 | 0.913 | 0.780 |
| RRF, equal weight *(shipped before Phase 9)* | 0.587 | 0.891 | 0.708 |
| RRF, lexical × 0.25 *(ships now)* | **0.696** | **0.935** | **0.793** |

**The exact weight is not meaningful.** Anything between 0.1 and 0.25 scores the
same within one question, `k` barely matters at all, and these weights were
chosen on the same 46 questions they were scored on — the setup that reliably
produces numbers which do not survive new data. What the benchmark supports is
*"much less than semantic"*, not *"0.25 precisely"*. Confirming it needs a
held-out split, and 46 questions cannot spare one.

<details>
<summary>Full sweep</summary>

| k | w_lex | R@1 | R@5 | R@10 | MRR | nDCG@10 |
| --- | --- | --- | --- | --- | --- | --- |
| 30 | 0.1 | 0.696 | 0.913 | 0.957 | 0.799 | 0.771 |
| 120 | 0.1 | 0.674 | 0.935 | 0.957 | 0.793 | 0.768 |
| 60 | 0.25 | 0.696 | 0.935 | 0.957 | 0.793 | 0.769 |
| 120 | 0.25 | 0.696 | 0.935 | 0.935 | 0.789 | 0.757 |
| 60 | 0.1 | 0.674 | 0.913 | 0.957 | 0.785 | 0.766 |
| 60 | 0.0 | 0.674 | 0.913 | 0.935 | 0.780 | 0.744 |

</details>

### Hybrid beats either retriever alone — by less than the pitch suggests

Fused retrieval beats semantic alone by **2.2 points of Recall@5** (0.935 vs
0.913) and 0.013 MRR. That is one question out of 46. The honest summary is that
on this corpus hybrid retrieval is *at least as good everywhere and better in
places*, not that it is transformative.

Where it clearly matters is the category split: lexical-only manages 0.750 on
questions containing an exact identifier but **0.400** on paraphrased ones,
while semantic is the mirror image. Neither retriever is safe alone; the fusion
is insurance, and insurance looks unnecessary right up until it isn't.

### Reranking does not do what Phase 6 claimed

[04-roadmap.md](04-roadmap.md) recorded that "reranking earns its place",
based on five probe queries judged by eye. A 46-question benchmark disagrees:

| | hybrid | hybrid+rerank |
| --- | --- | --- |
| Recall@1 | **0.696** | 0.674 |
| Recall@5 | **0.935** | 0.870 |
| Recall@10 | 0.957 | **0.978** |
| MRR | **0.793** | 0.765 |
| nDCG@10 | 0.769 | **0.770** |
| p95 latency | **66 ms** | 135 ms |

The cross-encoder pulls one more answer into the top 10 and reorders the top 5
slightly worse, for double the latency. On this corpus it is **neutral at best
for ranking**. Phase 6's claim was drawn from a sample too small to support it,
and this supersedes it.

It is still switched on, for a reason the ranking table does not show: its score
is what the refusal path fires on (§4). A calibrated "nothing here answers this"
signal is worth 40 ms even if the reordering is a wash.

### Ranking children and returning parents is the right split

`parent-direct` ranks 64-second parents instead of 17-second children:

| | hybrid (child) | parent-direct |
| --- | --- | --- |
| Recall@5 | 0.935 | 0.913 |
| MRR | **0.793** | 0.609 |
| Median error to the exact moment | **0.0 s** | 21.9 s |
| Mean returned span | 17 s | 64 s |

Recall is nearly identical — unsurprising, since a coarser window is more likely
to intersect a fixed span for reasons unrelated to retrieval quality. The
difference is precision: child-level retrieval puts you on **the exact chunk**
where the answer is spoken, while parent-level lands 22 seconds early on
average. On a six-hour video that is the difference between a click that works
and a click that starts you mid-sentence in the wrong explanation.

Note that MRR collapses (0.793 → 0.609) while Recall@5 barely moves. Coarse
units find the neighbourhood but rank it worse.

---

## 4 · Refusal — and a floor that was in the wrong place

All six negatives — sourdough, Portugal, Kubernetes, the French Revolution, oil
filters, SQL joins — are correctly refused. **With zero LLM calls spent**: the
cross-encoder floor declines before a prompt is ever built, which is what makes
this the one answer-contract metric measurable on an exhausted free tier.

But refusing negatives is only half the contract. The other half — *not*
refusing questions the system can answer — had never been measured, and it was
failing.

| | answerable (n=46) | off-corpus (n=6) |
| --- | --- | --- |
| min | **−1.75** | −10.32 |
| p10 | 1.30 | — |
| median | 5.49 | — |
| max | — | **−5.01** |

The two distributions separate cleanly, with a gap between −5.01 and −1.75. The
floor sat at **0.0** — *inside the answerable range*. It was derived back in
Phase 6 from two observed data points, and it refused two questions that had
already been retrieved correctly:

| score | question |
| --- | --- |
| −1.75 | How do I stop my game running at different speeds on slower computers? |
| −0.13 | How to open missing windows in Unity? |

The first one is the instructive case. Retrieval returned 0:52:14 — *"when a
computer with 50 FPS would make the character move slower than a computer…"* —
which is exactly the answer. The system found it and then declined to use it.

**False refusal is the worse failure of the two.** A wrong answer is visible and
arguable; a refusal on retrievable content looks to the user like the corpus
simply does not cover the topic, and there is nothing to appeal.

The floor is now **−3.0**, sitting in the gap with ~2 points of margin either
side:

| | floor 0.0 | floor −3.0 |
| --- | --- | --- |
| Answerable questions refused | 2 / 46 (4.3%) | **0 / 46** |
| Negatives not refused | 0 / 6 | **0 / 6** |

On 46 questions and 6 negatives, from one corpus. A wider corpus may well close
that gap — the number to watch is the false-refusal rate, which
`run_benchmark.py` now reports on every run, not the constant itself.

**A second bug fell out of fixing the first.** The floor existed twice: once in
`pipeline/rerank.py` for the search UI, once as `llm_relevance_floor` in
`config.py` for the answer path. Both were `0.0`, so nothing had ever revealed
that they were independent — recalibrating one left the answer path still
refusing at the old threshold. `config.py` now defaults from the reranker's
constant. Two constants that must agree will eventually disagree.

---

## 5 · Where retrieval still fails

Three questions miss at k=5 under the shipped configuration:

| id | question | why |
| --- | --- | --- |
| `q026` | How to make a character stop moving when attacking? | The corpus teaches this twice; retrieval finds the player version, gold marks the skeleton version. A ground-truth gap as much as a retrieval one. |
| `lexi003` | What does SerializeField do? | The identifier appears 14 times across the video; the *explanation* is one of them, and ranking cannot tell explanation from usage. |
| `conc005` | How do I make enemies keep coming, faster and faster? | Pure paraphrase — the transcript never says "faster and faster", it says "reduce cooldown by 0.5 every time". |

`lexi003` is the interesting one, and it is not fixable by tuning weights.
Distinguishing "where a thing is explained" from "where a thing is mentioned"
needs a signal the current pipeline does not compute — the topic segmentation
from Phase 5 is the obvious place to get it.

---

## 6 · Answer quality — **not measured**

The metrics in [03-retrieval.md](03-retrieval.md) that need the LLM — citation
validity, uncited-sentence rate, end-to-end latency — are **not reported here,
because the daily free-tier quota was exhausted before they could be run.**

Gemini's free tier allows **20 requests per day** on this key, and building the
question set spent six of them. The refusal numbers in §4 are real precisely
because that path costs nothing.

To fill this in:

```bash
python backend/scripts/run_benchmark.py --answers 12 --variants hybrid
```

Fabricated-citation behaviour is covered separately and does not depend on
quota: 45 unit tests in `tests/unit/test_answer.py` attack the guarantee
directly, including every marker-grouping format a model emits.

---

## 7 · Honest scorecard against the Phase 9 plan

| Planned | Status |
| --- | --- |
| Recall@k, MRR | ✅ measured, 46 questions |
| nDCG@10 | ✅ added (not in the original plan) |
| Timestamp error | ✅ measured, child vs parent |
| p50 / p95 latency | ✅ measured (retrieval only) |
| Lexical vs semantic vs fused ablation | ✅ measured — and it found a bug |
| Child-only vs parent-child ablation | ✅ measured |
| Refusal behaviour (both directions) | ✅ measured — and it moved the floor |
| Citation validity, uncited rate | ❌ blocked on LLM quota |
| Event P/R/F1 vs chapter markers | ❌ no video with official chapters |
| 40 videos, ~150 questions | ❌ 1 video, 46 questions |

**The largest caveat is the corpus.** One video means every number is a claim
about one screencast of one person teaching Unity at 360p. Retrieval quality on
lecture content, on multiple speakers, on slides, on anything with different
acoustics, is unmeasured. The harness is built and the question set regenerates
with two commands — the missing ingredient is videos, not code.
