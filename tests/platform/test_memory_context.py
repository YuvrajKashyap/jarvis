from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from jarvis.memory.history import (
    ConversationHistoryRepository,
    ConversationMessage,
    ConversationRole,
)
from jarvis.memory.store import MemoryCandidate, MemoryRepository
from jarvis.platform.memory_context import LocalMemoryContext
from jarvis.platform.sqlite import SQLiteStore

NOW = datetime(2026, 8, 7, 18, 30, tzinfo=UTC)
SESSION_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf0")
TURN_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf1")


@pytest.mark.asyncio
async def test_context_combines_bounded_history_with_source_grounded_memory(tmp_path) -> None:
    sqlite = SQLiteStore(tmp_path / "jarvis.db")
    sqlite.initialize()
    history = ConversationHistoryRepository(sqlite)
    facts = MemoryRepository(sqlite=sqlite, markdown_directory=tmp_path / "Memory")
    facts.initialize()
    history.append(
        ConversationMessage(
            message_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf2"),
            source_event_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf2"),
            session_id=SESSION_ID,
            turn_id=TURN_ID,
            role=ConversationRole.USER,
            content="Which phone do I use?",
            device_id="desktop",
            created_at=NOW,
        )
    )
    history.append(
        ConversationMessage(
            message_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf3"),
            source_event_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf3"),
            session_id=SESSION_ID,
            turn_id=TURN_ID,
            role=ConversationRole.ASSISTANT,
            content="You use an iPhone 17 Pro.",
            device_id="desktop",
            created_at=NOW + timedelta(seconds=1),
        )
    )
    source_id = UUID("019fd977-1d96-7892-950c-6afbb71f7cf4")
    facts.remember(
        MemoryCandidate(
            category="hardware",
            subject="iPhone",
            content="Yuvraj uses an iPhone 17 Pro.",
            source_event_ids=(source_id,),
            observed_at=NOW,
        )
    )
    context = LocalMemoryContext(history=history, memory=facts)

    messages = await context.context_for("iPhone")

    assert [(message.role, message.content) for message in messages[1:]] == [
        ("user", "Which phone do I use?"),
        ("assistant", "You use an iPhone 17 Pro."),
    ]
    assert messages[0].role == "system"
    assert "Retrieved local memory" in messages[0].content
    assert "Yuvraj uses an iPhone 17 Pro." in messages[0].content
    assert str(source_id) in messages[0].content
