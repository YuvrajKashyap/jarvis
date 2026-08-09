from datetime import UTC, datetime
from pathlib import Path

from jarvis.platform.backups import SQLiteBackupService


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
