# AWS Connect 구현 사전 계획

## 1. 목적

이 문서는 [`aws-connect.md`](../product-specs/aws-connect.md)의 요구사항을 구현하기 전에 기술 방향, 책임 경계, 데이터 구조, 단계별 검증 기준을 확정하기 위한 사전 계획이다. 아직 구현 코드를 확정하지 않으며, 설계 검토 후 실제 작업 계획으로 전환한다.

## 2. 현재 상태

현재 작업공간에는 다음 BAT 기반 구현이 있다.

- `ec2_connect.bat`: EC2 SSM 접속 진입점
- `rds_tunnel.bat`: RDS 터널링 진입점
- `_aws_common.bat`: INI 파싱, AWS 인증, MFA 토큰, EC2/RDS 조회 및 SSM 실행
- `aws_info.ini`: AWS 인증정보 및 RDS 설정

현재 확인된 제약은 다음과 같다.

- 현재 IAM 사용자에는 `rds:DescribeDBInstances`, `rds:DescribeDBClusters` 권한이 없다.
- 현재 IAM 사용자에는 `secretsmanager:ListSecrets` 권한이 없다.
- 따라서 RDS 저장 세션은 자동 RDS 탐색보다 우선하는 핵심 기능이다.
- 기존 INI의 평문 Access Key와 Secret Key는 애플리케이션 전환 시 제거해야 한다.
- gossm 의존성은 이미 제거 방향으로 결정했다.

## 3. 권장 기술 선택

### 3.1 애플리케이션

- 언어: Python 3.12 계열
- AWS SDK: boto3/botocore
- 로컬 DB: Python 표준 `sqlite3`
- 암호화: Windows DPAPI
- 패키징: PyInstaller
- 배포: Windows x64 Portable ZIP
- SSM 데이터 채널: 공식 Session Manager Plugin 바이너리
- CLI 명령 파서: Python 표준 `argparse`

### 3.2 선택 근거와 대안

- **boto3 직접 사용 — 추천:** AWS CLI 설치가 필요 없고 오류 코드와 응답 객체를 구조적으로 처리할 수 있다.
- **AWS CLI 동봉:** 기존 명령 재사용은 쉽지만 배포 크기와 업데이트 범위가 커지고 CLI 문자열 파싱 문제가 남는다.

- **DPAPI + SQLite — 추천:** 별도 마스터 비밀번호 없이 현재 Windows 사용자에게 자격증명을 묶을 수 있다.
- **AES-GCM + 마스터 비밀번호:** PC 간 이동은 가능하지만 키 파생·복구·비밀번호 UX가 추가된다.

- **Portable ZIP — 추천:** 설치 권한이 없어도 사용할 수 있고 플러그인과 라이선스 파일을 명확히 관리한다.
- **단일 one-file EXE:** 배포 파일은 하나지만 플러그인 임시 추출, 시작 지연, 백신 오탐 가능성이 증가한다.

- **표준 `argparse` — 추천:** 추가 런타임 의존성 없이 하위 명령과 종료 코드를 구성할 수 있고 PyInstaller 패키징 범위가 작다.
- **Typer:** 타입 기반 명령 정의와 도움말은 편리하지만 Click 계열 의존성과 패키징·버전 관리 범위가 늘어난다.

### 3.3 UI 방식

- 하이브리드 Windows GUI를 사용한다.
- 인증, 프로필, RDS 터널, Secrets Manager, S3와 로그는 앱 내부 화면에서 관리한다.
- EC2 대화형 셸은 별도 Windows Terminal 프로세스로 실행한다.
- 공식 UI 설계 기준은 [`docs/DESIGN.md`](../DESIGN.md)이며 시각적 기준은 [`aws-connect-ui-mockup.html`](aws-connect-ui-mockup.html)이다.

## 4. 아키텍처 원칙

- 도메인 규칙과 AWS/SQLite/Windows API 구현을 분리한다.
- AWS 오류 메시지를 UI 계층에서 직접 파싱하지 않는다.
- 자격증명 암복호화는 하나의 서비스에서만 수행한다.
- 세션 만료 판정 기준은 하나의 정책 객체에만 둔다.
- SQLite 스키마 변경은 버전 기반 migration으로만 수행한다.
- 초기 기능 수에 비해 과도한 프레임워크나 Repository 계층을 만들지 않는다.
- 테스트 가능한 계산과 검증은 순수 함수로 유지한다.
- CLI와 GUI는 비즈니스 로직을 포함하지 않는 입력·출력 어댑터로 제한한다.
- 모든 실행 경로는 동일한 Application Service와 도메인 정책을 사용한다.
- 의존성 생성과 연결은 하나의 composition root에서 수행한다.
- Application과 Domain 계층에서는 `print()`, `input()`, `sys.exit()`, GUI 위젯·모달, CLI 문자열 포맷팅을 사용하지 않는다.
- 사용자 입력이 추가로 필요한 상황은 타입이 정의된 중간 상태로 반환하며 presentation 계층이 입력을 수집한다.

```text
CLI command ─┐
             ├─ Application Service ─ Domain
GUI action ──┘          │
                        └─ Infrastructure ports ─ AWS / SQLite / DPAPI / Plugin
```

## 5. 제안 모듈 구조

