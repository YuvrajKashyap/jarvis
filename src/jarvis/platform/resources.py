import os
import subprocess
from collections.abc import Callable
from typing import Protocol, cast

import psutil

from jarvis.runtime.resources import ResourceSnapshot


class MemoryState(Protocol):
    available: int
    percent: float


class CommandRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]: ...


class WindowsResourceProbe:
    def __init__(
        self,
        *,
        memory_reader: Callable[[], MemoryState] = psutil.virtual_memory,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self._memory_reader = memory_reader
        self._command_runner = command_runner or cast(CommandRunner, subprocess.run)

    def snapshot(self) -> ResourceSnapshot:
        memory = self._memory_reader()
        temperature, gpu_memory_mib = self._nvidia_state()
        return ResourceSnapshot(
            available_memory_bytes=int(memory.available),
            committed_memory_percent=float(memory.percent),
            gpu_temperature_c=temperature,
            gpu_memory_used_bytes=(None if gpu_memory_mib is None else gpu_memory_mib * 1024**2),
        )

    def _nvidia_state(self) -> tuple[int | None, int | None]:
        command = [
            "nvidia-smi",
            "--query-gpu=temperature.gpu,memory.used",
            "--format=csv,noheader,nounits",
        ]
        creation_flags = 0x0800_0000 if os.name == "nt" else 0
        try:
            completed = self._command_runner(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
                shell=False,
                creationflags=creation_flags,
            )
            temperature, memory = (
                int(value.strip()) for value in completed.stdout.splitlines()[0].split(",")
            )
            return temperature, memory
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return None, None
