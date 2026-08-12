from datetime import UTC, datetime
from uuid import UUID

from jarvis.agency.capabilities import CapabilityContext
from jarvis.agency.observation import (
    ActiveWindowCapability,
    LocalTimeCapability,
    ObservationInput,
    SystemHealthCapability,
)
from jarvis.agency.policy import RiskClass
from jarvis.perception.context import (
    ActiveWindowSnapshot,
    PerceptionCoordinator,
    ScreenSnapshot,
    SystemHealthSnapshot,
)

NOW = datetime(2026, 8, 8, 1, 0, tzinfo=UTC)


class FakePerception:
    def active_window(self) -> ActiveWindowSnapshot:
        return ActiveWindowSnapshot(
            title="JARVIS - Visual Studio Code",
            process_id=7331,
            process_name="Code.exe",
            executable_path="C:/Program Files/Microsoft VS Code/Code.exe",
            captured_at=NOW,
        )

    def system_health(self) -> SystemHealthSnapshot:
        return SystemHealthSnapshot(
            cpu_percent=20.0,
            memory_percent=70.0,
            available_memory_bytes=4_000_000_000,
            captured_at=NOW,
        )

    def capture_screen(self) -> ScreenSnapshot:
        raise AssertionError("observation capabilities must not capture pixels")


CONTEXT = CapabilityContext(
    invocation_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf0"),
    device_id="desktop",
    requested_at=NOW,
)


async def test_active_window_capability_returns_typed_metadata_without_pixels() -> None:
    capability = ActiveWindowCapability(PerceptionCoordinator(FakePerception()))

    result = await capability.execute(ObservationInput(), CONTEXT)

    assert capability.metadata.name == "context.active_window"
    assert capability.metadata.risk is RiskClass.OBSERVE
    assert result.process_name == "Code.exe"
    assert result.title == "JARVIS - Visual Studio Code"


async def test_system_health_capability_is_read_only_and_bounded() -> None:
    capability = SystemHealthCapability(PerceptionCoordinator(FakePerception()))

    result = await capability.execute(ObservationInput(), CONTEXT)

    assert capability.metadata.name == "system.health"
    assert capability.metadata.risk is RiskClass.OBSERVE
    assert result.available_memory_bytes == 4_000_000_000


async def test_local_time_capability_returns_an_exact_sourced_time() -> None:
    instant = datetime(2026, 8, 11, 22, 30, tzinfo=UTC)
    capability = LocalTimeCapability(
        timezone_name="America/Chicago",
        now=lambda: instant,
    )

    result = await capability.execute(ObservationInput(), CONTEXT)

    assert result.local_datetime.isoformat() == "2026-08-11T17:30:00-05:00"
    assert result.timezone == "America/Chicago"
