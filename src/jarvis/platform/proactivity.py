import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from jarvis.agency.proactivity import ProactivePriority, ProactiveSignal
from jarvis.perception.context import ActiveWindowSnapshot, SystemHealthSnapshot
from jarvis.runtime.resources import ResourceSnapshot

PARTIAL_DOWNLOAD_SUFFIXES = frozenset({".crdownload", ".download", ".part", ".partial", ".tmp"})
MAX_DOWNLOAD_FILES = 512


class ProactivePerception(Protocol):
    def active_window(self) -> ActiveWindowSnapshot: ...

    def system_health(self) -> SystemHealthSnapshot: ...


class ProactiveResources(Protocol):
    def snapshot(self) -> ResourceSnapshot: ...


class WindowsProactiveProbe:
    """Observes bounded Windows metadata without screenshots or ambient transcription."""

    def __init__(
        self,
        *,
        perception: ProactivePerception,
        resources: ProactiveResources | None = None,
        downloads_directory: Path,
        focus_threshold: timedelta = timedelta(minutes=70),
    ) -> None:
        if focus_threshold <= timedelta(0):
            raise ValueError("focus threshold must be positive")
        self._perception = perception
        self._resources = resources
        self._downloads_directory = downloads_directory
        self._focus_threshold = focus_threshold
        self._known_downloads: dict[Path, tuple[int, int]] = {}
        self._pending_downloads: dict[Path, tuple[int, int]] = {}
        self._download_baseline_ready = False
        self._focus_key: str | None = None
        self._focus_started_at: datetime | None = None
        self._focus_emitted_for: str | None = None

    def scan(self, now: datetime) -> tuple[ProactiveSignal, ...]:
        signals: list[ProactiveSignal] = []
        resource_signal = self._resource_signal(now)
        if resource_signal is not None:
            signals.append(resource_signal)
        temperature_signal = self._temperature_signal(now)
        if temperature_signal is not None:
            signals.append(temperature_signal)
        signals.extend(self._download_signals(now))
        focus_signal = self._focus_signal(now)
        if focus_signal is not None:
            signals.append(focus_signal)
        return tuple(signals)

    def _resource_signal(self, now: datetime) -> ProactiveSignal | None:
        health = self._perception.system_health()
        if health.memory_percent < 90 or health.available_memory_bytes >= 2 * 1024**3:
            return None
        available_gib = health.available_memory_bytes / 1024**3
        return ProactiveSignal(
            signal_id=uuid4(),
            fingerprint="system-health:memory-pressure",
            topic="system_health",
            title="Memory pressure is building",
            message=(
                f"Available memory is down to {available_gib:.1f} GB. "
                "Want me to identify what's consuming it before JARVIS or your work slows down?"
            ),
            reason="Windows reports sustained high memory use and less than 2 GB available.",
            suggested_prompt="Show me the largest memory consumers and explain the safe options.",
            priority=(
                ProactivePriority.IMPORTANT
                if health.available_memory_bytes < 768 * 1024**2
                else ProactivePriority.NORMAL
            ),
            confidence=0.94,
            observed_at=now,
            expires_at=now + timedelta(minutes=30),
        )

    def _temperature_signal(self, now: datetime) -> ProactiveSignal | None:
        if self._resources is None:
            return None
        temperature = self._resources.snapshot().gpu_temperature_c
        if temperature is None or temperature < 82:
            return None
        return ProactiveSignal(
            signal_id=uuid4(),
            fingerprint="system-health:gpu-temperature",
            topic="system_health.temperature",
            title="The GPU is running hot",
            message=(
                f"The GPU is at {temperature} C. Want me to inspect the workload and cooling state?"
            ),
            reason="The local NVIDIA sensor crossed JARVIS's sustained-workload warning level.",
            suggested_prompt="Inspect current system load and explain the safest cooling options.",
            priority=(
                ProactivePriority.IMPORTANT if temperature >= 85 else ProactivePriority.NORMAL
            ),
            confidence=0.98,
            observed_at=now,
            expires_at=now + timedelta(minutes=10),
        )

    def _download_signals(self, now: datetime) -> tuple[ProactiveSignal, ...]:
        current = self._download_snapshot()
        if not self._download_baseline_ready:
            self._known_downloads = current
            self._download_baseline_ready = True
            return ()
        suggestions: list[ProactiveSignal] = []
        for path, state in current.items():
            known_state = self._known_downloads.get(path)
            if known_state is not None:
                self._known_downloads[path] = state
                continue
            if self._pending_downloads.get(path) != state:
                self._pending_downloads[path] = state
                continue
            self._pending_downloads.pop(path, None)
            self._known_downloads[path] = state
            suggestions.append(self._download_signal(path, now))
        current_paths = set(current)
        for path in tuple(self._pending_downloads):
            if path not in current_paths:
                self._pending_downloads.pop(path, None)
        for path in tuple(self._known_downloads):
            if path not in current_paths:
                self._known_downloads.pop(path, None)
        return tuple(suggestions)

    def _download_snapshot(self) -> dict[Path, tuple[int, int]]:
        if not self._downloads_directory.is_dir():
            return {}
        snapshots: dict[Path, tuple[int, int]] = {}
        for path in sorted(self._downloads_directory.iterdir(), key=lambda item: item.name.lower()):
            if len(snapshots) >= MAX_DOWNLOAD_FILES:
                break
            if path.suffix.lower() in PARTIAL_DOWNLOAD_SUFFIXES:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if path.is_file() and stat.st_size > 0:
                snapshots[path] = (stat.st_size, stat.st_mtime_ns)
        return snapshots

    @staticmethod
    def _download_signal(path: Path, now: datetime) -> ProactiveSignal:
        name = path.name[:180]
        kind = path.suffix.removeprefix(".").upper() or "file"
        label = path.stem.replace("_", " ").replace("-", " ").strip().title() or "Download"
        return ProactiveSignal(
            signal_id=uuid4(),
            fingerprint=f"download:{_private_fingerprint(name)}",
            topic="downloads",
            title=f"{label} {kind} is ready",
            message=f"{name} finished downloading. Want me to summarize or file it?",
            reason=f"A new completed {kind} appeared in Downloads.",
            suggested_prompt=f"Summarize {name} and suggest where to file it.",
            priority=ProactivePriority.QUIET,
            confidence=0.82,
            observed_at=now,
            expires_at=now + timedelta(hours=2),
        )

    def _focus_signal(self, now: datetime) -> ProactiveSignal | None:
        active = self._perception.active_window()
        if active.process_id <= 0 or active.process_name.lower() in {
            "jarvis-host.exe",
            "jarvis-core.exe",
        }:
            self._reset_focus()
            return None
        key = _private_fingerprint(f"{active.process_name}\0{active.title}")
        if key != self._focus_key:
            self._focus_key = key
            self._focus_started_at = now
            self._focus_emitted_for = None
            return None
        started_at = self._focus_started_at
        if (
            started_at is None
            or now - started_at < self._focus_threshold
            or self._focus_emitted_for == key
        ):
            return None
        self._focus_emitted_for = key
        app = _friendly_application_name(active.process_name)
        minutes = int((now - started_at).total_seconds() // 60)
        return ProactiveSignal(
            signal_id=uuid4(),
            fingerprint=f"focus:{key}",
            topic="focus",
            title="A useful checkpoint",
            message=(
                f"You've been focused in {app} for about {minutes} minutes. "
                "Want me to capture where things stand or help plan the next move?"
            ),
            reason="Foreground-window metadata shows one sustained work session.",
            suggested_prompt="Help me checkpoint my current work and decide the next step.",
            priority=ProactivePriority.QUIET,
            confidence=0.76,
            observed_at=now,
            expires_at=now + timedelta(minutes=20),
        )

    def _reset_focus(self) -> None:
        self._focus_key = None
        self._focus_started_at = None
        self._focus_emitted_for = None


def _private_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:24]


def _friendly_application_name(process_name: str) -> str:
    normalized = process_name.lower().removesuffix(".exe")
    names = {
        "code": "Visual Studio Code",
        "chrome": "Chrome",
        "msedge": "Edge",
        "devenv": "Visual Studio",
        "powershell": "PowerShell",
        "windowsterminal": "Windows Terminal",
        "winword": "Word",
        "excel": "Excel",
        "powerpnt": "PowerPoint",
    }
    return names.get(normalized, normalized.replace("-", " ").title() or "this application")
