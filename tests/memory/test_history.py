from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from jarvis.memory.history import (
    ConversationHistoryRepository,
    ConversationMessage,
    ConversationRole,
    DuplicateConversationMessage,
)
from jarvis.platform.sqlite import SQLiteStore

NOW = datetime(2026, 8, 7, 18, 30, tzinfo=UTC)
SESSION_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf0")
TURN_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf1")
MESSAGE_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf2")


def repository(tmp_path) -> ConversationHistoryRepository:
    sqlite = SQLiteStore(tmp_path / "jarvis.db")
    sqlite.initialize()
    return ConversationHistoryRepository(sqlite)


def message(
    *,
    message_id: UUID = MESSAGE_ID,
    role: ConversationRole = ConversationRole.USER,
    content: str = "What am I looking at?",
    created_at: datetime = NOW,
) -> ConversationMessage:
    return ConversationMessage(
        message_id=message_id,
        source_event_id=message_id,
        session_id=SESSION_ID,
        turn_id=TURN_ID,
        role=role,
        content=content,
        device_id="desktop",
        created_at=created_at,
    )


def test_history_is_durable_ordered_and_idempotent(tmp_path) -> None:
    history = repository(tmp_path)
    user = message()
    assistant = message(
        message_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf3"),
        role=ConversationRole.ASSISTANT,
        content="You are looking at the JARVIS project.",
        created_at=NOW + timedelta(seconds=1),
    )

    assert history.append(user) is True
    assert history.append(user) is False
    assert history.append(assistant) is True

    reopened = repository(tmp_path)
    assert reopened.recent(limit=10) == [user, assistant]
    assert reopened.recent(limit=1) == [assistant]
    assert reopened.recent(limit=10, session_id=SESSION_ID) == [user, assistant]


def test_message_ids_are_immutable(tmp_path) -> None:
    history = repository(tmp_path)
    history.append(message())

    with pytest.raises(DuplicateConversationMessage, match="immutable"):
        history.append(message(content="Different content"))


@pytest.mark.parametrize("limit", [0, 501])
def test_history_bounds_queries(tmp_path, limit: int) -> None:
    history = repository(tmp_path)

    with pytest.raises(ValueError, match="between 1 and 500"):
        history.recent(limit=limit)
