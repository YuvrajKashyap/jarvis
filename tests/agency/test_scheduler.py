from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from jarvis.agency.scheduler import (
    DurableScheduler,
    IntervalTrigger,
    ScheduledCapability,
    ScheduleRepository,
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
