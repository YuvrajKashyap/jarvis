import json
import os
import secrets as secret_tokens
import threading
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

import keyring
import uvicorn
from fastapi import FastAPI
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from jarvis.agency.capabilities import (
    CapabilityRegistry,
    InvocationCoordinator,
    InvocationEngine,
)
from jarvis.agency.files import ReadTextCapability, UndoFileCapability, WriteTextCapability
from jarvis.agency.observation import ActiveWindowCapability, SystemHealthCapability
from jarvis.agency.policy import PolicyEngine
from jarvis.memory.history import ConversationHistoryRepository
from jarvis.memory.store import MemoryRepository
from jarvis.perception.context import PerceptionCoordinator
from jarvis.platform.api import ApiSettings
from jarvis.platform.api import create_app as create_transport
from jarvis.platform.filesystem import LocalFileStore
from jarvis.platform.memory_context import LocalMemoryContext
from jarvis.platform.ollama import OllamaProvider
from jarvis.platform.pairing import PhonePairing
from jarvis.platform.pairing_sqlite import SQLitePairingStore
from jarvis.platform.resources import WindowsResourceProbe
from jarvis.platform.speech import (
    FasterWhisperTranscriber,
    OpenWakeWordDetector,
    SileroVad,
    SoundDeviceMicrophone,
)
from jarvis.platform.sqlite import SQLiteApprovalStore, SQLiteStore
from jarvis.platform.voice import ChatterboxTurboSynthesizer, SoundDeviceSpeaker
from jarvis.platform.windows import WindowsPerception
from jarvis.runtime.assistant import AssistantSettings, AssistantTurn
from jarvis.runtime.conversation import RuntimeCoordinator
from jarvis.runtime.resources import ResourceGovernor, ResourceLimits
from jarvis.speech.audio import AudioFormat, AudioRingBuffer
from jarvis.speech.desktop import DesktopSpeechService
from jarvis.speech.engine import SpeechCoordinator, SpeechSettings
from jarvis.speech.output import StreamingSpeechOutput
from jarvis.speech.remote import RemoteSpeechInput, RemoteSpeechSettings

SERVICE_NAME = "dev.yuvraj.jarvis"


class _PlaybackCancellation:
    def __init__(self, speaker: SoundDeviceSpeaker | None = None) -> None:
        self._speaker = speaker

    def cancel(self) -> None:
        if self._speaker is not None:
            self._speaker.cancel_now()


def _default_data_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base is None:
        base = str(Path.home() / ".local" / "share")
    return Path(base) / "JARVIS"


def _default_memory_directory() -> Path:
    return Path.home() / "Documents" / "JARVIS" / "Memory"


def _default_file_roots() -> tuple[Path, ...]:
    home = Path.home()
    candidates = (home / "dev", home / "Documents", home / "Downloads")
    return tuple(path for path in candidates if path.is_dir())


def _default_phone_base_url() -> str | None:
    config_path = _default_data_directory() / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(config, dict):
        return None
    value = config.get("phone_base_url")
    return value if isinstance(value, str) else None


def _default_whisper_model() -> str:
    managed = _default_data_directory() / "models" / "faster-whisper" / "distil-small.en"
    return str(managed) if (managed / "model.bin").is_file() else "distil-small.en"


def _default_ui_directory() -> Path | None:
    candidates = (
        Path(__file__).with_name("ui_dist"),
        Path(__file__).resolve().parents[2] / "ui" / "dist",
    )
    return next((candidate for candidate in candidates if candidate.is_dir()), None)


class BootstrapSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="forbid",
        frozen=True,
        env_prefix="JARVIS_",
    )

    data_directory: Path = Field(default_factory=_default_data_directory)
    memory_directory: Path = Field(default_factory=_default_memory_directory)
    file_roots: tuple[Path, ...] = Field(default_factory=_default_file_roots, min_length=1)
    ui_directory: Path | None = Field(default_factory=_default_ui_directory)
    host: str = "127.0.0.1"
    port: int = Field(default=7331, ge=1024, le=65_535)
    desktop_session_token: str | None = Field(default=None, min_length=32, max_length=512)
    primary_model: str = Field(default="qwen3.5:4b-q8_0", min_length=1, max_length=160)
    model_context_length: int = Field(default=4_096, ge=512, le=8_192)
    whisper_model: str = Field(default_factory=_default_whisper_model, min_length=1, max_length=512)
    desktop_speech_enabled: bool = True
    voice_reference_path: Path | None = None
    voice_device: Literal["cpu", "cuda"] = "cuda"
    phone_base_url: str | None = Field(default_factory=_default_phone_base_url)
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "https://tauri.localhost",
    )


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...


class InMemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = threading.RLock()

    def get(self, name: str) -> str | None:
        with self._lock:
            return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        with self._lock:
            self._values[name] = value


class WindowsCredentialStore:
    def get(self, name: str) -> str | None:
        return keyring.get_password(SERVICE_NAME, name)

    def set(self, name: str, value: str) -> None:
        keyring.set_password(SERVICE_NAME, name, value)


