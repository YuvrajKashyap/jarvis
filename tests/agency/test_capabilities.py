import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, Field

from jarvis.agency.capabilities import (
    CapabilityContext,
    CapabilityMetadata,
    CapabilityRegistry,
    ExecutionStatus,
    Invocation,
    InvocationCoordinator,
    InvocationEngine,
)
from jarvis.agency.policy import (
    ApprovalChoice,
    AuthorizationContext,
    InMemoryApprovalStore,
    PolicyEngine,
    RiskClass,
)
from jarvis.platform.sqlite import SQLiteStore

NOW = datetime(2026, 8, 7, 19, 0, tzinfo=UTC)
INVOCATION_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf0")


class RenameInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)


class RenameOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    renamed: bool
    undo_reference: str | None = None


class FakeRename:
    metadata = CapabilityMetadata(
        name="files.rename",
        description="Rename one local file",
        risk=RiskClass.LOCAL_REVERSIBLE,
        timeout_seconds=1,
        reversible=True,
    )
    input_model = RenameInput
    output_model = RenameOutput

    def __init__(self) -> None:
        self.calls: list[RenameInput] = []

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> BaseModel:
        rename = RenameInput.model_validate(arguments)
        self.calls.append(rename)
        return RenameOutput(
            renamed=True,
            undo_reference=f"rename:{rename.target}:{rename.source}",
        )


class FakeSend(FakeRename):
    metadata = CapabilityMetadata(
        name="messages.send",
        description="Send a message",
        risk=RiskClass.EXTERNAL_IRREVERSIBLE,
        timeout_seconds=1,
        reversible=False,
    )


class SlowCapability(FakeRename):
    metadata = CapabilityMetadata(
        name="test.slow",
        description="Exercise timeout cancellation",
        risk=RiskClass.OBSERVE,
        timeout_seconds=0.01,
        reversible=False,
    )

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> BaseModel:
        await asyncio.sleep(0.1)
        return RenameOutput(renamed=True)


def build_engine(tmp_path: Path, capability: FakeRename) -> tuple[InvocationEngine, PolicyEngine]:
    database = SQLiteStore(tmp_path / "jarvis.db")
    database.initialize()
    registry = CapabilityRegistry()
    registry.register(capability)
    policy = PolicyEngine(InMemoryApprovalStore())
    return InvocationEngine(registry=registry, policy=policy, audit=database), policy


def invocation(name: str) -> Invocation:
    return Invocation(
        invocation_id=INVOCATION_ID,
        capability=name,
        arguments={"source": "draft.txt", "target": "final.txt"},
        requested_at=NOW,
    )


def test_registry_rejects_duplicate_capability_names() -> None:
    registry = CapabilityRegistry()
    registry.register(FakeRename())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeRename())


def test_registry_exposes_stable_model_tool_schemas() -> None:
    registry = CapabilityRegistry()
    registry.register(FakeRename())

    schemas = registry.tool_schemas()

    assert schemas[0].name == "files.rename"
    assert schemas[0].parameters["additionalProperties"] is False
    assert schemas[0].parameters["required"] == ["source", "target"]


@pytest.mark.asyncio
async def test_local_reversible_action_executes_only_with_direct_authorization(
    tmp_path: Path,
) -> None:
    capability = FakeRename()
    engine, _policy = build_engine(tmp_path, capability)

    waiting = await engine.invoke(
        invocation("files.rename"),
        authorization=AuthorizationContext(),
        device_id="desktop",
        now=NOW,
    )
    completed = await engine.invoke(
        invocation("files.rename"),
        authorization=AuthorizationContext(direct_request=True),
        device_id="desktop",
        now=NOW,
    )

    assert waiting.status is ExecutionStatus.AWAITING_APPROVAL
    assert waiting.approval_id is not None
    assert completed.status is ExecutionStatus.SUCCEEDED
    assert completed.output == {"renamed": True, "undo_reference": "rename:final.txt:draft.txt"}
    assert len(capability.calls) == 1


@pytest.mark.asyncio
async def test_external_action_consumes_exact_approval_once(tmp_path: Path) -> None:
    capability = FakeSend()
    engine, policy = build_engine(tmp_path, capability)
    requested = invocation("messages.send")
    waiting = await engine.invoke(
        requested,
        authorization=AuthorizationContext(direct_request=True),
        device_id="desktop",
        now=NOW,
    )
    assert waiting.approval_id is not None
    policy.record_decision(
        waiting.approval_id,
        ApprovalChoice.APPROVE,
        device_id="desktop",
        decided_at=NOW,
    )

    allowed = await engine.invoke(
        requested,
        authorization=AuthorizationContext(approval_id=waiting.approval_id),
        device_id="desktop",
        now=NOW,
    )
    replayed = await engine.invoke(
        requested,
        authorization=AuthorizationContext(approval_id=waiting.approval_id),
        device_id="desktop",
        now=NOW,
    )

    assert allowed.status is ExecutionStatus.SUCCEEDED
    assert replayed.status is ExecutionStatus.DENIED
    assert replayed.reason == "approval_replayed"
    assert len(capability.calls) == 1


