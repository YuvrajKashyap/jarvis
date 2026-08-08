"""Create durable event, authorization, audit, and memory state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_event",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("turn_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.String(), nullable=False),
        sa.UniqueConstraint("session_id", "sequence"),
    )
    op.create_index("ix_source_event_session_id", "source_event", ["session_id"])
    op.create_index("ix_source_event_turn_id", "source_event", ["turn_id"])

    op.create_table(
        "action_audit",
        sa.Column("action_id", sa.String(), primary_key=True),
        sa.Column("invocation_id", sa.String(), nullable=False),
        sa.Column("capability", sa.String(), nullable=False),
        sa.Column("risk", sa.String(), nullable=False),
        sa.Column("policy_decision", sa.String(), nullable=False),
        sa.Column("approval_id", sa.String(), nullable=True),
        sa.Column("result_status", sa.String(), nullable=False),
        sa.Column("result_summary", sa.String(), nullable=False),
        sa.Column("undo_reference", sa.String(), nullable=True),
        sa.Column("recorded_at", sa.String(), nullable=False),
    )
    for column in ("invocation_id", "capability", "approval_id", "recorded_at"):
        op.create_index(f"ix_action_audit_{column}", "action_audit", [column])

    op.create_table(
        "approval",
        sa.Column("approval_id", sa.String(), primary_key=True),
        sa.Column("request_fingerprint", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("decision_device_id", sa.String(), nullable=True),
        sa.Column("decided_at", sa.String(), nullable=True),
    )
    for column in ("request_fingerprint", "expires_at", "status"):
        op.create_index(f"ix_approval_{column}", "approval", [column])

    op.create_table(
        "memory_fact",
        sa.Column("fact_id", sa.String(), primary_key=True),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("category_key", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("subject_key", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("source_event_ids_json", sa.String(), nullable=False),
        sa.Column("observed_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("category_key", "subject_key"),
    )
    op.create_index("ix_memory_fact_category_key", "memory_fact", ["category_key"])
    op.create_index("ix_memory_fact_subject_key", "memory_fact", ["subject_key"])

    op.create_table(
        "memory_conflict",
        sa.Column("conflict_id", sa.String(), primary_key=True),
        sa.Column("fact_id", sa.String(), nullable=False),
        sa.Column("candidate_content", sa.String(), nullable=False),
        sa.Column("source_event_ids_json", sa.String(), nullable=False),
        sa.Column("observed_at", sa.String(), nullable=False),
    )
    op.create_index("ix_memory_conflict_fact_id", "memory_conflict", ["fact_id"])

    op.create_table(
        "memory_revision",
        sa.Column("revision_id", sa.String(), primary_key=True),
        sa.Column("fact_id", sa.String(), nullable=False),
        sa.Column("prior_content", sa.String(), nullable=False),
        sa.Column("prior_version", sa.Integer(), nullable=False),
        sa.Column("corrected_at", sa.String(), nullable=False),
        sa.Column("source_event_id", sa.String(), nullable=False),
    )
    op.create_index("ix_memory_revision_fact_id", "memory_revision", ["fact_id"])

    op.create_table(
        "memory_deletion",
        sa.Column("deletion_id", sa.String(), primary_key=True),
        sa.Column("fact_id", sa.String(), nullable=False),
        sa.Column("forgotten_at", sa.String(), nullable=False),
    )
    op.create_index("ix_memory_deletion_fact_id", "memory_deletion", ["fact_id"])
    op.execute(
        "CREATE VIRTUAL TABLE memory_fts USING fts5("
        "fact_id UNINDEXED, category, subject, content, tokenize='unicode61')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_fts")
    for table in (
        "memory_deletion",
        "memory_revision",
        "memory_conflict",
        "memory_fact",
        "approval",
        "action_audit",
        "source_event",
    ):
        op.drop_table(table)
