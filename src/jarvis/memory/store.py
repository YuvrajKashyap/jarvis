import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import UniqueConstraint, delete, func
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

    def find(self, *, category: str, subject: str) -> MemoryFact | None:
        category_key = category.strip().casefold()
        subject_key = subject.strip().casefold()
        if not category_key or not subject_key:
            raise ValueError("memory category and subject cannot be empty")
        with Session(self._sqlite.engine) as session:
            row = session.exec(
                select(MemoryFactRow).where(
                    MemoryFactRow.category_key == category_key,
                    MemoryFactRow.subject_key == subject_key,
                )
            ).one_or_none()
            return None if row is None else _fact_from_row(row)

    def list_all(self, *, limit: int = 10_000) -> list[MemoryFact]:
        if limit < 1 or limit > 10_000:
            raise ValueError("memory list limit must be between 1 and 10000")
        with Session(self._sqlite.engine) as session:
            rows = session.exec(
                select(MemoryFactRow).order_by(col(MemoryFactRow.updated_at).desc()).limit(limit)
            ).all()
            return [_fact_from_row(row) for row in rows]

    def count(self) -> int:
        with Session(self._sqlite.engine) as session:
            return int(session.exec(select(func.count()).select_from(MemoryFactRow)).one())

    def list_conflicts(self) -> list[MemoryConflict]:
        with Session(self._sqlite.engine) as session:
            rows = session.exec(
                select(MemoryConflictRow).order_by(MemoryConflictRow.observed_at)
            ).all()
            return [_conflict_from_row(row) for row in rows]

    def delete_conflict(self, conflict_id: UUID) -> bool:
        with self._sqlite._write_lock, Session(self._sqlite.engine) as session:
            row = session.get(MemoryConflictRow, str(conflict_id))
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def resolve_conflict(
        self,
        conflict_id: UUID,
        *,
        accept: bool,
        resolved_at: datetime,
    ) -> MemoryFact | None:
        """Resolve one staged contradiction; acceptance is atomic and provenance-preserving."""
        _require_aware(resolved_at)
        with self._sqlite._write_lock, Session(self._sqlite.engine) as session:
            conflict = session.get(MemoryConflictRow, str(conflict_id))
            if conflict is None:
                raise LookupError("memory conflict not found")
            if not accept:
                session.delete(conflict)
                session.commit()
                return None

            fact = session.get(MemoryFactRow, conflict.fact_id)
            if fact is None:
                raise LookupError("memory fact not found")
            candidate_sources = _load_source_ids(conflict.source_event_ids_json)
            session.add(
                MemoryRevisionRow(
                    revision_id=str(uuid4()),
                    fact_id=fact.fact_id,
                    prior_content=fact.content,
                    prior_version=fact.version,
                    corrected_at=_dump_datetime(resolved_at),
                    source_event_id=str(candidate_sources[0]),
                )
            )
            fact.content = conflict.candidate_content
            fact.source_event_ids_json = _dump_source_ids(
                _merge_sources(_load_source_ids(fact.source_event_ids_json), candidate_sources)
            )
            fact.updated_at = _dump_datetime(resolved_at)
            fact.version += 1
            session.add(fact)
            session.delete(conflict)
            self._replace_fts(session, fact)
            session.commit()
            resolved = _fact_from_row(fact)
        self._render_markdown()
        return resolved

    def stage_markdown_edits(
        self,
        *,
        source_event_id: UUID,
        observed_at: datetime,
    ) -> tuple[MemoryConflict, ...]:
        """Validate the editable mirror and stage content changes for explicit review."""
        _require_aware(observed_at)
        edits = _parse_markdown_facts(self._markdown_path.read_text(encoding="utf-8"))
        with self._sqlite._write_lock, Session(self._sqlite.engine) as session:
            rows = list(session.exec(select(MemoryFactRow)).all())
            by_id = {UUID(row.fact_id): row for row in rows}
            if set(edits) != set(by_id):
                raise ValueError("memory Markdown is missing or contains an unknown fact")
            staged: list[MemoryConflictRow] = []
            for fact_id, edit in edits.items():
                row = by_id[fact_id]
                category, subject, content, version = edit
                if version != row.version:
                    raise ValueError("memory Markdown is stale; refresh it before importing edits")
                if category != row.category or subject != row.subject:
                    raise ValueError("memory Markdown headings cannot rename canonical facts")
                if content == row.content:
                    continue
                existing = session.exec(
                    select(MemoryConflictRow).where(
                        MemoryConflictRow.fact_id == row.fact_id,
                        MemoryConflictRow.candidate_content == content,
                    )
                ).first()
                if existing is not None:
                    staged.append(existing)
                    continue
                conflict = MemoryConflictRow(
                    conflict_id=str(uuid4()),
                    fact_id=row.fact_id,
                    candidate_content=content,
                    source_event_ids_json=_dump_source_ids((source_event_id,)),
                    observed_at=_dump_datetime(observed_at),
                )
                session.add(conflict)
                staged.append(conflict)
            session.commit()
            return tuple(_conflict_from_row(row) for row in staged)

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


def _conflict_from_row(row: MemoryConflictRow) -> MemoryConflict:
    return MemoryConflict(
        conflict_id=UUID(row.conflict_id),
        fact_id=UUID(row.fact_id),
        candidate_content=row.candidate_content,
        source_event_ids=_load_source_ids(row.source_event_ids_json),
        observed_at=_load_datetime(row.observed_at),
    )


def _parse_markdown_facts(document: str) -> dict[UUID, tuple[str, str, str, int]]:
    facts: dict[UUID, tuple[str, str, str, int]] = {}
    category: str | None = None
    subject: str | None = None
    content_lines: list[str] = []
    source_seen = False
    for line in (*document.splitlines(), "## __end__"):
        if line.startswith("## ") and not line.startswith("### "):
            if subject is not None:
                raise ValueError("memory Markdown fact metadata is incomplete")
            category = line.removeprefix("## ").strip()
            continue
        if line.startswith("### "):
            if category is None or subject is not None:
                raise ValueError("memory Markdown heading structure is invalid")
            subject = line.removeprefix("### ").strip()
            content_lines = []
            source_seen = False
            continue
        if subject is None:
            continue
        if line.startswith("Source events: "):
            source_seen = True
            continue
        if line.startswith("Fact ID: "):
            match = re.fullmatch(r"Fact ID: `([^`]+)` \| Version (\d+)", line)
            if match is None or not source_seen:
                raise ValueError("memory Markdown fact metadata is invalid")
            try:
                fact_id = UUID(match.group(1))
            except ValueError as error:
                raise ValueError("memory Markdown fact ID is invalid") from error
            if fact_id in facts:
                raise ValueError("memory Markdown contains a duplicate fact")
            content = "\n".join(content_lines).strip()
            if not content or category is None:
                raise ValueError("memory Markdown fact content cannot be empty")
            facts[fact_id] = (category, subject, content, int(match.group(2)))
            subject = None
            content_lines = []
            source_seen = False
            continue
        if not source_seen:
            content_lines.append(line)
    return facts


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
