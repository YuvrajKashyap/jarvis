from datetime import UTC, datetime
from uuid import UUID

import pytest

from jarvis.agency.capabilities import CapabilityContext
from jarvis.agency.memory import (
    RememberMemoryCapability,
    RememberMemoryInput,
    UndoRememberMemoryCapability,
    UndoRememberMemoryInput,
)
from jarvis.memory.store import MemoryRepository
from jarvis.platform.sqlite import SQLiteStore

NOW = datetime(2026, 8, 9, 20, 0, tzinfo=UTC)
SOURCE_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf0")
INVOCATION_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf1")


def repository(tmp_path) -> MemoryRepository:
    sqlite = SQLiteStore(tmp_path / "jarvis.db")
    sqlite.initialize()
    memory = MemoryRepository(sqlite=sqlite, markdown_directory=tmp_path / "Memory")
    memory.initialize()
    return memory


def context() -> CapabilityContext:
    return CapabilityContext(
        invocation_id=INVOCATION_ID,
        device_id="desktop",
        requested_at=NOW,
        source_event_id=SOURCE_ID,
    )


@pytest.mark.asyncio
async def test_remember_capability_uses_conversation_event_as_provenance(tmp_path) -> None:
    memory = repository(tmp_path)
    capability = RememberMemoryCapability(memory)

    result = await capability.execute(
        RememberMemoryInput(
            category="preference",
            subject="response style",
            content="Use concise, natural language.",
        ),
        context(),
    )

    assert result.kind == "created"
    assert result.undo_reference == f"memory-fact:{result.fact_id}"
    fact = memory.get(result.fact_id)
    assert fact is not None
    assert fact.source_event_ids == (SOURCE_ID,)


@pytest.mark.asyncio
async def test_conflicting_memory_is_queued_without_overwrite_and_can_be_undone(tmp_path) -> None:
    memory = repository(tmp_path)
    remember = RememberMemoryCapability(memory)
    undo = UndoRememberMemoryCapability(memory)
    original = await remember.execute(
        RememberMemoryInput(
            category="preference",
            subject="ambient buffer",
            content="Keep 120 seconds in RAM.",
        ),
        context(),
    )

    conflict = await remember.execute(
        RememberMemoryInput(
            category="preference",
            subject="ambient buffer",
            content="Keep 90 seconds in RAM.",
        ),
        context(),
    )

    assert conflict.kind == "conflict"
    assert conflict.undo_reference == f"memory-conflict:{conflict.conflict_id}"
    fact = memory.get(original.fact_id)
    assert fact is not None
    assert fact.content == "Keep 120 seconds in RAM."
    assert len(memory.list_conflicts()) == 1

    undone = await undo.execute(
        UndoRememberMemoryInput(undo_reference=conflict.undo_reference),
        context(),
    )

    assert undone.removed is True
    assert memory.list_conflicts() == []


@pytest.mark.asyncio
async def test_remembering_exact_existing_fact_is_a_noop(tmp_path) -> None:
    memory = repository(tmp_path)
    capability = RememberMemoryCapability(memory)
    request = RememberMemoryInput(
        category="person",
        subject="phone",
        content="Yuvraj uses an iPhone 17 Pro.",
    )

    created = await capability.execute(request, context())
    repeated = await capability.execute(request, context())

    assert repeated.kind == "existing"
    assert repeated.fact_id == created.fact_id
    assert repeated.undo_reference is None
    fact = memory.get(created.fact_id)
    assert fact is not None
    assert fact.version == 1
