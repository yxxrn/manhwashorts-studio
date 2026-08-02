"""add Shot Director fields to timeline scenes

Revision ID: 2f0d4d5e7a1
Revises: 92dae0b434f1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2f0d4d5e7a1"
down_revision: str | None = "92dae0b434f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("timeline_scenes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("focus_end_x", sa.Float(), nullable=False, server_default="0.5"))
        batch_op.add_column(sa.Column("focus_end_y", sa.Float(), nullable=False, server_default="0.4"))
        batch_op.add_column(sa.Column("roi_label", sa.String(length=40), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("camera_curve", sa.String(length=40), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("timeline_scenes", schema=None) as batch_op:
        batch_op.drop_column("camera_curve")
        batch_op.drop_column("roi_label")
        batch_op.drop_column("focus_end_y")
        batch_op.drop_column("focus_end_x")