```text
src/aws_connect/
├─ bootstrap.py
├─ gui_main.py
├─ cli_main.py
├─ domain/
│  ├─ aws_profile.py
│  ├─ session_credentials.py
│  ├─ tunnel_session.py
│  ├─ errors.py
│  └─ validation.py
├─ application/
│  ├─ dto.py
│  ├─ operations.py
│  ├─ ports/
│  │  ├─ profile_store.py
│  │  ├─ credential_cipher.py
│  │  ├─ aws_gateways.py
│  │  └─ process_gateway.py
│  ├─ profile_service.py
│  ├─ authentication_service.py
│  ├─ session_guard.py
│  ├─ operation_coordinator.py
│  ├─ ec2_connection_service.py
│  ├─ tunnel_service.py
│  ├─ secrets_service.py
│  └─ s3_service.py
├─ infrastructure/
│  ├─ aws_client_factory.py
│  ├─ boto3_sts_gateway.py
│  ├─ boto3_ec2_gateway.py
│  ├─ boto3_ssm_gateway.py
│  ├─ boto3_rds_gateway.py
│  ├─ boto3_secrets_gateway.py
│  ├─ boto3_s3_gateway.py
│  ├─ session_manager_plugin.py
│  ├─ sqlite_store.py
│  ├─ migrations.py
│  ├─ dpapi_cipher.py
│  └─ logging_config.py
└─ presentation/
   ├─ cli/
   │  ├─ parser.py
   │  ├─ output.py
   │  └─ commands/
   │     ├─ profile.py
   │     ├─ auth.py
   │     ├─ ec2.py
   │     ├─ rds.py
   │     ├─ secrets.py
   │     └─ s3.py
   └─ gui/
      ├─ app.py
      ├─ profile_views.py
      ├─ ec2_views.py
      ├─ tunnel_views.py
      ├─ secrets_views.py
      └─ s3_views.py

tests/
├─ unit/domain/
├─ unit/application/
├─ adapter/cli/
├─ adapter/gui/
├─ integration/infrastructure/
└─ e2e/
```

### 5.1 공통 Application 기능

다음 기능은 모든 유스케이스가 재사용하며 각각 한 곳에서만 구현한다.

- `ProfileService`: 프로필 CRUD, 기본 프로필 선택과 프로필 전환
- `AuthenticationService`: 장기 자격증명 검증과 MFA 토큰 발급
- `SessionGuard`: 토큰 재사용, 만료·불일치 판정과 작업 전 재검증
- AWS client 생성: 선택 프로필과 검증된 세션으로 서비스별 client 생성
- 공통 검증과 오류 모델: Account, IAM, ARN, host, port와 AWS 오류 변환
- 보안 저장과 로그 마스킹: DPAPI, SQLite, 민감정보 필터
- 프로세스 수명주기: Session Manager Plugin 실행, 추적과 종료

`common.py`와 같은 포괄적인 모듈 하나에 모으지 않고 변경 이유와 도메인 역할에 따라 위 서비스와 포트로 분리한다.

### 5.2 기능별 Application 서비스

| 기능 | Application 책임 | 프레젠테이션에서 담당할 내용 |
|---|---|---|
| 프로필 | 등록·수정·삭제·선택·검증 | CLI 입력/표, GUI 폼/모달 |
| 인증 | 상태 조회·MFA 갱신·재사용 | MFA 입력, 상태와 남은 시간 표시 |
| EC2 | SSM 대상 조회·선택 대상 검증·세션 시작 | CLI 목록/선택, GUI 테이블/외부 터미널 안내 |
| RDS | 저장 세션 CRUD·포트 검사·터널 시작/종료 | CLI 명령, GUI 편집기/실행 상태 |
| Secrets | Secret 조회·JSON 해석·민감 필드 마스킹 | CLI/GUI 결과 표현과 명시적 표시 동작 |
| S3 | 위치 CRUD·객체 조회·업로드·진행 이벤트 | CLI 진행 출력, GUI 진행률과 파일 선택 |

Application Service는 문자열로 꾸민 화면 메시지나 GUI 위젯을 반환하지 않고 타입이 정의된 DTO와 애플리케이션 오류를 반환한다.

### 5.3 의존성 및 호출 규칙

- `domain`은 다른 계층을 import하지 않는다.
- `application`은 `domain`과 application port만 참조한다.
- `infrastructure`는 application port를 구현한다.
- `presentation/cli`와 `presentation/gui`는 Application Service만 호출한다.
- CLI command가 다른 CLI command를 호출하거나 GUI가 CLI 프로세스를 실행해 기능을 사용하는 구조는 금지한다.
- `bootstrap.py`만 실제 SQLite, boto3, DPAPI와 Plugin 구현을 조립한다.
- 동일한 입력은 CLI와 GUI에서 같은 DTO로 변환하며 같은 오류 유형을 받는다.

### 5.4 CLI 명령 계약

CLI는 하나의 `aws_connect_cli.exe`와 기능별 하위 명령으로 제공한다. 개발환경에서는 `python -m aws_connect.cli_main`으로 동일하게 실행한다.

