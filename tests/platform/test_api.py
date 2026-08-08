import asyncio
import base64
import json
import queue
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from pydantic import JsonValue
from starlette.websockets import WebSocketDisconnect

from jarvis.agency.capabilities import (
    ApprovalPrompt,
    CoordinatedExecution,
    ExecutionResult,
    ExecutionStatus,
)
from jarvis.agency.policy import ApprovalChoice, RiskClass
from jarvis.memory.history import ConversationHistoryRepository
from jarvis.memory.store import MemoryCandidate, MemoryRepository
from jarvis.platform.api import ApiSettings, create_app
from jarvis.platform.models import ChatMessage, ToolCall
from jarvis.platform.pairing import InMemoryPairingStore, PhonePairing, public_key_to_jwk
from jarvis.platform.sqlite import SQLiteStore
from jarvis.runtime.assistant import TextDelta, ToolProposal, TurnComplete
from jarvis.runtime.conversation import ListeningMode, RuntimeCoordinator
from jarvis.speech.desktop import DesktopSpeechEvent, DesktopTranscript, DesktopWake
from jarvis.speech.output import SpeechOutputSession

TOKEN = "test-token-that-is-at-least-thirty-two-characters"
DESKTOP_TOKEN = "ephemeral-desktop-token-that-is-at-least-thirty-two-characters"
SESSION_ID = "019fd977-1d96-7892-950c-6afbb71f7cf0"
TURN_ID = "019fd977-1d96-7892-950c-6afbb71f7cf1"


class FakeAssistant:
    async def stream(
        self,
        user_text: str,
        *,
        cancelled: object,
        context: tuple[object, ...] = (),
        continuation: tuple[object, ...] = (),
    ):
        assert user_text == "What am I looking at?"
        yield TextDelta(text="You are looking ")
        yield TextDelta(text="at the JARVIS project.")
        yield TurnComplete(tokens_per_second=25)


class FakeSpeechInput:
    def __init__(self) -> None:
        self.frames: list[tuple[str, bytes]] = []

    async def ingest(self, device_id: str, pcm: bytes) -> str | None:
        self.frames.append((device_id, pcm))
        return "What am I looking at?"

    async def reset(self, device_id: str) -> None:
        return None


class FakeDesktopSpeech:
    def __init__(self) -> None:
        self.events: queue.Queue[DesktopSpeechEvent] = queue.Queue()
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def next_event(self) -> DesktopSpeechEvent:
        while True:
            try:
                return await asyncio.to_thread(self.events.get, True, 0.05)
            except queue.Empty:
                await asyncio.sleep(0)

    async def set_private(self, enabled: bool) -> None:
        return None

    def push(self, event: DesktopSpeechEvent) -> None:
        self.events.put(event)


APPROVAL_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cfa")
ACTION_ID = UUID("019fd977-1d96-7892-950c-6afbb71f7cfb")


class FakeToolAssistant:
    async def stream(
        self,
        user_text: str,
        *,
        cancelled: object,
        context: tuple[object, ...] = (),
        continuation: tuple[object, ...] = (),
    ):
        if continuation:
            yield TextDelta(text="Done. The prepared message was sent.")
            yield TurnComplete(tokens_per_second=24)
            return
        yield ToolProposal(
            call=ToolCall(
                name="messages.send",
                arguments={"recipient": "approved@example.test", "body": user_text},
            )
        )


class FakeObserveAssistant:
    def __init__(self) -> None:
        self.continuations: list[tuple[ChatMessage, ...]] = []

    async def stream(
        self,
        user_text: str,
        *,
        cancelled: object,
        context: tuple[ChatMessage, ...] = (),
        continuation: tuple[ChatMessage, ...] = (),
    ):
        self.continuations.append(continuation)
        if not continuation:
            yield ToolProposal(call=ToolCall(name="context.active_window", arguments={}))
            return
        yield TextDelta(text="You are looking at the JARVIS project.")
        yield TurnComplete(tokens_per_second=24)


