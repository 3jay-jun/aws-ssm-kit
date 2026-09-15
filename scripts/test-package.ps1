[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ZipPath,
    [switch] $AllowTestVendorPackage
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$resolved = Resolve-Path -LiteralPath $ZipPath -ErrorAction Stop
if ([IO.Path]::GetExtension($resolved.Path) -ne ".zip") {
    throw "Package smoke input must be a Portable ZIP: $($resolved.Path)"
}

$zipChecksumPath = "$($resolved.Path).sha256"
if (-not (Test-Path -LiteralPath $zipChecksumPath -PathType Leaf)) {
    throw "Portable ZIP checksum sidecar is missing: $zipChecksumPath"
}
$expectedZipHash = ((Get-Content -LiteralPath $zipChecksumPath -Raw).Trim() -split '\s+')[0]
$actualZipHash = (Get-FileHash -LiteralPath $resolved.Path -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expectedZipHash -cne $actualZipHash) {
    throw "Portable ZIP checksum mismatch."
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$testRoot = Join-Path $repositoryRoot ".test-package-$([guid]::NewGuid())"
$extractRoot = Join-Path $testRoot "압축 해제 경로"
$dataRoot = Join-Path $testRoot "사용자 데이터 경로"
$stdoutPath = Join-Path $testRoot "doctor.stdout.txt"
$stderrPath = Join-Path $testRoot "doctor.stderr.txt"
$helperStdoutPath = Join-Path $testRoot "helper.stdout.txt"
$helperStderrPath = Join-Path $testRoot "helper.stderr.txt"
New-Item -ItemType Directory -Force -Path $extractRoot, $dataRoot | Out-Null

$previousPath = $env:PATH
$previousLocalAppData = $env:LOCALAPPDATA
$previousQtPlatform = $env:QT_QPA_PLATFORM
$packageRoot = $null
$aclRestricted = $false
try {
    Expand-Archive -LiteralPath $resolved.Path -DestinationPath $extractRoot
    $roots = @(Get-ChildItem -LiteralPath $extractRoot -Directory)
    if ($roots.Count -ne 1) {
        throw "Portable ZIP must contain exactly one application directory."
    }
    $packageRoot = $roots[0].FullName
    $requiredFiles = @(
        "aws_connect.exe",
        "aws_connect_cli.exe",
        "aws_connect_session_host.exe",
        "session-manager-plugin.exe",
        "VERSION.txt",
        "build-manifest.json",
        "SHA256SUMS.txt",
        "licenses/session-manager-plugin-LICENSE",
        "licenses/session-manager-plugin-NOTICE",
        "licenses/third-party-licenses.txt"
    )
    foreach ($relative in $requiredFiles) {
        $candidate = Join-Path $packageRoot ($relative.Replace("/", [IO.Path]::DirectorySeparatorChar))
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "Portable ZIP is missing required file: $relative"
        }
    }

    $manifest = Get-Content -LiteralPath (Join-Path $packageRoot "build-manifest.json") -Raw | ConvertFrom-Json
    if ([bool]$manifest.test_only -and -not $AllowTestVendorPackage) {
        throw "TEST-ONLY package requires explicit -AllowTestVendorPackage."
    }
    if (-not [bool]$manifest.test_only -and -not [bool]$manifest.release_eligible) {
        throw "Release package manifest is not release eligible."
    }
    foreach ($record in $manifest.files) {
        $candidate = Join-Path $packageRoot ([string]$record.path).Replace("/", [IO.Path]::DirectorySeparatorChar)
        $actual = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -cne [string]$record.sha256) {
            throw "Payload checksum mismatch: $($record.path)"
        }
    }
    $sumPath = Join-Path $packageRoot "SHA256SUMS.txt"
    $sumRecords = @{}
    foreach ($line in Get-Content -LiteralPath $sumPath) {
        if ($line -notmatch '^([0-9a-f]{64})  (.+)$') {
            throw "Malformed payload checksum line: $line"
        }
        $sumRecords[$Matches[2]] = $Matches[1]
    }
    $filesBeforeSums = @(
        Get-ChildItem -LiteralPath $packageRoot -Recurse -File |
            Where-Object { $_.FullName -ne $sumPath }
    )
    if ($sumRecords.Count -ne $filesBeforeSums.Count) {
        throw "SHA256SUMS.txt does not cover every payload file exactly once."
    }
    foreach ($file in $filesBeforeSums) {
        $relative = [IO.Path]::GetRelativePath($packageRoot, $file.FullName).Replace("\", "/")
        $actual = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if (-not $sumRecords.ContainsKey($relative) -or $sumRecords[$relative] -cne $actual) {
            throw "SHA256SUMS.txt mismatch: $relative"
        }
    }

    # Remove development runtimes and AWS tools from discovery while invoking EXEs by absolute path.
    $systemRoot = [Environment]::GetFolderPath("Windows")
    $env:PATH = @(
        (Join-Path $systemRoot "System32"),
        (Join-Path $systemRoot "System32/WindowsPowerShell/v1.0"),
        (Join-Path $systemRoot "System32/Wbem")
    ) -join ";"
    foreach ($forbidden in @("python.exe", "python3.exe", "aws.exe", "gossm.exe", "session-manager-plugin.exe")) {
        if (Get-Command $forbidden -ErrorAction SilentlyContinue) {
            throw "Sanitized PATH unexpectedly exposes forbidden prerequisite: $forbidden"
        }
    }
    $env:LOCALAPPDATA = $dataRoot
    $env:QT_QPA_PLATFORM = "offscreen"

    # Files are read-only and the directory ACL grants this process tree read/execute only.
    Get-ChildItem -LiteralPath $packageRoot -Recurse -File | ForEach-Object { $_.IsReadOnly = $true }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    # Apply inheritable ACLs at the package root. Using /T with inheritance flags on
    # every file creates inherit-only ACEs and makes the EXEs non-executable.
    & icacls.exe $packageRoot /inheritance:r /grant:r "${identity}:(OI)(CI)(RX)" /grant:r "SYSTEM:(OI)(CI)(F)" /Q | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to make the extracted application directory read-only."
    }
    $aclRestricted = $true

    $before = @(Get-ChildItem -LiteralPath $packageRoot -Recurse -File | ForEach-Object FullName)
    $cli = Join-Path $packageRoot "aws_connect_cli.exe"
    $doctor = Start-Process -FilePath $cli -ArgumentList @("doctor", "--output", "json") `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -Wait -PassThru
    if ($doctor.ExitCode -ne 0) {
        throw "Packaged CLI doctor failed ($($doctor.ExitCode)): $(Get-Content -LiteralPath $stderrPath -Raw)"
    }
    $doctorPayload = Get-Content -LiteralPath $stdoutPath -Raw | ConvertFrom-Json
    if ($doctorPayload.status -ne "ready") {
        throw "Packaged CLI doctor was not ready: $(Get-Content -LiteralPath $stdoutPath -Raw)"
    }
    $secretsHelp = & $cli secrets get --help 2>&1
    if ($LASTEXITCODE -ne 0 -or ($secretsHelp | Out-String) -notmatch '--reveal') {
        throw "Packaged CLI is missing the Phase 7 secrets get contract."
    }
    $s3Help = & $cli s3 upload --help 2>&1
    if (
        $LASTEXITCODE -ne 0 -or
        ($s3Help | Out-String) -notmatch '--confirm' -or
        ($s3Help | Out-String) -notmatch '--overwrite'
    ) {
        throw "Packaged CLI is missing the Phase 8 safe S3 upload contract."
    }
    if (-not [bool]$doctorPayload.session_manager_plugin.exists) {
        throw "Packaged CLI did not discover its sibling Session Manager Plugin."
    }
    $expectedHelper = Join-Path $packageRoot "aws_connect_session_host.exe"
    if (
        [string]$doctorPayload.session_host.mode -ne "sibling_executable" -or
        -not [bool]$doctorPayload.session_host.exists -or
        -not [bool]$doctorPayload.session_host.argument_boundary_safe -or
        [IO.Path]::GetFullPath([string]$doctorPayload.session_host.path) -ne
            [IO.Path]::GetFullPath($expectedHelper)
    ) {
        throw "Frozen EC2 helper discovery or argument-boundary diagnostic failed."
    }
    if ($doctorPayload.database.migration_version -ne $doctorPayload.database.expected_migration_version) {
        throw "Packaged CLI database migration is not current."
    }
    if (-not [bool]$doctorPayload.data_protection.available) {
        throw "Packaged CLI DPAPI diagnostic failed."
    }
    $expectedDataRoot = (Join-Path $dataRoot "AWSConnect")
    if ([IO.Path]::GetFullPath([string]$doctorPayload.database.path) -ne [IO.Path]::GetFullPath((Join-Path $expectedDataRoot "aws_connect.db"))) {
        throw "Packaged CLI did not use the shared per-user database path."
    }
    if ([IO.Path]::GetFullPath([string]$doctorPayload.logging.path) -ne [IO.Path]::GetFullPath((Join-Path $expectedDataRoot "logs"))) {
        throw "Packaged CLI did not use the shared per-user log path."
    }

    # Persist a non-default setting through the packaged CLI, then reopen the
    # composition root through doctor. This proves settings live in the shared
    # per-user SQLite database instead of process-local or application-folder state.
    $sharedLogRoot = Join-Path $dataRoot "공유 로그 경로"
    $settingsOutput = & $cli settings update --log-directory $sharedLogRoot `
        --log-level WARNING --output json 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged settings update failed ($LASTEXITCODE): $($settingsOutput | Out-String)"
    }
    $settingsPayload = ($settingsOutput | Out-String) | ConvertFrom-Json
    if (
        [string]$settingsPayload.log_level -ne "WARNING" -or
        [IO.Path]::GetFullPath([string]$settingsPayload.log_directory) -ne
            [IO.Path]::GetFullPath($sharedLogRoot)
    ) {
        throw "Packaged settings update returned an unexpected projection."
    }
    $reopenedDoctorOutput = & $cli doctor --output json 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged CLI doctor failed after settings reopen: $($reopenedDoctorOutput | Out-String)"
    }
    $reopenedDoctor = ($reopenedDoctorOutput | Out-String) | ConvertFrom-Json
    if (
        [string]$reopenedDoctor.logging.level -ne "WARNING" -or
        [IO.Path]::GetFullPath([string]$reopenedDoctor.logging.path) -ne
            [IO.Path]::GetFullPath($sharedLogRoot)
    ) {
        throw "Packaged CLI did not reopen the persisted shared log settings."
    }

    # The private helper is independently executable and fails closed before any plugin launch
    # when its one-use named pipe is unavailable.
    $helper = Start-Process -FilePath (Join-Path $packageRoot "aws_connect_session_host.exe") `
        -ArgumentList @("--pipe", "missing-package-smoke-pipe", "--auth", "aW52YWxpZA==") `
        -RedirectStandardOutput $helperStdoutPath -RedirectStandardError $helperStderrPath `
        -Wait -PassThru
    if ($helper.ExitCode -ne 252) {
        throw "Packaged session host did not execute its fail-closed path: $($helper.ExitCode)"
    }

    $gui = Start-Process -FilePath (Join-Path $packageRoot "aws_connect.exe") -PassThru
    Start-Sleep -Seconds 3
    if ($gui.HasExited) {
        throw "Packaged GUI exited during startup smoke with code $($gui.ExitCode)."
    }
    Stop-Process -Id $gui.Id -Force
    $gui.WaitForExit()

    $after = @(Get-ChildItem -LiteralPath $packageRoot -Recurse -File | ForEach-Object FullName)
    if (Compare-Object $before $after) {
        throw "Packaged processes modified the read-only application directory."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $dataRoot "AWSConnect") -PathType Container)) {
        throw "Packaged GUI did not initialize the isolated LOCALAPPDATA directory."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $sharedLogRoot "aws-connect.log") -PathType Leaf)) {
        throw "Packaged applications did not use the configured shared log destination."
    }

    $secretPatterns = @(
        'AKIA[0-9A-Z]{16}',
        'ASIA[0-9A-Z]{16}',
        '(?i)aws_secret_access_key\s*[=:]\s*[^\s,;}]+',
        '(?i)TokenValue["'']?\s*[:=]\s*["'']?(?!\*{3})[^\s,"''}]+'
    )
    foreach ($file in Get-ChildItem -LiteralPath $dataRoot -Recurse -File -ErrorAction SilentlyContinue) {
        $text = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($file.FullName))
        foreach ($pattern in $secretPatterns) {
            if ($text -match $pattern) {
                throw "Potential plaintext secret found in packaged DB/log output: $($file.FullName)"
            }
        }
    }

    Write-Host "Package smoke passed: no Python/AWS CLI/gossm prerequisite, Korean+space path, read-only app directory, isolated LOCALAPPDATA, persistent shared settings, CLI doctor/Secrets/S3 contracts, GUI startup, sibling helper/plugin layout, and DB/log secret scan."
} finally {
    $env:PATH = $previousPath
    $env:LOCALAPPDATA = $previousLocalAppData
    $env:QT_QPA_PLATFORM = $previousQtPlatform
    if ($aclRestricted -and $packageRoot) {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        & icacls.exe $packageRoot /inheritance:e /grant:r "${identity}:(OI)(CI)(F)" /T /Q | Out-Null
    }
    if (Test-Path -LiteralPath $testRoot) {
        Get-ChildItem -LiteralPath $testRoot -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object { $_.IsReadOnly = $false }
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
}
