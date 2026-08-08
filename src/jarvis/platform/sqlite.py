import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
from sqlalchemy import UniqueConstraint, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field as SqlField
from sqlmodel import Session, SQLModel, col, create_engine, select

from jarvis.agency.audit import ActionAudit
from jarvis.agency.policy import (
    Approval,
    ApprovalChoice,
    ApprovalConsumeResult,
    ApprovalStatus,
    PolicyDecisionKind,
    RiskClass,
)
from jarvis.platform.migration import upgrade_database


class EventSequenceConflict(RuntimeError):
    pass


class DuplicateAuditRecord(RuntimeError):
    pass


class SourceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    session_id: UUID
    turn_id: UUID
    sequence: int = Field(strict=True, ge=0)
    event_type: str = Field(min_length=1, max_length=160)
    payload: dict[str, JsonValue]
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a UTC offset")
        return value


class SQLiteHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    journal_mode: str
    foreign_keys: bool
    busy_timeout_ms: int


class SourceEventRow(SQLModel, table=True):
    __tablename__ = "source_event"
    __table_args__ = (UniqueConstraint("session_id", "sequence"),)

    event_id: str = SqlField(primary_key=True)
    session_id: str = SqlField(index=True)
    turn_id: str = SqlField(index=True)
    sequence: int
    event_type: str
    payload_json: str
    occurred_at: str


class ActionAuditRow(SQLModel, table=True):
    __tablename__ = "action_audit"

    action_id: str = SqlField(primary_key=True)
    invocation_id: str = SqlField(index=True)
    capability: str = SqlField(index=True)
    risk: str
    policy_decision: str
    approval_id: str | None = SqlField(default=None, index=True)
    result_status: str
    result_summary: str
    undo_reference: str | None = None
    recorded_at: str = SqlField(index=True)


