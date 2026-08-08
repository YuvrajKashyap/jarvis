$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$workspace = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $workspace

function Require-Command {
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' is unavailable. See README.md for the Windows prerequisites."
    }
}

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

$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path -LiteralPath (Join-Path $cargoBin "cargo.exe")) {
    $env:PATH = "$cargoBin;$env:PATH"
}
$ollamaBin = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
if (Test-Path -LiteralPath (Join-Path $ollamaBin "ollama.exe")) {
    $env:PATH = "$ollamaBin;$env:PATH"
}

Require-Command "git"
Require-Command "node"
Require-Command "pnpm"
Require-Command "uv"
Require-Command "cargo"
Require-Command "ollama"

if (-not (Get-Command "tailscale" -ErrorAction SilentlyContinue)) {
    Write-Warning "Tailscale is not installed yet. Desktop development works; phone access remains unavailable."
}

Invoke-Checked "uv" @("sync", "--frozen", "--extra", "speech", "--extra", "voice")
Invoke-Checked "uv" @("run", "python", "scripts/prefetch_models.py", "--core")
Invoke-Checked "pnpm" @("install", "--frozen-lockfile")
Invoke-Checked "cargo" @("fetch", "--locked", "--manifest-path", "src-tauri/Cargo.toml")
Invoke-Checked "pnpm" @("contracts:check")

Write-Host "JARVIS project dependencies are ready."
