import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from jarvis.agency.audit import ActionAudit, AuditWriter
from jarvis.agency.policy import (
    ApprovalChoice,
    AuthorizationContext,
    CapabilityRequest,
    PolicyDecisionKind,
    PolicyEngine,
    RiskClass,
)
from jarvis.platform.models import ToolSchema


class CapabilityMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=3, max_length=160, pattern=r"^[a-z][a-z0-9_.-]+$")
    description: str = Field(min_length=1, max_length=500)
    risk: RiskClass
    timeout_seconds: float = Field(ge=0.01, le=300)
    reversible: bool


class Invocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invocation_id: UUID
    capability: str = Field(min_length=3, max_length=160, pattern=r"^[a-z][a-z0-9_.-]+$")
    arguments: dict[str, JsonValue]
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("requested_at must include a UTC offset")
        return value


@dataclass(frozen=True)
class CapabilityContext:
    invocation_id: UUID
    device_id: str
    requested_at: datetime
    source_event_id: UUID | None = None


class Capability(Protocol):
    @property
    def metadata(self) -> CapabilityMetadata: ...

    @property
    def input_model(self) -> type[BaseModel]: ...

    @property
    def output_model(self) -> type[BaseModel]: ...

    async def execute(
        self,
        arguments: BaseModel,
        context: CapabilityContext,
    ) -> BaseModel: ...


class ExecutionStatus(StrEnum):
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invocation_id: UUID
    status: ExecutionStatus
    approval_id: UUID | None = None
    output: dict[str, JsonValue] | None = None
    reason: str | None = None


class ApprovalPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: UUID
    capability: str = Field(min_length=3, max_length=160)
    summary: str = Field(min_length=1, max_length=1_000)
    risk: Literal[RiskClass.LOCAL_REVERSIBLE, RiskClass.EXTERNAL_IRREVERSIBLE]
    expires_at: datetime


class CoordinatedExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: ExecutionResult
    approval: ApprovalPrompt | None = None


class ActionCoordinator(Protocol):
    async def propose(
        self,
        *,
        capability: str,
        arguments: dict[str, JsonValue],
        device_id: str,
        requested_at: datetime,
        direct_request: bool,
        source_event_id: UUID | None = None,
        standing_rule_id: str | None = None,
        scheduled: bool = False,
    ) -> CoordinatedExecution: ...

    async def decide(
        self,
        *,
        approval_id: UUID,
        choice: ApprovalChoice,
        device_id: str,
        now: datetime,
    ) -> ExecutionResult: ...

    def pending_capability(self, approval_id: UUID) -> str | None: ...

    def pending_is_scheduled(self, approval_id: UUID) -> bool: ...


@dataclass(frozen=True)
class _PendingInvocation:
    invocation: Invocation
    device_id: str
    scheduled: bool
    source_event_id: UUID | None


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        name = capability.metadata.name
        if name in self._capabilities:
            raise ValueError(f"capability already registered: {name}")
        if (
            capability.metadata.risk is RiskClass.LOCAL_REVERSIBLE
            and not capability.metadata.reversible
        ):
            raise ValueError("local reversible capabilities must provide undo semantics")
        self._capabilities[name] = capability

    def get(self, name: str) -> Capability | None:
        return self._capabilities.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._capabilities)

    def tool_schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(
            ToolSchema(
                name=capability.metadata.name,
                description=capability.metadata.description,
                parameters=capability.input_model.model_json_schema(),
            )
            for capability in self._capabilities.values()
        )


