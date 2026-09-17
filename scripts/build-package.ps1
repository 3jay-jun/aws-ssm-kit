[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $VendorDirectory,
    [string] $OutputDirectory,
    [string] $Version,
    [switch] $AllowTestVendor
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "package-paths.ps1")
$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = Join-Path $repositoryRoot ".uv-cache"
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repositoryRoot "dist"
}
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$vendorRoot = [IO.Path]::GetFullPath($VendorDirectory)
$manifestPath = Join-Path $vendorRoot "manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Approved Session Manager Plugin manifest is required: $manifestPath"
}

$vendor = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
$required = @(
    "schema_version", "artifact", "version", "source_url", "sha256",
    "license_file", "notice_file", "approved", "test_only"
)
foreach ($name in $required) {
    if ($null -eq $vendor.PSObject.Properties[$name]) {
        throw "Vendor manifest is missing required property '$name'."
    }
}
if ([int]$vendor.schema_version -ne 1) {
    throw "Unsupported vendor manifest schema: $($vendor.schema_version)"
}
if ([string]$vendor.artifact -ne "session-manager-plugin.exe") {
    throw "Vendor artifact must be session-manager-plugin.exe."
}
if ([string]$vendor.source_url -notmatch '^https://') {
    throw "Vendor source_url must be an HTTPS review source."
}
$isTestOnly = [bool]$vendor.test_only
if ($isTestOnly) {
    if (-not $AllowTestVendor -or [bool]$vendor.approved) {
        throw "TEST-ONLY vendor input requires -AllowTestVendor and must not be approved."
    }
} elseif (-not [bool]$vendor.approved) {
    throw "Release vendor input must carry explicit approved=true review state."
}

$pluginPath = Join-Path $vendorRoot ([string]$vendor.artifact)
$licensePath = Join-Path $vendorRoot ([string]$vendor.license_file)
$noticePath = Join-Path $vendorRoot ([string]$vendor.notice_file)
foreach ($path in @($pluginPath, $licensePath, $noticePath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required vendor input is missing: $path"
    }
    if ((Get-Item -LiteralPath $path).Length -eq 0) {
        throw "Required vendor input is empty: $path"
    }
}
$actualHash = (Get-FileHash -LiteralPath $pluginPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -cne ([string]$vendor.sha256).ToLowerInvariant()) {
    throw "Session Manager Plugin checksum mismatch. Expected $($vendor.sha256), got $actualHash."
}
if (-not $isTestOnly) {
    if ($null -eq $vendor.PSObject.Properties["signer"] -or -not [string]$vendor.signer) {
        throw "Release vendor manifest must declare the expected Authenticode signer."
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $pluginPath
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "Session Manager Plugin Authenticode signature is not valid: $($signature.Status)."
    }
    $signerSubject = [string]$signature.SignerCertificate.Subject
    if ($signerSubject -notmatch [regex]::Escape([string]$vendor.signer)) {
        throw "Session Manager Plugin signer does not match the approved manifest signer."
    }
}
$reportedVersion = (& $pluginPath --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $reportedVersion -notmatch [regex]::Escape([string]$vendor.version)) {
    throw "Session Manager Plugin did not report manifest version '$($vendor.version)'."
}

if (-not $Version) {
    $project = Get-Content -LiteralPath (Join-Path $repositoryRoot "pyproject.toml") -Raw
    $matched = [regex]::Match($project, '(?ms)^\[project\].*?^version\s*=\s*"([^\"]+)"')
    if (-not $matched.Success) {
        throw "Unable to read [project].version from pyproject.toml."
    }
    $Version = $matched.Groups[1].Value
}
if ($Version -notmatch '^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$') {
    throw "Package version must be SemVer-compatible: $Version"
}

