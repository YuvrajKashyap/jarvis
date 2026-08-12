from datetime import UTC, datetime
from uuid import UUID

import pytest

from jarvis.memory.store import MemoryCandidate, MemoryRepository
from jarvis.platform.sqlite import SQLiteStore

NOW = datetime(2026, 8, 7, 18, 30, tzinfo=UTC)
SOURCE_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf0")


def repository(tmp_path) -> MemoryRepository:
    sqlite = SQLiteStore(tmp_path / "jarvis.db")
    sqlite.initialize()
    memory = MemoryRepository(sqlite=sqlite, markdown_directory=tmp_path / "Memory")
    memory.initialize()
    return memory


def test_remember_persists_source_grounded_fact_and_editable_markdown(tmp_path) -> None:
    memory = repository(tmp_path)
    assert memory.count() == 0
    candidate = MemoryCandidate(
        category="preference",
        subject="response style",
        content="Use concise, natural language and avoid generic filler.",
        source_event_ids=(SOURCE_ID,),
        observed_at=NOW,
    )

    result = memory.remember(candidate)

    assert result.kind == "created"
    assert memory.count() == 1
    remembered = memory.get(result.fact_id)
    assert remembered is not None
    assert remembered.content == candidate.content
    markdown = (tmp_path / "Memory" / "memory.md").read_text(encoding="utf-8")
    assert "Use concise, natural language" in markdown
    assert str(SOURCE_ID) in markdown


def test_conflicting_candidate_is_queued_without_overwriting_existing_fact(tmp_path) -> None:
    memory = repository(tmp_path)
    original = memory.remember(
        MemoryCandidate(
            category="preference",
            subject="ambient buffer",
            content="Keep 120 seconds in RAM.",
            source_event_ids=(SOURCE_ID,),
            observed_at=NOW,
        )
    )

    conflict = memory.remember(
        MemoryCandidate(
            category="preference",
            subject="ambient buffer",
            content="Keep 90 seconds in RAM.",
            source_event_ids=(UUID("019fd977-1d96-7892-950c-6afbb71f7cf1"),),
            observed_at=NOW,
        )
    )

    assert conflict.kind == "conflict"
    assert conflict.fact_id == original.fact_id
    remembered = memory.get(original.fact_id)
    assert remembered is not None
    assert remembered.content == "Keep 120 seconds in RAM."
    assert memory.list_conflicts()[0].candidate_content == "Keep 90 seconds in RAM."


def test_conflict_can_be_accepted_or_rejected_without_losing_provenance(tmp_path) -> None:
    memory = repository(tmp_path)
    original = memory.remember(
        MemoryCandidate(
            category="preference",
            subject="ambient buffer",
            content="Keep 120 seconds in RAM.",
            source_event_ids=(SOURCE_ID,),
            observed_at=NOW,
        )
    )
    accepted_source = UUID("019fd977-1d96-7892-950c-6afbb71f7cf1")
    accepted = memory.remember(
        MemoryCandidate(
            category="preference",
            subject="ambient buffer",
            content="Keep 90 seconds in RAM.",
            source_event_ids=(accepted_source,),
            observed_at=NOW,
        )
    )
    rejected = memory.remember(
        MemoryCandidate(
            category="preference",
            subject="ambient buffer",
            content="Persist the ambient microphone forever.",
            source_event_ids=(UUID("019fd977-1d96-7892-950c-6afbb71f7cf2"),),
            observed_at=NOW,
        )
    )

    assert accepted.conflict_id is not None
    resolved = memory.resolve_conflict(accepted.conflict_id, accept=True, resolved_at=NOW)
    assert resolved is not None
    assert resolved.fact_id == original.fact_id
    assert resolved.content == "Keep 90 seconds in RAM."
    assert resolved.source_event_ids == (SOURCE_ID, accepted_source)
    assert resolved.version == 2

    assert rejected.conflict_id is not None
    assert memory.resolve_conflict(rejected.conflict_id, accept=False, resolved_at=NOW) is None
    assert memory.list_conflicts() == []


def test_correction_reindexes_search_and_forgetting_removes_canonical_content(tmp_path) -> None:
    memory = repository(tmp_path)
    result = memory.remember(
        MemoryCandidate(
            category="project",
            subject="phone",
            content="Yuvraj uses an iPhone 16 Pro.",
            source_event_ids=(SOURCE_ID,),
            observed_at=NOW,
        )
    )

    corrected = memory.correct(
        result.fact_id,
        content="Yuvraj uses an iPhone 17 Pro.",
        source_event_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf2"),
        corrected_at=NOW,
    )

    assert corrected.version == 2
    assert [fact.fact_id for fact in memory.search("iPhone 17")] == [result.fact_id]
    assert memory.search("iPhone 16") == []

    memory.forget(result.fact_id, forgotten_at=NOW)

    assert memory.get(result.fact_id) is None
    assert memory.search("iPhone") == []
    markdown = (tmp_path / "Memory" / "memory.md").read_text(encoding="utf-8")
    assert "iPhone 16" not in markdown
    assert "iPhone 17" not in markdown
    deletion = memory.list_deletions()[0]
    assert deletion.fact_id == result.fact_id
    assert not hasattr(deletion, "content")


def test_manual_markdown_edits_are_staged_as_conflicts_before_becoming_canonical(tmp_path) -> None:
    memory = repository(tmp_path)
    mutation = memory.remember(
        MemoryCandidate(
            category="hardware",
            subject="phone",
            content="Yuvraj uses an iPhone 16 Pro.",
            source_event_ids=(SOURCE_ID,),
            observed_at=NOW,
        )
    )
    markdown_path = tmp_path / "Memory" / "memory.md"
    edited = markdown_path.read_text(encoding="utf-8").replace(
        "Yuvraj uses an iPhone 16 Pro.", "Yuvraj uses an iPhone 17 Pro."
    )
    markdown_path.write_text(edited, encoding="utf-8")
    edit_source = UUID("019fd977-1d96-7892-950c-6afbb71f7cf3")

    staged = memory.stage_markdown_edits(source_event_id=edit_source, observed_at=NOW)

    assert len(staged) == 1
    assert staged[0].candidate_content == "Yuvraj uses an iPhone 17 Pro."
    before = memory.get(mutation.fact_id)
    assert before is not None
    assert before.content == "Yuvraj uses an iPhone 16 Pro."
    memory.resolve_conflict(staged[0].conflict_id, accept=True, resolved_at=NOW)
    after = memory.get(mutation.fact_id)
    assert after is not None
    assert after.content == "Yuvraj uses an iPhone 17 Pro."


def test_manual_markdown_import_rejects_stale_or_unknown_fact_metadata(tmp_path) -> None:
    memory = repository(tmp_path)
    memory.remember(
        MemoryCandidate(
            category="hardware",
            subject="phone",
            content="Yuvraj uses an iPhone 17 Pro.",
            source_event_ids=(SOURCE_ID,),
            observed_at=NOW,
        )
    )
    markdown_path = tmp_path / "Memory" / "memory.md"
    edited = markdown_path.read_text(encoding="utf-8").replace("Version 1", "Version 99")
    markdown_path.write_text(edited, encoding="utf-8")

    with pytest.raises(ValueError, match="stale"):
        memory.stage_markdown_edits(source_event_id=SOURCE_ID, observed_at=NOW)

    assert memory.list_conflicts() == []
