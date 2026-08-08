import hashlib
import json
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class RiskClass(StrEnum):
    OBSERVE = "observe"
    LOCAL_REVERSIBLE = "local_reversible"
    EXTERNAL_IRREVERSIBLE = "external_irreversible"
    FORBIDDEN = "forbidden"


class PolicyDecisionKind(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class ApprovalChoice(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONSUMED = "consumed"


class ApprovalConsumeResult(StrEnum):
    CONSUMED = "consumed"
    UNKNOWN = "approval_unknown"
    MISMATCH = "approval_mismatch"
    EXPIRED = "approval_expired"
    REJECTED = "approval_rejected"
    REPLAYED = "approval_replayed"
    NOT_GRANTED = "approval_not_granted"


@dataclass(frozen=True)
class AuthorizationContext:
    direct_request: bool = False
    standing_rule_id: str | None = None
    approval_id: UUID | None = None
    scheduled: bool = False


class CapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invocation_id: UUID
    capability: str = Field(min_length=1, max_length=160, pattern=r"^[a-z][a-z0-9_.-]+$")
    risk: RiskClass
    arguments: dict[str, JsonValue]

    def fingerprint(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class PolicyDecision:
    kind: PolicyDecisionKind
    reason: str


@dataclass(frozen=True)
class Approval:
    approval_id: UUID
    request_fingerprint: str
    expires_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision_device_id: str | None = None
    decided_at: datetime | None = None


class ApprovalStore(Protocol):
    def get(self, approval_id: UUID) -> Approval | None: ...

    def put(self, approval: Approval) -> None: ...

    def decide(
        self,
        approval_id: UUID,
        choice: ApprovalChoice,
        *,
        device_id: str,
        decided_at: datetime,
    ) -> bool: ...

    def consume(
        self,
        approval_id: UUID,
        request_fingerprint: str,
        *,
        now: datetime,
    ) -> ApprovalConsumeResult: ...


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._approvals: dict[UUID, Approval] = {}
        self._lock = threading.RLock()

    def get(self, approval_id: UUID) -> Approval | None:
        with self._lock:
            return self._approvals.get(approval_id)

    def put(self, approval: Approval) -> None:
        with self._lock:
            self._approvals[approval.approval_id] = approval

    def decide(
        self,
        approval_id: UUID,
        choice: ApprovalChoice,
        *,
        device_id: str,
        decided_at: datetime,
    ) -> bool:
        with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None or approval.status is not ApprovalStatus.PENDING:
                return False
            status = (
                ApprovalStatus.APPROVED
                if choice is ApprovalChoice.APPROVE
                else ApprovalStatus.REJECTED
            )
            self._approvals[approval_id] = replace(
                approval,
                status=status,
                decision_device_id=device_id,
                decided_at=decided_at,
            )
            return True

    def consume(
        self,
        approval_id: UUID,
        request_fingerprint: str,
        *,
        now: datetime,
    ) -> ApprovalConsumeResult:
        with self._lock:
            approval = self._approvals.get(approval_id)
            result = _consumption_result(approval, request_fingerprint, now=now)
            if result is ApprovalConsumeResult.CONSUMED and approval is not None:
                self._approvals[approval_id] = replace(
                    approval,
                    status=ApprovalStatus.CONSUMED,
                )
            return result


class PolicyEngine:
    def __init__(self, store: ApprovalStore) -> None:
        self._store = store

    def evaluate(
        self,
        request: CapabilityRequest,
        context: AuthorizationContext,
        *,
        now: datetime,
    ) -> PolicyDecision:
        if request.risk is RiskClass.FORBIDDEN:
            return PolicyDecision(PolicyDecisionKind.DENY, "capability_forbidden")

        if context.approval_id is not None:
            return self._evaluate_approval(request, context.approval_id, now=now)

        if request.risk is RiskClass.OBSERVE:
            return PolicyDecision(PolicyDecisionKind.ALLOW, "observe")

        if request.risk is RiskClass.LOCAL_REVERSIBLE and (
            context.direct_request or context.standing_rule_id is not None
        ):
            return PolicyDecision(PolicyDecisionKind.ALLOW, "user_authorized_local_change")

        return PolicyDecision(PolicyDecisionKind.REQUIRE_APPROVAL, "fresh_approval_required")

    def request_approval(self, request: CapabilityRequest, *, expires_at: datetime) -> Approval:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("approval expiry must include a UTC offset")
        approval = Approval(
            approval_id=uuid4(),
            request_fingerprint=request.fingerprint(),
            expires_at=expires_at,
        )
        self._store.put(approval)
        return approval

    def record_decision(
        self,
        approval_id: UUID,
        choice: ApprovalChoice,
        *,
        device_id: str,
        decided_at: datetime,
    ) -> None:
        approval = self._store.get(approval_id)
        if approval is None:
            raise LookupError("approval not found")
        if approval.status is not ApprovalStatus.PENDING:
            raise ValueError("approval decision is final")
        if decided_at.tzinfo is None or decided_at.utcoffset() is None:
            raise ValueError("approval decision time must include a UTC offset")
        if not self._store.decide(
            approval_id,
            choice,
            device_id=device_id,
            decided_at=decided_at,
        ):
            raise ValueError("approval decision is final")

    def approval(self, approval_id: UUID) -> Approval | None:
        return self._store.get(approval_id)

    def _evaluate_approval(
        self,
        request: CapabilityRequest,
        approval_id: UUID,
        *,
        now: datetime,
    ) -> PolicyDecision:
        result = self._store.consume(approval_id, request.fingerprint(), now=now)
        if result is ApprovalConsumeResult.CONSUMED:
            return PolicyDecision(PolicyDecisionKind.ALLOW, "approval_consumed")
        return PolicyDecision(PolicyDecisionKind.DENY, result.value)


def _consumption_result(
    approval: Approval | None,
    request_fingerprint: str,
    *,
    now: datetime,
) -> ApprovalConsumeResult:
    if approval is None:
        return ApprovalConsumeResult.UNKNOWN
    if approval.request_fingerprint != request_fingerprint:
        return ApprovalConsumeResult.MISMATCH
    if now > approval.expires_at:
        return ApprovalConsumeResult.EXPIRED
    if approval.status is ApprovalStatus.REJECTED:
        return ApprovalConsumeResult.REJECTED
    if approval.status is ApprovalStatus.CONSUMED:
        return ApprovalConsumeResult.REPLAYED
    if approval.status is ApprovalStatus.PENDING:
        return ApprovalConsumeResult.NOT_GRANTED
    return ApprovalConsumeResult.CONSUMED
