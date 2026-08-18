"""Per-run job options.

Whether voice-activity detection runs is a property of the *media*, not the
installation — a lecture and a firefight need opposite answers. Holding it only
in server config made it unusable: changing it meant restarting the API with an
environment variable, and the choice vanished when the video was re-uploaded.

Revision ID: 4e43ff7f9a99
Revises: 5b11f292e10c
Create Date: 2026-08-17 19:26:50.717884
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '4e43ff7f9a99'
down_revision: str | None = '5b11f292e10c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A server default is required, not cosmetic: the column is NOT NULL and
    # the table already holds rows, so adding it without one fails outright.
    # Existing jobs simply had no per-run options, which is `{}`.
    op.add_column(
        "processing_jobs",
        sa.Column("options", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    # Drop the default once existing rows are backfilled. Keeping it would
    # leave the schema permanently "drifted" from the model: Alembic compares
    # defaults by equality, and Postgres has no `=` operator for `json`, so the
    # comparison raises rather than reporting a difference.
    op.alter_column("processing_jobs", "options", server_default=None)


def downgrade() -> None:
    op.drop_column("processing_jobs", "options")
