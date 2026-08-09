import asyncio
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from jarvis.agency.capabilities import CapabilityContext, CapabilityMetadata
from jarvis.agency.policy import RiskClass
from jarvis.memory.store import MemoryCandidate, MemoryRepository


class RememberMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str = Field(min_length=1, max_length=80)
    subject: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=16_000)


class RememberMemoryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["created", "existing", "conflict"]
    fact_id: UUID
    conflict_id: UUID | None = None
    undo_reference: str | None = Field(default=None, max_length=64)


class UndoRememberMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    undo_reference: str = Field(
        min_length=48,
        max_length=64,
        pattern=r"^memory-(fact|conflict):[0-9a-fA-F-]{36}$",
    )


class UndoRememberMemoryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    removed: bool
    undo_reference: str


class RememberMemoryCapability:
    metadata = CapabilityMetadata(
        name="memory.remember",
        description=(
            "Store one source-grounded local memory; conflicting content is queued and never "
            "silently replaces an existing fact"
        ),
        risk=RiskClass.LOCAL_REVERSIBLE,
        timeout_seconds=5,
        reversible=True,
    )
    input_model = RememberMemoryInput
    output_model = RememberMemoryResult

    def __init__(self, memory: MemoryRepository) -> None:
        self._memory = memory

    async def execute(
        self,
        arguments: BaseModel,
        context: CapabilityContext,
    ) -> RememberMemoryResult:
        request = RememberMemoryInput.model_validate(arguments)
        existing = await asyncio.to_thread(
            self._memory.find,
            category=request.category,
            subject=request.subject,
        )
        if existing is not None and existing.content == request.content.strip():
            return RememberMemoryResult(kind="existing", fact_id=existing.fact_id)

        source_event_id = context.source_event_id or context.invocation_id
        mutation = await asyncio.to_thread(
            self._memory.remember,
            MemoryCandidate(
                category=request.category,
                subject=request.subject,
                content=request.content,
                source_event_ids=(source_event_id,),
                observed_at=context.requested_at,
            ),
        )
        if mutation.kind == "created":
            undo_reference = f"memory-fact:{mutation.fact_id}"
        elif mutation.kind == "conflict" and mutation.conflict_id is not None:
            undo_reference = f"memory-conflict:{mutation.conflict_id}"
        else:
            undo_reference = None
        return RememberMemoryResult(
            kind=mutation.kind,
            fact_id=mutation.fact_id,
            conflict_id=mutation.conflict_id,
            undo_reference=undo_reference,
        )


class UndoRememberMemoryCapability:
    metadata = CapabilityMetadata(
        name="memory.undo_remember",
        description="Remove one exact memory fact or queued conflict created by JARVIS",
        risk=RiskClass.EXTERNAL_IRREVERSIBLE,
        timeout_seconds=5,
        reversible=False,
    )
    input_model = UndoRememberMemoryInput
    output_model = UndoRememberMemoryResult

    def __init__(self, memory: MemoryRepository) -> None:
        self._memory = memory

    async def execute(
        self,
        arguments: BaseModel,
        context: CapabilityContext,
    ) -> UndoRememberMemoryResult:
        request = UndoRememberMemoryInput.model_validate(arguments)
        prefix, raw_id = request.undo_reference.split(":", maxsplit=1)
        target_id = UUID(raw_id)
        if prefix == "memory-fact":
            existed = await asyncio.to_thread(self._memory.get, target_id)
            await asyncio.to_thread(
                self._memory.forget,
                target_id,
                forgotten_at=context.requested_at,
            )
            removed = existed is not None
        else:
            removed = await asyncio.to_thread(self._memory.delete_conflict, target_id)
        return UndoRememberMemoryResult(
            removed=removed,
            undo_reference=request.undo_reference,
        )
