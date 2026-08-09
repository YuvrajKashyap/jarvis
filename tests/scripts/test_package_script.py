from pathlib import Path


def test_pyinstaller_metadata_stays_under_ignored_build_directory() -> None:
    script = Path("scripts/package.ps1").read_text(encoding="utf-8")

    assert '"--specpath", "build/pyinstaller"' in script


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
