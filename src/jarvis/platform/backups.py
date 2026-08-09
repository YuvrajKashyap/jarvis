import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


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
