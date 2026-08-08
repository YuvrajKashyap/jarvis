"""Persist validated capability schedules without serialized callables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_schedules"
down_revision: str | None = "0002_phone_pairing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_capability",
        sa.Column("schedule_id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("capability", sa.String(), nullable=False),
        sa.Column("arguments_json", sa.String(), nullable=False),
        sa.Column("trigger_json", sa.String(), nullable=False),
        sa.Column("standing_rule_id", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    op.create_index(
        "ix_scheduled_capability_enabled",
        "scheduled_capability",
        ["enabled"],
    )
    op.create_index(
        "ix_scheduled_capability_capability",
        "scheduled_capability",
        ["capability"],
    )


def downgrade() -> None:
    op.drop_table("scheduled_capability")