class FakeActions:
    def pending_capability(self, approval_id: UUID) -> str | None:
        return "messages.send" if approval_id == APPROVAL_ID else None

    async def propose(
        self,
        *,
        capability: str,
        arguments: dict[str, JsonValue],
        device_id: str,
        requested_at: datetime,
        direct_request: bool,
    ) -> CoordinatedExecution:
        assert capability == "messages.send"
        assert device_id == "desktop"
        assert direct_request is False
        return CoordinatedExecution(
            result=ExecutionResult(
                invocation_id=ACTION_ID,
                status=ExecutionStatus.AWAITING_APPROVAL,
                approval_id=APPROVAL_ID,
            ),
            approval=ApprovalPrompt(
                approval_id=APPROVAL_ID,
                capability="messages.send",
                summary="Send the prepared message",
                risk=RiskClass.EXTERNAL_IRREVERSIBLE,
                expires_at=requested_at + timedelta(minutes=5),
            ),
        )

    async def decide(
        self,
        *,
        approval_id: UUID,
        choice: ApprovalChoice,
        device_id: str,
        now: datetime,
    ) -> ExecutionResult:
        assert approval_id == APPROVAL_ID
        assert choice is ApprovalChoice.APPROVE
        assert device_id == "desktop"
        return ExecutionResult(
            invocation_id=ACTION_ID,
            status=ExecutionStatus.SUCCEEDED,
            output={"sent": True},
        )


class FakeObserveActions(FakeActions):
    def pending_capability(self, approval_id: UUID) -> str | None:
        return None

    async def propose(
        self,
        *,
        capability: str,
        arguments: dict[str, JsonValue],
        device_id: str,
        requested_at: datetime,
        direct_request: bool,
    ) -> CoordinatedExecution:
        assert capability == "context.active_window"
        return CoordinatedExecution(
            result=ExecutionResult(
                invocation_id=ACTION_ID,
                status=ExecutionStatus.SUCCEEDED,
                output={"title": "JARVIS", "process_name": "Code.exe"},
            )
        )


class FakeSpeechSession:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.finished = False
        self.cancelled = False

    async def push(self, text: str) -> None:
        self.text.append(text)

    async def finish(self) -> None:
        self.finished = True

    async def cancel(self) -> None:
        self.cancelled = True


class FakeSpeechOutput:
    def __init__(self) -> None:
        self.sessions: list[FakeSpeechSession] = []

    def open(self, *, device_id: str, send_phone_pcm: object) -> SpeechOutputSession:
        assert device_id == "desktop"
        session = FakeSpeechSession()
        self.sessions.append(session)
        return session


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    ui_directory = tmp_path / "ui"
    ui_directory.mkdir()
    (ui_directory / "index.html").write_text("<title>JARVIS phone</title>", encoding="utf-8")
    app = create_app(
        runtime=RuntimeCoordinator(),
        settings=ApiSettings(
            bearer_token=TOKEN,
            desktop_session_token=DESKTOP_TOKEN,
            ui_directory=ui_directory,
            allowed_hosts=("testserver", "127.0.0.1"),
            allowed_origins=("http://127.0.0.1:1420",),
        ),
        phone_pairing=PhonePairing(InMemoryPairingStore()),
    )
    return TestClient(app)


def test_health_is_minimal_public_and_has_security_headers(client: TestClient) -> None:
    response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "protocol_version": 1}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert TOKEN not in response.text


