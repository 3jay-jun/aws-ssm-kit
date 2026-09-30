# aws-ssm-kit

Windows에서 AWS 인증, EC2 Session Manager 접속, RDS 포트 포워딩, Secrets Manager 조회와 S3 작업을 하나의 GUI와 CLI로 제공하는 Portable 애플리케이션입니다.

> 현재 버전은 `0.1.0`이며 Windows 10/11 x64만 지원합니다. 제품 요구사항은 아직 초안 상태이므로 운영 환경에 적용하기 전에 승인된 비운영 AWS 프로필로 검증하세요.

## 주요 기능

- AWS 프로필 등록과 STS 검증, 선택적 MFA 기반 임시 세션 관리
- EC2/SSM 인스턴스 조회, 시작·재부팅 및 외부 터미널 세션 연결
- 저장 가능한 RDS 터널 세션과 로컬 포트 충돌 검사
- Secrets Manager 값 조회, 기본 마스킹과 제한된 공개·복사 흐름
- S3 위치 저장, 객체 탐색·검색·업로드·다운로드·단건 이름 변경 및 삭제
- SQLite 실행 이력, 진단 로그 내보내기와 CLI 런타임 점검
- GUI와 CLI가 같은 Domain/Application Service를 사용하는 계층형 구조

전체 제품 범위와 비목표는 [제품 요구사항](docs/product-specs/aws-connect.md)을 참고하세요.

## 실행 환경

### 배포본 사용자

- Windows 10/11 x64
- 배포된 Portable ZIP
- 기능에 필요한 최소 권한의 AWS IAM 사용자와, 사용하는 경우 MFA 디바이스

배포본에는 Python, AWS CLI, gossm의 별도 설치가 필요하지 않습니다. 승인된 Session Manager Plugin도 ZIP에 포함됩니다.

1. ZIP을 원하는 폴더에 압축 해제합니다.
2. `aws_connect.exe`를 실행합니다.
3. 장애 진단이 필요하면 PowerShell에서 다음 명령을 실행합니다.

```powershell
.\aws_connect_cli.exe doctor --output text
```

프로필, 암호화된 자격증명, 설정과 로그는 실행 폴더가 아니라 `%LOCALAPPDATA%\AWSConnect`에 저장됩니다. 자격증명과 임시 토큰은 현재 Windows 사용자 범위 DPAPI로 보호되므로 다른 PC나 Windows 사용자로 자동 이전되지 않습니다.

### 소스 개발

필수 도구:

