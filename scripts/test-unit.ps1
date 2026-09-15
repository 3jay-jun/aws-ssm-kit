[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repoRoot
try {
    & uv run --no-sync pytest --basetemp .\.test-unit -o cache_dir=.\.test-unit-cache `
        .\tests\unit .\tests\adapter .\tests\architecture
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
