import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from importlib.util import find_spec
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from jarvis.platform.models import ModelHealth
from jarvis.runtime.resources import ResourceSnapshot


class DiagnosticState(StrEnum):
    READY = "ready"
    UNVERIFIED = "unverified"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class DiagnosticCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_]+$")
    state: DiagnosticState
    summary: str = Field(min_length=1, max_length=500)
    detail: str | None = Field(default=None, max_length=2_000)


class ReadinessSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    overall: DiagnosticState
    generated_at: datetime
    checks: tuple[DiagnosticCheck, ...]


def readiness_snapshot_schema() -> dict[str, object]:
    """Return the authoritative JSON schema shared with authenticated clients."""
    return ReadinessSnapshot.model_json_schema()


class DiagnosticProbe(Protocol):
    async def inspect(self) -> DiagnosticCheck: ...


class ModelHealthSource(Protocol):
    async def health(self) -> ModelHealth: ...


class AcceptanceEvidence(Protocol):
    def passed(self, code: str, *, subject: str | None = None) -> bool: ...


class ResourceSnapshotSource(Protocol):
    def snapshot(self) -> ResourceSnapshot: ...


class ModelResidencyProbe:
    def __init__(self, *, model: ModelHealthSource, primary_model: str) -> None:
        self._model = model
        self._primary_model = primary_model

    async def inspect(self) -> DiagnosticCheck:
        health = await self._model.health()
        if not health.available:
            return DiagnosticCheck(
                code="model_residency",
                state=DiagnosticState.BLOCKED,
                summary="The local model runtime is unavailable.",
            )
        loaded = next(
            (
                candidate
                for candidate in health.loaded_models
                if candidate.name == self._primary_model
            ),
            None,
        )
        if loaded is None:
            return DiagnosticCheck(
                code="model_residency",
                state=DiagnosticState.DEGRADED,
                summary="The selected model is not resident.",
                detail="JARVIS will attempt a governed load before the next turn.",
            )
        return DiagnosticCheck(
            code="model_residency",
            state=DiagnosticState.READY,
            summary="The selected local model is resident.",
            detail=f"Ollama {health.version or 'unknown'}; {loaded.context_length} context tokens.",
        )


class AcceptanceProbe:
    def __init__(
        self,
        *,
        evidence: AcceptanceEvidence,
        code: str,
        evidence_code: str,
        ready_summary: str,
        missing_summary: str,
        subject: str | None = None,
    ) -> None:
        self._evidence = evidence
        self._code = code
        self._evidence_code = evidence_code
        self._ready_summary = ready_summary
        self._missing_summary = missing_summary
        self._subject = subject

    async def inspect(self) -> DiagnosticCheck:
        accepted = await asyncio.to_thread(
            self._evidence.passed,
            self._evidence_code,
            subject=self._subject,
        )
        return DiagnosticCheck(
            code=self._code,
            state=DiagnosticState.READY if accepted else DiagnosticState.UNVERIFIED,
            summary=self._ready_summary if accepted else self._missing_summary,
        )


class CountProbe:
    def __init__(self, *, code: str, counter: Callable[[], int], noun: str) -> None:
        self._code = code
        self._counter = counter
        self._noun = noun

    async def inspect(self) -> DiagnosticCheck:
        count = await asyncio.to_thread(self._counter)
        if count < 0:
            raise ValueError("diagnostic count cannot be negative")
        return DiagnosticCheck(
            code=self._code,
            state=DiagnosticState.READY if count > 0 else DiagnosticState.UNVERIFIED,
            summary=(
                f"JARVIS has {count} {self._noun}." if count > 0 else f"JARVIS has no {self._noun}."
            ),
            detail=f"{count} {self._noun}.",
        )


class ConfigurationProbe:
    def __init__(
        self,
        *,
        code: str,
        configured: bool,
        ready_summary: str,
        missing_summary: str,
    ) -> None:
        self._code = code
        self._configured = configured
        self._ready_summary = ready_summary
        self._missing_summary = missing_summary

    async def inspect(self) -> DiagnosticCheck:
        return DiagnosticCheck(
            code=self._code,
            state=DiagnosticState.READY if self._configured else DiagnosticState.UNVERIFIED,
            summary=self._ready_summary if self._configured else self._missing_summary,
        )


