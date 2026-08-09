import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict

from jarvis.agency.capabilities import (
    CapabilityContext,
    CapabilityMetadata,
    CapabilityRegistry,
    CoordinatedExecution,
    ExecutionResult,
    ExecutionStatus,
)
from jarvis.agency.policy import RiskClass
from jarvis.agency.scheduler import (
    CreateSchedule,
    CreateScheduleCapability,
    DurableScheduler,
    IntervalTrigger,
    ScheduledCapability,
    ScheduledInvocationRuntime,
    ScheduleRepository,
    UndoScheduleCreation,
    UndoScheduleCreationCapability,
)
from jarvis.platform.sqlite import SQLiteStore

NOW = datetime(2026, 8, 7, 20, 0, tzinfo=UTC)
SCHEDULE_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf0")


def schedule() -> ScheduledCapability:
    return ScheduledCapability(
        schedule_id=SCHEDULE_ID,
        name="Watch the downloads folder",
        capability="files.scan_downloads",
        arguments={"pattern": "*.pdf"},
        trigger=IntervalTrigger(kind="interval", seconds=300),
        standing_rule_id="watch-downloads",
        enabled=True,
        created_at=NOW,
    )


def repository(path: Path) -> ScheduleRepository:
    database = SQLiteStore(path)
    database.initialize()
    return ScheduleRepository(database)


def test_schedule_is_validated_json_and_survives_restart(tmp_path: Path) -> None:
    first = repository(tmp_path / "jarvis.db")
    first.put(schedule())

    restarted = repository(tmp_path / "jarvis.db")

    assert restarted.get(SCHEDULE_ID) == schedule()
    assert restarted.list_enabled() == [schedule()]


def test_runtime_scheduler_materializes_only_enabled_typed_records(tmp_path: Path) -> None:
    records = repository(tmp_path / "jarvis.db")
    enabled = schedule()
    disabled = schedule().model_copy(
        update={
            "schedule_id": UUID("019fd977-1d96-7892-950c-6afbb71f7cf1"),
            "name": "Disabled routine",
            "enabled": False,
        }
    )
    records.put(enabled)
    records.put(disabled)
    dispatched: list[ScheduledCapability] = []
    scheduler = DurableScheduler(repository=records, dispatch=dispatched.append)

    scheduler.start()
    try:
        assert scheduler.active_schedule_ids() == {SCHEDULE_ID}
    finally:
        scheduler.shutdown()


def test_removing_schedule_removes_canonical_record_and_runtime_job(tmp_path: Path) -> None:
    records = repository(tmp_path / "jarvis.db")
    scheduler = DurableScheduler(repository=records, dispatch=lambda _schedule: None)
    scheduler.start()
    try:
        scheduler.put(schedule())
        assert scheduler.active_schedule_ids() == {SCHEDULE_ID}

        scheduler.remove(SCHEDULE_ID)

        assert scheduler.active_schedule_ids() == set()
        assert records.get(SCHEDULE_ID) is None
    finally:
        scheduler.shutdown()


def test_interval_trigger_rejects_unbounded_frequency() -> None:
    try:
        IntervalTrigger(kind="interval", seconds=0.01)
    except ValueError as error:
        assert "greater than or equal to 1" in str(error)
    else:
        raise AssertionError("sub-second schedules must be rejected")


class RecordingActions:
    def __init__(self) -> None:
        self.proposals: list[dict[str, object]] = []

    async def propose(self, **proposal: object) -> CoordinatedExecution:
        self.proposals.append(proposal)
        return CoordinatedExecution(
            result=ExecutionResult(
                invocation_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf2"),
                status=ExecutionStatus.SUCCEEDED,
                output={"observed": True},
            )
        )


class BlockingActions(RecordingActions):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def propose(self, **proposal: object) -> CoordinatedExecution:
        await self.release.wait()
        return await super().propose(**proposal)


@pytest.mark.asyncio
async def test_scheduled_runtime_dispatches_through_policy_aware_coordinator(
    tmp_path: Path,
) -> None:
    records = repository(tmp_path / "jarvis.db")
    actions = RecordingActions()
    runtime = ScheduledInvocationRuntime(repository=records, actions=actions)
    await runtime.start()
    try:
        runtime.dispatch(schedule())
        event = await runtime.next_event()
    finally:
        await runtime.stop()

    assert event.schedule_id == SCHEDULE_ID
    assert event.execution.result.status is ExecutionStatus.SUCCEEDED
    assert actions.proposals == [
        {
            "capability": "files.scan_downloads",
            "arguments": {"pattern": "*.pdf"},
            "device_id": "scheduler",
            "requested_at": event.occurred_at,
            "direct_request": False,
            "standing_rule_id": "watch-downloads",
            "scheduled": True,
        }
    ]


@pytest.mark.asyncio
async def test_scheduled_runtime_reports_capacity_instead_of_silently_dropping_job(
    tmp_path: Path,
) -> None:
    actions = BlockingActions()
    runtime = ScheduledInvocationRuntime(
        repository=repository(tmp_path / "jarvis.db"),
        actions=actions,
        maximum_in_flight=1,
    )
    await runtime.start()
    try:
        runtime.dispatch(schedule())
        runtime.dispatch(
            schedule().model_copy(
                update={"schedule_id": UUID("019fd977-1d96-7892-950c-6afbb71f7cf9")}
            )
        )
        event = await asyncio.wait_for(runtime.next_event(), timeout=1)
    finally:
        actions.release.set()
        await runtime.stop()

    assert event.execution.result.status is ExecutionStatus.FAILED
    assert event.execution.result.reason == "scheduler_capacity"


class ScanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern: str


class ScanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int


class ScanDownloads:
    metadata = CapabilityMetadata(
        name="files.scan_downloads",
        description="Scan downloads",
        risk=RiskClass.OBSERVE,
        timeout_seconds=1,
        reversible=False,
    )
    input_model = ScanInput
    output_model = ScanOutput

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> BaseModel:
        return ScanOutput(count=0)


@pytest.mark.asyncio
async def test_schedule_capabilities_create_validated_job_and_undo_it(tmp_path: Path) -> None:
    records = repository(tmp_path / "jarvis.db")
    registry = CapabilityRegistry()
    registry.register(ScanDownloads())
    runtime = ScheduledInvocationRuntime(repository=records, actions=RecordingActions())
    create = CreateScheduleCapability(scheduler=runtime, registry=registry)
    undo = UndoScheduleCreationCapability(scheduler=runtime)
    context = CapabilityContext(
        invocation_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf3"),
        device_id="desktop",
        requested_at=NOW,
    )
    await runtime.start()
    try:
        created = await create.execute(
            CreateSchedule(
                name="Watch downloads",
                capability="files.scan_downloads",
                arguments={"pattern": "*.pdf"},
                trigger=IntervalTrigger(kind="interval", seconds=300),
            ),
            context,
        )
        assert records.get(created.schedule_id) is not None
        assert created.schedule_id in runtime.active_schedule_ids()

        removed = await undo.execute(
            UndoScheduleCreation(schedule_id=created.schedule_id),
            context,
        )
    finally:
        await runtime.stop()

    assert removed.removed is True
    assert records.get(created.schedule_id) is None