class InvocationEngine:
    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        policy: PolicyEngine,
        audit: AuditWriter,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._audit = audit

    def metadata(self, capability: str) -> CapabilityMetadata | None:
        registered = self._registry.get(capability)
        return registered.metadata if registered is not None else None

    async def invoke(
        self,
        invocation: Invocation,
        *,
        authorization: AuthorizationContext,
        device_id: str,
        now: datetime,
        source_event_id: UUID | None = None,
    ) -> ExecutionResult:
        capability = self._registry.get(invocation.capability)
        if capability is None:
            return ExecutionResult(
                invocation_id=invocation.invocation_id,
                status=ExecutionStatus.DENIED,
                reason="unknown_capability",
            )

        request = CapabilityRequest(
            invocation_id=invocation.invocation_id,
            capability=capability.metadata.name,
            risk=capability.metadata.risk,
            arguments=invocation.arguments,
        )
        try:
            arguments = capability.input_model.model_validate(invocation.arguments)
        except ValidationError:
            result = ExecutionResult(
                invocation_id=invocation.invocation_id,
                status=ExecutionStatus.DENIED,
                reason="invalid_arguments",
            )
            self._record(
                request=request,
                decision=PolicyDecisionKind.DENY,
                authorization=authorization,
                result=result,
                recorded_at=now,
            )
            return result

        decision = self._policy.evaluate(request, authorization, now=now)
        if decision.kind is PolicyDecisionKind.REQUIRE_APPROVAL:
            approval = self._policy.request_approval(
                request,
                expires_at=now + timedelta(minutes=5),
            )
            return ExecutionResult(
                invocation_id=invocation.invocation_id,
                status=ExecutionStatus.AWAITING_APPROVAL,
                approval_id=approval.approval_id,
                reason=decision.reason,
            )
        if decision.kind is PolicyDecisionKind.DENY:
            result = ExecutionResult(
                invocation_id=invocation.invocation_id,
                status=ExecutionStatus.DENIED,
                reason=decision.reason,
            )
            self._record(
                request=request,
                decision=decision.kind,
                authorization=authorization,
                result=result,
                recorded_at=now,
            )
            return result

        context = CapabilityContext(
            invocation_id=invocation.invocation_id,
            device_id=device_id,
            requested_at=invocation.requested_at,
            source_event_id=source_event_id,
        )
        try:
            raw_output = await asyncio.wait_for(
                capability.execute(arguments, context),
                timeout=capability.metadata.timeout_seconds,
            )
            output = capability.output_model.model_validate(raw_output).model_dump(mode="json")
            result = ExecutionResult(
                invocation_id=invocation.invocation_id,
                status=ExecutionStatus.SUCCEEDED,
                output=output,
            )
        except TimeoutError:
            result = ExecutionResult(
                invocation_id=invocation.invocation_id,
                status=ExecutionStatus.FAILED,
                reason="timeout",
            )
        except asyncio.CancelledError:
            result = ExecutionResult(
                invocation_id=invocation.invocation_id,
                status=ExecutionStatus.CANCELLED,
                reason="cancelled",
            )
        except (OSError, RuntimeError, ValueError):
            result = ExecutionResult(
                invocation_id=invocation.invocation_id,
                status=ExecutionStatus.FAILED,
                reason="capability_failed",
            )

        self._record(
            request=request,
            decision=decision.kind,
            authorization=authorization,
            result=result,
            recorded_at=now,
        )
        return result

    def _record(
        self,
        *,
        request: CapabilityRequest,
        decision: PolicyDecisionKind,
        authorization: AuthorizationContext,
        result: ExecutionResult,
        recorded_at: datetime,
    ) -> None:
        undo_reference = None
        if result.output is not None:
            value = result.output.get("undo_reference")
            if isinstance(value, str):
                undo_reference = value
        summary = result.reason or result.status.value
        self._audit.append_action_audit(
            ActionAudit(
                action_id=uuid4(),
                invocation_id=request.invocation_id,
                capability=request.capability,
                risk=request.risk,
                policy_decision=decision,
                approval_id=authorization.approval_id,
                result_status=result.status.value,
                result_summary=summary,
                undo_reference=undo_reference,
                recorded_at=recorded_at,
            )
        )


class InvocationCoordinator:
    """Owns the approval pause/resume seam for exact, typed invocations."""

    def __init__(self, *, engine: InvocationEngine, policy: PolicyEngine) -> None:
        self._engine = engine
        self._policy = policy
        self._pending: dict[UUID, _PendingInvocation] = {}

    def pending_capability(self, approval_id: UUID) -> str | None:
        pending = self._pending.get(approval_id)
        return pending.invocation.capability if pending is not None else None

    def pending_is_scheduled(self, approval_id: UUID) -> bool:
        pending = self._pending.get(approval_id)
        return pending.scheduled if pending is not None else False

    async def propose(
        self,
        *,
        capability: str,
        arguments: dict[str, JsonValue],
        device_id: str,
        requested_at: datetime,
        direct_request: bool,
        source_event_id: UUID | None = None,
        standing_rule_id: str | None = None,
        scheduled: bool = False,
    ) -> CoordinatedExecution:
        invocation = Invocation(
            invocation_id=uuid4(),
            capability=capability,
            arguments=arguments,
            requested_at=requested_at,
        )
        result = await self._engine.invoke(
            invocation,
            authorization=AuthorizationContext(
                direct_request=direct_request,
                standing_rule_id=standing_rule_id,
                scheduled=scheduled,
            ),
            device_id=device_id,
            now=requested_at,
            source_event_id=source_event_id,
        )
        if result.status is not ExecutionStatus.AWAITING_APPROVAL or result.approval_id is None:
            return CoordinatedExecution(result=result)

        metadata = self._engine.metadata(capability)
        approval = self._policy.approval(result.approval_id)
        if metadata is None or approval is None:
            raise RuntimeError("approval state was not persisted")
        if metadata.risk not in {
            RiskClass.LOCAL_REVERSIBLE,
            RiskClass.EXTERNAL_IRREVERSIBLE,
        }:
            raise RuntimeError("unexpected approval risk class")
        prompt = ApprovalPrompt(
            approval_id=approval.approval_id,
            capability=metadata.name,
            summary=metadata.description,
            risk=metadata.risk,
            expires_at=approval.expires_at,
        )
        self._pending[prompt.approval_id] = _PendingInvocation(
            invocation=invocation,
            device_id=device_id,
            scheduled=scheduled,
            source_event_id=source_event_id,
        )
        return CoordinatedExecution(result=result, approval=prompt)

    async def decide(
        self,
        *,
        approval_id: UUID,
        choice: ApprovalChoice,
        device_id: str,
        now: datetime,
    ) -> ExecutionResult:
        pending = self._pending.get(approval_id)
        if pending is None:
            raise LookupError("pending approval not found")
        if not pending.scheduled and pending.device_id != device_id:
            raise PermissionError("approval belongs to the requesting device")

        self._policy.record_decision(
            approval_id,
            choice,
            device_id=device_id,
            decided_at=now,
        )
        try:
            return await self._engine.invoke(
                pending.invocation,
                authorization=AuthorizationContext(approval_id=approval_id),
                device_id=pending.device_id,
                now=now,
                source_event_id=pending.source_event_id,
            )
        finally:
            self._pending.pop(approval_id, None)
