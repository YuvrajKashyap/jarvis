from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jarvis.agency.capabilities import CapabilityContext, CapabilityMetadata
from jarvis.agency.policy import RiskClass

TerminalExecutable = Literal["git", "pnpm", "uv", "cargo", "rg"]
TerminalArgument = Annotated[str, Field(min_length=1, max_length=512)]


class TerminalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool


class TerminalCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    executable: TerminalExecutable
    arguments: tuple[TerminalArgument, ...] = Field(default=(), max_length=64)
    cwd: Path
    timeout_seconds: float = Field(default=60, ge=1, le=120)
    output_limit_bytes: int = Field(default=32_768, ge=1_024, le=131_072)

    @model_validator(mode="after")
    def reject_destructive_command(self) -> "TerminalCommand":
        arguments = tuple(argument.casefold() for argument in self.arguments)
        destructive = (
            (
                self.executable == "git"
                and (
                    (arguments[:1] == ("reset",) and "--hard" in arguments)
                    or arguments[:1] in {("clean",), ("filter-repo",)}
                    or (arguments[:1] == ("push",) and any("force" in value for value in arguments))
                    or (arguments[:1] == ("branch",) and "-d" in arguments)
                )
            )
            or (self.executable == "cargo" and arguments[:1] == ("clean",))
            or (
                self.executable in {"pnpm", "uv"}
                and any(value in {"remove", "uninstall"} for value in arguments[:2])
            )
        )
        if destructive:
            raise ValueError("destructive terminal command is forbidden")
        if any(
            "\x00" in argument or "\n" in argument or "\r" in argument
            for argument in self.arguments
        ):
            raise ValueError("terminal arguments cannot contain control separators")
        return self


class CommandRunner(Protocol):
    async def run(self, command: TerminalCommand) -> TerminalResult: ...


class TerminalCommandCapability:
    metadata = CapabilityMetadata(
        name="terminal.execute",
        description=(
            "Run one bounded allowlisted development command inside an allowed local directory; "
            "shell syntax and destructive command forms are rejected"
        ),
        risk=RiskClass.EXTERNAL_IRREVERSIBLE,
        timeout_seconds=125,
        reversible=False,
    )
    input_model = TerminalCommand
    output_model = TerminalResult

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    async def execute(
        self,
        arguments: BaseModel,
        context: CapabilityContext,
    ) -> TerminalResult:
        command = TerminalCommand.model_validate(arguments)
        return await self._runner.run(command)
