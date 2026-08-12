from datetime import UTC, datetime
from uuid import uuid4

from sqlmodel import Field as SqlField
from sqlmodel import Session, SQLModel, col, select

from jarvis.agency.proactivity import SuggestionReceipt, TopicPreference
from jarvis.platform.sqlite import SQLiteStore


class ProactivityReceiptRow(SQLModel, table=True):
    __tablename__ = "proactivity_receipt"

    receipt_id: str = SqlField(primary_key=True)
    fingerprint: str = SqlField(index=True)
    suggested_at: str = SqlField(index=True)


class ProactivityPreferenceRow(SQLModel, table=True):
    __tablename__ = "proactivity_preference"

    topic: str = SqlField(primary_key=True)
    muted: bool
    snoozed_until: str | None
    affinity: int


class SQLiteProactivityLedger:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def recent(self, since: datetime) -> tuple[SuggestionReceipt, ...]:
        boundary = _dump_datetime(since)
        with Session(self._store.engine) as session:
            rows = session.exec(
                select(ProactivityReceiptRow)
                .where(col(ProactivityReceiptRow.suggested_at) >= boundary)
                .order_by(col(ProactivityReceiptRow.suggested_at))
            ).all()
        return tuple(
            SuggestionReceipt(
                fingerprint=row.fingerprint,
                suggested_at=_load_datetime(row.suggested_at),
            )
            for row in rows
        )

    def record(self, receipt: SuggestionReceipt) -> None:
        row = ProactivityReceiptRow(
            receipt_id=str(uuid4()),
            fingerprint=receipt.fingerprint,
            suggested_at=_dump_datetime(receipt.suggested_at),
        )
        with self._store._write_lock, Session(self._store.engine) as session:
            session.add(row)
            session.commit()

    def preference(self, topic: str) -> TopicPreference:
        with Session(self._store.engine) as session:
            row = session.get(ProactivityPreferenceRow, topic)
            if row is None:
                return TopicPreference(topic=topic)
            return TopicPreference(
                topic=row.topic,
                muted=row.muted,
                snoozed_until=(
                    None if row.snoozed_until is None else _load_datetime(row.snoozed_until)
                ),
                affinity=row.affinity,
            )

    def set_preference(self, preference: TopicPreference) -> None:
        with self._store._write_lock, Session(self._store.engine) as session:
            row = session.get(ProactivityPreferenceRow, preference.topic)
            if row is None:
                row = ProactivityPreferenceRow(
                    topic=preference.topic,
                    muted=preference.muted,
                    snoozed_until=(
                        None
                        if preference.snoozed_until is None
                        else _dump_datetime(preference.snoozed_until)
                    ),
                    affinity=preference.affinity,
                )
            else:
                row.muted = preference.muted
                row.snoozed_until = (
                    None
                    if preference.snoozed_until is None
                    else _dump_datetime(preference.snoozed_until)
                )
                row.affinity = preference.affinity
            session.add(row)
            session.commit()


def _dump_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("proactivity timestamps must include a UTC offset")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
