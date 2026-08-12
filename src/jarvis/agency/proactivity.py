import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProactiveValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProactivePriority(StrEnum):
    QUIET = "quiet"
    NORMAL = "normal"
    IMPORTANT = "important"


class ProactivityFeedback(StrEnum):
    DISMISS = "dismiss"
    SNOOZE = "snooze"
    MUTE_TOPIC = "mute_topic"
    LESS = "less"
    MORE = "more"


class ProactiveSignal(ProactiveValue):
    signal_id: UUID
    fingerprint: str = Field(min_length=3, max_length=512)
    topic: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_.-]+$")
    title: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=1_000)
    reason: str = Field(min_length=1, max_length=500)
    suggested_prompt: str = Field(min_length=1, max_length=2_000)
    priority: ProactivePriority
    confidence: float = Field(ge=0, le=1)
    observed_at: datetime
    expires_at: datetime
    proposed_action: None = None

    @field_validator("observed_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("proactive timestamps must include a UTC offset")
        return value


class ProactiveSuggestion(ProactiveValue):
    suggestion_id: UUID
    fingerprint: str = Field(min_length=3, max_length=512)
    topic: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=1_000)
    reason: str = Field(min_length=1, max_length=500)
    suggested_prompt: str = Field(min_length=1, max_length=2_000)
    priority: ProactivePriority
    observed_at: datetime
    expires_at: datetime
    proposed_action: None = None


class SuggestionReceipt(ProactiveValue):
    fingerprint: str = Field(min_length=3, max_length=512)
    suggested_at: datetime

    @field_validator("suggested_at")
    @classmethod
    def require_suggestion_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("suggestion timestamps must include a UTC offset")
        return value


class TopicPreference(ProactiveValue):
    topic: str = Field(min_length=2, max_length=80)
    muted: bool = False
    snoozed_until: datetime | None = None
    affinity: int = Field(default=0, ge=-2, le=2)

    @field_validator("snoozed_until")
    @classmethod
    def require_snooze_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("snooze time must include a UTC offset")
        return value


class ProactivityLedger(Protocol):
    def recent(self, since: datetime) -> tuple[SuggestionReceipt, ...]: ...

    def record(self, receipt: SuggestionReceipt) -> None: ...

    def preference(self, topic: str) -> TopicPreference: ...

    def set_preference(self, preference: TopicPreference) -> None: ...


class InMemoryProactivityLedger:
    def __init__(self) -> None:
        self._receipts: list[SuggestionReceipt] = []
        self._preferences: dict[str, TopicPreference] = {}

    def recent(self, since: datetime) -> tuple[SuggestionReceipt, ...]:
        return tuple(receipt for receipt in self._receipts if receipt.suggested_at >= since)

    def record(self, receipt: SuggestionReceipt) -> None:
        self._receipts.append(receipt)

    def preference(self, topic: str) -> TopicPreference:
        return self._preferences.get(topic, TopicPreference(topic=topic))

    def set_preference(self, preference: TopicPreference) -> None:
        self._preferences[preference.topic] = preference


class ProactivityPolicy(ProactiveValue):
    minimum_confidence: float = Field(default=0.72, ge=0, le=1)
    fingerprint_cooldown: timedelta = timedelta(hours=12)
    global_cooldown: timedelta = timedelta(minutes=20)
    daily_budget: int = Field(default=5, ge=1, le=24)
    quiet_hours_start: int = Field(default=22, ge=0, le=23)
    quiet_hours_end: int = Field(default=7, ge=0, le=23)


