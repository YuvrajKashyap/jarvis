import sys
import time
from pathlib import Path
from shutil import which

import pytest

from jarvis.agency.terminal import TerminalCommand
from jarvis.platform.process import BoundedProcessRunner, LocalCommandRunner


async def test_command_runner_bounds_combined_output(tmp_path: Path) -> None:
    runner = LocalCommandRunner(
        roots=(tmp_path,),
        executables={"uv": Path(sys.executable)},
    )

    result = await runner.run(
        TerminalCommand(
            executable="uv",
            arguments=(
                "-c",
                "import sys; print('a' * 4000); print('b' * 4000, file=sys.stderr)",
            ),
            cwd=tmp_path,
            output_limit_bytes=1_024,
        )
    )

    assert result.exit_code == 0
    assert result.truncated is True
    assert len(result.stdout.encode()) + len(result.stderr.encode()) <= 1_024


async def test_command_runner_rejects_working_directory_outside_allowed_roots(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    runner = LocalCommandRunner(
        roots=(allowed,),
        executables={"uv": Path(sys.executable)},
    )

    with pytest.raises(ValueError, match="allowed root"):
        await runner.run(
            TerminalCommand(
                executable="uv",
                arguments=("--version",),
                cwd=tmp_path,
            )
        )


async def test_command_runner_terminates_timed_out_process(tmp_path: Path) -> None:
    runner = LocalCommandRunner(
        roots=(tmp_path,),
        executables={"uv": Path(sys.executable)},
    )
    started = time.perf_counter()

    result = await runner.run(
        TerminalCommand(
            executable="uv",
            arguments=("-c", "import time; time.sleep(30)"),
            cwd=tmp_path,
            timeout_seconds=1,
        )
    )

    assert result.timed_out is True
    assert time.perf_counter() - started < 5


@pytest.mark.skipif(which("winapp") is None, reason="Windows App CLI is not installed")
async def test_bounded_runner_supports_windows_execution_aliases(tmp_path: Path) -> None:
    executable = which("winapp")
    assert executable is not None

    result = await BoundedProcessRunner().run(
        Path(executable),
        ("--version",),
        cwd=tmp_path,
        environment={"WINAPP_CLI_TELEMETRY_OPTOUT": "1"},
        timeout_seconds=10,
        output_limit_bytes=32_768,
    )

    assert result.exit_code == 0
    assert "0.5.0" in result.stdout
