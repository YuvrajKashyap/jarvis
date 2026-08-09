"""Add a rebuildable local semantic-memory index."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_memory_embeddings"
down_revision: str | None = "0004_conversation_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_embedding",
        sa.Column("fact_id", sa.String(), primary_key=True),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index("ix_memory_embedding_model", "memory_embedding", ["model"])


def downgrade() -> None:
    op.drop_table("memory_embedding")