def test_phone_shell_is_served_locally_with_microphone_permission(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "JARVIS phone" in response.text
    assert (
        response.headers["permissions-policy"]
        == "camera=(), microphone=(self), geolocation=(), payment=()"
    )


def test_runtime_state_defaults_to_deny_without_valid_bearer(client: TestClient) -> None:
    missing = client.get("/v1/state")
    wrong = client.get("/v1/state", headers={"authorization": "Bearer wrong"})
    valid = client.get("/v1/state", headers={"authorization": f"Bearer {TOKEN}"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert valid.status_code == 200
    assert valid.json()["phase"] == "idle"


def test_ephemeral_desktop_token_authenticates_http_and_browser_websocket(
    client: TestClient,
) -> None:
    state = client.get(
        "/v1/state",
        headers={"authorization": f"Bearer {DESKTOP_TOKEN}"},
    )

    assert state.status_code == 200
    with client.websocket_connect(
        "/v1/live",
        headers={"origin": "http://127.0.0.1:1420"},
        subprotocols=["jarvis.v1", f"jarvis.desktop.{DESKTOP_TOKEN}"],
    ) as socket:
        assert socket.accepted_subprotocol == "jarvis.v1"
        assert socket.receive_json()["type"] == "state_changed"


def test_websocket_rejects_missing_auth_and_untrusted_origin(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as missing,
        client.websocket_connect(
            "/v1/live",
            headers={"origin": "http://127.0.0.1:1420"},
        ),
    ):
        pass
    with (
        pytest.raises(WebSocketDisconnect) as bad_origin,
        client.websocket_connect(
            "/v1/live",
            headers={
                "authorization": f"Bearer {TOKEN}",
                "origin": "https://attacker.example",
            },
        ),
    ):
        pass

    assert missing.value.code == 1008
    assert bad_origin.value.code == 1008


def test_websocket_dispatches_typed_events_and_returns_authoritative_state(
    client: TestClient,
) -> None:
    with client.websocket_connect(
        "/v1/live",
        headers={
            "authorization": f"Bearer {TOKEN}",
            "origin": "http://127.0.0.1:1420",
        },
    ) as socket:
        initial = socket.receive_json()
        socket.send_json(
            {
                "version": 1,
                "event_id": "019fd977-1d96-7892-950c-6afbb71f7cf2",
                "session_id": SESSION_ID,
                "turn_id": TURN_ID,
                "sequence": 0,
                "timestamp": "2026-08-07T18:30:00Z",
                "type": "activate",
                "payload": {"device_id": "desktop", "source": "wake_word"},
            }
        )
        activated = socket.receive_json()

    assert initial["type"] == "state_changed"
    assert initial["payload"]["state"] == "idle"
    assert activated["type"] == "state_changed"
    assert activated["payload"]["state"] == "listening"
    assert activated["session_id"] == SESSION_ID
    assert activated["turn_id"] == TURN_ID
    assert UUID(activated["event_id"])


def test_websocket_rejects_oversized_text_frames(client: TestClient) -> None:
    with client.websocket_connect(
        "/v1/live",
        headers={
            "authorization": f"Bearer {TOKEN}",
            "origin": "http://127.0.0.1:1420",
        },
    ) as socket:
        socket.receive_json()
        socket.send_text("x" * 70_000)
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()

    assert closed.value.code == 1009


def test_phone_pairing_endpoints_mint_browser_websocket_session(client: TestClient) -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    offer_response = client.post(
        "/v1/pairing/offers",
        headers={"authorization": f"Bearer {TOKEN}"},
    )
    offer = offer_response.json()
    paired = client.post(
        f"/v1/pairing/{offer['pairing_id']}/complete",
        json={
            "secret": offer["secret"],
            "device_id": "yuvraj-iphone",
            "public_key_jwk": public_key_to_jwk(private_key.public_key()),
        },
    )
    challenge_response = client.post(
        "/v1/auth/challenges",
        json={"device_id": "yuvraj-iphone"},
    )
    challenge = challenge_response.json()
    challenge_bytes = base64.urlsafe_b64decode(challenge["challenge"] + "==")
    signature = private_key.sign(challenge_bytes, ec.ECDSA(hashes.SHA256()))

    session_response = client.post(
        "/v1/auth/sessions",
        json={
            "challenge_id": challenge["challenge_id"],
            "signature": base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
        },
    )
    session = session_response.json()

    assert offer_response.status_code == 201
    assert paired.status_code == 201
    assert challenge_response.status_code == 201
    assert session_response.status_code == 201
    assert session["expires_at"] > datetime.now(UTC).isoformat()
    with client.websocket_connect(
        "/v1/live",
        headers={"origin": "http://127.0.0.1:1420"},
        subprotocols=["jarvis.v1", f"jarvis.session.{session['token']}"],
    ) as socket:
        assert socket.receive_json()["type"] == "state_changed"


def test_pairing_offer_places_secret_only_in_a_tailscale_fragment() -> None:
    app = create_app(
        runtime=RuntimeCoordinator(),
        settings=ApiSettings(
            bearer_token=TOKEN,
            phone_base_url="https://yuvraj-omen.example.ts.net",
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:1420",),
        ),
        phone_pairing=PhonePairing(InMemoryPairingStore()),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/pairing/offers",
            headers={"authorization": f"Bearer {TOKEN}"},
        )
        offer = response.json()

    parsed = urlsplit(offer["pairing_url"])
    encoded = parse_qs(parsed.fragment)["pair"][0]
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=="))
    assert parsed.scheme == "https"
    assert parsed.query == ""
    assert payload == {
        "pairingId": offer["pairing_id"],
        "secret": offer["secret"],
    }


def test_text_turn_streams_transcript_and_assistant_without_blocking_protocol(
    tmp_path: Path,
) -> None:
    speech_output = FakeSpeechOutput()
    app = create_app(
        runtime=RuntimeCoordinator(),
        assistant=FakeAssistant(),
        speech_output=speech_output,
        settings=ApiSettings(
            bearer_token=TOKEN,
            desktop_session_token=DESKTOP_TOKEN,
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:1420",),
        ),
        phone_pairing=PhonePairing(InMemoryPairingStore()),
    )
    with TestClient(app).websocket_connect(
        "/v1/live",
        headers={
            "authorization": f"Bearer {TOKEN}",
            "origin": "http://127.0.0.1:1420",
        },
    ) as socket:
        socket.receive_json()
        socket.send_json(
            client_event("activate", {"device_id": "desktop", "source": "keyboard"}, 0)
        )
        socket.receive_json()
        socket.send_json(
            client_event(
                "submit_text",
                {"device_id": "desktop", "text": "What am I looking at?"},
                1,
            )
        )
        streamed = [socket.receive_json() for _ in range(7)]

    assert [event["type"] for event in streamed] == [
        "state_changed",
        "transcript",
        "state_changed",
        "assistant_text",
        "assistant_text",
        "assistant_text",
        "state_changed",
    ]
    assert streamed[0]["payload"]["state"] == "thinking"
    assert streamed[1]["payload"]["speaker"] == "user"
    assert streamed[-1]["payload"]["state"] == "idle"
    assert speech_output.sessions[0].text == [
        "You are looking ",
        "at the JARVIS project.",
    ]
    assert speech_output.sessions[0].finished is True
    assert speech_output.sessions[0].cancelled is False


def test_intentional_turn_is_persisted_and_exposed_only_to_authenticated_clients(
    tmp_path: Path,
) -> None:
    database = SQLiteStore(tmp_path / "jarvis.db")
    database.initialize()
    history = ConversationHistoryRepository(database)
    app = create_app(
        runtime=RuntimeCoordinator(),
        assistant=FakeAssistant(),
        history=history,
        settings=ApiSettings(
            bearer_token=TOKEN,
            desktop_session_token=DESKTOP_TOKEN,
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:1420",),
        ),
        phone_pairing=PhonePairing(InMemoryPairingStore()),
    )
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/live",
            headers={"origin": "http://127.0.0.1:1420"},
            subprotocols=["jarvis.v1", f"jarvis.desktop.{DESKTOP_TOKEN}"],
        ) as socket:
            socket.receive_json()
            socket.send_json(client_event("activate", {"device_id": "desktop", "source": "ui"}, 0))
            socket.receive_json()
            socket.send_json(
                client_event(
                    "submit_text",
                    {"device_id": "desktop", "text": "What am I looking at?"},
                    1,
                )
            )
            for _ in range(7):
                socket.receive_json()

        unauthorized = client.get("/v1/history")
        response = client.get(
            "/v1/history?limit=10",
            headers={"authorization": f"Bearer {DESKTOP_TOKEN}"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    persisted = response.json()["messages"]
    assert [(item["role"], item["content"]) for item in persisted] == [
        ("user", "What am I looking at?"),
        ("assistant", "You are looking at the JARVIS project."),
    ]
    assert all(item["source_event_id"] for item in persisted)


def test_private_mode_does_not_persist_the_turn(tmp_path: Path) -> None:
    database = SQLiteStore(tmp_path / "jarvis.db")
    database.initialize()
    history = ConversationHistoryRepository(database)
    runtime = RuntimeCoordinator()
    runtime.set_mode(ListeningMode.PRIVATE)
    app = create_app(
        runtime=runtime,
        assistant=FakeAssistant(),
        history=history,
        settings=ApiSettings(
            bearer_token=TOKEN,
            desktop_session_token=DESKTOP_TOKEN,
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:1420",),
        ),
        phone_pairing=PhonePairing(InMemoryPairingStore()),
    )
    with TestClient(app).websocket_connect(
        "/v1/live",
        headers={"origin": "http://127.0.0.1:1420"},
        subprotocols=["jarvis.v1", f"jarvis.desktop.{DESKTOP_TOKEN}"],
    ) as socket:
        socket.receive_json()
        socket.send_json(client_event("activate", {"device_id": "desktop", "source": "ui"}, 0))
        socket.receive_json()
        socket.send_json(
            client_event(
                "submit_text",
                {"device_id": "desktop", "text": "What am I looking at?"},
                1,
            )
        )
        for _ in range(7):
            socket.receive_json()

    assert history.recent(limit=10) == []


def test_memory_can_be_inspected_corrected_and_forgotten_through_authenticated_admin(
    tmp_path: Path,
) -> None:
    database = SQLiteStore(tmp_path / "jarvis.db")
    database.initialize()
    memory = MemoryRepository(sqlite=database, markdown_directory=tmp_path / "Memory")
    memory.initialize()
    mutation = memory.remember(
        MemoryCandidate(
            category="hardware",
            subject="phone",
            content="Yuvraj uses an iPhone 16 Pro.",
            source_event_ids=(UUID(SESSION_ID),),
            observed_at=datetime(2026, 8, 7, 18, 30, tzinfo=UTC),
        )
    )
    app = create_app(
        runtime=RuntimeCoordinator(),
        memory=memory,
        settings=ApiSettings(
            bearer_token=TOKEN,
            desktop_session_token=DESKTOP_TOKEN,
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:1420",),
        ),
        phone_pairing=PhonePairing(InMemoryPairingStore()),
    )
    authorization = {"authorization": f"Bearer {DESKTOP_TOKEN}"}
    with TestClient(app) as client:
        unauthorized = client.get("/v1/memory/search?query=iPhone")
        found = client.get("/v1/memory/search?query=iPhone", headers=authorization)
        corrected = client.patch(
            f"/v1/memory/{mutation.fact_id}",
            headers=authorization,
            json={"content": "Yuvraj uses an iPhone 17 Pro."},
        )
        forgotten = client.delete(
            f"/v1/memory/{mutation.fact_id}",
            headers=authorization,
        )
        after = client.get("/v1/memory/search?query=iPhone", headers=authorization)

    assert unauthorized.status_code == 401
    assert found.json()["facts"][0]["content"] == "Yuvraj uses an iPhone 16 Pro."
    assert corrected.json()["content"] == "Yuvraj uses an iPhone 17 Pro."
    assert forgotten.status_code == 204
    assert after.json() == {"facts": []}


def test_binary_phone_pcm_enters_the_same_transcribed_turn_pipeline() -> None:
    speech = FakeSpeechInput()
    app = create_app(
        runtime=RuntimeCoordinator(),
        assistant=FakeAssistant(),
        speech_input=speech,
        settings=ApiSettings(
            bearer_token=TOKEN,
            desktop_session_token=DESKTOP_TOKEN,
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:1420",),
        ),
        phone_pairing=PhonePairing(InMemoryPairingStore()),
    )
    with TestClient(app).websocket_connect(
        "/v1/live",
        headers={"origin": "http://127.0.0.1:1420"},
        subprotocols=["jarvis.v1", f"jarvis.desktop.{DESKTOP_TOKEN}"],
    ) as socket:
        socket.receive_json()
        socket.send_json(client_event("activate", {"device_id": "desktop", "source": "ui"}, 0))
        socket.receive_json()
        socket.send_bytes(b"\x01\x00" * 512)
        streamed = [socket.receive_json() for _ in range(7)]

    assert speech.frames == [("desktop", b"\x01\x00" * 512)]
    assert streamed[1]["type"] == "transcript"
    assert streamed[1]["payload"]["text"] == "What am I looking at?"


def test_tool_proposal_waits_for_exact_approval_before_reporting_success() -> None:
    app = create_app(
        runtime=RuntimeCoordinator(),
        assistant=FakeToolAssistant(),
        actions=FakeActions(),
        settings=ApiSettings(
            bearer_token=TOKEN,
            desktop_session_token=DESKTOP_TOKEN,
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:1420",),
        ),
        phone_pairing=PhonePairing(InMemoryPairingStore()),
    )
    with TestClient(app).websocket_connect(
        "/v1/live",
        headers={"origin": "http://127.0.0.1:1420"},
        subprotocols=["jarvis.v1", f"jarvis.desktop.{DESKTOP_TOKEN}"],
    ) as socket:
        socket.receive_json()
        socket.send_json(client_event("activate", {"device_id": "desktop", "source": "ui"}, 0))
        socket.receive_json()
        socket.send_json(
            client_event(
                "submit_text",
                {"device_id": "desktop", "text": "Send the prepared message"},
                1,
            )
        )
        proposed = [socket.receive_json() for _ in range(4)]
        socket.send_json(
            client_event(
                "approval_decision",
                {
                    "device_id": "desktop",
                    "approval_id": str(APPROVAL_ID),
                    "decision": "approve",
                },
                2,
            )
        )
        completed = [socket.receive_json() for _ in range(7)]

    assert [event["type"] for event in proposed] == [
        "state_changed",
        "transcript",
        "state_changed",
        "approval_required",
    ]
    assert proposed[-1]["payload"]["approval_id"] == str(APPROVAL_ID)
    assert [event["type"] for event in completed] == [
        "state_changed",
        "capability_result",
        "state_changed",
        "state_changed",
        "assistant_text",
        "assistant_text",
        "state_changed",
    ]
    assert completed[1]["payload"]["status"] == "succeeded"
    assert completed[4]["payload"]["text"] == "Done. The prepared message was sent."


def test_observation_result_returns_to_the_model_for_a_grounded_answer() -> None:
    assistant = FakeObserveAssistant()
    app = create_app(
        runtime=RuntimeCoordinator(),
        assistant=assistant,
        actions=FakeObserveActions(),
        settings=ApiSettings(
            bearer_token=TOKEN,
            desktop_session_token=DESKTOP_TOKEN,
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:1420",),
        ),
        phone_pairing=PhonePairing(InMemoryPairingStore()),
    )
    with TestClient(app).websocket_connect(
        "/v1/live",
        headers={"origin": "http://127.0.0.1:1420"},
        subprotocols=["jarvis.v1", f"jarvis.desktop.{DESKTOP_TOKEN}"],
    ) as socket:
        socket.receive_json()
        socket.send_json(client_event("activate", {"device_id": "desktop", "source": "ui"}, 0))
        socket.receive_json()
        socket.send_json(
            client_event(
                "submit_text",
                {"device_id": "desktop", "text": "What am I looking at?"},
                1,
            )
        )
        streamed = [socket.receive_json() for _ in range(9)]

    assert [event["type"] for event in streamed] == [
        "state_changed",
        "transcript",
        "state_changed",
        "capability_result",
        "state_changed",
        "state_changed",
        "assistant_text",
        "assistant_text",
        "state_changed",
    ]
    assert assistant.continuations[0] == ()
    tool_context = assistant.continuations[1][0]
    assert tool_context.role == "tool"
    assert '"title":"JARVIS"' in tool_context.content


def test_unknown_approval_never_enters_acting_state() -> None:
    app = create_app(
        runtime=RuntimeCoordinator(),
        assistant=FakeToolAssistant(),
        actions=FakeActions(),
        settings=ApiSettings(
            bearer_token=TOKEN,
            desktop_session_token=DESKTOP_TOKEN,
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:1420",),
        ),
        phone_pairing=PhonePairing(InMemoryPairingStore()),
    )
    with TestClient(app).websocket_connect(
        "/v1/live",
        headers={"origin": "http://127.0.0.1:1420"},
        subprotocols=["jarvis.v1", f"jarvis.desktop.{DESKTOP_TOKEN}"],
    ) as socket:
        socket.receive_json()
        socket.send_json(client_event("activate", {"device_id": "desktop", "source": "ui"}, 0))
        socket.receive_json()
        socket.send_json(
            client_event(
                "submit_text",
                {"device_id": "desktop", "text": "Send the prepared message"},
                1,
            )
        )
        for _ in range(4):
            socket.receive_json()
        socket.send_json(
            client_event(
                "approval_decision",
                {
                    "device_id": "desktop",
                    "approval_id": "019fd977-1d96-7892-950c-6afbb71f7cff",
                    "decision": "approve",
                },
                2,
            )
        )
        response = socket.receive_json()

    assert response["type"] == "error"
    assert response["payload"]["code"] == "approval_invalid"


def test_background_desktop_wake_opens_a_turn_and_transcribes_without_ui_input() -> None:
    speech = FakeDesktopSpeech()
    app = create_app(
        runtime=RuntimeCoordinator(),
        assistant=FakeAssistant(),
        desktop_speech=speech,
        settings=ApiSettings(
            bearer_token=TOKEN,
            desktop_session_token=DESKTOP_TOKEN,
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:1420",),
        ),
        phone_pairing=PhonePairing(InMemoryPairingStore()),
    )
    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/v1/live",
            headers={"origin": "http://127.0.0.1:1420"},
            subprotocols=["jarvis.v1", f"jarvis.desktop.{DESKTOP_TOKEN}"],
        ) as socket,
    ):
        socket.receive_json()
        speech.push(DesktopWake(occurred_at=datetime.now(UTC)))
        activated = socket.receive_json()
        speech.push(
            DesktopTranscript(
                text="What am I looking at?",
                occurred_at=datetime.now(UTC),
            )
        )
        streamed = [socket.receive_json() for _ in range(7)]

    assert speech.started is True
    assert speech.stopped is True
    assert activated["payload"]["state"] == "listening"
    assert streamed[1]["type"] == "transcript"


def client_event(event_type: str, payload: dict[str, object], sequence: int) -> dict[str, object]:
    return {
        "version": 1,
        "event_id": f"019fd977-1d96-7892-950c-6afbb71f7c{sequence + 20:02x}",
        "session_id": SESSION_ID,
        "turn_id": TURN_ID,
        "sequence": sequence,
        "timestamp": "2026-08-07T18:30:00Z",
        "type": event_type,
        "payload": payload,
    }
