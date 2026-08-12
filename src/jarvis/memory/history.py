from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import UniqueConstraint, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field as SqlField
from sqlmodel import Session, SQLModel, col, select

from jarvis.platform.sqlite import SQLiteStore

CONSOLIDATION_VERSION = 2


class DuplicateConversationMessage(RuntimeError):
    pass


class ConversationRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    AMBIENT = "ambient"


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: UUID
    source_event_id: UUID
    session_id: UUID
    turn_id: UUID
    role: ConversationRole
    content: str = Field(min_length=1, max_length=128_000)
    device_id: str = Field(min_length=1, max_length=128)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a UTC offset")
        return value


class ConversationHistory(Protocol):
    def append(self, message: ConversationMessage) -> bool: ...

    def recent(
        self,
        *,
        limit: int,
        session_id: UUID | None = None,
    ) -> list[ConversationMessage]: ...

    def unconsolidated(self, *, limit: int) -> list[ConversationMessage]: ...

    def mark_consolidated(self, message_ids: tuple[UUID, ...]) -> None: ...


class ConversationMessageRow(SQLModel, table=True):
    __tablename__ = "conversation_message"
    __table_args__ = (UniqueConstraint("source_event_id"),)

    message_id: str = SqlField(primary_key=True)
    source_event_id: str
    session_id: str = SqlField(index=True)
    turn_id: str = SqlField(index=True)
    role: str
    content: str
    device_id: str
    created_at: str = SqlField(index=True)
    consolidation_version: int | None = SqlField(default=None, index=True)


class ConversationHistoryRepository:
    """Append-only durable transcript storage for intentional JARVIS turns."""

    def __init__(self, sqlite: SQLiteStore) -> None:
        self._sqlite = sqlite

    def append(self, message: ConversationMessage) -> bool:
        row = _to_row(message)
        with self._sqlite._write_lock, Session(self._sqlite.engine) as session:
            existing = session.get(ConversationMessageRow, row.message_id)
            if existing is not None:
                if _from_row(existing) == message:
                    return False
                raise DuplicateConversationMessage("conversation message IDs are immutable")
            session.add(row)
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise DuplicateConversationMessage(
                    "conversation source event IDs are immutable"
                ) from error
        return True

    def recent(
        self,
        *,
        limit: int,
        session_id: UUID | None = None,
    ) -> list[ConversationMessage]:
        if limit < 1 or limit > 500:
            raise ValueError("history limit must be between 1 and 500")
        with Session(self._sqlite.engine) as session:
            statement = select(ConversationMessageRow)
            if session_id is not None:
                statement = statement.where(ConversationMessageRow.session_id == str(session_id))
            rows = session.exec(
                statement.order_by(col(ConversationMessageRow.created_at).desc()).limit(limit)
            ).all()
        return [_from_row(row) for row in reversed(rows)]

    def unconsolidated(self, *, limit: int) -> list[ConversationMessage]:
        if limit < 1 or limit > 500:
            raise ValueError("history limit must be between 1 and 500")
        with Session(self._sqlite.engine) as session:
            rows = session.exec(
                select(ConversationMessageRow)
                .where(
                    (col(ConversationMessageRow.consolidation_version).is_(None))
                    | (col(ConversationMessageRow.consolidation_version) < CONSOLIDATION_VERSION)
                )
                .order_by(col(ConversationMessageRow.created_at))
                .limit(limit)
            ).all()
        return [_from_row(row) for row in rows]

    def mark_consolidated(self, message_ids: tuple[UUID, ...]) -> None:
        if not message_ids:
            return
        if len(message_ids) > 500 or len(set(message_ids)) != len(message_ids):
            raise ValueError("consolidation batch must contain unique bounded message IDs")
        values = tuple(str(message_id) for message_id in message_ids)
        with self._sqlite._write_lock, Session(self._sqlite.engine) as session:
            existing = session.exec(
                select(ConversationMessageRow.message_id).where(
                    col(ConversationMessageRow.message_id).in_(values)
                )
            ).all()
            if len(existing) != len(values):
                raise LookupError("conversation message not found")
            session.exec(
                update(ConversationMessageRow)
                .where(col(ConversationMessageRow.message_id).in_(values))
                .values(consolidation_version=CONSOLIDATION_VERSION)
            )
            session.commit()


def _to_row(message: ConversationMessage) -> ConversationMessageRow:
    return ConversationMessageRow(
        message_id=str(message.message_id),
        source_event_id=str(message.source_event_id),
        session_id=str(message.session_id),
        turn_id=str(message.turn_id),
        role=message.role.value,
        content=message.content,
        device_id=message.device_id,
        created_at=_dump_datetime(message.created_at),
        consolidation_version=None,
    )


def _from_row(row: ConversationMessageRow) -> ConversationMessage:
    return ConversationMessage(
        message_id=UUID(row.message_id),
        source_event_id=UUID(row.source_event_id),
        session_id=UUID(row.session_id),
        turn_id=UUID(row.turn_id),
        role=ConversationRole(row.role),
        content=row.content,
        device_id=row.device_id,
        created_at=_load_datetime(row.created_at),
    )


def _dump_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
