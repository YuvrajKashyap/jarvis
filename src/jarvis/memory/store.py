import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import UniqueConstraint, delete
from sqlmodel import Field as SqlField
from sqlmodel import Session, SQLModel, col, select

from jarvis.platform.sqlite import SQLiteStore


class MemoryValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemoryCandidate(MemoryValue):
    category: str = Field(min_length=1, max_length=80)
    subject: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=16_000)
    source_event_ids: tuple[UUID, ...] = Field(min_length=1)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        _require_aware(value)
        return value


class MemoryFact(MemoryValue):
    fact_id: UUID
    category: str
    subject: str
    content: str
    source_event_ids: tuple[UUID, ...]
    observed_at: datetime
    updated_at: datetime
    version: int


class MemoryMutation(MemoryValue):
    kind: Literal["created", "existing", "conflict"]
    fact_id: UUID
    conflict_id: UUID | None = None


class MemoryConflict(MemoryValue):
    conflict_id: UUID
    fact_id: UUID
    candidate_content: str
    source_event_ids: tuple[UUID, ...]
    observed_at: datetime


class MemoryDeletion(MemoryValue):
    deletion_id: UUID
    fact_id: UUID
    forgotten_at: datetime


class MemoryFactRow(SQLModel, table=True):
    __tablename__ = "memory_fact"
    __table_args__ = (UniqueConstraint("category_key", "subject_key"),)

    fact_id: str = SqlField(primary_key=True)
    category: str
    category_key: str = SqlField(index=True)
    subject: str
    subject_key: str = SqlField(index=True)
    content: str
    source_event_ids_json: str
    observed_at: str
    updated_at: str
    version: int


class MemoryConflictRow(SQLModel, table=True):
    __tablename__ = "memory_conflict"

    conflict_id: str = SqlField(primary_key=True)
    fact_id: str = SqlField(index=True)
    candidate_content: str
    source_event_ids_json: str
    observed_at: str


class MemoryRevisionRow(SQLModel, table=True):
    __tablename__ = "memory_revision"

    revision_id: str = SqlField(primary_key=True)
    fact_id: str = SqlField(index=True)
    prior_content: str
    prior_version: int
    corrected_at: str
    source_event_id: str


class MemoryDeletionRow(SQLModel, table=True):
    __tablename__ = "memory_deletion"

    deletion_id: str = SqlField(primary_key=True)
    fact_id: str = SqlField(index=True)
    forgotten_at: str


