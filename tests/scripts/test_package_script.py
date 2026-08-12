from pathlib import Path


def test_pyinstaller_metadata_stays_under_ignored_build_directory() -> None:
    script = Path("scripts/package.ps1").read_text(encoding="utf-8")

    assert '"--specpath", "build/pyinstaller"' in script


def test_sidecar_build_includes_the_complete_dynamic_speech_runtime() -> None:
    script = Path("scripts/package.ps1").read_text(encoding="utf-8")

    assert '"run", "--extra", "speech", "pyinstaller"' in script
    for package in ("openwakeword", "silero_vad", "faster_whisper", "chatterbox"):
        assert f'"--collect-all", "{package}"' in script
    for module in ("onnxruntime", "ctranslate2"):
        assert f'"--hidden-import", "{module}"' in script


def test_pyinstaller_inputs_are_absolute_when_spec_is_nested() -> None:
    script = Path("scripts/package.ps1").read_text(encoding="utf-8")

    assert '$sourceDirectory = Join-Path $workspace "src"' in script
    assert (
        '$migrationsDirectory = Join-Path $sourceDirectory "jarvis\\platform\\migrations"' in script
    )
    assert '$uiDistribution = Join-Path $workspace "ui\\dist"' in script
    assert '$entryPoint = Join-Path $sourceDirectory "jarvis\\__main__.py"' in script
    assert '"--paths", $sourceDirectory' in script
    assert '"$migrationsDirectory;jarvis/platform/migrations"' in script
    assert '"$uiDistribution;jarvis/ui_dist"' in script
    assert "$entryPoint\n)" in script


def test_package_smoke_tests_the_frozen_core_after_build() -> None:
    script = Path("scripts/package.ps1").read_text(encoding="utf-8")

    assert "scripts/packaged_core_acceptance.py" in script
    assert "src-tauri/resources/jarvis-core/jarvis-core.exe" in script
