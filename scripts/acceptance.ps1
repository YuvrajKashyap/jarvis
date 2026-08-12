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
        throw "Acceptance command failed with exit code ${LASTEXITCODE}: $Command $($Arguments -join ' ')"
    }
}

function Invoke-Recorded {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Command,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][System.Collections.Generic.List[string]]$Failures
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        [void]$Failures.Add("${Label}: exit code $LASTEXITCODE")
    }
}

& (Join-Path $PSScriptRoot "verify.ps1")
$failures = [System.Collections.Generic.List[string]]::new()
Invoke-Recorded "capability-core" "uv" @(
    "run", "python", "scripts/capability_acceptance.py"
) $failures
Invoke-Recorded "recovery-core" "uv" @(
    "run", "python", "scripts/recovery_acceptance.py"
) $failures
Invoke-Recorded "model-quality" "uv" @(
    "run", "python", "scripts/evaluate_models.py"
) $failures
try {
    & (Join-Path $PSScriptRoot "package.ps1") -SkipVerify
} catch {
    [void]$failures.Add("package: $($_.Exception.Message)")
}
Invoke-Checked "uv" @("run", "--extra", "speech", "python", "scripts/preflight.py")

$reportPath = Join-Path $workspace "artifacts\preflight.json"
$report = Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
if (-not $report.product_ready -or $failures.Count -gt 0) {
    $remaining = @(
        $report.items | Where-Object { $_.status -ne "ready" } | ForEach-Object {
            "$($_.code)=$($_.status)"
        }
    )
    $failureText = if ($failures.Count -gt 0) {
        " Acceptance failures: $($failures -join '; ')."
    } else {
        ""
    }
    throw "JARVIS is not daily-ready.$failureText Remaining acceptance: $($remaining -join ', ')"
}

Write-Host "All JARVIS software, installed-product, model, physical, and soak gates passed."
