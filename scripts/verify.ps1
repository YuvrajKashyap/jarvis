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

$cargo = Get-Command "cargo" -ErrorAction SilentlyContinue
if (-not $cargo) {
    $fallback = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"
    if (-not (Test-Path -LiteralPath $fallback)) {
        throw "cargo is unavailable"
    }
    $cargoPath = $fallback
} else {
    $cargoPath = $cargo.Source
}

Invoke-Checked "uv" @("run", "ruff", "format", "--check", ".")
Invoke-Checked "uv" @("run", "ruff", "check", ".")
Invoke-Checked "uv" @("run", "ty", "check")
Invoke-Checked "uv" @(
    "run", "pytest", "--cov=jarvis", "--cov-report=term-missing",
    "--cov-report=json:artifacts/coverage.json", "--cov-fail-under=0"
)
Invoke-Checked "uv" @("run", "python", "scripts/check_coverage.py", "artifacts/coverage.json", "85")

Invoke-Checked "pnpm" @("exec", "biome", "check", "ui")
Invoke-Checked "pnpm" @("contracts:check")
Invoke-Checked "pnpm" @("ui:test")
Invoke-Checked "pnpm" @("ui:build")

Invoke-Checked $cargoPath @("fmt", "--manifest-path", "src-tauri/Cargo.toml", "--all", "--", "--check")
Invoke-Checked $cargoPath @("check", "--locked", "--manifest-path", "src-tauri/Cargo.toml")
Invoke-Checked $cargoPath @("test", "--locked", "--manifest-path", "src-tauri/Cargo.toml")
Invoke-Checked $cargoPath @(
    "clippy", "--locked", "--manifest-path", "src-tauri/Cargo.toml", "--all-targets", "--",
    "-D", "warnings"
)

& (Join-Path $PSScriptRoot "audit-dependencies.ps1")

Write-Host "All JARVIS verification gates passed."