- Windows 10/11 x64와 PowerShell
- [uv](https://docs.astral.sh/uv/)
- Git

Python 3.12와 개발 의존성을 준비합니다.

```powershell
./scripts/bootstrap.ps1
```

GUI와 CLI를 실행합니다.

```powershell
uv run --no-sync aws-connect
uv run --no-sync aws-connect-cli doctor --output json
uv run --no-sync aws-connect-cli --help
```

`bootstrap.ps1`은 필요한 Python 3.12가 없으면 `uv`를 통해 설치하고 `uv.lock` 기준으로 모든 개발 의존성을 동기화합니다.

## CLI

CLI는 GUI와 같은 Application Service를 사용하며 기능 검증과 진단에 적합합니다.

```text
aws-connect-cli <command> [options]

dashboard  읽기 전용 권한 및 최근 작업 요약
doctor     로컬 DB, DPAPI, 로그, Plugin 등 런타임 점검
settings   공용 로컬 설정 관리
logs       재마스킹된 진단 로그 내보내기
legacy     기존 aws_info.ini 가져오기 미리보기/적용
profile    보호된 AWS 프로필 관리
auth       AWS 인증 검증과 임시 세션 갱신
ec2        SSM 관리 EC2 조회 및 연결
rds        저장된 RDS 터널 관리 및 시작
secrets    Secrets Manager 값 조회
s3         S3 위치와 객체, 업로드 작업 관리
```

각 명령의 정확한 인자와 안전 옵션은 하위 도움말에서 확인합니다.

```powershell
uv run --no-sync aws-connect-cli profile --help
uv run --no-sync aws-connect-cli s3 --help
```

실제 AWS 호출을 포함한 smoke test는 명시적으로 승인된 최소 권한 비운영 프로필에 한해서만 실행해야 합니다. [`scripts/test-aws.ps1`](scripts/test-aws.ps1)은 `AWS_CONNECT_ALLOW_AWS_SMOKE=1`이 없으면 실행을 거부합니다.

## 아키텍처

```text
presentation/cli ─┐
                  ├─ application ─> domain
presentation/gui ─┘       │
                           └─ ports <─ infrastructure
```

- `src/aws_connect/domain/`: 도메인 불변식, 값 객체, 타입화된 오류
- `src/aws_connect/application/`: 유스케이스, DTO, 작업 계약과 포트
- `src/aws_connect/infrastructure/`: AWS, SQLite, DPAPI, 파일시스템과 프로세스 어댑터
- `src/aws_connect/presentation/`: CLI/GUI 입력과 출력 매핑
- `src/aws_connect/bootstrap.py`: 실제 어댑터를 조립하는 유일한 composition root

Domain은 외부 계층을 참조하지 않고, Application은 Infrastructure나 Presentation에 의존하지 않습니다. GUI는 CLI 출력이나 프로세스를 호출하지 않습니다. 상세 경계는 [ARCHITECTURE.md](ARCHITECTURE.md)와 [애플리케이션 설계](docs/design-docs/aws-connect-application.md)에 정의되어 있습니다.

## 테스트와 품질 검사

빠른 범위별 검사를 실행할 수 있습니다.

```powershell
./scripts/test-unit.ps1
./scripts/test-integration.ps1
./scripts/verify-docs.ps1
```

커밋 전 표준 게이트는 다음 하나의 명령입니다.

```powershell
./scripts/check.ps1
```

이 게이트는 문서 정합성, Ruff 형식·lint, 계층 경계, mypy, pytest와 커버리지, secret scan, Bandit, 의존성 취약점 감사를 순서대로 실행하고 첫 실패에서 중단합니다. 실제 AWS 리소스는 기본 검사에서 사용하지 않습니다.

## Windows 패키징

릴리스 패키지는 PyInstaller `onedir` 기반 Portable ZIP입니다. 프로덕션 빌드는 검토되고 체크섬·서명·라이선스 정보가 기록된 Session Manager Plugin 입력이 없으면 실패합니다.

```powershell
./scripts/build-package.ps1 -VendorDirectory ./vendor/session-manager-plugin
./scripts/test-package.ps1 -ZipPath ./dist/aws-connect-0.1.0-windows-x64.zip
```

공급망 검증 조건, TEST-ONLY 패키지와 GitHub Actions 게시 흐름은 [패키징 안내](packaging/README.md)를 참고하세요.

## 문서

- [제품 명세 인덱스](docs/product-specs/index.md)
- [GUI 디자인 기준](docs/DESIGN.md)
- [프런트엔드 구현 규칙](docs/FRONTEND.md)
- [보안 정책](docs/SECURITY.md)
- [신뢰성 정책](docs/RELIABILITY.md)
- [활성 실행 계획](docs/exec-plans/active/001-aws-connect.md)

## 보안 원칙

- Access Key, Secret Key와 임시 세션 토큰은 SQLite에 평문으로 저장하지 않습니다.
- MFA 코드, Secret 원문과 세션 전송 값은 로그나 프로세스 인자에 남기지 않습니다.
- AWS/Windows 오류는 경계에서 사용자용 타입 오류로 변환하고, 진단 로그는 내보내기 전에 다시 마스킹합니다.
- 실제 AWS 검증에는 승인된 비운영 리소스와 최소 권한 프로필만 사용합니다.

취약점이나 자격증명 노출이 의심되면 값을 README나 이슈에 붙이지 말고 즉시 해당 자격증명을 폐기한 뒤 저장소 관리자의 보안 절차를 따르세요.
