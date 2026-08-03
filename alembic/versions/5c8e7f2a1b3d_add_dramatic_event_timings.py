"""persist dramatic narration events for audio-driven editorial timing

Revision ID: 5c8e7f2a1b3d
Revises: 4b7c2d1e8f0a
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c8e7f2a1b3d"
down_revision: str | None = "4b7c2d1e8f0a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audio_segments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("dramatic_events", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("audio_segments", schema=None) as batch_op:
        batch_op.drop_column("dramatic_events")
