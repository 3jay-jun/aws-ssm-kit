# 작업 기록 한국어 표시

## 전수 점검 후속 (2026-10-20)
- 사용자 확인 요청에 따라 모든 기록 생성 경로와 GUI에 기록될 수 있는 오류 코드를 대조한다.
- 추가 발견: EC2 `session_end` 누락, CLI 기록 작업명 추론 누락,
  인증 만료/네트워크/설정/저장소 등 알려진 오류의 일반 대체 안내, RDS 만료 종료 사유 누락.
- 수용 기준: 현재 코드가 생성하는 모든 작업 및 오류 코드에 한국어 표시가 있으며,
  AST 기반 누락 검사가 이후 추가도 탐지한다. 알 수 없는 미래 코드는 안전한 대체 안내 유지.
- 범위는 작업 기록/대시보드 표시다. AWS 실행·저장 형식·CLI 출력 계약은 유지한다.
- 기존 오류 카탈로그 및 공통 표시 매핑을 확장한다. 별도 번역 시스템 도입은 불필요하다.
- [x] 전체 코드 목록 대조, 누락 안내 보완 및 기록 단계·오류 내용 회귀 검증.
- [x] 전체 게이트 재실행과 증거 기록 후 완료 폴더 이동.

### 후속 구현 및 좁은 검증
- 기존 공통 매핑을 확장해 작업명 **50개**, 오류 카탈로그 **210개**로 보완했다.
  오류 신규 120개는 기존 dialogs/toasts와 로그가 같은 원본 문구를 사용한다.
- EC2 `session_end`, RDS `max_duration`, CLI nested command/drive 기록 표시를 보완했다.
  CLI drive 하위 명령을 target에 남기고 최상위 명령만 있으면 해당 명령을 target에 남긴다.
  기존 기록은 저장된 정보만 해석하며 복구 불가능한 과거 명령을 추정하지 않는다.
- 레벨 필터/설정/상세는 한국어로 표시하고 QComboBox userData로 원래 코드를 전달한다.
  오류 코드·AWS action·권한·식별자·내보내기 원본 진단 정보는 유지한다.
- `tests/adapter/test_log_catalog_completeness.py`는 실제 오류 생성자/팩토리·기록 호출 및
  CLI parser를 AST/등록 구조로 대조한다. 오류 카탈로그의 중복 key도 검사한다.
- `.venv/Scripts/python.exe -m pytest tests/adapter/test_log_catalog_completeness.py tests/adapter/gui/test_log_messages.py tests/adapter/gui/test_logs.py tests/adapter/gui/test_dashboard.py tests/adapter/cli/test_cli_main.py --no-cov -q --basetemp=.test-korean-inventory-v2 -o cache_dir=.test-cache-korean-inventory`
  → **41 passed**. 이후 drive 기록 경로 회귀 테스트 1개를 추가해 전체 게이트에 포함했다.
- 초기 소스 대조 검사에서 `drive.id.invalid`와 runtime 명령의 작업명 누락을 실제로 탐지하고
  카탈로그에 추가했다. 최초 lint의 import 순서와 긴 테스트 줄은 수정 후 전체 검사에 포함했다.
- `.venv/Scripts/python.exe -m mypy` → **101 source files passed**.
- 변경 source/test 및 신규 파일을 명시한 detect-secrets → potential secrets **0**,
  `.korean-logs-complete-secrets.json`.
- `graft build > .korean-logs-complete-graft.log` → exit 0, **186 cards**.
- 병행 작업의 ec2_rds.py/test_ec2_rds.py 및 관련 문서 변경은 보존한다.
  이 계획의 구현 범위나 검증 증거로 별도 기능 변경을 주장하지 않는다.

### 후속 최종 검증
- `./scripts/check.ps1 *> .korean-logs-complete-check.log`:
  문서/format/lint/architecture/import boundaries/mypy 통과,
  **724 passed, 1 skipped**, coverage **87.05%**,
  critical branch coverage 및 tracked secret scan 통과.
  Bandit은 `secret` 키의 공개 UI 문구를 hardcoded password로 오탐해 exit 1로 중단했다.
- 해당 한 줄에 비밀값이 아닌 UI label임을 밝히는 `nosec B105` 주석만 추가했다.
  `.venv/Scripts/python.exe -m bandit -q -r src > .korean-logs-complete-bandit.log 2>&1`
  → exit 0. 업무 로직 변경 및 광역 검사 제외 설정 없음.
- `.venv/Scripts/pip-audit.exe > .korean-logs-complete-audit.log 2>&1`
  → 승인된 네트워크 접근으로 exit 0, **No known vulnerabilities found**.
  로컬 앱 패키지는 PyPI 미등록으로 제외되며 이전 실행과 동일하다.
- `.venv/Scripts/python.exe -m ruff check src tests tools` 및
  `.venv/Scripts/python.exe -m ruff format --check src tests tools` → 통과, 188 files formatted.
- `.venv/Scripts/python.exe -m pytest tests/adapter/test_log_catalog_completeness.py tests/adapter/gui/test_log_messages.py tests/adapter/gui/test_logs.py tests/adapter/gui/test_dashboard.py tests/adapter/cli/test_cli_main.py --no-cov -q --basetemp=.test-korean-inventory-final -o cache_dir=.test-cache-korean-inventory`
  → **42 passed**. Bandit 주석 추가 후에도 전체 소스 대조 및 관련 회귀 확인.
