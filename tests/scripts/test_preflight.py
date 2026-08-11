import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_preflight",
    Path(__file__).resolve().parents[2] / "scripts" / "preflight.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_PREFLIGHT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PREFLIGHT)
ObservedEnvironment = _PREFLIGHT.ObservedEnvironment
evaluate_readiness = _PREFLIGHT.evaluate_readiness

REQUIRED_COMMANDS = frozenset(
    {"git", "node", "pnpm", "uv", "cargo", "ollama", "tailscale", "winapp"}
)


def ready_environment(**overrides: object) -> ObservedEnvironment:
    values: dict[str, object] = {
        "available_commands": REQUIRED_COMMANDS,
        "primary_model_installed": True,
        "model_capabilities": frozenset({"completion", "vision", "tools", "thinking"}),
        "wake_assets_ready": True,
        "whisper_assets_ready": True,
        "embedding_assets_ready": True,
        "microphone_available": True,
        "tailscale_online": True,
        "tailscale_serve_enabled": True,
        "phone_base_url_configured": True,
        "voice_reference_configured": True,
        "installer_built": True,
        "available_memory_bytes": 4 * 1024**3,
    }
    values.update(overrides)
    return ObservedEnvironment(**values)


def test_readiness_separates_automated_state_from_physical_acceptance() -> None:
    report = evaluate_readiness(
        ready_environment(
            tailscale_serve_enabled=False,
            phone_base_url_configured=False,
            voice_reference_configured=False,
        )
    )

    assert report.software_blockers == ()
    assert [item.code for item in report.manual_steps] == [
        "tailscale_serve",
        "voice_reference",
        "iphone_acceptance",
        "acoustic_acceptance",
    ]
    assert report.automation_complete is True


def test_readiness_blocks_missing_local_intelligence_assets() -> None:
    report = evaluate_readiness(
        ready_environment(
            primary_model_installed=False,
            wake_assets_ready=False,
            model_capabilities=frozenset({"completion"}),
        )
    )

    assert [item.code for item in report.software_blockers] == [
        "primary_model",
        "model_capabilities",
        "wake_assets",
    ]
    assert report.automation_complete is False


def test_measured_q4_headroom_is_ready_without_demanding_app_closure() -> None:
    report = evaluate_readiness(ready_environment(available_memory_bytes=int(1.5 * 1024**3)))

    headroom = next(item for item in report.items if item.code == "memory_headroom")
    assert headroom.status == "ready"


def test_low_memory_is_reported_as_transient_without_demanding_app_closure() -> None:
    report = evaluate_readiness(ready_environment(available_memory_bytes=int(0.75 * 1024**3)))

    pressure = next(item for item in report.items if item.code == "memory_headroom")
    assert pressure.status == "transient"
    assert "will not close" in pressure.detail
    assert report.software_blockers == ()
