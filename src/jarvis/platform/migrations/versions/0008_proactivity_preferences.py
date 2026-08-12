"""Persist snooze, mute, and relevance feedback for proactive topics."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_proactivity_preferences"
down_revision: str | None = "0007_memory_consolidation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proactivity_preference",
        sa.Column("topic", sa.String(), primary_key=True),
        sa.Column("muted", sa.Boolean(), nullable=False),
        sa.Column("snoozed_until", sa.String(), nullable=True),
        sa.Column("affinity", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("proactivity_preference")
