import json
import os
import secrets as secret_tokens
import shutil
import threading
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import keyring
import uvicorn
from fastapi import FastAPI
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from jarvis.agency.browser import (
    ClickBrowserCapability,
    FillBrowserCapability,
    InspectBrowserCapability,
    NavigateBrowserCapability,
)
from jarvis.agency.capabilities import (
    CapabilityRegistry,
    InvocationCoordinator,
    InvocationEngine,
)
from jarvis.agency.files import ReadTextCapability, UndoFileCapability, WriteTextCapability
from jarvis.agency.memory import RememberMemoryCapability, UndoRememberMemoryCapability
from jarvis.agency.notifications import ReminderCapability
from jarvis.agency.observation import (
    ActiveWindowCapability,
    LocalTimeCapability,
    SystemHealthCapability,
)
from jarvis.agency.policy import PolicyEngine
from jarvis.agency.proactivity import ProactivityEngine, ProactivityPolicy, ProactivityRuntime
from jarvis.agency.scheduler import (
    CreateScheduleCapability,
    ScheduledInvocationRuntime,
    ScheduleRepository,
    UndoScheduleCreationCapability,
)
from jarvis.agency.terminal import TerminalCommandCapability
from jarvis.agency.windows import (
    InspectWindowsCapability,
    InvokeWindowsCapability,
    SetWindowsValueCapability,
)
from jarvis.memory.consolidation import ConversationConsolidator
from jarvis.memory.history import ConversationHistoryRepository
from jarvis.memory.retrieval import HybridMemoryRetriever
from jarvis.memory.semantic import SemanticMemoryIndex
from jarvis.memory.store import MemoryRepository
from jarvis.perception.context import PerceptionCoordinator
from jarvis.perception.placement import ContentAwarePlacement
from jarvis.platform.acceptance import LocalAcceptanceEvidence
from jarvis.platform.api import ApiSettings
from jarvis.platform.api import create_app as create_transport
from jarvis.platform.backups import SQLiteBackupService
from jarvis.platform.browser import PlaywrightBrowser
from jarvis.platform.embeddings import FastEmbedTextEmbeddings
from jarvis.platform.filesystem import LocalFileStore
from jarvis.platform.logging import configure_local_logging
from jarvis.platform.memory_context import LocalMemoryContext
from jarvis.platform.ollama import OllamaProvider
from jarvis.platform.pairing import PhonePairing
from jarvis.platform.pairing_sqlite import SQLitePairingStore
from jarvis.platform.proactivity import WindowsProactiveProbe
from jarvis.platform.proactivity_sqlite import SQLiteProactivityLedger
from jarvis.platform.process import BoundedProcessRunner, LocalCommandRunner
from jarvis.platform.resources import WindowsResourceProbe
from jarvis.platform.speech import (
    FasterWhisperTranscriber,
    OpenWakeWordDetector,
    SileroVad,
    SoundDeviceMicrophone,
)
from jarvis.platform.sqlite import SQLiteApprovalStore, SQLiteStore
from jarvis.platform.voice import ChatterboxTurboSynthesizer, SoundDeviceSpeaker
from jarvis.platform.windows import (
    WinAppAutomation,
    WindowsDesktopLayoutProbe,
    WindowsPerception,
    foreground_window_handle,
)
from jarvis.runtime.assistant import AssistantSettings, AssistantTurn
from jarvis.runtime.context import ScreenContextSource, TurnContextAssembler
from jarvis.runtime.conversation import RuntimeCoordinator
from jarvis.runtime.diagnostics import (
    AcceptanceProbe,
    CapabilityProbe,
    ConfigurationProbe,
    CountProbe,
    ModelResidencyProbe,
    ModuleAvailabilityProbe,
    ReadinessDiagnostics,
    ResourceProbe,
)
from jarvis.runtime.lifecycle import RuntimeLifecycle
from jarvis.runtime.resources import ResourceGovernor, ResourceLimits
from jarvis.speech.audio import AudioFormat, AudioRingBuffer
from jarvis.speech.desktop import DesktopSpeechService
from jarvis.speech.engine import SpeechCoordinator, SpeechSettings
from jarvis.speech.output import StreamingSpeechOutput
from jarvis.speech.remote import RemoteSpeechInput, RemoteSpeechSettings

