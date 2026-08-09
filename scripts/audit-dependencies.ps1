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

# These are narrow, reviewed exceptions with no currently compatible fixed release:
# - cryptography: JARVIS never decrypts attacker-supplied PKCS#7/S-MIME payloads.
# - Starlette: TrustedHostMiddleware is mandatory; JARVIS does not authorize from request.url,
#   parse forms, or use HTTPEndpoint. The UI directory is fixed and local.
# - Torch: only approved local Silero assets are loaded; no untrusted pt2/JIT artifacts execute.
$pythonExceptions = @(
    "PYSEC-2026-3552",
    "PYSEC-2026-161",
    "PYSEC-2026-248",
    "PYSEC-2026-249",
    "PYSEC-2026-2281",
    "PYSEC-2026-2280",
    "PYSEC-2026-139",
    "PYSEC-2025-194"
)
$pythonArguments = @("run", "pip-audit")
foreach ($advisory in $pythonExceptions) {
    $pythonArguments += @("--ignore-vuln", $advisory)
}
Invoke-Checked "uv" $pythonArguments
Invoke-Checked "pnpm" @("audit", "--prod", "--audit-level", "high")

# quick-xml is reachable only through Tauri's plist build/OS integration path. JARVIS never
# accepts or parses an untrusted plist/XML document; plist has not yet published a compatible
# quick-xml upgrade. New Rust advisories still fail this gate.
Invoke-Checked "cargo" @(
    "audit", "--file", "src-tauri/Cargo.lock",
    "--ignore", "RUSTSEC-2026-0194",
    "--ignore", "RUSTSEC-2026-0195"
)

Write-Host "Dependency audits passed with only the documented narrow exceptions."
