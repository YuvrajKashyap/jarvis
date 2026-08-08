from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from jarvis.agency.capabilities import CapabilityContext
from jarvis.agency.files import (
    FileMutation,
    FileText,
    ReadTextCapability,
    UndoFileCapability,
    WriteTextCapability,
)
from jarvis.agency.policy import RiskClass


class FakeFiles:
    def __init__(self) -> None:
        self.writes: list[tuple[Path, str]] = []
        self.undos: list[str] = []

    def read_text(self, path: Path, *, offset: int, limit: int) -> FileText:
        return FileText(
            path=str(path),
            content=f"{offset}:{limit}",
            next_offset=offset + limit,
            truncated=False,
        )

    def write_text(self, path: Path, content: str) -> FileMutation:
        self.writes.append((path, content))
        return FileMutation(
            path=str(path),
            created=False,
            bytes_written=len(content),
            undo_reference="019fd977-1d96-7892-950c-6afbb71f7cf2",
        )

    def undo(self, undo_reference: str) -> FileMutation:
        self.undos.append(undo_reference)
        return self.write_text(Path("restored.txt"), "restored")


CONTEXT = CapabilityContext(
    invocation_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf0"),
    device_id="desktop",
    requested_at=datetime(2026, 8, 7, tzinfo=UTC),
)


@pytest.mark.asyncio
async def test_file_capabilities_keep_observation_and_mutation_risks_explicit() -> None:
    files = FakeFiles()
    read = await ReadTextCapability(files).execute(
        ReadTextCapability.input_model(path="C:/notes.txt", offset=2, limit=10),
        CONTEXT,
    )
    written = await WriteTextCapability(files).execute(
        WriteTextCapability.input_model(path="C:/notes.txt", content="updated"),
        CONTEXT,
    )
    undone = await UndoFileCapability(files).execute(
        UndoFileCapability.input_model(undo_reference=written.undo_reference),
        CONTEXT,
    )

    assert ReadTextCapability.metadata.risk is RiskClass.OBSERVE
    assert WriteTextCapability.metadata.risk is RiskClass.LOCAL_REVERSIBLE
    assert WriteTextCapability.metadata.reversible is True
    assert read.content == "2:10"
    assert files.writes[0] == (Path("C:/notes.txt"), "updated")
    assert files.undos == [written.undo_reference]
    assert undone.bytes_written == 8