REQUIRED_CAPABILITIES = frozenset(
    {
        "browser.click",
        "browser.fill",
        "browser.inspect",
        "browser.navigate",
        "context.active_window",
        "context.local_time",
        "files.read_text",
        "files.undo",
        "files.write_text",
        "memory.remember",
        "memory.undo_remember",
        "notifications.remind",
        "schedules.create",
        "schedules.undo_create",
        "system.health",
        "terminal.execute",
        "windows.inspect",
        "windows.invoke",
        "windows.set_value",
    }
)

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


def _default_winapp_executable() -> Path:
    discovered = shutil.which("winapp")
    if discovered is not None:
        return Path(discovered)
    return Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "winapp.exe"


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
    primary_model: str = Field(default="qwen3.5:4b-q4_K_M", min_length=1, max_length=160)
    model_context_length: int = Field(default=4_096, ge=512, le=8_192)
    model_prewarm_enabled: bool = True
    proactivity_enabled: bool = True
    proactivity_poll_seconds: float = Field(default=60, ge=15, le=900)
    proactivity_timezone: str = Field(default="America/Chicago", min_length=1, max_length=80)
    backup_retention: int = Field(default=7, ge=1, le=100)
    windows_automation_executable: Path = Field(default_factory=_default_winapp_executable)
    whisper_model: str = Field(default_factory=_default_whisper_model, min_length=1, max_length=512)
    desktop_speech_enabled: bool = True
    speech_input_device: str | None = Field(default=None, min_length=1, max_length=240)
    speech_output_device: str | None = Field(default=None, min_length=1, max_length=240)
    voice_reference_path: Path | None = None
    voice_device: Literal["cpu", "cuda"] = "cpu"
    voice_model: Literal["nano", "turbo"] = "nano"
    phone_base_url: str | None = Field(default_factory=_default_phone_base_url)
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:1420",
        "http://tauri.localhost",
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
    acceptance_evidence = LocalAcceptanceEvidence(settings.data_directory / "acceptance")
    primary_model = acceptance_evidence.passing_subject("model-quality") or settings.primary_model
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
    memory_retrieval = HybridMemoryRetriever(
        memory=memory,
        semantic=SemanticMemoryIndex(
            sqlite=sqlite,
            embeddings=FastEmbedTextEmbeddings(
                cache_directory=settings.data_directory / "models" / "fastembed"
            ),
        ),
    )
    history = ConversationHistoryRepository(sqlite)
    memory_consolidation = ConversationConsolidator(history=history, memory=memory)
    runtime = RuntimeCoordinator()
    perception = PerceptionCoordinator(WindowsPerception())
    capabilities = CapabilityRegistry()
    capabilities.register(ActiveWindowCapability(perception))
    capabilities.register(LocalTimeCapability(timezone_name=settings.proactivity_timezone))
    capabilities.register(SystemHealthCapability(perception))
    files = LocalFileStore(
        roots=settings.file_roots,
        undo_directory=settings.data_directory / "undo" / "files",
    )
    capabilities.register(ReadTextCapability(files))
    capabilities.register(WriteTextCapability(files))
    capabilities.register(UndoFileCapability(files))
    terminal = LocalCommandRunner.from_path(
        roots=settings.file_roots,
        names=("git", "pnpm", "uv", "cargo", "rg"),
    )
    capabilities.register(TerminalCommandCapability(terminal))
    browser = PlaywrightBrowser(settings.data_directory / "browser-profile")
    capabilities.register(InspectBrowserCapability(browser))
    capabilities.register(NavigateBrowserCapability(browser))
    capabilities.register(ClickBrowserCapability(browser))
    capabilities.register(FillBrowserCapability(browser))
    windows_automation = WinAppAutomation(
        executable=settings.windows_automation_executable,
        runner=BoundedProcessRunner(),
        window_handle=foreground_window_handle,
        working_directory=settings.data_directory,
    )
    capabilities.register(InspectWindowsCapability(windows_automation))
    capabilities.register(InvokeWindowsCapability(windows_automation))
    capabilities.register(SetWindowsValueCapability(windows_automation))
    capabilities.register(RememberMemoryCapability(memory))
    capabilities.register(UndoRememberMemoryCapability(memory))
    capabilities.register(ReminderCapability())
    policy = PolicyEngine(SQLiteApprovalStore(sqlite))
    action_engine = InvocationEngine(registry=capabilities, policy=policy, audit=sqlite)
    actions = InvocationCoordinator(engine=action_engine, policy=policy)
    scheduler = ScheduledInvocationRuntime(
        repository=ScheduleRepository(sqlite),
        actions=actions,
    )
    capabilities.register(CreateScheduleCapability(scheduler=scheduler, registry=capabilities))
    capabilities.register(UndoScheduleCreationCapability(scheduler=scheduler))
    resource_probe = WindowsResourceProbe()
    proactivity = (
        ProactivityRuntime(
            probe=WindowsProactiveProbe(
                perception=perception,
                resources=resource_probe,
                downloads_directory=Path.home() / "Downloads",
            ),
            engine=ProactivityEngine(
                ledger=SQLiteProactivityLedger(sqlite),
                policy=ProactivityPolicy(),
                timezone=ZoneInfo(settings.proactivity_timezone),
            ),
            poll_interval_seconds=settings.proactivity_poll_seconds,
        )
        if settings.proactivity_enabled
        else None
    )
    backups = SQLiteBackupService(
        store=sqlite,
        directory=settings.data_directory / "backups",
        retain=settings.backup_retention,
    )
    model = OllamaProvider()
    resources = ResourceGovernor(
        models=model,
        probe=resource_probe,
        limits=ResourceLimits(),
    )
    lifecycle = RuntimeLifecycle(
        models=resources if settings.model_prewarm_enabled else None,
        primary_model=primary_model if settings.model_prewarm_enabled else None,
        components=tuple(
            component
            for component in (backups, scheduler, memory_consolidation, proactivity)
            if component is not None
        ),
        async_closeables=(browser,),
        closeables=(sqlite,),
    )
    turn_context = TurnContextAssembler(
        (
            LocalMemoryContext(history=history, memory=memory_retrieval),
            ScreenContextSource(perception),
        )
    )
    assistant = AssistantTurn(
        model=model,
        settings=AssistantSettings(
            primary_model=primary_model,
            context_length=settings.model_context_length,
        ),
        tools=capabilities.tool_schemas(),
        readiness=resources,
        context_provider=turn_context,
    )
    transcriber = FasterWhisperTranscriber(model_name=settings.whisper_model)
    speech_input = RemoteSpeechInput(
        vad=SileroVad(),
        transcriber=transcriber,
        settings=RemoteSpeechSettings(),
    )
    speaker = (
        SoundDeviceSpeaker(device=settings.speech_output_device)
        if settings.voice_reference_path is not None
        else None
    )
    speech_coordinator = (
        SpeechCoordinator(
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
        )
        if settings.desktop_speech_enabled
        else None
    )
    speech_output = (
        None
        if settings.voice_reference_path is None or speaker is None
        else StreamingSpeechOutput(
            synthesizer=ChatterboxTurboSynthesizer(
                reference_path=settings.voice_reference_path,
                device=settings.voice_device,
                nano=settings.voice_model == "nano",
            ),
            desktop_sink=speaker,
            desktop_playback_state=speech_coordinator,
        )
    )
    desktop_speech = None
    if speech_coordinator is not None:
        desktop_speech = DesktopSpeechService(
            microphone=SoundDeviceMicrophone(device=settings.speech_input_device),
            coordinator=speech_coordinator,
            transcriber=transcriber,
        )
    phone_pairing = PhonePairing(SQLitePairingStore(sqlite))
    diagnostics = ReadinessDiagnostics(
        (
            ModelResidencyProbe(model=model, primary_model=primary_model),
            AcceptanceProbe(
                evidence=acceptance_evidence,
                code="model_quality",
                evidence_code="model-quality",
                subject=primary_model,
                ready_summary="The selected model passed JARVIS quality acceptance.",
                missing_summary="The selected model has not passed JARVIS quality acceptance.",
            ),
            ConfigurationProbe(
                code="speech_input",
                configured=desktop_speech is not None,
                ready_summary="Desktop wake and speech input are configured.",
                missing_summary="Desktop wake and speech input are not configured.",
            ),
            ModuleAvailabilityProbe(
                code="speech_dependencies",
                modules=(
                    "openwakeword",
                    "silero_vad",
                    "faster_whisper",
                    "chatterbox",
                    "sounddevice",
                    "onnxruntime",
                    "ctranslate2",
                ),
            ),
            ConfigurationProbe(
                code="voice",
                configured=speech_output is not None,
                ready_summary="The private original JARVIS voice is configured.",
                missing_summary="The private original JARVIS voice is not configured.",
            ),
            CountProbe(code="memory", counter=memory.count, noun="durable memory facts"),
            CountProbe(
                code="paired_devices",
                counter=phone_pairing.paired_device_count,
                noun="paired devices",
            ),
            ConfigurationProbe(
                code="phone_routing",
                configured=settings.phone_base_url is not None,
                ready_summary="Private phone routing is configured.",
                missing_summary="Private phone routing is not configured.",
            ),
            CapabilityProbe(names=capabilities.names, required=REQUIRED_CAPABILITIES),
            ResourceProbe(resources=resource_probe),
            *(
                AcceptanceProbe(
                    evidence=acceptance_evidence,
                    code=diagnostic_code,
                    evidence_code=evidence_code,
                    ready_summary=ready_summary,
                    missing_summary=missing_summary,
                )
                for diagnostic_code, evidence_code, ready_summary, missing_summary in (
                    (
                        "installed_product",
                        "installed-product",
                        "The installed product passed end-to-end acceptance.",
                        "The installed product has not passed end-to-end acceptance.",
                    ),
                    (
                        "speech_pipeline",
                        "speech-pipeline",
                        "The packaged speech pipeline passed acceptance.",
                        "The packaged speech pipeline has not passed acceptance.",
                    ),
                    (
                        "capability_acceptance",
                        "capabilities",
                        "Real capability and authorization scenarios passed.",
                        "Real capability and authorization scenarios remain unverified.",
                    ),
                    (
                        "recovery",
                        "recovery",
                        "Restart and recovery acceptance passed.",
                        "Restart and recovery acceptance remains unverified.",
                    ),
                    (
                        "resource_soak",
                        "resource-soak",
                        "Active and idle resource-soak acceptance passed.",
                        "Active and idle resource-soak acceptance remains unverified.",
                    ),
                    (
                        "iphone_acceptance",
                        "iphone",
                        "The physical iPhone passed acceptance.",
                        "The physical iPhone has not passed acceptance.",
                    ),
                    (
                        "acoustic_acceptance",
                        "acoustic",
                        "Real-room acoustic acceptance passed.",
                        "Real-room acoustic acceptance remains unverified.",
                    ),
                )
            ),
        )
    )
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
        lifecycle=lifecycle,
        scheduled_events=scheduler,
        proactive_events=proactivity,
        overlay_placement=ContentAwarePlacement(WindowsDesktopLayoutProbe()),
        diagnostics=diagnostics,
    )
    application.state.runtime = runtime
    application.state.sqlite = sqlite
    application.state.memory = memory
    application.state.memory_retrieval = memory_retrieval
    application.state.history = history
    application.state.perception = perception
    application.state.capabilities = capabilities
    application.state.files = files
    application.state.terminal = terminal
    application.state.browser = browser
    application.state.windows_automation = windows_automation
    application.state.policy = policy
    application.state.actions = actions
    application.state.scheduler = scheduler
    application.state.proactivity = proactivity
    application.state.backups = backups
    application.state.phone_pairing = phone_pairing
    application.state.model = model
    application.state.primary_model = primary_model
    application.state.resources = resources
    application.state.lifecycle = lifecycle
    application.state.assistant = assistant
    application.state.turn_context = turn_context
    application.state.speech_input = speech_input
    application.state.desktop_speech = desktop_speech
    application.state.speech_output = speech_output
    application.state.diagnostics = diagnostics
    return application


def create_app() -> FastAPI:
    return build_application(
        settings=BootstrapSettings(),
        secrets=WindowsCredentialStore(),
    )


def main() -> None:
    settings = BootstrapSettings()
    configure_local_logging(settings.data_directory)
    uvicorn.run(
        build_application(settings=settings, secrets=WindowsCredentialStore()),
        host=settings.host,
        port=settings.port,
        access_log=False,
        log_level="info",
    )
