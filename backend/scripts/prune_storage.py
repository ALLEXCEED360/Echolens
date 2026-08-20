"""Remove stored files with no video row behind them.

Deleting a video used to leave its derived artefacts — extracted audio, every
keyframe JPEG — on disk forever, because they never went through `Storage` and
so `Storage.delete` never saw them. That is fixed at the source, but anything
orphaned before the fix is still there, and a crash mid-delete can always leave
something behind.

Dry by default. Nothing is removed unless `--apply` is passed, because a script
that deletes files on a typo is not a maintenance tool.

    python scripts/prune_storage.py            # report only
    python scripts/prune_storage.py --apply    # actually delete
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Video  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("prune")


def _size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="delete, rather than report")
    args = ap.parse_args()

    settings = get_settings()
    root = settings.storage_local_path

    async with SessionLocal() as session:
        live = {str(r[0]) for r in (await session.execute(select(Video.id))).all()}
    logger.info("%d video(s) in the database", len(live))

    orphans: list[Path] = []

    # `videos/` is sharded by the first two characters of the id; `derived/`
    # is flat. Walking to the wrong depth compares shard names against ids and
    # reports every directory as orphaned.
    videos = root / "videos"
    if videos.exists():
        for shard in videos.iterdir():
            if shard.is_dir():
                orphans += [d for d in shard.iterdir() if d.is_dir() and d.name not in live]

    derived = root / "derived"
    if derived.exists():
        orphans += [d for d in derived.iterdir() if d.is_dir() and d.name not in live]

    if not orphans:
        logger.info("Nothing orphaned.")
        return 0

    total = 0
    for path in orphans:
        size = _size(path)
        total += size
        logger.info(
            "  %-9s %8.1f MB  %s",
            "DELETE" if args.apply else "would",
            size / 1048576,
            path,
        )
        if args.apply:
            shutil.rmtree(path, ignore_errors=True)

    verb = "Reclaimed" if args.apply else "Reclaimable"
    logger.info("%s %.1f MB across %d director%s", verb, total / 1048576,
                len(orphans), "y" if len(orphans) == 1 else "ies")
    if not args.apply:
        logger.info("Re-run with --apply to delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
