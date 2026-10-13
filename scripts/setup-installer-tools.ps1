[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$directory = Join-Path $repoRoot 'build/installer-tools'
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$setup = Join-Path $directory 'innosetup-6.7.3.exe'
if (-not (Test-Path -LiteralPath $setup)) {
    Invoke-WebRequest 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe' -OutFile $setup
}
$hash = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732' # pragma: allowlist secret -- public upstream SHA-256
if ((Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant() -cne $hash) {
    throw 'Inno Setup tool checksum mismatch.'
}
$signature = Get-AuthenticodeSignature -LiteralPath $setup
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike '*Pyrsys B.V.*') {
    throw 'Inno Setup tool signature verification failed.'
}
$compilerRoot = Join-Path $directory 'inno'
$process = Start-Process -FilePath $setup -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER',('/DIR="' + $compilerRoot + '"')) -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Inno tool preparation failed ($($process.ExitCode))." }
Write-Output (Join-Path $compilerRoot 'ISCC.exe')
