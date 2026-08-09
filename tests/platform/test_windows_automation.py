import json
from pathlib import Path

import pytest

from jarvis.platform.process import BoundedProcessResult
from jarvis.platform.windows import WinAppAutomation


class FakeProcessRunner:
    def __init__(self, result: BoundedProcessResult) -> None:
        self.result = result
        self.arguments: tuple[str, ...] = ()
        self.environment: dict[str, str] = {}

    async def run(
        self,
        executable: Path,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
        output_limit_bytes: int,
    ) -> BoundedProcessResult:
        self.arguments = arguments
        self.environment = environment
        return self.result


def _success(payload: object) -> BoundedProcessResult:
    return BoundedProcessResult(
        exit_code=0,
        stdout=json.dumps(payload),
        stderr="",
        timed_out=False,
        truncated=False,
    )


async def test_winapp_inspection_parses_and_bounds_the_active_window_tree(tmp_path: Path) -> None:
    runner = FakeProcessRunner(
        _success(
            {
                "depth": 3,
                "interactive": True,
                "windows": [
                    {
                        "hwnd": 7331,
                        "title": "Settings",
                        "elements": [
                            {
                                "type": "Button",
                                "name": "Save",
                                "selector": "btn-save-a1b2",
                                "isEnabled": True,
                                "isOffscreen": False,
                                "isInvokable": True,
                                "children": [],
                            }
                        ],
                    }
                ],
            }
        )
    )
    automation = WinAppAutomation(
        executable=tmp_path / "winapp.exe",
        runner=runner,
        window_handle=lambda: 7331,
        working_directory=tmp_path,
    )

    snapshot = await automation.inspect_active(depth=3, interactive_only=True)

    assert snapshot.window_handle == 7331
    assert snapshot.elements[0].control_type == "Button"
    assert snapshot.elements[0].selector == "btn-save-a1b2"
    assert runner.arguments == (
        "ui",
        "inspect",
        "--window",
        "7331",
        "--json",
        "--depth",
        "3",
        "--interactive",
        "--hide-disabled",
        "--hide-offscreen",
    )
    assert runner.environment == {"WINAPP_CLI_TELEMETRY_OPTOUT": "1"}


async def test_winapp_rejects_stale_or_ambiguous_window_output(tmp_path: Path) -> None:
    runner = FakeProcessRunner(_success({"windows": []}))
    automation = WinAppAutomation(
        executable=tmp_path / "winapp.exe",
        runner=runner,
        window_handle=lambda: 7331,
        working_directory=tmp_path,
    )

    with pytest.raises(RuntimeError, match="active window"):
        await automation.inspect_active(depth=3, interactive_only=False)


async def test_winapp_uses_uia_patterns_without_shell_or_input_injection(tmp_path: Path) -> None:
    runner = FakeProcessRunner(
        _success({"success": True, "message": "Invoked Save", "selector": "btn-save-a1b2"})
    )
    automation = WinAppAutomation(
        executable=tmp_path / "winapp.exe",
        runner=runner,
        window_handle=lambda: 7331,
        working_directory=tmp_path,
    )

    result = await automation.invoke_active("btn-save-a1b2")

    assert result.operation == "invoke"
    assert runner.arguments == (
        "ui",
        "invoke",
        "btn-save-a1b2",
        "--window",
        "7331",
        "--json",
    )


async def test_winapp_rejects_control_separators_before_process_execution(tmp_path: Path) -> None:
    runner = FakeProcessRunner(_success({"success": True}))
    automation = WinAppAutomation(
        executable=tmp_path / "winapp.exe",
        runner=runner,
        window_handle=lambda: 7331,
        working_directory=tmp_path,
    )

    with pytest.raises(ValueError, match="control"):
        await automation.set_active_value("txt-name", "Yuvraj\r\nsubmit")
