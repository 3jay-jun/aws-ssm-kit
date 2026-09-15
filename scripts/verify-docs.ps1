[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$generated = Join-Path $repoRoot "docs\generated\db-schema.md"
$temporaryDirectory = Join-Path $repoRoot ".test-docs"
New-Item -ItemType Directory -Path $temporaryDirectory -Force | Out-Null
$temporary = Join-Path $temporaryDirectory ("aws-connect-schema-" + [guid]::NewGuid() + ".md")

Push-Location $repoRoot
try {
    & uv run --no-sync python .\tools\verify_docs.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & .\scripts\generate-docs.ps1 -OutputPath $temporary
    if ((Get-Content -Raw -LiteralPath $generated) -ne (Get-Content -Raw -LiteralPath $temporary)) {
        Write-Error "Generated documentation drift detected. Run scripts/generate-docs.ps1."
        exit 1
    }
    Write-Host "Generated documentation is current."
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
}
