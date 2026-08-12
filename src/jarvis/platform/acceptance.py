import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

_EVIDENCE_CODE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")


class AcceptanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    passed: bool
    completed_at: datetime
    subject: str | None = None

    @field_validator("schema_version")
    @classmethod
    def require_supported_schema(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported acceptance evidence schema")
        return value

    @field_validator("completed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("acceptance evidence time must include an offset")
        return value


class LocalAcceptanceEvidence:
    """Reads validated, machine-local evidence emitted by acceptance runners."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory.resolve()

    def passed(self, code: str, *, subject: str | None = None) -> bool:
        record = self._read(code)
        return (
            record is not None and record.passed and (subject is None or record.subject == subject)
        )

    def passing_subject(self, code: str) -> str | None:
        record = self._read(code)
        if record is None or not record.passed:
            return None
        return record.subject

    def _read(self, code: str) -> AcceptanceRecord | None:
        if _EVIDENCE_CODE.fullmatch(code) is None:
            return None
        path = self._directory / f"{code}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return AcceptanceRecord.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError):
            return None

    def record_pass(self, code: str, *, subject: str | None = None) -> None:
        if _EVIDENCE_CODE.fullmatch(code) is None:
            raise ValueError("acceptance evidence code is invalid")
        self._directory.mkdir(parents=True, exist_ok=True)
        record = AcceptanceRecord(
            schema_version=1,
            passed=True,
            completed_at=datetime.now(UTC),
            subject=subject,
        )
        destination = self._directory / f"{code}.json"
        temporary = self._directory / f".{code}-{uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(record.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(destination)
