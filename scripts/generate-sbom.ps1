$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$workspace = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $workspace
$artifacts = Join-Path $workspace "artifacts"
New-Item -ItemType Directory -Path $artifacts -Force | Out-Null

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

Invoke-Checked "uv" @(
    "run", "cyclonedx-py", "environment", "--output-format", "JSON",
    "--output-file", "artifacts/sbom-python.json"
)
Invoke-Checked "pnpm" @(
    "sbom", "--sbom-format", "cyclonedx", "--prod", "--out", "artifacts/sbom-node.json"
)
Invoke-Checked "cargo" @(
    "cyclonedx", "--manifest-path", "src-tauri/Cargo.toml", "--format", "json", "--quiet"
)

$cargoOutput = Join-Path $workspace "src-tauri\jarvis-host.cdx.json"
$rustOutput = Join-Path $artifacts "sbom-rust.json"
$resolvedCargoOutput = [System.IO.Path]::GetFullPath($cargoOutput)
$resolvedTauriRoot = [System.IO.Path]::GetFullPath((Join-Path $workspace "src-tauri"))
if (-not $resolvedCargoOutput.StartsWith("$resolvedTauriRoot\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing unexpected Cargo SBOM path: $resolvedCargoOutput"
}
Copy-Item -LiteralPath $resolvedCargoOutput -Destination $rustOutput -Force
Remove-Item -LiteralPath $resolvedCargoOutput -Force

foreach ($path in @(
    (Join-Path $artifacts "sbom-python.json"),
    (Join-Path $artifacts "sbom-node.json"),
    (Join-Path $artifacts "sbom-rust.json")
)) {
    $document = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    if (-not $document.bomFormat -or $document.bomFormat -ne "CycloneDX") {
        throw "Invalid CycloneDX SBOM: $path"
    }
}

Write-Host "Python, Node, and Rust CycloneDX SBOMs are ready under artifacts/."
