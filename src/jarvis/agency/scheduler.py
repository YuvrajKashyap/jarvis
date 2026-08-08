import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

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
