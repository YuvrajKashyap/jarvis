import asyncio

from jarvis.platform.models import LoadedModel, ModelHealth
from jarvis.runtime.diagnostics import (
    AcceptanceProbe,
    CapabilityProbe,
    ConfigurationProbe,
    CountProbe,
    DiagnosticCheck,
    DiagnosticState,
    ModelResidencyProbe,
    ModuleAvailabilityProbe,
    ReadinessDiagnostics,
    ResourceProbe,
    readiness_snapshot_schema,
)
from jarvis.runtime.resources import ResourceSnapshot


class Probe:
    def __init__(self, check: DiagnosticCheck | Exception) -> None:
        self._check = check

    async def inspect(self) -> DiagnosticCheck:
        if isinstance(self._check, Exception):
            raise self._check
        return self._check


class Model:
    def __init__(self, health: ModelHealth) -> None:
        self._health = health

    async def health(self) -> ModelHealth:
        return self._health


class Evidence:
    def __init__(self, accepted: set[tuple[str, str | None]]) -> None:
        self._accepted = accepted

    def passed(self, code: str, *, subject: str | None = None) -> bool:
        return (code, subject) in self._accepted


class Resources:
    def __init__(self, snapshot: ResourceSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> ResourceSnapshot:
        return self._snapshot


def check(code: str, state: DiagnosticState) -> DiagnosticCheck:
    return DiagnosticCheck(code=code, state=state, summary=f"{code} summary")


def test_readiness_is_unverified_when_acceptance_evidence_is_missing() -> None:
    diagnostics = ReadinessDiagnostics(
        (
            Probe(check("model_residency", DiagnosticState.READY)),
            Probe(check("model_quality", DiagnosticState.UNVERIFIED)),
        )
    )

    snapshot = asyncio.run(diagnostics.snapshot())

    assert snapshot.overall is DiagnosticState.UNVERIFIED
    assert [item.code for item in snapshot.checks] == ["model_residency", "model_quality"]


def test_readiness_probe_failure_is_visible_without_leaking_exception_text() -> None:
    diagnostics = ReadinessDiagnostics(
        (Probe(RuntimeError("private machine detail")),),
    )

    snapshot = asyncio.run(diagnostics.snapshot())

    assert snapshot.overall is DiagnosticState.DEGRADED
    assert snapshot.checks[0].code == "diagnostic_probe"
    assert snapshot.checks[0].summary == "A readiness probe failed."
    assert "private machine detail" not in snapshot.model_dump_json()


def test_live_diagnostics_distinguish_residency_acceptance_inventory_and_pressure() -> None:
    model_name = "qwen3.5:4b-q4_K_M"
    probes = (
        ModelResidencyProbe(
            model=Model(
                ModelHealth(
                    available=True,
                    version="0.15.0",
                    loaded_models=(
                        LoadedModel(
                            name=model_name,
                            size_bytes=3_400_000_000,
                            vram_bytes=3_400_000_000,
                            context_length=4_096,
                        ),
                    ),
                )
            ),
            primary_model=model_name,
        ),
        AcceptanceProbe(
            evidence=Evidence(set()),
            code="model_quality",
            evidence_code="model-quality",
            subject=model_name,
            ready_summary="Model quality passed.",
            missing_summary="Model quality is unverified.",
        ),
        CountProbe(
            code="memory",
            counter=lambda: 0,
            noun="durable memory facts",
        ),
        CountProbe(
            code="paired_devices",
            counter=lambda: 1,
            noun="paired devices",
        ),
        CapabilityProbe(
            names=lambda: ("context.active_window", "files.read_text"),
            required=frozenset({"context.active_window", "files.read_text"}),
        ),
        ResourceProbe(
            resources=Resources(
                ResourceSnapshot(
                    available_memory_bytes=3 * 1024**3,
                    committed_memory_percent=80,
                    gpu_temperature_c=68,
                    gpu_memory_used_bytes=6 * 1024**3,
                )
            )
        ),
        ConfigurationProbe(
            code="voice",
            configured=False,
            ready_summary="Original voice configured.",
            missing_summary="Original voice not configured.",
        ),
    )

    snapshot = asyncio.run(ReadinessDiagnostics(probes).snapshot())

    assert [item.state for item in snapshot.checks] == [
        DiagnosticState.READY,
        DiagnosticState.UNVERIFIED,
        DiagnosticState.UNVERIFIED,
        DiagnosticState.READY,
        DiagnosticState.READY,
        DiagnosticState.READY,
        DiagnosticState.UNVERIFIED,
    ]
    assert snapshot.checks[0].detail == "Ollama 0.15.0; 4096 context tokens."
    assert snapshot.checks[2].detail == "0 durable memory facts."


def test_readiness_snapshot_schema_is_exportable_for_the_interface() -> None:
    schema = readiness_snapshot_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["overall", "generated_at", "checks"]


def test_module_probe_names_missing_packaged_speech_dependencies() -> None:
    available = {"openwakeword", "sounddevice"}
    probe = ModuleAvailabilityProbe(
        code="speech_dependencies",
        modules=("openwakeword", "sounddevice", "chatterbox"),
        resolver=lambda name: object() if name in available else None,
    )

    check = asyncio.run(probe.inspect())

    assert check.state is DiagnosticState.BLOCKED
    assert check.detail == "Missing: chatterbox"