```text
aws_connect_cli profile list|show|create|update|delete|use
aws_connect_cli auth status|validate|refresh
aws_connect_cli ec2 list|connect
aws_connect_cli rds session list|show|create|update|clone|delete
aws_connect_cli rds tunnel start
aws_connect_cli secrets get
aws_connect_cli s3 list|upload
aws_connect_cli doctor
```

- 모든 명령은 명시적인 `--profile`을 받을 수 있고 생략 시 기본 프로필을 사용한다.
- 기본 출력은 사용자용 표 또는 문장이고 `--output json`은 테스트 가능한 안정적 구조를 사용한다.
- 민감정보와 MFA 코드는 명령행 옵션으로 받지 않고 마스킹 입력 또는 표준입력으로만 받는다.
- JSON 출력의 필드명과 CLI 종료 코드는 호환성 계약으로 취급한다.
- `doctor`는 DB, 로그 경로, Plugin, 포트와 AWS 인증 상태를 비파괴적으로 점검한다.

### 5.5 CLI 종료 코드

| 코드 | 의미 |
|---:|---|
| `0` | 성공 |
| `2` | 명령 또는 입력값 오류 |
| `10` | 프로필·설정 오류 |
| `20` | 인증 또는 MFA 오류 |
| `30` | AWS 권한 부족 |
| `40` | AWS·네트워크 오류 |
| `50` | 대상 또는 로컬 리소스 오류 |
| `60` | Plugin·외부 프로세스 오류 |
| `70` | 예상하지 못한 내부 오류 |

### 5.6 공통 작업 상태와 비동기 계약

GUI 연결 시 Application Service의 반환형을 다시 변경하지 않도록 장시간 작업은 처음부터 다음 공통 계약을 사용한다.

```text
OperationId
OperationState = PENDING | MFA_REQUIRED | RUNNING | SUCCEEDED | FAILED | CANCELLED
ProgressEvent(operation_id, phase, completed, total, message_code, target?)
CancellationToken(is_cancellation_requested)
OperationResult[T](operation_id, state, value, error)
ExternalSessionHandle(operation_id, process_id, ssm_session_id, state)
TunnelHandle(operation_id, process_id, ssm_session_id, local_host, local_port, state)
MfaChallenge(operation_id, profile_id, device_arn, expires_at)
```

- `ProgressEvent`의 `message_code`는 표현 문자열이 아니라 안정적인 코드이며 CLI와 GUI가 각각 문구로 변환한다.
- `completed`와 `total`을 알 수 없는 작업은 단계 기반 `phase`만 제공할 수 있다.
- `target`은 다중 항목 작업의 현재 대상을 식별하는 선택 필드이며 S3는 최종 `s3://` URI를
  사용해 CLI와 GUI가 같은 파일별 진행 상태를 표시한다.
- 취소는 요청을 의미하며 실제 종료와 정리가 끝난 뒤에만 `CANCELLED`로 전환한다.
- 취소할 수 없는 구간에서는 취소 가능 여부를 상태로 알리고 버튼 또는 키 입력을 비활성화한다.
- 작업 상태 변경은 presentation 계층이 구독할 수 있지만 GUI 위젯이나 콘솔 출력 객체를 Application에 전달하지 않는다.
- 작업 결과와 오류는 동일한 `OperationId`와 correlation ID로 로그에서 연결한다.

### 5.7 MFA 중간 상태와 작업 재개

MFA 필요는 최종 실패가 아니라 복구 가능한 `MFA_REQUIRED` 상태로 처리한다.

```text
기능 요청 DTO
→ AuthenticatedOperationCoordinator.start(profile, request)
├─ 유효 세션: RUNNING → 기능 실행
└─ MFA 필요: MfaChallenge 반환
   → presentation이 MFA 코드 수집
   → AuthenticatedOperationCoordinator.resume(operation_id, mfa_code)
   → 세션 검증 성공
   → 보관된 원래 요청 DTO로 기능 자동 재개
```

- 원래 요청 DTO는 현재 프로세스 메모리에만 보관하고 SQLite와 로그에 직렬화하지 않는다.
- 원 요청 callable과 하위 MFA challenge는 공통 상한 128개를 공유한다. 만료 항목을 먼저
  제거한 뒤에도 가득 차 있으면 기존 요청을 제거하지 않고 새 요청만
  `auth.operation.capacity_exceeded`로 거부한다.
- `resume()`은 동일 작업에서 한 번만 토큰 재발급을 시도한다.
- MFA가 취소되거나 challenge가 만료되면 원래 작업은 `CANCELLED` 또는 `FAILED`로 끝난다.
- 재개 가능한 작업이 아닌 경우 새 요청으로 다시 실행하도록 명확한 오류를 반환한다.
- 재개 과정에서 업로드, 터널 생성 등 외부 사이드이펙트가 중복 실행되지 않도록 인증 완료 전에는 기능 실행을 시작하지 않는다.
- CLI는 MFA를 받을 대화형 TTY 또는 명시적 `--mfa-stdin`이 없으면 pending 상태를 지우고
  종료 코드 `20`을 반환한다. GUI는 GUI thread에서 코드만 수집하고 AWS 요청과 원 작업
  재개는 worker에서 수행한다.

### 5.8 프로세스 핸들과 소유권

