[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required for development. Install uv and run this script again."
}

Push-Location $repoRoot
try {
    & uv python find 3.12 *> $null
    if ($LASTEXITCODE -ne 0) {
        & uv python install 3.12
        if ($LASTEXITCODE -ne 0) { throw "Python 3.12 installation failed (exit $LASTEXITCODE)." }
    }
    & uv sync --all-groups
    if ($LASTEXITCODE -ne 0) { throw "Dependency synchronization failed (exit $LASTEXITCODE)." }
    Write-Host "aws-ssm-kit development environment is ready."
}
finally {
    Pop-Location
}
