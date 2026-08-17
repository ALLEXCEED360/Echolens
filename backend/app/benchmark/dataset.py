"""The benchmark question set.

**Ground truth is a time span, not a chunk id.** Chunk boundaries are an
implementation detail that every ablation changes — scoring against them would
make "did we retrieve the right unit" and "did we find the answer" the same
question, and only the second one matters. A result is correct when it overlaps
the span where the answer is actually spoken.

**The gold span is the source chunk's parent**, ~64 s rather than the ~17 s
child the question was written from. A 17 s window is narrower than the region a
human would call correct: an explanation that begins six seconds before the
child starts is not a retrieval failure. Widening to the parent makes the label
match the judgement a person would make, and the timestamp-error metric carries
the precision that this leniency gives up.

**Larger retrieval units are mechanically favoured by any overlap criterion.**
A 64 s window is more likely to intersect a fixed gold span than a 17 s one, for
reasons that have nothing to do with retrieval quality. This is why the
parent-versus-child ablation reports median timestamp error and mean returned
span width beside recall — read alone, recall would make the coarser unit look
strictly better when it is really a trade.

**A question may have several gold spans.** A six-hour tutorial explains flipping
a sprite three times, and marking only one of them correct measures nothing but
which occurrence the labeller happened to sample — a retriever that finds a
genuine explanation would be scored as having failed. `gold_spans` therefore
holds every region a human would accept, with the first one primary for
timestamp error.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Repo-relative default so a run is reproducible without arguments.
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "benchmarks" / "questions.jsonl"


@dataclass
class Question:
    """One benchmark item.

    `video_id` and the gold span are `None` for negatives — questions with no
    answer anywhere in the corpus, which exist to measure whether the system
    declines instead of dressing up its least-bad match as an answer.
    """

    id: str
    question: str
    category: str
    video_id: uuid.UUID | None = None
    # Every region a human would accept as answering the question. Empty for
    # negatives. The first entry is primary and defines timestamp error.
    gold_spans: list[list[float]] = field(default_factory=list)
    kind: str | None = None
    source_chunk_id: uuid.UUID | None = None
    # Start of the *child* chunk the question was written from: the tightest
    # available notion of "the exact moment the answer is given".
    #
    # Timestamp error has to be measured against this, not against a gold span
    # start. Gold spans are parent boundaries, so measuring against them scores
    # parent-level retrieval at exactly 0.0 by construction, while penalising a
    # more precise child chunk for beginning later inside a correct span — it
    # rewards coarseness and calls it accuracy. Hand-written questions have no
    # source chunk and are therefore excluded from that metric rather than
    # given a parent boundary to stand in for one.
    anchor_s: float | None = None
    # False means the item was LLM-proposed and not yet read by a human. Only
    # verified items are scored; see docs/08-evaluation.md.
    verified: bool = False
    note: str = ""

    @property
    def is_negative(self) -> bool:
        return not self.gold_spans

    @property
    def primary_start_s(self) -> float | None:
        return self.gold_spans[0][0] if self.gold_spans else None

    def overlaps(self, start_s: float, end_s: float) -> bool:
        """Does a retrieved span intersect any gold span?

        Boundary contact counts: a chunk ending exactly where a gold span
        begins shares a moment with it, and the alternative is a criterion that
        flips on floating-point noise.
        """
        return any(
            start_s <= gold_end and end_s >= gold_start
            for gold_start, gold_end in self.gold_spans
        )

    def nearest_gold_start_s(self, start_s: float) -> float | None:
        """The gold span start closest to a retrieved position.

        Timestamp error against the *primary* span would punish a result that
        landed precisely on a different, equally valid occurrence.
        """
        if not self.gold_spans:
            return None
        return min((g[0] for g in self.gold_spans), key=lambda g: abs(g - start_s))

    @property
    def has_anchor(self) -> bool:
        return self.anchor_s is not None

    def to_json(self) -> dict:
        data = asdict(self)
        for key in ("video_id", "source_chunk_id"):
            if data[key] is not None:
                data[key] = str(data[key])
        return data

    @classmethod
    def from_json(cls, data: dict) -> Question:
        payload = dict(data)
        for key in ("video_id", "source_chunk_id"):
            if payload.get(key):
                payload[key] = uuid.UUID(payload[key])
            else:
                payload[key] = None

        # Accept the single-span form the generator writes.
        if "gold_spans" not in payload and payload.get("gold_start_s") is not None:
            payload["gold_spans"] = [
                [float(payload["gold_start_s"]), float(payload["gold_end_s"])]
            ]
        payload["gold_spans"] = [[float(a), float(b)] for a, b in payload.get("gold_spans") or []]

        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in payload.items() if k in known})


@dataclass
class Dataset:
    questions: list[Question] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.questions)

    def __iter__(self) -> Iterator[Question]:
        return iter(self.questions)

    @property
    def scorable(self) -> list[Question]:
        """Verified questions that have an answer in the corpus.

        Unverified items are excluded rather than merely flagged. A benchmark
        that quietly scores machine-proposed labels is measuring the proposer.
        """
        return [q for q in self.questions if q.verified and not q.is_negative]

    @property
    def negatives(self) -> list[Question]:
        return [q for q in self.questions if q.verified and q.is_negative]

    def by_category(self, category: str) -> list[Question]:
        return [q for q in self.scorable if q.category == category]

    @property
    def categories(self) -> list[str]:
        return sorted({q.category for q in self.scorable})

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PATH) -> Dataset:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"No benchmark at {path}. Build one with scripts/build_benchmark.py."
            )
        questions = [
            Question.from_json(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        seen: set[str] = set()
        for q in questions:
            if q.id in seen:
                raise ValueError(f"Duplicate question id {q.id!r} in {path}")
            seen.add(q.id)
        return cls(questions=questions)

    def save(self, path: Path | str = DEFAULT_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(q.to_json(), ensure_ascii=False) for q in self.questions)
            + "\n",
            encoding="utf-8",
        )
