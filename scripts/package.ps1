param([switch]$SkipVerify)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$workspace = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $workspace

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$Command,
        [Parameter(Mandatory)][string[]]$Arguments
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command $($Arguments -join ' ')"
    }
}

if (-not $SkipVerify) {
    & (Join-Path $PSScriptRoot "verify.ps1")
}
& (Join-Path $PSScriptRoot "generate-sbom.ps1")

$sidecarDirectory = Join-Path $workspace "src-tauri\resources\jarvis-core"
$sourceDirectory = Join-Path $workspace "src"
$migrationsDirectory = Join-Path $sourceDirectory "jarvis\platform\migrations"
$uiDistribution = Join-Path $workspace "ui\dist"
$entryPoint = Join-Path $sourceDirectory "jarvis\__main__.py"
$resolvedWorkspace = [System.IO.Path]::GetFullPath($workspace)
$resolvedSidecar = [System.IO.Path]::GetFullPath($sidecarDirectory)
if (-not $resolvedSidecar.StartsWith("$resolvedWorkspace\src-tauri\resources\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean an unexpected sidecar directory: $resolvedSidecar"
}
if (Test-Path -LiteralPath $resolvedSidecar) {
    Remove-Item -LiteralPath $resolvedSidecar -Recurse -Force
}

Invoke-Checked "uv" @(
    "run", "--extra", "speech", "pyinstaller", "--noconfirm", "--clean", "--onedir",
    "--name", "jarvis-core",
    "--distpath", "src-tauri/resources", "--specpath", "build/pyinstaller",
    "--paths", $sourceDirectory, "--collect-submodules", "jarvis.platform.migrations",
    "--collect-all", "openwakeword", "--collect-all", "silero_vad",
    "--collect-all", "faster_whisper", "--collect-all", "chatterbox",
    "--collect-all", "sounddevice", "--hidden-import", "onnxruntime",
    "--hidden-import", "ctranslate2", "--add-data",
    "$migrationsDirectory;jarvis/platform/migrations", "--add-data",
    "$uiDistribution;jarvis/ui_dist", $entryPoint
)

if (-not (Test-Path -LiteralPath (Join-Path $resolvedSidecar "jarvis-core.exe"))) {
    throw "PyInstaller did not produce the expected JARVIS sidecar"
}

Invoke-Checked "pnpm" @("tauri", "build")
Invoke-Checked "uv" @(
    "run", "python", "scripts/desktop_acceptance.py",
    "src-tauri/target/release/jarvis-host.exe"
)
Invoke-Checked "uv" @(
    "run", "python", "scripts/packaged_core_acceptance.py",
    "src-tauri/resources/jarvis-core/jarvis-core.exe"
)
