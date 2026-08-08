import asyncio
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from jarvis.agency.capabilities import CapabilityContext, CapabilityMetadata
from jarvis.agency.policy import RiskClass


class FileText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    content: str
    next_offset: int
    truncated: bool


class FileMutation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    created: bool
    bytes_written: int
    undo_reference: str


class FileAccess(Protocol):
    def read_text(self, path: Path, *, offset: int, limit: int) -> FileText: ...

    def write_text(self, path: Path, content: str) -> FileMutation: ...

    def undo(self, undo_reference: str) -> FileMutation: ...


class ReadTextInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=32_768)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=12_000, ge=1, le=32_000)


class WriteTextInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=32_768)
    content: str = Field(max_length=1_000_000)


class UndoFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    undo_reference: str = Field(min_length=36, max_length=36)


class ReadTextCapability:
    metadata = CapabilityMetadata(
        name="files.read_text",
        description="Read a bounded UTF-8 text chunk from an allowed local file",
        risk=RiskClass.OBSERVE,
        timeout_seconds=3,
        reversible=False,
    )
    input_model = ReadTextInput
    output_model = FileText

    def __init__(self, files: FileAccess) -> None:
        self._files = files

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> FileText:
        request = ReadTextInput.model_validate(arguments)
        return await asyncio.to_thread(
            self._files.read_text,
            Path(request.path),
            offset=request.offset,
            limit=request.limit,
        )


class WriteTextCapability:
    metadata = CapabilityMetadata(
        name="files.write_text",
        description="Atomically write one UTF-8 file inside an allowed root with an undo record",
        risk=RiskClass.LOCAL_REVERSIBLE,
        timeout_seconds=5,
        reversible=True,
    )
    input_model = WriteTextInput
    output_model = FileMutation

    def __init__(self, files: FileAccess) -> None:
        self._files = files

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> FileMutation:
        request = WriteTextInput.model_validate(arguments)
        return await asyncio.to_thread(
            self._files.write_text,
            Path(request.path),
            request.content,
        )


class UndoFileCapability:
    metadata = CapabilityMetadata(
        name="files.undo",
        description="Apply one exact JARVIS file undo record and produce a new inverse undo record",
        risk=RiskClass.LOCAL_REVERSIBLE,
        timeout_seconds=5,
        reversible=True,
    )
    input_model = UndoFileInput
    output_model = FileMutation

    def __init__(self, files: FileAccess) -> None:
        self._files = files

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> FileMutation:
        request = UndoFileInput.model_validate(arguments)
        return await asyncio.to_thread(self._files.undo, request.undo_reference)