$buildRoot = Join-Path $repositoryRoot "build/package"
$pyinstallerDist = Join-Path $buildRoot "pyinstaller-dist"
$pyinstallerWork = Join-Path $buildRoot "pyinstaller-work"
if (Test-Path -LiteralPath $buildRoot) {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $pyinstallerDist, $pyinstallerWork, $outputRoot | Out-Null

$uvExecutable = (Get-Command uv -CommandType Application -ErrorAction Stop).Source
$previousBuildPath = $env:PATH
Push-Location $repositoryRoot
try {
    # Prevent unrelated DLLs on the developer PATH from contaminating the Portable ZIP.
    $env:PATH = Get-PackageIsolatedPath
    & $uvExecutable run --no-sync pyinstaller --noconfirm --clean `
        --distpath $pyinstallerDist `
        --workpath $pyinstallerWork `
        (Join-Path $repositoryRoot "packaging/aws_connect.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
} finally {
    $env:PATH = $previousBuildPath
    Pop-Location
}

$packageRoot = Join-Path $buildRoot "aws-connect"
Copy-Item -LiteralPath (Join-Path $pyinstallerDist "aws-connect") -Destination $packageRoot -Recurse
Copy-Item -LiteralPath $pluginPath -Destination (Join-Path $packageRoot "session-manager-plugin.exe")
$licenses = New-Item -ItemType Directory -Force -Path (Join-Path $packageRoot "licenses")
Copy-Item -LiteralPath $licensePath -Destination (Join-Path $licenses "session-manager-plugin-LICENSE")
Copy-Item -LiteralPath $noticePath -Destination (Join-Path $licenses "session-manager-plugin-NOTICE")
& uv run --no-sync python (Join-Path $repositoryRoot "tools/generate_third_party_notices.py") `
    (Join-Path $licenses "third-party-licenses.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Third-party notice generation failed with exit code $LASTEXITCODE."
}
Set-Content -LiteralPath (Join-Path $packageRoot "VERSION.txt") -Encoding ascii -Value $Version

$payloadFiles = Get-ChildItem -LiteralPath $packageRoot -Recurse -File | Sort-Object FullName
$fileRecords = foreach ($file in $payloadFiles) {
    [ordered]@{
        path = ConvertTo-PackageRelativePath -PackageRoot $packageRoot -Path $file.FullName
        size = $file.Length
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$buildManifest = [ordered]@{
    schema_version = 1
    application = "aws-ssm-kit"
    version = $Version
    package_format = "pyinstaller-onedir-portable-zip"
    platform = "windows-x64"
    release_eligible = -not $isTestOnly
    test_only = $isTestOnly
    vendor = [ordered]@{
        artifact = "session-manager-plugin.exe"
        version = [string]$vendor.version
        source_url = [string]$vendor.source_url
        sha256 = $actualHash
        approved = [bool]$vendor.approved
        signer = if ($isTestOnly) { $null } else { [string]$vendor.signer }
    }
    files = @($fileRecords)
}
$buildManifest | ConvertTo-Json -Depth 6 | Set-Content `
    -LiteralPath (Join-Path $packageRoot "build-manifest.json") -Encoding utf8

$checksumFiles = Get-ChildItem -LiteralPath $packageRoot -Recurse -File | Sort-Object FullName
$checksumLines = foreach ($file in $checksumFiles) {
    $relative = ConvertTo-PackageRelativePath -PackageRoot $packageRoot -Path $file.FullName
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $relative"
}
Set-Content -LiteralPath (Join-Path $packageRoot "SHA256SUMS.txt") -Encoding ascii -Value $checksumLines

$suffix = if ($isTestOnly) { "-TEST-ONLY" } else { "" }
$zipPath = Join-Path $outputRoot "aws-connect-$Version$suffix-windows-x64.zip"
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -LiteralPath $packageRoot -DestinationPath $zipPath -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$zipPath.sha256" -Encoding ascii -Value "$zipHash  $([IO.Path]::GetFileName($zipPath))"

Write-Host "Package: $zipPath"
Write-Host "SHA256: $zipHash"
Write-Output $zipPath
