import asyncio
import json
import threading
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger as APSCronTrigger
from apscheduler.triggers.date import DateTrigger as APSDateTrigger
from apscheduler.triggers.interval import IntervalTrigger as APSIntervalTrigger
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
)
from sqlmodel import Field as SqlField
from sqlmodel import Session, SQLModel, col, select

from jarvis.agency.capabilities import (
    CapabilityContext,
    CapabilityMetadata,
    CapabilityRegistry,
    CoordinatedExecution,
    ExecutionResult,
    ExecutionStatus,
)
from jarvis.agency.policy import RiskClass
from jarvis.platform.sqlite import SQLiteStore


class TriggerValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DateTrigger(TriggerValue):
    kind: Literal["date"]
    run_at: datetime

    @field_validator("run_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _require_aware(value)


class IntervalTrigger(TriggerValue):
    kind: Literal["interval"]
    seconds: float = Field(ge=1, le=31_536_000)


class CronTrigger(TriggerValue):
    kind: Literal["cron"]
    expression: str = Field(min_length=9, max_length=200)

    @field_validator("expression")
    @classmethod
    def require_five_fields(cls, value: str) -> str:
        if len(value.split()) != 5:
            raise ValueError("cron expressions must contain exactly five fields")
        APSCronTrigger.from_crontab(value, timezone=UTC)
        return value


Trigger = Annotated[DateTrigger | IntervalTrigger | CronTrigger, Field(discriminator="kind")]
TRIGGER_ADAPTER = TypeAdapter(Trigger)


class ScheduledCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schedule_id: UUID
    name: str = Field(min_length=1, max_length=240)
    capability: str = Field(min_length=3, max_length=160, pattern=r"^[a-z][a-z0-9_.-]+$")
    arguments: dict[str, JsonValue]
    trigger: Trigger
    standing_rule_id: str | None = Field(default=None, min_length=1, max_length=160)
    enabled: bool = True
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _require_aware(value)


class CreateSchedule(TriggerValue):
    name: str = Field(min_length=1, max_length=240)
    capability: str = Field(min_length=3, max_length=160, pattern=r"^[a-z][a-z0-9_.-]+$")
    arguments: dict[str, JsonValue]
    trigger: Trigger


class ScheduleMutation(TriggerValue):
    schedule_id: UUID
    removed: bool = False
    undo_reference: str = Field(min_length=36, max_length=36)


class UndoScheduleCreation(TriggerValue):
    schedule_id: UUID


class ScheduledCapabilityRow(SQLModel, table=True):
    __tablename__ = "scheduled_capability"

    schedule_id: str = SqlField(primary_key=True)
    name: str
    capability: str = SqlField(index=True)
    arguments_json: str
    trigger_json: str
    standing_rule_id: str | None = None
    enabled: bool = SqlField(index=True)
    created_at: str


class ScheduleRepository:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def put(self, schedule: ScheduledCapability) -> None:
        with self._store._write_lock, Session(self._store.engine) as session:
            session.merge(_to_row(schedule))
            session.commit()

    def get(self, schedule_id: UUID) -> ScheduledCapability | None:
        with Session(self._store.engine) as session:
            row = session.get(ScheduledCapabilityRow, str(schedule_id))
            return None if row is None else _from_row(row)

    def list_enabled(self) -> list[ScheduledCapability]:
        with Session(self._store.engine) as session:
            rows = session.exec(
                select(ScheduledCapabilityRow)
                .where(ScheduledCapabilityRow.enabled)
                .order_by(col(ScheduledCapabilityRow.created_at))
            ).all()
            return [_from_row(row) for row in rows]

    def remove(self, schedule_id: UUID) -> None:
        with self._store._write_lock, Session(self._store.engine) as session:
            row = session.get(ScheduledCapabilityRow, str(schedule_id))
            if row is not None:
                session.delete(row)
                session.commit()


class DurableScheduler:
    def __init__(
        self,
        *,
        repository: ScheduleRepository,
        dispatch: Callable[[ScheduledCapability], None],
    ) -> None:
        self._repository = repository
        self._dispatch_callback = dispatch
        self._scheduler = BackgroundScheduler(
            jobstores={"default": MemoryJobStore()},
            timezone=UTC,
            daemon=True,
        )
        self._lock = threading.RLock()
        self._started = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._scheduler.start(paused=True)
            for schedule in self._repository.list_enabled():
                self._materialize(schedule)
            self._scheduler.resume()
            self._started = True

    def shutdown(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._scheduler.shutdown(wait=False)
            self._started = False

    def put(self, schedule: ScheduledCapability) -> None:
        self._repository.put(schedule)
        with self._lock:
            if not self._started:
                return
            existing = self._scheduler.get_job(str(schedule.schedule_id))
            if existing is not None:
                self._scheduler.remove_job(str(schedule.schedule_id))
            if schedule.enabled:
                self._materialize(schedule)

    def remove(self, schedule_id: UUID) -> None:
        self._repository.remove(schedule_id)
        with self._lock:
            if self._started and self._scheduler.get_job(str(schedule_id)) is not None:
                self._scheduler.remove_job(str(schedule_id))

    def active_schedule_ids(self) -> set[UUID]:
        with self._lock:
            return {UUID(job.id) for job in self._scheduler.get_jobs()}

    def _materialize(self, schedule: ScheduledCapability) -> None:
        self._scheduler.add_job(
            self._dispatch,
            _aps_trigger(schedule.trigger),
            id=str(schedule.schedule_id),
            args=[schedule.schedule_id],
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=60,
        )

    def _dispatch(self, schedule_id: UUID) -> None:
        schedule = self._repository.get(schedule_id)
        if schedule is not None and schedule.enabled:
            self._dispatch_callback(schedule)


class ScheduledExecutionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schedule_id: UUID
    schedule_name: str = Field(min_length=1, max_length=240)
    capability: str = Field(min_length=3, max_length=160)
    occurred_at: datetime
    execution: CoordinatedExecution

    @field_validator("occurred_at")
    @classmethod
    def require_event_timezone(cls, value: datetime) -> datetime:
        return _require_aware(value)


class ScheduleControl(Protocol):
    def start(self) -> None: ...

    def shutdown(self) -> None: ...


class ScheduledActionCoordinator(Protocol):
    async def propose(
        self,
        *,
        capability: str,
        arguments: dict[str, JsonValue],
        device_id: str,
        requested_at: datetime,
        direct_request: bool,
        standing_rule_id: str | None = None,
        scheduled: bool = False,
    ) -> CoordinatedExecution: ...


class ScheduledInvocationRuntime:
    """Bridges APScheduler's worker thread into bounded async capability execution."""

    def __init__(
        self,
        *,
        repository: ScheduleRepository,
        actions: ScheduledActionCoordinator,
        maximum_in_flight: int = 4,
        event_buffer_size: int = 64,
    ) -> None:
        if maximum_in_flight < 1:
            raise ValueError("maximum_in_flight must be positive")
        if event_buffer_size < 1:
            raise ValueError("event_buffer_size must be positive")
        self._actions = actions
        self._scheduler: ScheduleControl = DurableScheduler(
            repository=repository,
            dispatch=self.dispatch,
        )
        self._maximum_in_flight = maximum_in_flight
        self._events: asyncio.Queue[ScheduledExecutionEvent] = asyncio.Queue(
            maxsize=event_buffer_size
        )
        self._event_buffer_size = event_buffer_size
        self._subscribers: set[asyncio.Queue[ScheduledExecutionEvent]] = set()
        self._pending_approvals: dict[UUID, ScheduledExecutionEvent] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        if self._loop is not None:
            return
        self._loop = asyncio.get_running_loop()
        try:
            await asyncio.to_thread(self._scheduler.start)
        except BaseException:
            self._loop = None
            raise

    async def stop(self) -> None:
        if self._loop is None:
            return
        await asyncio.to_thread(self._scheduler.shutdown)
        self._loop = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def dispatch(self, schedule: ScheduledCapability) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._accept, schedule)

    async def next_event(self) -> ScheduledExecutionEvent:
        return await self._events.get()

    async def subscribe(self) -> AsyncIterator[ScheduledExecutionEvent]:
        events: asyncio.Queue[ScheduledExecutionEvent] = asyncio.Queue(
            maxsize=self._event_buffer_size
        )
        self._subscribers.add(events)
        for event in self._pending_approvals.values():
            if not events.full():
                events.put_nowait(event)
        try:
            while True:
                yield await events.get()
        finally:
            self._subscribers.discard(events)

    def resolve(self, approval_id: UUID) -> None:
        self._pending_approvals.pop(approval_id, None)

    def put(self, schedule: ScheduledCapability) -> None:
        scheduler = self._scheduler
        if not isinstance(scheduler, DurableScheduler):
            raise RuntimeError("schedule persistence is unavailable")
        scheduler.put(schedule)

    def remove(self, schedule_id: UUID) -> None:
        scheduler = self._scheduler
        if not isinstance(scheduler, DurableScheduler):
            raise RuntimeError("schedule persistence is unavailable")
        scheduler.remove(schedule_id)

    def active_schedule_ids(self) -> set[UUID]:
        scheduler = self._scheduler
        if not isinstance(scheduler, DurableScheduler):
            return set()
        return scheduler.active_schedule_ids()

    def _accept(self, schedule: ScheduledCapability) -> None:
        if len(self._tasks) >= self._maximum_in_flight:
            self._broadcast(
                ScheduledExecutionEvent(
                    schedule_id=schedule.schedule_id,
                    schedule_name=schedule.name,
                    capability=schedule.capability,
                    occurred_at=datetime.now(UTC),
                    execution=CoordinatedExecution(
                        result=ExecutionResult(
                            invocation_id=uuid4(),
                            status=ExecutionStatus.FAILED,
                            reason="scheduler_capacity",
                        )
                    ),
                )
            )
            return
        task = asyncio.create_task(
            self._execute(schedule),
            name=f"jarvis-schedule-{schedule.schedule_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _execute(self, schedule: ScheduledCapability) -> None:
        occurred_at = datetime.now(UTC)
        execution = await self._actions.propose(
            capability=schedule.capability,
            arguments=schedule.arguments,
            device_id="scheduler",
            requested_at=occurred_at,
            direct_request=False,
            standing_rule_id=schedule.standing_rule_id,
            scheduled=True,
        )
        event = ScheduledExecutionEvent(
            schedule_id=schedule.schedule_id,
            schedule_name=schedule.name,
            capability=schedule.capability,
            occurred_at=occurred_at,
            execution=execution,
        )
        self._broadcast(event)

    def _broadcast(self, event: ScheduledExecutionEvent) -> None:
        if event.execution.approval is not None:
            self._pending_approvals[event.execution.approval.approval_id] = event
        self._publish(self._events, event)
        for subscriber in tuple(self._subscribers):
            self._publish(subscriber, event)

    @staticmethod
    def _publish(
        events: asyncio.Queue[ScheduledExecutionEvent],
        event: ScheduledExecutionEvent,
    ) -> None:
        if events.full():
            events.get_nowait()
        events.put_nowait(event)


class CreateScheduleCapability:
    metadata = CapabilityMetadata(
        name="schedules.create",
        description=(
            "Create one durable schedule for a validated JARVIS capability; external actions "
            "still require approval when they run"
        ),
        risk=RiskClass.LOCAL_REVERSIBLE,
        timeout_seconds=5,
        reversible=True,
    )
    input_model = CreateSchedule
    output_model = ScheduleMutation

    def __init__(
        self,
        *,
        scheduler: ScheduledInvocationRuntime,
        registry: CapabilityRegistry,
    ) -> None:
        self._scheduler = scheduler
        self._registry = registry

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> ScheduleMutation:
        request = CreateSchedule.model_validate(arguments)
        target = self._registry.get(request.capability)
        if target is None or request.capability.startswith("schedules."):
            raise ValueError("scheduled capability is unavailable")
        if target.metadata.risk is RiskClass.FORBIDDEN:
            raise ValueError("forbidden capabilities cannot be scheduled")
        target.input_model.model_validate(request.arguments)
        schedule_id = UUID(bytes=context.invocation_id.bytes)
        standing_rule_id = (
            f"schedule:{schedule_id}"
            if target.metadata.risk is RiskClass.LOCAL_REVERSIBLE
            else None
        )
        schedule = ScheduledCapability(
            schedule_id=schedule_id,
            name=request.name,
            capability=request.capability,
            arguments=request.arguments,
            trigger=request.trigger,
            standing_rule_id=standing_rule_id,
            enabled=True,
            created_at=context.requested_at,
        )
        await asyncio.to_thread(self._scheduler.put, schedule)
        return ScheduleMutation(
            schedule_id=schedule_id,
            undo_reference=str(schedule_id),
        )


class UndoScheduleCreationCapability:
    metadata = CapabilityMetadata(
        name="schedules.undo_create",
        description="Remove the exact durable schedule created by a previous JARVIS action",
        risk=RiskClass.LOCAL_REVERSIBLE,
        timeout_seconds=5,
        reversible=True,
    )
    input_model = UndoScheduleCreation
    output_model = ScheduleMutation

    def __init__(self, *, scheduler: ScheduledInvocationRuntime) -> None:
        self._scheduler = scheduler

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> ScheduleMutation:
        request = UndoScheduleCreation.model_validate(arguments)
        await asyncio.to_thread(self._scheduler.remove, request.schedule_id)
        return ScheduleMutation(
            schedule_id=request.schedule_id,
            removed=True,
            undo_reference=str(request.schedule_id),
        )


def _aps_trigger(trigger: TriggerValue) -> object:
    if isinstance(trigger, DateTrigger):
        return APSDateTrigger(run_date=trigger.run_at, timezone=UTC)
    if isinstance(trigger, IntervalTrigger):
        return APSIntervalTrigger(seconds=trigger.seconds, timezone=UTC)
    if isinstance(trigger, CronTrigger):
        return APSCronTrigger.from_crontab(trigger.expression, timezone=UTC)
    raise TypeError("unsupported schedule trigger")


def _to_row(schedule: ScheduledCapability) -> ScheduledCapabilityRow:
    return ScheduledCapabilityRow(
        schedule_id=str(schedule.schedule_id),
        name=schedule.name,
        capability=schedule.capability,
        arguments_json=json.dumps(
            schedule.arguments,
            separators=(",", ":"),
            sort_keys=True,
        ),
        trigger_json=json.dumps(
            schedule.trigger.model_dump(mode="json"),
            separators=(",", ":"),
            sort_keys=True,
        ),
        standing_rule_id=schedule.standing_rule_id,
        enabled=schedule.enabled,
        created_at=_dump_datetime(schedule.created_at),
    )


def _from_row(row: ScheduledCapabilityRow) -> ScheduledCapability:
    return ScheduledCapability(
        schedule_id=UUID(row.schedule_id),
        name=row.name,
        capability=row.capability,
        arguments=json.loads(row.arguments_json),
        trigger=TRIGGER_ADAPTER.validate_python(json.loads(row.trigger_json)),
        standing_rule_id=row.standing_rule_id,
        enabled=row.enabled,
        created_at=_load_datetime(row.created_at),
    )


def _dump_datetime(value: datetime) -> str:
    return _require_aware(value).astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    return value
