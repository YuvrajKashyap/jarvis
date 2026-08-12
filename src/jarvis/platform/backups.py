import asyncio
import contextlib
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class RestoreResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    destination: Path
    rollback_backup: Path | None


class SQLiteRecovery:
    """Validates and atomically restores a stopped JARVIS SQLite database."""

    def restore(
        self,
        backup: Path,
        *,
        destination: Path,
        rollback_directory: Path,
        now: datetime,
    ) -> RestoreResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("recovery time must include a UTC offset")
        try:
            source = backup.resolve(strict=True)
        except OSError as error:
            raise FileNotFoundError("backup was not found") from error
        if not source.is_file() or source.is_symlink():
            raise ValueError("backup must be a regular file")
        target = destination.resolve()
        if source == target:
            raise ValueError("backup and destination must be different files")
        target.parent.mkdir(parents=True, exist_ok=True)
        rollback_root = rollback_directory.resolve()
        rollback_root.mkdir(parents=True, exist_ok=True)
        self._validate(source)

        timestamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        rollback = None
        if target.exists():
            rollback = rollback_root / f"jarvis-pre-restore-{timestamp}.db"
            self._copy_database(target, rollback)

        temporary = target.parent / f".jarvis-restore-{uuid4().hex}.tmp"
        try:
            self._copy_database(source, temporary)
            self._validate(temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return RestoreResult(destination=target, rollback_backup=rollback)

    @staticmethod
    def _validate(path: Path) -> None:
        try:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            try:
                result = connection.execute("PRAGMA quick_check").fetchone()
                version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise ValueError("backup is not a valid SQLite database") from error
        if result != ("ok",) or version is None or not isinstance(version[0], str):
            raise ValueError("backup is not a valid SQLite database")

    @staticmethod
    def _copy_database(source: Path, destination: Path) -> None:
        source_connection = sqlite3.connect(source)
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
            source_connection.close()


class BackupStore(Protocol):
    def create_backup(
        self,
        directory: Path,
        *,
        retain: int,
        now: datetime,
    ) -> Path: ...


class SQLiteBackupService:
    """Creates one crash-consistent backup at startup and on a bounded interval."""

    def __init__(
        self,
        *,
        store: BackupStore,
        directory: Path,
        retain: int = 7,
        interval_seconds: float = 86_400,
    ) -> None:
        if retain < 1 or retain > 100:
            raise ValueError("backup retention must be between 1 and 100")
        if interval_seconds < 60 or interval_seconds > 604_800:
            raise ValueError("backup interval must be between one minute and seven days")
        self._store = store
        self._directory = directory.resolve()
        self._retain = retain
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._task is not None:
                return
            await self._backup()
            self._task = asyncio.create_task(self._run(), name="jarvis-sqlite-backups")

    async def stop(self) -> None:
        async with self._lock:
            task = self._task
            self._task = None
            if task is None:
                return
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            await self._backup()

    async def _backup(self) -> None:
        await asyncio.to_thread(
            self._store.create_backup,
            self._directory,
            retain=self._retain,
            now=datetime.now(UTC),
        )
