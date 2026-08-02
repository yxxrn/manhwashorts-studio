"""add editorial intent and narration timing to timeline scenes

Revision ID: 3a1c5e8d9b2f
Revises: 2f0d4d5e7a1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3a1c5e8d9b2f"
down_revision: str | None = "2f0d4d5e7a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("timeline_scenes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("camera_intent", sa.String(length=20), nullable=False, server_default="neutral"))
        batch_op.add_column(sa.Column("narration_timing", sa.String(length=20), nullable=False, server_default="narration_lead"))


def downgrade() -> None:
    with op.batch_alter_table("timeline_scenes", schema=None) as batch_op:
        batch_op.drop_column("narration_timing")
        batch_op.drop_column("camera_intent")
