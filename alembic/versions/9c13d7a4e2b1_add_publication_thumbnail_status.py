"""Track non-blocking YouTube thumbnail upload state."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9c13d7a4e2b1"
down_revision: str | None = "7776011fa52f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    with op.batch_alter_table("publications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("thumbnail_status", sa.String(length=20), nullable=False, server_default="pending"))
        batch_op.add_column(sa.Column("thumbnail_error", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("thumbnail_attempt", sa.Integer(), nullable=False, server_default="0"))

def downgrade() -> None:
    with op.batch_alter_table("publications", schema=None) as batch_op:
        batch_op.drop_column("thumbnail_attempt")
        batch_op.drop_column("thumbnail_error")
        batch_op.drop_column("thumbnail_status")
