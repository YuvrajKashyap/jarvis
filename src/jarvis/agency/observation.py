import asyncio

from pydantic import BaseModel, ConfigDict

from jarvis.agency.capabilities import CapabilityContext, CapabilityMetadata
from jarvis.agency.policy import RiskClass
from jarvis.perception.context import (
    ActiveWindowSnapshot,
    PerceptionCoordinator,
    SystemHealthSnapshot,
)


class ObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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
