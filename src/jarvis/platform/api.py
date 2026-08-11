import asyncio
import base64
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from jarvis.agency.capabilities import (
    ActionCoordinator,
    ApprovalPrompt,
    ExecutionResult,
    ExecutionStatus,
)
from jarvis.agency.policy import ApprovalChoice
from jarvis.agency.scheduler import ScheduledExecutionEvent
from jarvis.memory.history import (
    ConversationHistory,
    ConversationMessage,
    ConversationRole,
)
from jarvis.memory.store import MemoryConflict, MemoryFact
from jarvis.platform.models import ChatMessage
from jarvis.platform.pairing import AuthenticationError, PairingError, PhonePairing
from jarvis.platform.protocol import (
    Activate,
    ApprovalDecision,
    ApprovalRequired,
    ApprovalRequiredPayload,
    AssistantText,
    AssistantTextPayload,
    CapabilityResult,
    CapabilityResultPayload,
    ErrorEvent,
    ErrorPayload,
    Interrupt,
    ModeChange,
    ServerEvent,
    StateChanged,
    StateChangedPayload,
    SubmitText,
    SubmitTextPayload,
    Transcript,
    TranscriptPayload,
    TransferDevice,
    parse_client_event,
    serialize_server_event,
)
from jarvis.runtime.assistant import (
    AssistantResponder,
    TextDelta,
    ToolProposal,
    TurnCancelled,
    TurnComplete,
)
from jarvis.runtime.awareness import parse_awareness_command
from jarvis.runtime.conversation import ListeningMode, RuntimeCoordinator, RuntimeSnapshot
from jarvis.runtime.lifecycle import ApplicationLifecycle
from jarvis.runtime.resources import ResourcePressure
from jarvis.speech.desktop import (
    DesktopAmbientTranscript,
    DesktopBargeIn,
    DesktopSpeechError,
    DesktopSpeechSource,
    DesktopTranscript,
    DesktopWake,
)
from jarvis.speech.engine import AwarenessMode
from jarvis.speech.output import (
    SpeechOutputFactory,
    SpeechOutputSession,
)
from jarvis.speech.remote import SpeechInput

MAX_TEXT_FRAME_BYTES = 64 * 1024
MAX_AUDIO_FRAME_BYTES = 8 * 1024
MAX_TOOL_ROUNDS = 4


class ApiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bearer_token: str = Field(min_length=32, max_length=512)
    desktop_session_token: str | None = Field(default=None, min_length=32, max_length=512)
    ui_directory: Path | None = None
    phone_base_url: str | None = None
    allowed_hosts: tuple[str, ...] = Field(min_length=1)
    allowed_origins: tuple[str, ...] = Field(min_length=1)

    @field_validator("phone_base_url")
    @classmethod
    def require_private_https_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("phone base URL must be an HTTPS origin")
        return value.rstrip("/")


class HealthResponse(BaseModel):
    status: str
    protocol_version: int


class RuntimeStateResponse(BaseModel):
    phase: str
    mode: str
    session_id: UUID | None
    turn_id: UUID | None
    active_device_id: str | None
    cancellation_generation: int


class ConversationHistoryResponse(BaseModel):
    messages: tuple[ConversationMessage, ...]


class MemorySearchResponse(BaseModel):
    facts: tuple[MemoryFact, ...]


class MemoryConflictResponse(BaseModel):
    conflicts: tuple[MemoryConflict, ...]


class CorrectMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=16_000)


class MemoryAdministration(Protocol):
    def search(self, query: str, *, limit: int = 12) -> list[MemoryFact]: ...

    def correct(
        self,
        fact_id: UUID,
        *,
        content: str,
        source_event_id: UUID,
        corrected_at: datetime,
    ) -> MemoryFact: ...

    def forget(self, fact_id: UUID, *, forgotten_at: datetime) -> None: ...

    def get(self, fact_id: UUID) -> MemoryFact | None: ...

    def list_conflicts(self) -> list[MemoryConflict]: ...


class ScheduledEventSource(Protocol):
    def subscribe(self) -> AsyncIterator[ScheduledExecutionEvent]: ...

    def resolve(self, approval_id: UUID) -> None: ...


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PairingOfferResponse(BaseModel):
    pairing_id: UUID
    secret: str
    expires_at: datetime
    pairing_url: str | None


class CompletePairingRequest(StrictRequest):
    secret: str = Field(min_length=32, max_length=512)
    device_id: str = Field(min_length=1, max_length=128)
    public_key_jwk: dict[str, str]


class PairedDeviceResponse(BaseModel):
    device_id: str
    paired_at: datetime


class ChallengeRequest(StrictRequest):
    device_id: str = Field(min_length=1, max_length=128)


class ChallengeResponse(BaseModel):
    challenge_id: UUID
    challenge: str
    expires_at: datetime


class SessionRequest(StrictRequest):
    challenge_id: UUID
    signature: str = Field(min_length=8, max_length=512)


class SessionResponse(BaseModel):
    token: str
    device_id: str
    expires_at: datetime


@dataclass(frozen=True)
class ConnectionIdentity:
    device_id: str
    kind: str


@dataclass(frozen=True)
class PendingApprovalTurn:
    source_event: SubmitText
    continuation: tuple[ChatMessage, ...]


