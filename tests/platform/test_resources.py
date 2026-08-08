import subprocess
from types import SimpleNamespace

from jarvis.platform.resources import WindowsResourceProbe


def test_windows_resource_probe_reads_ram_and_nvidia_without_shell() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="61, 1745\n", stderr="")

    probe = WindowsResourceProbe(
        memory_reader=lambda: SimpleNamespace(available=3_000_000_000, percent=72.5),
        command_runner=run,
    )

    snapshot = probe.snapshot()

    assert snapshot.available_memory_bytes == 3_000_000_000
    assert snapshot.committed_memory_percent == 72.5
    assert snapshot.gpu_temperature_c == 61
    assert snapshot.gpu_memory_used_bytes == 1745 * 1024**2
    assert calls[0][0][0] == "nvidia-smi"
    assert calls[0][1]["shell"] is False


def test_windows_resource_probe_degrades_when_nvidia_is_unavailable() -> None:
    def unavailable(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    probe = WindowsResourceProbe(
        memory_reader=lambda: SimpleNamespace(available=2_000_000_000, percent=80.0),
        command_runner=unavailable,
    )

    snapshot = probe.snapshot()

    assert snapshot.gpu_temperature_c is None
    assert snapshot.gpu_memory_used_bytes is None
