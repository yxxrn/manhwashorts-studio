"""Add configurable final-video watermark settings.

Revision ID: f3c9a2d4e6b8
Revises: e8f1a2b3c4d5
"""

import sqlalchemy as sa

from alembic import op

revision = "f3c9a2d4e6b8"
down_revision = "e8f1a2b3c4d5"
branch_labels = None
depends_on = None

def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("watermark_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("watermark_text", sa.String(length=120), nullable=False, server_default=""))

def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("watermark_text")
        batch.drop_column("watermark_enabled")
