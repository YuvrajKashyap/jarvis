from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from jarvis.platform.acceptance import LocalAcceptanceEvidence
from jarvis.platform.backups import SQLiteRecovery
from jarvis.platform.sqlite import SourceEvent, SQLiteStore

ROOT = Path(__file__).resolve().parents[1]


def run_recovery_acceptance(root: Path) -> dict[str, bool]:
    root.mkdir(parents=True, exist_ok=True)
    database_path = root / "jarvis.db"
    session_id = UUID("019fd977-1d96-7892-950c-6afbb71f7cf1")
    original = _event(
        event_id="019fd977-1d96-7892-950c-6afbb71f7cf0",
        session_id=session_id,
        sequence=0,
        text="before backup",
    )
    later = _event(
        event_id="019fd977-1d96-7892-950c-6afbb71f7cf3",
        session_id=session_id,
        sequence=1,
        text="after backup",
    )

    store = SQLiteStore(database_path)
    store.initialize()
    migration_ok = store.health().journal_mode == "wal"
    store.append_source_event(original)
    backup = store.create_backup(root / "backups", retain=2, now=datetime.now(UTC))
    store.append_source_event(later)
    store.close()

    recovery = SQLiteRecovery()
    restored = recovery.restore(
        backup,
        destination=database_path,
        rollback_directory=root / "rollback",
        now=datetime.now(UTC),
    )
    restored_store = SQLiteStore(database_path)
    restored_store.initialize()
    restore_ok = restored_store.list_source_events(session_id) == [original]
    restored_store.close()

    if restored.rollback_backup is None:
        raise RuntimeError("recovery did not preserve a rollback database")
    rollback_store = SQLiteStore(restored.rollback_backup)
    rollback_store.initialize()
    rollback_ok = rollback_store.list_source_events(session_id) == [original, later]
    rollback_store.close()

    before_corrupt_attempt = database_path.read_bytes()
    corrupt = root / "corrupt.db"
    corrupt.write_text("not sqlite", encoding="utf-8")
    corruption_rejected = False
    try:
        recovery.restore(
            corrupt,
            destination=database_path,
            rollback_directory=root / "rollback",
            now=datetime.now(UTC),
        )
    except ValueError:
        corruption_rejected = database_path.read_bytes() == before_corrupt_attempt

    result = {
        "migration": migration_ok,
        "backup": backup.is_file(),
        "restore": restore_ok,
        "rollback": rollback_ok,
        "corruption_rejected": corruption_rejected,
    }
    if not all(result.values()):
        raise RuntimeError(f"recovery acceptance failed: {result}")
    return result


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="jarvis-recovery-") as directory:
        result = run_recovery_acceptance(Path(directory))
    artifact_directory = ROOT / "artifacts" / "acceptance"
    artifact_directory.mkdir(parents=True, exist_ok=True)
    path = artifact_directory / "recovery-core.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    LocalAcceptanceEvidence(_data_directory() / "acceptance").record_pass("recovery")
    print(path)


def _event(*, event_id: str, session_id: UUID, sequence: int, text: str) -> SourceEvent:
    return SourceEvent(
        event_id=UUID(event_id),
        session_id=session_id,
        turn_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf2"),
        sequence=sequence,
        event_type="user_input",
        payload={"text": text},
        occurred_at=datetime(2026, 8, 11, tzinfo=UTC),
    )


def _data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base / "JARVIS"


if __name__ == "__main__":
    main()
