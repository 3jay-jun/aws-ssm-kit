@echo off
setlocal

set "CONFIG=%~dp0aws_info.ini"
set "AWS_CONNECTION_MODE=ec2"

if /I "%~1"=="rds" set "AWS_CONNECTION_MODE=rds"

if not exist "%CONFIG%" (
    echo [ERROR] 설정 파일을 찾을 수 없습니다.
    echo %CONFIG%
    echo.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
"$content = Get-Content -Raw -LiteralPath '%~f0'; $marker = '###POWERSHELL_START###'; $index = $content.LastIndexOf($marker); if ($index -lt 0) { throw 'PowerShell section not found.' }; $code = $content.Substring($index + $marker.Length); $env:AWS_INFO_CONFIG = '%CONFIG%'; Invoke-Expression $code"

set "EXIT_CODE=%ERRORLEVEL%"

echo.
pause
exit /b %EXIT_CODE%

###POWERSHELL_START###

$ErrorActionPreference = "Stop"

$configPath = $env:AWS_INFO_CONFIG

function Read-Ini {
    param([string]$Path)

    $result = @{}
    $section = ""

    foreach ($line in Get-Content $Path -Encoding UTF8) {
        $line = $line.Trim()

        if (
            [string]::IsNullOrWhiteSpace($line) -or
            $line.StartsWith("#") -or
            $line.StartsWith(";")
        ) {
            continue
        }

        if ($line -match '^\[(.+)\]$') {
            $section = $Matches[1]

            if (-not $result.ContainsKey($section)) {
                $result[$section] = @{}
            }

            continue
        }

        if ($line -match '^([^=]+)=(.*)$') {
            $key = $Matches[1].Trim()
            $value = $Matches[2].Trim()

            if ($section) {
                $result[$section][$key] = $value
            }
        }
    }

    return $result
}

function Set-IniValue {
    param(
        [string]$Path,
        [string]$Section,
        [string]$Key,
        [string]$Value
    )

    $lines = [System.Collections.Generic.List[string]](
        Get-Content $Path -Encoding UTF8
    )

    $sectionStart = -1
    $nextSection = $lines.Count

    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].Trim() -eq "[$Section]") {
            $sectionStart = $i
            break
        }
    }

    if ($sectionStart -lt 0) {
        $lines.Add("")
        $lines.Add("[$Section]")
        $lines.Add("$Key=$Value")
    }
    else {
        for ($i = $sectionStart + 1; $i -lt $lines.Count; $i++) {
            if ($lines[$i].Trim() -match '^\[.+\]$') {
                $nextSection = $i
                break
            }
        }

        $found = $false

        for ($i = $sectionStart + 1; $i -lt $nextSection; $i++) {
            if ($lines[$i] -match ('^\s*' + [regex]::Escape($Key) + '\s*=')) {
                $lines[$i] = "$Key=$Value"
                $found = $true
                break
            }
        }

        if (-not $found) {
            $lines.Insert($nextSection, "$Key=$Value")
        }
    }

    Set-Content -Path $Path -Value $lines -Encoding UTF8
}

function Clear-AwsEnvironment {
    Remove-Item Env:AWS_ACCESS_KEY_ID -ErrorAction SilentlyContinue
    Remove-Item Env:AWS_SECRET_ACCESS_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:AWS_SESSION_TOKEN -ErrorAction SilentlyContinue
}

function Invoke-AwsCli {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $stdoutFile = [System.IO.Path]::GetTempFileName()
    $stderrFile = [System.IO.Path]::GetTempFileName()

    try {
        $process = Start-Process `
            -FilePath "aws" `
            -ArgumentList $Arguments `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $stdoutFile `
            -RedirectStandardError $stderrFile

        return [PSCustomObject]@{
            ExitCode = $process.ExitCode
            StdOut   = Get-Content $stdoutFile -Raw -ErrorAction SilentlyContinue
            StdErr   = Get-Content $stderrFile -Raw -ErrorAction SilentlyContinue
        }
    }
    catch {
        throw "AWS CLI 실행에 실패했습니다: $($_.Exception.Message)"
    }
    finally {
        Remove-Item $stdoutFile -ErrorAction SilentlyContinue
        Remove-Item $stderrFile -ErrorAction SilentlyContinue
    }
}

