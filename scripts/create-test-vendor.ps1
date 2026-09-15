[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# PowerShell 7 removed executable output from Add-Type. Windows PowerShell's .NET
# Framework compiler is available on every supported Windows target and keeps this
# test fixture independent from a .NET SDK or system Python installation.
if ($PSVersionTable.PSEdition -eq "Core") {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath `
        -OutputDirectory $OutputDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Windows PowerShell failed to build the TEST-ONLY plugin fixture."
    }
    return
}

$output = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $output | Out-Null
$binary = Join-Path $output "session-manager-plugin.exe"
$source = @'
using System;
public static class TestOnlySessionManagerPlugin {
    public static int Main(string[] args) {
        if (args.Length == 1 && args[0] == "--version") {
            Console.WriteLine("session-manager-plugin 1.2.707.0 TEST-ONLY");
            return 0;
        }
        Console.Error.WriteLine("TEST-ONLY Session Manager Plugin stub; no AWS session was started.");
        return 64;
    }
}
'@

if (Test-Path -LiteralPath $binary) {
    Remove-Item -LiteralPath $binary -Force
}
Add-Type -TypeDefinition $source -OutputAssembly $binary -OutputType ConsoleApplication
Set-Content -LiteralPath (Join-Path $output "LICENSE") -Encoding utf8 -Value @(
    "TEST-ONLY synthetic binary generated for AWS Connect package smoke tests."
    "This is not the AWS Session Manager Plugin and must never be released."
)
Set-Content -LiteralPath (Join-Path $output "NOTICE") -Encoding utf8 -Value @(
    "TEST-ONLY: this directory contains no AWS-distributed executable."
)
$sha256 = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest = [ordered]@{
    schema_version = 1
    artifact = "session-manager-plugin.exe"
    version = "1.2.707.0"
    source_url = "https://invalid.example.test/aws-connect/test-only-stub"
    sha256 = $sha256
    license_file = "LICENSE"
    notice_file = "NOTICE"
    approved = $false
    test_only = $true
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output "manifest.json") -Encoding utf8
Write-Host "Created TEST-ONLY vendor input at $output"
