"""Persist paired phone keys and short-lived authentication evidence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_phone_pairing"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pairing_offer",
        sa.Column("pairing_id", sa.String(), primary_key=True),
        sa.Column("secret_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "paired_device",
        sa.Column("device_id", sa.String(), primary_key=True),
        sa.Column("public_key_jwk_json", sa.String(), nullable=False),
        sa.Column("paired_at", sa.String(), nullable=False),
    )
    op.create_table(
        "phone_challenge",
        sa.Column("challenge_id", sa.String(), primary_key=True),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("challenge", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_phone_challenge_device_id", "phone_challenge", ["device_id"])
    op.create_table(
        "phone_session",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
    )
    op.create_index("ix_phone_session_device_id", "phone_session", ["device_id"])


def downgrade() -> None:
    for table in ("phone_session", "phone_challenge", "paired_device", "pairing_offer"):
        op.drop_table(table)
