"""Turn LLM-proposed candidates into a verified benchmark.

This script is the audit trail for the hand-verification step
docs/03-retrieval.md calls for. Every editorial decision is recorded here with
its reason, so the question set can be regenerated and disagreed with rather
than taken on trust.

Three things happen:

1. **Generated candidates are reviewed.** Each was read against its full gold
   span, not just the child it was written from. Items are dropped when the span
   does not actually answer the question, when the question merely restates the
   passage, or when another item asks the same thing about a different moment —
   a duplicate intent with single-span ground truth scores a correct retrieval
   as a failure.

2. **Hand-written questions are added** in two categories the generator cannot
   produce. `lexical` questions carry an exact identifier and exist to test
   where embeddings are known to fail. `conceptual` questions are phrased with
   deliberately no vocabulary in common with the transcript, to test the
   opposite. Both were located by reading the transcript, and their gold spans
   are real parent boundaries taken from the database.

3. **Negatives are added.** Questions with no answer anywhere in the corpus,
   which measure whether the system declines instead of dressing up its
   least-bad match as an answer.

Run: python scripts/curate_benchmark.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.benchmark.dataset import DEFAULT_PATH, Dataset, Question  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Chunk, ChunkLevel  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("curate")

# The candidates a human has actually read. Anything outside this set — a later
# generation run, a re-sample after reindexing — stays unverified and unscored.
# Without this the script would stamp `verified` on questions nobody reviewed,
# which is the exact failure the flag exists to prevent.
REVIEWED = {f"q{i:03d}" for i in range(1, 38)}

# Generated candidates that do not survive review.
DROP = {
    "q015": "gold span is the ground-check gizmo, not flipping; duplicate intent with q013",
    "q027": "restates the passage ('why the error? because we did not implement it')",
    "q030": "gold span is 'use these lights if you like them' — no answerable content",
    "q031": "duplicate intent with q037 (flip on player X position) at a different moment",
}

# Rewrites. The generator's phrasing borrowed too much of the passage's
# vocabulary, or bundled two intents into one question.
REWRITE = {
    "q010": "Where can I download free art assets for my game?",
    "q016": "How do I call a method that lives on a different script?",
    "q035": "How can I access a script from anywhere without dragging in a reference?",
}

VIDEO_TITLE = "videoplayback"

# Hand-written. Gold spans are real parent boundaries, read and confirmed
# against the transcript; see the docstring in each entry for what is said there.
LEXICAL = [
    (
        "What does Time.deltaTime do?",
        [(11301, 11362), (11362, 11426)],
        "3:08 'time.deltaTime shows you time that has passed since the last frame'",
    ),
    (
        "How do I use Instantiate to spawn an enemy?",
        [(20949, 21009), (21009, 21071)],
        "5:49-5:51 spawning enemies at random respawn points",
    ),
    (
        "What does SerializeField do?",
        [(4633, 4694), (4567, 4633)],
        "1:16-1:18 exposing a private field in the inspector",
    ),
    (
        "What are gizmos for in Unity?",
        [(7433, 7502), (7568, 7628)],
        "2:03 'OnDrawGizmos allows you to visually display information on the screen'",
    ),
    (
        "What does the Awake method do?",
        [(2918, 2978), (2858, 2917)],
        "0:47-0:48 the MonoBehaviour lifecycle: Awake, Start, Update, FixedUpdate",
    ),
    (
        "What is a sorting layer?",
        [(17142, 17209), (17209, 17271)],
        "4:45 'to fix that, we usually use sorting layers'",
    ),
    (
        "What is a physics material used for?",
        [(8893, 8958)],
        "2:28 create a 2D physics material with zero friction and apply it",
    ),
    (
        "How do I add a canvas for the UI?",
        [(19372, 19432)],
        "5:22 'we make a canvas. You do UI, canvas' and set the scale mode",
    ),
]

# Deliberately share almost no vocabulary with the transcript: these test
# paraphrase, which is the thing a lexical retriever cannot do.
CONCEPTUAL = [
    (
        "How do I stop my game running at different speeds on slower computers?",
        [(11301, 11362), (3099, 3161)],
        "frame-rate independence via deltaTime and the fixed 50 Hz step",
    ),
    (
        "How do I make one image draw in front of another?",
        [(17142, 17209), (17209, 17271)],
        "sorting layers and order in layer",
    ),
    (
        "How can I see where an invisible detection line is pointing?",
        [(7433, 7502), (7568, 7628)],
        "drawing gizmos to visualise a raycast",
    ),
    (
        "How do I expose a hidden field in the editor without making it public?",
        [(4633, 4694)],
        "SerializeField",
    ),
    (
        "How do I make enemies keep coming, faster and faster?",
        [(20949, 21009), (21201, 21267)],
        "spawning on a cooldown that decreases each time",
    ),
]

# No answer anywhere in the corpus. Deliberately avoids machine learning: the
# second indexed video is a backpropagation lecture, so an ML question would be
# a legitimate hit rather than a negative.
NEGATIVES = [
    "How do I bake sourdough bread at home?",
    "What is the capital of Portugal?",
    "How do I set up a Kubernetes ingress controller?",
    "What were the main causes of the French Revolution?",
    "How often should I change the oil filter in my car?",
    "How do I write a SQL left join across three tables?",
]


async def snap(session, video_id, spans: list[tuple[float, float]]) -> list[list[float]]:
    """Snap a hand-typed span to the parent chunk containing its midpoint.

    Spans in this file were read off a transcript display that rounds to whole
    seconds, so typing them back produces boundaries that are close to real
    ones but not equal to them. Snapping removes that entire class of error —
    and, more importantly, it means a hand-written label cannot silently drift
    away from the chunk it was meant to point at when the corpus is reindexed.
    """
    rows = (
        await session.execute(
            select(Chunk.start_s, Chunk.end_s)
            .where(Chunk.video_id == video_id, Chunk.level == ChunkLevel.PARENT)
            .order_by(Chunk.start_s)
        )
    ).all()

    out: list[list[float]] = []
    for start, end in spans:
        midpoint = (start + end) / 2
        match = next((r for r in rows if r[0] <= midpoint <= r[1]), None)
        if match is None:
            # Fall back to the nearest boundary rather than dropping the label
            # silently; run_benchmark --check reports anything still wrong.
            match = min(rows, key=lambda r: abs(r[0] - start))
            logger.warning("  span %.0f-%.0f had no containing parent", start, end)
        snapped = [float(match[0]), float(match[1])]
        if snapped not in out:
            out.append(snapped)
    return out


async def main() -> int:
    dataset = Dataset.load(DEFAULT_PATH)
    logger.info("Loaded %d candidates", len(dataset))

    video_id = next((q.video_id for q in dataset if q.video_id), None)
    if video_id is None:
        logger.error("No generated questions carry a video id; run build_benchmark.py first.")
        return 1

    kept: list[Question] = []
    for question in dataset:
        if question.category != "generated":
            continue  # a previous curation run; rebuilt below
        if question.id not in REVIEWED:
            question.verified = False
            question.note = "awaiting review"
            kept.append(question)
            continue
        if question.id in DROP:
            logger.info("  drop %s — %s", question.id, DROP[question.id])
            continue
        if question.id in REWRITE:
            logger.info("  rewrite %s", question.id)
            question.question = REWRITE[question.id]
            question.note = "reworded during review"
        question.verified = True
        kept.append(question)

    logger.info("Kept %d of %d generated", len(kept), len(dataset))

    async with SessionLocal() as session:
        # The child chunk each generated question was written from is the
        # tightest anchor available for timestamp error; see Question.anchor_s.
        anchors = {
            r[0]: float(r[1])
            for r in (
                await session.execute(
                    select(Chunk.id, Chunk.start_s).where(
                        Chunk.id.in_([q.source_chunk_id for q in kept if q.source_chunk_id])
                    )
                )
            ).all()
        }
        for question in kept:
            if question.source_chunk_id in anchors:
                question.anchor_s = anchors[question.source_chunk_id]
        logger.info("Anchored %d questions to their source chunk", len(anchors))

        for label, items in (("lexical", LEXICAL), ("conceptual", CONCEPTUAL)):
            for index, (text, spans, note) in enumerate(items, start=1):
                kept.append(
                    Question(
                        id=f"{label[:4]}{index:03d}",
                        question=text,
                        category=label,
                        video_id=video_id,
                        gold_spans=await snap(session, video_id, spans),
                        kind="transcript",
                        verified=True,
                        note=note,
                    )
                )
            logger.info("Added %d %s questions", len(items), label)

    for index, text in enumerate(NEGATIVES, start=1):
        kept.append(
            Question(
                id=f"neg{index:03d}",
                question=text,
                category="negative",
                gold_spans=[],
                verified=True,
                note="no answer in the corpus",
            )
        )
    logger.info("Added %d negatives", len(NEGATIVES))

    out = Dataset(questions=kept)
    out.save(DEFAULT_PATH)
    logger.info(
        "Wrote %d questions (%d scorable, %d negatives) to %s",
        len(out), len(out.scorable), len(out.negatives), DEFAULT_PATH,
    )
    for category in out.categories:
        logger.info("  %-12s %d", category, len(out.by_category(category)))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
