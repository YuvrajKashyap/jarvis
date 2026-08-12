from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from jarvis.agency.capabilities import (
    CapabilityContext,
    CapabilityMetadata,
    CapabilityRegistry,
    ExecutionStatus,
    InvocationCoordinator,
    InvocationEngine,
)
from jarvis.agency.files import ReadTextCapability, UndoFileCapability, WriteTextCapability
from jarvis.agency.policy import (
    ApprovalChoice,
    AuthorizationContext,
    CapabilityRequest,
    PolicyDecisionKind,
    PolicyEngine,
    RiskClass,
)
from jarvis.agency.terminal import TerminalCommand, TerminalCommandCapability
from jarvis.platform.acceptance import LocalAcceptanceEvidence
from jarvis.platform.filesystem import LocalFileStore
from jarvis.platform.process import LocalCommandRunner
from jarvis.platform.sqlite import SQLiteApprovalStore, SQLiteStore

ROOT = Path(__file__).resolve().parents[1]


class _Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _SlowAcceptanceCapability:
    metadata = CapabilityMetadata(
        name="acceptance.slow",
        description="A cancellable in-process acceptance operation",
        risk=RiskClass.OBSERVE,
        timeout_seconds=5,
        reversible=False,
    )
    input_model = _Empty
    output_model = _Empty

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> BaseModel:
        await asyncio.sleep(5)
        return _Empty()


