"""persist render resource metrics

Revision ID: 8fb2c3d4e5a6
Revises: 7ea1b2c3d4f5
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8fb2c3d4e5a6"
down_revision: str | None = "7ea1b2c3d4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("render_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("render_wall_seconds", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("peak_rss_bytes", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("scratch_bytes", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("render_jobs", schema=None) as batch_op:
        batch_op.drop_column("scratch_bytes")
        batch_op.drop_column("peak_rss_bytes")
        batch_op.drop_column("render_wall_seconds")