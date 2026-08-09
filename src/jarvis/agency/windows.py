from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jarvis.agency.capabilities import CapabilityContext, CapabilityMetadata
from jarvis.agency.policy import RiskClass


class WindowsValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WindowElement(WindowsValue):
    selector: str | None = Field(default=None, max_length=256)
    control_type: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=2_048)
    class_name: str = Field(default="", max_length=512)
    enabled: bool
    offscreen: bool
    invokable: bool
    children: tuple["WindowElement", ...] = Field(default=(), max_length=256)


class WindowSnapshot(WindowsValue):
    window_handle: int = Field(gt=0)
    title: str = Field(max_length=4_096)
    captured_at: datetime
    elements: tuple[WindowElement, ...] = Field(max_length=256)


class WindowActionResult(WindowsValue):
    window_handle: int = Field(gt=0)
    operation: Literal["invoke", "set_value"]
    selector: str = Field(min_length=1, max_length=256)
    detail: str = Field(max_length=4_096)
    completed_at: datetime


class InspectWindowsInput(WindowsValue):
    depth: int = Field(default=4, ge=1, le=6)
    interactive_only: bool = True


class InvokeWindowsInput(WindowsValue):
    selector: str = Field(min_length=1, max_length=256)

    @field_validator("selector")
    @classmethod
    def reject_control_separators(cls, value: str) -> str:
        return _safe_operand(value)


class SetWindowsValueInput(WindowsValue):
    selector: str = Field(min_length=1, max_length=256)
    value: str = Field(max_length=16_000)

    @field_validator("selector", "value")
    @classmethod
    def reject_control_separators(cls, value: str) -> str:
        return _safe_operand(value)


class WindowsAutomation(Protocol):
    async def inspect_active(self, *, depth: int, interactive_only: bool) -> WindowSnapshot: ...

    async def invoke_active(self, selector: str) -> WindowActionResult: ...

    async def set_active_value(self, selector: str, value: str) -> WindowActionResult: ...


class InspectWindowsCapability:
    metadata = CapabilityMetadata(
        name="windows.inspect",
        description=(
            "Inspect accessible controls in the currently active Windows application using "
            "structured UI Automation"
        ),
        risk=RiskClass.OBSERVE,
        timeout_seconds=12,
        reversible=False,
    )
    input_model = InspectWindowsInput
    output_model = WindowSnapshot

    def __init__(self, automation: WindowsAutomation) -> None:
        self._automation = automation

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> WindowSnapshot:
        request = InspectWindowsInput.model_validate(arguments)
        return await self._automation.inspect_active(
            depth=request.depth,
            interactive_only=request.interactive_only,
        )


class InvokeWindowsCapability:
    metadata = CapabilityMetadata(
        name="windows.invoke",
        description=(
            "Invoke one accessible control in the currently active Windows application through "
            "a UI Automation control pattern"
        ),
        risk=RiskClass.EXTERNAL_IRREVERSIBLE,
        timeout_seconds=12,
        reversible=False,
    )
    input_model = InvokeWindowsInput
    output_model = WindowActionResult

    def __init__(self, automation: WindowsAutomation) -> None:
        self._automation = automation

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> WindowActionResult:
        request = InvokeWindowsInput.model_validate(arguments)
        return await self._automation.invoke_active(request.selector)


class SetWindowsValueCapability:
    metadata = CapabilityMetadata(
        name="windows.set_value",
        description=(
            "Set one accessible value in the currently active Windows application through a UI "
            "Automation value pattern"
        ),
        risk=RiskClass.EXTERNAL_IRREVERSIBLE,
        timeout_seconds=12,
        reversible=False,
    )
    input_model = SetWindowsValueInput
    output_model = WindowActionResult

    def __init__(self, automation: WindowsAutomation) -> None:
        self._automation = automation

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> WindowActionResult:
        request = SetWindowsValueInput.model_validate(arguments)
        return await self._automation.set_active_value(request.selector, request.value)


def _safe_operand(value: str) -> str:
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError("Windows automation values cannot contain control separators")
    return value