class ApprovalRow(SQLModel, table=True):
    __tablename__ = "approval"

    approval_id: str = SqlField(primary_key=True)
    request_fingerprint: str = SqlField(index=True)
    expires_at: str = SqlField(index=True)
    status: str = SqlField(index=True)
    decision_device_id: str | None = None
    decided_at: str | None = None


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._write_lock = threading.RLock()
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        self._configure_connections(self.engine)

    @staticmethod
    def _configure_connections(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            upgrade_database(self.path)
        with self._write_lock, self.engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.exec_driver_sql("PRAGMA synchronous=FULL")

    def health(self) -> SQLiteHealth:
        with self.engine.connect() as connection:
            journal_mode = str(connection.exec_driver_sql("PRAGMA journal_mode").scalar_one())
            foreign_keys = bool(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one())
            busy_timeout_ms = int(connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one())
        return SQLiteHealth(
            journal_mode=journal_mode.lower(),
            foreign_keys=foreign_keys,
            busy_timeout_ms=busy_timeout_ms,
        )

    def create_backup(
        self,
        directory: Path,
        *,
        retain: int,
        now: datetime,
    ) -> Path:
        if retain < 1 or retain > 100:
            raise ValueError("backup retention must be between 1 and 100")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("backup time must include a UTC offset")
        destination_directory = directory.resolve()
        destination_directory.mkdir(parents=True, exist_ok=True)
        timestamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        destination = destination_directory / f"jarvis-{timestamp}.db"
        temporary = destination_directory / f".{destination.name}.tmp"

        with self._write_lock:
            source_connection = sqlite3.connect(self.path)
            destination_connection = sqlite3.connect(temporary)
            try:
                source_connection.backup(destination_connection)
            finally:
                destination_connection.close()
                source_connection.close()
            temporary.replace(destination)
            backups = sorted(destination_directory.glob("jarvis-*.db"))
            for obsolete in backups[:-retain]:
                obsolete.unlink()
        return destination

    def append_source_event(self, source_event: SourceEvent) -> bool:
        row = _source_event_to_row(source_event)
        with self._write_lock, Session(self.engine) as session:
            existing_id = session.get(SourceEventRow, row.event_id)
            if existing_id is not None:
                if _source_event_from_row(existing_id) == source_event:
                    return False
                raise EventSequenceConflict("event ID already exists with different content")

            existing_sequence = session.exec(
                select(SourceEventRow).where(
                    SourceEventRow.session_id == row.session_id,
                    SourceEventRow.sequence == row.sequence,
                )
            ).one_or_none()
            if existing_sequence is not None:
                raise EventSequenceConflict("session sequence already belongs to another event")

            session.add(row)
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise EventSequenceConflict("event conflicts with persisted ordering") from error
        return True

    def list_source_events(self, session_id: UUID) -> list[SourceEvent]:
        with Session(self.engine) as session:
            rows = session.exec(
                select(SourceEventRow)
                .where(SourceEventRow.session_id == str(session_id))
                .order_by(col(SourceEventRow.sequence))
            ).all()
            return [_source_event_from_row(row) for row in rows]

    def append_action_audit(self, audit: ActionAudit) -> None:
        with self._write_lock, Session(self.engine) as session:
            if session.get(ActionAuditRow, str(audit.action_id)) is not None:
                raise DuplicateAuditRecord("action audit IDs are immutable")
            session.add(_action_audit_to_row(audit))
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise DuplicateAuditRecord("action audit could not be appended") from error

    def list_action_audit(self) -> list[ActionAudit]:
        with Session(self.engine) as session:
            rows = session.exec(
                select(ActionAuditRow).order_by(col(ActionAuditRow.recorded_at))
            ).all()
            return [_action_audit_from_row(row) for row in rows]


class SQLiteApprovalStore:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def get(self, approval_id: UUID) -> Approval | None:
        with Session(self._store.engine) as session:
            row = session.get(ApprovalRow, str(approval_id))
            return None if row is None else _approval_from_row(row)

    def put(self, approval: Approval) -> None:
        with self._store._write_lock, Session(self._store.engine) as session:
            row = session.get(ApprovalRow, str(approval.approval_id))
            if row is None:
                session.add(_approval_to_row(approval))
            else:
                row.request_fingerprint = approval.request_fingerprint
                row.expires_at = _dump_datetime(approval.expires_at)
                row.status = approval.status.value
                row.decision_device_id = approval.decision_device_id
                row.decided_at = (
                    None if approval.decided_at is None else _dump_datetime(approval.decided_at)
                )
                session.add(row)
            session.commit()

    def decide(
        self,
        approval_id: UUID,
        choice: ApprovalChoice,
        *,
        device_id: str,
        decided_at: datetime,
    ) -> bool:
        with self._store._write_lock, Session(self._store.engine) as session:
            row = session.get(ApprovalRow, str(approval_id))
            if row is None or row.status != ApprovalStatus.PENDING.value:
                return False
            row.status = (
                ApprovalStatus.APPROVED.value
                if choice is ApprovalChoice.APPROVE
                else ApprovalStatus.REJECTED.value
            )
            row.decision_device_id = device_id
            row.decided_at = _dump_datetime(decided_at)
            session.add(row)
            session.commit()
            return True

    def consume(
        self,
        approval_id: UUID,
        request_fingerprint: str,
        *,
        now: datetime,
    ) -> ApprovalConsumeResult:
        with self._store._write_lock, Session(self._store.engine) as session:
            row = session.get(ApprovalRow, str(approval_id))
            if row is None:
                return ApprovalConsumeResult.UNKNOWN
            if row.request_fingerprint != request_fingerprint:
                return ApprovalConsumeResult.MISMATCH
            if now > _load_datetime(row.expires_at):
                return ApprovalConsumeResult.EXPIRED
            status = ApprovalStatus(row.status)
            if status is ApprovalStatus.REJECTED:
                return ApprovalConsumeResult.REJECTED
            if status is ApprovalStatus.CONSUMED:
                return ApprovalConsumeResult.REPLAYED
            if status is ApprovalStatus.PENDING:
                return ApprovalConsumeResult.NOT_GRANTED
            row.status = ApprovalStatus.CONSUMED.value
            session.add(row)
            session.commit()
            return ApprovalConsumeResult.CONSUMED


def _dump_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _dump_json(value: dict[str, JsonValue]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _source_event_to_row(source_event: SourceEvent) -> SourceEventRow:
    return SourceEventRow(
        event_id=str(source_event.event_id),
        session_id=str(source_event.session_id),
        turn_id=str(source_event.turn_id),
        sequence=source_event.sequence,
        event_type=source_event.event_type,
        payload_json=_dump_json(source_event.payload),
        occurred_at=_dump_datetime(source_event.occurred_at),
    )


def _source_event_from_row(row: SourceEventRow) -> SourceEvent:
    return SourceEvent(
        event_id=UUID(row.event_id),
        session_id=UUID(row.session_id),
        turn_id=UUID(row.turn_id),
        sequence=row.sequence,
        event_type=row.event_type,
        payload=json.loads(row.payload_json),
        occurred_at=_load_datetime(row.occurred_at),
    )


def _action_audit_to_row(audit: ActionAudit) -> ActionAuditRow:
    return ActionAuditRow(
        action_id=str(audit.action_id),
        invocation_id=str(audit.invocation_id),
        capability=audit.capability,
        risk=audit.risk.value,
        policy_decision=audit.policy_decision.value,
        approval_id=None if audit.approval_id is None else str(audit.approval_id),
        result_status=audit.result_status,
        result_summary=audit.result_summary,
        undo_reference=audit.undo_reference,
        recorded_at=_dump_datetime(audit.recorded_at),
    )


def _action_audit_from_row(row: ActionAuditRow) -> ActionAudit:
    return ActionAudit(
        action_id=UUID(row.action_id),
        invocation_id=UUID(row.invocation_id),
        capability=row.capability,
        risk=RiskClass(row.risk),
        policy_decision=PolicyDecisionKind(row.policy_decision),
        approval_id=None if row.approval_id is None else UUID(row.approval_id),
        result_status=row.result_status,
        result_summary=row.result_summary,
        undo_reference=row.undo_reference,
        recorded_at=_load_datetime(row.recorded_at),
    )


def _approval_to_row(approval: Approval) -> ApprovalRow:
    return ApprovalRow(
        approval_id=str(approval.approval_id),
        request_fingerprint=approval.request_fingerprint,
        expires_at=_dump_datetime(approval.expires_at),
        status=approval.status.value,
        decision_device_id=approval.decision_device_id,
        decided_at=None if approval.decided_at is None else _dump_datetime(approval.decided_at),
    )


def _approval_from_row(row: ApprovalRow) -> Approval:
    return Approval(
        approval_id=UUID(row.approval_id),
        request_fingerprint=row.request_fingerprint,
        expires_at=_load_datetime(row.expires_at),
        status=ApprovalStatus(row.status),
        decision_device_id=row.decision_device_id,
        decided_at=None if row.decided_at is None else _load_datetime(row.decided_at),
    )
