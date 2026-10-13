[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $metadata = (& uv run --no-sync python -c "import json; from aws_connect.infrastructure.winfsp_installer import RUNTIME_SPEC; print(json.dumps(RUNTIME_SPEC))" | Out-String)
    if ($LASTEXITCODE -ne 0) { throw 'Runtime metadata unavailable.' }
    $spec = $metadata | ConvertFrom-Json
} finally { Pop-Location }
$destination = Join-Path $repoRoot 'vendor/winfsp'
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$msi = Join-Path $destination $spec.artifact
if (-not (Test-Path -LiteralPath $msi)) { Invoke-WebRequest $spec.source_url -OutFile $msi }
if ((Get-FileHash -LiteralPath $msi -Algorithm SHA256).Hash.ToLowerInvariant() -cne $spec.sha256) {
    throw 'WinFsp checksum mismatch.'
}
$signature = Get-AuthenticodeSignature -LiteralPath $msi
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike "*$($spec.signer)*") {
    throw 'WinFsp Authenticode verification failed.'
}
$license = Join-Path $destination 'LICENSE.txt'
if (-not (Test-Path -LiteralPath $license)) { Invoke-WebRequest $spec.license_url -OutFile $license }
if ((Get-Item -LiteralPath $license).Length -eq 0) { throw 'WinFsp license missing.' }
$metadata | Set-Content -LiteralPath (Join-Path $destination 'manifest.json') -Encoding utf8
Write-Host "Verified offline WinFsp payload: $msi"
