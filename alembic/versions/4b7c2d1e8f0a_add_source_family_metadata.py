"""persist source-family metadata for panel cooldowns

Revision ID: 4b7c2d1e8f0a
Revises: 3a1c5e8d9b2f
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b7c2d1e8f0a"
down_revision: str | None = "3a1c5e8d9b2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_family", sa.String(length=255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("source_family_manual", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("timeline_scenes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_family", sa.String(length=255), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("timeline_scenes", schema=None) as batch_op:
        batch_op.drop_column("source_family")
    with op.batch_alter_table("source_assets", schema=None) as batch_op:
        batch_op.drop_column("source_family_manual")
        batch_op.drop_column("source_family")