- `git diff --check`에 변경한 CLI/GUI/테스트 파일을 명시 → exit 0.
- 사이드이펙트: 번역 카탈로그의 작은 메모리 증가와 CLI 기록 target의 정보 보완만 있다.
  제품의 AWS 요청·DB schema·연결 소유권·포트·프로세스·타이머·의존성은 변경하지 않는다.
- 잔여 구현 차단 사항 없음. 개발 앱 재시작, 배포 EXE/ZIP 재빌드가 다음 적용 단계다.

## 범위와 수용 기준
- Python 3.12 / PySide6 GUI의 작업 기록·상세·복사·대시보드 최근 작업은 같은 표시 모델을 사용한다.
- `list`는 `목록`, RDS `start`/`start_managed`는 `연결 실행`으로 표시한다.
- 성공·시작·진행·취소·실패를 한국어 문장으로 구분하고 알려진 오류는 기존 GUI 오류 안내를 재사용한다.
- 알 수 없는 코드는 한국어 대체 안내를 제공한다. 오류 코드·권한·요청 식별자는 진단용으로 유지한다.
- 기존 한국어 성공/진행 내용은 보존한다. 실패는 승인된 오류 설명과 해결 안내를 우선한다.
  DB 저장 코드·검색·CLI·AWS 동작은 변경하지 않는다.

## 설계와 영향
- 권위: [제품 요구사항](../../product-specs/aws-connect.md), [GUI 설계](../../DESIGN.md).
- SSOT 검색: graft map/ask/skeleton 및 rg `message_code`, `operation`, `record_success`,
  `logged_operation`. view_models의 원문 코드 표시가 원인이며 logs/dashboard가 공유한다.
- 선택: 기존 표시 모델에 순수 한국어 매핑 적용(추천: 과거 기록에도 즉시 적용).
  대안: 기록 생성 시 한국어 저장(과거 기록 이관 필요, 표시 책임이 Application으로 침범).
- 오류 문구는 errors.py의 단일 카탈로그를 함수로 공개해 재사용한다.
- 추가 AWS/DB 요청, 프로세스·포트·타이머·의존성·마이그레이션 변경 없음.
- 실제 AWS·사용자 저장 데이터에 접근하지 않는다. 배포 EXE 반영에는 재빌드가 필요하다.

## 실행과 검증
- [x] 기존 구조·로그 생성/표시 경로 조사.
- [x] 공통 한국어 표시 및 오류 카탈로그 재사용 구현.
- [x] 순수 표시 매핑과 GUI/대시보드/복사 회귀 테스트.
- [x] `./scripts/check.ps1`, 보안 검사, graft 갱신 및 결과 기록.

## 다음 단계
소스 구현 및 관련 게이트 검증 완료. 개발 앱은 재시작으로 표시를 반영하며 배포 EXE는 재빌드가 필요하다.
실제 AWS 호출 및 배포본 생성은 이번 표시 수정 범위에 포함하지 않았다.

## 검증 증거 (2026-10-20)
- `.venv/Scripts/python.exe -m pytest tests/adapter/gui/test_log_messages.py tests/adapter/gui/test_logs.py tests/adapter/gui/test_dashboard.py tests/adapter/test_error_mapper_completeness.py --no-cov -q --basetemp=.test-korean-logs-v2 -o cache_dir=.test-cache-korean-logs`
  → **28 passed**. 최초 실행의 긴 영어 원문 스크롤 fixture는 한국어 긴 내용으로 갱신했다.
- 최초 `./scripts/check.ps1 *> .korean-logs-check.log`는 타입 검사에서 실패했다.
  optional 오류 분류의 안내 카탈로그 타입을 명시해 수정하고 전체 검사를 다시 실행했다.
- `.venv/Scripts/detect-secrets.exe scan`에 변경 source/test/docs 및 신규 파일을 명시적으로 전달
  → `.korean-logs-secrets.json`, potential secrets **0**.
- `git diff --check -- docs/DESIGN.md docs/product-specs/aws-connect.md src tests` → exit 0.
- `graft build` → exit 0, 185 cards / 7 changed files parsed.
- 재실행 `./scripts/check.ps1 *> .korean-logs-check.log`:
  문서/format/lint/architecture/import boundaries/mypy(101 source files) 통과.
  전체 테스트 **708 passed, 1 skipped**, coverage **86.93%**,
  critical branch coverage(5 files 100%), tracked secret scan, Bandit 통과.
  마지막 dependency audit만 PyPI DNS 제한으로 실패하여 script exit 1.
- 네트워크 접근 승인을 받아 `.venv/Scripts/pip-audit.exe > .korean-logs-audit.log 2>&1`
  별도 재실행 → exit 0, **No known vulnerabilities found**.
  로컬 앱 패키지 자체는 PyPI 미등록으로 감사에서 제외(기존 editable metadata 1.0.1).
  검사 실패를 숨기거나 검사 스크립트/의존성을 변경하지 않았다.
- 최종 효과: DB 이관·환경변수·라이브러리 설치 불필요. 네트워크 접근은 개발 의존성 감사에만 사용.
  제품 실행 시 추가 네트워크/포트/프로세스/초기화/타이머 영향 없음. 잔여 구현 차단 사항 없음.
- 완료 계획 이동 후 `UV_CACHE_DIR=<workspace>/.uv-cache`로 `./scripts/verify-docs.ps1`
  → exit 0, documentation passed / generated documentation current.
  최초 캐시 미지정 실행은 sandbox 외부 uv cache 쓰기 거부로 실패하여 workspace cache로 재실행했다.