class ModuleAvailabilityProbe:
    """Checks that frozen/runtime dependency modules are discoverable without importing them."""

    def __init__(
        self,
        *,
        code: str,
        modules: tuple[str, ...],
        resolver: Callable[[str], object | None] = find_spec,
    ) -> None:
        if not modules or len(modules) > 32:
            raise ValueError("module diagnostics require between one and 32 modules")
        self._code = code
        self._modules = modules
        self._resolver = resolver

    async def inspect(self) -> DiagnosticCheck:
        missing = await asyncio.to_thread(
            lambda: tuple(name for name in self._modules if self._resolver(name) is None)
        )
        return DiagnosticCheck(
            code=self._code,
            state=DiagnosticState.BLOCKED if missing else DiagnosticState.READY,
            summary=(
                "Required packaged runtime dependencies are missing."
                if missing
                else "Required packaged runtime dependencies are available."
            ),
            detail="Missing: " + ", ".join(missing) if missing else ", ".join(self._modules),
        )


class CapabilityProbe:
    def __init__(
        self,
        *,
        names: Callable[[], tuple[str, ...]],
        required: frozenset[str],
    ) -> None:
        self._names = names
        self._required = required

    async def inspect(self) -> DiagnosticCheck:
        names = await asyncio.to_thread(self._names)
        missing = sorted(self._required - set(names))
        return DiagnosticCheck(
            code="capabilities",
            state=DiagnosticState.BLOCKED if missing else DiagnosticState.READY,
            summary=(
                "Required capability registrations are missing."
                if missing
                else "Required capabilities are registered."
            ),
            detail=(
                "Missing: " + ", ".join(missing)
                if missing
                else f"{len(names)} typed capabilities registered."
            ),
        )


class ResourceProbe:
    def __init__(
        self,
        *,
        resources: ResourceSnapshotSource,
        minimum_available_memory_bytes: int = 1024**3,
        maximum_gpu_temperature_c: int = 85,
    ) -> None:
        self._resources = resources
        self._minimum_memory = minimum_available_memory_bytes
        self._maximum_temperature = maximum_gpu_temperature_c

    async def inspect(self) -> DiagnosticCheck:
        snapshot = await asyncio.to_thread(self._resources.snapshot)
        temperature = (
            str(snapshot.gpu_temperature_c) if snapshot.gpu_temperature_c is not None else "unknown"
        )
        pressure = snapshot.available_memory_bytes < self._minimum_memory
        thermal = (
            snapshot.gpu_temperature_c is not None
            and snapshot.gpu_temperature_c > self._maximum_temperature
        )
        state = DiagnosticState.DEGRADED if pressure or thermal else DiagnosticState.READY
        return DiagnosticCheck(
            code="resources",
            state=state,
            summary=(
                "Current resource pressure prevents a safe model workload."
                if pressure or thermal
                else "Current memory and GPU temperature are within safety limits."
            ),
            detail=(
                f"{snapshot.available_memory_bytes / 1024**3:.1f} GiB available; "
                f"GPU {temperature} C."
            ),
        )


class ReadinessDiagnostics:
    """Aggregates bounded subsystem probes into one truthful product status."""

    def __init__(
        self,
        probes: tuple[DiagnosticProbe, ...],
        *,
        probe_timeout_seconds: float = 5,
    ) -> None:
        if not probes or len(probes) > 32:
            raise ValueError("readiness diagnostics require between one and 32 probes")
        if probe_timeout_seconds <= 0 or probe_timeout_seconds > 30:
            raise ValueError("diagnostic probe timeout must be between zero and 30 seconds")
        self._probes = probes
        self._probe_timeout_seconds = probe_timeout_seconds

    async def snapshot(self) -> ReadinessSnapshot:
        checks = tuple(await asyncio.gather(*(self._inspect(probe) for probe in self._probes)))
        return ReadinessSnapshot(
            overall=_overall_state(checks),
            generated_at=datetime.now(UTC),
            checks=checks,
        )

    async def _inspect(self, probe: DiagnosticProbe) -> DiagnosticCheck:
        try:
            return await asyncio.wait_for(
                probe.inspect(),
                timeout=self._probe_timeout_seconds,
            )
        except (OSError, RuntimeError, TimeoutError, ValueError):
            return DiagnosticCheck(
                code="diagnostic_probe",
                state=DiagnosticState.DEGRADED,
                summary="A readiness probe failed.",
                detail="Open JARVIS diagnostics and retry after the subsystem recovers.",
            )


def _overall_state(checks: tuple[DiagnosticCheck, ...]) -> DiagnosticState:
    states = {check.state for check in checks}
    for state in (
        DiagnosticState.BLOCKED,
        DiagnosticState.DEGRADED,
        DiagnosticState.UNVERIFIED,
    ):
        if state in states:
            return state
    return DiagnosticState.READY
