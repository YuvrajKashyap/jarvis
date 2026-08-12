from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from jarvis.memory.consolidation import ConversationConsolidator
from jarvis.memory.history import (
    ConversationHistoryRepository,
    ConversationMessage,
    ConversationRole,
)
from jarvis.memory.store import MemoryRepository
from jarvis.platform.sqlite import SQLiteStore

NOW = datetime(2026, 8, 11, 20, 0, tzinfo=UTC)


def message(role: ConversationRole, content: str, *, offset: int) -> ConversationMessage:
    message_id = uuid4()
    return ConversationMessage(
        message_id=message_id,
        source_event_id=message_id,
        session_id=uuid4(),
        turn_id=uuid4(),
        role=role,
        content=content,
        device_id="desktop",
        created_at=NOW + timedelta(seconds=offset),
    )


def setup(tmp_path: Path) -> tuple[ConversationHistoryRepository, MemoryRepository]:
    sqlite = SQLiteStore(tmp_path / "jarvis.db")
    sqlite.initialize()
    history = ConversationHistoryRepository(sqlite)
    memory = MemoryRepository(sqlite=sqlite, markdown_directory=tmp_path / "Memory")
    memory.initialize()
    return history, memory


async def test_idle_consolidation_extracts_explicit_user_facts_with_provenance(tmp_path) -> None:
    history, memory = setup(tmp_path)
    source = message(
        ConversationRole.USER,
        "I have an iPhone 17 Pro, and I prefer JARVIS to stay local and free.",
        offset=0,
    )
    history.append(source)
    history.append(
        message(
            ConversationRole.ASSISTANT,
            "Understood. I will keep the product local-first.",
            offset=1,
        )
    )
    consolidator = ConversationConsolidator(history=history, memory=memory)

    result = await consolidator.run_once()

    assert result.messages_processed == 2
    assert result.candidates_created == 2
    phone = memory.find(category="device", subject="phone")
    constraint = memory.find(category="preference", subject="JARVIS operating cost")
    assert phone is not None and phone.content == "Yuvraj has an iPhone 17 Pro."
    assert source.source_event_id in phone.source_event_ids
    assert constraint is not None and "local and free" in constraint.content
    assert history.unconsolidated(limit=10) == []


async def test_idle_consolidation_queues_corrections_as_conflicts(tmp_path) -> None:
    history, memory = setup(tmp_path)
    first = message(ConversationRole.USER, "I have an iPhone 16 Pro.", offset=0)
    history.append(first)
    await ConversationConsolidator(history=history, memory=memory).run_once()
    correction = message(
        ConversationRole.USER,
        "Correction: I have an iPhone 17 Pro.",
        offset=2,
    )
    history.append(correction)

    result = await ConversationConsolidator(history=history, memory=memory).run_once()

    assert result.conflicts_created == 1
    phone = memory.find(category="device", subject="phone")
    assert phone is not None and phone.content == "Yuvraj has an iPhone 16 Pro."
    assert memory.list_conflicts()[0].candidate_content == "Yuvraj has an iPhone 17 Pro."


async def test_idle_consolidation_does_not_treat_assistant_claims_as_user_facts(tmp_path) -> None:
    history, memory = setup(tmp_path)
    history.append(
        message(
            ConversationRole.ASSISTANT,
            "You own an RTX 5090 and prefer cloud APIs.",
            offset=0,
        )
    )

    result = await ConversationConsolidator(history=history, memory=memory).run_once()

    assert result.candidates_created == 0
    assert memory.count() == 0


async def test_idle_consolidation_covers_identity_people_projects_decisions_and_open_work(
    tmp_path,
) -> None:
    history, memory = setup(tmp_path)
    statements = (
        "My name is Yuvraj Kashyap.",
        "Aarav is my brother.",
        "I'm working on Project Atlas.",
        "We decided to use SQLite for JARVIS memory.",
        "I still need to pair my iPhone with JARVIS.",
        "I prefer concise answers.",
    )
    for offset, statement in enumerate(statements):
        history.append(message(ConversationRole.USER, statement, offset=offset))

    result = await ConversationConsolidator(history=history, memory=memory).run_once()

    assert result.candidates_created == 6
    identity = memory.find(category="identity", subject="name")
    person = memory.find(category="person", subject="Aarav")
    project = memory.find(category="project", subject="Project Atlas")
    preference = memory.find(category="preference", subject="concise answers")
    assert identity is not None and identity.content == "Yuvraj's name is Yuvraj Kashyap."
    assert person is not None and person.content == "Aarav is Yuvraj's brother."
    assert project is not None and project.content == "Yuvraj is working on Project Atlas."
    assert memory.search("SQLite JARVIS", limit=10)[0].category == "decision"
    assert memory.search("pair iPhone", limit=10)[0].category == "unfinished_work"
    assert preference is not None and preference.content == "Yuvraj prefers concise answers."