class MemoryRepository:
    def __init__(self, *, sqlite: SQLiteStore, markdown_directory: Path) -> None:
        self._sqlite = sqlite
        self._markdown_directory = markdown_directory.resolve()
        self._markdown_path = self._markdown_directory / "memory.md"

    def initialize(self) -> None:
        self._markdown_directory.mkdir(parents=True, exist_ok=True)
        with self._sqlite._write_lock, self._sqlite.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                "USING fts5(fact_id UNINDEXED, category, subject, content, tokenize='unicode61')"
            )
            connection.exec_driver_sql("DELETE FROM memory_fts")
            rows = connection.exec_driver_sql(
                "SELECT fact_id, category, subject, content FROM memory_fact"
            ).all()
            for row in rows:
                connection.exec_driver_sql(
                    "INSERT INTO memory_fts(fact_id, category, subject, content) "
                    "VALUES (?, ?, ?, ?)",
                    tuple(row),
                )
        self._render_markdown()

    def remember(self, candidate: MemoryCandidate) -> MemoryMutation:
        category = candidate.category.strip()
        subject = candidate.subject.strip()
        content = candidate.content.strip()
        with self._sqlite._write_lock, Session(self._sqlite.engine) as session:
            existing = session.exec(
                select(MemoryFactRow).where(
                    MemoryFactRow.category_key == category.casefold(),
                    MemoryFactRow.subject_key == subject.casefold(),
                )
            ).one_or_none()
            if existing is not None:
                if existing.content == content:
                    merged_sources = _merge_sources(
                        _load_source_ids(existing.source_event_ids_json),
                        candidate.source_event_ids,
                    )
                    existing.source_event_ids_json = _dump_source_ids(merged_sources)
                    existing.updated_at = _dump_datetime(candidate.observed_at)
                    session.add(existing)
                    session.commit()
                    self._render_markdown()
                    return MemoryMutation(kind="existing", fact_id=UUID(existing.fact_id))

                conflict_id = uuid4()
                session.add(
                    MemoryConflictRow(
                        conflict_id=str(conflict_id),
                        fact_id=existing.fact_id,
                        candidate_content=content,
                        source_event_ids_json=_dump_source_ids(candidate.source_event_ids),
                        observed_at=_dump_datetime(candidate.observed_at),
                    )
                )
                session.commit()
                return MemoryMutation(
                    kind="conflict",
                    fact_id=UUID(existing.fact_id),
                    conflict_id=conflict_id,
                )

            fact_id = uuid4()
            row = MemoryFactRow(
                fact_id=str(fact_id),
                category=category,
                category_key=category.casefold(),
                subject=subject,
                subject_key=subject.casefold(),
                content=content,
                source_event_ids_json=_dump_source_ids(candidate.source_event_ids),
                observed_at=_dump_datetime(candidate.observed_at),
                updated_at=_dump_datetime(candidate.observed_at),
                version=1,
            )
            session.add(row)
            session.flush()
            self._replace_fts(session, row)
            session.commit()
        self._render_markdown()
        return MemoryMutation(kind="created", fact_id=fact_id)

    def get(self, fact_id: UUID) -> MemoryFact | None:
        with Session(self._sqlite.engine) as session:
            row = session.get(MemoryFactRow, str(fact_id))
            return None if row is None else _fact_from_row(row)

    def list_conflicts(self) -> list[MemoryConflict]:
        with Session(self._sqlite.engine) as session:
            rows = session.exec(
                select(MemoryConflictRow).order_by(MemoryConflictRow.observed_at)
            ).all()
            return [
                MemoryConflict(
                    conflict_id=UUID(row.conflict_id),
                    fact_id=UUID(row.fact_id),
                    candidate_content=row.candidate_content,
                    source_event_ids=_load_source_ids(row.source_event_ids_json),
                    observed_at=_load_datetime(row.observed_at),
                )
                for row in rows
            ]

    def correct(
        self,
        fact_id: UUID,
        *,
        content: str,
        source_event_id: UUID,
        corrected_at: datetime,
    ) -> MemoryFact:
        _require_aware(corrected_at)
        corrected_content = content.strip()
        if not corrected_content:
            raise ValueError("corrected memory cannot be empty")
        with self._sqlite._write_lock, Session(self._sqlite.engine) as session:
            row = session.get(MemoryFactRow, str(fact_id))
            if row is None:
                raise LookupError("memory fact not found")
            session.add(
                MemoryRevisionRow(
                    revision_id=str(uuid4()),
                    fact_id=row.fact_id,
                    prior_content=row.content,
                    prior_version=row.version,
                    corrected_at=_dump_datetime(corrected_at),
                    source_event_id=str(source_event_id),
                )
            )
            row.content = corrected_content
            row.source_event_ids_json = _dump_source_ids(
                _merge_sources(
                    _load_source_ids(row.source_event_ids_json),
                    (source_event_id,),
                )
            )
            row.updated_at = _dump_datetime(corrected_at)
            row.version += 1
            session.add(row)
            self._replace_fts(session, row)
            session.commit()
            corrected = _fact_from_row(row)
        self._render_markdown()
        return corrected

    def search(self, query: str, *, limit: int = 12) -> list[MemoryFact]:
        if limit <= 0 or limit > 100:
            raise ValueError("search limit must be between 1 and 100")
        terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
        if not terms:
            return []
        expression = " AND ".join(f'"{term}"' for term in terms[:16])
        with Session(self._sqlite.engine) as session:
            connection = session.connection()
            ids = [
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT fact_id FROM memory_fts WHERE memory_fts MATCH ? "
                    "ORDER BY bm25(memory_fts) LIMIT ?",
                    (expression, limit),
                ).all()
            ]
            facts: list[MemoryFact] = []
            for fact_id in ids:
                row = session.get(MemoryFactRow, fact_id)
                if row is not None:
                    facts.append(_fact_from_row(row))
            return facts

    def forget(self, fact_id: UUID, *, forgotten_at: datetime) -> None:
        _require_aware(forgotten_at)
        with self._sqlite._write_lock, Session(self._sqlite.engine) as session:
            row = session.get(MemoryFactRow, str(fact_id))
            if row is None:
                return
            session.exec(
                delete(MemoryConflictRow).where(col(MemoryConflictRow.fact_id) == row.fact_id)
            )
            session.exec(
                delete(MemoryRevisionRow).where(col(MemoryRevisionRow.fact_id) == row.fact_id)
            )
            session.connection().exec_driver_sql(
                "DELETE FROM memory_fts WHERE fact_id = ?", (row.fact_id,)
            )
            session.delete(row)
            session.add(
                MemoryDeletionRow(
                    deletion_id=str(uuid4()),
                    fact_id=str(fact_id),
                    forgotten_at=_dump_datetime(forgotten_at),
                )
            )
            session.commit()
        self._render_markdown()

    def list_deletions(self) -> list[MemoryDeletion]:
        with Session(self._sqlite.engine) as session:
            rows = session.exec(
                select(MemoryDeletionRow).order_by(MemoryDeletionRow.forgotten_at)
            ).all()
            return [
                MemoryDeletion(
                    deletion_id=UUID(row.deletion_id),
                    fact_id=UUID(row.fact_id),
                    forgotten_at=_load_datetime(row.forgotten_at),
                )
                for row in rows
            ]

    def _replace_fts(self, session: Session, row: MemoryFactRow) -> None:
        connection = session.connection()
        connection.exec_driver_sql("DELETE FROM memory_fts WHERE fact_id = ?", (row.fact_id,))
        connection.exec_driver_sql(
            "INSERT INTO memory_fts(fact_id, category, subject, content) VALUES (?, ?, ?, ?)",
            (row.fact_id, row.category, row.subject, row.content),
        )

    def _render_markdown(self) -> None:
        with self._sqlite._write_lock, Session(self._sqlite.engine) as session:
            rows = list(
                session.exec(
                    select(MemoryFactRow).order_by(
                        MemoryFactRow.category_key,
                        MemoryFactRow.subject_key,
                    )
                ).all()
            )
        lines = [
            "# JARVIS memory",
            "",
            "> This file mirrors canonical local memory and can be inspected or edited "
            "deliberately.",
            "",
        ]
        current_category: str | None = None
        for row in rows:
            if row.category != current_category:
                lines.extend([f"## {row.category}", ""])
                current_category = row.category
            sources = ", ".join(str(value) for value in _load_source_ids(row.source_event_ids_json))
            lines.extend(
                [
                    f"### {row.subject}",
                    "",
                    row.content,
                    "",
                    f"Source events: `{sources}`  ",
                    f"Fact ID: `{row.fact_id}` | Version {row.version}",
                    "",
                ]
            )
        temporary = self._markdown_directory / f".memory-{uuid4().hex}.tmp"
        temporary.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        temporary.replace(self._markdown_path)


def _fact_from_row(row: MemoryFactRow) -> MemoryFact:
    return MemoryFact(
        fact_id=UUID(row.fact_id),
        category=row.category,
        subject=row.subject,
        content=row.content,
        source_event_ids=_load_source_ids(row.source_event_ids_json),
        observed_at=_load_datetime(row.observed_at),
        updated_at=_load_datetime(row.updated_at),
        version=row.version,
    )


def _merge_sources(existing: tuple[UUID, ...], additional: tuple[UUID, ...]) -> tuple[UUID, ...]:
    return tuple(dict.fromkeys((*existing, *additional)))


def _dump_source_ids(values: tuple[UUID, ...]) -> str:
    return json.dumps([str(value) for value in values], separators=(",", ":"))


def _load_source_ids(value: str) -> tuple[UUID, ...]:
    return tuple(UUID(item) for item in json.loads(value))


def _dump_datetime(value: datetime) -> str:
    _require_aware(value)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