@pytest.mark.asyncio
async def test_capability_arguments_are_strictly_validated(tmp_path: Path) -> None:
    engine, _policy = build_engine(tmp_path, FakeRename())
    malformed = invocation("files.rename").model_copy(
        update={"arguments": {"source": "draft.txt", "target": "final.txt", "hidden": True}}
    )

    result = await engine.invoke(
        malformed,
        authorization=AuthorizationContext(direct_request=True),
        device_id="desktop",
        now=NOW,
    )

    assert result.status is ExecutionStatus.DENIED
    assert result.reason == "invalid_arguments"


@pytest.mark.asyncio
async def test_timeout_is_audited_and_does_not_escape(tmp_path: Path) -> None:
    engine, _policy = build_engine(tmp_path, SlowCapability())

    result = await engine.invoke(
        invocation("test.slow"),
        authorization=AuthorizationContext(),
        device_id="desktop",
        now=NOW + timedelta(seconds=2),
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.reason == "timeout"


@pytest.mark.asyncio
async def test_coordinator_resumes_the_exact_pending_invocation_after_approval(
    tmp_path: Path,
) -> None:
    capability = FakeSend()
    engine, policy = build_engine(tmp_path, capability)
    coordinator = InvocationCoordinator(engine=engine, policy=policy)

    proposed = await coordinator.propose(
        capability="messages.send",
        arguments={"source": "draft.txt", "target": "final.txt"},
        device_id="desktop",
        requested_at=NOW,
        direct_request=True,
    )
    assert proposed.approval is not None
    assert proposed.approval.capability == "messages.send"
    assert proposed.approval.risk is RiskClass.EXTERNAL_IRREVERSIBLE

    completed = await coordinator.decide(
        approval_id=proposed.approval.approval_id,
        choice=ApprovalChoice.APPROVE,
        device_id="desktop",
        now=NOW,
    )

    assert completed.status is ExecutionStatus.SUCCEEDED
    assert len(capability.calls) == 1
    with pytest.raises(LookupError, match="pending approval"):
        await coordinator.decide(
            approval_id=proposed.approval.approval_id,
            choice=ApprovalChoice.APPROVE,
            device_id="desktop",
            now=NOW,
        )


@pytest.mark.asyncio
async def test_coordinator_binds_approval_to_the_requesting_device(tmp_path: Path) -> None:
    engine, policy = build_engine(tmp_path, FakeSend())
    coordinator = InvocationCoordinator(engine=engine, policy=policy)
    proposed = await coordinator.propose(
        capability="messages.send",
        arguments={"source": "draft.txt", "target": "final.txt"},
        device_id="desktop",
        requested_at=NOW,
        direct_request=True,
    )
    assert proposed.approval is not None

    with pytest.raises(PermissionError, match="requesting device"):
        await coordinator.decide(
            approval_id=proposed.approval.approval_id,
            choice=ApprovalChoice.APPROVE,
            device_id="phone",
            now=NOW,
        )


@pytest.mark.asyncio
async def test_coordinator_executes_scheduled_local_action_under_standing_rule(
    tmp_path: Path,
) -> None:
    capability = FakeRename()
    engine, policy = build_engine(tmp_path, capability)
    coordinator = InvocationCoordinator(engine=engine, policy=policy)

    proposed = await coordinator.propose(
        capability="files.rename",
        arguments={"source": "draft.txt", "target": "final.txt"},
        device_id="scheduler",
        requested_at=NOW,
        direct_request=False,
        standing_rule_id="weekday-rename",
        scheduled=True,
    )

    assert proposed.result.status is ExecutionStatus.SUCCEEDED
    assert proposed.approval is None
    assert len(capability.calls) == 1


@pytest.mark.asyncio
async def test_scheduled_external_approval_can_be_resolved_from_an_active_device(
    tmp_path: Path,
) -> None:
    capability = FakeSend()
    engine, policy = build_engine(tmp_path, capability)
    coordinator = InvocationCoordinator(engine=engine, policy=policy)
    proposed = await coordinator.propose(
        capability="messages.send",
        arguments={"source": "draft.txt", "target": "final.txt"},
        device_id="scheduler",
        requested_at=NOW,
        direct_request=False,
        standing_rule_id="weekday-message",
        scheduled=True,
    )
    assert proposed.approval is not None
    assert coordinator.pending_is_scheduled(proposed.approval.approval_id)

    completed = await coordinator.decide(
        approval_id=proposed.approval.approval_id,
        choice=ApprovalChoice.APPROVE,
        device_id="phone",
        now=NOW,
    )

    assert completed.status is ExecutionStatus.SUCCEEDED
    assert len(capability.calls) == 1