- CLI의 EC2 접속은 현재 콘솔에 연결하고 프로세스 종료까지 대기한다.
- GUI의 EC2 접속은 별도 Windows Terminal을 열고 `ExternalSessionHandle`로 시작·실행·종료·실패 상태를 추적한다.
- 성공적으로 열린 EC2 외부 터미널은 사용자 소유로 전환하며 GUI 종료만으로 강제 종료하지 않는다.
- RDS 터널은 생성한 GUI 또는 CLI 프로세스가 소유한다.
- GUI가 소유한 RDS 터널은 GUI 종료 시 정리한다.
- CLI의 `rds tunnel start`는 기본적으로 foreground에서 실행하고 `Ctrl+C`를 취소 요청으로 처리한 뒤 터널을 정리한다.
- 별도 프로세스에서 시작한 터널을 `status` 또는 `stop`으로 제어하는 detached 모드는 MVP에서 제외한다.
- DB에는 재사용할 터널 설정만 저장하고 PID, 세션 토큰과 실행 중 handle을 영속 상태의 진실로 사용하지 않는다.

이에 따라 MVP CLI 명령은 다음과 같이 제한한다.

```text
aws_connect_cli rds session list|show|create|update|clone|delete
aws_connect_cli rds tunnel start
```

### 5.9 중앙 오류와 프레젠테이션 매핑

Application 오류 유형과 의미는 `domain/errors.py` 한 곳에서 정의하고, CLI와 GUI 변환은 각 presentation의 중앙 mapper 한 곳에서 처리한다.

| Application 오류·상태 | CLI 표현 | GUI 표현 | 기본 복구 동작 |
|---|---|---|---|
| `MFA_REQUIRED` | 대화형 MFA 입력, 비대화형은 JSON 상태와 코드 `20` | MFA 모달 | 성공 후 원래 작업 재개 |
| `ConfigurationError` | stderr 또는 JSON 오류, 코드 `10` | 해당 설정 필드 강조 | 설정 화면 이동 |
| `CredentialValidationError` | stderr 또는 JSON 오류, 코드 `20` | 프로필 인증 오류 | 프로필 수정·변경 |
| `MfaValidationError` | stderr 또는 JSON 오류, 코드 `20` | MFA 코드 오류 | 허용 횟수 내 재입력 |
| `AwsPermissionError` | stderr 또는 JSON 오류, 코드 `30` | 필요한 IAM Action 안내 | 다른 프로필 또는 권한 요청 |
| `AwsNetworkError` | stderr 또는 JSON 오류, 코드 `40` | 재시도 가능한 알림 | 재시도 |
| `TargetNotConnectedError` | stderr 또는 JSON 오류, 코드 `50` | 대상 상태 안내 | 목록 새로고침 |
| `PortAlreadyInUseError` | stderr 또는 JSON 오류, 코드 `50` | Local Port 강조 | 다른 포트 선택 |
| `PluginExecutionError` | stderr 또는 JSON 오류, 코드 `60` | Plugin 진단 안내 | `doctor` 실행·재시도 |
| `SecretAccessError` | stderr 또는 JSON 오류, 코드 `50` | Secret 이름·상태 안내 | 입력 확인 |
| `S3TransferError` | stderr 또는 JSON 오류, 코드 `40` | 파일별 실패와 재시도 | 재시도 또는 취소 |
| `DataProtectionError` | stderr 또는 JSON 오류, 코드 `10` | 자격증명 복호화 오류 | 프로필 재등록 |
| 예상하지 못한 오류 | stderr 또는 JSON 오류, 코드 `70` | correlation ID와 일반 오류 | 로그 확인 |

- AWS `AccessDenied`는 기능별 오류보다 먼저 `AwsPermissionError`로 변환한다.
- 오류 객체는 사용자 메시지 코드, 기술 원인, AWS 서비스·Action, 재시도 가능 여부와 correlation ID를 가진다.
- CLI mapper만 stderr·JSON·종료 코드를 생성하고 GUI mapper만 모달·토스트·필드 오류와 후속 동작을 생성한다.
- 오류를 문자열로 변환한 뒤 다른 계층에서 다시 파싱하지 않는다.

## 6. 주요 도메인 모델

### 6.1 AwsProfile

```text
id
name
region
account_id
user_id
mfa_arn
mfa_enabled
encrypted_access_key
encrypted_secret_key
is_default
created_at
updated_at
```

규칙:

- 프로필 이름은 중복될 수 없다.
- Region, Account ID, User ID, Access Key, Secret Key는 등록 전에 검증한다.
- `mfa_enabled`가 꺼진 프로필도 관례적 ARN은 메타데이터로 보존하지만 세션 발급 요청에는
  MFA ARN과 코드를 전달하지 않는다.
- 키 원문은 모델 직렬화나 로그 출력 대상이 아니다.

### 6.2 SessionCredentials

```text
profile_id
encrypted_access_key
encrypted_secret_key
encrypted_session_token
expires_at_utc
verified_at_utc
```

규칙:

- 한 프로필당 활성 세션은 최대 하나다.
- 만료까지 30분 이하이면 갱신 대상으로 판정한다.
- 새 세션 검증이 완료된 뒤 기존 세션을 교체한다.

### 6.3 TunnelSession

