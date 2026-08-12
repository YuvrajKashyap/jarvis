import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
import psutil
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
PRIMARY_MODEL = "qwen3.5:4b-q4_K_M"
REQUIRED_COMMANDS = frozenset(
    {"git", "node", "pnpm", "uv", "cargo", "ollama", "tailscale", "winapp"}
)
REQUIRED_MODEL_CAPABILITIES = frozenset({"completion", "vision", "tools"})
MINIMUM_AVAILABLE_MEMORY_BYTES = 1024**3


class PreflightValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObservedEnvironment(PreflightValue):
    primary_model: str = PRIMARY_MODEL
    available_commands: frozenset[str]
    primary_model_installed: bool
    model_capabilities: frozenset[str]
    wake_assets_ready: bool
    whisper_assets_ready: bool
    embedding_assets_ready: bool
    microphone_available: bool
    tailscale_online: bool
    tailscale_serve_enabled: bool
    phone_base_url_configured: bool
    voice_reference_configured: bool
    installer_built: bool
    available_memory_bytes: int = Field(ge=0)
    model_quality_verified: bool
    installed_product_verified: bool
    speech_pipeline_verified: bool
    capability_acceptance_verified: bool
    recovery_verified: bool
    resource_soak_verified: bool
    phone_device_paired: bool
    iphone_acceptance_verified: bool
    acoustic_acceptance_verified: bool


class ReadinessItem(PreflightValue):
    code: str
    status: Literal["ready", "blocked", "manual", "pending", "transient", "unverified"]
    detail: str


class ReadinessReport(PreflightValue):
    generated_at: datetime
    items: tuple[ReadinessItem, ...]
    software_blockers: tuple[ReadinessItem, ...]
    manual_steps: tuple[ReadinessItem, ...]
    automation_complete: bool
    product_ready: bool


