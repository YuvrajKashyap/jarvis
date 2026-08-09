from datetime import UTC, datetime
from uuid import UUID

from jarvis.agency.capabilities import CapabilityContext
from jarvis.agency.policy import RiskClass
from jarvis.agency.windows import (
    InspectWindowsCapability,
    InspectWindowsInput,
    InvokeWindowsCapability,
    InvokeWindowsInput,
    SetWindowsValueCapability,
    SetWindowsValueInput,
    WindowActionResult,
    WindowElement,
    WindowSnapshot,
)

NOW = datetime(2026, 8, 9, 14, 0, tzinfo=UTC)
CONTEXT = CapabilityContext(
    invocation_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf0"),
    device_id="desktop",
    requested_at=NOW,
)


class FakeWindowsAutomation:
    async def inspect_active(self, *, depth: int, interactive_only: bool) -> WindowSnapshot:
        return WindowSnapshot(
            window_handle=7331,
            title="Settings",
            captured_at=NOW,
            elements=(
                WindowElement(
                    selector="btn-save-a1b2",
                    control_type="Button",
                    name="Save",
                    enabled=True,
                    offscreen=False,
                    invokable=True,
                ),
            ),
        )

    async def invoke_active(self, selector: str) -> WindowActionResult:
        return WindowActionResult(
            window_handle=7331,
            operation="invoke",
            selector=selector,
            detail="Invoked Save",
            completed_at=NOW,
        )

    async def set_active_value(self, selector: str, value: str) -> WindowActionResult:
        return WindowActionResult(
            window_handle=7331,
            operation="set_value",
            selector=selector,
            detail="Value set",
            completed_at=NOW,
        )


async def test_windows_inspection_is_structured_observation() -> None:
    capability = InspectWindowsCapability(FakeWindowsAutomation())

    result = await capability.execute(InspectWindowsInput(depth=3), CONTEXT)

    assert capability.metadata.name == "windows.inspect"
    assert capability.metadata.risk is RiskClass.OBSERVE
    assert result.title == "Settings"
    assert result.elements[0].selector == "btn-save-a1b2"


async def test_windows_invoke_requires_external_action_approval() -> None:
    capability = InvokeWindowsCapability(FakeWindowsAutomation())

    result = await capability.execute(InvokeWindowsInput(selector="btn-save-a1b2"), CONTEXT)

    assert capability.metadata.name == "windows.invoke"
    assert capability.metadata.risk is RiskClass.EXTERNAL_IRREVERSIBLE
    assert result.operation == "invoke"


async def test_windows_value_change_requires_external_action_approval() -> None:
    capability = SetWindowsValueCapability(FakeWindowsAutomation())

    result = await capability.execute(
        SetWindowsValueInput(selector="txt-name-a1b2", value="Yuvraj"),
        CONTEXT,
    )

    assert capability.metadata.name == "windows.set_value"
    assert capability.metadata.risk is RiskClass.EXTERNAL_IRREVERSIBLE
    assert result.operation == "set_value"
