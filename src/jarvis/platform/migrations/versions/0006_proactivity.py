"""Persist tasteful proactivity cooldown receipts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_proactivity"
down_revision: str | None = "0005_memory_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proactivity_receipt",
        sa.Column("receipt_id", sa.String(), primary_key=True),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("suggested_at", sa.String(), nullable=False),
    )
    op.create_index(
        "ix_proactivity_receipt_fingerprint",
        "proactivity_receipt",
        ["fingerprint"],
    )
    op.create_index(
        "ix_proactivity_receipt_suggested_at",
        "proactivity_receipt",
        ["suggested_at"],
    )


def downgrade() -> None:
    op.drop_table("proactivity_receipt")
