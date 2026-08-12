from datetime import datetime
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventEnvelope(ProtocolModel):
    version: Literal[1]
    event_id: UUID
    session_id: UUID
    turn_id: UUID
    sequence: Annotated[int, Field(strict=True, ge=0)]
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp must include a UTC offset")
        return timestamp


DeviceId = Annotated[str, Field(min_length=1, max_length=128)]


class ActivatePayload(ProtocolModel):
    device_id: DeviceId
    source: Literal["wake_word", "keyboard", "ui", "shortcut"]


class Activate(EventEnvelope):
    type: Literal["activate"]
    payload: ActivatePayload


class SubmitTextPayload(ProtocolModel):
    text: Annotated[str, Field(min_length=1, max_length=32_000)]
    device_id: DeviceId


class SubmitText(EventEnvelope):
    type: Literal["submit_text"]
    payload: SubmitTextPayload


class InterruptPayload(ProtocolModel):
    device_id: DeviceId
    reason: Literal["user_speech", "user_command", "device_disconnect"]


class Interrupt(EventEnvelope):
    type: Literal["interrupt"]
    payload: InterruptPayload


class ApprovalDecisionPayload(ProtocolModel):
    device_id: DeviceId
    approval_id: UUID
    decision: Literal["approve", "reject"]


class ApprovalDecision(EventEnvelope):
    type: Literal["approval_decision"]
    payload: ApprovalDecisionPayload


class TransferDevicePayload(ProtocolModel):
    from_device_id: DeviceId
    to_device_id: DeviceId


class TransferDevice(EventEnvelope):
    type: Literal["transfer_device"]
    payload: TransferDevicePayload


class ModeChangePayload(ProtocolModel):
    device_id: DeviceId
    mode: Literal["normal", "private", "meeting", "lecture", "ambient"]


class ModeChange(EventEnvelope):
    type: Literal["mode_change"]
    payload: ModeChangePayload


ClientEvent: TypeAlias = Annotated[
    Activate | SubmitText | Interrupt | ApprovalDecision | TransferDevice | ModeChange,
    Field(discriminator="type"),
]


class StateChangedPayload(ProtocolModel):
    state: Literal[
        "idle",
        "listening",
        "transcribing",
        "thinking",
        "acting",
        "awaiting_approval",
        "speaking",
        "private",
        "meeting",
        "lecture",
        "ambient",
        "unavailable",
    ]
    detail: Annotated[str | None, Field(max_length=512)] = None


class StateChanged(EventEnvelope):
    type: Literal["state_changed"]
    payload: StateChangedPayload


class TranscriptPayload(ProtocolModel):
    text: Annotated[str, Field(min_length=1, max_length=32_000)]
    speaker: Literal["user", "assistant", "ambient"]
    is_final: bool
    device_id: DeviceId


class Transcript(EventEnvelope):
    type: Literal["transcript"]
    payload: TranscriptPayload


class AssistantTextPayload(ProtocolModel):
    text: Annotated[str, Field(max_length=32_000)]
    is_final: bool

    @model_validator(mode="after")
    def require_content_for_delta(self) -> "AssistantTextPayload":
        if not self.is_final and not self.text:
            raise ValueError("a streaming assistant delta cannot be empty")
        return self


class AssistantText(EventEnvelope):
    type: Literal["assistant_text"]
    payload: AssistantTextPayload


class ApprovalRequiredPayload(ProtocolModel):
    approval_id: UUID
    capability: Annotated[str, Field(min_length=1, max_length=160)]
    summary: Annotated[str, Field(min_length=1, max_length=1_000)]
    risk: Literal["local_reversible", "external_irreversible"]
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def require_expiry_timezone(cls, timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("expires_at must include a UTC offset")
        return timestamp


class ApprovalRequired(EventEnvelope):
    type: Literal["approval_required"]
    payload: ApprovalRequiredPayload


class CapabilityResultPayload(ProtocolModel):
    action_id: UUID
    capability: Annotated[str, Field(min_length=1, max_length=160)]
    status: Literal["succeeded", "failed", "cancelled", "denied"]
    message: Annotated[str, Field(min_length=1, max_length=4_000)]
    undo_available: bool


class CapabilityResult(EventEnvelope):
    type: Literal["capability_result"]
    payload: CapabilityResultPayload


class ProactiveSuggestionPayload(ProtocolModel):
    suggestion_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=120)]
    message: Annotated[str, Field(min_length=1, max_length=1_000)]
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    suggested_prompt: Annotated[str, Field(min_length=1, max_length=2_000)]
    priority: Literal["quiet", "normal", "important"]
    expires_at: datetime
    proposed_action: None = None

    @field_validator("expires_at")
    @classmethod
    def require_suggestion_expiry_timezone(cls, timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("expires_at must include a UTC offset")
        return timestamp


class ProactiveSuggestionEvent(EventEnvelope):
    type: Literal["proactive_suggestion"]
    payload: ProactiveSuggestionPayload


class ErrorPayload(ProtocolModel):
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")]
    message: Annotated[str, Field(min_length=1, max_length=1_000)]
    recoverable: bool


class ErrorEvent(EventEnvelope):
    type: Literal["error"]
    payload: ErrorPayload


ServerEvent: TypeAlias = Annotated[
    StateChanged
    | Transcript
    | AssistantText
    | ApprovalRequired
    | CapabilityResult
    | ProactiveSuggestionEvent
    | ErrorEvent,
    Field(discriminator="type"),
]


_CLIENT_EVENT = TypeAdapter(ClientEvent)
_SERVER_EVENT = TypeAdapter(ServerEvent)


def parse_client_event(raw_event: object) -> ClientEvent:
    return _CLIENT_EVENT.validate_python(raw_event)


def client_event_schema() -> dict[str, object]:
    return _CLIENT_EVENT.json_schema()


def server_event_schema() -> dict[str, object]:
    return _SERVER_EVENT.json_schema()


def serialize_server_event(event: ServerEvent) -> dict[str, object]:
    return _SERVER_EVENT.dump_python(event, mode="json")
