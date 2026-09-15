[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Profile,
    [string] $InstanceId,
    [switch] $Connect,
    [string] $RdsSession,
    [string] $TargetInstanceId,
    [switch] $Tunnel,
    [string] $SecretId,
    [string] $S3Bucket,
    [string] $S3Prefix = "",
    [string[]] $S3UploadFile,
    [switch] $S3Upload,
    [switch] $S3Overwrite
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($env:AWS_CONNECT_ALLOW_AWS_SMOKE -ne "1") {
    throw "AWS smoke tests are disabled. Set AWS_CONNECT_ALLOW_AWS_SMOKE=1 only for an approved least-privilege test profile."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repoRoot
try {
    & uv run aws-connect-cli auth validate --profile $Profile --output json
    if ($LASTEXITCODE -ne 0) { throw "Authentication smoke failed (exit $LASTEXITCODE)." }

    & uv run aws-connect-cli ec2 list --profile $Profile --output json
    if ($LASTEXITCODE -ne 0) { throw "EC2 list smoke failed (exit $LASTEXITCODE)." }

    if ($Connect) {
        if (-not $InstanceId) { throw "-InstanceId is required with -Connect." }
        & uv run aws-connect-cli ec2 connect $InstanceId --profile $Profile
        if ($LASTEXITCODE -ne 0) { throw "EC2 connect smoke failed (exit $LASTEXITCODE)." }
    }
    elseif ($InstanceId) {
        Write-Host "InstanceId supplied without -Connect; no interactive session was started."
    }

    if ($Tunnel) {
        if (-not $RdsSession) { throw "-RdsSession is required with -Tunnel." }
        $arguments = @(
            "run", "aws-connect-cli", "rds", "tunnel", "start", $RdsSession,
            "--profile", $Profile
        )
        if ($TargetInstanceId) {
            $arguments += @("--target-instance-id", $TargetInstanceId)
        }
        & uv @arguments
        if ($LASTEXITCODE -ne 0) { throw "RDS tunnel smoke failed (exit $LASTEXITCODE)." }
    }
    elseif ($RdsSession) {
        & uv run aws-connect-cli rds session show $RdsSession --profile $Profile --output json
        if ($LASTEXITCODE -ne 0) { throw "RDS saved session smoke failed (exit $LASTEXITCODE)." }
        Write-Host "RdsSession supplied without -Tunnel; no port-forwarding session was started."
    }

    if ($SecretId) {
        & uv run aws-connect-cli secrets get $SecretId --profile $Profile --output json
        if ($LASTEXITCODE -ne 0) { throw "Secrets Manager direct-get smoke failed (exit $LASTEXITCODE)." }
        Write-Host "Secret values above remain masked; this smoke never passes --reveal."
    }

    if ($S3Bucket) {
        & uv run aws-connect-cli s3 list --bucket $S3Bucket --prefix $S3Prefix `
            --profile $Profile --output json
        if ($LASTEXITCODE -ne 0) { throw "S3 direct bucket/prefix list smoke failed (exit $LASTEXITCODE)." }
    }
    if ($S3UploadFile) {
        if (-not $S3Bucket) { throw "-S3Bucket is required with -S3UploadFile." }
        $s3Arguments = @(
            "run", "aws-connect-cli", "s3", "upload"
        ) + $S3UploadFile + @(
            "--bucket", $S3Bucket, "--prefix", $S3Prefix, "--profile", $Profile,
            "--output", "json"
        )
        if ($S3Upload) { $s3Arguments += "--confirm" }
        if ($S3Overwrite) { $s3Arguments += "--overwrite" }
        & uv @s3Arguments
        if ($LASTEXITCODE -ne 0) { throw "S3 upload preview/smoke failed (exit $LASTEXITCODE)." }
        if (-not $S3Upload) {
            Write-Host "S3 upload was preview-only; pass -S3Upload to explicitly perform PutObject."
        }
    }
}
finally {
    Pop-Location
}
