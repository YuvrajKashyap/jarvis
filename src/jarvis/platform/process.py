import asyncio
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path

import psutil

from jarvis.agency.terminal import TerminalCommand, TerminalResult


class _OutputBudget:
    def __init__(self, limit: int) -> None:
        self.remaining = limit
        self.truncated = False

    def take(self, chunk: bytes) -> bytes:
        kept = chunk[: self.remaining]
        self.remaining -= len(kept)
        if len(kept) != len(chunk):
            self.truncated = True
        return kept


class LocalCommandRunner:
    """Runs shell-free commands with path, time, and output confinement."""

    def __init__(
        self,
        *,
        roots: Sequence[Path],
        executables: Mapping[str, Path],
    ) -> None:
        if not roots:
            raise ValueError("at least one terminal root is required")
        self._roots = tuple(root.resolve(strict=True) for root in roots)
        self._executables = {name: path.resolve(strict=True) for name, path in executables.items()}

    @classmethod
    def from_path(cls, *, roots: Sequence[Path], names: Sequence[str]) -> "LocalCommandRunner":
        executables: dict[str, Path] = {}
        for name in names:
            resolved = shutil.which(name)
            if resolved is not None:
                executables[name] = Path(resolved)
        return cls(roots=roots, executables=executables)

    async def run(self, command: TerminalCommand) -> TerminalResult:
        cwd = command.cwd.resolve(strict=True)
        if not cwd.is_dir() or not any(cwd.is_relative_to(root) for root in self._roots):
            raise ValueError("terminal working directory is outside an allowed root")
        executable = self._executables.get(command.executable)
        if executable is None:
            raise ValueError("terminal executable is not installed or allowlisted")

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        process = await asyncio.create_subprocess_exec(
            str(executable),
            *command.arguments,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
        assert process.stdout is not None and process.stderr is not None
        budget = _OutputBudget(command.output_limit_bytes)
        stdout_task = asyncio.create_task(_capture(process.stdout, budget))
        stderr_task = asyncio.create_task(_capture(process.stderr, budget))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=command.timeout_seconds)
        except TimeoutError:
            timed_out = True
            await _terminate_tree(process.pid)
            await process.wait()
        except asyncio.CancelledError:
            await _terminate_tree(process.pid)
            await process.wait()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        stdout_bytes, stderr_bytes = await asyncio.gather(stdout_task, stderr_task)
        return TerminalResult(
            exit_code=None if timed_out else process.returncode,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            timed_out=timed_out,
            truncated=budget.truncated,
        )


async def _capture(stream: asyncio.StreamReader, budget: _OutputBudget) -> bytes:
    chunks: list[bytes] = []
    while chunk := await stream.read(65_536):
        kept = budget.take(chunk)
        if kept:
            chunks.append(kept)
    return b"".join(chunks)


async def _terminate_tree(process_id: int) -> None:
    await asyncio.to_thread(_terminate_tree_sync, process_id)


def _terminate_tree_sync(process_id: int) -> None:
    try:
        parent = psutil.Process(process_id)
    except psutil.NoSuchProcess:
        return
    processes = [*parent.children(recursive=True), parent]
    for process in processes:
        with suppress(psutil.NoSuchProcess):
            process.terminate()
    _, alive = psutil.wait_procs(processes, timeout=1)
    for process in alive:
        with suppress(psutil.NoSuchProcess):
            process.kill()
