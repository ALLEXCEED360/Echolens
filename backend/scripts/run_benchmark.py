"""Run the benchmark and its ablations.

    python scripts/run_benchmark.py                 # retrieval ablations
    python scripts/run_benchmark.py --answers 8     # + answer quality (LLM calls)
    python scripts/run_benchmark.py --check         # validate gold spans only

Prints markdown tables ready to paste into docs/08-evaluation.md, and writes
the full per-query record to benchmarks/results.json so a number in the
document can be traced back to the query that produced it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.benchmark.dataset import DEFAULT_PATH, Dataset  # noqa: E402
from app.benchmark.harness import VARIANTS, embed_questions, run_all, sweep_fusion  # noqa: E402
from app.benchmark.metrics import percentile  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Chunk, ChunkLevel  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("benchmark")

RESULTS = DEFAULT_PATH.parent / "results.json"


async def check_gold_spans(session, dataset: Dataset) -> int:
    """Confirm every gold span is a real parent boundary.

    A gold span typed by hand is a span that can be wrong by a minute without
    anything complaining, and a benchmark with silently bad labels reports
    confident nonsense. Cheap to check, so it runs before every scoring pass.
    """
    rows = (
        await session.execute(
            select(Chunk.video_id, Chunk.start_s, Chunk.end_s).where(
                Chunk.level == ChunkLevel.PARENT
            )
        )
    ).all()
    known = {(r[0], round(float(r[1]), 1), round(float(r[2]), 1)) for r in rows}

    problems = 0
    for question in dataset.scorable:
        for start, end in question.gold_spans:
            if (question.video_id, round(start, 1), round(end, 1)) not in known:
                logger.warning(
                    "  %s: gold span %.0f-%.0f is not a parent boundary", question.id, start, end
                )
                problems += 1
    if problems:
        logger.warning("%d gold spans do not match a parent chunk", problems)
    else:
        logger.info("All %d gold spans match real parent boundaries", len(dataset.scorable))
    return problems


async def score_refusal(session, dataset: Dataset) -> dict:
    """Where the relevance floor sits relative to real questions.

    Costs no LLM quota at all — the floor is a decision about retrieval scores,
    taken before any prompt is built. That makes it the one answer-contract
    metric measurable on a free tier, and it governs the failure that matters
    most: **refusing a question the system already retrieved correctly** looks
    to the user like the corpus does not cover it.
    """
    from app.benchmark.harness import embed_questions, retrieve
    from app.pipeline.embedding import embed_query
    from app.pipeline.rerank import RELEVANCE_FLOOR

    embeddings = await embed_questions(dataset.scorable)

    answerable = []
    for question in dataset.scorable:
        _, score = await retrieve(
            session, "hybrid+rerank", question.question, embeddings[question.id]
        )
        answerable.append((question, score))

    negatives = []
    for negative in dataset.negatives:
        _, score = await retrieve(
            session, "hybrid+rerank", negative.question, await embed_query(negative.question)
        )
        negatives.append((negative, score))

    false_refusals = [
        {"id": q.id, "category": q.category, "question": q.question, "score": s}
        for q, s in answerable
        if s is not None and s < RELEVANCE_FLOOR
    ]
    missed = [
        {"id": q.id, "question": q.question, "score": s}
        for q, s in negatives
        if s is not None and s >= RELEVANCE_FLOOR
    ]

    positive_scores = sorted(s for _, s in answerable if s is not None)
    negative_scores = sorted(s for _, s in negatives if s is not None)
    return {
        "floor": RELEVANCE_FLOOR,
        "n_answerable": len(answerable),
        "n_negatives": len(negatives),
        "false_refusals": false_refusals,
        "missed_refusals": missed,
        "worst_true_positive": positive_scores[0] if positive_scores else None,
        "best_true_negative": negative_scores[-1] if negative_scores else None,
        "answerable_median": (
            positive_scores[len(positive_scores) // 2] if positive_scores else None
        ),
    }


async def score_answers(session, dataset: Dataset, limit: int) -> dict:
    """Answer-quality metrics from docs/03-retrieval.md.

    Deliberately run over a small sample: each question is a paid LLM call
    against a free tier that allows 5 per minute, and the retrieval ablations
    above are where the engineering questions actually live.
    """
    from app.answer import answer_question
    from app.pipeline.llm import LLMQuotaExceeded

    questions = dataset.scorable[:limit]
    negatives = dataset.negatives

    answered, refused_wrongly = [], []
    quota_hit = None
    for question in questions:
        started = time.perf_counter()
        try:
            result = await answer_question(session, question.question)
        except LLMQuotaExceeded as exc:
            # Report what was measured rather than losing it. The refusal path
            # below costs no quota at all — it declines before any prompt is
            # built — so it is still worth running.
            quota_hit = str(exc)
            logger.warning("Stopping answer scoring after %d: %s", len(answered), exc)
            break
        took_ms = (time.perf_counter() - started) * 1000.0
        if result.refused:
            refused_wrongly.append(question.id)
            continue
        cited_spans = [(c.start_s, c.end_s) for c in result.citations]
        answered.append(
            {
                "id": question.id,
                "citations": len(result.citations),
                "fabricated": len(result.fabricated_citations),
                "uncited": result.uncited_sentences,
                "sentences": result.total_sentences,
                # A citation is on-target when it points inside a gold span.
                "on_target": sum(1 for s, e in cited_spans if question.overlaps(s, e)),
                "took_ms": took_ms,
            }
        )

    # Negatives cost nothing: the relevance floor declines before a prompt is
    # ever built, so this runs even when the daily allowance is gone.
    refused = []
    for negative in negatives:
        try:
            result = await answer_question(session, negative.question)
        except LLMQuotaExceeded:
            logger.warning("  %s reached the model despite the floor", negative.id)
            refused.append(False)
            continue
        refused.append(result.refused)

    total_citations = sum(a["citations"] for a in answered)
    total_sentences = sum(a["sentences"] for a in answered)
    return {
        "quota_hit": quota_hit,
        "n_answered": len(answered),
        "n_refused_wrongly": len(refused_wrongly),
        "refused_wrongly": refused_wrongly,
        "fabricated_citations": sum(a["fabricated"] for a in answered),
        "total_citations": total_citations,
        "citation_on_target_rate": (
            sum(a["on_target"] for a in answered) / total_citations if total_citations else 0.0
        ),
        "uncited_rate": (
            sum(a["uncited"] for a in answered) / total_sentences if total_sentences else 0.0
        ),
        "negative_refusal_rate": sum(refused) / len(refused) if refused else 0.0,
        "p50_ms": percentile([a["took_ms"] for a in answered], 50) if answered else 0.0,
        "detail": answered,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--variants", nargs="*", default=list(VARIANTS))
    ap.add_argument("--answers", type=int, default=0, help="sample size for answer quality")
    ap.add_argument("--check", action="store_true", help="validate gold spans and exit")
    ap.add_argument("--sweep", action="store_true", help="sweep RRF weight and k")
    args = ap.parse_args()

    dataset = Dataset.load(args.dataset)
    logger.info(
        "%d questions: %d scorable, %d negatives, %d unverified (excluded)",
        len(dataset), len(dataset.scorable), len(dataset.negatives),
        sum(1 for q in dataset if not q.verified),
    )

    async with SessionLocal() as session:
        problems = await check_gold_spans(session, dataset)
        if args.check:
            return 1 if problems else 0

        started = time.perf_counter()
        results = await run_all(session, dataset, variants=args.variants)
        elapsed = time.perf_counter() - started

        print()
        print(f"### Retrieval ablations — {len(dataset.scorable)} questions")
        print()
        print(
            "| variant | R@1 | R@5 | R@10 | MRR | nDCG@10 | err(s) | span(s) | p50 | p95 |"
        )
        print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for variant in args.variants:
            print(results[variant][0].row())

        categories = dataset.categories
        print()
        print("### Recall@5 by question category")
        print()
        print("| variant | " + " | ".join(f"{c} (n={len(dataset.by_category(c))})"
                                          for c in categories) + " |")
        print("| --- | " + " | ".join("---" for _ in categories) + " |")
        for variant in args.variants:
            report = results[variant][0]
            cells = " | ".join(f"{report.by_category.get(c, 0):.3f}" for c in categories)
            print(f"| {variant} | {cells} |")

        payload = {
            "n_questions": len(dataset.scorable),
            "elapsed_s": round(elapsed, 1),
            "variants": {
                name: {
                    "summary": {
                        k: v for k, v in asdict(report).items() if k != "by_category"
                    }
                    | {"by_category": report.by_category},
                    "queries": [asdict(o) for o in outcomes],
                }
                for name, (report, outcomes) in results.items()
            },
        }

        if args.sweep:
            logger.info("Sweeping fusion parameters...")
            rows = await sweep_fusion(
                session, dataset, await embed_questions(dataset.scorable)
            )
            payload["sweep"] = rows
            print()
            print("### Fusion sweep (lexical weight x RRF k)")
            print()
            print("| k | w_lex | R@1 | R@5 | R@10 | MRR | nDCG@10 |")
            print("| --- | --- | --- | --- | --- | --- | --- |")
            for row in sorted(rows, key=lambda r: (-r["mrr"], r["k"]))[:12]:
                print(
                    f"| {row['k']} | {row['lexical_weight']} | {row['recall_at_1']:.3f} "
                    f"| {row['recall_at_5']:.3f} | {row['recall_at_10']:.3f} "
                    f"| {row['mrr']:.3f} | {row['ndcg_at_10']:.3f} |"
                )

        refusal = await score_refusal(session, dataset)
        payload["refusal"] = refusal
        print()
        print("### Refusal — the relevance floor")
        print()
        print("| | |")
        print("| --- | --- |")
        print(f"| floor | {refusal['floor']} |")
        print(
            f"| answerable questions refused | {len(refusal['false_refusals'])}"
            f" / {refusal['n_answerable']} |"
        )
        print(
            f"| negatives NOT refused | {len(refusal['missed_refusals'])}"
            f" / {refusal['n_negatives']} |"
        )
        print(f"| worst answerable score | {refusal['worst_true_positive']:.2f} |")
        print(f"| best off-corpus score | {refusal['best_true_negative']:.2f} |")
        print(f"| answerable median | {refusal['answerable_median']:.2f} |")
        for item in refusal["false_refusals"]:
            print(f"| refused at {item['score']:.2f} | {item['question'][:60]} |")

        if args.answers:
            logger.info("\nScoring answers over %d questions...", args.answers)
            payload["answers"] = await score_answers(session, dataset, args.answers)
            a = payload["answers"]
            print()
            print("### Answer quality")
            print()
            print(f"| answered | {a['n_answered']} |")
            print("| --- | --- |")
            if a["quota_hit"]:
                print(f"| **incomplete** | {a['quota_hit']} |")
            print(f"| fabricated citations | {a['fabricated_citations']} |")
            print(f"| citations landing in a gold span | {a['citation_on_target_rate']:.1%} |")
            print(f"| uncited sentences stripped | {a['uncited_rate']:.1%} |")
            print(f"| refused despite an answer existing | {a['n_refused_wrongly']} |")
            print(f"| negatives correctly refused | {a['negative_refusal_rate']:.1%} |")
            print(f"| p50 latency | {a['p50_ms']:.0f} ms |")

    RESULTS.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("\nWrote per-query results to %s (%.0fs)", RESULTS, elapsed)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