def create_app(
    *,
    runtime: RuntimeCoordinator,
    settings: ApiSettings,
    phone_pairing: PhonePairing,
    assistant: AssistantResponder | None = None,
    speech_input: SpeechInput | None = None,
    actions: ActionCoordinator | None = None,
    desktop_speech: DesktopSpeechSource | None = None,
    history: ConversationHistory | None = None,
    speech_output: SpeechOutputFactory | None = None,
    memory: MemoryAdministration | None = None,
    lifecycle: ApplicationLifecycle | None = None,
    scheduled_events: ScheduledEventSource | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        lifecycle_started = False
        speech_started = False
        if lifecycle is not None:
            await lifecycle.start()
            lifecycle_started = True
        if desktop_speech is not None:
            try:
                await desktop_speech.start()
                speech_started = True
            except (OSError, RuntimeError, ValueError) as error:
                application.state.desktop_speech_error = type(error).__name__
        try:
            yield
        finally:
            try:
                if speech_started and desktop_speech is not None:
                    await desktop_speech.stop()
            finally:
                if lifecycle_started and lifecycle is not None:
                    await lifecycle.stop()

    app = FastAPI(
        title="JARVIS local control plane",
        version="0.1.0",
        debug=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["DELETE", "GET", "OPTIONS", "PATCH", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(self), geolocation=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; "
            "style-src 'self'; font-src 'self'; media-src 'self' blob:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    public = APIRouter(prefix="/v1")

    @public.get("/health")
    def health() -> HealthResponse:
        return HealthResponse(status="ok", protocol_version=1)

    bearer = HTTPBearer(auto_error=False)

    def require_token(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        supplied = (
            credentials.credentials
            if credentials is not None and credentials.scheme.lower() == "bearer"
            else None
        )
        accepted = supplied is not None and _accepted_http_token(supplied, settings)
        if not accepted:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    protected = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])

    @protected.get("/state")
    def state() -> RuntimeStateResponse:
        return _state_response(runtime.snapshot())

    @protected.get("/history")
    def conversation_history(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> ConversationHistoryResponse:
        messages = () if history is None else tuple(history.recent(limit=limit))
        return ConversationHistoryResponse(messages=messages)

    @protected.get("/memory/search")
    def search_memory(
        query: Annotated[str, Query(min_length=1, max_length=1_000)],
        limit: Annotated[int, Query(ge=1, le=100)] = 12,
    ) -> MemorySearchResponse:
        facts = () if memory is None else tuple(memory.search(query, limit=limit))
        return MemorySearchResponse(facts=facts)

    @protected.get("/memory/conflicts")
    def memory_conflicts() -> MemoryConflictResponse:
        conflicts = () if memory is None else tuple(memory.list_conflicts())
        return MemoryConflictResponse(conflicts=conflicts)

    @protected.patch("/memory/{fact_id}")
    def correct_memory(fact_id: UUID, request: CorrectMemoryRequest) -> MemoryFact:
        if memory is None:
            raise HTTPException(status_code=503, detail="memory is unavailable")
        try:
            return memory.correct(
                fact_id,
                content=request.content,
                source_event_id=uuid4(),
                corrected_at=datetime.now(UTC),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail="memory fact not found") from error

    @protected.delete("/memory/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
    def forget_memory(fact_id: UUID) -> Response:
        if memory is None:
            raise HTTPException(status_code=503, detail="memory is unavailable")
        if memory.get(fact_id) is None:
            raise HTTPException(status_code=404, detail="memory fact not found")
        memory.forget(fact_id, forgotten_at=datetime.now(UTC))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @protected.post("/pairing/offers", status_code=status.HTTP_201_CREATED)
    def create_pairing_offer() -> PairingOfferResponse:
        offer = phone_pairing.create_offer(now=datetime.now(UTC))
        return PairingOfferResponse(
            pairing_id=offer.pairing_id,
            secret=offer.secret,
            expires_at=offer.expires_at,
            pairing_url=_phone_pairing_url(
                settings.phone_base_url,
                pairing_id=offer.pairing_id,
                secret=offer.secret,
            ),
        )

    @public.post(
        "/pairing/{pairing_id}/complete",
        status_code=status.HTTP_201_CREATED,
    )
    def complete_pairing(pairing_id: UUID, request: CompletePairingRequest) -> PairedDeviceResponse:
        try:
            device = phone_pairing.complete_pairing(
                pairing_id=pairing_id,
                secret=request.secret,
                device_id=request.device_id,
                public_key_jwk=request.public_key_jwk,
                now=datetime.now(UTC),
            )
        except PairingError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return PairedDeviceResponse(device_id=device.device_id, paired_at=device.paired_at)

    @public.post("/auth/challenges", status_code=status.HTTP_201_CREATED)
    def create_challenge(request: ChallengeRequest) -> ChallengeResponse:
        try:
            challenge = phone_pairing.create_challenge(
                device_id=request.device_id,
                now=datetime.now(UTC),
            )
        except AuthenticationError as error:
            raise HTTPException(status_code=401, detail="authentication failed") from error
        return ChallengeResponse(
            challenge_id=challenge.challenge_id,
            challenge=_base64url(challenge.challenge),
            expires_at=challenge.expires_at,
        )

    @public.post("/auth/sessions", status_code=status.HTTP_201_CREATED)
    def create_phone_session(request: SessionRequest) -> SessionResponse:
        try:
            session = phone_pairing.verify_challenge(
                challenge_id=request.challenge_id,
                signature=request.signature,
                now=datetime.now(UTC),
            )
        except AuthenticationError as error:
            raise HTTPException(status_code=401, detail="authentication failed") from error
        return SessionResponse(
            token=session.token,
            device_id=session.device_id,
            expires_at=session.expires_at,
        )

    app.include_router(public)
    app.include_router(protected)

    @app.websocket("/v1/live")
    async def live(websocket: WebSocket) -> None:
        identity = _websocket_identity(websocket, settings, phone_pairing)
        if identity is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept(subprotocol=_negotiated_subprotocol(websocket))
        sequence = 0
        send_lock = asyncio.Lock()
        generation_task: asyncio.Task[None] | None = None
        pending_approvals: dict[UUID, PendingApprovalTurn] = {}
        ambient_session_id: UUID | None = None
        connection_session = uuid4()
        connection_turn = uuid4()

        async def emit(builder: Callable[[int], ServerEvent]) -> None:
            nonlocal sequence
            async with send_lock:
                event = builder(sequence)
                sequence += 1
                await websocket.send_json(serialize_server_event(event))

        async def send_phone_pcm(pcm: bytes) -> None:
            async with send_lock:
                await websocket.send_bytes(pcm)

        def hold_approval(
            approval_id: UUID,
            source_event: SubmitText,
            continuation: tuple[ChatMessage, ...],
        ) -> None:
            pending_approvals[approval_id] = PendingApprovalTurn(
                source_event=source_event,
                continuation=continuation,
            )

        async def cancel_generation() -> None:
            nonlocal generation_task
            if generation_task is None or generation_task.done():
                return
            generation_task.cancel()
            with suppress(asyncio.CancelledError):
                await generation_task

        async def start_text_turn(source_event: SubmitText) -> None:
            nonlocal ambient_session_id, generation_task
            await cancel_generation()
            awareness = parse_awareness_command(source_event.payload.text)
            if awareness is not None:
                await emit(
                    lambda current: _transcript_event(
                        source_event,
                        sequence=current,
                    )
                )
                runtime.set_mode(awareness.mode)
                if desktop_speech is not None:
                    await desktop_speech.set_mode(AwarenessMode(awareness.mode.value))
                ambient_session_id = (
                    uuid4()
                    if awareness.mode
                    in {
                        ListeningMode.MEETING,
                        ListeningMode.LECTURE,
                        ListeningMode.AMBIENT,
                    }
                    else None
                )
                await emit(
                    lambda current: _assistant_text_event(
                        source_event,
                        text=awareness.acknowledgement,
                        is_final=False,
                        sequence=current,
                    )
                )
                await emit(
                    lambda current: _assistant_text_event(
                        source_event,
                        text="",
                        is_final=True,
                        sequence=current,
                    )
                )
                if speech_output is not None:
                    awareness_speech = speech_output.open(
                        device_id=source_event.payload.device_id,
                        send_phone_pcm=send_phone_pcm,
                    )
                    await awareness_speech.push(awareness.acknowledgement)
                    await awareness_speech.finish()
                completed = runtime.complete_turn()
                await emit(
                    lambda current: _state_event(
                        completed,
                        session_id=source_event.session_id,
                        turn_id=source_event.turn_id,
                        sequence=current,
                    )
                )
                return
            snapshot = _dispatch(runtime, source_event)
            persist_turn = history is not None and snapshot.mode is not ListeningMode.PRIVATE
            await emit(
                lambda current: _state_event(
                    snapshot,
                    session_id=source_event.session_id,
                    turn_id=source_event.turn_id,
                    sequence=current,
                )
            )
            await emit(
                lambda current: _transcript_event(
                    source_event,
                    sequence=current,
                )
            )
            if persist_turn and history is not None:
                await asyncio.to_thread(
                    history.append,
                    ConversationMessage(
                        message_id=source_event.event_id,
                        source_event_id=source_event.event_id,
                        session_id=source_event.session_id,
                        turn_id=source_event.turn_id,
                        role=ConversationRole.USER,
                        content=source_event.payload.text,
                        device_id=source_event.payload.device_id,
                        created_at=source_event.timestamp,
                    ),
                )
            generation_task = asyncio.create_task(
                _stream_assistant(
                    assistant=assistant,
                    actions=actions,
                    runtime=runtime,
                    client_event=source_event,
                    emit=emit,
                    history=history if persist_turn else None,
                    speech=(
                        None
                        if speech_output is None or assistant is None
                        else speech_output.open(
                            device_id=source_event.payload.device_id,
                            send_phone_pcm=send_phone_pcm,
                        )
                    ),
                    hold_approval=hold_approval,
                ),
                name=f"jarvis-turn-{source_event.turn_id}",
            )

        async def pump_desktop_speech() -> None:
            if desktop_speech is None:
                return
            while True:
                speech_event = await desktop_speech.next_event()
                age_seconds = (datetime.now(UTC) - speech_event.occurred_at).total_seconds()
                if age_seconds > 5:
                    continue
                if isinstance(speech_event, DesktopWake):
                    await cancel_generation()
                    wake_session = uuid4()
                    wake_turn = uuid4()
                    snapshot = runtime.activate(
                        session_id=wake_session,
                        turn_id=wake_turn,
                        device_id="desktop",
                    )
                    await emit(
                        lambda current, state=snapshot, session=wake_session, turn=wake_turn: (
                            _state_event(
                                state,
                                session_id=session,
                                turn_id=turn,
                                sequence=current,
                            )
                        )
                    )
                elif isinstance(speech_event, DesktopTranscript):
                    snapshot = runtime.snapshot()
                    if (
                        snapshot.phase.value != "listening"
                        or snapshot.active_device_id != "desktop"
                        or snapshot.session_id is None
                        or snapshot.turn_id is None
                    ):
                        continue
                    await start_text_turn(
                        SubmitText(
                            version=1,
                            event_id=uuid4(),
                            session_id=snapshot.session_id,
                            turn_id=snapshot.turn_id,
                            sequence=0,
                            timestamp=speech_event.occurred_at,
                            type="submit_text",
                            payload=SubmitTextPayload(
                                text=speech_event.text,
                                device_id="desktop",
                            ),
                        )
                    )
                elif isinstance(speech_event, DesktopAmbientTranscript):
                    snapshot = runtime.snapshot()
                    if snapshot.mode not in {
                        ListeningMode.MEETING,
                        ListeningMode.LECTURE,
                        ListeningMode.AMBIENT,
                    }:
                        continue
                    session_id = ambient_session_id or uuid4()
                    turn_id = uuid4()
                    event_id = uuid4()
                    if history is not None:
                        await asyncio.to_thread(
                            history.append,
                            ConversationMessage(
                                message_id=event_id,
                                source_event_id=event_id,
                                session_id=session_id,
                                turn_id=turn_id,
                                role=ConversationRole.AMBIENT,
                                content=speech_event.text,
                                device_id="desktop",
                                created_at=speech_event.occurred_at,
                            ),
                        )
                    ambient_text = speech_event.text
                    ambient_occurred_at = speech_event.occurred_at

                    def build_ambient_transcript(
                        current: int,
                        session: UUID = session_id,
                        turn: UUID = turn_id,
                        event: UUID = event_id,
                        text: str = ambient_text,
                        occurred: datetime = ambient_occurred_at,
                    ) -> ServerEvent:
                        return _ambient_transcript_event(
                            text=text,
                            occurred_at=occurred,
                            event_id=event,
                            session_id=session,
                            turn_id=turn,
                            sequence=current,
                        )

                    await emit(build_ambient_transcript)
                elif isinstance(speech_event, DesktopBargeIn):
                    snapshot = runtime.snapshot()
                    if snapshot.phase.value == "idle" or snapshot.active_device_id != "desktop":
                        continue
                    await cancel_generation()
                    interrupted = runtime.interrupt(device_id="desktop")
                    if interrupted.session_id is None or interrupted.turn_id is None:
                        continue
                    interrupted_session = interrupted.session_id
                    interrupted_turn = interrupted.turn_id

                    def build_interrupted_state(
                        sequence: int,
                        state: RuntimeSnapshot = interrupted,
                        session: UUID = interrupted_session,
                        turn: UUID = interrupted_turn,
                    ) -> ServerEvent:
                        return _state_event(
                            state,
                            session_id=session,
                            turn_id=turn,
                            sequence=sequence,
                        )

                    await emit(build_interrupted_state)
                elif isinstance(speech_event, DesktopSpeechError):
                    snapshot = runtime.snapshot()
                    error_session = snapshot.session_id or connection_session
                    error_turn = snapshot.turn_id or connection_turn
                    await emit(
                        lambda current, session=error_session, turn=error_turn: (
                            _desktop_speech_error_event(
                                session_id=session,
                                turn_id=turn,
                                sequence=current,
                            )
                        )
                    )

        async def pump_scheduled_events() -> None:
            if scheduled_events is None:
                return
            async for scheduled_event in scheduled_events.subscribe():
                snapshot = runtime.snapshot()
                event_session = snapshot.session_id or connection_session
                event_turn = snapshot.turn_id or connection_turn
                approval = scheduled_event.execution.approval
                if approval is not None:
                    await emit(
                        lambda current, prompt=approval, session=event_session, turn=event_turn: (
                            _scheduled_approval_required_event(
                                session_id=session,
                                turn_id=turn,
                                approval=prompt,
                                sequence=current,
                            )
                        )
                    )
                    continue
                result = scheduled_event.execution.result
                if result.status is ExecutionStatus.AWAITING_APPROVAL:
                    continue
                await emit(
                    lambda current, event=scheduled_event, session=event_session, turn=event_turn: (
                        _scheduled_capability_result_event(
                            session_id=session,
                            turn_id=turn,
                            capability=event.capability,
                            result=event.execution.result,
                            sequence=current,
                        )
                    )
                )

        await emit(
            lambda current: _state_event(
                runtime.snapshot(),
                session_id=connection_session,
                turn_id=connection_turn,
                sequence=current,
            )
        )
        desktop_speech_task = (
            asyncio.create_task(pump_desktop_speech(), name="jarvis-desktop-speech-events")
            if identity.kind == "desktop" and desktop_speech is not None
            else None
        )
        scheduled_event_task = (
            asyncio.create_task(
                pump_scheduled_events(),
                name="jarvis-scheduled-events",
            )
            if scheduled_events is not None
            else None
        )

        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                text_frame = message.get("text")
                binary_frame = message.get("bytes")
                if text_frame is not None:
                    if len(text_frame.encode("utf-8")) > MAX_TEXT_FRAME_BYTES:
                        await websocket.close(code=status.WS_1009_MESSAGE_TOO_BIG)
                        break
                    client_event = parse_client_event(json.loads(text_frame))
                    if not _event_belongs_to_identity(client_event, identity):
                        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                        break
                    if isinstance(client_event, SubmitText):
                        await start_text_turn(client_event)
                        continue
                    if isinstance(client_event, ApprovalDecision):
                        approval_event = client_event
                        if actions is None:
                            await emit(
                                lambda sequence, event=approval_event: _approval_error_event(
                                    event,
                                    sequence=sequence,
                                    code="approval_unavailable",
                                    message="The capability engine is not configured.",
                                    recoverable=True,
                                )
                            )
                            continue
                        capability_name = actions.pending_capability(
                            client_event.payload.approval_id
                        )
                        if actions.pending_is_scheduled(client_event.payload.approval_id):
                            try:
                                scheduled_result = await actions.decide(
                                    approval_id=client_event.payload.approval_id,
                                    choice=ApprovalChoice(client_event.payload.decision),
                                    device_id=client_event.payload.device_id,
                                    now=datetime.now(UTC),
                                )
                            except (LookupError, PermissionError, ValueError):
                                await emit(
                                    lambda sequence, event=client_event: _approval_error_event(
                                        event,
                                        sequence=sequence,
                                        code="approval_invalid",
                                        message=(
                                            "That approval is invalid, expired, or already "
                                            "resolved."
                                        ),
                                        recoverable=True,
                                    )
                                )
                                continue
                            if scheduled_events is not None:
                                scheduled_events.resolve(client_event.payload.approval_id)
                            assert capability_name is not None
                            await emit(
                                lambda sequence, event=client_event, capability=capability_name, result=scheduled_result: (  # noqa: E501
                                    _capability_result_event(
                                        event,
                                        capability=capability,
                                        result=result,
                                        sequence=sequence,
                                    )
                                )
                            )
                            continue
                        pending_turn = pending_approvals.get(client_event.payload.approval_id)
                        current = runtime.snapshot()
                        if (
                            capability_name is None
                            or pending_turn is None
                            or pending_turn.source_event.session_id != client_event.session_id
                            or pending_turn.source_event.turn_id != client_event.turn_id
                            or current.session_id != client_event.session_id
                            or current.turn_id != client_event.turn_id
                        ):
                            await emit(
                                lambda sequence, event=approval_event: _approval_error_event(
                                    event,
                                    sequence=sequence,
                                    code="approval_invalid",
                                    message=(
                                        "That approval is invalid, expired, or already resolved."
                                    ),
                                    recoverable=True,
                                )
                            )
                            continue
                        await cancel_generation()
                        pending_approvals.pop(client_event.payload.approval_id, None)
                        try:
                            result = await actions.decide(
                                approval_id=client_event.payload.approval_id,
                                choice=ApprovalChoice(client_event.payload.decision),
                                device_id=client_event.payload.device_id,
                                now=datetime.now(UTC),
                            )
                        except (LookupError, PermissionError, ValueError):
                            await emit(
                                lambda sequence, event=approval_event: _approval_error_event(
                                    event,
                                    sequence=sequence,
                                    code="approval_invalid",
                                    message=(
                                        "That approval is invalid, expired, or already resolved."
                                    ),
                                    recoverable=True,
                                )
                            )
                            continue
                        if client_event.payload.decision == "approve":
                            snapshot = runtime.mark_acting(device_id=client_event.payload.device_id)
                            await emit(
                                lambda sequence, state=snapshot, event=approval_event: _state_event(
                                    state,
                                    session_id=event.session_id,
                                    turn_id=event.turn_id,
                                    sequence=sequence,
                                )
                            )

                        def build_capability_result(
                            sequence: int,
                            event: ApprovalDecision = approval_event,
                            capability: str = capability_name,
                            execution: ExecutionResult = result,
                        ) -> ServerEvent:
                            return _capability_result_event(
                                event,
                                capability=capability,
                                result=execution,
                                sequence=sequence,
                            )

                        await emit(build_capability_result)
                        resumed = runtime.resume_thinking(device_id=client_event.payload.device_id)
                        await emit(
                            lambda sequence, state=resumed, event=approval_event: _state_event(
                                state,
                                session_id=event.session_id,
                                turn_id=event.turn_id,
                                sequence=sequence,
                            )
                        )
                        source_event = pending_turn.source_event
                        generation_task = asyncio.create_task(
                            _stream_assistant(
                                assistant=assistant,
                                actions=actions,
                                runtime=runtime,
                                client_event=source_event,
                                emit=emit,
                                history=(
                                    history
                                    if history is not None
                                    and resumed.mode is not ListeningMode.PRIVATE
                                    else None
                                ),
                                speech=(
                                    None
                                    if speech_output is None or assistant is None
                                    else speech_output.open(
                                        device_id=source_event.payload.device_id,
                                        send_phone_pcm=send_phone_pcm,
                                    )
                                ),
                                continuation=(
                                    *pending_turn.continuation,
                                    _tool_result_context(capability_name, result),
                                ),
                                hold_approval=hold_approval,
                            ),
                            name=f"jarvis-approved-turn-{source_event.turn_id}",
                        )
                        continue
                    if isinstance(client_event, (Activate, Interrupt)):
                        await cancel_generation()
                        pending_approvals.clear()
                        if speech_input is not None:
                            await speech_input.reset(identity.device_id)
                    if isinstance(client_event, ModeChange) and desktop_speech is not None:
                        await desktop_speech.set_mode(AwarenessMode(client_event.payload.mode))
                        if client_event.payload.mode in {"meeting", "lecture", "ambient"}:
                            ambient_session_id = ambient_session_id or uuid4()
                        else:
                            ambient_session_id = None
                    snapshot = _dispatch(runtime, client_event)
                    await emit(
                        lambda current, current_snapshot=snapshot, source_event=client_event: (
                            _state_event(
                                current_snapshot,
                                session_id=source_event.session_id,
                                turn_id=source_event.turn_id,
                                sequence=current,
                            )
                        )
                    )
                elif binary_frame is not None:
                    if (
                        not binary_frame
                        or len(binary_frame) > MAX_AUDIO_FRAME_BYTES
                        or len(binary_frame) % 2
                    ):
                        await websocket.close(code=status.WS_1009_MESSAGE_TOO_BIG)
                        break
                    snapshot = runtime.snapshot()
                    if (
                        speech_input is None
                        or snapshot.phase.value != "listening"
                        or snapshot.active_device_id != identity.device_id
                    ):
                        continue
                    transcript = await speech_input.ingest(identity.device_id, binary_frame)
                    if transcript is not None and snapshot.session_id and snapshot.turn_id:
                        await start_text_turn(
                            SubmitText(
                                version=1,
                                event_id=uuid4(),
                                session_id=snapshot.session_id,
                                turn_id=snapshot.turn_id,
                                sequence=0,
                                timestamp=datetime.now(UTC),
                                type="submit_text",
                                payload=SubmitTextPayload(
                                    device_id=identity.device_id,
                                    text=transcript,
                                ),
                            )
                        )
        except (WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            await cancel_generation()
            if desktop_speech_task is not None:
                desktop_speech_task.cancel()
                with suppress(asyncio.CancelledError):
                    await desktop_speech_task
            if scheduled_event_task is not None:
                scheduled_event_task.cancel()
                with suppress(asyncio.CancelledError):
                    await scheduled_event_task

    if settings.ui_directory is not None:
        app.mount(
            "/",
            StaticFiles(directory=settings.ui_directory, html=True, check_dir=True),
            name="phone-ui",
        )

    return app


def _websocket_identity(
    websocket: WebSocket,
    settings: ApiSettings,
    phone_pairing: PhonePairing,
) -> ConnectionIdentity | None:
    origin = websocket.headers.get("origin")
    authorization = websocket.headers.get("authorization")
    if origin not in settings.allowed_origins:
        return None
    if authorization is not None:
        scheme, separator, token = authorization.partition(" ")
        if bool(separator) and scheme.lower() == "bearer" and _accepted_http_token(token, settings):
            return ConnectionIdentity(device_id="desktop", kind="desktop")

    desktop_prefix = "jarvis.desktop."
    session_prefix = "jarvis.session."
    for protocol in _requested_subprotocols(websocket):
        if protocol.startswith(desktop_prefix):
            desktop_token = settings.desktop_session_token
            if desktop_token is not None and secrets.compare_digest(
                protocol.removeprefix(desktop_prefix), desktop_token
            ):
                return ConnectionIdentity(device_id="desktop", kind="desktop")
            return None
        if not protocol.startswith(session_prefix):
            continue
        try:
            device_id = phone_pairing.authenticate_session(
                protocol.removeprefix(session_prefix),
                now=datetime.now(UTC),
            )
        except AuthenticationError:
            return None
        return ConnectionIdentity(device_id=device_id, kind="phone")
    return None


def _accepted_http_token(supplied: str, settings: ApiSettings) -> bool:
    if secrets.compare_digest(supplied, settings.bearer_token):
        return True
    return settings.desktop_session_token is not None and secrets.compare_digest(
        supplied,
        settings.desktop_session_token,
    )


def _requested_subprotocols(websocket: WebSocket) -> list[str]:
    header = websocket.headers.get("sec-websocket-protocol", "")
    return [protocol.strip() for protocol in header.split(",") if protocol.strip()]


def _event_belongs_to_identity(client_event: object, identity: ConnectionIdentity) -> bool:
    if isinstance(client_event, (Activate, SubmitText, Interrupt, ApprovalDecision, ModeChange)):
        return client_event.payload.device_id == identity.device_id
    if isinstance(client_event, TransferDevice):
        return client_event.payload.from_device_id == identity.device_id
    return False


def _negotiated_subprotocol(websocket: WebSocket) -> str | None:
    return "jarvis.v1" if "jarvis.v1" in _requested_subprotocols(websocket) else None


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _phone_pairing_url(base_url: str | None, *, pairing_id: UUID, secret: str) -> str | None:
    if base_url is None:
        return None
    payload = json.dumps(
        {"pairingId": str(pairing_id), "secret": secret},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{base_url}/#pair={_base64url(payload)}"


def _dispatch(runtime: RuntimeCoordinator, client_event: object) -> RuntimeSnapshot:
    if isinstance(client_event, Activate):
        return runtime.activate(
            session_id=client_event.session_id,
            turn_id=client_event.turn_id,
            device_id=client_event.payload.device_id,
        )
    if isinstance(client_event, SubmitText):
        return runtime.submit_input(
            device_id=client_event.payload.device_id,
            text=client_event.payload.text,
        )
    if isinstance(client_event, Interrupt):
        return runtime.interrupt(device_id=client_event.payload.device_id)
    if isinstance(client_event, TransferDevice):
        return runtime.transfer_device(
            from_device_id=client_event.payload.from_device_id,
            to_device_id=client_event.payload.to_device_id,
        )
    if isinstance(client_event, ModeChange):
        return runtime.set_mode(ListeningMode(client_event.payload.mode))
    raise HTTPException(status_code=409, detail="event requires another runtime module")


def _state_response(snapshot: RuntimeSnapshot) -> RuntimeStateResponse:
    return RuntimeStateResponse(
        phase=snapshot.phase.value,
        mode=snapshot.mode.value,
        session_id=snapshot.session_id,
        turn_id=snapshot.turn_id,
        active_device_id=snapshot.active_device_id,
        cancellation_generation=snapshot.cancellation_generation,
    )


def _state_event(
    snapshot: RuntimeSnapshot,
    *,
    session_id: UUID,
    turn_id: UUID,
    sequence: int,
) -> StateChanged:
    state = snapshot.phase.value
    if snapshot.phase.value == "idle" and snapshot.mode is not ListeningMode.NORMAL:
        state = snapshot.mode.value
    return StateChanged(
        version=1,
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        sequence=sequence,
        timestamp=datetime.now(UTC),
        type="state_changed",
        payload=StateChangedPayload(state=state, detail=snapshot.active_device_id),
    )


async def _stream_assistant(
    *,
    assistant: AssistantResponder | None,
    actions: ActionCoordinator | None,
    runtime: RuntimeCoordinator,
    client_event: SubmitText,
    emit: Callable[[Callable[[int], ServerEvent]], Awaitable[None]],
    history: ConversationHistory | None = None,
    speech: SpeechOutputSession | None = None,
    continuation: tuple[ChatMessage, ...] = (),
    tool_round: int = 0,
    hold_approval: (Callable[[UUID, SubmitText, tuple[ChatMessage, ...]], None] | None) = None,
) -> None:
    cancellation_generation = runtime.snapshot().cancellation_generation

    def cancelled() -> bool:
        snapshot = runtime.snapshot()
        return (
            snapshot.cancellation_generation != cancellation_generation
            or snapshot.turn_id != client_event.turn_id
        )

    async def emit_error(code: str, message: str, *, recoverable: bool) -> None:
        await emit(
            lambda sequence: _error_event(
                client_event,
                sequence=sequence,
                code=code,
                message=message,
                recoverable=recoverable,
            )
        )

    if assistant is None:
        await emit_error(
            "model_unavailable",
            "The local intelligence engine is not configured.",
            recoverable=True,
        )
        await _complete_active_turn(runtime, client_event, emit)
        return

    started_speaking = False
    assistant_text: list[str] = []
    speech_finished = False

    async def finish_speech() -> None:
        nonlocal speech_finished
        if speech is not None and not speech_finished:
            await speech.finish()
            speech_finished = True

    try:
        async for event in assistant.stream(
            client_event.payload.text,
            cancelled=cancelled,
            continuation=continuation,
        ):
            if isinstance(event, TextDelta):
                assistant_text.append(event.text)
                if not started_speaking:
                    snapshot = runtime.mark_speaking(device_id=client_event.payload.device_id)
                    await emit(
                        lambda sequence, current_snapshot=snapshot: _state_event(
                            current_snapshot,
                            session_id=client_event.session_id,
                            turn_id=client_event.turn_id,
                            sequence=sequence,
                        )
                    )
                    started_speaking = True
                await emit(
                    lambda sequence, text=event.text: _assistant_text_event(
                        client_event,
                        text=text,
                        is_final=False,
                        sequence=sequence,
                    )
                )
                if speech is not None:
                    await speech.push(event.text)
            elif isinstance(event, ToolProposal):
                if actions is None:
                    await emit_error(
                        "tool_loop_unavailable",
                        (
                            "I identified the required action, but that capability is not "
                            "connected yet."
                        ),
                        recoverable=True,
                    )
                    break
                if tool_round >= MAX_TOOL_ROUNDS:
                    await emit_error(
                        "tool_loop_limit",
                        "I stopped because that request exceeded the safe action loop limit.",
                        recoverable=True,
                    )
                    break
                coordinated = await actions.propose(
                    capability=event.call.name,
                    arguments=event.call.arguments,
                    device_id=client_event.payload.device_id,
                    requested_at=datetime.now(UTC),
                    direct_request=False,
                    source_event_id=client_event.event_id,
                )
                if coordinated.approval is not None:
                    approval_prompt = coordinated.approval
                    snapshot = runtime.mark_awaiting_approval(
                        device_id=client_event.payload.device_id
                    )
                    await emit(
                        lambda sequence, current_snapshot=snapshot: _state_event(
                            current_snapshot,
                            session_id=client_event.session_id,
                            turn_id=client_event.turn_id,
                            sequence=sequence,
                        )
                    )
                    await emit(
                        lambda sequence, prompt=approval_prompt: _approval_required_event(
                            client_event,
                            approval=prompt,
                            sequence=sequence,
                        )
                    )
                    if hold_approval is not None:
                        hold_approval(
                            approval_prompt.approval_id,
                            client_event,
                            continuation,
                        )
                    await finish_speech()
                    return
                snapshot = runtime.mark_acting(device_id=client_event.payload.device_id)
                await emit(
                    lambda sequence, current_snapshot=snapshot: _state_event(
                        current_snapshot,
                        session_id=client_event.session_id,
                        turn_id=client_event.turn_id,
                        sequence=sequence,
                    )
                )
                await emit(
                    lambda sequence, capability=event.call.name, result=coordinated.result: (
                        _capability_result_event(
                            client_event,
                            capability=capability,
                            result=result,
                            sequence=sequence,
                        )
                    )
                )
                resumed = runtime.resume_thinking(device_id=client_event.payload.device_id)
                await emit(
                    lambda sequence, current_snapshot=resumed: _state_event(
                        current_snapshot,
                        session_id=client_event.session_id,
                        turn_id=client_event.turn_id,
                        sequence=sequence,
                    )
                )
                await _stream_assistant(
                    assistant=assistant,
                    actions=actions,
                    runtime=runtime,
                    client_event=client_event,
                    emit=emit,
                    history=history,
                    speech=speech,
                    continuation=(
                        *continuation,
                        _tool_result_context(event.call.name, coordinated.result),
                    ),
                    tool_round=tool_round + 1,
                    hold_approval=hold_approval,
                )
                speech_finished = True
                return
            elif isinstance(event, TurnComplete):
                complete_text = "".join(assistant_text).strip()
                if history is not None and complete_text:
                    message_id = uuid4()
                    await asyncio.to_thread(
                        history.append,
                        ConversationMessage(
                            message_id=message_id,
                            source_event_id=message_id,
                            session_id=client_event.session_id,
                            turn_id=client_event.turn_id,
                            role=ConversationRole.ASSISTANT,
                            content=complete_text,
                            device_id=client_event.payload.device_id,
                            created_at=datetime.now(UTC),
                        ),
                    )
                if started_speaking:
                    await emit(
                        lambda sequence: _assistant_text_event(
                            client_event,
                            text="",
                            is_final=True,
                            sequence=sequence,
                        )
                    )
                await finish_speech()
        await _complete_active_turn(runtime, client_event, emit)
    except TurnCancelled:
        return
    except asyncio.CancelledError:
        raise
    except ResourcePressure as error:
        condition = "memory" if error.reason == "available_memory" else "temperature"
        await emit_error(
            "resource_pressure",
            (
                f"JARVIS is online, but current {condition} pressure makes loading the local "
                "model unsafe. I will not close your applications or force the model to load; "
                "try again when the pressure drops."
            ),
            recoverable=True,
        )
        await _complete_active_turn(runtime, client_event, emit)
    except (OSError, RuntimeError):
        await emit_error(
            "generation_failed",
            "The local model could not finish that response.",
            recoverable=True,
        )
        await _complete_active_turn(runtime, client_event, emit)
    finally:
        if speech is not None and not speech_finished:
            await speech.cancel()


async def _complete_active_turn(
    runtime: RuntimeCoordinator,
    client_event: SubmitText,
    emit: Callable[[Callable[[int], ServerEvent]], Awaitable[None]],
) -> None:
    snapshot = runtime.snapshot()
    if snapshot.turn_id != client_event.turn_id:
        return
    completed = runtime.complete_turn()
    await emit(
        lambda sequence: _state_event(
            completed,
            session_id=client_event.session_id,
            turn_id=client_event.turn_id,
            sequence=sequence,
        )
    )


def _transcript_event(client_event: SubmitText, *, sequence: int) -> Transcript:
    return Transcript(
        version=1,
        event_id=uuid4(),
        session_id=client_event.session_id,
        turn_id=client_event.turn_id,
        sequence=sequence,
        timestamp=datetime.now(UTC),
        type="transcript",
        payload=TranscriptPayload(
            text=client_event.payload.text,
            speaker="user",
            is_final=True,
            device_id=client_event.payload.device_id,
        ),
    )


def _ambient_transcript_event(
    *,
    text: str,
    occurred_at: datetime,
    event_id: UUID,
    session_id: UUID,
    turn_id: UUID,
    sequence: int,
) -> Transcript:
    return Transcript(
        version=1,
        event_id=event_id,
        session_id=session_id,
        turn_id=turn_id,
        sequence=sequence,
        timestamp=occurred_at,
        type="transcript",
        payload=TranscriptPayload(
            text=text,
            speaker="ambient",
            is_final=True,
            device_id="desktop",
        ),
    )


def _assistant_text_event(
    client_event: SubmitText,
    *,
    text: str,
    is_final: bool,
    sequence: int,
) -> AssistantText:
    return AssistantText(
        version=1,
        event_id=uuid4(),
        session_id=client_event.session_id,
        turn_id=client_event.turn_id,
        sequence=sequence,
        timestamp=datetime.now(UTC),
        type="assistant_text",
        payload=AssistantTextPayload(text=text, is_final=is_final),
    )


def _error_event(
    client_event: SubmitText,
    *,
    sequence: int,
    code: str,
    message: str,
    recoverable: bool,
) -> ErrorEvent:
    return ErrorEvent(
        version=1,
        event_id=uuid4(),
        session_id=client_event.session_id,
        turn_id=client_event.turn_id,
        sequence=sequence,
        timestamp=datetime.now(UTC),
        type="error",
        payload=ErrorPayload(code=code, message=message, recoverable=recoverable),
    )


def _approval_error_event(
    client_event: ApprovalDecision,
    *,
    sequence: int,
    code: str,
    message: str,
    recoverable: bool,
) -> ErrorEvent:
    return ErrorEvent(
        version=1,
        event_id=uuid4(),
        session_id=client_event.session_id,
        turn_id=client_event.turn_id,
        sequence=sequence,
        timestamp=datetime.now(UTC),
        type="error",
        payload=ErrorPayload(code=code, message=message, recoverable=recoverable),
    )


def _desktop_speech_error_event(
    *,
    session_id: UUID,
    turn_id: UUID,
    sequence: int,
) -> ErrorEvent:
    return ErrorEvent(
        version=1,
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        sequence=sequence,
        timestamp=datetime.now(UTC),
        type="error",
        payload=ErrorPayload(
            code="desktop_speech_failed",
            message="The desktop microphone pipeline needs attention.",
            recoverable=True,
        ),
    )


def _approval_required_event(
    client_event: SubmitText,
    *,
    approval: ApprovalPrompt,
    sequence: int,
) -> ApprovalRequired:
    return ApprovalRequired(
        version=1,
        event_id=uuid4(),
        session_id=client_event.session_id,
        turn_id=client_event.turn_id,
        sequence=sequence,
        timestamp=datetime.now(UTC),
        type="approval_required",
        payload=ApprovalRequiredPayload(
            approval_id=approval.approval_id,
            capability=approval.capability,
            summary=approval.summary,
            risk=approval.risk.value,
            expires_at=approval.expires_at,
        ),
    )


def _scheduled_approval_required_event(
    *,
    session_id: UUID,
    turn_id: UUID,
    approval: ApprovalPrompt,
    sequence: int,
) -> ApprovalRequired:
    return ApprovalRequired(
        version=1,
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        sequence=sequence,
        timestamp=datetime.now(UTC),
        type="approval_required",
        payload=ApprovalRequiredPayload(
            approval_id=approval.approval_id,
            capability=approval.capability,
            summary=approval.summary,
            risk=approval.risk.value,
            expires_at=approval.expires_at,
        ),
    )


def _capability_result_event(
    client_event: SubmitText | ApprovalDecision,
    *,
    capability: str,
    result: ExecutionResult,
    sequence: int,
) -> CapabilityResult:
    if result.status is ExecutionStatus.AWAITING_APPROVAL:
        raise ValueError("an awaiting result cannot be emitted as completed")
    message = _capability_result_message(result, fallback=f"{capability} completed.")
    return CapabilityResult(
        version=1,
        event_id=uuid4(),
        session_id=client_event.session_id,
        turn_id=client_event.turn_id,
        sequence=sequence,
        timestamp=datetime.now(UTC),
        type="capability_result",
        payload=CapabilityResultPayload(
            action_id=result.invocation_id,
            capability=capability,
            status=result.status.value,
            message=message,
            undo_available=bool(result.output and result.output.get("undo_reference")),
        ),
    )


def _scheduled_capability_result_event(
    *,
    session_id: UUID,
    turn_id: UUID,
    capability: str,
    result: ExecutionResult,
    sequence: int,
) -> CapabilityResult:
    if result.status is ExecutionStatus.AWAITING_APPROVAL:
        raise ValueError("an awaiting result cannot be emitted as completed")
    return CapabilityResult(
        version=1,
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        sequence=sequence,
        timestamp=datetime.now(UTC),
        type="capability_result",
        payload=CapabilityResultPayload(
            action_id=result.invocation_id,
            capability=capability,
            status=result.status.value,
            message=_capability_result_message(
                result,
                fallback=f"Scheduled {capability} completed.",
            ),
            undo_available=bool(result.output and result.output.get("undo_reference")),
        ),
    )


def _capability_result_message(result: ExecutionResult, *, fallback: str) -> str:
    if result.reason:
        return result.reason
    if result.output is not None:
        message = result.output.get("message")
        if isinstance(message, str) and 0 < len(message) <= 4_000:
            return message
    return fallback


def _tool_result_context(capability: str, result: ExecutionResult) -> ChatMessage:
    payload: dict[str, object] = {
        "capability": capability,
        "invocation_id": str(result.invocation_id),
        "status": result.status.value,
        "reason": result.reason,
        "output": result.output,
    }
    content = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(content) > 30_000:
        payload["output"] = None
        payload["output_note"] = "omitted because the capability output exceeded 30000 characters"
        content = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    return ChatMessage(role="tool", content=content)
