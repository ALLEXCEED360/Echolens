"""Propose benchmark questions from the indexed corpus.

docs/03-retrieval.md sets the strategy: hand-labelling hundreds of questions is
months of unglamorous work that will not get finished, so LLM-generate
candidates and **hand-verify a subset**. This script does the first half only.
Everything it writes is marked `verified: false` and is excluded from scoring
until a human has read it — an unverified label measures the proposer, not the
system.

**The bias this introduces, stated up front.** A question written *from* a
passage shares that passage's vocabulary and framing, which flatters semantic
retrieval and makes the benchmark easier than real use. Two things push back:
the prompt forbids reusing the passage's distinctive wording, and the
hand-written `lexical` and `conceptual` categories are authored independently of
any single chunk. It does not eliminate the bias. Read the absolute numbers as
optimistic; the *differences between variants* are what this is for, and they
share the bias equally.

Usage:
    python scripts/build_benchmark.py --per-video 40
    python scripts/build_benchmark.py --dry-run     # sample only, no LLM calls
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import aliased  # noqa: E402

from app.benchmark.dataset import DEFAULT_PATH, Dataset, Question  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Chunk, ChunkLevel, Video  # noqa: E402
from app.pipeline.llm import LLMQuotaExceeded, get_provider  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("build_benchmark")

BATCH = 8
MIN_WORDS = 25
PACE_S = 13.0  # 5 requests/minute on the free tier, with room to spare

SYSTEM = """You write evaluation questions for a video search benchmark.

For each numbered passage, write ONE question that the passage answers.

Rules:
- Write what a real user would type into a search box. Natural, specific, short.
- Do NOT reuse the passage's distinctive wording. If the passage says "we're
  gonna slap a rigidbody on it", ask "how do I make an object fall?" — not
  "how do I slap a rigidbody on it?". Reusing phrasing makes the benchmark
  measure string overlap instead of retrieval.
- The question must be answerable from that passage ALONE, and must make sense
  to someone who has not read it. No "this", "that", "the above".
- If a passage is too vague, too short, or is filler with no factual content,
  return "SKIP" for it. Skipping is expected and correct — roughly a third of
  passages in a spoken transcript carry nothing worth asking about.

Return JSON only, no prose, no code fence:
[{"n": 1, "question": "..."}, {"n": 2, "question": "SKIP"}]"""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse(raw: str, batch: list) -> list[tuple[int, str]]:
    """Pull the JSON array out of a model response.

    Models wrap JSON in fences roughly half the time even when told not to, and
    failing the whole batch over a fence would throw away paid-for output.
    """
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    items: list = []
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            items = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            items = []

    if not items:
        # Salvage whole objects from a truncated array. A thinking model
        # charges its reasoning to the same output budget, so a response can
        # stop mid-array with eight good questions already written; discarding
        # them because the closing bracket never arrived wastes quota that has
        # already been spent.
        items = [
            {"n": m.group(1), "question": m.group(2)}
            for m in re.finditer(r'\{\s*"n"\s*:\s*(\d+)\s*,\s*"question"\s*:\s*"([^"]*)"', text)
        ]
        if items:
            logger.warning("  recovered %d items from a truncated response", len(items))

    if not items:
        logger.warning("  unparseable response: %s", text[:120])
        return []

    out = []
    for item in items:
        try:
            index = int(item["n"]) - 1
            question = _clean(str(item["question"]))
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= index < len(batch) and question.upper() != "SKIP" and len(question) > 10:
            out.append((index, question))
    return out


async def sample_chunks(session, video_id: uuid.UUID, count: int) -> list:
    """Child chunks spread evenly across the video, with their parent span.

    Even spread matters: taking the top *n* by any ordering would concentrate
    the benchmark in one region of a six-hour video and measure retrieval over
    a fraction of the corpus while claiming to measure all of it.
    """
    parent = aliased(Chunk)
    rows = (
        await session.execute(
            select(
                Chunk.id, Chunk.start_s, Chunk.end_s, Chunk.text, Chunk.kind,
                parent.start_s, parent.end_s,
            )
            .join(parent, parent.id == Chunk.parent_id)
            .where(Chunk.video_id == video_id, Chunk.level == ChunkLevel.CHILD)
            .order_by(Chunk.start_s)
        )
    ).all()

    usable = [r for r in rows if len(r[3].split()) >= MIN_WORDS]
    if len(usable) <= count:
        return usable
    step = len(usable) / count
    return [usable[int(i * step)] for i in range(count)]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-video", type=int, default=40)
    ap.add_argument("--out", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--dry-run", action="store_true", help="sample only, no LLM calls")
    args = ap.parse_args()

    existing = Dataset.load(args.out) if args.out.exists() else Dataset()
    seen = {q.source_chunk_id for q in existing if q.source_chunk_id}
    logger.info("Existing dataset: %d questions", len(existing))

    async with SessionLocal() as session:
        videos = [
            v
            for v in (await session.execute(select(Video).order_by(Video.created_at))).scalars()
            if (v.duration_s or 0) > 60
        ]
        if not videos:
            logger.error("No indexed video longer than a minute. Nothing to sample.")
            return 1

        provider = None if args.dry_run else get_provider()
        added = 0

        for video in videos:
            chunks = [c for c in await sample_chunks(session, video.id, args.per_video)
                      if c[0] not in seen]
            logger.info("%s: %d sampled passages", video.title[:40], len(chunks))
            if args.dry_run:
                for c in chunks[:3]:
                    logger.info("  [%7.1fs] %s", c[1], _clean(c[3])[:100])
                continue

            for start in range(0, len(chunks), BATCH):
                # The free tier allows 5 requests/minute. The provider will wait
                # out a rate limit, but pacing ahead of it is cheaper than
                # spending a request to be told to wait.
                if start:
                    await asyncio.sleep(PACE_S)
                batch = chunks[start : start + BATCH]
                prompt = "\n\n".join(
                    f"{i + 1}. {_clean(c[3])}" for i, c in enumerate(batch)
                )
                try:
                    # Generous, because thinking tokens are charged here too.
                    completion = await provider.complete(SYSTEM, prompt, max_tokens=4000)
                except LLMQuotaExceeded as exc:
                    logger.error("Stopping: %s", exc)
                    existing.save(args.out)
                    logger.info("Saved %d questions so far to %s", len(existing), args.out)
                    return 2

                for index, question in _parse(completion.text, batch):
                    chunk = batch[index]
                    existing.questions.append(
                        Question(
                            id=f"q{len(existing.questions) + 1:03d}",
                            question=question,
                            category="generated",
                            video_id=video.id,
                            # Gold is the PARENT span: the child is narrower
                            # than the region a human would call correct. A
                            # reviewer adds further spans where the same
                            # question is answered more than once.
                            gold_spans=[[float(chunk[5]), float(chunk[6])]],
                            kind=str(chunk[4]),
                            source_chunk_id=chunk[0],
                            verified=False,
                        )
                    )
                    added += 1
                logger.info("  batch %d: +%d", start // BATCH + 1, added)

    existing.save(args.out)
    logger.info("Wrote %d questions (%d new) to %s", len(existing), added, args.out)
    logger.info("All new items are verified=false and will NOT be scored until reviewed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