function Resolve-Port {
    [OutputType([int])]
    param(
        [AllowEmptyString()]
        [string]$Value,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $port = 0

    if (-not [int]::TryParse($Value, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw "$Name 값은 1~65535 범위의 포트 번호여야 합니다: $Value"
    }

    return $port
}

function Get-OnlineSsmTargets {
    [OutputType([object[]])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Region
    )

    $ssmResult = Invoke-AwsCli @(
        "ssm",
        "describe-instance-information",
        "--filters", "Key=PingStatus,Values=Online",
        "--region", $Region,
        "--output", "json",
        "--no-cli-pager"
    )

    if ($ssmResult.ExitCode -ne 0) {
        $ssmError = ([string]$ssmResult.StdErr).Trim()
        throw "SSM 접속 가능 인스턴스 조회에 실패했습니다.`nAWS 응답: $ssmError"
    }

    try {
        $ssmInfo = $ssmResult.StdOut | ConvertFrom-Json
    }
    catch {
        throw "SSM 인스턴스 조회 응답을 해석할 수 없습니다: $($_.Exception.Message)"
    }

    $instanceNames = @{}
    $ec2Result = Invoke-AwsCli @(
        "ec2",
        "describe-instances",
        "--filters", "Name=instance-state-name,Values=running",
        "--region", $Region,
        "--output", "json",
        "--no-cli-pager"
    )

    if ($ec2Result.ExitCode -eq 0) {
        try {
            $ec2Info = $ec2Result.StdOut | ConvertFrom-Json

            foreach ($reservation in @($ec2Info.Reservations)) {
                foreach ($instance in @($reservation.Instances)) {
                    $nameTag = @($instance.Tags | Where-Object { $_.Key -eq "Name" }) | Select-Object -First 1

                    if ($null -ne $nameTag -and -not [string]::IsNullOrWhiteSpace($nameTag.Value)) {
                        $instanceNames[$instance.InstanceId] = $nameTag.Value
                    }
                }
            }
        }
        catch {
            Write-Host "[WARN] EC2 Name 태그를 해석하지 못해 SSM 정보만 표시합니다."
        }
    }
    else {
        Write-Host "[WARN] EC2 Name 태그 조회 권한이 없어 SSM 정보만 표시합니다."
    }

    $targets = foreach ($item in @($ssmInfo.InstanceInformationList)) {
        if ($item.ResourceType -ne "EC2Instance") {
            continue
        }

        $displayName = $instanceNames[$item.InstanceId]

        if ([string]::IsNullOrWhiteSpace($displayName)) {
            $displayName = $item.ComputerName
        }

        [PSCustomObject]@{
            InstanceId = [string]$item.InstanceId
            Name       = [string]$displayName
            IPAddress  = [string]$item.IPAddress
            Platform   = [string]$item.PlatformName
        }
    }

    return @($targets | Sort-Object Name, InstanceId)
}

function Get-AvailableRdsTargets {
    [OutputType([object[]])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Region
    )

    $targets = @()
    $lookupErrors = @()
    $instanceResult = Invoke-AwsCli @(
        "rds",
        "describe-db-instances",
        "--region", $Region,
        "--output", "json",
        "--no-cli-pager"
    )

    if ($instanceResult.ExitCode -eq 0) {
        try {
            $instanceInfo = $instanceResult.StdOut | ConvertFrom-Json

            foreach ($instance in @($instanceInfo.DBInstances)) {
                if (
                    $instance.DBInstanceStatus -ne "available" -or
                    [string]::IsNullOrWhiteSpace($instance.Endpoint.Address) -or
                    $null -eq $instance.Endpoint.Port
                ) {
                    continue
                }

                $targets += [PSCustomObject]@{
                    Identifier   = [string]$instance.DBInstanceIdentifier
                    EndpointType = "Instance"
                    Engine       = [string]$instance.Engine
                    Host         = [string]$instance.Endpoint.Address
                    Port         = [int]$instance.Endpoint.Port
                }
            }
        }
        catch {
            throw "RDS 인스턴스 조회 응답을 해석할 수 없습니다: $($_.Exception.Message)"
        }
    }
    else {
        $lookupErrors += "RDS 인스턴스: $(([string]$instanceResult.StdErr).Trim())"
    }

    $clusterResult = Invoke-AwsCli @(
        "rds",
        "describe-db-clusters",
        "--region", $Region,
        "--output", "json",
        "--no-cli-pager"
    )

    if ($clusterResult.ExitCode -eq 0) {
        try {
            $clusterInfo = $clusterResult.StdOut | ConvertFrom-Json

            foreach ($cluster in @($clusterInfo.DBClusters)) {
                if ($cluster.Status -ne "available" -or $null -eq $cluster.Port) {
                    continue
                }

                if (-not [string]::IsNullOrWhiteSpace($cluster.Endpoint)) {
                    $targets += [PSCustomObject]@{
                        Identifier   = [string]$cluster.DBClusterIdentifier
                        EndpointType = "Cluster writer"
                        Engine       = [string]$cluster.Engine
                        Host         = [string]$cluster.Endpoint
                        Port         = [int]$cluster.Port
                    }
                }

                if (
                    -not [string]::IsNullOrWhiteSpace($cluster.ReaderEndpoint) -and
                    $cluster.ReaderEndpoint -ne $cluster.Endpoint
                ) {
                    $targets += [PSCustomObject]@{
                        Identifier   = [string]$cluster.DBClusterIdentifier
                        EndpointType = "Cluster reader"
                        Engine       = [string]$cluster.Engine
                        Host         = [string]$cluster.ReaderEndpoint
                        Port         = [int]$cluster.Port
                    }
                }
            }
        }
        catch {
            throw "RDS 클러스터 조회 응답을 해석할 수 없습니다: $($_.Exception.Message)"
        }
    }
    else {
        $lookupErrors += "RDS 클러스터: $(([string]$clusterResult.StdErr).Trim())"
    }

    if ($targets.Count -eq 0) {
        if ($lookupErrors.Count -gt 0) {
            throw "RDS 목록 조회에 실패했습니다.`n$($lookupErrors -join "`n")"
        }

        throw "현재 접속 가능한 RDS 인스턴스 또는 클러스터가 없습니다."
    }

    return @($targets | Sort-Object Identifier, EndpointType)
}

function Select-RdsTarget {
    [OutputType([object])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Region
    )

    $targets = @(Get-AvailableRdsTargets $Region)

    Write-Host ""
    Write-Host "[SELECT] 터널링할 RDS를 선택하세요."

    for ($index = 0; $index -lt $targets.Count; $index++) {
        $target = $targets[$index]
        $number = $index + 1
        Write-Host ("  [{0}] {1} | {2} | {3} | {4}:{5}" -f $number, $target.Identifier, $target.EndpointType, $target.Engine, $target.Host, $target.Port)
    }

    $selectionText = Read-Host "번호 입력"
    $selection = 0

    if (-not [int]::TryParse($selectionText, [ref]$selection) -or $selection -lt 1 -or $selection -gt $targets.Count) {
        throw "RDS 선택 번호가 올바르지 않습니다: $selectionText"
    }

    return $targets[$selection - 1]
}

function Select-SsmTarget {
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Region,

        [Parameter(Mandatory = $true)]
        [string]$Purpose
    )

    $targets = @(Get-OnlineSsmTargets $Region)

    if ($targets.Count -eq 0) {
        throw "현재 SSM으로 접속 가능한 온라인 EC2 인스턴스가 없습니다."
    }

    Write-Host ""
    Write-Host "[SELECT] $Purpose 대상 EC2 인스턴스를 선택하세요."

    for ($index = 0; $index -lt $targets.Count; $index++) {
        $target = $targets[$index]
        $number = $index + 1
        Write-Host ("  [{0}] {1} | {2} | {3} | {4}" -f $number, $target.Name, $target.InstanceId, $target.IPAddress, $target.Platform)
    }

    $selectionText = Read-Host "번호 입력"
    $selection = 0

    if (-not [int]::TryParse($selectionText, [ref]$selection) -or $selection -lt 1 -or $selection -gt $targets.Count) {
        throw "EC2 선택 번호가 올바르지 않습니다: $selectionText"
    }

    return $targets[$selection - 1].InstanceId
}

function Start-AwsInteractive {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    if ($null -eq (Get-Command session-manager-plugin -ErrorAction SilentlyContinue)) {
        throw "AWS Session Manager Plugin을 찾을 수 없습니다. 설치 후 다시 실행하세요."
    }

    $previousErrorActionPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = "Continue"
        & aws @Arguments
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($exitCode -ne 0) {
        throw "AWS SSM 세션 실행에 실패했습니다. ExitCode=$exitCode"
    }
}

try {
    # ============================================================
    # 설정 로딩
    # ============================================================

    $config = Read-Ini $configPath

    $region         = $config.aws.region
    $accountId      = $config.aws.account_id
    $userId         = $config.aws.user_id
    $accessKey      = $config.aws.access_key
    $secretKey      = $config.aws.secret_key
    $expirationText = $config.session.expiration
    $executionMode  = $env:AWS_CONNECTION_MODE

    if ([string]::IsNullOrWhiteSpace($executionMode)) {
        $executionMode = "ec2"
    }

    if ($executionMode -notin @("ec2", "rds")) {
        throw "지원하지 않는 실행 모드입니다: $executionMode"
    }

    if ([string]::IsNullOrWhiteSpace($region)) {
        throw "region 설정이 없습니다."
    }

    if ([string]::IsNullOrWhiteSpace($accountId)) {
        throw "account_id 설정이 없습니다."
    }

    if ([string]::IsNullOrWhiteSpace($userId)) {
        throw "user_id 설정이 없습니다."
    }

    if ([string]::IsNullOrWhiteSpace($accessKey)) {
        throw "access_key 설정이 없습니다."
    }

    if ([string]::IsNullOrWhiteSpace($secretKey)) {
        throw "secret_key 설정이 없습니다."
    }

    $expectedArn = "arn:aws:iam::${accountId}:user/${userId}"
    $mfaArn      = "arn:aws:iam::${accountId}:mfa/${userId}"

    Clear-AwsEnvironment

    # ============================================================
    # 장기 AccessKey / SecretKey 검증
    # ============================================================

    Write-Host "[CHECK] Access Key / Secret Key 인증정보 확인 중..."

    $env:AWS_ACCESS_KEY_ID     = $accessKey
    $env:AWS_SECRET_ACCESS_KEY = $secretKey
    $env:AWS_SESSION_TOKEN    = ""
    $env:AWS_DEFAULT_REGION   = $region

    $baseIdentityResult = Invoke-AwsCli @(
        "sts",
        "get-caller-identity",
        "--region", $region,
        "--output", "json",
        "--no-cli-pager"
    )

    $baseIdentityJson = $baseIdentityResult.StdOut
    $awsError = [string]$baseIdentityResult.StdErr

    if ($baseIdentityResult.ExitCode -ne 0) {
            $awsError = $awsError.Trim()

            if ($awsError -match "InvalidClientTokenId") {
                throw "Access Key가 올바르지 않거나 비활성화된 키입니다.`nAWS 응답: $awsError"
            }

            if ($awsError -match "SignatureDoesNotMatch") {
                throw "Secret Access Key가 올바르지 않습니다.`nAWS 응답: $awsError"
            }

            if ($awsError -match "ExpiredToken") {
                throw "AWS 인증 토큰이 만료되었습니다.`nAWS 응답: $awsError"
            }

            if ($awsError -match "AccessDenied") {
                throw "AWS 인증정보는 확인되었지만 STS 호출 권한이 거부되었습니다.`nAWS 응답: $awsError"
            }

            if ([string]::IsNullOrWhiteSpace($awsError)) {
                throw "Access Key / Secret Key 인증에 실패했습니다. AWS CLI ExitCode=$($baseIdentityResult.ExitCode)"
            }

            throw "Access Key / Secret Key 인증에 실패했습니다.`nAWS 응답: $awsError"
    }

    if ([string]::IsNullOrWhiteSpace($baseIdentityJson)) {
        throw "AWS 인증은 성공했지만 GetCallerIdentity 응답이 비어 있습니다."
    }

    $baseIdentity = $baseIdentityJson | ConvertFrom-Json

    if ($baseIdentity.Account -ne $accountId) {
        throw "장기 인증정보의 AWS Account 불일치. Expected=$accountId, Actual=$($baseIdentity.Account)"
    }

    if ($baseIdentity.Arn -ne $expectedArn) {
        throw "장기 인증정보의 IAM User 불일치. Expected=$expectedArn, Actual=$($baseIdentity.Arn)"
    }

    Write-Host "[OK] 장기 인증정보 확인 완료"
    Write-Host "     $($baseIdentity.Arn)"

    Clear-AwsEnvironment

    # ============================================================
    # 기존 Session Token 확인
    # ============================================================

    $sessionValid = $false
    $needRefresh = $false
    $remaining = $null

    $existingTokenResult = Invoke-AwsCli @(
        "configure",
        "get",
        "aws_session_token"
    )
    $existingToken = if ($existingTokenResult.ExitCode -eq 0) {
        $existingTokenResult.StdOut.Trim()
    }
    else {
        ""
    }

    if (-not [string]::IsNullOrWhiteSpace($existingToken)) {

        Write-Host "[CHECK] 기존 AWS Session Token 확인 중..."

        # PowerShell 5.1은 native command의 stderr를 terminating error로 바꿀 수 있으므로
        # AWS 오류를 직접 수집해 만료/불일치 세션을 정상적인 갱신 흐름으로 전환한다.
        $identityResult = Invoke-AwsCli @(
            "sts",
            "get-caller-identity",
            "--region", $region,
            "--output", "json",
            "--no-cli-pager"
        )

        if ($identityResult.ExitCode -eq 0) {
            try {
                $identity = $identityResult.StdOut | ConvertFrom-Json
            }
            catch {
                Write-Host "[WARN] 기존 Session Token의 응답을 해석할 수 없어 재발급합니다."
                $needRefresh = $true
            }

            if (-not $needRefresh -and $identity.Account -ne $accountId) {
                Write-Host "[WARN] 기존 Session Token의 AWS Account가 달라 재발급합니다."
                $needRefresh = $true
            }

            if (-not $needRefresh -and $identity.Arn -ne $expectedArn) {
                Write-Host "[WARN] 기존 Session Token의 IAM User가 달라 재발급합니다."
                $needRefresh = $true
            }

            if (-not $needRefresh) {
                $sessionValid = $true
            }

            if ($sessionValid -and -not [string]::IsNullOrWhiteSpace($expirationText)) {
                try {
                    $expiration = [DateTimeOffset]::Parse($expirationText)
                    $remaining = $expiration - [DateTimeOffset]::UtcNow

                    if ($remaining.TotalMinutes -le 30) {
                        Write-Host "[CHECK] 토큰 만료까지 약 $([Math]::Floor($remaining.TotalMinutes))분 남았습니다."
                        $needRefresh = $true
                    }
                }
                catch {
                    Write-Host "[WARN] 저장된 만료 시간을 해석할 수 없습니다."
                    $needRefresh = $true
                }
            }
            elseif ($sessionValid) {
                Write-Host "[WARN] Session Token 만료시간 정보가 없습니다."
                $needRefresh = $true
            }
        }
        else {
            $sessionError = ([string]$identityResult.StdErr).Trim()

            if ($sessionError -match "ExpiredToken") {
                Write-Host "[CHECK] 기존 Session Token이 만료되어 재발급합니다."
            }
            elseif ($sessionError -match "InvalidClientTokenId|SignatureDoesNotMatch|UnrecognizedClientException") {
                Write-Host "[CHECK] 저장된 세션 인증정보가 서로 일치하지 않아 재발급합니다."
            }
            else {
                Write-Host "[WARN] 기존 Session Token을 검증하지 못해 재발급합니다."

                if (-not [string]::IsNullOrWhiteSpace($sessionError)) {
                    Write-Host "       AWS 응답: $sessionError"
                }
            }

            $needRefresh = $true
        }
    }
    else {
        Write-Host "[CHECK] 기존 Session Token이 없습니다."
    }

    # ============================================================
    # 기존 토큰 사용 또는 신규 발급
    # ============================================================

    if ($sessionValid -and -not $needRefresh) {

        if ($null -ne $remaining) {
            $hours = [Math]::Floor($remaining.TotalHours)
            $minutes = $remaining.Minutes

            Write-Host "[OK] 기존 Session Token 사용 - 약 ${hours}시간 ${minutes}분 남음"
        }
        else {
            Write-Host "[OK] 기존 Session Token 사용"
        }
    }
    else {

        Write-Host "[AUTH] 새로운 AWS Session Token을 발급합니다."

        $mfa = Read-Host "MFA 6자리 입력"

        if ($mfa -notmatch '^[0-9]{6}$') {
            throw "MFA 코드는 6자리 숫자여야 합니다."
        }

        $env:AWS_ACCESS_KEY_ID      = $accessKey
        $env:AWS_SECRET_ACCESS_KEY  = $secretKey
        $env:AWS_SESSION_TOKEN      = ""
        $env:AWS_DEFAULT_REGION     = $region

        $sessionTokenResult = Invoke-AwsCli @(
            "sts",
            "get-session-token",
            "--serial-number", $mfaArn,
            "--token-code", $mfa,
            "--region", $region,
            "--output", "json",
            "--no-cli-pager"
        )

        if ($sessionTokenResult.ExitCode -ne 0) {
            $sessionTokenError = ([string]$sessionTokenResult.StdErr).Trim()

            if ([string]::IsNullOrWhiteSpace($sessionTokenError)) {
                throw "Session Token 발급에 실패했습니다. AWS CLI ExitCode=$($sessionTokenResult.ExitCode)"
            }

            throw "Session Token 발급에 실패했습니다.`nAWS 응답: $sessionTokenError"
        }

        $result = $sessionTokenResult.StdOut | ConvertFrom-Json

        Clear-AwsEnvironment

        # ========================================================
        # 새로운 자격증명 저장
        # ========================================================

        aws configure set aws_access_key_id $result.Credentials.AccessKeyId

        if ($LASTEXITCODE -ne 0) {
            throw "AccessKey 저장 실패"
        }

        aws configure set aws_secret_access_key $result.Credentials.SecretAccessKey

        if ($LASTEXITCODE -ne 0) {
            throw "SecretKey 저장 실패"
        }

        aws configure set aws_session_token $result.Credentials.SessionToken

        if ($LASTEXITCODE -ne 0) {
            throw "SessionToken 저장 실패"
        }

        aws configure set region $region

        if ($LASTEXITCODE -ne 0) {
            throw "Region 저장 실패"
        }

        Set-IniValue `
            $configPath `
            "session" `
            "expiration" `
            $result.Credentials.Expiration

        # ========================================================
        # 새 세션 검증
        # ========================================================

        $verifyResult = Invoke-AwsCli @(
            "sts",
            "get-caller-identity",
            "--region", $region,
            "--output", "json",
            "--no-cli-pager"
        )

        if ($verifyResult.ExitCode -ne 0) {
            $verifyError = ([string]$verifyResult.StdErr).Trim()

            if ([string]::IsNullOrWhiteSpace($verifyError)) {
                throw "새 Session Token 검증에 실패했습니다. AWS CLI ExitCode=$($verifyResult.ExitCode)"
            }

            throw "새 Session Token 검증에 실패했습니다.`nAWS 응답: $verifyError"
        }

        $verify = $verifyResult.StdOut | ConvertFrom-Json

        if (
            $verify.Account -ne $accountId -or
            $verify.Arn -ne $expectedArn
        ) {
            throw "IAM 인증정보가 예상 사용자와 다릅니다: $($verify.Arn)"
        }

        Write-Host "[OK] 새로운 Session Token 발급 완료"
        Write-Host "     사용자: $($verify.Arn)"
        Write-Host "     만료 시간: $($result.Credentials.Expiration)"
    }

    # ============================================================
    # AWS Systems Manager 세션 실행
    # ============================================================

    if ($executionMode -eq "rds") {
        $localPortText = $config.rds.local_port
        try {
            $rdsTarget = Select-RdsTarget $region
        }
        catch {
            if ($_.Exception.Message -notmatch "AccessDenied") {
                throw
            }

            Write-Host "[WARN] RDS 목록 조회 권한이 없어 fallback 접속 정보를 사용합니다."
            $fallbackHost = $config.rds.fallback_host
            $fallbackPortText = $config.rds.fallback_port

            if ([string]::IsNullOrWhiteSpace($fallbackHost)) {
                $fallbackHost = Read-Host "RDS endpoint 입력"
            }

            if ([string]::IsNullOrWhiteSpace($fallbackHost) -or $fallbackHost -match '\s') {
                throw "fallback_host가 비어 있거나 올바르지 않습니다: $fallbackHost"
            }

            if ([string]::IsNullOrWhiteSpace($fallbackPortText)) {
                $fallbackPortText = Read-Host "RDS 원격 포트 입력 (예: MySQL 3306, PostgreSQL 5432)"
            }

            $fallbackPort = Resolve-Port $fallbackPortText "fallback_port"
            $rdsTarget = [PSCustomObject]@{
                Identifier   = "fallback"
                EndpointType = "Configured endpoint"
                Engine       = "unknown"
                Host         = $fallbackHost
                Port         = $fallbackPort
            }
        }

        $rdsHost = $rdsTarget.Host
        $remotePort = Resolve-Port $rdsTarget.Port.ToString() "RDS endpoint port"

        if ([string]::IsNullOrWhiteSpace($localPortText)) {
            $localPortText = Read-Host "로컬 포트 입력 (Enter: $remotePort)"

            if ([string]::IsNullOrWhiteSpace($localPortText)) {
                $localPortText = $remotePort.ToString()
            }
        }

        $localPort = Resolve-Port $localPortText "local_port"
        $targetId = Select-SsmTarget $region "RDS 터널 중계"

        # Windows PowerShell 5.1은 native command에 JSON을 넘길 때 큰따옴표를
        # 제거할 수 있으므로 AWS CLI가 지원하는 shorthand 형식을 사용한다.
        $portForwardingParameters = "host=$rdsHost,portNumber=$remotePort,localPortNumber=$localPort"

        $sessionArguments = @(
            "ssm",
            "start-session",
            "--target", $targetId,
            "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
            "--parameters", $portForwardingParameters,
            "--region", $region,
            "--no-cli-pager"
        )

        Write-Host ""
        Write-Host "[RDS] 포트포워딩을 시작합니다."
        Write-Host "      RDS: $($rdsTarget.Identifier) ($($rdsTarget.EndpointType))"
        Write-Host "      localhost:$localPort -> ${rdsHost}:$remotePort"
        Write-Host "      중계 EC2: $targetId"
        Write-Host "      이 창을 닫으면 포트포워딩도 종료됩니다."
    }
    else {
        $targetId = Select-SsmTarget $region "EC2 접속"
        $sessionArguments = @(
            "ssm",
            "start-session",
            "--target", $targetId,
            "--region", $region,
            "--no-cli-pager"
        )

        Write-Host ""
        Write-Host "[EC2] SSM 세션을 시작합니다: $targetId"
    }

    Start-AwsInteractive $sessionArguments
}
catch {
    Write-Host ""
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Clear-AwsEnvironment
}
