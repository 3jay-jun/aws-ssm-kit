[CmdletBinding()]
param([Parameter(Mandatory)][string]$PackageDirectory, [string]$OutputDirectory, [string]$CompilerPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$packageRoot = (Resolve-Path -LiteralPath $PackageDirectory).Path
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot 'dist' }
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$outputRoot = (Resolve-Path -LiteralPath $OutputDirectory).Path
if (-not $CompilerPath) { $CompilerPath = Join-Path $repoRoot 'build/installer-tools/inno/ISCC.exe' }
if (-not (Test-Path -LiteralPath $CompilerPath)) { throw 'Run scripts/setup-installer-tools.ps1 first.' }
$manifest = Get-Content -LiteralPath (Join-Path $packageRoot 'build-manifest.json') -Raw | ConvertFrom-Json
foreach ($record in $manifest.files) {
    $file = [IO.Path]::GetFullPath((Join-Path $packageRoot $record.path))
    if (-not $file.StartsWith($packageRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Payload file outside package.' }
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -cne $record.sha256) { throw 'Payload checksum mismatch.' }
}
$version = (Get-Content -LiteralPath (Join-Path $packageRoot 'VERSION.txt') -Raw).Trim()
if ($version -cne $manifest.version -or $version -notmatch '^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$') { throw 'Version mismatch.' }
$suffix = if ($manifest.test_only) { '-TEST-ONLY' } else { '' }
$name = "aws-connect-$version$suffix-windows-x64-Setup"
& $CompilerPath "/DPackageDir=$packageRoot" "/DAppVersion=$version" "/DOutputDir=$outputRoot" "/DOutputName=$name" (Join-Path $repoRoot 'packaging/aws_connect.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer compiler failed.' }
$setup = Join-Path $outputRoot "$name.exe"
$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$setup.sha256" -Encoding ascii -Value "$hash  $name.exe"
Write-Output $setup
