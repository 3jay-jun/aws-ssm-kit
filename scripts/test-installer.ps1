[CmdletBinding()]
param([Parameter(Mandatory)][string]$SetupPath, [ValidateSet('ready','missing')][string]$ExpectedWinFspState='ready', [switch]$ExecuteOnTestVm)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$setup=(Resolve-Path -LiteralPath $SetupPath).Path
$expected=((Get-Content -LiteralPath "$setup.sha256" -Raw).Trim() -split '\s+')[0]
if ((Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant() -cne $expected) { throw 'Setup checksum mismatch.' }
if (-not $ExecuteOnTestVm) {
    Write-Host 'Installer integrity verified. Installation/UAC/reboot tests remain pending; use -ExecuteOnTestVm only on an approved disposable VM.'
    exit 0
}
$installRoot=Join-Path $env:ProgramFiles 'AWS-SSM-KIT'
if (Test-Path -LiteralPath $installRoot) { throw 'This test requires a clean test VM, not an existing app installation.' }
$tasks=if ($ExpectedWinFspState -eq 'missing') { '/TASKS=desktopicon' } else { '/TASKS=desktopicon,winfspsetup' }
$process=Start-Process -FilePath $setup -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',$tasks) -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -notin @(0,3010)) { throw "Setup failed ($($process.ExitCode))." }
$cli=Join-Path $installRoot 'aws_connect_cli.exe'
$payload=(& $cli doctor --output json | Out-String) | ConvertFrom-Json
if (-not $payload.core_ready) { throw 'Installed app core not ready.' }
if ($ExpectedWinFspState -eq 'ready' -and -not $payload.features.s3_mount.ready) { throw 'Installed S3 runtime not ready; inspect reboot requirement.' }
if ($ExpectedWinFspState -eq 'missing' -and $payload.features.s3_mount.ready) { throw 'Expected missing WinFsp.' }
Write-Host 'Installation smoke passed. Interactive UAC, reboot, upgrade/uninstall and data preservation require recorded VM cases.'
