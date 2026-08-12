from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from jarvis.platform.protocol import (
    Activate,
    ApprovalDecision,
    AssistantText,
    Interrupt,
    ModeChange,
    ProactiveSuggestionEvent,
    StateChanged,
    SubmitText,
    TransferDevice,
    client_event_schema,
    parse_client_event,
    serialize_server_event,
)


def valid_submit_event() -> dict[str, object]:
    return {
        "version": 1,
        "event_id": "019fd977-1d96-7892-950c-6afbb71f7cf0",
        "session_id": "019fd977-1d96-7892-950c-6afbb71f7cf1",
        "turn_id": "019fd977-1d96-7892-950c-6afbb71f7cf2",
        "sequence": 7,
        "timestamp": "2026-08-07T18:30:00Z",
        "type": "submit_text",
        "payload": {"text": "What am I looking at?", "device_id": "desktop"},
    }


def test_submit_text_parses_into_a_typed_immutable_event() -> None:
    event = parse_client_event(valid_submit_event())

    assert isinstance(event, SubmitText)
    assert event.version == 1
    assert event.event_id == UUID("019fd977-1d96-7892-950c-6afbb71f7cf0")
    assert event.timestamp == datetime(2026, 8, 7, 18, 30, tzinfo=UTC)
    assert event.payload.text == "What am I looking at?"
    assert event.payload.device_id == "desktop"

    with pytest.raises(ValidationError):
        event.__setattr__("sequence", 9)


def test_client_event_rejects_unknown_fields_in_envelope_and_payload() -> None:
    envelope_attack = valid_submit_event() | {"grant_permission": True}
    payload_attack = valid_submit_event()
    payload_attack["payload"] = {
        "text": "delete it",
        "device_id": "phone",
        "bypass_policy": True,
    }

    with pytest.raises(ValidationError):
        parse_client_event(envelope_attack)
    with pytest.raises(ValidationError):
        parse_client_event(payload_attack)


@pytest.mark.parametrize("sequence", [-1, 1.5, "7"])
def test_client_event_requires_a_nonnegative_strict_integer_sequence(sequence: object) -> None:
    raw_event = valid_submit_event() | {"sequence": sequence}

    with pytest.raises(ValidationError):
        parse_client_event(raw_event)


def test_client_event_requires_timezone_aware_timestamp() -> None:
    raw_event = valid_submit_event() | {"timestamp": "2026-08-07T18:30:00"}

    with pytest.raises(ValidationError):
        parse_client_event(raw_event)


@pytest.mark.parametrize("mode", ["normal", "private", "meeting", "lecture", "ambient"])
def test_awareness_modes_are_versioned_protocol_values(mode: str) -> None:
    raw_event = valid_submit_event() | {
        "type": "mode_change",
        "payload": {"device_id": "desktop", "mode": mode},
    }

    parsed = parse_client_event(raw_event)

    assert isinstance(parsed, ModeChange)
    assert parsed.payload.mode == mode


@pytest.mark.parametrize(
    ("event_type", "payload", "expected_type"),
    [
        ("activate", {"device_id": "desktop", "source": "wake_word"}, Activate),
        (
            "interrupt",
            {"device_id": "desktop", "reason": "user_speech"},
            Interrupt,
        ),
        (
            "approval_decision",
            {
                "device_id": "phone",
                "approval_id": "019fd977-1d96-7892-950c-6afbb71f7cf3",
                "decision": "approve",
            },
            ApprovalDecision,
        ),
        (
            "transfer_device",
            {"from_device_id": "desktop", "to_device_id": "phone"},
            TransferDevice,
        ),
        (
            "mode_change",
            {"device_id": "desktop", "mode": "private"},
            ModeChange,
        ),
    ],
)
def test_client_event_discriminator_selects_the_exact_command_type(
    event_type: str,
    payload: dict[str, object],
    expected_type: type[object],
) -> None:
    raw_event = valid_submit_event() | {"type": event_type, "payload": payload}

    assert isinstance(parse_client_event(raw_event), expected_type)


def test_client_event_rejects_an_unknown_command_type() -> None:
    raw_event = valid_submit_event() | {"type": "execute_anything"}

    with pytest.raises(ValidationError):
        parse_client_event(raw_event)


def test_client_event_schema_declares_type_as_the_discriminator() -> None:
    schema = client_event_schema()
    discriminator = schema["discriminator"]
    assert isinstance(discriminator, dict)

    assert discriminator["propertyName"] == "type"
    assert set(discriminator["mapping"]) == {
        "activate",
        "approval_decision",
        "interrupt",
        "mode_change",
        "submit_text",
        "transfer_device",
    }


def test_server_event_serialization_preserves_typed_envelope() -> None:
    event = StateChanged(
        version=1,
        event_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf4"),
        session_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf1"),
        turn_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf2"),
        sequence=8,
        timestamp=datetime(2026, 8, 7, 18, 30, tzinfo=UTC),
        type="state_changed",
        payload={"state": "listening", "detail": "wake_word"},
    )

    serialized = serialize_server_event(event)

    assert serialized["type"] == "state_changed"
    assert serialized["payload"] == {"state": "listening", "detail": "wake_word"}
    assert serialized["timestamp"] == "2026-08-07T18:30:00Z"


def test_server_text_event_has_a_bounded_nonempty_delta() -> None:
    with pytest.raises(ValidationError):
        AssistantText(
            version=1,
            event_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf4"),
            session_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf1"),
            turn_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf2"),
            sequence=8,
            timestamp=datetime(2026, 8, 7, 18, 30, tzinfo=UTC),
            type="assistant_text",
            payload={"text": "", "is_final": False},
        )


def test_proactive_suggestion_is_observe_only_and_explains_why_it_appeared() -> None:
    event = ProactiveSuggestionEvent(
        version=1,
        event_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf4"),
        session_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf1"),
        turn_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf2"),
        sequence=8,
        timestamp=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
        type="proactive_suggestion",
        payload={
            "suggestion_id": "019fd977-1d96-7892-950c-6afbb71f7cf5",
            "title": "Research PDF is ready",
            "message": "The PDF finished downloading. Want me to summarize or file it?",
            "reason": "A new completed PDF appeared in Downloads.",
            "suggested_prompt": "Summarize the new PDF in Downloads.",
            "priority": "quiet",
            "expires_at": "2026-08-10T22:00:00Z",
            "proposed_action": None,
        },
    )

    serialized = serialize_server_event(event)

    assert serialized["type"] == "proactive_suggestion"
    payload = serialized["payload"]
    assert isinstance(payload, dict)
    assert payload["proposed_action"] is None
    assert payload["reason"] == "A new completed PDF appeared in Downloads."
