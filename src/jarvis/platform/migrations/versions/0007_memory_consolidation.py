"""Track conversation messages incorporated into durable memory."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_memory_consolidation"
down_revision: str | None = "0006_proactivity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_message",
        sa.Column("consolidation_version", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_conversation_message_consolidation_version",
        "conversation_message",
        ["consolidation_version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_message_consolidation_version",
        table_name="conversation_message",
    )
    op.drop_column("conversation_message", "consolidation_version")