```text
id
profile_id
name
host
remote_port
local_port
target_mode        # select 또는 fixed
target_instance_id
created_at
updated_at
last_used_at
```

규칙:

- 프로필 내 세션 이름은 중복될 수 없다.
- 모든 포트는 1~65535 범위다.
- `target_mode=fixed`이면 Instance ID가 필수다.
- `target_mode=select`이면 실행 시 온라인 SSM EC2를 선택한다.

### 6.4 S3Location

```text
id
profile_id
name
bucket
prefix
created_at
updated_at
```

## 7. SQLite 스키마 초안

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE aws_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    region TEXT NOT NULL,
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    mfa_arn TEXT NOT NULL,
    mfa_enabled INTEGER NOT NULL DEFAULT 1 CHECK (mfa_enabled IN (0, 1)),
    encrypted_access_key BLOB NOT NULL,
    encrypted_secret_key BLOB NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE session_credentials (
    profile_id INTEGER PRIMARY KEY,
    encrypted_access_key BLOB NOT NULL,
    encrypted_secret_key BLOB NOT NULL,
    encrypted_session_token BLOB NOT NULL,
    expires_at_utc TEXT NOT NULL,
    verified_at_utc TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES aws_profiles(id) ON DELETE CASCADE
);

CREATE TABLE tunnel_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    remote_port INTEGER NOT NULL CHECK (remote_port BETWEEN 1 AND 65535),
    local_port INTEGER NOT NULL CHECK (local_port BETWEEN 1 AND 65535),
    target_mode TEXT NOT NULL CHECK (target_mode IN ('select', 'fixed')),
    target_instance_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT,
    UNIQUE (profile_id, name),
    FOREIGN KEY (profile_id) REFERENCES aws_profiles(id) ON DELETE CASCADE,
    CHECK (
        (target_mode = 'select' AND target_instance_id IS NULL) OR
        (target_mode = 'fixed' AND target_instance_id IS NOT NULL)
    )
);

CREATE TABLE s3_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    bucket TEXT NOT NULL,
    prefix TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (profile_id, name),
    FOREIGN KEY (profile_id) REFERENCES aws_profiles(id) ON DELETE CASCADE
);

CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

SQLite 연결 시 `PRAGMA foreign_keys = ON`을 반드시 적용한다.

## 8. 주요 데이터 흐름

### 8.1 인증과 세션 재사용

```text
프로필 선택
→ SQLite에서 암호문 읽기
→ DPAPI 복호화
→ 저장 세션 만료 확인
├─ 유효: STS identity 확인 후 재사용
└─ 만료/불일치: MFA 입력 → GetSessionToken → identity 검증 → 암호화 저장
```

### 8.2 EC2 접속

```text
유효 AWS 세션
→ SSM DescribeInstanceInformation
→ 필요 시 EC2 DescribeInstances로 Name 보강
→ EC2 선택
→ boto3 StartSession
→ session-manager-plugin 실행
→ 종료 코드 및 오류 처리
```

### 8.3 RDS 터널

```text
저장 터널 세션 선택
→ host/port 검증
→ 로컬 포트 사용 여부 확인
→ 고정 EC2 또는 온라인 EC2 선택
→ boto3 StartSession(document=AWS-StartPortForwardingSessionToRemoteHost)
→ session-manager-plugin 실행
→ 터널 상태 표시 및 종료 처리
```

### 8.4 S3 조회와 업로드

```text
저장 위치 또는 직접 Bucket/Prefix 입력
→ ListObjectsV2(Delimiter=/) 페이지 탐색
→ 로컬 파일 선택 또는 드롭
→ ListObjectsV2 exact-key로 존재 여부와 최종 s3:// URI preflight
→ 사용자 확인(기존 key는 별도 overwrite 동의)
→ 16 MiB 미만 PutObject / 이상 multipart
→ ProgressEvent 발행
→ 성공 완료 또는 취소·실패 시 AbortMultipartUpload 정리
```

- `ListBuckets`는 port와 adapter에 존재하지 않으며 Bucket 직접 입력을 막을 수 없다.
- multipart는 8 MiB 한 part만 메모리에 두고 순차 전송한다. 따라서 동시성은 1로 제한된다.
- cancel/failure 이후 abort 실패는 원 오류의 typed diagnostic에 덧붙이고 원인을 교체하지 않는다.
- CLI는 기본적으로 preflight만 출력하며 `--confirm`, 기존 key에는 `--overwrite`가 필요하다.
- human CLI는 같은 `ProgressEvent`를 전송 중 즉시 stderr에 출력하고 stdout의 최종 결과와
  JSON machine contract를 분리한다. JSON 모드는 완료 payload에 event 배열을 포함한다.
- GUI는 같은 Application `UploadPlan`, `ProgressEvent`, `CancellationToken`을 사용한다.
- 프로필 변경 또는 GUI 종료는 업로드 취소를 요청하고 multipart abort를 포함한 작업 종료
  콜백이 온 뒤에만 앱 종료 절차를 계속한다.
- 전송 실패는 자동 재시도하지 않는다. 사용자가 명시적으로 다시 실행하면 새로운 operation과
  multipart upload ID, 0부터 시작하는 progress를 사용한다. 부분 성공 후 재시도는 preflight를
  새로 수행해 이미 생성된 key에 대한 별도 overwrite 동의를 다시 받아야 한다.

