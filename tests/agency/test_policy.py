from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import JsonValue

from jarvis.agency.policy import (
    ApprovalChoice,
    AuthorizationContext,
    CapabilityRequest,
    InMemoryApprovalStore,
    PolicyDecisionKind,
    PolicyEngine,
    RiskClass,
)

NOW = datetime(2026, 8, 7, 18, 30, tzinfo=UTC)
INVOCATION_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cf0")


def request(risk: RiskClass, arguments: dict[str, JsonValue] | None = None) -> CapabilityRequest:
    return CapabilityRequest(
        invocation_id=INVOCATION_ID,
        capability="files.rename",
        risk=risk,
        arguments=arguments or {"source": "draft.txt", "target": "final.txt"},
    )


@pytest.mark.parametrize(
    ("risk", "context", "expected"),
    [
        (RiskClass.OBSERVE, AuthorizationContext(), PolicyDecisionKind.ALLOW),
        (
            RiskClass.LOCAL_REVERSIBLE,
            AuthorizationContext(direct_request=True),
            PolicyDecisionKind.ALLOW,
        ),
        (
            RiskClass.LOCAL_REVERSIBLE,
            AuthorizationContext(standing_rule_id="rename-downloads"),
            PolicyDecisionKind.ALLOW,
        ),
        (
            RiskClass.LOCAL_REVERSIBLE,
            AuthorizationContext(),
            PolicyDecisionKind.REQUIRE_APPROVAL,
        ),
        (
            RiskClass.EXTERNAL_IRREVERSIBLE,
            AuthorizationContext(direct_request=True),
            PolicyDecisionKind.REQUIRE_APPROVAL,
        ),
        (
            RiskClass.FORBIDDEN,
            AuthorizationContext(direct_request=True, standing_rule_id="anything"),
            PolicyDecisionKind.DENY,
        ),
    ],
)
def test_policy_maps_risk_and_user_authority_to_deterministic_decision(
    risk: RiskClass,
    context: AuthorizationContext,
    expected: PolicyDecisionKind,
) -> None:
    engine = PolicyEngine(InMemoryApprovalStore())

    decision = engine.evaluate(request(risk), context, now=NOW)

    assert decision.kind is expected


def test_approval_is_bound_to_exact_invocation_and_consumed_once() -> None:
    engine = PolicyEngine(InMemoryApprovalStore())
    original = request(RiskClass.EXTERNAL_IRREVERSIBLE)
    approval = engine.request_approval(original, expires_at=NOW + timedelta(minutes=2))
    engine.record_decision(
        approval.approval_id,
        ApprovalChoice.APPROVE,
        device_id="desktop",
        decided_at=NOW,
    )

    first = engine.evaluate(
        original,
        AuthorizationContext(approval_id=approval.approval_id),
        now=NOW,
    )
    replay = engine.evaluate(
        original,
        AuthorizationContext(approval_id=approval.approval_id),
        now=NOW,
    )
    tampered = engine.evaluate(
        request(RiskClass.EXTERNAL_IRREVERSIBLE, {"target": "different.txt"}),
        AuthorizationContext(approval_id=approval.approval_id),
        now=NOW,
    )

    assert first.kind is PolicyDecisionKind.ALLOW
    assert replay.kind is PolicyDecisionKind.DENY
    assert replay.reason == "approval_replayed"
    assert tampered.kind is PolicyDecisionKind.DENY


def test_parallel_approval_consumption_allows_exactly_one_execution() -> None:
    engine = PolicyEngine(InMemoryApprovalStore())
    capability = request(RiskClass.EXTERNAL_IRREVERSIBLE)
    pending = engine.request_approval(
        capability,
        expires_at=NOW + timedelta(minutes=5),
    )
    engine.record_decision(
        pending.approval_id,
        ApprovalChoice.APPROVE,
        device_id="desktop",
        decided_at=NOW,
    )

    def evaluate() -> PolicyDecisionKind:
        return engine.evaluate(
            capability,
            AuthorizationContext(approval_id=pending.approval_id),
            now=NOW,
        ).kind

    with ThreadPoolExecutor(max_workers=8) as executor:
        decisions = list(executor.map(lambda _index: evaluate(), range(32)))

    assert decisions.count(PolicyDecisionKind.ALLOW) == 1
    assert decisions.count(PolicyDecisionKind.DENY) == 31


def test_expired_or_rejected_approval_never_authorizes_execution() -> None:
    engine = PolicyEngine(InMemoryApprovalStore())
    capability = request(RiskClass.LOCAL_REVERSIBLE)
    expired = engine.request_approval(capability, expires_at=NOW - timedelta(seconds=1))
    rejected = engine.request_approval(capability, expires_at=NOW + timedelta(minutes=1))
    engine.record_decision(
        rejected.approval_id,
        ApprovalChoice.REJECT,
        device_id="phone",
        decided_at=NOW,
    )

    expired_decision = engine.evaluate(
        capability,
        AuthorizationContext(approval_id=expired.approval_id),
        now=NOW,
    )
    rejected_decision = engine.evaluate(
        capability,
        AuthorizationContext(approval_id=rejected.approval_id),
        now=NOW,
    )

    assert expired_decision.kind is PolicyDecisionKind.DENY
    assert expired_decision.reason == "approval_expired"
    assert rejected_decision.kind is PolicyDecisionKind.DENY
    assert rejected_decision.reason == "approval_rejected"


def test_scheduled_external_action_still_requires_immediate_approval() -> None:
    engine = PolicyEngine(InMemoryApprovalStore())

    decision = engine.evaluate(
        request(RiskClass.EXTERNAL_IRREVERSIBLE),
        AuthorizationContext(standing_rule_id="weekday-report", scheduled=True),
        now=NOW,
    )

    assert decision.kind is PolicyDecisionKind.REQUIRE_APPROVAL
