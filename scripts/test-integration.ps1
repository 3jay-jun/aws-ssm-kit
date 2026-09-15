[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repoRoot
try {
    $tests = @(Get-ChildItem .\tests\integration -Recurse -Filter 'test_*.py')
    if ($tests.Count -eq 0) {
        Write-Host "No infrastructure integration tests exist yet; Phase 1 owns the first tests."
        exit 0
    }
    & uv run --no-sync pytest --basetemp .\.test-integration `
        -o cache_dir=.\.test-integration-cache .\tests\integration
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
