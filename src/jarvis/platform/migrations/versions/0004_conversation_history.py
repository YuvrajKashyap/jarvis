"""Persist intentional user and assistant conversation messages."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_conversation_history"
down_revision: str | None = "0003_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_message",
        sa.Column("message_id", sa.String(), primary_key=True),
        sa.Column("source_event_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("turn_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint("source_event_id"),
    )
    op.create_index(
        "ix_conversation_message_session_id",
        "conversation_message",
        ["session_id"],
    )
    op.create_index(
        "ix_conversation_message_turn_id",
        "conversation_message",
        ["turn_id"],
    )
    op.create_index(
        "ix_conversation_message_created_at",
        "conversation_message",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("conversation_message")
