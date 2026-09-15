# AWS Connect 실행 계획

## 1. 계획 목적

이 문서는 [`aws-connect.md`](../../product-specs/aws-connect.md)의 제품 요구사항과 [`aws-connect-application.md`](../../design-docs/aws-connect-application.md)의 애플리케이션 구조를 실제 구현 순서로 전환한다. 개발은 [`harness-engineering.md`](../../references/harness-engineering.md)가 가리키는 [OpenAI Harness Engineering](https://openai.com/ko-KR/index/harness-engineering/) 원칙을 적용한다.

목표는 에이전트나 개발자가 다음 작업을 반복해서 안전하게 수행할 수 있는 저장소를 만드는 것이다.

1. 필요한 문맥을 저장소 안에서 찾는다.
2. 작은 기능 단위로 계획한다.
3. Application Service를 구현한다.
4. CLI에서 기능과 오류를 먼저 검증한다.
5. 동일한 Application Service를 GUI에 연결한다.
6. 자동 검증 결과를 근거로 완료 여부를 판단한다.

이 계획에서 GUI가 CLI 프로세스를 호출하는 구조는 허용하지 않는다. CLI와 GUI는 동일한 Domain 및 Application 계층을 사용하는 독립적인 presentation adapter다.

## 2. 기준 문서

| 공식 문서 | 역할 |
|---|---|
| [`aws-connect.md`](../../product-specs/aws-connect.md) | 제품 범위, 사용자 흐름, 기능·보안 요구사항 |
| [`DESIGN.md`](../../DESIGN.md) | GUI 정보 구조와 상호작용의 공식 기준 |
| [`aws-connect-application.md`](../../design-docs/aws-connect-application.md) | 계층, 도메인 모델, CLI 계약, 오류와 비동기 작업 계약 |
| [`aws-connect-ui-mockup.html`](../../design-docs/aws-connect-ui-mockup.html) | 시각적 참고와 화면 흐름 |
| [`harness-engineering.md`](../../references/harness-engineering.md) | 하네스 레퍼런스와 적용 메모 |
| 이 `001-aws-connect.md` | 구현 순서, 검증 게이트, 진행 기록 |

상세 지식은 `docs/` 아래의 공식 위치에서만 관리한다. 문서 간 충돌은 제품 명세 → 보안·안정성 → 설계 문서 → 실행 계획 순으로 해결하며, 구현 중 발견한 변경은 코드와 관련 기준 문서를 같은 변경에서 갱신한다.

## 3. 하네스 엔지니어링 적용 원칙

### 3.1 저장소가 실행 가능한 기록 시스템이어야 한다

- 채팅이나 사람의 기억에만 존재하는 결정은 완료된 결정으로 간주하지 않는다.
- `AGENTS.md`는 거대한 설명서가 아니라 문서, 명령, 경계로 안내하는 짧은 지도 역할만 한다.
- 상세 정보는 목적별 문서에 나누고 인덱스를 통해 연결한다.
- 진행 중 계획, 완료 계획과 기술 부채를 저장소에서 함께 관리한다.
- 생성 가능한 자료는 수작업으로 중복 관리하지 않고 스크립트로 재생성한다.

### 3.2 규칙은 가능한 한 기계적으로 검증한다

- 계층 의존성은 architecture test로 검사한다.
- 코드 형식, lint, 타입, 테스트, 문서 링크와 보안 검사를 자동화한다.
- 오류 매핑과 CLI JSON 계약의 완전성을 테스트한다.
- SQLite 스키마 문서는 migration으로부터 생성하고 drift를 검사한다.
- 문서에만 적힌 중요한 금지 규칙은 정적 검사 또는 테스트로 승격한다.

### 3.3 하나의 빠른 피드백 진입점을 제공한다

로컬과 CI는 같은 명령을 사용한다.

```powershell
./scripts/check.ps1
```

이 명령은 빠른 검사부터 실패 즉시 중단한다.

1. 문서 구조와 내부 링크 검사
2. format check와 lint
3. architecture boundary 검사
4. 타입 검사
5. 단위 및 adapter 테스트
6. infrastructure 통합 테스트
7. 민감정보·보안 검사

실제 AWS, GUI 렌더링과 패키징 테스트는 별도 명령으로 분리하되 CI 또는 릴리스 게이트에서 실행한다.

### 3.4 실패는 하네스 개선 신호로 사용한다

같은 유형의 실패가 반복되면 단순히 다시 시도하지 않는다. 다음 중 누락된 항목을 찾아 저장소에 추가한다.

- 재현 fixture
- fake 또는 Stubber 응답
- 진단 명령
- 오류 메시지와 복구 지침
- architecture rule
- regression test
- 문서 링크 또는 실행 예시

## 4. 목표 저장소 구조

```text
.
├─ AGENTS.md
├─ ARCHITECTURE.md
├─ docs/
│  ├─ design-docs/
│  │  ├─ index.md
│  │  ├─ core-beliefs.md
│  │  ├─ aws-connect-application.md
│  │  ├─ aws-connect-ui-mockup.html
│  │  └─ decisions/
│  ├─ exec-plans/
│  │  ├─ active/
│  │  │  └─ 001-aws-connect.md
│  │  ├─ completed/
│  │  └─ tech-debt-tracker.md
│  ├─ generated/
│  │  └─ db-schema.md
│  ├─ product-specs/
│  │  ├─ index.md
│  │  ├─ aws-connect.md
│  │  └─ new-user-onboarding.md
│  ├─ references/
│  │  ├─ harness-engineering.md
│  │  ├─ pyside6-llms.txt
│  │  ├─ pyinstaller-llms.txt
│  │  └─ uv-llms.txt
│  ├─ DESIGN.md
│  ├─ FRONTEND.md
│  ├─ PLANS.md
│  ├─ PRODUCT_SENSE.md
│  ├─ QUALITY_SCORE.md
│  ├─ RELIABILITY.md
│  └─ SECURITY.md
├─ pyproject.toml
├─ uv.lock
├─ src/aws_connect/
│  ├─ bootstrap.py
│  ├─ cli_main.py
│  ├─ gui_main.py
│  ├─ domain/
│  ├─ application/
│  ├─ infrastructure/
│  └─ presentation/
│     ├─ cli/
│     └─ gui/
├─ tests/
│  ├─ architecture/
│  ├─ unit/domain/
│  ├─ unit/application/
│  ├─ adapter/cli/
│  ├─ adapter/gui/
│  ├─ integration/infrastructure/
│  └─ e2e/
├─ scripts/
│  ├─ bootstrap.ps1
│  ├─ check.ps1
│  ├─ test-unit.ps1
│  ├─ test-integration.ps1
│  ├─ test-aws.ps1
│  ├─ test-package.ps1
│  ├─ generate-docs.ps1
│  └─ verify-docs.ps1
└─ build/
   └─ pyinstaller/
```

이 구조에서 루트의 `AGENTS.md`와 `ARCHITECTURE.md`는 저장소 지식의 지도다. 상세 지식은 `docs/` 아래에만 두며 다음 역할을 가진다.

- `design-docs`: 아키텍처, 핵심 신념, ADR과 UI 목업
- `exec-plans`: 진행 중·완료 실행 계획과 기술 부채
- `generated`: migration이나 코드에서 재생성되는 문서
- `product-specs`: 제품 요구사항과 사용자 여정
- `references`: 외부 도구·프레임워크의 검증된 참고자료
- `DESIGN.md`: 제품 UI 설계 원칙과 화면 기준
- `FRONTEND.md`: PySide6 화면 구성, worker/event와 view model 구현 규칙
- `PLANS.md`: 실행 계획 형식과 상태 전환 규칙
- `PRODUCT_SENSE.md`: 사용자 가치, 비목표와 제품 판단 기준
- `QUALITY_SCORE.md`: 영역별 품질 점수와 개선 추적
- `RELIABILITY.md`: 오류, 재시도, 프로세스·포트·SQLite 안정성
- `SECURITY.md`: 자격증명, DPAPI, 마스킹과 최소 권한 정책

제품 코드와 테스트는 `docs/`에 넣지 않는다. 문서는 구현을 안내하는 지식 시스템이고 `src`, `tests`, `scripts`는 그 규칙을 실행하고 검증하는 하네스다.

## 5. 도구와 기술 선택

### 5.1 기본 선택

- Python 3.12
- 패키지 및 가상환경: `uv`
- AWS SDK: boto3/botocore
- CLI: 표준 `argparse`
- GUI: PySide6 권장, Phase 0 결정 게이트에서 확정
- DB: 표준 `sqlite3`와 versioned migration
- Windows 암호화: DPAPI
- 테스트: pytest, pytest-cov, botocore Stubber
- 코드 품질: Ruff, mypy
- 계층 검사: import-linter 권장
- 보안 검사: detect-secrets, Bandit, pip-audit
- 패키징: PyInstaller onedir 권장
- CI: GitHub Actions `windows-latest`

### 5.2 주요 대안

- **PySide6 — 추천:** 현재 목업 수준의 테이블, 모달, 백그라운드 작업과 상태 표현에 적합하지만 배포 크기가 커진다.
- **Tkinter:** 기본 포함이라 가볍지만 복잡한 화면과 일관된 Windows UI 구현 비용이 커진다.

- **import-linter — 추천:** 허용 import 경계를 선언적으로 검사하지만 개발 의존성이 하나 추가된다.
- **직접 작성한 AST 검사:** 의존성은 줄지만 규칙 구현과 유지보수를 프로젝트가 직접 맡아야 한다.

- **PyInstaller onedir — 추천:** Plugin, 라이선스와 Qt 파일 구성이 투명하고 진단이 쉽지만 파일 수가 많다.
- **one-file:** 배포는 단순하지만 시작 지연, 임시 추출과 백신 오탐 위험이 커진다.

## 6. 공통 개발 계약

### 6.1 의존성 방향

```text
presentation/cli ─┐
                  ├─ application ─ domain
presentation/gui ─┘       │
                           └─ ports ← infrastructure
```

- Domain은 외부 계층을 import하지 않는다.
- Application은 Domain과 application port만 참조한다.
- Infrastructure는 port를 구현한다.
- Presentation은 Application Service와 DTO만 사용한다.
- 실제 구현 조립은 `bootstrap.py` 한 곳에서만 한다.
- GUI가 CLI를 실행하거나 CLI 출력 문자열을 파싱하는 구현을 금지한다.

### 6.2 Application 출력 계약

Application과 Domain에서 다음 동작을 금지한다.

- `print()`, `input()`, `sys.exit()`
- GUI 위젯, 모달, 메시지 박스 참조
- CLI용 표, 색상, stderr 문자열 생성
- AWS 오류 문자열을 기반으로 한 presentation 분기

Application은 타입이 정의된 DTO, `OperationResult[T]`, `ProgressEvent`, handle 또는 애플리케이션 오류만 반환한다.

### 6.3 오류 처리 계약

- 오류 타입과 의미는 `domain/errors.py`에서만 정의한다.
- boto3 및 Windows 오류는 infrastructure 경계에서 애플리케이션 오류로 변환한다.
- CLI mapper는 stderr, JSON 오류와 종료 코드를 만든다.
- GUI mapper는 모달, 토스트, 필드 오류와 복구 동작을 만든다.
- 모든 오류는 message code, 기술 원인, 관련 AWS 서비스·Action, 재시도 가능 여부와 correlation ID를 가진다.
- 새 오류 타입을 추가하면 CLI와 GUI mapping test를 동시에 추가한다.

### 6.4 장시간 작업 계약

- MFA, EC2 세션, RDS 터널과 S3 업로드는 공통 `OperationState`를 사용한다.
- 진행률은 `ProgressEvent`로 전달한다.
- 취소는 `CancellationToken`으로 요청하며 리소스 정리 후 `CANCELLED`가 된다.
- MFA 필요는 실패가 아니라 `MFA_REQUIRED` 상태다.
- MFA 성공 후 보관된 원래 요청 DTO를 한 번만 재개한다.
- RDS는 `TunnelHandle`, EC2 외부 터미널은 `ExternalSessionHandle`로 추적한다.

## 7. 기능 개발 순서

모든 기능은 다음 수직 슬라이스 순서를 따른다.

```text
요구사항/수용 기준
→ Domain·DTO·오류
→ Application Service·port
→ 단위 테스트
→ Infrastructure adapter·통합 테스트
→ CLI 연결·시나리오 검증
→ GUI 연결·adapter 테스트
→ 패키징 smoke test
→ 문서와 실행 계획 완료 처리
```

GUI 작업은 해당 기능의 Application 및 CLI 게이트를 통과하기 전 시작하지 않는다. 단, GUI 셸과 fake 데이터를 사용하는 화면 골격은 기능 로직과 독립적으로 개발할 수 있다.

## 8. 단계별 실행 계획

### Phase 0. 저장소 및 하네스 기반

목표: 기능 구현 전에 에이전트가 스스로 탐색·검증·복구할 수 있는 최소 환경을 만든다.

진행 상태(2026-09-09): **로컬 완료, 원격 CI 실행 대기**

- [x] 추천 결정 게이트를 확정하고 `docs/design-docs/decisions/0001-foundation-decisions.md`에 기록
- [x] Git, Python 3.12/uv, `src` layout, 테스트와 문서 구조 생성
- [x] bootstrap 문서를 공식 `docs/` 위치로 이동하고 내부 링크 검증
- [x] CLI/GUI가 같은 Application Service를 사용하는 최소 수직 슬라이스 구현
- [x] architecture 위반 fixture가 자동 검사에서 실패함을 테스트
- [x] Windows CI가 로컬과 같은 `scripts/bootstrap.ps1`, `scripts/check.ps1` 사용
- [x] 로컬 `scripts/bootstrap.ps1` 성공
- [x] 로컬 `scripts/check.ps1` 성공: 9 tests, overall 96%, Domain/Application 100%
- [ ] GitHub 원격 저장소 첫 push 후 `windows-latest` CI 실행 확인

검증 기록:

- `./scripts/bootstrap.ps1` → 성공, CPython 3.12.13 및 65개 패키지 해석
- `./scripts/check.ps1` → 성공; docs, Ruff, import boundaries, mypy, pytest,
  detect-secrets, Bandit, pip-audit 통과
- `uv run pytest` → 9 passed, overall coverage 96.23%
- 최초 실행에서 Markdown 코드 표기를 링크로 오인한 문제를 회귀 테스트로 수정
- 보안 감사에서 취약한 pytest 8.4.2를 발견하여 9.1.1로 갱신

다음 단계: Phase 3의 RDS 저장 세션과 foreground 터널 CLI를 구현한다.

작업:

- Git 저장소 초기화와 `.gitignore` 작성
- `pyproject.toml`, Python 3.12, `uv.lock` 생성
- `src` layout과 테스트 디렉터리 생성
- 짧은 `AGENTS.md`와 `ARCHITECTURE.md` 작성
- `docs` 인덱스, 실행 계획 형식과 기술 부채 추적기 생성
- 현재 루트의 PRD, 설계, 사전 계획, 실행 계획과 하네스 참고 문서를 표에 정의한 `docs/` 공식 위치로 이동
- 이동 후 문서 내부 링크를 새 위치 기준으로 갱신하고 중복 원본을 남기지 않음
- Ruff, mypy, pytest, coverage와 import boundary 설정
- `scripts/bootstrap.ps1`과 `scripts/check.ps1` 작성
- CLI/GUI 최소 진입점과 공통 `bootstrap.py` 작성
- Windows CI와 artifact 업로드 구성

게이트:

- 새 checkout에서 `./scripts/bootstrap.ps1` 성공
- `./scripts/check.ps1` 성공
- architecture rule 위반 fixture가 검사에서 실패함을 확인
- GUI와 CLI Hello World가 같은 Application fake를 호출
- CI가 로컬과 동일한 검사 명령을 사용

산출물:

- `AGENTS.md`, `ARCHITECTURE.md`
- `pyproject.toml`, `uv.lock`
- `scripts/*`, CI workflow
- `docs/DESIGN.md`, `docs/FRONTEND.md`, `docs/PLANS.md`, `docs/PRODUCT_SENSE.md`
- `docs/QUALITY_SCORE.md`, `docs/SECURITY.md`, `docs/RELIABILITY.md`
- `docs/design-docs/index.md`, `docs/design-docs/core-beliefs.md`
- `docs/product-specs/index.md`, `docs/product-specs/aws-connect.md`
- `docs/exec-plans/active/001-aws-connect.md`

### Phase 1. 공통 코어와 프로필·인증 CLI

목표: 이후 모든 AWS 기능이 공유할 보안 저장과 인증 흐름을 CLI에서 완성한다.

진행 상태(2026-09-09): **로컬 완료, 실제 AWS 확인 대기**

- [x] 중앙 typed error, DTO, Operation/MFA 재개·취소 계약 구현
- [x] versioned SQLite migration과 프로필 CRUD·기본 프로필·세션 cascade 구현
- [x] Windows Current User DPAPI와 설치별 entropy, 테스트 fake 구현
- [x] boto3 STS identity/session gateway와 botocore 오류 변환 구현
- [x] 프로필 검증, 세션 재사용·만료 임박·불일치 제거, MFA 1회 재개 구현
- [x] 모든 AWS 기능의 공통 인증 재개 SSOT, 프로필별 refresh 직렬화·내부 재검사,
  128개 pending 상한과 만료·취소·오입력·이중 재개 cleanup 구현
- [x] 중앙 recursive masking과 CLI/GUI 오류 mapper 완전성 검사 구현
- [x] `profile` 및 `auth` CLI text/JSON/exit-code 계약 구현
- [x] migration 기반 `docs/generated/db-schema.md` 생성 및 drift 검사 구현
- [x] fake, botocore Stubber, 임시 SQLite와 실제 Windows DPAPI 테스트 통과
- [ ] 승인된 최소 권한 AWS 테스트 프로필로 `GetCallerIdentity`와 MFA
  `GetSessionToken` smoke 확인

검증 기록:

- `./scripts/check.ps1` → 전체 게이트 성공. 감독 리뷰 후 DPAPI로 보호된 설치 entropy,
  평문 entropy 원자적 migration, 손상 파일 오류와 예상 밖 CLI 오류 correlation ID
  회귀 테스트를 추가했으며 47 tests, 전체 coverage 86% 이상,
  Authentication/Profile Application line·branch 100%; 최초 secret scan에서 테스트
  placeholder 3개를 탐지하여 명시적 allowlist 주석으로 fixture 의도를 기록한 후 재검증
- `aws-connect-cli profile list --output json` → `{"profiles": []}`, exit `0`
- `aws-connect-cli auth status --output json` (빈 DB) → `profile.not_found`, exit `10`
- Windows current-user-only 보호 DACL adapter를 bootstrap의 SQLite DB·sidecar 상속 디렉터리,
  DPAPI entropy, 관리 로그와 진단 ZIP 생성 경계에 연결했다. 주입 backend 단위 테스트와
  실제 Windows SDDL 조회를 포함한 focused 28 tests, Ruff, mypy가 통과했다.
- GUI 시작 시 `auth status`는 expiry만 보는 로컬·비네트워크 projection으로 유지한다.
  이후 EC2·RDS·Secrets·S3 공통 coordinator가 `auth.mfa_required`를 받으면 그 결과를
  authoritative하게 취급하여 비만료 캐시라도 임시 세션만 삭제하고 MFA challenge를 만든다.
  비만료 복호화 실패·identity mismatch·stale feature 상태, MFA 취소·오입력·정확히 1회
  재개, 동일 프로필 동시 재개의 STS 1회 발급을 공통 회귀 테스트로 검증했다. 장기 Access
  Key와 Secret Key가 저장된 프로필 레코드는 이 복구 과정에서 변경하지 않는다.
- 2026-09-14 `./scripts/check.ps1`은 dependency audit 직전까지 305 passed, 전체 coverage
  85.84%, 인증/MFA·마스킹·CLI/GUI 오류 mapper 5개 파일 branch 100%와 docs, format,
  Ruff, architecture/import, mypy, secret scan, Bandit 통과를 확인했다. sandbox의 outbound
  socket 제한으로 실패한 마지막 단계는 승인된 네트워크에서
  `uv run --no-sync pip-audit`를 별도 실행해 `No known vulnerabilities found`를 확인했다.
- 실제 AWS 호출은 승인된 프로필과 리소스가 없어 실행하지 않았으며 코드 누락 없이
  Stubber 시나리오로 대체했다. 후속 확인은 `scripts/test-aws.ps1`의 Phase 1 인증
  smoke 시나리오로 추가한다.
- 2026-09-15 최초 GitHub 동기화 전 `ref/aws_info.ini`, 루트의 로컬
  `session-manager-plugin.exe`, IDE 설정, agent/test/visual QA 임시 산출물을 `.gitignore`로
  제외했다. `./scripts/check.ps1`은 365 tests, 전체 coverage 86.75%, 문서·format·Ruff·
  architecture/import·mypy·critical coverage·secret scan·Bandit·`pip-audit`까지 통과했다.
  `3jay-jun/aws-ssm-kit`의 `main` 첫 push를 완료했으며, 다음 단계는 GitHub Actions의
  `windows-latest` check·package job 결과 확인이다.

작업:

- `AwsProfile`, `SessionCredentials`와 validation 구현
- 공통 DTO, Operation 상태, 진행·취소 계약 구현
- 중앙 오류 타입과 CLI/GUI mapper 계약 구현
- SQLite migration과 profile/session store 구현
- DPAPI 암복호화와 중앙 로그 마스킹 구현
- STS `GetCallerIdentity`, `GetSessionToken` gateway 구현
- `ProfileService`, `AuthenticationService`, `SessionGuard`, `OperationCoordinator` 구현
- `profile list|show|create|update|delete|use` CLI 구현
- `auth status|validate|refresh` CLI 구현
- 비대화형 JSON 출력과 종료 코드 구현
- EC2·RDS·Secrets·S3 CLI/GUI를 동일한 인증 coordinator에 연결하고 S3 upload의
  중첩 `OperationResult`, 진행·취소·shutdown 수명주기를 보존

게이트:

- 정상·잘못된·비활성 Access Key 오류 구분
- 유효 토큰 재사용, 만료·불일치·만료 임박 갱신 검증
- MFA 취소·오입력·만료·성공 재개 검증
- 동일 작업에서 토큰 재발급 한 번 제한 검증
- DB, 로그, CLI 인자와 출력에서 비밀 원문 미검출
- 실제 AWS 없이 Stubber와 fake로 전체 CLI 시나리오 재현

CLI 증거:

```powershell
aws_connect_cli profile list --output json
aws_connect_cli auth status --profile brandbay-dev --output json
aws_connect_cli auth validate --profile brandbay-dev --output json
```

### Phase 2. EC2 SSM CLI

목표: gossm과 AWS CLI 없이 EC2 목록 조회와 접속을 CLI에서 검증한다.

진행 상태(2026-09-09): **로컬 완료, 실제 AWS/동봉 Plugin 확인 대기**

- [x] SSM Online EC2 조회, pagination과 EC2 Name/Private IP 보강 구현
- [x] EC2 `DescribeInstances` 권한 부족 시 SSM 정보 fallback 구현
- [x] 명시적 임시 자격증명을 사용하는 boto3 Start/TerminateSession gateway 구현
- [x] Plugin 경로·Windows 실행 환경·최소 버전 `1.2.0.0` doctor 구현
- [x] 공식 Plugin argv와 진단용 마스킹 snapshot 구현
- [x] 현재 콘솔 foreground 실행 및 정상·오류·Ctrl+C 정리 구현
- [x] `ec2 list|connect` text/JSON/exit-code CLI 연결
- [x] fake, botocore Stubber와 process fake 회귀 테스트 구현
- [ ] 승인된 최소 권한 계정과 동봉 Plugin으로 실제 EC2 대화형 연결 확인

검증 기록:

- `./scripts/check.ps1` → 로컬 전체 게이트 성공, 70 tests, 전체 coverage 86% 이상
- SSM Online/Offline·pagination, Name 없음, EC2 Describe 권한 부족을 Stubber/fake로 검증
- Plugin 누락·구버전·실행 환경 오류, 비정상 종료, argv 마스킹과 Ctrl+C
  terminate/kill을 process fake로 검증
- 실제 AWS/Plugin smoke는 승인된 프로필과 대상 인스턴스가 없어 실행하지 않았다.
  `AWS_CONNECT_ALLOW_AWS_SMOKE=1` 설정 후
  `./scripts/test-aws.ps1 -Profile <profile> -InstanceId <instance-id> -Connect`로 재현한다.
- 공식 Plugin 계약상 단기 `TokenValue`와 `StreamUrl`은 Plugin argv에 전달해야 한다.
  애플리케이션은 이를 repr·출력·로그·진단 snapshot에 노출하지 않으며 Phase 6의 깨끗한
  Windows 패키지 검사에서 프로세스 진단 도구 노출 범위를 별도 확인한다.

작업:

- SSM Online 대상 조회와 EC2 Name 태그 보강
- Describe 권한 부족 fallback
- Session Manager `start_session` gateway
- Plugin 명령 인자 조립과 민감값 마스킹
- CLI 현재 콘솔 연결과 안전한 종료
- `ec2 list|connect` CLI 구현
- Plugin 존재·버전·실행 환경을 확인하는 `doctor` 확장

게이트:

- SSM Online/Offline, Name 태그 없음, Describe 권한 없음 검증
- Session Manager Plugin 인자 snapshot 검증
- Plugin 누락·구버전·비정상 종료 오류 mapping 검증
- CLI 접속 종료 후 자식 프로세스와 세션 정리 검증
- 승인된 테스트 계정에서 실제 EC2 연결 smoke test

다음 단계: Phase 3의 RDS 저장 세션과 foreground 터널 CLI를 구현한다.

### Phase 3. RDS 저장 세션과 터널 CLI

목표: PuTTY Saved Session과 유사한 RDS 터널 재사용 흐름을 CLI에서 완성한다.

진행 상태(2026-09-11): **로컬 완료, 실제 AWS/RDS/동봉 Plugin 확인 대기**

- [x] `TunnelSession` host·port·fixed/select target 불변식 구현
- [x] migration v2와 프로필별 이름 유일 SQLite CRUD·cascade 구현
- [x] 로컬 포트 점유를 인증·AWS·Plugin 호출 전에 검사
- [x] SSM remote-host document와 SDK-native `Parameters` mapping 구현
- [x] EC2/RDS가 공유하는 foreground SSM Plugin·세션 소유/정리 runner 구현
- [x] 고정 EC2와 실행 시 선택 EC2의 Online 검증 구현
- [x] `rds session list|show|create|update|clone|delete` 및 `rds tunnel start` CLI 구현
- [x] 복제 시 새 이름을 필수로 받고 원본 설정만 복사하며 ID·생성/수정 시각·최근 사용
  이력을 새 레코드로 분리하는 Application/CLI/GUI 흐름 구현
- [x] MFA 완료 전 터널 side effect 차단 및 원 요청 1회 재개 구현
- [x] detached `status|stop` 명령이 MVP에 없음을 parser test로 고정
- [x] fake, Stubber, process, SQLite와 JSON quoting 회귀 테스트 통과
- [ ] 승인된 최소 권한 계정과 동봉 Plugin으로 실제 RDS 터널 smoke 확인

검증 기록:

- `./scripts/check.ps1` → 로컬 전체 게이트 성공. 98 tests, 전체 coverage 87.09%
- 2026-09-11 최종 감사에서 FR-RDS-001의 복제 누락을 확인해 공통
  `TunnelSessionService.clone`을 추가했다. CLI `rds session clone <selector> --name <new-name>`과
  GUI의 명시적 새 이름 대화상자가 같은 use case를 사용하며, unit·SQLite·CLI·GUI 회귀
  테스트로 원본 비갱신과 독립 ID/사용 이력을 검증했다.
- `uv run pytest tests/unit/application/test_rds_tunnel_service.py
  tests/integration/infrastructure/test_sqlite_profile_store.py tests/adapter/cli/test_rds_cli.py
  tests/adapter/gui/test_ec2_rds.py -q --no-cov` → 30 passed. 복제 성공·중복 이름 오류 JSON,
  CLI help 계약, GUI 확인/취소와 SQLite 이력 분리를 함께 검증했다.
- `./scripts/check.ps1` → 209 passed, 전체 coverage 84.06%; docs, format, Ruff, import
  boundary, mypy, detect-secrets, Bandit와 pip-audit를 포함한 전체 게이트 성공.
- 세션 복제는 로컬 SQLite CRUD만 수행하므로 AWS·Plugin·포트·프로세스 side effect가 없으며
  실제 AWS smoke의 추가 범위가 아니다.
- `StartSession`은 `Parameters={host=[...], portNumber=[...], localPortNumber=[...]}`를
  boto3에 dict로 전달하고, Plugin 경계에서만 JSON 직렬화함을 Stubber와 JSON parse로 검증
- 포트 점유 시 `SessionGuard`, AWS target 조회, StartSession과 Plugin이 모두 호출되지 않음을
  fake로 검증
- Plugin 정상/비정상 종료와 `Ctrl+C`에서 `TerminateSession`이 호출되고, Plugin process는
  기존 adapter의 terminate/kill 회귀 테스트로 검증
- 실제 smoke는 승인된 프로필, Online SSM EC2, 접근 가능한 RDS host와 동봉 Plugin이 없어
  실행하지 않았다. 저장 세션 생성 후 `AWS_CONNECT_ALLOW_AWS_SMOKE=1`을 설정하고
  `./scripts/test-aws.ps1 -Profile <profile> -RdsSession <session> -Tunnel`로 재현한다.
  `target_mode=select`이면 `-TargetInstanceId <instance-id>`를 함께 지정한다.

작업:

- `TunnelSession` 불변식과 SQLite CRUD
- host, remote/local port와 세션명 검증
- 중계 EC2 고정/실행 시 선택
- 로컬 포트 점유 검사
- `AWS-StartPortForwardingSessionToRemoteHost` 요청 생성
- `TunnelHandle` 상태와 프로세스 소유권 구현
- `rds session list|show|create|update|clone|delete` CLI 구현
- foreground `rds tunnel start`와 `Ctrl+C` 정리 구현

게이트:

- RDS Describe 권한 없이 저장 세션으로 연결
- 포트 충돌 시 Plugin 실행 전 차단
- 올바른 JSON parameters와 Session Manager 요청 검증
- MFA 후 터널 시작이 중복 실행되지 않음
- `Ctrl+C`, 오류, 정상 종료에서 Plugin과 SSM 세션 정리
- 승인된 테스트 계정에서 실제 RDS 터널 smoke test

MVP에서는 detached CLI 터널과 다른 프로세스의 `status|stop`을 지원하지 않는다.

다음 단계: Phase 4의 GUI 셸, 프로필·인증·대시보드를 구현한다.

### Phase 4. GUI 셸, 프로필·인증·대시보드

목표: 확정된 목업을 기준으로 검증된 공통 Application 기능을 GUI에 연결한다.

진행 상태(2026-09-10): **로컬 자동 검증 완료, 실제 AWS·사람 눈 visual QA 대기**

- [x] PySide6 앱 셸, 고정 인증 헤더, 탐색 영역과 기능 대시보드 구현
- [x] 프로필 목록·선택·CRUD 모달과 마스킹된 Access/Secret 입력 구현
- [x] 프로필 MFA 장치 ARN 기본값·직접 수정·기존 `SaveProfileRequest` 저장 흐름 구현
- [x] 앱 시작 시 로컬 상태 표시 후 AWS 검증을 별도 worker 작업으로 실행
- [x] MFA 모달 취소·재개와 프로필 전환 후 헤더·기능 데이터 갱신 구현
- [x] 중앙 GUI 오류 mapper와 앱 생존형 알림 구현
- [x] 중앙 mapper의 `FIELD`·`TOAST`·`DIALOG` 표시 종류를 실제 필드 강조·앱 내부
  알림·비차단 오류 모달로 연결
- [x] `QRunnable` worker, event signal과 공통 `CancellationToken` 경계 구현
- [x] GUI의 boto3·SQLite·Infrastructure·CLI adapter 직접 의존 금지 검사 추가
- [x] Qt offscreen `1024 × 720` clipping·overlap geometry 검사 통과
- [ ] 승인된 실제 AWS 프로필에서 프로필 검증과 MFA 갱신 visual smoke 확인
- [ ] Windows 10/11 디스플레이 배율 100%·125%에서 사람 눈 visual QA

검증 기록:

- `./scripts/check.ps1` → 성공; 106 tests, 전체 coverage 85.80%, Ruff, mypy,
  architecture/import 계약, 문서 drift, secret scan, Bandit, dependency audit 통과
- `python -m pytest tests/adapter/gui -q --no-cov` → 8 passed; 인증 상태 view model,
  MFA 단일 재개, 프로필 전환 갱신, 오류 후 앱 생존, worker 비-GUI-thread 실행,
  취소, `1024 × 720` geometry 검증
- 실제 AWS visual smoke는 이 환경에 승인된 자격증명과 MFA 장치가 없어 실행하지 않았다.
  후속 확인은 `aws-connect`를 실행해 프로필 등록 → 프로필 연결 → MFA 입력 →
  인증 헤더의 Account ID·IAM 사용자·만료 시간 갱신을 확인하고, 실패하는 MFA를
  한 번 입력한 뒤에도 창과 프로필 관리가 유지되는지 확인한다.
- 디스플레이 배율 visual QA는 Windows 설정을 100%와 125%로 각각 변경한 뒤 창을
  `1024 × 720`로 맞추고 인증 헤더, 두 열 기능 카드, 활성 터널 요약, 프로필 모달의
  입력·하단 작업 버튼이 잘리거나 겹치지 않는지 확인한다.

작업:

- GUI framework 확정과 앱 셸 구현
- 상단 프로필·인증·Account·IAM·만료 정보 구현
- 프로필 CRUD, 선택과 연결 모달 구현
- MFA 모달과 원래 작업 재개 연결
- 대시보드와 활성 RDS 터널 요약 구현
- 중앙 GUI error mapper와 알림 구현
- worker/event 기반 비동기 실행과 취소 구현

게이트:

- GUI에 AWS·SQLite·DPAPI 직접 호출이 없음
- GUI가 CLI 프로세스와 출력에 의존하지 않음
- fake Application Service로 모든 인증 상태 재현
- UI thread가 AWS 또는 파일 I/O로 멈추지 않음
- `1024 × 720`에서 목업 핵심 요소가 겹치거나 잘리지 않음
- 프로필 변경 후 공통 상단 정보와 기능 데이터 갱신

### Phase 5. EC2와 RDS GUI

목표: Phase 2와 3에서 검증한 기능을 새 비즈니스 로직 없이 GUI에 연결한다.

진행 상태(2026-09-10): **로컬 자동 검증 완료, 실제 AWS·Windows Terminal·Plugin visual smoke 대기**

- [x] EC2 Name/ID/Private IP/Platform 필터, Online 목록, 단일 선택과 새로고침 구현
- [x] EC2 Region·SSM Online·키워드 control을 Application의 Region 검증과
  `Ec2TargetFilter` 규칙에 연결하고 GUI worker 시작 전 입력 snapshot 구현
- [x] CLI 호출·출력 파싱 없이 공통 `Ec2Service.connect_external`을 Windows Terminal에 연결
- [x] `ExternalSessionHandle`의 operation/process/SSM session/state 추적 구현
- [x] 지원되지 않는 Windows Terminal `--wait`를 제거하고 전용 session host가 실제
  Plugin PID·종료 코드를 AF_PIPE로 보고하도록 구현
- [x] 매 실행 256-bit pipe 인증키와 Current User DPAPI 보호 전달을 구현하고
  `StreamUrl`·`TokenValue`가 WT/helper argv·로그·임시 파일에 남지 않도록 검증
- [x] RDS 저장 세션 검색·목록·생성·수정·삭제와 fixed/select 중계 EC2 구현
- [x] 공통 `StartTunnelRequest`와 MFA 단일 재개를 GUI-owned 비차단 터널에 연결
- [x] `TunnelHandle` 실행 상태, 개별 종료, 대시보드·RDS 화면 동기화 구현
- [x] RDS 화면과 대시보드에 터널별 `127.0.0.1:<port>` 표시·30초 조건부 복사·
  개별 종료 action 구현
- [x] GUI 종료 시 RDS `stop_all`을 worker에서 실행하고 EC2 외부 세션은 유지
- [x] 오류 뒤 앱 생존, 프로필 변경 새로고침과 `1024 × 720` 회귀 검사 통과
- [x] handshake/IPC EOF/process stop 실패에서 listener·connection·dispatch와 AWS
  세션을 정리하고, RDS `stop_all`이 첫 실패 뒤에도 모든 소유 터널을 시도하도록 검증
- [x] managed SSM launch·observer·poll·natural exit·explicit stop·EndSession의 최초 typed
  오류와 cleanup 진단 보존, terminal 이후 임시 자격증명 소유 상태 제거를 검증
- [x] EC2/RDS가 공통 operation identity·progress·cancel 계약을 유지하고 terminal 상태를
  한 번 전달한 뒤 feature metadata를 제거하며 중첩 `OperationResult`를 만들지 않도록 검증
- [x] 프로필 삭제 전 EC2/RDS 상태를 모두 조회하고, 활성 연결 stop 선택 시 두 cleanup을
  모두 시도하며 조회·정리 실패 때 삭제하지 않는 Application/GUI/bootstrap 계약을 검증
- [ ] 승인된 실제 AWS 계정과 동봉 Plugin으로 EC2/RDS visual smoke 확인

검증 기록:

- 2026-09-14 세션 수명주기 감사에서 partial regression fixture의 누락 import와 잘못된
  instance-id를 복구하고, RDS 취소 검사를 포트·자격증명·AWS 조회보다 앞으로 이동했다.
  `uv --cache-dir .uv-cache run pytest tests/unit/application/test_managed_ssm_session.py
  tests/unit/application/test_ec2_service.py tests/unit/application/test_rds_tunnel_service.py
  tests/unit/application/test_connection_lifecycle.py tests/adapter/gui/test_shell.py
  tests/adapter/gui/test_ec2_rds.py tests/adapter/cli/test_ec2_cli.py
  tests/adapter/cli/test_rds_cli.py tests/adapter/test_presentation_consistency.py
  -q --no-cov -p no:cacheprovider` → 78 passed. 최초 typed 오류와 후속 cleanup 진단,
  terminal snapshot 1회/metadata 제거, flat foreground/managed result, GUI 삭제 선택과
  `gui_main` composition 전달을 fault-injection/fake로 검증했다.
- `./scripts/check.ps1` → dependency audit 직전까지 성공: documentation/generated drift,
  Ruff format/lint, architecture/import 경계, mypy, 264 tests(coverage 83.92%), secret scan,
  Bandit 통과. 마지막 `pip-audit`만 관리형 환경의 PyPI 소켓 접근 거부
  (`WinError 10013`, `pypi.org:443`)로 실행하지 못했으며 코드·테스트 실패는 아니다.
  네트워크가 허용된 CI/개발 환경에서 동일 명령의 dependency audit 재실행이 필요하다.
- 감독 보완 후 `./scripts/check.ps1` → 성공; 124 tests, 전체 coverage 83.21%,
  Ruff, mypy, architecture/import 계약, 문서 drift, secret scan, Bandit,
  dependency audit 통과. 관리형 샌드박스의 공유 TEMP/uv cache ACL 충돌을 피하기 위해
  고유한 workspace-local basetemp와 기존 동기화된 venv를 사용했으며, 실행 후 해당
  임시 경로를 삭제했다.
- `tests/adapter/gui/test_ec2_rds.py`는 EC2 필터·선택·외부 연결 요청, 오류 복구,
  RDS CRUD·fixed/select 선택·시작/종료, 대시보드 projection, 프로필 변경,
  `MainWindow.closeEvent` 정리와 `1024 × 720` feature page geometry를 fake로 검증한다.
- `tests/unit/application/test_managed_ssm_session.py`와 EC2/RDS Application 테스트는
  process poll/terminate/kill, AWS EndSession 단일 실행, handle 상태와 소유권을 검증한다.
- `tests/unit/infrastructure/test_session_manager_plugin.py`와 `test_session_host.py`는
  WT argv 지원 옵션, raw transport 값 부재, 독립된 DPAPI 보호 pipe 인증키,
  실제 Plugin PID/exit 보고, handshake 실패·IPC EOF의 listener/connection/dispatch
  정리를 검증한다. 파일 기반 IPC를 사용하지 않으므로 테스트 후 임시 key/payload
  artifact가 생성되지 않는다.
- 실제 smoke는 이 환경에 승인된 AWS 프로필, Online SSM EC2, 접근 가능한 RDS,
  동봉 `session-manager-plugin.exe`가 없어 실행하지 않았다. 후속 확인 절차는 다음과 같다.
  1. Portable 폴더에서 `aws-connect`를 실행하고 승인된 테스트 프로필을 연결한다.
  2. EC2 화면에서 목록 새로고침 → Name/IP 필터 → 대상 선택 → 터미널 열기를 실행한다.
   3. Windows Terminal 탭에서 셸 입력을 확인하고 GUI의 PID/running 표시가 탭 종료 후
      closed로 바뀌는지 확인한다. GUI를 먼저 닫아도 해당 EC2 탭이 유지되어야 한다.
      Process Explorer 등으로 WT와 `aws_connect_session_host.exe`의 command line에
      `StreamUrl`·`TokenValue`가 없고 pipe key가 평문이 아닌 DPAPI ciphertext인지 확인한다.
      공식 Plugin 자체의 최종 argv 노출은 upstream 계약상 불가피하므로 로그·dump를
      수집하지 않고 테스트 계정의 단기 세션으로만 이 검사를 수행한다.
  4. RDS 화면에서 fixed와 select 세션을 각각 저장·시작하고 대시보드와 RDS 화면의
     `127.0.0.1:local → host:remote`가 일치하는지 확인한다.
  5. `Test-NetConnection 127.0.0.1 -Port <local-port>`로 연결을 확인하고 개별 종료 후
     포트가 해제되는지 확인한다. 다시 시작한 뒤 GUI를 닫아도 포트가 해제되어야 한다.
   6. Offline EC2와 이미 사용 중인 포트로 실패시켜 CLI의 message code와 GUI 중앙
      error mapper의 안내가 같은 Application 오류를 표현하고 앱이 유지되는지 확인한다.

감독 리뷰 결정(2026-09-10):

- Windows Terminal의 공식 명령행 계약을 재확인하여 `-w new`, `new-tab`, `--title`,
  `--suppressApplicationTitle`만 사용하고 지원되지 않는 `--wait`는 사용하지 않는다.
- `.phase5-ipc-tmp/`와 `.phase5-ipc-cache/`는 제품 artifact가 아니라 중단된 pytest의
  고정 basetemp/cache 잔재로 확인했다. workspace 내부 절대경로를 검증한 뒤 삭제했고,
  회귀·전체 검증은 고유한 `.tmp/phase5-*` 경로를 사용해 `finally`에서 정리했다.
- GUI는 여전히 `Ec2Service`와 `RdsTunnelOperationCoordinator`만 호출하며 CLI 실행이나
  출력 파싱을 하지 않는다. EC2는 helper handshake 뒤 사용자 소유로 전환되고,
  RDS만 GUI 종료의 `stop_all` 정리 대상이다.

작업:

- EC2 필터, 목록, 선택과 외부 Windows Terminal 실행
- `ExternalSessionHandle` 상태 표시
- RDS 저장 세션 목록과 편집 화면
- 터널 시작·진행·연결·오류·종료 상태 표시
- 대시보드와 RDS 화면의 터널 상태 동기화
- 앱 종료 시 소유 RDS 터널 정리

게이트:

- CLI와 GUI가 같은 요청 DTO와 Application Service 사용
- EC2 외부 터미널은 GUI 종료 후 유지
- GUI 소유 RDS 터널은 GUI 종료 시 정리
- 오류별 GUI 후속 동작이 중앙 mapping 표와 일치
- 실제 AWS smoke 시나리오가 CLI와 GUI에서 같은 결과 코드 사용

### Phase 6. 설정·로그와 MVP 패키징

목표: AWS Connect MVP를 AWS CLI, Python, gossm 설치 없이 실행 가능한 Portable ZIP으로 만든다.

진행 상태(2026-09-10): **로컬 TEST-ONLY 패키지 완료, 실제 공급망·깨끗한 VM 확인 대기**

- [x] SQLite migration v3 기반 로그 경로·수준 설정과 CLI `settings` 계약 구현
- [x] 중앙 마스킹 filter를 거치는 size/count 순환 로그와 진단 ZIP 재마스킹 구현
- [x] `aws_info.ini` metadata preview → 명시적 confirm apply와 원본 불변성 구현
- [x] `doctor`의 DB/migration, DPAPI, 로그, Plugin, 기본 profile/session/region,
  sibling session host와 민감 argv 경계 진단 구현
- [x] 세 EXE sibling onedir, LICENSE/NOTICE/third-party inventory, VERSION,
  build manifest, payload/ZIP checksum과 Portable ZIP 생성
- [x] GUI·CLI·session host가 같은 user-data/settings/plugin locator를 사용하는 composition 구현
- [x] TEST-ONLY vendor의 명시적 opt-in build/smoke와 production fail-closed gate 구현
- [x] 로컬 패키지 smoke에서 한글·공백 경로, read-only 앱 폴더, 격리 사용자 데이터,
  무 Python/AWS CLI/gossm, 설정 재개방, 세 EXE, DB/로그 평문 secret 부재 검증
- [ ] 승인된 실제 Session Manager Plugin binary/hash/version/LICENSE/NOTICE로 production build
- [ ] 깨끗한 Windows 10/11 VM에서 production ZIP과 10분 내 첫 프로필 등록 검증

검증 기록:

- 승인된 네트워크에서 최종 `./scripts/check.ps1` → 성공; docs, Ruff/format,
  architecture/import, mypy, 141 tests, overall coverage 82.76%, secret scan, Bandit,
  pip-audit(`No known vulnerabilities found`)까지 전체 통과. 로컬 `aws-connect`만 PyPI에
  게시되지 않은 프로젝트이므로 audit 대상에서 명시적으로 skip되었다.
- `./scripts/build-package.ps1 -VendorDirectory ./build/test-vendor -AllowTestVendor` → 성공;
  `dist/aws-connect-0.1.0-TEST-ONLY-windows-x64.zip`, ZIP SHA-256
  `2c1bc4c91046d52cf0cd9849f20396ec7708cf537a282aeef29941b9515c18b0`.
- `./scripts/test-package.ps1 -ZipPath ./dist/aws-connect-0.1.0-TEST-ONLY-windows-x64.zip
  -AllowTestVendorPackage` → 성공. `PATH`에서 Python/AWS CLI/gossm/Plugin을 제거하고,
  한글·공백 경로의 read-only package에서 CLI doctor, settings update/reopen, GUI offscreen,
  helper fail-closed 실행, sibling Plugin/helper, DB schema v3·DPAPI와 DB/로그 secret scan을 검증했다.
- 최초 package smoke의 `Access Denied`는 제품 EXE 결함이 아니라 smoke가 `icacls /T`로
  각 파일에 inherit-only ACE를 적용한 하네스 결함이었다. 앱 루트에 상속 가능한 RX ACL만
  적용하도록 수정한 뒤 동일 EXE가 exit 0으로 실행되고 최종 smoke가 통과했다.
- 관리형 sandbox가 남긴 고정 pytest 임시 폴더 ACL 때문에 중간 전체 검사가 실패했으며,
  check가 실행별 저장소 직하 고유 basetemp/cache만 사용하고 정리하도록 변경했다. Ruff도
  ACL 제한 smoke 디렉터리를 순회하지 않도록 source-controlled Python roots만 검사한다.
- 실제 Plugin 입력 누락, TEST-ONLY vendor의 production build 사용, TEST-ONLY ZIP의 일반
  smoke 사용을 각각 실행해 모두 명확한 오류로 실패함을 확인했다. 합성 Plugin은
  `approved=false`, `test_only=true`이고 산출물·manifest에 `TEST-ONLY`가 유지된다.
- 실제 공급망/VM 확인은 `packaging/README.md` 절차와 `PKG-001`~`PKG-003` 후속 항목으로
  남겼다. CI package job은 동일 TEST-ONLY gate를 실행하고 실패 로그/warn 파일을
  always-run artifact로 보존한다. 원격 push 전이므로 GitHub runner 실행은 아직 확인하지 않았다.
- 2026-09-11 감독 최종 감사에서 인증 없이 사용하는 GUI 실행 로그/설정 화면을 연결했다.
  현재 로그 경로·수준, 저장/기본값 복원, 최근 마스킹 이벤트 새로고침, 로그 폴더 열기와
  명시적 진단 ZIP 내보내기를 공통 Application service와 GUI worker로 실행한다. 설정 저장
  실패 시 Application이 이전 handler를 복구하고 GUI도 실제 저장값을 다시 불러온다.
- 관리 로그 reader는 설정된 폴더의 고정 파일명만 최대 8개/1 MiB 범위에서 읽고, 파일당
  256 KiB·필드 160자·결과 500행 상한을 적용한다. 누락·손상·읽기 불가 파일은 안전하게
  건너뛰며 각 필드를 중앙 정책으로 다시 마스킹한다. CLI/GUI 작업 결과는 arguments나
  credential/Secret/file payload를 받지 않는 고정 event schema로 기록한다.
- 최종 감사 narrow test(`pytest ... --no-cov`) → 30 passed. 승인된 네트워크에서
  `./scripts/check.ps1` → 225 passed, overall coverage 84.35%, 문서/Ruff/architecture/import/
  mypy/secret scan/Bandit/pip-audit 모두 통과했다.
- RDS session clone과 실행 로그 GUI를 함께 포함해 TEST-ONLY Portable ZIP을 재빌드했다.
  SHA-256 `ba7d0306fa85262090d00ed567694b2e0ead1ff9ebb63ae84acddb5c4c166757`.
  `./scripts/test-package.ps1 ... -AllowTestVendorPackage` → 무 Python/AWS CLI/gossm,
  한글·공백 및 read-only 실행 경로, 격리 LOCALAPPDATA, 설정 persistence, GUI 시작,
  sibling helper/Plugin과 DB/로그 secret scan까지 성공했다. 이후 코드가 변경되면 이
  산출물은 stale이므로 최종 배포 후보는 반드시 다시 빌드하고 같은 smoke를 반복한다.
- 2026-09-11 개발 GUI 실행 점검에서 `rds_tunnel_service.py`의 `StrEnum` import 누락을
  복구했다. `.venv\Scripts\python.exe -c "import aws_connect.gui_main"`은 성공했다.
  관련 RDS 단위 테스트는 코드에 없는 이전 `TunnelState`를 import해 수집 실패했으며,
  `OperationState` 계약으로 테스트를 정렬하는 작업이 다음 검증 단계다.

작업:

- 로그 경로·수준 설정과 순환 로그
- 진단 로그 내보내기와 재마스킹
- 기존 `aws_info.ini` 가져오기와 원본 보존
- `aws_connect.exe`, `aws_connect_cli.exe`, `aws_connect_session_host.exe` onedir 빌드.
  GUI executable과 session host는 같은 배포 폴더에 있어야 한다.
- Session Manager Plugin, LICENSE/NOTICE와 third-party license 포함
- 버전, checksum과 빌드 manifest 생성

게이트:

- Python, AWS CLI, gossm, Plugin 미설치 Windows VM에서 실행
- 한글·공백 경로, 읽기 전용 실행 폴더와 사용자 데이터 경로 검증
- DB와 로그에 비밀 원문이 없음
- GUI·CLI가 동일한 DB, 로그 설정과 Plugin을 사용
- frozen EC2 실행이 sibling `aws_connect_session_host.exe`를 찾고 WT/helper argv에
  raw Plugin session payload를 포함하지 않음
- Portable ZIP 압축 해제 후 10분 안에 첫 프로필 등록

이 단계가 PRD의 MVP 완료 지점이다.

### Phase 7. Secrets Manager

목표: 목록 권한이 없는 계정에서도 Secret 이름 또는 ARN으로 안전하게 조회한다.

진행 상태(2026-09-10): **로컬 완료, 실제 AWS 최소 권한 확인 대기**

- [x] 이름/ARN `GetSecretValue`, JSON·일반 문자열 해석과 재귀 민감 필드 마스킹 구현
- [x] 선택적으로만 실행하는 pagination `ListSecrets` 메타데이터 조회 구현
- [x] `secrets get` 기본 text/JSON 마스킹과 명시적 `--reveal` 계약 구현
- [x] GUI 직접 입력, 선택 목록, 필드별 일시 표시·복사 및 30초 정리 구현
- [x] top-level `host`, `port`만 기존 RDS 미저장 편집기로 복사하도록 연결
- [x] typed AWS 오류와 중앙 CLI/GUI mapper 경계 유지
- [x] Application fake, boto3 Stubber, CLI/GUI 및 원문 비노출 회귀 테스트 구현
- [ ] 승인된 `GetSecretValue` 전용 non-production 프로필로 실제 AWS smoke 확인

검증 기록:

- `uv run pytest tests/unit/application/test_secrets_service.py tests/integration/infrastructure/test_aws_secrets_gateway.py tests/adapter/cli/test_secrets_cli.py tests/adapter/gui/test_secrets.py -q --no-cov` → 21 passed
- `./scripts/check.ps1` → 전체 게이트 성공; 162 passed, overall coverage 83.60%,
  Secrets Application 97%, boto3 adapter 92%, GUI adapter 86%; docs, Ruff, architecture,
  mypy, detect-secrets, Bandit, pip-audit 통과
- `./scripts/build-package.ps1 -VendorDirectory ./.test-vendor-phase7 -AllowTestVendor` →
  TEST-ONLY 3-EXE ZIP 생성 성공; SHA-256
  `d46f0de11f08ca29ce641abaf01b46e3dc55adad239806cb8c632d842d9454c5`
- `./scripts/test-package.ps1 -ZipPath ./dist/aws-connect-0.1.0-TEST-ONLY-windows-x64.zip -AllowTestVendorPackage`
  → Python/AWS CLI/gossm 없는 PATH, 한글·공백, read-only 앱 경로, 격리된
  LOCALAPPDATA, GUI 시작, 3 EXE/Plugin/DB·로그 secret scan smoke 성공
- `GetSecretValue` 테스트는 `ListSecrets` 호출을 실패하도록 설정해도 직접 조회가 성공하며,
  목록 권한 실패 후에도 GUI 직접 입력 버튼이 활성 상태임을 검증한다.
- 기본 CLI text/JSON과 GUI 테이블에서 민감 필드 및 일반 문자열 원문이 보이지 않고,
  typed 오류의 기술 원인에도 원문이 직렬화되지 않음을 검증한다.
- 실제 AWS는 승인된 프로필이 없어 실행하지 않았다. `AWS_CONNECT_ALLOW_AWS_SMOKE=1`
  설정 후 `./scripts/test-aws.ps1 -Profile <profile> -SecretId <name-or-arn>`으로 재현한다.
  이 스크립트는 `--reveal`을 사용하지 않는다. 상세 권한 조합은 `AWS-002`에 기록했다.

순서:

1. Secret 조회·JSON 해석·민감 필드 마스킹 Application 구현
2. `secrets get` CLI와 오류·권한 시나리오 검증
3. GUI 목록·직접 입력·결과 표시 연결
4. 선택한 `host`, `port`를 RDS 세션으로 복사

게이트:

- `ListSecrets` 없이 `GetSecretValue`만으로 동작
- 평문 Secret이 DB, 로그와 기본 CLI/GUI 출력에 남지 않음
- 명시적 reveal/copy 동작만 원문 접근 허용

다음 단계: Phase 8의 S3 저장 위치, 객체 목록과 안전한 업로드 수직 슬라이스를 구현한다.

### Phase 8. S3

목표: 지정 Bucket/Prefix 목록과 안전한 파일 업로드를 제공한다.

진행 상태(2026-09-11): **로컬 완료, 실제 AWS 대용량·취소 확인 대기**

- [x] 프로필별 S3 Bucket/Prefix 저장 위치 CRUD와 SQLite migration v4 구현
- [x] 직접 Bucket/Prefix 및 `ListObjectsV2` delimiter pagination 탐색 구현
- [x] 단일·다중 파일 exact-key 존재 확인과 최종 URI preflight 구현
- [x] preview 기본값, 명시적 confirm 및 기존 key 별도 overwrite 동의 구현
- [x] 16 MiB 기준, 8 MiB part, 동시성 1의 bounded upload 구현
- [x] cancel/실패 후 multipart abort와 abort 실패 diagnostic 보존 구현
- [x] 공통 `ProgressEvent.target`과 `CancellationToken`을 CLI/GUI에 연결
- [x] human CLI 실시간 stderr 진행과 JSON stdout 최종 event 배열을 분리
- [x] 자동 재시도 없이 명시적 재실행의 새 operation/upload ID 및 progress 초기화 검증
- [x] 부분 성공 요약 보존과 stale preflight 대상의 overwrite 재동의 구현
- [x] GUI 저장 위치, 상위 Prefix 탐색, 파일 선택·drop, 파일별 진행률·취소 구현
- [x] GUI 종료가 multipart 정리를 포함한 취소 완료를 기다리도록 구현
- [x] S3 typed 오류와 중앙 CLI/GUI mapper, boto3/SQLite/보안 회귀 테스트 구현
- [x] 저장 위치 update가 요청 profile과 기존 location 소유권이 다르면 갱신 전
  `s3.location.not_found`로 거부하는 Application invariant 구현
- [x] `ListBuckets`, `HeadObject`, 원격 `DeleteObject` 부재를 architecture test로 고정
- [ ] 승인된 최소 권한 non-production S3에서 실제 대용량·취소 smoke 확인

검증 기록:

- `uv run pytest tests/unit/domain/test_s3_location.py tests/unit/application/test_s3_service.py tests/integration/infrastructure/test_aws_s3_gateway.py tests/integration/infrastructure/test_sqlite_profile_store.py tests/adapter/cli/test_s3_cli.py tests/adapter/gui/test_s3.py tests/adapter/test_presentation_consistency.py tests/architecture/test_boundaries.py tests/adapter/test_error_mapper_completeness.py -q --no-cov` → 50 passed
- `./scripts/check.ps1` → 전체 게이트 성공; 203 passed, overall coverage 83.77%,
  S3 Application 100%, boto3 S3 adapter 90%; docs, Ruff, architecture/import boundaries,
  mypy, detect-secrets, Bandit, pip-audit 통과
- `./scripts/build-package.ps1 -VendorDirectory ./build/test-vendor -AllowTestVendor` →
  중단 산출물을 제거한 clean PyInstaller onedir 3-EXE TEST-ONLY ZIP 빌드 성공;
  SHA-256 `197d893e5b4f4002ade49762bd26079439c6f308915ab57b0b2d59064bbc95be`
- `./scripts/test-package.ps1 -ZipPath ./dist/aws-connect-0.1.0-TEST-ONLY-windows-x64.zip -AllowTestVendorPackage`
  → Python/AWS CLI/gossm 없는 PATH, 한글·공백, read-only 앱 경로, 격리된
  LOCALAPPDATA, 3 EXE, S3 CLI 안전 옵션, GUI 시작, DB·로그 secret scan 성공
- multipart create/upload/complete 각 실패 action을 구분하고, create 이후의 실패·취소는
  항상 abort를 시도하며 abort 자체가 실패해도 최초 typed 오류 code/action/cause를 보존한다.
- human CLI가 작업 중 `target URI: completed/total bytes`를 stderr에 즉시 쓰고 JSON 모드는
  중간 stdout/stderr 없이 최종 progress 배열만 반환함을 adapter test로 검증했다. GUI projection도
  동일한 `ProgressEvent`의 operation/target/completed/total을 소비함을 일관성 테스트로 고정했다.
- 실패한 multipart를 자동 재시도하지 않으며 같은 plan을 사용자가 다시 실행할 때 새 upload ID와
  operation ID, 0 progress로 성공함을 fake/adapter에서 검증했다. 다중 파일 부분 실패는 완료 URI와
  byte summary를 보존하고, 재실행 전에 새로 생긴 key는 overwrite 동의를 다시 요구한다.
- 2026-09-14 UI·로컬 보안 보완 focused suite는 Application EC2/S3, GUI EC2/RDS·shell·
  Secrets, SQLite/DPAPI/log/diagnostic ACL을 함께 실행해 `83 passed`였다. 전체
  `./scripts/check.ps1`은 문서/generated drift, Ruff, architecture/import, mypy,
  `280 passed`와 coverage `85.36%`, secret scan, Bandit, pip-audit
  (`No known vulnerabilities found`)까지 모두 통과했다.
- 실제 AWS와 깨끗한 별도 Windows VM은 사용할 승인 프로필·VM이 없어 실행하지 않았다.
  S3 List/Put/multipart 대용량·Ctrl+C 취소는 `AWS-003`, production Plugin 및 clean VM은
  `PKG-001`, `PKG-002`의 명령과 확인 절차로 재현한다.
- 2026-09-14 최종 보안·품질 보완에서 보호된 current-user-only 부모 디렉터리에서 실제
  SQLite rollback journal, WAL/SHM과 `RotatingFileHandler` rollover 파일을 생성하고 SDDL의
  effective trustee가 현재 사용자 하나뿐임을 검증했다. 관련 focused suite는 `36 passed`였다.
- 핵심 branch 범위를 `authentication_service.py`, `authenticated_operation.py`,
  `masking.py`, CLI/GUI `errors.py` 5개 파일로 명시하고 pytest-cov XML parser를
  `scripts/check.ps1`에 연결했다. coverage 제외 설정 없이 5개 파일 모두 branch 100%다.
- `./scripts/check.ps1`은 문서/generated drift, Ruff, architecture/import, mypy,
  `302 passed`, overall line coverage `85.83%`, Application `94.40%`, Domain `95.56%`,
  핵심 branch 100%, secret scan과 Bandit까지 통과했다. 관리형 sandbox의 외부 소켓 차단
  (`WinError 10013`)으로 그 실행의 마지막 `pip-audit`만 실패했으며, 같은 lock 환경에서
  승인된 네트워크로 별도 실행한 `uv run --no-sync pip-audit`은
  `No known vulnerabilities found`로 통과했다.
- mutable DB/entropy/log 위치를 `%LOCALAPPDATA%\AWSConnect`로 제품·설계 문서와 일치시켰고,
  실제 빌드가 사용하지 않던 루트 `AWSConnect.spec`을 제거했다.

순서:

1. S3 위치, 객체 조회와 업로드 Application 구현
2. `s3 list|upload` CLI와 진행·취소 검증
3. GUI 탐색, 파일 선택, 진행률과 취소 연결
4. multipart 실패 정리와 재시도 검증

게이트:

- `ListAllMyBuckets` 없이 지정 Bucket 사용
- 덮어쓰기 전에 대상 URI 확인
- 취소·실패한 multipart upload 정리
- 진행 이벤트가 CLI와 GUI에서 같은 작업 상태를 표현

다음 단계: Phase 9의 목업 정합성 및 운영 UX 보완을 작은 수직 슬라이스로 나눠 구현한다.

### Phase 9. 목업 정합성 및 운영 UX 보완

목표: 프로필, 대시보드, EC2, RDS, Secrets, S3와 실행 로그를
`docs/design-docs/aws-connect-ui-mockup.html`의 정보 구조와 상호작용에 맞추고,
권한이 제한된 운영 계정에서도 복구 가능한 경로를 제공한다.

진행 상태(2026-09-15): **로컬 구현·자동/렌더 검증 완료, 실제 AWS·Windows 배율 visual smoke 대기**

가정과 결정:

- 요청의 EC2 `종료`는 복구할 수 없는 `terminated`가 아니라 `stopped(중지됨)`을 뜻한다.
  화면 문구도 `중지됨`으로 통일하며 `TerminateInstances`는 절대 호출하지 않는다.
- EC2 필터의 `전체/실행 중/중지됨`은 EC2 전원 상태이고 SSM Online/Offline은 화면 열 대신
  작업 버튼 상태에 반영한다. 실행 중이지만 SSM Offline인 인스턴스에만 `재부팅`, 중지된 인스턴스에는
  `시작`을 제공한다.
- RDS `새 세션`은 기존 세션을 수정하지 않는 생성 모드로 들어가며 편집값을 비우는 것이
  맞다. 다만 제목, 버튼 상태와 미저장 변경 확인으로 그 의미를 명확히 한다.
- Secrets의 EC2 경유 조회는 직접 `GetSecretValue` 실패 시 몰래 실행하는 fallback이 아니다.
  사용자가 Online 중계 EC2와 경유 조회를 명시적으로 선택한 경우에만 실행한다.

SSOT 검색 결과:

- `mfa_arn`, `access_key`, `secret_key`: 프로필 Domain/Application은 MFA ARN 자동 생성을
  이미 제공하고 GUI는 키 원문을 기존 프로필 편집기에 다시 채우지 않는다.
- `clone`, `delete`, `copy_selected_address`, `active_tunnels`: RDS 검색·복제·삭제·주소 복사와
  터널 수명주기는 이미 구현되어 있으므로 화면을 다시 만들지 않고 기존 동작을 재배치한다.
- `list_online`, `Ec2TargetFilter`: 현재 EC2 목록은 SSM Online만 반환한다. stopped 인스턴스와
  전원 상태는 기존 결과를 꾸미는 것만으로 얻을 수 없어 EC2 inventory port 확장이 필요하다.
- `SecretsService.get`, `list_secrets`: 직접 조회와 목록 권한 없는 입력 경로는 이미 있다.
  EC2 경유 조회만 별도 port가 필요하며 결과 파싱·마스킹은 기존 `_parse`를 재사용한다.
- `S3Page`, `list_objects`, `dropEvent`: 직접 Bucket/Prefix, 목록, 파일 선택·drop과 업로드는
  이미 있다. 현재 3열 테이블과 버튼 배치를 목업의 4열·breadcrumb·drop zone으로 바꾼다.
- `ActivityLogService`, `MaskedManagedLogReader`: bounded read와 중앙 마스킹은 이미 있다.
  상세 오류는 이 파이프라인의 고정 스키마를 확장하고 별도 로그 파서를 만들지 않는다.

권위 문서 선행 변경:

1. `docs/DESIGN.md`와 HTML 목업에서 프로필 MFA ARN 입력 제거, EC2 전원/SSM 이중 상태,
   즐겨찾기, RDS 단일 연결 버튼, S3 Bucket 권한 fallback, 로그 상세 패널을 확정한다.
2. `docs/product-specs/aws-connect.md`에 EC2 `StartInstances`/`RebootInstances`, 즐겨찾기와
   SSM Run Command 기반 Secret 조회의 사용자 흐름·최소 IAM 권한·보안 경고를 추가한다.
3. `docs/SECURITY.md`와 `docs/RELIABILITY.md`에 Run Command 출력의 민감정보 수명,
   polling timeout/cancel, 비기록·재마스킹, EC2 비동기 상태 확인 규칙을 추가한다.
4. S3의 기존 `ListBuckets` 금지 규칙은 "자동 필수 호출 금지"로 좁힌다. 사용자가 화면을
   열었을 때 선택적 catalog 조회를 한 번 시도하고 권한 거부 시 직접 입력을 유지한다.

구현 대안과 선택:

- **공통 UI 토큰/컴포넌트 정리 — 추천:** QSS 색상·간격·버튼 역할과 page header/card/empty
  state helper를 한곳에 두어 목업 정합성을 재사용한다. 페이지별 QSS 덧대기는 diff는 작지만
  다시 디자인이 갈라진다.
- **EC2 inventory + SSM 상태 join — 추천:** `DescribeInstances`의 전원 상태와
  `DescribeInstanceInformation`의 PingStatus를 Instance ID로 합친다. SSM 목록만 확장하면
  stopped 인스턴스는 API 특성상 반환되지 않아 요구사항을 충족할 수 없다.
- **SSM Run Command 경유 Secret 조회 — 조건부 추천:** 앱 사용자에게는 `SendCommand`와
  `GetCommandInvocation`, EC2 instance role에는 `GetSecretValue` 권한이 필요하다. 직접 조회만
  유지하면 안전하지만 사용자가 요청한 제한 권한 계정 복구 경로를 제공하지 못한다.
- **대화형 Session Manager 셸 자동화 — 비추천:** 기존 외부 터미널은 사용자 소유라 앱이
  stdout을 안전하게 회수할 계약이 없고, shell quoting과 프로세스 수명주기를 중복 구현한다.

구현 슬라이스:

#### 9.1 공통 디자인, 프로필과 대시보드

- 목업의 top bar, navigation, page header, card, primary/danger/small button, status pill,
  split layout과 1024×720 기준을 공통 QSS 및 작은 presentation helper로 만든다.
- 프로필 편집기에서 `MFA 장치 ARN`을 제거한다. 생성 시 Domain의 `default_mfa_arn()`을
  사용하고, 기존 프로필 갱신에서 `None`은 저장된 ARN 보존을 뜻하도록 Application 규칙을
  명확히 한다.
- 기존 프로필의 키 필드는 복호화 값을 주입하지 않고 고정 마스크 placeholder로
  `등록됨` 상태만 보여준다. 새 값을 입력한 경우에만 두 키를 함께 교체한다.
- 대시보드의 4개 기능 카드와 활성 RDS 터널 요약을 목업의 순서·문구·버튼 계층에 맞춘다.

#### 9.2 EC2 inventory, 전원 복구와 즐겨찾기

- `Ec2Target`에 `instance_state`, `ssm_ping_status`, `favorite`를 추가하고, EC2 inventory
  gateway가 pagination된 `DescribeInstances` 결과를 반환하도록 한다.
- Application Service가 EC2 inventory와 SSM managed node를 Instance ID로 join한다.
  `DescribeInstances` 권한이 없으면 기존 SSM Online 목록으로 degrade하고 전원 작업과
  `전체/실행 중/중지됨` 필터는 권한 안내와 함께 비활성화한다.
- profile/region/instance ID를 키로 하는 `ec2_favorites` SQLite migration과 좁은 store port를
  추가한다. 정렬은 `favorite desc → Name(case-insensitive) → Instance ID` 한 곳에서만 정의한다.
- 실행 중 + SSM Online은 `터미널 열기`, 실행 중 + SSM Offline은 확인 후 `재부팅`,
  중지됨은 비용 안내 확인 후 `시작`을 노출한다. 모든 write API는 단일 선택, 명시적 확인,
  `DryRun` 권한 검사, 중복 클릭 차단 후 호출한다.
- `StartInstances`/`RebootInstances` 성공은 완료가 아니라 요청 수락이다. 제한된 polling으로
  상태를 갱신하고 timeout 시 백그라운드 작업이 계속될 수 있음을 안내한다.

#### 9.3 RDS 저장 오류와 상태 기반 단일 버튼

- 기존 검색, 로컬 주소 복사, 복제, 삭제를 유지하고 좌측 저장 세션/우측 편집기의 목업
  split layout으로 정렬한다.
- GUI의 `중계 방식`을 제거하고 `중계 EC2` 한 항목만 제공한다. GUI에서 새로 저장하는 세션은
  `TargetMode.FIXED`로 만들되 Domain/CLI의 기존 `SELECT` 계약은 호환성을 위해 유지한다.
- relay 목록이 아직 로드되지 않았거나 비어 있으면 저장 버튼을 비활성화하고 이유를 표시한다.
  현재 기본 FIXED + `relay=None`이 `rds.session.target_instance_id.invalid`로 끝나는 경로를
  GUI regression test로 재현한 뒤 수정한다.
- `연결 시작`과 `연결 종료`를 동시에 두지 않고 선택 세션의 active operation ID를 기준으로
  한 버튼의 text/style/action을 전환한다. 시작·종료 중에는 같은 버튼을 잠근다.
- `새 세션`은 생성 모드 제목, 선택 해제, delete/clone/start 비활성화와 기본 포트를 함께
  표시한다. 편집 중인 값이 있으면 버리기 확인 후 초기화한다.

#### 9.4 Secrets 직접 조회와 EC2 경유 조회

- 현재 직접 입력, 선택 목록, JSON flatten, field reveal/copy, RDS host/port 복사를 유지한다.
- `SecretLookupMode(DIRECT, VIA_EC2)`와 별도 `RemoteSecretCommandGateway` port를 추가한다.
  Application은 두 경로의 raw `SecretString`을 동일한 기존 parser/masking 결과로 변환한다.
- 경유 조회는 Online EC2 한 대만 허용하고 플랫폼에 따라 AWS 제공 Run Command document를
  선택한다. command는 고정 template과 엄격히 검증된 Secret ID만 사용해 shell injection을
  막고, CloudWatch/S3 output은 활성화하지 않는다.
- `SendCommand` 후 `GetCommandInvocation`을 bounded polling한다. Success만 결과로 받고
  Failed/TimedOut/Cancelled/DeliveryTimedOut과 앱 취소를 typed error로 변환한다.
- 결과 원문은 memory-only, `repr=False`, 30초 reveal/copy 정책을 그대로 적용한다. SSM command
  output에 Secret 원문이 일시 존재한다는 경고와 필요한 caller/instance-role 권한을 UI에
  명시하고 사용자의 매번 명시적 동의를 받는다.

#### 9.5 S3 목업 정합성과 권한 fallback

- optional `list_buckets` port를 추가한다. 성공하면 Bucket select를, permission failure면
  기존 직접 입력을 렌더링하며 객체 조회·업로드는 어느 경로에서도 동일 서비스로 처리한다.
- Bucket과 Prefix를 클릭 가능한 breadcrumb로 투영하고 각 segment 이동은 현재
  `list_objects`를 재사용한다. 객체 테이블은 `이름/유형/크기/수정 시간` 4열로 바꾼다.
- MIME 유형은 key suffix 기반 presentation hint로만 표시하고 도메인 규칙이나 추가 HeadObject
  호출로 만들지 않는다. prefix는 `폴더`, 알 수 없는 suffix는 `파일`로 표시한다.
- 파일 선택 버튼, drop zone, 선택 파일 요약, 업로드 확인, 파일별 진행과 취소 영역을 목업
  순서로 재배치한다. 기존 preflight/overwrite/multipart/cancel Application 계약은 변경하지 않는다.

#### 9.6 실행 로그 상세

- 테이블 기본 열은 목업의 `시간/기능/대상/결과`로 단순화하고 선택 행 아래 상세 패널에
  `message_code`, correlation/operation ID, AWS service/action, retryable, 마스킹된 상세 원인을
  표시한다.
- `ActivityEvent`와 `ManagedLogEntry`의 고정 allowlist schema를 위 필드만큼 확장한다.
  임의 traceback/command args/Secret value는 수용하지 않으며 각 문자열은 기존 bounded
  `mask_text`를 거쳐 저장과 읽기 양쪽에서 다시 마스킹한다.
- 사용자 조치 문구는 중앙 GUI error mapper의 message code mapping을 재사용한다. 상세 패널은
  복사 시에도 다시 마스킹하고 진단 ZIP 링크를 제공한다.

#### 9.7 운영 피드백 후속 보완 (2026-09-15)

목표: 실제 화면 사용에서 확인된 잘림, 불필요한 조작, 선택 피드백과 AWS 리소스
catalog 재사용 문제를 기존 Application 계약을 확장하여 해결한다.

가정과 보안 결정:

- 대화에 언급된 닫기 SVG 원본은 저장소와 현재 첨부 컨텍스트에서 발견되지 않았다. 프로필
  모달의 중복 커스텀 X는 제거하고, 새 SVG가 제공되면 남아 있는 앱 소유 닫기 아이콘에만 적용한다.
- “Secret 키-값 저장”은 로컬 SQLite 저장이 아니라 사용자가 명시적으로 확인한 뒤 AWS
  Secrets Manager의 기존 Secret에 새 버전을 저장하는 의미로 한정한다. 원문은 DB·로그에 남기지 않는다.
- S3 삭제는 선택한 객체 한 개만 대상으로 하며, prefix(폴더) 재귀 삭제는 제공하지 않는다.

SSOT 검색: `인증됨|터미널 열기|EC2 터미널에서 조회|선택 값 표시|목록|삭제|RDS Host|Bucket|기능`을
검색했다. 공통 헤더/프로필은 `window.py`, EC2/RDS는 `ec2_rds.py`, Secret/S3/로그는 각 전용
GUI 모듈, AWS 호출은 기존 service/port/gateway 계층에 집중되어 있어 해당 경로만 확장한다.

구현 및 검증 체크리스트:

- [x] 헤더 인증 상태를 토큰 만료 옆으로 이동하고 1024×720 비겹침을 검증한다.
- [x] 프로필 모달의 중복 X를 제거하고 저장 전 임시 프로필 삭제가 즉시 동작하게 한다.
- [x] 선택 행 강조선은 공통 delegate를 통해 첫 번째 셀에만 표시한다.
- [x] 대시보드 활성 터널, EC2 상태/행 버튼, S3/로그 행 높이와 열 폭 잘림을 수정한다.
- [x] RDS endpoint catalog를 Application port와 boto3 adapter로 추가하고 권한 실패 시 직접 입력을 유지한다.
- [x] Secret/중계 EC2 catalog를 프로필 변경 시 자동 조회하고 Secret 목록 검색을 제공한다.
- [x] EC2 직접 조회는 일반 대화형 세션을 열고 고정 명령을 클립보드로 전달해 터미널을 유지한다.
- [x] Secret JSON 편집 저장은 명시적 확인 후 `PutSecretValue`를 호출하고 원문 비기록을 검증한다.
- [x] S3 Bucket 자동 조회·즉시 객체 조회, 선택 객체 단건 삭제와 전체 폭 목록을 구현한다.
- [x] 실행 로그를 `No/시간/대상(탭)/내용/결과` 5열과 충분한 행 높이로 변경한다.
- [x] focused tests, `./scripts/check.ps1`, 1024×720/1424×894 render QA 결과를 기록한다.

#### 9.8 첨부 화면 기반 레이아웃 회귀 보완 (2026-09-15)

SSOT 검색: `State_Selected|use_first_column_selection_bar|dashboard_active_tunnels|rds_host_catalog|`
`AWS-StartInteractiveCommand|ec2_row_action`을 검색했다. 선택 표시는 공통 delegate, 터널 행은
`ActiveTunnelRow`, Secret 명령은 `build_remote_secret_command`와 `Ec2Service.connect_external`에
이미 집중되어 있으므로 새 GUI별 규칙을 만들지 않고 해당 계약을 확장한다.

가정과 결정:

- “명령어까지 넣기”는 검증된 Secret 조회 명령을 `AWS-StartInteractiveCommand`의 command
  parameter로 전달해 자동 실행하고, 실행 뒤 플랫폼 셸을 이어 열어 세션을 유지하는 의미로 해석한다.
- RDS Host 표시 문자열은 유지하되 콤보박스의 size hint만 제한해 패널 폭을 넘지 않게 한다.

체크리스트:

- [x] 상단 프로필/Account/IAM/토큰/인증 상태를 동일한 caption/value 세로 축에 맞춘다.
- [x] 활성 터널 한 행의 실제 size hint와 목록 높이를 일치시키고 공통 목록 padding을 제거한다.
- [x] 공통 table delegate가 선택 배경을 직접 그리고 첫 열에만 강조선을 그리도록 수정한다.
- [x] EC2 이름 열 폭과 행 작업 버튼의 내부 여백·폭을 줄이되 버튼 문구는 유지한다.
- [x] RDS Host 콤보박스 size hint를 제한하고 연결 상태 패널을 한 행 높이로 축소한다.
- [x] Secret 고정 명령을 자동 실행한 뒤 Linux/Windows 셸을 유지하는 계약과 테스트를 추가한다.
- [x] focused/full tests와 1024×720/1424×894 렌더 QA 결과를 기록한다.

검증 기록:

- 첨부 EC2/대시보드 화면을 기준으로 Qt 선택 state와 `QListWidget::item` padding을 재현해
  공통 delegate의 직접 배경 painting과 dashboard 전용 무-padding 규칙으로 수정했다.
- `uv run --no-sync pytest ... -q --no-cov` focused GUI/Application 묶음 → `81 passed`.
- `./scripts/check.ps1`의 문서, Ruff, architecture/import boundaries, mypy, `365 passed`
  (coverage `86.68%`), critical branch coverage, secret scan과 Bandit가 통과했다. sandbox에서
  네트워크만 차단된 `pip-audit`은 승인된 별도 실행으로 `No known vulnerabilities found`를 확인했다.
- `.visual-qa-followup-layout-1024`와 `.visual-qa-followup-layout-1424`에 전체 7개 화면을 렌더해
  상단 grid 정렬, RDS 편집기 내부 폭과 42px 상태 행, EC2 열 구성을 육안 검토했다.

런타임 사이드이펙트: 일반 UI 변경은 프로세스·포트·메모리 수명주기에 영향이 없다. Secret
fallback은 기존 `StartSession` 호출에 고정 command parameter를 추가하고, 명령 완료 뒤 셸을
유지하므로 사용자가 터미널을 닫을 때까지 기존 세션 핸들이 계속 추적된다.

#### 9.9 목업 정합성과 저장-연결 순서 보완 (2026-09-15)

SSOT 검색: `toggle_connection|SaveTunnelSessionRequest|secret_catalog|relay_refresh|ActiveTunnelRow|`
`Instance ID`와 목업의 `.split/.form-grid/.actions/.secret-layout`을 비교했다. RDS 저장과 연결은
기존 `TunnelSessionService`/`RdsTunnelOperationCoordinator`, Secret 목록과 값은 기존
`SecretsService` 결과를 그대로 사용하고 GUI orchestration과 표현만 수정한다.

- [x] 활성 터널 한 행 아래 버튼이 잘리지 않도록 item/viewport 높이에 여유를 둔다.
- [x] EC2 Instance ID 열을 고정 축소하고 마지막 작업 열을 완전히 보존한다.
- [x] RDS 편집기의 필드 간격과 actions를 목업처럼 조밀한 상단 form + 단일 하단 행으로 맞춘다.
- [x] 편집된 기존 RDS 세션은 저장 성공 후 반환된 ID로 연결을 시작하고 저장 실패 시 시작하지 않는다.
- [x] Secret 왼쪽 패널에 목록 로딩/권한 없음/빈 목록/건수 상태를 명시한다.
- [x] Secret 항목 한 번 선택으로 오른쪽 값 패널을 조회하고 첫 저장 가능 필드를 선택한다.
- [x] 자동 로드되는 중계 EC2의 수동 `Online EC2 불러오기` 버튼을 제거한다.
- [x] focused/full tests와 두 viewport 렌더 QA를 기록한다.

구현 결정과 검증:

- 정규 폭에서는 목업처럼 `310px + 1fr` split과 form 바로 아래 상태/actions를 사용한다. 900px
  미만에서는 왼쪽 카드만 230px로 줄이고 actions를 내부 스크롤 밖에 두어 주 작업을 보존한다.
- EC2는 Instance ID를 145px(좁은 폭 125px), Name을 250px(좁은 폭 180px)로 제한하고
  Private IP만 stretch해 마지막 92px 작업 열이 밀리지 않게 했다.
- `toggle_connection`은 dirty editor를 `TunnelSessionService.update/create`로 먼저 저장하며,
  성공 콜백에서 반환 ID로 시작한다. 저장 오류 콜백에서는 시작 호출이 발생하지 않는다.
- focused GUI 검증은 `pytest tests/adapter/gui/test_ec2_rds.py tests/adapter/gui/test_shell.py
  tests/adapter/gui/test_secrets.py -q --no-cov`로 통과했고, 전체 `scripts/check.ps1`의 문서·포맷·
  lint·architecture·types·367 tests(86.76%)·critical coverage·secret scan·Bandit가 통과했다.
  네트워크가 필요한 `pip-audit`은 승인된 별도 실행으로 `No known vulnerabilities found`를 확인했다.
- `.visual-qa-followup-mockup-1024`와 `.visual-qa-followup-mockup-1424`의 전체 화면을 렌더해
  RDS 간격/단일 버튼 행, EC2 무가로스크롤, Secret master-detail 배치를 확인했다.

런타임 사이드이펙트: 편집 후 연결은 로컬 세션 저장 I/O가 한 번 선행되지만 AWS 연결·포트 생성은
저장 성공 뒤 한 번만 수행한다. 나머지는 GUI geometry와 조회 상태 표현 변경이라 프로세스·포트·
메모리 수명주기에 영향이 없다.

#### 9.10 Windows CI 도구·패키징 입력 재현성 보완 (2026-09-15)

SSOT 검색: `rg|secret scan|PyInstaller|aws_connect.spec` 결과 secret scan의 유일한 파일 열거는
`scripts/check.ps1`, 패키징 명세의 유일한 소비자는 `scripts/build-package.ps1`로 확인했다.

- [x] secret scan 파일 열거를 러너 제공 `rg`에서 저장소 필수 도구인 `git ls-files`로 변경해
  CI checkout과 동일한 Git 추적 파일만 검사한다.
- [x] 전역 `*.spec` 무시 규칙에서 `packaging/aws_connect.spec`을 명시적으로 제외한다.
- [x] secret scan과 TEST-ONLY Portable ZIP 빌드·smoke를 다시 실행해 수정된 CI 경로를 검증한다.

검증 결과:

- `git ls-files -- ':!ref/**' ':!uv.lock'` → 180개 추적 파일, `ref/**`와 `uv.lock` 제외
- 현재 추적 파일과 새로 추적할 `packaging/aws_connect.spec`을 합친 CI 예상 181개 파일에
  `detect-secrets scan` → 탐지 0건
- `scripts/build-package.ps1 -VendorDirectory ./build/test-vendor -AllowTestVendor` → PyInstaller
  3-EXE TEST-ONLY ZIP 빌드 성공
- `scripts/test-package.ps1 -ZipPath ./dist/aws-connect-0.1.0-TEST-ONLY-windows-x64.zip
  -AllowTestVendorPackage` → 전체 package smoke 성공
- `scripts/check.ps1` → 이번 변경 이전부터 존재한 DB 생성 문서 drift로 documentation gate에서 중단;
  다음 단계는 해당 스키마 변경 작업에서 `scripts/generate-docs.ps1`로 authority를 동기화한 뒤 재실행

런타임 사이드이펙트: 개발/CI 검사와 패키징 입력 추적만 변경하며 애플리케이션 프로세스, 포트,
메모리 사용량과 초기화 순서에는 영향이 없다.

#### 9.11 EC2 전체 열 재배분·S3 파일 열기·Secret 저장 목록 (2026-09-15)

SSOT 검색: `setColumnWidth|_target_action_text|list_objects|prepare_upload|delete_object|`
`SecretsService|SqliteProfileStore|secret_catalog` 결과 EC2/S3 기존 동작은 확장하고, Secret 원문은
기존 메모리 전용 규칙을 유지한 채 식별자 저장 계약만 추가한다.

- [x] EC2 이름·Instance ID·Private IP·상태 열을 모두 축소하고 120px 작업 열을 고정한다.
- [x] 중지 인스턴스 작업명을 `인스턴스 실행`으로 바꾸고 112px 버튼으로 좌우 여백을 확보한다.
- [x] S3 업로드 완료 후 목록을 갱신하고 선택 파일 다운로드·열기와 단건 삭제를 제공한다.
- [x] ListSecrets 성공 시 상단 선택 상자, 권한 거부 시 이름/ARN 입력 상자를 제공한다.
- [x] 조회 성공 식별자를 SQLite에 자동 등록하고 왼쪽 하단 삭제·수정·등록을 제공한다.
- [x] 오른쪽 값 패널에서 키-값 저장·복사 버튼을 제거하고 시간 제한 값 확인만 유지한다.
- [x] migration/generated schema, focused/full tests, 1024/1424 렌더 QA를 기록한다.

데이터 흐름과 결정:

```text
GetSecretValue 성공 → SecretResult(메모리 원문) ─→ 오른쪽 값 확인(30초 reveal)
                  └→ identifier(name/ARN) only ─→ saved_secrets(SQLite) ─→ 왼쪽 CRUD

S3 선택 파일 → GetObject → destination 옆 임시 파일 → 성공 시 atomic replace → 기본 앱 열기
                                         └→ 실패 시 임시 파일 삭제
```

대안 검토:

- SecretString 전체 DPAPI 저장: 오프라인 조회는 가능하지만 원문 영구 저장과 키 수명주기가 생겨 미채택.
- Secret 이름/ARN만 SQLite 저장: 기존 보안 경계를 유지하면서 재선택 UX를 제공하므로 채택.
- S3 presigned URL 열기: 임시 서명 URL 노출과 브라우저 의존성이 있어 미채택.
- 사용자 지정 경로 원자 다운로드 후 열기: 저장 위치가 명확하고 부분 파일을 제거할 수 있어 채택.

검증 결과:

- focused 102 tests 통과: EC2 고정 열/무가로스크롤, S3 업로드 재조회·다운로드·열기·삭제,
  Secret selector/input fallback·SQLite CRUD·임시 reveal, migration 6과 S3 adapter를 검증했다.
- `scripts/generate-docs.ps1`와 `scripts/verify-docs.ps1`로 migration 6
  `saved_secrets(profile_id, identifier)` 생성 스키마를 동기화했다.
- 전체 `scripts/check.ps1`의 문서·Ruff·architecture/import boundaries·mypy·376 tests
  (coverage 86.56%)·critical coverage·secret scan·Bandit가 통과했다. 샌드박스가 차단한
  `pip-audit`은 승인된 네트워크 실행으로 분리해 `No known vulnerabilities found`를 확인했다.
- `.visual-qa-current-1024`와 `.visual-qa-current-1424`의 전체 화면을 렌더해 EC2 작업 열,
  S3 세 작업, Secret 선택/저장/값 패널을 육안 확인했다.

런타임 사이드이펙트: Secret 조회 성공마다 이름/ARN에 한해 짧은 SQLite 조회/조건부 insert가
추가된다. S3 `선택 파일 열기`는 사용자 선택 시 GetObject, 로컬 임시 파일, 원자 교체와 기본 앱
실행을 수행한다. EC2 변경은 geometry와 작업 문구뿐이며 프로세스·포트 수명주기에 영향이 없다.

#### 9.12 프로필별 MFA 사용 여부와 무MFA 세션 발급 (2026-09-15)

SSOT 검색: `mfa_arn|MFA_REQUIRED|get_session_token|SerialNumber|TokenCode` 결과 프로필과
세션 발급 규칙은 `AwsProfile`/`ProfileService`/`AuthenticationService`에 모여 있었지만,
MFA 없음 상태를 표현하거나 STS MFA 파라미터를 생략하는 분기는 없었다.

- [x] `mfa_enabled`를 프로필 도메인 값과 CLI/GUI DTO에 추가하고 생성·수정·복제에 보존한다.
- [x] SQLite migration 7로 사용 여부를 저장하며 기존 레코드는 MFA 활성 상태로 이관한다.
- [x] GUI 프로필 편집기에 기본 활성 체크박스, CLI에 `--mfa`/`--no-mfa`를 제공한다.
- [x] MFA 미사용 프로필은 challenge 없이 `GetSessionToken`을 호출하고 MFA 파라미터를 생략한다.
- [x] 공통 authenticated operation이 세션 발급 후 원래 기능 작업을 정확히 한 번 재실행한다.
- [x] 제품·GUI·애플리케이션 설계와 생성 DB 스키마를 구현에 맞춘다.

대안 검토:

- 프로필별 명시적 사용 여부 저장: 권한 오류와 MFA 없음이 섞이지 않고 보안 의도가 유지되어 채택.
- `iam:ListMFADevices` 자동 감지: 추가 IAM 권한과 IAM 계층 결합이 생겨 미채택.
- MFA 오류 후 무MFA 자동 fallback: 권한 오류를 인증 강도 완화로 오인할 수 있어 미채택.

검증 결과:

- focused: 관련 Domain/Application/STS/SQLite/CLI/GUI `pytest ... --no-cov` → 89 passed
- `scripts/check.ps1` → 문서 생성 정합성, Ruff, architecture/import, mypy, 386 tests,
  전체 coverage 86.68%, critical coverage, secret scan, Bandit 통과
- sandbox 네트워크 제한으로 전체 명령의 마지막 `pip-audit`만 중단되어 승인된 네트워크에서
  `uv run --no-sync pip-audit` 별도 실행 → `No known vulnerabilities found`
- `tools/render_gui.py .visual-qa-mfa --width 1024 --height 720`의 프로필 화면에서 MFA 체크박스,
  안내문과 하단 작업 버튼이 잘리거나 겹치지 않음을 확인했다.

런타임 사이드이펙트: 시작 시 SQLite migration 7이 `mfa_enabled` 열을 추가하며 기존 값은 `1`이다.
MFA 설정 변경은 기존 임시 세션을 삭제한다. MFA 미사용 프로필은 코드 입력 없이 STS 네트워크 호출을
한 번 수행하지만 프로세스 시작, 포트, 메모리 수명주기에는 변화가 없다.

추가 작업 필요: 승인된 비운영 MFA 사용/미사용 IAM 사용자로 두 `GetSessionToken` 요청과 이후
EC2/RDS/S3/Secrets 원래 작업 1회 재개를 smoke한다.

상태와 데이터 흐름:

```text
EC2 DescribeInstances ── power state/name/ip ─┐
                                              ├─ join(instance_id) ─ favorite sort ─ GUI
SSM DescribeInstanceInformation ─ ping status ┘                         │
                                                                       ├─ running+online: connect
                                                                       ├─ running+offline: confirm→reboot
                                                                       └─ stopped: confirm→start

Secret 조회 선택
├─ DIRECT ─ SecretsGateway.get_secret_value ─┐
└─ VIA_EC2 ─ SendCommand → bounded poll ─────┴─ existing parse/mask ─ GUI
```

수용 기준:

- 정규 `1424×894` viewport에서 앱 셸 `1380×850`, 상단 바 `92`, 탐색 `220`, 아이콘 버튼
  `42×42`와 목업의 색·간격·패널·상태·아이콘 계층을 재현하고, 1024×720에서도 7개 화면의 필수 정보와 주 작업 버튼이 잘리거나 겹치지 않는다.
  계층, 간격, 색, 상태 표현을 따른다.
- 기존 프로필 편집 시 Access Key와 Secret Access Key 원문은 화면·로그·DTO repr에 나타나지
  않고 마스크만 보이며, MFA ARN 입력 없이 생성·갱신·인증이 동작한다.
- EC2는 `전체/실행 중/중지됨` 필터를 제공하고 SSM 상태를 작업 가능 여부에 반영하며, 즐겨찾기가 재시작 후에도
  profile/region별로 유지되며 항상 상단에 정렬된다.
- EC2 reboot/start는 올바른 상태에서만, 확인과 권한 검사 후 한 번 호출되고 비동기 결과를
  명확히 표시한다. `StopInstances`와 `TerminateInstances`는 호출하지 않는다.
- RDS 완전 입력 저장이 성공하며 relay 미선택 오류는 저장 전에 필드 수준으로 안내된다.
  실행 중인 저장 세션은 단일 버튼이 `연결 종료`로 바뀌고 기존 검색·복사·복제·삭제가 유지된다.
- Secrets 직접 조회 권한이 없을 때 명시적으로 선택한 Online EC2 경유 조회가 가능하고,
  command 실패/timeout/cancel/권한 부족이 typed error와 복구 지침으로 표시된다.
- S3 Bucket catalog 권한 유무에 따라 select/direct-input이 전환되고 breadcrumb, 4열 목록,
  drag/drop 및 파일 선택이 같은 upload 계약을 사용한다.
- 로그 상세에는 안전한 진단 필드와 사용자가 취할 조치가 보이되 비밀, command arguments와
  원문 traceback은 DB·로그·UI·클립보드에서 발견되지 않는다.

테스트 계획:

```text
CODE PATHS                                      USER FLOWS
Profile preserve/default MFA ARN                edit profile → masked keys → save/connect
├─ create derives default                       ├─ existing credentials unchanged
└─ update None preserves current                └─ partial key replacement rejected

EC2 inventory join + power action               filter/favorite/select/action
├─ EC2+SSM success                              ├─ running+online → terminal
├─ Describe denied → online-only fallback       ├─ running+offline → confirm/reboot
├─ start/reboot deny/timeout                    └─ stopped → confirm/start
└─ favorite migration/cascade/sort

RDS editor                                     new/select/save/start/stop
├─ relay loading/empty/selected                 ├─ dirty new-session confirmation
├─ create/update validation                     └─ active button state transition
└─ legacy SELECT session compatibility

Secrets via EC2                                explicit opt-in → relay → lookup
├─ command success + JSON/text                  ├─ warning/consent
├─ polling eventual consistency                 ├─ selected value copy auto-clear
└─ failed/timedout/cancelled/output bounds      └─ retry or direct mode recovery

S3/log presentation                            permission and detail flows
├─ bucket list success/denied                   ├─ select/input → breadcrumb → upload
├─ MIME projection/breadcrumb                  └─ log row → safe detail/copy
└─ structured detail re-mask/bounds
```

- Domain/Application unit: EC2 join/filter/action invariant, favorite ordering, MFA ARN preserve,
  RDS fixed relay validation, Run Command state mapping, shared Secret parsing, log detail allowlist.
- Infrastructure Stubber/SQLite: Describe/Start/Reboot pagination·denial·DryRun, favorite migration and
  cascade, SendCommand/GetCommandInvocation eventual consistency·timeout, optional ListBuckets denial.
- GUI adapter: 각 목업 요소의 object name/label/column/button state, RDS 저장 regression,
  EC2 확인·중복 클릭, S3 select/input fallback·breadcrumb/drop, 로그 상세 마스킹.
- 일관성/architecture: CLI와 GUI가 동일 Application DTO/error를 사용하고 presentation이 boto3,
  SQLite 또는 shell command를 직접 호출하지 않음을 고정한다.
- 검증 순서: 관련 focused pytest → `./scripts/generate-docs.ps1` →
  `./scripts/verify-docs.ps1` → `./scripts/check.ps1` → TEST-ONLY package smoke → 승인된 실제 AWS
  profile에서 EC2 start/reboot, Run Command Secret, ListBuckets visual smoke.

구현 및 검증 기록(2026-09-15):

- 목업 CSS/DOM의 shell, top bar, navigation, page header, card, form, table, status pill,
  split layout, drop zone와 profile dialog 수치를 `presentation/gui/styles.py`와 기존 화면
  widget에 직접 매핑했다. 페이지별 중복 비즈니스 로직은 추가하지 않았다.
- 프로필 마스킹/MFA ARN 제거, EC2 inventory·상태 필터·즐겨찾기·시작/재부팅, RDS 저장과
  단일 연결 버튼, Secrets EC2 경유, S3 bucket fallback·breadcrumb·4열·drop, 로그 안전 상세를
  각 Application 계약과 기존 GUI 흐름에 연결했다.
- `uv run --no-sync pytest tests/adapter/gui -q --no-cov -p no:cacheprovider` → `55 passed`.
- `uv run --no-sync python tools/render_gui.py .visual-qa` 및 `--width 1024 --height 720`로
  7개 화면을 렌더했다. 정규/최소 viewport에서 셸 절단과 기능 버튼 겹침이 없음을 확인했고,
  RDS 내부 최소 높이가 셸을 미는 회귀를 제거했다.
- 서브에이전트의 1차 교차 검토에서 발견한 compact 겹침, 실제 입력/아이콘 크기, 카드 여백,
  breadcrumb/drop zone/status pill 차이를 수정했다. 최종 재검토 요청은 작업 공간 크레딧 부족으로
  실행되지 않아 루트 에이전트가 같은 GUI suite와 렌더로 재검증했다.
- `./scripts/check.ps1`의 documentation, Ruff format/lint, architecture/import boundaries, mypy,
  `345 passed`(coverage `86.82%`), critical branch coverage, secret scan과 Bandit가 통과했다.
  최초 secret scan이 새 테스트 fixture 2개를 탐지해 명시적 allowlist로 의도를 기록한 뒤
  재검증했다. 샌드박스 네트워크로 중단된 `pip-audit`은 승인된 네트워크 실행으로 분리 재시도해
  `No known vulnerabilities found`를 확인했다(`aws-connect` 자체 패키지는 PyPI 비공개라 제외).
- 후속 UX 수정에서 맑은 고딕을 Qt에 명시 등록하고 QComboBox drop-down을 공통 스타일로
  통일했다. EC2의 중복 item/widget 상태 렌더를 제거하고 54px 행, 단일 EC2 상태 열,
  첨부 SVG 즐겨찾기와 빨간 중지 pill을 적용했다. RDS polling은 선택 signal을 차단해 편집 중
  Local Port를 덮어쓰지 않으며 편집 배경을 흰색으로 고정했다.
- Secrets 중계 목록은 기존 EC2 metadata port로 Name을 보강한다. `SendCommand` 거부 시에만
  사용자가 누르는 `AWS-StartInteractiveCommand` 외부 터미널 fallback을 추가했고, 고정 명령과
  Secret ID validation을 자동/터미널 경로의 SSOT로 통합했다. 프로필은 저장 전 임시 항목,
  상세 재선택과 보호된 자격증명 복제를 지원한다.
- 운영 피드백 보완에서 인증 상태를 토큰 옆으로 이동하고 공통 table delegate로 선택 강조선을
  첫 셀에만 그렸다. 프로필의 중복 X와 S3 목록 버튼을 제거하고, 임시 프로필 삭제·대시보드
  터널 목록·EC2 상태/작업 열·S3/로그 표 크기를 수정했다.
- RDS `DescribeDBInstances`, Secrets `ListSecrets`/`PutSecretValue`, S3 `DeleteObject`를 기존
  Application port/service와 boto3 adapter에 추가했다. catalog 권한이 없으면 기존 직접 입력을
  유지하고, Secret 저장과 S3 단건 삭제는 명시적 확인 뒤에만 실행한다.
- EC2 직접 조회 fallback은 종료되는 단일 명령 세션 대신 일반 외부 터미널을 유지하고 검증된
  고정 조회 명령을 임시 클립보드로 전달한다. Secret 선택 값은 표시 버튼 없이 복사만 제공한다.
- focused test `112 passed`; 전체 `./scripts/check.ps1`은 문서, Ruff, architecture/import,
  mypy, `363 passed`(coverage `86.55%`), critical branch coverage, secret scan, Bandit까지 통과했다.
  의존성 감사만 샌드박스 네트워크 차단으로 중단되어 승인된 네트워크로 분리 실행했고
  `No known vulnerabilities found`를 확인했다(`aws-connect` 자체 패키지는 PyPI 비공개라 제외).
- `tools/render_gui.py`로 `.visual-qa-request-1024`(1024×720)와
  `.visual-qa-request-1424`(1424×894)의 7개 화면을 렌더하고 잘림·겹침을 육안 검토했다.

예상 실패 모드와 사용자 표시:

| 경로 | 현실적 실패 | 테스트/처리/표시 |
|---|---|---|
| EC2 inventory join | SSM 정보가 늦거나 EC2 권한 없음 | Stubber, online-only fallback, 권한 안내 |
| EC2 start/reboot | 요청 수락 뒤 상태 변화 timeout | fake clock, bounded polling, 계속 진행 가능 안내 |
| favorite write | SQLite busy/profile 삭제 경합 | migration/transaction test, typed 저장 오류 |
| RDS save | relay 비동기 로드 전 저장 | GUI regression, 버튼 차단과 필드 안내 |
| Secret Run Command | instance role/AWS CLI/SSM Agent 문제 | 상태별 Stubber, relay/권한/설치 복구 지침 |
| Secret output | SSM output에 원문이 일시 보존됨 | 비기록·재마스킹 test, opt-in 경고; 무음 실패 금지 |
| S3 bucket catalog | `ListAllMyBuckets` 거부 | Stubber, 직접 입력으로 즉시 전환 |
| log detail | 기술 원인에 credential 조각 포함 | known pattern fixture, write/read/copy 3중 마스킹 |

구현 순서와 병렬화:

| Lane | 작업 | 의존성 |
|---|---|---|
| A | 9.1 공통 디자인·프로필·대시보드 | 권위 문서 |
| B | 9.2 EC2 inventory·즐겨찾기·전원 복구 | 권위 문서, 공통 UI token |
| C | 9.3 RDS, 9.4 Secrets | 공통 UI token; 각 슬라이스 내부 순차 |
| D | 9.5 S3, 9.6 로그 | 공통 UI token; 각 슬라이스 내부 순차 |

권위 문서와 공통 UI 토큰을 먼저 확정한 뒤 B/C/D의 Application·adapter·GUI focused 작업은
병렬화할 수 있다. `application/ports.py`, `bootstrap.py`, `window.py`, SQLite migration은 충돌
가능성이 높으므로 각 lane을 작은 커밋으로 합친 뒤 다음 lane이 rebase한다.

NOT in scope:

- EC2 stop/terminate, Auto Scaling Group/Spot/ECS가 관리하는 인스턴스 제어: 데이터 손실과
  상위 orchestrator 충돌 위험이 있어 별도 제품 결정이 필요하다.
- SSM Agent 자동 설치·복구: reboot 뒤에도 Offline이면 AWS Systems Manager 진단으로 안내한다.
- 임의 원격 명령 실행기: Secrets 경유 조회는 고정 command template 한 개로 제한한다.
- S3 prefix(폴더) 재귀 삭제와 다중 객체 일괄 삭제는 제공하지 않는다.
- Secret 원문 또는 전체 traceback의 영구 로그 저장: 보안 규칙과 충돌하므로 안전한 구조화
  진단 필드만 제공한다.

런타임 사이드이펙트 점검:

- 새 side effect는 EC2 `StartInstances`/`RebootInstances`, SSM Run Command와 SQLite favorite
  쓰기다. start는 과금, reboot는 서비스 중단, Run Command output은 민감정보 잔존 가능성이
  있으므로 모두 명시적 확인·최소 권한·timeout·activity record가 필요하다.
- UI/QSS, RDS 버튼 전환, S3 breadcrumb와 로그 상세 자체는 새 프로세스·포트·대용량 메모리
  영향을 만들지 않는다. 기존 RDS/S3 수명주기와 GUI worker 경계는 유지한다.
- 후속 보완은 프로필 선택 시 RDS/Secret/S3 catalog 조회 요청을 추가한다. Secret 저장은 새
  AWS 버전을 만들고 S3 삭제는 선택 객체를 제거하므로 둘 다 확인 dialog와 단건 경계로 제한한다.
  EC2 직접 조회 터미널은 사용자가 닫을 때까지 유지되며 새 포트나 상주 백그라운드 프로세스는 없다.
- 이번 보완은 SQLite migration 6에서 Secret 식별자만 저장한다. S3 열기는 사용자 동작 시
  GetObject와 로컬 임시 파일 쓰기·원자 교체·기본 앱 실행을 수행하며 실패한 임시 파일은 삭제한다.

추가 작업 필요:

- 승인된 비운영 AWS 프로필에 `rds:DescribeDBInstances`, `secretsmanager:ListSecrets`,
  `secretsmanager:PutSecretValue`, `s3:ListAllMyBuckets`, `s3:ListBucket`, `s3:PutObject`,
  `s3:GetObject`, `s3:DeleteObject`를 최소 권한으로 부여해 catalog/전송/단건 삭제를 smoke한다.
- start/reboot, Run Command Secret도 비운영 리소스에서 smoke하고,
  Windows 10/11 배율 100%·125%에서 사람이 최종 visual QA한 뒤 Portable ZIP을 재빌드한다.

## 9. 검증 하네스

### 9.1 로컬 명령

```powershell
./scripts/bootstrap.ps1
./scripts/check.ps1
./scripts/test-unit.ps1
./scripts/test-integration.ps1
./scripts/test-aws.ps1 -Profile brandbay-dev
./scripts/test-package.ps1 -ZipPath ./dist/aws-connect.zip
./scripts/generate-docs.ps1
./scripts/verify-docs.ps1
```

스크립트는 실행 위치에 의존하지 않고 저장소 루트를 스스로 찾는다. 실패 시 비정상 종료 코드와 다음 행동을 출력한다.

### 9.2 테스트 계층

| 계층 | 외부 자원 | 목적 |
|---|---|---|
| Architecture | 없음 | import 방향, 금지 API와 composition root 검사 |
| Domain unit | 없음 | 불변식과 순수 규칙 |
| Application unit | fake | 유스케이스, 오류, MFA 재개와 중복 실행 방지 |
| CLI adapter | fake | 인자, JSON, stderr와 종료 코드 |
| GUI adapter | fake | 이벤트, view model과 오류 표시 mapping |
| Infrastructure integration | 임시 DB/Stubber/가짜 프로세스 | SQLite, boto3, DPAPI와 Plugin 경계 |
| AWS smoke | 승인된 테스트 계정 | 실제 권한, SSM, EC2와 RDS 연결 |
| Package smoke | 깨끗한 Windows VM | Portable ZIP과 동봉 런타임 |

### 9.3 초기 품질 기준

- 전체 line coverage 80% 이상
- Domain과 Application line coverage 90% 이상
- 핵심 branch coverage 100%: 인증·MFA 재개 및 세션 토큰 갱신은
  `application/authentication_service.py`와 `application/authenticated_operation.py`, 로그
  마스킹은 `infrastructure/masking.py`, 오류 mapping은 `presentation/cli/errors.py`와
  `presentation/gui/errors.py`를 대상으로 한다. `scripts/check.ps1`이 pytest-cov XML을
  파싱해 각 파일을 개별적으로 강제하며 coverage 제외 설정으로 우회하지 않는다.
- Ruff, mypy와 architecture test 오류 0건
- 문서 내부 링크와 생성 문서 drift 0건
- detect-secrets 신규 탐지 0건
- 실제 AWS 테스트는 명시적으로 승인된 프로필에서만 실행

Coverage 숫자는 테스트를 위한 테스트를 만들기 위한 목표가 아니다. 중요한 오류와 상태 전이는 branch test로 직접 검증한다.

### 9.4 테스트 fixture

최소 fixture 세트를 저장소에 유지한다.

- 정상·만료·불일치·파싱 실패 세션
- MFA 성공·실패·취소·만료
- STS Account/IAM 일치·불일치
- SSM Online/Offline과 EC2 Describe 권한 부족
- RDS host/port 정상·오류와 로컬 포트 충돌
- Plugin 성공·누락·구버전·비정상 종료
- Secrets JSON·문자열·권한 부족·목록 권한 없음
- S3 단일·multipart·덮어쓰기·취소·네트워크 실패

fixture에는 실제 Access Key, Session Token, Secret과 고객 리소스 식별자를 넣지 않는다.

## 10. CI 게이트

Pull Request 또는 로컬 변경 검증은 다음 순서를 사용한다.

1. `docs`: 문서 구조, 링크, generated drift
2. `static`: Ruff, mypy, import boundary, 금지 API
3. `unit`: Domain, Application, CLI/GUI adapter
4. `integration`: SQLite, DPAPI, Stubber, fake Plugin
5. `security`: detect-secrets, Bandit, pip-audit
6. `package-windows`: GUI/CLI onedir 빌드와 smoke test
7. `aws-smoke`: 수동 승인 또는 보호된 릴리스 환경에서만 실행

필수 게이트가 실패한 상태에서는 기능 완료로 표시하지 않는다. 불안정한 실제 AWS 테스트는 unit/integration 게이트와 분리하되 실패 원인을 기술 부채로 숨기지 않는다.

## 11. 실행 계획 운영 방식

### 11.1 작업 시작

복잡한 기능은 `docs/exec-plans/active/NNN-title.md`에 실행 계획을 만든다. 계획은 다음 내용을 포함한다.

- 사용자 결과와 비목표
- 영향받는 문서와 모듈
- 수용 기준
- 예상 실패 모드
- 구현 단계와 검증 명령
- 보안·프로세스·포트·파일 사이드이펙트
- 결정 로그
- 진행 체크리스트

### 11.2 작업 중

- 완료한 항목과 실행한 명령을 계획에 기록한다.
- 실패한 검증 결과와 해결 방법을 남긴다.
- 새 기술 부채는 `docs/exec-plans/tech-debt-tracker.md`에 등록한다.
- 계획과 실제 구현이 달라지면 결정 이유를 기록한다.

### 11.3 작업 완료

- 모든 수용 기준과 필수 게이트를 통과한다.
- 코드, 테스트, 생성 문서와 기준 문서를 함께 갱신한다.
- active 계획을 completed로 이동한다.
- 남은 후속 작업은 명시적인 기술 부채 또는 새 실행 계획으로 분리한다.

## 12. 기능별 Definition of Done

기능 하나는 다음 조건을 모두 충족해야 완료다.

- Domain 규칙과 Application Service가 presentation에 독립적이다.
- 정상·경계·실패 단위 테스트가 있다.
- AWS 또는 Windows 경계는 fake/Stubber 기반 통합 테스트가 있다.
- CLI에서 사람이 읽는 출력과 JSON 계약을 검증했다.
- 오류 종료 코드와 GUI mapping이 정의됐다.
- GUI는 검증된 Application Service만 호출한다.
- 진행률·취소·MFA·프로세스 상태가 필요한 경우 공통 Operation 계약을 사용한다.
- 로그와 결과에 민감정보가 없다.
- `./scripts/check.ps1`이 성공한다.
- 관련 문서와 실행 계획이 현재 구현을 설명한다.

## 13. 관측 가능성과 진단성

- 모든 작업에 Operation ID와 correlation ID를 부여한다.
- 로그는 구조화된 이벤트 이름, 기능, 단계, 결과와 duration을 기록한다.
- 민감한 요청·응답 전체를 로그로 남기지 않는다.
- `aws_connect_cli doctor --output json`은 다음을 비파괴적으로 확인한다.
  - DB와 migration
  - DPAPI 사용 가능 여부
  - 로그 경로 쓰기 가능 여부
  - Plugin 존재와 버전
  - 기본 프로필과 세션 상태
  - 선택 Region 접근 가능 여부
- 실패한 테스트와 패키징 로그는 CI artifact로 보존한다.

## 14. 보안 가드레일

- 실제 자격증명은 fixture, 명령행 인자, 환경 출력과 CI 로그에 넣지 않는다.
- CLI의 Secret Key와 MFA 입력은 마스킹 프롬프트 또는 표준입력으로만 받는다.
- Secret 조회 결과는 명시적인 reveal 전까지 마스킹한다.
- SQLite와 로그 부모 디렉터리는 상속을 차단하고 현재 Windows 사용자의 상속 가능한
  Full Control ACE 하나로 제한한다. 이후 생성되는 DB sidecar/회전 로그는 이 ACE를
  상속할 수 있지만 effective DACL에 다른 trustee가 없어야 하며, 기존 파일과 DPAPI
  entropy/진단 archive는 같은 current-user-only ACL을 직접 적용한다.
- AWS Connect launcher/helper subprocess 인자와 예외 직렬화 전에 민감정보를 제거한다.
  `StreamUrl`과 `TokenValue`는 upstream 계약상 최종 Session Manager Plugin argv에만
  존재하며 그 argv는 로그·진단·오류에 직렬화하지 않는다.
- 실제 AWS smoke test는 전용 최소 권한 프로필과 테스트 리소스를 사용한다.
- 파괴적인 S3 삭제, IAM 변경과 RDS 데이터 조작은 테스트 범위에서 제외한다.

## 15. 성능과 안정성 기준

- GUI thread에서 AWS, SQLite, 파일 업로드와 Plugin 대기를 수행하지 않는다.
- 앱 시작 시 로컬 프로필과 토큰 상태를 먼저 표시하고 AWS 검증은 백그라운드에서 수행한다.
- 동일 프로필의 토큰 갱신은 process 내부 lock으로 직렬화한다.
- SQLite는 busy timeout과 짧은 transaction을 사용한다.
- S3 multipart 동시성은 제한하고 메모리 상한을 테스트한다.
- RDS 터널과 Plugin 프로세스는 명시적인 소유자와 종료 순서를 가진다.
- 오류가 발생한 기능만 중단하고 앱과 다른 기능은 계속 사용할 수 있어야 한다.

## 16. 문서 및 코드 드리프트 방지

다음 항목을 자동 검사 대상으로 만든다.

- `AGENTS.md`가 필수 문서와 검사 명령에 연결되는지
- 문서 내부 상대 링크가 유효한지
- migration과 `docs/generated/db-schema.md`가 일치하는지
- 정의된 Application 오류가 CLI/GUI mapper에 모두 존재하는지
- 정의된 CLI 명령과 help snapshot이 일치하는지
- 계층별 허용 import가 지켜지는지
- 목업의 주요 화면이 GUI smoke test 목록에 대응되는지
- 완료된 실행 계획이 active 폴더에 남아 있지 않은지

정기적인 문서·기술 부채 정리는 별도의 작은 작업으로 수행하고 기능 PR에 무관한 대규모 정리를 섞지 않는다.

## 17. 결정 게이트

Phase 0에서 다음 항목을 확정해야 한다.

| 결정 | 추천 기본값 | 대안·영향 |
|---|---|---|
| GUI framework | PySide6 | Tkinter는 배포가 작지만 목업 구현 비용 증가 |
| 패키징 | PyInstaller onedir | one-file은 단순하지만 Plugin·Qt 진단 어려움 |
| 사용자 데이터 경로 | `%LOCALAPPDATA%/AWSConnect` | 실행 폴더는 Portable하지만 쓰기 권한 실패 가능 |
| 지원 Windows | Windows 10 22H2 이상, Windows 11 | 더 오래된 Windows 지원 시 CI matrix 확대 |
| Secrets/S3 일정 | MVP 이후 Phase 7·8 | MVP 포함 시 첫 릴리스와 AWS 권한 matrix 확대 |
| 실제 AWS 테스트 | 전용 최소 권한 계정 | 운영 계정 사용은 안전성과 재현성 저하 |

추천 기본값으로 진행하더라도 결정 결과와 이유를 `docs/design-docs/decisions`에 기록한다.

## 18. 주요 리스크와 대응

| 리스크 | 영향 | 하네스 대응 |
|---|---|---|
| GUI가 Application 규칙을 재구현 | CLI/GUI 결과 불일치 | architecture test와 fake 기반 일관성 테스트 |
| 오류 문자열 파싱 | 로케일·SDK 변경 시 오작동 | 타입 오류와 mapper 완전성 테스트 |
| 토큰 갱신 동시 실행 | 토큰 덮어쓰기·중복 MFA | 프로필별 lock과 동시성 테스트 |
| Plugin 프로세스 잔존 | 리소스·세션 누수 | handle 상태 머신과 종료 fault injection |
| SQLite 잠금 | GUI·CLI 저장 실패 | busy timeout, 짧은 transaction과 경합 테스트 |
| 민감정보 노출 | 보안 사고 | 중앙 마스킹, detect-secrets와 회귀 테스트 |
| 실제 AWS 테스트 불안정 | 개발 피드백 지연 | fake/Stubber 게이트와 승인형 smoke 분리 |
| 문서와 코드 drift | 에이전트의 잘못된 구현 | 링크·schema·command·error contract 검사 |
| PyInstaller 누락 파일 | 사용자 환경에서 실행 실패 | 깨끗한 Windows VM 패키지 smoke |

## 19. 첫 실행 순서

구현을 시작할 때 다음 순서로 진행한다.

1. 결정 게이트 중 GUI framework, 패키징, 데이터 경로를 확정한다.
2. Git 저장소와 Phase 0 하네스를 만든다.
3. bootstrap 문서를 목표 `docs/` 구조로 이동하고 현재 `plan.md`를 `docs/exec-plans/active/001-aws-connect.md`로 전환한다.
4. 모든 문서 링크와 `AGENTS.md`·`ARCHITECTURE.md`의 지도를 검증한다.
5. architecture test와 `scripts/check.ps1`을 먼저 실패하는 상태로 만든다.
6. 최소 구조를 구현하여 하네스를 통과시킨다.
7. Phase 1 프로필·인증을 Application과 CLI로 완성한다.
8. CLI 실제 시나리오가 통과한 후 프로필·인증 GUI를 연결한다.
9. 이후 EC2, RDS도 같은 수직 슬라이스 순서를 반복한다.

## 20. 전체 완료 기준

- PRD MVP 기능이 CLI와 GUI에서 동일한 Application 계약으로 동작한다.
- AWS CLI, Python, gossm과 별도 Plugin 설치 없이 Portable ZIP이 실행된다.
- GUI가 `docs/DESIGN.md`와 `docs/design-docs/aws-connect-ui-mockup.html`의 주요 흐름을 충족한다.
- 인증정보, 토큰, MFA와 Secret 원문이 DB, 로그, 기본 출력과 AWS Connect/WT/helper
  프로세스 인자에서 발견되지 않는다. upstream 계약상 단기 `StreamUrl`·`TokenValue`가
  필요한 최종 Session Manager Plugin argv는 비기록·마스킹 경계로 제한한다.
- 필수 CI 게이트와 깨끗한 Windows 패키지 smoke가 통과한다.
- 활성 실행 계획과 기술 부채가 실제 상태를 반영한다.
- 새 에이전트가 `AGENTS.md`와 저장소 내 문서만으로 기능을 탐색하고 검증할 수 있다.
