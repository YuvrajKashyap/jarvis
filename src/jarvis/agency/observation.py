import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from jarvis.agency.capabilities import CapabilityContext, CapabilityMetadata
from jarvis.agency.policy import RiskClass
from jarvis.perception.context import (
    ActiveWindowSnapshot,
    PerceptionCoordinator,
    SystemHealthSnapshot,
)


class ObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LocalTimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    local_datetime: datetime
    timezone: str = Field(min_length=1, max_length=80)


class LocalTimeCapability:
    metadata = CapabilityMetadata(
        name="context.local_time",
        description="Read the exact current local date, time, UTC offset, and timezone",
        risk=RiskClass.OBSERVE,
        timeout_seconds=1,
        reversible=False,
    )
    input_model = ObservationInput
    output_model = LocalTimeSnapshot

    def __init__(
        self,
        *,
        timezone_name: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._timezone_name = timezone_name
        self._timezone = ZoneInfo(timezone_name)
        self._now = now

    async def execute(
        self,
        arguments: BaseModel,
        context: CapabilityContext,
    ) -> LocalTimeSnapshot:
        ObservationInput.model_validate(arguments)
        instant = self._now()
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("clock source must return a timezone-aware datetime")
        return LocalTimeSnapshot(
            local_datetime=instant.astimezone(self._timezone),
            timezone=self._timezone_name,
        )


class ActiveWindowCapability:
    metadata = CapabilityMetadata(
        name="context.active_window",
        description="Read the title, process, and capture time of the active Windows application",
        risk=RiskClass.OBSERVE,
        timeout_seconds=2,
        reversible=False,
    )
    input_model = ObservationInput
    output_model = ActiveWindowSnapshot

    def __init__(self, perception: PerceptionCoordinator) -> None:
        self._perception = perception

    async def execute(
        self,
        arguments: BaseModel,
        context: CapabilityContext,
    ) -> ActiveWindowSnapshot:
        ObservationInput.model_validate(arguments)
        return await asyncio.to_thread(self._perception.active_window)


class SystemHealthCapability:
    metadata = CapabilityMetadata(
        name="system.health",
        description="Read current CPU and memory pressure from the laptop",
        risk=RiskClass.OBSERVE,
        timeout_seconds=2,
        reversible=False,
    )
    input_model = ObservationInput
    output_model = SystemHealthSnapshot

    def __init__(self, perception: PerceptionCoordinator) -> None:
        self._perception = perception

    async def execute(
        self,
        arguments: BaseModel,
        context: CapabilityContext,
    ) -> SystemHealthSnapshot:
        ObservationInput.model_validate(arguments)
        return await asyncio.to_thread(self._perception.system_health)
