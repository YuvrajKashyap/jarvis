from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import inspect

from jarvis.agency.audit import ActionAudit
from jarvis.agency.policy import (
    ApprovalChoice,
    AuthorizationContext,
    CapabilityRequest,
    PolicyDecisionKind,
    PolicyEngine,
    RiskClass,
)
from jarvis.platform.sqlite import (
    DuplicateAuditRecord,
    EventSequenceConflict,
    SourceEvent,
    SQLiteApprovalStore,
    SQLiteStore,
)


def test_store_close_releases_database_file(tmp_path: Path) -> None:
    database_path = tmp_path / "jarvis.db"
    store = SQLiteStore(database_path)
    store.initialize()

    store.close()
    database_path.unlink()

    assert database_path.exists() is False


NOW = datetime(2026, 8, 7, 18, 30, tzinfo=UTC)
SESSION_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf0")
TURN_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf1")


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    database = SQLiteStore(tmp_path / "jarvis.db")
    database.initialize()
    return database


def source_event(*, event_id: UUID, sequence: int) -> SourceEvent:
    return SourceEvent(
        event_id=event_id,
        session_id=SESSION_ID,
        turn_id=TURN_ID,
        sequence=sequence,
        event_type="submit_text",
        payload={"text": "remember this"},
        occurred_at=NOW,
    )


def test_sqlite_initializes_with_wal_foreign_keys_and_bounded_wait(store: SQLiteStore) -> None:
    health = store.health()

    assert health.journal_mode == "wal"
    assert health.foreign_keys is True
    assert health.busy_timeout_ms == 5_000
    assert "alembic_version" in inspect(store.engine).get_table_names()


def test_source_event_append_is_idempotent_but_sequence_conflicts_are_rejected(
    store: SQLiteStore,
) -> None:
    first = source_event(
        event_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf2"),
        sequence=1,
    )
    conflicting = source_event(
        event_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf3"),
        sequence=1,
    )

    assert store.append_source_event(first) is True
    assert store.append_source_event(first) is False
    with pytest.raises(EventSequenceConflict):
        store.append_source_event(conflicting)

    assert store.list_source_events(SESSION_ID) == [first]


def test_action_audit_is_append_only_and_duplicate_ids_cannot_replace_evidence(
    store: SQLiteStore,
) -> None:
    audit = ActionAudit(
        action_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf4"),
        invocation_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf5"),
        capability="files.rename",
        risk=RiskClass.LOCAL_REVERSIBLE,
        policy_decision=PolicyDecisionKind.ALLOW,
        approval_id=None,
        result_status="succeeded",
        result_summary="Renamed draft.txt to final.txt",
        undo_reference="rename:final.txt:draft.txt",
        recorded_at=NOW,
    )

    store.append_action_audit(audit)
    with pytest.raises(DuplicateAuditRecord):
        store.append_action_audit(
            audit.model_copy(update={"result_summary": "fabricated replacement"})
        )

    assert store.list_action_audit() == [audit]


def test_approval_evidence_survives_restart_and_remains_one_time(tmp_path) -> None:
    database_path = tmp_path / "jarvis.db"
    first_store = SQLiteStore(database_path)
    first_store.initialize()
    request = CapabilityRequest(
        invocation_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf5"),
        capability="messages.send",
        risk=RiskClass.EXTERNAL_IRREVERSIBLE,
        arguments={"recipient": "test@example.com", "body": "hello"},
    )
    first_engine = PolicyEngine(SQLiteApprovalStore(first_store))
    approval = first_engine.request_approval(request, expires_at=NOW + timedelta(minutes=1))
    first_engine.record_decision(
        approval.approval_id,
        ApprovalChoice.APPROVE,
        device_id="desktop",
        decided_at=NOW,
    )

    restarted_store = SQLiteStore(database_path)
    restarted_store.initialize()
    restarted_engine = PolicyEngine(SQLiteApprovalStore(restarted_store))
    allowed = restarted_engine.evaluate(
        request,
        AuthorizationContext(approval_id=approval.approval_id),
        now=NOW,
    )
    replayed = restarted_engine.evaluate(
        request,
        AuthorizationContext(approval_id=approval.approval_id),
        now=NOW,
    )

    assert allowed.kind is PolicyDecisionKind.ALLOW
    assert replayed.kind is PolicyDecisionKind.DENY
    assert replayed.reason == "approval_replayed"


def test_sqlite_approval_consumption_is_atomic(tmp_path) -> None:
    database = SQLiteStore(tmp_path / "jarvis.db")
    database.initialize()
    engine = PolicyEngine(SQLiteApprovalStore(database))
    capability = CapabilityRequest(
        invocation_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf5"),
        capability="messages.send",
        risk=RiskClass.EXTERNAL_IRREVERSIBLE,
        arguments={"recipient": "test@example.com", "body": "hello"},
    )
    pending = engine.request_approval(capability, expires_at=NOW + timedelta(minutes=5))
    engine.record_decision(
        pending.approval_id,
        ApprovalChoice.APPROVE,
        device_id="desktop",
        decided_at=NOW,
    )

    def evaluate() -> PolicyDecisionKind:
        return engine.evaluate(
            capability,
            AuthorizationContext(approval_id=pending.approval_id),
            now=NOW,
        ).kind

    with ThreadPoolExecutor(max_workers=8) as executor:
        decisions = list(executor.map(lambda _index: evaluate(), range(32)))

    assert decisions.count(PolicyDecisionKind.ALLOW) == 1


def test_online_backup_is_consistent_and_rotates_oldest_files(tmp_path) -> None:
    database = SQLiteStore(tmp_path / "jarvis.db")
    database.initialize()
    event = source_event(
        event_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf9"),
        sequence=0,
    )
    database.append_source_event(event)
    backup_directory = tmp_path / "backups"

    first = database.create_backup(
        backup_directory,
        retain=2,
        now=NOW,
    )
    database.create_backup(
        backup_directory,
        retain=2,
        now=NOW + timedelta(seconds=1),
    )
    last = database.create_backup(
        backup_directory,
        retain=2,
        now=NOW + timedelta(seconds=2),
    )

    assert not first.exists()
    assert last.exists()
    assert len(list(backup_directory.glob("jarvis-*.db"))) == 2
    restored = SQLiteStore(last)
    restored.initialize()
    assert restored.list_source_events(SESSION_ID) == [event]
