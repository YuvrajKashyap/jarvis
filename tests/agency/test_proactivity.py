from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from jarvis.agency.proactivity import (
    InMemoryProactivityLedger,
    ProactivePriority,
    ProactiveSignal,
    ProactivityEngine,
    ProactivityFeedback,
    ProactivityPolicy,
    ProactivityRuntime,
)

NOW = datetime(2026, 8, 10, 20, 0, tzinfo=UTC)


def signal(
    *,
    fingerprint: str = "downloads:research.pdf",
    priority: ProactivePriority = ProactivePriority.NORMAL,
    confidence: float = 0.86,
    observed_at: datetime = NOW,
) -> ProactiveSignal:
    return ProactiveSignal(
        signal_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf0"),
        fingerprint=fingerprint,
        topic="downloads",
        title="Research paper ready",
        message="The PDF finished downloading. Want me to summarize or file it?",
        reason="A new completed PDF appeared in Downloads.",
        suggested_prompt="Summarize the new PDF in Downloads.",
        priority=priority,
        confidence=confidence,
        observed_at=observed_at,
        expires_at=observed_at + timedelta(hours=2),
    )


def engine() -> ProactivityEngine:
    return ProactivityEngine(
        ledger=InMemoryProactivityLedger(),
        policy=ProactivityPolicy(),
        timezone=ZoneInfo("America/Chicago"),
    )


def test_useful_signal_becomes_an_observe_only_suggestion() -> None:
    suggestion = engine().consider(signal(), now=NOW)

    assert suggestion is not None
    assert suggestion.title == "Research paper ready"
    assert suggestion.suggested_prompt == "Summarize the new PDF in Downloads."
    assert suggestion.priority is ProactivePriority.NORMAL
    assert suggestion.proposed_action is None


def test_low_confidence_and_repeated_signals_stay_quiet() -> None:
    decision = engine()

    assert decision.consider(signal(confidence=0.4), now=NOW) is None
    assert decision.consider(signal(), now=NOW) is not None
    assert (
        decision.consider(
            signal(observed_at=NOW + timedelta(hours=1)), now=NOW + timedelta(hours=1)
        )
        is None
    )


def test_global_cooldown_limits_chatter_but_important_signal_can_interrupt() -> None:
    decision = engine()

    assert decision.consider(signal(), now=NOW) is not None
    assert (
        decision.consider(
            signal(
                fingerprint="focus:editor",
                observed_at=NOW + timedelta(minutes=5),
            ),
            now=NOW + timedelta(minutes=5),
        )
        is None
    )
    assert (
        decision.consider(
            signal(
                fingerprint="resources:gpu-temperature",
                priority=ProactivePriority.IMPORTANT,
                observed_at=NOW + timedelta(minutes=5),
            ),
            now=NOW + timedelta(minutes=5),
        )
        is not None
    )


def test_quiet_hours_suppress_nonurgent_suggestions() -> None:
    decision = engine()
    late = datetime(2026, 8, 11, 4, 0, tzinfo=UTC)

    assert decision.consider(signal(observed_at=late), now=late) is None
    assert (
        decision.consider(
            signal(
                fingerprint="resources:critical-memory",
                priority=ProactivePriority.IMPORTANT,
                observed_at=late,
            ),
            now=late,
        )
        is not None
    )


def test_feedback_can_snooze_mute_and_tune_a_topic_without_enabling_actions() -> None:
    ledger = InMemoryProactivityLedger()
    decision = ProactivityEngine(
        ledger=ledger,
        policy=ProactivityPolicy(global_cooldown=timedelta(0), fingerprint_cooldown=timedelta(0)),
        timezone=ZoneInfo("America/Chicago"),
    )
    first = decision.consider(signal(), now=NOW)
    assert first is not None

    decision.apply_feedback(first, feedback=ProactivityFeedback.SNOOZE, now=NOW)
    assert (
        decision.consider(
            signal(fingerprint="downloads:another", observed_at=NOW + timedelta(hours=1)),
            now=NOW + timedelta(hours=1),
        )
        is None
    )
    decision.apply_feedback(first, feedback=ProactivityFeedback.MUTE_TOPIC, now=NOW)
    preference = ledger.preference("downloads")
    assert preference.muted is True
    assert preference.affinity == 0


class OneSignalProbe:
    def __init__(self) -> None:
        self._sent = False

    def scan(self, now: datetime) -> tuple[ProactiveSignal, ...]:
        if self._sent:
            return ()
        self._sent = True
        return (signal(observed_at=now, priority=ProactivePriority.IMPORTANT),)


@pytest.mark.asyncio
async def test_runtime_publishes_eligible_suggestions_without_taking_action() -> None:
    runtime = ProactivityRuntime(
        probe=OneSignalProbe(),
        engine=engine(),
        poll_interval_seconds=0.01,
    )

    await runtime.start()
    try:
        suggestion = await runtime.next_event(timeout_seconds=1)
    finally:
        await runtime.stop()

    assert suggestion.message.endswith("summarize or file it?")
    assert suggestion.proposed_action is None


@pytest.mark.asyncio
async def test_runtime_applies_feedback_only_to_a_suggestion_it_actually_emitted() -> None:
    runtime = ProactivityRuntime(
        probe=OneSignalProbe(),
        engine=engine(),
        poll_interval_seconds=0.01,
    )
    await runtime.start()
    try:
        suggestion = await runtime.next_event(timeout_seconds=1)
        preference = runtime.apply_feedback(
            suggestion.suggestion_id,
            feedback=ProactivityFeedback.LESS,
            now=NOW,
        )
    finally:
        await runtime.stop()

    assert preference.affinity == -1
    with pytest.raises(LookupError, match="suggestion"):
        runtime.apply_feedback(
            UUID("019fd977-1d96-7892-950c-6afbb71f7cf9"),
            feedback=ProactivityFeedback.MORE,
            now=NOW,
        )
