from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from jarvis.agency.capabilities import CapabilityContext
from jarvis.agency.policy import RiskClass
from jarvis.agency.terminal import (
    TerminalCommand,
    TerminalCommandCapability,
    TerminalResult,
)

NOW = datetime(2026, 8, 8, 20, 0, tzinfo=UTC)


class FakeCommandRunner:
    def __init__(self) -> None:
        self.commands: list[TerminalCommand] = []

    async def run(self, command: TerminalCommand) -> TerminalResult:
        self.commands.append(command)
        return TerminalResult(
            exit_code=0,
            stdout="On branch main\n",
            stderr="",
            timed_out=False,
            truncated=False,
        )


def test_terminal_input_rejects_destructive_git_commands_before_execution() -> None:
    with pytest.raises(ValidationError, match="destructive terminal command"):
        TerminalCommand(
            executable="git",
            arguments=("reset", "--hard"),
            cwd=Path("C:/Users/ykyuv/dev/jarvis"),
        )


async def test_terminal_capability_requires_external_irreversible_policy_class() -> None:
    runner = FakeCommandRunner()
    capability = TerminalCommandCapability(runner)
    command = TerminalCommand(
        executable="git",
        arguments=("status", "--short"),
        cwd=Path("C:/Users/ykyuv/dev/jarvis"),
    )

    result = await capability.execute(
        command,
        CapabilityContext(
            invocation_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf0"),
            device_id="desktop",
            requested_at=NOW,
        ),
    )

    assert capability.metadata.risk is RiskClass.EXTERNAL_IRREVERSIBLE
    assert capability.metadata.reversible is False
    assert result.stdout == "On branch main\n"
    assert runner.commands == [command]
