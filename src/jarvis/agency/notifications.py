from pydantic import BaseModel, ConfigDict, Field

from jarvis.agency.capabilities import CapabilityContext, CapabilityMetadata
from jarvis.agency.policy import RiskClass


class ReminderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=1_000)


class ReminderNotification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    message: str


class ReminderCapability:
    metadata = CapabilityMetadata(
        name="notifications.remind",
        description="Present one local JARVIS reminder on connected authorized devices",
        risk=RiskClass.OBSERVE,
        timeout_seconds=1,
        reversible=False,
    )
    input_model = ReminderInput
    output_model = ReminderNotification

    async def execute(
        self,
        arguments: BaseModel,
        context: CapabilityContext,
    ) -> ReminderNotification:
        request = ReminderInput.model_validate(arguments)
        return ReminderNotification(title=request.title, message=request.message)