## 9. Session Manager Plugin 연동

- boto3 `start_session()` 응답의 SessionId, StreamUrl, TokenValue를 플러그인에 전달한다.
- 세션 요청의 Target, DocumentName, Parameters와 Region을 함께 전달한다.
- 플러그인은 애플리케이션 폴더의 고정 상대경로에서 찾는다.
- 플러그인 버전을 시작 시 확인하고 지원 최소 버전 미만이면 안내한다.
- 프로세스 표준 입출력을 현재 콘솔 또는 전용 터미널 창에 연결한다.
- EC2 전용 터미널은 `wt.exe -w new new-tab --title ... --suppressApplicationTitle`의
  공식 지원 옵션만 사용하며, 지원되지 않는 `--wait`에 의존하지 않는다.
- 외부 터미널 helper의 명령행에는 Plugin 세션 payload를 넣지 않는다. 매 실행마다
  별도의 256-bit named-pipe 인증키를 만들고 Current User DPAPI 암호문만 helper에
  전달한 뒤, `StreamUrl`과 `TokenValue`를 포함한 Plugin 계약은 인증된 AF_PIPE의
  메모리 메시지로 전달한다. 키·payload 임시 파일은 만들지 않는다.
- helper는 실제 Plugin PID와 종료 코드를 pipe로 보고한다. handshake가 끝나기 전에
  실패하면 listener, connection과 dispatch를 정리하고 Application runner가 시작된
  AWS 세션을 종료한다.
- 애플리케이션이 소유한 RDS 터널은 비정상 종료 시 정리를 시도할 수 있도록 세션 ID를 추적한다. 사용자 소유로 전환된 EC2 외부 터미널 세션은 GUI 종료 정리 대상에서 제외한다.
- 공식 바이너리와 Apache 2.0 LICENSE/NOTICE를 배포물에 포함한다.

## 10. 보안 설계

### 10.1 DPAPI

- Windows Current User 범위로 암호화한다.
- Access Key, Secret Key, 임시 Access Key, 임시 Secret Key, Session Token을 각각 암호화한다.
- 추가 엔트로피 값은 애플리케이션 상수로 하드코딩하지 않고 설치 인스턴스별로 생성하여 보호한다.
- 설치별 엔트로피 자체는 추가 엔트로피 없이 Current User DPAPI로 암호화한 뒤
  버전 헤더와 함께 저장한다. 기존 32바이트 평문 엔트로피 파일은 최초 로드 시
  원자적으로 암호문 형식으로 교체하며, 알 수 없거나 손상된 형식은 자동 재생성하지
  않고 명확한 `DataProtectionError`로 중단한다.
- 복호화 실패는 DB 손상, 다른 사용자, 다른 PC 이동 가능성을 포함한 메시지로 안내한다.

### 10.2 로그 마스킹

다음 값은 로깅 금지 목록으로 중앙 관리한다.

- Access Key ID
- Secret Access Key
- Session Token
- MFA 코드
- SecretString 전체
- DB password
- boto3 StartSession TokenValue 및 StreamUrl

오류 객체와 HTTP 요청을 직렬화하기 전에 민감 필드를 제거한다.

### 10.3 SQLite 파일

- 기본 위치는 `%LOCALAPPDATA%\AWSConnect\aws_connect.db`다. 실행 파일이 있는 Portable
  폴더는 변경 불가능한 배포 자산 위치로 취급한다.
- 데이터/로그 디렉터리는 상속을 차단한 뒤 현재 사용자의 상속 가능한 Full Control ACE
  하나만 둔다. 이후 생성되는 SQLite sidecar와 회전 로그는 이 ACE를 상속할 수 있지만
  effective DACL에는 다른 trustee가 없어야 한다. 기존 파일과 entropy/진단 archive는 같은
  current-user-only ACL을 직접 적용한다.
- DB에는 비밀 원문을 저장하지 않는다.
- 백업 또는 내보내기 시 암호문을 유지하고 복호화된 값은 포함하지 않는다.

## 11. 오류 모델

애플리케이션 계층에서 다음 오류 유형으로 통일한다.

MFA 사용 프로필에서 입력이 필요하다는 사실은 오류가 아니라 `MFA_REQUIRED` 작업 상태이며,
잘못된 MFA 입력만 `MfaValidationError`로 처리한다. MFA 미사용 프로필은 `SESSION_REQUIRED`
상태에서 입력 없이 토큰을 발급하고 원래 작업을 한 번 재개한다.

```text
ConfigurationError
CredentialValidationError
MfaValidationError
AwsPermissionError
AwsNetworkError
TargetNotConnectedError
PortAlreadyInUseError
PluginExecutionError
SecretAccessError
S3TransferError
DataProtectionError
```

각 오류는 다음 정보를 가진다.

- 사용자 메시지
- 기술 원인
- 관련 AWS 서비스와 Action
- 재시도 가능 여부
- 로그용 correlation ID

## 12. 구현 단계

각 기능은 다음 순서를 지키는 하나의 수직 슬라이스로 개발한다.