def evaluate_readiness(observed: ObservedEnvironment) -> ReadinessReport:
    missing_commands = sorted(REQUIRED_COMMANDS - observed.available_commands)
    missing_capabilities = sorted(REQUIRED_MODEL_CAPABILITIES - observed.model_capabilities)
    items = (
        _item(
            "toolchain",
            not missing_commands,
            "All required local commands are installed.",
            "Missing required commands: " + ", ".join(missing_commands),
        ),
        _item(
            "primary_model",
            observed.primary_model_installed,
            f"{observed.primary_model} is installed locally.",
            f"{observed.primary_model} is not installed in Ollama.",
        ),
        _item(
            "model_capabilities",
            not missing_capabilities,
            "The primary model exposes completion, vision, and tools.",
            "Primary model lacks: " + ", ".join(missing_capabilities),
        ),
        _item(
            "wake_assets",
            observed.wake_assets_ready,
            "Wake-word and feature models are cached locally.",
            "Managed openWakeWord assets are incomplete.",
        ),
        _item(
            "whisper_assets",
            observed.whisper_assets_ready,
            "The local speech-recognition model is cached.",
            "The managed faster-whisper model is incomplete.",
        ),
        _item(
            "embedding_assets",
            observed.embedding_assets_ready,
            "The local semantic-memory model is cached.",
            "The managed semantic-memory model is incomplete.",
        ),
        _item(
            "microphone",
            observed.microphone_available,
            "Windows exposes a usable input device.",
            "No usable microphone input device was detected.",
        ),
        _item(
            "tailscale_online",
            observed.tailscale_online,
            "The laptop is online in its tailnet.",
            "Tailscale needs a signed-in, online laptop session.",
            failure_status="manual",
        ),
        _item(
            "tailscale_serve",
            observed.tailscale_serve_enabled,
            "Tailnet-only HTTPS Serve is configured.",
            "Enable Tailscale Serve for this tailnet, then JARVIS can finish phone routing.",
            failure_status="manual",
        ),
        _item(
            "phone_configuration",
            observed.phone_base_url_configured,
            "The private phone origin is recorded in JARVIS configuration.",
            "Phone routing will be generated automatically after Tailscale Serve is enabled.",
            failure_status="pending",
        ),
        _item(
            "voice_reference",
            observed.voice_reference_configured,
            "A private original JARVIS voice reference is configured.",
            "Record and select the private original voice reference.",
            failure_status="manual",
        ),
        _item(
            "installer",
            observed.installer_built,
            "A Windows installer has been built.",
            "The Windows installer has not been built.",
        ),
        ReadinessItem(
            code="memory_headroom",
            status=(
                "ready"
                if observed.available_memory_bytes >= MINIMUM_AVAILABLE_MEMORY_BYTES
                else "transient"
            ),
            detail=(
                "Current memory headroom satisfies the model safety gate."
                if observed.available_memory_bytes >= MINIMUM_AVAILABLE_MEMORY_BYTES
                else "Current memory is below the safe model-load gate; JARVIS will not close "
                "applications or force the model to load."
            ),
        ),
        _acceptance_item(
            "model_quality_acceptance",
            observed.model_quality_verified,
            "The selected model passed the complete JARVIS quality and safety evaluation.",
            "No passing model-quality evidence exists for the selected model.",
        ),
        _acceptance_item(
            "installed_product_acceptance",
            observed.installed_product_verified,
            "The installed desktop product passed end-to-end acceptance.",
            "The installed desktop product has not passed end-to-end acceptance.",
        ),
        _acceptance_item(
            "speech_pipeline_acceptance",
            observed.speech_pipeline_verified,
            "The packaged wake, transcription, synthesis, and interruption path passed.",
            "The packaged speech pipeline has not passed real end-to-end acceptance.",
        ),
        _acceptance_item(
            "capability_acceptance",
            observed.capability_acceptance_verified,
            "The capability, permission, approval, cancellation, and undo scenarios passed.",
            "Real capability and authorization scenarios have not passed acceptance.",
        ),
        _acceptance_item(
            "recovery_acceptance",
            observed.recovery_verified,
            "Restart, migration, upgrade, and recovery scenarios passed.",
            "Restart, migration, upgrade, and recovery scenarios remain unverified.",
        ),
        _acceptance_item(
            "resource_soak_acceptance",
            observed.resource_soak_verified,
            "Active and idle resource-soak acceptance passed.",
            "The required active and idle resource-soak runs have not passed.",
        ),
        ReadinessItem(
            code="iphone_acceptance",
            status=(
                "ready"
                if (
                    observed.tailscale_online
                    and observed.tailscale_serve_enabled
                    and observed.phone_base_url_configured
                    and observed.phone_device_paired
                    and observed.iphone_acceptance_verified
                )
                else "manual"
            ),
            detail=(
                "The physical iPhone 17 Pro passed pairing, conversation, interruption, "
                "reconnection, notification, and unavailable-state acceptance."
                if (
                    observed.tailscale_online
                    and observed.tailscale_serve_enabled
                    and observed.phone_base_url_configured
                    and observed.phone_device_paired
                    and observed.iphone_acceptance_verified
                )
                else (
                    "The iPhone is not paired; pair it and run the physical phone acceptance."
                    if not observed.phone_device_paired
                    else "Run the physical iPhone conversation and network acceptance."
                )
            ),
        ),
        ReadinessItem(
            code="acoustic_acceptance",
            status=(
                "ready"
                if observed.voice_reference_configured and observed.acoustic_acceptance_verified
                else "manual"
            ),
            detail=(
                "Personalized wake-word, echo, barge-in, and selected-voice acceptance passed."
                if observed.voice_reference_configured and observed.acoustic_acceptance_verified
                else "Run personalized wake-word, echo, barge-in, and selected-voice checks in "
                "the real room."
            ),
        ),
    )
    blockers = tuple(item for item in items if item.status == "blocked")
    manual = tuple(item for item in items if item.status == "manual")
    unverified = tuple(item for item in items if item.status == "unverified")
    return ReadinessReport(
        generated_at=datetime.now(UTC),
        items=items,
        software_blockers=blockers,
        manual_steps=manual,
        automation_complete=not blockers and not unverified,
        product_ready=all(item.status == "ready" for item in items),
    )


def collect_environment() -> ObservedEnvironment:
    data_directory = _data_directory()
    model_directory = data_directory / "models"
    acceptance_directory = data_directory / "acceptance"
    primary_model = _accepted_subject(acceptance_directory / "model-quality.json") or PRIMARY_MODEL
    installed_models, capabilities = _ollama_state(primary_model)
    tailscale_online, serve_enabled = _tailscale_state()
    config = _read_json(data_directory / "config.json")
    voice_path = os.environ.get("JARVIS_VOICE_REFERENCE_PATH")
    available_commands = frozenset(
        command for command in REQUIRED_COMMANDS if _command_available(command)
    )
    wake_directory = model_directory / "openwakeword"
    whisper_directory = model_directory / "faster-whisper" / "distil-small.en"
    return ObservedEnvironment(
        primary_model=primary_model,
        available_commands=available_commands,
        primary_model_installed=primary_model in installed_models,
        model_capabilities=frozenset(capabilities),
        wake_assets_ready=all(
            (wake_directory / name).is_file()
            for name in (
                "hey_jarvis_v0.1.onnx",
                "melspectrogram.onnx",
                "embedding_model.onnx",
            )
        ),
        whisper_assets_ready=(whisper_directory / "model.bin").is_file(),
        embedding_assets_ready=any((model_directory / "fastembed").rglob("model_optimized.onnx")),
        microphone_available=_microphone_available(),
        tailscale_online=tailscale_online,
        tailscale_serve_enabled=serve_enabled,
        phone_base_url_configured=isinstance(config.get("phone_base_url"), str),
        voice_reference_configured=bool(voice_path and Path(voice_path).is_file()),
        installer_built=any(
            (ROOT / "src-tauri" / "target" / "release" / "bundle" / "nsis").glob(
                "JARVIS_*_x64-setup.exe"
            )
        ),
        available_memory_bytes=psutil.virtual_memory().available,
        model_quality_verified=_acceptance_passed(
            acceptance_directory / "model-quality.json",
            subject=primary_model,
        ),
        installed_product_verified=_acceptance_passed(
            acceptance_directory / "installed-product.json"
        ),
        speech_pipeline_verified=_acceptance_passed(acceptance_directory / "speech-pipeline.json"),
        capability_acceptance_verified=_acceptance_passed(
            acceptance_directory / "capabilities.json"
        ),
        recovery_verified=_acceptance_passed(acceptance_directory / "recovery.json"),
        resource_soak_verified=_acceptance_passed(acceptance_directory / "resource-soak.json"),
        phone_device_paired=_paired_device_exists(data_directory / "jarvis.db"),
        iphone_acceptance_verified=_acceptance_passed(acceptance_directory / "iphone.json"),
        acoustic_acceptance_verified=_acceptance_passed(acceptance_directory / "acoustic.json"),
    )


