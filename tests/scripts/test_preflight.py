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
accepted_subject = _PREFLIGHT._accepted_subject

REQUIRED_COMMANDS = frozenset(
    {"git", "node", "pnpm", "uv", "cargo", "ollama", "tailscale", "winapp"}
)


def test_preflight_uses_only_a_valid_passing_model_subject(tmp_path: Path) -> None:
    evidence = tmp_path / "model-quality.json"
    evidence.write_text(
        '{"schema_version":1,"passed":true,"subject":"winner:model",'
        '"completed_at":"2026-08-11T12:00:00Z"}',
        encoding="utf-8",
    )

    assert accepted_subject(evidence) == "winner:model"
    evidence.write_text('{"schema_version":1,"passed":false,"subject":"unsafe"}', encoding="utf-8")
    assert accepted_subject(evidence) is None


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
        "model_quality_verified": True,
        "installed_product_verified": True,
        "speech_pipeline_verified": True,
        "capability_acceptance_verified": True,
        "recovery_verified": True,
        "resource_soak_verified": True,
        "phone_device_paired": True,
        "iphone_acceptance_verified": True,
        "acoustic_acceptance_verified": True,
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
    assert report.product_ready is False


def test_installed_prerequisites_do_not_masquerade_as_acceptance() -> None:
    report = evaluate_readiness(
        ready_environment(
            model_quality_verified=False,
            installed_product_verified=False,
            speech_pipeline_verified=False,
            capability_acceptance_verified=False,
            recovery_verified=False,
            resource_soak_verified=False,
            phone_device_paired=False,
        )
    )

    unverified = [item.code for item in report.items if item.status == "unverified"]
    assert unverified == [
        "model_quality_acceptance",
        "installed_product_acceptance",
        "speech_pipeline_acceptance",
        "capability_acceptance",
        "recovery_acceptance",
        "resource_soak_acceptance",
    ]
    assert report.automation_complete is False
    assert report.product_ready is False

    phone = next(item for item in report.items if item.code == "iphone_acceptance")
    assert phone.status == "manual"
    assert "not paired" in phone.detail.lower()


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
