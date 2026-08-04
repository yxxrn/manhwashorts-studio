"""P0 editorial, voice identity, subtitle, and panel metadata

Revision ID: a4p0_editorial_voice_visual_contract
Revises: 8fb2c3d4e5a6
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4p0_editorial_voice_visual_contract"
down_revision: str | None = "8fb2c3d4e5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    with op.batch_alter_table("script_versions") as b:
        b.add_column(sa.Column("editorial_metadata", sa.JSON(), nullable=False, server_default="{}"))
    with op.batch_alter_table("audio_segments") as b:
        b.add_column(sa.Column("spoken_text", sa.Text(), nullable=False, server_default=""))
        b.add_column(sa.Column("display_text", sa.Text(), nullable=False, server_default=""))
        b.add_column(sa.Column("voice_profile_hash", sa.String(64), nullable=False, server_default=""))
        b.add_column(sa.Column("voice_profile", sa.JSON(), nullable=False, server_default="{}"))
    with op.batch_alter_table("source_assets") as b:
        b.add_column(sa.Column("panel_bbox", sa.JSON(), nullable=False, server_default="{}"))
        b.add_column(sa.Column("panel_quality", sa.JSON(), nullable=False, server_default="{}"))
        b.add_column(sa.Column("panel_decision", sa.String(20), nullable=False, server_default="accept"))
    with op.batch_alter_table("timeline_scenes") as b:
        b.add_column(sa.Column("alignment_score", sa.Float(), nullable=False, server_default="0"))
        b.add_column(sa.Column("alignment_reasons", sa.JSON(), nullable=False, server_default="[]"))
        b.add_column(sa.Column("rejected_candidates", sa.JSON(), nullable=False, server_default="[]"))
        b.add_column(sa.Column("visual_signature", sa.String(128), nullable=False, server_default=""))

def downgrade() -> None:
    with op.batch_alter_table("script_versions") as b:
        b.drop_column("editorial_metadata")
    with op.batch_alter_table("timeline_scenes") as b:
        b.drop_column("visual_signature")
        b.drop_column("rejected_candidates")
        b.drop_column("alignment_reasons")
        b.drop_column("alignment_score")
    with op.batch_alter_table("source_assets") as b:
        b.drop_column("panel_decision")
        b.drop_column("panel_quality")
        b.drop_column("panel_bbox")
    with op.batch_alter_table("audio_segments") as b:
        b.drop_column("voice_profile")
        b.drop_column("voice_profile_hash")
        b.drop_column("display_text")
        b.drop_column("spoken_text")
