[CmdletBinding()]
param([Parameter(Mandatory)][string]$Directory,[Parameter(Mandatory)][string]$Commit,[Parameter(Mandatory)][string]$Ref,[Parameter(Mandatory)][string]$RunId)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$files=@(Get-ChildItem -LiteralPath $Directory -File | Where-Object {$_.Name -match '\.zip$|Setup\.exe$'})
if ($files.Count -ne 2 -or ($files.Name -match 'TEST-ONLY').Count -gt 0) { throw 'Exactly one production ZIP and Setup are required.' }
$records=@($files | ForEach-Object { [ordered]@{name=$_.Name;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()} })
[ordered]@{schema_version=1;commit=$Commit;ref=$Ref;run_id=$RunId;files=$records} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Directory 'release-candidate.json') -Encoding utf8