def main() -> None:
    report = evaluate_readiness(collect_environment())
    output_directory = ROOT / "artifacts"
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / "preflight.json"
    output_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for item in report.items:
        print(f"{item.status.upper():9} {item.code:24} {item.detail}")
    print(output_path)


def _item(
    code: str,
    condition: bool,
    ready_detail: str,
    failure_detail: str,
    *,
    failure_status: Literal["blocked", "manual", "pending"] = "blocked",
) -> ReadinessItem:
    return ReadinessItem(
        code=code,
        status="ready" if condition else failure_status,
        detail=ready_detail if condition else failure_detail,
    )


def _acceptance_item(
    code: str,
    verified: bool,
    ready_detail: str,
    unverified_detail: str,
) -> ReadinessItem:
    return ReadinessItem(
        code=code,
        status="ready" if verified else "unverified",
        detail=ready_detail if verified else unverified_detail,
    )


def _data_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "share") / "JARVIS"


def _command_available(command: str) -> bool:
    if shutil.which(command) is not None:
        return True
    return command == "cargo" and (Path.home() / ".cargo" / "bin" / "cargo.exe").is_file()


def _ollama_state(primary_model: str = PRIMARY_MODEL) -> tuple[set[str], set[str]]:
    try:
        with httpx.Client(base_url="http://127.0.0.1:11434", timeout=5) as client:
            tags = client.get("/api/tags")
            tags.raise_for_status()
            installed = {
                str(item.get("name"))
                for item in tags.json().get("models", [])
                if isinstance(item, dict)
            }
            if primary_model not in installed:
                return installed, set()
            shown = client.post("/api/show", json={"model": primary_model})
            shown.raise_for_status()
            raw_capabilities = shown.json().get("capabilities", [])
            capabilities = {str(value) for value in raw_capabilities if isinstance(value, str)}
            return installed, capabilities
    except (httpx.HTTPError, json.JSONDecodeError, TypeError, AttributeError):
        return set(), set()


def _tailscale_state() -> tuple[bool, bool]:
    environment = os.environ.copy()
    environment["WINAPP_CLI_TELEMETRY_OPTOUT"] = "1"
    status = _fixed_command(("tailscale", "status", "--json"), environment)
    serve = _fixed_command(("tailscale", "serve", "status", "--json"), environment)
    online = False
    serve_enabled = False
    if status is not None:
        try:
            payload = json.loads(status)
            online = payload.get("BackendState") == "Running" and bool(
                payload.get("Self", {}).get("Online")
            )
        except (json.JSONDecodeError, AttributeError):
            pass
    if serve is not None:
        with contextlib.suppress(json.JSONDecodeError):
            serve_enabled = bool(json.loads(serve))
    return online, serve_enabled


def _fixed_command(arguments: tuple[str, ...], environment: dict[str, str]) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed diagnostic allowlist
            arguments,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return completed.stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _acceptance_passed(path: Path, *, subject: str | None = None) -> bool:
    payload = _read_json(path)
    return (
        payload.get("schema_version") == 1
        and payload.get("passed") is True
        and (subject is None or payload.get("subject") == subject)
    )


def _accepted_subject(path: Path) -> str | None:
    payload = _read_json(path)
    subject = payload.get("subject")
    if (
        payload.get("schema_version") == 1
        and payload.get("passed") is True
        and isinstance(subject, str)
        and subject.strip()
    ):
        return subject
    return None


def _paired_device_exists(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1) as connection:
            row = connection.execute("SELECT 1 FROM paired_device LIMIT 1").fetchone()
            return row is not None
    except sqlite3.Error:
        return False


def _microphone_available() -> bool:
    try:
        import sounddevice

        device = sounddevice.query_devices(kind="input")
        return float(device["max_input_channels"]) > 0
    except (ImportError, OSError, TypeError, ValueError):
        return False


if __name__ == "__main__":
    main()
