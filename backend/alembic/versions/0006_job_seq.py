"""Phase 4: monotonic job sequence

Revision ID: 8be17b1bb84a
Revises: 85aaa61fda82
Create Date: 2026-08-16 14:46:29.823101
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '8be17b1bb84a'
down_revision: str | None = '85aaa61fda82'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres backfills existing rows during the rewrite, so the column can be
    # NOT NULL immediately. The constraint is named rather than left to Alembic's
    # `None`, which produces an undroppable constraint on downgrade.
    op.add_column(
        "processing_jobs",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    )

    # Existing rows are numbered in physical order, which need not match the
    # order they were created in. Renumber by wall clock: imperfect where the
    # clock itself misbehaved, but the best available signal for history, and
    # every *future* job is ordered by the sequence alone.
    #
    # This runs *before* the unique constraint exists: a set-based renumber
    # passes through transiently duplicated values, which a live constraint
    # would reject even though the final state is unique.
    op.execute(
        """
        WITH ordered AS (
            SELECT id, row_number() OVER (
                ORDER BY COALESCE(finished_at, started_at, created_at), created_at
            ) AS rn
            FROM processing_jobs
        )
        UPDATE processing_jobs j
        SET seq = ordered.rn
        FROM ordered
        WHERE j.id = ordered.id
        """
    )
    op.create_unique_constraint("uq_processing_jobs_seq", "processing_jobs", ["seq"])

    # Move the identity sequence past the values just written, or the next
    # insert would collide with a renumbered row.
    op.execute(
        "SELECT setval(pg_get_serial_sequence('processing_jobs', 'seq'), "
        "COALESCE((SELECT max(seq) FROM processing_jobs), 1))"
    )


def downgrade() -> None:
    op.drop_constraint("uq_processing_jobs_seq", "processing_jobs", type_="unique")
    op.drop_column("processing_jobs", "seq")
