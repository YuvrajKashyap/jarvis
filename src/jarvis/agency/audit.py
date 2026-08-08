from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jarvis.agency.policy import PolicyDecisionKind, RiskClass


class ActionAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: UUID
    invocation_id: UUID
    capability: str = Field(min_length=1, max_length=160)
    risk: RiskClass
    policy_decision: PolicyDecisionKind
    approval_id: UUID | None
    result_status: str = Field(pattern=r"^(succeeded|failed|cancelled|denied)$")
    result_summary: str = Field(min_length=1, max_length=4_000)
    undo_reference: str | None = Field(default=None, max_length=1_000)
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a UTC offset")
        return value


class AuditWriter(Protocol):
    def append_action_audit(self, audit: ActionAudit) -> None: ...
