[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$vendorRoot = Join-Path $repoRoot 'vendor/rclone'
$version = '1.75.1'
$archiveName = "rclone-v$version-windows-amd64.zip"
New-Item -ItemType Directory -Force -Path $vendorRoot | Out-Null
$archive = Join-Path $vendorRoot $archiveName
$checksums = Join-Path $vendorRoot 'SHA256SUMS'
if (-not (Test-Path -LiteralPath $checksums)) {
    Invoke-WebRequest "https://downloads.rclone.org/v$version/SHA256SUMS" -OutFile $checksums
}
if (-not (Test-Path -LiteralPath $archive)) {
    Invoke-WebRequest "https://downloads.rclone.org/v$version/$archiveName" -OutFile $archive
}
$entry = @(Get-Content -LiteralPath $checksums | Where-Object {
    $_ -match ('\s+' + [regex]::Escape($archiveName) + '$')
})
if ($entry.Count -ne 1) { throw 'Missing or ambiguous rclone checksum.' }
$expected = ($entry[0] -split '\s+')[0]
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
    throw 'rclone archive checksum mismatch.'
}
$extracted = Join-Path $vendorRoot 'extracted'
Expand-Archive -LiteralPath $archive -DestinationPath $extracted -Force
$source = Join-Path $extracted "rclone-v$version-windows-amd64"
Copy-Item -LiteralPath (Join-Path $source 'rclone.exe') -Destination $vendorRoot
Copy-Item -LiteralPath (Join-Path $source 'README.txt') -Destination $vendorRoot
$binary = Join-Path $vendorRoot 'rclone.exe'
& $binary version
if ($LASTEXITCODE -ne 0) { throw 'rclone version probe failed.' }
[ordered]@{
    version = $version
    source_url = "https://downloads.rclone.org/v$version/$archiveName"
    sha256 = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
    sha256_note = 'Public upstream binary checksum, not a credential.'
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $vendorRoot 'manifest.json') -Encoding utf8
Write-Host 'rclone prepared. WinFsp Core must be installed separately with administrator consent.'
