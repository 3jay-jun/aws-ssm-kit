[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = Join-Path $repoRoot ".uv-cache"
}
$runId = [guid]::NewGuid()
$pytestBase = Join-Path $repoRoot ".test-check-$runId"
$pytestCache = Join-Path $repoRoot ".test-cache-$runId"

function Invoke-Check {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [scriptblock] $Command
    )
    Write-Host "==> $Name"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed (exit $LASTEXITCODE)."
    }
}

Push-Location $repoRoot
try {
    Invoke-Check "documentation" { & .\scripts\verify-docs.ps1 }
    # Restrict Ruff to source-controlled Python roots. Packaging/read-only smoke tests
    # intentionally create ACL-restricted ignored directories, and Ruff may panic while
    # traversing those even though their names match .gitignore.
    Invoke-Check "format" { & uv run --no-sync ruff format --check src tests tools }
    Invoke-Check "lint" { & uv run --no-sync ruff check src tests tools }
    Invoke-Check "architecture policy" { & uv run --no-sync python .\tools\architecture_check.py }
    Invoke-Check "import boundaries" { & uv run --no-sync lint-imports }
    Invoke-Check "types" { & uv run --no-sync mypy }
    Invoke-Check "tests" {
        & uv run --no-sync pytest --basetemp $pytestBase -o "cache_dir=$pytestCache"
    }
    Invoke-Check "critical branch coverage" {
        & uv run --no-sync python .\tools\verify_critical_coverage.py .\coverage.xml
    }
    Invoke-Check "secret scan" {
        # Scan the same source-controlled file set in every environment without
        # depending on a runner-provided ripgrep installation.
        $files = @(& git ls-files -- ':!ref/**' ':!uv.lock')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $scan = (& uv run --no-sync detect-secrets scan @files | Out-String)
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $result = $scan | ConvertFrom-Json
        if (($result.results.PSObject.Properties | Measure-Object).Count -ne 0) {
            Write-Error "Potential secrets detected. Run detect-secrets locally and inspect the reported files."
            exit 1
        }
        Write-Host "No potential secrets detected in repository files."
    }
    Invoke-Check "Bandit" { & uv run --no-sync bandit -q -r .\src }
    Invoke-Check "dependency audit" { & uv run --no-sync pip-audit }
    Write-Host "All local checks passed."
}
finally {
    Pop-Location
    foreach ($temporaryPath in @($pytestBase, $pytestCache)) {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Recurse -Force
        }
    }
}