async def run_capability_acceptance(root: Path) -> dict[str, bool]:
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    note = root / "note.txt"
    await asyncio.to_thread(note.write_text, "original", encoding="utf-8", newline="\n")
    database = SQLiteStore(root / "jarvis.db")
    await asyncio.to_thread(database.initialize)
    policy = PolicyEngine(SQLiteApprovalStore(database))
    files = LocalFileStore(roots=(root,), undo_directory=root / "undo")
    terminal = LocalCommandRunner.from_path(roots=(root,), names=("git",))
    registry = CapabilityRegistry()
    registry.register(ReadTextCapability(files))
    registry.register(WriteTextCapability(files))
    registry.register(UndoFileCapability(files))
    registry.register(TerminalCommandCapability(terminal))
    registry.register(_SlowAcceptanceCapability())
    engine = InvocationEngine(registry=registry, policy=policy, audit=database)
    coordinator = InvocationCoordinator(engine=engine, policy=policy)
    now = datetime.now(UTC)

    observed = await coordinator.propose(
        capability="files.read_text",
        arguments={"path": str(note), "offset": 0, "limit": 1024},
        device_id="desktop",
        requested_at=now,
        direct_request=False,
    )
    observe_ok = (
        observed.result.status is ExecutionStatus.SUCCEEDED
        and (observed.result.output or {}).get("content") == "original"
    )

    rejected_offer = await coordinator.propose(
        capability="files.write_text",
        arguments={"path": str(note), "content": "rejected"},
        device_id="desktop",
        requested_at=now,
        direct_request=False,
    )
    if rejected_offer.approval is None:
        raise RuntimeError("local mutation did not request approval")
    rejected = await coordinator.decide(
        approval_id=rejected_offer.approval.approval_id,
        choice=ApprovalChoice.REJECT,
        device_id="desktop",
        now=now,
    )
    rejection_ok = (
        rejected.status is ExecutionStatus.DENIED
        and (await asyncio.to_thread(note.read_text, encoding="utf-8")) == "original"
    )

    written = await coordinator.propose(
        capability="files.write_text",
        arguments={"path": str(note), "content": "updated"},
        device_id="desktop",
        requested_at=now,
        direct_request=True,
    )
    write_ok = (
        written.result.status is ExecutionStatus.SUCCEEDED
        and (await asyncio.to_thread(note.read_text, encoding="utf-8")) == "updated"
    )
    undo_reference = (written.result.output or {}).get("undo_reference")
    if not isinstance(undo_reference, str):
        raise RuntimeError("file mutation did not emit an undo reference")
    undone = await coordinator.propose(
        capability="files.undo",
        arguments={"undo_reference": undo_reference},
        device_id="desktop",
        requested_at=now,
        direct_request=True,
    )
    undo_ok = (
        undone.result.status is ExecutionStatus.SUCCEEDED
        and (await asyncio.to_thread(note.read_text, encoding="utf-8")) == "original"
    )

    terminal_offer = await coordinator.propose(
        capability="terminal.execute",
        arguments={
            "executable": "git",
            "arguments": ["--version"],
            "cwd": str(root),
            "timeout_seconds": 10,
            "output_limit_bytes": 4096,
        },
        device_id="desktop",
        requested_at=now,
        direct_request=True,
    )
    if terminal_offer.approval is None:
        raise RuntimeError("terminal invocation did not request approval")
    device_binding_ok = False
    try:
        await coordinator.decide(
            approval_id=terminal_offer.approval.approval_id,
            choice=ApprovalChoice.APPROVE,
            device_id="forged-phone",
            now=now,
        )
    except PermissionError:
        device_binding_ok = True
    terminal_result = await coordinator.decide(
        approval_id=terminal_offer.approval.approval_id,
        choice=ApprovalChoice.APPROVE,
        device_id="desktop",
        now=now,
    )
    terminal_ok = (
        terminal_result.status is ExecutionStatus.SUCCEEDED
        and "git version" in str((terminal_result.output or {}).get("stdout", "")).casefold()
    )

    exact_request = CapabilityRequest(
        invocation_id=uuid4(),
        capability="messages.send",
        risk=RiskClass.EXTERNAL_IRREVERSIBLE,
        arguments={"destination": "acceptance", "body": "safe"},
    )
    approval = policy.request_approval(exact_request, expires_at=now + timedelta(minutes=5))
    policy.record_decision(
        approval.approval_id,
        ApprovalChoice.APPROVE,
        device_id="desktop",
        decided_at=now,
    )
    forged = exact_request.model_copy(update={"arguments": {"destination": "other"}})
    forged_decision = policy.evaluate(
        forged,
        AuthorizationContext(approval_id=approval.approval_id),
        now=now,
    )
    allowed_decision = policy.evaluate(
        exact_request,
        AuthorizationContext(approval_id=approval.approval_id),
        now=now,
    )
    replay_decision = policy.evaluate(
        exact_request,
        AuthorizationContext(approval_id=approval.approval_id),
        now=now,
    )

    destructive_rejected = False
    try:
        TerminalCommand(executable="git", arguments=("reset", "--hard"), cwd=root)
    except ValueError:
        destructive_rejected = True

    cancellation_task = asyncio.create_task(
        coordinator.propose(
            capability="acceptance.slow",
            arguments={},
            device_id="desktop",
            requested_at=now,
            direct_request=False,
        )
    )
    await asyncio.sleep(0.01)
    cancellation_task.cancel()
    cancelled = await cancellation_task

    audits = await asyncio.to_thread(database.list_action_audit)
    result = {
        "observe": observe_ok,
        "approval_rejection": rejection_ok,
        "write": write_ok,
        "undo": undo_ok,
        "terminal": terminal_ok,
        "device_binding": device_binding_ok,
        "forged_approval": forged_decision.reason == "approval_mismatch",
        "approval_replay": (
            allowed_decision.kind is PolicyDecisionKind.ALLOW
            and replay_decision.reason == "approval_replayed"
        ),
        "destructive_rejection": destructive_rejected,
        "cancellation": cancelled.result.status is ExecutionStatus.CANCELLED,
        "audit": (
            len(audits) >= 6
            and any(audit.result_status == "cancelled" for audit in audits)
            and any(audit.undo_reference for audit in audits)
        ),
    }
    await asyncio.to_thread(database.close)
    if not all(result.values()):
        raise RuntimeError(f"capability acceptance failed: {result}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run isolated JARVIS capability acceptance")
    parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="jarvis-capabilities-") as directory:
        result = asyncio.run(run_capability_acceptance(Path(directory)))
    artifact_directory = ROOT / "artifacts" / "acceptance"
    artifact_directory.mkdir(parents=True, exist_ok=True)
    path = artifact_directory / "capability-core.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    LocalAcceptanceEvidence(_data_directory() / "acceptance").record_pass("capabilities")
    print(path)


def _data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base / "JARVIS"


if __name__ == "__main__":
    main()