1. 도메인 규칙과 DTO 정의
2. Application Service와 port 정의
3. 단위 테스트와 fake 기반 서비스 테스트
4. Infrastructure adapter 구현과 통합 테스트
5. CLI command 연결과 성공·실패 시나리오 검증
6. GUI 화면 연결과 GUI adapter 테스트
7. CLI와 GUI 결과 일관성 회귀 테스트

### Phase 0: 개발환경과 골격

- Python 설치 및 버전 고정
- 프로젝트 구조와 의존성 잠금
- 테스트·린트·타입 검사 명령 구성
- CLI와 GUI 진입점 및 공통 `bootstrap.py` 구성
- `argparse` 하위 명령 골격과 공통 JSON 출력·종료 코드 구현
- SQLite migration 기반 마련
- GUI/CLI PyInstaller 최소 실행 파일 검증

완료 조건:

- 깨끗한 Windows 테스트 환경에서 GUI와 CLI Hello World Portable ZIP 실행
- 로그와 DB가 예상 상대경로에 생성

### Phase 1: 프로필 및 인증

- SQLite 프로필 CRUD
- DPAPI 암복호화
- STS identity 검증
- MFA Session Token 발급·재사용
- 만료·불일치 갱신 정책
- `profile`과 `auth` CLI 명령

완료 조건:

- 정상/잘못된/비활성 키 오류 구분
- 토큰 재사용과 만료 갱신 통합 테스트 통과
- DB와 로그에서 평문 키 미검출
- GUI 없이 CLI에서 프로필 CRUD와 인증 정상·실패 시나리오 검증

### Phase 2: EC2 SSM

- 온라인 SSM 대상 조회
- EC2 Name 태그 보강과 권한 부족 fallback
- EC2 선택 화면
- boto3 StartSession 및 플러그인 실행
- `ec2 list|connect` CLI 명령

완료 조건:

- AWS CLI와 gossm이 없는 테스트 PC에서 EC2 세션 연결
- GUI 연결 전에 CLI의 목록 조회와 접속 시나리오 통과

### Phase 3: RDS 저장 터널

- 터널 세션 CRUD와 독립된 복제
- 로컬 포트 충돌 검사
- 고정/선택형 EC2 target
- 원격 호스트 포워딩
- 세션 종료 및 프로세스 정리
- `rds session`과 `rds tunnel` CLI 명령

완료 조건:

- 저장 세션으로 반복 연결
- 사용 중 포트에서 연결 차단
- 프로그램 종료 시 플러그인 프로세스 정리
- GUI 연결 전에 CLI의 세션 CRUD와 터널 수명주기 시나리오 통과

### Phase 4: Secrets Manager

- Secret 이름/ARN 직접 조회
- JSON 렌더링과 민감 필드 마스킹
- 일시 표시 및 클립보드 정책
- `secrets get` CLI 명령

완료 조건:

- 목록 권한 없이 이름 직접 입력으로 조회
- Secret 원문 로그 미기록
- GUI 연결 전에 CLI의 정상 조회·권한 부족·마스킹 시나리오 통과

### Phase 5: S3

- 저장 Bucket/Prefix CRUD
- 객체 목록
- 파일 선택 및 업로드
- 덮어쓰기 확인과 진행률
- multipart upload
- `s3 list|upload` CLI 명령

완료 조건:

- `ListAllMyBuckets` 없이 지정 Bucket 사용
- 동일 키 덮어쓰기 확인
- 실패한 multipart upload 정리
- GUI 연결 전에 CLI의 목록·업로드·덮어쓰기 시나리오 통과

### Phase 6: 패키징과 릴리스

- PyInstaller 빌드
- `aws_connect.exe`와 `aws_connect_cli.exe`를 같은 Application 패키지에서 빌드
- 플러그인 및 라이선스 포함
- 버전 정보 및 체크섬
- 깨끗한 Windows VM 회귀 테스트
- Portable ZIP 생성

## 13. 테스트 전략

### 13.1 단위 테스트

- 포트와 host 검증
- ARN과 Account/User 일치 판정
- 토큰 만료 및 30분 임계값
- AWS 오류 코드 매핑
- 터널 세션 불변식
- 로그 마스킹
- Secret JSON 민감 필드 탐지
- `OperationState`의 허용된 상태 전이
- 진행 이벤트 순서와 완료값 범위
- 취소 요청 후 리소스 정리 완료 전 `CANCELLED` 전환 금지
- MFA challenge 생성·성공 재개·취소·만료와 중복 실행 방지
- 모든 Application 오류에 대한 presentation mapping 완전성

### 13.2 CLI와 GUI 어댑터 테스트

- 기능별 CLI 인자 파싱과 Application DTO 변환
- 기본 출력과 `--output json` 계약
- 오류별 CLI 종료 코드
- 민감정보가 AWS Connect/Windows Terminal/helper 인자와 출력에 포함되지 않는지
  검사한다. 공식 Plugin의 최종 argv에는 upstream 계약상 단기 transport 값이 필요하므로
  이 경계는 별도로 마스킹·비기록·프로세스 진단 범위를 검사한다.
- GUI 이벤트와 Application DTO 변환
- GUI가 도메인 규칙을 별도로 계산하지 않는지 검사
- 동일 fake Application Service에 대한 CLI와 GUI 결과 일관성
- Application과 Domain에서 `print`, `input`, `sys.exit` 및 GUI 모듈 import 금지 검사
- CLI `Ctrl+C`가 취소 요청과 RDS 터널 정리를 수행하는지 검사
- GUI의 진행률·취소 이벤트와 MFA 재개 연결 검사