class ProactivityEngine:
    """Turns raw digital signals into restrained, observe-only suggestions."""

    def __init__(
        self,
        *,
        ledger: ProactivityLedger,
        policy: ProactivityPolicy,
        timezone: ZoneInfo,
    ) -> None:
        self._ledger = ledger
        self._policy = policy
        self._timezone = timezone

    def consider(
        self,
        signal: ProactiveSignal,
        *,
        now: datetime,
    ) -> ProactiveSuggestion | None:
        preference = self._ledger.preference(signal.topic)
        minimum_confidence = min(
            1.0,
            max(0.0, self._policy.minimum_confidence - preference.affinity * 0.08),
        )
        if (
            preference.muted
            or (preference.snoozed_until is not None and preference.snoozed_until > now)
            or signal.confidence < minimum_confidence
            or signal.expires_at <= now
        ):
            return None
        recent = self._ledger.recent(now - timedelta(days=1))
        repeated = any(
            receipt.fingerprint == signal.fingerprint
            and receipt.suggested_at >= now - self._policy.fingerprint_cooldown
            for receipt in recent
        )
        if repeated:
            return None
        important = signal.priority is ProactivePriority.IMPORTANT
        if not important:
            if self._is_quiet_hour(now):
                return None
            if len(recent) >= self._policy.daily_budget:
                return None
            latest = max((receipt.suggested_at for receipt in recent), default=None)
            if latest is not None and latest > now - self._policy.global_cooldown:
                return None
        receipt = SuggestionReceipt(fingerprint=signal.fingerprint, suggested_at=now)
        self._ledger.record(receipt)
        return ProactiveSuggestion(
            suggestion_id=signal.signal_id,
            fingerprint=signal.fingerprint,
            topic=signal.topic,
            title=signal.title,
            message=signal.message,
            reason=signal.reason,
            suggested_prompt=signal.suggested_prompt,
            priority=signal.priority,
            observed_at=signal.observed_at,
            expires_at=signal.expires_at,
        )

    def apply_feedback(
        self,
        suggestion: ProactiveSuggestion,
        *,
        feedback: ProactivityFeedback,
        now: datetime,
        snooze_for: timedelta = timedelta(hours=4),
    ) -> TopicPreference:
        preference = self._ledger.preference(suggestion.topic)
        if feedback is ProactivityFeedback.SNOOZE:
            preference = preference.model_copy(update={"snoozed_until": now + snooze_for})
        elif feedback is ProactivityFeedback.MUTE_TOPIC:
            preference = preference.model_copy(update={"muted": True})
        elif feedback is ProactivityFeedback.LESS:
            preference = preference.model_copy(
                update={"affinity": max(-2, preference.affinity - 1)}
            )
        elif feedback is ProactivityFeedback.MORE:
            preference = preference.model_copy(update={"affinity": min(2, preference.affinity + 1)})
        self._ledger.set_preference(preference)
        return preference

    def _is_quiet_hour(self, now: datetime) -> bool:
        hour = now.astimezone(self._timezone).hour
        start = self._policy.quiet_hours_start
        end = self._policy.quiet_hours_end
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end


class ProactiveSignalProbe(Protocol):
    def scan(self, now: datetime) -> tuple[ProactiveSignal, ...]: ...


class ProactivityRuntime:
    def __init__(
        self,
        *,
        probe: ProactiveSignalProbe,
        engine: ProactivityEngine,
        poll_interval_seconds: float = 60,
        event_buffer_size: int = 16,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("proactivity poll interval must be positive")
        if event_buffer_size < 1:
            raise ValueError("proactivity event buffer must be positive")
        self._probe = probe
        self._engine = engine
        self._poll_interval_seconds = poll_interval_seconds
        self._events: asyncio.Queue[ProactiveSuggestion] = asyncio.Queue(maxsize=event_buffer_size)
        self._event_buffer_size = event_buffer_size
        self._subscribers: set[asyncio.Queue[ProactiveSuggestion]] = set()
        self._latest: ProactiveSuggestion | None = None
        self._suggestions: dict[UUID, ProactiveSuggestion] = {}
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="jarvis-proactivity")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def next_event(self, *, timeout_seconds: float | None = None) -> ProactiveSuggestion:
        if timeout_seconds is None:
            return await self._events.get()
        return await asyncio.wait_for(self._events.get(), timeout=timeout_seconds)

    async def subscribe(self) -> AsyncIterator[ProactiveSuggestion]:
        events: asyncio.Queue[ProactiveSuggestion] = asyncio.Queue(maxsize=self._event_buffer_size)
        self._subscribers.add(events)
        latest = self._latest
        if latest is not None and latest.expires_at > datetime.now(UTC):
            events.put_nowait(latest)
        try:
            while True:
                yield await events.get()
        finally:
            self._subscribers.discard(events)

    def apply_feedback(
        self,
        suggestion_id: UUID,
        *,
        feedback: ProactivityFeedback,
        now: datetime,
    ) -> TopicPreference:
        suggestion = self._suggestions.get(suggestion_id)
        if suggestion is None:
            raise LookupError("proactive suggestion not found or expired")
        return self._engine.apply_feedback(suggestion, feedback=feedback, now=now)

    async def _run(self) -> None:
        while True:
            now = datetime.now(UTC)
            try:
                signals = await asyncio.to_thread(self._probe.scan, now)
            except (OSError, RuntimeError, ValueError):
                signals = ()
            for signal in signals:
                suggestion = await asyncio.to_thread(self._engine.consider, signal, now=now)
                if suggestion is not None:
                    self._broadcast(suggestion)
            await asyncio.sleep(self._poll_interval_seconds)

    def _broadcast(self, suggestion: ProactiveSuggestion) -> None:
        self._latest = suggestion
        self._suggestions[suggestion.suggestion_id] = suggestion
        while len(self._suggestions) > 64:
            self._suggestions.pop(next(iter(self._suggestions)))
        self._publish(self._events, suggestion)
        for subscriber in tuple(self._subscribers):
            self._publish(subscriber, suggestion)

    @staticmethod
    def _publish(
        events: asyncio.Queue[ProactiveSuggestion],
        suggestion: ProactiveSuggestion,
    ) -> None:
        if events.full():
            events.get_nowait()
        events.put_nowait(suggestion)