def build_application(
    *,
    settings: BootstrapSettings,
    secrets: SecretStore,
) -> FastAPI:
    settings.data_directory.mkdir(parents=True, exist_ok=True)
    token = secrets.get("api-token")
    if token is None:
        token = secret_tokens.token_urlsafe(48)
        secrets.set("api-token", token)

    sqlite = SQLiteStore(settings.data_directory / "jarvis.db")
    sqlite.initialize()
    memory = MemoryRepository(
        sqlite=sqlite,
        markdown_directory=settings.memory_directory,
    )
    memory.initialize()
    history = ConversationHistoryRepository(sqlite)
    runtime = RuntimeCoordinator()
    perception = PerceptionCoordinator(WindowsPerception())
    capabilities = CapabilityRegistry()
    capabilities.register(ActiveWindowCapability(perception))
    capabilities.register(SystemHealthCapability(perception))
    files = LocalFileStore(
        roots=settings.file_roots,
        undo_directory=settings.data_directory / "undo" / "files",
    )
    capabilities.register(ReadTextCapability(files))
    capabilities.register(WriteTextCapability(files))
    capabilities.register(UndoFileCapability(files))
    policy = PolicyEngine(SQLiteApprovalStore(sqlite))
    action_engine = InvocationEngine(registry=capabilities, policy=policy, audit=sqlite)
    actions = InvocationCoordinator(engine=action_engine, policy=policy)
    model = OllamaProvider()
    resources = ResourceGovernor(
        models=model,
        probe=WindowsResourceProbe(),
        limits=ResourceLimits(),
    )
    assistant = AssistantTurn(
        model=model,
        settings=AssistantSettings(
            primary_model=settings.primary_model,
            context_length=settings.model_context_length,
        ),
        tools=capabilities.tool_schemas(),
        readiness=resources,
        context_provider=LocalMemoryContext(history=history, memory=memory),
    )
    transcriber = FasterWhisperTranscriber(model_name=settings.whisper_model)
    speech_input = RemoteSpeechInput(
        vad=SileroVad(),
        transcriber=transcriber,
        settings=RemoteSpeechSettings(),
    )
    speaker = SoundDeviceSpeaker() if settings.voice_reference_path is not None else None
    speech_output = (
        None
        if settings.voice_reference_path is None or speaker is None
        else StreamingSpeechOutput(
            synthesizer=ChatterboxTurboSynthesizer(
                reference_path=settings.voice_reference_path,
                device=settings.voice_device,
            ),
            desktop_sink=speaker,
        )
    )
    desktop_speech = None
    if settings.desktop_speech_enabled:
        desktop_speech = DesktopSpeechService(
            microphone=SoundDeviceMicrophone(),
            coordinator=SpeechCoordinator(
                buffer=AudioRingBuffer(
                    audio_format=AudioFormat(
                        sample_rate=16_000,
                        channels=1,
                        sample_width_bytes=2,
                    ),
                    duration_seconds=120,
                ),
                wake_word=OpenWakeWordDetector(
                    model_path=(
                        settings.data_directory / "models" / "openwakeword" / "hey_jarvis_v0.1.onnx"
                    )
                ),
                vad=SileroVad(),
                playback=_PlaybackCancellation(speaker),
                settings=SpeechSettings(
                    frame_duration_ms=32,
                    end_of_speech_silence_ms=640,
                ),
            ),
            transcriber=transcriber,
        )
    phone_pairing = PhonePairing(SQLitePairingStore(sqlite))
    allowed_hosts = settings.allowed_hosts
    allowed_origins = settings.allowed_origins
    if settings.phone_base_url is not None:
        phone_host = urlsplit(settings.phone_base_url).hostname
        if phone_host is not None:
            allowed_hosts = tuple(dict.fromkeys((*allowed_hosts, phone_host)))
        allowed_origins = tuple(
            dict.fromkeys((*allowed_origins, settings.phone_base_url.rstrip("/")))
        )
    application = create_transport(
        runtime=runtime,
        settings=ApiSettings(
            bearer_token=token,
            desktop_session_token=settings.desktop_session_token,
            ui_directory=settings.ui_directory,
            phone_base_url=settings.phone_base_url,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        ),
        phone_pairing=phone_pairing,
        assistant=assistant,
        speech_input=speech_input,
        actions=actions,
        desktop_speech=desktop_speech,
        history=history,
        speech_output=speech_output,
        memory=memory,
    )
    application.state.runtime = runtime
    application.state.sqlite = sqlite
    application.state.memory = memory
    application.state.history = history
    application.state.perception = perception
    application.state.capabilities = capabilities
    application.state.files = files
    application.state.policy = policy
    application.state.actions = actions
    application.state.phone_pairing = phone_pairing
    application.state.model = model
    application.state.resources = resources
    application.state.assistant = assistant
    application.state.speech_input = speech_input
    application.state.desktop_speech = desktop_speech
    application.state.speech_output = speech_output
    return application


def create_app() -> FastAPI:
    return build_application(
        settings=BootstrapSettings(),
        secrets=WindowsCredentialStore(),
    )


def main() -> None:
    settings = BootstrapSettings()
    uvicorn.run(
        build_application(settings=settings, secrets=WindowsCredentialStore()),
        host=settings.host,
        port=settings.port,
        access_log=False,
        log_level="info",
    )