### 13.3 통합 테스트

- SQLite migration과 FK cascade
- DPAPI 암복호화 및 다른 사용자 복호화 실패
- boto3 gateway Stubber 기반 AWS 응답
- Session Manager Plugin 프로세스 인자 조립
- S3 multipart 성공·중단·재시도
- `ExternalSessionHandle`과 `TunnelHandle` 상태 전이 및 프로세스 종료 정리
- GUI 종료 시 RDS 터널 정리와 EC2 외부 터미널 유지 정책

### 13.4 실제 AWS 검증

- 유효/만료 임시 세션
- SSM 온라인/오프라인 EC2
- EC2 Describe 권한이 없는 프로필
- RDS 고정 및 선택 target
- Secrets List 권한 없이 Get 권한만 있는 프로필
- S3 List/Put 권한 조합
- 실제 AWS 검증은 기능별 CLI를 우선 사용하고 GUI는 동일 시나리오의 화면 연결을 확인

### 13.5 패키징 테스트

- Python 미설치 PC
- AWS CLI 미설치 PC
- gossm 미설치 PC
- Session Manager Plugin 미설치 PC
- 한글과 공백이 포함된 실행 경로
- 읽기 전용 폴더 및 로그 경로 오류
- GUI와 CLI가 같은 SQLite, 로그와 Plugin 경로를 사용하는지 검사

## 14. 주요 리스크와 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| DPAPI 때문에 PC 간 자격증명 이동 불가 | Portable 기대와 차이 | 새 PC에서 재등록 안내, 암호화 내보내기는 후속 검토 |
| Plugin 호출 규약 변경 | SSM 접속 실패 | 지원 버전 고정, 시작 시 버전 검사, 회귀 테스트 |
| PyInstaller 백신 오탐 | 실행 차단 | 코드 서명, onedir 우선 검토, 해시 제공 |
| RDS Describe 권한 부재 | 자동 endpoint 조회 불가 | 저장 터널 세션을 기본 경로로 유지 |
| Secrets List 권한 부재 | Secret 탐색 불가 | 이름/ARN 직접 입력 지원 |
| 평문 로그 유출 | 자격증명 사고 | 중앙 마스킹 필터, 보안 회귀 테스트 |
| 로컬 포트 충돌 | 터널 시작 실패 | 시작 전 bind 검사 및 대체 포트 안내 |
| 플러그인 프로세스 잔존 | 리소스 누수 | 자식 프로세스 추적과 종료 처리 |

## 15. 런타임 사이드이펙트 점검

- EC2/RDS 연결 중 Session Manager Plugin 자식 프로세스가 유지된다.
- RDS 터널은 로컬 TCP 포트를 점유한다.
- SQLite 쓰기와 로그 파일 순환이 발생한다.
- S3 업로드는 네트워크와 로컬 파일 읽기 I/O를 사용한다.
- 앱이 소유한 RDS 터널의 종료 순서는 Plugin 프로세스 종료 → 세션 종료 요청 → DB commit → 로그 flush로 관리한다. 사용자 소유 EC2 외부 터미널은 이 순서에서 제외한다.

## 16. 마이그레이션 계획

1. 기존 `aws_info.ini`를 최초 실행 시 탐지한다.
2. 사용자 확인 후 AWS 프로필과 RDS 터널 정보를 SQLite로 가져온다.
3. Access Key와 Secret Key를 즉시 DPAPI 암호화한다.
4. 가져오기 성공 후 INI에서 민감정보를 제거할지 사용자에게 안내한다.
5. BAT는 초기 안정화 기간 동안 fallback으로 보관한다.
6. EXE 기능 검증 완료 후 BAT 지원 종료 여부를 결정한다.

INI를 자동 삭제하거나 덮어쓰지 않는다.

## 17. 구현 시작 전 결정 필요 사항

1. GUI 프레임워크와 지원할 최소 Windows 버전
2. PyInstaller: one-file EXE 또는 onedir EXE
3. ~~DB 기본 위치: 실행 폴더 또는 `%LOCALAPPDATA%`~~ — ADR 0001에서
   `%LOCALAPPDATA%\AWSConnect`로 결정 완료
4. 코드 서명 인증서 사용 여부
5. MVP에 Secrets Manager와 S3를 포함할지 후속 릴리스로 둘지
6. 기존 INI 자동 가져오기 제공 여부

## 18. Definition of Ready

다음 조건을 충족하면 구현을 시작한다.

- [`aws-connect.md`](../product-specs/aws-connect.md)의 MVP 범위 승인
- GUI 프레임워크와 최소 Windows 버전 결정
- DB 기본 위치 결정(ADR 0001의 `%LOCALAPPDATA%\AWSConnect`)
- 패키징 방식 결정
- Session Manager Plugin 배포 버전 결정
- 최소 IAM 권한 목록 확인
- 테스트용 AWS 프로필과 EC2/RDS/S3 리소스 준비
- 기능별 CLI 명령, JSON 출력과 종료 코드 계약 승인
