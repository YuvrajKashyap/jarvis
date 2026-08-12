from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from jarvis.platform.backups import SQLiteBackupService, SQLiteRecovery
from jarvis.platform.sqlite import SourceEvent, SQLiteStore


class FakeBackupStore:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, int, datetime]] = []

    def create_backup(self, directory: Path, *, retain: int, now: datetime) -> Path:
        self.calls.append((directory, retain, now))
        return directory / "jarvis.db"


async def test_backup_service_creates_rotating_backup_at_start_and_stops_cleanly(tmp_path) -> None:
    store = FakeBackupStore()
    service = SQLiteBackupService(
        store=store,
        directory=tmp_path / "backups",
        retain=7,
        interval_seconds=86_400,
    )

    await service.start()
    await service.stop()

    assert len(store.calls) == 1
    directory, retain, created_at = store.calls[0]
    assert directory == (tmp_path / "backups").resolve()
    assert retain == 7
    assert created_at.tzinfo is UTC


async def test_backup_service_start_and_stop_are_idempotent(tmp_path) -> None:
    store = FakeBackupStore()
    service = SQLiteBackupService(store=store, directory=tmp_path, interval_seconds=86_400)

    await service.start()
    await service.start()
    await service.stop()
    await service.stop()

    assert len(store.calls) == 1


def test_recovery_restores_a_valid_backup_and_preserves_a_rollback_copy(tmp_path: Path) -> None:
    database_path = tmp_path / "jarvis.db"
    store = SQLiteStore(database_path)
    store.initialize()
    original = SourceEvent(
        event_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf0"),
        session_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf1"),
        turn_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf2"),
        sequence=0,
        event_type="user_input",
        payload={"text": "before"},
        occurred_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    later = original.model_copy(
        update={
            "event_id": UUID("019fd977-1d96-7892-950c-6afbb71f7cf3"),
            "sequence": 1,
            "payload": {"text": "after"},
        }
    )
    store.append_source_event(original)
    backup = store.create_backup(tmp_path / "backups", retain=2, now=datetime.now(UTC))
    store.append_source_event(later)
    store.close()

    result = SQLiteRecovery().restore(
        backup,
        destination=database_path,
        rollback_directory=tmp_path / "rollback",
        now=datetime.now(UTC),
    )

    restored = SQLiteStore(database_path)
    restored.initialize()
    assert restored.list_source_events(original.session_id) == [original]
    restored.close()
    assert result.rollback_backup is not None and result.rollback_backup.is_file()


def test_recovery_rejects_a_corrupt_backup_without_changing_the_database(tmp_path: Path) -> None:
    database_path = tmp_path / "jarvis.db"
    store = SQLiteStore(database_path)
    store.initialize()
    store.close()
    original = database_path.read_bytes()
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("not sqlite", encoding="utf-8")

    with pytest.raises(ValueError, match="valid SQLite"):
        SQLiteRecovery().restore(
            corrupt,
            destination=database_path,
            rollback_directory=tmp_path / "rollback",
            now=datetime.now(UTC),
        )

    assert database_path.read_bytes() == original
