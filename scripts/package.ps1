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

& (Join-Path $PSScriptRoot "verify.ps1")

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
    "run", "pyinstaller", "--noconfirm", "--clean", "--onedir", "--name", "jarvis-core",
    "--distpath", "src-tauri/resources", "--specpath", "build/pyinstaller",
    "--paths", $sourceDirectory, "--collect-submodules",
    "jarvis.platform.migrations", "--add-data",
    "$migrationsDirectory;jarvis/platform/migrations", "--add-data",
    "$uiDistribution;jarvis/ui_dist", $entryPoint
)

if (-not (Test-Path -LiteralPath (Join-Path $resolvedSidecar "jarvis-core.exe"))) {
    throw "PyInstaller did not produce the expected JARVIS sidecar"
}

Invoke-Checked "pnpm" @("tauri", "build")
