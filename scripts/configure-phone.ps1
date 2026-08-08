$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$tailscale = "C:\Program Files\Tailscale\tailscale.exe"
if (-not (Test-Path -LiteralPath $tailscale -PathType Leaf)) {
    throw "Tailscale is not installed. Run pnpm bootstrap first."
}

$statusJson = & $tailscale status --json
if ($LASTEXITCODE -ne 0) {
    throw "Could not read Tailscale status."
}
$status = $statusJson | ConvertFrom-Json
if ($status.BackendState -ne "Running" -or -not $status.Self.Online) {
    throw "Tailscale is not signed in and online. Sign in to Tailscale, then run this command again."
}

$dnsName = [string]$status.Self.DNSName
if ([string]::IsNullOrWhiteSpace($dnsName) -or -not $dnsName.EndsWith(".ts.net.")) {
    throw "Tailscale did not provide the expected private HTTPS DNS name."
}
$phoneOrigin = "https://$($dnsName.TrimEnd('.'))"

& $tailscale serve --bg --yes http://127.0.0.1:7331
if ($LASTEXITCODE -ne 0) {
    throw "Tailscale Serve could not expose the local JARVIS core."
}

$dataDirectory = Join-Path $env:LOCALAPPDATA "JARVIS"
$resolvedDataDirectory = [System.IO.Path]::GetFullPath($dataDirectory)
$expectedRoot = [System.IO.Path]::GetFullPath($env:LOCALAPPDATA)
if (-not $resolvedDataDirectory.StartsWith($expectedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to write JARVIS configuration outside local application data."
}
New-Item -ItemType Directory -Path $resolvedDataDirectory -Force | Out-Null
$configPath = Join-Path $resolvedDataDirectory "config.json"
$temporaryPath = Join-Path $resolvedDataDirectory "config.$([guid]::NewGuid().ToString('N')).tmp"
$configJson = @{ phone_base_url = $phoneOrigin } | ConvertTo-Json
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($temporaryPath, $configJson, $utf8WithoutBom)
Move-Item -LiteralPath $temporaryPath -Destination $configPath -Force

Write-Host "JARVIS phone access configured at $phoneOrigin"
